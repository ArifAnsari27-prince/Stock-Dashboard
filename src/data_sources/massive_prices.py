"""Massive.com implementation of PriceSource (formerly Polygon.io).

This is the ONLY module permitted to import the `massive` client library.
Free tier: end-of-day aggregates, 5 API requests/minute — we pace one symbol
per request via `list_aggs` (daily bars, up to 50k per call) with a configurable
minimum interval between calls.

yfinance remains available via `prices.py`; select the adapter with PRICE_SOURCE.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timezone
from typing import Any, Protocol

from src.data_sources.base import PriceSource
from src.models import DataSource, PriceBar, Provenance, Snapshot

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]


class AggLike(Protocol):
    """Subset of Massive Agg / GroupedDailyAgg used by this adapter."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int


def agg_timestamp_to_date(timestamp_ms: int) -> date:
    """Convert Massive millisecond epoch timestamp to a UTC calendar date."""
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).date()


def aggs_to_bars(symbol: str, aggs: Iterable[AggLike]) -> list[PriceBar]:
    """Convert Massive daily aggregate objects to PriceBar rows (pure, testable)."""
    bars: list[PriceBar] = []
    for agg in aggs:
        o, h, low, c, vol = agg.open, agg.high, agg.low, agg.close, agg.volume
        if any(v is None for v in (o, h, low, c, vol)):  # type: ignore[redundant-expr]
            continue
        if any(_is_nan(v) for v in (o, h, low, c, vol)):
            continue
        close = float(c)
        bars.append(
            PriceBar(
                symbol=symbol,
                date=agg_timestamp_to_date(int(agg.timestamp)),
                open=float(o),
                high=float(h),
                low=float(low),
                close=close,
                adj_close=close,  # adjusted=True on the API request
                volume=int(vol),
            )
        )
    return bars


def _is_nan(value: float) -> bool:
    return value != value  # noqa: PLR0124


class MassivePriceSource(PriceSource):
    """Fetches daily OHLCV from Massive.com REST API, rate-limited per symbol."""

    def __init__(
        self,
        api_key: str,
        *,
        min_request_interval_seconds: float = 12.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 30.0,
        list_aggs_fn: Callable[..., Iterable[AggLike]] | None = None,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self._api_key = api_key
        self.min_request_interval_seconds = min_request_interval_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._sleep = sleep
        self._list_aggs_fn = list_aggs_fn
        self._last_request_at: float | None = None

    @property
    def source(self) -> DataSource:
        return DataSource.MASSIVE

    def _pace(self) -> None:
        """Enforce minimum spacing between API calls (free tier: 5/min)."""
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_request_interval_seconds - elapsed
        if wait > 0:
            self._sleep(wait)

    def _mark_request(self) -> None:
        self._last_request_at = time.monotonic()

    def _fetch_symbol_aggs(
        self, symbol: str, start: date, end: date
    ) -> list[AggLike]:
        """Fetch daily adjusted aggregates for one symbol with retry on rate limit."""
        list_aggs = self._list_aggs_fn
        if list_aggs is None:
            from massive import RESTClient

            client = RESTClient(self._api_key)

            def list_aggs(**kwargs: Any) -> Iterable[AggLike]:
                return client.list_aggs(**kwargs)

        for attempt in range(self.max_retries):
            self._pace()
            self._mark_request()
            try:
                return list(
                    list_aggs(
                        ticker=symbol,
                        multiplier=1,
                        timespan="day",
                        from_=start.isoformat(),
                        to=end.isoformat(),
                        adjusted=True,
                        sort="asc",
                        limit=50_000,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — Massive raises varied HTTP errors
                msg = str(exc).lower()
                is_rate_limit = "429" in msg or "rate" in msg
                if is_rate_limit and attempt + 1 < self.max_retries:
                    wait = self.backoff_base_seconds * (2**attempt)
                    logger.warning(
                        "Massive rate limit for %s (attempt %d/%d), sleeping %.0fs",
                        symbol,
                        attempt + 1,
                        self.max_retries,
                        wait,
                    )
                    self._sleep(wait)
                    continue
                logger.warning("Massive fetch failed for %s: %s", symbol, exc)
                return []
        return []

    def fetch_prices(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> Snapshot[PriceBar]:
        """Fetch daily adjusted OHLCV for each symbol (one API call per symbol).

        Partial failure is tolerated: symbols that error or return no bars are
        logged and omitted. Free-tier pacing applies between every symbol.
        """
        requested = list(dict.fromkeys(symbols))
        all_bars: list[PriceBar] = []
        succeeded: set[str] = set()

        logger.info(
            "Massive price fetch: %d symbols, %s to %s (%.1fs min interval)",
            len(requested),
            start,
            end,
            self.min_request_interval_seconds,
        )

        for symbol in requested:
            aggs = self._fetch_symbol_aggs(symbol, start, end)
            bars = aggs_to_bars(symbol, aggs)
            if bars:
                all_bars.extend(bars)
                succeeded.add(symbol)
            else:
                logger.warning("No Massive bars for %s", symbol)

        failed = [s for s in requested if s not in succeeded]
        if failed:
            logger.warning(
                "Massive price fetch: %d/%d symbols returned no data: %s",
                len(failed),
                len(requested),
                failed,
            )

        est_minutes = max(0, (len(requested) - 1)) * self.min_request_interval_seconds / 60.0
        notes = (
            f"{len(succeeded)}/{len(requested)} symbols fetched; "
            f"Massive.com EOD aggregates (adjusted); "
            f"free-tier paced (~{est_minutes:.0f} min for full universe)"
        )
        if failed:
            notes += f"; failed: {failed}"

        return Snapshot[PriceBar](
            provenance=Provenance(
                source=self.source,
                fetched_at=datetime.now(timezone.utc),
                notes=notes,
            ),
            rows=all_bars,
        )
