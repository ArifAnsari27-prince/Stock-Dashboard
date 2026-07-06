"""Test the pure comparison-table transform (Phase F frontend)."""

from __future__ import annotations

from app.compare import build_comparison_frames


def test_build_comparison_frames():
    comparison = {
        "aggregates": [
            {"index_id": "nasdaq100", "name": "Nasdaq-100", "constituents": 100.0,
             "agg_pe": 30.0, "median_net_margin": 0.2, "perf_return_ytd": 0.12,
             "total_market_cap": 2.5e13, "breadth_above_200d": 62.0},
            {"index_id": "sp500", "name": "S&P 500", "constituents": 500.0,
             "agg_pe": 22.0, "median_net_margin": 0.11, "perf_return_ytd": 0.08,
             "total_market_cap": 5.0e13, "breadth_above_200d": 55.0},
        ],
        "sectors": [
            {"index_id": "sp500", "sector": "Information Technology", "weight": 0.3},
            {"index_id": "sp500", "sector": "Financials", "weight": 0.13},
            {"index_id": "nasdaq100", "sector": "Information Technology", "weight": 0.5},
        ],
    }
    frames = build_comparison_frames(comparison)

    # Index of each section table is the display names.
    assert set(frames["performance"].index) == {"Nasdaq-100", "S&P 500"}
    # Formatting: YTD as percent, P/E as number, market cap abbreviated.
    assert frames["performance"].loc["Nasdaq-100", "YTD"] == "12.0%"
    assert frames["quantamental"].loc["S&P 500", "P/E (agg)"] == "22.00"
    assert frames["construction"].loc["S&P 500", "Total Mkt Cap"] == "$50.0T"
    assert frames["quantamental"].loc["Nasdaq-100", "% > 200D MA"] == "62%"

    # Sector pivot: rows = index names, columns = sectors.
    piv = frames["sectors_pivot"]
    assert set(piv.index) == {"Nasdaq-100", "S&P 500"}
    assert "Information Technology" in piv.columns
    assert piv.loc["S&P 500", "Financials"] == 0.13


def test_build_comparison_frames_empty():
    frames = build_comparison_frames({"aggregates": [], "sectors": []})
    assert frames["performance"].empty
    assert frames["sectors_pivot"].empty
