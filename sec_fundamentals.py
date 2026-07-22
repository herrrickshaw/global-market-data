#!/usr/bin/env python3
# sec_fundamentals.py
# ===================
# US company fundamentals straight from SEC EDGAR's XBRL filing data — the same
# regulatory source Bloomberg's US fundamentals ultimately trace to. Free,
# official, no key (just a descriptive User-Agent, as the SEC requires), and not
# rate-limited the way Yahoo is.
#
# Turns a ticker into the fundamentals dict the strategies expect (net_income,
# roa, revenue, debt ratios, current ratio, cfo, ebit, eps growth, dividends …)
# so Piotroski / Coffee Can / Magic Formula / GARP / etc. can screen US names on
# audited filing data.
#
#   from sec_fundamentals import fundamentals
#   f = fundamentals("AAPL")            # -> dict for strategies.base.StockData

from __future__ import annotations

import warnings
from functools import lru_cache
from typing import Dict, List, Optional

import requests

warnings.filterwarnings("ignore")
_UA = {"User-Agent": "market-research umashankartd1991@gmail.com"}  # SEC requires contact


@lru_cache(maxsize=1)
def _ticker_cik() -> Dict[str, str]:
    """Map TICKER -> zero-padded 10-digit CIK (SEC official registry)."""
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=_UA, timeout=30)
    r.raise_for_status()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}


def _annual(facts: dict, concept: str, n: int = 4) -> List[float]:
    """Latest n ANNUAL (10-K / FY) values for a us-gaap concept, newest first."""
    node = facts.get("us-gaap", {}).get(concept)
    if not node:
        return []
    rows = []
    for unit_vals in node.get("units", {}).values():
        for r in unit_vals:
            if r.get("form") == "10-K" and r.get("fp") == "FY" and r.get("val") is not None:
                rows.append((r.get("end"), r["val"]))
    # dedup by period-end (keep last), newest first
    by_end = {}
    for end, val in rows:
        by_end[end] = val
    out = [by_end[k] for k in sorted(by_end, reverse=True)]
    return out[:n]


def _first(facts, *concepts, n=4):
    for c in concepts:
        v = _annual(facts, c, n)
        if v:
            return v
    return []


@lru_cache(maxsize=2048)
def companyfacts(ticker: str) -> Optional[dict]:
    cik = _ticker_cik().get(ticker.upper())
    if not cik:
        return None
    try:
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=_UA, timeout=30
        )
        if r.status_code != 200:
            return None
        return r.json().get("facts", {})
    except Exception:
        return None


def _ratio(a, b):
    return (a / b) if (a is not None and b not in (None, 0)) else None


def fundamentals(ticker: str) -> Dict:
    """Return a fundamentals dict (strategy-ready) for a US ticker, or {}."""
    f = companyfacts(ticker)
    if not f:
        return {}
    ni = _first(f, "NetIncomeLoss")
    rev = _first(
        f,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    )
    assets = _first(f, "Assets")
    liab = _first(f, "Liabilities")
    equity = _first(f, "StockholdersEquity")
    cur_a = _first(f, "AssetsCurrent")
    cur_l = _first(f, "LiabilitiesCurrent")
    cfo = _first(
        f,
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    )
    ebit = _first(f, "OperatingIncomeLoss")
    gp = _first(f, "GrossProfit")
    shares = _first(
        f,
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "dei:EntityCommonStockSharesOutstanding",
    )
    eps = _first(f, "EarningsPerShareBasic", "EarningsPerShareDiluted")
    debt = _first(f, "LongTermDebtNoncurrent", "LongTermDebt", "DebtCurrent")
    capex = _first(f, "PaymentsToAcquirePropertyPlantAndEquipment")
    div = _first(f, "PaymentsOfDividendsCommonStock", "PaymentsOfDividends")
    inv = _first(f, "InventoryNet", "InventoryFinishedGoodsNetOfReserves")
    recv = _first(f, "AccountsReceivableNetCurrent", "ReceivablesNetCurrent")
    pay = _first(f, "AccountsPayableCurrent", "AccountsPayableTradeCurrent")
    cogs = _first(f, "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")

    def g(lst, i=0):
        return lst[i] if len(lst) > i else None

    roa = _ratio(g(ni), g(assets))
    roa_p = _ratio(g(ni, 1), g(assets, 1))
    out = {
        "source": "SEC-EDGAR",
        "net_income": g(ni),
        "net_income_prev": g(ni, 1),
        "revenue": g(rev),
        "revenue_5y_ago": g(rev, 3) or g(rev, -1) if rev else None,
        "roa": roa * 100 if roa is not None else None,
        "roa_prev": roa_p * 100 if roa_p is not None else None,
        "roe": (_ratio(g(ni), g(equity)) or 0) * 100 if g(equity) else None,
        "cfo": g(cfo),
        "ebit": g(ebit),
        "current_ratio": _ratio(g(cur_a), g(cur_l)),
        "current_ratio_prev": _ratio(g(cur_a, 1), g(cur_l, 1)),
        "debt_to_assets": _ratio(g(liab), g(assets)),
        "debt_to_assets_prev": _ratio(g(liab, 1), g(assets, 1)),
        "debt_to_equity": _ratio(g(liab), g(equity)),
        "gross_margin": _margin(g(gp), g(rev)),
        "gross_margin_prev": _margin(g(gp, 1), g(rev, 1)),
        "asset_turnover": _ratio(g(rev), g(assets)),
        "asset_turnover_prev": _ratio(g(rev, 1), g(assets, 1)),
        "shares": g(shares),
        "shares_prev": g(shares, 1),
        "eps_ttm": g(eps),
        "eps_growth": _pct(g(eps), g(eps, 1)),
        "debt_history": [v for v in debt] or None,
        "capex_history": [v for v in capex] or None,
        "roe_history": _roe_history(ni, equity),
        "dividend_history": [v for v in div] or None,
        # cash-conversion-cycle inputs (used by the CCC strategy)
        "inventory": g(inv),
        "receivables": g(recv),
        "payables": g(pay),
        "cogs": g(cogs),
    }
    return {k: v for k, v in out.items() if v is not None}


def _margin(gp, rev):
    """Gross margin %, nulled if implausible (concept mismatch)."""
    if gp is None or rev in (None, 0):
        return None
    m = gp / rev * 100
    return round(m, 2) if 0 <= m <= 100 else None


def _pct(new, old):
    if new is None or old in (None, 0):
        return None
    return (new - old) / abs(old) * 100


def _roe_history(ni, equity):
    out = []
    for i in range(min(len(ni), len(equity))):
        if equity[i]:
            out.append(round(ni[i] / equity[i] * 100, 1))
    return out or None


if __name__ == "__main__":
    import sys

    for t in sys.argv[1:] or ["AAPL", "MSFT", "KO"]:
        f = fundamentals(t)
        keys = [
            "net_income",
            "roa",
            "roe",
            "current_ratio",
            "debt_to_equity",
            "gross_margin",
            "eps_growth",
        ]
        print(f"\n{t}:  " + ("no data" if not f else ""))
        for k in keys:
            if k in f:
                print(f"   {k:18} {round(f[k],2) if isinstance(f[k],float) else f[k]}")
