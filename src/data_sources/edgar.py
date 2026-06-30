"""SEC EDGAR access: fundamentals + filing links (CLAUDE.md §2, build step 6).

Implements `FundamentalsSource` (XBRL company facts) and `FilingsSource`
(submissions API) on top of a small rate-limited HTTP client.

SEC etiquette enforced here (CLAUDE.md §2):
  - Every request sends a descriptive User-Agent ("AppName contact@email"),
    read from config (env var), never hardcoded.
  - Max 10 requests/second — the client enforces a minimum spacing between calls.
  - Calls are wrapped in retry/backoff; one ticker failing never aborts the
    batch (partial results are returned).

Design for testability: pure parsers (`parse_company_tickers`,
`parse_latest_filings`) are network-free and unit-tested. The source classes take
an injectable `get_json` callable so they can be driven with canned payloads;
`EdgarClient.get_json` is the real network implementation. Fundamentals
normalization lives in `compute/fundamentals.py`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone

import requests

from src.compute.fundamentals import extract_fundamentals
from src.config import get_config
from src.data_sources.base import FilingsSource, FundamentalsSource
from src.models import (
    DataSource,
    Filing,
    FilingType,
    Fundamentals,
    Provenance,
    Snapshot,
    Ticker,
)

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn}/{doc}"

# Map of SEC form strings we track to our FilingType enum (CLAUDE.md §1).
_TRACKED_FORMS = {
    "10-K": FilingType.FORM_10K,
    "10-Q": FilingType.FORM_10Q,
    "8-K": FilingType.FORM_8K,
    "4": FilingType.FORM_4,
}

GetJsonFn = Callable[[str], dict]


def pad_cik(cik: str | int) -> str:
    """Zero-pad a CIK to the 10-digit form EDGAR URLs use."""
    return str(int(cik)).zfill(10)


def parse_company_tickers(payload: dict) -> dict[str, str]:
    """Parse company_tickers.json into {TICKER: zero-padded-CIK} (pure).

    Yahoo/SEC use '-' for share classes inconsistently; we upper-case the ticker
    and keep it verbatim otherwise.
    """
    mapping: dict[str, str] = {}
    for entry in payload.values():
        ticker = str(entry.get("ticker", "")).strip().upper()
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker] = pad_cik(cik)
    return mapping


def enrich_tickers_with_cik(
    tickers: Sequence[Ticker], cik_map: dict[str, str]
) -> list[Ticker]:
    """Return copies of `tickers` with `cik` filled from `cik_map` where found."""
    out: list[Ticker] = []
    for ticker in tickers:
        cik = cik_map.get(ticker.symbol.upper())
        out.append(ticker.model_copy(update={"cik": cik}) if cik else ticker)
    return out


def parse_latest_filings(submissions: dict, symbol: str) -> list[Filing]:
    """Extract the latest 10-K/10-Q/8-K/Form 4 links from a submissions payload (pure).

    The `filings.recent` arrays are newest-first, so the first occurrence of each
    tracked form is its latest filing.
    """
    cik_int = int(submissions.get("cik", 0))
    cik_padded = pad_cik(cik_int) if cik_int else None
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])

    found: dict[FilingType, Filing] = {}
    for i, form in enumerate(forms):
        filing_type = _TRACKED_FORMS.get(form)
        if filing_type is None or filing_type in found:
            continue
        accn = accns[i] if i < len(accns) else ""
        doc = docs[i] if i < len(docs) else ""
        filed = dates[i] if i < len(dates) else None
        url = ARCHIVES_URL.format(
            cik_int=cik_int, accn=accn.replace("-", ""), doc=doc
        )
        found[filing_type] = Filing(
            symbol=symbol,
            cik=cik_padded,
            form=filing_type,
            filed_date=date.fromisoformat(filed) if filed else date.min,
            accession_number=accn or None,
            url=url,
        )
        if len(found) == len(_TRACKED_FORMS):
            break
    return list(found.values())


class EdgarClient:
    """Rate-limited, retrying JSON client for SEC endpoints."""

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        min_interval_seconds: float = 0.11,  # < 10 req/s
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_agent = user_agent or get_config().require_sec_user_agent()
        self._min_interval = min_interval_seconds
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = self._clock() - self._last_request_at
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_request_at = self._clock()

    def get_json(self, url: str) -> dict:
        """GET a URL as JSON, honoring the rate limit with retry/backoff."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            self._throttle()
            try:
                response = self._session.get(
                    url, headers={"User-Agent": self._user_agent}, timeout=30
                )
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 — network/JSON errors vary
                last_exc = exc
                wait = self._backoff_base * (2**attempt)
                logger.warning(
                    "EDGAR GET failed (attempt %d/%d) %s: %s",
                    attempt + 1,
                    self._max_retries,
                    url,
                    exc,
                )
                if attempt + 1 < self._max_retries:
                    self._sleep(wait)
        raise RuntimeError(f"EDGAR GET failed after retries: {url}") from last_exc

    def fetch_cik_map(self) -> dict[str, str]:
        """Fetch and parse the ticker -> CIK map."""
        return parse_company_tickers(self.get_json(COMPANY_TICKERS_URL))


class EdgarFundamentalsSource(FundamentalsSource):
    """Fetches and normalizes fundamentals from EDGAR XBRL company facts."""

    def __init__(self, get_json: GetJsonFn) -> None:
        self._get_json = get_json

    @property
    def source(self) -> DataSource:
        return DataSource.SEC_EDGAR

    def fetch_fundamentals(self, tickers: Sequence[Ticker]) -> Snapshot[Fundamentals]:
        rows: list[Fundamentals] = []
        skipped: list[str] = []
        for ticker in tickers:
            if not ticker.cik:
                skipped.append(ticker.symbol)
                continue
            url = COMPANYFACTS_URL.format(cik=ticker.cik)
            try:
                payload = self._get_json(url)
                rows.append(
                    extract_fundamentals(payload, ticker.symbol, ticker.cik)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fundamentals fetch failed for %s: %s", ticker.symbol, exc)
                skipped.append(ticker.symbol)

        if skipped:
            logger.warning("Fundamentals: skipped %d tickers: %s", len(skipped), skipped)
        return Snapshot[Fundamentals](
            provenance=Provenance(
                source=self.source,
                fetched_at=datetime.now(timezone.utc),
                notes=f"{len(rows)}/{len(tickers)} fundamentals from SEC EDGAR companyfacts",
            ),
            rows=rows,
        )


class EdgarFilingsSource(FilingsSource):
    """Fetches the latest filing links from EDGAR submissions."""

    def __init__(self, get_json: GetJsonFn) -> None:
        self._get_json = get_json

    @property
    def source(self) -> DataSource:
        return DataSource.SEC_EDGAR

    def fetch_filings(self, tickers: Sequence[Ticker]) -> Snapshot[Filing]:
        rows: list[Filing] = []
        skipped: list[str] = []
        for ticker in tickers:
            if not ticker.cik:
                skipped.append(ticker.symbol)
                continue
            url = SUBMISSIONS_URL.format(cik=ticker.cik)
            try:
                payload = self._get_json(url)
                rows.extend(parse_latest_filings(payload, ticker.symbol))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Filings fetch failed for %s: %s", ticker.symbol, exc)
                skipped.append(ticker.symbol)

        if skipped:
            logger.warning("Filings: skipped %d tickers: %s", len(skipped), skipped)
        return Snapshot[Filing](
            provenance=Provenance(
                source=self.source,
                fetched_at=datetime.now(timezone.utc),
                notes=f"latest filings for {len(tickers) - len(skipped)}/{len(tickers)} tickers",
            ),
            rows=rows,
        )
