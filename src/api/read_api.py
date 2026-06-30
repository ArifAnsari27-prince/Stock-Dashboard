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

from src.storage.base import Storage

logger = logging.getLogger(__name__)

UNIVERSE_DATASET = "universe"
METRICS_DATASET = "metrics"
FUNDAMENTALS_DATASET = "fundamentals"
FILINGS_DATASET = "filings"

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

    def get_universe(self) -> pd.DataFrame:
        """The current constituent list (symbol, name, cik, weight)."""
        return _strip_provenance(self._read_latest(UNIVERSE_DATASET))

    def get_table(self) -> pd.DataFrame:
        """The dashboard grid: one row per ticker, metrics joined with fundamentals.

        Left-joins the latest `fundamentals` onto the latest `metrics` by symbol.
        Empty if no metrics snapshot exists yet. Provenance columns are stripped;
        use `provenance()` for the data's source/timestamp.
        """
        metrics = self._read_latest(METRICS_DATASET)
        if metrics.empty:
            return pd.DataFrame()
        table = _strip_provenance(metrics)

        fundamentals = self._read_latest(FUNDAMENTALS_DATASET)
        if not fundamentals.empty:
            funds = _strip_provenance(fundamentals)
            table = table.merge(funds, on="symbol", how="left", suffixes=("", "_fund"))
        return table

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


@lru_cache(maxsize=1)
def default_read_api() -> ReadAPI:
    """Build a ReadAPI backed by the Parquet store at the configured data dir.

    Cached so the frontend can call the module-level helpers freely. Importing
    storage/config here keeps that dependency out of the frontend.
    """
    from src.config import get_config
    from src.storage.parquet_store import ParquetStore

    return ReadAPI(ParquetStore(get_config().data_dir))


def get_universe() -> pd.DataFrame:
    """Module-level convenience: current universe via the default store."""
    return default_read_api().get_universe()


def get_table() -> pd.DataFrame:
    """Module-level convenience: dashboard grid via the default store."""
    return default_read_api().get_table()


def get_tearsheet(ticker: str) -> dict[str, object]:
    """Module-level convenience: single-ticker tearsheet via the default store."""
    return default_read_api().get_tearsheet(ticker)


def get_market_overview() -> dict[str, object]:
    """Module-level convenience: market overview via the default store."""
    return default_read_api().get_market_overview()
