"""Typed domain models for the Nasdaq 100 dashboard backend.

These are pydantic v2 models used as the row-level schema for everything that
flows through the system: universe constituents, price bars, fundamentals, and
filing links. Every stored dataset is wrapped in a `Snapshot`, which carries
provenance (source + fetch timestamp + a self-describing disclaimer) so the data
is always traceable back to "prototype / delayed / unofficial source"
(CLAUDE.md §2).

No I/O here. Units are documented on each field.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class DataSource(str, Enum):
    """Origin of a dataset, recorded in provenance."""

    YFINANCE = "yfinance"  # unofficial, delayed 15-20 min, personal use only
    MASSIVE = "massive"  # Massive.com REST API (formerly Polygon.io)
    SEC_EDGAR = "sec_edgar"  # official filings + XBRL company facts
    NASDAQ_INDEX = "nasdaq_index"  # official Nasdaq-100 constituent list (QQQ tracks this)
    QQQ_HOLDINGS = "qqq_holdings"  # Invesco QQQ holdings CSV (universe proxy)
    COMPUTED = "computed"  # derived metrics (technicals/returns/ratios)


class FilingType(str, Enum):
    """SEC filing form types tracked in V1 (CLAUDE.md §1)."""

    FORM_10K = "10-K"
    FORM_10Q = "10-Q"
    FORM_8K = "8-K"
    FORM_4 = "4"


class Ticker(BaseModel):
    """A single universe constituent.

    Sourced from QQQ holdings as a Nasdaq 100 proxy. `cik` is needed downstream
    for SEC EDGAR lookups (zero-padded 10-digit string when present).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Exchange ticker symbol, e.g. 'AAPL'.")
    name: str | None = Field(default=None, description="Company display name.")
    cik: str | None = Field(
        default=None,
        description="SEC Central Index Key, zero-padded to 10 digits, e.g. '0000320193'.",
    )
    weight: float | None = Field(
        default=None,
        description="Index/ETF weight as a fraction in [0, 1], if known.",
    )


class PriceBar(BaseModel):
    """One OHLCV bar for a symbol on a given date.

    Prices are in the listing currency (USD for the Nasdaq 100). `adj_close` is
    split/dividend-adjusted; `close` is the raw close. Volume is shares traded.
    """

    symbol: str
    date: dt.date = Field(description="Trading date of the bar.")
    open: float
    high: float
    low: float
    close: float = Field(description="Raw closing price.")
    adj_close: float = Field(description="Split/dividend-adjusted close.")
    volume: int = Field(description="Shares traded during the bar.")


class Fundamentals(BaseModel):
    """Canonical fundamental metrics for a company at a point in time.

    Normalized from SEC EDGAR XBRL company facts (build step 6). All monetary
    values are in USD; ratios/margins are fractions (e.g. 0.42 == 42%) unless
    noted. Every field is optional: when a free source cannot reliably supply a
    metric we store null rather than fabricating it (CLAUDE.md §6).
    """

    symbol: str
    cik: str | None = Field(default=None, description="SEC Central Index Key.")
    period_end: dt.date | None = Field(
        default=None, description="Fiscal period end date the figures cover."
    )
    fiscal_period: str | None = Field(
        default=None, description="e.g. 'FY2024', 'Q3-2024'."
    )

    # --- Raw line items (USD) -------------------------------------------
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    net_debt: float | None = Field(
        default=None, description="Total debt minus cash and equivalents (USD)."
    )
    shares_outstanding: float | None = None
    capex: float | None = Field(default=None, description="Capital expenditure (USD).")
    research_and_development: float | None = None

    # --- Growth / margins / returns (fractions unless noted) ------------
    revenue_growth: float | None = Field(
        default=None, description="YoY revenue growth as a fraction."
    )
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    fcf_margin: float | None = None
    roe: float | None = Field(default=None, description="Return on equity, fraction.")
    roic: float | None = Field(
        default=None, description="Return on invested capital, fraction."
    )
    fcf_conversion: float | None = Field(
        default=None, description="Free cash flow / net income, fraction."
    )
    capex_to_revenue: float | None = None
    rnd_to_revenue: float | None = None

    # --- Valuation (where derivable) ------------------------------------
    pe_ratio: float | None = Field(default=None, description="Price / earnings (x).")
    ps_ratio: float | None = Field(default=None, description="Price / sales (x).")
    pb_ratio: float | None = Field(default=None, description="Price / book (x).")
    ev_to_sales: float | None = Field(
        default=None, description="Enterprise value / sales (x)."
    )
    fcf_yield: float | None = Field(
        default=None, description="Free cash flow / market cap, fraction."
    )


class Filing(BaseModel):
    """A link to one SEC filing for a company (CLAUDE.md §1).

    Sourced from the EDGAR submissions API. `url` points at the primary
    document or filing index.
    """

    symbol: str
    cik: str | None = None
    form: FilingType
    filed_date: dt.date = Field(description="Date the filing was accepted by SEC.")
    accession_number: str | None = Field(
        default=None, description="EDGAR accession number, e.g. '0000320193-24-000123'."
    )
    url: str = Field(description="Link to the filing document or index page.")


class Provenance(BaseModel):
    """Source + fetch metadata stamped onto every stored snapshot (CLAUDE.md §2)."""

    model_config = ConfigDict(frozen=True)

    source: DataSource
    fetched_at: dt.datetime = Field(
        description="UTC timestamp when the underlying data was fetched/computed."
    )
    disclaimer: str = Field(
        default="prototype / delayed / unofficial source",
        description="Self-describing label so consumers never mistake this for "
        "production-grade, real-time, or licensed data.",
    )
    notes: str | None = Field(
        default=None, description="Optional free-text context for this snapshot."
    )


class MetricsRow(BaseModel):
    """One dashboard row: a universe constituent joined with its latest computed
    technical + return metrics (build step 5).

    Identity/context fields are validated explicitly. The wide set of computed
    indicators (sma_*, rsi_14, macd*, volatility_*, beta_*, ...) is attached
    dynamically from the compute aggregators via `extra="allow"`, so this model
    need not be kept in lockstep with every indicator name. All computed metric
    values are floats or None; their units are documented on the producing
    functions in compute/technicals.py and compute/returns.py.
    """

    model_config = ConfigDict(extra="allow")

    symbol: str
    name: str | None = None
    weight: float | None = Field(
        default=None, description="Index/ETF weight as a fraction, if known."
    )
    as_of: dt.date | None = Field(
        default=None, description="Date of the latest price bar the metrics use."
    )
    latest_close: float | None = Field(
        default=None, description="Latest raw closing price (USD)."
    )


# Row type for the generic Snapshot wrapper.
RowT = TypeVar("RowT", bound=BaseModel)


class Snapshot(BaseModel, Generic[RowT]):
    """A provenance-stamped batch of rows of a single model type.

    This is the unit the storage layer persists: `provenance` describes where
    and when the data came from; `rows` is the tabular payload. The Parquet
    store flattens provenance into columns so the resulting file is
    self-describing on its own.
    """

    provenance: Provenance
    rows: list[RowT]
