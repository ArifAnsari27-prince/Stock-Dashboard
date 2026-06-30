"""Streamlit UI components — Tradytics-inspired dark dashboard."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from app.charts import (
    advancers_bar,
    breadth_gauge,
    median_returns_chart,
    movers_table_figure,
    price_chart,
    return_treemap,
)
from app.columns import (
    COLUMN_LABELS,
    DEFAULT_SCREENER_COLUMNS,
    FILING_FORM_LABELS,
    FRACTION_COLUMNS,
    RETURN_COLUMNS,
    TEARSHEET_GROUPS,
    USD_LARGE_COLUMNS,
    USD_PRICE_COLUMNS,
    VALUATION_COLUMNS,
)
from app.formatters import (
    NA,
    fmt_date,
    fmt_number,
    fmt_pct,
    fmt_shares,
    fmt_usd,
    fmt_usd_large,
    is_missing,
)
from app.theme import MUTED, disclaimer_bar, metric_card, metric_grid, section_title


def render_disclaimer(disclaimer: str) -> None:
    """Subtle provenance bar (not st.warning — avoids alarm styling)."""
    disclaimer_bar(disclaimer or "prototype / delayed / unofficial source")


def render_overview_header(overview: dict[str, Any], table: pd.DataFrame) -> None:
    """Hero metrics + breadth charts (Tradytics 'Markets Today' summary)."""
    render_disclaimer(overview.get("disclaimer", ""))

    if overview.get("constituents", 0) == 0:
        st.info(
            "No data yet — the backend's scheduled jobs haven't produced snapshots. "
            "Once GitHub Actions commits Parquet files to `data/`, metrics will appear here."
        )
        st.stop()

    as_of_str = fmt_date(overview.get("as_of"))

    st.markdown(
        f"""
        <div class="dash-hero">
            <div>
                <p class="dash-title">Nasdaq 100 · Market Pulse</p>
                <p class="dash-subtitle">Cached metrics dashboard · data as of {as_of_str}</p>
            </div>
            <span class="dash-badge">{overview["constituents"]} constituents</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    med_1d = overview.get("median_return_1d")
    tone_1d = "accent" if not is_missing(med_1d) else "neutral"

    metric_grid(
        [
            metric_card("Above 50D MA", _metric_pct(overview.get("pct_above_sma_50")), tone="accent"),
            metric_card("Above 200D MA", _metric_pct(overview.get("pct_above_sma_200")), tone="accent"),
            metric_card("Advancers", str(overview.get("advancers", NA)), sub="today", tone="accent"),
            metric_card("Decliners", str(overview.get("decliners", NA)), sub="today", tone="negative"),
            metric_card("Median 1D", _metric_fraction(med_1d), tone=tone_1d),
            metric_card("Median RSI", _metric_rsi(overview.get("median_rsi_14"))),
        ]
    )

    left, right = st.columns([1.1, 1])
    with left:
        g1, g2 = st.columns(2)
        with g1:
            st.plotly_chart(
                breadth_gauge("Above 50D MA", overview.get("pct_above_sma_50")),
                use_container_width=True,
                config={"displayModeBar": False},
            )
        with g2:
            st.plotly_chart(
                breadth_gauge("Above 200D MA", overview.get("pct_above_sma_200"), color="#38bdf8"),
                use_container_width=True,
                config={"displayModeBar": False},
            )
        st.plotly_chart(
            median_returns_chart(overview),
            use_container_width=True,
            config={"displayModeBar": False},
        )
    with right:
        adv = int(overview.get("advancers", 0) or 0)
        dec = int(overview.get("decliners", 0) or 0)
        st.plotly_chart(
            advancers_bar(adv, dec),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        section_title("Biggest Gainers · 1D")
        st.plotly_chart(
            movers_table_figure(table, ascending=False),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        section_title("Biggest Losers · 1D")
        st.plotly_chart(
            movers_table_figure(table, ascending=True),
            use_container_width=True,
            config={"displayModeBar": False},
        )


def _metric_pct(value: Any) -> str:
    if is_missing(value):
        return NA
    return f"{float(value):.0f}%"


def _metric_fraction(value: Any) -> str:
    if is_missing(value):
        return NA
    return fmt_pct(value)


def _metric_rsi(value: Any) -> str:
    if is_missing(value):
        return NA
    return f"{float(value):.1f}"


def _screener_styler(df: pd.DataFrame) -> Any:
    """Format via pandas Styler only — never pair with column_config (causes ⚠ triangles)."""
    formatters: dict[str, Any] = {}
    for col in df.columns:
        if col in FRACTION_COLUMNS:
            formatters[col] = "{:.2%}"
        elif col in USD_PRICE_COLUMNS:
            formatters[col] = "${:,.2f}"
        elif col in USD_LARGE_COLUMNS:
            formatters[col] = "${:,.0f}"
        elif col == "rsi_14":
            formatters[col] = "{:.1f}"
        elif col in ("beta_qqq", "beta_spy", "correlation_qqq", "correlation_spy"):
            formatters[col] = "{:.2f}"
        elif col in ("volume", "shares_outstanding"):
            formatters[col] = "{:,.0f}"

    def color_return(val: Any) -> str:
        if is_missing(val):
            return f"color: {MUTED}"
        v = float(val)
        if v > 0:
            return "color: #34d399; font-weight: 600"
        if v < 0:
            return "color: #f87171; font-weight: 600"
        return f"color: {MUTED}"

    def color_rsi(val: Any) -> str:
        if is_missing(val):
            return f"color: {MUTED}"
        v = float(val)
        if v >= 70:
            return "color: #fbbf24; font-weight: 600"
        if v <= 30:
            return "color: #38bdf8; font-weight: 600"
        return "color: #ffffff"

    styler = df.style.format(formatters, na_rep=NA)
    return_cols = [c for c in RETURN_COLUMNS if c in df.columns]
    if return_cols:
        styler = styler.map(color_return, subset=return_cols)
    if "rsi_14" in df.columns:
        styler = styler.map(color_rsi, subset=["rsi_14"])
    return styler


def render_heatmap(table: pd.DataFrame) -> None:
    """Market heatmap treemap tab."""
    section_title("Market Heatmap · 1D Return")
    metric = st.selectbox(
        "Color by",
        ["return_1d", "return_1m", "return_ytd", "return_1y"],
        format_func=lambda c: COLUMN_LABELS.get(c, c),
        key="heatmap_metric",
    )
    st.plotly_chart(
        return_treemap(table, metric=metric),
        use_container_width=True,
        config={"displayModeBar": False},
    )


def render_screener(table: pd.DataFrame) -> str | None:
    """Sortable screener grid — Styler formatting only (no column_config)."""
    if table.empty:
        st.info("Screener table is empty — waiting for a metrics snapshot.")
        return None

    section_title("Active Stocks · Nasdaq 100")

    c1, c2 = st.columns([2, 3])
    with c1:
        search = st.text_input("Search", placeholder="Symbol or company…", label_visibility="collapsed")
    with c2:
        extra = [c for c in table.columns if c not in DEFAULT_SCREENER_COLUMNS]
        chosen = st.multiselect(
            "Columns",
            options=DEFAULT_SCREENER_COLUMNS + sorted(extra),
            default=DEFAULT_SCREENER_COLUMNS,
        )

    view = table.copy()
    if search.strip():
        q = search.strip().lower()
        mask = pd.Series(False, index=view.index)
        if "symbol" in view.columns:
            mask |= view["symbol"].astype(str).str.lower().str.contains(q, na=False)
        if "name" in view.columns:
            mask |= view["name"].astype(str).str.lower().str.contains(q, na=False)
        view = view[mask]

    display_cols = [c for c in chosen if c in view.columns]
    if not display_cols:
        st.warning("Select at least one column to display.")
        return None

    display_df = view[display_cols].copy()

    selection = st.dataframe(
        _screener_styler(display_df),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        height=min(560, 38 + 35 * len(view)),
    )

    selected_symbol: str | None = None
    if selection.selection.rows:
        row_idx = selection.selection.rows[0]
        if row_idx < len(view) and "symbol" in view.columns:
            selected_symbol = str(view.iloc[row_idx]["symbol"])

    st.caption(f"{len(view)} of {len(table)} names · click a row for tearsheet")
    return selected_symbol


def _format_metric_value(col: str, value: Any) -> str:
    if col in VALUATION_COLUMNS:
        return NA
    if is_missing(value):
        return NA
    if col in FRACTION_COLUMNS:
        return fmt_pct(value)
    if col in USD_PRICE_COLUMNS:
        return fmt_usd(value)
    if col in USD_LARGE_COLUMNS:
        return fmt_usd_large(value)
    if col == "rsi_14":
        return fmt_number(value, 1)
    if col in ("beta_qqq", "beta_spy", "correlation_qqq", "correlation_spy"):
        return fmt_number(value, 2)
    if col == "volume":
        return fmt_shares(value)
    if col == "shares_outstanding":
        return fmt_shares(value)
    if col in ("as_of", "period_end"):
        return fmt_date(value)
    if col in ("symbol", "name", "fiscal_period", "cik"):
        return str(value)
    if col == "bollinger_percent_b":
        return fmt_number(value, 2)
    if col == "relative_volume_20":
        return fmt_number(value, 2)
    return str(value)


def _metric_tone(col: str, value: Any) -> str:
    if col in RETURN_COLUMNS and not is_missing(value):
        v = float(value)
        return "negative" if v < 0 else "accent"
    return "neutral"


def render_tearsheet(sheet: dict[str, Any], hist: pd.DataFrame) -> None:
    """Ticker detail: charts + metric cards + filings."""
    symbol = sheet.get("symbol", "")

    if not sheet.get("found"):
        st.warning(f"No metrics found for {symbol or 'this ticker'}.")
        return

    data: dict[str, Any] = sheet.get("data") or {}
    if not data:
        st.info("Ticker found but row data is empty.")
        return

    name = data.get("name", "")
    close = data.get("latest_close")
    ret_1d = data.get("return_1d")

    st.markdown(
        f"""
        <div class="dash-hero">
            <div>
                <p class="dash-title">{symbol}</p>
                <p class="dash-subtitle">{name} · {fmt_date(data.get('as_of'))}</p>
            </div>
            <span class="dash-badge">{fmt_usd(close)} · {fmt_pct(ret_1d)} 1D</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not hist.empty:
        st.plotly_chart(
            price_chart(hist, symbol),
            use_container_width=True,
            config={"displayModeBar": True, "scrollZoom": True},
        )
    else:
        st.caption("Price history not available yet for this symbol.")

    for group_name, fields in TEARSHEET_GROUPS.items():
        section_title(group_name)
        cards: list[str] = []
        for col_key, label in fields:
            if col_key not in data and col_key not in VALUATION_COLUMNS:
                continue
            cards.append(
                metric_card(
                    label,
                    _format_metric_value(col_key, data.get(col_key)),
                    tone=_metric_tone(col_key, data.get(col_key)),
                )
            )
        if cards:
            metric_grid(cards)
        else:
            st.caption("No metrics in this group.")

    section_title("SEC Filings")
    filings: list[dict[str, Any]] = sheet.get("filings") or []
    if not filings:
        st.caption("No filing links available yet for this symbol.")
        return

    preferred_order = ("10-K", "10-Q", "8-K", "4")
    by_form: dict[str, dict[str, Any]] = {}
    for filing in filings:
        form = filing.get("form", "")
        if form not in by_form:
            by_form[form] = filing

    for form in preferred_order:
        filing = by_form.get(form)
        if not filing:
            continue
        label = FILING_FORM_LABELS.get(form, form)
        filed = fmt_date(filing.get("filed_date"))
        url = filing.get("url", "")
        if url:
            st.markdown(
                f'<a class="filing-link" href="{url}" target="_blank">{label} · filed {filed}</a>',
                unsafe_allow_html=True,
            )
