"""Index comparison view (Phase F): performance, construction, quantamental.

Consumes the read API's `get_index_comparison()` (per-index aggregates + sector
weights) and `get_index_performance()` (rebased ETF-proxy series). The pure
`build_comparison_frames` reshapes the records into display DataFrames and is
unit-tested; `render_comparison` draws them with Streamlit + Plotly.
"""

from __future__ import annotations

import pandas as pd

# Aggregate columns -> (display label, formatter kind) for each section.
_PERFORMANCE_COLS = [
    ("perf_return_1m", "1M", "pct"),
    ("perf_return_3m", "3M", "pct"),
    ("perf_return_ytd", "YTD", "pct"),
    ("perf_return_1y", "1Y", "pct"),
    ("perf_volatility_252d", "Vol (1Y)", "pct"),
    ("perf_max_drawdown", "Max DD", "pct"),
]
_CONSTRUCTION_COLS = [
    ("constituents", "Constituents", "int"),
    ("total_market_cap", "Total Mkt Cap", "usd"),
    ("top10_weight", "Top-10 Weight", "pct"),
    ("effective_n", "Effective N", "num"),
]
_QUANTAMENTAL_COLS = [
    ("agg_pe", "P/E (agg)", "num"),
    ("agg_ps", "P/S (agg)", "num"),
    ("median_net_margin", "Net Margin", "pct"),
    ("median_roe", "ROE", "pct"),
    ("median_revenue_growth", "Rev Growth", "pct"),
    ("breadth_above_200d", "% > 200D MA", "pct1"),
]


def _fmt(value: object, kind: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    v = float(value)
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "pct1":  # already a 0-100 percentage
        return f"{v:.0f}%"
    if kind == "int":
        return f"{int(v):,}"
    if kind == "usd":
        for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(v) >= scale:
                return f"${v / scale:.1f}{unit}"
        return f"${v:,.0f}"
    return f"{v:.2f}"  # num


def _section_table(aggregates: list[dict], cols: list[tuple]) -> pd.DataFrame:
    """One row per index, columns formatted per the section spec."""
    rows = {}
    for agg in aggregates:
        label = agg.get("name") or agg.get("index_id")
        rows[label] = {disp: _fmt(agg.get(key), kind) for key, disp, kind in cols}
    return pd.DataFrame(rows).T


def build_comparison_frames(comparison: dict) -> dict[str, pd.DataFrame]:
    """Reshape a get_index_comparison() payload into display DataFrames (pure)."""
    aggregates = comparison.get("aggregates", [])
    sectors = comparison.get("sectors", [])
    sectors_pivot = pd.DataFrame()
    if sectors:
        sdf = pd.DataFrame(sectors)
        id_to_name = {a["index_id"]: (a.get("name") or a["index_id"]) for a in aggregates}
        sdf["index"] = sdf["index_id"].map(id_to_name).fillna(sdf["index_id"])
        sectors_pivot = sdf.pivot_table(
            index="index", columns="sector", values="weight", aggfunc="sum"
        ).fillna(0.0)
    return {
        "performance": _section_table(aggregates, _PERFORMANCE_COLS),
        "construction": _section_table(aggregates, _CONSTRUCTION_COLS),
        "quantamental": _section_table(aggregates, _QUANTAMENTAL_COLS),
        "sectors_pivot": sectors_pivot,
    }


def render_comparison(comparison: dict, performance: pd.DataFrame) -> None:
    """Render the index comparison view (Streamlit + Plotly)."""
    import plotly.express as px
    import streamlit as st

    aggregates = comparison.get("aggregates", [])
    if not aggregates:
        st.info("Index comparison data isn't available yet — run the aggregates job.")
        return

    frames = build_comparison_frames(comparison)

    st.subheader("Relative performance")
    if performance is not None and not performance.empty:
        fig = px.line(performance, labels={"value": "Rebased (=100)", "index": "Date",
                                           "variable": "Index"})
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No ETF-proxy price history yet for the performance chart.")
    st.dataframe(frames["performance"], use_container_width=True)

    st.subheader("Construction")
    st.dataframe(frames["construction"], use_container_width=True)
    if not frames["sectors_pivot"].empty:
        sp = frames["sectors_pivot"].reset_index().melt(
            id_vars="index", var_name="Sector", value_name="Weight")
        fig = px.bar(sp, x="index", y="Weight", color="Sector", barmode="stack",
                     labels={"index": ""})
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), yaxis_tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Quantamental")
    st.dataframe(frames["quantamental"], use_container_width=True)

    disclaimer = comparison.get("provenance", {}).get(
        "disclaimer", "prototype / delayed / unofficial source")
    st.caption(f"⚠️ {disclaimer} · Russell 1000/3000 are market-cap proxies.")
