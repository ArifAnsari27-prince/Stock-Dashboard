"""yfinance implementation of the PriceSource interface (CLAUDE.md §2, build step 3).

This is the ONLY module in the codebase permitted to import yfinance. Everything
else depends on the `PriceSource` ABC, so swapping yfinance for a licensed
provider later is a one-file change.

yfinance caveats this module is built around (CLAUDE.md §2):
  - Unofficial, delayed 15-20 min, "personal use only" — stamped into provenance.
  - Breaks without warning across versions — pinned in requirements.txt.
  - Flaky — every batch is wrapped in retry/backoff, and a failure on one batch
    or one ticker never aborts the run; partial results are returned.

Design for testability: the network call is injected as `downloader` (defaults
to `yfinance.download`) and `sleep` is injected for backoff. The pure transform
from a yfinance DataFrame to `PriceBar` rows (`frame_to_bars`) is exercised by
unit tests against an in-memory fixture frame — no network.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from src.data_sources.base import PriceSource
from src.models import DataSource, PriceBar, Provenance, Snapshot

logger = logging.getLogger(__name__)

# OHLCV fields we read from a yfinance frame. We request auto_adjust=False so
# both raw "Close" and "Adj Close" are present.
_REQUIRED_FIELDS = ("Open", "High", "Low", "Close", "Volume")

DownloaderFn = Callable[..., pd.DataFrame]
SleepFn = Callable[[float], None]


def _extract_symbol_frame(df: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    """Return the single-symbol sub-frame from a yfinance download result.

    Handles all three column shapes yfinance can produce:
      - MultiIndex grouped by ticker (level 0 == symbol),
      - MultiIndex grouped by column (level 1 == symbol),
      - flat columns (single-ticker download).

    Returns None if the symbol is absent.
    """
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(0):
            return df[symbol]
        if symbol in df.columns.get_level_values(1):
            return df.xs(symbol, axis=1, level=1)
        return None
    # Flat columns: assume the whole frame is this symbol.
    return df


def frame_to_bars(df: pd.DataFrame, symbols: Sequence[str]) -> dict[str, list[PriceBar]]:
    """Convert a yfinance download frame into PriceBar rows per symbol (pure).

    For each requested symbol, rows with any missing required OHLCV value are
    skipped. A symbol that yields zero usable bars is simply absent from the
    result dict — callers treat that as a fetch failure for that symbol. If
    "Adj Close" is missing (e.g. auto-adjusted data), `adj_close` falls back to
    the raw close.
    """
    bars_by_symbol: dict[str, list[PriceBar]] = {}
    if df is None or df.empty:
        return bars_by_symbol

    for symbol in symbols:
        sub = _extract_symbol_frame(df, symbol)
        if sub is None or sub.empty:
            continue
        if not set(_REQUIRED_FIELDS).issubset(sub.columns):
            continue

        has_adj = "Adj Close" in sub.columns
        bars: list[PriceBar] = []
        for index, row in sub.iterrows():
            values = [row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]]
            if any(pd.isna(v) for v in values):
                continue
            close = float(row["Close"])
            adj_close = float(row["Adj Close"]) if has_adj and not pd.isna(
                row["Adj Close"]
            ) else close
            bar_date = index.date() if hasattr(index, "date") else index
            bars.append(
                PriceBar(
                    symbol=symbol,
                    date=bar_date,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=close,
                    adj_close=adj_close,
                    volume=int(row["Volume"]),
                )
            )
        if bars:
            bars_by_symbol[symbol] = bars

    return bars_by_symbol


def _chunked(items: Sequence[str], size: int) -> list[list[str]]:
    """Split `items` into consecutive chunks of at most `size`."""
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


class YFinancePriceSource(PriceSource):
    """Fetches delayed daily OHLCV bars from yfinance, batch + retry tolerant."""

    def __init__(
        self,
        *,
        batch_size: int = 50,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        downloader: DownloaderFn | None = None,
        sleep: SleepFn = time.sleep,
    ) -> None:
        """Configure batching and retry behavior.

        Args:
            batch_size: max symbols per yfinance download call.
            max_retries: attempts per batch before giving up on that batch.
            backoff_base_seconds: base for exponential backoff (base * 2**attempt).
            downloader: injectable replacement for `yfinance.download` (tests).
            sleep: injectable sleep for backoff (tests).
        """
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._downloader: DownloaderFn = downloader or yf.download
        self._sleep = sleep

    @property
    def source(self) -> DataSource:
        return DataSource.YFINANCE

    def _download_batch(
        self, symbols: list[str], start: date, end: date
    ) -> pd.DataFrame | None:
        """Download one batch with retry/backoff. Returns None if all retries fail."""
        for attempt in range(self.max_retries):
            try:
                df = self._downloader(
                    tickers=symbols,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    interval="1d",
                    auto_adjust=False,
                    group_by="ticker",
                    progress=False,
                    threads=True,
                )
                return df
            except Exception as exc:  # noqa: BLE001 — yfinance raises many types
                wait = self.backoff_base_seconds * (2**attempt)
                logger.warning(
                    "yfinance batch download failed (attempt %d/%d) for %d symbols: %s",
                    attempt + 1,
                    self.max_retries,
                    len(symbols),
                    exc,
                )
                if attempt + 1 < self.max_retries:
                    self._sleep(wait)
        logger.error(
            "Giving up on batch after %d attempts (%d symbols): %s",
            self.max_retries,
            len(symbols),
            symbols,
        )
        return None

    def fetch_prices(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> Snapshot[PriceBar]:
        """Fetch daily OHLCV bars for `symbols` over [start, end].

        Batches the symbol list, retries each batch with exponential backoff,
        and tolerates partial failure: symbols whose batch fails or that return
        no usable bars are logged and omitted, never raised. The returned
        snapshot is stamped with the yfinance source and the fetch timestamp.
        """
        requested = list(dict.fromkeys(symbols))  # de-dup, preserve order
        all_bars: list[PriceBar] = []
        succeeded: set[str] = set()

        for batch in _chunked(requested, self.batch_size):
            df = self._download_batch(batch, start, end)
            if df is None:
                continue  # whole batch failed after retries; keep going
            bars_by_symbol = frame_to_bars(df, batch)
            for symbol, bars in bars_by_symbol.items():
                all_bars.extend(bars)
                succeeded.add(symbol)

        failed = [s for s in requested if s not in succeeded]
        if failed:
            logger.warning(
                "Price fetch: %d/%d symbols returned no data: %s",
                len(failed),
                len(requested),
                failed,
            )

        notes = (
            f"{len(succeeded)}/{len(requested)} symbols fetched"
            f"{f'; failed: {failed}' if failed else ''}; "
            "delayed 15-20min, yfinance personal-use only"
        )
        return Snapshot[PriceBar](
            provenance=Provenance(
                source=self.source,
                fetched_at=datetime.now(timezone.utc),
                notes=notes,
            ),
            rows=all_bars,
        )
