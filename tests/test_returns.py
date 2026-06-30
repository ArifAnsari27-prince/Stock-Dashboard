"""Tests for return/risk/drawdown metrics (build step 4). Pure, fixture-based."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.compute import returns as rr


def _series(values: list[float], start: str = "2025-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_period_return_basic() -> None:
    s = _series([100.0, 110.0, 121.0])
    assert rr.period_return(s, 1) == pytest.approx(0.1)  # 121/110 - 1
    assert rr.period_return(s, 2) == pytest.approx(0.21)  # 121/100 - 1


def test_period_return_insufficient_history() -> None:
    s = _series([100.0, 110.0])
    assert rr.period_return(s, 5) is None


def test_ytd_return_uses_prior_year_close() -> None:
    idx = pd.to_datetime(["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05"])
    s = pd.Series([90.0, 100.0, 110.0, 121.0], index=idx)
    # Prior-year last close = 100 (2025-12-31); latest = 121 -> +21%.
    assert rr.ytd_return(s) == pytest.approx(0.21)


def test_ytd_return_no_prior_year() -> None:
    s = _series([100.0, 101.0], start="2026-01-01")
    assert rr.ytd_return(s) is None


def test_volatility_constant_log_return_is_zero() -> None:
    # Prices with a constant daily log return -> zero dispersion -> zero vol.
    n = 60
    prices = 100.0 * np.exp(0.01 * np.arange(n))
    s = _series(list(prices))
    assert rr.volatility(s, 20) == pytest.approx(0.0, abs=1e-9)


def test_volatility_matches_manual_computation() -> None:
    rng = np.random.default_rng(42)
    prices = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 300))
    s = _series(list(prices))
    lr = np.log(s / s.shift(1)).dropna()
    expected = lr.iloc[-60:].std(ddof=1) * np.sqrt(252)
    assert rr.volatility(s, 60) == pytest.approx(expected)


def test_momentum_excludes_recent_month() -> None:
    prices = list(np.arange(1.0, 121.0))  # 120 strictly increasing points
    s = _series(prices)
    # 3M momentum: end = 21d ago, start = 84d ago.
    end = s.iloc[-1 - 21]
    start = s.iloc[-1 - 84]
    assert rr.momentum(s, 3) == pytest.approx(end / start - 1.0)


def test_momentum_insufficient_history() -> None:
    s = _series(list(np.arange(1.0, 50.0)))  # < 84 points
    assert rr.momentum(s, 3) is None


def test_beta_and_correlation_perfect_scaled() -> None:
    rng = np.random.default_rng(0)
    bench_r = rng.normal(0, 0.01, 300)
    bench = 100.0 * np.cumprod(1.0 + bench_r)
    # Asset return is exactly 2x the benchmark return each day -> beta 2, corr 1.
    asset = 100.0 * np.cumprod(1.0 + 2.0 * bench_r)
    bs, as_ = _series(list(bench)), _series(list(asset))
    assert rr.beta(as_, bs) == pytest.approx(2.0, abs=1e-6)
    assert rr.correlation(as_, bs) == pytest.approx(1.0, abs=1e-9)


def test_beta_insufficient_overlap_returns_none() -> None:
    bs = _series([100.0, 101.0, 102.0])
    as_ = _series([100.0, 102.0, 104.0])
    assert rr.beta(as_, bs, window=252) is None


def test_max_drawdown() -> None:
    s = _series([100.0, 120.0, 60.0, 90.0])
    # Worst trough 60 from peak 120 -> -50%.
    assert rr.max_drawdown(s) == pytest.approx(-0.5)


def test_drawdown_52w_and_high_low() -> None:
    s = _series([100.0, 120.0, 60.0, 90.0])
    assert rr.drawdown_52w(s) == pytest.approx(90.0 / 120.0 - 1.0)
    assert rr.fifty_two_week_high(s) == pytest.approx(120.0)
    assert rr.fifty_two_week_low(s) == pytest.approx(60.0)


def test_return_metrics_aggregator_keys() -> None:
    rng = np.random.default_rng(7)
    prices = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 300))
    bench = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 300))
    s = _series(list(prices))
    m = rr.return_metrics(s, benchmarks={"qqq": _series(list(bench))})
    for key in ("return_1d", "return_ytd", "volatility_252d", "momentum_12m",
                "max_drawdown", "drawdown_52w", "high_52w", "beta_qqq",
                "correlation_qqq"):
        assert key in m
    assert m["beta_qqq"] is not None
