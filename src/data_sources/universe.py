"""Nasdaq 100 universe construction (CLAUDE.md §1, build step 2).

The Nasdaq 100 is the universe; QQQ is the ETF that tracks it. We support two
interchangeable sources behind the same `Snapshot[Ticker]` output, in keeping
with the swappable-source principle (CLAUDE.md §2/§3):

  1. PRIMARY (live): Nasdaq's official constituent list endpoint
     (api.nasdaq.com). This is what `fetch_universe` uses. It is robust and
     free; QQQ tracks this exact index, so the membership is the QQQ universe.
     It does NOT provide ETF weights, so `Ticker.weight` is left None (we don't
     fabricate a weight — CLAUDE.md §6).

  2. ALTERNATIVE: Invesco's QQQ holdings CSV export, in the format::

         Ticker,Company,Share/ Par,% TNA,Class of shares,Market value

     This carries true ETF weights but Invesco's download endpoint is behind
     bot protection (returns 406 to non-browser clients), so it is not usable
     for an unattended job. The CSV parser is retained for when a file is
     supplied another way (e.g. a manual download committed to the repo).

Separation of concerns: parsing/cleaning functions are PURE and network-free
(unit-tested against fixtures); only `fetch_*` functions touch the network.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timezone

import pandas as pd
import requests

from src.models import DataSource, Provenance, Snapshot, Ticker

logger = logging.getLogger(__name__)

# --- Primary source: Nasdaq official Nasdaq-100 constituent list -------------

NASDAQ100_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"

# api.nasdaq.com serves junk/blocks requests without a browser-like User-Agent.
# This is unrelated to the SEC User-Agent (that one is for EDGAR only).
_NASDAQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Trailing security-type descriptors to strip from Nasdaq company names.
_NAME_SUFFIX_RE = re.compile(r"\s+Common Stock$", re.IGNORECASE)

# Invesco's official QQQ holdings CSV download endpoint. The `audienceType` and
# `action=download` query params trigger the CSV response.
#
# NOTE: This endpoint and its parameters are Invesco's public download URL as
# documented at build time. yfinance is NOT used for the universe. Because this
# is an unofficial, undocumented-contract URL that Invesco can change without
# notice, `fetch_universe` must be validated against a live response the first
# time the refresh job runs, and failures must surface loudly (CLAUDE.md §2/§6).
INVESCO_HOLDINGS_URL = (
    "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0"
)

# Default query parameters for a QQQ CSV download.
INVESCO_QQQ_PARAMS: dict[str, str] = {
    "audienceType": "Investor",
    "action": "download",
    "ticker": "QQQ",
}

# The "Class of shares" value that identifies a real common-equity holding.
# Everything else (money-market funds, swaps, index legs, cash, currency) is
# excluded. Note "Swap Common Stock" is intentionally NOT a match.
EQUITY_SHARE_CLASS = "Common Stock"

# Ticker cells that are never real equities.
_PLACEHOLDER_TICKERS = frozenset({"--", "", "USD"})

# Canonical column names in the Invesco export.
_COL_TICKER = "Ticker"
_COL_COMPANY = "Company"
_COL_SHARE_CLASS = "Class of shares"
_COL_WEIGHT = "% TNA"

_AS_OF_RE = re.compile(r"as of\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def parse_holdings_csv(content: str) -> pd.DataFrame:
    """Parse raw Invesco holdings CSV text into a DataFrame.

    Strips a leading UTF-8 BOM if present, treats `#`-prefixed lines (e.g. the
    trailing "# as of YYYY-MM-DD" comment) and blank lines as non-data, and
    trims whitespace from column names. Returns the rows verbatim (strings);
    filtering/typing happens in `clean_universe`.
    """
    text = content.lstrip("﻿")
    df = pd.read_csv(
        io.StringIO(text),
        dtype=str,
        comment="#",
        skip_blank_lines=True,
        skipinitialspace=True,
    )
    df.columns = [str(c).strip() for c in df.columns]
    return df


def extract_as_of_date(content: str) -> date | None:
    """Return the holdings "as of" date from a trailing comment, if present.

    Invesco exports end with a line like ``# as of 2026-06-29``. Returns None if
    no such marker is found.
    """
    match = _AS_OF_RE.search(content)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def _parse_weight(raw: object) -> float | None:
    """Convert a "% TNA" cell like ``"8.84%"`` to a fraction (0.0884).

    Returns None if the value is missing or unparseable.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip().replace("%", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def clean_universe(df: pd.DataFrame) -> list[Ticker]:
    """Filter a holdings DataFrame down to clean equity constituents.

    Rules (CLAUDE.md §1, step 2):
      1. Keep only rows whose `Class of shares` is exactly "Common Stock".
      2. Drop placeholder/non-equity tickers ("--", "USD", blank).
      3. De-duplicate by ticker symbol, keeping the first occurrence (Invesco
         sorts equities by weight descending, so this keeps the largest).

    Dual-class listings with distinct symbols (e.g. GOOG and GOOGL) are both
    retained — they are different securities, not duplicates. Returns Tickers in
    input order (weight-descending), with `weight` populated from "% TNA" and
    `cik` left None (CIK mapping happens later via EDGAR).
    """
    required = {_COL_TICKER, _COL_SHARE_CLASS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Holdings frame is missing required column(s): {sorted(missing)}"
        )

    has_company = _COL_COMPANY in df.columns
    has_weight = _COL_WEIGHT in df.columns

    tickers: list[Ticker] = []
    seen: set[str] = set()

    for _, row in df.iterrows():
        share_class = str(row[_COL_SHARE_CLASS]).strip()
        if share_class != EQUITY_SHARE_CLASS:
            continue

        symbol = str(row[_COL_TICKER]).strip().upper()
        if symbol in _PLACEHOLDER_TICKERS or symbol == "NAN":
            continue
        if symbol in seen:
            continue
        seen.add(symbol)

        name = None
        if has_company:
            raw_name = str(row[_COL_COMPANY]).strip()
            name = raw_name or None

        weight = _parse_weight(row[_COL_WEIGHT]) if has_weight else None

        tickers.append(Ticker(symbol=symbol, name=name, weight=weight))

    return tickers


def build_universe_snapshot(
    content: str,
    *,
    fetched_at: datetime | None = None,
    source: DataSource = DataSource.QQQ_HOLDINGS,
) -> Snapshot[Ticker]:
    """Parse + clean raw holdings CSV into a provenance-stamped Snapshot.

    Pure given `content`. `fetched_at` defaults to now (UTC). The holdings
    "as of" date, if present in the file, is recorded in provenance notes so the
    constituent list is self-describing and dated.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)

    df = parse_holdings_csv(content)
    tickers = clean_universe(df)

    as_of = extract_as_of_date(content)
    notes = f"QQQ holdings as of {as_of.isoformat()}" if as_of else None

    return Snapshot[Ticker](
        provenance=Provenance(source=source, fetched_at=fetched_at, notes=notes),
        rows=tickers,
    )


def fetch_holdings_csv(
    url: str = INVESCO_HOLDINGS_URL,
    params: dict[str, str] | None = None,
    *,
    timeout: float = 30.0,
) -> str:
    """Download holdings CSV text over HTTP (network).

    Raises on a non-2xx response so failures surface loudly rather than writing
    an empty/garbage universe. Decoding uses the response's apparent encoding so
    a BOM is handled by `parse_holdings_csv` downstream.
    """
    params = params if params is not None else dict(INVESCO_QQQ_PARAMS)
    logger.info("Fetching holdings CSV from %s params=%s", url, params)
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_universe_from_invesco_csv(
    url: str = INVESCO_HOLDINGS_URL,
    params: dict[str, str] | None = None,
) -> Snapshot[Ticker]:
    """Fetch QQQ holdings from the Invesco CSV endpoint (network).

    NOTE: Invesco's endpoint is bot-protected and currently returns 406 to
    non-browser clients, so this is not usable for the unattended job. Kept for
    the case where the CSV is obtained another way.
    """
    content = fetch_holdings_csv(url, params)
    snapshot = build_universe_snapshot(content)
    logger.info("Built universe with %d constituents", len(snapshot.rows))
    return snapshot


def _clean_company_name(raw: object) -> str | None:
    """Strip trailing security descriptors (e.g. ' Common Stock') from a name."""
    if raw is None:
        return None
    name = _NAME_SUFFIX_RE.sub("", str(raw).strip()).strip()
    return name or None


def parse_nasdaq100_payload(payload: dict) -> list[Ticker]:
    """Parse the api.nasdaq.com nasdaq100 JSON payload into clean Tickers (pure).

    Expects the documented shape ``payload["data"]["data"]["rows"]`` where each
    row has at least ``symbol`` and ``companyName``. De-duplicates by symbol
    (preserving order); dual-class listings with distinct symbols are both kept.
    Weights are not provided by this source and are left None.
    """
    try:
        rows = payload["data"]["data"]["rows"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unexpected Nasdaq payload shape: {exc}") from exc

    tickers: list[Ticker] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        tickers.append(
            Ticker(symbol=symbol, name=_clean_company_name(row.get("companyName")))
        )
    return tickers


def fetch_universe(
    url: str = NASDAQ100_URL,
    *,
    timeout: float = 30.0,
) -> Snapshot[Ticker]:
    """Fetch the Nasdaq-100 constituents and return a provenance-stamped Snapshot (network).

    This is the PRIMARY universe source. Raises on a non-2xx response so failures
    surface loudly rather than writing an empty universe (CLAUDE.md §2).
    """
    logger.info("Fetching Nasdaq-100 constituents from %s", url)
    response = requests.get(url, headers=_NASDAQ_HEADERS, timeout=timeout)
    response.raise_for_status()
    tickers = parse_nasdaq100_payload(response.json())

    snapshot = Snapshot[Ticker](
        provenance=Provenance(
            source=DataSource.NASDAQ_INDEX,
            fetched_at=datetime.now(timezone.utc),
            notes="Nasdaq-100 constituents via api.nasdaq.com (QQQ tracks this index)",
        ),
        rows=tickers,
    )
    logger.info("Built universe with %d constituents", len(snapshot.rows))
    return snapshot
