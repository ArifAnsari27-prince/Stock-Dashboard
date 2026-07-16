"""Tests for the daily fundamentals + filings refresh jobs (build step 7). No network."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data_sources.base import FilingsSource, FundamentalsSource
from src.jobs.common import ensure_universe_ciks
from src.jobs.refresh_filings import run_filings_refresh
from src.jobs.refresh_fundamentals import run_fundamentals_refresh
from src.models import (
    DataSource,
    Filing,
    FilingType,
    Fundamentals,
    Provenance,
    Snapshot,
    Ticker,
)
from src.storage.parquet_store import ParquetStore

CIK_MAP = {"AAPL": "0000320193", "MSFT": "0000789019"}  # ZZZZ intentionally absent


def _universe_snapshot() -> Snapshot[Ticker]:
    # Nasdaq-style universe: no CIKs yet.
    return Snapshot[Ticker](
        provenance=Provenance(
            source=DataSource.NASDAQ_INDEX,
            fetched_at=datetime(2026, 6, 29, tzinfo=timezone.utc),
        ),
        rows=[
            Ticker(symbol="AAPL", name="Apple Inc."),
            Ticker(symbol="MSFT", name="Microsoft Corp"),
            Ticker(symbol="ZZZZ", name="Unknown Co"),
        ],
    )


class FakeFundamentalsSource(FundamentalsSource):
    def __init__(self) -> None:
        self.received: list[Ticker] = []

    @property
    def source(self) -> DataSource:
        return DataSource.SEC_EDGAR

    def fetch_fundamentals(self, tickers) -> Snapshot[Fundamentals]:
        self.received = list(tickers)
        rows = [
            Fundamentals(symbol=t.symbol, cik=t.cik, revenue=1.0)
            for t in tickers
            if t.cik  # mirror the real source: skip tickers without a CIK
        ]
        return Snapshot[Fundamentals](
            provenance=Provenance(
                source=DataSource.SEC_EDGAR, fetched_at=datetime.now(timezone.utc)
            ),
            rows=rows,
        )


class FakeFilingsSource(FilingsSource):
    @property
    def source(self) -> DataSource:
        return DataSource.SEC_EDGAR

    def fetch_filings(self, tickers) -> Snapshot[Filing]:
        rows = [
            Filing(
                symbol=t.symbol,
                cik=t.cik,
                form=FilingType.FORM_10K,
                filed_date=date(2025, 11, 1),
                url=f"https://example.com/{t.symbol}",
            )
            for t in tickers
            if t.cik
        ]
        return Snapshot[Filing](
            provenance=Provenance(
                source=DataSource.SEC_EDGAR, fetched_at=datetime.now(timezone.utc)
            ),
            rows=rows,
        )


def test_fundamentals_job_enriches_and_persists_ciks(tmp_path):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())
    source = FakeFundamentalsSource()

    summary = run_fundamentals_refresh(
        fundamentals_source=source,
        fetch_cik_map=lambda: CIK_MAP,
        storage=store,
    )

    # Source received CIK-enriched tickers; only the two known ones got CIKs.
    received_ciks = {t.symbol: t.cik for t in source.received}
    assert received_ciks["AAPL"] == "0000320193"
    assert received_ciks["ZZZZ"] is None
    assert summary["with_cik"] == 2
    assert summary["fundamentals_rows"] == 2  # ZZZZ skipped (no CIK)

    # Universe snapshot was re-persisted with CIKs filled.
    uni = store.read_latest("universe")
    aapl_cik = uni.loc[uni["symbol"] == "AAPL", "cik"].iloc[0]
    assert aapl_cik == "0000320193"


def test_ensure_universe_ciks_is_idempotent(tmp_path):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())

    tickers = _universe_snapshot().rows
    enriched = ensure_universe_ciks(store, tickers, CIK_MAP)  # adds 2 CIKs -> persists
    n_after_first = len(list((tmp_path / "universe").glob("*.parquet")))

    # Running again with already-enriched tickers adds no new universe snapshot.
    ensure_universe_ciks(store, enriched, CIK_MAP)
    n_after_second = len(list((tmp_path / "universe").glob("*.parquet")))
    assert n_after_second == n_after_first


def test_filings_job_writes_snapshot(tmp_path):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())

    summary = run_filings_refresh(
        filings_source=FakeFilingsSource(),
        fetch_cik_map=lambda: CIK_MAP,
        storage=store,
    )
    assert summary["filings_rows"] == 2  # AAPL, MSFT
    filings = store.read_latest("filings")
    assert set(filings["symbol"]) == {"AAPL", "MSFT"}
    assert (filings["_source"] == "sec_edgar").all()
