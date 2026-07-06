"""Tests for multi-index EDGAR jobs (Phase C): incremental fundamentals + filings."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data_sources.base import FilingsSource, FundamentalsSource
from src.data_sources.indices import NASDAQ100, RUSSELL3000, SP500
from src.jobs.refresh_index_filings import run_index_filings_refresh
from src.jobs.refresh_index_fundamentals import run_index_fundamentals_refresh
from src.models import (
    DataSource, Filing, FilingType, Fundamentals, Provenance, Snapshot, Ticker,
)
from src.storage.object_store import ObjectStore


def _prov(src=DataSource.SEC_EDGAR):
    return Provenance(source=src, fetched_at=datetime.now(timezone.utc))


def _universe():
    return Snapshot[Ticker](
        provenance=_prov(DataSource.NASDAQ_INDEX),
        rows=[
            Ticker(symbol="AAPL", cik="0000320193", memberships=(NASDAQ100, SP500, RUSSELL3000)),
            Ticker(symbol="MSFT", cik="0000789019", memberships=(SP500, RUSSELL3000)),
            Ticker(symbol="NVDA", cik="0001045810", memberships=(NASDAQ100, SP500, RUSSELL3000)),
        ],
    )


def _filings_snapshot(dates: dict[str, str]):
    rows = []
    for sym, d in dates.items():
        rows.append(Filing(symbol=sym, form=FilingType.FORM_10K,
                           filed_date=date.fromisoformat(d), url=f"https://x/{sym}"))
    return Snapshot[Filing](provenance=_prov(), rows=rows)


class FakeFundamentalsSource(FundamentalsSource):
    def __init__(self):
        self.calls: list[list[str]] = []

    @property
    def source(self):
        return DataSource.SEC_EDGAR

    def fetch_fundamentals(self, tickers):
        self.calls.append([t.symbol for t in tickers])
        rows = [Fundamentals(symbol=t.symbol, cik=t.cik, revenue=1.0) for t in tickers]
        return Snapshot[Fundamentals](provenance=_prov(), rows=rows)


class FakeFilingsSource(FilingsSource):
    @property
    def source(self):
        return DataSource.SEC_EDGAR

    def fetch_filings(self, tickers):
        rows = [Filing(symbol=t.symbol, cik=t.cik, form=FilingType.FORM_10K,
                       filed_date=date(2026, 5, 1), url=f"https://x/{t.symbol}")
                for t in tickers if t.cik]
        return Snapshot[Filing](provenance=_prov(), rows=rows)


@pytest.fixture
def store(tmp_path):
    s = ObjectStore(str(tmp_path))
    s.write_snapshot("universe", _universe())
    return s


def test_filings_job_writes_master_filings(store):
    summary = run_index_filings_refresh(
        filings_source=FakeFilingsSource(), fetch_cik_map=lambda: {}, storage=store)
    assert summary["filings_rows"] == 3
    assert set(store.read_latest("filings")["symbol"]) == {"AAPL", "MSFT", "NVDA"}


def test_fundamentals_incremental_lifecycle(store):
    src = FakeFundamentalsSource()
    # Seed filing dates (fundamentals reads these to decide staleness).
    store.write_snapshot("filings", _filings_snapshot(
        {"AAPL": "2026-02-01", "MSFT": "2026-01-15", "NVDA": "2026-02-20"}))

    # Run 1: no prior fundamentals -> everything fetched.
    s1 = run_index_fundamentals_refresh(fundamentals_source=src, fetch_cik_map=lambda: {}, storage=store)
    assert s1["changed"] == 3 and s1["refetched"] == 3 and s1["fundamentals_rows"] == 3
    assert src.calls[-1] == ["AAPL", "MSFT", "NVDA"]
    # latest_filing_date stamped for the next comparison.
    fund = store.read_latest("fundamentals")
    aapl = fund[fund["symbol"] == "AAPL"].iloc[0]
    assert str(aapl["latest_filing_date"])[:10] == "2026-02-01"

    # Run 2: no new filings -> nothing refetched, all reused.
    s2 = run_index_fundamentals_refresh(fundamentals_source=src, fetch_cik_map=lambda: {}, storage=store)
    assert s2["changed"] == 0 and s2["refetched"] == 0 and s2["fundamentals_rows"] == 3
    assert len(src.calls) == 1  # source not called again

    # Run 3: MSFT files a new 10-Q -> only MSFT refetched.
    store.write_snapshot("filings", _filings_snapshot(
        {"AAPL": "2026-02-01", "MSFT": "2026-04-30", "NVDA": "2026-02-20"}))
    s3 = run_index_fundamentals_refresh(fundamentals_source=src, fetch_cik_map=lambda: {}, storage=store)
    assert s3["changed"] == 1 and s3["refetched"] == 1
    assert src.calls[-1] == ["MSFT"]
    assert s3["fundamentals_rows"] == 3  # AAPL/NVDA reused, MSFT refreshed


def test_fundamentals_force_full_refetches_all(store):
    src = FakeFundamentalsSource()
    store.write_snapshot("filings", _filings_snapshot({"AAPL": "2026-02-01"}))
    run_index_fundamentals_refresh(fundamentals_source=src, fetch_cik_map=lambda: {}, storage=store)
    s = run_index_fundamentals_refresh(fundamentals_source=src, fetch_cik_map=lambda: {},
                                       storage=store, force_full=True)
    assert s["changed"] == 3  # forced despite no filing changes
