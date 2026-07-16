"""Streamlit UI components — Tradytics-inspired dark dashboard."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
import streamlit as st

from app.charts import (
    advancers_bar,
    breadth_gauge,
    median_returns_chart,
    metric_treemap,
    movers_table_figure,
    price_chart,
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
from app.screener_filters import apply_screen, describe_screen, filterable_columns
from app.theme import MUTED, disclaimer_bar, metric_card, metric_grid, section_title


def render_disclaimer(disclaimer: str) -> None:
    """Subtle provenance bar (not st.warning — avoids alarm styling)."""
    disclaimer_bar(disclaimer or "prototype / delayed / unofficial source")


def render_overview_header(
    overview: dict[str, Any], table: pd.DataFrame, index_name: str | None = None
) -> None:
    """Hero metrics + breadth charts (Tradytics 'Markets Today' summary)."""
    render_disclaimer(overview.get("disclaimer", ""))

    if overview.get("constituents", 0) == 0:
        st.info(
            "No data yet — the backend's scheduled jobs haven't produced snapshots. "
            "Once the refresh jobs have written to the data store, metrics will appear here."
        )
        st.stop()

    as_of_str = fmt_date(overview.get("as_of"))
    title = f"{index_name or 'All Indices'} · Market Pulse"

    st.markdown(
        f"""
        <div class="dash-hero">
            <div>
                <p class="dash-title">{title}</p>
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


# Heatmap color metrics: column -> (diverging?, fraction?). Diverging metrics
# are signed (center at 0); sequential ones are unsigned magnitudes.
_HEATMAP_METRICS: dict[str, tuple[bool, bool]] = {
    "return_1d": (True, True),
    "return_5d": (True, True),
    "return_1m": (True, True),
    "return_3m": (True, True),
    "return_ytd": (True, True),
    "return_1y": (True, True),
    "momentum_6m": (True, True),
    "price_vs_sma_50": (True, True),
    "price_vs_sma_200": (True, True),
    "revenue_growth": (True, True),
    "drawdown_52w": (True, True),
    "rsi_14": (False, False),
    "volatility_252d": (False, True),
    "relative_volume_20": (False, False),
    "gross_margin": (True, True),
    "net_margin": (True, True),
    "fcf_margin": (True, True),
    "roe": (True, True),
    "roic": (True, True),
}

_HEATMAP_GROUPINGS: dict[str, list[str]] = {
    "Sector": ["sector"],
    "Sector → Industry": ["sector", "industry"],
    "Industry": ["industry"],
    "Flat (no grouping)": [],
}


def render_heatmap(table: pd.DataFrame) -> None:
    """Market heatmap treemap, filterable and groupable by sector/industry/metric."""
    section_title("Market Heatmap")
    if table.empty:
        st.info("Heatmap is empty — waiting for a metrics snapshot.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_options = [m for m in _HEATMAP_METRICS if m in table.columns]
        metric = st.selectbox(
            "Color by",
            metric_options,
            format_func=lambda c: COLUMN_LABELS.get(c, c),
            key="heatmap_metric",
        )
    with c2:
        grouping_options = [
            label
            for label, cols in _HEATMAP_GROUPINGS.items()
            if all(c in table.columns for c in cols)
        ]
        grouping = st.selectbox("Group by", grouping_options, key="heatmap_grouping")
    with c3:
        size_options = [c for c in ("market_cap", "volume", "latest_close") if c in table.columns]
        size_by = st.selectbox(
            "Tile size",
            size_options or ["equal"],
            format_func=lambda c: COLUMN_LABELS.get(c, c),
            key="heatmap_size",
        )

    f1, f2 = st.columns(2)
    view = table
    with f1:
        if "sector" in table.columns:
            sectors = sorted(table["sector"].dropna().unique().tolist())
            chosen_sectors = st.multiselect("Sectors", sectors, key="heatmap_sectors")
            if chosen_sectors:
                view = view[view["sector"].isin(chosen_sectors)]
    with f2:
        if "industry" in view.columns:
            industries = sorted(view["industry"].dropna().unique().tolist())
            chosen_industries = st.multiselect("Industries", industries, key="heatmap_industries")
            if chosen_industries:
                view = view[view["industry"].isin(chosen_industries)]

    max_names = st.slider("Max names (largest first)", 50, 1000, 300, step=50,
                          key="heatmap_max_names")
    if "market_cap" in view.columns and len(view) > max_names:
        view = view.nlargest(max_names, "market_cap")

    diverging, is_fraction = _HEATMAP_METRICS.get(metric, (True, True))
    st.plotly_chart(
        metric_treemap(
            view,
            metric=metric,
            group_by=_HEATMAP_GROUPINGS.get(grouping, []),
            size_by=size_by,
            is_fraction=is_fraction,
            diverging=diverging,
            metric_label=COLUMN_LABELS.get(metric, metric),
        ),
        use_container_width=True,
        config={"displayModeBar": False},
    )
    st.caption(f"{len(view)} names shown · tile size = {COLUMN_LABELS.get(size_by, size_by)}")


def _parse_bound(raw: str) -> float | None:
    """Parse a filter bound text input; blank/invalid -> None (no bound)."""
    text = (raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_spec_into_state(spec: dict[str, Any]) -> None:
    """Push a saved screen spec into the filter widgets' session state."""
    st.session_state["scr_sectors"] = spec.get("sectors") or []
    st.session_state["scr_industries"] = spec.get("industries") or []
    rules = spec.get("numeric") or []
    st.session_state["scr_filter_cols"] = [r["column"] for r in rules if r.get("column")]
    for rule in rules:
        col = rule.get("column")
        if not col:
            continue
        st.session_state[f"scr_min_{col}"] = "" if rule.get("min") is None else str(rule["min"])
        st.session_state[f"scr_max_{col}"] = "" if rule.get("max") is None else str(rule["max"])
    st.session_state["scr_sort"] = spec.get("sort_by") or "(none)"
    st.session_state["scr_asc"] = "Ascending" if spec.get("ascending") else "Descending"


def _render_filter_builder(table: pd.DataFrame) -> dict[str, Any]:
    """Filter/sort controls; returns the screen spec they currently describe."""
    spec: dict[str, Any] = {"numeric": [], "sectors": [], "industries": []}

    cat1, cat2 = st.columns(2)
    with cat1:
        if "sector" in table.columns:
            sectors = sorted(table["sector"].dropna().unique().tolist())
            spec["sectors"] = st.multiselect("Sector", sectors, key="scr_sectors")
    with cat2:
        if "industry" in table.columns:
            industries = sorted(table["industry"].dropna().unique().tolist())
            spec["industries"] = st.multiselect("Industry", industries, key="scr_industries")

    candidates = filterable_columns(table)
    chosen_cols = st.multiselect(
        "Numeric filters (pick metrics, then set bounds)",
        candidates,
        format_func=lambda c: COLUMN_LABELS.get(c, c),
        key="scr_filter_cols",
    )
    for col in chosen_cols:
        label = COLUMN_LABELS.get(col, col)
        b1, b2 = st.columns(2)
        with b1:
            lo = _parse_bound(st.text_input(f"{label} · min", key=f"scr_min_{col}"))
        with b2:
            hi = _parse_bound(st.text_input(f"{label} · max", key=f"scr_max_{col}"))
        spec["numeric"].append({"column": col, "min": lo, "max": hi})

    s1, s2 = st.columns(2)
    with s1:
        sort_options = ["(none)"] + candidates
        sort_by = st.selectbox(
            "Sort by",
            sort_options,
            format_func=lambda c: COLUMN_LABELS.get(c, c),
            key="scr_sort",
        )
    with s2:
        direction = st.radio(
            "Direction", ["Descending", "Ascending"], horizontal=True, key="scr_asc"
        )
    spec["sort_by"] = None if sort_by == "(none)" else sort_by
    spec["ascending"] = direction == "Ascending"
    return spec


def _render_saved_screens(
    spec: dict[str, Any],
    saved_screens: dict[str, dict],
    on_save: Callable[[str, dict], None],
    on_delete: Callable[[str], None],
) -> None:
    """Load/save/delete controls for persisted screener configurations."""
    load_col, save_col = st.columns(2)
    with load_col:
        names = ["(choose a saved screen)"] + sorted(saved_screens)
        picked = st.selectbox("Saved screens", names, key="scr_saved_pick")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Load", use_container_width=True, disabled=picked == names[0]):
                _load_spec_into_state(saved_screens[picked])
                st.rerun()
        with b2:
            if st.button("Delete", use_container_width=True, disabled=picked == names[0]):
                on_delete(picked)
                st.rerun()
        if picked != names[0]:
            st.caption(describe_screen(saved_screens[picked]))
    with save_col:
        new_name = st.text_input("Save current filters as", key="scr_save_name")
        if st.button("Save screen", use_container_width=True, disabled=not new_name.strip()):
            on_save(new_name.strip(), spec)
            st.rerun()


def render_screener(
    table: pd.DataFrame,
    saved_screens: dict[str, dict] | None = None,
    on_save: Callable[[str, dict], None] | None = None,
    on_delete: Callable[[str], None] | None = None,
) -> str | None:
    """Customizable screener: parameter filters, sorting, saved screens, row-click."""
    if table.empty:
        st.info("Screener table is empty — waiting for a metrics snapshot.")
        return None

    section_title("Screener")

    c1, c2 = st.columns([2, 3])
    with c1:
        search = st.text_input("Search", placeholder="Symbol or company…", label_visibility="collapsed")
    with c2:
        extra = [c for c in table.columns if c not in DEFAULT_SCREENER_COLUMNS]
        chosen = st.multiselect(
            "Columns",
            options=DEFAULT_SCREENER_COLUMNS + sorted(extra),
            default=[c for c in DEFAULT_SCREENER_COLUMNS if c in table.columns],
        )

    with st.expander("Filters & sort", expanded=False):
        spec = _render_filter_builder(table)
        if saved_screens is not None and on_save and on_delete:
            st.divider()
            _render_saved_screens(spec, saved_screens, on_save, on_delete)

    view = apply_screen(table, spec)
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


def render_news(news: pd.DataFrame, *, title: str = "Headlines", limit: int = 25) -> None:
    """Cached news headlines list (OpenStock-style): source · time · link · summary."""
    section_title(title)
    if news is None or news.empty:
        st.caption(
            "No cached headlines. News is optional — it appears once the scheduled "
            "news job has run (requires FINNHUB_API_KEY)."
        )
        return
    for _, row in news.head(limit).iterrows():
        headline = str(row.get("headline") or "").strip()
        if not headline:
            continue
        url = row.get("url")
        source = row.get("source") or "—"
        published = fmt_date(row.get("published_at"))
        symbol = row.get("symbol")
        tag = f"**{symbol}** · " if isinstance(symbol, str) and symbol else ""
        if isinstance(url, str) and url:
            st.markdown(f"{tag}[{headline}]({url})")
        else:
            st.markdown(f"{tag}{headline}")
        st.caption(f"{source} · {published}")
        summary = row.get("summary")
        if isinstance(summary, str) and summary.strip():
            with st.expander("Summary"):
                st.write(summary.strip())


def render_tearsheet(
    sheet: dict[str, Any], hist: pd.DataFrame, news: pd.DataFrame | None = None
) -> None:
    """Ticker detail: charts + metric cards + filings + cached headlines."""
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

    if news is not None:
        render_news(news, title="Recent News", limit=10)

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
