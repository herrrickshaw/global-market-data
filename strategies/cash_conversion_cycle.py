#!/usr/bin/env python3
# Cash Conversion Cycle (CCC) — working-capital efficiency.
#   CCC = DIO + DSO - DPO
#     DIO = Inventory / COGS * 365      (days inventory outstanding)
#     DSO = Receivables / Revenue * 365 (days sales outstanding)
#     DPO = Payables / COGS * 365       (days payables outstanding)
# A LOW or NEGATIVE CCC means the company collects from customers before it has
# to pay suppliers — it funds growth with other people's money. Mirrors the
# screener.in "Cash Conversion Cycle" screen (screens/228040).
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Cash Conversion Cycle",
    "slug": "cash_conversion_cycle",
    "category": "fundamental",
    "description": "Working-capital efficiency: CCC = DIO + DSO - DPO. Low / "
    "negative CCC = collects before it pays (screener.in 228040).",
    "needs": "fundamentals",
}
CCC_MAX = 45.0  # days; pass if CCC <= this (tweak per universe)


def _days(num, denom):
    return (num / denom * 365.0) if (num is not None and denom not in (None, 0)) else None


def screen(s: StockData) -> Result | None:
    g = s.f
    inv, rec, pay = safe(g("inventory")), safe(g("receivables")), safe(g("payables"))
    cogs, rev = safe(g("cogs")), safe(g("revenue"))
    ccc = safe(g("ccc"))  # accept a precomputed value if supplied
    dio = dso = dpo = None
    if ccc is None:
        dio = _days(inv, cogs)
        dso = _days(rec, rev)
        dpo = _days(pay, cogs)
        if dio is None and dso is None and dpo is None:
            return None
        ccc = (dio or 0) + (dso or 0) - (dpo or 0)
    passed = ccc <= CCC_MAX
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(ccc, 1),
        metrics={
            "CCC_days": round(ccc, 1),
            "DIO": round(dio, 1) if dio is not None else None,
            "DSO": round(dso, 1) if dso is not None else None,
            "DPO": round(dpo, 1) if dpo is not None else None,
        },
        note="efficient WC" if passed else "",
    )
