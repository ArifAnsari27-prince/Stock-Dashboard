"""Price refresh job: universe -> prices -> compute -> storage (build step 5).

This is a composition root. It wires together the universe, the price source,
the pure compute functions, and storage into one batch run intended to be
invoked by a scheduled GitHub Actions cron (~every 15 min during market hours).
The dashboard never runs this; it only reads the snapshots this writes
(CLAUDE.md §2).

What it writes per run:
  - dataset "prices":  the raw OHLCV PriceBar snapshot (yfinance provenance).
  - dataset "metrics": one MetricsRow per universe constituent — the latest
    technical + return metrics joined to the universe row. Tagged COMPUTED with
    a note pointing back at the yfinance fetch it was derived from.

The universe is read from the latest stored "universe" snapshot; on first run
(none stored yet) it is fetched live and persisted to bootstrap.

Dependencies are injected so the whole flow is unit-testable with fake sources
and a temp store — no network. `main()` builds the real implementations.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from src.compute.returns import return_metrics
from src.compute.technicals import technical_indicators
from src.config import Config, get_config
from src.data_sources import universe as universe_module
from src.data_sources.base import PriceSource
from src.jobs.common import UniverseFetchFn, load_universe
from src.models import (
    DataSource,
    MetricsRow,
    PriceBar,
    Provenance,
    Snapshot,
)
from src.storage.base import Storage

logger = logging.getLogger(__name__)

PRICES_DATASET = "prices"
METRICS_DATASET = "metrics"


def _bars_to_frame(bars: list[PriceBar]) -> pd.DataFrame:
    """Build a date-indexed, ascending OHLCV frame from one symbol's bars."""
    df = pd.DataFrame([bar.model_dump() for bar in bars])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def run_price_refresh(
    *,
    price_source: PriceSource,
    storage: Storage,
    config: Config | None = None,
    fetch_universe_fn: UniverseFetchFn = universe_module.fetch_universe,
    today: date | None = None,
) -> dict[str, object]:
    """Run one full price refresh and return a summary dict.

    Steps: load universe -> fetch prices for universe + benchmarks -> persist raw
    prices -> compute per-ticker metrics -> persist metrics. Partial failure
    (a ticker with no price data) is logged and that ticker is omitted, never
    raised (CLAUDE.md §2).
    """
    config = config or get_config()
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=config.price_lookback_days)
    # yfinance treats `end` as exclusive, so add a day to include today's bar.
    end = today + timedelta(days=1)

    logger.info("Price refresh start: today=%s lookback=%dd", today, config.price_lookback_days)

    universe_tickers = load_universe(storage, fetch_universe_fn)
    if not universe_tickers:
        logger.error("Universe is empty; nothing to refresh")
        return {"universe": 0, "priced": 0, "metrics_rows": 0}

    benchmark_symbols = list(config.benchmark_symbols)
    universe_symbols = [t.symbol for t in universe_tickers]
    fetch_symbols = list(dict.fromkeys(universe_symbols + benchmark_symbols))

    logger.info(
        "Fetching prices for %d symbols (%d universe + %d benchmarks)",
        len(fetch_symbols),
        len(universe_symbols),
        len(benchmark_symbols),
    )
    price_snapshot = price_source.fetch_prices(fetch_symbols, start, end)
    prices_path = storage.write_snapshot(PRICES_DATASET, price_snapshot)
    logger.info("Wrote %d price bars to %s", len(price_snapshot.rows), prices_path)

    # Group bars by symbol.
    bars_by_symbol: dict[str, list[PriceBar]] = defaultdict(list)
    for bar in price_snapshot.rows:
        bars_by_symbol[bar.symbol].append(bar)

    # Benchmark adjusted-close series, keyed by lowercase label (qqq, spy).
    benchmark_series: dict[str, pd.Series] = {}
    for symbol in benchmark_symbols:
        if symbol in bars_by_symbol:
            benchmark_series[symbol.lower()] = _bars_to_frame(
                bars_by_symbol[symbol]
            )["adj_close"]
        else:
            logger.warning("Benchmark %s missing from price fetch", symbol)

    # Compute metrics per universe ticker.
    rows: list[MetricsRow] = []
    missing: list[str] = []
    for ticker in universe_tickers:
        bars = bars_by_symbol.get(ticker.symbol)
        if not bars:
            missing.append(ticker.symbol)
            continue
        frame = _bars_to_frame(bars)
        technicals = technical_indicators(frame)
        returns = return_metrics(frame["adj_close"], benchmarks=benchmark_series)
        rows.append(
            MetricsRow(
                symbol=ticker.symbol,
                name=ticker.name,
                weight=ticker.weight,
                as_of=frame.index[-1].date(),
                latest_close=float(frame["close"].iloc[-1]),
                **technicals,
                **returns,
            )
        )

    if missing:
        logger.warning("%d universe symbols had no price data: %s", len(missing), missing)

    metrics_snapshot = Snapshot[MetricsRow](
        provenance=Provenance(
            source=DataSource.COMPUTED,
            fetched_at=datetime.now(timezone.utc),
            notes=(
                f"derived from {price_snapshot.provenance.source.value} prices "
                f"fetched {price_snapshot.provenance.fetched_at.isoformat()}"
            ),
        ),
        rows=rows,
    )
    metrics_path = storage.write_snapshot(METRICS_DATASET, metrics_snapshot)
    logger.info("Wrote %d metric rows to %s", len(rows), metrics_path)

    summary: dict[str, object] = {
        "universe": len(universe_tickers),
        "priced": len(bars_by_symbol),
        "metrics_rows": len(rows),
        "missing": missing,
        "prices_path": str(prices_path),
        "metrics_path": str(metrics_path),
    }
    logger.info("Price refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled job: build real deps and run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Imported here so the only yfinance import stays inside the price source
    # module's import graph, not at module top of the job.
    from src.data_sources.prices import YFinancePriceSource
    from src.storage.parquet_store import ParquetStore

    config = get_config()
    storage = ParquetStore(config.data_dir)
    price_source = YFinancePriceSource()
    run_price_refresh(price_source=price_source, storage=storage, config=config)


if __name__ == "__main__":
    main()
