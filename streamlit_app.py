"""Nasdaq 100 metrics dashboard — Streamlit frontend (read-only, cached snapshots)."""

from __future__ import annotations

import streamlit as st

from app.theme import inject_theme
from app.ui import render_heatmap, render_overview_header, render_screener, render_tearsheet
from src.api.read_api import get_market_overview, get_price_history, get_table, get_tearsheet

st.set_page_config(
    page_title="Nasdaq 100 Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data(ttl=600)
def load_overview() -> dict:
    return get_market_overview()


@st.cache_data(ttl=600)
def load_table():
    return get_table()


@st.cache_data(ttl=600)
def load_tearsheet(symbol: str) -> dict:
    return get_tearsheet(symbol)


@st.cache_data(ttl=600)
def load_price_history(symbol: str):
    return get_price_history(symbol)


def _clear_data_cache() -> None:
    load_overview.clear()
    load_table.clear()
    load_tearsheet.clear()
    load_price_history.clear()


def main() -> None:
    inject_theme()

    with st.sidebar:
        st.markdown("### Controls")
        if st.button("↻ Refresh data", use_container_width=True):
            _clear_data_cache()
            st.rerun()
        st.caption("Reloads Parquet snapshots from `data/`.")

    overview = load_overview()
    table = load_table()

    render_overview_header(overview, table)

    tab_heatmap, tab_screener, tab_tearsheet = st.tabs(
        ["Heatmap", "Screener", "Ticker"]
    )

    with tab_heatmap:
        render_heatmap(table)

    with tab_screener:
        selected = render_screener(table)
        if selected:
            st.session_state["tearsheet_symbol"] = selected
            st.markdown(
                f'<span class="dash-badge">Selected: {selected} → open Ticker tab</span>',
                unsafe_allow_html=True,
            )

    with tab_tearsheet:
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
