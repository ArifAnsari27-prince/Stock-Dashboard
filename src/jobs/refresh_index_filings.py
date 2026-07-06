"""Multi-index filing-links job (Phase C): full master universe -> EDGAR -> ObjectStore.

Fetches the latest 10-K/10-Q/8-K/Form 4 links for every constituent of the master
universe (~3,000 names) and writes a `filings` latest table. Also produces the
per-company latest-financial-filing dates that the fundamentals job reads to skip
unchanged companies. Requires an ObjectStore backend (DATA_URI set).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.config import get_config
from src.data_sources import indices as indices_module
from src.data_sources.base import FilingsSource
from src.jobs.common import ensure_universe_ciks, load_master_universe
from src.storage.base import Storage

logger = logging.getLogger(__name__)

FILINGS_DATASET = "filings"

CikMapFn = Callable[[], dict[str, str]]
UniverseFetchFn = Callable[[], object]


def run_index_filings_refresh(
    *,
    filings_source: FilingsSource,
    fetch_cik_map: CikMapFn,
    storage: Storage,
    fetch_universe_fn: UniverseFetchFn = indices_module.fetch_master_universe,
) -> dict[str, object]:
    """Run one filings refresh over the master universe; return a summary dict."""
    logger.info("Index filings refresh start")
    tickers = load_master_universe(storage, fetch_universe_fn)
    if not tickers:
        return {"universe": 0, "with_cik": 0, "filings_rows": 0}

    tickers = ensure_universe_ciks(storage, tickers, fetch_cik_map())
    snapshot = filings_source.fetch_filings(tickers)
    storage.write_snapshot(FILINGS_DATASET, snapshot)

    with_cik = sum(1 for t in tickers if t.cik)
    summary: dict[str, object] = {
        "universe": len(tickers),
        "with_cik": with_cik,
        "filings_rows": len(snapshot.rows),
    }
    logger.info("Index filings refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled daily filings job."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from src.data_sources.edgar import EdgarClient, EdgarFilingsSource
    from src.storage.factory import get_storage

    config = get_config()
    client = EdgarClient()
    run_index_filings_refresh(
        filings_source=EdgarFilingsSource(client.get_json),
        fetch_cik_map=client.fetch_cik_map,
        storage=get_storage(config),
    )


if __name__ == "__main__":
    main()
