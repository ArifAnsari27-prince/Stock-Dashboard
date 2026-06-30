"""Return, risk, and drawdown metrics as pure functions (CLAUDE.md §3, build step 4).

All functions take pandas Series in and return numbers out, with zero I/O.
Price inputs are adjusted close, indexed by date ascending.

Conventions (fixed per CLAUDE.md §4):
  - Period returns: simple % change over a fixed number of trading days
    (1D=1, 5D=5, 1M=21, 3M=63, 6M=126, 1Y=252). Returned as fractions.
  - YTD: from the last close of the prior calendar year to the latest close.
  - Volatility: annualized stdev of daily LOG returns, sample stdev (ddof=1),
    times sqrt(252), over a trailing window (20/60/252).
  - Momentum (3/6/12M): total return over the window ENDING one month (21
    trading days) ago — i.e. excluding the most recent month.
  - Beta / correlation: on daily simple returns vs a benchmark, trailing 252
    days, on the dates the two series share.
  - Drawdowns: on price levels. Max drawdown over full history; 52W drawdown is
    the current decline from the trailing 252-day high.

A metric returns None when there is insufficient history to compute it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252
TRADING_DAYS_PER_MONTH = 21

# Named period-return windows in trading days.
PERIOD_RETURN_DAYS = {
    "1d": 1,
    "5d": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
}

VOLATILITY_WINDOWS = (20, 60, 252)
MOMENTUM_MONTHS = (3, 6, 12)


def simple_returns(prices: pd.Series) -> pd.Series:
    """Daily simple returns: price_t / price_{t-1} - 1."""
    return prices.pct_change()


def log_returns(prices: pd.Series) -> pd.Series:
    """Daily log returns: ln(price_t / price_{t-1})."""
    return np.log(prices / prices.shift(1))


def period_return(prices: pd.Series, days: int) -> float | None:
    """Simple return over `days` trading days, or None if history is too short."""
    if len(prices) <= days:
        return None
    end = prices.iloc[-1]
    start = prices.iloc[-1 - days]
    if pd.isna(start) or pd.isna(end) or start == 0:
        return None
    return float(end / start - 1.0)


def ytd_return(prices: pd.Series) -> float | None:
    """Return from the prior year's last close to the latest close.

    Requires a DatetimeIndex and at least one observation in a prior calendar
    year; otherwise None.
    """
    if prices.empty or not isinstance(prices.index, pd.DatetimeIndex):
        return None
    current_year = prices.index[-1].year
    prior = prices[prices.index.year < current_year]
    if prior.empty:
        return None
    base = prior.iloc[-1]
    if pd.isna(base) or base == 0:
        return None
    return float(prices.iloc[-1] / base - 1.0)


def volatility(prices: pd.Series, window: int) -> float | None:
    """Annualized stdev of daily log returns over the trailing `window`.

    Sample stdev (ddof=1) * sqrt(252). None if fewer than `window` returns.
    """
    lr = log_returns(prices).dropna()
    if len(lr) < window:
        return None
    sample = lr.iloc[-window:]
    return float(sample.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def momentum(
    prices: pd.Series,
    lookback_months: int,
    skip_months: int = 1,
) -> float | None:
    """Total return over a window ending `skip_months` ago (excludes recent month).

    e.g. 3M momentum = return from 4 months ago to 1 month ago. Uses 21 trading
    days per month. None if history is too short.
    """
    end_offset = skip_months * TRADING_DAYS_PER_MONTH
    start_offset = (skip_months + lookback_months) * TRADING_DAYS_PER_MONTH
    if len(prices) <= start_offset:
        return None
    end = prices.iloc[-1 - end_offset]
    start = prices.iloc[-1 - start_offset]
    if pd.isna(start) or pd.isna(end) or start == 0:
        return None
    return float(end / start - 1.0)


def _aligned_returns(
    asset_prices: pd.Series, benchmark_prices: pd.Series, window: int
) -> tuple[pd.Series, pd.Series] | None:
    """Return trailing-`window` daily simple returns on shared dates, or None."""
    joined = pd.concat(
        [simple_returns(asset_prices), simple_returns(benchmark_prices)],
        axis=1,
        join="inner",
    ).dropna()
    if len(joined) < window:
        return None
    tail = joined.iloc[-window:]
    return tail.iloc[:, 0], tail.iloc[:, 1]


def beta(
    asset_prices: pd.Series,
    benchmark_prices: pd.Series,
    window: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Beta of asset vs benchmark over trailing `window` shared days.

    beta = cov(asset, benchmark) / var(benchmark). None if insufficient overlap
    or the benchmark has zero variance.
    """
    aligned = _aligned_returns(asset_prices, benchmark_prices, window)
    if aligned is None:
        return None
    asset_r, bench_r = aligned
    bench_var = bench_r.var(ddof=1)
    if bench_var == 0 or pd.isna(bench_var):
        return None
    covariance = asset_r.cov(bench_r)
    return float(covariance / bench_var)


def correlation(
    asset_prices: pd.Series,
    benchmark_prices: pd.Series,
    window: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Pearson correlation of daily returns vs benchmark over trailing `window`."""
    aligned = _aligned_returns(asset_prices, benchmark_prices, window)
    if aligned is None:
        return None
    asset_r, bench_r = aligned
    corr = asset_r.corr(bench_r)
    return None if pd.isna(corr) else float(corr)


def max_drawdown(prices: pd.Series) -> float | None:
    """Largest peak-to-trough decline over the full series, as a negative fraction."""
    if prices.empty:
        return None
    running_max = prices.cummax()
    drawdown = prices / running_max - 1.0
    value = drawdown.min()
    return None if pd.isna(value) else float(value)


def drawdown_52w(prices: pd.Series) -> float | None:
    """Current decline from the trailing 252-day high, as a fraction (<= 0)."""
    if prices.empty:
        return None
    window = prices.iloc[-TRADING_DAYS_PER_YEAR:]
    high = window.max()
    if pd.isna(high) or high == 0:
        return None
    return float(prices.iloc[-1] / high - 1.0)


def fifty_two_week_high(prices: pd.Series) -> float | None:
    """Highest close over the trailing 252 days (or all available if fewer)."""
    if prices.empty:
        return None
    value = prices.iloc[-TRADING_DAYS_PER_YEAR:].max()
    return None if pd.isna(value) else float(value)


def fifty_two_week_low(prices: pd.Series) -> float | None:
    """Lowest close over the trailing 252 days (or all available if fewer)."""
    if prices.empty:
        return None
    value = prices.iloc[-TRADING_DAYS_PER_YEAR:].min()
    return None if pd.isna(value) else float(value)


def return_metrics(
    prices: pd.Series,
    benchmarks: dict[str, pd.Series] | None = None,
) -> dict[str, float | None]:
    """Compute the full latest-value return/risk/drawdown metric set (df in -> metrics out).

    `prices` is adjusted close indexed by date. `benchmarks` maps a label (e.g.
    'qqq', 'spy') to that benchmark's adjusted-close series; beta and correlation
    are computed against each. Returns a flat dict of scalar metrics (fractions
    for returns/momentum/drawdowns/volatility, unitless for beta/correlation,
    price levels for 52W high/low).
    """
    metrics: dict[str, float | None] = {}

    for name, days in PERIOD_RETURN_DAYS.items():
        metrics[f"return_{name}"] = period_return(prices, days)
    metrics["return_ytd"] = ytd_return(prices)

    for window in VOLATILITY_WINDOWS:
        metrics[f"volatility_{window}d"] = volatility(prices, window)

    for months in MOMENTUM_MONTHS:
        metrics[f"momentum_{months}m"] = momentum(prices, months)

    metrics["max_drawdown"] = max_drawdown(prices)
    metrics["drawdown_52w"] = drawdown_52w(prices)
    metrics["high_52w"] = fifty_two_week_high(prices)
    metrics["low_52w"] = fifty_two_week_low(prices)

    for label, bench_prices in (benchmarks or {}).items():
        metrics[f"beta_{label}"] = beta(prices, bench_prices)
        metrics[f"correlation_{label}"] = correlation(prices, bench_prices)

    return metrics
