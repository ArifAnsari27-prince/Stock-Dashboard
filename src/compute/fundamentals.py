"""XBRL company-facts normalization into canonical fundamentals (CLAUDE.md §3, build step 6).

Pure and network-free: takes a parsed SEC `companyfacts` JSON dict in and returns
a `Fundamentals` model out. Fetching lives in `data_sources/edgar.py`.

The hard part (CLAUDE.md calls this the hardest module) is picking the *right*
value for each concept:

  - SEC facts are grouped as facts["us-gaap"][Concept]["units"][unit] -> a list
    of facts, each {start?, end, val, form, fy, fp, filed, frame, ...}.
  - Income-statement / cash-flow concepts are FLOW values: durational facts with
    a `start`; the annual one spans ~365 days and is reported on form 10-K.
  - Balance-sheet concepts are INSTANT values: anchored on the period-end date.
  - The same period can appear multiple times (a later 10-K restates the prior
    year); we keep the most recently *filed* value.
  - A canonical metric may map to several possible tags across companies, so each
    is resolved against a priority list of concept names.

We anchor everything to the latest fiscal-year-end derived from annual revenue/
net-income facts, and read every concept at that date (and the prior year for
growth). Metrics that cannot be resolved are left None — never fabricated
(CLAUDE.md §6). Price-based valuation (P/E, P/S, ...) is intentionally NOT
computed here: it needs market price and is joined in a later layer.

All monetary values are USD; margins/returns are fractions (0.42 == 42%).
"""

from __future__ import annotations

from datetime import date

from src.models import Fundamentals

# Concept priority lists. First present (with the needed unit) wins.
_REVENUE = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
_GROSS_PROFIT = ["GrossProfit"]
_COST_OF_REVENUE = [
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
]
_OPERATING_INCOME = ["OperatingIncomeLoss"]
_NET_INCOME = ["NetIncomeLoss"]
_RND = ["ResearchAndDevelopmentExpense"]
_CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment"]
_OPERATING_CASH_FLOW = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]

_CASH = ["CashAndCashEquivalentsAtCarryingValue"]
_EQUITY = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
]
_SHARES = ["CommonStockSharesOutstanding"]
_TOTAL_DEBT_COMBINED = ["DebtLongtermAndShorttermCombinedAmount"]
_LT_DEBT_NONCURRENT = ["LongTermDebtNoncurrent"]
_LT_DEBT_CURRENT = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]
_SHORT_TERM_DEBT = ["ShortTermBorrowings", "DebtCurrent"]

# Acceptable span (days) for a fact to count as a full fiscal year.
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 350, 380


def _to_date(value: str) -> date:
    return date.fromisoformat(value)


def _is_annual_form(form: str) -> bool:
    return form.startswith("10-K")


def _concept_unit_facts(gaap: dict, name: str, unit: str) -> list[dict] | None:
    """Return the fact list for one concept name with `unit`, or None."""
    concept = gaap.get(name)
    if concept and unit in concept.get("units", {}):
        return concept["units"][unit]
    return None


def _select_value(
    facts: list[dict], period_end: str, kind: str
) -> float | None:
    """Pick a concept's value at `period_end`.

    kind='flow' requires a ~1-year duration ending at `period_end`; kind='instant'
    just matches the end date. On multiple matches, the most recently filed wins
    (handles restatements). Returns None if nothing matches.
    """
    candidates: list[dict] = []
    for fact in facts:
        if not _is_annual_form(fact.get("form", "")):
            continue
        if fact.get("end") != period_end:
            continue
        if kind == "flow":
            start = fact.get("start")
            if not start:
                continue
            duration = (_to_date(period_end) - _to_date(start)).days
            if not (_ANNUAL_MIN_DAYS <= duration <= _ANNUAL_MAX_DAYS):
                continue
        candidates.append(fact)
    if not candidates:
        return None
    candidates.sort(key=lambda f: f.get("filed", ""))
    return float(candidates[-1]["val"])


def _annual_ends(gaap: dict, name_lists: list[list[str]]) -> list[str]:
    """Sorted fiscal-year-end dates with full-year facts, unioned across concepts.

    Scans every concept name in every provided priority list. Unioning matters
    because a company can switch the tag it reports a metric under between years
    (XBRL tag drift), so no single tag necessarily covers all years.
    """
    ends: set[str] = set()
    for names in name_lists:
        for name in names:
            for fact in _concept_unit_facts(gaap, name, "USD") or []:
                if not _is_annual_form(fact.get("form", "")) or not fact.get("start"):
                    continue
                duration = (_to_date(fact["end"]) - _to_date(fact["start"])).days
                if _ANNUAL_MIN_DAYS <= duration <= _ANNUAL_MAX_DAYS:
                    ends.add(fact["end"])
    return sorted(ends)


def _flow(gaap: dict, names: list[str], period_end: str) -> float | None:
    """First concept in `names` that has a full-year value at `period_end`."""
    for name in names:
        facts = _concept_unit_facts(gaap, name, "USD")
        if not facts:
            continue
        value = _select_value(facts, period_end, "flow")
        if value is not None:
            return value
    return None


def _instant(
    gaap: dict, names: list[str], period_end: str, unit: str = "USD"
) -> float | None:
    """First concept in `names` that has an instant value at `period_end`."""
    for name in names:
        facts = _concept_unit_facts(gaap, name, unit)
        if not facts:
            continue
        value = _select_value(facts, period_end, "instant")
        if value is not None:
            return value
    return None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _sum_present(*values: float | None) -> float | None:
    """Sum the non-None values; None if all are None (don't invent a zero)."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _total_debt(gaap: dict, period_end: str) -> float | None:
    """Total debt: a combined tag if present, else sum of debt components."""
    combined = _instant(gaap, _TOTAL_DEBT_COMBINED, period_end)
    if combined is not None:
        return combined
    return _sum_present(
        _instant(gaap, _LT_DEBT_NONCURRENT, period_end),
        _instant(gaap, _LT_DEBT_CURRENT, period_end),
        _instant(gaap, _SHORT_TERM_DEBT, period_end),
    )


def extract_fundamentals(
    companyfacts: dict, symbol: str, cik: str | None = None
) -> Fundamentals:
    """Normalize a parsed SEC companyfacts payload into a `Fundamentals` row.

    Reads the latest fiscal year (and prior year for revenue growth). Any metric
    that cannot be resolved is left None.
    """
    facts_root = companyfacts.get("facts", {})
    gaap = facts_root.get("us-gaap", {})

    ends = _annual_ends(gaap, [_REVENUE, _NET_INCOME])
    if not ends:
        # No usable annual data; return an essentially empty, labeled row.
        return Fundamentals(symbol=symbol, cik=cik)

    fy_end = ends[-1]
    # Prior fiscal-year end ~1 year earlier, for growth.
    prior_end = None
    for end in reversed(ends[:-1]):
        gap = (_to_date(fy_end) - _to_date(end)).days
        if 330 <= gap <= 400:
            prior_end = end
            break

    # --- Raw line items (USD) -------------------------------------------
    revenue = _flow(gaap, _REVENUE, fy_end)
    gross_profit = _flow(gaap, _GROSS_PROFIT, fy_end)
    if gross_profit is None:
        # Many firms (e.g. Alphabet) file cost of revenue but not gross profit.
        cost_of_revenue = _flow(gaap, _COST_OF_REVENUE, fy_end)
        if revenue is not None and cost_of_revenue is not None:
            gross_profit = revenue - cost_of_revenue
    operating_income = _flow(gaap, _OPERATING_INCOME, fy_end)
    net_income = _flow(gaap, _NET_INCOME, fy_end)
    rnd = _flow(gaap, _RND, fy_end)
    capex = _flow(gaap, _CAPEX, fy_end)
    operating_cash_flow = _flow(gaap, _OPERATING_CASH_FLOW, fy_end)

    cash = _instant(gaap, _CASH, fy_end)
    equity = _instant(gaap, _EQUITY, fy_end)
    shares = _instant(gaap, _SHARES, fy_end, unit="shares")
    total_debt = _total_debt(gaap, fy_end)

    free_cash_flow = (
        operating_cash_flow - capex
        if operating_cash_flow is not None and capex is not None
        else None
    )
    net_debt = (
        total_debt - cash
        if total_debt is not None and cash is not None
        else None
    )

    prior_revenue = _flow(gaap, _REVENUE, prior_end) if prior_end else None
    revenue_growth = (
        (revenue - prior_revenue) / prior_revenue
        if revenue is not None and prior_revenue not in (None, 0)
        else None
    )

    # Simplified pre-tax ROIC = operating income / (total debt + equity).
    invested_capital = _sum_present(total_debt, equity)
    roic = _safe_div(operating_income, invested_capital)

    return Fundamentals(
        symbol=symbol,
        cik=cik,
        period_end=_to_date(fy_end),
        fiscal_period=f"FY{_to_date(fy_end).year}",
        revenue=revenue,
        gross_profit=gross_profit,
        operating_income=operating_income,
        net_income=net_income,
        free_cash_flow=free_cash_flow,
        cash_and_equivalents=cash,
        total_debt=total_debt,
        net_debt=net_debt,
        shares_outstanding=shares,
        capex=capex,
        research_and_development=rnd,
        revenue_growth=revenue_growth,
        gross_margin=_safe_div(gross_profit, revenue),
        operating_margin=_safe_div(operating_income, revenue),
        net_margin=_safe_div(net_income, revenue),
        fcf_margin=_safe_div(free_cash_flow, revenue),
        roe=_safe_div(net_income, equity),
        roic=roic,
        fcf_conversion=_safe_div(free_cash_flow, net_income),
        capex_to_revenue=_safe_div(capex, revenue),
        rnd_to_revenue=_safe_div(rnd, revenue),
    )
