"""Tests for the yfinance PriceSource adapter (build step 3).

All tests run offline: the pure transform `frame_to_bars` is tested against
in-memory yfinance-shaped frames, and `YFinancePriceSource` is driven with an
injected fake downloader and a recording sleep — no network, no real yfinance
calls.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.data_sources.prices import (
    YFinancePriceSource,
    _extract_symbol_frame,
    frame_to_bars,
)
from src.models import DataSource

_FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
_DATES = pd.to_datetime(["2026-06-25", "2026-06-26", "2026-06-29"])


def _grouped_by_ticker_frame() -> pd.DataFrame:
    """Build a yfinance group_by='ticker' frame: AAPL has data, MSFT all-NaN."""
    cols = pd.MultiIndex.from_product([["AAPL", "MSFT"], _FIELDS])
    df = pd.DataFrame(index=_DATES, columns=cols, dtype=float)
    # AAPL: clean rows.
    df[("AAPL", "Open")] = [200.0, 201.0, 202.0]
    df[("AAPL", "High")] = [205.0, 206.0, 207.0]
    df[("AAPL", "Low")] = [199.0, 200.0, 201.0]
    df[("AAPL", "Close")] = [204.0, 205.0, 206.0]
    df[("AAPL", "Adj Close")] = [203.5, 204.5, 205.5]
    df[("AAPL", "Volume")] = [1_000_000, 1_100_000, 1_200_000]
    # MSFT: left all NaN to simulate a failed ticker.
    return df


def test_frame_to_bars_extracts_good_symbol_only() -> None:
    df = _grouped_by_ticker_frame()
    bars = frame_to_bars(df, ["AAPL", "MSFT"])
    assert set(bars) == {"AAPL"}  # MSFT (all-NaN) omitted
    assert len(bars["AAPL"]) == 3
    first = bars["AAPL"][0]
    assert first.symbol == "AAPL"
    assert first.date == date(2026, 6, 25)
    assert first.close == 204.0
    assert first.adj_close == 203.5
    assert first.volume == 1_000_000


def test_frame_to_bars_skips_rows_with_missing_values() -> None:
    df = _grouped_by_ticker_frame()
    df.loc[_DATES[1], ("AAPL", "Close")] = np.nan  # one bad row
    bars = frame_to_bars(df, ["AAPL"])
    assert len(bars["AAPL"]) == 2  # the NaN-close row dropped
    assert [b.date for b in bars["AAPL"]] == [date(2026, 6, 25), date(2026, 6, 29)]


def test_frame_to_bars_adj_close_fallback_on_flat_frame() -> None:
    # Flat single-ticker frame with no "Adj Close" -> adj_close falls back to close.
    df = pd.DataFrame(
        {
            "Open": [10.0],
            "High": [11.0],
            "Low": [9.0],
            "Close": [10.5],
            "Volume": [500],
        },
        index=pd.to_datetime(["2026-06-29"]),
    )
    bars = frame_to_bars(df, ["NFLX"])
    assert bars["NFLX"][0].adj_close == bars["NFLX"][0].close == 10.5


def test_extract_symbol_frame_grouped_by_column() -> None:
    # group_by='column' shape: level 0 = field, level 1 = ticker.
    cols = pd.MultiIndex.from_product([_FIELDS, ["AAPL"]])
    df = pd.DataFrame(0.0, index=_DATES, columns=cols)
    sub = _extract_symbol_frame(df, "AAPL")
    assert sub is not None
    assert set(_FIELDS).issubset(sub.columns)


def test_extract_symbol_frame_absent_returns_none() -> None:
    df = _grouped_by_ticker_frame()
    assert _extract_symbol_frame(df, "TSLA") is None


# --- YFinancePriceSource: batching / retry / partial failure -----------------


class _Recorder:
    """Fake downloader recording calls; returns a frame or raises per script."""

    def __init__(self, results: list) -> None:
        # Each entry is either a DataFrame to return or an Exception to raise.
        self._results = results
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> pd.DataFrame:
        self.calls.append(kwargs)
        result = self._results[min(len(self.calls) - 1, len(self._results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def test_fetch_prices_partial_failure_in_snapshot() -> None:
    downloader = _Recorder([_grouped_by_ticker_frame()])
    src = YFinancePriceSource(downloader=downloader, sleep=lambda _s: None)
    snap = src.fetch_prices(["AAPL", "MSFT"], date(2026, 6, 24), date(2026, 6, 30))

    assert snap.provenance.source == DataSource.YFINANCE
    assert {b.symbol for b in snap.rows} == {"AAPL"}
    assert "1/2 symbols fetched" in snap.provenance.notes
    assert "MSFT" in snap.provenance.notes
    # start/end/interval passed through correctly.
    assert downloader.calls[0]["start"] == "2026-06-24"
    assert downloader.calls[0]["end"] == "2026-06-30"
    assert downloader.calls[0]["interval"] == "1d"
    assert downloader.calls[0]["auto_adjust"] is False


def test_fetch_prices_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    # Fail twice, then return data on the 3rd attempt.
    downloader = _Recorder(
        [RuntimeError("flaky"), RuntimeError("flaky"), _grouped_by_ticker_frame()]
    )
    src = YFinancePriceSource(
        downloader=downloader,
        sleep=sleeps.append,
        max_retries=3,
        backoff_base_seconds=1.0,
    )
    snap = src.fetch_prices(["AAPL"], date(2026, 6, 24), date(2026, 6, 30))

    assert len(downloader.calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff between the 3 attempts
    assert {b.symbol for b in snap.rows} == {"AAPL"}


def test_fetch_prices_batch_total_failure_is_tolerated() -> None:
    downloader = _Recorder([RuntimeError("down")])  # always raises
    src = YFinancePriceSource(
        downloader=downloader, sleep=lambda _s: None, max_retries=2
    )
    snap = src.fetch_prices(["AAPL"], date(2026, 6, 24), date(2026, 6, 30))
    assert snap.rows == []  # no crash, empty partial result
    assert len(downloader.calls) == 2  # retried up to max_retries


def test_fetch_prices_chunks_into_batches() -> None:
    downloader = _Recorder([_grouped_by_ticker_frame()])
    src = YFinancePriceSource(
        downloader=downloader, sleep=lambda _s: None, batch_size=1
    )
    src.fetch_prices(["AAPL", "MSFT"], date(2026, 6, 24), date(2026, 6, 30))
    assert len(downloader.calls) == 2  # one call per symbol with batch_size=1
    assert downloader.calls[0]["tickers"] == ["AAPL"]
    assert downloader.calls[1]["tickers"] == ["MSFT"]
