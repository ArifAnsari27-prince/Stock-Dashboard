"""Multi-index bulk price + metrics job (Phase B).

Prices the full master universe (~3,000 names across Nasdaq-100 / S&P 500 /
Russell 1000 / Russell 3000) plus the index ETF proxies, using Massive
grouped-daily aggregates (one API call per trading day for ALL tickers — the only
free-tier-viable way at this scale). Writes:

  - `prices`  — date-partitioned Parquet (append-only history) via the ObjectStore.
  - `metrics` — one latest row per priced name (identity + index-membership flags +
    sector/market_cap + technicals + returns), overwritten each run.

Self-seeding: if no price history exists yet it backfills `price_lookback_days`;
otherwise it fetches only recent days and reads the full history back from the
store to recompute metrics. Requires an ObjectStore backend (DATA_URI set) for
partitioning. Dependencies are injected for offline testing; `main()` wires reals.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from src.config import Config, get_config
from src.data_sources.indices import ETF_PROXIES
from src.data_sources.massive_prices import MassivePriceSource
from src.jobs.common import (
    build_metrics_rows,
    frames_from_price_history,
    load_master_universe,
    write_job_summary,
)
from src.models import DataSource, PriceBar, Provenance, Snapshot

logger = logging.getLogger(__name__)

PRICES_DATASET = "prices"
METRICS_DATASET = "metrics"

# Days re-fetched on an incremental run (small overlap absorbs late/adjusted bars).
_INCREMENTAL_FETCH_DAYS = 7
# History window read back to recompute metrics. ~420 calendar days covers the
# longest indicators (252-day vol/beta, 200-day MA, 12-month momentum) while
# reading far less than the full stored history (egress/ops optimization).
_METRICS_WINDOW_DAYS = 420


def _latest_stored_date(storage) -> date | None:
    """Most recent date already in the price store, or None if empty."""
    existing = storage.read_dataset(PRICES_DATASET, columns=["date"])
    if existing.empty:
        return None
    return pd.to_datetime(existing["date"]).max().date()


def run_index_price_refresh(
    *,
    price_source: MassivePriceSource,
    storage,
    config: Config | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Run one bulk price + metrics refresh; return a summary dict."""
    config = config or get_config()
    today = today or datetime.now(timezone.utc).date()

    tickers = load_master_universe(storage)
    if not tickers:
        logger.error("Master universe empty; nothing to refresh")
        return {"universe": 0, "priced": 0, "metrics_rows": 0}

    symbols = list(dict.fromkeys([t.symbol for t in tickers] + list(ETF_PROXIES)))

    # Backfill on first run; otherwise just recent days.
    latest = _latest_stored_date(storage)
    if latest is None:
        start = today - timedelta(days=config.price_lookback_days)
        logger.info("No price history: backfilling from %s", start)
    else:
        start = latest - timedelta(days=2)
        logger.info("Incremental: fetching from %s (latest stored %s)", start, latest)
        start = max(start, today - timedelta(days=_INCREMENTAL_FETCH_DAYS))

    snapshot = price_source.fetch_grouped_daily(symbols, start, today)

    # Write date-partitioned prices (one partition per trading day).
    bars_by_date: dict[date, list[PriceBar]] = defaultdict(list)
    for bar in snapshot.rows:
        bars_by_date[bar.date].append(bar)
    for day, day_bars in sorted(bars_by_date.items()):
        storage.write_partition(
            PRICES_DATASET,
            f"date={day.isoformat()}",
            Snapshot[PriceBar](provenance=snapshot.provenance, rows=day_bars),
        )
    logger.info("Wrote %d partitions (%d bars)", len(bars_by_date), len(snapshot.rows))

    # Recompute metrics from a trailing window of stored history (enough for the
    # longest indicator), not the full 2yr — bounds per-run reads.
    metrics_cutoff = today - timedelta(days=_METRICS_WINDOW_DAYS)
    history = storage.read_dataset(
        PRICES_DATASET, where=f"date >= '{metrics_cutoff.isoformat()}'"
    )
    frames = frames_from_price_history(history)
    benchmark_series = {
        etf.lower(): frames[etf]["adj_close"] for etf in ETF_PROXIES if etf in frames
    }
    rows, missing = build_metrics_rows(tickers, frames, benchmark_series)
    if missing:
        logger.warning("%d universe symbols had no price data (e.g. %s)",
                       len(missing), missing[:10])

    storage.write_latest(
        METRICS_DATASET,
        Snapshot(
            provenance=Provenance(
                source=DataSource.COMPUTED,
                fetched_at=datetime.now(timezone.utc),
                notes=(
                    f"metrics for {len(rows)} names across "
                    f"{len(ETF_PROXIES)} index proxies; derived from "
                    f"{snapshot.provenance.source.value} prices"
                ),
            ),
            rows=rows,
        ),
    )

    summary: dict[str, object] = {
        "universe": len(tickers),
        "priced": len(frames),
        "metrics_rows": len(rows),
        "missing": len(missing),
        "partitions_written": len(bars_by_date),
    }
    logger.info("Index price refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled bulk-pricing job."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from src.storage.factory import get_storage

    config = get_config()
    storage = get_storage(config)
    price_source = MassivePriceSource(
        config.require_massive_api_key(),
        min_request_interval_seconds=config.massive_min_request_interval_seconds,
    )
    summary = run_index_price_refresh(
        price_source=price_source, storage=storage, config=config
    )
    write_job_summary("Index price refresh", summary)


if __name__ == "__main__":
    main()
