"""Technical indicators as pure functions (CLAUDE.md §3, build step 4).

Every function here takes pandas Series/DataFrames in and returns results out,
with zero I/O, so the module is unit-testable against fixtures with no network.

Indicator conventions (fixed to avoid the silent-bug ambiguity warned about in
CLAUDE.md §4):
  - Moving averages: simple (SMA) on adjusted close, windows 20/50/200.
  - Price-vs-MA: (price - MA) / MA, a fraction (0.05 == +5% above the MA).
  - RSI(14): Wilder's smoothing (EWM with alpha = 1/period, adjust=False).
  - MACD: EMA(12) - EMA(26), signal = EMA(9) of the MACD line, adjust=False.
  - Bollinger: 20-day SMA +/- 2 population stdev (ddof=0).
  - ATR(14): Wilder's smoothing of True Range.
  - Relative volume: volume / trailing 20-day average volume.

Series-returning functions are index-aligned to their input and carry NaN where
there is insufficient history (governed by `min_periods`). Scalar metrics for
the dashboard are produced by `technical_indicators`, which takes the latest
value of each.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Standard windows / periods (CLAUDE.md §1).
MA_WINDOWS = (20, 50, 200)
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BOLLINGER_WINDOW, BOLLINGER_STD = 20, 2.0
ATR_PERIOD = 14
RELATIVE_VOLUME_WINDOW = 20


def sma(prices: pd.Series, window: int) -> pd.Series:
    """Simple moving average over `window` periods (NaN until `window` points)."""
    return prices.rolling(window=window, min_periods=window).mean()


def price_vs_ma(prices: pd.Series, window: int) -> pd.Series:
    """Fractional distance of price above/below its SMA: (price - MA) / MA."""
    ma = sma(prices, window)
    return (prices - ma) / ma


def ema(prices: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (adjust=False, seeded with the first value)."""
    return prices.ewm(span=span, adjust=False).mean()


def rsi(prices: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing.

    Returns values in [0, 100], NaN before `period` deltas are available. When
    average loss is zero (no down moves in the window) RSI is defined as 100.
    """
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    result = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> rs is inf/NaN; RSI is 100 by convention (only where we
    # already have enough history, i.e. avg_gain is not NaN).
    result = result.where(avg_loss != 0.0, other=100.0)
    result = result.where(avg_gain.notna(), other=np.nan)
    return result


def macd(
    prices: pd.Series,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram as a 3-column DataFrame."""
    macd_line = ema(prices, fast) - ema(prices, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram}
    )


def bollinger_bands(
    prices: pd.Series,
    window: int = BOLLINGER_WINDOW,
    num_std: float = BOLLINGER_STD,
) -> pd.DataFrame:
    """Bollinger bands (middle/upper/lower) plus %B.

    Middle band is the SMA; bands are +/- `num_std` population stdev (ddof=0).
    %B = (price - lower) / (upper - lower), where 0.5 sits on the middle band.
    """
    middle = sma(prices, window)
    std = prices.rolling(window=window, min_periods=window).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    width = upper - lower
    percent_b = (prices - lower) / width
    return pd.DataFrame(
        {"middle": middle, "upper": upper, "lower": lower, "percent_b": percent_b}
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range: max(high-low, |high-prev_close|, |low-prev_close|)."""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = ATR_PERIOD,
) -> pd.Series:
    """Average True Range using Wilder's smoothing of True Range."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def relative_volume(
    volume: pd.Series, window: int = RELATIVE_VOLUME_WINDOW
) -> pd.Series:
    """Volume divided by its trailing `window`-day average (1.0 == average)."""
    avg = volume.rolling(window=window, min_periods=window).mean()
    return volume / avg


def _latest(series: pd.Series) -> float | None:
    """Return the last non-NaN-safe value of a series as a float, or None."""
    if series is None or series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def technical_indicators(ohlcv: pd.DataFrame) -> dict[str, float | None]:
    """Compute latest-value technical metrics from an OHLCV frame (df in -> metrics out).

    Expects columns: 'high', 'low', 'close', 'adj_close', 'volume', indexed by
    date ascending. Indicators that use price levels operate on 'adj_close';
    ATR uses raw high/low/close. Missing history yields None for that metric.

    Returns a flat dict of scalar metrics (fractions for price-vs-MA; absolute
    levels for MAs/MACD/Bollinger/ATR; ratio for relative volume).
    """
    price = ohlcv["adj_close"]
    metrics: dict[str, float | None] = {}

    for window in MA_WINDOWS:
        metrics[f"sma_{window}"] = _latest(sma(price, window))
        metrics[f"price_vs_sma_{window}"] = _latest(price_vs_ma(price, window))

    metrics["rsi_14"] = _latest(rsi(price))

    macd_df = macd(price)
    metrics["macd"] = _latest(macd_df["macd"])
    metrics["macd_signal"] = _latest(macd_df["signal"])
    metrics["macd_histogram"] = _latest(macd_df["histogram"])

    bb = bollinger_bands(price)
    metrics["bollinger_upper"] = _latest(bb["upper"])
    metrics["bollinger_middle"] = _latest(bb["middle"])
    metrics["bollinger_lower"] = _latest(bb["lower"])
    metrics["bollinger_percent_b"] = _latest(bb["percent_b"])

    metrics["atr_14"] = _latest(
        atr(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    )

    metrics["volume"] = _latest(ohlcv["volume"])
    metrics["relative_volume_20"] = _latest(relative_volume(ohlcv["volume"]))

    return metrics
