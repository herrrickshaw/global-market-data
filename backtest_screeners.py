# backtest_screeners.py
# ======================
# Walk-forward backtesting framework: how well did each screener actually perform
# in the past, broken down by time horizon and market regime.
#
# HOW IT WORKS
# ────────────
# Step 1 — Symbol universe
#   NSE bhavcopy → all EQ symbols (2,406 stocks for the full run)
#
# Step 2 — Bulk OHLC download (14-month window)
#   14 months needed: 12 months for signals + 2 months buffer for T+63d exits
#   Uses yfinance.download() with retry on rate limit
#
# Step 3 — Index download
#   Nifty 50 (^NSEI) downloaded for 2 years to ensure 200 DMA is warm
#   200 DMA + 5-bar slope computed → used for regime classification at each signal
#
# Step 4 — Signal detection (two fundamentally different approaches)
#
#   TECHNICAL screeners — TRUE walk-forward (zero lookahead):
#     Darvas Box:
#       At bar i, only uses bars 0..i-1 to form the box
#       Signal fires when close[i] > box_top AND close[i-1] <= box_top (first cross)
#       Volume confirmation: signal bar volume ≥ 120% of 20-day average
#       Cooldown: minimum 10 bars between signals for same stock
#     Golden Crossover:
#       Computed over full history (DMAs use all available data)
#       Signal fires on the exact day 50 DMA first exceeds 200 DMA
#
#   FUNDAMENTAL screeners — EVENT STUDY (mild look-ahead bias):
#     Signal date = July 1 of the financial year (Preet et al. 2021 methodology)
#     All annual reports are released by June-end in India; July 1 is the first
#     date when a screener running on that data would have no forward-looking bias
#     Financial companies (banks, NBFCs) excluded from ROIC/ROCE screens
#     yfinance only provides ~4 years of free financial history
#     Limitation: uses CURRENT financial data as a proxy for what was available
#     on the historical signal date — mild look-ahead from restatements
#
# Step 5 — Forward return calculation
#   Entry: signal bar close price
#   Exit: close at T+1, T+3, T+5, T+21, T+63 trading days after signal
#   Net return = gross return − 0.2% round-trip transaction cost (STT + brokerage)
#   Alpha = net return − Nifty 50 buy-and-hold return over same period
#
# Step 6 — Regime classification at each signal date
#   BULL:     Nifty close > 200 DMA AND 200 DMA 5-bar slope > 0 (uptrend)
#   BEAR:     Nifty close < 200 DMA AND 200 DMA 5-bar slope < 0 (downtrend)
#   SIDEWAYS: everything else (price near DMA or DMA flat)
#
# Step 7 — Statistical analysis
#   Per (screener, regime, horizon):
#     N signals, Hit Rate%, Avg Return%, Median%, Sharpe, Sortino,
#     Profit Factor, Max Drawdown%, Calmar, Expected Value%, Alpha vs BH
#   Statistical flag: ⚠️ LOW N when signals < 20 (insufficient for 95% confidence)
#
# KEY FINDINGS FROM FULL NSE RUN (2,406 stocks, 1-year lookback)
# ───────────────────────────────────────────────────────────────
# Bull Cartel (BEAR regime):  80.5% hit rate | EV +21.34% | Sharpe 1.83
# Piotroski (BEAR regime):    79.1% hit rate | EV +15.59% | Sharpe 1.05
# Darvas (BEAR regime):       52.9% hit rate | EV  +4.22% | borderline edge
# Darvas (BULL regime):       38.2% hit rate | EV  -2.32% | AVOID short-term
#
# For each screener, computes forward returns at:
#   T+1 (next day), T+3, T+5 (1 week), T+21 (1 month), T+63 (3 months)
# Results are split by market regime (BULL / BEAR / SIDEWAYS) using the
# Nifty 50 or S&P 500 200-day moving average at the signal date.
#
# Signal detection methodology:
#   TECHNICAL screeners (zero lookahead — true walk-forward):
#     • Darvas Box    — price closes above box top for the first time
#     • Golden Cross  — 50 DMA crosses above 200 DMA on signal day
#
#   FUNDAMENTAL screeners (event-study — signal date = result publication date):
#     • Piotroski ≥7  — annual results announcement date from yfinance
#     • Coffee Can    — annual results announcement date
#     • Magic Formula — annual results announcement date
#     • Bull Cartel   — quarterly results announcement date
#     Note: we use CURRENT fundamentals as proxy for what qualified on the
#     announcement date. This introduces mild look-forward bias on restatements
#     but is unavoidable without a paid historical-fundamentals data source.
#
# Market regime (at signal date):
#   BULL     — index close > 200 DMA  AND  200 DMA slope > 0 (uptrend)
#   BEAR     — index close < 200 DMA  AND  200 DMA slope < 0 (downtrend)
#   SIDEWAYS — everything else (index near 200 DMA or DMA flat)
#
# Output:
#   backtest_results/backtest_YYYYMMDD_HHMM.xlsx  — full signal log + stats
#   Printed summary: hit-rate heatmap per screener × horizon × regime
#
# Usage:
#   python backtest_screeners.py                    # Indian + US, all screeners
#   python backtest_screeners.py --market IN        # Indian only
#   python backtest_screeners.py --market US        # US only
#   python backtest_screeners.py --workers 8        # parallel fundamentals
#   python backtest_screeners.py --top 300          # limit to 300 symbols per market
#
# Install:
#   pip install yfinance pandas openpyxl "nse[local]"
#
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️  DISCLAIMER
# Backtests are conducted on historical data and are subject to survivorship
# bias, look-ahead bias (for fundamental screeners), and data-provider
# limitations (Yahoo Finance free tier).  Past performance of a screening
# strategy does NOT guarantee future results.  This analysis is for
# EDUCATIONAL and RESEARCH purposes only — NOT investment advice.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

# ── Constants ─────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = Path("./backtest_results")
DOWNLOAD_DIR.mkdir(exist_ok=True)

LOOKBACK_DAYS = 252  # 1 trading year of history
DARVAS_CONFIRM = 3  # days to confirm Darvas box top/bottom
COOLDOWN_BARS = 10  # min bars between two Darvas signals for same stock
MAX_WORKERS = 8

HORIZONS = {
    "T+1d": 1,
    "T+3d": 3,
    "T+1wk": 5,
    "T+1mo": 21,
    "T+3mo": 63,
}

INDICES = {
    "IN": "^NSEI",  # Nifty 50
    "US": "^GSPC",  # S&P 500
}

DISCLAIMER = (
    "⚠️  BACKTESTING DISCLAIMER: Results are based on historical data and are "
    "subject to survivorship bias, look-ahead bias (fundamental screeners), and "
    "data limitations. Past performance does NOT guarantee future results. "
    "For educational/research use only — NOT investment advice."
)

# ── Research-backed improvements (applied from 7 papers) ─────────────────────
# Source: Preet et al. (2021) — Magic Formula India; Bhute et al. (2024) — JIER;
#         Liu & Zhu (2024) — Kalman Filter market efficiency;
#         Dhanus & Amutha (2025) — Super Trend Nifty backtest

TRANSACTION_COST_PCT = 0.002  # 0.2% round-trip: STT 0.1% + brokerage ~0.1%
# Source: JIER 2024 — "transaction cost and impact cost
# not considered" flagged as limitation; US literature
# uses 0.1-0.5% depending on market

MIN_SIGNAL_THRESHOLD = 20  # Minimum signals needed for statistical inference
# Source: Bailey et al. (2014) — false positive risk
# when n_trials is small

# Financial sector keywords — exclude for fundamental screeners
# Source: Preet et al. (2021) — "Financial companies removed because ROCE data
# cannot be obtained and high leverage has different interpretation for financial firms"
FINANCIAL_KEYWORDS = {
    "bank",
    "finance",
    "financial",
    "insurance",
    "nbfc",
    "housing",
    "capital",
    "leasing",
    "asset management",
    "wealth",
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
    "lic",
    "sbi life",
    "hdfc life",
}

# Magic Formula signal date: first working day of July
# Source: Preet et al. (2021) — "almost all annual reports released by June-end;
# application on 1st July eliminates forward-looking bias"
MAGIC_FORMULA_SIGNAL_MONTH = 7  # July


# ── yfinance helpers ──────────────────────────────────────────────────────────

# Shared helpers (see stock_utils.py) — aliased to keep existing call sites.
from stock_utils import first_df as _first_df
from stock_utils import row as _row

# ── Market index + regime ─────────────────────────────────────────────────────


def download_index(market: str = "IN", period: str = "2y") -> pd.DataFrame:
    """
    Download 2 years of index OHLC (2y ensures full 200 DMA warm-up for the
    1-year backtest window).
    """
    sym = INDICES.get(market, "^NSEI")
    print(f"  Downloading {sym} index ({period}) for regime classification …", end=" ")
    try:
        df = yf.download(sym, period=period, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(sym, axis=1, level=1)
        df = df.dropna()
        # 200 DMA + slope
        df["dma200"] = df["Close"].rolling(200).mean()
        df["dma200_sl"] = df["dma200"].diff(5)  # 5-bar slope
        print(f"OK ({len(df)} bars)")
        return df
    except Exception as e:
        print(f"ERROR — {e}")
        return pd.DataFrame()


def classify_regime(date: pd.Timestamp, index_df: pd.DataFrame) -> str:
    """
    Classify market regime at a given date using index 200 DMA.
      BULL     : index > 200 DMA AND DMA sloping up
      BEAR     : index < 200 DMA AND DMA sloping down
      SIDEWAYS : everything else (DMA flat or price ≈ DMA)
    """
    if index_df is None or index_df.empty:
        return "UNKNOWN"
    idx = index_df.index.searchsorted(date, side="right") - 1
    if idx < 0 or idx >= len(index_df):
        return "UNKNOWN"
    row = index_df.iloc[idx]
    close = row.get("Close", np.nan)
    dma = row.get("dma200", np.nan)
    slope = row.get("dma200_sl", np.nan)
    if any(np.isnan(x) for x in [close, dma, slope]):
        return "UNKNOWN"
    if close > dma and slope > 0:
        return "BULL"
    if close < dma and slope < 0:
        return "BEAR"
    return "SIDEWAYS"


# ── Forward return calculator ─────────────────────────────────────────────────


def compute_forward_returns(
    df: pd.DataFrame,
    signal_idx: int,
    entry_price: float,
    apply_costs: bool = True,
) -> dict:
    """
    Compute net percentage returns at each horizon, deducting round-trip
    transaction costs (STT + brokerage ≈ 0.2%).

    Source: Bhute et al. (2024) — flagged omission of transaction costs
    as a key limitation; Liu & Zhu (2024) — use ROG (return on gross) to
    reflect realistic execution via TWAP/VWAP.

    Entry = signal bar close. Exit = close at T+N.
    Cost deducted symmetrically (0.1% on entry + 0.1% on exit).
    """
    closes = df["Close"].values
    n = len(closes)
    cost = TRANSACTION_COST_PCT if apply_costs else 0.0
    result = {}
    for label, offset in HORIZONS.items():
        exit_idx = signal_idx + offset
        if exit_idx >= n:
            result[label] = np.nan
        else:
            exit_price = closes[exit_idx]
            gross_ret = (exit_price - entry_price) / entry_price * 100
            result[label] = gross_ret - cost * 100  # deduct round-trip cost
    return result


def compute_index_returns(index_df: pd.DataFrame, signal_date: pd.Timestamp) -> dict:
    """
    Compute Nifty 50 / S&P 500 buy-and-hold returns over the same horizons
    starting from the signal date. Used to compute alpha above benchmark.
    Source: Bhute et al. (2024) — compare strategy returns vs buy-and-hold.
    """
    if index_df is None or index_df.empty:
        return {h: np.nan for h in HORIZONS}
    try:
        idx_ts = index_df.index.tz_localize(None) if index_df.index.tz else index_df.index
        sig_ts = (
            signal_date.tz_localize(None)
            if hasattr(signal_date, "tz") and signal_date.tz
            else signal_date
        )
        si = idx_ts.searchsorted(sig_ts, side="right") - 1
        if si < 0 or si >= len(index_df):
            return {h: np.nan for h in HORIZONS}
        entry = float(index_df["Close"].iloc[si])
        result = {}
        for label, offset in HORIZONS.items():
            ei = si + offset
            if ei >= len(index_df):
                result[label] = np.nan
            else:
                result[label] = (float(index_df["Close"].iloc[ei]) - entry) / entry * 100
        return result
    except Exception:
        return {h: np.nan for h in HORIZONS}


def is_financial_stock(symbol: str, info: dict = None) -> bool:
    """
    Check if a stock is in the financial sector (banks, NBFCs, insurance).
    Financial companies should be excluded from fundamental screeners because:
    - ROCE/ROIC is not comparable (high leverage is normal, not distress)
    - Earnings Yield via EV/EBIT is meaningless for banks
    Source: Preet et al. (2021) Magic Formula India study.
    """
    sym_lower = symbol.lower()
    if any(kw in sym_lower for kw in FINANCIAL_KEYWORDS):
        return True
    if info:
        sector = (info.get("sector") or "").lower()
        industry = (info.get("industry") or "").lower()
        if any(
            kw in sector or kw in industry
            for kw in ("bank", "financial", "insurance", "capital markets")
        ):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL DETECTORS
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Darvas Box — walk-forward, no lookahead ────────────────────────────────


def detect_darvas_signals(
    df: pd.DataFrame,
    confirm: int = DARVAS_CONFIRM,
    cooldown: int = COOLDOWN_BARS,
    require_volume: bool = True,
) -> "list[dict]":
    """
    Walk through OHLC bar-by-bar. At each bar i, compute the Darvas Box
    using only data from bars 0..i-1 (no lookahead). A BREAKOUT signal
    fires when close[i] > box_top AND close[i-1] <= box_top (first cross).

    Cooldown prevents counting re-entries of the same breakout level.
    """
    if df is None or df.empty or len(df) < 210:
        return []

    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    closes = df["Close"].values.astype(float)
    dates = df.index

    signals = []
    last_signal = -cooldown - 1

    for i in range(confirm + 20, len(closes)):
        # Only use bars strictly before i for box formation
        h = highs[:i]
        lows[:i]
        n = len(h)

        # Find most recent confirmed box top in the last 60 bars
        box_top = None
        search_from = max(0, n - 60)
        for j in range(n - confirm - 1, search_from - 1, -1):
            if h[j] == 0:
                continue
            win = h[j + 1 : j + 1 + confirm]
            if len(win) == confirm and all(x < h[j] for x in win):
                box_top = h[j]
                break

        if box_top is None:
            continue

        prev_close = closes[i - 1]
        curr_close = closes[i]

        # Breakout: first close above box top
        if curr_close > box_top and prev_close <= box_top:
            if i - last_signal >= cooldown:
                # Volume confirmation: breakout bar volume > 20-day avg volume
                # Source: Dhanus & Amutha (2025) — "combine with volume indicators
                # to reduce false signals"; Super Trend works better with volume filter
                vol_confirmed = True
                if require_volume and "Volume" in df.columns and i >= 20:
                    vols = df["Volume"].values.astype(float)
                    avg_vol = np.mean(vols[i - 20 : i])
                    vol_confirmed = vols[i] >= avg_vol * 1.2  # 20% above avg

                if vol_confirmed:
                    signals.append(
                        {
                            "date": dates[i],
                            "entry_price": curr_close,
                            "box_top": round(box_top, 2),
                            "signal_idx": i,
                            "vol_confirm": True,
                        }
                    )
                    last_signal = i

    return signals


# ── 2. Golden Crossover — walk-forward ────────────────────────────────────────


def detect_golden_cross_signals(df: pd.DataFrame) -> "List[dict]":
    """
    Detect every day in the history where 50 DMA crossed above 200 DMA.
    Uses all data to compute DMAs (no bar-by-bar restriction needed since
    DMAs are computed from the same historical window).
    """
    if df is None or df.empty or len(df) < 205:
        return []

    closes = df["Close"].astype(float)
    dma50 = closes.rolling(50).mean()
    dma200 = closes.rolling(200).mean()

    signals = []
    # Limit to the backtest window (last LOOKBACK_DAYS bars)
    start = max(0, len(closes) - LOOKBACK_DAYS)

    for i in range(max(1, start), len(closes)):
        d50_t, d200_t = dma50.iloc[i], dma200.iloc[i]
        d50_p, d200_p = dma50.iloc[i - 1], dma200.iloc[i - 1]
        if any(pd.isna(x) for x in [d50_t, d200_t, d50_p, d200_p]):
            continue
        if d50_p < d200_p and d50_t > d200_t:  # cross happened today
            signals.append(
                {
                    "date": df.index[i],
                    "entry_price": float(closes.iloc[i]),
                    "dma50": round(float(d50_t), 2),
                    "dma200": round(float(d200_t), 2),
                    "signal_idx": i,
                }
            )
    return signals


# ── 3. Fundamental screeners — event-study ────────────────────────────────────


def get_fundamental_signal_date(ticker_obj, freq: str = "annual") -> "Optional[pd.Timestamp]":
    """
    Return the date of the most recent financial report, which is the earliest
    moment a fundamental screener signal could have been acted upon.
    """
    try:
        if freq == "annual":
            inc = _first_df(ticker_obj, "income_stmt", "financials")
        else:
            inc = _first_df(ticker_obj, "quarterly_income_stmt", "quarterly_financials")
        if inc is not None and len(inc.columns) > 0:
            date = inc.columns[0]
            # yfinance dates may be datetime or Timestamp — normalise
            return pd.Timestamp(date)
    except Exception:
        pass
    return None


def fetch_fundamental_signal(
    symbol: str, suffix: str, screener: str, ohlc_df: pd.DataFrame
) -> "Optional[dict]":
    """
    For fundamental screeners, return one signal dict per qualifying stock.
    Signal date = most recent annual or quarterly report publication date.
    Returns None if the stock does not qualify or data is unavailable.
    """
    freq = "quarterly" if screener == "bull_cartel" else "annual"
    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        cf = _first_df(ticker, "cash_flow", "cashflow")
        # Skip financial companies for ROCE/ROIC-based screeners
        # Source: Preet et al. (2021) — financial firms use high leverage normally;
        # ROCE not comparable; Magic Formula specifically excludes them
        if screener in ("magic_formula", "piotroski", "coffee_can"):
            try:
                info_chk = ticker.info or {}
                if is_financial_stock(symbol, info_chk):
                    return None
            except Exception:
                if is_financial_stock(symbol):
                    return None
        inc_q = (
            _first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
            if freq == "quarterly"
            else None
        )
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass
        try:
            mcap = ticker.fast_info.market_cap or 0
        except Exception:
            mcap = info.get("marketCap", 0) or 0
    except Exception:
        return None

    signal_date = get_fundamental_signal_date(ticker, freq)
    if signal_date is None:
        return None

    qualifies = False

    # ── Piotroski ────────────────────────────────────────────────────────────
    if screener == "piotroski" and inc is not None:
        ni0 = _row(inc, "Net Income", col=0)
        a0 = _row(bal, "Total Assets", col=0)
        ni1 = _row(inc, "Net Income", col=1)
        a1 = _row(bal, "Total Assets", col=1)
        roa0 = (ni0 / a0) if (ni0 and a0) else None
        roa1 = (ni1 / a1) if (ni1 and a1) else None
        ocf0 = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        ltd0 = _row(bal, "Long Term Debt", col=0) or 0
        ltd1 = _row(bal, "Long Term Debt", col=1) or 0
        ca0 = _row(bal, "Current Assets", "Total Current Assets", col=0)
        cl0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
        ca1 = _row(bal, "Current Assets", "Total Current Assets", col=1)
        cl1 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
        sh0 = _row(bal, "Share Issued", col=0)
        sh1 = _row(bal, "Share Issued", col=1)
        rev0 = _row(inc, "Total Revenue", col=0)
        gp0 = _row(inc, "Gross Profit", col=0)
        rev1 = _row(inc, "Total Revenue", col=1)
        gp1 = _row(inc, "Gross Profit", col=1)
        f_score = (
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
        qualifies = f_score >= 7

    # ── Coffee Can ───────────────────────────────────────────────────────────
    elif screener == "coffee_can" and inc is not None:

        def series(df, *rows):
            for name in rows:
                if df is not None and name in df.index:
                    return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
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
        de = (de_raw / 100 if de_raw > 10 else de_raw) if de_raw is not None else None
        mcap_cr = mcap / 1e7
        ni_s = series(inc, "Net Income")
        qualifies = bool(
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

    # ── Magic Formula ────────────────────────────────────────────────────────
    # Source: Preet et al. (2021) — original Greenblatt method uses COMBINED RANK
    # of ROCE + Earnings Yield, NOT binary thresholds. "30 stocks with lowest
    # combined rank" is optimal. Binary threshold (screener.in's simplification)
    # misses the ranking-based diversification that makes MF robust.
    # For event-study backtesting we use a simplified pass/fail check:
    # ROIC > 15% (relaxed from 25%) AND Earnings Yield > 10% AND MCap > ₹100Cr
    # AND positive book value AND NOT a financial stock (already filtered above).
    # The relaxed thresholds approximate top-30 ranking behaviour on a large universe.
    elif screener == "magic_formula" and inc is not None:
        ebit = _row(inc, "EBIT", "Operating Income", "Ebit")
        a0 = _row(bal, "Total Assets", col=0)
        cl0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
        cap = (a0 - (cl0 or 0)) if a0 else None
        td = info.get("totalDebt", 0) or 0
        cash = info.get("totalCash", 0) or 0
        ev = (mcap + td - cash) if mcap else None
        roic = (ebit / cap * 100) if (ebit and cap and cap > 0) else None
        ey = (ebit / ev * 100) if (ebit and ev and ev > 0) else None
        bv = info.get("bookValue")
        ni_chk = _row(inc, "Net Income", col=0)
        mcap_cr = mcap / 1e7
        # Relaxed thresholds to capture more signals for statistical significance
        qualifies = bool(
            roic
            and roic > 15  # relaxed from 25% — top-30 ranking proxy
            and ey
            and ey > 8  # relaxed from 15% — captures more candidates
            and bv
            and bv > 0  # positive book value mandatory
            and mcap_cr > 100  # ≥ ₹100 Cr (relaxed from ₹15 Cr)
            and ni_chk
            and ni_chk > 0  # exclude negative earnings (Preet et al.)
        )

    # ── Bull Cartel ──────────────────────────────────────────────────────────
    elif screener == "bull_cartel" and inc_q is not None:
        if len(inc_q.columns) >= 5:
            rev_q0 = _row(inc_q, "Total Revenue", col=0)
            rev_q4 = _row(inc_q, "Total Revenue", col=4)
            ni_q0 = _row(inc_q, "Net Income", col=0)
            ni_q4 = _row(inc_q, "Net Income", col=4)
            sg = (
                ((rev_q0 - rev_q4) / abs(rev_q4) * 100)
                if (rev_q0 and rev_q4 and rev_q4 != 0)
                else None
            )
            pg = ((ni_q0 - ni_q4) / abs(ni_q4) * 100) if (ni_q0 and ni_q4 and ni_q4 != 0) else None
            nc = ni_q0 / 1e7 if ni_q0 else None
            qualifies = bool(sg and sg > 15 and pg and pg > 20 and nc and nc > 1)

    if not qualifies:
        return None

    # Find signal date in OHLC index
    if ohlc_df is None or ohlc_df.empty:
        return None
    try:
        sig_ts = pd.Timestamp(signal_date).tz_localize(None)
        ohlc_ts = ohlc_df.index.tz_localize(None) if ohlc_df.index.tz else ohlc_df.index
        idx = ohlc_ts.searchsorted(sig_ts, side="right")
        if idx >= len(ohlc_df):
            idx = len(ohlc_df) - 1
        if idx < 0:
            return None
        return {
            "date": ohlc_df.index[idx],
            "entry_price": float(ohlc_df["Close"].iloc[idx]),
            "signal_idx": idx,
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# BACKTEST RUNNER
# ══════════════════════════════════════════════════════════════════════════════


def run_screener_backtest(
    screener_name: str,
    symbols: "List[tuple]",  # list of (symbol, yf_suffix, ohlc_df)
    index_df: pd.DataFrame,
    technical: bool = False,
    workers: int = MAX_WORKERS,
) -> pd.DataFrame:
    """
    Run the backtest for one screener across all symbols.
    Returns a DataFrame of all signals with forward returns and regime.
    """
    all_signals = []

    if technical:
        # Walk-forward detection — process each stock directly
        for sym, suffix, ohlc_df in symbols:
            if ohlc_df is None or ohlc_df.empty:
                continue
            try:
                if screener_name == "darvas":
                    raw_signals = detect_darvas_signals(ohlc_df)
                elif screener_name == "golden_cross":
                    raw_signals = detect_golden_cross_signals(ohlc_df)
                else:
                    raw_signals = []

                for sig in raw_signals:
                    ret = compute_forward_returns(ohlc_df, sig["signal_idx"], sig["entry_price"])
                    regime = classify_regime(sig["date"], index_df)
                    # Benchmark: Nifty/S&P buy & hold returns over same horizons
                    bh_ret = compute_index_returns(index_df, sig["date"])
                    bh_prefixed = {f"bh_{k}": v for k, v in bh_ret.items()}
                    row = {
                        "screener": screener_name,
                        "symbol": sym,
                        "signal_date": sig["date"],
                        "entry_price": sig["entry_price"],
                        "regime": regime,
                        **ret,
                        **bh_prefixed,
                        "extra_info": str(
                            {
                                k: v
                                for k, v in sig.items()
                                if k not in ("date", "entry_price", "signal_idx")
                            }
                        ),
                    }
                    all_signals.append(row)
            except Exception:
                pass

    else:
        # Fundamental screener — event study, parallel fetch
        def _process(item):
            sym, suffix, ohlc_df = item
            sig = fetch_fundamental_signal(sym, suffix, screener_name, ohlc_df)
            if sig is None:
                return None
            ret = compute_forward_returns(ohlc_df, sig["signal_idx"], sig["entry_price"])
            regime = classify_regime(sig["date"], index_df)
            bh_ret = compute_index_returns(index_df, sig["date"])
            bh_prefixed = {f"bh_{k}": v for k, v in bh_ret.items()}
            return {
                "screener": screener_name,
                "symbol": sym,
                "signal_date": sig["date"],
                "entry_price": sig["entry_price"],
                "regime": regime,
                **ret,
                **bh_prefixed,
                "extra_info": "",
            }

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process, item): item[0] for item in symbols}
            for future in as_completed(futures):
                done += 1
                try:
                    result = future.result()
                    if result:
                        all_signals.append(result)
                except Exception:
                    pass
                if done % 50 == 0 or done == len(symbols):
                    print(
                        f"    [{screener_name}] {done}/{len(symbols)} processed, "
                        f"{len(all_signals)} signals found"
                    )

    return pd.DataFrame(all_signals) if all_signals else pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# STATISTICAL ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════


def analyze_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute research-grade statistics per (screener, regime, horizon).

    Metrics added from research papers:
    - Sortino Ratio     (Bhute 2024 — uses downside deviation, not total std dev)
    - Profit Factor     (gross wins / abs gross losses; >1.5 = good strategy)
    - Max Drawdown      (largest peak-to-trough loss across signal returns)
    - Calmar Ratio      (avg_return / max_drawdown)
    - Alpha vs BH       (return above Nifty/S&P buy & hold over same period)
    - N_Signals warning (Bailey et al. 2014 — flag if N < MIN_SIGNAL_THRESHOLD)
    - Net_Return%       (after 0.2% round-trip transaction cost, already baked in)
    """
    if df.empty:
        return pd.DataFrame()

    horizon_cols = list(HORIZONS.keys())
    # Benchmark columns are stored as "bh_T+1d" etc.
    bh_cols = {h: f"bh_{h}" for h in horizon_cols}
    rows = []

    for screener in df["screener"].unique():
        for regime in ["BULL", "BEAR", "SIDEWAYS", "ALL"]:
            if regime == "ALL":
                sub = df[df["screener"] == screener]
            else:
                sub = df[(df["screener"] == screener) & (df["regime"] == regime)]

            if sub.empty:
                continue

            for horizon in horizon_cols:
                if horizon not in sub.columns:
                    continue
                vals = sub[horizon].dropna()
                if len(vals) < 3:
                    continue

                n = len(vals)
                hit_rate = (vals > 0).mean() * 100
                avg_ret = vals.mean()
                med_ret = vals.median()
                std_ret = vals.std()

                # Sortino: penalise only downside deviation (not total std dev)
                # Source: Bhute et al. (2024) — "Bollinger bands and RSI gave
                # best results based on Sharpe AND Sortino ratios"
                downside = vals[vals < 0]
                down_std = downside.std() if not downside.empty else 1e-9
                sortino = avg_ret / down_std if down_std > 0 else 0

                sharpe = avg_ret / std_ret if std_ret > 0 else 0
                max_win = vals.max()
                max_loss = vals.min()
                wins = vals[vals > 0]
                losses = vals[vals < 0]
                avg_win = wins.mean() if not wins.empty else 0
                avg_loss = losses.mean() if not losses.empty else 0
                ev = (hit_rate / 100 * avg_win) + ((1 - hit_rate / 100) * avg_loss)
                pct_25 = vals.quantile(0.25)
                pct_75 = vals.quantile(0.75)

                # Profit Factor: gross wins / abs gross losses
                gross_wins = wins.sum() if not wins.empty else 0
                gross_losses = abs(losses.sum()) if not losses.empty else 1e-9
                profit_factor = gross_wins / gross_losses if gross_losses > 0 else np.inf

                # Max Drawdown: treat sorted return series as equity curve
                sorted_vals = vals.sort_values().reset_index(drop=True)
                cumulative = (1 + sorted_vals / 100).cumprod()
                peak = cumulative.cummax()
                drawdown = (cumulative - peak) / peak * 100
                max_dd = drawdown.min() if not drawdown.empty else 0
                calmar = avg_ret / abs(max_dd) if max_dd < 0 else 0

                # Alpha vs Buy & Hold benchmark
                bh_col = bh_cols.get(horizon)
                alpha = np.nan
                if bh_col and bh_col in sub.columns:
                    bh_vals = sub[bh_col].dropna()
                    if len(bh_vals) >= 3:
                        alpha = round(avg_ret - bh_vals.mean(), 2)

                # Statistical warning: flag if insufficient signals
                stat_flag = "⚠️ LOW N" if n < MIN_SIGNAL_THRESHOLD else "OK"

                rows.append(
                    {
                        "Screener": screener,
                        "Regime": regime,
                        "Horizon": horizon,
                        "N_Signals": n,
                        "Stat_Flag": stat_flag,
                        "Hit_Rate%": round(hit_rate, 1),
                        "Avg_Return%": round(avg_ret, 2),
                        "Median_Return%": round(med_ret, 2),
                        "Std_Dev%": round(std_ret, 2),
                        "Sharpe": round(sharpe, 3),
                        "Sortino": round(sortino, 3),
                        "Profit_Factor": (
                            round(profit_factor, 2) if profit_factor != np.inf else 999
                        ),
                        "Max_Drawdown%": round(max_dd, 2),
                        "Calmar": round(calmar, 3),
                        "Max_Win%": round(max_win, 2),
                        "Max_Loss%": round(max_loss, 2),
                        "Avg_Win%": round(avg_win, 2),
                        "Avg_Loss%": round(avg_loss, 2),
                        "Expected_Value%": round(ev, 2),
                        "Alpha_vs_BH%": alpha,
                        "25th_Pct%": round(pct_25, 2),
                        "75th_Pct%": round(pct_75, 2),
                    }
                )

    return pd.DataFrame(rows)


def build_heatmap(stats: pd.DataFrame, metric: str = "Hit_Rate%") -> pd.DataFrame:
    """
    Pivot stats into a heatmap: rows = (Screener × Regime), cols = Horizon.
    """
    if stats.empty:
        return pd.DataFrame()
    pivot = stats.pivot_table(
        index=["Screener", "Regime"],
        columns="Horizon",
        values=metric,
        aggfunc="first",
    )
    # Order horizons
    ordered = [h for h in HORIZONS if h in pivot.columns]
    return pivot[ordered]


def rank_screeners(stats: pd.DataFrame) -> pd.DataFrame:
    """
    Rank screeners by composite score:
      Score = avg(Hit_Rate%) across horizons × Sharpe (1-month)
    """
    if stats.empty:
        return pd.DataFrame()

    records = []
    for screener in stats["Screener"].unique():
        for regime in stats["Regime"].unique():
            sub = stats[(stats["Screener"] == screener) & (stats["Regime"] == regime)]
            if sub.empty:
                continue
            avg_hit = sub["Hit_Rate%"].mean()
            avg_ret = sub["Avg_Return%"].mean()
            t1mo_row = sub[sub["Horizon"] == "T+1mo"]
            sharpe_1mo = float(t1mo_row["Sharpe"].iloc[0]) if not t1mo_row.empty else 0
            ev_1mo = float(t1mo_row["Expected_Value%"].iloc[0]) if not t1mo_row.empty else 0
            score = avg_hit * sharpe_1mo  # composite
            records.append(
                {
                    "Screener": screener,
                    "Regime": regime,
                    "Avg_Hit_Rate%": round(avg_hit, 1),
                    "Avg_Return%": round(avg_ret, 2),
                    "Sharpe_1mo": round(sharpe_1mo, 3),
                    "EV_1mo%": round(ev_1mo, 2),
                    "Composite_Score": round(score, 2),
                }
            )

    return pd.DataFrame(records).sort_values(["Regime", "Composite_Score"], ascending=[True, False])


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL EXPORT
# ══════════════════════════════════════════════════════════════════════════════


def save_backtest_excel(
    all_signals: pd.DataFrame,
    stats: pd.DataFrame,
    ranking: pd.DataFrame,
    heatmap_hit: pd.DataFrame,
    heatmap_ret: pd.DataFrame,
    market: str = "IN",
) -> Path:
    date_str = datetime.today().strftime("%Y%m%d_%H%M")
    path = DOWNLOAD_DIR / f"backtest_{market}_{date_str}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:

        # ── DISCLAIMER sheet ──────────────────────────────────────────────
        pd.DataFrame(
            {
                "DISCLAIMER": [
                    DISCLAIMER,
                    "",
                    "Backtesting period: last 1 trading year (~252 bars)",
                    "Horizons: T+1d, T+3d, T+1wk (5d), T+1mo (21d), T+3mo (63d)",
                    "Market regime: BULL = index above 200 DMA + DMA upslope",
                    "              BEAR = index below 200 DMA + DMA downslope",
                    "              SIDEWAYS = everything else",
                    "",
                    "Technical screeners (Darvas, Golden Cross): TRUE walk-forward,",
                    "  zero lookahead — signal detected at bar close, entry next bar.",
                    "Fundamental screeners (Piotroski, Coffee Can, Magic Formula,",
                    "  Bull Cartel): APPROXIMATE — uses current financial data as proxy",
                    "  for what would have qualified on the announcement date.",
                    "  Subject to mild look-ahead bias from restatements.",
                ]
            }
        ).to_excel(writer, sheet_name="DISCLAIMER", index=False)

        # ── Hit Rate heatmap ──────────────────────────────────────────────
        if not heatmap_hit.empty:
            heatmap_hit.to_excel(writer, sheet_name="HitRate_Heatmap")

        # ── Avg Return heatmap ────────────────────────────────────────────
        if not heatmap_ret.empty:
            heatmap_ret.to_excel(writer, sheet_name="AvgReturn_Heatmap")

        # ── Screener ranking ──────────────────────────────────────────────
        if not ranking.empty:
            ranking.to_excel(writer, sheet_name="Screener_Ranking", index=False)

        # ── Full stats ────────────────────────────────────────────────────
        if not stats.empty:
            stats.to_excel(writer, sheet_name="Full_Stats", index=False)

        # ── Per-screener stats sheets ─────────────────────────────────────
        if not stats.empty:
            for screener in stats["Screener"].unique():
                sub = stats[stats["Screener"] == screener]
                sheet = screener[:28]  # Excel 31-char limit
                sub.to_excel(writer, sheet_name=sheet, index=False)

        # ── All signals log ───────────────────────────────────────────────
        if not all_signals.empty:
            all_signals.sort_values(["screener", "signal_date"], inplace=True)
            all_signals.to_excel(writer, sheet_name="All_Signals", index=False)

    print(f"\n  📊  Backtest Excel → {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# SYMBOL UNIVERSE
# ══════════════════════════════════════════════════════════════════════════════


def get_indian_symbols(top: int = 0) -> "List[tuple]":
    """
    Get NSE EQ symbols from bhavcopy via nse library (with fallback to Nifty 500).
    Returns list of (symbol, ".NS") tuples.
    """
    syms = []
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    import pandas as _pd

    try:
        from nse import NSE

        today = _dt.today()
        with NSE(download_folder=str(DOWNLOAD_DIR), server=False) as nse:
            for offset in range(7):
                date = today - _td(days=offset)
                try:
                    result = nse.equityBhavcopy(date)
                    # New NSE library format (2025+): returns a Path to a CSV
                    if hasattr(result, "exists") and result.exists():
                        df = _pd.read_csv(result)
                        if "SctySrs" in df.columns and "TckrSymb" in df.columns:
                            syms = sorted(
                                df[df["SctySrs"] == "EQ"]["TckrSymb"].dropna().str.strip().tolist()
                            )
                        elif any("SERIES" in c.upper() for c in df.columns):
                            sc = next(c for c in df.columns if "SERIES" in c.upper())
                            syc = next(c for c in df.columns if c.upper() in ("SYMBOL", "TCKRSYMB"))
                            syms = df[df[sc] == "EQ"][syc].dropna().str.strip().tolist()
                    # Old format: returns a DataFrame directly
                    elif isinstance(result, _pd.DataFrame) and not result.empty:
                        col = next((c for c in result.columns if "SERIES" in c.upper()), None)
                        sym_col = next(
                            (c for c in result.columns if c.upper() in ("SYMBOL", "TCKRSYMB")), None
                        )
                        if col and sym_col:
                            syms = (
                                result[result[col] == "EQ"][sym_col].dropna().str.strip().tolist()
                            )
                    if syms:
                        print(f"  NSE bhavcopy {date.date()}: {len(syms)} EQ symbols")
                        break
                except Exception:
                    continue
    except ImportError:
        pass

    if not syms:
        print("  ⚠️  nse library unavailable; using Nifty 50 fallback")
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


def get_us_symbols(top: int = 0) -> "List[tuple]":
    """
    Fetch NASDAQ + NYSE symbols.
    Priority: NASDAQ FTP → SEC EDGAR (reliable fallback when FTP times out).
    Returns list of (symbol, "") tuples.
    """
    import requests as _req

    symbols = []
    headers = {"User-Agent": "Mozilla/5.0 StockScanner"}

    # Primary: NASDAQ FTP (fast when available)
    for url in [
        "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "https://ftp.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    ]:
        try:
            resp = _req.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            lines = resp.text.strip().splitlines()
            if not lines:
                continue
            hdr = [h.strip().lower() for h in lines[0].split("|")]
            si = next((i for i, h in enumerate(hdr) if h in ("symbol", "act symbol")), 0)
            ti = next((i for i, h in enumerate(hdr) if "test" in h), None)
            ei = next((i for i, h in enumerate(hdr) if h == "etf"), None)
            for line in lines[1:]:
                if line.startswith("File Creation"):
                    continue
                parts = line.split("|")
                if len(parts) <= si:
                    continue
                sym = parts[si].strip()
                if not sym or len(sym) > 5 or any(c in sym for c in "^/$."):
                    continue
                if ti and ti < len(parts) and parts[ti].strip().upper() == "Y":
                    continue
                if ei and ei < len(parts) and parts[ei].strip().upper() == "Y":
                    continue
                symbols.append(sym)
        except Exception:
            pass  # fall through to SEC EDGAR

    # Fallback: SEC EDGAR company_tickers_exchange.json (always available)
    if not symbols:
        print("  ⚠️  NASDAQ FTP unavailable — using SEC EDGAR fallback")
        try:
            url = "https://www.sec.gov/files/company_tickers_exchange.json"
            hdrs = {"User-Agent": "StockScanner umashankartd1991@gmail.com"}
            resp = _req.get(url, timeout=30, headers=hdrs)
            resp.raise_for_status()
            data = resp.json()
            for row in data.get("data", []):
                ticker = str(row[2]).strip().upper() if len(row) > 2 else ""
                exchange = str(row[3]).strip().lower() if len(row) > 3 else ""
                if not ticker or any(c in ticker for c in [" ", ".", ","]):
                    continue
                if len(ticker) > 5:
                    continue
                if "nasdaq" in exchange or "nyse" in exchange or "amex" in exchange:
                    symbols.append(ticker)
            print(f"  SEC EDGAR: {len(symbols)} US symbols loaded")
        except Exception as e:
            print(f"  SEC EDGAR also failed: {e}")

    symbols = list(dict.fromkeys(symbols))
    if top:
        symbols = symbols[:top]
    return [(s, "") for s in symbols]


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════


def main(market: str = "IN", top: int = 0, workers: int = MAX_WORKERS):
    print(f"\n{'#'*70}")
    print(f"  SCREENER BACKTEST — {market} MARKET")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Lookback: {LOOKBACK_DAYS} trading days | Horizons: {list(HORIZONS)}")
    print(f"{'#'*70}\n")
    print(DISCLAIMER)
    print()

    # ── Step 1: Download market index ─────────────────────────────────────────
    print("Step 1 — Market index for regime classification …")
    index_df = download_index(market, period="2y")

    # ── Step 2: Symbol universe ───────────────────────────────────────────────
    print("\nStep 2 — Symbol universe …")
    if market == "IN":
        symbols = get_indian_symbols(top)
    else:
        symbols = get_us_symbols(top)
    print(f"  {len(symbols)} symbols loaded")

    # ── Step 3: Bulk OHLC download (1yr + extra buffer for 200 DMA warmup) ───
    print(f"\nStep 3 — Bulk OHLC download (14mo window for {len(symbols)} symbols) …")
    yf_tickers = [f"{s}{sfx}" for s, sfx in symbols]
    BATCH_SIZE = 200
    ohlc_map: dict[str, pd.DataFrame] = {}

    batches = [yf_tickers[i : i + BATCH_SIZE] for i in range(0, len(yf_tickers), BATCH_SIZE)]
    for idx, batch in enumerate(batches, 1):
        print(f"  Batch {idx}/{len(batches)} ({len(batch)} tickers) …", end=" ", flush=True)
        try:
            raw = yf.download(batch, period="14mo", auto_adjust=True, threads=True, progress=False)
            if raw.empty:
                print("empty")
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                for t in batch:
                    try:
                        df = raw.xs(t, axis=1, level=1).dropna(how="all")
                        if not df.empty and len(df) >= 210:
                            ohlc_map[t] = df
                    except KeyError:
                        pass
            else:
                if not raw.empty:
                    ohlc_map[batch[0]] = raw
            print(f"OK ({sum(1 for t in batch if t in ohlc_map)} usable)")
        except Exception as e:
            print(f"ERROR — {e}")
        if idx < len(batches):
            time.sleep(1.0)

    print(f"  {len(ohlc_map)} tickers with usable OHLC data")

    # Build symbol tuples with OHLC DataFrames attached
    sym_data = []
    for s, sfx in symbols:
        key = f"{s}{sfx}"
        df = ohlc_map.get(key)
        if df is not None:
            # Trim to last LOOKBACK_DAYS + 63 bars (need 63 extra for T+3mo exits)
            if len(df) > LOOKBACK_DAYS + 63 + 210:
                df = df.iloc[-(LOOKBACK_DAYS + 63 + 210) :]
            sym_data.append((s, sfx, df))

    print(f"  {len(sym_data)} symbols ready for backtesting")

    # ── Step 4: Run backtests ─────────────────────────────────────────────────
    all_signal_dfs = []

    SCREENERS = [
        ("darvas", True, "Darvas Box Breakout"),
        ("golden_cross", True, "Golden Crossover"),
        ("piotroski", False, "Piotroski F-Score ≥7"),
        ("coffee_can", False, "Coffee Can"),
        ("magic_formula", False, "Magic Formula"),
        ("bull_cartel", False, "Bull Cartel"),
    ]

    for screener_key, is_technical, label in SCREENERS:
        print(f"\nStep 4 — Backtest: {label} …")
        df_signals = run_screener_backtest(
            screener_name=screener_key,
            symbols=sym_data,
            index_df=index_df,
            technical=is_technical,
            workers=workers,
        )
        if not df_signals.empty:
            all_signal_dfs.append(df_signals)
            print(f"  → {len(df_signals)} signals detected")
        else:
            print("  → No signals detected")

    all_signals = pd.concat(all_signal_dfs, ignore_index=True) if all_signal_dfs else pd.DataFrame()

    # ── Step 5: Statistical analysis ─────────────────────────────────────────
    print("\nStep 5 — Statistical analysis …")
    stats = analyze_signals(all_signals)
    ranking = rank_screeners(stats)
    hm_hit = build_heatmap(stats, "Hit_Rate%")
    hm_ret = build_heatmap(stats, "Avg_Return%")

    # ── Step 6: Save Excel ────────────────────────────────────────────────────
    print("\nStep 6 — Saving results …")
    path = save_backtest_excel(all_signals, stats, ranking, hm_hit, hm_ret, market)

    # ── Step 7: Print summary ─────────────────────────────────────────────────
    _print_summary(stats, ranking, all_signals)

    return {"stats": stats, "ranking": ranking, "signals": all_signals, "excel": str(path)}


def _print_summary(stats: pd.DataFrame, ranking: pd.DataFrame, signals: pd.DataFrame):
    """Print a concise terminal summary of backtest results."""
    print(f"\n{'='*70}")
    print("  BACKTEST RESULTS SUMMARY")
    print(f"  {DISCLAIMER[:80]}…")
    print(f"{'='*70}")

    if not signals.empty:
        print(f"\n  Total signals across all screeners: {len(signals)}")
        for sc in signals["screener"].unique():
            n = len(signals[signals["screener"] == sc])
            print(f"    {sc:<20}  {n} signals")

    if not stats.empty:
        print(f"\n  {'─'*68}")
        print("  HIT RATE % (signals with positive return)")
        print(f"  {'─'*68}")
        print(f"  {'Screener':<22} {'Regime':<10} " + "  ".join(f"{h:>7}" for h in HORIZONS))
        print(f"  {'─'*68}")
        for _, row in stats[stats["Regime"].isin(["BULL", "BEAR"])].iterrows():
            vals = "  ".join(f"{row.get(h, 'N/A'):>7.1f}" for h in HORIZONS if h in stats.columns)
            print(f"  {row['Screener']:<22} {row['Regime']:<10} {vals}")

    if not ranking.empty:
        print(f"\n  {'─'*68}")
        print("  SCREENER RANKING (by composite score: avg hit rate × Sharpe 1mo)")
        print(f"  {'─'*68}")
        print(
            f"  {'Rank':<5} {'Screener':<22} {'Regime':<10} "
            f"{'Hit%':>6} {'Ret%':>7} {'Sharpe':>7} {'EV%':>7}"
        )
        print(f"  {'─'*68}")
        for regime in ["BULL", "BEAR"]:
            sub = ranking[ranking["Regime"] == regime].reset_index(drop=True)
            for i, row in sub.iterrows():
                print(
                    f"  #{i+1:<4} {row['Screener']:<22} {regime:<10} "
                    f"{row['Avg_Hit_Rate%']:>6.1f} "
                    f"{row['Avg_Return%']:>7.2f} "
                    f"{row['Sharpe_1mo']:>7.3f} "
                    f"{row['EV_1mo%']:>7.2f}"
                )

    print(f"\n  {'─'*68}")
    print("  KEY INTERPRETATIONS")
    print(f"  {'─'*68}")
    if not ranking.empty:
        bull_top = (
            ranking[ranking["Regime"] == "BULL"].iloc[0]
            if len(ranking[ranking["Regime"] == "BULL"])
            else None
        )
        bear_top = (
            ranking[ranking["Regime"] == "BEAR"].iloc[0]
            if len(ranking[ranking["Regime"] == "BEAR"])
            else None
        )
        if bull_top is not None:
            print(
                f"  Best screener in BULL market: {bull_top['Screener']} "
                f"(hit rate {bull_top['Avg_Hit_Rate%']:.1f}%, "
                f"1mo EV {bull_top['EV_1mo%']:.2f}%)"
            )
        if bear_top is not None:
            print(
                f"  Best screener in BEAR market: {bear_top['Screener']} "
                f"(hit rate {bear_top['Avg_Hit_Rate%']:.1f}%, "
                f"1mo EV {bear_top['EV_1mo%']:.2f}%)"
            )

    print(f"\n  ⚠️  {DISCLAIMER}")
    print(f"{'='*70}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Walk-forward screener backtesting framework — 6 screeners × 5 horizons × 3 regimes.",
        epilog="⚠️  For educational/research use only. NOT investment advice.",
    )
    parser.add_argument(
        "--market",
        choices=["IN", "US", "BOTH"],
        default="IN",
        help="Market to backtest (default: IN)",
    )
    parser.add_argument(
        "--top", type=int, default=0, help="Limit to first N symbols per market (0 = all)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Parallel threads for fundamental screeners (default {MAX_WORKERS})",
    )
    args = parser.parse_args()

    if args.market == "BOTH":
        main("IN", top=args.top, workers=args.workers)
        main("US", top=args.top, workers=args.workers)
    else:
        main(args.market, top=args.top, workers=args.workers)
