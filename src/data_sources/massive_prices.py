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

import pandas as pd

from src.data_sources.base import PriceSource
from src.models import DataSource, PriceBar, Provenance, Snapshot

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]
GroupedDailyFn = Callable[[str], Iterable["GroupedAggLike"]]


def _norm_symbol(ticker: str) -> str:
    """Normalize a ticker to the master-universe convention (BRK.B -> BRK-B)."""
    return str(ticker).strip().upper().replace(".", "-").replace("/", "-")


class AggLike(Protocol):
    """Subset of Massive Agg / GroupedDailyAgg used by this adapter."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int


class GroupedAggLike(AggLike, Protocol):
    """A grouped-daily aggregate additionally carries its own ticker."""

    ticker: str


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


def grouped_aggs_to_bars(
    aggs: Iterable[GroupedAggLike], symbols: set[str]
) -> list[PriceBar]:
    """Convert one day's grouped-daily aggs to PriceBars, keeping only `symbols` (pure).

    `symbols` must be normalized (BRK-B form); each agg's own ticker is normalized
    the same way before matching.
    """
    bars: list[PriceBar] = []
    seen: set[str] = set()  # grouped-daily occasionally repeats a ticker in a day
    for agg in aggs:
        ticker = getattr(agg, "ticker", None)
        if ticker is None:
            continue
        symbol = _norm_symbol(ticker)
        if symbol not in symbols or symbol in seen:
            continue
        seen.add(symbol)
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
                adj_close=close,
                volume=int(vol),
            )
        )
    return bars


class MassivePriceSource(PriceSource):
    """Fetches daily OHLCV from Massive.com REST API, rate-limited per symbol."""

    def __init__(
        self,
        api_key: str,
        *,
        min_request_interval_seconds: float = 13.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 30.0,
        connect_timeout_seconds: float = 15.0,
        read_timeout_seconds: float = 120.0,
        client_retries: int = 1,
        list_aggs_fn: Callable[..., Iterable[AggLike]] | None = None,
        grouped_daily_fn: GroupedDailyFn | None = None,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self._api_key = api_key
        # 13s spacing (~4.6/min) keeps margin under the 5/min free-tier limit.
        self.min_request_interval_seconds = min_request_interval_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        # The grouped-daily payload is large (~12k tickers), so the default 10s
        # read timeout is too short — bump it. Keep the client's own retries low
        # so a failure surfaces to our paced retry instead of bursting past 5/min.
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._client_retries = client_retries
        self._sleep = sleep
        self._list_aggs_fn = list_aggs_fn
        self._grouped_daily_fn = grouped_daily_fn
        self._last_request_at: float | None = None

    def _rest_client(self):
        """Construct a Massive RESTClient with our timeout/retry settings."""
        from massive import RESTClient

        return RESTClient(
            self._api_key,
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            retries=self._client_retries,
        )

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

    def _resolve_grouped_fn(self) -> GroupedDailyFn:
        if self._grouped_daily_fn is not None:
            return self._grouped_daily_fn
        client = self._rest_client()

        def grouped(day_iso: str) -> Iterable[GroupedAggLike]:
            return client.get_grouped_daily_aggs(day_iso, adjusted=True)

        return grouped

    def _grouped_with_retry(self, grouped_fn: GroupedDailyFn, day_iso: str) -> list:
        """One grouped-daily call with pacing + retry on rate limit; [] on failure."""
        for attempt in range(self.max_retries):
            self._pace()
            self._mark_request()
            try:
                return list(grouped_fn(day_iso))
            except Exception as exc:  # noqa: BLE001 — Massive raises varied HTTP errors
                msg = str(exc).lower()
                if ("429" in msg or "rate" in msg) and attempt + 1 < self.max_retries:
                    wait = self.backoff_base_seconds * (2**attempt)
                    logger.warning("Massive grouped-daily rate limit %s, sleeping %.0fs",
                                   day_iso, wait)
                    self._sleep(wait)
                    continue
                logger.warning("Massive grouped-daily failed for %s: %s", day_iso, exc)
                return []
        return []

    def fetch_grouped_daily(
        self, symbols: Sequence[str], start: date, end: date
    ) -> Snapshot[PriceBar]:
        """Fetch daily bars for `symbols` over [start, end] via grouped-daily aggregates.

        ONE API call per trading day returns every US ticker, from which we keep
        only `symbols`. This is the free-tier-friendly way to price thousands of
        names: ~1 call/day incrementally, ~500 calls to backfill 2 years. Weekend
        days are skipped; holidays return empty and are tolerated.
        """
        symset = {_norm_symbol(s) for s in symbols}
        grouped_fn = self._resolve_grouped_fn()
        days = pd.bdate_range(start, end)

        logger.info(
            "Massive grouped-daily: %d trading days, %d target symbols (%.1fs interval)",
            len(days), len(symset), self.min_request_interval_seconds,
        )
        all_bars: list[PriceBar] = []
        days_with_data = 0
        for ts in days:
            day_iso = ts.date().isoformat()
            aggs = self._grouped_with_retry(grouped_fn, day_iso)
            day_bars = grouped_aggs_to_bars(aggs, symset)
            if day_bars:
                days_with_data += 1
            all_bars.extend(day_bars)

        symbols_seen = {b.symbol for b in all_bars}
        notes = (
            f"grouped-daily: {len(symbols_seen)}/{len(symset)} symbols over "
            f"{days_with_data}/{len(days)} trading days; Massive.com EOD (adjusted)"
        )
        return Snapshot[PriceBar](
            provenance=Provenance(
                source=self.source,
                fetched_at=datetime.now(timezone.utc),
                notes=notes,
            ),
            rows=all_bars,
        )

    def _fetch_symbol_aggs(
        self, symbol: str, start: date, end: date
    ) -> list[AggLike]:
        """Fetch daily adjusted aggregates for one symbol with retry on rate limit."""
        list_aggs = self._list_aggs_fn
        if list_aggs is None:
            client = self._rest_client()

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
