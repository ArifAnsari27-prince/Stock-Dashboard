# Stock Dashboard

A private, multi-index stock research dashboard: Nasdaq-100, S&P 500, and
Russell 1000/3000 (market-cap proxies) — ~3,000 US names with technicals,
fundamentals, filings, news, screening, heatmaps, and index comparison.

Built for personal, non-commercial investment research on a ~$0/month stack:

```
GitHub Actions (cron jobs) ──► Cloudflare R2 (Parquet, DuckDB) ──► read API ──► Streamlit
```

## ⚠️ Disclaimers

- **Not investment advice.** This is a personal research prototype. Nothing in
  this repository or the running application is a recommendation to buy or sell
  any security.
- **Prototype / delayed / unofficial data.** Prices are end-of-day or delayed
  and come from free-tier and unofficial sources (Massive.com, yfinance —
  personal use only). Fundamentals come from SEC EDGAR. Russell index
  membership is a market-cap proxy, not the official FTSE Russell list. Every
  dataset is provenance-stamped and the UI shows this disclaimer persistently.

## Architecture

- `src/data_sources/` — provider adapters (Massive, yfinance, SEC EDGAR, Nasdaq,
  Wikipedia, Finnhub news), each behind an interface, rate-limited, retried.
- `src/compute/` — pure, network-free indicator/fundamental math (unit-tested).
- `src/storage/` — `Storage` ABC; local Parquet for dev, DuckDB/R2 object store
  for production (selected by `DATA_URI`).
- `src/jobs/` — idempotent scheduled jobs (`python -m src.jobs.<name>`), run by
  the workflows in `.github/workflows/`.
- `src/api/read_api.py` — the only module the frontend imports. The dashboard
  never fetches live data; it reads cached snapshots.
- `streamlit_app.py` + `app/` — the Streamlit frontend.

See `docs/ARCHITECTURE_AUDIT.md` for the full architecture report and roadmap,
`docs/api-contract.md` for the read-API contract, and
`docs/data-dictionary.md` for metric definitions and units.

## Running locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-backend.txt -r requirements-dev.txt
cp .env.example .env   # fill in what you need (see comments)
streamlit run streamlit_app.py
```

With `DATA_URI` unset, the app reads the frozen fixture snapshots in `data/`
(Nasdaq-100, stale). Set `DATA_URI=s3://…` plus the `R2_*` variables to read
production data.

Tests: `python -m pytest -q` (network-free). Lint: `ruff check .`

## Deployment

- **Frontend:** Streamlit Community Cloud pointing at `streamlit_app.py`.
  Access is restricted via Streamlit's viewer allowlist (app is private).
  Credentials go in Streamlit **secrets** (bridged to env vars at startup):
  `DATA_URI`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_REGION`.
- **Ingestion:** GitHub Actions cron (see `.github/workflows/`): weekly universe,
  nightly prices/filings/fundamentals/aggregates, hourly news (optional,
  needs `FINNHUB_API_KEY`). Secrets are repository secrets; never committed.

## Security & compliance

- No API keys in code or git; `.env` is gitignored — copy from `.env.example`.
- SEC EDGAR: descriptive `User-Agent` (`SEC_USER_AGENT`), ≤10 req/s.
- Provider rate limits enforced in code (Massive ~4.6/min, Finnhub ~55/min).
- Every snapshot carries provenance (source, fetched-at, disclaimer).
