"""Weekly master-universe refresh (Phase D2): fetch all index memberships -> ObjectStore.

Fetches the multi-index master universe (Nasdaq-100 + S&P 500 + Russell 1000/3000)
and overwrites the `universe` table. The daily jobs read this; they also bootstrap
it on first run, but this keeps memberships/sectors/market caps fresh weekly.
"""

from __future__ import annotations

import logging

from src.config import get_config
from src.data_sources.indices import fetch_master_universe
from src.storage.base import Storage

logger = logging.getLogger(__name__)

UNIVERSE_DATASET = "universe"


def run_index_universe_refresh(*, storage: Storage) -> dict[str, object]:
    """Fetch and persist the master universe; return a summary dict."""
    logger.info("Master universe refresh start")
    snapshot = fetch_master_universe()
    storage.write_snapshot(UNIVERSE_DATASET, snapshot)
    summary: dict[str, object] = {"constituents": len(snapshot.rows)}
    logger.info("Master universe refresh complete: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from src.storage.factory import get_storage

    run_index_universe_refresh(storage=get_storage(get_config()))


if __name__ == "__main__":
    main()
