# Frontend Handoff — Nasdaq 100 Dashboard (Streamlit)

This document is the complete spec for building the **Streamlit frontend** against the
already-built backend in this repo. Read it fully before writing code. It is the contract;
do not reverse-engineer internals.

---

## 1. What already exists (don't rebuild it)

The backend is done, tested (77 passing tests), and live on GitHub. It ingests data on a
schedule (GitHub Actions cron), computes metrics, and writes **Parquet snapshots** into
`data/`. Your job is **only the read-side UI**.

- **Universe:** Nasdaq-100 (~100–102 names, includes dual-class like GOOGL/GOOG).
- **Prices:** delayed (15–20 min) daily OHLCV via yfinance, refreshed ~every 15 min in market hours.
- **Fundamentals + filings:** SEC EDGAR, refreshed daily.
- **Computed metrics:** technicals (MAs, RSI, MACD, Bollinger, ATR, volume) and
  returns/risk (period returns, momentum, volatility, beta, correlation, drawdowns).

### Hard rules (non-negotiable)
1. **Import ONLY from `src.api.read_api`.** Never import or call `src.data_sources`,
   `src.jobs`, `src.storage`, or `src.compute`. That boundary is the whole point.
2. **Never fetch live data.** No `yfinance`, no HTTP, no SEC calls. The UI reads cached
   snapshots only. All freshness comes from the backend's scheduled jobs.
3. **Read-only.** Never write to `data/`. (Streamlit Community Cloud's filesystem is
   ephemeral anyway.)
4. **Always show the provenance disclaimer** (`"prototype / delayed / unofficial source"`,
   delayed 15–20 min, yfinance personal-use only). It is returned by the API — surface it
   visibly on every page. This is a compliance/trust requirement, not decoration.
5. **Degrade gracefully.** Before the first backend run, datasets may be empty. The API
   returns empty DataFrames / `{"constituents": 0}` rather than raising — render a friendly
   "data not available yet" state, never a stack trace.

### Out of scope for V1 (do NOT build)
Factor z-scores, composite/quant scores, buy/sell signals, AI-beneficiary baskets,
backtesting, watchlists with persistence, or anything requiring live/real-time data. This is
a **metrics dashboard**, not a screener or trading tool. Valuation columns (P/E, P/S, P/B,
EV/Sales, FCF yield) exist in the schema but are **always null in V1** — show "n/a", don't
build features on them.

---

## 2. Integration architecture (recommended)

Build the app **inside this same repo**, with the entry file at the **repo root** as
`streamlit_app.py` (Streamlit's conventional default). This is the simplest correct setup:
- The `src` package and the committed `data/` Parquet files are both present at deploy time.
- The backend's GitHub Actions cron commits fresh snapshots to `data/`; Streamlit Community
  Cloud auto-reboots on new commits, so the UI updates automatically.

**Import note:** put the entry file at the repo root so `from src.api.read_api import ...`
resolves (Streamlit adds the script's directory to `sys.path`; a subdir entry would put
`app/` on the path, not the repo root, and `import src` would fail). Helper UI modules can
live under `app/` and be imported by the root `streamlit_app.py`; if any helper needs `src`,
either import through the root entry or add the repo root to `sys.path` explicitly.

Deploy via **Streamlit Community Cloud** pointing at `streamlit_app.py` on `main`.

> A separate frontend repo is possible but then you must vendor the backend's `src` package
> and get the `data/` files there (git submodule or an artifact sync) — more moving parts.
> Prefer the same-repo approach unless there's a strong reason not to.

**Dependencies:** the read API only needs `pandas`, `pyarrow`, `pydantic` (already pinned in
`requirements.txt`). Add `streamlit` (and optionally `plotly`/`altair` for charts) to
`requirements.txt`. Do not add data-fetching libs.

---

## 3. The API contract (`src.api.read_api`)

Two ways to call, identical behavior:
- Module functions: `get_universe()`, `get_table()`, `get_tearsheet(ticker)`, `get_market_overview()`.
- Or `ReadAPI(storage).<method>()` if you need a custom store (you don't, for the app).

```python
from src.api.read_api import get_table, get_tearsheet, get_market_overview, get_universe
```

### `get_universe() -> pd.DataFrame`
Columns: `symbol, name, cik, weight`. (`weight` is **all null** in V1 — the Nasdaq source
provides no ETF weights. `cik` may be null until the daily EDGAR job has run.)

### `get_table() -> pd.DataFrame`
**The main dashboard grid.** One row per constituent: latest metrics joined with latest
fundamentals. Empty DataFrame if no metrics snapshot exists yet. Columns = identity +
technicals + returns + fundamentals (see the data dictionary in §4). Provenance columns are
stripped; call `provenance()` / `get_market_overview()` for the disclaimer.

### `get_tearsheet(ticker: str) -> dict`
Case-insensitive. Returns:
```python
{
  "symbol": "AAPL",
  "found": True,                 # False if ticker not in the table
  "data": { ...one flat row... },# same columns as get_table() for that ticker; {} if not found
  "filings": [                   # latest links, may be empty
    {"symbol","cik","form","filed_date","accession_number","url"}, ...
  ],
  "provenance": {"source","fetched_at","disclaimer","notes"},
}
```
`filings[].form` is one of `"10-K","10-Q","8-K","4"`. `filings[].url` links to the SEC document.

### `get_price_history(ticker: str, indicators: bool = True) -> pd.DataFrame`
**For charts.** Daily OHLCV history for one ticker (~2 years), **indexed by date ascending**.
Empty DataFrame if the symbol has no price snapshot yet. Case-insensitive. Works for
benchmarks too (`"QQQ"`, `"SPY"`) for overlay lines.
- Always: `open, high, low, close, adj_close, volume`.
- With `indicators=True` (default): also `sma_20, sma_50, sma_200, rsi_14, macd,
  macd_signal, macd_histogram, bollinger_upper, bollinger_middle, bollinger_lower` —
  computed by the **same backend functions** as the table, so the chart overlays agree with
  the grid's scalar values. Indicator columns are NaN early (warmup) and `sma_200` stays NaN
  until ~200 bars exist — render gaps, don't fill.

### `get_market_overview() -> dict`
Header/breadth stats across the universe:
```python
{
  "constituents": int,           # 0 if no data yet (the only key in the empty case)
  "as_of": date,                 # latest metrics date
  "pct_above_sma_50": float,     # 0–100
  "pct_above_sma_200": float,    # 0–100
  "median_return_1d": float, "median_return_1m": float, "median_return_3m": float,
  "median_return_ytd": float, "median_return_1y": float,   # fractions
  "median_rsi_14": float,        # 0–100
  "advancers": int, "decliners": int,
  "disclaimer": str,
}
```

### `ReadAPI.provenance(dataset="metrics") -> dict`
`{source, fetched_at, disclaimer, notes}` for any dataset
(`"universe"|"prices"|"metrics"|"fundamentals"|"filings"`). Use it to show "data as of
<fetched_at>" badges.

---

## 4. Data dictionary & UNITS (critical — read carefully)

Ambiguous units are the #1 source of dashboard bugs. Here is every `get_table()` column.

### Identity / context
| Column | Meaning / unit |
|---|---|
| `symbol` | Ticker (string) |
| `name` | Company name |
| `weight` | **Always null in V1** |
| `as_of` | Date of the latest price bar the metrics use (date) |
| `latest_close` | Latest raw close, **USD** |
| `cik` | SEC CIK (from fundamentals merge; may be null) |
| `period_end` | Fiscal year-end the fundamentals cover (date) |
| `fiscal_period` | e.g. `"FY2025"` |

### Returns / momentum / drawdowns — **all FRACTIONS** (× 100 for %)
`return_1d, return_5d, return_1m, return_3m, return_6m, return_1y, return_ytd`,
`momentum_3m, momentum_6m, momentum_12m` (12-1 style, excludes the most recent month),
`max_drawdown` (≤ 0), `drawdown_52w` (≤ 0). `high_52w, low_52w` are **price levels (USD)**.

### Risk
| Column | Unit |
|---|---|
| `volatility_20d, volatility_60d, volatility_252d` | Annualized stdev of daily log returns, **fraction** (0.25 = 25%) |
| `beta_qqq, beta_spy` | Unitless (vs QQQ / SPY, trailing 252d) |
| `correlation_qqq, correlation_spy` | −1..1 |

### Technicals
| Column | Unit |
|---|---|
| `sma_20, sma_50, sma_200` | Price, **USD** |
| `price_vs_sma_20, price_vs_sma_50, price_vs_sma_200` | **Fraction** distance from MA (+0.05 = 5% above) |
| `rsi_14` | 0–100 |
| `macd, macd_signal, macd_histogram` | Price units (USD) |
| `bollinger_upper, bollinger_middle, bollinger_lower` | Price, USD |
| `bollinger_percent_b` | ~0–1 (can exceed) |
| `atr_14` | Price, USD |
| `volume` | Shares (latest bar) |
| `relative_volume_20` | Ratio (1.0 = 20-day average) |

### Fundamentals — **USD** unless noted; ratios are **FRACTIONS**
`revenue, gross_profit, operating_income, net_income, free_cash_flow, cash_and_equivalents,
total_debt, net_debt, capex, research_and_development` → USD.
`shares_outstanding` → share count.
`revenue_growth, gross_margin, operating_margin, net_margin, fcf_margin, roe, roic,
fcf_conversion, capex_to_revenue, rnd_to_revenue` → **fractions**.
`pe_ratio, ps_ratio, pb_ratio, ev_to_sales, fcf_yield` → **always null in V1** (show n/a).

**Any value can be `None`/`NaN`** when a free source can't supply it (e.g. some firms have no
`gross_profit`). Render nulls as "n/a"; never coerce to 0.

---

## 5. Suggested pages (V1)

Keep it a clean, MarketWatch-style **metrics dashboard**. Suggested structure:

1. **Overview / header** (every page): `get_market_overview()` — constituent count, "data as
   of <as_of>", breadth (% above 50/200D MA), advancers/decliners, median returns, and the
   **disclaimer banner**.
2. **Screener table** (main page): `get_table()` rendered as a sortable/filterable grid
   (`st.dataframe` with `column_config` for % and $ formatting, or AgGrid). Sensible default
   columns: symbol, name, latest_close, return_1d/1m/ytd/1y, rsi_14, price_vs_sma_50/200,
   volatility_252d, beta_qqq, net_margin, roe, revenue_growth. Let users add/sort others.
   Color-code returns (red/green) and RSI (overbought/oversold) — display only, no signals.
3. **Tearsheet** (per ticker, via selectbox or table click): `get_tearsheet(symbol)` —
   grouped metric cards (returns, technicals, risk, fundamentals), a price-vs-MA context
   line, and a **Filings** section linking the latest 10-K/10-Q/8-K/Form 4 (`url`).

**Charts (supported):** use `get_price_history(symbol)` for the tearsheet's time-series
visuals — it returns ~2 years of OHLCV plus indicator overlays (computed in the backend, so
they match the grid). Good charts to build:
- **Price** line/candlestick with `sma_20/50/200` and Bollinger band overlays.
- **Volume** bar subplot.
- **RSI(14)** subplot with 30/70 guide lines; **MACD** subplot (line + signal + histogram).
- Optional **relative-to-benchmark** overlay by also pulling `get_price_history("QQQ")`.

Render indicator warmup gaps as gaps (NaN), don't fill. Plotly works well for multi-pane
candlestick + indicators; `st.line_chart`/Altair are fine for simpler line charts. Do not
fetch or recompute indicators in the frontend — `get_price_history` is the single source.

### Formatting helpers to build
- Fractions → `f"{x:.1%}"` (returns, margins) with null-safe "n/a".
- USD → `$` with thousands separators; large values as `$416.2B`.
- Dates → ISO.
- Null-safe everywhere (`pd.isna`).

---

## 6. Performance & freshness
- Wrap reads in `st.cache_data(ttl=...)` (e.g. 5–15 min) so Parquet isn't re-read on every
  interaction. New backend commits reboot the app, refreshing the cache anyway.
- No secrets are needed by the frontend (reading needs no `SEC_USER_AGENT`).

---

## 7. Minimal example

```python
import streamlit as st
from src.api.read_api import (
    get_table, get_market_overview, get_tearsheet, get_price_history,
)

@st.cache_data(ttl=600)
def load_table():
    return get_table()

@st.cache_data(ttl=600)
def load_overview():
    return get_market_overview()

ov = load_overview()
st.caption(f"⚠️ {ov.get('disclaimer', 'prototype / delayed / unofficial source')}")
if ov["constituents"] == 0:
    st.info("No data yet — the backend's scheduled jobs haven't produced snapshots.")
    st.stop()

st.metric("Constituents", ov["constituents"])
st.metric("% above 200D MA", f"{ov.get('pct_above_sma_200', float('nan')):.0f}%")

df = load_table()
st.dataframe(df)  # add column_config for %/$ formatting

sym = st.selectbox("Tearsheet", sorted(df["symbol"]))
sheet = get_tearsheet(sym)
st.write(sheet["data"])
for f in sheet["filings"]:
    st.markdown(f"[{f['form']} — {f['filed_date']}]({f['url']})")

hist = get_price_history(sym)            # ~2yr OHLCV + indicator overlays
st.line_chart(hist[["close", "sma_50", "sma_200"]])  # or Plotly candlestick + RSI/MACD panes
```
