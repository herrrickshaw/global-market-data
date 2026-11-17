"""Cash Conversion Cycle strategy: CCC = DIO + DSO - DPO (days)."""

import pytest

from strategies import cash_conversion_cycle as ccc
from strategies.base import StockData


def _run(**f):
    return ccc.screen(StockData("T", fundamentals=f))


def test_hand_computed_ccc():
    # DIO = 73/365*365 = 73, DSO = 50/730*365 = 25, DPO = 36.5/365*365 = 36.5
    r = _run(inventory=73, receivables=50, payables=36.5, cogs=365, revenue=730)
    assert r.metrics["DIO"] == 73.0 and r.metrics["DSO"] == 25.0 and r.metrics["DPO"] == 36.5
    assert r.metrics["CCC_days"] == pytest.approx(61.5)
    assert not r.passed  # 61.5 > CCC_MAX (45)


def test_boundary_at_ccc_max_passes():
    assert _run(ccc=ccc.CCC_MAX).passed
    assert not _run(ccc=ccc.CCC_MAX + 0.1).passed


def test_service_company_without_inventory_uses_zero_dio():
    # no inventory line is normal for a services firm: DIO = 0
    r = _run(receivables=20, payables=10, cogs=365, revenue=365)
    assert r.metrics["CCC_days"] == pytest.approx(10.0) and r.passed


def test_no_inputs_returns_none():
    assert _run() is None
    assert _run(cogs=100, revenue=100) is None  # denominators only


def test_zero_denominator_is_not_a_division_error():
    assert _run(inventory=10, cogs=0) is None


# BUG: missing receivables were coerced to DSO = 0, so a name reporting only
# payables got CCC = -DPO and passed the "efficient working capital" screen.
def test_payables_only_does_not_pass_as_efficient():
    r = _run(payables=200, cogs=365)  # DPO = 200 days, nothing else known
    assert r is None or not r.passed


def test_missing_receivables_with_inventory_does_not_score():
    # DSO unknown and DPO large: coercing DSO to 0 would understate CCC (false pass)
    assert _run(inventory=10, payables=200, cogs=365) is None


def test_missing_payables_is_a_conservative_upper_bound():
    # DPO unknown -> treated as 0, which can only OVERstate CCC (never a false pass)
    r = _run(inventory=10, receivables=10, cogs=365, revenue=365)
    assert r.metrics["CCC_days"] == pytest.approx(20.0)
