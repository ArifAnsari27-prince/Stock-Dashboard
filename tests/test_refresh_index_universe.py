"""Master-universe refresh job: latest overwrite + point-in-time dt= partition."""

from __future__ import annotations

from datetime import datetime, timezone

from src.jobs import refresh_index_universe as job
from src.models import DataSource, Provenance, Snapshot, Ticker
from src.storage.object_store import ObjectStore
from src.storage.parquet_store import ParquetStore


def _snapshot() -> Snapshot[Ticker]:
    return Snapshot[Ticker](
        provenance=Provenance(
            source=DataSource.NASDAQ_INDEX, fetched_at=datetime.now(timezone.utc)
        ),
        rows=[Ticker(symbol="AAPL", memberships=("nasdaq100", "sp500"))],
    )


def test_writes_latest_and_pit_partition(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "fetch_master_universe", _snapshot)
    store = ObjectStore(str(tmp_path))

    summary = job.run_index_universe_refresh(storage=store)

    assert summary["constituents"] == 1
    assert str(summary["pit_partition"]).startswith("dt=")
    assert not store.read_latest("universe").empty
    pit = store.read_dataset("universe")
    assert len(pit) == 1  # the dt= partition is readable on its own


def test_backend_without_partitions_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "fetch_master_universe", _snapshot)
    store = ParquetStore(tmp_path)

    summary = job.run_index_universe_refresh(storage=store)
    assert summary["constituents"] == 1
    assert "n/a" in str(summary["pit_partition"])
