# CLAUDE.md — Stock Dashboard (multi-index, full stack)

Persistent project context for Claude Code. This supersedes the original
"Nasdaq 100 backend-only" version (retired 2026-07-16 per the owner's decisions
on `docs/ARCHITECTURE_AUDIT.md` — read that report for the full roadmap).

## 1. What this is

A **private, multi-index stock research dashboard** for personal,
non-commercial use: Nasdaq-100 + S&P 500 + Russell 1000/3000 proxies
(~3,000 US names). **Backend and Streamlit frontend live in this repo** —
frontend work is in scope. Current surface: market overview, index comparison,
filterable heatmap, customizable screener with saved screens, news headlines,
ticker tearsheets with SEC filing links.

Long-term direction (see audit §19): Finviz-style research → personalization →
public-data market intelligence (FINRA/insider/13F) → quantamental feature
registry → research-grade backtester (`src/features/`, `src/backtest/`).

## 2. Hard constraints

- **Budget ≈ $0/month.** No paid APIs/hosting without an explicit owner
  decision documenting cost and trigger (audit §18). Options data has no free
  source — Phase E is scoped to free datasets until the owner approves spend.
- **Ingestion is decoupled from display.** The dashboard NEVER fetches provider
  data at page load. Scheduled jobs write snapshots; the frontend reads caches.
- **Storage:** Cloudflare R2 (S3 API) is the production store, DuckDB the query
  engine, behind the `Storage` ABC. `DATA_URI` unset → local Parquet fixtures
  in `data/` (frozen; never commit new snapshots — legacy git-commit workflows
  are manual-fallback only).
- **Provider adapters are isolated**: yfinance only in `data_sources/prices.py`,
  Massive only in `massive_prices.py`, Finnhub only in `news.py`. Swap = one file.
- **Rate limits are law**: SEC EDGAR ≤10 req/s + `SEC_USER_AGENT` env header;
  Massive free tier ~5/min (13s spacing); Finnhub ~60/min (1.1s spacing).
  Concurrency must never multiply past these.
- **Never fabricate data.** Missing metric → null + log. Every snapshot carries
  provenance (`_source`, `_fetched_at`, `_disclaimer`) and the UI always shows
  the prototype/delayed disclaimer. Russell membership is labeled as a proxy.
- **No secrets in code or git.** Env vars via `.env` locally (gitignored),
  repo secrets in Actions, Streamlit secrets (bridged to env in
  `streamlit_app.py`) in production.
- **Point-in-time universe snapshots** (`universe/dt=YYYY-MM-DD/`) must keep
  being written — they are irreplaceable backtester raw material.

## 3. Architecture & boundaries

```
GitHub Actions cron -> src/jobs/* -> Storage (R2 via ObjectStore | data/ via ParquetStore)
                                        -> src/api/read_api.py (+ user_store.py)
                                        -> streamlit_app.py + app/*
```

- The frontend imports **only `src/api/`** (read_api + user_store). Never
  data_sources, jobs, or storage internals.
- `src/compute/` is pure (df in → metrics out, zero I/O) and unit-tested
  against fixtures. Fetching lives only in `data_sources/` + `jobs/`.
- Jobs are idempotent `python -m src.jobs.<name>` composition roots with
  injected deps for offline tests; they write run summaries via
  `jobs.common.write_job_summary`.
- UI: pure logic (filters, comparison frames) goes in importable modules
  (`app/screener_filters.py`, `app/compare.py::build_*`) so it's testable;
  Streamlit rendering stays thin.

## 4. Tech & conventions

- Python 3.12, pinned deps (`requirements*.txt`) — ask before adding any.
- Type hints + docstrings with units on every public function
  (fractions vs USD vs 0–100 is the #1 silent-bug source — see
  `docs/data-dictionary.md`).
- `ruff check .` and `python -m pytest -q` must pass (CI enforces both).
  Tests are network-free; fake sessions/injected downloaders, `tmp_path` stores.
- Logging via stdlib `logging`, never `print`. Partial failure beats aborting
  a batch; retry with backoff; persist partial results.

## 5. When in doubt

- Prefer the smallest correct change; flag scope creep instead of building it.
- Storage/format migrations: dual-read transition, never destroy history
  without owner approval.
- Anything forcing paid dependencies, licensing questions, or real-time data:
  STOP and ask.
