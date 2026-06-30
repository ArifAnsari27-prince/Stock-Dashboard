"""Tests for XBRL fundamentals normalization (build step 6). Pure, no network."""

from __future__ import annotations

from datetime import date

import pytest

from src.compute.fundamentals import extract_fundamentals


def _flow(val, end, start, form="10-K", filed="2025-02-01"):
    return {"val": val, "end": end, "start": start, "form": form, "filed": filed}


def _instant(val, end, form="10-K", filed="2025-02-01"):
    return {"val": val, "end": end, "form": form, "filed": filed}


def _companyfacts() -> dict:
    """Synthetic FY2024 (vs FY2023) facts with a restatement + a quarterly row."""
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _flow(1000, "2023-12-31", "2023-01-01", filed="2024-02-01"),
                            _flow(1150, "2024-12-31", "2024-01-01", filed="2025-02-01"),
                            # Restated later -> should win over the 1150 above.
                            _flow(1200, "2024-12-31", "2024-01-01", filed="2025-05-01"),
                            # Q4 only -> must be ignored for an annual flow.
                            _flow(350, "2024-12-31", "2024-10-01", form="10-Q"),
                        ]
                    }
                },
                "GrossProfit": {"units": {"USD": [_flow(600, "2024-12-31", "2024-01-01")]}},
                "OperatingIncomeLoss": {"units": {"USD": [_flow(360, "2024-12-31", "2024-01-01")]}},
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            _flow(240, "2024-12-31", "2024-01-01"),
                            _flow(200, "2023-12-31", "2023-01-01", filed="2024-02-01"),
                        ]
                    }
                },
                "ResearchAndDevelopmentExpense": {"units": {"USD": [_flow(120, "2024-12-31", "2024-01-01")]}},
                "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [_flow(100, "2024-12-31", "2024-01-01")]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [_flow(300, "2024-12-31", "2024-01-01")]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_instant(500, "2024-12-31")]}},
                "StockholdersEquity": {"units": {"USD": [_instant(800, "2024-12-31")]}},
                "LongTermDebtNoncurrent": {"units": {"USD": [_instant(300, "2024-12-31")]}},
                "LongTermDebtCurrent": {"units": {"USD": [_instant(50, "2024-12-31")]}},
                "CommonStockSharesOutstanding": {"units": {"shares": [_instant(1000, "2024-12-31")]}},
            }
        }
    }


@pytest.fixture
def fundamentals():
    return extract_fundamentals(_companyfacts(), "TEST", cik="0000000001")


def test_period_and_identity(fundamentals) -> None:
    assert fundamentals.symbol == "TEST"
    assert fundamentals.cik == "0000000001"
    assert fundamentals.period_end == date(2024, 12, 31)
    assert fundamentals.fiscal_period == "FY2024"


def test_restatement_latest_filed_wins_and_quarterly_ignored(fundamentals) -> None:
    assert fundamentals.revenue == pytest.approx(1200.0)  # restated, not 1150 or 350


def test_raw_line_items(fundamentals) -> None:
    assert fundamentals.gross_profit == pytest.approx(600.0)
    assert fundamentals.operating_income == pytest.approx(360.0)
    assert fundamentals.net_income == pytest.approx(240.0)
    assert fundamentals.cash_and_equivalents == pytest.approx(500.0)
    assert fundamentals.shares_outstanding == pytest.approx(1000.0)
    assert fundamentals.total_debt == pytest.approx(350.0)  # 300 + 50
    assert fundamentals.net_debt == pytest.approx(-150.0)  # 350 - 500
    assert fundamentals.free_cash_flow == pytest.approx(200.0)  # 300 - 100


def test_ratios_and_growth(fundamentals) -> None:
    assert fundamentals.revenue_growth == pytest.approx(0.20)  # 1000 -> 1200
    assert fundamentals.gross_margin == pytest.approx(0.50)
    assert fundamentals.operating_margin == pytest.approx(0.30)
    assert fundamentals.net_margin == pytest.approx(0.20)
    assert fundamentals.fcf_margin == pytest.approx(200 / 1200)
    assert fundamentals.fcf_conversion == pytest.approx(200 / 240)
    assert fundamentals.roe == pytest.approx(0.30)  # 240 / 800
    assert fundamentals.roic == pytest.approx(360 / 1150)  # op income / (debt + equity)
    assert fundamentals.capex_to_revenue == pytest.approx(100 / 1200)
    assert fundamentals.rnd_to_revenue == pytest.approx(0.10)


def test_valuation_left_none(fundamentals) -> None:
    # Price-based valuation is not computed in this layer.
    assert fundamentals.pe_ratio is None
    assert fundamentals.fcf_yield is None


def test_concept_priority_prefers_contract_revenue() -> None:
    cf = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [_flow(999, "2024-12-31", "2024-01-01")]}
                },
                "Revenues": {"units": {"USD": [_flow(111, "2024-12-31", "2024-01-01")]}},
            }
        }
    }
    assert extract_fundamentals(cf, "X").revenue == pytest.approx(999.0)


def test_gross_profit_derived_from_cost_of_revenue() -> None:
    # No GrossProfit tag, but Revenue and CostOfRevenue present (the Alphabet case).
    cf = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [_flow(1000, "2024-12-31", "2024-01-01")]}},
                "CostOfRevenue": {"units": {"USD": [_flow(400, "2024-12-31", "2024-01-01")]}},
            }
        }
    }
    f = extract_fundamentals(cf, "X")
    assert f.gross_profit == pytest.approx(600.0)  # 1000 - 400
    assert f.gross_margin == pytest.approx(0.60)


def test_no_annual_data_returns_empty_labeled_row() -> None:
    f = extract_fundamentals({"facts": {"us-gaap": {}}}, "EMPTY", cik="0000000009")
    assert f.symbol == "EMPTY" and f.cik == "0000000009"
    assert f.revenue is None and f.period_end is None
