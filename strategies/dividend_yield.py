#!/usr/bin/env python3
# Highest Dividend Yield — consistent dividend payers ranked by current yield.
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Highest Dividend Yield",
    "slug": "dividend_yield",
    "category": "income",
    "description": "Consistently dividend-paying stocks ranked by highest current "
    "dividend yield.",
    "needs": "fundamentals",
}
YIELD_MIN = 2.0  # % minimum to qualify
CONSISTENCY_YEARS = 3  # paid a dividend in each of the last N years


def screen(s: StockData) -> Result | None:
    g = s.f
    dy = safe(g("dividend_yield"))
    pay_years = g("dividend_pay_years")  # count of recent yrs with a payout
    div_hist = g("dividend_history") or []  # list of recent annual dividends
    if dy is None:
        return None
    if pay_years is None:
        paid = [safe(x) for x in div_hist if safe(x) is not None]
        pay_years = sum(1 for v in paid if v and v > 0)
    consistent = (pay_years or 0) >= CONSISTENCY_YEARS
    passed = dy >= YIELD_MIN and consistent
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(dy, 2),
        metrics={
            "DividendYield%": round(dy, 2),
            "PayYears": pay_years,
            "Consistent": int(consistent),
        },
        note="income" if passed else "",
    )
