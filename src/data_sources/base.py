"""Abstract interfaces for external data sources (CLAUDE.md §3).

Implementations live in sibling modules (prices.py for yfinance, edgar.py for
SEC). Jobs and the rest of the system depend only on these ABCs, never on the
concrete classes. This is what keeps the yfinance -> licensed-provider swap a
one-file change.

These interfaces define *what* a source returns, not *how*. Each method returns
a provenance-stamped `Snapshot`. Implementations are responsible for batching,
retry/backoff, partial-failure tolerance, and rate limiting (CLAUDE.md §2);
none of that leaks into these signatures.

No I/O or network access in this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date

from src.models import DataSource, Filing, Fundamentals, PriceBar, Snapshot, Ticker


class PriceSource(ABC):
    """Source of historical/delayed OHLCV price bars.

    The ONLY implementation that may import yfinance is prices.py (CLAUDE.md §2).
    """

    @property
    @abstractmethod
    def source(self) -> DataSource:
        """Provenance tag this source stamps onto its snapshots."""

    @abstractmethod
    def fetch_prices(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
    ) -> Snapshot[PriceBar]:
        """Fetch daily OHLCV bars for `symbols` over [start, end].

        Must tolerate partial failure: a symbol that fails to fetch is logged
        and omitted, never raised through to abort the batch. The returned
        snapshot carries the fetch timestamp and source provenance.
        """


class FundamentalsSource(ABC):
    """Source of normalized company fundamentals (SEC EDGAR in V1)."""

    @property
    @abstractmethod
    def source(self) -> DataSource:
        """Provenance tag this source stamps onto its snapshots."""

    @abstractmethod
    def fetch_fundamentals(self, tickers: Sequence[Ticker]) -> Snapshot[Fundamentals]:
        """Fetch fundamentals for the given tickers.

        Tickers should carry `cik` where available (required for EDGAR lookups).
        Metrics that cannot be reliably derived are returned as null rather than
        fabricated (CLAUDE.md §6). Partial failure is tolerated and logged.
        """


class FilingsSource(ABC):
    """Source of links to the latest SEC filings per company."""

    @property
    @abstractmethod
    def source(self) -> DataSource:
        """Provenance tag this source stamps onto its snapshots."""

    @abstractmethod
    def fetch_filings(self, tickers: Sequence[Ticker]) -> Snapshot[Filing]:
        """Fetch the latest 10-K/10-Q/8-K/Form 4 links for the given tickers.

        Tickers should carry `cik` where available. Partial failure is tolerated
        and logged; the snapshot carries fetch-time provenance.
        """
