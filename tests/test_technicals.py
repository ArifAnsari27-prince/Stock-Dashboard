"""Tests for technical indicators (build step 4). Pure, fixture-based, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.compute import technicals as ti


def _series(values: list[float]) -> pd.Series:
    idx = pd.bdate_range("2025-01-01", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_sma_last_value_and_warmup() -> None:
    s = _series(list(range(1, 21)))  # 1..20
    result = ti.sma(s, 3)
    assert np.isnan(result.iloc[0]) and np.isnan(result.iloc[1])  # warmup NaN
    assert result.iloc[-1] == pytest.approx((18 + 19 + 20) / 3)


def test_price_vs_ma_fraction() -> None:
    s = _series([10.0] * 5 + [11.0])  # last price above its own MA
    pv = ti.price_vs_ma(s, 5)
    ma_last = ti.sma(s, 5).iloc[-1]
    assert pv.iloc[-1] == pytest.approx((11.0 - ma_last) / ma_last)


def test_ema_seeded_with_first_value() -> None:
    s = _series([5.0, 6.0, 7.0])
    assert ti.ema(s, 3).iloc[0] == pytest.approx(5.0)


def test_rsi_all_gains_is_100_all_losses_is_0() -> None:
    up = _series([float(x) for x in range(1, 31)])
    down = _series([float(x) for x in range(30, 0, -1)])
    assert ti.rsi(up).iloc[-1] == pytest.approx(100.0)
    assert ti.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_rsi_warmup_is_nan() -> None:
    s = _series([float(x) for x in range(1, 31)])
    assert np.isnan(ti.rsi(s).iloc[0])


def test_macd_constant_series_is_zero() -> None:
    s = _series([50.0] * 60)
    out = ti.macd(s)
    assert out["macd"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert out["signal"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert out["histogram"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_bollinger_middle_is_sma_and_bands_straddle() -> None:
    s = _series([float(x) for x in range(1, 41)])
    bb = ti.bollinger_bands(s, window=20, num_std=2.0)
    # Middle = mean of last 20 values (21..40) = 30.5.
    assert bb["middle"].iloc[-1] == pytest.approx(30.5)
    assert bb["lower"].iloc[-1] < bb["middle"].iloc[-1] < bb["upper"].iloc[-1]


def test_bollinger_constant_series_collapses_bands() -> None:
    s = _series([20.0] * 25)
    bb = ti.bollinger_bands(s, window=20)
    assert bb["upper"].iloc[-1] == pytest.approx(20.0)
    assert bb["lower"].iloc[-1] == pytest.approx(20.0)


def test_atr_constant_range() -> None:
    n = 30
    low = _series([100.0] * n)
    high = _series([102.0] * n)
    close = _series([101.0] * n)
    # TR is 2.0 every day, so Wilder ATR converges to exactly 2.0.
    assert ti.atr(high, low, close).iloc[-1] == pytest.approx(2.0)


def test_relative_volume_constant_is_one() -> None:
    vol = _series([1000.0] * 30)
    assert ti.relative_volume(vol, window=20).iloc[-1] == pytest.approx(1.0)


def test_technical_indicators_aggregator() -> None:
    n = 260
    price = np.linspace(100.0, 200.0, n)
    df = pd.DataFrame(
        {
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "adj_close": price,
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.bdate_range("2025-01-01", periods=n),
    )
    m = ti.technical_indicators(df)
    # All expected keys present.
    for key in ("sma_200", "price_vs_sma_50", "rsi_14", "macd", "bollinger_percent_b",
                "atr_14", "relative_volume_20", "volume"):
        assert key in m
    # With 260 rows of rising prices these are computable and sensible.
    assert m["sma_200"] is not None
    assert m["rsi_14"] == pytest.approx(100.0)  # monotonically rising
    assert m["relative_volume_20"] == pytest.approx(1.0)
