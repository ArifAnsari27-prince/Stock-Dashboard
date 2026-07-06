"""Multi-index metrics dashboard — Streamlit frontend (read-only, cached snapshots)."""

from __future__ import annotations

import streamlit as st

from app.compare import render_comparison
from app.theme import inject_theme
from app.ui import render_heatmap, render_overview_header, render_screener, render_tearsheet
from src.api.read_api import (
    get_index_comparison,
    get_index_performance,
    get_indices,
    get_market_overview,
    get_price_history,
    get_table,
    get_tearsheet,
)

st.set_page_config(
    page_title="Index Metrics Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data(ttl=600)
def load_overview() -> dict:
    return get_market_overview()


@st.cache_data(ttl=600)
def load_table(index: str | None = None):
    return get_table(index=index)


@st.cache_data(ttl=600)
def load_indices() -> list[dict]:
    return get_indices()


@st.cache_data(ttl=600)
def load_index_comparison() -> dict:
    return get_index_comparison()


@st.cache_data(ttl=600)
def load_index_performance():
    return get_index_performance()


@st.cache_data(ttl=600)
def load_tearsheet(symbol: str) -> dict:
    return get_tearsheet(symbol)


@st.cache_data(ttl=600)
def load_price_history(symbol: str):
    return get_price_history(symbol)


def _clear_data_cache() -> None:
    for loader in (load_overview, load_table, load_indices, load_index_comparison,
                   load_index_performance, load_tearsheet, load_price_history):
        loader.clear()


def main() -> None:
    inject_theme()

    indices = load_indices()

    selected_index: str | None = None
    with st.sidebar:
        st.markdown("### Controls")
        if indices:
            name_to_id = {i.get("name") or i["index_id"]: i["index_id"] for i in indices}
            choice = st.selectbox("Index", ["All indices", *name_to_id])
            selected_index = name_to_id.get(choice)
        if st.button("↻ Refresh data", use_container_width=True):
            _clear_data_cache()
            st.rerun()
        st.caption("Reloads cached snapshots from the data store.")

    overview = load_overview()
    table = load_table(selected_index)

    render_overview_header(overview, table)

    tabs = ["Compare", "Heatmap", "Screener", "Ticker"] if indices else \
        ["Heatmap", "Screener", "Ticker"]
    rendered = st.tabs(tabs)
    tab_map = dict(zip(tabs, rendered))

    if "Compare" in tab_map:
        with tab_map["Compare"]:
            render_comparison(load_index_comparison(), load_index_performance())

    with tab_map["Heatmap"]:
        render_heatmap(table)

    with tab_map["Screener"]:
        selected = render_screener(table)
        if selected:
            st.session_state["tearsheet_symbol"] = selected
            st.markdown(
                f'<span class="dash-badge">Selected: {selected} → open Ticker tab</span>',
                unsafe_allow_html=True,
            )

    with tab_map["Ticker"]:
        symbols: list[str] = []
        if not table.empty and "symbol" in table.columns:
            symbols = sorted(table["symbol"].dropna().unique().tolist())

        if not symbols:
            st.info("Select a ticker once screener data is available.")
        else:
            default = st.session_state.get("tearsheet_symbol")
            idx = symbols.index(default) if default in symbols else 0
            symbol = st.selectbox("Ticker", symbols, index=idx, key="tearsheet_picker")
            st.session_state["tearsheet_symbol"] = symbol
            sheet = load_tearsheet(symbol)
            hist = load_price_history(symbol)
            render_tearsheet(sheet, hist)


if __name__ == "__main__":
    main()
