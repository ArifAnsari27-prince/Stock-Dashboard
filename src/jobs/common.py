"""Shared helpers for refresh jobs (build steps 5/7).

Universe loading (with first-run bootstrap) and CIK enrichment live here so the
price, fundamentals, and filings jobs share one implementation.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timezone

import pandas as pd

from src.data_sources import universe as universe_module
from src.data_sources.edgar import enrich_tickers_with_cik
from src.models import DataSource, Provenance, Snapshot, Ticker
from src.storage.base import Storage

logger = logging.getLogger(__name__)

UNIVERSE_DATASET = "universe"

UniverseFetchFn = Callable[[], Snapshot[Ticker]]


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
