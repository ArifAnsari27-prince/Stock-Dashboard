"""Multi-index metrics dashboard — Streamlit frontend (read-only, cached snapshots)."""

from __future__ import annotations

import os

import streamlit as st

# Must be the first Streamlit command — even before reading st.secrets.
st.set_page_config(
    page_title="Index Metrics Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def _bridge_secrets_to_env() -> None:
    """Copy Streamlit Cloud secrets into os.environ (existing env wins).

    src/config.py reads only environment variables; on Streamlit Community
    Cloud, credentials (DATA_URI, R2_*) arrive via st.secrets. Bridging here —
    before any src import resolves config — makes both deployments identical.
    """
    try:
        secrets = dict(st.secrets)
    except Exception:  # no secrets.toml locally — normal
        return
    for key, value in secrets.items():
        if isinstance(value, str | int | float | bool):
            os.environ.setdefault(key, str(value))


_bridge_secrets_to_env()

from app.compare import render_comparison, render_stock_comparison  # noqa: E402
from app.theme import inject_theme  # noqa: E402
from app.ui import (  # noqa: E402
    render_heatmap,
    render_news,
    render_overview_header,
    render_screener,
    render_tearsheet,
)
from src.api.read_api import (  # noqa: E402
    get_index_comparison,
    get_index_performance,
    get_indices,
    get_market_overview,
    get_news,
    get_price_history,
    get_table,
    get_tearsheet,
)
from src.api.user_store import (  # noqa: E402
    delete_saved_screen,
    list_saved_screens,
    save_saved_screen,
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


@st.cache_data(ttl=600)
def load_news(symbol: str | None = None):
    return get_news(symbol=symbol)


@st.cache_data(ttl=60)
def load_saved_screens() -> dict[str, dict]:
    return list_saved_screens()


def _clear_data_cache() -> None:
    for loader in (load_overview, load_table, load_indices, load_index_comparison,
                   load_index_performance, load_tearsheet, load_price_history,
                   load_news, load_saved_screens):
        loader.clear()


def _on_save_screen(name: str, params: dict) -> None:
    save_saved_screen(name, params)
    load_saved_screens.clear()


def _on_delete_screen(name: str) -> None:
    delete_saved_screen(name)
    load_saved_screens.clear()


def main() -> None:
    inject_theme()

    indices = load_indices()

    selected_index: str | None = None
    selected_name: str | None = None
    with st.sidebar:
        st.markdown("### Controls")
        if indices:
            name_to_id = {i.get("name") or i["index_id"]: i["index_id"] for i in indices}
            choice = st.selectbox("Index", ["All indices", *name_to_id])
            selected_index = name_to_id.get(choice)
            selected_name = choice if selected_index else None
        if st.button("↻ Refresh data", use_container_width=True):
            _clear_data_cache()
            st.rerun()
        st.caption("Reloads cached snapshots from the data store.")

    overview = load_overview()
    table = load_table(selected_index)

    render_overview_header(overview, table, index_name=selected_name)

    tabs = ["Compare", "Heatmap", "Screener", "News", "Ticker"] if indices else \
        ["Heatmap", "Screener", "News", "Ticker"]
    rendered = st.tabs(tabs)
    tab_map = dict(zip(tabs, rendered, strict=False))

    if "Compare" in tab_map:
        with tab_map["Compare"]:
            view = st.radio(
                "Compare", ["Indices", "Stocks"], horizontal=True,
                label_visibility="collapsed", key="compare_view",
            )
            if view == "Indices":
                render_comparison(load_index_comparison(), load_index_performance())
            else:
                render_stock_comparison(table, load_price_history)

    with tab_map["Heatmap"]:
        render_heatmap(table)

    with tab_map["Screener"]:
        selected = render_screener(
            table,
            saved_screens=load_saved_screens(),
            on_save=_on_save_screen,
            on_delete=_on_delete_screen,
        )
        if selected:
            st.session_state["tearsheet_symbol"] = selected
            st.markdown(
                f'<span class="dash-badge">Selected: {selected} → open Ticker tab</span>',
                unsafe_allow_html=True,
            )

    with tab_map["News"]:
        render_news(load_news(None), title="Market Headlines")
        symbol_query = st.text_input(
            "Company headlines for symbol", placeholder="e.g. NVDA",
            key="news_symbol_query",
        )
        if symbol_query.strip():
            symbol = symbol_query.strip().upper()
            render_news(load_news(symbol), title=f"{symbol} Headlines")

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
            render_tearsheet(sheet, hist, news=load_news(symbol))


if __name__ == "__main__":
    main()
