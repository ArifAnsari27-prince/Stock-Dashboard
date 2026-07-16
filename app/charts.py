"""Plotly charts — colorful series on dark background, read_api data only."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.formatters import is_missing

# Dark canvas (matches B&W UI shell) with vivid chart colors
_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#0a0a0a",
    font=dict(color="#ffffff", size=11),
    margin=dict(l=40, r=20, t=36, b=30),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color="#a3a3a3")),
)
_POS = "#34d399"
_NEG = "#f87171"
_ACCENT = "#5b8def"
_ACCENT2 = "#38bdf8"
_WARN = "#fbbf24"
_GRID = "rgba(255,255,255,0.08)"


def _layout(**overrides: Any) -> dict[str, Any]:
    base = dict(_LAYOUT)
    base.update(overrides)
    return base


def breadth_gauge(label: str, value: float | None, *, color: str = _ACCENT) -> go.Figure:
    val = 0.0 if is_missing(value) else float(value)
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=val,
            number=dict(suffix="%", font=dict(size=22, color="#ffffff")),
            title=dict(text=label, font=dict(size=12, color="#a3a3a3")),
            gauge=dict(
                axis=dict(range=[0, 100], tickwidth=0, tickcolor="#525252"),
                bar=dict(color=color),
                bgcolor="rgba(255,255,255,0.06)",
                borderwidth=0,
                steps=[
                    dict(range=[0, 40], color="rgba(248,113,113,0.25)"),
                    dict(range=[40, 60], color="rgba(251,191,36,0.2)"),
                    dict(range=[60, 100], color="rgba(52,211,153,0.25)"),
                ],
            ),
        )
    )
    fig.update_layout(**_layout(height=190, margin=dict(l=20, r=20, t=50, b=10)))
    return fig


def advancers_bar(advancers: int, decliners: int) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            y=["Market"],
            x=[advancers],
            name="Advancers",
            orientation="h",
            marker_color=_POS,
        )
    )
    fig.add_trace(
        go.Bar(
            y=["Market"],
            x=[-decliners],
            name="Decliners",
            orientation="h",
            marker_color=_NEG,
        )
    )
    fig.update_layout(
        **_layout(
            height=120,
            barmode="relative",
            xaxis=dict(title="", showgrid=False, zeroline=True, zerolinecolor="#525252"),
            yaxis=dict(showticklabels=False),
        )
    )
    return fig


_DIVERGING_SCALE = [
    [0.0, "#7f1d1d"],
    [0.35, "#991b1b"],
    [0.5, "#1f2937"],
    [0.65, "#14532d"],
    [1.0, "#059669"],
]


def metric_treemap(
    table: pd.DataFrame,
    *,
    metric: str = "return_1d",
    group_by: list[str] | None = None,
    size_by: str = "market_cap",
    is_fraction: bool = True,
    diverging: bool = True,
    metric_label: str | None = None,
) -> go.Figure:
    """Treemap of the universe colored by `metric`.

    `group_by` nests tiles under grouping columns (e.g. ["sector"] or
    ["sector", "industry"]); tile area is `size_by` (falls back to equal sizes
    when absent/null). `is_fraction` picks % vs plain number formatting;
    `diverging=False` uses a sequential scale (for unsigned metrics like RSI).
    """
    group_by = [c for c in (group_by or []) if c in table.columns]
    if table.empty or metric not in table.columns:
        return go.Figure()

    keep = ["symbol", "name", metric] + group_by
    if size_by in table.columns:
        keep.append(size_by)
    df = table[list(dict.fromkeys(keep))].dropna(subset=["symbol", metric]).copy()
    if df.empty:
        return go.Figure()

    for col in group_by:
        df[col] = df[col].fillna("Unknown")

    labels = df["symbol"] + " · " + df["name"].fillna("").str.slice(0, 18)
    if size_by in df.columns:
        df["_tile_size"] = pd.to_numeric(df[size_by], errors="coerce").fillna(1).clip(lower=1)
    else:
        df["_tile_size"] = 1.0

    # Build the hierarchy: group levels as parent nodes, symbols as leaves.
    node_ids: list[str] = []
    node_labels: list[str] = []
    node_parents: list[str] = []
    node_values: list[float] = []
    node_colors: list[float | None] = []

    for depth in range(len(group_by)):
        levels = group_by[: depth + 1]
        sizes = df.groupby(levels, dropna=False)["_tile_size"].sum()
        for key, total in sizes.items():
            key_tuple = key if isinstance(key, tuple) else (key,)
            node_ids.append("/".join(str(k) for k in key_tuple))
            node_labels.append(str(key_tuple[-1]))
            node_parents.append("/".join(str(k) for k in key_tuple[:-1]))
            node_values.append(float(total))
            node_colors.append(None)

    leaf_parent = (
        df[group_by].astype(str).agg("/".join, axis=1) if group_by
        else pd.Series("", index=df.index)
    )
    leaf_ids = (leaf_parent + "/" + df["symbol"]) if group_by else df["symbol"]
    node_ids += leaf_ids.tolist()
    node_labels += labels.tolist()
    node_parents += leaf_parent.tolist()
    node_values += df["_tile_size"].tolist()
    node_colors += df[metric].tolist()

    value_format = ".2%" if is_fraction else ",.1f"
    marker = dict(
        colors=[c if c is not None else 0 for c in node_colors],
        showscale=True,
        colorbar=dict(
            title=metric_label or metric,
            tickformat=".1%" if is_fraction else ",.0f",
            tickcolor="#a3a3a3",
        ),
    )
    if diverging:
        marker["colorscale"] = _DIVERGING_SCALE
        marker["cmid"] = 0
    else:
        marker["colorscale"] = [[0.0, "#1f2937"], [1.0, "#059669"]]

    fig = go.Figure(
        go.Treemap(
            ids=node_ids,
            labels=node_labels,
            parents=node_parents,
            values=node_values,
            branchvalues="total",
            marker=marker,
            textfont=dict(color="#ffffff"),
            texttemplate=f"%{{label}}<br>%{{color:{value_format}}}",
            hovertemplate=f"<b>%{{label}}</b><br>%{{color:{value_format}}}<extra></extra>",
        )
    )
    fig.update_layout(**_layout(height=520, margin=dict(l=10, r=10, t=30, b=10)))
    return fig


def return_treemap(table: pd.DataFrame, *, metric: str = "return_1d") -> go.Figure:
    """Back-compat wrapper: flat return-colored treemap."""
    return metric_treemap(table, metric=metric, group_by=None)


def movers_table_figure(
    table: pd.DataFrame,
    *,
    ascending: bool = False,
    n: int = 8,
) -> go.Figure:
    if table.empty or "return_1d" not in table.columns:
        return go.Figure()

    df = table.dropna(subset=["return_1d"]).sort_values("return_1d", ascending=ascending).head(n)
    if df.empty:
        return go.Figure()

    colors = [_POS if v >= 0 else _NEG for v in df["return_1d"]]
    fig = go.Figure(
        go.Bar(
            y=df["symbol"],
            x=df["return_1d"],
            orientation="h",
            marker_color=colors,
            text=[f"{v:.2%}" for v in df["return_1d"]],
            textposition="outside",
            textfont=dict(color="#ffffff"),
        )
    )
    fig.update_layout(
        **_layout(
            height=max(220, 28 * len(df) + 60),
            xaxis=dict(tickformat=".1%", showgrid=True, gridcolor=_GRID, tickfont=dict(color="#a3a3a3")),
            yaxis=dict(autorange="reversed", tickfont=dict(color="#ffffff")),
            showlegend=False,
        )
    )
    return fig


def price_chart(hist: pd.DataFrame, symbol: str) -> go.Figure:
    if hist.empty:
        return go.Figure()

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.48, 0.14, 0.19, 0.19],
        subplot_titles=(f"{symbol} Price", "Volume", "RSI(14)", "MACD"),
    )
    for ann in fig.layout.annotations:
        ann.font.color = "#a3a3a3"

    fig.add_trace(
        go.Scatter(x=hist.index, y=hist["close"], name="Close", line=dict(color=_ACCENT2, width=1.5)),
        row=1,
        col=1,
    )
    for col, color, name in [
        ("sma_20", "#a78bfa", "SMA 20"),
        ("sma_50", _ACCENT, "SMA 50"),
        ("sma_200", _WARN, "SMA 200"),
    ]:
        if col in hist.columns:
            fig.add_trace(
                go.Scatter(x=hist.index, y=hist[col], name=name, line=dict(color=color, width=1)),
                row=1,
                col=1,
            )
    if "bollinger_upper" in hist.columns:
        fig.add_trace(
            go.Scatter(
                x=hist.index,
                y=hist["bollinger_upper"],
                name="BB upper",
                line=dict(color="rgba(148,163,184,0.4)", width=0.8),
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=hist.index,
                y=hist["bollinger_lower"],
                name="BB lower",
                line=dict(color="rgba(148,163,184,0.4)", width=0.8),
                fill="tonexty",
                fillcolor="rgba(91,141,239,0.08)",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    vol_colors = [
        _POS if hist["close"].iloc[i] >= hist["open"].iloc[i] else _NEG
        for i in range(len(hist))
    ] if "open" in hist.columns else _NEG
    fig.add_trace(
        go.Bar(x=hist.index, y=hist["volume"], name="Volume", marker_color=vol_colors, showlegend=False),
        row=2,
        col=1,
    )

    if "rsi_14" in hist.columns:
        fig.add_trace(
            go.Scatter(x=hist.index, y=hist["rsi_14"], name="RSI", line=dict(color="#c084fc", width=1.2)),
            row=3,
            col=1,
        )
        fig.add_hline(y=70, line_dash="dot", line_color="rgba(248,113,113,0.5)", row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="rgba(52,211,153,0.5)", row=3, col=1)

    if "macd" in hist.columns:
        fig.add_trace(
            go.Scatter(x=hist.index, y=hist["macd"], name="MACD", line=dict(color=_ACCENT2, width=1)),
            row=4,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=hist.index, y=hist["macd_signal"], name="Signal", line=dict(color=_WARN, width=1)),
            row=4,
            col=1,
        )
        if "macd_histogram" in hist.columns:
            hist_colors = [_POS if v >= 0 else _NEG for v in hist["macd_histogram"].fillna(0)]
            fig.add_trace(
                go.Bar(x=hist.index, y=hist["macd_histogram"], name="Hist", marker_color=hist_colors),
                row=4,
                col=1,
            )

    fig.update_layout(**_layout(height=720, showlegend=True))
    fig.update_xaxes(showgrid=False, tickfont=dict(color="#737373"))
    fig.update_yaxes(showgrid=True, gridcolor=_GRID, tickfont=dict(color="#a3a3a3"))
    return fig


def median_returns_chart(overview: dict[str, Any]) -> go.Figure:
    periods = [
        ("1D", overview.get("median_return_1d")),
        ("1M", overview.get("median_return_1m")),
        ("3M", overview.get("median_return_3m")),
        ("YTD", overview.get("median_return_ytd")),
        ("1Y", overview.get("median_return_1y")),
    ]
    labels, values = [], []
    for label, val in periods:
        if not is_missing(val):
            labels.append(label)
            values.append(float(val))

    if not labels:
        return go.Figure()

    colors = [_POS if v >= 0 else _NEG for v in values]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color=colors,
            text=[f"{v:.2%}" for v in values],
            textposition="outside",
            textfont=dict(color="#ffffff"),
        )
    )
    fig.update_layout(
        **_layout(
            height=220,
            yaxis=dict(tickformat=".1%", gridcolor=_GRID),
            title=dict(text="Median Returns", font=dict(size=13, color="#ffffff")),
        )
    )
    return fig
