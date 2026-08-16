# Stock Dashboard

A private, multi-index stock research dashboard: Nasdaq-100, S&P 500, and
Russell 1000/3000 (market-cap proxies) — ~3,000 US names with technicals,
fundamentals, filings, news, a customizable screener with saved screens,
filterable heatmaps, and stock/index comparison.

Built for personal, non-commercial investment research on a ~$0/month stack:

```
GitHub Actions (nightly jobs) ──► Cloudflare R2 (Parquet + DuckDB) ──► read API ──► Streamlit
```

> **New here / non-technical?** Read **[`docs/OPERATIONS_GUIDE.md`](docs/OPERATIONS_GUIDE.md)** —
> a plain-English manual covering accounts, setup, maintenance, changing the app
> with an AI, costs, and a full troubleshooting Q&A. This README is the
> developer-facing quickstart.

---

## ⚠️ Disclaimers

- **Not investment advice.** This is a personal research prototype. Nothing here
  is a recommendation to buy or sell any security.
- **Prototype / delayed / unofficial data.** Prices are end-of-day or delayed
  from free-tier / unofficial sources (Massive.com; yfinance is personal-use
  only). Fundamentals come from SEC EDGAR. Russell membership is a **market-cap
  proxy**, not the official FTSE Russell list. Every dataset is provenance-
  stamped and the UI shows this disclaimer persistently.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Downloads & dependencies](#downloads--dependencies)
- [Quick start (local)](#quick-start-local)
- [Configuration (environment variables)](#configuration-environment-variables)
- [Accounts & keys](#accounts--keys)
- [Running the data jobs](#running-the-data-jobs)
- [Testing & linting](#testing--linting)
- [Deployment](#deployment)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Documentation index](#documentation-index)
- [Security & compliance](#security--compliance)

---

## Prerequisites

| Tool | Version | Why | Get it |
|------|---------|-----|--------|
| **Python** | **3.12.x** | Runtime for everything (pins target 3.12) | [python.org/downloads](https://www.python.org/downloads/) · macOS: `brew install python@3.12` |
| **Git** | any recent | Clone the repo, manage branches | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **pip** + **venv** | bundled with Python 3.12 | Install dependencies in isolation | included with Python |

Optional but recommended:

| Tool | Why | Get it |
|------|-----|--------|
| **GitHub CLI (`gh`)** | Open PRs, manage secrets from the terminal | [cli.github.com](https://cli.github.com/) |
| **rclone** | Manually inspect/backup the R2 bucket | [rclone.org/downloads](https://rclone.org/downloads/) |

The project needs **no database server, no Docker, and no cloud CLI** to run
locally — it reads the frozen Parquet fixtures in `data/` out of the box.

---

## Downloads & dependencies

Dependencies are split into three pinned files (exact `==` versions):

| File | Purpose | Install when |
|------|---------|--------------|
| **`requirements.txt`** | App + read API runtime — what Streamlit Cloud installs | Always |
| **`requirements-backend.txt`** | Data ingestion (price/news/EDGAR fetch) | For running jobs locally |
| **`requirements-dev.txt`** | Tests + linter (`pytest`, `ruff`) | For development |

**Core runtime packages** (`requirements.txt`):

| Package | Version | Role |
|---------|---------|------|
| `streamlit` | 1.41.1 | The web UI |
| `pandas` | 2.3.3 | Dataframes / metric tables |
| `numpy` | 2.2.5 | Numeric math |
| `pyarrow` | 22.0.0 | Parquet read/write |
| `duckdb` | 1.1.3 | Queries local Parquet **and** Cloudflare R2 (via httpfs) |
| `pydantic` | 2.10.4 | Typed data models / validation |
| `plotly` | 5.24.1 | Charts, heatmaps, comparisons |

**Ingestion packages** (`requirements-backend.txt`): `yfinance==1.5.1`
(pinned — do not float), `requests==2.32.3`, `massive==2.8.0`.

**Dev packages** (`requirements-dev.txt`): `pytest==8.3.4`, `ruff==0.8.4`.

Install everything for full local development:

```bash
pip install -r requirements.txt -r requirements-backend.txt -r requirements-dev.txt
```

---

## Quick start (local)

```bash
# 1. Get the code
git clone https://github.com/ArifAnsari27-prince/Stock-Dashboard.git
cd Stock-Dashboard

# 2. Create an isolated Python 3.12 environment
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 3. Install dependencies (full dev set)
pip install -r requirements.txt -r requirements-backend.txt -r requirements-dev.txt

# 4. (optional) configure — copy the template and fill in what you need
cp .env.example .env

# 5. Run the dashboard
streamlit run streamlit_app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`).

- **With `DATA_URI` unset** (default): the app reads the **frozen fixture
  snapshots** in `data/` — Nasdaq-100 only and deliberately stale. Great for UI
  work with zero setup.
- **With `DATA_URI=s3://…`** and the `R2_*` values set: the app reads **live
  production data** (~3,000 names) from Cloudflare R2.

---

## Configuration (environment variables)

All configuration is environment variables (see `.env.example`). Locally they
can live in a gitignored `.env`; in production they come from GitHub Actions
secrets and Streamlit secrets.

| Variable | Required for | Example / default |
|----------|--------------|-------------------|
| `DATA_URI` | R2 mode (else local Parquet) | `s3://stock-dashboard-data/prod` |
| `R2_ENDPOINT` | R2 | `https://<acct>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | R2 | — |
| `R2_SECRET_ACCESS_KEY` | R2 | — |
| `R2_REGION` | R2 | `auto` |
| `PRICE_SOURCE` | price ingestion | `massive` (or `yfinance`) |
| `MASSIVE_API_KEY` | `PRICE_SOURCE=massive` | — |
| `SEC_USER_AGENT` | EDGAR ingestion | `Stock Dashboard you@example.com` |
| `FINNHUB_API_KEY` | news ingestion (optional) | — |
| `DATA_DIR` | override local Parquet dir | `data` |

The dashboard (read side) needs only the `DATA_URI` + `R2_*` values. The jobs
(ingestion side) need the provider keys.

---

## Accounts & keys

Five free accounts run the full production system: **GitHub** (code + jobs),
**Cloudflare R2** (storage), **Streamlit Community Cloud** (hosting),
**Massive.com** (prices), **Finnhub** (news, optional). SEC EDGAR needs no
account — just the `SEC_USER_AGENT` email string.

Step-by-step signup and exactly where each key goes is in
**[`docs/OPERATIONS_GUIDE.md`](docs/OPERATIONS_GUIDE.md)** §4–5.

---

## Running the data jobs

Each job is an idempotent module (`python -m src.jobs.<name>`). Locally, set the
provider keys in `.env` first.

| Command | What it does | Cadence in prod |
|---------|--------------|-----------------|
| `python -m src.jobs.refresh_index_universe` | Master universe + memberships + PIT snapshot | Weekly (Sun) |
| `python -m src.jobs.refresh_index_prices` | Bulk prices + computed metrics | Nightly |
| `python -m src.jobs.refresh_index_filings` | SEC filing links | Nightly |
| `python -m src.jobs.refresh_index_fundamentals` | EDGAR fundamentals (incremental) | Nightly |
| `python -m src.jobs.refresh_index_aggregates` | Per-index construction/quantamental/performance | Nightly |
| `python -m src.jobs.refresh_news` | Finnhub headlines (skips if no key) | Hourly (market hours) |

In production these are driven by `.github/workflows/`. The first
`refresh-index-data` run backfills ~2 years and takes 2–3 hours.

---

## Testing & linting

```bash
python -m pytest -q      # 134 tests, fully network-free (fakes + tmp fixtures)
ruff check .             # lint (config in ruff.toml)
```

CI (`.github/workflows/ci.yml`) runs both on every push and pull request.

---

## Deployment

- **Frontend — Streamlit Community Cloud:** point a new app at `streamlit_app.py`
  on `main`. Put the `DATA_URI` + `R2_*` values in **Settings → Secrets**
  (bridged to env at startup by `streamlit_app.py`). Restrict access with the
  **viewer allowlist** in **Settings → Sharing**.
- **Ingestion — GitHub Actions:** provider keys as **repository secrets**,
  `DATA_URI`/`R2_REGION` as **repository variables**. Never commit real values.

Full deployment walkthrough: `docs/OPERATIONS_GUIDE.md` §4 and §6.

---

## Architecture

- `src/data_sources/` — provider adapters (Massive, yfinance, SEC EDGAR, Nasdaq,
  Wikipedia, Finnhub), each isolated behind an interface, rate-limited, retried.
- `src/compute/` — pure, network-free indicator/fundamental math (unit-tested).
- `src/storage/` — `Storage` ABC; local Parquet for dev, DuckDB/R2 object store
  for production (selected by `DATA_URI`).
- `src/jobs/` — idempotent scheduled jobs, run by `.github/workflows/`.
- `src/api/read_api.py` + `src/api/user_store.py` — the **only** modules the
  frontend imports. The dashboard never fetches live data; it reads cached
  snapshots.
- `streamlit_app.py` + `app/` — the Streamlit frontend (pure filter/compare
  logic lives in importable, tested modules; rendering stays thin).

---

## Repository layout

```
streamlit_app.py            # Streamlit entry point
app/                        # UI: screener_filters, compare, charts, columns, ui, theme
src/
  config.py  models.py      # settings + typed data models
  data_sources/             # provider adapters (prices, massive_prices, edgar, indices, news)
  compute/                  # pure technicals / returns / fundamentals / aggregates
  storage/                  # Storage ABC + ParquetStore + R2 ObjectStore + factory
  jobs/                     # scheduled refresh jobs + shared helpers
  api/                      # read_api (data) + user_store (saved screens)
tests/                      # 134 network-free tests
.github/workflows/          # scheduled ingestion + CI
data/                       # frozen local fixtures (see data/README.md)
docs/                       # guides + architecture (see below)
requirements*.txt           # pinned dependencies
```

---

## Documentation index

| Document | Audience | Contents |
|----------|----------|----------|
| **`docs/OPERATIONS_GUIDE.md`** | Non-technical owner | Accounts, setup, maintenance, AI changes, cost, troubleshooting Q&A |
| **`docs/ARCHITECTURE_AUDIT.md`** | Technical | Full architecture report, salvage matrix, phased roadmap A–G |
| **`docs/api-contract.md`** | Developers | The read-API + user-store contract |
| **`docs/data-dictionary.md`** | Developers | Every metric, its units, and definitions |
| **`CLAUDE.md`** | AI assistant + devs | Project guardrails and conventions |

---

## Security & compliance

- **No secrets in code or git.** `.env` is gitignored; copy from `.env.example`.
  Keys live only in GitHub secrets and Streamlit secrets.
- **SEC EDGAR:** descriptive `User-Agent` (`SEC_USER_AGENT`), ≤10 req/s.
- **Provider rate limits enforced in code:** Massive ~4.6/min, Finnhub ~55/min,
  EDGAR <10/s.
- **Provenance on every snapshot** (`_source`, `_fetched_at`, `_disclaimer`).
- **Private by default** via the Streamlit viewer allowlist.
- If a key is ever exposed, **rotate it immediately** (see the guide's Q&A).
