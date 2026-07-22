#!/usr/bin/env python3
# Piotroski F-Score — 9 binary tests across profitability, leverage, efficiency.
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Piotroski Score",
    "slug": "piotroski",
    "category": "fundamental",
    "description": "9-point fundamental quality score across profitability, "
    "leverage/liquidity, and operating efficiency.",
    "needs": "fundamentals",
}
STRONG = 7  # F-Score >= 7 considered strong


def screen(s: StockData) -> Result | None:
    g = s.f
    ni = safe(g("net_income"))
    roa, roa_p = safe(g("roa")), safe(g("roa_prev"))
    cfo = safe(g("cfo"))
    lev, lev_p = safe(g("debt_to_assets")), safe(g("debt_to_assets_prev"))
    cr, cr_p = safe(g("current_ratio")), safe(g("current_ratio_prev"))
    sh, sh_p = safe(g("shares")), safe(g("shares_prev"))
    gm, gm_p = safe(g("gross_margin")), safe(g("gross_margin_prev"))
    at, at_p = safe(g("asset_turnover")), safe(g("asset_turnover_prev"))
    if ni is None or roa is None:
        return None
    pts, checks = 0, {}

    def add(name, cond):
        nonlocal pts
        ok = bool(cond)
        checks[name] = int(ok)
        pts += int(ok)

    add("ROA>0", roa is not None and roa > 0)
    add("CFO>0", cfo is not None and cfo > 0)
    add("dROA>0", roa is not None and roa_p is not None and roa > roa_p)
    add("Accrual(CFO>NI)", cfo is not None and ni is not None and cfo > ni)
    add("dLeverage<0", lev is not None and lev_p is not None and lev < lev_p)
    add("dCurrentRatio>0", cr is not None and cr_p is not None and cr > cr_p)
    add("NoDilution", sh is not None and sh_p is not None and sh <= sh_p)
    add("dGrossMargin>0", gm is not None and gm_p is not None and gm > gm_p)
    add("dAssetTurnover>0", at is not None and at_p is not None and at > at_p)
    return Result(
        s.symbol,
        META["slug"],
        passed=pts >= STRONG,
        score=pts,
        metrics={"F_Score": pts, **checks},
        note="strong" if pts >= STRONG else "",
    )
