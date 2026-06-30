"""Parquet implementation of the Storage interface (CLAUDE.md §2).

Layout on disk::

    <data_dir>/<name>/<name>_<UTC-timestamp>.parquet

Each file is one snapshot. The snapshot's provenance is flattened into reserved
columns (prefixed `_`) on every row, so an individual Parquet file is fully
self-describing without any sidecar metadata:

    _source       provenance source (e.g. "yfinance")
    _fetched_at   UTC fetch/compute timestamp
    _disclaimer   "prototype / delayed / unofficial source"
    _notes        optional free text (may be null)

`read_latest` selects the lexicographically greatest filename, which is also the
chronologically latest because timestamps are zero-padded and in UTC.

This module performs local filesystem I/O only — no network access.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.models import Snapshot
from src.storage.base import Storage

# Reserved provenance column names attached to every row.
_PROVENANCE_COLUMNS = ("_source", "_fetched_at", "_disclaimer", "_notes")

# Filename timestamp format: zero-padded, UTC, sorts chronologically as text.
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


class ParquetStore(Storage):
    """Stores dataset snapshots as Parquet files under a base directory."""

    def __init__(self, base_dir: Path | str) -> None:
        """Create a store rooted at `base_dir` (typically Config.data_dir)."""
        self.base_dir = Path(base_dir)

    def _dataset_dir(self, name: str) -> Path:
        """Return (without creating) the directory holding snapshots for `name`."""
        return self.base_dir / name

    def _snapshot_to_frame(self, snapshot: Snapshot) -> pd.DataFrame:
        """Flatten a snapshot's rows + provenance into a single DataFrame."""
        df = pd.DataFrame([row.model_dump() for row in snapshot.rows])
        prov = snapshot.provenance
        # Assign provenance to every row (broadcasts even when df is empty).
        df["_source"] = prov.source.value
        df["_fetched_at"] = pd.Timestamp(prov.fetched_at)
        df["_disclaimer"] = prov.disclaimer
        df["_notes"] = prov.notes
        return df

    def write_snapshot(self, name: str, snapshot: Snapshot) -> Path:
        """Write `snapshot` as a new timestamped Parquet file; return its path."""
        dataset_dir = self._dataset_dir(name)
        dataset_dir.mkdir(parents=True, exist_ok=True)

        df = self._snapshot_to_frame(snapshot)
        timestamp = snapshot.provenance.fetched_at.strftime(_TIMESTAMP_FORMAT)
        path = dataset_dir / f"{name}_{timestamp}.parquet"
        df.to_parquet(path, engine="pyarrow", index=False)
        return path

    def _snapshot_files(self, name: str) -> list[Path]:
        """Return this dataset's snapshot files, sorted oldest -> newest."""
        dataset_dir = self._dataset_dir(name)
        if not dataset_dir.is_dir():
            return []
        return sorted(dataset_dir.glob(f"{name}_*.parquet"))

    def read_latest(self, name: str) -> pd.DataFrame:
        """Read the most recent snapshot for `name`."""
        files = self._snapshot_files(name)
        if not files:
            raise FileNotFoundError(f"No snapshots found for dataset '{name}'.")
        return pd.read_parquet(files[-1], engine="pyarrow")

    def read_history(self, name: str) -> pd.DataFrame:
        """Read and concatenate all snapshots for `name` (empty if none)."""
        files = self._snapshot_files(name)
        if not files:
            return pd.DataFrame()
        frames = [pd.read_parquet(f, engine="pyarrow") for f in files]
        return pd.concat(frames, ignore_index=True)
