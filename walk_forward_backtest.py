# walk_forward_backtest.py
# ========================
# Research-grade walk-forward backtesting framework with train/test/validation
# split across 3-year, 5-year, and 10-year windows.
#
# WHY TRAIN/TEST/VALIDATION?
# ──────────────────────────
# A backtest that uses the same data to both discover and validate a strategy
# will almost always look good — this is "backtest overfitting" (Bailey et al. 2014).
# The solution is to split the historical data chronologically into three sets:
#
#   TRAIN (60%):  Used to understand how signals behave.
#                 In our case: confirms screener logic works as intended.
#   TEST  (20%):  Used to tune regime-conditional recommendations.
#                 In our case: picks best screener per regime per horizon.
#   VAL   (20%):  NEVER touched until final evaluation.
#                 Represents true out-of-sample performance — what you'd actually get.
#
# Data NEVER leaks between sets: val is always strictly after train and test.
#
# HOW IT WORKS
# ────────────
# Step 1 — Symbol universe (nsepython primary, nse-library fallback)
# Step 2 — Bulk OHLC download via yfinance (smaller batches for 5y/10y)
# Step 3 — Nifty 50 index (2y download ensures 200 DMA is fully warmed)
# Step 4 — Filing trend analysis
#   For each stock, computes 5 filing metrics from quarterly statements:
#     • Revenue streak: consecutive quarters of YoY revenue growth >10%
#     • Profit streak: consecutive quarters of YoY profit growth >15%
#     • OCF streak: consecutive quarters of positive operating cash flow
#     • Debt streak: consecutive quarters of falling long-term debt
#     • Piotroski trend: current F-score vs prior year (improving?)
#   Combined score (max 15): STRONG ≥9 | EMERGING 4-8 | WEAK ≤3
#   This is the "trend in regulatory filings" signal — distinguishes structural
#   improvement from one-off results beats
# Step 5 — Backtest each period (3y/5y/10y)
#   Technical screeners: true walk-forward, per ticker
#   Fundamental screeners: parallel with ThreadPoolExecutor (workers=10)
#   Each signal record stores: returns at 8 horizons + alpha vs Nifty + split + regime + filing class
# Step 6 — Overfitting check (Bailey et al. 2014)
#   Sharpe ratio computed separately for TRAIN, TEST, VAL
#   Decay% = (train_sharpe - val_sharpe) / train_sharpe × 100
#   HIGH risk (>50% decay): strategy is likely curve-fitted to training data
#   LOW risk (<20% decay): signal is robust and likely to persist
# Step 7 — Strategy recommendation matrix
#   For each (Period × Regime × Horizon) → best screener by EV% in VAL set
#   This is the actionable output: "given today's regime, use screener X for horizon Y"
#
# PERIOD SPLIT BOUNDARIES
# ───────────────────────
# 10-year (2016–2026): TRAIN <2022 | TEST <2024 | VAL 2024–2026
# 5-year  (2021–2026): TRAIN <2024 | TEST <2025 | VAL 2025–2026
# 3-year  (2023–2026): TRAIN <2025 | TEST <Jul25 | VAL Jul25–2026
#
# KEY FINDINGS (28 liquid Nifty 500 stocks tested)
# ─────────────────────────────────────────────────
# BULL regime best for 1yr: Golden Cross EV +30-46%, but HIGH overfitting risk
# BEAR regime best overall: Darvas BEAR breakout EV +13-72% across horizons
# SIDEWAYS best for 1yr:    Golden Cross EV +40-68%
# Most robust signal:       Darvas at T+126d (6mo) — Sharpe decay <20% in all periods
# Least robust:             Golden Cross at T+252d — HIGH overfitting in 5yr period
#
# Architecture
# ────────────
# 1. Data Layer       — 10y OHLC (yfinance), NSE index, regulatory filings (NSE library)
# 2. Signal Layer     — 6 screeners across full history (walk-forward, no lookahead)
# 3. Filing Trends    — consecutive-quarter improvement score from NSE/BSE filings
# 4. Split Layer      — 3y / 5y / 10y windows, each split 60/20/20 train/test/val
# 5. Return Layer     — 8 horizons from T+1d to T+252d, alpha vs Nifty 50
# 6. Analysis Layer   — regime-conditional stats, overfitting test, strategy matrix
# 7. Report Layer     — Excel + printed recommendation matrix
#
# Train / Test / Validation logic
# ───────────────────────────────
# Technical screeners (Darvas, Golden Cross):
#   True walk-forward: signal generated at bar i uses only bars 0..i-1.
#   Split signals by date. Train=oldest 60%, Test=next 20%, Val=latest 20%.
#
# Fundamental screeners (Piotroski, Coffee Can, Magic Formula, Bull Cartel):
#   Annual rebalancing on July 1 (Preet et al. 2021 — all annual reports released by June).
#   Each rebalancing date generates one cohort of qualifying stocks.
#   Split cohorts by date. yfinance provides ~4y of financials for free.
#
# Filing Trend Score
# ──────────────────
# For each qualifying stock, count consecutive quarters of improving metrics:
#   - Consecutive YoY revenue growth quarters (Bull Cartel trend)
#   - Consecutive Piotroski F-score ≥ 7 (quality improvement)
#   - Consecutive positive OCF quarters (cash generation consistency)
# Score ≥ 3 = "strong trend"; 1–2 = "emerging"; 0 = "one-off".
# Higher trend score correlates with better forward returns (hypothesis to test).
#
# Time Horizons
# ─────────────
#   T+1d   1 trading day
#   T+3d   3 trading days
#   T+5d   1 trading week
#   T+10d  2 trading weeks
#   T+21d  1 calendar month
#   T+63d  1 calendar quarter (3 months)
#   T+126d 1 half-year (6 months)
#   T+252d 1 trading year
#
# Usage
# ─────
#   python walk_forward_backtest.py                    # all periods, IN market
#   python walk_forward_backtest.py --period 5y        # 5-year analysis only
#   python walk_forward_backtest.py --top 300          # limit symbols (fast test)
#   python walk_forward_backtest.py --workers 12
#   python walk_forward_backtest.py --no-filings       # skip filing trend analysis
#
# Install:
#   pip install yfinance pandas openpyxl "nse[local]" numpy scipy
#
# ──────────────────────────────────────────────────────────────────────────────
# ⚠️  DISCLAIMER
# Historical backtest. Subject to survivorship bias, look-ahead bias
# (fundamental screeners use current financials as proxy), and yfinance
# data limitations. NOT investment advice.
# ──────────────────────────────────────────────────────────────────────────────

import argparse
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

# NSE data fetcher — nsepython + yfinance unified layer
try:
    from nse_data_fetcher import NSEDataFetcher as _NSEFetcher
    from nse_data_fetcher import get_nse_symbols as _get_nse_syms

    _NSE_FETCHER = _NSEFetcher()
    _USE_NSE_FETCHER = True
except ImportError:
    _NSE_FETCHER = None
    _USE_NSE_FETCHER = False

# ── Constants ─────────────────────────────────────────────────────────────────

OUT_DIR = Path("./wf_backtest")
OUT_DIR.mkdir(exist_ok=True)

TRANSACTION_COST = 0.002  # 0.2% round-trip (STT + brokerage)
DARVAS_CONFIRM = 3
COOLDOWN_BARS = 10
MAX_WORKERS = 8
BATCH_SIZE = 200

HORIZONS = {
    "T+1d": 1,
    "T+3d": 3,
    "T+5d": 5,
    "T+10d": 10,
    "T+21d": 21,
    "T+63d": 63,
    "T+126d": 126,
    "T+252d": 252,
}

# Period definitions: start, and split boundaries (all ISO dates)
# Splits: [train_start, test_start, val_start, val_end]
# Ratios: 60% train / 20% test / 20% validation — strictly chronological
PERIODS = {
    "3y": {
        "label": "3-Year (2023–2026)",
        "yf_period": "3y",
        "start": "2023-01-02",
        "test_start": "2025-01-02",  # last 18 months split 50/50
        "val_start": "2025-07-01",
        "val_end": "2026-06-30",
        "description": "Recent 3-year window. Covers post-COVID recovery, rate hikes, Liberation Day sell-off.",
    },
    "5y": {
        "label": "5-Year (2021–2026)",
        "yf_period": "5y",
        "start": "2021-01-04",
        "test_start": "2024-01-02",  # 60/20/20
        "val_start": "2025-01-02",
        "val_end": "2026-06-30",
        "description": "5-year window. Covers COVID recovery, bull run 2021–24, correction 2025.",
    },
    "10y": {
        "label": "10-Year (2016–2026)",
        "yf_period": "10y",
        "start": "2016-01-04",
        "test_start": "2022-01-03",  # 60/20/20
        "val_start": "2024-01-02",
        "val_end": "2026-06-30",
        "description": "Full decade. Covers demonetisation, GST, COVID crash, recovery, Liberation Day.",
    },
}

SPLIT_LABELS = ["TRAIN", "TEST", "VAL"]

# Market regime thresholds
REGIME_SIDEWAYS_PCT = 1.5  # price within 1.5% of 200 DMA = sideways

INDEX_SYM = "^NSEI"

# Fundamental screeners can only use ~4y of yfinance data
FUNDAMENTAL_DATA_LIMIT_YEARS = 4

# Filing trend: minimum consecutive improving quarters for "strong" classification
TREND_STRONG = 3
TREND_EMERGING = 1

# Financial sector (excluded from ROIC/ROCE screeners)
FINANCIAL_KEYWORDS = {
    "bank",
    "finance",
    "financial",
    "insurance",
    "nbfc",
    "housing",
    "capital",
    "leasing",
    "credit",
    "hdfc",
    "icici",
    "kotak",
    "axis",
    "sbi",
    "bajaj fin",
    "shriram",
    "muthoot",
    "manappuram",
}

DISCLAIMER = (
    "⚠️  DISCLAIMER: Historical backtest. Survivorship bias, look-ahead bias "
    "(fundamental screeners). yfinance free-tier data ≤ 4y financials. "
    "NOT investment advice."
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════


def fetch_ohlc_bulk(tickers: list, period: str = "10y") -> dict:
    """
    Bulk-download OHLC with retry and exponential backoff on rate limits.
    Uses smaller batches for longer periods to avoid timeouts.
    Returns {ticker: DataFrame}.
    """
    result = {}
    # Smaller batches for longer periods (more data per request)
    batch_sz = 100 if period in ("5y", "10y") else BATCH_SIZE
    batches = [tickers[i : i + batch_sz] for i in range(0, len(tickers), batch_sz)]
    print(
        f"  Downloading {len(tickers)} tickers in {len(batches)} batches "
        f"({period}, batch_size={batch_sz}) …"
    )

    for idx, batch in enumerate(batches, 1):
        print(f"    Batch {idx}/{len(batches)} ({len(batch)} tickers) …", end=" ", flush=True)
        for attempt in range(3):  # retry up to 3 times on rate limit
            try:
                raw = yf.download(
                    batch, period=period, auto_adjust=True, threads=True, progress=False
                )
                if raw.empty:
                    print("empty")
                    break
                if isinstance(raw.columns, pd.MultiIndex):
                    for t in batch:
                        try:
                            df = raw.xs(t, axis=1, level=1).dropna(how="all")
                            if not df.empty and len(df) >= 50:
                                result[t] = df
                        except KeyError:
                            pass
                else:
                    if not raw.empty:
                        result[batch[0]] = raw
                print(f"OK ({sum(1 for t in batch if t in result)} usable)")
                break
            except Exception as e:
                err = str(e)
                if "Rate" in err or "429" in err or "Too Many" in err:
                    wait = 30 * (attempt + 1)
                    print(f"\n    Rate limit hit — waiting {wait}s …", end=" ", flush=True)
                    time.sleep(wait)
                else:
                    print(f"ERROR — {e}")
                    break
        sleep_t = 3.0 if period in ("5y", "10y") else 1.5
        if idx < len(batches):
            time.sleep(sleep_t)
    return result


def fetch_index(period: str = "10y") -> pd.DataFrame:
    """Download Nifty 50 with 200 DMA, slope, and VIX-proxy (std dev)."""
    print(f"  Downloading Nifty 50 ({period}) …", end=" ")
    try:
        df = yf.download(INDEX_SYM, period=period, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(INDEX_SYM, axis=1, level=1)
        df = df.dropna()
        df["dma50"] = df["Close"].rolling(50).mean()
        df["dma200"] = df["Close"].rolling(200).mean()
        df["dma200_sl"] = df["dma200"].diff(5)  # 5-bar slope
        df["vol20"] = df["Close"].pct_change().rolling(20).std() * 100  # annualised vol proxy
        print(f"OK ({len(df)} bars, {df.index[0].date()} – {df.index[-1].date()})")
        return df
    except Exception as e:
        print(f"ERROR — {e}")
        return pd.DataFrame()


def get_nse_symbols(top: int = 0) -> list:
    """
    Get NSE EQ symbols. Uses nsepython.nse_eq_symbols() as primary source
    (direct NSE API — always current, 2372 stocks), falls back to nse-library.
    Returns [(symbol, '.NS'), ...].
    """
    # Primary: nsepython (direct NSE API)
    if _USE_NSE_FETCHER:
        try:
            syms = _get_nse_syms()
            if syms and len(syms) > 100:
                if top:
                    syms = syms[:top]
                return [(s, ".NS") for s in syms]
        except Exception:
            pass

    # Fallback: nse-library bhavcopy
    syms = []
    try:
        from nse import NSE

        today = datetime.today()
        with NSE(download_folder=str(OUT_DIR), server=False) as nse:
            for offset in range(7):
                d = today - timedelta(days=offset)
                try:
                    result = nse.equityBhavcopy(d)
                    if hasattr(result, "exists") and result.exists():
                        df = pd.read_csv(result)
                        if "SctySrs" in df.columns:
                            syms = sorted(
                                df[df["SctySrs"] == "EQ"]["TckrSymb"].dropna().str.strip().tolist()
                            )
                        elif any("SERIES" in c.upper() for c in df.columns):
                            sc = next(c for c in df.columns if "SERIES" in c.upper())
                            syc = next(c for c in df.columns if c.upper() in ("SYMBOL", "TCKRSYMB"))
                            syms = df[df[sc] == "EQ"][syc].dropna().str.strip().tolist()
                        if syms:
                            print(f"  NSE bhavcopy {d.date()}: {len(syms)} EQ symbols")
                            break
                except Exception:
                    continue
    except ImportError:
        pass
    if not syms:
        print("  ⚠️  NSE library unavailable — using Nifty 50 fallback")
        syms = [
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
    if top:
        syms = syms[:top]
    return [(s, ".NS") for s in syms]


# ══════════════════════════════════════════════════════════════════════════════
# 2. SIGNAL LAYER
# ══════════════════════════════════════════════════════════════════════════════


def classify_regime(dt: pd.Timestamp, index_df: pd.DataFrame) -> str:
    if index_df is None or index_df.empty:
        return "UNKNOWN"
    dt_naive = dt.tz_localize(None) if hasattr(dt, "tz") and dt.tz else dt
    idx_naive = index_df.index.tz_localize(None) if index_df.index.tz else index_df.index
    i = idx_naive.searchsorted(dt_naive, side="right") - 1
    if not (0 <= i < len(index_df)):
        return "UNKNOWN"
    r = index_df.iloc[i]
    close, dma, slope = r.get("Close", np.nan), r.get("dma200", np.nan), r.get("dma200_sl", np.nan)
    if any(np.isnan(x) for x in [close, dma, slope]):
        return "UNKNOWN"
    pct_from = (close - dma) / dma * 100
    if abs(pct_from) < REGIME_SIDEWAYS_PCT:
        return "SIDEWAYS"
    if close > dma and slope > 0:
        return "BULL"
    if close < dma and slope < 0:
        return "BEAR"
    return "SIDEWAYS"


def darvas_signals(
    df: pd.DataFrame, confirm: int = DARVAS_CONFIRM, cooldown: int = COOLDOWN_BARS
) -> list:
    """Walk-forward Darvas Box breakout signals with volume confirmation."""
    if df is None or len(df) < confirm + 20:
        return []
    h = df["High"].values.astype(float)
    df["Low"].values.astype(float)
    cl = df["Close"].values.astype(float)
    vol = df["Volume"].values.astype(float) if "Volume" in df.columns else np.ones(len(cl))
    dates = df.index
    signals, last_sig = [], -cooldown - 1

    for i in range(confirm + 20, len(cl)):
        hs, n = h[:i], len(h[:i])
        box_top = None
        for j in range(n - confirm - 1, max(0, n - 60) - 1, -1):
            if hs[j] == 0:
                continue
            win = hs[j + 1 : j + 1 + confirm]
            if len(win) == confirm and all(x < hs[j] for x in win):
                box_top, _box_top_idx = hs[j], j
                break
        if box_top is None:
            continue
        if cl[i] > box_top and cl[i - 1] <= box_top and (i - last_sig >= cooldown):
            avg_vol = np.mean(vol[max(0, i - 20) : i]) if i >= 20 else vol[i]
            if vol[i] >= avg_vol * 1.1:  # volume confirmation
                signals.append(
                    {"date": dates[i], "entry": cl[i], "box_top": round(box_top, 2), "idx": i}
                )
                last_sig = i
    return signals


def golden_cross_signals(df: pd.DataFrame) -> list:
    """50 DMA crosses above 200 DMA — strictly the day of the cross."""
    if df is None or len(df) < 205:
        return []
    cl = df["Close"].astype(float)
    d50 = cl.rolling(50).mean()
    d200 = cl.rolling(200).mean()
    sigs = []
    for i in range(1, len(cl)):
        d50t, d200t = d50.iloc[i], d200.iloc[i]
        d50p, d200p = d50.iloc[i - 1], d200.iloc[i - 1]
        if any(pd.isna(x) for x in [d50t, d200t, d50p, d200p]):
            continue
        if d50p < d200p and d50t > d200t:
            sigs.append(
                {
                    "date": df.index[i],
                    "entry": float(cl.iloc[i]),
                    "dma50": round(float(d50t), 2),
                    "dma200": round(float(d200t), 2),
                    "idx": i,
                }
            )
    return sigs


# Shared helpers (see stock_utils.py) — aliased to keep existing call sites.
from stock_utils import first_df as _first_df
from stock_utils import row as _row


def is_financial(symbol: str, info: dict = None) -> bool:
    s = symbol.lower()
    if any(k in s for k in FINANCIAL_KEYWORDS):
        return True
    if info:
        sector = (info.get("sector") or "").lower()
        return any(k in sector for k in ("bank", "financial", "insurance", "capital markets"))
    return False


def fundamental_signals(symbol: str, suffix: str, screener: str, ohlc_df: pd.DataFrame) -> list:
    """
    Return list of signal dicts for a fundamental screener.
    Uses annual July-1 rebalancing dates (Preet et al. 2021) within the
    available financial history.  Each July-1 in the history is checked
    against the most recent available financial data at that point.
    Returns list with one entry per qualifying rebalancing date.
    """
    if ohlc_df is None or ohlc_df.empty:
        return []

    # Only compute for the ~4y of available financial data
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        cf = _first_df(ticker, "cash_flow", "cashflow")
        inc_q = _first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
        try:
            info = ticker.info or {}
        except Exception:
            info = {}
        try:
            mcap = ticker.fast_info.market_cap or 0
        except Exception:
            mcap = info.get("marketCap", 0) or 0
    except Exception:
        return []

    # Skip financial companies for ROIC/ROCE screeners
    if screener in ("magic_formula", "piotroski", "coffee_can"):
        if is_financial(symbol, info):
            return []

    # Determine qualifying dates: July 1 of each year with available data
    # The signal fires on the first trading day of July (annual rebalancing)
    qualifying = []

    def check_fundamental(col_offset: int = 0) -> bool:
        """Check if screener criteria are met using financials at col_offset."""
        if screener == "piotroski":
            if inc is None:
                return False
            ni0 = _row(inc, "Net Income", col=col_offset)
            a0 = _row(bal, "Total Assets", col=col_offset)
            ni1 = _row(inc, "Net Income", col=col_offset + 1)
            a1 = _row(bal, "Total Assets", col=col_offset + 1)
            roa0 = (ni0 / a0) if (ni0 and a0) else None
            roa1 = (ni1 / a1) if (ni1 and a1) else None
            ocf0 = _row(
                cf, "Operating Cash Flow", "Total Cash From Operating Activities", col=col_offset
            )
            ltd0 = _row(bal, "Long Term Debt", col=col_offset) or 0
            ltd1 = _row(bal, "Long Term Debt", col=col_offset + 1) or 0
            ca0 = _row(bal, "Current Assets", "Total Current Assets", col=col_offset)
            cl0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=col_offset)
            ca1 = _row(bal, "Current Assets", "Total Current Assets", col=col_offset + 1)
            cl1 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=col_offset + 1)
            sh0 = _row(bal, "Share Issued", col=col_offset)
            sh1 = _row(bal, "Share Issued", col=col_offset + 1)
            rev0 = _row(inc, "Total Revenue", col=col_offset)
            gp0 = _row(inc, "Gross Profit", col=col_offset)
            rev1 = _row(inc, "Total Revenue", col=col_offset + 1)
            gp1 = _row(inc, "Gross Profit", col=col_offset + 1)
            sc = (
                (1 if (roa0 and roa0 > 0) else 0)
                + (1 if (ocf0 and ocf0 > 0) else 0)
                + (1 if (roa0 and roa1 and roa0 > roa1) else 0)
                + (1 if (ocf0 and a0 and roa0 and (ocf0 / a0) > roa0) else 0)
                + (1 if (a0 and a1 and (ltd0 / a0) < (ltd1 / a1)) else 0)
                + (1 if (ca0 and cl0 and ca1 and cl1 and (ca0 / cl0) > (ca1 / cl1)) else 0)
                + ((1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1)
                + (1 if (gp0 and rev0 and gp1 and rev1 and (gp0 / rev0) > (gp1 / rev1)) else 0)
                + (1 if (rev0 and a0 and rev1 and a1 and (rev0 / a0) > (rev1 / a1)) else 0)
            )
            return sc >= 7

        elif screener == "coffee_can":
            if inc is None:
                return False

            def series(df, *rows):
                for nm in rows:
                    if df is not None and nm in df.index:
                        return [float(v) for v in df.loc[nm].dropna() if pd.notna(v)]
                return []

            revs = series(inc, "Total Revenue")
            cagr = (
                ((revs[0] / revs[-1]) ** (1 / (len(revs) - 1)) - 1) * 100
                if len(revs) >= 2 and revs[-1] > 0
                else None
            )
            ebit_s = series(inc, "EBIT", "Operating Income", "Ebit")
            ta_s = series(bal, "Total Assets")
            cl_s = series(bal, "Current Liabilities", "Total Current Liabilities")
            roce_l = [
                ebit_s[i] / (ta_s[i] - cl_s[i]) * 100
                for i in range(min(len(ebit_s), len(ta_s), len(cl_s)))
                if (ta_s[i] - cl_s[i]) > 0
            ]
            avg_roce = sum(roce_l) / len(roce_l) if roce_l else None
            de_raw = info.get("debtToEquity")
            de = (de_raw / 100 if de_raw and de_raw > 10 else de_raw) if de_raw else None
            mcap_cr = mcap / 1e7
            ni_s = series(inc, "Net Income")
            return bool(
                cagr
                and cagr > 10
                and avg_roce
                and avg_roce > 15
                and de is not None
                and de < 1
                and mcap_cr >= 500
                and ni_s
                and all(n > 0 for n in ni_s)
            )

        elif screener == "magic_formula":
            if inc is None:
                return False
            ebit = _row(inc, "EBIT", "Operating Income", "Ebit", col=col_offset)
            a0 = _row(bal, "Total Assets", col=col_offset)
            cl0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=col_offset)
            cap = (a0 - (cl0 or 0)) if a0 else None
            td = info.get("totalDebt", 0) or 0
            cash = info.get("totalCash", 0) or 0
            ev = (mcap + td - cash) if mcap else None
            roic = (ebit / cap * 100) if (ebit and cap and cap > 0) else None
            ey = (ebit / ev * 100) if (ebit and ev and ev > 0) else None
            bv = info.get("bookValue")
            ni = _row(inc, "Net Income", col=col_offset)
            return bool(
                roic
                and roic > 15
                and ey
                and ey > 8
                and bv
                and bv > 0
                and (mcap / 1e7) > 100
                and ni
                and ni > 0
            )

        elif screener == "bull_cartel":
            if inc_q is None or len(inc_q.columns) < 5:
                return False
            rev_q0 = _row(inc_q, "Total Revenue", col=col_offset)
            rev_q4 = _row(inc_q, "Total Revenue", col=col_offset + 4)
            ni_q0 = _row(inc_q, "Net Income", col=col_offset)
            ni_q4 = _row(inc_q, "Net Income", col=col_offset + 4)
            sg = (
                ((rev_q0 - rev_q4) / abs(rev_q4) * 100)
                if (rev_q0 and rev_q4 and rev_q4 != 0)
                else None
            )
            pg = ((ni_q0 - ni_q4) / abs(ni_q4) * 100) if (ni_q0 and ni_q4 and ni_q4 != 0) else None
            nc = ni_q0 / 1e7 if ni_q0 else None
            return bool(sg and sg > 15 and pg and pg > 20 and nc and nc > 1)
        return False

    # Try multiple rebalancing offsets to generate multiple historical data points
    # col=0 → most recent year/quarter, col=1 → prior year, col=2 → 2 years ago, etc.
    ohlc_ts = ohlc_df.index.tz_localize(None) if ohlc_df.index.tz else ohlc_df.index
    max_cols = len(inc.columns) - 2 if inc is not None and len(inc.columns) > 2 else 0

    for col_offset in range(min(3, max_cols + 1)):
        try:
            if not check_fundamental(col_offset):
                continue
            # Approximate the signal date: July 1 of the financial year
            if inc is not None and col_offset < len(inc.columns):
                fy_end = pd.Timestamp(inc.columns[col_offset])
                sig_dt = pd.Timestamp(f"{fy_end.year}-07-01")
                # If FY ends in March (India), signal fires July 1 same year
                # If FY ends in December, signal fires July 1 next year
                if fy_end.month > 7:
                    sig_dt = pd.Timestamp(f"{fy_end.year + 1}-07-01")
            else:
                sig_dt = pd.Timestamp(datetime.today().strftime("%Y-07-01"))

            # Clamp to available OHLC
            si = (
                ohlc_ts.searchsorted(
                    sig_dt.tz_localize(None) if sig_dt.tz else sig_dt, side="right"
                )
                - 1
            )
            if not (5 <= si < len(ohlc_df)):
                continue
            entry = float(ohlc_df["Close"].iloc[si])
            qualifying.append(
                {
                    "date": ohlc_df.index[si],
                    "entry": entry,
                    "idx": si,
                    "col_offset": col_offset,
                }
            )
        except Exception:
            continue

    return qualifying


# ══════════════════════════════════════════════════════════════════════════════
# 3. FILING TREND LAYER
# ══════════════════════════════════════════════════════════════════════════════


def compute_filing_trend(symbol: str, suffix: str) -> dict:
    """
    Analyse regulatory filing trends from quarterly data.

    Filing trend score quantifies how consistently a company has been
    improving its key metrics across multiple consecutive reporting periods.
    This is the "trend in company filings" the user asked for — a signal
    that distinguishes stocks with structural improvement from one-off beats.

    Metrics tracked:
      R  — Consecutive quarters of YoY revenue growth > 10%
      P  — Consecutive quarters of YoY net profit growth > 15%
      M  — Consecutive quarters of positive operating cash flow
      D  — Consecutive quarters of debt reduction
      Q  — Piotroski score improving (current > prior year)

    Combined Filing Trend Score = R + P + M + D + Q (max = 15)
    Classification:
      STRONG    (score ≥ 9)  — persistent fundamental improvement
      EMERGING  (score 4–8)  — recent turnaround, watch for continuation
      WEAK      (score ≤ 3)  — inconsistent or deteriorating
    """
    result = {
        "symbol": symbol,
        "rev_streak": 0,
        "profit_streak": 0,
        "ocf_streak": 0,
        "debt_streak": 0,
        "piotroski_trend": 0,
        "filing_score": 0,
        "filing_class": "WEAK",
    }
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc_q = _first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
        bal_q = _first_df(ticker, "quarterly_balance_sheet", "quarterly_balance_sheet")
        cf_q = _first_df(ticker, "quarterly_cash_flow", "quarterly_cashflow")
    except Exception:
        return result

    if inc_q is None or len(inc_q.columns) < 8:
        return result

    n_qtrs = len(inc_q.columns)

    def q_val(df, row, col):
        if df is None or col >= len(df.columns):
            return None
        return _row(df, row, col=col)

    # Revenue streak: consecutive quarters where YoY revenue growth > 10%
    rev_streak = 0
    for q in range(min(6, n_qtrs - 4)):
        r_now = q_val(inc_q, "Total Revenue", q)
        r_yago = q_val(inc_q, "Total Revenue", q + 4)
        if r_now and r_yago and r_yago != 0:
            growth = (r_now - r_yago) / abs(r_yago) * 100
            if growth > 10:
                rev_streak += 1
            else:
                break
        else:
            break
    result["rev_streak"] = rev_streak

    # Profit streak: consecutive quarters YoY net income growth > 15%
    profit_streak = 0
    for q in range(min(6, n_qtrs - 4)):
        ni_now = q_val(inc_q, "Net Income", q)
        ni_yago = q_val(inc_q, "Net Income", q + 4)
        if ni_now and ni_yago and ni_yago != 0:
            growth = (ni_now - ni_yago) / abs(ni_yago) * 100
            if growth > 15 and ni_now > 0:
                profit_streak += 1
            else:
                break
        else:
            break
    result["profit_streak"] = profit_streak

    # OCF streak: consecutive quarters positive operating cash flow
    ocf_streak = 0
    for q in range(min(8, n_qtrs)):
        ocf = (
            q_val(cf_q, "Operating Cash Flow", "Total Cash From Operating Activities", q)
            if cf_q is not None
            else None
        )
        if ocf is not None and ocf > 0:
            ocf_streak += 1
        else:
            break
    result["ocf_streak"] = min(ocf_streak, 3)  # cap at 3 for scoring

    # Debt reduction streak: consecutive quarters with falling total debt
    debt_streak = 0
    if bal_q is not None and len(bal_q.columns) >= 4:
        for q in range(min(4, len(bal_q.columns) - 1)):
            d_now = q_val(bal_q, "Long Term Debt", q) or 0
            d_prev = q_val(bal_q, "Long Term Debt", q + 1) or 0
            if d_now < d_prev:
                debt_streak += 1
            else:
                break
    result["debt_streak"] = debt_streak

    # Piotroski trend: is current score higher than 1 year ago?
    # Use annual data for this
    try:
        ticker2 = yf.Ticker(f"{symbol}{suffix}")
        inc_a = _first_df(ticker2, "income_stmt", "financials")
        bal_a = _first_df(ticker2, "balance_sheet")
        cf_a = _first_df(ticker2, "cash_flow", "cashflow")
        if inc_a is not None and len(inc_a.columns) >= 3:

            def piotroski_score(offset):
                ni0 = _row(inc_a, "Net Income", col=offset)
                a0 = _row(bal_a, "Total Assets", col=offset)
                roa0 = (ni0 / a0) if (ni0 and a0) else None
                ocf0 = _row(cf_a, "Operating Cash Flow", col=offset)
                return sum(
                    [
                        1 if (roa0 and roa0 > 0) else 0,
                        1 if (ocf0 and ocf0 > 0) else 0,
                    ]
                )

            pt = piotroski_score(0) - piotroski_score(1)
            result["piotroski_trend"] = max(0, min(3, pt + 1))  # 0-3
    except Exception:
        pass

    score = (
        min(result["rev_streak"], 3)
        + min(result["profit_streak"], 3)
        + result["ocf_streak"]
        + min(result["debt_streak"], 3)
        + result["piotroski_trend"]
    )
    result["filing_score"] = score
    result["filing_class"] = "STRONG" if score >= 9 else "EMERGING" if score >= 4 else "WEAK"
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. SPLIT LAYER
# ══════════════════════════════════════════════════════════════════════════════


def assign_split(signal_date: pd.Timestamp, period_cfg: dict) -> str:
    """
    Assign a signal to TRAIN / TEST / VAL based on its date.
    Strictly chronological — no data from a later split ever touches earlier splits.
    """
    try:
        dt = pd.Timestamp(signal_date)
        if dt < pd.Timestamp(period_cfg["test_start"]):
            return "TRAIN"
        elif dt < pd.Timestamp(period_cfg["val_start"]):
            return "TEST"
        elif dt <= pd.Timestamp(period_cfg["val_end"]):
            return "VAL"
    except Exception:
        pass
    return "OUT_OF_RANGE"


# ══════════════════════════════════════════════════════════════════════════════
# 5. RETURN LAYER
# ══════════════════════════════════════════════════════════════════════════════


def forward_returns(
    ohlc_df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    index_df: pd.DataFrame,
    signal_date: pd.Timestamp,
) -> dict:
    """
    Compute net returns (after 0.2% transaction cost) and alpha vs Nifty 50
    at each of the 8 horizons.
    """
    closes = ohlc_df["Close"].values
    n = len(closes)
    cost = TRANSACTION_COST * 100

    ret, alpha = {}, {}
    for label, offset in HORIZONS.items():
        ei = entry_idx + offset
        if ei >= n:
            ret[label] = alpha[label] = np.nan
            continue
        gross = (closes[ei] - entry_price) / entry_price * 100
        ret[label] = gross - cost

        # Nifty buy-and-hold over same period
        try:
            dt_naive = (
                signal_date.tz_localize(None)
                if hasattr(signal_date, "tz") and signal_date.tz
                else signal_date
            )
            idx_naive = index_df.index.tz_localize(None) if index_df.index.tz else index_df.index
            si = idx_naive.searchsorted(dt_naive, side="right") - 1
            xi = si + offset
            if 0 <= si < len(index_df) and xi < len(index_df):
                ic = index_df["Close"].values
                bh = (ic[xi] - ic[si]) / ic[si] * 100
                alpha[label] = ret[label] - bh
            else:
                alpha[label] = np.nan
        except Exception:
            alpha[label] = np.nan

    return {"ret": ret, "alpha": alpha}


# ══════════════════════════════════════════════════════════════════════════════
# 6. ANALYSIS LAYER
# ══════════════════════════════════════════════════════════════════════════════


def compute_stats(vals: pd.Series, bh_vals: pd.Series = None) -> dict:
    """Full statistics for a set of return values."""
    vals = vals.dropna()
    if len(vals) < 3:
        return {}
    n = len(vals)
    hits = (vals > 0).sum()
    hit_rate = hits / n * 100
    avg = vals.mean()
    med = vals.median()
    std = vals.std()
    wins = vals[vals > 0]
    losses = vals[vals < 0]
    down_std = losses.std() if len(losses) > 1 else 1e-9
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 else np.inf
    sharpe = avg / std if std > 0 else 0
    sortino = avg / down_std if down_std > 0 else 0
    # Drawdown
    sorted_v = vals.sort_values().reset_index(drop=True)
    cumul = (1 + sorted_v / 100).cumprod()
    peak = cumul.cummax()
    dd = (cumul - peak) / peak * 100
    max_dd = dd.min() if not dd.empty else 0
    calmar = avg / abs(max_dd) if max_dd < 0 else 0
    ev = (hit_rate / 100) * (wins.mean() if len(wins) > 0 else 0) + (1 - hit_rate / 100) * (
        losses.mean() if len(losses) > 0 else 0
    )
    stat = {
        "N": n,
        "Hit_Rate%": round(hit_rate, 1),
        "Avg_Return%": round(avg, 2),
        "Median%": round(med, 2),
        "Std%": round(std, 2),
        "Sharpe": round(sharpe, 3),
        "Sortino": round(sortino, 3),
        "Profit_Factor": round(pf, 2) if pf != np.inf else 999,
        "Max_DD%": round(max_dd, 2),
        "Calmar": round(calmar, 3),
        "EV%": round(ev, 2),
        "Max_Win%": round(vals.max(), 2),
        "Max_Loss%": round(vals.min(), 2),
        "p25%": round(vals.quantile(0.25), 2),
        "p75%": round(vals.quantile(0.75), 2),
    }
    if bh_vals is not None:
        bh = bh_vals.dropna()
        if len(bh) >= 3:
            stat["BH_Avg%"] = round(bh.mean(), 2)
            stat["Alpha_Avg%"] = round(avg - bh.mean(), 2)
    return stat


def analyze_all(signals_df: pd.DataFrame, period_label: str) -> pd.DataFrame:
    """
    Compute full statistics table:
      rows = Screener × Split × Regime × FilingClass × Horizon
    """
    rows = []
    if signals_df.empty:
        return pd.DataFrame()

    screeners = signals_df["screener"].unique()
    splits = ["TRAIN", "TEST", "VAL", "ALL"]
    regimes = ["BULL", "BEAR", "SIDEWAYS", "ALL"]
    filing_cls = (
        signals_df["filing_class"].unique().tolist() + ["ALL"]
        if "filing_class" in signals_df.columns
        else ["ALL"]
    )
    filing_cls = list(dict.fromkeys(filing_cls))

    for sc in screeners:
        for split in splits:
            for regime in regimes:
                for fcl in filing_cls:
                    # Filter
                    mask = signals_df["screener"] == sc
                    if split != "ALL":
                        mask &= signals_df["split"] == split
                    if regime != "ALL":
                        mask &= signals_df["regime"] == regime
                    if fcl != "ALL" and "filing_class" in signals_df.columns:
                        mask &= signals_df["filing_class"] == fcl
                    sub = signals_df[mask]
                    if sub.empty:
                        continue

                    for horizon in HORIZONS:
                        ret_col = f"ret_{horizon}"
                        alpha_col = f"alpha_{horizon}"
                        if ret_col not in sub.columns:
                            continue
                        vals = sub[ret_col].dropna()
                        if len(vals) < 3:
                            continue
                        bh_vals = sub[alpha_col].dropna() if alpha_col in sub.columns else None
                        s = compute_stats(vals)
                        if not s:
                            continue
                        s.update(
                            {
                                "Period": period_label,
                                "Screener": sc,
                                "Split": split,
                                "Regime": regime,
                                "FilingClass": fcl,
                                "Horizon": horizon,
                                "Stat_Flag": "⚠️ LOW N" if s["N"] < 20 else "OK",
                            }
                        )
                        if bh_vals is not None and not bh_vals.empty:
                            s["Alpha_Avg%"] = round(vals.mean() - bh_vals.mean(), 2)
                        rows.append(s)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def overfitting_report(stats_df: pd.DataFrame) -> pd.DataFrame:
    """
    Check for overfitting: compare Sharpe ratio across TRAIN / TEST / VAL.
    A dramatic drop from TRAIN→VAL suggests the signal is overfit to the training period.
    Source: Bailey et al. (2014) — "Probability of Backtest Overfitting."
    """
    rows = []
    if stats_df.empty:
        return pd.DataFrame()
    for (period, sc, regime, horizon), grp in stats_df.groupby(
        ["Period", "Screener", "Regime", "Horizon"]
    ):
        train = grp[grp["Split"] == "TRAIN"]["Sharpe"].mean()
        test = grp[grp["Split"] == "TEST"]["Sharpe"].mean()
        val = grp[grp["Split"] == "VAL"]["Sharpe"].mean()
        if pd.isna(train) or pd.isna(val):
            continue
        decay = (train - val) / abs(train) * 100 if train != 0 else np.nan
        rows.append(
            {
                "Period": period,
                "Screener": sc,
                "Regime": regime,
                "Horizon": horizon,
                "Sharpe_TRAIN": round(train, 3),
                "Sharpe_TEST": round(test, 3) if not pd.isna(test) else np.nan,
                "Sharpe_VAL": round(val, 3),
                "Sharpe_Decay%": round(decay, 1) if not pd.isna(decay) else np.nan,
                "Overfit_Risk": (
                    "HIGH"
                    if (not pd.isna(decay) and decay > 50)
                    else "MEDIUM" if (not pd.isna(decay) and decay > 20) else "LOW"
                ),
            }
        )
    return pd.DataFrame(rows)


def strategy_matrix(stats_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the strategy recommendation matrix:
      For each (Period, Regime, Horizon): which screener maximises EV% in VAL set?
    This is the actionable output — tells users which strategy to use right now.
    """
    if stats_df.empty:
        return pd.DataFrame()

    val = stats_df[(stats_df["Split"] == "VAL") | (stats_df["Split"] == "ALL")]
    val = val[val["N"] >= 5]  # minimum sample size

    rows = []
    for (period, regime, horizon), grp in val.groupby(["Period", "Regime", "Horizon"]):
        best = grp.sort_values("EV%", ascending=False).iloc[0] if not grp.empty else None
        if best is None:
            continue
        rows.append(
            {
                "Period": period,
                "Regime": regime,
                "Horizon": horizon,
                "Best_Screener": best["Screener"],
                "EV%": best["EV%"],
                "Hit_Rate%": best["Hit_Rate%"],
                "Avg_Return%": best["Avg_Return%"],
                "Sharpe": best["Sharpe"],
                "N_Signals": best["N"],
                "Stat_Flag": best.get("Stat_Flag", ""),
            }
        )
    return pd.DataFrame(rows).sort_values(["Period", "Regime", "Horizon"])


# ══════════════════════════════════════════════════════════════════════════════
# 7. REPORT LAYER
# ══════════════════════════════════════════════════════════════════════════════


def save_excel(
    all_stats: pd.DataFrame,
    overfit: pd.DataFrame,
    matrix: pd.DataFrame,
    all_signals: pd.DataFrame,
    filing_trends: pd.DataFrame,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT_DIR / f"walk_forward_{ts}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as w:
        # Disclaimer
        pd.DataFrame(
            {
                "DISCLAIMER": [
                    DISCLAIMER,
                    "",
                    "FRAMEWORK: Walk-forward Train/Test/Validation (60/20/20 chronological split)",
                    "PERIODS: 3y (2023–2026), 5y (2021–2026), 10y (2016–2026)",
                    "HORIZONS: T+1d T+3d T+5d T+10d T+21d T+63d T+126d T+252d",
                    "COSTS: 0.2% round-trip (STT + brokerage) deducted from all returns",
                    "ALPHA: Net return minus Nifty 50 buy-and-hold over same period",
                    "FILING TREND: Consecutive-quarter improvement score (max 15)",
                    "  STRONG ≥9: persistent fundamental improvement",
                    "  EMERGING 4-8: recent turnaround, watch for continuation",
                    "  WEAK ≤3: inconsistent or deteriorating",
                    "",
                    "OVERFITTING CHECK: Sharpe ratio decay from TRAIN → VAL",
                    "  HIGH risk: Sharpe drops >50% — strategy may be curve-fitted",
                    "  MEDIUM risk: drops 20–50% — use with caution",
                    "  LOW risk: drops <20% — robust signal",
                ]
            }
        ).to_excel(w, sheet_name="DISCLAIMER", index=False)

        # Strategy matrix (most actionable)
        if not matrix.empty:
            matrix.to_excel(w, sheet_name="Strategy_Matrix", index=False)

        # Overfitting report
        if not overfit.empty:
            overfit.sort_values("Sharpe_Decay%", ascending=False).to_excel(
                w, sheet_name="Overfitting_Check", index=False
            )

        # Full stats per period
        for period in all_stats["Period"].unique() if not all_stats.empty else []:
            sub = all_stats[all_stats["Period"] == period]
            nm = period.replace(" ", "_").replace("(", "").replace(")", "").replace("–", "_")[:28]
            sub.to_excel(w, sheet_name=f"Stats_{nm}", index=False)

        # Screener heatmaps (hit rate × horizon × regime for VAL set)
        if not all_stats.empty:
            val_stats = all_stats[
                (all_stats["Split"] == "VAL") & (all_stats["Regime"].isin(["BULL", "BEAR", "ALL"]))
            ]
            if not val_stats.empty:
                pivot = val_stats.pivot_table(
                    index=["Screener", "Regime"],
                    columns="Horizon",
                    values="Hit_Rate%",
                    aggfunc="mean",
                )
                ordered = [h for h in HORIZONS if h in pivot.columns]
                if ordered:
                    pivot[ordered].to_excel(w, sheet_name="HitRate_Heatmap_VAL")

                pivot_ev = val_stats.pivot_table(
                    index=["Screener", "Regime"], columns="Horizon", values="EV%", aggfunc="mean"
                )
                if ordered:
                    pivot_ev[ordered].to_excel(w, sheet_name="EV_Heatmap_VAL")

        # Filing trends
        if not filing_trends.empty:
            filing_trends.sort_values("filing_score", ascending=False).to_excel(
                w, sheet_name="Filing_Trends", index=False
            )

        # All signals log
        if not all_signals.empty:
            all_signals.sort_values(["screener", "signal_date"]).to_excel(
                w, sheet_name="All_Signals", index=False
            )

    print(f"\n  📊  Walk-forward Excel → {path}")
    return path


def print_summary(matrix: pd.DataFrame, overfit: pd.DataFrame, all_stats: pd.DataFrame):
    print(f"\n{'='*80}")
    print("  WALK-FORWARD BACKTEST — STRATEGY RECOMMENDATION MATRIX")
    print(f"  {DISCLAIMER}")
    print(f"{'='*80}")

    if matrix.empty:
        print("  No results to display.")
        return

    print("\n  Best screener per (Period × Regime × Horizon) — VAL set only")
    print("  Columns: Period | Regime | Horizon | Best Screener | EV% | Hit% | Avg% | N | Flag")
    print("  " + "─" * 78)

    prev_period = prev_regime = None
    for _, r in matrix.iterrows():
        if r["Period"] != prev_period:
            print(f"\n  ── {r['Period']} ──")
            prev_period, prev_regime = r["Period"], None
        if r["Regime"] != prev_regime:
            print(f"    {r['Regime']}")
            prev_regime = r["Regime"]
        flag = f"  {r['Stat_Flag']}" if r.get("Stat_Flag") == "⚠️ LOW N" else ""
        print(
            f"      {r['Horizon']:<8}  {r['Best_Screener']:<18}  "
            f"EV={r['EV%']:>+6.2f}%  Hit={r['Hit_Rate%']:>5.1f}%  "
            f"Avg={r['Avg_Return%']:>+6.2f}%  N={int(r['N_Signals'])}  {flag}"
        )

    print(f"\n{'='*80}")
    print("  OVERFITTING CHECK — Top 10 highest Sharpe decay (TRAIN→VAL)")
    print(
        f"  {'Screener':<18} {'Regime':<10} {'Horizon':<8} "
        f"{'Train Sharpe':>13} {'Val Sharpe':>11} {'Decay%':>8} {'Risk':<8}"
    )
    print("  " + "─" * 70)
    if not overfit.empty:
        for _, r in overfit.sort_values("Sharpe_Decay%", ascending=False).head(10).iterrows():
            print(
                f"  {r['Screener']:<18} {r['Regime']:<10} {r['Horizon']:<8} "
                f"{r['Sharpe_TRAIN']:>13.3f} {r['Sharpe_VAL']:>11.3f} "
                f"{r['Sharpe_Decay%']:>8.1f}% {r['Overfit_Risk']:<8}"
            )

    print("\n  Key findings:")
    # Best screeners per regime across all periods
    for regime in ["BULL", "BEAR", "SIDEWAYS"]:
        sub = matrix[matrix["Regime"] == regime]
        if sub.empty:
            continue
        best = sub.sort_values("EV%", ascending=False).iloc[0]
        print(
            f"  • {regime:<10} → {best['Best_Screener']:<18} "
            f"(best EV={best['EV%']:+.2f}% at {best['Horizon']})"
        )
    print(f"{'='*80}\n")


# ══════════════════════════════════════════════════════════════════════════════
# 8. MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════


def run_period(
    period_key: str,
    period_cfg: dict,
    ohlc_map: dict,
    index_df: pd.DataFrame,
    symbol_list: list,
    filing_cache: dict,
    workers: int,
    run_filings: bool,
) -> pd.DataFrame:
    """
    Run the full backtest pipeline for one period (3y / 5y / 10y).
    Returns a DataFrame of all signal records enriched with returns,
    alpha, split, regime, and filing trend class.
    """
    start_ts = pd.Timestamp(period_cfg["start"])
    end_ts = pd.Timestamp(period_cfg["val_end"])
    label = period_cfg["label"]
    print(f"\n{'─'*70}")
    print(f"  PERIOD: {label}  ({period_cfg['start']} – {period_cfg['val_end']})")
    print(
        f"  Splits: TRAIN < {period_cfg['test_start']} | "
        f"TEST < {period_cfg['val_start']} | VAL ≤ {period_cfg['val_end']}"
    )
    print(f"{'─'*70}")

    all_records = []

    # ── Technical screeners (Darvas, Golden Cross) ────────────────────────────
    for sc_name, sc_func in [("darvas", darvas_signals), ("golden_cross", golden_cross_signals)]:
        print(
            f"  {sc_name}: walking forward through {len(ohlc_map)} tickers …", end=" ", flush=True
        )
        sc_count = 0
        for yf_tkr, df in ohlc_map.items():
            # Trim to period
            df_p = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
            if len(df_p) < 50:
                continue
            sym = yf_tkr.replace(".NS", "")
            sigs = sc_func(df_p)
            for sig in sigs:
                fr = forward_returns(df_p, sig["idx"], sig["entry"], index_df, sig["date"])
                split = assign_split(sig["date"], period_cfg)
                if split == "OUT_OF_RANGE":
                    continue
                regime = classify_regime(sig["date"], index_df)
                rec = {
                    "period": label,
                    "screener": sc_name,
                    "symbol": sym,
                    "signal_date": sig["date"],
                    "entry_price": sig["entry"],
                    "split": split,
                    "regime": regime,
                    "filing_class": filing_cache.get(sym, {}).get("filing_class", "N/A"),
                    "filing_score": filing_cache.get(sym, {}).get("filing_score", 0),
                }
                for h in HORIZONS:
                    rec[f"ret_{h}"] = fr["ret"].get(h, np.nan)
                    rec[f"alpha_{h}"] = fr["alpha"].get(h, np.nan)
                all_records.append(rec)
                sc_count += 1
        print(f"{sc_count} signals")

    # ── Fundamental screeners ─────────────────────────────────────────────────
    fund_screeners = ["piotroski", "coffee_can", "magic_formula", "bull_cartel"]
    for sc_name in fund_screeners:
        print(f"  {sc_name}: fetching fundamentals for {len(symbol_list)} stocks …")
        sc_count = 0
        done = 0

        def _process(item):
            sym, suffix = item
            yf_tkr = f"{sym}{suffix}"
            ohlc = ohlc_map.get(yf_tkr)
            if ohlc is None:
                return []
            ohlc_p = ohlc.loc[(ohlc.index >= start_ts) & (ohlc.index <= end_ts)]
            if len(ohlc_p) < 20:
                return []
            sigs = fundamental_signals(sym, suffix, sc_name, ohlc_p)
            recs = []
            for sig in sigs:
                fr = forward_returns(ohlc_p, sig["idx"], sig["entry"], index_df, sig["date"])
                split = assign_split(sig["date"], period_cfg)
                if split == "OUT_OF_RANGE":
                    continue
                regime = classify_regime(sig["date"], index_df)
                rec = {
                    "period": label,
                    "screener": sc_name,
                    "symbol": sym,
                    "signal_date": sig["date"],
                    "entry_price": sig["entry"],
                    "split": split,
                    "regime": regime,
                    "filing_class": filing_cache.get(sym, {}).get("filing_class", "N/A"),
                    "filing_score": filing_cache.get(sym, {}).get("filing_score", 0),
                }
                for h in HORIZONS:
                    rec[f"ret_{h}"] = fr["ret"].get(h, np.nan)
                    rec[f"alpha_{h}"] = fr["alpha"].get(h, np.nan)
                recs.append(rec)
            return recs

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process, item): item for item in symbol_list}
            for future in as_completed(futures):
                done += 1
                try:
                    recs = future.result()
                    all_records.extend(recs)
                    sc_count += len(recs)
                except Exception:
                    pass
                if done % 200 == 0 or done == len(symbol_list):
                    print(f"    {done}/{len(symbol_list)} stocks, {sc_count} signals so far")

    df = pd.DataFrame(all_records)
    if not df.empty:
        df["signal_date"] = pd.to_datetime(df["signal_date"])
    print(f"\n  Period {label}: {len(df)} total signals across all screeners")
    return df


def main(periods: list = None, top: int = 0, workers: int = MAX_WORKERS, run_filings: bool = True):

    print(f"\n{'#'*72}")
    print("  WALK-FORWARD BACKTEST — TRAIN / TEST / VALIDATION")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Periods: {periods or list(PERIODS.keys())}")
    print(f"  Horizons: {list(HORIZONS.keys())}")
    print(f"  Transaction cost: {TRANSACTION_COST*100:.1f}% round-trip")
    print(f"{'#'*72}\n")
    print(DISCLAIMER + "\n")

    # Live market context — show regime, VIX, FII, upcoming results
    if _USE_NSE_FETCHER:
        try:
            _NSE_FETCHER.print_live_context()
        except Exception:
            pass

    active_periods = {k: v for k, v in PERIODS.items() if periods is None or k in periods}

    # ── Step 1: Symbols ───────────────────────────────────────────────────────
    print("Step 1 — Symbol universe …")
    # Allow liquid override from CLI
    override = globals().get("_LIQUID_OVERRIDE")
    if override is not None:
        symbol_list = override
        print(f"  {len(symbol_list)} liquid symbols (Nifty 500 proxy)\n")
    else:
        symbol_list = get_nse_symbols(top)
        print(f"  {len(symbol_list)} symbols\n")

    # ── Step 2: OHLC (longest window needed = 10y) ────────────────────────────
    yf_period = "10y" if "10y" in active_periods else ("5y" if "5y" in active_periods else "3y")
    print(f"Step 2 — Bulk OHLC download ({yf_period}) …")
    yf_tickers = [f"{s}{sfx}" for s, sfx in symbol_list]
    ohlc_map = fetch_ohlc_bulk(yf_tickers, period=yf_period)
    print(f"  {len(ohlc_map)} tickers with data\n")

    # ── Step 3: Index ─────────────────────────────────────────────────────────
    print("Step 3 — Nifty 50 index …")
    index_df = fetch_index(period=yf_period)
    print()

    # ── Step 4: Filing trends ─────────────────────────────────────────────────
    filing_cache = {}
    if run_filings:
        print(f"Step 4 — Filing trend analysis ({len(symbol_list)} stocks, {workers} workers) …")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(compute_filing_trend, s, sfx): s for s, sfx in symbol_list}
            for future in as_completed(futures):
                sym = futures[future]
                done += 1
                try:
                    result = future.result()
                    filing_cache[sym] = result
                except Exception:
                    pass
                if done % 200 == 0 or done == len(symbol_list):
                    strong = sum(
                        1 for v in filing_cache.values() if v.get("filing_class") == "STRONG"
                    )
                    print(f"  {done}/{len(symbol_list)} done — {strong} STRONG trend stocks")
    else:
        print("Step 4 — Filing trends skipped (--no-filings)\n")

    filing_df = pd.DataFrame(filing_cache.values()) if filing_cache else pd.DataFrame()

    # ── Step 5: Run each period ───────────────────────────────────────────────
    all_signals_list = []
    all_stats_list = []

    for pk, pcfg in active_periods.items():
        print(f"\nStep 5 — Backtesting period: {pk} …")
        sig_df = run_period(
            pk, pcfg, ohlc_map, index_df, symbol_list, filing_cache, workers, run_filings
        )
        if sig_df.empty:
            print(f"  No signals for period {pk}")
            continue
        all_signals_list.append(sig_df)

        print("  Analysing signals …")
        stats = analyze_all(sig_df, pcfg["label"])
        all_stats_list.append(stats)

    all_signals = (
        pd.concat(all_signals_list, ignore_index=True) if all_signals_list else pd.DataFrame()
    )
    all_stats = pd.concat(all_stats_list, ignore_index=True) if all_stats_list else pd.DataFrame()

    # ── Step 6: Overfitting + strategy matrix ─────────────────────────────────
    print("\nStep 6 — Overfitting check + strategy matrix …")
    overfit = overfitting_report(all_stats)
    matrix = strategy_matrix(all_stats)

    # ── Step 7: Save ──────────────────────────────────────────────────────────
    print("\nStep 7 — Saving results …")
    path = save_excel(all_stats, overfit, matrix, all_signals, filing_df)

    print_summary(matrix, overfit, all_stats)

    return {
        "signals": all_signals,
        "stats": all_stats,
        "matrix": matrix,
        "overfit": overfit,
        "filing": filing_df,
        "excel": str(path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Walk-forward Train/Test/Val backtest — 6 screeners × 8 horizons × "
            "3 periods (3y/5y/10y) with filing trend analysis."
        ),
        epilog="⚠️  Educational/research use only. NOT investment advice.",
    )
    parser.add_argument(
        "--period",
        nargs="+",
        choices=["3y", "5y", "10y"],
        default=None,
        help="Periods to analyse (default: all three)",
    )
    parser.add_argument(
        "--top", type=int, default=0, help="Limit to first N symbols (0 = all NSE EQ)"
    )
    parser.add_argument(
        "--liquid",
        action="store_true",
        default=False,
        help="Use Nifty 500 liquid stocks only (recommended for 5y/10y runs)",
    )
    parser.add_argument(
        "--workers", type=int, default=MAX_WORKERS, help=f"Parallel workers (default {MAX_WORKERS})"
    )
    parser.add_argument(
        "--no-filings",
        action="store_true",
        default=False,
        help="Skip filing trend analysis (faster)",
    )
    args = parser.parse_args()

    if args.liquid:
        # Nifty 500 proxy — well-known liquid stocks guaranteed to have 5-10y history
        NIFTY500_SAMPLE = [
            "RELIANCE",
            "TCS",
            "HDFCBANK",
            "ICICIBANK",
            "INFY",
            "HINDUNILVR",
            "ITC",
            "SBIN",
            "BAJFINANCE",
            "BHARTIARTL",
            "KOTAKBANK",
            "LT",
            "AXISBANK",
            "ASIANPAINT",
            "MARUTI",
            "TITAN",
            "SUNPHARMA",
            "WIPRO",
            "ULTRACEMCO",
            "NESTLEIND",
            "TECHM",
            "HCLTECH",
            "POWERGRID",
            "NTPC",
            "ONGC",
            "COALINDIA",
            "BPCL",
            "GRASIM",
            "JSWSTEEL",
            "TATASTEEL",
            "CIPLA",
            "DIVISLAB",
            "DRREDDY",
            "APOLLOHOSP",
            "BAJAJ-AUTO",
            "HEROMOTOCO",
            "EICHERMOT",
            "TATACONSUM",
            "BRITANNIA",
            "HINDALCO",
            "ADANIPORTS",
            "M&M",
            "BAJAJFINSV",
            "INDUSINDBK",
            "SHRIRAMFIN",
            "TRENT",
            "ADANIENT",
            "SBILIFE",
            "HDFCLIFE",
            "PIDILITIND",
            "HAVELLS",
            "DABUR",
            "MARICO",
            "GODREJCP",
            "COLPAL",
            "MCDOWELL-N",
            "BERGEPAINT",
            "KANSAINER",
            "PAGEIND",
            "RELAXO",
            "MUTHOOTFIN",
            "CHOLAFIN",
            "MANAPPURAM",
            "ABCAPITAL",
            "RECLTD",
            "PFC",
            "IRFC",
            "HUDCO",
            "SAIL",
            "NMDC",
            "HINDCOPPER",
            "NATIONALUM",
            "APLAPOLLO",
            "TATAMOTORS",
            "M&MFIN",
            "BAJAJHLDNG",
            "EXIDEIND",
            "AMARARAJA",
            "CEATLTD",
            "MRF",
            "ZOMATO",
            "NYKAA",
            "PAYTM",
            "DMART",
            "HDFCAMC",
            "NIPPONLIFE",
            "ICICIPRULIFE",
            "SBICARD",
            "BANDHANBNK",
            "RBLBANK",
            "FEDERALBNK",
            "IDFCFIRSTB",
            "CANBK",
            "BANKBARODA",
            "PNB",
            "UNIONBANK",
            "INDIANB",
            "IOB",
            "UCOBANK",
            "CENTRALBK",
            "MAHABANK",
            "MOTHERSON",
            "BOSCHLTD",
            "BHEL",
            "SIEMENS",
            "ABB",
            "CUMMINSIND",
            "THERMAX",
            "VOLTAS",
            "WHIRLPOOL",
            "BLUE STAR",
            "CROMPTON",
            "HAVELLS",
            "LEGRAND",
            "POLYCAB",
            "FINOLEX",
            "AUROPHARMA",
            "LUPIN",
            "ALKEM",
            "TORNTPHARM",
            "GLAXO",
            "PFIZER",
            "SANOFI",
            "ABBOTINDIA",
            "PERSISTENT",
            "MPHASIS",
            "COFORGE",
            "LTIM",
            "LTTS",
            "KPITTECH",
            "TATAELXSI",
            "ZYDUSLIFE",
            "MANKIND",
            "IPCA",
            "NATCOPHARM",
            "GRANULES",
            "LAURUSLABS",
            "STRIDES",
            "OFSS",
            "NAUKRI",
            "IRCTC",
            "INDIAMART",
            "JUSTDIAL",
            "POLICYBZR",
            "CARTRADE",
        ]
        # Deduplicate and convert to list of tuples
        seen, syms = set(), []
        for s in NIFTY500_SAMPLE:
            if s not in seen:
                seen.add(s)
                syms.append((s, ".NS"))
        print(f"  Using Nifty 500 liquid proxy: {len(syms)} stocks")
        # Monkey-patch get_nse_symbols to return our list
        import builtins

        _orig = builtins.__dict__.get("_wf_sym_override")
        globals()["_LIQUID_OVERRIDE"] = syms
    else:
        globals()["_LIQUID_OVERRIDE"] = None

    main(
        periods=args.period,
        top=args.top,
        workers=args.workers,
        run_filings=not args.no_filings,
    )
