# Architecture Audit & Long-Term Technical Plan

**Repo:** `ArifAnsari27-prince/Stock-Dashboard` · **Audit date:** 2026-07-16 · **Branch audited:** `fix/actions-node24` (clean, up to date with origin)
**Scope:** Read-only audit + phased plan. No code, workflows, or data were modified. Every file:line claim below was verified against the working tree.

---

## 1. Executive summary

The platform is in far better shape than a typical solo prototype. The layering rules in CLAUDE.md have actually held: Streamlit imports only `src/api/read_api.py`; yfinance is confined to `src/data_sources/prices.py`; storage sits behind a `Storage` ABC with a factory that switches local Parquet ↔ Cloudflare R2 on `DATA_URI`. The test suite is real and green (**118 passed, 0 failed, 0 skipped**, network-free, ~6 s). Production already serves the multi-index universe (3,001 tickers) from R2 via DuckDB with acceptable latency (`get_universe()` ≈ 1.3 s, full `get_table()` ≈ 2.3 s cold).

The main problems are **transitional debt from the Nasdaq-100 → multi-index migration**, not architectural mistakes:

1. **Two parallel pipelines coexist.** Four legacy workflows still commit Parquet snapshots to git (`data/` = 50 MB, 26 near-duplicate ~1.9 MB price files, git data 2 weeks stale, Nasdaq-100 only) while the real serving path is R2 (3,001 tickers, nightly). The git pipeline is now vestigial and is the single largest source of repo rot.
2. **The R2 object store leaves easy performance on the table**: a fresh DuckDB connection + `LOAD httpfs` + secret creation on every operation; `hive_partitioning=false` disables file pruning so every date-filtered read opens every partition; `_latest_stored_date` scans the whole price history each nightly run.
3. **Five valuation ratios (P/E, P/S, P/B, EV/Sales, FCF yield) are 100 % null in production** — declared in the models, documented in the handoff, never computed anywhere.
4. **The "private" dashboard has no authentication** and no `st.secrets` bridge.
5. **Documentation lags reality** badly (CLAUDE.md still says backend-only / Nasdaq-100-only / REST endpoints; no README).

Recommendation: **keep Tier 0 exactly as-is** (Streamlit Cloud + R2 + Parquet + DuckDB + GitHub Actions, $0/mo) and spend the next two phases stabilizing (retire git-data pipeline, auth, docs, error handling) and hardening the data lake (connection reuse, pruning, manifests, atomic publish, valuation ratios). The Finviz/Tradytics/quantamental/backtester ambitions all layer cleanly on top of this foundation without any platform change.

---

## 2. Repository inventory

```
streamlit_app.py          # Streamlit entry point (tabs: Compare / Heatmap / Screener / Ticker)
app/                      # UI helpers: ui.py, compare.py, charts.py, columns.py, formatters.py, theme.py
src/
  config.py               # env-driven Config dataclass; .env loader; R2/SEC/Massive settings
  models.py               # Pydantic v2: Ticker, PriceBar, Fundamentals, Filing, MetricsRow, Snapshot+Provenance
  data_sources/
    base.py               # PriceSource / FundamentalsSource / FilingsSource ABCs
    universe.py           # Nasdaq-100 via api.nasdaq.com (+ dead Invesco CSV path, lines 94–252)
    indices.py            # Multi-index master universe (S&P 500 Wikipedia + Nasdaq screener; Russell = cap proxies)
    prices.py             # yfinance PriceSource (only yfinance import; batch/retry/backoff)
    massive_prices.py     # Massive.com adapter; grouped-daily bulk mode; rate limiter (~4.6 req/min)
    edgar.py              # SEC EDGAR client (<10 req/s throttle, UA from env) + parsers
    price_factory.py      # PRICE_SOURCE → yfinance | massive
  compute/                # Pure, network-free: technicals.py, returns.py, fundamentals.py, index_aggregates.py
  storage/
    base.py               # Storage ABC (write_snapshot / read_latest / read_history)
    parquet_store.py      # V1 local timestamped-file store (git-committed data/)
    object_store.py       # DuckDB/R2 store: write_latest, write_partition, read_dataset, query
    factory.py            # DATA_URI unset → ParquetStore; set → ObjectStore
  jobs/
    common.py             # shared: load_master_universe, build_metrics_rows, CIK enrichment
    refresh_{universe,prices,fundamentals,filings}.py        # V1 legacy (git-commit pipeline)
    refresh_index_{universe,prices,filings,fundamentals,aggregates}.py  # production (R2 pipeline)
  api/read_api.py         # the only frontend-facing module; joins latest snapshots; backend-agnostic
tests/                    # 20 files, 118 tests, all network-free with fixtures
.github/workflows/        # 6 workflows (4 legacy git-commit, 2 R2)
data/                     # 50 MB of git-committed V1 snapshots (73 files; stale)
docs/                     # FRONTEND_HANDOFF.md (stale scope), MULTI_INDEX_DESIGN.md (current)
requirements.txt / requirements-backend.txt / requirements-dev.txt   # all pinned ==
.env.example / .streamlit/config.toml / .python-version (3.12)
```

Missing entirely: `README.md`, `docs/product-spec.md`, `docs/data-dictionary.md`, `docs/api-contract.md` (all mandated by CLAUDE.md), any lint/type config, Dockerfile, backtesting package, user/watchlist persistence.

---

## 3. Current architecture

```
                       ┌────────────── GitHub Actions ──────────────┐
                       │                                            │
   LEGACY (V1)         │  refresh_{universe,prices,fundamentals,    │   PRODUCTION (multi-index)
   Nasdaq-100 only     │           filings}.yml                     │   refresh_index_universe.yml (Sun)
   commits to git ◄────┤  cron: 15-min intraday + daily             │   refresh_index_data.yml (nightly,
                       │                                            │     prices→filings→fundamentals→aggregates)
        ▼              └────────────────────────────────────────────┘        ▼
   data/*.parquet                                                    Cloudflare R2 (s3://…)
   (50 MB, stale) ──► ParquetStore ─┐                              ┌─ ObjectStore (DuckDB httpfs)
                                    ├──  Storage ABC / factory ────┤
                                    │    (DATA_URI decides)        │
                                    ▼                              ▼
                              src/api/read_api.py  (joins, provenance, index filter)
                                          ▼
                              streamlit_app.py + app/*  (st.cache_data ttl=600)
                                          ▼
                              Streamlit Community Cloud (public URL, NO auth)
```

- **Backend selection** is a single env var: `DATA_URI` unset → local Parquet; `s3://…` → R2 ([factory.py](../src/storage/factory.py)). The deployed app inherits whatever env Streamlit Cloud provides — there is no `st.secrets` bridge in code, so R2 credentials must be plain env vars there.
- **Config**: `src/config.py` reads env (optional `.env`, never overriding real env). SEC UA and Massive key are `require_*()` fail-loud accessors. R2 creds: `R2_ENDPOINT/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_REGION`.
- **Rate limits honored**: EDGAR throttle 0.11 s (< 10 req/s) with UA header; Massive paced ≥ 13 s between requests (~4.6/min under the 5/min free tier) with 429-aware exponential backoff.

---

## 4. Current functionality

**Fully implemented and working**
- Multi-index universe: Nasdaq-100 (Nasdaq API) + S&P 500 (Wikipedia GICS) + Russell 1000/3000 *proxies* (Nasdaq screener top-N by market cap, labeled as proxies in provenance and UI).
- Bulk pricing via Massive grouped-daily (1 call/trading day for all ~3 k names), date-partitioned into R2; incremental with 7-day overlap; (symbol,date) dedupe.
- Full technicals/returns suite (SMA/RSI/MACD/Bollinger/ATR/vol/beta/corr/drawdowns/momentum) as pure, unit-tested functions.
- EDGAR fundamentals (XBRL normalization with restatement handling, tag-drift unions, never fabricates) + filings links; **incremental** fundamentals refresh keyed on latest 10-K/10-Q filing date.
- Index aggregates: sector weights, concentration (top-10, effective N = 1/HHI), cap-weighted quantamental medians, breadth, ETF-proxy performance.
- Read API: `get_universe / get_table(index=) / get_tearsheet / get_price_history / get_market_overview / get_indices / get_index_comparison / get_index_performance`.
- Streamlit UI: index selector, comparison, heatmap, screener with row-click → tearsheet, disclaimers and as-of stamps everywhere, 10-min `st.cache_data`, manual refresh button.
- CI hygiene on the R2 workflow: per-step `workflow_dispatch` toggles, 350-min timeout, concurrency groups; legacy workflows have a 5-attempt rebase-push retry loop.

**Partially implemented**
- Valuation: fields exist in `models.py:150–158` and appear in `get_table()` output but are **100 % null** (see §9-TD1).
- Multi-index UI: works, but `app/ui.py:64` and `:215` hardcode "Nasdaq 100" hero/section labels regardless of selected index.
- V1 metrics job emits no `in_<index>` membership columns (`refresh_prices.py:116–136` bypasses `jobs.common.build_metrics_rows`), so ParquetStore-mode `get_table(index=…)` cannot filter.

**Documented but not implemented**
- The entire REST surface in CLAUDE.md (`GET /health`, `GET /api/stocks/...`, `POST /api/refresh/*`) — no HTTP server exists; the "API" is an in-process module. (This is fine — the module API is the better design for Streamlit — but the doc is wrong.)
- `docs/product-spec.md`, `data-dictionary.md`, `api-contract.md`, README.

**Implemented but not accurately documented**
- Multi-index + same-repo Streamlit frontend + R2 storage (CLAUDE.md still says Nasdaq-100-only, backend-only, git-committed Parquet; FRONTEND_HANDOFF still says ~100 names).

**Absent**
- Authentication/access control; user persistence (watchlists/alerts/saved screens/portfolios); backtesting; feature registry; options/FINRA/insider/13F data; Docker; lint/type tooling; manifests/watermarks; compaction/retention.

---

## 5. Test and performance baseline

**Tests (actually run):** `.venv/bin/python -m pytest -q` → **118 passed, 0 failed, 0 skipped, 8 warnings, 6.02 s**. Warnings are benign numpy `Mean of empty slice` from `test_index_aggregates.py`. `python -m compileall src app streamlit_app.py` → clean. Ruff/black/mypy: **not installed, no configs** — lint/type baselines could not be run.

Coverage gaps: `streamlit_app.py` and all of `app/` except `compare.py`'s pure function; `config.py` (.env parsing); `storage/factory.py` / `price_factory.py`; `refresh_index_universe.py` wrapper; `app/formatters.py` (pure and trivially testable).

**Performance (measured against live R2, cold process):**

| Operation | Measured | Notes |
|---|---|---|
| `import src.api.read_api` | 398 ms | pandas/duckdb import cost |
| `get_universe()` — 3,001 rows | 1,298 ms | R2 `latest.parquet` read incl. connection+secret setup |
| `get_table()` — 3,001 × 83 | 2,286 ms | metrics ⋈ fundamentals, two R2 reads |
| Streamlit page (warm cache) | ~ms | `st.cache_data(ttl=600)` — R2 touched ≤ once/10 min/loader |
| Full test suite | 6.0 s | network-free |
| Nightly index refresh | up to 2–3 h first backfill; bounded by Massive 5/min limit thereafter | 350-min workflow timeout |

**R2 requests per cold session:** roughly one DuckDB connection + object GETs per loader (7 cached loaders), plus per-partition GETs for `get_price_history` (every date partition opened — see §7). Well within R2 free tier (10 GB storage, unlimited egress, 10 M class-B ops/mo), but per-partition opens grow linearly with history (~500 partitions at 2 years; ~250/yr added).

**Git growth:** `.git` = 20 MB, `data/` = 50 MB (prices 48 MB = 26 snapshots × ~1.9 MB). 39 commits touch `data/`. Each legacy price refresh adds ~1.9 MB of pack forever. At the old 15-min cadence that is ~50 MB/月 of permanent history — the top bottleneck for repo health, already mostly dormant (last data commit 2026-07-06) but the workflows remain enabled.

**Largest current bottlenecks (ranked):** (1) per-operation DuckDB connection + secret setup and no partition pruning on R2 reads; (2) `_latest_stored_date` full-history scan per nightly run; (3) Massive free-tier 5 req/min ceiling (inherent, correctly handled); (4) git-committed data growth.

---

## 6. Data-flow map

| Dataset | Source | Job | Cadence | Storage layout | Consumer |
|---|---|---|---|---|---|
| `universe` (master, 3,001) | Nasdaq API + Wikipedia + Nasdaq screener | `refresh_index_universe` | Sun 06:00 UTC | R2 `universe/latest.parquet` | read_api, all index jobs |
| `prices` | Massive grouped-daily (adjusted) | `refresh_index_prices` | nightly 03:00 Tue–Sat | R2 `prices/date=YYYY-MM-DD/data.parquet` (append-only) | metrics recompute, `get_price_history` |
| `metrics` | computed (technicals+returns+membership) | same job | nightly | R2 `metrics/latest.parquet` (overwrite) | screener/`get_table` |
| `filings` | SEC EDGAR submissions | `refresh_index_filings` | nightly | R2 `filings/latest.parquet` | tearsheet links, fundamentals staleness |
| `fundamentals` | SEC EDGAR companyfacts | `refresh_index_fundamentals` (incremental by filing date) | nightly | R2 `fundamentals/latest.parquet` | `get_table`, tearsheet |
| `index_aggregates` / sectors | computed | `refresh_index_aggregates` | nightly | R2 `latest.parquet` | Compare tab, overview |
| legacy `universe/prices/metrics/fundamentals/filings` | Nasdaq API / yfinance / EDGAR | `refresh_{…}` V1 jobs | weekly / 15-min / daily | **git** `data/<name>_<ts>.parquet` (timestamped, append-only files) | only if `DATA_URI` unset |

Streamlit obtains everything through `read_api` module functions wrapped in `st.cache_data(ttl=600)`; a sidebar button clears all caches. The app never fetches from providers.

**Which workflows commit to git vs write to R2:** `refresh_universe.yml`, `refresh_prices.yml`, `refresh_fundamentals.yml`, `refresh_filings.yml` → git commits (`permissions: contents: write`). `refresh_index_universe.yml`, `refresh_index_data.yml` → R2 only (`contents: read`).

---

## 7. Storage assessment (verified against code)

Each "likely concern" from the brief, checked:

| # | Concern | Verdict | Evidence |
|---|---|---|---|
| S1 | New DuckDB connection per operation; repeated httpfs setup | **Confirmed.** `_connect()` ([object_store.py:73–91](../src/storage/object_store.py#L73-L91)) runs `duckdb.connect()` + `INSTALL httpfs; LOAD httpfs;` + `CREATE OR REPLACE SECRET` on every read/write. `INSTALL` is disk-cached (cheap), but LOAD + secret + TLS/session setup recur per call. | Fix: cache one connection per `ObjectStore` instance (Phase B). |
| S2 | `read_dataset` hides errors | **Confirmed, partial.** [object_store.py:169–170](../src/storage/object_store.py#L169-L170) catches `IOException`/`CatalogException` → empty DF, conflating "dataset not created yet" with auth failures, network outages, corrupt objects. Binder/parser errors do propagate. `read_latest` correctly re-raises as `FileNotFoundError`. | Fix: distinguish "no objects matched glob" (empty OK) from other IO errors (raise). |
| S3 | `_latest_stored_date` over-scans | **Confirmed.** [refresh_index_prices.py:49–54](../src/jobs/refresh_index_prices.py#L49-L54) reads the `date` column of **every** partition nightly just to compute `max()`. Column projection helps but every file is still opened (~500 objects at 2 yr). | Fix: dataset manifest/watermark (Phase B); interim: `max()` pushed into SQL still opens all files, so manifest is the real fix. |
| S4 | Hive dirs but `hive_partitioning=false` → no pruning | **Confirmed and deliberate.** [object_store.py:157–163](../src/storage/object_store.py#L157-L163): dirs are `date=YYYY-MM-DD` but parsing is disabled to avoid colliding with the row-level `date` column. Consequence: `where="date >= …"` ([refresh_index_prices.py:102–104](../src/jobs/refresh_index_prices.py#L102-L104)) and `get_price_history`'s symbol predicate cannot skip files by path — every partition is opened, relying only on row-group stats. | Fix options benchmarked in §15. |
| S5 | Small-file / listing overhead | **Real but modest today.** One ~3,000-row file per trading day (~250/yr). Glob listing + per-file GET dominates single-ticker history reads. | Fix: monthly compaction (§15). |
| S6 | Writes not atomic | **Confirmed.** `_copy_frame` ([object_store.py:101–105](../src/storage/object_store.py#L101-L105)) is a direct `COPY … TO path` overwrite; a mid-write failure can leave a truncated `latest.parquet` that then breaks every page until the next run. No temp-key + promote, no checksum. | Fix: write `latest.parquet.tmp-<runid>` then copy/rename; add row-count manifest (§15). |
| S7 | Two operating modes (local Parquet vs R2) | **Confirmed by design** (factory on `DATA_URI`) — good for dev/tests. The problem is only that the *legacy git pipeline* still feeds mode 1 with stale divergent data. | Fix: retire legacy workflows (Phase A); keep ParquetStore for dev/tests. |
| S8 | DuckDB opens no threat from `query()` | Note: `read_dataset(where=…)` and `query(sql)` interpolate raw SQL. Internal-only today (`read_api` builds predicates itself and sanitizes symbol quotes), but this is an injection surface if ever exposed to user input. Track as debt (TD-9). |

R2 remains the right default store: free tier is generous, egress is free (unusual and valuable for DuckDB scan patterns), and the S3 interface keeps a future S3/MinIO migration trivial. **No storage-platform change recommended.**

---

## 8. Salvage matrix

| File / module | Responsibility | Quality / tests | Verdict | Problem / action | Risk | Phase |
|---|---|---|---|---|---|---|
| `src/storage/base.py`, `factory.py` | Storage ABC + selection | Clean; factory untested | **KEEP AS-IS** (+ tiny factory test) | — | none | A |
| `src/storage/object_store.py` | R2/DuckDB store | Tested; works in prod | **REFACTOR** | S1/S2/S4/S6 above; dead `_PROVENANCE` (line 39); duplicated `_snapshot_to_frame` | medium (prod path) — behind existing tests + new ones | B |
| `src/storage/parquet_store.py` | Local dev/test store | Tested indirectly | **KEEP AS-IS** (demote to dev/test backend) | timestamped-file accumulation is by design; stop feeding it from CI | low | A |
| `src/api/read_api.py` | Frontend contract | Well tested | **KEEP AND EXTEND** | add valuation join, freshness/health endpoint; keep boundary sacred | low | B/C |
| `src/compute/*` | Pure indicators/fundamentals/aggregates | Best-tested code in repo | **KEEP AS-IS** → later feeds `src/features/` registry | ROIC is simplified pre-tax (document) | none | — |
| `src/data_sources/base.py`, `prices.py`, `massive_prices.py`, `edgar.py`, `price_factory.py` | Provider adapters | Tested, rate-limited, injectable | **KEEP AS-IS / EXTEND** | edgar `date.min` sentinel for missing filedDate; indices.py reaches into `universe._clean_company_name` | low | C |
| `src/data_sources/universe.py` | Nasdaq-100 fetch | Working path tested | **CONSOLIDATE** | delete dead Invesco CSV path (lines 94–252) + `QQQ_HOLDINGS` enum once confirmed unused | none | B |
| `src/data_sources/indices.py` | Master universe | Tested | **KEEP AND EXTEND** | Russell = cap-proxy (documented); no retry on Wikipedia/screener fetch; promote `_clean_company_name` to shared helper | low | B/C |
| `src/jobs/common.py` | Shared job helpers | Tested | **KEEP AND EXTEND** | absorb duplicated `_read_or_empty` (in 2 index jobs) | none | B |
| `src/jobs/refresh_index_*.py` | Production pipeline | Tested | **KEEP AND EXTEND** | `_latest_stored_date` scan → manifest | low | B |
| `src/jobs/refresh_{universe,prices,fundamentals,filings}.py` | Legacy git pipeline | Tested but divergent (no membership cols) | **REMOVE LATER** | disable workflows now (Phase A); delete code after one clean month of R2-only operation | low — R2 pipeline is already authoritative | A (disable) / C (delete) |
| `.github/workflows/refresh_index_*.yml` | Production CI | Good (toggles, timeout, concurrency) | **KEEP AND EXTEND** | add failure notifications + run summaries | none | A |
| `.github/workflows/refresh_{universe,prices,fundamentals,filings}.yml` | Legacy CI | Functional | **REMOVE LATER** (disable schedules first) | drives git bloat; data 2 wk stale anyway | low | A |
| `data/` (50 MB snapshots) | Committed V1 data | Stale | **REMOVE LATER** | keep one latest snapshot per dataset as dev fixture; stop new commits; history rewrite = owner decision (§22) | destructive if rewritten — needs approval | A (freeze) |
| `streamlit_app.py`, `app/*` | Frontend | Works; mostly untested | **KEEP AND EXTEND** | fix hardcoded "Nasdaq 100" labels (`app/ui.py:64,215`); add auth gate; test `formatters.py` | low | A/C |
| `src/config.py`, `models.py` | Config + schema | Solid; stale naming | **KEEP AS-IS** (+ st.secrets bridge, rename comments) | unused `universe_etf`/`universe_target_size`; unpopulated valuation fields | none | A/B |
| `docs/FRONTEND_HANDOFF.md` | Frontend spec | Stale scope | **REFACTOR** into `docs/api-contract.md` + `data-dictionary.md` | claims Nasdaq-100-only | none | A |
| `docs/MULTI_INDEX_DESIGN.md` | Current design | Accurate | **KEEP AS-IS** | — | none | — |
| `CLAUDE.md` | Project context | Materially wrong on scope/UI/REST/storage | **REFACTOR** | rewrite to multi-index + Streamlit + R2 reality | none | A |
| `tests/*` (20 files) | 118 tests | Green, network-free | **KEEP AND EXTEND** | — | none | — |
| Backtester / features / Docker / user DB | — | absent | **DEFER** (designed in §13/§16) | — | — | E–G |

---

## 9. Technical-debt register

| ID | Item | Severity | Evidence | Fix phase |
|---|---|---|---|---|
| TD-1 | Valuation ratios 100 % null (pe/ps/pb/ev_to_sales/fcf_yield) — declared ([models.py:150–158](../src/models.py#L150-L158)), shown in table schema, never computed; `compute/fundamentals.py` deliberately excludes price-based ratios and nothing joins price×shares later | **High** (user-facing wrong-looking data) | measured: 5 columns 100 % null in latest snapshot | B |
| TD-2 | Dual pipelines; git-committed data stale/divergent; git grows ~1.9 MB per legacy price run | High | §5, §6 | A |
| TD-3 | No auth on "private" dashboard; no st.secrets bridge | High | grep: zero auth/st.secrets usage in app | A |
| TD-4 | ObjectStore per-call connection + no pruning + non-atomic writes + error swallowing (S1/S2/S4/S6) | High (perf+reliability) | §7 | B |
| TD-5 | `_latest_stored_date` full scan nightly | Medium | [refresh_index_prices.py:49–54](../src/jobs/refresh_index_prices.py#L49-L54) | B |
| TD-6 | Docs wrong/missing (CLAUDE.md, README, product-spec, data-dictionary, api-contract); hardcoded "Nasdaq 100" UI labels | Medium | §4 | A |
| TD-7 | Dead/duplicated code: Invesco path (`universe.py:94–252`), `_PROVENANCE` (object_store.py:39), duplicated `_snapshot_to_frame`, duplicated `_read_or_empty`, V1 `refresh_prices` inline metric building without membership cols | Low–Medium | explorer line refs | B |
| TD-8 | No lint/type tooling despite CLAUDE.md mandate; no CI test job (tests only run locally) | Medium | §5 | A/B |
| TD-9 | Raw-SQL interpolation in `read_dataset(where=)` / `query()` — internal-only today, injection surface if ever user-exposed | Low (latent) | object_store.py:160–165, 176–179 | B (document contract), D (harden before user input) |
| TD-10 | Minor: EDGAR `date.min` sentinel; numpy empty-slice warnings; `indices.py` → `universe._clean_company_name` private coupling; no test for config/.env parsing | Low | explorer reports | C |

---

## 10. Security and data-license register

| Area | Status | Action |
|---|---|---|
| **Local `.env` credentials** | Contains live R2 + Massive keys with an in-file note "ROTATE THIS TOKEN … it was pasted in chat". Gitignored and confirmed never committed — but rotation is unverified. | **Rotate Massive key + R2 token now** (owner action, Phase A, day 1). |
| Committed secrets | None found (`git ls-files` clean; scans hit only env-var reads). | Add GitHub secret scanning / push protection (free). |
| GitHub Actions secrets | `SEC_USER_AGENT`, `MASSIVE_API_KEY`, `R2_*` as repo secrets; `DATA_URI`/`R2_REGION` as vars. Correct pattern. | Scope the R2 API token to the single bucket, object-RW only (least privilege). |
| Streamlit secrets | No `st.secrets` usage; R2 creds must be plaintext env vars in Streamlit Cloud settings. | Add a small `st.secrets`→`os.environ` bridge at app start (Phase A). |
| **Private access** | **None.** The deployed app is public. | Options: (a) Streamlit Community Cloud built-in viewer allowlist (free, email-gated, simplest); (b) shared-passphrase gate via `st.secrets` (weak but free); (c) Cloudflare Access — **not feasible in front of Community Cloud** (no custom domain/origin control there); it becomes viable only if self-hosting later. Recommend (a). |
| yfinance / Yahoo | Unofficial, personal-use only, delayed. Already isolated behind `PriceSource` and now secondary to Massive. | Keep for dev fallback only; never redistribute; disclaimer already shown. |
| Massive.com | Free tier, adjusted EOD grouped aggregates. | Confirm ToS on retention + derived-data display for personal use (owner: read plan terms). Rate limits already respected with margin. |
| SEC EDGAR | Public domain; fair-access rules honored (<10 req/s, descriptive UA from secret). | ✔ compliant. |
| Wikipedia (S&P 500) / Nasdaq screener | Public pages/APIs; index *membership lists* are facts, but official Russell constituents are FTSE-licensed — which is exactly why the proxy approach is correct. Keep the "proxy" labeling. | ✔ keep labels; never claim official Russell membership. |
| FINRA / 13F / insider (future) | FINRA short-sale & ATS files, SEC Form 4/13F are free/public with attribution norms. | Verify per-dataset terms in Phase E design. |
| Disclaimers | "Prototype / delayed / unofficial / not investment advice" shown in UI (`app/theme.py`) and provenance. | Add README-level disclaimer (missing README). |
| Backups | R2 bucket is single-copy; git history is an accidental partial backup of stale V1 data. | Phase B: nightly manifest + optional weekly `rclone` copy of `latest.parquet` set to a second free bucket. |
| Dependency vulnerabilities | All pins exact; no audit tooling. | Enable Dependabot (free) — low noise with pinned reqs. |
| Audit logging | Actions run logs only. | Sufficient at Tier 0; add job summaries (Phase A). |

---

## 11. Target Tier 0 architecture (current / personal / free — optimize first)

Same platform, tightened:

```
GitHub Actions (cron, idempotent python -m jobs)          Owner
  refresh_index_universe (Sun)                              │
  refresh_index_data (nightly, step-toggled)                ▼
        │  writes with tmp-key → promote + _manifest.json   Streamlit Community Cloud
        ▼                                                   (viewer allowlist = auth)
Cloudflare R2  s3://bucket/prefix/                          │
  <dataset>/latest.parquet + latest.manifest.json           │ st.secrets → env bridge
  prices/dt=YYYY-MM/part-*.parquet   (compacted monthly)    │ st.cache_data ttl=600
  prices/_manifest.json  (watermark, row counts, schema v)  │
        ▲                                                   ▼
        └──────────── ObjectStore (one cached DuckDB con, ← read_api ← app/
                      hive pruning ON, errors surfaced)
```

Changes vs today (all free): retire legacy git-data workflows; auth via Streamlit viewer allowlist; secrets bridge; connection reuse; month-level hive partitions with pruning; manifests/watermarks; atomic publish; CI test+ruff job; docs rewritten. **Scale headroom:** 3 k tickers × daily EOD + fundamentals is comfortably inside R2 free tier and Actions free minutes (public repo = unlimited standard-runner minutes).

## 12. Optional Tier 1 architecture (containerized & reproducible — introduce only when useful)

Trigger: first time a job outgrows Actions' 6-h limit, or a second machine/contributor needs identical envs, or self-hosting Streamlit becomes necessary (e.g., for Cloudflare Access).

- One `Dockerfile` (python:3.12-slim, multi-stage, non-root, pinned reqs, no secrets, `HEALTHCHECK` on Streamlit port), two commands: `streamlit run streamlit_app.py` and `python -m src.jobs.<job>`.
- `docker-compose.yml` for local dev: app + a MinIO container standing in for R2.
- CI builds the image and runs pytest inside it.
- A tiny `python -m src.jobs run <name>` CLI wrapper adding `--dry-run`, retries, timeouts, and a machine-readable run summary — making jobs orchestrator-agnostic.
- Airflow/Prefect/Dagster: **evaluated, not adopted** (see §17).

Docker is *not* used by Streamlit Community Cloud, so at Tier 0 it is purely a reproducibility tool — hence Tier 1, not Phase A.

---

## 13. Backtester design (interface-first; build in Phase G)

New packages (no implementation yet):

```
src/features/                       src/backtest/
  registry.py   # FeatureSpec: name, version, deps,      engine.py       # event loop over rebalance calendar
                #   point-in-time rule, units, fn         portfolio.py    # positions, cash, corporate actions
  technical.py  # wraps src/compute/technicals+returns    execution.py    # fills at next-bar open/close policy
  fundamental.py# PIT view keyed on FILING date            costs.py        # commission + spread + slippage models
  quality.py    valuation.py   risk.py   event.py         metrics.py      # returns, DD, Sharpe/Sortino, turnover,
                                                          #   exposure, benchmark-relative
                                                          validation.py   # lookahead/survivorship audits
                                                          walk_forward.py # train/validate/test splits, param search
                                                          result_store.py # runs/<run_id>/ on R2: config, code+data
                                                          #   versions, equity curve, trades, features hash
```

Key design commitments (all achievable with existing data + planned manifests):

- **Point-in-time universe:** persist dated universe snapshots (the weekly `refresh_index_universe` output becomes `universe/dt=YYYY-MM-DD/` instead of overwrite-only) — this is the one *data* prerequisite that must start early, because history can't be reconstructed later. Flagged in Phase A as "start capturing now."
- **Point-in-time fundamentals:** availability keyed on **filing date** (already stored as `latest_filing_date` per row; extend to keep per-period filing dates rather than latest-only).
- **Adjusted vs unadjusted:** Massive supplies adjusted bars with `adj_close = close`; store the adjustment flag in the dataset manifest; returns math on adjusted, display/liquidity on raw when available.
- Rebalance calendars, long/short, position sizing, max-position/sector constraints, transaction costs (fixed + bps), spread approximation (bps by liquidity bucket), slippage, turnover and liquidity caps (max % of ADV), cash accounting, benchmark comparison (QQQ/SPY/IWM proxies already ingested).
- **Survivorship:** the cap-proxy Russell universes are reconstructed point-in-time from the dated snapshots; delisted names persist in stored history even after leaving the universe. Document that pre-capture history (before PIT snapshots begin) is survivorship-biased and must not be used for reported results.
- Reproducibility: `run_id = hash(strategy version, param set, dataset manifest versions, git SHA)`; results persisted to R2; reruns idempotent; failures resumable from last completed rebalance date.
- Walk-forward with parameter search strictly on train/validation windows; the final test window is evaluated once.

## 14. Computation-optimization strategy

Ordered, measure-first (applies to features + backtests):

1. Correct single-process pandas/NumPy baseline (current `src/compute` already is this) — profile with `cProfile`/`py-spy` before touching anything.
2. Eliminate repeated remote reads: one DuckDB connection, feature matrices materialized to R2 (`features/dt=…`) and reused across strategies/params.
3. Push joins/filters/aggregations into DuckDB where dataframes exceed memory comfort; keep indicator math in vectorized pandas/NumPy (it is already vectorized).
4. Batch parameter combinations over a shared preloaded feature matrix (Arrow table, read-only) rather than re-loading per combination.
5. Cache results by `(dataset version, strategy version, param hash)`.
6. Threads (`ThreadPoolExecutor`) only for I/O (R2 fetches, provider calls behind the shared rate limiter — concurrency must never multiply request rates past SEC/Massive limits).
7. `ProcessPoolExecutor` only for CPU-bound independent partitions (e.g., param sweep shards); workers read their own partition files rather than receiving pickled DataFrames; worker count configurable; never nested; never inside the Streamlit request path — large backtests run as background jobs writing status files that Streamlit polls.
8. **Polars: do not adopt now.** Current workloads (3 k × ~500 rows) complete in seconds in pandas; DuckDB already covers the heavy relational work. Re-benchmark only if a measured feature-matrix build exceeds ~1–2 min.
9. Distributed batch infra: not until a container on a single machine measurably can't finish nightly work — no current trajectory reaches that.

## 15. R2 / Parquet / Hive partitioning strategy

**Layout (target):**

```
s3://<bucket>/<prefix>/
  <dataset>/latest.parquet + latest.manifest.json        # presentation tables (metrics, fundamentals, aggregates)
  prices/dt=YYYY-MM/part-000.parquet                     # monthly partitions after compaction
  prices/dt=YYYY-MM-DD/part-000.parquet                  # current month, daily until compacted
  prices/_manifest.json                                  # watermark (max date), file list, row counts,
                                                         #   schema_version, dataset_version, source,
                                                         #   fetched_at, adjustment=adjusted, universe_version
  universe/dt=YYYY-MM-DD/data.parquet                    # PIT snapshots (backtester prerequisite)
  features/… runs/…                                      # future layers
```

**The `date=` collision decision.** Today dirs are `date=YYYY-MM-DD` with `hive_partitioning=false` because the partition name would collide with the row-level `date` column ([object_store.py:157–159](../src/storage/object_store.py#L157-L159)). Options considered:

| Option | Pros | Cons |
|---|---|---|
| (a) Rename partition key to `dt=` and enable `hive_partitioning=true` + `hive_types` | True file pruning on date predicates; no schema change to rows | one-time object rename/rewrite (cheap: server-side copy of ~500 small objects) |
| (b) Keep layout; maintain `_manifest.json` and expand explicit path lists per query | no rename; also fixes watermark scan | query code builds path lists; pruning logic lives in our code not DuckDB |
| (c) Drop row-level `date`, derive from partition | smallest files | breaks row self-containedness and every consumer; rejected |

**Recommendation: (a) + (b) together** — rename to `dt=` for engine-native pruning *and* add manifests (needed anyway for watermarks, atomicity, row counts). Benchmark before/after with the three canonical queries (latest date, one-symbol 1-y history, 420-day window scan); acceptance = single-symbol history no longer opens all partitions. Migration is additive: writer emits `dt=`, reader globs both during transition, old `date=` dirs compacted into monthly `dt=` files, then removed.

**Other commitments:** projection + predicate pushdown everywhere (already partly done via `columns=`/`where=`); atomic publish = write `*.tmp-<runid>` then promote + update manifest last (manifest is the commit point; a reader never sees a half-published state); corrupt-partition recovery = re-run that day's grouped fetch (jobs are already idempotent per-partition); retention = keep everything (R2 free tier is 10 GB; 2 yr × 3 k tickers ≈ ~0.5 GB); compaction = monthly job folds last month's daily files into one monthly file (~64–256 MB row-group targets only for future options data; small daily equity data should *not* be padded to big files — monthly is the right grain); point-in-time reproducibility = dated universe/fundamentals snapshots + manifest versions recorded in every backtest run.

---

## 16. Docker and deployment plan

- **Now (Tier 0):** none required. Streamlit Community Cloud deploys from the repo (`requirements.txt`) and does not consume Dockerfiles. GitHub Actions installs pinned reqs per run — reproducible enough at current scale via exact pins + `.python-version`.
- **Tier 1 (when triggered):** single image as specified in §12 (python:3.12-slim multi-stage, non-root, healthcheck, no secrets, R2 config via env, read-only rootfs where possible), Compose with MinIO for offline dev, CI image build + in-image pytest. Separation of concerns achieved by *commands*, not separate images: `streamlit run …` vs `python -m src.jobs …` vs future `python -m src.backtest …`.
- Serving separation roadmap: interactive Streamlit (Cloud) / scheduled ingestion (Actions) / feature computation (Actions, later same image) / backtests (background job writing status to R2, Streamlit polls) / alerts+reports (scheduled Action posting email/webhook).

## 17. Orchestration decision

**GitHub Actions remains the orchestrator.** Evidence: the DAG is 5 sequential steps + 1 weekly job; steps are already idempotent Python modules with injected deps; per-step toggles, concurrency groups, and a 350-min timeout already exist; a public repo gets unlimited free standard-runner minutes. Airflow (even self-hosted) would add an always-on server — violating both the $0 target and "no always-running servers".

Improvements inside Actions (Phase A/B): job summary written to `$GITHUB_STEP_SUMMARY` (rows written, watermark, duration, warnings); failure notifications (Actions email is default; optionally a webhook); explicit step dependencies stay encoded in workflow order; retries stay inside Python (already present at the HTTP layer).

Re-evaluation triggers: > ~15 interdependent tasks, cross-day backfill DAGs, or SLA/observability needs beyond run logs → then compare **Dagster (self-hosted, free) vs Prefect OSS vs staying put**, in that order; Airflow and MWAA are explicitly rejected at this scale (operational burden; MWAA ≈ $350+/mo).

---

## 18. Cost and scale-trigger matrix

| Resource | Today | Free ceiling | Trigger to spend / migrate |
|---|---|---|---|
| Cloudflare R2 | ~0.1 GB, few thousand ops/day | 10 GB, 1 M class-A + 10 M class-B ops/mo, free egress | > 10 GB (≈ options data era) → ~$0.015/GB-mo — still cheapest; no migration |
| GitHub Actions | ~1–3 h/night | unlimited std minutes (public repo) | private repo or > 6 h/job → self-hosted runner (free) or Tier 1 container on a VPS |
| Streamlit Community Cloud | 1 app | 1 GB RAM, sleeps when idle | RAM > 1 GB (big screener frames) or need custom domain/Cloudflare Access → self-host on a ~$5/mo VPS or free-tier Fly/Render |
| Massive free tier | 5 req/min, EOD grouped | fixed | need intraday multi-index or options chains → paid market-data plan (document exact plan/cost when the feature is scheduled; likely the **first real dollar** this project spends) |
| User persistence (Phase D) | none | SQLite file on R2 (single-writer OK for 1 user) / Turso / Supabase free tiers | multi-user concurrent writes → Supabase/Neon free → paid at ~50k rows+auth needs |
| Orchestration | Actions | — | §17 triggers → Dagster self-hosted (free, +ops burden) |

## 19. Phased roadmap (dependency-aware)

**Phase A — Stabilize (1–2 weeks of part-time work; no behavior change to serving)**
| Item | Depends on | Acceptance criteria | Complexity | Rollback |
|---|---|---|---|---|
| A1 Rotate Massive + R2 credentials | owner | old keys revoked; workflows green with new secrets | XS | issue new again |
| A2 Disable 4 legacy workflow schedules (keep files, `workflow_dispatch` only) | — | no new `data:` commits; R2 pipeline unaffected | XS | re-enable cron |
| A3 Freeze `data/`: keep one latest snapshot per dataset as dev fixture; stop committing | A2 | repo size stops growing; tests still pass against fixtures | S | git revert |
| A4 Auth: Streamlit viewer allowlist + `st.secrets`→env bridge | owner has app admin | unauthenticated visit blocked; R2 creds via secrets.toml | S | remove gate |
| A5 `read_dataset` error separation (empty-glob vs real IO error) + log | — | unit test: auth failure raises, empty dataset returns empty | S | revert commit |
| A6 CI: pytest + ruff on PR/push (add ruff config) | — | red CI on test/lint failure | S | remove workflow |
| A7 Docs rewrite: README (disclaimer, deploy, architecture), CLAUDE.md corrected, FRONTEND_HANDOFF → api-contract + data-dictionary | audit report | docs match code; stale claims gone | M | n/a |
| A8 Start PIT universe snapshots (`universe/dt=…` alongside `latest`) | — | dated snapshot appears weekly | S | stop writing |
| A9 Job run summaries + failure notification | — | `$GITHUB_STEP_SUMMARY` populated nightly | XS | remove step |

**Phase B — Performance & data foundation (2–4 weeks)**
| Item | Depends on | Acceptance | Complexity |
|---|---|---|---|
| B1 Cached DuckDB connection per ObjectStore | A5 | ≥ 30 % latency cut on `get_table` cold path (benchmark before/after) | S |
| B2 `dt=` partition migration + `hive_partitioning=true` + dual-read transition | B1 | single-symbol history opens ≤ 1/30th of files; all tests green | M |
| B3 Dataset manifests/watermarks (+ `_latest_stored_date` uses watermark) | B2 | nightly job does zero full-history scans; manifest row counts match | M |
| B4 Atomic publish (tmp-key → promote; manifest as commit point) | B3 | kill -9 mid-write leaves previous version readable | S |
| B5 Monthly compaction job | B2–B4 | prior months = 1 file each; queries unchanged | M |
| B6 **Valuation ratios** (close × shares → P/E, P/S, P/B, EV/S, FCF yield in a post-fundamentals join step) | — | 5 columns majority-populated; unit-tested against fixture; nulls only where inputs null | M |
| B7 Consolidate duplicated helpers; delete Invesco dead path; fix "Nasdaq 100" UI labels | A7 | ruff clean; grep shows no dead refs | S |
| B8 (Tier 1, optional) Dockerfile + Compose + MinIO dev env | A6 | `docker compose up` serves app against MinIO | M |

**Phase C — Finviz-style research**: expanded screener filters (server-side DuckDB predicates), saved screens (needs first user store — see D), sector/industry heatmaps, richer tearsheets (fundamental history time series — requires keeping per-period fundamentals rather than latest-only), filing/event timeline. Depends on B2/B3 read performance.

**Phase D — Personalization**: real auth beyond allowlist if needed, persistent watchlists/alerts/preferences/portfolio. First mutable store: recommend starting with **SQLite on R2** (single user, jobs are single-writer) and moving to Supabase/Turso free tier when a second user appears — owner decision §22. Alerts run as a scheduled Action evaluating rules against latest data and emailing.

**Phase E — Public-data intelligence**: market breadth (already partial), FINRA short-sale + ATS files, Form 4 insider (EDGAR — client already exists), 13F diffs, congressional disclosures, earnings/filing scanners, delayed options volume/OI and clearly-labeled estimated GEX (data source must be identified; likely the first paid trigger — evaluate then). Each dataset = one new adapter + one new job + manifest, following existing patterns.

**Phase F — Quantamental engine**: `src/features/` registry over existing compute; PIT feature snapshots to R2; transparent scoring with per-factor attribution surfaced in the UI (explainable screens). Depends on A8 (PIT universe), C (fundamental history), B (manifests).

**Phase G — Backtester**: per §13, correctness-first single-process engine → validation/bias audits → cached feature matrices → multiprocessing param sweeps → walk-forward → background execution. Depends on F + accumulated PIT history (A8 makes the clock start early — that is deliberate).

All phases: cost impact $0 until the Phase E options-data decision; every item ships on its own small branch with tests and a documented rollback (revert; storage migrations are dual-read/dual-write with the old layout retained until cutover is verified).

## 20. Acceptance criteria (program-level)

- Phase A done ⇔ no data commits to git; app gated; CI green gate exists; docs truthful; credentials rotated; PIT universe capture running.
- Phase B done ⇔ benchmarked read-latency improvement recorded in the repo; zero full-dataset scans in nightly logs; valuation columns populated; atomic-publish crash test passes; 118+ tests still green.
- Any phase: `pytest` green, ruff clean, no unrelated-file changes, no historical data deleted without explicit approval.

## 21. Risks and rollback

| Risk | Mitigation / rollback |
|---|---|
| `dt=` migration breaks prod reads | dual-glob reader during transition; old layout retained until parity checks (row counts per manifest) pass; rollback = point reader back at `date=` |
| Legacy-workflow retirement loses a dataset nobody realized was used | disable schedules (A2) before deleting code (post-C); one-month observation window; `workflow_dispatch` remains as escape hatch |
| Auth lockout | allowlist is admin-reversible in Streamlit Cloud UI |
| Massive free tier changes/disappears | `PriceSource` abstraction + yfinance fallback already exist; swap = one factory change |
| Connection caching introduces stale-secret or thread-safety issues under Streamlit reruns | one connection per ObjectStore instance, recreated on failure; covered by tests before rollout |
| Git-history rewrite (if chosen) breaks clones/PRs | it's optional; default recommendation is freeze-only (§22 Q2) |

## 22. Open decisions requiring owner approval

1. **Q1 — Credential rotation:** confirm the Massive key and R2 token flagged in `.env` have been (or will now be) rotated.
2. **Q2 — Git data history:** freeze only (recommended, zero risk), or also rewrite history to purge the ~13 MB pack of old snapshots (destructive; requires force-push and re-clone)?
3. **Q3 — Auth mechanism:** Streamlit Cloud viewer allowlist (recommended) vs passphrase gate vs defer?
4. **Q4 — Legacy V1 job code:** delete after one clean month of R2-only operation, or keep indefinitely as a yfinance/local fallback path?
5. **Q5 — Phase D user store:** SQLite-on-R2 (simplest, single-user) vs Supabase/Turso free tier (multi-user-ready) — decide when Phase D starts.
6. **Q6 — Options data (Phase E):** willingness to take the first paid data dependency when delayed options analytics are scheduled, or scope Phase E to free FINRA/EDGAR datasets only?
7. **Q7 — Report/doc placement:** adopt this file as the living architecture doc and retire the stale sections of CLAUDE.md per A7?

---

## Final classification

**Keep now**
- Entire `src/compute/`, `src/data_sources/` adapters (yfinance, Massive, EDGAR, indices), `Storage` ABC + factory, `read_api.py`, Streamlit app + `app/` modules, `refresh_index_*` jobs and workflows, all 118 tests + fixtures, R2 + Parquet + DuckDB + GitHub Actions platform, provenance/disclaimer system, multi-index universe design (with proxy labeling).

**Refactor next** (Phases A–B)
- `object_store.py`: connection reuse, error separation, `dt=` hive pruning, atomic publish, manifests/watermarks.
- Retire legacy git-data workflows and freeze `data/`; consolidate duplicated job helpers; delete Invesco dead path.
- Compute the five valuation ratios (closes the 100 %-null columns).
- Auth + secrets bridge; CI test/lint gate; truthful docs (README, CLAUDE.md, api-contract, data-dictionary); fix hardcoded "Nasdaq 100" labels.
- Start point-in-time universe snapshots immediately (cheap now, irreplaceable later).

**Build later** (Phases C–G, in order)
- Expanded screener/saved screens/heatmaps/tearsheet history → personalization (auth+, watchlists, alerts, first mutable store) → FINRA/insider/13F/congressional/earnings intelligence → `src/features/` registry with PIT snapshots and transparent scoring → `src/backtest/` engine (correctness → validation → caching → multiprocessing → walk-forward).

**Do not build yet**
- Docker in production, Airflow/Prefect/Dagster, Postgres/Redis/Kubernetes/AWS Batch/Snowflake/Databricks, Polars rewrite, distributed backtesting, real-time/intraday multi-index data, REST API server, options analytics infrastructure — each has a written trigger in §17/§18; none is met.

**Questions requiring owner decision** — Q1–Q7 in §22.
