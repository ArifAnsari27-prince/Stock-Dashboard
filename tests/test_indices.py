"""Tests for the multi-index master universe assembly (pure, no network)."""

from __future__ import annotations

import pytest

from src.data_sources import indices
from src.data_sources.indices import (
    NASDAQ100,
    RUSSELL1000,
    RUSSELL3000,
    SP500,
    assemble_master,
    normalize_sector,
    normalize_symbol,
    parse_screener_payload,
    parse_sp500_wikipedia,
)
from src.models import Ticker


def test_normalize_symbol():
    assert normalize_symbol("BRK.B") == "BRK-B"
    assert normalize_symbol("brk/b") == "BRK-B"
    assert normalize_symbol(" aapl ") == "AAPL"


def test_normalize_sector():
    assert normalize_sector("Technology") == "Information Technology"
    assert normalize_sector("Basic Materials") == "Materials"
    assert normalize_sector("Telecommunications") == "Communication Services"
    assert normalize_sector("Financials") == "Financials"  # GICS passthrough
    assert normalize_sector(None) is None


def test_parse_screener_payload():
    payload = {
        "data": {
            "rows": [
                {"symbol": "aapl", "name": "Apple Inc. Common Stock",
                 "marketCap": "3000000000000.00", "sector": "Technology",
                 "industry": "Computer Manufacturing"},
                {"symbol": "BAD", "name": "No Cap Co", "marketCap": "",
                 "sector": "Energy", "industry": "x"},  # dropped: no market cap
            ]
        }
    }
    rows = parse_screener_payload(payload)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "AAPL"
    assert r["name"] == "Apple Inc."  # " Common Stock" stripped
    assert r["sector"] == "Information Technology"
    assert r["market_cap"] == pytest.approx(3e12)


def test_parse_screener_bad_shape_raises():
    with pytest.raises(ValueError, match="Unexpected Nasdaq screener payload"):
        parse_screener_payload({"data": {}})


def test_parse_sp500_wikipedia():
    html = (
        "<table>"
        "<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>"
        "<tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td></tr>"
        "<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>"
        "</table>"
    )
    rows = parse_sp500_wikipedia(html)
    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {"AAPL", "BRK-B"}  # BRK.B normalized
    assert by_symbol["AAPL"]["sector"] == "Information Technology"


def test_assemble_master(monkeypatch):
    # Shrink the Russell cutoffs so a 4-row screener exercises them.
    monkeypatch.setattr(indices, "RUSSELL1000_SIZE", 2)
    monkeypatch.setattr(indices, "RUSSELL3000_SIZE", 3)

    nasdaq100 = [Ticker(symbol="AAPL"), Ticker(symbol="NVDA")]
    sp500 = [
        {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Information Technology"},
        {"symbol": "JPM", "name": "JPMorgan", "sector": "Financials"},
    ]
    screener = [
        {"symbol": "NVDA", "name": "NVIDIA", "sector": "Information Technology", "market_cap": 4e12},
        {"symbol": "AAPL", "name": "Apple", "sector": "Miscellaneous", "market_cap": 3e12},
        {"symbol": "JPM", "name": "JPMorgan", "sector": "Financials", "market_cap": 5e11},
        {"symbol": "SMALL", "name": "Small Co", "sector": "Industrials", "market_cap": 1e9},
    ]
    master = assemble_master(nasdaq100, sp500, screener)
    by_symbol = {t.symbol: t for t in master}

    # SMALL is outside top-3 (russell3000) and no other membership -> excluded.
    assert set(by_symbol) == {"NVDA", "AAPL", "JPM"}
    # Sorted by market cap descending.
    assert [t.symbol for t in master] == ["NVDA", "AAPL", "JPM"]

    # Membership flags.
    assert set(by_symbol["AAPL"].memberships) == {NASDAQ100, SP500, RUSSELL1000, RUSSELL3000}
    assert set(by_symbol["NVDA"].memberships) == {NASDAQ100, RUSSELL1000, RUSSELL3000}
    assert set(by_symbol["JPM"].memberships) == {SP500, RUSSELL3000}  # not top-2 -> no R1000

    # Sector prefers S&P GICS over screener ("Miscellaneous" for AAPL).
    assert by_symbol["AAPL"].sector == "Information Technology"
    assert by_symbol["AAPL"].market_cap == pytest.approx(3e12)
