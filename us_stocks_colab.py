# us_stocks_colab.py
# ===================
# Combined Google Colab notebook script:
#   • Per-stock daily report (quote, valuation, options, insiders, news)
#   • Batch reports (Dow30, Nasdaq50, or any custom list)
#   • Full NASDAQ + NYSE/AMEX market screener with SQLite checkpoint
#   • Three scans on shared engine: Darvas Box, Piotroski F-Score, Coffee Can
#
# ── Colab quick-start ─────────────────────────────────────────────────────────
#   !pip install yfinance pandas openpyxl tqdm requests "markitdown[all]"
#   exec(open('/content/us_stocks_colab.py').read())
#
#   # Single stock
#   run("AAPL", run_scans=True, show_options=True)
#
#   # Just the three scans
#   run_scans_only("NVDA")
#
#   # Curated batches
#   run_batch(symbols=DOW_JONES_30, run_scans=True)
#   run_batch(symbols=NASDAQ_50, output_format="json")
#
#   # Full US market scan (~6,500 stocks, NASDAQ + NYSE + AMEX)
#   scan_all_us_stocks()                              # full run, all 3 scans
#   scan_all_us_stocks(darvas_only=True)              # fast (~5 min) — Darvas only
#   scan_all_us_stocks(resume=True)                   # resume after interrupt
#   scan_all_us_stocks(limit=200)                     # quick test on first 200

# ── Standard library ─────────────────────────────────────────────────────────
import io
import json
import logging
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ── Third-party ──────────────────────────────────────────────────────────────
import pandas as pd
import requests

try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance pandas openpyxl tqdm requests")

try:
    from tqdm import tqdm

    TQDM_OK = True
except ImportError:
    TQDM_OK = False
    print("tip: pip install tqdm  for progress bars")


# ── Constants ────────────────────────────────────────────────────────────────
# Colab default working directory is /content; fall back to ./ elsewhere.
BASE_DIR = Path("/content") if Path("/content").exists() else Path(".")
DOWNLOAD_DIR = BASE_DIR / "us_stock_data"
OUTPUT_DIR = BASE_DIR / "us_screener_output"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = OUTPUT_DIR / "screener.db"

DARVAS_CONFIRM = 3  # consecutive days a high/low must hold to confirm a box
PIOTROSKI_STRONG = 7  # F-Score >= this -> STRONG
COFFEE_CAN_CRITERIA = 6  # total criteria in Coffee Can screen

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("us_stocks")

# Dow Jones Industrial Average (30 components, 2025-26 composition).
DOW_JONES_30 = [
    "AAPL",
    "AMGN",
    "AMZN",
    "AXP",
    "BA",
    "CAT",
    "CRM",
    "CSCO",
    "CVX",
    "DIS",
    "DOW",
    "GS",
    "HD",
    "HON",
    "IBM",
    "JNJ",
    "JPM",
    "KO",
    "MCD",
    "MMM",
    "MRK",
    "MSFT",
    "NKE",
    "NVDA",
    "PG",
    "TRV",
    "UNH",
    "V",
    "VZ",
    "WMT",
]

# NASDAQ 50 — top 50 NASDAQ-100 components by market weight (2025-26).
NASDAQ_50 = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
    "GOOG",
    "AVGO",
    "COST",
    "NFLX",
    "AMD",
    "ADBE",
    "QCOM",
    "AMAT",
    "MU",
    "CSCO",
    "INTU",
    "TXN",
    "AMGN",
    "HON",
    "SBUX",
    "GILD",
    "ADI",
    "VRTX",
    "REGN",
    "ISRG",
    "PANW",
    "CRWD",
    "MELI",
    "LRCX",
    "KLAC",
    "SNPS",
    "CDNS",
    "ORLY",
    "FTNT",
    "MRVL",
    "WDAY",
    "PCAR",
    "MNST",
    "ODFL",
    "FAST",
    "PAYX",
    "BIIB",
    "IDXX",
    "VRSK",
    "EXC",
    "CEG",
    "CSGP",
    "APP",
]


# ── Formatting helpers ───────────────────────────────────────────────────────


def fmt(val, prefix="$", decimals=2):
    try:
        return f"{prefix}{float(val):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(val) if val else "N/A"


def fmt_large(val, prefix="$"):
    try:
        v = float(val)
        if abs(v) >= 1e12:
            return f"{prefix}{v/1e12:.2f}T"
        elif abs(v) >= 1e9:
            return f"{prefix}{v/1e9:.2f}B"
        elif abs(v) >= 1e6:
            return f"{prefix}{v/1e6:.2f}M"
        elif abs(v) >= 1e3:
            return f"{prefix}{v/1e3:.2f}K"
        else:
            return f"{prefix}{v:.2f}"
    except (TypeError, ValueError):
        return "N/A"


def pct(val):
    try:
        v = float(val)
        arrow = "▲" if v >= 0 else "▼"
        color = "\033[92m" if v >= 0 else "\033[91m"
        return f"{color}{arrow} {abs(v):.2f}%\033[0m"
    except (TypeError, ValueError):
        return "N/A"


def section(title):
    w = 60
    print(f"\n{'-' * w}\n  {title.upper()}\n{'-' * w}")


def row(label, value, width=30):
    print(f"  {label:<{width}} {value}")


# ── yfinance helpers ─────────────────────────────────────────────────────────


def _get_ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol.upper().strip())


def _first_df(obj, *attrs):
    """
    Return first non-None, non-empty DataFrame from `obj`'s attributes.
    Never use `or` between DataFrames — raises on ambiguous truth value.
    """
    for attr in attrs:
        df = getattr(obj, attr, None)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return None


def _yf_financials(ticker: yf.Ticker):
    inc = _first_df(ticker, "income_stmt", "financials")
    bal = _first_df(ticker, "balance_sheet")
    cf = _first_df(ticker, "cash_flow", "cashflow")
    return inc, bal, cf


def _val(df, *row_names, col: int = 0):
    """Safely fetch a scalar from a yfinance financial DataFrame."""
    if df is None or df.empty:
        return None
    for name in row_names:
        if name in df.index:
            try:
                v = df.loc[name].iloc[col]
                return float(v) if pd.notna(v) else None
            except (IndexError, TypeError, ValueError):
                pass
    return None


# ── Data-fetch functions (single stock) ──────────────────────────────────────


def fetch_quote(ticker: yf.Ticker) -> dict:
    fi = ticker.fast_info
    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        pass

    def fi_get(attr):
        try:
            v = getattr(fi, attr, None)
            return v if v is not None else None
        except Exception:
            return None

    return {
        "lastPrice": fi_get("last_price")
        or info.get("currentPrice")
        or info.get("regularMarketPrice"),
        "previousClose": fi_get("previous_close") or info.get("previousClose"),
        "open": fi_get("open") or info.get("open"),
        "dayHigh": fi_get("day_high") or info.get("dayHigh"),
        "dayLow": fi_get("day_low") or info.get("dayLow"),
        "volume": fi_get("last_volume") or info.get("volume"),
        "avgVolume": fi_get("three_month_average_volume") or info.get("averageVolume"),
        "marketCap": fi_get("market_cap") or info.get("marketCap"),
        "52wHigh": fi_get("year_high") or info.get("fiftyTwoWeekHigh"),
        "52wLow": fi_get("year_low") or info.get("fiftyTwoWeekLow"),
        "ytdChange": fi_get("year_change"),
        "50dAvg": fi_get("fifty_day_average") or info.get("fiftyDayAverage"),
        "200dAvg": fi_get("two_hundred_day_average") or info.get("twoHundredDayAverage"),
        "currency": fi_get("currency") or info.get("currency", "USD"),
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "trailingEps": info.get("trailingEps"),
        "forwardEps": info.get("forwardEps"),
        "pegRatio": info.get("pegRatio"),
        "priceToBook": info.get("priceToBook"),
        "dividendYield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "shortRatio": info.get("shortRatio"),
        "targetMean": info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationKey", "—").upper(),
        "analystCount": info.get("numberOfAnalystOpinions"),
        "name": info.get("shortName") or info.get("longName", "—"),
        "exchange": info.get("exchange") or fi_get("exchange") or "—",
        "sector": info.get("sector", "—"),
        "industry": info.get("industry", "—"),
        "country": info.get("country", "US"),
    }


def fetch_historical(ticker: yf.Ticker, period: str = "1mo") -> pd.DataFrame:
    try:
        df = ticker.history(period=period, auto_adjust=True)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        print(f"  [Historical error] {e}")
        return pd.DataFrame()


def fetch_corporate_actions(ticker: yf.Ticker) -> dict:
    result = {"dividends": [], "splits": [], "calendar": {}}
    try:
        acts = ticker.actions
        if acts is not None and not acts.empty:
            div = acts[acts["Dividends"] > 0]["Dividends"]
            spl = acts[acts["Stock Splits"] > 0]["Stock Splits"]
            result["dividends"] = [
                {"date": str(d.date()), "amount": v} for d, v in div.tail(5).items()
            ]
            result["splits"] = [{"date": str(d.date()), "ratio": v} for d, v in spl.tail(3).items()]
    except Exception:
        pass

    try:
        cal = ticker.calendar or {}
        earnings_dates = cal.get("Earnings Date", [])
        result["calendar"] = {
            "next_earnings": str(earnings_dates[0]) if earnings_dates else "—",
            "ex_dividend_date": str(cal.get("Ex-Dividend Date", "—")),
            "dividend_date": str(cal.get("Dividend Date", "—")),
            "eps_est_avg": cal.get("Earnings Average"),
            "eps_est_low": cal.get("Earnings Low"),
            "eps_est_high": cal.get("Earnings High"),
            "rev_est_avg": cal.get("Revenue Average"),
        }
    except Exception:
        pass

    return result


def fetch_options_data(ticker: yf.Ticker) -> dict:
    """
    Nearest-expiry options chain: PCR and Max Pain.

    Max Pain = strike that minimises aggregate market-maker payout,
      pain(P) = sum_K max(0, P-K)*call_OI(K) + sum_K max(0, K-P)*put_OI(K)
    """
    try:
        expiries = ticker.options
        if not expiries:
            return {}
        chain = ticker.option_chain(expiries[0])
        calls, puts = chain.calls.copy(), chain.puts.copy()

        call_oi_total = calls["openInterest"].fillna(0).sum()
        put_oi_total = puts["openInterest"].fillna(0).sum()
        pcr = put_oi_total / call_oi_total if call_oi_total > 0 else None

        call_oi = dict(zip(calls["strike"], calls["openInterest"].fillna(0)))
        put_oi = dict(zip(puts["strike"], puts["openInterest"].fillna(0)))
        all_strikes = sorted(set(call_oi) | set(put_oi))

        pain = {}
        for candidate in all_strikes:
            c_pain = sum(max(0.0, candidate - k) * v for k, v in call_oi.items())
            p_pain = sum(max(0.0, k - candidate) * v for k, v in put_oi.items())
            pain[candidate] = c_pain + p_pain

        max_pain_strike = min(pain, key=pain.get) if pain else None
        return {
            "expiry_date": expiries[0],
            "call_oi": int(call_oi_total),
            "put_oi": int(put_oi_total),
            "pcr": round(pcr, 3) if pcr else None,
            "max_pain": round(max_pain_strike, 2) if max_pain_strike else None,
            "sentiment": "Bullish (PCR>1)" if (pcr and pcr > 1) else "Bearish (PCR≤1)",
        }
    except Exception as e:
        print(f"  [Options error] {e}")
        return {}


def fetch_insider_trades(ticker: yf.Ticker) -> list:
    try:
        df = ticker.insider_transactions
        if df is None or df.empty:
            return []
        df = df.sort_values("Start Date", ascending=False).head(5)
        return [
            {
                "insider": r.get("Insider", "—"),
                "position": r.get("Position", "—"),
                "transaction": r.get("Transaction", "—"),
                "shares": r.get("Shares"),
                "value": r.get("Value"),
                "date": str(r.get("Start Date", "—")),
            }
            for _, r in df.iterrows()
        ]
    except Exception as e:
        print(f"  [Insider trades error] {e}")
        return []


def fetch_institutional_holders(ticker: yf.Ticker) -> list:
    try:
        df = ticker.institutional_holders
        if df is None or df.empty:
            return []
        return df.head(5).to_dict(orient="records")
    except Exception as e:
        print(f"  [Institutional holders error] {e}")
        return []


def fetch_news(ticker: yf.Ticker) -> list:
    try:
        news = ticker.news or []
        result = []
        for item in news[:5]:
            content = item.get("content") or item
            title = content.get("title") if isinstance(content, dict) else item.get("title", "")
            pub = content.get("pubDate") or item.get("providerPublishTime", "")
            if isinstance(pub, (int, float)):
                pub = datetime.utcfromtimestamp(pub).strftime("%Y-%m-%d %H:%M")
            result.append({"title": title, "published": str(pub)[:16]})
        return result
    except Exception as e:
        print(f"  [News error] {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# SCAN 1 — DARVAS BOX
# ═════════════════════════════════════════════════════════════════════════════


def _darvas_core(highs, lows, closes, confirm: int = DARVAS_CONFIRM) -> dict:
    """
    Shared Darvas Box engine.

    KEY RULE: box formation uses ONLY historical bars (all bars except the
    last). The current bar is excluded so a breakdown bar can't pull the box
    bottom down and mask the BREAKDOWN_SELL signal.
    """
    if len(closes) < confirm + 5:
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": f"Need >= {confirm + 5} bars; got {len(closes)}",
        }

    current = closes[-1]
    highs_h, lows_h = highs[:-1], lows[:-1]
    n = len(highs_h)

    # Step 1: most recent confirmed box top
    box_top_idx = box_top = None
    for i in range(n - confirm - 1, -1, -1):
        c = highs_h[i]
        if c == 0:
            continue
        window = highs_h[i + 1 : i + 1 + confirm]
        if len(window) == confirm and all(h < c for h in window):
            box_top_idx, box_top = i, c
            break

    if box_top is None:
        return {
            "signal": "NO_BOX",
            "box_top": None,
            "box_bottom": None,
            "note": "No confirmed box top in look-back window",
        }

    # Step 2: confirmed box bottom from box-top day forward
    seg = lows_h[box_top_idx:]
    box_bottom = None
    for i in range(len(seg) - confirm):
        c = seg[i]
        if c == 0:
            continue
        window = seg[i + 1 : i + 1 + confirm]
        if len(window) == confirm and all(l > c for l in window):
            box_bottom = c
            break
    if box_bottom is None:
        valid = [l for l in seg if l > 0]
        box_bottom = min(valid) if valid else None

    if box_bottom is None:
        return {
            "signal": "NO_BOX",
            "box_top": round(box_top, 2),
            "box_bottom": None,
            "note": "Could not confirm a box bottom",
        }

    # Step 3: classify today's close
    if current > box_top:
        signal = "BREAKOUT_BUY"
    elif current < box_bottom:
        signal = "BREAKDOWN_SELL"
    else:
        signal = "IN_BOX"

    rng = box_top - box_bottom
    pos_in_box = ((current - box_bottom) / rng * 100) if rng else 0
    upside_pct = ((box_top - current) / current * 100) if current else 0

    return {
        "signal": signal,
        "box_top": round(box_top, 2),
        "box_bottom": round(box_bottom, 2),
        "current_price": round(current, 2),
        "box_range": round(rng, 2),
        "position_in_box_pct": round(pos_in_box, 1),
        "upside_to_top_pct": round(upside_pct, 2),
        "upside_pct": round(upside_pct, 2),  # screener alias
        "pos_in_box": round(pos_in_box, 1),  # screener alias
        "confirm_days": confirm,
        "data_points": len(closes),
    }


def compute_darvas_box(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """Detect Darvas Box from an OHLC DataFrame (e.g. from ticker.history())."""
    if df is None or df.empty:
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": "Empty OHLC DataFrame",
        }

    def find_col(df, candidates):
        for c in candidates:
            match = next((col for col in df.columns if c.upper() in col.upper()), None)
            if match:
                return match
        return None

    h_col = find_col(df, ["High"])
    l_col = find_col(df, ["Low"])
    c_col = find_col(df, ["Close"])
    if not all([h_col, l_col, c_col]):
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": f"Could not identify OHLC columns in {list(df.columns)}",
        }

    highs = pd.to_numeric(df[h_col], errors="coerce").fillna(0).tolist()
    lows = pd.to_numeric(df[l_col], errors="coerce").fillna(0).tolist()
    closes = pd.to_numeric(df[c_col], errors="coerce").fillna(0).tolist()
    return _darvas_core(highs, lows, closes, confirm)


def display_darvas_box(result: dict):
    section("Darvas Box Scan")
    sig = result.get("signal", "N/A")
    labels = {
        "BREAKOUT_BUY": "\033[92m● BREAKOUT BUY\033[0m  — close above box top",
        "BREAKDOWN_SELL": "\033[91m● BREAKDOWN SELL\033[0m — close below box bottom",
        "IN_BOX": "\033[93m● IN BOX\033[0m        — consolidating",
        "NO_BOX": "No confirmed Darvas box in look-back window",
        "INSUFFICIENT_DATA": "Insufficient data",
    }
    print(f"\n  Signal: {labels.get(sig, sig)}")
    if result.get("box_top"):
        print()
        row("Box Top", fmt(result["box_top"]))
        row("Box Bottom", fmt(result.get("box_bottom")))
        row("Current Price", fmt(result.get("current_price")))
        row("Box Range", fmt(result.get("box_range"), prefix="$"))
        row("Position in Box", f"{result.get('position_in_box_pct', 0):.1f}%")
        row("Upside to Top", f"{result.get('upside_to_top_pct', 0):.2f}%")
        row("Confirmation (days)", str(result.get("confirm_days", DARVAS_CONFIRM)))
        row("OHLC bars used", str(result.get("data_points", "—")))
    elif result.get("note"):
        print(f"  Note: {result['note']}")


# ═════════════════════════════════════════════════════════════════════════════
# SCAN 2 — PIOTROSKI F-SCORE
# ═════════════════════════════════════════════════════════════════════════════


def compute_piotroski_score(ticker: yf.Ticker, symbol: str = "") -> dict:
    """
    Piotroski F-Score (0-9) from annual statements.
      Profitability (4): ROA>0, OCF>0, dROA>0, accruals quality
      Leverage (3):      dLT-debt<0, dCurrent-ratio>0, no dilution
      Efficiency (2):    dGross-margin>0, dAsset-turnover>0
      >=7 STRONG, 4-6 MODERATE, <=3 WEAK
    """
    inc, bal, cf = _yf_financials(ticker)
    if inc is None or inc.empty:
        return {"symbol": symbol, "error": "No income statement data available"}

    s, d = {}, {}

    ni0 = _val(inc, "Net Income", col=0)
    ni1 = _val(inc, "Net Income", col=1)
    a0 = _val(bal, "Total Assets", col=0)
    a1 = _val(bal, "Total Assets", col=1)
    roa0 = (ni0 / a0) if (ni0 and a0) else None
    roa1 = (ni1 / a1) if (ni1 and a1) else None

    s["F1_ROA_positive"] = 1 if (roa0 and roa0 > 0) else 0
    d["ROA_current_%"] = round(roa0 * 100, 2) if roa0 else "N/A"

    ocf0 = _val(cf, "Operating Cash Flow", "Total Cash From Operating Activities", col=0)
    s["F2_OCF_positive"] = 1 if (ocf0 and ocf0 > 0) else 0
    d["OCF_current_$M"] = round(ocf0 / 1e6, 1) if ocf0 else "N/A"

    s["F3_ROA_improving"] = 1 if (roa0 is not None and roa1 is not None and roa0 > roa1) else 0
    d["ROA_prev_%"] = round(roa1 * 100, 2) if roa1 else "N/A"

    s["F4_Accruals"] = 1 if (ocf0 and a0 and roa0 is not None and (ocf0 / a0) > roa0) else 0

    ltd0 = _val(bal, "Long Term Debt", col=0) or 0
    ltd1 = _val(bal, "Long Term Debt", col=1) or 0
    lev0 = (ltd0 / a0) if a0 else None
    lev1 = (ltd1 / a1) if a1 else None
    s["F5_Leverage_down"] = 1 if (lev0 is not None and lev1 is not None and lev0 < lev1) else 0
    d["LTD_ratio_curr_%"] = round(lev0 * 100, 2) if lev0 else "N/A"
    d["LTD_ratio_prev_%"] = round(lev1 * 100, 2) if lev1 else "N/A"

    ca0 = _val(bal, "Current Assets", "Total Current Assets", col=0)
    cl0 = _val(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca1 = _val(bal, "Current Assets", "Total Current Assets", col=1)
    cl1 = _val(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    cr0 = (ca0 / cl0) if (ca0 and cl0) else None
    cr1 = (ca1 / cl1) if (ca1 and cl1) else None
    s["F6_CurrentRatio_up"] = 1 if (cr0 is not None and cr1 is not None and cr0 > cr1) else 0
    d["CurrentRatio_curr"] = round(cr0, 2) if cr0 else "N/A"
    d["CurrentRatio_prev"] = round(cr1, 2) if cr1 else "N/A"

    sh0 = _val(bal, "Share Issued", col=0)
    sh1 = _val(bal, "Share Issued", col=1)
    s["F7_No_dilution"] = (1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1
    d["Shares_curr_M"] = round(sh0 / 1e6, 1) if sh0 else "N/A"

    rev0 = _val(inc, "Total Revenue", col=0)
    gp0 = _val(inc, "Gross Profit", col=0)
    rev1 = _val(inc, "Total Revenue", col=1)
    gp1 = _val(inc, "Gross Profit", col=1)
    gm0 = (gp0 / rev0) if (gp0 and rev0) else None
    gm1 = (gp1 / rev1) if (gp1 and rev1) else None
    s["F8_GrossMargin_up"] = 1 if (gm0 is not None and gm1 is not None and gm0 > gm1) else 0
    d["GrossMargin_curr_%"] = round(gm0 * 100, 2) if gm0 else "N/A"
    d["GrossMargin_prev_%"] = round(gm1 * 100, 2) if gm1 else "N/A"

    at0 = (rev0 / a0) if (rev0 and a0) else None
    at1 = (rev1 / a1) if (rev1 and a1) else None
    s["F9_AssetTurnover_up"] = 1 if (at0 is not None and at1 is not None and at0 > at1) else 0
    d["AssetTurnover_curr"] = round(at0, 3) if at0 else "N/A"
    d["AssetTurnover_prev"] = round(at1, 3) if at1 else "N/A"

    total = sum(s.values())
    interp = (
        "STRONG — likely outperformer"
        if total >= 7
        else "MODERATE — neutral stance" if total >= 4 else "WEAK — avoid or short candidate"
    )
    return {
        "symbol": symbol,
        "f_score": total,
        "interpretation": interp,
        "component_scores": s,
        "details": d,
    }


def display_piotroski_score(result: dict):
    section("Piotroski F-Score")
    if "error" in result:
        print(f"  ⚠️  {result['error']}")
        return
    total = result["f_score"]
    color = "\033[92m" if total >= 7 else "\033[93m" if total >= 4 else "\033[91m"
    print(f"\n  Score: {color}{total}/9\033[0m  — {result['interpretation']}")
    print("\n  ── Component Scores ──────────────────────────────────────────")
    labels = {
        "F1_ROA_positive": "F1  ROA > 0",
        "F2_OCF_positive": "F2  Operating Cash Flow > 0",
        "F3_ROA_improving": "F3  ROA improving YoY",
        "F4_Accruals": "F4  Earnings cash-backed",
        "F5_Leverage_down": "F5  Long-term debt ratio ↓",
        "F6_CurrentRatio_up": "F6  Current ratio ↑",
        "F7_No_dilution": "F7  No new shares issued",
        "F8_GrossMargin_up": "F8  Gross margin ↑",
        "F9_AssetTurnover_up": "F9  Asset turnover ↑",
    }
    for key, label in labels.items():
        val = result["component_scores"].get(key, 0)
        tick = "\033[92m✔\033[0m" if val else "\033[91m✘\033[0m"
        print(f"    {tick}  {label}")
    print("\n  ── Key Financials ────────────────────────────────────────────")
    for k, v in result["details"].items():
        row(k.replace("_", " "), str(v))


# ═════════════════════════════════════════════════════════════════════════════
# SCAN 3 — COFFEE CAN (US-adapted)
# ═════════════════════════════════════════════════════════════════════════════


def compute_coffee_can(ticker: yf.Ticker, symbol: str = "") -> dict:
    """
    US Coffee Can — all 6 criteria must pass.
      C1 Revenue CAGR>10%   C2 ROE>15% avg     C3 Debt/Equity<1
      C4 MCap>=$1B          C5 No loss year    C6 Free Cash Flow>0
    """
    inc, bal, cf = _yf_financials(ticker)
    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        pass

    if inc is None or inc.empty:
        return {"symbol": symbol, "error": "No income statement data available"}

    def series(df, *rows):
        for name in rows:
            if df is not None and name in df.index:
                return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
        return []

    c, d = {}, {}

    revs = series(inc, "Total Revenue")
    if len(revs) >= 2:
        years = len(revs) - 1
        cagr = ((revs[0] / revs[-1]) ** (1 / years) - 1) * 100 if revs[-1] > 0 else None
        c["C1_Revenue_CAGR_gt10"] = 1 if (cagr and cagr > 10) else 0
        d["Revenue_CAGR_%"] = round(cagr, 2) if cagr else "N/A"
        d["Revenue_years"] = years
    else:
        c["C1_Revenue_CAGR_gt10"] = 0
        d["Revenue_CAGR_%"] = "N/A"

    ni_s = series(inc, "Net Income")
    eq_s = series(
        bal,
        "Stockholders Equity",
        "Total Stockholder Equity",
        "Total Equity Gross Minority Interest",
    )
    roe_list = [ni_s[i] / eq_s[i] * 100 for i in range(min(len(ni_s), len(eq_s))) if eq_s[i] > 0]
    avg_roe = sum(roe_list) / len(roe_list) if roe_list else None
    c["C2_ROE_gt15"] = 1 if (avg_roe and avg_roe > 15) else 0
    d["ROE_avg_%"] = round(avg_roe, 2) if avg_roe else "N/A"
    if roe_list:
        d["ROE_min_%"] = round(min(roe_list), 2)

    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        # yfinance returns D/E in percent in some builds (45.2 = 0.452x); normalise
        de = de_raw / 100 if de_raw > 10 else de_raw
        c["C3_LowDebt"] = 1 if de < 1 else 0
        d["Debt_to_Equity"] = round(de, 2)
    else:
        ltd_s = series(bal, "Long Term Debt")
        if ltd_s and eq_s and eq_s[0] != 0:
            de = ltd_s[0] / abs(eq_s[0])
            c["C3_LowDebt"] = 1 if de < 1 else 0
            d["Debt_to_Equity"] = round(de, 2)
        else:
            c["C3_LowDebt"] = 0
            d["Debt_to_Equity"] = "N/A"

    mcap = info.get("marketCap")
    try:
        mcap = mcap or ticker.fast_info.market_cap
    except Exception:
        pass
    c["C4_MCap_ge1B"] = 1 if (mcap and mcap >= 1e9) else 0
    d["Market_Cap"] = fmt_large(mcap) if mcap else "N/A"

    if ni_s:
        c["C5_NoProfitLoss"] = 1 if all(n > 0 for n in ni_s) else 0
        d["Loss_years"] = sum(1 for n in ni_s if n <= 0)
        d["Years_analysed"] = len(ni_s)
    else:
        c["C5_NoProfitLoss"] = 0

    fcf_s = series(cf, "Free Cash Flow")
    if fcf_s:
        c["C6_FreeCashFlow_pos"] = 1 if fcf_s[0] > 0 else 0
        d["FCF_latest_$M"] = round(fcf_s[0] / 1e6, 1)
    else:
        ocf_s = series(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex_s = series(cf, "Capital Expenditure", "Capital Expenditures")
        if ocf_s and capex_s:
            fcf = ocf_s[0] - abs(capex_s[0])
            c["C6_FreeCashFlow_pos"] = 1 if fcf > 0 else 0
            d["FCF_latest_$M"] = round(fcf / 1e6, 1)
        else:
            c["C6_FreeCashFlow_pos"] = 0
            d["FCF_latest_$M"] = "N/A"

    total = sum(c.values())
    return {
        "symbol": symbol,
        "qualifies": total == COFFEE_CAN_CRITERIA,
        "score": f"{total}/{COFFEE_CAN_CRITERIA}",
        "criteria": c,
        "details": d,
        "roe_avg": round(avg_roe, 2) if avg_roe else None,
    }


def display_coffee_can(result: dict):
    section("Coffee Can Portfolio Screen (US)")
    if "error" in result:
        print(f"  ⚠️  {result['error']}")
        return
    badge = (
        "\033[92m✔ QUALIFIES\033[0m" if result["qualifies"] else "\033[91m✘ DOES NOT QUALIFY\033[0m"
    )
    print(f"\n  Result: {badge}   ({result['score']} criteria met)")
    print("\n  ── Criteria ──────────────────────────────────────────────────")
    labels = {
        "C1_Revenue_CAGR_gt10": "C1  Revenue CAGR > 10%",
        "C2_ROE_gt15": "C2  ROE > 15% (avg)",
        "C3_LowDebt": "C3  Debt/Equity < 1",
        "C4_MCap_ge1B": "C4  Market Cap ≥ $1B",
        "C5_NoProfitLoss": "C5  No loss-making year",
        "C6_FreeCashFlow_pos": "C6  Free Cash Flow > 0",
    }
    for key, label in labels.items():
        val = result["criteria"].get(key, 0)
        tick = "\033[92m✔\033[0m" if val else "\033[91m✘\033[0m"
        print(f"    {tick}  {label}")
    print("\n  ── Supporting Data ───────────────────────────────────────────")
    for k, v in result["details"].items():
        row(k.replace("_", " "), str(v))


# ── Display functions for the daily report ───────────────────────────────────


def display_price_summary(quote: dict):
    section("Live Quote & Valuation")
    ltp = quote.get("lastPrice")
    prev = quote.get("previousClose")
    chg = (ltp - prev) if (ltp and prev) else None
    chg_pct = (chg / prev * 100) if (chg and prev) else None

    row("Company", quote.get("name", "—"))
    row("Exchange", quote.get("exchange", "—"))
    row("Sector", quote.get("sector", "—"))
    row("Industry", quote.get("industry", "—"))
    row("Country", quote.get("country", "—"))
    print()
    row("Last Price", fmt(ltp))
    row("Prev Close", fmt(prev))
    row("Change", f"{fmt(chg)}  {pct(chg_pct)}")
    row("Open", fmt(quote.get("open")))
    row("Day High", fmt(quote.get("dayHigh")))
    row("Day Low", fmt(quote.get("dayLow")))
    print()
    row("52-Week High", fmt(quote.get("52wHigh")))
    row("52-Week Low", fmt(quote.get("52wLow")))
    row(
        "YTD Change",
        pct((quote.get("ytdChange") or 0) * 100) if quote.get("ytdChange") is not None else "N/A",
    )
    row("50-Day Avg", fmt(quote.get("50dAvg")))
    row("200-Day Avg", fmt(quote.get("200dAvg")))
    print()
    row("Volume", f"{int(quote['volume']):,}" if quote.get("volume") else "N/A")
    row("Avg Volume (3m)", f"{int(quote['avgVolume']):,}" if quote.get("avgVolume") else "N/A")
    row("Market Cap", fmt_large(quote.get("marketCap")))
    print()
    row("Trailing P/E", fmt(quote.get("trailingPE"), prefix=""))
    row("Forward P/E", fmt(quote.get("forwardPE"), prefix=""))
    row("PEG Ratio", fmt(quote.get("pegRatio"), prefix=""))
    row("Price/Book", fmt(quote.get("priceToBook"), prefix=""))
    row("Trailing EPS", fmt(quote.get("trailingEps")))
    row("Forward EPS", fmt(quote.get("forwardEps")))
    row(
        "Dividend Yield",
        f"{quote['dividendYield']*100:.2f}%" if quote.get("dividendYield") else "N/A",
    )
    row("Beta", fmt(quote.get("beta"), prefix=""))
    row("Short Ratio", fmt(quote.get("shortRatio"), prefix=""))
    print()
    row("Analyst Target", fmt(quote.get("targetMean")))
    row("Recommendation", quote.get("recommendation", "—"))
    row("# Analysts", str(quote.get("analystCount") or "—"))


def display_corporate_actions(actions: dict):
    section("Corporate Actions & Earnings Calendar")
    cal = actions.get("calendar", {})
    row("Next Earnings", str(cal.get("next_earnings", "—")))
    row(
        "EPS Est (avg/lo/hi)",
        (
            (
                f"{fmt(cal.get('eps_est_avg'))} / "
                f"{fmt(cal.get('eps_est_low'))} / "
                f"{fmt(cal.get('eps_est_high'))}"
            )
            if cal.get("eps_est_avg")
            else "N/A"
        ),
    )
    row("Rev Est (avg)", fmt_large(cal.get("rev_est_avg")) if cal.get("rev_est_avg") else "N/A")
    row("Ex-Dividend Date", str(cal.get("ex_dividend_date", "—")))
    row("Dividend Pay Date", str(cal.get("dividend_date", "—")))

    divs = actions.get("dividends", [])
    print("\n  Recent Dividends:")
    if divs:
        for d in reversed(divs):
            print(f"    • {d['date']}  {fmt(d['amount'])} per share")
    else:
        print("    No dividend history.")

    splits = actions.get("splits", [])
    if splits:
        print("\n  Recent Stock Splits:")
        for s in reversed(splits):
            print(f"    • {s['date']}  {s['ratio']}:1 split")


def display_options_summary(opts: dict):
    section("Options Summary (Nearest Expiry)")
    if not opts:
        print("  Options data not available.")
        return
    row("Expiry Date", str(opts.get("expiry_date", "—")))
    row("Call OI", f"{opts.get('call_oi', 0):,}")
    row("Put OI", f"{opts.get('put_oi', 0):,}")
    row("Put-Call Ratio", f"{opts['pcr']:.3f}" if opts.get("pcr") else "N/A")
    row("Market Sentiment", opts.get("sentiment", "—"))
    row("Max Pain Strike", fmt(opts.get("max_pain")) if opts.get("max_pain") else "N/A")


def display_insider_trades(trades: list, symbol: str):
    section("Recent Insider Transactions")
    if not trades:
        print(f"  No recent insider transactions found for {symbol}.")
        return
    for t in trades:
        bs = t.get("transaction", "—")
        name = t.get("insider", "—")
        pos = t.get("position", "—")
        shs = f"{int(t['shares']):,}" if t.get("shares") else "—"
        val = fmt_large(t.get("value")) if t.get("value") else "—"
        date = str(t.get("date", "—"))[:10]
        print(f"  • {date}  {bs:<15} {shs:>12} shs ({val})  {name} [{pos}]")


def display_institutional_holders(holders: list):
    section("Top Institutional Holders")
    if not holders:
        print("  No institutional holder data available.")
        return
    for h in holders:
        name = h.get("Holder", "—")
        pct_h = h.get("pctHeld", 0)
        shs = h.get("Shares", 0)
        chg = h.get("pctChange", 0)
        chg_str = pct(chg * 100) if chg else "—"
        print(f"  • {name:<45} {pct_h*100:5.2f}%  {fmt_large(shs, prefix='')} shs  Δ {chg_str}")


def display_historical_summary(df: pd.DataFrame):
    section("Historical Summary (Last 30 Days)")
    if df is None or df.empty:
        print("  No historical data available.")
        return
    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(closes) < 2:
        print("  Insufficient closing-price data.")
        return
    ret_pct = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] * 100 if closes.iloc[0] else 0
    row("Trading days", str(len(closes)))
    row("Period High", fmt(closes.max()))
    row("Period Low", fmt(closes.min()))
    row("Avg Close", fmt(closes.mean()))
    row("Std Dev", fmt(closes.std(), prefix="$"))
    row("Period Return", pct(ret_pct))


def display_news(news: list, symbol: str):
    section(f"Latest News — {symbol}")
    if not news:
        print("  No news available.")
        return
    for item in news:
        print(f"  • [{item.get('published',''):<16}] {item.get('title','')}")


# ═════════════════════════════════════════════════════════════════════════════
# DAILY REPORT — single stock
# ═════════════════════════════════════════════════════════════════════════════


def run(
    symbol: str, show_options: bool = False, output_format: str = "text", run_scans: bool = False
) -> dict:
    """Generate a daily stock report for one US equity symbol."""
    symbol = symbol.upper().strip()
    w = 60
    print(f"\n{'=' * w}")
    print(f"  📊  US STOCK REPORT — {symbol}")
    print(f"  ⏱  Generated: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'=' * w}")

    ticker = _get_ticker(symbol)
    all_data = {"symbol": symbol, "timestamp": datetime.now().isoformat()}

    quote = fetch_quote(ticker)
    hist_df = fetch_historical(ticker, period="1mo")
    actions = fetch_corporate_actions(ticker)
    opts = fetch_options_data(ticker) if show_options else {}
    insiders = fetch_insider_trades(ticker)
    instit = fetch_institutional_holders(ticker)
    news = fetch_news(ticker)

    darvas_r = piotroski_r = coffee_r = {}
    if run_scans:
        print("  Running quantitative scans …")
        hist_6mo = fetch_historical(ticker, period="6mo")
        darvas_r = compute_darvas_box(hist_6mo)
        piotroski_r = compute_piotroski_score(ticker, symbol)
        coffee_r = compute_coffee_can(ticker, symbol)

    if output_format == "text":
        display_price_summary(quote)
        display_corporate_actions(actions)
        if show_options:
            display_options_summary(opts)
        display_historical_summary(hist_df)
        display_insider_trades(insiders, symbol)
        display_institutional_holders(instit)
        display_news(news, symbol)
        if run_scans:
            display_darvas_box(darvas_r)
            display_piotroski_score(piotroski_r)
            display_coffee_can(coffee_r)
        print(f"\n{'=' * w}")
        print("  Data sourced from Yahoo Finance via yfinance")
        print(f"{'=' * w}\n")
    elif output_format == "json":
        all_data.update(
            {
                "quote": quote,
                "historical_30d": (
                    hist_df.reset_index().to_dict(orient="records") if not hist_df.empty else []
                ),
                "corporate_actions": actions,
                "options": opts,
                "insider_trades": insiders,
                "institutional_holders": instit,
                "news": news,
                "scans": (
                    {
                        "darvas": darvas_r,
                        "piotroski": piotroski_r,
                        "coffee_can": coffee_r,
                    }
                    if run_scans
                    else {}
                ),
            }
        )
        out = DOWNLOAD_DIR / f"{symbol}_report_{datetime.today().strftime('%Y%m%d')}.json"
        out.write_text(json.dumps(all_data, indent=2, default=str))
        print(f"  📁  JSON saved → {out}")

    return all_data


def run_scans_only(symbol: str) -> dict:
    """Run only the three quantitative scans without the full daily report."""
    symbol = symbol.upper().strip()
    print(f"\n{'=' * 60}\n  🔍  SCANS — {symbol}\n{'=' * 60}")
    ticker = _get_ticker(symbol)
    hist_6mo = fetch_historical(ticker, period="6mo")
    darv = compute_darvas_box(hist_6mo)
    piotr = compute_piotroski_score(ticker, symbol)
    coff = compute_coffee_can(ticker, symbol)
    display_darvas_box(darv)
    display_piotroski_score(piotr)
    display_coffee_can(coff)
    return {"symbol": symbol, "darvas": darv, "piotroski": piotr, "coffee_can": coff}


# ═════════════════════════════════════════════════════════════════════════════
# BATCH REPORT — list of symbols (Dow30, Nasdaq50, custom)
# ═════════════════════════════════════════════════════════════════════════════


def run_batch(
    symbols: list = None,
    show_options: bool = False,
    output_format: str = "json",
    run_scans: bool = False,
) -> list:
    """Run reports for a list of symbols. Defaults to Dow Jones 30."""
    targets = symbols or DOW_JONES_30
    print(f"\n{'#' * 60}")
    print(f"  US BATCH REPORT — {len(targets)} stocks")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#' * 60}")

    results, failed = [], []
    for i, sym in enumerate(targets, 1):
        print(f"\n[{i:02d}/{len(targets)}] {sym}")
        try:
            data = run(
                sym, show_options=show_options, output_format=output_format, run_scans=run_scans
            )
            results.append(data)
        except Exception as e:
            print(f"  ❌  {sym} failed: {e}")
            failed.append(sym)

    _write_summary_csv(results, include_scans=run_scans)

    print(f"\n{'#' * 60}")
    print(f"  Done.  {len(results)} OK, {len(failed)} failed.")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
    print(f"{'#' * 60}\n")
    return results


def _write_summary_csv(results: list, include_scans: bool = False):
    rows = []
    for d in results:
        sym = d.get("symbol", "")
        q = d.get("quote", {})
        ltp = q.get("lastPrice")
        prev = q.get("previousClose")
        chg_pct = ((ltp - prev) / prev * 100) if (ltp and prev) else None
        base = {
            "Symbol": sym,
            "Name": q.get("name", ""),
            "Exchange": q.get("exchange", ""),
            "Sector": q.get("sector", ""),
            "Price": ltp,
            "Change%": round(chg_pct, 2) if chg_pct else None,
            "MarketCap": q.get("marketCap"),
            "TrailingPE": q.get("trailingPE"),
            "ForwardPE": q.get("forwardPE"),
            "Beta": q.get("beta"),
            "DivYield%": (
                round(q.get("dividendYield", 0) * 100, 3) if q.get("dividendYield") else None
            ),
            "Recommendation": q.get("recommendation"),
            "AnalystTarget": q.get("targetMean"),
            "52wHigh": q.get("52wHigh"),
            "52wLow": q.get("52wLow"),
            "Timestamp": d.get("timestamp"),
        }
        if include_scans:
            scans = d.get("scans", {})
            darv = scans.get("darvas", {})
            piofr = scans.get("piotroski", {})
            coff = scans.get("coffee_can", {})
            base.update(
                {
                    "Darvas_Signal": darv.get("signal"),
                    "Darvas_BoxTop": darv.get("box_top"),
                    "Darvas_BoxBottom": darv.get("box_bottom"),
                    "Piotroski_Score": piofr.get("f_score"),
                    "CoffeeCan": "YES" if coff.get("qualifies") else "NO",
                    "CoffeeCan_Score": coff.get("score"),
                }
            )
        rows.append(base)

    if rows:
        tag = "us_scan" if include_scans else "us_batch"
        out = DOWNLOAD_DIR / f"{tag}_summary_{datetime.today().strftime('%Y%m%d')}.csv"
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f"\n  📊  Summary CSV → {out}")


# ═════════════════════════════════════════════════════════════════════════════
# FULL MARKET SCREENER — NASDAQ + NYSE/AMEX (~6,500 stocks)
# ═════════════════════════════════════════════════════════════════════════════


def fetch_stock_universe(exchange_filter: str = "all") -> pd.DataFrame:
    """
    Download official NASDAQ Trader stock lists.
    Filters: Test Issue=N, ETF=N, Financial Status=N, common-share symbols only.
    """

    def _parse(url, is_nasdaq):
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = [l for l in r.text.strip().split("\n") if not l.startswith("File Creation")]
        df = pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str).fillna("")
        if is_nasdaq:
            df = df.rename(columns={"Security Name": "Name"})
            keep = (
                (df["Test Issue"] == "N")
                & (df["ETF"] == "N")
                & (df["Financial Status"] == "N")
                & df["Symbol"].str.match(r"^[A-Z]{1,5}$", na=False)
            )
            df["Exchange"] = "NASDAQ"
        else:
            df = df.rename(columns={"ACT Symbol": "Symbol", "Security Name": "Name"})
            keep = (
                (df["Test Issue"] == "N")
                & (df["ETF"] == "N")
                & df["Symbol"].str.match(r"^[A-Z]{1,5}$", na=False)
            )
            df["Exchange"] = (
                df.get("Exchange", pd.Series(["OTHER"] * len(df)))
                .map({"N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS"})
                .fillna("OTHER")
            )
        return df[keep][["Symbol", "Name", "Exchange"]].copy()

    frames = []
    if exchange_filter in ("all", "nasdaq"):
        print("  Fetching NASDAQ listed stocks …")
        frames.append(_parse(NASDAQ_URL, is_nasdaq=True))
    if exchange_filter in ("all", "nyse", "amex", "other"):
        print("  Fetching NYSE/AMEX/other listed stocks …")
        frames.append(_parse(OTHER_URL, is_nasdaq=False))
    return pd.concat(frames).drop_duplicates("Symbol").reset_index(drop=True)


def run_darvas_batch(symbols: list, batch_size: int = 500, pbar=None) -> dict:
    """Batch-download 6-month OHLC and compute Darvas signal per symbol."""
    results = {}
    batches = [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]

    for batch in batches:
        label = f"Darvas OHLC [{batch[0]}…]"
        try:
            raw = yf.download(
                tickers=" ".join(batch),
                period="6mo",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            log.warning("Batch download failed (%s): %s", label, e)
            if pbar:
                pbar.update(len(batch))
            continue

        for sym in batch:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if sym not in raw.columns.get_level_values(0):
                        results[sym] = {"signal": "NO_DATA", "box_top": None, "box_bottom": None}
                        continue
                    df = raw[sym].dropna()
                else:
                    df = raw.dropna()
                results[sym] = compute_darvas_box(df)
            except Exception as e:
                log.debug("Darvas error %s: %s", sym, e)
                results[sym] = {"signal": "ERROR", "box_top": None, "box_bottom": None}
            if pbar:
                pbar.update(1)
    return results


# ── SQLite checkpoint (resume support) ──────────────────────────────────────


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) a SQLite connection with safe pragmas for macOS."""
    con = sqlite3.connect(db_path, timeout=30.0)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _init_db(db_path: Path) -> sqlite3.Connection:
    # journal_mode=WAL is fragile on macOS Downloads (idle-handle disk I/O
    # errors). Use DELETE journal. Additionally, callers reopen the connection
    # right before each write phase since the handle goes idle for many
    # minutes during Phase 1's yfinance downloads.
    con = _open_db(db_path)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            symbol          TEXT PRIMARY KEY,
            name            TEXT,
            exchange        TEXT,
            sector          TEXT,
            industry        TEXT,
            market_cap      REAL,
            last_price      REAL,
            darvas_signal   TEXT,
            darvas_box_top  REAL,
            darvas_box_bot  REAL,
            darvas_current  REAL,
            darvas_upside   REAL,
            darvas_pos      REAL,
            f_score         INTEGER,
            f1 INTEGER, f2 INTEGER, f3 INTEGER, f4 INTEGER,
            f5 INTEGER, f6 INTEGER, f7 INTEGER, f8 INTEGER, f9 INTEGER,
            cc_qualifies    INTEGER,
            cc_score        TEXT,
            cc_c1 INTEGER, cc_c2 INTEGER, cc_c3 INTEGER,
            cc_c4 INTEGER, cc_c5 INTEGER, cc_c6 INTEGER,
            cc_roe_avg      REAL,
            scanned_at      TEXT,
            error           TEXT
        )
    """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS progress (
            symbol  TEXT PRIMARY KEY,
            phase   TEXT,
            done_at TEXT
        )
    """
    )
    con.commit()
    return con


def _already_done(con, phase: str) -> set:
    cur = con.execute("SELECT symbol FROM progress WHERE phase = ?", (phase,))
    return {r[0] for r in cur.fetchall()}


def _mark_done(con, symbol: str, phase: str, _no_commit: bool = False):
    con.execute(
        "INSERT OR REPLACE INTO progress VALUES (?,?,?)",
        (symbol, phase, datetime.now().isoformat()),
    )
    if not _no_commit:
        con.commit()


def _upsert_result(con, row: dict, _no_commit: bool = False):
    cols = ", ".join(row.keys())
    placeh = ", ".join(["?"] * len(row))
    update = ", ".join([f"{k} = excluded.{k}" for k in row if k != "symbol"])
    con.execute(
        f"INSERT INTO results ({cols}) VALUES ({placeh}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {update}",
        list(row.values()),
    )
    if not _no_commit:
        con.commit()


def _scan_fundamentals_one(symbol: str, name: str, exchange: str) -> dict:
    """Per-stock fundamentals worker (thread-safe)."""
    row = {
        "symbol": symbol,
        "name": name,
        "exchange": exchange,
        "scanned_at": datetime.now().isoformat(),
    }
    try:
        ticker = yf.Ticker(symbol)
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass

        row["sector"] = info.get("sector", "")
        row["industry"] = info.get("industry", "")
        row["market_cap"] = info.get("marketCap")
        try:
            row["last_price"] = ticker.fast_info.last_price
        except Exception:
            row["last_price"] = info.get("currentPrice") or info.get("regularMarketPrice")

        piotr = compute_piotroski_score(ticker, symbol)
        row["f_score"] = piotr.get("f_score")
        comps = piotr.get("component_scores", {})
        labels_f = [
            "F1_ROA_positive",
            "F2_OCF_positive",
            "F3_ROA_improving",
            "F4_Accruals",
            "F5_Leverage_down",
            "F6_CurrentRatio_up",
            "F7_No_dilution",
            "F8_GrossMargin_up",
            "F9_AssetTurnover_up",
        ]
        for i, key in enumerate(labels_f, 1):
            row[f"f{i}"] = comps.get(key)

        cc = compute_coffee_can(ticker, symbol)
        row["cc_qualifies"] = 1 if cc.get("qualifies") else 0
        row["cc_score"] = cc.get("score")
        labels_c = [
            "C1_Revenue_CAGR_gt10",
            "C2_ROE_gt15",
            "C3_LowDebt",
            "C4_MCap_ge1B",
            "C5_NoProfitLoss",
            "C6_FreeCashFlow_pos",
        ]
        crit = cc.get("criteria", {})
        for i, key in enumerate(labels_c, 1):
            row[f"cc_c{i}"] = crit.get(key)
        row["cc_roe_avg"] = cc.get("roe_avg")

        if piotr.get("error") and cc.get("error"):
            row["error"] = piotr["error"]

    except Exception as e:
        row["error"] = str(e)[:200]
        log.debug("Error scanning %s: %s", symbol, e)
    return row


def _compile_screener_outputs(con, today: str):
    """Read SQLite results and emit focused CSVs (breakouts, strong, triple-hits)."""
    df = pd.read_sql("SELECT * FROM results", con)
    if df.empty:
        print("  No results in database yet.")
        return

    out_summary = OUTPUT_DIR / f"scan_summary_{today}.csv"
    df.to_csv(out_summary, index=False)
    print(f"  Summary ({len(df):,} rows)       → {out_summary}")

    darvas_cols = [
        "symbol",
        "name",
        "exchange",
        "sector",
        "last_price",
        "darvas_signal",
        "darvas_box_top",
        "darvas_box_bot",
        "darvas_current",
        "darvas_upside",
        "darvas_pos",
    ]
    breakouts = df[df["darvas_signal"] == "BREAKOUT_BUY"][
        [c for c in darvas_cols if c in df.columns]
    ].sort_values("darvas_upside")
    out_darvas = OUTPUT_DIR / f"darvas_breakouts_{today}.csv"
    breakouts.to_csv(out_darvas, index=False)
    print(f"  Darvas breakouts ({len(breakouts):,})    → {out_darvas}")

    pio_cols = [
        "symbol",
        "name",
        "exchange",
        "sector",
        "last_price",
        "market_cap",
        "f_score",
        "f1",
        "f2",
        "f3",
        "f4",
        "f5",
        "f6",
        "f7",
        "f8",
        "f9",
    ]
    strong_pio = df[df["f_score"].notna() & (df["f_score"] >= PIOTROSKI_STRONG)][
        [c for c in pio_cols if c in df.columns]
    ].sort_values("f_score", ascending=False)
    out_pio = OUTPUT_DIR / f"strong_piotroski_{today}.csv"
    strong_pio.to_csv(out_pio, index=False)
    print(f"  Strong Piotroski ({len(strong_pio):,})    → {out_pio}")

    cc_cols = [
        "symbol",
        "name",
        "exchange",
        "sector",
        "industry",
        "last_price",
        "market_cap",
        "cc_score",
        "cc_roe_avg",
        "cc_c1",
        "cc_c2",
        "cc_c3",
        "cc_c4",
        "cc_c5",
        "cc_c6",
    ]
    qualifiers = df[df["cc_qualifies"] == 1][[c for c in cc_cols if c in df.columns]].sort_values(
        "market_cap", ascending=False
    )
    out_cc = OUTPUT_DIR / f"coffee_can_{today}.csv"
    qualifiers.to_csv(out_cc, index=False)
    print(f"  Coffee Can qualifiers ({len(qualifiers):,}) → {out_cc}")

    triple_mask = (
        (df["darvas_signal"] == "BREAKOUT_BUY")
        & (df["f_score"].notna())
        & (df["f_score"] >= PIOTROSKI_STRONG)
        & (df["cc_qualifies"] == 1)
    )
    triple_cols = [
        "symbol",
        "name",
        "exchange",
        "sector",
        "industry",
        "last_price",
        "market_cap",
        "darvas_signal",
        "darvas_box_top",
        "darvas_upside",
        "f_score",
        "cc_score",
        "cc_roe_avg",
    ]
    triple = df[triple_mask][[c for c in triple_cols if c in df.columns]].sort_values(
        "f_score", ascending=False
    )
    out_triple = OUTPUT_DIR / f"triple_hits_{today}.csv"
    triple.to_csv(out_triple, index=False)
    print(f"\n  ★  Triple hits ({len(triple):,} stocks)       → {out_triple}")
    if not triple.empty:
        print(f"\n  {'Symbol':<8} {'Name':<35} {'Signal':<15} {'F':>3} {'CC':>4}")
        print("  " + "-" * 70)
        for _, r in triple.head(20).iterrows():
            print(
                f"  {r['symbol']:<8} {str(r.get('name',''))[:34]:<35} "
                f"{r['darvas_signal']:<15} {int(r['f_score']):>3} "
                f"{r['cc_score']:>4}"
            )


def screen_market(
    exchange: str = "all",
    limit: int = 0,
    resume: bool = False,
    darvas_only: bool = False,
    batch_size: int = 500,
    workers: int = 10,
    delay: float = 0.05,
):
    """
    Full NASDAQ + NYSE/AMEX equity screener.

    Phase 1 (Darvas, fast):   bulk OHLC download in batches of `batch_size`.
    Phase 2 (Fundamentals):   per-stock Piotroski + Coffee Can via threadpool.
    Outputs CSVs to OUTPUT_DIR; checkpoints to SQLite for --resume support.
    """
    today = datetime.today().strftime("%Y%m%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    con = _init_db(DB_FILE)

    print("\n── Step 1: Stock Universe ────────────────────────────────────────────")
    universe_file = OUTPUT_DIR / "stock_universe.csv"
    if universe_file.exists() and resume:
        universe = pd.read_csv(universe_file)
        print(f"  Loaded cached universe: {len(universe):,} stocks")
    else:
        universe = fetch_stock_universe(exchange)
        universe.to_csv(universe_file, index=False)
        print(f"  Fetched {len(universe):,} stocks → {universe_file}")

    if limit:
        universe = universe.head(limit)
        print(f"  Limited to {len(universe):,} stocks (limit={limit})")

    symbols = universe["Symbol"].tolist()
    name_map = dict(zip(universe["Symbol"], universe["Name"]))
    exch_map = dict(zip(universe["Symbol"], universe["Exchange"]))

    print("\n── Phase 1: Darvas Box (batch OHLC download) ─────────────────────────")
    done_darvas = _already_done(con, "darvas") if resume else set()
    todo_darvas = [s for s in symbols if s not in done_darvas]
    print(
        f"  Symbols to scan: {len(todo_darvas):,}  " f"(skipping {len(done_darvas):,} already done)"
    )

    pbar_d = (
        tqdm(total=len(todo_darvas), unit="stocks", desc="Darvas", ncols=80) if TQDM_OK else None
    )
    darvas_results = run_darvas_batch(todo_darvas, batch_size=batch_size, pbar=pbar_d)
    if pbar_d:
        pbar_d.close()

    # Reopen the connection — the original handle has sat idle for ~8 min
    # during the yfinance batch download and macOS sometimes invalidates it
    # (disk I/O error on the first INSERT).
    con.close()
    con = _open_db(DB_FILE)

    # Bulk-insert Phase 1 results in a single transaction.
    now = datetime.now().isoformat()
    con.execute("BEGIN")
    try:
        for sym, dr in darvas_results.items():
            _upsert_result(
                con,
                {
                    "symbol": sym,
                    "name": name_map.get(sym, ""),
                    "exchange": exch_map.get(sym, ""),
                    "darvas_signal": dr.get("signal"),
                    "darvas_box_top": dr.get("box_top"),
                    "darvas_box_bot": dr.get("box_bottom"),
                    "darvas_current": dr.get("current_price"),
                    "darvas_upside": dr.get("upside_pct"),
                    "darvas_pos": dr.get("pos_in_box"),
                    "scanned_at": now,
                },
                _no_commit=True,
            )
            _mark_done(con, sym, "darvas", _no_commit=True)
        con.commit()
    except Exception:
        con.rollback()
        raise

    print(
        f"  Phase 1 done.  "
        f"Breakouts: {sum(1 for v in darvas_results.values() if v.get('signal')=='BREAKOUT_BUY'):,}  "
        f"In-box: {sum(1 for v in darvas_results.values() if v.get('signal')=='IN_BOX'):,}  "
        f"Breakdown: {sum(1 for v in darvas_results.values() if v.get('signal')=='BREAKDOWN_SELL'):,}"
    )

    if darvas_only:
        print("\n  darvas_only=True. Skipping fundamentals.")
        print("\n── Results ───────────────────────────────────────────────────────────")
        _compile_screener_outputs(con, today)
        con.close()
        return

    print(f"\n── Phase 2: Piotroski + Coffee Can ({workers} workers) ──────────────────")
    print(
        f"  Estimated time: {len(symbols) * 4 / workers / 60:.0f}–"
        f"{len(symbols) * 6 / workers / 60:.0f} minutes (depends on Yahoo latency)"
    )

    done_fund = _already_done(con, "fundamentals") if resume else set()
    todo_fund = [s for s in symbols if s not in done_fund]
    print(f"  Symbols to scan: {len(todo_fund):,}  " f"(skipping {len(done_fund):,} already done)")

    pbar_f = (
        tqdm(total=len(todo_fund), unit="stocks", desc="Fundamentals", ncols=80)
        if TQDM_OK
        else None
    )

    def _worker(sym):
        r = _scan_fundamentals_one(sym, name_map.get(sym, ""), exch_map.get(sym, ""))
        time.sleep(delay)
        return sym, r

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, sym): sym for sym in todo_fund}
        for future in as_completed(futures):
            sym, r = future.result()
            _upsert_result(con, r)
            _mark_done(con, sym, "fundamentals")
            if pbar_f:
                pbar_f.update(1)

    if pbar_f:
        pbar_f.close()

    print("\n── Results ───────────────────────────────────────────────────────────")
    _compile_screener_outputs(con, today)
    con.close()
    print(f"\n  Database: {DB_FILE}")
    print(f"  Output:   {OUTPUT_DIR}/\n")


# ═════════════════════════════════════════════════════════════════════════════
# CONVENIENCE: scan every common stock on NASDAQ + NYSE + AMEX
# ═════════════════════════════════════════════════════════════════════════════


def scan_all_us_stocks(
    darvas_only: bool = False,
    resume: bool = False,
    limit: int = 0,
    workers: int = 10,
    batch_size: int = 500,
    delay: float = 0.05,
) -> None:
    """
    Scan EVERY common stock on NASDAQ + NYSE + AMEX (~6,500 tickers).

    Runs Darvas Box on all stocks (Phase 1, batch OHLC, ~5 min) then
    Piotroski F-Score + Coffee Can on each (Phase 2, threaded, ~35-50 min).
    Results are checkpointed to SQLite — pass resume=True to continue after
    an interrupt. Final CSVs land in OUTPUT_DIR.

    Args:
        darvas_only: skip Phase 2 (fundamentals) for a fast technical-only run
        resume:      skip symbols already in the SQLite checkpoint
        limit:       cap to first N stocks (0 = full universe)
        workers:     thread-pool size for fundamentals fetch
        batch_size:  tickers per yf.download() call
        delay:       per-worker sleep between Yahoo calls (rate-limit cushion)
    """
    screen_market(
        exchange="all",
        limit=limit,
        resume=resume,
        darvas_only=darvas_only,
        batch_size=batch_size,
        workers=workers,
        delay=delay,
    )


# ═════════════════════════════════════════════════════════════════════════════
# DOCUMENT → MARKDOWN  (Excel, PDF, Word, PowerPoint via Microsoft markitdown)
# ═════════════════════════════════════════════════════════════════════════════
#
# Why markitdown:
#   Microsoft's `markitdown` is a single library that converts office docs
#   (xlsx/docx/pptx), PDFs, images (OCR), HTML, CSV/TSV, and more into clean
#   Markdown — ideal as a preprocessing step for LLM ingestion or for
#   archiving research reports alongside the screener output.
#
# Install (Colab cell):
#   !pip install "markitdown[all]"          # all optional converters
#   # or minimal:  !pip install markitdown
#
# Supported extensions handled by default:
#   .xlsx .xls  → spreadsheet rows
#   .pdf        → text + table extraction
#   .docx .doc  → Word body (paragraphs, lists, tables)
#   .pptx       → slide text + notes
#   .csv .tsv .html .htm .xml .json .txt .md
#   .png .jpg .jpeg  → OCR (requires markitdown[all])

CONVERT_DIR = BASE_DIR / "converted_markdown"
CONVERT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DOC_EXTENSIONS = (
    ".xlsx",
    ".xls",
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".xml",
    ".json",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
)


def _get_markitdown():
    """Lazy-import MarkItDown so the rest of the script works without it."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise ImportError(
            "markitdown is not installed.  Run:\n"
            '    pip install "markitdown[all]"\n'
            "in a Colab cell, then re-exec this file."
        )
    return MarkItDown()


def convert_to_markdown(file_path, output_dir=None, overwrite: bool = False) -> Path:
    """
    Convert a single Excel / PDF / Word / PowerPoint / etc. file to Markdown.

    Args:
        file_path:  Path to input file (str or Path).
        output_dir: Where the .md goes. Defaults to CONVERT_DIR.
        overwrite:  If False (default), skip files whose .md already exists.

    Returns:
        Path to the written .md file.
    """
    src = Path(file_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(src)

    dst_dir = Path(output_dir).expanduser().resolve() if output_dir else CONVERT_DIR
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / (src.stem + ".md")

    if dst.exists() and not overwrite:
        print(f"  skip (exists): {dst.name}")
        return dst

    md = _get_markitdown()
    result = md.convert(str(src))

    header = (
        f"# {src.name}\n\n"
        f"> Source: `{src}`  \n"
        f"> Converted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
        f"> Size: {src.stat().st_size:,} bytes\n\n"
        f"---\n\n"
    )
    dst.write_text(header + (result.text_content or ""), encoding="utf-8")
    print(
        f"  ✓ {src.name}  →  {dst.relative_to(dst_dir.parent) if dst.is_relative_to(dst_dir.parent) else dst}"
    )
    return dst


def convert_all_documents(
    input_dir,
    output_dir=None,
    extensions=DEFAULT_DOC_EXTENSIONS,
    recursive: bool = True,
    overwrite: bool = False,
) -> dict:
    """
    Convert every supported document in a directory to Markdown.

    Args:
        input_dir:  Directory to scan.
        output_dir: Where .md files go (default: CONVERT_DIR).
        extensions: Iterable of file extensions to include (lower-case, with dot).
        recursive:  Descend into subdirectories.
        overwrite:  Re-convert files whose .md already exists.

    Returns:
        {"ok": [Path,...], "failed": [(Path, str), ...], "skipped": [Path,...]}
    """
    src_dir = Path(input_dir).expanduser().resolve()
    if not src_dir.is_dir():
        raise NotADirectoryError(src_dir)

    out_dir = Path(output_dir).expanduser().resolve() if output_dir else CONVERT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    exts = {e.lower() for e in extensions}
    walker = src_dir.rglob("*") if recursive else src_dir.glob("*")
    files = sorted(
        p for p in walker if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")
    )

    print(f"\n{'=' * 60}")
    print("  DOC → MARKDOWN")
    print(f"  Source : {src_dir}")
    print(f"  Output : {out_dir}")
    print(f"  Files  : {len(files)} matched ({', '.join(sorted(exts))})")
    print(f"{'=' * 60}\n")

    if not files:
        print("  Nothing to convert.")
        return {"ok": [], "failed": [], "skipped": []}

    ok, failed, skipped = [], [], []
    iterator = tqdm(files, unit="file", desc="Converting") if TQDM_OK else files
    for src in iterator:
        try:
            dst = out_dir / (src.stem + ".md")
            if dst.exists() and not overwrite:
                skipped.append(src)
                continue
            convert_to_markdown(src, output_dir=out_dir, overwrite=overwrite)
            ok.append(src)
        except Exception as e:
            log.warning("Convert failed for %s: %s", src.name, e)
            failed.append((src, str(e)[:200]))

    print(f"\n  Done.  OK={len(ok)}  Skipped={len(skipped)}  Failed={len(failed)}")
    if failed:
        print("  Failed files:")
        for p, err in failed[:10]:
            print(f"    • {p.name}: {err}")
        if len(failed) > 10:
            print(f"    … and {len(failed) - 10} more")

    return {"ok": ok, "failed": failed, "skipped": skipped}


# ═════════════════════════════════════════════════════════════════════════════
# Example calls (uncomment in a Colab cell to run)
# ═════════════════════════════════════════════════════════════════════════════
# scan_all_us_stocks()                       # full ~6,500 stock run, all 3 scans
# scan_all_us_stocks(darvas_only=True)       # fast (~5 min) — Darvas only
# scan_all_us_stocks(resume=True)            # resume after interrupt
# scan_all_us_stocks(limit=200)              # quick test on first 200
#
# run("AAPL", run_scans=True, show_options=True)
# run_scans_only("NVDA")
# run_batch(symbols=DOW_JONES_30, run_scans=True)
# run_batch(symbols=NASDAQ_50, output_format="json")
#
# # Document → Markdown
# convert_to_markdown("/content/research_report.pdf")
# convert_all_documents("/content/inbox")                       # full inbox
# convert_all_documents("/content/inbox", extensions=(".pdf",)) # PDFs only
# convert_all_documents("/content/inbox", overwrite=True)       # re-convert all
