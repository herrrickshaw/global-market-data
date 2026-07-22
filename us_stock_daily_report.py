# us_stock_daily_report.py
# ==========================
# Daily stock report for US-listed equities (NYSE / NASDAQ / AMEX).
# All data is sourced from Yahoo Finance via the yfinance library.
#
# Sections per stock:
#   • Live quote, valuation ratios, analyst consensus
#   • Corporate actions (dividends, splits, next earnings)
#   • Options summary (PCR, Max Pain) — nearest expiry
#   • Insider trades and top institutional holders
#   • 30-day historical price summary
#   • Latest news headlines
#
# Quantitative scans (--scans flag):
#   • Darvas Box       — technical momentum breakout
#   • Piotroski F-Score — 9-point financial-strength score
#   • Coffee Can Screen — US-adapted quality + growth filter
#
# Install dependencies (run once):
#   pip install yfinance pandas openpyxl
#
# Single-stock usage:
#   python us_stock_daily_report.py AAPL
#   python us_stock_daily_report.py NVDA --options --scans
#   python us_stock_daily_report.py MSFT --output json
#
# Batch usage:
#   python us_stock_daily_report.py --dow30
#   python us_stock_daily_report.py --dow30 --scans --output json
#   python us_stock_daily_report.py --nasdaq50
#
# Colab quick-start:
#   !pip install yfinance pandas openpyxl
#   from us_stock_daily_report import run, run_scans_only, run_batch
#   run("AAPL", run_scans=True)
#   run_batch(symbols=["AAPL","MSFT","NVDA","AMZN","META"], run_scans=True)

# ── Standard library ──────────────────────────────────────────────────────────
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# ── Third-party ───────────────────────────────────────────────────────────────
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")


# ── Constants ─────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = Path("./us_stock_data")
DOWNLOAD_DIR.mkdir(exist_ok=True)

DARVAS_CONFIRM = 3  # consecutive days a high/low must hold to confirm a box

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


# ── Formatting helpers ────────────────────────────────────────────────────────


def fmt(val, prefix="$", decimals=2):
    """Return 'prefix + comma-formatted number' or 'N/A' on bad input."""
    try:
        return f"{prefix}{float(val):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(val) if val else "N/A"


def fmt_large(val, prefix="$"):
    """Format large dollar values with B/M/K suffix (e.g. $4.48T, $182.5B)."""
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


def row(label, value, width=30):
    print(f"  {label:<{width}} {value}")


# ── yfinance helpers ──────────────────────────────────────────────────────────


def _get_ticker(symbol: str) -> yf.Ticker:
    """Create a yfinance Ticker; US symbols need no suffix."""
    return yf.Ticker(symbol.upper().strip())


def _yf_financials(ticker: yf.Ticker):
    """
    Retrieve income statement, balance sheet, and cash flow.

    Handles the yfinance API rename (financials→income_stmt, cashflow→cash_flow).

    BUG FIX: Never use Python `or` between two DataFrame expressions — if the
    first attribute returns a non-empty DataFrame, `or` tries to evaluate its
    truth value and raises ValueError ("ambiguous truth value of a DataFrame").
    We use explicit None-and-empty checks instead.
    """

    def _first_df(*attrs):
        for attr in attrs:
            df = getattr(ticker, attr, None)
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                return df
        return None

    inc = _first_df("income_stmt", "financials")
    bal = _first_df("balance_sheet")
    cf = _first_df("cash_flow", "cashflow")
    return inc, bal, cf


def _row(df, *row_names, col: int = 0):
    """
    Safely fetch a scalar from a yfinance financial DataFrame.
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


# ── Data-fetch functions ──────────────────────────────────────────────────────


def fetch_quote(ticker: yf.Ticker) -> dict:
    """
    Return a unified quote dict from fast_info (for speed) supplemented by
    info (for valuation ratios, analyst data, and metadata).

    fast_info is a lightweight endpoint that avoids the full info payload;
    we fall back to info for fields fast_info doesn't carry.
    """
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
        # Price data — prefer fast_info, fallback to info
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
        # Valuation
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "trailingEps": info.get("trailingEps"),
        "forwardEps": info.get("forwardEps"),
        "pegRatio": info.get("pegRatio"),
        "priceToBook": info.get("priceToBook"),
        "dividendYield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "shortRatio": info.get("shortRatio"),
        # Analyst
        "targetMean": info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationKey", "—").upper(),
        "analystCount": info.get("numberOfAnalystOpinions"),
        # Identity
        "name": info.get("shortName") or info.get("longName", "—"),
        "exchange": info.get("exchange") or fi_get("exchange") or "—",
        "sector": info.get("sector", "—"),
        "industry": info.get("industry", "—"),
        "country": info.get("country", "US"),
    }


def fetch_historical(ticker: yf.Ticker, period: str = "1mo") -> pd.DataFrame:
    """Fetch OHLCV history for the given period (e.g. '1mo', '3mo', '6mo')."""
    try:
        df = ticker.history(period=period, auto_adjust=True)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception as e:
        print(f"  [Historical error] {e}")
        return pd.DataFrame()


def fetch_corporate_actions(ticker: yf.Ticker) -> dict:
    """
    Fetch dividends, stock splits, next earnings, and ex-dividend date.
    yfinance.calendar returns a dict with structured earnings and dividend dates.
    """
    result = {"dividends": [], "splits": [], "calendar": {}}
    try:
        acts = ticker.actions
        if acts is not None and not acts.empty:
            # Most recent 5 dividends and splits
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
    Fetch the nearest-expiry options chain and compute:
      • Put-Call Ratio (PCR) by open interest
      • Max Pain strike — the price where aggregate option-buyer losses peak,
        i.e., where market-maker obligations are minimized.

    Max Pain formula:
      For each candidate strike P:
        pain(P) = Σ max(0, P − K) × call_OI(K)   ← in-the-money calls cost MMs
                + Σ max(0, K − P) × put_OI(K)    ← in-the-money puts cost MMs
      Max Pain = argmin_P pain(P)
    """
    try:
        expiries = ticker.options
        if not expiries:
            return {}

        # Use nearest expiry for the most liquid / relevant data.
        chain = ticker.option_chain(expiries[0])
        calls = chain.calls.copy()
        puts = chain.puts.copy()

        call_oi_total = calls["openInterest"].fillna(0).sum()
        put_oi_total = puts["openInterest"].fillna(0).sum()
        pcr = put_oi_total / call_oi_total if call_oi_total > 0 else None

        # Build strike → OI dicts for pain computation.
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
    """Fetch the 5 most recent insider transactions."""
    try:
        df = ticker.insider_transactions
        if df is None or df.empty:
            return []
        df = df.sort_values("Start Date", ascending=False).head(5)
        rows = []
        for _, r in df.iterrows():
            rows.append(
                {
                    "insider": r.get("Insider", "—"),
                    "position": r.get("Position", "—"),
                    "transaction": r.get("Transaction", "—"),
                    "shares": r.get("Shares"),
                    "value": r.get("Value"),
                    "date": str(r.get("Start Date", "—")),
                }
            )
        return rows
    except Exception as e:
        print(f"  [Insider trades error] {e}")
        return []


def fetch_institutional_holders(ticker: yf.Ticker) -> list:
    """Fetch the top 5 institutional holders by shares held."""
    try:
        df = ticker.institutional_holders
        if df is None or df.empty:
            return []
        return df.head(5).to_dict(orient="records")
    except Exception as e:
        print(f"  [Institutional holders error] {e}")
        return []


def fetch_news(ticker: yf.Ticker) -> list:
    """Fetch the 5 most recent news headlines for the stock."""
    try:
        news = ticker.news or []
        result = []
        for item in news[:5]:
            # yfinance news structure varies; handle both old and new formats
            content = item.get("content") or item
            title = content.get("title") if isinstance(content, dict) else item.get("title", "")
            pub = content.get("pubDate") or item.get("providerPublishTime", "")
            # Convert Unix timestamp if needed
            if isinstance(pub, (int, float)):
                pub = datetime.utcfromtimestamp(pub).strftime("%Y-%m-%d %H:%M")
            result.append({"title": title, "published": str(pub)[:16]})
        return result
    except Exception as e:
        print(f"  [News error] {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 1 — DARVAS BOX  (yfinance OHLC history)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_darvas_box(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """
    Detect a Darvas Box and classify the current price relative to it.

    ── Algorithm (Nicolas Darvas, 1960) ─────────────────────────────────────
    Nicolas Darvas, a ballroom dancer who turned $25K into $2M by 1960,
    developed a simple box-detection system for momentum stocks:

      Box Top   : The most recent high that was NOT exceeded for `confirm`
                  consecutive trading days — confirmed resistance ceiling.

      Box Bottom: The lowest low after the box-top day that held for
                  `confirm` days — confirmed support floor.

      Signal    : BREAKOUT_BUY  → today's close > box top  (momentum entry)
                  BREAKDOWN_SELL → today's close < box bottom (exit / short)
                  IN_BOX         → price consolidating inside the box (wait)

    KEY DESIGN RULE: Box formation uses ONLY historical bars (all bars except
    the last).  The current bar is deliberately excluded so its low cannot
    pull the box bottom down and make a breakdown undetectable.

    Args:
        df      : OHLC DataFrame from ticker.history() — columns High/Low/Close
        confirm : Days the high/low must hold unbroken (default 3)
    """
    if df is None or df.empty:
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": "Empty OHLC DataFrame",
        }

    # ── Locate OHLC columns (yfinance uses 'High', 'Low', 'Close') ────────────
    def find_col(df, candidates):
        for c in candidates:
            match = next((col for col in df.columns if c.upper() in col.upper()), None)
            if match:
                return match
        return None

    h_col = find_col(df, ["High", "CH_TRADE_HIGH_PRICE", "DAYHIGH"])
    l_col = find_col(df, ["Low", "CH_TRADE_LOW_PRICE", "DAYLOW"])
    c_col = find_col(df, ["Close", "CH_CLOSING_PRICE", "LAST"])

    if not all([h_col, l_col, c_col]):
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": f"Could not identify OHLC columns in {list(df.columns)}",
        }

    all_highs = pd.to_numeric(df[h_col], errors="coerce").fillna(0).tolist()
    all_lows = pd.to_numeric(df[l_col], errors="coerce").fillna(0).tolist()
    all_closes = pd.to_numeric(df[c_col], errors="coerce").fillna(0).tolist()

    if len(all_closes) < confirm + 5:
        return {
            "signal": "INSUFFICIENT_DATA",
            "box_top": None,
            "box_bottom": None,
            "note": f"Need ≥ {confirm + 5} bars; got {len(all_closes)}",
        }

    # Separate the current bar from the history used for box construction.
    current = all_closes[-1]
    highs = all_highs[:-1]
    lows = all_lows[:-1]
    n = len(highs)

    # ── Step 1: most recent confirmed box top ─────────────────────────────────
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
            "note": "No confirmed box top in look-back window",
        }

    # ── Step 2: confirmed box bottom (from box-top day onward, history only) ──
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

    if box_bottom is None:
        valid = [l for l in segment if l > 0]
        box_bottom = min(valid) if valid else None

    if box_bottom is None:
        return {
            "signal": "NO_BOX",
            "box_top": box_top,
            "box_bottom": None,
            "note": "Could not confirm a box bottom",
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
    """Print Darvas Box scan result."""
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
        row("Box Bottom", fmt(result["box_bottom"]))
        row("Current Price", fmt(result.get("current_price")))
        row("Box Range", fmt(result.get("box_range"), prefix="$"))
        row("Position in Box", f"{result.get('position_in_box_pct', 0):.1f}%")
        row("Upside to Top", f"{result.get('upside_to_top_pct', 0):.2f}%")
        row("Confirmation (days)", str(result.get("confirm_days", DARVAS_CONFIRM)))
        row("OHLC bars used", str(result.get("data_points", "—")))
    elif result.get("note"):
        print(f"  Note: {result['note']}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 2 — PIOTROSKI F-SCORE  (yfinance annual financials)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_piotroski_score(ticker: yf.Ticker, symbol: str = "") -> dict:
    """
    Piotroski F-Score (0–9) from annual financial statements.

    Joseph Piotroski (2000) showed this 9-point accounting score predicts
    one-year outperformance.  Score ≥ 7 → strong; ≤ 3 → weak.

    ── 9 Criteria ───────────────────────────────────────────────────────────
    Profitability (4 pts)
      F1  ROA > 0
      F2  Operating Cash Flow > 0
      F3  Δ ROA > 0 (improving)
      F4  Accruals: OCF/Assets > ROA (cash-backed earnings)

    Leverage & Liquidity (3 pts)
      F5  Δ Long-term debt ratio < 0 (less leverage)
      F6  Δ Current ratio > 0 (more liquidity)
      F7  No new shares issued (no dilution)

    Operating Efficiency (2 pts)
      F8  Δ Gross margin > 0
      F9  Δ Asset turnover > 0
    """
    inc, bal, cf = _yf_financials(ticker)
    if inc is None or inc.empty:
        return {"symbol": symbol, "error": "No income statement data available"}

    scores = {}
    details = {}

    # ── Profitability ─────────────────────────────────────────────────────────
    net_inc_0 = _row(inc, "Net Income", col=0)
    assets_0 = _row(bal, "Total Assets", col=0)
    roa_0 = (net_inc_0 / assets_0) if (net_inc_0 and assets_0) else None

    net_inc_1 = _row(inc, "Net Income", col=1)
    assets_1 = _row(bal, "Total Assets", col=1)
    roa_1 = (net_inc_1 / assets_1) if (net_inc_1 and assets_1) else None

    scores["F1_ROA_positive"] = 1 if (roa_0 and roa_0 > 0) else 0
    details["ROA_current_%"] = round(roa_0 * 100, 2) if roa_0 else "N/A"

    ocf_0 = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities", col=0)
    scores["F2_OCF_positive"] = 1 if (ocf_0 and ocf_0 > 0) else 0
    details["OCF_current_$M"] = round(ocf_0 / 1e6, 1) if ocf_0 else "N/A"

    if roa_0 is not None and roa_1 is not None:
        scores["F3_ROA_improving"] = 1 if roa_0 > roa_1 else 0
    else:
        scores["F3_ROA_improving"] = 0
    details["ROA_prev_%"] = round(roa_1 * 100, 2) if roa_1 else "N/A"

    if ocf_0 and assets_0 and roa_0 is not None:
        scores["F4_Accruals"] = 1 if (ocf_0 / assets_0) > roa_0 else 0
    else:
        scores["F4_Accruals"] = 0

    # ── Leverage & Liquidity ──────────────────────────────────────────────────
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

    sh_0 = _row(bal, "Share Issued", col=0)
    sh_1 = _row(bal, "Share Issued", col=1)
    scores["F7_No_dilution"] = (1 if sh_0 <= sh_1 else 0) if (sh_0 and sh_1) else 1
    details["Shares_curr_M"] = round(sh_0 / 1e6, 1) if sh_0 else "N/A"

    # ── Operating Efficiency ──────────────────────────────────────────────────
    rev_0 = _row(inc, "Total Revenue", col=0)
    gp_0 = _row(inc, "Gross Profit", col=0)
    rev_1 = _row(inc, "Total Revenue", col=1)
    gp_1 = _row(inc, "Gross Profit", col=1)
    gm_0 = (gp_0 / rev_0) if (gp_0 and rev_0) else None
    gm_1 = (gp_1 / rev_1) if (gp_1 and rev_1) else None
    if gm_0 is not None and gm_1 is not None:
        scores["F8_GrossMargin_up"] = 1 if gm_0 > gm_1 else 0
    else:
        scores["F8_GrossMargin_up"] = 0
    details["GrossMargin_curr_%"] = round(gm_0 * 100, 2) if gm_0 else "N/A"
    details["GrossMargin_prev_%"] = round(gm_1 * 100, 2) if gm_1 else "N/A"

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
    """Print Piotroski F-Score with per-criterion pass/fail."""
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
# SCAN 3 — COFFEE CAN PORTFOLIO SCREEN (US-adapted)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_coffee_can(ticker: yf.Ticker, symbol: str = "") -> dict:
    """
    US-adapted Coffee Can Portfolio screen.

    Robert Kirby (1984) described clients who literally forgot they owned stocks
    and found them worth far more years later.  Saurabh Mukherjea popularised
    the framework for India; here it is adapted for US markets.

    ── Criteria (ALL must pass for qualification) ──────────────────────────
    C1  Revenue CAGR > 10%       (over available history — up to 4 yrs free)
    C2  Return on Equity > 15%   (avg; measures how well equity is deployed)
    C3  Debt/Equity < 1          (capital-light, not over-leveraged)
    C4  Market Cap ≥ $1 Billion  (mid/large cap — avoids illiquid micro-caps)
    C5  No loss-making year      (consistent profitability)
    C6  Free Cash Flow > 0       (US bonus criterion — real cash generation)

    Data: yfinance (up to ~4 years of annual financials for free).
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

    criteria = {}
    details = {}

    # ── C1: Revenue CAGR > 10% ───────────────────────────────────────────────
    revs = series(inc, "Total Revenue")
    if len(revs) >= 2:
        years = len(revs) - 1
        cagr = ((revs[0] / revs[-1]) ** (1 / years) - 1) * 100 if revs[-1] > 0 else None
        criteria["C1_Revenue_CAGR_gt10"] = 1 if (cagr and cagr > 10) else 0
        details["Revenue_CAGR_%"] = round(cagr, 2) if cagr else "N/A"
        details["Revenue_years"] = years
    else:
        criteria["C1_Revenue_CAGR_gt10"] = 0
        details["Revenue_CAGR_%"] = "N/A"

    # ── C2: Return on Equity > 15% avg ───────────────────────────────────────
    # ROE = Net Income / Shareholders' Equity
    ni_s = series(inc, "Net Income")
    eq_s = series(
        bal,
        "Stockholders Equity",
        "Total Stockholder Equity",
        "Total Equity Gross Minority Interest",
    )
    roe_list = []
    for i in range(min(len(ni_s), len(eq_s))):
        if eq_s[i] > 0:
            roe_list.append(ni_s[i] / eq_s[i] * 100)

    if roe_list:
        avg_roe = sum(roe_list) / len(roe_list)
        criteria["C2_ROE_gt15"] = 1 if avg_roe > 15 else 0
        details["ROE_avg_%"] = round(avg_roe, 2)
        details["ROE_min_%"] = round(min(roe_list), 2)
    else:
        criteria["C2_ROE_gt15"] = 0
        details["ROE_avg_%"] = "N/A"

    # ── C3: Debt/Equity < 1 ──────────────────────────────────────────────────
    # yfinance info.debtToEquity is in percent in some builds (45.2 means 0.452×).
    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        de = de_raw / 100 if de_raw > 10 else de_raw
        criteria["C3_LowDebt"] = 1 if de < 1 else 0
        details["Debt_to_Equity"] = round(de, 2)
    else:
        ltd_s = series(bal, "Long Term Debt")
        if ltd_s and eq_s and eq_s[0] != 0:
            de = ltd_s[0] / abs(eq_s[0])
            criteria["C3_LowDebt"] = 1 if de < 1 else 0
            details["Debt_to_Equity"] = round(de, 2)
        else:
            criteria["C3_LowDebt"] = 0
            details["Debt_to_Equity"] = "N/A"

    # ── C4: Market Cap ≥ $1B ─────────────────────────────────────────────────
    mcap = info.get("marketCap")
    try:
        mcap_fi = ticker.fast_info.market_cap
        mcap = mcap or mcap_fi
    except Exception:
        pass
    criteria["C4_MCap_ge1B"] = 1 if (mcap and mcap >= 1e9) else 0
    details["Market_Cap"] = fmt_large(mcap) if mcap else "N/A"

    # ── C5: No loss year ─────────────────────────────────────────────────────
    if ni_s:
        all_profit = all(n > 0 for n in ni_s)
        criteria["C5_NoProfitLoss"] = 1 if all_profit else 0
        details["Loss_years"] = sum(1 for n in ni_s if n <= 0)
        details["Years_analysed"] = len(ni_s)
    else:
        criteria["C5_NoProfitLoss"] = 0

    # ── C6: Free Cash Flow > 0 (US bonus criterion) ───────────────────────────
    # Free Cash Flow = Operating Cash Flow − Capital Expenditure
    fcf_s = series(cf, "Free Cash Flow")
    if fcf_s:
        criteria["C6_FreeCashFlow_pos"] = 1 if fcf_s[0] > 0 else 0
        details["FCF_latest_$M"] = round(fcf_s[0] / 1e6, 1)
    else:
        # Compute OCF - CapEx if FCF row not directly available
        ocf_s = series(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex_s = series(cf, "Capital Expenditure", "Capital Expenditures")
        if ocf_s and capex_s:
            fcf = ocf_s[0] - abs(capex_s[0])
            criteria["C6_FreeCashFlow_pos"] = 1 if fcf > 0 else 0
            details["FCF_latest_$M"] = round(fcf / 1e6, 1)
        else:
            criteria["C6_FreeCashFlow_pos"] = 0
            details["FCF_latest_$M"] = "N/A"

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


# ── Display functions for the daily report ────────────────────────────────────


def display_price_summary(quote: dict):
    """Live price, valuation ratios, analyst consensus."""
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
    """Upcoming dividends, splits, and earnings calendar."""
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
    """PCR and Max Pain from nearest-expiry options."""
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
    """Recent insider buy/sell transactions."""
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
    """Top institutional holders by position size."""
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
    """30-day closing-price summary from OHLCV history."""
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
    """Recent news headlines."""
    section(f"Latest News — {symbol}")
    if not news:
        print("  No news available.")
        return
    for item in news:
        print(f"  • [{item.get('published',''):<16}] {item.get('title','')}")


# ── Main report function ──────────────────────────────────────────────────────


def run(
    symbol: str, show_options: bool = False, output_format: str = "text", run_scans: bool = False
) -> dict:
    """
    Generate a daily stock report for one US equity symbol.

    Args:
        symbol:        Ticker symbol (e.g. 'AAPL', 'NVDA')
        show_options:  Include options PCR and Max Pain analysis
        output_format: 'text' (console) or 'json' (saved to file)
        run_scans:     Run Darvas Box, Piotroski, and Coffee Can scans

    Returns:
        dict of all fetched and computed data (useful for batch runs)
    """
    symbol = symbol.upper().strip()
    w = 60
    print(f"\n{'=' * w}")
    print(f"  📊  US STOCK REPORT — {symbol}")
    print(f"  ⏱  Generated: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'=' * w}")

    ticker = _get_ticker(symbol)
    all_data = {"symbol": symbol, "timestamp": datetime.now().isoformat()}

    # ── Fetch all data ────────────────────────────────────────────────────────
    quote = fetch_quote(ticker)
    hist_df = fetch_historical(ticker, period="1mo")
    actions = fetch_corporate_actions(ticker)
    opts = fetch_options_data(ticker) if show_options else {}
    insiders = fetch_insider_trades(ticker)
    instit = fetch_institutional_holders(ticker)
    news = fetch_news(ticker)

    # ── Three quantitative scans ──────────────────────────────────────────────
    darvas_r = {}
    piotroski_r = {}
    coffee_r = {}

    if run_scans:
        print("  Running quantitative scans …")
        # Darvas needs 6 months of OHLC data
        hist_6mo = fetch_historical(ticker, period="6mo")
        darvas_r = compute_darvas_box(hist_6mo)
        piotroski_r = compute_piotroski_score(ticker, symbol)
        coffee_r = compute_coffee_can(ticker, symbol)

    # ── Text display ──────────────────────────────────────────────────────────
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

    # ── JSON output ───────────────────────────────────────────────────────────
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


# ── Batch report ──────────────────────────────────────────────────────────────


def run_batch(
    symbols: list = None,
    show_options: bool = False,
    output_format: str = "json",
    run_scans: bool = False,
) -> list:
    """
    Run reports for a list of symbols.  Defaults to Dow Jones 30.
    Saves a combined summary CSV alongside the per-symbol files.

    Examples:
        run_batch()                                          # Dow 30
        run_batch(symbols=NASDAQ_50, run_scans=True)
        run_batch(symbols=["AAPL","MSFT","NVDA"])
    """
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
    """Write a one-row-per-symbol summary CSV from batch results."""
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


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Daily US stock report with Darvas Box, Piotroski, and Coffee Can scans."
    )
    parser.add_argument("symbol", nargs="?", help="Ticker symbol (e.g. AAPL, NVDA, MSFT)")
    parser.add_argument(
        "--options",
        action="store_true",
        default=False,
        help="Include options PCR and Max Pain analysis",
    )
    parser.add_argument(
        "--scans",
        action="store_true",
        default=False,
        help="Run Darvas Box, Piotroski F-Score, Coffee Can screen",
    )
    parser.add_argument("--output", choices=["text", "json"], default="text")
    parser.add_argument(
        "--dow30",
        action="store_true",
        default=False,
        help="Batch report for all 30 Dow Jones components",
    )
    parser.add_argument(
        "--nasdaq50",
        action="store_true",
        default=False,
        help="Batch report for the top 50 NASDAQ-100 components",
    )

    args = parser.parse_args()

    if args.dow30:
        run_batch(
            symbols=DOW_JONES_30,
            show_options=args.options,
            output_format=args.output,
            run_scans=args.scans,
        )
    elif args.nasdaq50:
        run_batch(
            symbols=NASDAQ_50,
            show_options=args.options,
            output_format=args.output,
            run_scans=args.scans,
        )
    else:
        if not args.symbol:
            print("No symbol given — defaulting to AAPL.")
            args.symbol = "AAPL"
        run(args.symbol, show_options=args.options, output_format=args.output, run_scans=args.scans)
