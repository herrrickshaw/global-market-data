#!/usr/bin/env python3
# Magic Formula — Joel Greenblatt: rank by Earnings Yield (EBIT/EV) + Return on
# Capital (EBIT/(Net WC + Net Fixed Assets)). High of both = cheap & good.
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Magic Formula",
    "slug": "magic_formula",
    "category": "fundamental",
    "description": "Greenblatt: high earnings yield (EBIT/EV) AND high return on "
    "capital. Ranked composite of the two.",
    "needs": "fundamentals",
}
EY_MIN = 8.0  # % earnings yield floor for a standalone pass
ROC_MIN = 15.0  # % return on capital floor


def screen(s: StockData) -> Result | None:
    g = s.f
    ebit = safe(g("ebit"))
    ev = safe(g("enterprise_value"))
    roc = safe(g("roc")) or safe(g("roce"))
    ey = safe(g("earnings_yield"))
    if ey is None and ebit is not None and ev and ev != 0:
        ey = ebit / ev * 100
    if ey is None or roc is None:
        return None
    passed = ey >= EY_MIN and roc >= ROC_MIN
    # composite score: higher EY + higher ROC rank better (sum, higher=better)
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(ey + roc, 2),
        metrics={"EarningsYield%": round(ey, 2), "ROC%": round(roc, 2)},
        note="cheap+quality" if passed else "",
    )
