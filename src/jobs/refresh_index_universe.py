"""Weekly master-universe refresh (Phase D2): fetch all index memberships -> ObjectStore.

Fetches the multi-index master universe (Nasdaq-100 + S&P 500 + Russell 1000/3000)
and overwrites the `universe` table. The daily jobs read this; they also bootstrap
it on first run, but this keeps memberships/sectors/market caps fresh weekly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.config import get_config
from src.data_sources.indices import fetch_master_universe
from src.jobs.common import write_job_summary
from src.storage.base import Storage

logger = logging.getLogger(__name__)

UNIVERSE_DATASET = "universe"


def run_index_universe_refresh(*, storage: Storage) -> dict[str, object]:
    """Fetch and persist the master universe; return a summary dict.

    Besides overwriting the `latest` table, a dated point-in-time partition
    (`universe/dt=YYYY-MM-DD/`) is written when the backend supports partitions.
    These PIT snapshots are the raw material for survivorship-safe backtests —
    membership history cannot be reconstructed later, so capture starts now
    (docs/ARCHITECTURE_AUDIT.md §13).
    """
    logger.info("Master universe refresh start")
    snapshot = fetch_master_universe()
    storage.write_snapshot(UNIVERSE_DATASET, snapshot)

    pit_partition = None
    if hasattr(storage, "write_partition"):
        today = datetime.now(timezone.utc).date().isoformat()
        pit_partition = f"dt={today}"
        storage.write_partition(UNIVERSE_DATASET, pit_partition, snapshot)

    summary: dict[str, object] = {
        "constituents": len(snapshot.rows),
        "pit_partition": pit_partition or "n/a (backend has no partitions)",
    }
    logger.info("Master universe refresh complete: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from src.storage.factory import get_storage

    summary = run_index_universe_refresh(storage=get_storage(get_config()))
    write_job_summary("Master universe refresh", summary)


if __name__ == "__main__":
    main()
