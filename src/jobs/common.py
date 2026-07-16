"""Shared helpers for refresh jobs (build steps 5/7).

Universe loading (with first-run bootstrap) and CIK enrichment live here so the
price, fundamentals, and filings jobs share one implementation.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.compute.returns import return_metrics
from src.compute.technicals import technical_indicators
from src.data_sources import indices as indices_module
from src.data_sources import universe as universe_module
from src.data_sources.edgar import enrich_tickers_with_cik
from src.models import DataSource, Fundamentals, MetricsRow, Provenance, Snapshot, Ticker
from src.storage.base import Storage

# SEC forms that carry financial statements (drive fundamentals staleness).
_FINANCIAL_FORMS = {"10-K", "10-Q"}

logger = logging.getLogger(__name__)

UNIVERSE_DATASET = "universe"

# Index id -> boolean membership column emitted on each metrics row (for the
# frontend/DuckDB to filter a grid to one index cheaply).
INDEX_MEMBERSHIP_COLUMNS = {
    indices_module.NASDAQ100: "in_nasdaq100",
    indices_module.SP500: "in_sp500",
    indices_module.RUSSELL1000: "in_russell1000",
    indices_module.RUSSELL3000: "in_russell3000",
}

UniverseFetchFn = Callable[[], Snapshot[Ticker]]
MasterUniverseFetchFn = Callable[[], Snapshot[Ticker]]


def write_job_summary(title: str, summary: dict[str, object]) -> None:
    """Append a Markdown run summary to GitHub Actions' step summary, if present.

    No-op outside Actions (GITHUB_STEP_SUMMARY unset), so jobs stay
    orchestrator-agnostic. Failures here must never fail the job itself.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        lines = [f"### {title}", "", "| key | value |", "| --- | --- |"]
        lines += [f"| {key} | {value} |" for key, value in summary.items()]
        lines.append("")
        with Path(path).open("a") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:  # pragma: no cover — environment-specific
        logger.warning("Could not write job summary: %s", exc)


def _none_if_nan(value: object) -> object | None:
    """Coerce a NaN (from a Parquet read) to None; pass other values through."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _universe_from_frame(df: pd.DataFrame) -> list[Ticker]:
    """Reconstruct Tickers from a stored universe snapshot DataFrame."""
    tickers: list[Ticker] = []
    for record in df.to_dict("records"):
        tickers.append(
            Ticker(
                symbol=str(record["symbol"]),
                name=_none_if_nan(record.get("name")),
                cik=_none_if_nan(record.get("cik")),
                weight=_none_if_nan(record.get("weight")),
            )
        )
    return tickers


def load_universe(
    storage: Storage,
    fetch_universe_fn: UniverseFetchFn = universe_module.fetch_universe,
) -> list[Ticker]:
    """Return the current universe, bootstrapping from a live fetch if needed."""
    try:
        df = storage.read_latest(UNIVERSE_DATASET)
        tickers = _universe_from_frame(df)
        logger.info("Loaded %d universe constituents from storage", len(tickers))
        return tickers
    except FileNotFoundError:
        logger.info("No stored universe; fetching live to bootstrap")
        snapshot = fetch_universe_fn()
        storage.write_snapshot(UNIVERSE_DATASET, snapshot)
        logger.info("Bootstrapped and persisted %d constituents", len(snapshot.rows))
        return snapshot.rows


def ensure_universe_ciks(
    storage: Storage, tickers: list[Ticker], cik_map: dict[str, str]
) -> list[Ticker]:
    """Fill missing CIKs from `cik_map`, persisting an updated universe if any were added.

    EDGAR is keyed by CIK, which the Nasdaq universe source does not provide. We
    enrich once and persist so later runs read CIKs straight from storage.
    """
    before = sum(1 for t in tickers if t.cik)
    enriched = enrich_tickers_with_cik(tickers, cik_map)
    after = sum(1 for t in enriched if t.cik)

    if after > before:
        logger.info("Enriched universe with %d new CIKs; persisting", after - before)
        storage.write_snapshot(
            UNIVERSE_DATASET,
            Snapshot[Ticker](
                provenance=Provenance(
                    source=DataSource.NASDAQ_INDEX,
                    fetched_at=datetime.now(timezone.utc),
                    notes="Nasdaq-100 constituents, CIK-enriched via SEC company_tickers",
                ),
                rows=enriched,
            ),
        )
    else:
        logger.info("Universe CIKs already complete (%d/%d)", after, len(tickers))
    return enriched


# --- Master (multi-index) universe + metrics (Phase B) -----------------------


def _master_from_frame(df: pd.DataFrame) -> list[Ticker]:
    """Reconstruct master-universe Tickers (incl. memberships/sector/market_cap)."""
    tickers: list[Ticker] = []
    for record in df.to_dict("records"):
        memberships = record.get("memberships")
        if memberships is None or (isinstance(memberships, float) and math.isnan(memberships)):
            memberships = ()
        else:
            memberships = tuple(memberships)  # list column -> tuple
        tickers.append(
            Ticker(
                symbol=str(record["symbol"]),
                name=_none_if_nan(record.get("name")),
                cik=_none_if_nan(record.get("cik")),
                weight=_none_if_nan(record.get("weight")),
                sector=_none_if_nan(record.get("sector")),
                industry=_none_if_nan(record.get("industry")),
                market_cap=_none_if_nan(record.get("market_cap")),
                memberships=memberships,
            )
        )
    return tickers


def load_master_universe(
    storage: Storage,
    fetch_master_fn: MasterUniverseFetchFn = indices_module.fetch_master_universe,
) -> list[Ticker]:
    """Return the master multi-index universe, bootstrapping from a live fetch if needed."""
    try:
        df = storage.read_latest(UNIVERSE_DATASET)
        if "memberships" not in df.columns:
            raise FileNotFoundError("stored universe predates multi-index schema")
        tickers = _master_from_frame(df)
        logger.info("Loaded master universe (%d tickers) from storage", len(tickers))
        return tickers
    except FileNotFoundError:
        logger.info("No master universe stored; fetching live to bootstrap")
        snapshot = fetch_master_fn()
        storage.write_snapshot(UNIVERSE_DATASET, snapshot)
        logger.info("Bootstrapped master universe (%d tickers)", len(snapshot.rows))
        return snapshot.rows


def frames_from_price_history(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split a flat price-history DataFrame into date-indexed frames per symbol."""
    if df.empty or "symbol" not in df.columns:
        return {}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    frames: dict[str, pd.DataFrame] = {}
    for symbol, group in df.groupby("symbol"):
        frame = group.set_index("date").sort_index()
        # Some data sources emit a symbol twice on a day; a duplicated date index
        # breaks downstream align/concat, so keep the last row per date.
        frame = frame[~frame.index.duplicated(keep="last")]
        frames[str(symbol)] = frame
    return frames


def latest_financial_filing_dates(filings_df: pd.DataFrame) -> dict[str, str]:
    """Map symbol -> latest 10-K/10-Q filing date (ISO str) from a filings table."""
    if filings_df.empty or "form" not in filings_df.columns:
        return {}
    fins = filings_df[filings_df["form"].isin(_FINANCIAL_FORMS)]
    if fins.empty:
        return {}
    latest = fins.groupby("symbol")["filed_date"].max()
    return {str(sym): str(pd.to_datetime(d).date()) for sym, d in latest.items()}


def _scalar_or_none(value: object) -> object | None:
    """Null-coerce a scalar read from Parquet (handles None, NaN, and NaT)."""
    if value is None:
        return None
    try:
        if pd.isna(value):  # covers float NaN and datetime NaT
            return None
    except (TypeError, ValueError):
        pass  # non-scalar (shouldn't happen for Fundamentals fields)
    return value


def fundamentals_from_frame(df: pd.DataFrame) -> list[Fundamentals]:
    """Reconstruct Fundamentals rows from a stored fundamentals table."""
    fields = set(Fundamentals.model_fields)
    rows: list[Fundamentals] = []
    for record in df.to_dict("records"):
        clean = {k: _scalar_or_none(v) for k, v in record.items() if k in fields}
        rows.append(Fundamentals(**clean))
    return rows


def build_metrics_rows(
    tickers: Sequence[Ticker],
    frames_by_symbol: dict[str, pd.DataFrame],
    benchmark_series: dict[str, pd.Series],
) -> tuple[list[MetricsRow], list[str]]:
    """Compute latest metric rows for each ticker that has price history.

    `frames_by_symbol` maps symbol -> a date-indexed OHLCV frame (columns
    high/low/close/adj_close/volume). Returns (rows, missing_symbols). Each row
    carries identity + index-membership booleans + sector/market_cap + the
    technical and return/risk metrics. Tickers without a frame are returned in
    `missing` (logged by the caller), never raised.
    """
    rows: list[MetricsRow] = []
    missing: list[str] = []
    for ticker in tickers:
        frame = frames_by_symbol.get(ticker.symbol)
        if frame is None or frame.empty:
            missing.append(ticker.symbol)
            continue
        technicals = technical_indicators(frame)
        returns = return_metrics(frame["adj_close"], benchmarks=benchmark_series)
        membership_flags = {
            col: (idx in ticker.memberships)
            for idx, col in INDEX_MEMBERSHIP_COLUMNS.items()
        }
        rows.append(
            MetricsRow(
                symbol=ticker.symbol,
                name=ticker.name,
                weight=ticker.weight,
                sector=ticker.sector,
                industry=ticker.industry,
                market_cap=ticker.market_cap,
                as_of=frame.index[-1].date(),
                latest_close=float(frame["close"].iloc[-1]),
                **membership_flags,
                **technicals,
                **returns,
            )
        )
    return rows, missing
