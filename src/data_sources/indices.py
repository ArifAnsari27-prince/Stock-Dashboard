"""Multi-index master universe: Nasdaq-100, S&P 500, Russell 1000, Russell 3000.

The universe is a single master list of unique US common stocks (the union of the
four indices ≈ the Russell 3000 superset, ~3,000 names). Each `Ticker` carries
`memberships` (which indices it belongs to), a normalized `sector`, and
`market_cap`, so index-level views are just filters/aggregations over one list.

Sources (all free; see the memory note "data-source-decisions"):
  - Nasdaq-100: Nasdaq list-type API (exact). Reuses universe.fetch_universe.
  - S&P 500: Wikipedia "List of S&P 500 companies" (exact, GICS sector).
  - Russell 1000 / 3000: reconstructed as the top-1,000 / top-3,000 US stocks by
    market cap from the Nasdaq screener API. These are market-cap PROXIES, not
    official FTSE Russell constituents (iShares holdings are bot-blocked, and no
    free official list exists) — labeled as such in provenance.

Pure parse/assemble functions are network-free and unit-tested; only `fetch_*`
functions touch the network.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from src.data_sources import universe as universe_module
from src.models import DataSource, Provenance, Snapshot, Ticker

logger = logging.getLogger(__name__)

# --- Index identifiers + metadata -------------------------------------------

NASDAQ100 = "nasdaq100"
SP500 = "sp500"
RUSSELL1000 = "russell1000"
RUSSELL3000 = "russell3000"

# id -> (display name, ETF price proxy used for index-level performance).
INDEX_METADATA: dict[str, dict[str, str]] = {
    NASDAQ100: {"name": "Nasdaq-100", "etf": "QQQ"},
    SP500: {"name": "S&P 500", "etf": "IVV"},
    RUSSELL1000: {"name": "Russell 1000", "etf": "IWB"},
    RUSSELL3000: {"name": "Russell 3000", "etf": "IWV"},
}

# ETF proxies used for index-level performance comparison.
ETF_PROXIES: tuple[str, ...] = tuple(m["etf"] for m in INDEX_METADATA.values())

RUSSELL1000_SIZE = 1000
RUSSELL3000_SIZE = 3000

SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks"
    "?tableonly=true&limit=10000&offset=0&download=true"
)
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Canonical GICS-like sectors. Nasdaq's screener taxonomy is mapped onto these;
# Wikipedia's GICS labels already match (identity passthrough).
_NASDAQ_SECTOR_MAP = {
    "Technology": "Information Technology",
    "Finance": "Financials",
    "Health Care": "Health Care",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Basic Materials": "Materials",
    "Telecommunications": "Communication Services",
    "Miscellaneous": "Miscellaneous",
}


def normalize_sector(raw: str | None) -> str | None:
    """Map a source sector label to the canonical GICS-like taxonomy."""
    if not raw:
        return None
    label = str(raw).strip()
    return _NASDAQ_SECTOR_MAP.get(label, label)


def normalize_symbol(raw: str) -> str:
    """Normalize a ticker across sources (BRK.B / BRK/B -> BRK-B), upper-cased.

    Aligns Wikipedia/Nasdaq share-class punctuation with the yfinance/EDGAR
    convention so the same security matches across sources.
    """
    return str(raw).strip().upper().replace(".", "-").replace("/", "-")


def _parse_market_cap(raw: object) -> float | None:
    """Parse a screener marketCap cell like '37199111344.00' to a float, or None."""
    if raw is None:
        return None
    text = str(raw).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_screener_payload(payload: dict) -> list[dict]:
    """Parse the Nasdaq screener JSON into rows of {symbol,name,sector,industry,market_cap}."""
    try:
        rows = payload["data"]["rows"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unexpected Nasdaq screener payload shape: {exc}") from exc

    out: list[dict] = []
    for row in rows or []:
        symbol = normalize_symbol(row.get("symbol", ""))
        market_cap = _parse_market_cap(row.get("marketCap"))
        if not symbol or market_cap is None:
            continue
        out.append(
            {
                "symbol": symbol,
                "name": universe_module._clean_company_name(row.get("name")),
                "sector": normalize_sector(row.get("sector")),
                "industry": (row.get("industry") or None),
                "market_cap": market_cap,
            }
        )
    return out


def parse_sp500_wikipedia(html: str) -> list[dict]:
    """Parse the Wikipedia S&P 500 constituents table into {symbol,name,sector}."""
    tables = pd.read_html(io.StringIO(html))
    table = next(
        (t for t in tables if {"Symbol", "Security"}.issubset(set(map(str, t.columns)))),
        None,
    )
    if table is None:
        raise ValueError("Could not find the S&P 500 constituents table on the page")

    sector_col = "GICS Sector" if "GICS Sector" in table.columns else None
    out: list[dict] = []
    for _, row in table.iterrows():
        symbol = normalize_symbol(row["Symbol"])
        if not symbol:
            continue
        out.append(
            {
                "symbol": symbol,
                "name": str(row["Security"]).strip() or None,
                "sector": normalize_sector(row[sector_col]) if sector_col else None,
            }
        )
    return out


def assemble_master(
    nasdaq100: list[Ticker],
    sp500: list[dict],
    screener: list[dict],
) -> list[Ticker]:
    """Build the master universe (pure) from the three parsed sources.

    Russell 1000/3000 are the top-N screener rows by market cap. The master is the
    union of Nasdaq-100, S&P 500, and Russell 3000, sorted by market cap
    descending. Sector prefers the S&P's GICS value where available, else the
    normalized screener sector.
    """
    ranked = sorted(screener, key=lambda r: r["market_cap"], reverse=True)
    r1000 = {r["symbol"] for r in ranked[:RUSSELL1000_SIZE]}
    r3000 = {r["symbol"] for r in ranked[:RUSSELL3000_SIZE]}
    screener_by_symbol = {r["symbol"]: r for r in ranked}

    n100_by_symbol = {normalize_symbol(t.symbol): t for t in nasdaq100}
    sp500_by_symbol = {r["symbol"]: r for r in sp500}
    n100 = set(n100_by_symbol)
    sp = set(sp500_by_symbol)

    master_symbols = n100 | sp | r3000

    tickers: list[Ticker] = []
    for symbol in master_symbols:
        scr = screener_by_symbol.get(symbol)
        sp_row = sp500_by_symbol.get(symbol)
        n1_row = n100_by_symbol.get(symbol)

        memberships = tuple(
            idx
            for idx, present in (
                (NASDAQ100, symbol in n100),
                (SP500, symbol in sp),
                (RUSSELL1000, symbol in r1000),
                (RUSSELL3000, symbol in r3000),
            )
            if present
        )

        # Sector: prefer S&P GICS, then screener. Industry only exists on screener.
        sector = (sp_row or {}).get("sector") or (scr or {}).get("sector")
        industry = (scr or {}).get("industry")
        # Name: prefer screener (clean), then S&P, then Nasdaq-100.
        name = (
            (scr or {}).get("name")
            or (sp_row or {}).get("name")
            or (n1_row.name if n1_row else None)
        )
        market_cap = (scr or {}).get("market_cap")

        tickers.append(
            Ticker(
                symbol=symbol,
                name=name,
                sector=sector,
                industry=industry,
                market_cap=market_cap,
                memberships=memberships,
            )
        )

    tickers.sort(key=lambda t: (t.market_cap or 0.0), reverse=True)
    return tickers


def fetch_screener_rows(url: str = SCREENER_URL, *, timeout: float = 60.0) -> list[dict]:
    """Fetch all US stocks with market cap + sector from the Nasdaq screener (network)."""
    logger.info("Fetching Nasdaq screener universe")
    resp = requests.get(
        url, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"}, timeout=timeout
    )
    resp.raise_for_status()
    return parse_screener_payload(resp.json())


def fetch_sp500_rows(url: str = SP500_WIKI_URL, *, timeout: float = 30.0) -> list[dict]:
    """Fetch S&P 500 constituents from Wikipedia (network)."""
    logger.info("Fetching S&P 500 constituents from Wikipedia")
    html = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=timeout).text
    return parse_sp500_wikipedia(html)


def fetch_master_universe() -> Snapshot[Ticker]:
    """Fetch all three sources and assemble the provenance-stamped master universe (network)."""
    nasdaq100 = universe_module.fetch_universe().rows
    sp500 = fetch_sp500_rows()
    screener = fetch_screener_rows()
    tickers = assemble_master(nasdaq100, sp500, screener)

    counts = {
        idx: sum(1 for t in tickers if idx in t.memberships) for idx in INDEX_METADATA
    }
    logger.info("Assembled master universe: %d unique tickers, memberships=%s",
                len(tickers), counts)
    return Snapshot[Ticker](
        provenance=Provenance(
            source=DataSource.NASDAQ_INDEX,
            fetched_at=datetime.now(timezone.utc),
            notes=(
                "Master universe: Nasdaq-100 (Nasdaq API), S&P 500 (Wikipedia GICS), "
                "Russell 1000/3000 (top-N by market cap from Nasdaq screener — "
                "market-cap PROXY, not official FTSE Russell). "
                f"counts={counts}"
            ),
        ),
        rows=tickers,
    )
