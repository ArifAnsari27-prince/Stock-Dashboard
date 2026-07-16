"""Tests for the pure screener filter/sort engine (app/screener_filters.py)."""

from __future__ import annotations

import pandas as pd

from app.screener_filters import (
    apply_screen,
    describe_screen,
    empty_screen,
    filterable_columns,
)


def _table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD"],
            "sector": ["Tech", "Tech", "Energy", None],
            "industry": ["Software", "Semis", "Oil", None],
            "market_cap": [4e12, 1e11, 5e10, None],
            "rsi_14": [75.0, 45.0, 25.0, 50.0],
            "return_1y": [0.5, -0.1, 0.2, None],
        }
    )


def test_empty_screen_is_noop():
    table = _table()
    out = apply_screen(table, empty_screen())
    assert list(out["symbol"]) == ["AAA", "BBB", "CCC", "DDD"]


def test_numeric_min_max_bounds_inclusive_and_null_excluded():
    out = apply_screen(_table(), {"numeric": [{"column": "rsi_14", "min": 25, "max": 45}]})
    assert list(out["symbol"]) == ["BBB", "CCC"]
    # Null in a filtered column fails the filter.
    out = apply_screen(_table(), {"numeric": [{"column": "return_1y", "min": -1}]})
    assert "DDD" not in set(out["symbol"])


def test_categorical_and_sort():
    spec = {"sectors": ["Tech"], "sort_by": "market_cap", "ascending": True, "numeric": []}
    out = apply_screen(_table(), spec)
    assert list(out["symbol"]) == ["BBB", "AAA"]


def test_unknown_columns_ignored_never_raise():
    spec = {
        "numeric": [{"column": "nonexistent", "min": 0}],
        "sectors": [],
        "sort_by": "also_missing",
    }
    out = apply_screen(_table(), spec)
    assert len(out) == 4


def test_filterable_columns_only_numeric_present():
    cols = filterable_columns(_table())
    assert "rsi_14" in cols and "market_cap" in cols
    assert "sector" not in cols and "pe_ratio" not in cols


def test_describe_screen():
    text = describe_screen(
        {
            "numeric": [{"column": "rsi_14", "max": 30}],
            "sectors": ["Tech"],
            "sort_by": "market_cap",
            "ascending": False,
        }
    )
    assert "rsi_14 ≤ 30" in text and "Tech" in text and "market_cap desc" in text
    assert describe_screen(empty_screen()) == "no filters"
