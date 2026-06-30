"""Tests for Massive.com PriceSource adapter — offline with injected aggs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from src.data_sources.massive_prices import (
    MassivePriceSource,
    agg_timestamp_to_date,
    aggs_to_bars,
)
from src.models import DataSource


@dataclass
class FakeAgg:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int


def test_agg_timestamp_to_date() -> None:
    # 2026-06-25 00:00:00 UTC in ms (example from live API shape)
    ts = 1782360000000
    assert agg_timestamp_to_date(ts) == date(2026, 6, 25)


def test_aggs_to_bars_skips_invalid_rows() -> None:
    good = FakeAgg(10, 11, 9, 10.5, 1000, 1782360000000)
    bad = FakeAgg(float("nan"), 11, 9, 10.5, 1000, 1782456400000)
    bars = aggs_to_bars("AAPL", [good, bad])
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].close == bars[0].adj_close == 10.5


def test_massive_price_source_fetches_per_symbol() -> None:
    calls: list[str] = []

    def fake_list_aggs(**kwargs):  # noqa: ANN003
        ticker = kwargs["ticker"]
        calls.append(ticker)
        if ticker == "BAD":
            return []
        ts = 1782360000000
        return [FakeAgg(100, 101, 99, 100.5, 1_000_000, ts)]

    sleeps: list[float] = []
    source = MassivePriceSource(
        "test-key",
        min_request_interval_seconds=0.0,
        list_aggs_fn=fake_list_aggs,
        sleep=sleeps.append,
    )
    snap = source.fetch_prices(["AAPL", "BAD", "MSFT"], date(2026, 1, 1), date(2026, 6, 30))

    assert snap.provenance.source == DataSource.MASSIVE
    assert calls == ["AAPL", "BAD", "MSFT"]
    assert len(snap.rows) == 2
    assert {r.symbol for r in snap.rows} == {"AAPL", "MSFT"}


def test_massive_price_source_requires_pacing() -> None:
    def fake_list_aggs(**_kwargs):  # noqa: ANN003
        return [FakeAgg(1, 2, 0.5, 1.5, 100, 1782360000000)]

    sleeps: list[float] = []
    source = MassivePriceSource(
        "test-key",
        min_request_interval_seconds=10.0,
        list_aggs_fn=fake_list_aggs,
        sleep=sleeps.append,
    )
    source.fetch_prices(["A", "B"], date(2026, 6, 1), date(2026, 6, 30))
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(10.0, rel=0.1)
