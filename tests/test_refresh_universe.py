"""Test for the weekly universe refresh job (build step 9)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.jobs.refresh_universe import run_universe_refresh
from src.models import DataSource, Provenance, Snapshot, Ticker
from src.storage.parquet_store import ParquetStore


def test_run_universe_refresh_writes_snapshot(tmp_path):
    store = ParquetStore(tmp_path)

    def fake_fetch():
        return Snapshot[Ticker](
            provenance=Provenance(
                source=DataSource.NASDAQ_INDEX,
                fetched_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
            ),
            rows=[Ticker(symbol="AAPL", name="Apple Inc."),
                  Ticker(symbol="MSFT", name="Microsoft Corp")],
        )

    summary = run_universe_refresh(storage=store, fetch_universe_fn=fake_fetch)
    assert summary["constituents"] == 2
    assert set(store.read_latest("universe")["symbol"]) == {"AAPL", "MSFT"}
