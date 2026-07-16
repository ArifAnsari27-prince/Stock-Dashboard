# Read-API contract

The frontend imports **only** `src/api/read_api.py` (data) and
`src/api/user_store.py` (saved screens). Everything is read-from-cache; nothing
here fetches provider data. All tables are pandas DataFrames with provenance
columns stripped; missing datasets return empty frames (never raise), so the UI
can render empty states.

## read_api

| Function | Returns | Notes |
|---|---|---|
| `get_universe()` | DataFrame: symbol, name, cik, weight, sector, industry, market_cap, memberships | master multi-index list |
| `get_table(index=None)` | one row per ticker: metrics ⋈ fundamentals | `index` ∈ {nasdaq100, sp500, russell1000, russell3000} filters via `in_<index>` |
| `get_price_history(ticker, indicators=True)` | date-indexed OHLCV (+ sma_20/50/200, rsi_14, macd*, bollinger_*) | ~2y daily; works for ETF proxies (QQQ/IVV/IWB/IWV) |
| `get_tearsheet(ticker)` | dict: `found`, `data` (merged row), `filings` (list), `provenance` | |
| `get_market_overview()` | dict: constituents, as_of, breadth (%>50/200D MA), advancers/decliners, median returns/RSI, `disclaimer` | |
| `get_indices()` | list of {index_id, name, etf} | |
| `get_index_comparison()` | dict: `aggregates` (per-index rows), `sectors` (index × sector weights), `provenance` | |
| `get_index_performance(rebased=True)` | DataFrame date × index_id (ETF-proxy series, rebased to 100) | |
| `get_news(symbol=None, limit=50)` | DataFrame: symbol, headline, source, url, published_at, summary — newest first | `symbol=None` → market headlines; empty if the optional news job hasn't run |
| `provenance(dataset)` | dict: source, fetched_at, disclaimer, notes | |

## user_store (saved screens)

| Function | Behavior |
|---|---|
| `list_saved_screens()` | `{name: params_dict}` |
| `save_saved_screen(name, params)` | upsert; params is the JSON-safe spec from `app/screener_filters.py` |
| `delete_saved_screen(name)` | remove if present |

Persisted as the `saved_screens` dataset through the configured `Storage`
backend (R2 in production), so screens survive Streamlit Cloud restarts.

## Rules for the frontend

1. Never import data_sources, jobs, or storage internals.
2. Never fetch live provider data.
3. Always render the provenance disclaimer ("prototype / delayed / unofficial").
4. Degrade gracefully on empty frames/dicts.
5. Cache with `st.cache_data` (TTL ≈ 600s) — the store is only refreshed by
   scheduled jobs, so tighter TTLs buy nothing.
