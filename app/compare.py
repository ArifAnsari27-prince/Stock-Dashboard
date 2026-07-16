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


# Metrics shown side-by-side in the stock comparison table (column, label, kind).
_STOCK_COMPARE_ROWS = [
    ("latest_close", "Price", "usd_px"),
    ("market_cap", "Market cap", "usd"),
    ("sector", "Sector", "str"),
    ("industry", "Industry", "str"),
    ("return_1m", "1M return", "pct"),
    ("return_ytd", "YTD return", "pct"),
    ("return_1y", "1Y return", "pct"),
    ("rsi_14", "RSI(14)", "num"),
    ("price_vs_sma_200", "vs 200D MA", "pct"),
    ("volatility_252d", "Vol (1Y)", "pct"),
    ("beta_qqq", "Beta vs QQQ", "num"),
    ("max_drawdown", "Max drawdown", "pct"),
    ("revenue_growth", "Rev growth", "pct"),
    ("gross_margin", "Gross margin", "pct"),
    ("net_margin", "Net margin", "pct"),
    ("roe", "ROE", "pct"),
    ("fcf_margin", "FCF margin", "pct"),
]


def build_stock_comparison(table: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Side-by-side metric table (pure): one column per symbol, one row per metric."""
    if table.empty or "symbol" not in table.columns or not symbols:
        return pd.DataFrame()
    subset = table[table["symbol"].isin(symbols)].drop_duplicates("symbol").set_index("symbol")
    data: dict[str, list[str]] = {}
    for symbol in symbols:
        if symbol not in subset.index:
            continue
        row = subset.loc[symbol]
        cells: list[str] = []
        for col, _, kind in _STOCK_COMPARE_ROWS:
            value = row.get(col) if col in subset.columns else None
            if kind == "str":
                cells.append(str(value) if value and not pd.isna(value) else "—")
            elif kind == "usd_px":
                cells.append("—" if value is None or pd.isna(value) else f"${float(value):,.2f}")
            else:
                cells.append(_fmt(None if value is None or pd.isna(value) else value, kind))
        data[symbol] = cells
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data, index=[label for _, label, _ in _STOCK_COMPARE_ROWS])


def render_stock_comparison(table: pd.DataFrame, history_loader) -> None:
    """Stock-vs-stock comparison: rebased price chart + side-by-side metrics.

    `history_loader(symbol)` returns a date-indexed OHLCV frame (the cached
    read-API loader from the app shell), so this view never fetches live data.
    """
    import plotly.graph_objects as go
    import streamlit as st

    if table.empty or "symbol" not in table.columns:
        st.info("Stock comparison needs a metrics snapshot.")
        return

    symbols = sorted(table["symbol"].dropna().unique().tolist())
    chosen = st.multiselect(
        "Compare stocks (2–6)", symbols, max_selections=6, key="stock_compare_symbols"
    )
    if len(chosen) < 2:
        st.caption("Pick at least two tickers to compare.")
        return

    series = []
    for symbol in chosen:
        hist = history_loader(symbol)
        if hist is None or hist.empty or "adj_close" not in hist.columns:
            continue
        s = hist["adj_close"].dropna()
        if s.empty:
            continue
        series.append((s / s.iloc[0] * 100.0).rename(symbol))

    if series:
        wide = pd.concat(series, axis=1).sort_index()
        fig = go.Figure()
        for column in wide.columns:
            fig.add_trace(go.Scatter(x=wide.index, y=wide[column], name=column, mode="lines"))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#0a0a0a",
            height=380,
            margin=dict(l=40, r=20, t=36, b=30),
            yaxis_title="Rebased to 100",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.caption("No cached price history for the selected tickers yet.")

    comparison = build_stock_comparison(table, chosen)
    if not comparison.empty:
        st.dataframe(comparison, use_container_width=True)
