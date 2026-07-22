#!/usr/bin/env python3
# Coffee Can Portfolio — Saurabh Mukherjea: long-term consistency, high ROE,
# steady revenue growth, low leverage. "Buy and forget" quality compounders.
from __future__ import annotations

from .base import Result, StockData, cagr, safe

META = {
    "name": "Coffee Can Portfolio",
    "slug": "coffee_can",
    "category": "fundamental",
    "description": "Consistent high-ROE compounders with steady revenue growth and "
    "low debt (Saurabh Mukherjea philosophy).",
    "needs": "fundamentals",
}
ROE_MIN = 15.0  # %
REV_GROWTH_MIN = 10.0  # % CAGR
DE_MAX = 1.0


def screen(s: StockData) -> Result | None:
    g = s.f
    roe = safe(g("roe"))
    roe_hist = g("roe_history") or []  # list of yearly ROE, newest-first
    rev, rev_old = safe(g("revenue")), safe(g("revenue_5y_ago"))
    de = safe(g("debt_to_equity"))
    if roe is None and not roe_hist:
        return None
    roe_vals = [safe(x) for x in roe_hist if safe(x) is not None]
    consistent = bool(roe_vals) and all(v >= ROE_MIN for v in roe_vals)
    rev_cagr = cagr(rev, rev_old, 5) if rev and rev_old else None
    checks = {
        "ROE>=15": int((roe or 0) >= ROE_MIN),
        "ROE_consistent": int(consistent),
        "RevCAGR>=10": int(rev_cagr is not None and rev_cagr >= REV_GROWTH_MIN),
        "DE<=1": int(de is not None and de <= DE_MAX),
    }
    passed = checks["ROE>=15"] and checks["RevCAGR>=10"] and checks["DE<=1"]
    return Result(
        s.symbol,
        META["slug"],
        passed=bool(passed),
        score=round(roe or 0, 2),
        metrics={
            "ROE": roe,
            "RevCAGR5y": round(rev_cagr, 1) if rev_cagr else None,
            "DE": de,
            **checks,
        },
        note="quality compounder" if passed else "",
    )
