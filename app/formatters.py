"""Display formatting helpers for the Streamlit frontend (null-safe)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

NA = "n/a"


def is_missing(value: Any) -> bool:
    """True when a value should render as n/a."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return False


def fmt_pct(value: Any, decimals: int = 2) -> str:
    """Format a fraction as a percentage (0.05 -> 5.00%)."""
    if is_missing(value):
        return NA
    return f"{float(value):.{decimals}%}"


def fmt_usd(value: Any, decimals: int = 2) -> str:
    """Format a USD price with thousands separators."""
    if is_missing(value):
        return NA
    return f"${float(value):,.{decimals}f}"


def fmt_usd_large(value: Any) -> str:
    """Compact USD for large fundamentals (e.g. $416.2B)."""
    if is_missing(value):
        return NA
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000_000_000:
        return f"{sign}${v / 1_000_000_000_000:.1f}T"
    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:.1f}K"
    return f"{sign}${v:,.0f}"


def fmt_number(value: Any, decimals: int = 2) -> str:
    """Format a unitless number."""
    if is_missing(value):
        return NA
    return f"{float(value):,.{decimals}f}"


def fmt_shares(value: Any) -> str:
    """Format share counts with thousands separators."""
    if is_missing(value):
        return NA
    return f"{float(value):,.0f}"


def fmt_date(value: Any) -> str:
    """Format dates as ISO strings."""
    if is_missing(value):
        return NA
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return str(value)
