"""Daily fundamentals refresh job: universe -> CIK enrich -> EDGAR -> storage (build step 7).

Reads the stored universe, enriches it with CIKs (persisting once), fetches and
normalizes SEC company-facts via the injected FundamentalsSource, and writes a
"fundamentals" snapshot. Dependencies are injected for offline testing; `main()`
builds the real EDGAR-backed implementations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.config import get_config
from src.data_sources import universe as universe_module
from src.data_sources.base import FundamentalsSource
from src.jobs.common import UniverseFetchFn, ensure_universe_ciks, load_universe
from src.storage.base import Storage

logger = logging.getLogger(__name__)

FUNDAMENTALS_DATASET = "fundamentals"

CikMapFn = Callable[[], dict[str, str]]


def run_fundamentals_refresh(
    *,
    fundamentals_source: FundamentalsSource,
    fetch_cik_map: CikMapFn,
    storage: Storage,
    fetch_universe_fn: UniverseFetchFn = universe_module.fetch_universe,
) -> dict[str, object]:
    """Run one fundamentals refresh and return a summary dict."""
    logger.info("Fundamentals refresh start")
    tickers = load_universe(storage, fetch_universe_fn)
    if not tickers:
        logger.error("Universe is empty; nothing to refresh")
        return {"universe": 0, "with_cik": 0, "fundamentals_rows": 0}

    cik_map = fetch_cik_map()
    tickers = ensure_universe_ciks(storage, tickers, cik_map)

    snapshot = fundamentals_source.fetch_fundamentals(tickers)
    path = storage.write_snapshot(FUNDAMENTALS_DATASET, snapshot)

    with_cik = sum(1 for t in tickers if t.cik)
    summary: dict[str, object] = {
        "universe": len(tickers),
        "with_cik": with_cik,
        "fundamentals_rows": len(snapshot.rows),
        "path": str(path),
    }
    logger.info("Fundamentals refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled daily job."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from src.data_sources.edgar import EdgarClient, EdgarFundamentalsSource
    from src.storage.parquet_store import ParquetStore

    config = get_config()
    storage = ParquetStore(config.data_dir)
    client = EdgarClient()
    run_fundamentals_refresh(
        fundamentals_source=EdgarFundamentalsSource(client.get_json),
        fetch_cik_map=client.fetch_cik_map,
        storage=storage,
    )


if __name__ == "__main__":
    main()
