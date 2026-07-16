"""Pure screener filter/sort engine — no Streamlit, unit-testable.

A "screen" is a JSON-serializable spec the UI builds and `user_store` persists:

    {
      "numeric":    [{"column": "rsi_14", "min": 30.0, "max": 70.0}, ...],
      "sectors":    ["Information Technology", ...],   # empty = all
      "industries": ["Semiconductors", ...],           # empty = all
      "sort_by":    "market_cap",                      # or None
      "ascending":  false,
      "columns":    ["symbol", "name", ...],           # display columns, optional
    }

`apply_screen` filters and sorts a metrics table with that spec. Unknown columns
are ignored (never raise — the table schema can evolve independently of saved
screens). Numeric bounds are inclusive; rows with a null value in a filtered
column are excluded by that filter (a bounded filter is an assertion about the
value, and null fails it).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Columns offered in the filter-builder dropdown, in display order. Only those
# actually present in the table are shown. Units follow the data dictionary
# (returns/margins are fractions; market_cap USD; RSI 0-100).
FILTERABLE_COLUMNS: list[str] = [
    "market_cap",
    "latest_close",
    "return_1d",
    "return_5d",
    "return_1m",
    "return_3m",
    "return_6m",
    "return_ytd",
    "return_1y",
    "momentum_3m",
    "momentum_6m",
    "momentum_12m",
    "rsi_14",
    "price_vs_sma_20",
    "price_vs_sma_50",
    "price_vs_sma_200",
    "volatility_20d",
    "volatility_60d",
    "volatility_252d",
    "beta_qqq",
    "beta_spy",
    "drawdown_52w",
    "max_drawdown",
    "relative_volume_20",
    "volume",
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roe",
    "roic",
    "fcf_conversion",
    "revenue",
    "free_cash_flow",
    "net_debt",
    "rnd_to_revenue",
    "capex_to_revenue",
    "pe_ratio",
    "ps_ratio",
    "pb_ratio",
    "ev_to_sales",
    "fcf_yield",
]


def empty_screen() -> dict[str, Any]:
    """A fresh, no-op screen spec."""
    return {
        "numeric": [],
        "sectors": [],
        "industries": [],
        "sort_by": None,
        "ascending": False,
        "columns": [],
    }


def filterable_columns(table: pd.DataFrame) -> list[str]:
    """The filter candidates that exist (and are numeric) in this table."""
    numeric = set(table.select_dtypes(include="number").columns)
    return [c for c in FILTERABLE_COLUMNS if c in numeric]


def apply_screen(table: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    """Filter + sort `table` per the screen spec (pure; never mutates input)."""
    if table.empty:
        return table
    view = table

    for cat_key, column in (("sectors", "sector"), ("industries", "industry")):
        chosen = spec.get(cat_key) or []
        if chosen and column in view.columns:
            view = view[view[column].isin(chosen)]

    for rule in spec.get("numeric") or []:
        column = rule.get("column")
        if not column or column not in view.columns:
            continue
        series = pd.to_numeric(view[column], errors="coerce")
        mask = series.notna()
        lo, hi = rule.get("min"), rule.get("max")
        if lo is not None:
            mask &= series >= float(lo)
        if hi is not None:
            mask &= series <= float(hi)
        view = view[mask]

    sort_by = spec.get("sort_by")
    if sort_by and sort_by in view.columns:
        view = view.sort_values(
            sort_by, ascending=bool(spec.get("ascending", False)), na_position="last"
        )

    return view.reset_index(drop=True)


def describe_screen(spec: dict[str, Any]) -> str:
    """One-line human summary of a spec (for the saved-screens list)."""
    parts: list[str] = []
    for rule in spec.get("numeric") or []:
        column, lo, hi = rule.get("column"), rule.get("min"), rule.get("max")
        if not column:
            continue
        if lo is not None and hi is not None:
            parts.append(f"{lo} ≤ {column} ≤ {hi}")
        elif lo is not None:
            parts.append(f"{column} ≥ {lo}")
        elif hi is not None:
            parts.append(f"{column} ≤ {hi}")
    if spec.get("sectors"):
        parts.append("sector ∈ " + "/".join(spec["sectors"]))
    if spec.get("industries"):
        parts.append("industry ∈ " + "/".join(spec["industries"]))
    if spec.get("sort_by"):
        direction = "asc" if spec.get("ascending") else "desc"
        parts.append(f"sort {spec['sort_by']} {direction}")
    return "; ".join(parts) if parts else "no filters"
