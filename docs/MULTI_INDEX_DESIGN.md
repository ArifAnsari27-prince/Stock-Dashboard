# Multi-Index Expansion — Design & Tradeoffs

Adds **S&P 500, Russell 1000, Russell 3000** alongside the Nasdaq-100, plus an
index **comparison** feature (performance, construction, quantamental). Scales the
pipeline to ~3,000 names.

## 1. Sourcing (verified live, all free)
| Index | Constituents | Perf proxy (ETF) |
|---|---|---|
| Nasdaq-100 | Nasdaq list-type API (exact, 102) | QQQ |
| S&P 500 | Wikipedia GICS table (exact, 503) | IVV |
| Russell 1000 | Nasdaq screener top-1,000 by market cap **(proxy)** | IWB |
| Russell 3000 | Nasdaq screener top-3,000 by market cap **(proxy)** | IWV |

Russell lists are **market-cap proxies** — no free official FTSE Russell list exists
(iShares holdings are Akamai-blocked, like Invesco). Labeled as a proxy in provenance.

## 2. Data model
One **master universe** = union of the four indices (~3,001 unique tickers ≈ Russell
3000 superset). Each `Ticker` now carries `sector`, `market_cap`, and `memberships`
(which indices it's in). Index views are filters over this one list; no row duplication.
✅ Implemented in `data_sources/indices.py` + `models.Ticker`.

## 3. Ingestion at scale
- **Prices — the key scaling move:** use Massive/Polygon `get_grouped_daily_aggs(date)`
  — **one API call returns all ~12k US tickers for a day** (free-tier friendly: 1 call/day
  ongoing; ~500 calls to backfill 2yr). This replaces per-symbol fetching for the broad
  universe. Nasdaq-100 keeps the 15-min yfinance intraday path (the "live-ish" browsable set).
- **Fundamentals / filings:** SEC EDGAR for all ~3,000 names, daily. Heavy download, small output.
- **Metrics:** computed for all ~3,000 (pure pandas — fast).
- **Index performance:** ETF proxies (QQQ/IVV/IWB/IWV) → an `index_performance` dataset.
- **Index aggregates:** per-index construction (sector weights, # constituents, top-10
  concentration, effective-N) + quantamental (weighted/median valuation, margins, ROE,
  growth, breadth) → an `index_aggregates` dataset.

## 4. Storage / repo strategy (the scale problem)
**Constraint:** Streamlit Community Cloud *clones the app repo* on deploy, and the price
job currently **appends a timestamped snapshot every run** (history accumulates in git).
Full price history for 3,000 names is tens of MB/snapshot × many/day → the repo would
bloat past GitHub/Streamlit limits and break deploys.

**Strategy:**
- Large datasets (`prices`, `metrics`, `fundamentals`, `filings`) switch to **latest-only**
  (overwrite, not append) so committed size is bounded (~a few MB each at 3,000 rows).
- **Per-stock price history (for charts) is committed only for the browsable subset**
  (Nasdaq-100 + S&P 500, ~500 unique). Russell-only small caps get grid metrics + aggregates,
  but not a full 2-yr price chart.
- Small datasets (`universe`, `index_performance`, `index_aggregates`) are trivially small.

## 5. Read API (additions)
- `get_table(index=...)` — grid filtered to an index's members.
- `get_indices()` / `get_index_comparison()` — performance + construction + quantamental per index.
- Existing `get_tearsheet` / `get_price_history` unchanged (work for names we keep history for).

## 6. Frontend — comparison page
Tabs: **Performance** (rebased cumulative return, trailing returns, correlation, vol,
drawdown — all 4 via ETF proxies) · **Construction** (sector weights, count, concentration —
per index) · **Quantamental** (aggregate valuation/margins/ROE/growth/breadth — per index).

## 7. Tradeoffs (explicit)
1. **Russell = market-cap proxy**, not official constituents (minor membership diffs; no
   banding/reconstitution rules).
2. **Freshness:** full 3,000-name universe is **daily EOD** (grouped-daily); only Nasdaq-100
   stays 15-min intraday. Massive free tier can't intraday-price thousands of names.
3. **No full git history for big datasets:** switching to latest-only means we lose
   point-in-time snapshot history in git (the Storage ABC still allows a Postgres/Supabase
   pivot later if history matters).
4. **Per-stock charts limited** to Nasdaq-100 + S&P 500 to bound repo size; Russell-only
   names show metrics but no 2-yr chart.
5. **EDGAR load:** 3,000 companyfacts daily ≈ 10–20 min job and large transfer (~tens of GB
   downloaded, tiny output). Mitigations: stagger, or skip unchanged.
6. **Backfill cost:** ~500 Massive calls (~100 min, one-time) to seed 2yr of grouped-daily.
7. **Sector taxonomy** normalized across Nasdaq/GICS — imperfect but consistent.
8. **Streamlit deploy** depends on the repo staying lean; the latest-only strategy is what
   keeps it deployable.

## 8. Roadmap / status
- **A. Master universe** — ✅ done + live-validated (3,001 names, membership/sector/mcap).
- **B. Scale ingestion** — Massive grouped-daily bulk adapter, multi-index refresh jobs,
  latest-only storage.
- **C. EDGAR at scale** — fundamentals/filings for the full universe.
- **D. Index performance + aggregates** datasets.
- **E. Read API** multi-index methods.
- **F. Frontend** comparison page.
