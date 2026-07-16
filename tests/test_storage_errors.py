"""ObjectStore error separation: missing data is empty, real errors surface."""

from __future__ import annotations

import duckdb
import pytest

from src.storage.object_store import ObjectStore


def test_missing_dataset_returns_empty(tmp_path):
    store = ObjectStore(str(tmp_path))
    df = store.read_dataset("never_written")
    assert df.empty


def test_corrupt_partition_raises_instead_of_empty(tmp_path):
    dataset_dir = tmp_path / "prices" / "date=2026-07-01"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "data.parquet").write_bytes(b"this is not parquet")

    store = ObjectStore(str(tmp_path))
    with pytest.raises(duckdb.Error):
        store.read_dataset("prices")
