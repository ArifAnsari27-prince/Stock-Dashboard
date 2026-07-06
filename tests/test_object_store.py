"""Tests for the DuckDB object store (local backend; R2 path is the same code)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.models import DataSource, MetricsRow, PriceBar, Provenance, Snapshot
from src.storage.object_store import ObjectStore


def _prov(source=DataSource.COMPUTED) -> Provenance:
    return Provenance(source=source, fetched_at=datetime(2026, 6, 30, tzinfo=timezone.utc))


def _price_snapshot(symbols, d: date) -> Snapshot[PriceBar]:
    return Snapshot[PriceBar](
        provenance=_prov(DataSource.MASSIVE),
        rows=[
            PriceBar(symbol=s, date=d, open=1, high=2, low=0.5, close=1.5,
                     adj_close=1.5, volume=1000)
            for s in symbols
        ],
    )


@pytest.fixture
def store(tmp_path):
    return ObjectStore(str(tmp_path))


def test_write_and_read_latest(store):
    snap = Snapshot[MetricsRow](
        provenance=_prov(),
        rows=[MetricsRow(symbol="AAPL", latest_close=200.0),
              MetricsRow(symbol="MSFT", latest_close=400.0)],
    )
    store.write_latest("metrics", snap)
    df = store.read_latest("metrics")
    assert set(df["symbol"]) == {"AAPL", "MSFT"}
    # Provenance flattened into columns, like ParquetStore.
    assert (df["_source"] == "computed").all()
    assert "_fetched_at" in df.columns


def test_write_latest_overwrites(store):
    store.write_latest("metrics", Snapshot[MetricsRow](provenance=_prov(),
                       rows=[MetricsRow(symbol="AAPL")]))
    store.write_latest("metrics", Snapshot[MetricsRow](provenance=_prov(),
                       rows=[MetricsRow(symbol="MSFT")]))
    df = store.read_latest("metrics")
    assert set(df["symbol"]) == {"MSFT"}  # replaced, not appended


def test_partitions_append_and_query(store):
    # Two daily partitions accumulate history without rewriting each other.
    store.write_partition("prices", "date=2026-06-29", _price_snapshot(["AAPL", "MSFT"], date(2026, 6, 29)))
    store.write_partition("prices", "date=2026-06-30", _price_snapshot(["AAPL", "MSFT"], date(2026, 6, 30)))

    allrows = store.read_dataset("prices")
    assert len(allrows) == 4  # 2 symbols x 2 days

    # Column projection + predicate pushdown (the price-history read path).
    aapl = store.read_dataset("prices", columns=["symbol", "date", "close"], where="symbol = 'AAPL'")
    assert set(aapl.columns) == {"symbol", "date", "close"}
    assert len(aapl) == 2
    assert set(aapl["symbol"]) == {"AAPL"}


def test_repartition_overwrites_only_that_partition(store):
    store.write_partition("prices", "date=2026-06-30", _price_snapshot(["AAPL"], date(2026, 6, 30)))
    store.write_partition("prices", "date=2026-06-29", _price_snapshot(["AAPL", "MSFT"], date(2026, 6, 29)))
    # Re-run of one day replaces only it.
    store.write_partition("prices", "date=2026-06-30", _price_snapshot(["AAPL", "MSFT", "NVDA"], date(2026, 6, 30)))
    assert len(store.read_dataset("prices")) == 2 + 3


def test_read_latest_missing_raises(store):
    with pytest.raises(FileNotFoundError):
        store.read_latest("nope")


def test_read_dataset_missing_is_empty(store):
    assert store.read_dataset("nope").empty
