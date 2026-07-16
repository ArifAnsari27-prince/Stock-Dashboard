"""Finnhub news adapter — market-wide and per-company headlines (free tier).

Finnhub (https://finnhub.io) is the same provider OpenStock uses for headlines.
The free tier allows ~60 requests/minute; this client paces itself well under
that. Requires FINNHUB_API_KEY — jobs skip news gracefully when it is unset,
so the feature is strictly optional.

Only the `fetch_*` methods touch the network; `parse_news_items` is pure and
unit-tested. The frontend never calls this module — a scheduled job persists
headlines to storage and the dashboard reads the cached snapshot (CLAUDE.md
ingestion/display decoupling).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone

import requests

from src.models import DataSource, NewsItem, Provenance, Snapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"

# Free tier is 60 req/min; 1.1s spacing keeps ~15% margin.
_DEFAULT_MIN_INTERVAL_SECONDS = 1.1


def parse_news_items(payload: list[dict], *, symbol: str | None = None) -> list[NewsItem]:
    """Parse a Finnhub news array into NewsItems (pure).

    Finnhub rows carry `headline`, `source`, `url`, `summary`, and `datetime`
    (unix seconds). Rows without a headline or timestamp are skipped, never
    fabricated.
    """
    items: list[NewsItem] = []
    for row in payload or []:
        headline = (row.get("headline") or "").strip()
        ts = row.get("datetime")
        if not headline or not ts:
            continue
        try:
            published = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            continue
        items.append(
            NewsItem(
                symbol=symbol,
                headline=headline,
                source=(row.get("source") or None),
                url=(row.get("url") or None),
                published_at=published,
                summary=(row.get("summary") or None) or None,
            )
        )
    return items


class FinnhubNewsSource:
    """Rate-limited Finnhub client for market + company news.

    `session` and `sleep` are injectable for offline tests. Per-symbol failures
    are logged and skipped so one bad ticker never aborts the batch.
    """

    source = DataSource.FINNHUB

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_seconds: float = _DEFAULT_MIN_INTERVAL_SECONDS,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._sleep = sleep
        self._min_interval = min_interval_seconds
        self._timeout = timeout
        self._last_request: float | None = None

    def _get(self, path: str, params: dict[str, str]) -> list[dict]:
        if self._last_request is not None:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()
        resp = self._session.get(
            f"{BASE_URL}/{path}",
            params={**params, "token": self._api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def fetch_market_news(self, *, limit: int = 50) -> list[NewsItem]:
        """Latest general market headlines (symbol=None), newest first."""
        payload = self._get("news", {"category": "general"})
        items = parse_news_items(payload)
        items.sort(key=lambda i: i.published_at, reverse=True)
        return items[:limit]

    def fetch_company_news(
        self,
        symbols: Sequence[str],
        *,
        days: int = 7,
        per_symbol_limit: int = 10,
        today: date | None = None,
    ) -> list[NewsItem]:
        """Recent headlines for each symbol over the trailing `days` window.

        Failed symbols are logged and skipped (partial results persist).
        """
        today = today or datetime.now(timezone.utc).date()
        start = (today - timedelta(days=days)).isoformat()
        end = today.isoformat()

        items: list[NewsItem] = []
        failed: list[str] = []
        for symbol in symbols:
            try:
                payload = self._get(
                    "company-news", {"symbol": symbol, "from": start, "to": end}
                )
            except requests.RequestException as exc:
                logger.warning("Company news failed for %s: %s", symbol, exc)
                failed.append(symbol)
                continue
            parsed = parse_news_items(payload, symbol=symbol)
            parsed.sort(key=lambda i: i.published_at, reverse=True)
            items.extend(parsed[:per_symbol_limit])
        if failed:
            logger.warning("Company news failed for %d symbols: %s", len(failed), failed[:10])
        return items

    def fetch_news_snapshot(
        self,
        symbols: Sequence[str],
        *,
        market_limit: int = 50,
        days: int = 7,
        per_symbol_limit: int = 10,
    ) -> Snapshot[NewsItem]:
        """Market + company headlines as one provenance-stamped snapshot."""
        market = self.fetch_market_news(limit=market_limit)
        company = self.fetch_company_news(
            symbols, days=days, per_symbol_limit=per_symbol_limit
        )
        rows = market + company
        return Snapshot[NewsItem](
            provenance=Provenance(
                source=self.source,
                fetched_at=datetime.now(timezone.utc),
                notes=(
                    f"{len(market)} market + {len(company)} company headlines "
                    f"({len(symbols)} symbols, {days}d window) via Finnhub free tier"
                ),
            ),
            rows=rows,
        )
