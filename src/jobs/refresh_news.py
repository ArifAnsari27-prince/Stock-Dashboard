"""Scheduled news refresh: Finnhub headlines -> `news` dataset (latest-only).

Fetches general market headlines plus company headlines for the top-N universe
names by market cap (each company is one API call on the 60/min free tier, so N
stays modest — config.news_top_symbols). The dashboard reads the cached snapshot;
it never fetches news live.

Optional feature: when FINNHUB_API_KEY is unset the job logs and exits cleanly,
so the pipeline works without it.
"""

from __future__ import annotations

import logging

from src.config import Config, get_config
from src.data_sources.news import FinnhubNewsSource
from src.jobs.common import load_master_universe, write_job_summary
from src.storage.base import Storage

logger = logging.getLogger(__name__)

NEWS_DATASET = "news"


def run_news_refresh(
    *,
    news_source: FinnhubNewsSource,
    storage: Storage,
    config: Config | None = None,
) -> dict[str, object]:
    """Fetch and persist one news snapshot; return a summary dict."""
    config = config or get_config()

    tickers = load_master_universe(storage)
    ranked = sorted(tickers, key=lambda t: t.market_cap or 0.0, reverse=True)
    symbols = [t.symbol for t in ranked[: config.news_top_symbols]]

    snapshot = news_source.fetch_news_snapshot(symbols)
    storage.write_snapshot(NEWS_DATASET, snapshot)

    summary: dict[str, object] = {
        "headlines": len(snapshot.rows),
        "company_symbols": len(symbols),
    }
    logger.info("News refresh complete: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from src.storage.factory import get_storage

    config = get_config()
    if not config.finnhub_api_key:
        logger.info("FINNHUB_API_KEY unset — news refresh skipped (optional feature)")
        return
    summary = run_news_refresh(
        news_source=FinnhubNewsSource(config.require_finnhub_api_key()),
        storage=get_storage(config),
        config=config,
    )
    write_job_summary("News refresh", summary)


if __name__ == "__main__":
    main()
