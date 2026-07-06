"""Index aggregates job (Phase D): compute per-index comparison tables -> ObjectStore.

Reads the master universe, latest metrics, latest fundamentals, and the ETF-proxy
price history, then writes two small tables the comparison page consumes:
  - `index_aggregates` — one row per index (construction + quantamental +
    performance scalars).
  - `index_sectors`    — index_id x sector x weight (long format) for sector bars.

Pure math lives in compute/index_aggregates.py; this job is the wiring.
Requires an ObjectStore backend (DATA_URI set).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from src.compute import index_aggregates as agg
from src.config import get_config
from src.data_sources.indices import INDEX_METADATA
from src.jobs.common import load_master_universe
from src.models import (
    DataSource,
    IndexAggregateRow,
    IndexSectorRow,
    Provenance,
    Snapshot,
)
from src.storage.base import Storage

logger = logging.getLogger(__name__)

AGGREGATES_DATASET = "index_aggregates"
SECTORS_DATASET = "index_sectors"


def _read_or_empty(storage: Storage, dataset: str) -> pd.DataFrame:
    try:
        return storage.read_latest(dataset)
    except FileNotFoundError:
        return pd.DataFrame()


def run_index_aggregates_refresh(*, storage: Storage) -> dict[str, object]:
    """Compute and persist per-index construction/quantamental/performance tables."""
    logger.info("Index aggregates refresh start")
    tickers = load_master_universe(storage)
    metrics = _read_or_empty(storage, "metrics")
    fundamentals = _read_or_empty(storage, "fundamentals")

    # ETF-proxy adjusted-close series (proxies are priced in the prices dataset).
    proxies = [meta["etf"] for meta in INDEX_METADATA.values()]
    etf_prices = storage.read_dataset(
        "prices", columns=["symbol", "date", "adj_close"],
        where="symbol IN (" + ", ".join(f"'{p}'" for p in proxies) + ")",
    )
    etf_series: dict[str, pd.Series] = {}
    if not etf_prices.empty:
        etf_prices = etf_prices.copy()
        etf_prices["date"] = pd.to_datetime(etf_prices["date"])
        for sym, grp in etf_prices.groupby("symbol"):
            etf_series[str(sym)] = grp.sort_values("date").set_index("date")["adj_close"]

    agg_rows: list[IndexAggregateRow] = []
    sector_rows: list[IndexSectorRow] = []
    for index_id, meta in INDEX_METADATA.items():
        members = [t for t in tickers if index_id in t.memberships]
        member_symbols = {t.symbol for t in members}
        etf = etf_series.get(meta["etf"])

        row: dict[str, object] = {"index_id": index_id, "name": meta["name"], "etf": meta["etf"]}
        row.update(agg.construction(members))
        row.update(agg.quantamental(metrics, fundamentals, member_symbols))
        row.update(agg.performance(etf) if etf is not None else {})
        agg_rows.append(IndexAggregateRow(**row))

        for sector, weight in agg.sector_weights(members).items():
            sector_rows.append(IndexSectorRow(index_id=index_id, sector=sector, weight=weight))

    provenance = Provenance(source=DataSource.COMPUTED, fetched_at=datetime.now(timezone.utc),
                            notes="per-index construction/quantamental/performance aggregates")
    storage.write_snapshot(AGGREGATES_DATASET, Snapshot(provenance=provenance, rows=agg_rows))
    storage.write_snapshot(SECTORS_DATASET, Snapshot(provenance=provenance, rows=sector_rows))

    summary: dict[str, object] = {
        "indices": len(agg_rows),
        "sector_rows": len(sector_rows),
        "with_performance": sum(1 for r in agg_rows if getattr(r, "perf_return_1y", None) is not None),
    }
    logger.info("Index aggregates refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled aggregates job (runs after prices/fundamentals)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from src.storage.factory import get_storage

    run_index_aggregates_refresh(storage=get_storage(get_config()))


if __name__ == "__main__":
    main()
