"""Tests for the Finnhub news adapter and the news refresh job (network-free)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from src.data_sources.news import FinnhubNewsSource, parse_news_items
from src.jobs.refresh_news import NEWS_DATASET, run_news_refresh
from src.models import DataSource, Provenance, Snapshot, Ticker
from src.storage.parquet_store import ParquetStore

_TS = int(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).timestamp())


def _payload(n: int = 2, offset: int = 0) -> list[dict]:
    return [
        {
            "headline": f"Headline {i}",
            "source": "TestWire",
            "url": f"https://example.com/{i}",
            "summary": f"Summary {i}",
            "datetime": _TS + offset + i,
        }
        for i in range(n)
    ]


def test_parse_news_items_skips_bad_rows():
    payload = _payload(2) + [{"headline": "", "datetime": _TS}, {"headline": "no ts"}]
    items = parse_news_items(payload, symbol="NVDA")
    assert len(items) == 2
    assert all(i.symbol == "NVDA" for i in items)
    assert items[0].published_at.tzinfo is not None


class _FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, responses):
        self._responses = responses  # url-substring -> response or exception
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        for key, resp in self._responses.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected url {url}")


def test_fetch_news_snapshot_market_plus_company_with_partial_failure():
    session = _FakeSession(
        {
            "company-news": _FakeResponse(_payload(3, offset=100)),
            "/news": _FakeResponse(_payload(2)),
        }
    )
    source = FinnhubNewsSource("key", session=session, sleep=lambda s: None)

    # One symbol fails; the batch must still return the rest.
    original_get = source._get

    def flaky_get(path, params):
        if params.get("symbol") == "BAD":
            raise requests.ConnectionError("boom")
        return original_get(path, params)

    source._get = flaky_get
    snapshot = source.fetch_news_snapshot(["NVDA", "BAD"], per_symbol_limit=2)
    assert snapshot.provenance.source == DataSource.FINNHUB
    market = [r for r in snapshot.rows if r.symbol is None]
    company = [r for r in snapshot.rows if r.symbol == "NVDA"]
    assert len(market) == 2 and len(company) == 2


def test_news_job_persists_snapshot(tmp_path):
    storage = ParquetStore(tmp_path)
    universe = Snapshot[Ticker](
        provenance=Provenance(
            source=DataSource.NASDAQ_INDEX, fetched_at=datetime.now(timezone.utc)
        ),
        rows=[
            Ticker(symbol="NVDA", market_cap=4e12, memberships=("nasdaq100",)),
            Ticker(symbol="TINY", market_cap=1e9, memberships=("russell3000",)),
        ],
    )
    storage.write_snapshot("universe", universe)

    session = _FakeSession(
        {
            "company-news": _FakeResponse(_payload(1, offset=100)),
            "/news": _FakeResponse(_payload(1)),
        }
    )
    source = FinnhubNewsSource("key", session=session, sleep=lambda s: None)

    from src.config import Config

    config = Config(data_dir=tmp_path, sec_user_agent=None, news_top_symbols=1)
    summary = run_news_refresh(news_source=source, storage=storage, config=config)
    assert summary["company_symbols"] == 1  # top-1 by market cap only

    df = storage.read_latest(NEWS_DATASET)
    assert len(df) == 2  # 1 market + 1 company headline
    assert set(df["headline"]) == {"Headline 0"}


def test_rate_limit_pacing_sleeps_between_calls():
    session = _FakeSession({"/news": _FakeResponse(_payload(1))})
    sleeps: list[float] = []
    source = FinnhubNewsSource(
        "key", session=session, sleep=sleeps.append, min_interval_seconds=5.0
    )
    source.fetch_market_news()
    source.fetch_market_news()
    assert sleeps and sleeps[0] == pytest.approx(5.0, abs=0.5)
