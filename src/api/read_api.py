"""Read API — the clean contract the Streamlit frontend builds against (build step 8).

This is the ONLY module the frontend imports. It never fetches live data and never
touches data_sources or jobs; it reads the latest cached snapshots through the
`Storage` interface and joins them into frontend-friendly shapes (CLAUDE.md §3).

Snapshots are self-describing: every stored row carries provenance columns
(`_source`, `_fetched_at`, `_disclaimer`, `_notes`). The API strips those from the
returned tables but surfaces them via `provenance()` and embeds the
"prototype / delayed / unofficial source" disclaimer in tearsheets and the market
overview, so consumers can never mistake this for production-grade data.

Functions degrade gracefully: if a dataset has not been produced yet, tables come
back empty rather than raising, so the dashboard can show an empty state.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

from src.compute import technicals
from src.storage.base import Storage

logger = logging.getLogger(__name__)

UNIVERSE_DATASET = "universe"
PRICES_DATASET = "prices"
METRICS_DATASET = "metrics"
FUNDAMENTALS_DATASET = "fundamentals"
FILINGS_DATASET = "filings"
AGGREGATES_DATASET = "index_aggregates"
SECTORS_DATASET = "index_sectors"

# OHLCV columns kept from a prices snapshot, in display order.
_OHLCV_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")

_PROVENANCE_COLUMNS = ("_source", "_fetched_at", "_disclaimer", "_notes")

# Period-return columns summarized in the market overview, if present.
_OVERVIEW_RETURNS = ("return_1d", "return_1m", "return_3m", "return_ytd", "return_1y")


def _strip_provenance(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the reserved `_`-prefixed provenance columns from a snapshot frame."""
    return df[[c for c in df.columns if not c.startswith("_")]]


def _extract_provenance(df: pd.DataFrame) -> dict[str, object]:
    """Pull provenance (source/fetched_at/disclaimer/notes) from a snapshot frame."""
    if df.empty:
        return {}
    row = df.iloc[0]
    return {col[1:]: row[col] for col in _PROVENANCE_COLUMNS if col in df.columns}


class ReadAPI:
    """Read-only views over the latest cached snapshots."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def _read_latest(self, name: str) -> pd.DataFrame:
        """Latest snapshot for `name`, or an empty frame if none exists yet."""
        try:
            return self._storage.read_latest(name)
        except FileNotFoundError:
            logger.warning("No snapshot yet for dataset '%s'", name)
            return pd.DataFrame()

    def provenance(self, name: str = METRICS_DATASET) -> dict[str, object]:
        """Provenance of a dataset's latest snapshot (source, fetched_at, disclaimer)."""
        return _extract_provenance(self._read_latest(name))

    def _prices_for_symbol(self, symbol: str) -> pd.DataFrame:
        """Rows of the prices dataset for one symbol.

        On an object store, prices are date-partitioned; read them with a pushed-down
        predicate so only that symbol's data is scanned. On a Parquet store, prices
        are a single latest snapshot we filter in memory.
        """
        safe = symbol.replace("'", "")
        if hasattr(self._storage, "read_dataset"):
            return self._storage.read_dataset(  # type: ignore[attr-defined]
                PRICES_DATASET, where=f"upper(symbol) = '{safe}'"
            )
        prices = self._read_latest(PRICES_DATASET)
        if prices.empty or "symbol" not in prices.columns:
            return pd.DataFrame()
        return prices[prices["symbol"].str.upper() == symbol]

    def get_universe(self) -> pd.DataFrame:
        """The current constituent list (symbol, name, cik, weight)."""
        return _strip_provenance(self._read_latest(UNIVERSE_DATASET))

    def get_table(self, index: str | None = None) -> pd.DataFrame:
        """The dashboard grid: one row per ticker, metrics joined with fundamentals.

        Left-joins the latest `fundamentals` onto the latest `metrics` by symbol.
        If `index` is given (e.g. "sp500"), filters to that index's members via the
        `in_<index>` boolean column (empty if that column is absent). Empty if no
        metrics snapshot exists yet. Provenance columns are stripped.
        """
        metrics = self._read_latest(METRICS_DATASET)
        if metrics.empty:
            return pd.DataFrame()
        table = _strip_provenance(metrics)

        if index:
            col = f"in_{index}"
            if col not in table.columns:
                return pd.DataFrame()
            table = table[table[col] == True]  # noqa: E712 — pandas boolean mask

        fundamentals = self._read_latest(FUNDAMENTALS_DATASET)
        if not fundamentals.empty:
            funds = _strip_provenance(fundamentals)
            table = table.merge(funds, on="symbol", how="left", suffixes=("", "_fund"))
        return table.reset_index(drop=True)

    def get_price_history(
        self, ticker: str, indicators: bool = True
    ) -> pd.DataFrame:
        """Daily OHLCV history for one ticker, for charting (date-indexed, ascending).

        Reads the latest `prices` snapshot (which carries the full ~2yr history per
        symbol) and filters to `ticker`. Columns: open, high, low, close,
        adj_close, volume. When `indicators=True`, overlay series computed by the
        SAME backend functions used for the table are appended, so charts agree
        with the grid: sma_20/50/200, rsi_14, macd/macd_signal/macd_histogram,
        bollinger_upper/middle/lower (all on adjusted close). Empty if the symbol
        has no price snapshot yet. Works for benchmarks too (e.g. "QQQ", "SPY").
        """
        symbol = ticker.upper()
        rows = self._prices_for_symbol(symbol)
        if rows.empty or "symbol" not in rows.columns:
            return pd.DataFrame()

        df = _strip_provenance(rows).copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        keep = [c for c in _OHLCV_COLUMNS if c in df.columns]
        df = df[keep]

        if indicators and "adj_close" in df.columns:
            close = df["adj_close"]
            for window in (20, 50, 200):
                df[f"sma_{window}"] = technicals.sma(close, window)
            df["rsi_14"] = technicals.rsi(close)
            macd_df = technicals.macd(close)
            df["macd"] = macd_df["macd"]
            df["macd_signal"] = macd_df["signal"]
            df["macd_histogram"] = macd_df["histogram"]
            bands = technicals.bollinger_bands(close)
            df["bollinger_upper"] = bands["upper"]
            df["bollinger_middle"] = bands["middle"]
            df["bollinger_lower"] = bands["lower"]

        return df

    def get_tearsheet(self, ticker: str) -> dict[str, object]:
        """Everything for one ticker: merged metrics+fundamentals row plus filings.

        Returns a dict with `found`, `data` (the flat merged row), `filings` (a list
        of the ticker's latest filing links), and `provenance`.
        """
        symbol = ticker.upper()
        table = self.get_table()
        row: dict[str, object] = {}
        found = False
        if not table.empty and "symbol" in table.columns:
            match = table[table["symbol"].str.upper() == symbol]
            if not match.empty:
                row = match.iloc[0].to_dict()
                found = True

        filings_df = self._read_latest(FILINGS_DATASET)
        filings: list[dict] = []
        if not filings_df.empty and "symbol" in filings_df.columns:
            f = _strip_provenance(filings_df)
            filings = f[f["symbol"].str.upper() == symbol].to_dict("records")

        return {
            "symbol": symbol,
            "found": found,
            "data": row,
            "filings": filings,
            "provenance": self.provenance(METRICS_DATASET),
        }

    def get_market_overview(self) -> dict[str, object]:
        """Breadth / aggregate stats across the universe for the dashboard header."""
        table = self.get_table()
        if table.empty:
            return {"constituents": 0}

        overview: dict[str, object] = {"constituents": int(len(table))}
        if "as_of" in table.columns and table["as_of"].notna().any():
            overview["as_of"] = table["as_of"].dropna().max()

        for window in (50, 200):
            col = f"price_vs_sma_{window}"
            if col in table.columns:
                valid = table[col].dropna()
                if not valid.empty:
                    overview[f"pct_above_sma_{window}"] = round(
                        float((valid > 0).mean() * 100.0), 1
                    )

        for col in _OVERVIEW_RETURNS:
            if col in table.columns:
                valid = table[col].dropna()
                if not valid.empty:
                    overview[f"median_{col}"] = round(float(valid.median()), 4)

        if "rsi_14" in table.columns:
            valid = table["rsi_14"].dropna()
            if not valid.empty:
                overview["median_rsi_14"] = round(float(valid.median()), 1)

        if "return_1d" in table.columns:
            valid = table["return_1d"].dropna()
            overview["advancers"] = int((valid > 0).sum())
            overview["decliners"] = int((valid < 0).sum())

        prov = self.provenance(METRICS_DATASET)
        overview["disclaimer"] = prov.get(
            "disclaimer", "prototype / delayed / unofficial source"
        )
        return overview

    # --- Multi-index comparison (Phase E) --------------------------------

    def get_indices(self) -> list[dict]:
        """List the available indices (id, name, ETF proxy) from the aggregates table."""
        aggs = _strip_provenance(self._read_latest(AGGREGATES_DATASET))
        if aggs.empty:
            return []
        cols = [c for c in ("index_id", "name", "etf") if c in aggs.columns]
        return aggs[cols].to_dict("records")

    def get_index_comparison(self) -> dict[str, object]:
        """Per-index aggregates + sector weights for the comparison page.

        Returns `{aggregates: [...one row per index...], sectors: [...index x
        sector x weight...], provenance: {...}}`. Empty lists if not computed yet.
        """
        aggs = _strip_provenance(self._read_latest(AGGREGATES_DATASET))
        sectors = _strip_provenance(self._read_latest(SECTORS_DATASET))
        return {
            "aggregates": aggs.to_dict("records") if not aggs.empty else [],
            "sectors": sectors.to_dict("records") if not sectors.empty else [],
            "provenance": self.provenance(AGGREGATES_DATASET),
        }

    def get_index_performance(self, rebased: bool = True) -> pd.DataFrame:
        """Index ETF-proxy price series as date x index_id, for the relative chart.

        Reads each index's ETF proxy history from `prices` and pivots to one column
        per index. When `rebased=True`, each series starts at 100 for easy relative
        comparison. Empty if no aggregates/prices exist yet.
        """
        indices = self.get_indices()
        etf_to_index = {i["etf"]: i["index_id"] for i in indices if i.get("etf")}
        if not etf_to_index:
            return pd.DataFrame()

        frames = []
        for etf, index_id in etf_to_index.items():
            hist = self._prices_for_symbol(etf.upper())
            if hist.empty or "adj_close" not in hist.columns:
                continue
            s = hist.copy()
            s["date"] = pd.to_datetime(s["date"])
            series = s.sort_values("date").set_index("date")["adj_close"].rename(index_id)
            frames.append(series)
        if not frames:
            return pd.DataFrame()

        wide = pd.concat(frames, axis=1).sort_index()
        if rebased:
            wide = wide.apply(lambda col: col / col.dropna().iloc[0] * 100.0 if col.dropna().size else col)
        return wide


@lru_cache(maxsize=1)
def default_read_api() -> ReadAPI:
    """Build a ReadAPI backed by the configured storage (local Parquet or R2 object store).

    Cached so the frontend can call the module-level helpers freely. The storage
    backend is chosen by DATA_URI (see storage/factory.py); importing it here keeps
    that dependency out of the frontend.
    """
    from src.config import get_config
    from src.storage.factory import get_storage

    return ReadAPI(get_storage(get_config()))


def get_universe() -> pd.DataFrame:
    """Module-level convenience: current universe via the default store."""
    return default_read_api().get_universe()


def get_table(index: str | None = None) -> pd.DataFrame:
    """Module-level convenience: dashboard grid (optionally one index) via the default store."""
    return default_read_api().get_table(index=index)


def get_indices() -> list[dict]:
    """Module-level convenience: available indices via the default store."""
    return default_read_api().get_indices()


def get_index_comparison() -> dict[str, object]:
    """Module-level convenience: per-index comparison tables via the default store."""
    return default_read_api().get_index_comparison()


def get_index_performance(rebased: bool = True) -> pd.DataFrame:
    """Module-level convenience: rebased index performance series via the default store."""
    return default_read_api().get_index_performance(rebased=rebased)


def get_tearsheet(ticker: str) -> dict[str, object]:
    """Module-level convenience: single-ticker tearsheet via the default store."""
    return default_read_api().get_tearsheet(ticker)


def get_price_history(ticker: str, indicators: bool = True) -> pd.DataFrame:
    """Module-level convenience: OHLCV + indicator history via the default store."""
    return default_read_api().get_price_history(ticker, indicators=indicators)


def get_market_overview() -> dict[str, object]:
    """Module-level convenience: market overview via the default store."""
    return default_read_api().get_market_overview()
