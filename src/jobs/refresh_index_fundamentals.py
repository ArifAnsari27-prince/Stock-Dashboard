"""Multi-index fundamentals job (Phase C): incremental EDGAR -> ObjectStore.

Fetches SEC company-facts for the master universe (~3,000 names) but only for
companies that have **filed a new 10-K/10-Q since the last run** — determined from
the `filings` table's latest financial-filing dates versus each stored
fundamentals row's `latest_filing_date`. Unchanged companies reuse their prior row.
This turns a ~3,000-companyfacts daily job (tens of GB) into a few downloads once
the initial backfill is done. Requires an ObjectStore backend (DATA_URI set).

Run order: filings job before fundamentals (so current filing dates are available).
On the first run, or if `filings` is missing, every company is fetched (full backfill).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timezone

import pandas as pd

from src.config import get_config
from src.data_sources import indices as indices_module
from src.data_sources.base import FundamentalsSource
from src.jobs.common import (
    ensure_universe_ciks,
    fundamentals_from_frame,
    latest_financial_filing_dates,
    load_master_universe,
)
from src.models import DataSource, Fundamentals, Provenance, Snapshot
from src.storage.base import Storage

logger = logging.getLogger(__name__)

FUNDAMENTALS_DATASET = "fundamentals"
FILINGS_DATASET = "filings"

CikMapFn = Callable[[], dict[str, str]]
UniverseFetchFn = Callable[[], object]


def _read_or_empty(storage: Storage, dataset: str) -> pd.DataFrame:
    try:
        return storage.read_latest(dataset)
    except FileNotFoundError:
        return pd.DataFrame()


def run_index_fundamentals_refresh(
    *,
    fundamentals_source: FundamentalsSource,
    fetch_cik_map: CikMapFn,
    storage: Storage,
    fetch_universe_fn: UniverseFetchFn = indices_module.fetch_master_universe,
    force_full: bool = False,
) -> dict[str, object]:
    """Run one incremental fundamentals refresh; return a summary dict."""
    logger.info("Index fundamentals refresh start (force_full=%s)", force_full)
    tickers = load_master_universe(storage, fetch_universe_fn)
    if not tickers:
        return {"universe": 0, "changed": 0, "unchanged": 0, "fundamentals_rows": 0}

    tickers = ensure_universe_ciks(storage, tickers, fetch_cik_map())
    tickers = [t for t in tickers if t.cik]

    current_dates = latest_financial_filing_dates(_read_or_empty(storage, FILINGS_DATASET))
    prior_rows = fundamentals_from_frame(_read_or_empty(storage, FUNDAMENTALS_DATASET))
    prior_by_symbol = {r.symbol: r for r in prior_rows}
    prior_dates = {
        r.symbol: (r.latest_filing_date.isoformat() if r.latest_filing_date else None)
        for r in prior_rows
    }

    changed, unchanged = [], []
    for ticker in tickers:
        current = current_dates.get(ticker.symbol)
        if (
            force_full
            or ticker.symbol not in prior_by_symbol
            or current is None
            or current != prior_dates.get(ticker.symbol)
        ):
            changed.append(ticker)
        else:
            unchanged.append(ticker)

    logger.info("Fundamentals: %d changed (fetch), %d unchanged (reuse)",
                len(changed), len(unchanged))

    fetched = (
        fundamentals_source.fetch_fundamentals(changed).rows if changed else []
    )
    fetched_by_symbol = {r.symbol: r for r in fetched}

    final_rows: list[Fundamentals] = []
    for ticker in tickers:
        symbol = ticker.symbol
        if symbol in fetched_by_symbol:
            row = fetched_by_symbol[symbol]
            filed = current_dates.get(symbol)
            if filed:
                row = row.model_copy(update={"latest_filing_date": date.fromisoformat(filed)})
            final_rows.append(row)
        elif symbol in prior_by_symbol:
            final_rows.append(prior_by_symbol[symbol])  # reuse unchanged (or failed fetch)

    storage.write_snapshot(
        FUNDAMENTALS_DATASET,
        Snapshot(
            provenance=Provenance(
                source=DataSource.SEC_EDGAR,
                fetched_at=datetime.now(timezone.utc),
                notes=(
                    f"{len(final_rows)} fundamentals across the master universe; "
                    f"incremental: {len(fetched)} refetched this run"
                ),
            ),
            rows=final_rows,
        ),
    )

    summary: dict[str, object] = {
        "universe": len(tickers),
        "changed": len(changed),
        "unchanged": len(unchanged),
        "refetched": len(fetched),
        "fundamentals_rows": len(final_rows),
    }
    logger.info("Index fundamentals refresh complete: %s", summary)
    return summary


def main() -> None:
    """Entry point for the scheduled daily fundamentals job."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from src.data_sources.edgar import EdgarClient, EdgarFundamentalsSource
    from src.storage.factory import get_storage

    config = get_config()
    client = EdgarClient()
    run_index_fundamentals_refresh(
        fundamentals_source=EdgarFundamentalsSource(client.get_json),
        fetch_cik_map=client.fetch_cik_map,
        storage=get_storage(config),
    )


if __name__ == "__main__":
    main()
