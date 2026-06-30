"""Monochrome black & white theme for Streamlit."""

from __future__ import annotations

import streamlit as st

# Strict B&W palette
BG = "#000000"
PANEL = "#111111"
PANEL_ALT = "#0a0a0a"
BORDER = "rgba(255, 255, 255, 0.14)"
TEXT = "#ffffff"
MUTED = "#a3a3a3"
DIM = "#737373"
HIGHLIGHT = "#ffffff"
SUBTLE = "#525252"


def inject_theme() -> None:
    """Inject global CSS once per run."""
    st.markdown(
        f"""
        <style>
        /* App shell — pure black */
        .stApp {{
            background: {BG};
            color: {TEXT};
        }}
        [data-testid="stAppViewContainer"] {{
            background: {BG};
        }}
        [data-testid="stSidebar"] {{
            background: {PANEL};
            border-right: 1px solid {BORDER};
        }}
        [data-testid="stSidebar"] * {{
            color: {TEXT};
        }}
        [data-testid="stHeader"] {{
            background: {BG};
        }}
        .block-container {{
            padding-top: 1.5rem;
            max-width: 100%;
        }}

        /* Typography — avoid forcing white on all spans (breaks multiselect chips) */
        h1, h2, h3, h4, h5, h6, p, label {{
            color: {TEXT};
        }}
        .stCaption, small {{
            color: {MUTED} !important;
        }}

        /* Hero */
        .dash-hero {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.25rem;
        }}
        .dash-title {{
            font-size: 1.65rem;
            font-weight: 700;
            color: {TEXT};
            margin: 0;
        }}
        .dash-subtitle {{
            color: {MUTED};
            font-size: 0.9rem;
            margin: 0.15rem 0 0 0;
        }}
        .dash-badge {{
            display: inline-block;
            padding: 0.35rem 0.75rem;
            border-radius: 4px;
            border: 1px solid {BORDER};
            background: {PANEL};
            color: {TEXT};
            font-size: 0.78rem;
            font-weight: 600;
            white-space: nowrap;
        }}

        /* Metric cards */
        .metric-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 0.65rem;
            margin: 0.5rem 0 1rem 0;
        }}
        .metric-card {{
            background: {PANEL};
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 0.85rem 1rem;
            min-height: 88px;
        }}
        .metric-card.emphasis {{
            border-color: rgba(255, 255, 255, 0.35);
            background: {PANEL_ALT};
        }}
        .metric-label {{
            color: {MUTED};
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 0.35rem;
        }}
        .metric-value {{
            color: {TEXT};
            font-size: 1.35rem;
            font-weight: 700;
            line-height: 1.2;
        }}
        .metric-value.dim {{
            color: {DIM};
            font-weight: 500;
        }}
        .metric-sub {{
            color: {MUTED};
            font-size: 0.75rem;
            margin-top: 0.25rem;
        }}

        /* Disclaimer */
        .disclaimer-bar {{
            background: {PANEL};
            border: 1px solid {BORDER};
            border-left: 3px solid {TEXT};
            border-radius: 4px;
            padding: 0.65rem 0.9rem;
            color: {MUTED};
            font-size: 0.8rem;
            margin-bottom: 1rem;
        }}

        .section-title {{
            color: {TEXT};
            font-size: 0.95rem;
            font-weight: 600;
            padding: 0.65rem 0 0.35rem 0;
            border-bottom: 1px solid {BORDER};
            margin-bottom: 0.5rem;
        }}

        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 0;
            background: transparent;
            border-bottom: 1px solid {BORDER};
        }}
        .stTabs [data-baseweb="tab"] {{
            background: transparent;
            color: {MUTED};
            padding: 0.55rem 1.1rem;
        }}
        .stTabs [aria-selected="true"] {{
            background: transparent !important;
            color: {TEXT} !important;
            border-bottom: 2px solid {TEXT};
        }}

        /* Inputs — text, select, multiselect (Base Web widgets) */
        .stTextInput input {{
            background: {PANEL} !important;
            color: {TEXT} !important;
            border: 1px solid {BORDER} !important;
        }}
        .stTextInput input::placeholder {{
            color: {DIM} !important;
        }}

        /* Selectbox */
        .stSelectbox div[data-baseweb="select"] > div,
        .stSelectbox div[data-baseweb="select"] > div > div {{
            background: {PANEL} !important;
            color: {TEXT} !important;
            border-color: {BORDER} !important;
        }}
        .stSelectbox div[data-baseweb="select"] svg {{
            fill: {MUTED} !important;
        }}

        /* Multiselect — container + value area */
        .stMultiSelect div[data-baseweb="select"] > div,
        .stMultiSelect div[data-baseweb="select"] > div > div {{
            background: {PANEL} !important;
            color: {TEXT} !important;
            border-color: {BORDER} !important;
        }}
        .stMultiSelect div[data-baseweb="select"] input {{
            background: transparent !important;
            color: {TEXT} !important;
            -webkit-text-fill-color: {TEXT} !important;
        }}
        .stMultiSelect div[data-baseweb="select"] svg {{
            fill: {MUTED} !important;
        }}

        /* Multiselect — selected column tags/chips */
        .stMultiSelect span[data-baseweb="tag"],
        div[data-baseweb="tag"] {{
            background: {SUBTLE} !important;
            border: 1px solid {BORDER} !important;
            color: {TEXT} !important;
        }}
        .stMultiSelect span[data-baseweb="tag"] span,
        div[data-baseweb="tag"] span {{
            color: {TEXT} !important;
        }}
        .stMultiSelect span[data-baseweb="tag"] svg,
        div[data-baseweb="tag"] svg {{
            fill: {MUTED} !important;
        }}

        /* Dropdown popover (select + multiselect options list) */
        div[data-baseweb="popover"],
        div[data-baseweb="popover"] > div,
        ul[data-baseweb="menu"],
        li[data-baseweb="option"],
        [role="listbox"],
        [role="option"] {{
            background: {PANEL} !important;
            color: {TEXT} !important;
        }}
        li[data-baseweb="option"]:hover,
        [role="option"]:hover,
        li[data-baseweb="option"][aria-selected="true"],
        [role="option"][aria-selected="true"] {{
            background: {SUBTLE} !important;
            color: {TEXT} !important;
        }}
        li[data-baseweb="option"] div,
        [role="option"] div {{
            color: {TEXT} !important;
        }}

        /* Widget labels ("Columns", "Ticker", etc.) */
        .stSelectbox label,
        .stMultiSelect label,
        .stTextInput label {{
            color: {MUTED} !important;
        }}

        /* Dataframe — dark table on black page */
        [data-testid="stDataFrame"] {{
            border: 1px solid {BORDER};
            border-radius: 6px;
            background: {BG};
        }}
        [data-testid="stDataFrame"] div[class*="glideDataEditor"] {{
            background: {BG} !important;
        }}
        [data-testid="stDataFrame"] canvas + div {{
            color: {TEXT};
        }}

        /* Plotly charts */
        .js-plotly-plot .plotly .modebar {{
            background: transparent !important;
        }}
        .js-plotly-plot .plotly .modebar-btn {{
            color: {MUTED} !important;
        }}

        /* Filing links */
        .filing-link {{
            display: block;
            padding: 0.55rem 0.75rem;
            margin: 0.35rem 0;
            border-radius: 4px;
            background: {PANEL};
            border: 1px solid {BORDER};
            color: {TEXT};
            text-decoration: none;
            font-size: 0.88rem;
        }}
        .filing-link:hover {{
            background: {PANEL_ALT};
            border-color: rgba(255, 255, 255, 0.3);
        }}

        /* Streamlit alerts on black */
        [data-testid="stAlert"] {{
            background: {PANEL};
            color: {TEXT};
            border: 1px solid {BORDER};
        }}

        #MainMenu {{ visibility: hidden; }}
        footer {{ visibility: hidden; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def metric_card(
    label: str,
    value: str,
    *,
    sub: str | None = None,
    tone: str = "neutral",
) -> str:
    """Return HTML for a single metric card (B&W tones only)."""
    card_class = " emphasis" if tone in ("positive", "negative", "accent") else ""
    value_class = " dim" if tone == "negative" else ""
    sub_html = f'<div class="metric-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="metric-card{card_class}">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value{value_class}">{value}</div>'
        f"{sub_html}</div>"
    )


def metric_grid(cards: list[str]) -> None:
    html = '<div class="metric-grid">' + "".join(cards) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def disclaimer_bar(text: str) -> None:
    st.markdown(
        f'<div class="disclaimer-bar"><strong>Prototype data.</strong> {text} '
        "Prices delayed 15–20 min via unofficial sources (yfinance). Not investment advice.</div>",
        unsafe_allow_html=True,
    )


def section_title(title: str) -> None:
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
