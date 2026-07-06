"""Tests for multi-index read-API methods over an ObjectStore (Phase E)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.api.read_api import ReadAPI
from src.models import (
    DataSource, Fundamentals, IndexAggregateRow, IndexSectorRow, MetricsRow,
    PriceBar, Provenance, Snapshot,
)
from src.storage.object_store import ObjectStore


def _prov(src=DataSource.COMPUTED):
    return Provenance(source=src, fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc))


@pytest.fixture
def store(tmp_path):
    s = ObjectStore(str(tmp_path))
    s.write_latest("metrics", Snapshot[MetricsRow](provenance=_prov(), rows=[
        MetricsRow(symbol="AAPL", market_cap=3e12, return_1d=0.01, in_nasdaq100=True, in_sp500=True),
        MetricsRow(symbol="JPM", market_cap=5e11, return_1d=-0.01, in_nasdaq100=False, in_sp500=True),
    ]))
    s.write_latest("fundamentals", Snapshot[Fundamentals](provenance=_prov(DataSource.SEC_EDGAR), rows=[
        Fundamentals(symbol="AAPL", revenue=4e11, net_margin=0.25),
        Fundamentals(symbol="JPM", revenue=1.5e11, net_margin=0.33),
    ]))
    s.write_latest("index_aggregates", Snapshot[IndexAggregateRow](provenance=_prov(), rows=[
        IndexAggregateRow(index_id="nasdaq100", name="Nasdaq-100", etf="QQQ", constituents=1.0, agg_pe=30.0),
        IndexAggregateRow(index_id="sp500", name="S&P 500", etf="IVV", constituents=2.0, agg_pe=22.0),
    ]))
    s.write_latest("index_sectors", Snapshot[IndexSectorRow](provenance=_prov(), rows=[
        IndexSectorRow(index_id="sp500", sector="Information Technology", weight=0.6),
        IndexSectorRow(index_id="sp500", sector="Financials", weight=0.4),
    ]))
    # Partitioned prices for AAPL + the two ETF proxies.
    idx = pd.bdate_range("2026-06-01", periods=20)
    for i, ts in enumerate(idx):
        rows = [PriceBar(symbol=s_, date=ts.date(), open=100+i, high=101+i, low=99+i,
                         close=100+i, adj_close=100+i, volume=1000) for s_ in ("AAPL", "QQQ", "IVV")]
        s.write_partition("prices", f"date={ts.date().isoformat()}", Snapshot[PriceBar](provenance=_prov(DataSource.MASSIVE), rows=rows))
    return s


def test_get_table_index_filter(store):
    api = ReadAPI(store)
    assert set(api.get_table()["symbol"]) == {"AAPL", "JPM"}
    n100 = api.get_table(index="nasdaq100")
    assert set(n100["symbol"]) == {"AAPL"}  # JPM not in nasdaq100
    sp = api.get_table(index="sp500")
    assert set(sp["symbol"]) == {"AAPL", "JPM"}
    # fundamentals merged in
    assert "net_margin" in sp.columns


def test_get_indices(store):
    ids = ReadAPI(store).get_indices()
    by = {i["index_id"]: i for i in ids}
    assert set(by) == {"nasdaq100", "sp500"}
    assert by["sp500"]["etf"] == "IVV"


def test_get_index_comparison(store):
    comp = ReadAPI(store).get_index_comparison()
    agg_by = {a["index_id"]: a for a in comp["aggregates"]}
    assert agg_by["sp500"]["agg_pe"] == 22.0
    assert agg_by["sp500"]["constituents"] == 2.0
    sp_sectors = [s for s in comp["sectors"] if s["index_id"] == "sp500"]
    assert {s["sector"] for s in sp_sectors} == {"Information Technology", "Financials"}
    assert "disclaimer" in comp["provenance"]


def test_get_index_performance_rebased(store):
    perf = ReadAPI(store).get_index_performance(rebased=True)
    assert set(perf.columns) == {"nasdaq100", "sp500"}  # mapped from QQQ/IVV
    assert perf.iloc[0]["nasdaq100"] == pytest.approx(100.0)  # rebased to 100
    assert perf.iloc[-1]["nasdaq100"] > 100.0  # rose over the window


def test_get_price_history_over_partitions(store):
    hist = ReadAPI(store).get_price_history("AAPL")
    assert len(hist) == 20
    assert hist.index.is_monotonic_increasing
    for col in ("open", "close", "adj_close", "sma_20"):
        assert col in hist.columns
