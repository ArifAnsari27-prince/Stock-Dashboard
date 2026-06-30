"""Abstract storage interface (CLAUDE.md §2, §3).

V1 stores Parquet snapshots in a `data/` directory (see parquet_store.py). This
ABC exists so we can later swap to Postgres/Supabase by adding one new
implementation, without touching jobs or the read API.

A "dataset" is identified by a `name` (e.g. "universe", "prices",
"fundamentals"). Each `write_snapshot` call appends a new timestamped snapshot;
history is the accumulation of those snapshots.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

from src.models import Snapshot


class Storage(ABC):
    """Reads and writes provenance-stamped dataset snapshots."""

    @abstractmethod
    def write_snapshot(self, name: str, snapshot: Snapshot) -> Path:
        """Persist `snapshot` under dataset `name`, returning the written path.

        Provenance (source, fetch timestamp, disclaimer) must be persisted
        alongside the rows so the stored artifact is self-describing.
        """

    @abstractmethod
    def read_latest(self, name: str) -> pd.DataFrame:
        """Return the most recent snapshot for `name` as a DataFrame.

        Provenance is included as columns. Raises FileNotFoundError if the
        dataset has no snapshots yet.
        """

    @abstractmethod
    def read_history(self, name: str) -> pd.DataFrame:
        """Return all snapshots for `name` concatenated into one DataFrame.

        Provenance columns distinguish the snapshots. Returns an empty
        DataFrame if the dataset has no snapshots yet.
        """
