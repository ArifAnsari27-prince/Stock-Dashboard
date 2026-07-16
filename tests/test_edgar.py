"""Tests for EDGAR client, parsers, and sources (build step 6). No network."""

from __future__ import annotations

from datetime import date

import pytest

from src.data_sources.edgar import (
    EdgarClient,
    EdgarFilingsSource,
    EdgarFundamentalsSource,
    enrich_tickers_with_cik,
    pad_cik,
    parse_company_tickers,
    parse_latest_filings,
)
from src.models import DataSource, FilingType, Ticker

# --- pure parsers ------------------------------------------------------------


def test_pad_cik() -> None:
    assert pad_cik(320193) == "0000320193"
    assert pad_cik("1045810") == "0001045810"


def test_parse_company_tickers() -> None:
    payload = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        "1": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
    }
    mapping = parse_company_tickers(payload)
    assert mapping["NVDA"] == "0001045810"
    assert mapping["AAPL"] == "0000320193"  # upper-cased


def test_enrich_tickers_with_cik() -> None:
    tickers = [Ticker(symbol="AAPL"), Ticker(symbol="ZZZZ")]
    out = enrich_tickers_with_cik(tickers, {"AAPL": "0000320193"})
    assert out[0].cik == "0000320193"
    assert out[1].cik is None  # not in map -> unchanged


def _submissions() -> dict:
    return {
        "cik": 320193,
        "filings": {
            "recent": {
                "form": ["4", "8-K", "10-Q", "4", "10-K", "3"],
                "accessionNumber": [
                    "0001-26-1", "0002-26-2", "0003-26-3",
                    "0004-26-4", "0005-25-5", "0006-25-6",
                ],
                "filingDate": [
                    "2026-06-01", "2026-05-01", "2026-04-01",
                    "2026-03-01", "2025-11-01", "2025-01-01",
                ],
                "primaryDocument": ["a.xml", "b.htm", "c.htm", "d.xml", "e.htm", "f.htm"],
            }
        },
    }


def test_parse_latest_filings_picks_latest_per_form() -> None:
    filings = parse_latest_filings(_submissions(), "AAPL")
    by_form = {f.form: f for f in filings}
    # Form 3 is not tracked; the other four are, newest occurrence each.
    assert set(by_form) == {FilingType.FORM_4, FilingType.FORM_8K, FilingType.FORM_10Q, FilingType.FORM_10K}
    assert by_form[FilingType.FORM_4].filed_date == date(2026, 6, 1)  # idx 0, not idx 3
    tenk = by_form[FilingType.FORM_10K]
    assert tenk.filed_date == date(2025, 11, 1)
    assert tenk.cik == "0000320193"
    # URL uses int CIK + de-dashed accession + primary doc.
    assert tenk.url == "https://www.sec.gov/Archives/edgar/data/320193/0005255/e.htm"


# --- EdgarClient: throttle / retry -------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, script):
        # script: list of payloads or Exceptions, consumed per call.
        self._script = script
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        result = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)


def test_client_throttles_between_calls() -> None:
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 0.0, 0.05, 0.05])  # clock advances slowly
    session = _FakeSession([{"ok": 1}, {"ok": 2}])
    client = EdgarClient(
        user_agent="Test test@example.com",
        min_interval_seconds=0.11,
        session=session,
        sleep=sleeps.append,
        clock=lambda: next(ticks),
    )
    client.get_json("https://x/1")
    client.get_json("https://x/2")
    # Second call had to wait because <0.11s elapsed.
    assert any(s > 0 for s in sleeps)


def test_client_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    session = _FakeSession([RuntimeError("boom"), {"ok": True}])
    client = EdgarClient(
        user_agent="Test test@example.com",
        session=session,
        sleep=sleeps.append,
        clock=lambda: 100.0,
    )
    assert client.get_json("https://x")["ok"] is True
    assert session.calls == 2


def test_client_raises_after_max_retries() -> None:
    session = _FakeSession([RuntimeError("down")])
    client = EdgarClient(
        user_agent="Test test@example.com",
        max_retries=2,
        session=session,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    with pytest.raises(RuntimeError, match="failed after retries"):
        client.get_json("https://x")
    assert session.calls == 2


# --- sources with injected get_json ------------------------------------------


def _companyfacts() -> dict:
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    {"val": 1200, "end": "2024-12-31", "start": "2024-01-01", "form": "10-K", "filed": "2025-02-01"},
                ]}},
                "NetIncomeLoss": {"units": {"USD": [
                    {"val": 240, "end": "2024-12-31", "start": "2024-01-01", "form": "10-K", "filed": "2025-02-01"},
                ]}},
            }
        }
    }


def test_fundamentals_source_partial_failure_and_skip_no_cik() -> None:
    def get_json(url):
        if "0000000002" in url:
            raise RuntimeError("404")
        return _companyfacts()

    src = EdgarFundamentalsSource(get_json)
    tickers = [
        Ticker(symbol="AAA", cik="0000000001"),
        Ticker(symbol="BBB", cik="0000000002"),  # will fail
        Ticker(symbol="CCC"),  # no cik -> skipped
    ]
    snap = src.fetch_fundamentals(tickers)
    assert snap.provenance.source == DataSource.SEC_EDGAR
    assert [r.symbol for r in snap.rows] == ["AAA"]
    assert snap.rows[0].revenue == pytest.approx(1200.0)


def test_filings_source_returns_rows() -> None:
    src = EdgarFilingsSource(lambda url: _submissions())
    snap = src.fetch_filings([Ticker(symbol="AAPL", cik="0000320193")])
    assert snap.provenance.source == DataSource.SEC_EDGAR
    forms = {f.form for f in snap.rows}
    assert FilingType.FORM_10K in forms and FilingType.FORM_4 in forms
