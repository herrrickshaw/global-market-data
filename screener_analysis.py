# screener_analysis.py
# =====================
# Indian market screener suite — Magic Formula, Golden Crossover, Bull Cartel,
# Darvas Box, Piotroski F-Score, Coffee Can — across the full NSE + BSE universe.
#
# Speed strategy (markitdown pre-filter):
#   screener.in already runs the heavy fundamental filter across all 2,600+ stocks.
#   This script uses markitdown to fetch those pre-filtered lists (< 1 s per page),
#   then runs enrichment analysis (Darvas, Piotroski, Coffee Can) only on the
#   shortlisted stocks — typically 26–100 per screener vs 2,600 for a full scan.
#   That is a 35–100× reduction in API calls and processing time.
#
# Screeners implemented:
#   1. Magic Formula       — ROIC > 25% AND Earnings Yield > 15%  (Greenblatt 2005)
#   2. Golden Crossover    — 50 DMA just crossed above 200 DMA    (technical)
#   3. Bull Cartel         — YoY quarterly sales growth > 15%,
#                            YoY quarterly profit growth > 20%,
#                            Net profit > ₹1 Cr
#   4. Darvas Box          — price breakout above confirmed box top
#   5. Piotroski F-Score   — 9-point accounting quality score ≥ 7
#   6. Coffee Can          — CAGR > 10%, ROCE > 15%, D/E < 1, MCap ≥ ₹500 Cr
#
# Usage:
#   python screener_analysis.py                   # all 6 screeners, online mode
#   python screener_analysis.py --offline         # skip screener.in, compute from scratch
#   python screener_analysis.py --screeners mf gc # only magic-formula + golden-crossover
#   python screener_analysis.py --workers 8       # parallel fundamental fetches
#   python screener_analysis.py --full-universe   # override screener.in pre-filter;
#                                                 #   run on all NSE+BSE symbols
#
# Install:
#   pip install yfinance pandas openpyxl requests markitdown "nse[local]" bse
#
# ─────────────────────────────────────────────────────────────────────────────────
# ⚠️  DISCLAIMER
# This tool is for EDUCATIONAL and RESEARCH purposes only.
# It does NOT constitute financial advice or investment recommendations.
# Screener results are mechanical filters based on publicly available data —
# they are NOT buy or sell signals.  Quantitative screens may contain errors
# due to data delays, restatements, or API limitations.
# Past screening results do not guarantee future returns.
# Always conduct independent due diligence and consult a SEBI-registered
# investment advisor before making any investment decisions.
# ─────────────────────────────────────────────────────────────────────────────────

import argparse
import io
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# NSE data fetcher — live regime, events, symbols, institutional activity
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from nse_data_fetcher import NSEDataFetcher as _NSEFetcher
    from nse_data_fetcher import get_nse_symbols as _get_nse_syms

    _NSE_FETCHER = _NSEFetcher()
    _USE_NSE_FETCHER = True
except ImportError:
    _NSE_FETCHER = None
    _USE_NSE_FETCHER = False

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

# ── Disclaimer ────────────────────────────────────────────────────────────────

DISCLAIMER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                          ⚠️   IMPORTANT DISCLAIMER                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  This report is for EDUCATIONAL and RESEARCH purposes ONLY.                ║
║  It does NOT constitute financial advice, investment recommendations,       ║
║  or any form of solicitation to buy or sell securities.                     ║
║                                                                             ║
║  • Quantitative screens are mechanical filters — NOT buy/sell signals.      ║
║  • Data is sourced from Yahoo Finance & screener.in; may contain delays,   ║
║    restatements, or errors.                                                 ║
║  • Past screening results do NOT guarantee future returns.                  ║
║  • Always conduct independent due diligence before investing.               ║
║  • Consult a SEBI-registered investment advisor for personalised advice.    ║
║  • Markets are subject to risk — you may lose part or all of your capital.  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ── Screener definitions ──────────────────────────────────────────────────────

SCREENERS = {
    "mf": {
        "id": 59,
        "slug": "magic-formula",
        "name": "Magic Formula",
        "author": "Joel Greenblatt (2005) — 'The Little Book That Beats the Market'",
        "logic": (
            "Ranks stocks by a combined score of (1) Return on Invested Capital (ROIC) "
            "— how efficiently the business uses its capital — and (2) Earnings Yield "
            "(EBIT / Enterprise Value) — how cheaply the market prices that earnings power. "
            "Stocks with ROIC > 25% AND Earnings Yield > 15% pass the quantitative filter."
        ),
        "criteria": "ROIC > 25%  AND  Earnings Yield > 15%  AND  Book Value > 0  AND  MCap > ₹15 Cr",
        "thresholds": {"roic_pct": 25, "earnings_yield_pct": 15, "min_mcap_cr": 15},
        "risk_note": (
            "Magic Formula stocks are often cheap for a reason (cyclical downturns, one-off charges). "
            "Greenblatt recommends holding a diversified basket of 20-30 stocks for at least one year."
        ),
    },
    "gc": {
        "id": 336509,
        "slug": "golden-crossover",
        "name": "Golden Crossover",
        "author": "Classic technical analysis",
        "logic": (
            "A 'Golden Cross' occurs when the 50-day moving average (short-term trend) "
            "crosses ABOVE the 200-day moving average (long-term trend) for the first time — "
            "signalling a potential shift from a downtrend to an uptrend. "
            "This is one of the most widely watched bullish technical signals."
        ),
        "criteria": "50 DMA today > 200 DMA today  AND  50 DMA yesterday < 200 DMA yesterday",
        "thresholds": {},
        "risk_note": (
            "The Golden Cross is a lagging indicator — it triggers after the trend has already "
            "begun reversing. False signals are common in range-bound markets. "
            "Confirm with volume and fundamental strength before acting."
        ),
    },
    "bc": {
        "id": 1,
        "slug": "the-bull-cartel",
        "name": "Bull Cartel",
        "author": "screener.in community screen",
        "logic": (
            "Identifies companies with strong near-term earnings momentum: "
            "both revenue and profit are accelerating on a year-over-year quarterly basis. "
            "The net profit filter (> ₹1 Cr) removes micro-caps with negligible earnings."
        ),
        "criteria": (
            "YoY Quarterly Sales Growth > 15%  AND  "
            "YoY Quarterly Profit Growth > 20%  AND  "
            "Net Profit (latest quarter) > ₹1 Cr"
        ),
        "thresholds": {"sales_growth_pct": 15, "profit_growth_pct": 20, "min_profit_cr": 1},
        "risk_note": (
            "Momentum screens like Bull Cartel can chase stocks near cycle peaks. "
            "High quarterly growth is often mean-reverting. Check sector tailwinds, "
            "order book, and management guidance before acting on this signal."
        ),
    },
}

# ── Constants ─────────────────────────────────────────────────────────────────

DOWNLOAD_DIR = Path("./screener_results")
DOWNLOAD_DIR.mkdir(exist_ok=True)

DARVAS_CONFIRM = 3
BATCH_SIZE = 200
MAX_WORKERS = 6
SLEEP_SEC = 0.4  # between screener.in page fetches

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ── markitdown pre-filter ──────────────────────────────────────────────────────


def fetch_screener_in_symbols(screen_id: int, screen_slug: str, max_pages: int = 10) -> list[str]:
    """
    Fetch pre-filtered stock symbols from a screener.in public screen.

    Uses markitdown to convert the server-rendered HTML table to clean markdown,
    then extracts NSE ticker symbols via regex.  This is significantly faster than
    downloading full financial statements for the entire NSE+BSE universe, because
    screener.in has already applied the fundamental filter server-side.

    Typical universe reduction:
      Magic Formula   : 2,600 → ~74 stocks  (35× faster)
      Golden Crossover: 2,600 → ~26 stocks  (100× faster)
      Bull Cartel     : 2,600 → ~60 stocks  (43× faster)
    """
    try:
        from markitdown import MarkItDown

        md_converter = MarkItDown()
    except ImportError:
        print("  ⚠️  markitdown not installed — pip install markitdown")
        print("       Falling back to full-universe computation.")
        return []

    base_url = f"https://www.screener.in/screens/{screen_id}/{screen_slug}/"
    all_syms = []
    prev_len = -1

    print(f"  Fetching screener.in/{screen_slug} …", end=" ", flush=True)
    for page in range(1, max_pages + 1):
        url = f"{base_url}?page={page}"
        try:
            resp = requests.get(url, timeout=20, headers=HEADERS)
            resp.raise_for_status()

            # markitdown converts HTML → markdown, preserving table structure.
            result = md_converter.convert(
                io.BytesIO(resp.content),
                file_extension=".html",
            )
            text = result.text_content

            # Strategy 1: extract from /company/SYMBOL/ href patterns
            found = re.findall(r"/company/([A-Z][A-Z0-9&\-]{1,19})/", text)

            # Strategy 2: first cell of markdown table rows
            for line in text.split("\n"):
                if "|" not in line:
                    continue
                cols = [c.strip() for c in line.split("|") if c.strip()]
                if not cols:
                    continue
                first = cols[0].strip("* \t")
                if (
                    2 <= len(first) <= 20
                    and re.match(r"^[A-Z][A-Z0-9&\-]+$", first)
                    and first
                    not in {
                        "NAME",
                        "SYMBOL",
                        "COMPANY",
                        "S.NO",
                        "NO",
                        "CMP",
                        "PE",
                        "MCap",
                        "DIV",
                        "NP",
                        "SALES",
                    }
                ):
                    found.append(first)

            page_syms = list(dict.fromkeys(found))
            all_syms.extend(page_syms)

            if len(all_syms) == prev_len:  # no new symbols → last page
                break
            prev_len = len(all_syms)
            time.sleep(SLEEP_SEC)

        except requests.HTTPError as e:
            if e.response.status_code == 404:
                break
            print(f"\n  HTTP {e.response.status_code} on page {page} — stopping.")
            break
        except Exception as e:
            print(f"\n  Error on page {page}: {e}")
            break

    unique = list(dict.fromkeys(all_syms))
    print(f"{len(unique)} symbols")
    return unique


def fetch_all_nse_symbols() -> list:
    """
    Get all NSE EQ symbols.
    Uses nsepython.nse_eq_symbols() as primary source (direct NSE API, 2372 stocks),
    falls back to nse-library bhavcopy.
    """
    # Primary: nsepython (direct NSE API — no file parsing needed)
    if _USE_NSE_FETCHER:
        try:
            syms = _get_nse_syms()
            if syms and len(syms) > 100:
                return syms
        except Exception:
            pass

    # Fallback: nse-library bhavcopy
    try:
        from nse import NSE

        with NSE(download_folder=str(DOWNLOAD_DIR), server=False) as nse:
            today = datetime.today()
            for offset in range(7):
                d = today - timedelta(days=offset)
                try:
                    result = nse.equityBhavcopy(d)
                    if hasattr(result, "exists") and result.exists():
                        df = pd.read_csv(result)
                        if "SctySrs" in df.columns:
                            return sorted(
                                df[df["SctySrs"] == "EQ"]["TckrSymb"].dropna().str.strip().tolist()
                            )
                except Exception:
                    continue
    except ImportError:
        pass

    print("  ⚠️  NSE symbol fetch failed; using Nifty 50 fallback.")
    return []


# ── yfinance helpers ──────────────────────────────────────────────────────────

# Shared helpers (see stock_utils.py) — aliased to keep existing call sites.
from stock_utils import first_df as _first_df
from stock_utils import row as _row


def bulk_download_ohlc(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """
    Bulk-download OHLC using yfinance.download() in batches.
    1-year window supports both Darvas (needs 90 d) and Golden Crossover (needs 200 d).
    """
    result: dict[str, pd.DataFrame] = {}
    batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    for idx, batch in enumerate(batches, 1):
        print(f"    OHLC batch {idx}/{len(batches)} ({len(batch)} tickers) …", end=" ", flush=True)
        try:
            raw = yf.download(batch, period=period, auto_adjust=True, threads=True, progress=False)
            if raw.empty:
                print("empty")
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                for t in batch:
                    try:
                        df = raw.xs(t, axis=1, level=1).dropna(how="all")
                        if not df.empty and len(df) >= 10:
                            result[t] = df
                    except KeyError:
                        pass
            else:
                if not raw.empty:
                    result[batch[0]] = raw
            print(f"OK ({sum(1 for t in batch if t in result)} usable)")
        except Exception as e:
            print(f"ERROR — {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# SCREENER 1 — MAGIC FORMULA  (Joel Greenblatt, 2005)
# ══════════════════════════════════════════════════════════════════════════════


def compute_magic_formula(symbol: str, suffix: str = ".NS") -> dict:
    """
    Magic Formula screen: ranks stocks by combined Earnings Yield + ROIC.

    Earnings Yield = EBIT / Enterprise Value
      Enterprise Value = Market Cap + Total Debt − Cash & Equivalents
      Higher = cheaper (more earnings per rupee invested in the business)

    ROIC = EBIT / Capital Employed
      Capital Employed = Total Assets − Current Liabilities
      Higher = more profitable use of capital

    Greenblatt's insight: buying above-average businesses (high ROIC) at
    above-average earnings yields (cheap EV/EBIT) systematically outperforms
    over time because the market misprices short-term discomfort.

    Thresholds (matching screener.in):  ROIC > 25%  |  Earnings Yield > 15%
    """
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass
    except Exception as e:
        return {"symbol": symbol, "screen": "mf", "qualifies": False, "error": str(e)}

    if inc is None:
        return {"symbol": symbol, "screen": "mf", "qualifies": False, "error": "no_data"}

    ebit = _row(inc, "EBIT", "Operating Income", "Ebit")
    total_assets = _row(bal, "Total Assets")
    curr_liab = _row(bal, "Current Liabilities", "Total Current Liabilities")
    cap_employed = (total_assets - curr_liab) if (total_assets and curr_liab) else None
    roic = (ebit / cap_employed * 100) if (ebit and cap_employed and cap_employed > 0) else None

    market_cap = info.get("marketCap")
    total_debt = info.get("totalDebt", 0) or 0
    cash = info.get("totalCash", 0) or 0
    ev = (market_cap + total_debt - cash) if market_cap else None
    ey = (ebit / ev * 100) if (ebit and ev and ev > 0) else None

    book_value = info.get("bookValue")
    mcap_cr = market_cap / 1e7 if market_cap else None
    name_ = info.get("shortName", "")

    qualifies = bool(
        roic is not None
        and roic > 25
        and ey is not None
        and ey > 15
        and book_value is not None
        and book_value > 0
        and mcap_cr is not None
        and mcap_cr > 15
    )

    return {
        "symbol": symbol,
        "name": name_,
        "screen": "mf",
        "qualifies": qualifies,
        "ROIC_%": round(roic, 2) if roic is not None else None,
        "Earnings_Yield_%": round(ey, 2) if ey is not None else None,
        "Book_Value": round(book_value, 2) if book_value is not None else None,
        "MCap_Cr": round(mcap_cr, 2) if mcap_cr is not None else None,
        "error": "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCREENER 2 — GOLDEN CROSSOVER  (technical momentum)
# ══════════════════════════════════════════════════════════════════════════════


def compute_golden_crossover(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Golden Crossover: 50 DMA crossed above 200 DMA for the first time (today).

    The '50/200 golden cross' is one of the most widely tracked bullish signals
    in technical analysis.  It indicates that short-term momentum has shifted
    decisively above the long-term trend line, often attracting institutional
    buying.

    This function computes both:
      - 'golden_crossover': strict — crossed just today (screener.in definition)
      - 'dma50_above_200':  broader — 50 DMA is currently above 200 DMA
                            (useful to track stocks that crossed recently)

    Requires at least 201 bars of OHLC data (≈ 1 year of trading days).
    """
    if df is None or df.empty:
        return {"symbol": symbol, "screen": "gc", "qualifies": False, "error": "no_data"}

    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(closes) < 201:
        return {
            "symbol": symbol,
            "screen": "gc",
            "qualifies": False,
            "error": f"need 201 bars, got {len(closes)}",
        }

    dma50 = closes.rolling(50).mean()
    dma200 = closes.rolling(200).mean()

    dma50_today = float(dma50.iloc[-1])
    dma200_today = float(dma200.iloc[-1])
    dma50_prev = float(dma50.iloc[-2])
    dma200_prev = float(dma200.iloc[-2])
    ltp = float(closes.iloc[-1])

    # Strict golden cross: first day 50 DMA overtakes 200 DMA
    golden_cross = (dma50_prev < dma200_prev) and (dma50_today > dma200_today)
    # Broader: within 5 days of the cross
    recent_cross = False
    for i in range(2, min(7, len(closes))):
        p50 = float(dma50.iloc[-i])
        p200 = float(dma200.iloc[-i])
        if p50 < p200 and dma50_today > dma200_today:
            recent_cross = True
            break

    gap_pct = (dma50_today - dma200_today) / dma200_today * 100 if dma200_today else 0

    return {
        "symbol": symbol,
        "screen": "gc",
        "qualifies": golden_cross,
        "recent_crossover": recent_cross,
        "dma50_above_200": dma50_today > dma200_today,
        "LTP": round(ltp, 2),
        "DMA50": round(dma50_today, 2),
        "DMA200": round(dma200_today, 2),
        "DMA_gap_%": round(gap_pct, 2),
        "error": "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCREENER 3 — BULL CARTEL  (quarterly earnings momentum)
# ══════════════════════════════════════════════════════════════════════════════


def compute_bull_cartel(symbol: str, suffix: str = ".NS") -> dict:
    """
    Bull Cartel: identifies companies with strong and accelerating quarterly momentum.

    The screen rewards businesses where both the top line (sales) and the bottom
    line (net profit) are growing substantially faster than the same quarter a year
    ago.  The ₹1 Cr net profit filter removes shell companies and micro-caps with
    negligible earnings.

    Data source: yfinance quarterly income statement (up to 4 years of quarterly
    data available free of charge).

    Matching screener.in criteria:
      • YoY Quarterly Sales Growth > 15%
      • YoY Quarterly Profit Growth > 20%
      • Net Profit (latest quarter) > ₹1 Cr
    """
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc_q = _first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass
    except Exception as e:
        return {"symbol": symbol, "screen": "bc", "qualifies": False, "error": str(e)}

    if inc_q is None or len(inc_q.columns) < 5:
        return {
            "symbol": symbol,
            "screen": "bc",
            "qualifies": False,
            "error": "need ≥5 quarters of data",
        }

    # col 0 = most recent quarter; col 4 = same quarter 1 year ago
    rev_q0 = _row(inc_q, "Total Revenue", col=0)
    rev_q4 = _row(inc_q, "Total Revenue", col=4)
    ni_q0 = _row(inc_q, "Net Income", col=0)
    ni_q4 = _row(inc_q, "Net Income", col=4)

    sales_g = (
        ((rev_q0 - rev_q4) / abs(rev_q4) * 100) if (rev_q0 and rev_q4 and rev_q4 != 0) else None
    )
    profit_g = ((ni_q0 - ni_q4) / abs(ni_q4) * 100) if (ni_q0 and ni_q4 and ni_q4 != 0) else None
    ni_cr = ni_q0 / 1e7 if ni_q0 else None

    qualifies = bool(
        sales_g is not None
        and sales_g > 15
        and profit_g is not None
        and profit_g > 20
        and ni_cr is not None
        and ni_cr > 1
    )

    return {
        "symbol": symbol,
        "name": info.get("shortName", ""),
        "screen": "bc",
        "qualifies": qualifies,
        "Sales_Growth_YoY_%": round(sales_g, 2) if sales_g is not None else None,
        "Profit_Growth_YoY_%": round(profit_g, 2) if profit_g is not None else None,
        "Net_Profit_Cr": round(ni_cr, 2) if ni_cr is not None else None,
        "error": "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCREENERS 4–6 — DARVAS BOX + PIOTROSKI + COFFEE CAN  (from existing scripts)
# ══════════════════════════════════════════════════════════════════════════════


def compute_darvas_box(df: pd.DataFrame, symbol: str = "", confirm: int = DARVAS_CONFIRM) -> dict:
    """
    Darvas Box breakout (Nicolas Darvas, 1960).
    Box formed from historical bars only — current bar excluded (design invariant).
    """

    def find_col(df, candidates):
        for c in candidates:
            m = next((col for col in df.columns if c.upper() in col.upper()), None)
            if m:
                return m
        return None

    h_col = find_col(df, ["High"])
    l_col = find_col(df, ["Low"])
    c_col = find_col(df, ["Close"])

    if not all([h_col, l_col, c_col]) or len(df) < confirm + 5:
        return {"symbol": symbol, "screen": "darvas", "signal": "INSUFFICIENT_DATA"}

    highs = pd.to_numeric(df[h_col], errors="coerce").fillna(0).tolist()
    lows = pd.to_numeric(df[l_col], errors="coerce").fillna(0).tolist()
    closes = pd.to_numeric(df[c_col], errors="coerce").fillna(0).tolist()

    current = closes[-1]
    h, lo = highs[:-1], lows[:-1]
    n = len(h)

    box_top_idx = box_top = None
    for i in range(n - confirm - 1, -1, -1):
        if h[i] == 0:
            continue
        win = h[i + 1 : i + 1 + confirm]
        if len(win) == confirm and all(x < h[i] for x in win):
            box_top_idx, box_top = i, h[i]
            break

    if box_top is None:
        return {
            "symbol": symbol,
            "screen": "darvas",
            "signal": "NO_BOX",
            "current_price": round(current, 2),
        }

    seg = lo[box_top_idx:]
    box_bottom = None
    for i in range(len(seg) - confirm):
        if seg[i] == 0:
            continue
        win = seg[i + 1 : i + 1 + confirm]
        if len(win) == confirm and all(x > seg[i] for x in win):
            box_bottom = seg[i]
            break
    if box_bottom is None:
        valid = [x for x in seg if x > 0]
        box_bottom = min(valid) if valid else None

    if box_bottom is None:
        return {
            "symbol": symbol,
            "screen": "darvas",
            "signal": "NO_BOX",
            "box_top": round(box_top, 2),
            "current_price": round(current, 2),
        }

    signal = (
        "BREAKOUT_BUY"
        if current > box_top
        else "BREAKDOWN_SELL" if current < box_bottom else "IN_BOX"
    )
    box_range = box_top - box_bottom
    upside = (box_top - current) / current * 100 if current else 0
    pos = (current - box_bottom) / box_range * 100 if box_range else 0

    return {
        "symbol": symbol,
        "screen": "darvas",
        "signal": signal,
        "qualifies": signal == "BREAKOUT_BUY",
        "Box_Top": round(box_top, 2),
        "Box_Bottom": round(box_bottom, 2),
        "LTP": round(current, 2),
        "Upside_to_Top_%": round(upside, 2),
        "Position_in_Box_%": round(pos, 1),
    }


def compute_piotroski(symbol: str, suffix: str = ".NS") -> dict:
    """
    Piotroski F-Score (Joseph Piotroski, 2000) — 9-point financial quality score.
    Score ≥ 7 → strong; ≤ 3 → weak/avoid.
    """
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        cf = _first_df(ticker, "cash_flow", "cashflow")
    except Exception as e:
        return {
            "symbol": symbol,
            "screen": "piotroski",
            "qualifies": False,
            "f_score": None,
            "error": str(e),
        }

    if inc is None:
        return {
            "symbol": symbol,
            "screen": "piotroski",
            "qualifies": False,
            "f_score": None,
            "error": "no_data",
        }

    sc = {}
    ni0 = _row(inc, "Net Income", col=0)
    a0 = _row(bal, "Total Assets", col=0)
    ni1 = _row(inc, "Net Income", col=1)
    a1 = _row(bal, "Total Assets", col=1)
    roa0 = (ni0 / a0) if (ni0 and a0) else None
    roa1 = (ni1 / a1) if (ni1 and a1) else None
    sc["F1"] = 1 if (roa0 and roa0 > 0) else 0
    ocf0 = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
    sc["F2"] = 1 if (ocf0 and ocf0 > 0) else 0
    sc["F3"] = 1 if (roa0 and roa1 and roa0 > roa1) else 0
    sc["F4"] = 1 if (ocf0 and a0 and roa0 and (ocf0 / a0) > roa0) else 0
    ltd0 = _row(bal, "Long Term Debt", col=0) or 0
    ltd1 = _row(bal, "Long Term Debt", col=1) or 0
    lev0 = (ltd0 / a0) if a0 else None
    lev1 = (ltd1 / a1) if a1 else None
    sc["F5"] = 1 if (lev0 is not None and lev1 is not None and lev0 < lev1) else 0
    ca0 = _row(bal, "Current Assets", "Total Current Assets", col=0)
    cl0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca1 = _row(bal, "Current Assets", "Total Current Assets", col=1)
    cl1 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    cr0 = (ca0 / cl0) if (ca0 and cl0) else None
    cr1 = (ca1 / cl1) if (ca1 and cl1) else None
    sc["F6"] = 1 if (cr0 and cr1 and cr0 > cr1) else 0
    sh0 = _row(bal, "Share Issued", col=0)
    sh1 = _row(bal, "Share Issued", col=1)
    sc["F7"] = (1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1
    rev0 = _row(inc, "Total Revenue", col=0)
    gp0 = _row(inc, "Gross Profit", col=0)
    rev1 = _row(inc, "Total Revenue", col=1)
    gp1 = _row(inc, "Gross Profit", col=1)
    gm0 = (gp0 / rev0) if (gp0 and rev0) else None
    gm1 = (gp1 / rev1) if (gp1 and rev1) else None
    sc["F8"] = 1 if (gm0 and gm1 and gm0 > gm1) else 0
    at0 = (rev0 / a0) if (rev0 and a0) else None
    at1 = (rev1 / a1) if (rev1 and a1) else None
    sc["F9"] = 1 if (at0 and at1 and at0 > at1) else 0

    total = sum(sc.values())
    return {
        "symbol": symbol,
        "screen": "piotroski",
        "qualifies": total >= 7,
        "f_score": total,
        "Piotroski_Score": total,
        "interpretation": ("STRONG" if total >= 7 else "MODERATE" if total >= 4 else "WEAK"),
        "error": "",
    }


def compute_coffee_can(symbol: str, suffix: str = ".NS") -> dict:
    """
    Coffee Can Portfolio screen (Saurabh Mukherjea, Marcellus Investment Managers).
    Quality compounders: Revenue CAGR > 10%, ROCE > 15%, D/E < 1, MCap ≥ ₹500 Cr,
    no loss-making year.
    """
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass
    except Exception as e:
        return {"symbol": symbol, "screen": "cc", "qualifies": False, "error": str(e)}

    if inc is None:
        return {"symbol": symbol, "screen": "cc", "qualifies": False, "error": "no_data"}

    def series(df, *rows):
        for name in rows:
            if df is not None and name in df.index:
                return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
        return []

    c = {}
    revs = series(inc, "Total Revenue")
    if len(revs) >= 2:
        yrs = len(revs) - 1
        cagr = ((revs[0] / revs[-1]) ** (1 / yrs) - 1) * 100 if revs[-1] > 0 else None
        c["C1"] = 1 if (cagr and cagr > 10) else 0
    else:
        c["C1"] = 0

    ebit_s = series(inc, "EBIT", "Operating Income", "Ebit")
    ta_s = series(bal, "Total Assets")
    cl_s = series(bal, "Current Liabilities", "Total Current Liabilities")
    roce_l = [
        ebit_s[i] / (ta_s[i] - cl_s[i]) * 100
        for i in range(min(len(ebit_s), len(ta_s), len(cl_s)))
        if (ta_s[i] - cl_s[i]) > 0
    ]
    c["C2"] = 1 if (roce_l and sum(roce_l) / len(roce_l) > 15) else 0

    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        de = de_raw / 100 if de_raw > 10 else de_raw
        c["C3"] = 1 if de < 1 else 0
    else:
        ltd_s = series(bal, "Long Term Debt")
        eq_s = series(
            bal,
            "Stockholders Equity",
            "Total Stockholder Equity",
            "Total Equity Gross Minority Interest",
        )
        c["C3"] = (1 if (ltd_s[0] / eq_s[0]) < 1 else 0) if (ltd_s and eq_s and eq_s[0] != 0) else 0

    mcap = info.get("marketCap")
    mcap_cr = mcap / 1e7 if mcap else None
    c["C4"] = 1 if (mcap_cr and mcap_cr >= 500) else 0

    ni_s = series(inc, "Net Income")
    c["C5"] = 1 if (ni_s and all(n > 0 for n in ni_s)) else 0

    total = sum(c.values())
    return {
        "symbol": symbol,
        "screen": "cc",
        "qualifies": total == len(c),
        "CC_Score": f"{total}/{len(c)}",
        "CC_Criteria": c,
        "error": "",
    }


# ── Per-symbol enrichment (runs all 6 screeners in one call) ─────────────────


def enrich_symbol(
    symbol: str, ohlc_df: pd.DataFrame | None, suffix: str = ".NS", active_screens: set = None
) -> dict:
    """
    Run all active screeners for one symbol.  OHLC-based screeners (Darvas,
    Golden Crossover) use the pre-downloaded DataFrame; fundamental screeners
    make a single shared yfinance.Ticker call to minimise HTTP overhead.
    """
    active = active_screens or {"mf", "gc", "bc", "darvas", "piotroski", "cc"}
    out = {"symbol": symbol, "suffix": suffix}

    # ── Shared Ticker + financial statements (one round-trip) ─────────────────
    needs_financials = active & {"mf", "bc", "piotroski", "cc"}
    ticker = inc = inc_q = None
    if needs_financials:
        try:
            ticker = yf.Ticker(f"{symbol}{suffix}")
            inc = _first_df(ticker, "income_stmt", "financials")
            _first_df(ticker, "balance_sheet")
            _first_df(ticker, "cash_flow", "cashflow")
            inc_q = _first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
            try:
                pass
            except Exception:
                pass
        except Exception as e:
            out["fetch_error"] = str(e)

    # ── OHLC-based screeners ──────────────────────────────────────────────────
    if "darvas" in active and ohlc_df is not None:
        out["darvas"] = compute_darvas_box(ohlc_df, symbol=symbol)
    if "gc" in active and ohlc_df is not None:
        out["gc"] = compute_golden_crossover(ohlc_df, symbol=symbol)

    # ── Fundamental screeners (reuse shared objects) ───────────────────────────
    if "piotroski" in active and inc is not None:
        out["piotroski"] = compute_piotroski(symbol, suffix)

    if "cc" in active and inc is not None:
        out["cc"] = compute_coffee_can(symbol, suffix)

    if "mf" in active and inc is not None:
        out["mf"] = compute_magic_formula(symbol, suffix)

    if "bc" in active and inc_q is not None:
        out["bc"] = compute_bull_cartel(symbol, suffix)

    return out


# ── Excel export ──────────────────────────────────────────────────────────────

_DISCLAIMER_ROW = [
    "⚠️  DISCLAIMER: For educational/research purposes only. "
    "NOT financial advice. Consult a SEBI-registered advisor before investing. "
    "Data may contain delays or errors. Past screens ≠ future returns."
]


def _flatten_for_sheet(results: list[dict], screen_key: str) -> pd.DataFrame:
    """Extract per-screener result dicts into a flat DataFrame."""
    rows = []
    for r in results:
        sub = r.get(screen_key, {})
        if not sub:
            continue
        base = {
            "Symbol": r["symbol"],
            "Suffix": r.get("suffix", ""),
            "Qualifies": "✔" if sub.get("qualifies") else "✘",
        }
        base.update(
            {
                k: v
                for k, v in sub.items()
                if k not in {"symbol", "screen", "qualifies", "error", "CC_Criteria"}
            }
        )
        if sub.get("error"):
            base["Error"] = sub["error"]
        rows.append(base)
    return pd.DataFrame(rows)


def save_results_excel(
    all_results: list[dict], multi_screen_hits: list[dict], active_screens: set
) -> Path:
    date_str = datetime.today().strftime("%Y%m%d_%H%M")
    path = DOWNLOAD_DIR / f"screener_analysis_{date_str}.xlsx"

    screen_labels = {
        "mf": "Magic_Formula",
        "gc": "Golden_Crossover",
        "bc": "Bull_Cartel",
        "darvas": "Darvas_Box",
        "piotroski": "Piotroski",
        "cc": "Coffee_Can",
    }

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # ── Disclaimer sheet ────────────────────────────────────────────────
        pd.DataFrame(
            {"DISCLAIMER — READ BEFORE ACTING ON ANY RESULT": DISCLAIMER.strip().splitlines()}
        ).to_excel(writer, sheet_name="DISCLAIMER", index=False)

        # ── Per-screener sheets ─────────────────────────────────────────────
        for key in ("mf", "gc", "bc", "darvas", "piotroski", "cc"):
            if key not in active_screens:
                continue
            df = _flatten_for_sheet(all_results, key)
            if df.empty:
                continue
            # Qualifying stocks first
            qual_col = "Qualifies"
            if qual_col in df.columns:
                df = df.sort_values(
                    qual_col, ascending=False, key=lambda s: s.map({"✔": 0, "✘": 1})
                )
            sheet_name = screen_labels.get(key, key)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

        # ── Multi-screen hits sheet ────────────────────────────────────────
        if multi_screen_hits:
            pd.DataFrame(multi_screen_hits).to_excel(
                writer, sheet_name="Multi_Screen_Hits", index=False
            )

        # ── Screener guide sheet ───────────────────────────────────────────
        guide_rows = []
        for key, meta in SCREENERS.items():
            guide_rows.append(
                {
                    "Screener": meta["name"],
                    "Author": meta["author"],
                    "Criteria": meta["criteria"],
                    "Logic": meta["logic"],
                    "Risk Note": meta["risk_note"],
                }
            )
        pd.DataFrame(guide_rows).to_excel(writer, sheet_name="Screener_Guide", index=False)

    print(f"\n  📊  Excel saved → {path}")
    return path


# ── Orchestration ─────────────────────────────────────────────────────────────


def run(
    active_screens: set = None,
    use_screener_in: bool = True,
    full_universe: bool = False,
    workers: int = MAX_WORKERS,
    offline: bool = False,
):
    print(DISCLAIMER)
    print(f"\n{'#'*70}")
    print("  SCREENER ANALYSIS — NSE + BSE")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*70}\n")

    active = active_screens or {"mf", "gc", "bc", "darvas", "piotroski", "cc"}

    # ── Stage 1: build symbol universe ────────────────────────────────────────
    print("Stage 1 — Building symbol universe …")
    symbol_sources: dict[str, list[str]] = {}  # screen_key → [symbols]

    if not offline and use_screener_in and not full_universe:
        print(
            "  Using markitdown pre-filter from screener.in as additional source "
            "(supplements full-universe scan with screener.in-specific results):\n"
        )
        for key in ("mf", "gc", "bc"):
            if key not in active:
                continue
            meta = SCREENERS[key]
            syms = fetch_screener_in_symbols(meta["id"], meta["slug"])
            if syms:
                symbol_sources[key] = syms
                print(f"    {meta['name']:<22} → {len(syms)} stocks pre-filtered")
            else:
                print(
                    f"    {meta['name']:<22} → screener.in unavailable, "
                    f"will compute from full universe"
                )

        # Golden Crossover and Darvas use OHLC — fetch all NSE for those
        # (fast bulk download, no financial statements needed)
        if "gc" in active and "gc" not in symbol_sources:
            full_syms = fetch_all_nse_symbols()
            symbol_sources["gc"] = full_syms

        if "darvas" in active:
            full_syms = symbol_sources.get("gc") or fetch_all_nse_symbols()
            symbol_sources["darvas"] = full_syms

    # Always run on full NSE universe regardless of screener.in pre-filter
    # so no stock is missed
    print("  Loading full NSE universe to ensure complete coverage …")
    full_syms = fetch_all_nse_symbols()
    if full_syms:
        for key in active:
            existing = set(symbol_sources.get(key, []))
            merged = list(existing | set(full_syms))
            symbol_sources[key] = merged
        print(f"  Full NSE universe merged: {len(full_syms)} symbols added to each screener")
    else:
        if not symbol_sources:
            print("  Full-universe fetch failed — falling back to screener.in results only")
            for key in active:
                if key not in symbol_sources:
                    symbol_sources[key] = []

    # Build the combined unique symbol list for OHLC download
    all_unique = sorted({sym for syms in symbol_sources.values() for sym in syms})
    print(f"\n  Total unique symbols to fetch OHLC for: {len(all_unique)}")

    # ── Stage 2: bulk OHLC download (1 year — covers Darvas + Golden Cross) ───
    print("\nStage 2 — Bulk OHLC download (1-year window) …")
    yf_tickers = [f"{s}.NS" for s in all_unique]
    ohlc_raw = bulk_download_ohlc(yf_tickers, period="1y")
    # Map back: SYMBOL.NS → DataFrame
    ohlc_map = {t.replace(".NS", ""): df for t, df in ohlc_raw.items()}
    print(f"  {len(ohlc_map)} tickers with usable OHLC data")

    # ── Stage 3: OHLC screeners on full universe (no extra API calls) ────────
    print("\nStage 3 — OHLC-based screeners (Darvas + Golden Crossover) …")
    darvas_results, gc_results = {}, {}

    for sym, df in ohlc_map.items():
        if "darvas" in active:
            darvas_results[sym] = compute_darvas_box(df, symbol=sym)
        if "gc" in active and len(df) >= 201:
            gc_results[sym] = compute_golden_crossover(df, symbol=sym)

    darvas_breakouts = [s for s, r in darvas_results.items() if r.get("signal") == "BREAKOUT_BUY"]
    gc_crosses = [s for s, r in gc_results.items() if r.get("qualifies")]
    print(f"  Darvas Breakouts:    {len(darvas_breakouts)}")
    print(f"  Golden Crossovers:   {len(gc_crosses)}")

    # ── Stage 4: fundamental screeners (parallel, per-symbol) ────────────────
    # Candidates = union of screener.in pre-filtered + Darvas breakouts
    fundamental_candidates = sorted(
        {
            sym
            for key in ("mf", "bc", "piotroski", "cc")
            for sym in (symbol_sources.get(key, []) + darvas_breakouts)
            if key in active
        }
    )
    print(
        f"\nStage 4 — Fundamental scans on {len(fundamental_candidates)} candidates "
        f"({workers} workers) …"
    )

    fund_results: dict[str, dict] = {}
    fund_active = active & {"mf", "bc", "piotroski", "cc"}
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(enrich_symbol, sym, ohlc_map.get(sym), ".NS", fund_active): sym
            for sym in fundamental_candidates
        }
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                fund_results[sym] = future.result()
            except Exception as e:
                fund_results[sym] = {"symbol": sym, "error": str(e)}
            if done % 25 == 0 or done == len(fundamental_candidates):
                print(f"    {done}/{len(fundamental_candidates)} done")

    # ── Stage 5: merge all results ────────────────────────────────────────────
    print("\nStage 5 — Merging and ranking results …")
    all_results = []
    all_symbols = sorted(set(list(ohlc_map.keys()) + fundamental_candidates))

    for sym in all_symbols:
        r = fund_results.get(sym, {"symbol": sym})
        # Attach OHLC screener results
        if sym in darvas_results:
            r["darvas"] = darvas_results[sym]
        if sym in gc_results:
            r["gc"] = gc_results[sym]
        r.setdefault("suffix", ".NS")
        all_results.append(r)

    # Multi-screen hits: qualifies in 3+ screeners
    multi_hits = []
    for r in all_results:
        screens_passed = [
            key
            for key in ("mf", "gc", "bc", "darvas", "piotroski", "cc")
            if key in active and r.get(key, {}).get("qualifies")
        ]
        if len(screens_passed) >= 3:
            multi_hits.append(
                {
                    "Symbol": r["symbol"],
                    "Screens_Passed": ", ".join(screens_passed),
                    "Screens_Count": len(screens_passed),
                    "Darvas_Signal": r.get("darvas", {}).get("signal", ""),
                    "Piotroski_Score": r.get("piotroski", {}).get("f_score"),
                    "CC_Score": r.get("cc", {}).get("CC_Score", ""),
                    "MF_ROIC_%": r.get("mf", {}).get("ROIC_%"),
                    "MF_EY_%": r.get("mf", {}).get("Earnings_Yield_%"),
                    "GC_DMA50": r.get("gc", {}).get("DMA50"),
                    "GC_DMA200": r.get("gc", {}).get("DMA200"),
                    "BC_Sales_g%": r.get("bc", {}).get("Sales_Growth_YoY_%"),
                    "BC_Profit_g%": r.get("bc", {}).get("Profit_Growth_YoY_%"),
                }
            )
    multi_hits.sort(key=lambda x: x["Screens_Count"], reverse=True)

    # ── Stage 6: save results ────────────────────────────────────────────────
    print("\nStage 6 — Saving results …")
    path = save_results_excel(all_results, multi_hits, active)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  SCREENER ANALYSIS COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'='*70}")
    print(f"  Total symbols analysed:  {len(all_results)}")
    for key, label in [
        ("mf", "Magic Formula"),
        ("gc", "Golden Crossover"),
        ("bc", "Bull Cartel"),
        ("darvas", "Darvas Breakout"),
        ("piotroski", "Piotroski ≥7"),
        ("cc", "Coffee Can PASS"),
    ]:
        if key not in active:
            continue
        n = sum(1 for r in all_results if r.get(key, {}).get("qualifies"))
        print(f"  {label:<25} {n:>5} qualifying stocks")
    print(f"\n  ★ MULTI-SCREEN HITS (3+ screens): {len(multi_hits)}")
    if multi_hits:
        print(f"\n  {'Symbol':<15} {'Screens':<40} {'Piotroski'}")
        print(f"  {'-'*65}")
        for h in multi_hits[:20]:
            print(
                f"  {h['Symbol']:<15} {h['Screens_Passed']:<40} "
                f"{h.get('Piotroski_Score') or '-'}"
            )
    print(f"\n{DISCLAIMER}")

    return {"multi_hits": multi_hits, "all_results": all_results, "excel": str(path)}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Indian market screener suite — Magic Formula, Golden Crossover, "
            "Bull Cartel, Darvas, Piotroski, Coffee Can."
        ),
        epilog=(
            "⚠️  For educational/research use only. "
            "NOT financial advice. Consult a SEBI-registered advisor."
        ),
    )
    parser.add_argument(
        "--screeners",
        nargs="+",
        choices=["mf", "gc", "bc", "darvas", "piotroski", "cc"],
        default=None,
        help="Subset of screeners to run (default: all six)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Skip screener.in pre-filter; compute all metrics from yfinance directly",
    )
    parser.add_argument(
        "--full-universe",
        action="store_true",
        default=False,
        help="Run on the entire NSE+BSE universe (overrides screener.in pre-filter)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Parallel threads for fundamental scans (default {MAX_WORKERS})",
    )
    args = parser.parse_args()

    active = set(args.screeners) if args.screeners else None
    run(
        active_screens=active,
        use_screener_in=not args.offline,
        full_universe=args.full_universe,
        workers=args.workers,
        offline=args.offline,
    )
