#!/usr/bin/env python3
# Bluest of the Blue Chips — large caps (> ₹3000 cr) with strong profit growth,
# high ROE, and attractive valuation (PE at/below industry benchmark).
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Bluest of the Blue Chips",
    "slug": "bluest_blue_chips",
    "category": "fundamental",
    "description": "Large-cap (>₹3000cr) with high profit growth, high ROE, and "
    "attractive valuation vs industry.",
    "needs": "fundamentals",
}
MCAP_MIN_CR = 3000.0
PROFIT_GROWTH_MIN = 10.0
ROE_MIN = 15.0


def screen(s: StockData) -> Result | None:
    g = s.f
    mcap_cr = safe(g("market_cap_cr"))
    if mcap_cr is None:
        mc = safe(g("market_cap"))
        mcap_cr = mc / 1e7 if mc else None  # raw INR → crore
    roe = safe(g("roe"))
    profit_growth = safe(g("profit_growth")) or safe(g("eps_growth"))
    pe = safe(g("pe"))
    industry_pe = safe(g("industry_pe"))  # from reference_data (Damodaran)
    if mcap_cr is None or roe is None:
        return None
    attractive_val = pe is not None and pe > 0 and (industry_pe is None or pe <= industry_pe)
    checks = {
        "MCap>3000cr": int(mcap_cr >= MCAP_MIN_CR),
        "ProfitGrowth>=10": int((profit_growth or 0) >= PROFIT_GROWTH_MIN),
        "ROE>=15": int(roe >= ROE_MIN),
        "ValAttractive": int(attractive_val),
    }
    passed = all(checks.values())
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(roe, 2),
        metrics={
            "MCap_cr": round(mcap_cr, 0),
            "ROE": roe,
            "ProfitGrowth%": profit_growth,
            "PE": pe,
            "IndustryPE": industry_pe,
            **checks,
        },
        note="blue chip" if passed else "",
    )
