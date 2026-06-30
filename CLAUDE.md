# CLAUDE.md — Nasdaq 100 Dashboard (Backend)

This file is the persistent project context for Claude Code. Read it fully before each task.
It defines scope, architecture, conventions, and the build order. Do not exceed the V1 scope
defined here without asking.

---

## 1. What we are building

A **private, MarketWatch-style stock dashboard for the Nasdaq 100**, built for an investor at a
small family office. Version 1 is a **metrics dashboard**, NOT a quant screener. It shows technical
and fundamental quality metrics per stock; it does **not** produce factor scores, composite rankings,
AI-thematic baskets, or buy/sell labels yet. Those are explicitly deferred to later versions.

The frontend (Streamlit) will be built separately. **This repo is the backend: data ingestion,
storage, metric computation, and a clean read API the frontend consumes.** Do not build UI here.

### V1 scope (build exactly this)
- Universe: Nasdaq 100, sourced from QQQ holdings as a proxy, refreshed on a schedule.
- Prices: delayed/intraday via `yfinance`, re-pulled every ~15 minutes during market hours.
- Fundamentals: SEC EDGAR companyfacts API, refreshed daily.
- Filing links: SEC EDGAR submissions API (latest 10-K, 10-Q, 8-K, Form 4), refreshed daily.
- Computed technicals: returns (1D/5D/1M/3M/6M/YTD/1Y), 20/50/200D MAs, price-vs-MA, RSI(14),
  MACD, Bollinger Bands, ATR, 20/60/252D volatility, beta vs QQQ/SPY, correlation vs QQQ/SPY,
  52W high/low, 52W drawdown, max drawdown, momentum (3M/6M/12M excl. most recent month),
  volume + relative volume.
- Computed fundamentals: revenue growth, gross/operating/net margin, FCF margin, ROE, ROIC,
  cash, total debt, net debt, shares outstanding, FCF conversion, capex/revenue, R&D/revenue,
  plus valuation (P/E, P/S, P/B, EV/Sales, FCF yield) where derivable.

### Explicitly OUT of scope for V1 (do NOT build)
- Factor z-scores, composite scores, or the 100-point scoring model.
- AI-beneficiary / AI-risk tagging or thematic baskets.
- Private-company / startup sourcing.
- Backtesting, point-in-time universes, survivorship handling.
- Live (sub-15-min) or real-time data.
- Analyst estimates / EPS revisions (free sources are unreliable here).

---

## 2. Hard constraints & non-negotiables

- **Budget is $0.** No paid APIs, no paid hosting. If a task seems to require a paid source,
  stop and flag it rather than adding a dependency.
- **yfinance is an adapter, not a foundation.** It is unofficial, delayed 15-20 min, "personal use
  only" per Yahoo's terms, and breaks without warning. ALL yfinance access goes through a single
  module (`data_sources/prices.py`) behind a `PriceSource` interface, so it can be swapped for a
  licensed provider later by changing one file. Never call `yfinance` directly elsewhere.
- **Decouple ingestion from display.** The dashboard must NEVER fetch live data on page load.
  A scheduled job (GitHub Actions cron) fetches and writes cached snapshots; the frontend only
  reads cached data. Design every data source as a batch job that writes to storage.
- **Storage for V1 = Parquet snapshots in a `data/` directory**, committed by the scheduled job.
  (Streamlit Community Cloud has an ephemeral filesystem, so runtime-written DB files don't persist.)
  Abstract this behind a `Storage` interface so we can move to Postgres/Supabase later.
- **SEC EDGAR etiquette:** max 10 requests/second, and EVERY request must send a descriptive
  `User-Agent` header (format: `"AppName contact@email"`). Read it from an env var; never hardcode.
- **Rate-limit & fail gracefully.** Wrap every external call in try/except, log errors, retry with
  exponential backoff, and never let one failed ticker abort a whole batch. Persist partial results.
- **Label data provenance.** Every stored snapshot records its source and fetch timestamp. The
  dataset must be self-describing as "prototype / delayed / unofficial source."

---

## 3. Architecture

```
GitHub Actions (cron)  ->  ingestion jobs  ->  data/ Parquet snapshots  ->  read API  ->  Streamlit (separate repo/dir)
```

### Module layout
```
src/
  config.py             # env vars, paths, universe settings, refresh cadences
  models.py             # typed dataclasses/pydantic for Ticker, PriceBar, Fundamentals, etc.
  data_sources/
    base.py             # PriceSource, FundamentalsSource, FilingsSource interfaces (ABCs)
    universe.py         # QQQ holdings -> cleaned Nasdaq 100 ticker list (filter cash/dupes)
    prices.py           # yfinance implementation of PriceSource (the ONLY yfinance import)
    edgar.py            # SEC EDGAR companyfacts + submissions (fundamentals + filing links)
  compute/
    technicals.py       # all technical indicators (pure functions: df in -> metrics out)
    fundamentals.py     # XBRL tag -> canonical metric normalization + ratios
    returns.py          # period returns, momentum, drawdowns, volatility, beta, correlation
  storage/
    base.py             # Storage interface (write_snapshot / read_latest / read_history)
    parquet_store.py    # Parquet implementation writing to data/
  jobs/
    refresh_universe.py # weekly/monthly
    refresh_prices.py   # every 15 min (market hours)
    refresh_fundamentals.py  # daily
    refresh_filings.py  # daily
  api/
    read_api.py         # clean functions the frontend calls: get_universe(), get_table(),
                        # get_tearsheet(ticker), get_market_overview()
tests/
  ...                   # pytest; pure compute funcs tested against fixtures, no network
data/                   # Parquet snapshots (committed by Actions)
.github/workflows/      # cron jobs
```

### Key design rules
- **Pure compute is network-free.** Everything in `compute/` takes DataFrames/objects in and returns
  results out, with zero I/O, so it's unit-testable against fixtures. Fetching lives only in
  `data_sources/` and `jobs/`.
- **Interfaces first.** `data_sources/base.py` and `storage/base.py` define ABCs. Implementations
  depend on the interface. This is what makes the yfinance->licensed and Parquet->Postgres swaps cheap.
- **The frontend only ever imports from `api/read_api.py`.** It never touches data_sources, jobs,
  or storage internals. Keep that boundary clean — Cursor will build against this API.

---

## 4. Tech & conventions

- Python 3.11+. Dependencies: `yfinance`, `pandas`, `numpy`, `pyarrow`, `requests`, `pydantic`,
  `pytest`. Keep the list minimal; ask before adding anything.
- Pin dependencies in `requirements.txt` (yfinance especially — it breaks across versions).
- Type hints everywhere. Docstrings on every public function describing inputs, outputs, and units.
- Format with `ruff`/`black` conventions. No dead code, no commented-out blocks left behind.
- Config via environment variables (`.env` for local, repo secrets for Actions). Never commit
  secrets or the SEC User-Agent email.
- Logging via the stdlib `logging` module, not `print`. Each job logs start, per-source counts,
  errors, and a summary.
- Every metric's **units and definition** are documented (e.g., "volatility = annualized stdev of
  daily log returns, 252-day window"). Ambiguity here is the #1 source of silent bugs.


Avoid:
- Heavy ML frameworks.
- Paid API dependencies.
- Web scraping unless explicitly approved later.
- Recommendation logic.
- Overengineering.

## Data Model Requirements

Ticker universe fields:
- ticker
- company_name
- sector
- industry
- exchange
- index_membership
- active

Price fields:
- ticker
- timestamp
- open
- high
- low
- close
- adjusted_close
- volume
- source

Technical metric fields:
- ticker
- timestamp
- return_1d
- return_5d
- return_1m
- return_3m
- return_6m
- return_ytd
- return_1y
- sma_20
- sma_50
- sma_200
- price_vs_sma_20
- price_vs_sma_50
- price_vs_sma_200
- rsi_14
- macd
- macd_signal
- macd_histogram
- atr_14
- volatility_20d
- volatility_60d
- volatility_252d
- high_52w
- low_52w
- drawdown_52w
- beta_vs_qqq
- beta_vs_spy
- corr_vs_qqq
- corr_vs_spy
- relative_volume

Fundamental quality fields:
- ticker
- fiscal_period
- revenue
- revenue_growth_yoy
- gross_margin
- operating_margin
- net_margin
- free_cash_flow
- fcf_margin
- roe
- roic
- cash_and_equivalents
- total_debt
- net_debt
- shares_outstanding
- capex
- rd_expense
- rd_to_revenue

Filing fields:
- ticker
- cik
- form_type
- filing_date
- report_date
- accession_number
- sec_url

## API Endpoints

Build these endpoints:

- GET /health
- GET /api/universe
- GET /api/market/overview
- GET /api/stocks
- GET /api/stocks/{ticker}
- GET /api/stocks/{ticker}/prices
- GET /api/stocks/{ticker}/technicals
- GET /api/stocks/{ticker}/fundamentals
- GET /api/stocks/{ticker}/filings
- POST /api/refresh/prices
- POST /api/refresh/fundamentals
- POST /api/refresh/filings

## Response Principles

- Always return JSON.
- Use explicit nulls when a metric is unavailable.
- Never silently invent data.
- Include source and last_updated fields where relevant.
- Include warnings when data is delayed, missing, stale, or prototype-only.

## Indicator Calculation Rules

Use adjusted close for historical daily return calculations where available.
Use regular OHLC for intraday candle display.
RSI should use standard 14-period calculation.
MACD should use 12/26 EMA with 9-period signal.
Volatility should be annualized using daily returns for daily data.
Beta should be calculated using daily returns vs QQQ and SPY.
Drawdown should be calculated from rolling 52-week high.

## Security and Compliance

- Do not commit API keys.
- Do not include user secrets in code.
- Add .env.example for configuration.
- Add clear disclaimer that data is prototype/delayed/unofficial.
- Add no investment advice disclaimer in README.

## Development Workflow

Before writing code:
1. Create or update docs/product-spec.md.
2. Create docs/data-dictionary.md.
3. Create docs/api-contract.md.
4. Then implement backend.

After code changes:
1. Run formatting.
2. Run tests.
3. Summarize what changed.
4. List any limitations or TODOs.

---

## 5. Build order (do these in sequence; finish and test each before moving on)

1. **Scaffold**: repo structure, `config.py`, `models.py`, interfaces in `data_sources/base.py`
   and `storage/base.py`, `requirements.txt`, `parquet_store.py`. No fetching yet.
2. **Universe**: `universe.py` — fetch QQQ holdings, filter non-equity/cash/duplicate rows,
   output a clean dated ticker list. Write a snapshot. Test the filtering against a fixture.
3. **Price source**: `prices.py` — yfinance behind `PriceSource`, batch fetch with retry/backoff,
   partial-failure tolerance, provenance + timestamp on output.
4. **Technicals + returns**: `compute/technicals.py` and `compute/returns.py` as pure functions.
   Unit-test each indicator against a known fixture series.
5. **Price refresh job**: `jobs/refresh_prices.py` wiring universe -> prices -> compute -> storage.
6. **EDGAR fundamentals**: `edgar.py` (companyfacts + submissions, correct User-Agent, 10 req/s cap)
   and `compute/fundamentals.py` (XBRL normalization + ratios). This is the hardest module — go slow.
7. **Fundamentals + filings refresh jobs** (daily).
8. **Read API**: `api/read_api.py` — `get_universe`, `get_table`, `get_tearsheet`, `get_market_overview`.
   This is the contract the Streamlit frontend builds against.
9. **GitHub Actions workflows**: universe (weekly), prices (15 min, market hours), fundamentals/filings
   (daily). Each commits its Parquet snapshots.

---

## 6. When in doubt

- Prefer the smallest correct thing that fits V1 scope. Flag scope creep instead of building it.
- If a free data source can't reliably supply a metric, return null + log it; don't fake it.
- If something forces a paid dependency, a licensing question, or a real-time requirement, STOP
  and ask before proceeding.
