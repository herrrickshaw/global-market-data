# stock_daily_report_improved.py
# ================================
# Generates a daily stock report for any NSE/BSE-listed Indian equity, with
# three optional quantitative scans:
#   • Darvas Box       — technical momentum breakout (uses nsepython OHLC history)
#   • Piotroski F-Score — 9-point financial-strength score (uses yfinance)
#   • Coffee Can Screen — quality+growth filter à la Saurabh Mukherjea (yfinance)
#
# Original report author: BennyThadikaran (https://github.com/BennyThadikaran)
# Extended by: Claude (bug fixes, nsepython integration, quantitative scans)
#
# Install dependencies (run once):
#   pip install "nse[local]" bse nsepython yfinance pandas openpyxl
#
# Single-stock usage:
#   python stock_daily_report_improved.py RELIANCE
#   python stock_daily_report_improved.py TCS --output json --scans
#   python stock_daily_report_improved.py INFY --fno --scans
#
# Nifty 50 batch:
#   python stock_daily_report_improved.py --nifty50
#   python stock_daily_report_improved.py --nifty50 --output json --scans

# ── Colab install (uncomment when running in Google Colab) ───────────────────
# !pip install "nse[local]" bse nsepython yfinance pandas openpyxl

# ── Standard library ─────────────────────────────────────────────────────────
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Third-party ──────────────────────────────────────────────────────────────
import pandas as pd

# Primary NSE/BSE market-data libraries
try:
    from nse import NSE
except ImportError:
    sys.exit("❌  pip install 'nse[local]'")

try:
    from bse import BSE
except ImportError:
    sys.exit("❌  pip install bse")

# nsepython — used for:
#   • equity_history()   → clean OHLC DataFrames for Darvas Box scan
#   • get_bulkdeals()    → live bulk-deal feed (eliminates CSV parsing issues)
#   • nse_get_top_gainers/losers → market-context sidebar
try:
    import nsepython as nspy

    NSEPYTHON_OK = True
except ImportError:
    NSEPYTHON_OK = False
    print("⚠️  nsepython not found — Darvas scan and live bulk deals disabled.")
    print("    Install with: pip install nsepython")

# yfinance — used for multi-year financial statements (Piotroski + Coffee Can)
try:
    import yfinance as yf

    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False
    print("⚠️  yfinance not found — Piotroski and Coffee Can scans disabled.")
    print("    Install with: pip install yfinance")


# ── Constants ─────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = Path("./nse_bse_data")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Darvas Box: number of consecutive days a high/low must hold to be "confirmed"
DARVAS_CONFIRM = 3

# Nifty 50 constituent symbols (NSE format, 2025-26 composition).
NIFTY_50_SYMBOLS = [
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BPCL",
    "BHARTIARTL",
    "BRITANNIA",
    "CIPLA",
    "COALINDIA",
    "DIVISLAB",
    "DRREDDY",
    "EICHERMOT",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "ITC",
    "INDUSINDBK",
    "INFY",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NTPC",
    "NESTLEIND",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SHRIRAMFIN",
    "SBIN",
    "SUNPHARMA",
    "TCS",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
    "WIPRO",
]


# ── Formatting helpers ────────────────────────────────────────────────────────


def fmt(val, prefix="₹", decimals=2):
    """Return 'prefix + comma-formatted number' or 'N/A' on bad input."""
    try:
        return f"{prefix}{float(val):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(val) if val else "N/A"


def pct(val):
    """Return a coloured ▲/▼ percentage string, or 'N/A'."""
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


def row(label, value, width=28):
    print(f"  {label:<{width}} {value}")


# ── NSE data fetching (nse library) ──────────────────────────────────────────


def fetch_nse_quote(nse: NSE, symbol: str) -> dict:
    """trade_info section: price band, 52-week range, total volume."""
    try:
        return nse.quote(symbol, section="trade_info") or {}
    except Exception as e:
        print(f"  [NSE quote error] {e}")
        return {}


def fetch_nse_equity_quote(nse: NSE, symbol: str) -> dict:
    """OHLCV + metadata from NSE equityQuote endpoint."""
    try:
        return nse.equityQuote(symbol) or {}
    except Exception as e:
        print(f"  [NSE equityQuote error] {e}")
        return {}


def fetch_nse_meta(nse: NSE, symbol: str) -> dict:
    """Company metadata (ISIN, industry, listing date)."""
    try:
        return nse.equityMetaInfo(symbol) or {}
    except Exception as e:
        print(f"  [NSE meta error] {e}")
        return {}


def fetch_nse_actions(nse: NSE, symbol: str) -> list:
    """Upcoming corporate actions — top 5."""
    try:
        return (nse.actions(symbol=symbol) or [])[:5]
    except Exception as e:
        print(f"  [NSE actions error] {e}")
        return []


def fetch_nse_board_meetings(nse: NSE, symbol: str) -> list:
    """Upcoming board meetings — top 3."""
    try:
        return (nse.boardMeetings(symbol=symbol) or [])[:3]
    except Exception as e:
        print(f"  [NSE board meetings error] {e}")
        return []


def fetch_nse_historical(nse: NSE, symbol: str, days: int = 30) -> list:
    """
    Historical OHLCV list from the nse library (used for the report summary).

    BUG FIX: original code called nse.fetch_equity_historical_data() which
    does not exist in all library builds.  We now try both known method names.
    """
    end, start = datetime.today(), datetime.today() - timedelta(days=days)
    for method in ("equityHistoricalData", "fetch_equity_historical_data"):
        fn = getattr(nse, method, None)
        if fn is None:
            continue
        try:
            return fn(symbol=symbol, start=start, end=end) or []
        except Exception as e:
            print(f"  [NSE historical via {method}] {e}")
    return []


def fetch_nse_option_chain(nse: NSE, symbol: str) -> dict:
    """Option chain → Max Pain and Put-Call Ratio."""
    try:
        oc = nse.optionChain(symbol)
        if oc:
            return {
                "maxpain": nse.maxpain(oc),
                "pcr": nse.compileOptionChain(oc).get("pcr"),
                "raw": oc,
            }
        return {}
    except Exception as e:
        print(f"  [NSE option chain error] {e}")
        return {}


# ── Bulk deals (nsepython primary, nse-library fallback) ─────────────────────


def _normalize_bulk_df(df: pd.DataFrame) -> list:
    """
    Rename bulk-deal DataFrame columns to fixed internal keys and return as
    a list of dicts.

    BUG FIX (original): exact lowercased string matching failed when NSE CSVs
    had trailing spaces or non-standard slash characters.  We now use keyword-
    based partial matching so minor formatting differences are tolerated.

    BUG FIX (original): 'Quantity Traded' from archived CSVs used Indian comma
    notation ("1,26,922") stored as strings.  nsepython's get_bulkdeals()
    already returns numeric quantities; the fallback CSV path still strips commas.
    """
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    rename = {}
    for col in df.columns:
        if col == "symbol":
            rename[col] = "symbol"
        elif "client" in col:
            rename[col] = "clientname"
        elif "quantity" in col:
            rename[col] = "quantity"
        elif "price" in col or "wght" in col:
            rename[col] = "price"
        elif "buy" in col or "sell" in col:
            rename[col] = "buysell"

    df = df.rename(columns=rename)

    # Strip commas from quantity in case this came from an archived CSV.
    if "quantity" in df.columns:
        df["quantity"] = df["quantity"].astype(str).str.replace(",", "", regex=False).str.strip()
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")

    required = {"symbol", "clientname", "quantity", "price", "buysell"}
    missing = required - set(df.columns)
    if missing:
        print(f"  [Bulk deals] Missing columns after rename: {missing}")
        print(f"  Available: {list(df.columns)}")
        return []

    return df.to_dict(orient="records")


def fetch_bulk_deals(nse: NSE) -> list:
    """
    Fetch today's bulk deals.  Priority order:
      1. nsepython.get_bulkdeals()  — reads NSE's live bulk.csv (clean, no JSON issues)
      2. nse.bulkdeals()            — live API call (sometimes returns bad JSON)
      3. Local nse_bse_data/bulk_deals.csv — user-placed fallback

    The nsepython source is the most reliable because it reads the CSV archive
    directly rather than hitting the JSON endpoint that occasionally returns an
    empty body (causing the original JSONDecodeError bug).
    """
    # ── Primary: nsepython get_bulkdeals ────────────────────────────────────
    if NSEPYTHON_OK:
        try:
            df = nspy.get_bulkdeals()
            if isinstance(df, pd.DataFrame) and not df.empty:
                deals = _normalize_bulk_df(df)
                if deals:
                    return deals
        except Exception as e:
            print(f"  [nsepython bulk deals error] {e}")

    # ── Secondary: nse library live API ─────────────────────────────────────
    today = datetime.today()
    try:
        raw = nse.bulkdeals(option_type="ALL", fromdate=today, todate=today) or []
        if raw:
            return raw
    except Exception as e:
        print(f"  [NSE bulk deals API error] {e}")

    # ── Tertiary: local CSV fallback ─────────────────────────────────────────
    fallback = DOWNLOAD_DIR / "bulk_deals.csv"
    print(f"  Trying local fallback: {fallback}")
    try:
        df = pd.read_csv(fallback)
        return _normalize_bulk_df(df)
    except FileNotFoundError:
        print(f"  [Bulk deals] No local fallback found at {fallback}.")
    except Exception as e:
        print(f"  [Bulk deals CSV error] {e}")
    return []


# ── BSE data fetching ─────────────────────────────────────────────────────────


def fetch_bse_data(bse: BSE, ticker: str) -> dict:
    """BSE scrip code, OHLC quote, and corporate actions."""
    result = {}
    try:
        scrip_code = bse.getScripCode(ticker)
        if not scrip_code:
            print(f"  [BSE] Scrip code not found for {ticker}")
            return result
        result["scrip_code"] = scrip_code
        result["quote"] = bse.quote(scrip_code) or {}
        result["actions"] = (bse.actions(scripcode=scrip_code) or [])[:5]
    except Exception as e:
        print(f"  [BSE error] {e}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 1 — DARVAS BOX  (nsepython equity_history → OHLC)
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_ohlc_history(symbol: str, days: int = 90) -> pd.DataFrame:
    """
    Fetch OHLC history for Darvas scan using nsepython.equity_history().

    equity_history() returns a DataFrame whose rows come from NSE's historical
    API with columns including CH_TRADE_HIGH_PRICE, CH_TRADE_LOW_PRICE,
    CH_CLOSING_PRICE, CH_OPENING_PRICE, and CH_TIMESTAMP.

    The function breaks the range into 40-day chunks internally, so any date
    range is supported.
    """
    if not NSEPYTHON_OK:
        return pd.DataFrame()
    end = datetime.today().strftime("%d-%m-%Y")
    start = (datetime.today() - timedelta(days=days)).strftime("%d-%m-%Y")
    try:
        df = nspy.equity_history(symbol, "EQ", start, end)
        if isinstance(df, pd.DataFrame) and not df.empty:
            # Sort by date ascending so index 0 = oldest, -1 = latest
            date_col = next(
                (c for c in df.columns if "TIMESTAMP" in c.upper() or "DATE" in c.upper()),
                None,
            )
            if date_col:
                df = df.sort_values(date_col).reset_index(drop=True)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        print(f"  [Darvas OHLC fetch error] {e}")
        return pd.DataFrame()


def compute_darvas_box(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """
    Detect a Darvas Box and classify the current price relative to it.

    ── Algorithm (Nicolas Darvas, 1960) ─────────────────────────────────────
    A "box" forms after a stock reaches a local high and then consolidates:

      Box Top   : A high H[i] that is NOT exceeded by the next `confirm`
                  trading-day highs.  We scan backwards to find the most
                  recent such confirmed top.

      Box Bottom: The lowest low from the box-top day onward that is NOT
                  undercut for `confirm` consecutive days.

      Signal    : BREAKOUT_BUY  → current close > box top
                  BREAKDOWN_SELL → current close < box bottom
                  IN_BOX         → price is contained within the box

    Args:
        df      : DataFrame from fetch_ohlc_history (or any OHLC DataFrame)
        confirm : Consecutive days a high/low must hold to be "confirmed"
                  (Darvas used 3; tighten to 2 for volatile stocks)

    Returns dict with signal, box_top, box_bottom, position_in_box_pct, etc.
    """

    # ── Identify high / low / close columns robustly ─────────────────────────
    def find_col(df, candidates):
        for c in candidates:
            match = next((col for col in df.columns if c.upper() in col.upper()), None)
            if match:
                return match
        return None

    h_col = find_col(df, ["CH_TRADE_HIGH_PRICE", "HIGH", "DAYHIGH"])
    l_col = find_col(df, ["CH_TRADE_LOW_PRICE", "LOW", "DAYLOW"])
    c_col = find_col(df, ["CH_CLOSING_PRICE", "CLOSE", "LAST"])

    if not all([h_col, l_col, c_col]) or len(df) < confirm + 5:
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": f"Need ≥ {confirm + 5} rows; got {len(df)}",
        }

    all_highs = pd.to_numeric(df[h_col], errors="coerce").fillna(0).tolist()
    all_lows = pd.to_numeric(df[l_col], errors="coerce").fillna(0).tolist()
    all_closes = pd.to_numeric(df[c_col], errors="coerce").fillna(0).tolist()

    # The current bar (last row) is the price we want to classify.
    # Box formation uses only the HISTORICAL bars (all except the last) so
    # the current bar's data cannot "contaminate" the box boundaries.
    current = all_closes[-1]
    highs = all_highs[:-1]
    lows = all_lows[:-1]
    n = len(highs)

    # ── Step 1: find the most recent confirmed box top ────────────────────────
    # Scan backwards through historical highs; a top is "confirmed" when the
    # next `confirm` bars all have highs strictly below the candidate.
    box_top_idx = None
    box_top = None
    for i in range(n - confirm - 1, -1, -1):
        candidate = highs[i]
        if candidate == 0:
            continue
        window = highs[i + 1 : i + 1 + confirm]
        if len(window) == confirm and all(h < candidate for h in window):
            box_top_idx = i
            box_top = candidate
            break

    if box_top is None:
        return {
            "signal": "NO_BOX",
            "box_top": None,
            "box_bottom": None,
            "note": "No confirmed Darvas top found in the look-back window.",
        }

    # ── Step 2: find the confirmed box bottom ────────────────────────────────
    # Look only at historical lows from the box-top day onward (still
    # excluding the current bar so it cannot pull the bottom down).
    segment = lows[box_top_idx:]
    box_bottom = None
    for i in range(len(segment) - confirm):
        candidate = segment[i]
        if candidate == 0:
            continue
        window = segment[i + 1 : i + 1 + confirm]
        if len(window) == confirm and all(l > candidate for l in window):
            box_bottom = candidate
            break

    # Fallback: minimum of the historical segment when no confirmed bottom found.
    if box_bottom is None:
        valid_lows = [l for l in segment if l > 0]
        box_bottom = min(valid_lows) if valid_lows else None

    if box_bottom is None:
        return {
            "signal": "NO_BOX",
            "box_top": box_top,
            "box_bottom": None,
            "note": "Could not confirm a box bottom.",
        }

    # ── Step 3: classify today's close against the historical box ─────────────
    if current > box_top:
        signal = "BREAKOUT_BUY"
    elif current < box_bottom:
        signal = "BREAKDOWN_SELL"
    else:
        signal = "IN_BOX"

    box_range = box_top - box_bottom
    pos_in_box = ((current - box_bottom) / box_range * 100) if box_range else 0
    upside_to_top = ((box_top - current) / current * 100) if current else 0

    return {
        "signal": signal,
        "box_top": round(box_top, 2),
        "box_bottom": round(box_bottom, 2),
        "current_price": round(current, 2),
        "box_range": round(box_range, 2),
        "position_in_box_pct": round(pos_in_box, 1),
        "upside_to_top_pct": round(upside_to_top, 2),
        "confirm_days": confirm,
        "data_points": len(all_closes),
    }


def display_darvas_box(result: dict):
    """Print the Darvas Box scan result."""
    section("Darvas Box Scan")
    sig = result.get("signal", "N/A")

    signal_labels = {
        "BREAKOUT_BUY": "\033[92m● BREAKOUT BUY\033[0m  — price above box top",
        "BREAKDOWN_SELL": "\033[91m● BREAKDOWN SELL\033[0m — price below box bottom",
        "IN_BOX": "\033[93m● IN BOX\033[0m        — price consolidating",
        "NO_BOX": "No confirmed Darvas box found",
        "INSUFFICIENT_DATA": "Insufficient OHLC data",
    }
    print(f"\n  Signal: {signal_labels.get(sig, sig)}")

    if result.get("box_top"):
        print()
        row("Box Top", fmt(result["box_top"]))
        row("Box Bottom", fmt(result["box_bottom"]))
        row("Current Price", fmt(result.get("current_price")))
        row("Box Range", fmt(result.get("box_range"), prefix="₹"))
        row("Position in Box", f"{result.get('position_in_box_pct', 0):.1f}%")
        row("Upside to Top", f"{result.get('upside_to_top_pct', 0):.2f}%")
        row("Confirmation (days)", str(result.get("confirm_days", DARVAS_CONFIRM)))
        row("Data points used", str(result.get("data_points", "—")))
    elif result.get("note"):
        print(f"  Note: {result['note']}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 2 — PIOTROSKI F-SCORE  (yfinance financial statements)
# ═══════════════════════════════════════════════════════════════════════════════


def _yf_financials(ticker):
    """
    Retrieve income statement, balance sheet, and cash flow from yfinance.

    Handles API differences between old (ticker.financials / ticker.cashflow)
    and new (ticker.income_stmt / ticker.cash_flow) yfinance versions.
    """
    inc = getattr(ticker, "income_stmt", None) or getattr(ticker, "financials", None)
    bal = getattr(ticker, "balance_sheet", None)
    cf = getattr(ticker, "cash_flow", None) or getattr(ticker, "cashflow", None)
    return inc, bal, cf


def _row(df, *row_names, col: int = 0):
    """
    Safely get a scalar from a yfinance financial DataFrame.

    Tries each row name in order; returns None if none match or value is NaN.
    `col` is the column index (0 = most recent fiscal year, 1 = prior year).
    """
    if df is None or df.empty:
        return None
    for name in row_names:
        if name in df.index:
            try:
                val = df.loc[name].iloc[col]
                return float(val) if pd.notna(val) else None
            except (IndexError, TypeError, ValueError):
                pass
    return None


def compute_piotroski_score(symbol: str) -> dict:
    """
    Compute Piotroski F-Score (0–9) from annual financial statements.

    Joseph Piotroski (2000) showed that a simple 9-point accounting score
    predicts one-year stock performance.  Each criterion scores 1 (pass) or
    0 (fail); total ≥ 7 → financially strong; ≤ 3 → weak.

    ── 9 Criteria ───────────────────────────────────────────────────────────
    Profitability  (4 pts)
      F1  ROA > 0                 — positive return on assets
      F2  Operating Cash Flow > 0 — cash-generative operations
      F3  Δ ROA > 0               — profitability improving YoY
      F4  Accruals: OCF/Assets > ROA — earnings are cash-backed (quality signal)

    Leverage & Liquidity  (3 pts)
      F5  Δ Long-term Debt ratio < 0  — less financial leverage
      F6  Δ Current Ratio > 0         — improving short-term liquidity
      F7  No new shares issued         — no shareholder dilution

    Operating Efficiency  (2 pts)
      F8  Δ Gross Margin > 0          — improving product/service profitability
      F9  Δ Asset Turnover > 0        — generating more revenue per rupee of assets

    Data source: yfinance (symbol.NS for NSE-listed stocks).
    """
    if not YFINANCE_OK:
        return {"error": "yfinance not installed — pip install yfinance"}

    yf_sym = f"{symbol}.NS"
    try:
        ticker = yf.Ticker(yf_sym)
        inc, bal, cf = _yf_financials(ticker)
        info = ticker.info or {}
    except Exception as e:
        return {"error": f"yfinance error: {e}"}

    if inc is None or inc.empty:
        return {"error": f"No financial data found for {yf_sym} on Yahoo Finance"}

    scores = {}
    details = {}

    # ── Profitability ─────────────────────────────────────────────────────────

    net_inc_0 = _row(inc, "Net Income", col=0)
    assets_0 = _row(bal, "Total Assets", col=0)
    roa_0 = (net_inc_0 / assets_0) if (net_inc_0 and assets_0) else None

    net_inc_1 = _row(inc, "Net Income", col=1)
    assets_1 = _row(bal, "Total Assets", col=1)
    roa_1 = (net_inc_1 / assets_1) if (net_inc_1 and assets_1) else None

    # F1 — ROA positive
    scores["F1_ROA_positive"] = 1 if (roa_0 and roa_0 > 0) else 0
    details["ROA_current_%"] = round(roa_0 * 100, 2) if roa_0 else "N/A"

    # F2 — Operating Cash Flow positive
    ocf_0 = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities", col=0)
    scores["F2_OCF_positive"] = 1 if (ocf_0 and ocf_0 > 0) else 0
    details["OCF_current_Cr"] = round(ocf_0 / 1e7, 2) if ocf_0 else "N/A"

    # F3 — ROA improving
    if roa_0 is not None and roa_1 is not None:
        scores["F3_ROA_improving"] = 1 if roa_0 > roa_1 else 0
    else:
        scores["F3_ROA_improving"] = 0
    details["ROA_prev_%"] = round(roa_1 * 100, 2) if roa_1 else "N/A"

    # F4 — Accruals (cash-backed earnings): OCF/Assets > ROA
    if ocf_0 and assets_0 and roa_0 is not None:
        scores["F4_Accruals"] = 1 if (ocf_0 / assets_0) > roa_0 else 0
    else:
        scores["F4_Accruals"] = 0

    # ── Leverage & Liquidity ──────────────────────────────────────────────────

    # F5 — Long-term debt ratio decreasing
    ltd_0 = _row(bal, "Long Term Debt", col=0) or 0
    ltd_1 = _row(bal, "Long Term Debt", col=1) or 0
    lev_0 = (ltd_0 / assets_0) if assets_0 else None
    lev_1 = (ltd_1 / assets_1) if assets_1 else None
    if lev_0 is not None and lev_1 is not None:
        scores["F5_Leverage_down"] = 1 if lev_0 < lev_1 else 0
    else:
        scores["F5_Leverage_down"] = 0
    details["LTD_ratio_curr_%"] = round(lev_0 * 100, 2) if lev_0 else "N/A"
    details["LTD_ratio_prev_%"] = round(lev_1 * 100, 2) if lev_1 else "N/A"

    # F6 — Current Ratio improving (compute from Current Assets / Current Liabilities)
    ca_0 = _row(bal, "Current Assets", "Total Current Assets", col=0)
    cl_0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca_1 = _row(bal, "Current Assets", "Total Current Assets", col=1)
    cl_1 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    cr_0 = (ca_0 / cl_0) if (ca_0 and cl_0) else None
    cr_1 = (ca_1 / cl_1) if (ca_1 and cl_1) else None
    if cr_0 is not None and cr_1 is not None:
        scores["F6_CurrentRatio_up"] = 1 if cr_0 > cr_1 else 0
    else:
        scores["F6_CurrentRatio_up"] = 0
    details["CurrentRatio_curr"] = round(cr_0, 2) if cr_0 else "N/A"
    details["CurrentRatio_prev"] = round(cr_1, 2) if cr_1 else "N/A"

    # F7 — No new equity shares issued (non-dilution)
    sh_0 = _row(bal, "Share Issued", col=0) or info.get("sharesOutstanding")
    sh_1 = _row(bal, "Share Issued", col=1)
    if sh_0 and sh_1:
        scores["F7_No_dilution"] = 1 if sh_0 <= sh_1 else 0
    else:
        scores["F7_No_dilution"] = 1  # assume no dilution when data absent

    # ── Operating Efficiency ──────────────────────────────────────────────────

    rev_0 = _row(inc, "Total Revenue", col=0)
    gp_0 = _row(inc, "Gross Profit", col=0)
    rev_1 = _row(inc, "Total Revenue", col=1)
    gp_1 = _row(inc, "Gross Profit", col=1)
    gm_0 = (gp_0 / rev_0) if (gp_0 and rev_0) else None
    gm_1 = (gp_1 / rev_1) if (gp_1 and rev_1) else None

    # F8 — Gross margin improving
    if gm_0 is not None and gm_1 is not None:
        scores["F8_GrossMargin_up"] = 1 if gm_0 > gm_1 else 0
    else:
        scores["F8_GrossMargin_up"] = 0
    details["GrossMargin_curr_%"] = round(gm_0 * 100, 2) if gm_0 else "N/A"
    details["GrossMargin_prev_%"] = round(gm_1 * 100, 2) if gm_1 else "N/A"

    # F9 — Asset turnover improving (revenue generated per rupee of assets)
    at_0 = (rev_0 / assets_0) if (rev_0 and assets_0) else None
    at_1 = (rev_1 / assets_1) if (rev_1 and assets_1) else None
    if at_0 is not None and at_1 is not None:
        scores["F9_AssetTurnover_up"] = 1 if at_0 > at_1 else 0
    else:
        scores["F9_AssetTurnover_up"] = 0
    details["AssetTurnover_curr"] = round(at_0, 3) if at_0 else "N/A"
    details["AssetTurnover_prev"] = round(at_1, 3) if at_1 else "N/A"

    total = sum(scores.values())
    interp = (
        "STRONG — likely outperformer"
        if total >= 7
        else "MODERATE — neutral stance" if total >= 4 else "WEAK — avoid or short candidate"
    )

    return {
        "symbol": symbol,
        "f_score": total,
        "interpretation": interp,
        "component_scores": scores,
        "details": details,
    }


def display_piotroski_score(result: dict):
    """Print Piotroski F-Score with per-criterion breakdown."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 3 — COFFEE CAN PORTFOLIO SCREEN  (yfinance financial statements)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_coffee_can(symbol: str) -> dict:
    """
    Coffee Can Portfolio screen for Indian equities.

    Coined by Robert Kirby (1984) and popularised for India by Saurabh Mukherjea
    (Marcellus Investment Managers, "Coffee Can Investing", 2018).

    ── Criteria (all must pass for qualification) ──────────────────────────
    C1  Revenue CAGR > 10%    over available history (target: 10 years)
    C2  ROCE > 15% on average  consistently positive over available years
    C3  Debt/Equity < 1        capital-light balance sheet
    C4  Market Cap ≥ ₹500 Cr   filters out micro-caps with liquidity risk
    C5  No loss year            every fiscal year profitable (net income > 0)

    The idea: buy a basket of such companies and "put it in a coffee can" —
    ignore short-term price noise for 10+ years.

    Data source: yfinance (up to 4 years of annual financials available for
    free; for a full 10-year check, a premium data source is required).
    """
    if not YFINANCE_OK:
        return {"error": "yfinance not installed — pip install yfinance"}

    yf_sym = f"{symbol}.NS"
    try:
        ticker = yf.Ticker(yf_sym)
        inc, bal, _ = _yf_financials(ticker)
        info = ticker.info or {}
    except Exception as e:
        return {"error": f"yfinance error: {e}"}

    if inc is None or inc.empty:
        return {"error": f"No data for {yf_sym} on Yahoo Finance"}

    def series(df, *rows):
        """Return a list of floats for the first matching row (most recent first)."""
        for name in rows:
            if df is not None and name in df.index:
                return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
        return []

    criteria = {}
    details = {}

    # ── C1: Revenue CAGR > 10% ───────────────────────────────────────────────
    revs = series(inc, "Total Revenue")
    if len(revs) >= 2:
        # revs[0] = most recent, revs[-1] = oldest
        years = len(revs) - 1
        cagr = ((revs[0] / revs[-1]) ** (1 / years) - 1) * 100 if revs[-1] > 0 else None
        criteria["C1_RevenuCAGR_gt10"] = 1 if (cagr and cagr > 10) else 0
        details["Revenue_CAGR_%"] = round(cagr, 2) if cagr else "N/A"
        details["Revenue_years"] = years
    else:
        criteria["C1_RevenuCAGR_gt10"] = 0
        details["Revenue_CAGR_%"] = "N/A"

    # ── C2: ROCE > 15% (average across available years) ──────────────────────
    # ROCE = EBIT / Capital Employed;  Capital Employed = Total Assets − Current Liabilities
    ebit_s = series(inc, "EBIT", "Operating Income", "Ebit")
    ta_s = series(bal, "Total Assets")
    cl_s = series(bal, "Current Liabilities", "Total Current Liabilities")

    roce_list = []
    for i in range(min(len(ebit_s), len(ta_s), len(cl_s))):
        cap_emp = ta_s[i] - cl_s[i]
        if cap_emp > 0:
            roce_list.append(ebit_s[i] / cap_emp * 100)

    if roce_list:
        avg_roce = sum(roce_list) / len(roce_list)
        criteria["C2_ROCE_gt15"] = 1 if avg_roce > 15 else 0
        details["ROCE_avg_%"] = round(avg_roce, 2)
        details["ROCE_min_%"] = round(min(roce_list), 2)
        details["ROCE_years"] = len(roce_list)
    else:
        criteria["C2_ROCE_gt15"] = 0
        details["ROCE_avg_%"] = "N/A"

    # ── C3: Debt/Equity < 1 ──────────────────────────────────────────────────
    # yfinance info.debtToEquity is in percent in some builds (45.2 = 0.452×).
    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        de = de_raw / 100 if de_raw > 10 else de_raw  # normalise if needed
        criteria["C3_LowDebt"] = 1 if de < 1 else 0
        details["Debt_to_Equity"] = round(de, 2)
    else:
        # Compute from balance sheet directly
        ltd_s = series(bal, "Long Term Debt")
        eq_s = series(
            bal,
            "Stockholders Equity",
            "Total Stockholder Equity",
            "Total Equity Gross Minority Interest",
        )
        if ltd_s and eq_s and eq_s[0] != 0:
            de = ltd_s[0] / eq_s[0]
            criteria["C3_LowDebt"] = 1 if de < 1 else 0
            details["Debt_to_Equity"] = round(de, 2)
        else:
            criteria["C3_LowDebt"] = 0
            details["Debt_to_Equity"] = "N/A"

    # ── C4: Market Cap ≥ ₹500 Cr ─────────────────────────────────────────────
    mcap = info.get("marketCap")
    mcap_cr = mcap / 1e7 if mcap else None  # convert to crores
    criteria["C4_MCap_ge500Cr"] = 1 if (mcap_cr and mcap_cr >= 500) else 0
    details["Market_Cap_Cr"] = round(mcap_cr, 2) if mcap_cr else "N/A"

    # ── C5: No loss year ─────────────────────────────────────────────────────
    ni_s = series(inc, "Net Income")
    if ni_s:
        all_profit = all(n > 0 for n in ni_s)
        criteria["C5_NoProfitLoss"] = 1 if all_profit else 0
        details["Loss_years"] = sum(1 for n in ni_s if n <= 0)
        details["Years_analysed"] = len(ni_s)
    else:
        criteria["C5_NoProfitLoss"] = 0

    total = sum(criteria.values())
    max_pts = len(criteria)
    qualifies = total == max_pts

    return {
        "symbol": symbol,
        "qualifies": qualifies,
        "score": f"{total}/{max_pts}",
        "criteria": criteria,
        "details": details,
    }


def display_coffee_can(result: dict):
    """Print Coffee Can screen result with per-criterion pass/fail."""
    section("Coffee Can Portfolio Screen")
    if "error" in result:
        print(f"  ⚠️  {result['error']}")
        return

    qualifies = result["qualifies"]
    badge = "\033[92m✔ QUALIFIES\033[0m" if qualifies else "\033[91m✘ DOES NOT QUALIFY\033[0m"
    print(f"\n  Result: {badge}   ({result['score']} criteria met)")

    print("\n  ── Criteria ──────────────────────────────────────────────────")
    labels = {
        "C1_RevenuCAGR_gt10": "C1  Revenue CAGR > 10%",
        "C2_ROCE_gt15": "C2  ROCE > 15% (avg)",
        "C3_LowDebt": "C3  Debt/Equity < 1",
        "C4_MCap_ge500Cr": "C4  Market Cap ≥ ₹500 Cr",
        "C5_NoProfitLoss": "C5  No loss-making year",
    }
    for key, label in labels.items():
        val = result["criteria"].get(key, 0)
        tick = "\033[92m✔\033[0m" if val else "\033[91m✘\033[0m"
        print(f"    {tick}  {label}")

    print("\n  ── Supporting Data ───────────────────────────────────────────")
    for k, v in result["details"].items():
        row(k.replace("_", " "), str(v))


# ═══════════════════════════════════════════════════════════════════════════════
# Display functions for the daily report
# ═══════════════════════════════════════════════════════════════════════════════


def display_nse_price(eq_quote: dict, trade_info: dict):
    """Live NSE price, volume, 52-week range, and price band."""
    section("NSE — Live Price & Trade Info")
    pi = eq_quote.get("priceInfo", {})
    md = eq_quote.get("metadata", {})
    si = eq_quote.get("securityInfo", {})
    ti = trade_info.get("trade_info", {})

    ltp = pi.get("lastPrice") or pi.get("ltp")
    prev = pi.get("previousClose") or pi.get("prevClose")
    change = pi.get("change")
    pchg = pi.get("pChange")
    open_ = pi.get("open")
    high = (pi.get("intraDayHighLow") or {}).get("max") or pi.get("dayHigh")
    low = (pi.get("intraDayHighLow") or {}).get("min") or pi.get("dayLow")
    vol = ti.get("totalTradedVolume") or pi.get("totalTradedVolume")
    val = ti.get("totalTradedValue") or pi.get("totalTradedValue")
    vwap = pi.get("vwap")
    w52h = (pi.get("weekHighLow") or {}).get("max")
    w52l = (pi.get("weekHighLow") or {}).get("min")
    pb = pi.get("priceBand") or {}
    upper = pb.get("upper") or ti.get("priceBandHigh")
    lower = pb.get("lower") or ti.get("priceBandLow")

    row("Company", md.get("companyName") or md.get("symbol", "—"))
    row("ISIN", md.get("isin", "—"))
    row("Series", md.get("series", "—"))
    row("Industry", md.get("industry", "—"))
    row("Face Value", fmt(si.get("faceValue")))
    print()
    row("Last Traded Price", fmt(ltp))
    row("Prev Close", fmt(prev))
    row("Change", f"{fmt(change)}  {pct(pchg)}")
    row("Open", fmt(open_))
    row("Day High", fmt(high))
    row("Day Low", fmt(low))
    row("VWAP", fmt(vwap))
    print()
    row("52-Week High", fmt(w52h))
    row("52-Week Low", fmt(w52l))
    row("Volume", f"{int(vol):,}" if vol else "N/A")
    val_cr = val / 1e7 if val else None
    row("Traded Value", fmt(val_cr, decimals=2) + " Cr" if val_cr else "N/A")
    print()
    row("Price Band Upper", fmt(upper))
    row("Price Band Lower", fmt(lower))


def display_bse_price(bse_data: dict):
    """
    BSE OHLC quote.

    BUG FIX (original): assumed exact key names ('LastRate', 'OpenRate', etc.)
    that don't always exist.  We now probe multiple candidate names per field.
    """
    section("BSE — Live Quote (OHLC)")
    q = bse_data.get("quote", {})
    if not q:
        print("  No BSE data available.")
        return

    def first(*keys):
        for k in keys:
            v = q.get(k)
            if v is not None:
                return v
        return None

    row("BSE Scrip Code", str(bse_data.get("scrip_code", "—")))
    row("LTP", fmt(first("LastRate", "CurrRate", "ltp", "last")))
    row("Open", fmt(first("OpenRate", "Open", "open")))
    row("High", fmt(first("High", "DayHigh", "dayHigh")))
    row("Low", fmt(first("Low", "DayLow", "dayLow")))
    row("Prev Close", fmt(first("PrevRate", "PrevClose", "prevClose", "Prev")))
    raw_vol = first("Volume", "TotalVolume", "volume", "TrdVol") or 0
    row("Volume", f"{int(raw_vol):,}")
    raw_mc = first("Mktcap", "MktCap", "MarketCap", "marketCap")
    mc_cr = raw_mc / 1e7 if raw_mc else None
    row("Market Cap", fmt(mc_cr, decimals=2) + " Cr" if mc_cr else "N/A")


def display_corporate_actions(nse_actions: list, bse_actions: list):
    """Upcoming dividends, bonuses, splits from NSE and BSE."""
    section("Upcoming Corporate Actions")
    print("  NSE:")
    if nse_actions:
        for a in nse_actions:
            date = a.get("exDate") or a.get("recordDate") or "—"
            purpose = a.get("purpose") or a.get("subject") or "—"
            print(f"    • {date:<14} {purpose}")
    else:
        print("    No upcoming actions found.")

    print("\n  BSE:")
    if bse_actions:
        for a in bse_actions:
            date = a.get("exDate") or a.get("Ex_Date") or "—"
            purpose = a.get("purpose") or a.get("Purpose") or "—"
            print(f"    • {date:<14} {purpose}")
    else:
        print("    No upcoming actions found.")


def display_board_meetings(meetings: list):
    """Upcoming board meeting dates and purpose."""
    section("Board Meetings")
    if not meetings:
        print("  No upcoming board meetings.")
        return
    for m in meetings:
        date = m.get("meetingDate") or m.get("bm_date") or "—"
        purpose = m.get("purpose") or m.get("bm_purpose") or "—"
        print(f"  • {date:<14} {purpose}")


def display_option_chain(oc_data: dict):
    """Max Pain and Put-Call Ratio from NSE option chain."""
    section("F&O — Option Chain Summary")
    if not oc_data:
        print("  Not an F&O stock or data unavailable.")
        return
    maxpain = oc_data.get("maxpain")
    pcr_val = oc_data.get("pcr")
    row("Max Pain Strike", fmt(maxpain) if maxpain else "N/A")
    row("Put-Call Ratio (PCR)", f"{pcr_val:.2f}" if pcr_val is not None else "N/A")
    if pcr_val is not None:
        row("Sentiment", "Bullish (PCR > 1)" if pcr_val > 1 else "Bearish (PCR ≤ 1)")


def display_historical_summary(history: list):
    """
    30-day historical closing-price summary.

    BUG FIX (original): only tried 'CH_CLOSING_PRICE' and 'close'; if the
    library returned a different key every record silently became 0 and was
    discarded → "Insufficient data."  We now probe 5 candidate key names and
    surface the actual keys seen on failure.
    """
    section("Recent Historical Data (Last 30 Days)")
    if not history:
        print("  No historical data returned.")
        return

    CLOSE_KEYS = ("CH_CLOSING_PRICE", "close", "CLOSE", "ClosePrice", "LTP")
    closes = []
    for rec in history:
        for key in CLOSE_KEYS:
            v = rec.get(key)
            if v:
                try:
                    closes.append(float(v))
                    break
                except (TypeError, ValueError):
                    pass

    if len(closes) < 2:
        sample_keys = list(history[0].keys()) if history else []
        print("  Insufficient closing-price data.")
        print(f"  Sample record keys: {sample_keys}")
        return

    row("Days of data", str(len(closes)))
    row("Period High", fmt(max(closes)))
    row("Period Low", fmt(min(closes)))
    row("Average Close", fmt(sum(closes) / len(closes)))
    period_ret = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0
    row("Period Return", pct(period_ret))


def display_bulk_deals(deals: list, symbol: str):
    """Today's bulk deals filtered for the queried symbol."""
    section("Today's Bulk Deals (NSE)")
    sym_deals = [d for d in deals if str(d.get("symbol", "")).strip().upper() == symbol.upper()]
    if not sym_deals:
        print(f"  No bulk deals for {symbol} today.")
        return
    for d in sym_deals:
        client = d.get("clientname") or "—"
        quantity = d.get("quantity") or "—"
        price = fmt(d.get("price"))
        bs = (d.get("buysell") or "—").strip()
        print(f"  • {bs:<5} {str(quantity):>12} shares @ {price}  [{client}]")


# ── Main report + scan orchestration ─────────────────────────────────────────


def run(
    symbol: str, show_fno: bool = False, output_format: str = "text", run_scans: bool = False
) -> dict:
    """
    Generate a daily stock report for one NSE symbol, optionally including
    the three quantitative scans (Darvas, Piotroski, Coffee Can).

    Returns the collected data dict (useful for batch runs).
    """
    symbol = symbol.upper().strip()
    w = 60
    print(f"\n{'=' * w}")
    print(f"  📊  DAILY STOCK REPORT — {symbol}")
    print(f"  ⏱  Generated: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'=' * w}")

    all_data = {"symbol": symbol, "timestamp": datetime.now().isoformat()}

    with (
        NSE(download_folder=str(DOWNLOAD_DIR), server=False) as nse,
        BSE(download_folder=str(DOWNLOAD_DIR)) as bse,
    ):

        eq_quote = fetch_nse_equity_quote(nse, symbol)
        trade_info = fetch_nse_quote(nse, symbol)
        nse_meta = fetch_nse_meta(nse, symbol)
        nse_actions = fetch_nse_actions(nse, symbol)
        board_meetings = fetch_nse_board_meetings(nse, symbol)
        history = fetch_nse_historical(nse, symbol, days=30)
        bulk_deals = fetch_bulk_deals(nse)
        bse_data = fetch_bse_data(bse, symbol)
        oc_data = fetch_nse_option_chain(nse, symbol) if show_fno else {}

    # ── Three quantitative scans ──────────────────────────────────────────────
    darvas_result = {}
    piotroski_result = {}
    coffee_result = {}

    if run_scans:
        print("  Running quantitative scans …")
        # Darvas needs 90 days of OHLC from nsepython
        ohlc_df = fetch_ohlc_history(symbol, days=90)
        darvas_result = compute_darvas_box(ohlc_df)
        piotroski_result = compute_piotroski_score(symbol)
        coffee_result = compute_coffee_can(symbol)

    # ── Text display ──────────────────────────────────────────────────────────
    if output_format == "text":
        display_nse_price(eq_quote, trade_info)
        display_bse_price(bse_data)
        display_corporate_actions(nse_actions, bse_data.get("actions", []))
        display_board_meetings(board_meetings)
        if show_fno:
            display_option_chain(oc_data)
        display_historical_summary(history)
        display_bulk_deals(bulk_deals, symbol)

        if run_scans:
            display_darvas_box(darvas_result)
            display_piotroski_score(piotroski_result)
            display_coffee_can(coffee_result)

        print(f"\n{'=' * w}")
        print("  Data: NSE India · BSE India · nsepython · yfinance")
        print(f"{'=' * w}\n")

    # ── JSON output ───────────────────────────────────────────────────────────
    elif output_format == "json":
        all_data.update(
            {
                "nse": {
                    "equityQuote": eq_quote,
                    "tradeInfo": trade_info,
                    "meta": nse_meta,
                    "actions": nse_actions,
                    "boardMeetings": board_meetings,
                    "optionChain": {k: v for k, v in oc_data.items() if k != "raw"},
                    "history": history,
                    "bulkDeals": [
                        d for d in bulk_deals if str(d.get("symbol", "")).strip().upper() == symbol
                    ],
                },
                "bse": bse_data,
                "scans": (
                    {
                        "darvas": darvas_result,
                        "piotroski": piotroski_result,
                        "coffee_can": coffee_result,
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


# ── Nifty 50 batch report ─────────────────────────────────────────────────────


def run_nifty50_batch(
    show_fno: bool = False,
    output_format: str = "json",
    run_scans: bool = False,
    symbols: list = None,
) -> list:
    """
    Run the daily report for every Nifty 50 stock (or a custom list).
    Saves one file per stock and writes a summary CSV.
    """
    targets = symbols or NIFTY_50_SYMBOLS
    print(f"\n{'#' * 60}")
    print(f"  NIFTY 50 BATCH — {len(targets)} stocks")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#' * 60}")

    results, failed = [], []
    for i, sym in enumerate(targets, 1):
        print(f"\n[{i:02d}/{len(targets)}] {sym}")
        try:
            data = run(sym, show_fno=show_fno, output_format=output_format, run_scans=run_scans)
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
    """One-row-per-symbol summary CSV from batch results."""
    rows = []
    for d in results:
        sym = d.get("symbol", "")
        pi = d.get("nse", {}).get("equityQuote", {}).get("priceInfo", {})
        base = {
            "Symbol": sym,
            "LTP": pi.get("lastPrice") or pi.get("ltp"),
            "Change%": pi.get("pChange"),
            "DayHigh": (pi.get("intraDayHighLow") or {}).get("max") or pi.get("dayHigh"),
            "DayLow": (pi.get("intraDayHighLow") or {}).get("min") or pi.get("dayLow"),
            "52wHigh": (pi.get("weekHighLow") or {}).get("max"),
            "52wLow": (pi.get("weekHighLow") or {}).get("min"),
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
                    "Darvas_BoxBot": darv.get("box_bottom"),
                    "Piotroski_Score": piofr.get("f_score"),
                    "CoffeeCan": "YES" if coff.get("qualifies") else "NO",
                    "CoffeeCan_Score": coff.get("score"),
                }
            )
        rows.append(base)

    if rows:
        tag = "nifty50_scan" if include_scans else "nifty50"
        out = DOWNLOAD_DIR / f"{tag}_summary_{datetime.today().strftime('%Y%m%d')}.csv"
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f"\n  📊  Summary CSV → {out}")


# ── Convenience: run all three scans standalone ───────────────────────────────


def run_scans_only(symbol: str) -> dict:
    """
    Run just the three quantitative scans for a symbol without the full
    daily report.  Useful for quick screening in Colab.
    """
    symbol = symbol.upper().strip()
    print(f"\n{'=' * 60}\n  🔍  SCANS — {symbol}\n{'=' * 60}")
    ohlc = fetch_ohlc_history(symbol, days=90)
    darv = compute_darvas_box(ohlc)
    piotr = compute_piotroski_score(symbol)
    coff = compute_coffee_can(symbol)
    display_darvas_box(darv)
    display_piotroski_score(piotr)
    display_coffee_can(coff)
    return {"darvas": darv, "piotroski": piotr, "coffee_can": coff}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Strip Jupyter/Colab internal args so argparse doesn't misread them.
    filtered = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "-f" and i + 1 < len(sys.argv):
            i += 2
        elif arg.startswith("/root/.local/share/jupyter/runtime/kernel-"):
            i += 1
        else:
            filtered.append(arg)
            i += 1

    parser = argparse.ArgumentParser(
        description="Daily NSE+BSE report with Darvas Box, Piotroski, and Coffee Can scans."
    )
    parser.add_argument("symbol", nargs="?", help="NSE ticker (e.g. RELIANCE, TCS)")
    parser.add_argument(
        "--fno", action="store_true", default=False, help="Fetch F&O option chain, PCR, Max Pain"
    )
    parser.add_argument("--output", choices=["text", "json"], default="text")
    parser.add_argument(
        "--scans",
        action="store_true",
        default=False,
        help="Run Darvas Box, Piotroski F-Score, Coffee Can screen",
    )
    parser.add_argument(
        "--nifty50",
        action="store_true",
        default=False,
        help="Batch report for all 50 Nifty 50 constituents",
    )

    args = parser.parse_args(filtered[1:])

    if args.nifty50:
        run_nifty50_batch(show_fno=args.fno, output_format=args.output, run_scans=args.scans)
    else:
        if not args.symbol:
            print("No symbol given — defaulting to RELIANCE.")
            args.symbol = "RELIANCE"
        run(args.symbol, show_fno=args.fno, output_format=args.output, run_scans=args.scans)
