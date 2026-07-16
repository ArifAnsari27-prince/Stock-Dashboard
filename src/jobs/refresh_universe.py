"""Weekly universe refresh job (build steps 2/9).

Fetches the current Nasdaq-100 constituents and writes a fresh `universe`
snapshot. Intentionally has no SEC dependency (needs no secret): CIKs are
re-added by the daily fundamentals/filings jobs via `ensure_universe_ciks`, so a
freshly fetched universe without CIKs is fine — the enrichment is idempotent.
"""

from __future__ import annotations

import logging

from src.config import get_config
from src.data_sources import universe as universe_module
from src.jobs.common import UNIVERSE_DATASET, UniverseFetchFn
from src.storage.base import Storage

logger = logging.getLogger(__name__)


def run_universe_refresh(
    *,
    storage: Storage,
    fetch_universe_fn: UniverseFetchFn = universe_module.fetch_universe,
) -> dict[str, object]:
    """Fetch the universe and write a snapshot; return a summary dict."""
    logger.info("Universe refresh start")
    snapshot = fetch_universe_fn()
    path = storage.write_snapshot(UNIVERSE_DATASET, snapshot)
    summary: dict[str, object] = {"constituents": len(snapshot.rows), "path": str(path)}
    logger.info("Universe refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled weekly job."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from src.storage.parquet_store import ParquetStore

    storage = ParquetStore(get_config().data_dir)
    run_universe_refresh(storage=storage)


if __name__ == "__main__":
    main()
