#!/usr/bin/env python3
# Debt Reduction — companies steadily cutting debt while expanding capacity
# (rising capex / gross block), a classic deleveraging-turnaround setup.
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Debt Reduction",
    "slug": "debt_reduction",
    "category": "fundamental",
    "description": "Shrinking total debt over recent years alongside capacity "
    "expansion (rising capex / fixed assets).",
    "needs": "fundamentals",
}


def _decreasing(series):
    vals = [safe(x) for x in series]
    vals = [v for v in vals if v is not None]
    return len(vals) >= 2 and all(earlier > later for earlier, later in zip(vals, vals[1:]))
    # series is newest-first → newest < older means debt fell


def screen(s: StockData) -> Result | None:
    g = s.f
    debt = g("debt_history") or []  # newest-first list
    capex = g("capex_history") or []  # newest-first list
    gross_block = g("gross_block_history") or []
    debt_vals = [safe(x) for x in debt if safe(x) is not None]
    if len(debt_vals) < 2:
        return None
    # newest-first: debt falling means debt_vals[0] < debt_vals[1] < ...
    debt_falling = all(a < b for a, b in zip(debt_vals, debt_vals[1:]))
    debt_drop_pct = None
    if debt_vals[-1]:
        debt_drop_pct = (debt_vals[0] - debt_vals[-1]) / abs(debt_vals[-1]) * 100
    capex_vals = [safe(x) for x in capex if safe(x) is not None]
    gb_vals = [safe(x) for x in gross_block if safe(x) is not None]
    expanding = (len(capex_vals) >= 2 and capex_vals[0] > capex_vals[-1]) or (
        len(gb_vals) >= 2 and gb_vals[0] > gb_vals[-1]
    )
    passed = debt_falling and expanding
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(-(debt_drop_pct or 0), 2),  # bigger drop ranks higher
        metrics={
            "DebtFalling": int(debt_falling),
            "DebtChange%": round(debt_drop_pct, 1) if debt_drop_pct is not None else None,
            "CapacityExpanding": int(expanding),
        },
        note="deleveraging+capex" if passed else "",
    )
