#!/usr/bin/env python3
# screener_in_auth.py
# ===================
# Authenticated screener.in access — logs in with YOUR credentials to unlock
# per-company financial exports (full P&L / balance sheet / cash flow / ratios),
# which fills the India fundamentals gap so the fundamental screeners (piotroski,
# magic_formula, garp, debt_reduction, dividend_yield, loss_to_profit, coffee_can)
# can run on real Indian data.
#
# ⚠️ SECURITY: credentials come ONLY from environment variables — never hard-code
# them, never paste them into chat, never commit them:
#     export SCREENER_EMAIL="you@example.com"
#     export SCREENER_PASSWORD="••••••••"
# Use a dedicated/low-value password; respect screener.in's Terms of Service and
# rate limits (this is for your own account's data, fetched politely).
#
#   from screener_in_auth import session, company_financials
#   s = session()                       # logged-in requests.Session (cached)
#   f = company_financials("RELIANCE")  # -> fundamentals dict for strategies

from __future__ import annotations

import io
import os
import re
import time
import warnings
from functools import lru_cache
from typing import Optional

import pandas as pd
import requests

warnings.filterwarnings("ignore")
_UA = {"User-Agent": "Mozilla/5.0 (research)"}
BASE = "https://www.screener.in"


class AuthError(RuntimeError):
    pass


def _load_dotenv() -> None:
    """Load SCREENER_* from a local, gitignored .env (KEY=VALUE lines) into the
    environment if not already set. Keeps the password out of code and chat."""
    from pathlib import Path

    envf = Path(__file__).parent / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@lru_cache(maxsize=1)
def session() -> requests.Session:
    """Return a logged-in session (cached). Reads SCREENER_EMAIL / SCREENER_PASSWORD
    from the environment or a local .env file."""
    _load_dotenv()
    email = os.environ.get("SCREENER_EMAIL")
    pw = os.environ.get("SCREENER_PASSWORD")
    if not (email and pw):
        raise AuthError("set SCREENER_EMAIL and SCREENER_PASSWORD env vars (never hard-code them)")
    s = requests.Session()
    s.headers.update(_UA)
    r = s.get(f"{BASE}/login/", timeout=25)
    token = re.search(r'name=["\']csrfmiddlewaretoken["\'] value=["\']([^"\']+)', r.text)
    if not token:
        raise AuthError("could not read CSRF token from login page")
    s.post(
        f"{BASE}/login/",
        data={
            "csrfmiddlewaretoken": token.group(1),
            "username": email,
            "password": pw,
            "next": "/",
        },
        headers={"Referer": f"{BASE}/login/"},
        timeout=25,
    )
    # verify: the dashboard/account page is only reachable when logged in
    who = s.get(f"{BASE}/", timeout=25)
    if "/logout/" not in who.text and "logout" not in who.text.lower():
        raise AuthError("login failed — check credentials")
    return s


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def company_export_bytes(symbol: str) -> Optional[bytes]:
    """Download the authenticated 'Export to Excel' workbook for a company.

    The button is a POST form: <button formaction="/user/company/export/<id>/">.
    We read that action + the CSRF token and POST to it."""
    s = session()
    page = s.get(f"{BASE}/company/{symbol}/", timeout=25)
    m = re.search(
        r'(?:formaction|action|href)=["\']([^"\']*company/export/\d+/?[^"\']*)["\']', page.text
    )
    if not m:
        return None
    url = m.group(1)
    if url.startswith("/"):
        url = BASE + url
    mt = re.search(r'name=["\']csrfmiddlewaretoken["\'] value=["\']([^"\']+)', page.text)
    tok = mt.group(1) if mt else s.cookies.get("csrftoken")
    headers = {"Referer": f"{BASE}/company/{symbol}/"}
    # try POST (with CSRF) then GET
    for how in ("post", "get"):
        try:
            if how == "post":
                r = s.post(url, data={"csrfmiddlewaretoken": tok}, headers=headers, timeout=40)
            else:
                r = s.get(url, headers=headers, timeout=40)
            if r.status_code == 200 and r.content[:2] == b"PK":  # xlsx = zip (PK)
                return r.content
        except Exception:
            continue
    return None


_RATIO_KEYS = {
    "market cap": "market_cap_cr",
    "current price": "current_price",
    "stock p/e": "pe",
    "p/e": "pe",
    "dividend yield": "dividend_yield",
    "roce": "roce_page",
    "roe": "roe_page",
    "book value": "book_value",
    "face value": "face_value",
}


def page_ratios(symbol: str) -> dict:
    """Top-of-page ratios from the company page (market cap, P/E, ROE, ROCE,
    dividend yield, current price) — the market/valuation inputs the export lacks."""
    try:
        html = session().get(f"{BASE}/company/{symbol}/", timeout=25).text
    except Exception:
        return {}
    out = {}
    for name, num in re.findall(
        r'<span class="name">\s*([^<]+?)\s*</span>.*?<span class="(?:nowrap )?value">(.*?)</span>',
        html,
        re.S,
    ):
        key = _RATIO_KEYS.get(name.strip().lower())
        if not key:
            continue
        v = _num(re.sub(r"<[^>]+>", "", num))
        if v is not None:
            out[key] = v
    return out


def company_financials(symbol: str) -> dict:
    """Parse the screener.in export + company-page ratios into a strategy-ready
    fundamentals dict covering all 11 screeners (what each field can be derived
    from; missing inputs simply make a given screener skip that stock)."""
    raw = company_export_bytes(symbol)
    if not raw:
        return {}
    try:
        xl = pd.ExcelFile(io.BytesIO(raw))
        sheet = next((s for s in xl.sheet_names if "data" in s.lower()), xl.sheet_names[0])
        df = pd.read_excel(io.BytesIO(raw), sheet_name=sheet, header=None)
    except Exception:
        return {}

    def row(*labels):
        for lab in labels:
            m = df[
                df.apply(
                    lambda r: r.astype(str).str.strip().str.lower().eq(lab.lower()).any(), axis=1
                )
            ]
            if not m.empty:
                vals = [_num(v) for v in m.iloc[0].tolist()[1:] if _num(v) is not None]
                if vals:
                    return vals
        return []

    def g(lst, i=0):
        return lst[i] if len(lst) > i else None

    def pct(a, b):
        return (a - b) / abs(b) * 100 if (a is not None and b not in (None, 0)) else None

    ni = row("Net profit", "Net Profit")
    rev = row("Sales", "Sales+", "Revenue", "Total Revenue")
    interest = row("Interest")
    pbt = row("Profit before tax", "Profit Before Tax")
    eps = row("EPS in Rs", "Adjusted EPS in Rs", "EPS")
    debt = row("Borrowings", "Borrowings+", "Total Debt")
    reserves = row("Reserves")
    capital = row("Equity Capital", "Equity Share Capital", "Share Capital")
    other_liab = row("Other Liabilities", "Other Liabilities+")
    net_block = row("Net Block", "Fixed Assets")
    inv = row("Inventory", "Inventories")
    recv = row("Trade receivables", "Debtors", "Receivables")
    cash = row("Cash & Bank", "Cash Equivalents")
    cfo = row("Cash from Operating Activity", "Cash from Operating Activity+")
    shares = row("No. of Equity Shares", "No of Equity Shares")
    div = row("Dividend Amount", "Dividend Paid", "Dividend Payout")

    # equity, assets, derived ratios (statement-based)
    eq = (
        [
            (capital[i] if i < len(capital) else 0) + (reserves[i] if i < len(reserves) else 0)
            for i in range(max(len(capital), len(reserves)))
        ]
        if (capital or reserves)
        else []
    )
    assets_tot = (
        [
            (eq[i] if i < len(eq) else 0)
            + (debt[i] if i < len(debt) else 0)
            + (other_liab[i] if i < len(other_liab) else 0)
            for i in range(max(len(eq), len(debt), len(other_liab)))
        ]
        if eq
        else []
    )
    roe_hist = [round(ni[i] / eq[i] * 100, 1) for i in range(min(len(ni), len(eq))) if eq[i]]
    ebit = (g(pbt) or 0) + (g(interest) or 0) if (pbt or interest) else None

    r = page_ratios(symbol)
    out = {
        "source": "screener.in",
        # profitability
        "net_income": g(ni),
        "net_income_prev": g(ni, 1),
        "profit_growth": pct(g(ni), g(ni, 1)),
        "revenue": g(rev),
        "revenue_5y_ago": g(rev, 5) or (rev[-1] if rev else None),
        "eps_ttm": g(eps),
        "eps_growth": pct(g(eps), g(eps, 1)),
        "cfo": g(cfo),
        "ebit": ebit,
        # returns / leverage
        "roe": r.get("roe_page") or (round(g(ni) / g(eq) * 100, 1) if g(eq) else None),
        "roce": r.get("roce_page"),
        "roc": r.get("roce_page"),
        "roe_history": roe_hist or None,
        "roa": (round(g(ni) / g(assets_tot) * 100, 1) if g(assets_tot) else None),
        "roa_prev": (round(g(ni, 1) / g(assets_tot, 1) * 100, 1) if g(assets_tot, 1) else None),
        "debt_to_equity": (round(g(debt) / g(eq), 2) if g(eq) else None),
        "debt_to_assets": (round(g(debt) / g(assets_tot), 2) if g(assets_tot) else None),
        "debt_to_assets_prev": (
            round(g(debt, 1) / g(assets_tot, 1), 2) if g(assets_tot, 1) else None
        ),
        "asset_turnover": (round(g(rev) / g(assets_tot), 2) if g(assets_tot) else None),
        "asset_turnover_prev": (
            round(g(rev, 1) / g(assets_tot, 1), 2) if g(assets_tot, 1) else None
        ),
        "shares": g(shares),
        "shares_prev": g(shares, 1),
        "debt_history": debt or None,
        "capex_history": net_block or None,
        # working capital + CCC inputs (payables absent in export → CCC via the screen)
        "inventory": g(inv),
        "receivables": g(recv),
        "cash": g(cash),
        # income / valuation (page)
        "market_cap_cr": r.get("market_cap_cr"),
        "current_price": r.get("current_price"),
        "pe": r.get("pe"),
        "book_value": r.get("book_value"),
        "dividend_yield": r.get("dividend_yield"),
        "dividend_history": div or None,
        "dividend_pay_years": sum(1 for x in (div or []) if x and x > 0) or None,
        # magic formula
        "enterprise_value": (
            ((r.get("market_cap_cr") or 0) + (g(debt) or 0) - (g(cash) or 0))
            if r.get("market_cap_cr")
            else None
        ),
        "earnings_yield": (
            round(ebit / ((r.get("market_cap_cr") or 0) + (g(debt) or 0) - (g(cash) or 0)) * 100, 2)
            if (ebit and r.get("market_cap_cr"))
            else None
        ),
        # turnaround (annual NI as a coarse proxy; quarterly parse is a TODO)
        "quarterly_net_income": ni or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def fundamentals_batch(symbols, pause: float = 1.0, verbose: bool = True) -> dict:
    """Fetch fundamentals for many symbols (polite pause between requests)."""
    out = {}
    for i, sym in enumerate(symbols, 1):
        try:
            f = company_financials(sym)
            if f:
                out[sym] = f
        except AuthError:
            raise
        except Exception:
            pass
        if verbose and i % 25 == 0:
            print(f"  {i}/{len(symbols)} fetched ({len(out)} ok)")
        time.sleep(pause)
    return out


if __name__ == "__main__":
    import sys

    try:
        session()
        print("logged in ✓")
        for t in [a for a in sys.argv[1:] if a.isalnum()] or ["RELIANCE"]:
            raw = company_export_bytes(t)
            print(f"{t}: export bytes = {0 if not raw else len(raw)}")
            f = company_financials(t)
            print(f"{t}: {f if f else '(empty — export not found/parsed)'}")
    except AuthError as e:
        print("AUTH:", e)
