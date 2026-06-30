"""Tests for universe construction filtering (build step 2).

These exercise only the pure, network-free functions in
`src.data_sources.universe` against a real Invesco holdings export fixture. No
HTTP is performed.

The fixture is an Invesco *QBIG* (Top QQQ ETF) export rather than QQQ itself,
but it shares the exact export format and conveniently contains every junk row
type the filter must reject: money-market fund, index legs, swap-common-stock,
uninvestible cash, currency, and swap rows.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.data_sources.universe import (
    EQUITY_SHARE_CLASS,
    build_universe_snapshot,
    clean_universe,
    extract_as_of_date,
    parse_holdings_csv,
    parse_nasdaq100_payload,
)
from src.models import DataSource

FIXTURE = Path(__file__).parent / "fixtures" / "invesco_qbig_holdings.csv"
NASDAQ_FIXTURE = Path(__file__).parent / "fixtures" / "nasdaq100_payload.json"

# The only real common-equity holdings in the fixture, in weight-descending order.
EXPECTED_SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "GOOGL", "AMZN", "GOOG", "AVGO"]


@pytest.fixture
def holdings_text() -> str:
    # Read with utf-8-sig to mirror a real download; the BOM is intentional.
    return FIXTURE.read_text(encoding="utf-8-sig")


def test_parse_strips_bom_and_trailing_comment(holdings_text: str) -> None:
    df = parse_holdings_csv(holdings_text)
    # Column header must be clean despite the leading BOM.
    assert df.columns[0] == "Ticker"
    # The "# as of ..." trailing comment line must not become a data row.
    assert not df["Ticker"].astype(str).str.startswith("#").any()


def test_clean_universe_keeps_only_common_stock(holdings_text: str) -> None:
    df = parse_holdings_csv(holdings_text)
    tickers = clean_universe(df)
    assert [t.symbol for t in tickers] == EXPECTED_SYMBOLS


def test_clean_universe_excludes_non_equity_classes(holdings_text: str) -> None:
    df = parse_holdings_csv(holdings_text)
    symbols = {t.symbol for t in clean_universe(df)}
    # Money-market fund, index legs, cash, currency placeholders must be gone.
    assert "IUGXX" not in symbols  # money market fund
    assert "MLQBIG02" not in symbols  # index leg
    assert "USD" not in symbols  # currency
    assert "--" not in symbols  # swap / cash placeholders


def test_swap_common_stock_is_not_treated_as_equity(holdings_text: str) -> None:
    # Societe Generale is "Swap Common Stock" with a "--" ticker — must be dropped.
    df = parse_holdings_csv(holdings_text)
    names = {t.name for t in clean_universe(df)}
    assert "Societe Generale SA" not in names


def test_dual_class_symbols_both_retained(holdings_text: str) -> None:
    # GOOG and GOOGL are distinct securities, not duplicates.
    df = parse_holdings_csv(holdings_text)
    symbols = [t.symbol for t in clean_universe(df)]
    assert "GOOG" in symbols and "GOOGL" in symbols


def test_weight_parsed_as_fraction(holdings_text: str) -> None:
    df = parse_holdings_csv(holdings_text)
    by_symbol = {t.symbol: t for t in clean_universe(df)}
    # NVDA "% TNA" is 8.84% -> 0.0884.
    assert by_symbol["NVDA"].weight == pytest.approx(0.0884)
    assert by_symbol["NVDA"].name == "NVIDIA Corp"
    assert by_symbol["NVDA"].cik is None


def test_extract_as_of_date(holdings_text: str) -> None:
    assert extract_as_of_date(holdings_text) == date(2026, 6, 29)


def test_build_universe_snapshot(holdings_text: str) -> None:
    snap = build_universe_snapshot(
        holdings_text, fetched_at=datetime(2026, 6, 30, tzinfo=timezone.utc)
    )
    assert snap.provenance.source == DataSource.QQQ_HOLDINGS
    assert snap.provenance.notes == "QQQ holdings as of 2026-06-29"
    assert len(snap.rows) == len(EXPECTED_SYMBOLS)


def test_clean_universe_dedups_repeated_symbol() -> None:
    # Inline frame: same symbol twice -> first (higher weight) wins.
    df = pd.DataFrame(
        {
            "Ticker": ["AAPL", "AAPL", "MSFT"],
            "Company": ["Apple Inc", "Apple Inc", "Microsoft Corp"],
            "% TNA": ["6.04%", "5.00%", "4.69%"],
            "Class of shares": [EQUITY_SHARE_CLASS] * 3,
        }
    )
    tickers = clean_universe(df)
    assert [t.symbol for t in tickers] == ["AAPL", "MSFT"]
    assert tickers[0].weight == pytest.approx(0.0604)


def test_clean_universe_raises_on_missing_columns() -> None:
    df = pd.DataFrame({"Ticker": ["AAPL"]})  # no "Class of shares"
    with pytest.raises(ValueError, match="missing required column"):
        clean_universe(df)


# --- Nasdaq-100 API payload (primary live source) ----------------------------


def test_parse_nasdaq100_payload_cleans_names_and_dedups() -> None:
    payload = json.loads(NASDAQ_FIXTURE.read_text())
    tickers = parse_nasdaq100_payload(payload)
    # Duplicate AAPL row collapsed; GOOG/GOOGL both retained.
    assert [t.symbol for t in tickers] == ["AAPL", "MSFT", "GOOGL", "GOOG"]
    by_symbol = {t.symbol: t for t in tickers}
    # " Common Stock" suffix stripped; class descriptor retained on dual class.
    assert by_symbol["AAPL"].name == "Apple Inc."
    assert by_symbol["GOOGL"].name == "Alphabet Inc. Class A"
    # This source supplies no weights -> left None (not fabricated).
    assert by_symbol["AAPL"].weight is None


def test_parse_nasdaq100_payload_bad_shape_raises() -> None:
    with pytest.raises(ValueError, match="Unexpected Nasdaq payload shape"):
        parse_nasdaq100_payload({"data": {}})
