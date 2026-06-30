"""Tests for the read API (build step 8). No network; reads a temp ParquetStore."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.api.read_api import ReadAPI
from src.models import (
    DataSource,
    Filing,
    FilingType,
    Fundamentals,
    MetricsRow,
    Provenance,
    Snapshot,
    Ticker,
)
from src.storage.parquet_store import ParquetStore


def _prov(source: DataSource) -> Provenance:
    return Provenance(source=source, fetched_at=datetime(2026, 6, 30, tzinfo=timezone.utc))


@pytest.fixture
def store(tmp_path):
    s = ParquetStore(tmp_path)
    s.write_snapshot(
        "universe",
        Snapshot[Ticker](
            provenance=_prov(DataSource.NASDAQ_INDEX),
            rows=[Ticker(symbol="AAPL", name="Apple Inc.", cik="0000320193"),
                  Ticker(symbol="MSFT", name="Microsoft Corp", cik="0000789019")],
        ),
    )
    s.write_snapshot(
        "metrics",
        Snapshot[MetricsRow](
            provenance=_prov(DataSource.COMPUTED),
            rows=[
                MetricsRow(symbol="AAPL", name="Apple Inc.", as_of=date(2026, 6, 29),
                           latest_close=200.0, return_1d=0.01, price_vs_sma_50=0.05,
                           price_vs_sma_200=0.10, rsi_14=60.0),
                MetricsRow(symbol="MSFT", name="Microsoft Corp", as_of=date(2026, 6, 29),
                           latest_close=400.0, return_1d=-0.02, price_vs_sma_50=-0.03,
                           price_vs_sma_200=0.08, rsi_14=45.0),
            ],
        ),
    )
    s.write_snapshot(
        "fundamentals",
        Snapshot[Fundamentals](
            provenance=_prov(DataSource.SEC_EDGAR),
            rows=[
                Fundamentals(symbol="AAPL", revenue=4.0e11, net_margin=0.27),
                Fundamentals(symbol="MSFT", revenue=2.8e11, net_margin=0.36),
            ],
        ),
    )
    s.write_snapshot(
        "filings",
        Snapshot[Filing](
            provenance=_prov(DataSource.SEC_EDGAR),
            rows=[
                Filing(symbol="AAPL", form=FilingType.FORM_10K,
                       filed_date=date(2025, 10, 31), url="https://x/aapl-10k"),
                Filing(symbol="MSFT", form=FilingType.FORM_10Q,
                       filed_date=date(2026, 4, 1), url="https://x/msft-10q"),
            ],
        ),
    )
    return s


def test_get_universe(store):
    df = ReadAPI(store).get_universe()
    assert set(df["symbol"]) == {"AAPL", "MSFT"}
    # Provenance columns are stripped from the public view.
    assert not any(c.startswith("_") for c in df.columns)


def test_get_table_merges_metrics_and_fundamentals(store):
    table = ReadAPI(store).get_table()
    assert set(table["symbol"]) == {"AAPL", "MSFT"}
    aapl = table[table["symbol"] == "AAPL"].iloc[0]
    assert aapl["rsi_14"] == 60.0  # from metrics
    assert aapl["net_margin"] == pytest.approx(0.27)  # from fundamentals
    assert aapl["revenue"] == pytest.approx(4.0e11)
    assert not any(c.startswith("_") for c in table.columns)


def test_get_tearsheet(store):
    sheet = ReadAPI(store).get_tearsheet("aapl")  # case-insensitive
    assert sheet["found"] is True
    assert sheet["symbol"] == "AAPL"
    assert sheet["data"]["latest_close"] == 200.0
    assert sheet["data"]["net_margin"] == pytest.approx(0.27)
    assert len(sheet["filings"]) == 1
    assert sheet["filings"][0]["url"] == "https://x/aapl-10k"
    assert "disclaimer" in sheet["provenance"]


def test_get_tearsheet_unknown_ticker(store):
    sheet = ReadAPI(store).get_tearsheet("ZZZZ")
    assert sheet["found"] is False
    assert sheet["data"] == {}
    assert sheet["filings"] == []


def test_get_market_overview(store):
    ov = ReadAPI(store).get_market_overview()
    assert ov["constituents"] == 2
    assert ov["as_of"] == date(2026, 6, 29)
    # AAPL above 50D, MSFT below -> 50% above.
    assert ov["pct_above_sma_50"] == 50.0
    # Both above 200D -> 100%.
    assert ov["pct_above_sma_200"] == 100.0
    assert ov["advancers"] == 1 and ov["decliners"] == 1
    assert ov["median_rsi_14"] == 52.5  # median(60, 45)
    assert "disclaimer" in ov


def test_empty_store_degrades_gracefully(tmp_path):
    api = ReadAPI(ParquetStore(tmp_path))
    assert api.get_universe().empty
    assert api.get_table().empty
    assert api.get_market_overview() == {"constituents": 0}
    sheet = api.get_tearsheet("AAPL")
    assert sheet["found"] is False and sheet["filings"] == []
