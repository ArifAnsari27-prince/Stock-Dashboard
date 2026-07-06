"""Tests for index aggregates: pure compute (Phase D) + the aggregates job."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.compute import index_aggregates as agg
from src.data_sources.indices import NASDAQ100, SP500
from src.jobs.refresh_index_aggregates import run_index_aggregates_refresh
from src.models import (
    DataSource, Fundamentals, MetricsRow, PriceBar, Provenance, Snapshot, Ticker,
)
from src.storage.object_store import ObjectStore


def _t(sym, sector, cap, memberships):
    return Ticker(symbol=sym, sector=sector, market_cap=cap, memberships=memberships)


def test_sector_weights_and_construction():
    members = [_t("A", "Tech", 600, (SP500,)), _t("B", "Tech", 300, (SP500,)),
               _t("C", "Energy", 100, (SP500,))]
    sw = agg.sector_weights(members)
    assert sw["Tech"] == pytest.approx(0.9)
    assert sw["Energy"] == pytest.approx(0.1)
    c = agg.construction(members)
    assert c["constituents"] == 3
    assert c["total_market_cap"] == pytest.approx(1000)
    assert c["top10_weight"] == pytest.approx(1.0)  # only 3 names
    assert c["effective_n"] == pytest.approx(1.0 / (0.6**2 + 0.3**2 + 0.1**2))


def test_quantamental():
    metrics = pd.DataFrame({"symbol": ["A", "B"], "market_cap": [600.0, 400.0],
                            "price_vs_sma_200": [0.1, -0.2], "return_ytd": [0.1, 0.3],
                            "return_1y": [0.2, 0.4], "rsi_14": [60.0, 40.0]})
    funds = pd.DataFrame({"symbol": ["A", "B"], "net_income": [60.0, 40.0],
                          "revenue": [600.0, 400.0], "net_margin": [0.1, 0.1],
                          "roe": [0.2, 0.3], "revenue_growth": [0.15, 0.25]})
    q = agg.quantamental(metrics, funds, {"A", "B"})
    assert q["agg_pe"] == pytest.approx(1000 / 100)  # sum(mcap)/sum(ni)
    assert q["agg_ps"] == pytest.approx(1.0)
    assert q["median_net_margin"] == pytest.approx(0.1)
    assert q["median_roe"] == pytest.approx(0.25)
    assert q["breadth_above_200d"] == pytest.approx(50.0)  # 1 of 2 above


def test_performance():
    idx = pd.bdate_range("2025-01-01", periods=260)
    prices = pd.Series(100.0 * np.exp(0.001 * np.arange(260)), index=idx)
    p = agg.performance(prices)
    assert p["perf_return_1y"] is not None and p["perf_return_1y"] > 0
    assert p["perf_max_drawdown"] == pytest.approx(0.0, abs=1e-9)  # monotonic up


def test_aggregates_job(tmp_path):
    store = ObjectStore(str(tmp_path))
    prov = Provenance(source=DataSource.NASDAQ_INDEX, fetched_at=datetime.now(timezone.utc))
    store.write_snapshot("universe", Snapshot[Ticker](provenance=prov, rows=[
        _t("AAPL", "Information Technology", 3e12, (NASDAQ100, SP500)),
        _t("JPM", "Financials", 5e11, (SP500,)),
    ]))
    store.write_snapshot("metrics", Snapshot[MetricsRow](provenance=prov, rows=[
        MetricsRow(symbol="AAPL", market_cap=3e12, price_vs_sma_200=0.1, return_ytd=0.1, rsi_14=55.0),
        MetricsRow(symbol="JPM", market_cap=5e11, price_vs_sma_200=-0.05, return_ytd=0.2, rsi_14=48.0),
    ]))
    store.write_snapshot("fundamentals", Snapshot[Fundamentals](provenance=prov, rows=[
        Fundamentals(symbol="AAPL", net_income=1e11, revenue=4e11, net_margin=0.25, roe=1.5),
        Fundamentals(symbol="JPM", net_income=5e10, revenue=1.5e11, net_margin=0.33, roe=0.15),
    ]))
    # ETF proxy history for QQQ (nasdaq100) + IVV (sp500).
    idx = pd.bdate_range("2025-06-01", periods=30)
    for etf, base in (("QQQ", 400.0), ("IVV", 500.0)):
        for i, ts in enumerate(idx):
            store.write_partition("prices", f"date={ts.date().isoformat()}_{etf}",
                Snapshot[PriceBar](provenance=prov, rows=[PriceBar(
                    symbol=etf, date=ts.date(), open=base+i, high=base+i+1, low=base+i-1,
                    close=base+i, adj_close=base+i, volume=1000)]))

    summary = run_index_aggregates_refresh(storage=store)
    assert summary["indices"] == 4  # nasdaq100/sp500/russell1000/russell3000

    aggs = store.read_latest("index_aggregates")
    by_id = {r["index_id"]: r for _, r in aggs.iterrows()}
    assert by_id["nasdaq100"]["constituents"] == 1  # AAPL
    assert by_id["sp500"]["constituents"] == 2  # AAPL, JPM
    assert by_id["sp500"]["agg_pe"] == pytest.approx(3.5e12 / 1.5e11)
    # Performance came from the QQQ/IVV proxy series.
    assert pd.notna(by_id["nasdaq100"]["perf_return_1m"])

    sectors = store.read_latest("index_sectors")
    sp_sectors = sectors[sectors["index_id"] == "sp500"]
    assert set(sp_sectors["sector"]) == {"Information Technology", "Financials"}
