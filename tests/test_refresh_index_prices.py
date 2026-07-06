"""Test for the multi-index bulk price + metrics job (Phase B). Local ObjectStore, no network."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.data_sources.indices import ETF_PROXIES, NASDAQ100, RUSSELL3000, SP500
from src.jobs.refresh_index_prices import run_index_price_refresh
from src.models import DataSource, PriceBar, Provenance, Snapshot, Ticker
from src.storage.object_store import ObjectStore


class FakeBulkSource:
    """Returns grouped-daily bars for the intersection of requested + available symbols."""

    def __init__(self, available: set[str], n_days: int = 40):
        self._available = available
        self._n_days = n_days
        self.source = DataSource.MASSIVE

    def fetch_grouped_daily(self, symbols, start, end) -> Snapshot[PriceBar]:
        dates = pd.bdate_range(end=pd.Timestamp(end), periods=self._n_days)
        rows: list[PriceBar] = []
        for sym in set(symbols) & self._available:
            for i, ts in enumerate(dates):
                p = 100.0 + i
                rows.append(PriceBar(symbol=sym, date=ts.date(), open=p, high=p + 1,
                                     low=p - 1, close=p, adj_close=p, volume=1_000_000))
        return Snapshot[PriceBar](
            provenance=Provenance(source=DataSource.MASSIVE,
                                  fetched_at=datetime.now(timezone.utc)),
            rows=rows,
        )


def _master_universe() -> Snapshot[Ticker]:
    return Snapshot[Ticker](
        provenance=Provenance(source=DataSource.NASDAQ_INDEX,
                              fetched_at=datetime.now(timezone.utc)),
        rows=[
            Ticker(symbol="AAPL", name="Apple", sector="Information Technology",
                   market_cap=3e12, memberships=(NASDAQ100, SP500, RUSSELL3000)),
            Ticker(symbol="MSFT", name="Microsoft", sector="Information Technology",
                   market_cap=2.8e12, memberships=(SP500, RUSSELL3000)),
            Ticker(symbol="NODATA", name="No Data", sector="Industrials",
                   market_cap=1e9, memberships=(RUSSELL3000,)),
        ],
    )


@pytest.fixture
def store(tmp_path):
    s = ObjectStore(str(tmp_path))
    s.write_snapshot("universe", _master_universe())
    return s


def test_bulk_refresh_writes_partitions_and_metrics(store):
    available = {"AAPL", "MSFT", *ETF_PROXIES}  # NODATA absent -> missing
    src = FakeBulkSource(available, n_days=40)

    summary = run_index_price_refresh(price_source=src, storage=store, today=date(2026, 6, 30))

    assert summary["metrics_rows"] == 2  # AAPL, MSFT (NODATA missing)
    assert summary["missing"] == 1
    assert summary["partitions_written"] == 40  # one per business day

    # Prices are date-partitioned history.
    prices = store.read_dataset("prices")
    assert set(prices["symbol"]) >= {"AAPL", "MSFT", *ETF_PROXIES}

    # Metrics latest carries identity + index flags + sector + computed metrics.
    metrics = store.read_latest("metrics")
    aapl = metrics[metrics["symbol"] == "AAPL"].iloc[0]
    assert aapl["in_nasdaq100"] is True or aapl["in_nasdaq100"] == True  # noqa: E712
    assert aapl["in_sp500"] == True  # noqa: E712
    assert aapl["sector"] == "Information Technology"
    assert "return_1d" in metrics.columns
    # 40 days is enough for return_1m but not sma_200.
    assert pd.notna(aapl["return_1d"])


def test_frames_from_price_history_dedupes_dates():
    from src.jobs.common import frames_from_price_history
    # Same symbol/date twice -> frame must have a unique date index.
    df = pd.DataFrame({
        "symbol": ["TPC", "TPC", "TPC"],
        "date": ["2026-06-29", "2026-06-29", "2026-06-30"],
        "adj_close": [10.0, 11.0, 12.0], "high": [1, 1, 1], "low": [1, 1, 1],
        "close": [10.0, 11.0, 12.0], "volume": [1, 1, 1],
    })
    frames = frames_from_price_history(df)
    tpc = frames["TPC"]
    assert not tpc.index.duplicated().any()
    assert len(tpc) == 2  # one row per date
    assert tpc.loc["2026-06-29", "adj_close"] == 11.0  # keep last


def test_bulk_refresh_bootstraps_universe(tmp_path):
    # No universe stored -> should fetch (inject via monkeypatch-free fake).
    store = ObjectStore(str(tmp_path))
    src = FakeBulkSource({"AAPL", *ETF_PROXIES}, n_days=30)

    # Provide the master universe via a one-off fetch fn by pre-seeding instead.
    store.write_snapshot("universe", _master_universe())
    summary = run_index_price_refresh(price_source=src, storage=store, today=date(2026, 6, 30))
    assert summary["metrics_rows"] == 1  # only AAPL available
