"""Per-index aggregate metrics as pure functions (Phase D).

Given an index's members (from the master universe) plus the metrics and
fundamentals tables, compute the three comparison dimensions:
  - construction: # constituents, total market cap, top-10 weight, effective N,
    and sector weights (cap-weighted).
  - quantamental: aggregate valuation (cap-weighted P/E, P/S), median margins /
    ROE / growth, and breadth (% above 200-day MA).
  - performance: trailing returns, volatility, drawdown from the index's ETF
    proxy price series.

All network-free and unit-testable. Values are floats or None (never fabricated).
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from src.compute import returns as rr
from src.models import Ticker


def _f(value: object) -> float | None:
    """Return a plain float or None (null-safe for NaN)."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def sector_weights(members: list[Ticker]) -> dict[str, float]:
    """Cap-weighted sector weights (fractions summing to ~1) for an index's members."""
    total = sum(t.market_cap for t in members if t.market_cap and t.sector)
    if total <= 0:
        return {}
    agg: dict[str, float] = defaultdict(float)
    for t in members:
        if t.market_cap and t.sector:
            agg[t.sector] += t.market_cap
    return {sector: value / total for sector, value in agg.items()}


def construction(members: list[Ticker]) -> dict[str, float | None]:
    """Construction/concentration stats for an index's members."""
    caps = sorted((t.market_cap for t in members if t.market_cap), reverse=True)
    total = sum(caps)
    weights = [c / total for c in caps] if total > 0 else []
    return {
        "constituents": float(len(members)),
        "total_market_cap": total or None,
        "top10_weight": sum(weights[:10]) if weights else None,
        "effective_n": (1.0 / sum(w * w for w in weights)) if weights else None,
    }


def quantamental(
    metrics: pd.DataFrame, fundamentals: pd.DataFrame, member_symbols: set[str]
) -> dict[str, float | None]:
    """Aggregate valuation / quality / growth / breadth for an index's members."""
    m = metrics[metrics["symbol"].isin(member_symbols)] if not metrics.empty else metrics
    f = fundamentals[fundamentals["symbol"].isin(member_symbols)] if not fundamentals.empty else fundamentals

    out: dict[str, float | None] = {}

    mcap = m["market_cap"].sum(min_count=1) if "market_cap" in m.columns else None
    net_income = f["net_income"].sum(min_count=1) if "net_income" in f.columns else None
    revenue = f["revenue"].sum(min_count=1) if "revenue" in f.columns else None
    out["agg_pe"] = _f(mcap / net_income) if mcap and net_income and net_income > 0 else None
    out["agg_ps"] = _f(mcap / revenue) if mcap and revenue and revenue > 0 else None

    for col, key in (
        ("net_margin", "median_net_margin"),
        ("gross_margin", "median_gross_margin"),
        ("operating_margin", "median_operating_margin"),
        ("roe", "median_roe"),
        ("roic", "median_roic"),
        ("revenue_growth", "median_revenue_growth"),
    ):
        out[key] = _f(f[col].median()) if col in f.columns else None

    for col, key in (
        ("return_ytd", "median_return_ytd"),
        ("return_1y", "median_return_1y"),
        ("rsi_14", "median_rsi_14"),
    ):
        out[key] = _f(m[col].median()) if col in m.columns else None

    if "price_vs_sma_200" in m.columns:
        valid = m["price_vs_sma_200"].dropna()
        out["breadth_above_200d"] = _f((valid > 0).mean() * 100.0) if not valid.empty else None
    else:
        out["breadth_above_200d"] = None

    return out


def performance(etf_adj_close: pd.Series) -> dict[str, float | None]:
    """Trailing performance stats from an index's ETF-proxy adjusted-close series."""
    return {
        "perf_return_1m": rr.period_return(etf_adj_close, 21),
        "perf_return_3m": rr.period_return(etf_adj_close, 63),
        "perf_return_ytd": rr.ytd_return(etf_adj_close),
        "perf_return_1y": rr.period_return(etf_adj_close, 252),
        "perf_volatility_252d": rr.volatility(etf_adj_close, 252),
        "perf_max_drawdown": rr.max_drawdown(etf_adj_close),
    }
