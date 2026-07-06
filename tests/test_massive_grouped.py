"""Tests for Massive grouped-daily bulk pricing (Phase B). No network."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data_sources.massive_prices import (
    MassivePriceSource,
    grouped_aggs_to_bars,
)


class FakeAgg:
    def __init__(self, ticker, ts_ms, o=1.0, h=2.0, low=0.5, c=1.5, v=1000):
        self.ticker = ticker
        self.timestamp = ts_ms
        self.open, self.high, self.low, self.close, self.volume = o, h, low, c, v


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 13, tzinfo=timezone.utc).timestamp() * 1000)


def test_grouped_aggs_to_bars_filters_and_normalizes():
    ts = _ms(date(2026, 6, 30))
    aggs = [FakeAgg("AAPL", ts), FakeAgg("MSFT", ts), FakeAgg("ZZZZ", ts),
            FakeAgg("BRK.B", ts)]
    bars = grouped_aggs_to_bars(aggs, {"AAPL", "MSFT", "BRK-B"})
    got = {b.symbol for b in bars}
    assert got == {"AAPL", "MSFT", "BRK-B"}  # ZZZZ excluded; BRK.B normalized
    assert all(b.date == date(2026, 6, 30) for b in bars)


def test_grouped_aggs_dedupes_repeated_ticker():
    # Massive occasionally returns a ticker twice in one day's response.
    ts = _ms(date(2026, 6, 30))
    aggs = [FakeAgg("TPC", ts, c=10.0), FakeAgg("TPC", ts, c=11.0)]
    bars = grouped_aggs_to_bars(aggs, {"TPC"})
    assert len(bars) == 1  # kept once, not twice


def test_grouped_aggs_skips_incomplete():
    ts = _ms(date(2026, 6, 30))
    bad = FakeAgg("AAPL", ts)
    bad.close = None
    assert grouped_aggs_to_bars([bad], {"AAPL"}) == []


def test_fetch_grouped_daily_one_call_per_trading_day():
    calls: list[str] = []

    def grouped_fn(day_iso: str):
        calls.append(day_iso)
        ts = _ms(date.fromisoformat(day_iso))
        return [FakeAgg("AAPL", ts), FakeAgg("MSFT", ts), FakeAgg("ZZZZ", ts)]

    src = MassivePriceSource(
        "key", grouped_daily_fn=grouped_fn, sleep=lambda _s: None
    )
    # Mon 2026-06-29 .. Wed 2026-07-01 -> 3 business days.
    snap = src.fetch_grouped_daily(["AAPL", "MSFT"], date(2026, 6, 29), date(2026, 7, 1))
    assert calls == ["2026-06-29", "2026-06-30", "2026-07-01"]  # one call/day
    assert {b.symbol for b in snap.rows} == {"AAPL", "MSFT"}
    assert len(snap.rows) == 6  # 2 symbols x 3 days
    assert "2/2 symbols" in snap.provenance.notes


def test_fetch_grouped_daily_skips_weekends():
    calls: list[str] = []

    def grouped_fn(day_iso: str):
        calls.append(day_iso)
        return []

    src = MassivePriceSource("key", grouped_daily_fn=grouped_fn, sleep=lambda _s: None)
    # Sat 2026-06-27 .. Sun 2026-06-28 -> no business days.
    src.fetch_grouped_daily(["AAPL"], date(2026, 6, 27), date(2026, 6, 28))
    assert calls == []
