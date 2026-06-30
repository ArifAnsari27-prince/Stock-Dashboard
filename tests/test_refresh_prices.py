"""Tests for the price refresh job wiring (build step 5).

Runs fully offline: a FakePriceSource returns canned bars, universe is provided
via a temp ParquetStore or an injected fetch fn. No network, no real yfinance.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.config import get_config
from src.data_sources.base import PriceSource
from src.jobs.refresh_prices import run_price_refresh
from src.models import DataSource, PriceBar, Provenance, Snapshot, Ticker
from src.storage.parquet_store import ParquetStore


def _make_bars(symbol: str, n: int = 300, base: float = 100.0) -> list[PriceBar]:
    dates = pd.bdate_range("2025-01-01", periods=n)
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    prices = base * np.cumprod(1.0 + rng.normal(0.0003, 0.01, n))
    bars = []
    for d, p in zip(dates, prices):
        bars.append(
            PriceBar(
                symbol=symbol,
                date=d.date(),
                open=float(p),
                high=float(p) * 1.01,
                low=float(p) * 0.99,
                close=float(p),
                adj_close=float(p),
                volume=1_000_000,
            )
        )
    return bars


class FakePriceSource(PriceSource):
    """Returns bars for requested symbols from a fixed in-memory universe of bars."""

    def __init__(self, bars_by_symbol: dict[str, list[PriceBar]]) -> None:
        self._bars = bars_by_symbol
        self.last_call: tuple | None = None

    @property
    def source(self) -> DataSource:
        return DataSource.YFINANCE

    def fetch_prices(self, symbols, start, end) -> Snapshot[PriceBar]:
        self.last_call = (list(symbols), start, end)
        rows: list[PriceBar] = []
        for sym in symbols:
            rows.extend(self._bars.get(sym, []))
        return Snapshot[PriceBar](
            provenance=Provenance(
                source=DataSource.YFINANCE,
                fetched_at=datetime(2026, 6, 30, 13, 0, tzinfo=timezone.utc),
            ),
            rows=rows,
        )


@pytest.fixture
def config(tmp_path):
    # Real config but small lookback; data_dir unused (storage injected).
    return dataclasses.replace(get_config(), price_lookback_days=400)


@pytest.fixture
def price_source() -> FakePriceSource:
    bars = {
        "AAPL": _make_bars("AAPL"),
        "MSFT": _make_bars("MSFT"),
        "QQQ": _make_bars("QQQ"),
        "SPY": _make_bars("SPY"),
        # NODATA intentionally absent -> partial failure path.
    }
    return FakePriceSource(bars)


def _universe_snapshot() -> Snapshot[Ticker]:
    return Snapshot[Ticker](
        provenance=Provenance(
            source=DataSource.QQQ_HOLDINGS,
            fetched_at=datetime(2026, 6, 29, tzinfo=timezone.utc),
        ),
        rows=[
            Ticker(symbol="AAPL", name="Apple Inc", weight=0.06),
            Ticker(symbol="MSFT", name="Microsoft Corp", weight=0.05),
            Ticker(symbol="NODATA", name="No Data Co", weight=0.01),
        ],
    )


def test_bootstrap_fetches_and_persists_universe(tmp_path, config, price_source):
    store = ParquetStore(tmp_path)
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return _universe_snapshot()

    summary = run_price_refresh(
        price_source=price_source,
        storage=store,
        config=config,
        fetch_universe_fn=fake_fetch,
        today=date(2026, 6, 30),
    )

    assert calls["n"] == 1  # fetched live because storage was empty
    assert summary["universe"] == 3
    assert summary["metrics_rows"] == 2  # AAPL, MSFT (NODATA dropped)
    assert summary["missing"] == ["NODATA"]
    # Universe got persisted during bootstrap.
    assert not store.read_latest("universe").empty


def test_cached_universe_not_refetched(tmp_path, config, price_source):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())

    def fail_fetch():
        raise AssertionError("should not fetch when universe is cached")

    summary = run_price_refresh(
        price_source=price_source,
        storage=store,
        config=config,
        fetch_universe_fn=fail_fetch,
        today=date(2026, 6, 30),
    )
    assert summary["metrics_rows"] == 2


def test_price_fetch_called_with_universe_plus_benchmarks(
    tmp_path, config, price_source
):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())
    run_price_refresh(
        price_source=price_source,
        storage=store,
        config=config,
        today=date(2026, 6, 30),
    )
    symbols, start, end = price_source.last_call
    assert symbols == ["AAPL", "MSFT", "NODATA", "QQQ", "SPY"]
    assert start == date(2025, 5, 26)  # today - 400 days
    assert end == date(2026, 7, 1)  # today + 1 (yfinance end exclusive)


def test_metrics_snapshot_content_and_provenance(tmp_path, config, price_source):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())
    run_price_refresh(
        price_source=price_source,
        storage=store,
        config=config,
        today=date(2026, 6, 30),
    )
    metrics = store.read_latest("metrics")

    assert set(metrics["symbol"]) == {"AAPL", "MSFT"}
    # Identity + a representative selection of computed columns are present.
    for col in ("name", "weight", "as_of", "latest_close", "return_1d",
                "rsi_14", "sma_200", "beta_qqq", "correlation_spy", "volatility_252d"):
        assert col in metrics.columns
    # Provenance: COMPUTED, with a note pointing back at the yfinance fetch.
    assert (metrics["_source"] == "computed").all()
    assert "yfinance" in metrics["_notes"].iloc[0]
    # With 300 bars, the 252-day beta is computable (not null).
    assert metrics["beta_qqq"].notna().all()


def test_raw_prices_snapshot_written(tmp_path, config, price_source):
    store = ParquetStore(tmp_path)
    store.write_snapshot("universe", _universe_snapshot())
    run_price_refresh(
        price_source=price_source,
        storage=store,
        config=config,
        today=date(2026, 6, 30),
    )
    prices = store.read_latest("prices")
    assert (prices["_source"] == "yfinance").all()
    assert set(prices["symbol"]) >= {"AAPL", "MSFT", "QQQ", "SPY"}
