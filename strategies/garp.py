#!/usr/bin/env python3
# Growth at a Reasonable Price (GARP) — high earnings growth without an inflated
# P/E. Classic Lynch test: PEG <= 1, or P/E at/below industry while growth is high.
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Growth at a Reasonable Price",
    "slug": "garp",
    "category": "fundamental",
    "description": "High earnings growth with a non-inflated P/E (PEG<=1, or P/E "
    "below industry while growth stays high).",
    "needs": "fundamentals",
}
GROWTH_MIN = 15.0  # % earnings growth
PEG_MAX = 1.0
PE_MAX = 40.0


def screen(s: StockData) -> Result | None:
    g = s.f
    growth = safe(g("eps_growth")) or safe(g("earnings_growth"))
    pe = safe(g("pe"))
    peg = safe(g("peg"))
    industry_pe = safe(g("industry_pe"))  # Damodaran reference
    if growth is None or pe is None or pe <= 0:
        return None
    if peg is None and growth not in (None, 0):
        peg = pe / growth
    reasonable = (
        (peg is not None and peg <= PEG_MAX)
        or (industry_pe is not None and pe <= industry_pe)
        or (pe <= PE_MAX)
    )
    passed = growth >= GROWTH_MIN and reasonable and pe <= PE_MAX
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(peg, 2) if peg is not None else None,  # lower PEG = better
        metrics={
            "EPS_Growth%": round(growth, 1),
            "PE": round(pe, 1),
            "PEG": round(peg, 2) if peg is not None else None,
            "IndustryPE": industry_pe,
        },
        note="GARP" if passed else "",
    )
