# intraday_monitor.py
# ====================
# Real-time intraday monitoring daemon for NSE stocks.
# Fetches 15-min / 30-min OHLC and detects actionable intraday patterns.
#
# PATTERNS DETECTED
# ─────────────────
# 1. Opening Range Breakout (ORB)
#    First 15-min candle defines the opening range.
#    Signal: price closes above (LONG) or below (SHORT) the ORB.
#    Best entry: 9:30–10:30 IST. High success rate in trending markets.
#
# 2. VWAP Deviation Alert
#    Volume-Weighted Average Price = Σ(price × volume) / Σ(volume)
#    Signal: price deviates > VWAP_THRESHOLD% from VWAP (mean-reversion setup).
#    Long when price < VWAP by threshold; Short when > VWAP by threshold.
#
# 3. Volume Surge
#    Signal: current bar volume > 3× the rolling 10-bar average.
#    Confirms breakouts and momentum moves.
#
# 4. Momentum Burst
#    Signal: 3 or more consecutive 15-min bars closing in the same direction.
#    Indicates sustained intraday trend — trade with the momentum.
#
# 5. Darvas Intraday Box
#    Darvas Box algorithm applied to 15-min bars (same logic as daily).
#    Requires ≥ 20 intraday bars (≈ 5 trading days at 15-min frequency).
#    Breakout above 15-min box top = intraday momentum signal.
#
# 6. Bollinger Band Squeeze Breakout
#    Squeeze: upper band − lower band < squeeze_threshold × price.
#    Signal: price breaks outside bands after a squeeze period.
#
# MARKET HOURS
# ────────────
# NSE:  09:15 – 15:30 IST (Mon–Fri, excluding holidays)
# BSE:  09:15 – 15:30 IST
# US:   09:30 – 16:00 ET (handled separately)
#
# The monitor sleeps when market is closed. It wakes every {interval} minutes
# during market hours, fetches intraday OHLC, runs pattern detection, and
# writes signals to a daily JSON + Excel output.
#
# USAGE
# ─────
#   python intraday_monitor.py                    # 15-min interval, Nifty 50
#   python intraday_monitor.py --interval 30      # 30-min bars
#   python intraday_monitor.py --symbols RELIANCE HDFCBANK TCS
#   python intraday_monitor.py --all-fno          # all F&O eligible stocks
#   python intraday_monitor.py --once             # single scan, no loop
#
# Output: ./intraday_results/intraday_YYYYMMDD.json
#         ./intraday_results/intraday_YYYYMMDD.xlsx (end of day)

import argparse
import json
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import yfinance as yf

    _YF_OK = True
except ImportError:
    _YF_OK = False
    sys.exit("❌  pip install yfinance")

# ── Constants ─────────────────────────────────────────────────────────────────

OUT_DIR = Path("./intraday_results")
OUT_DIR.mkdir(exist_ok=True)

# NSE market hours (IST = UTC+5:30)
NSE_OPEN = (9, 15)  # 09:15 IST
NSE_CLOSE = (15, 30)  # 15:30 IST

# Pattern thresholds
ORB_BARS = 2  # first N 15-min bars define Opening Range (default: 2 = 30 min)
VWAP_THRESHOLD = 1.0  # % deviation from VWAP to trigger alert
VOLUME_SURGE_X = 3.0  # volume must be this × rolling average
MOMENTUM_BARS = 3  # consecutive same-direction bars = momentum burst
SQUEEZE_FACTOR = 0.015  # BB width < 1.5% of price = squeeze
BB_PERIOD = 20  # Bollinger Band period (bars)
BB_STD = 2.0  # Bollinger Band std devs
DARVAS_BARS = 20  # minimum bars for intraday Darvas
MAX_WORKERS = 6

DISCLAIMER = (
    "⚠️  INTRADAY DISCLAIMER: Intraday signals are for educational purposes only. "
    "High noise-to-signal ratio in intraday data. NOT trading advice. "
    "Past intraday patterns do NOT predict future movements. "
    "Consider transaction costs, STT, brokerage, and slippage before acting."
)

# Default watchlist: Nifty 50 constituents (liquid, lower spread)
NIFTY_50 = [
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


# ══════════════════════════════════════════════════════════════════════════════
# MARKET HOURS UTILITIES
# ══════════════════════════════════════════════════════════════════════════════


def is_market_open_nse() -> bool:
    """Check if NSE is currently open (IST timezone)."""
    now = datetime.utcnow() + timedelta(hours=5, minutes=30)  # UTC → IST
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h, m = now.hour, now.minute
    open_min = NSE_OPEN[0] * 60 + NSE_OPEN[1]
    close_min = NSE_CLOSE[0] * 60 + NSE_CLOSE[1]
    current = h * 60 + m
    return open_min <= current <= close_min


def minutes_to_next_open() -> int:
    """Minutes until NSE next opens."""
    now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    h, m = now.hour, now.minute
    current_min = h * 60 + m
    open_min = NSE_OPEN[0] * 60 + NSE_OPEN[1]

    if now.weekday() < 5 and current_min < open_min:
        return open_min - current_min

    # Next trading day
    days_ahead = 1
    while (now + timedelta(days=days_ahead)).weekday() >= 5:
        days_ahead += 1
    return (days_ahead * 24 * 60) - current_min + open_min


# ══════════════════════════════════════════════════════════════════════════════
# INTRADAY DATA FETCH
# ══════════════════════════════════════════════════════════════════════════════


def fetch_intraday(symbol: str, interval: int = 15, days_back: int = 5) -> pd.DataFrame:
    """
    Fetch intraday OHLCV for one stock.

    yfinance supports intraday intervals ≤ 60 days:
      interval='15m' — 15-minute bars
      interval='30m' — 30-minute bars

    days_back: how many trading days to fetch (5 = 1 trading week).
    More history enables better pattern detection (Darvas needs ≥20 bars).
    """
    if not _YF_OK:
        return pd.DataFrame()
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(period=f"{days_back}d", interval=f"{interval}m")
        if df.empty:
            return pd.DataFrame()
        # Standardise columns
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index)
        # Convert to IST if timezone-aware
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Kolkata")
        return df.dropna()
    except Exception:
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTORS
# ══════════════════════════════════════════════════════════════════════════════


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP = cumulative(price × volume) / cumulative(volume). Resets each day."""
    # Group by date to reset VWAP each morning
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = typical * df["Volume"]

    vwap_list = []
    for day in df.index.normalize().unique():
        mask = df.index.normalize() == day
        cum_pv = pv[mask].cumsum()
        cum_vol = df["Volume"][mask].cumsum().replace(0, np.nan)
        day_vwap = cum_pv / cum_vol
        vwap_list.append(day_vwap)

    return pd.concat(vwap_list).sort_index()


def detect_orb(df: pd.DataFrame, n_bars: int = ORB_BARS) -> dict:
    """
    Opening Range Breakout (ORB).

    The first n_bars of the trading day define the Opening Range (High/Low).
    A breakout occurs when the current close exceeds the ORB High (LONG signal)
    or drops below the ORB Low (SHORT signal).

    Best results: 9:15–10:30 IST entries, target = 1.5–2× range, stop = opposite side.
    """
    if df.empty or len(df) < n_bars + 1:
        return {"pattern": "ORB", "signal": None}

    # Use today's bars only
    today = df.index.normalize().max()
    today_df = df[df.index.normalize() == today]

    if len(today_df) < n_bars + 1:
        return {"pattern": "ORB", "signal": None}

    # Opening range: first n_bars
    orb_high = today_df.iloc[:n_bars]["High"].max()
    orb_low = today_df.iloc[:n_bars]["Low"].min()
    current = today_df.iloc[-1]
    curr_close = float(current["Close"])
    orb_range = orb_high - orb_low

    signal = None
    if curr_close > orb_high:
        signal = "BREAKOUT_LONG"
    elif curr_close < orb_low:
        signal = "BREAKDOWN_SHORT"

    return {
        "pattern": "ORB",
        "signal": signal,
        "orb_high": round(float(orb_high), 2),
        "orb_low": round(float(orb_low), 2),
        "orb_range": round(orb_range, 2),
        "current": round(curr_close, 2),
        "range_pct": round(orb_range / curr_close * 100, 2),
        "time": str(today_df.index[-1]),
    }


def detect_vwap_deviation(df: pd.DataFrame, threshold_pct: float = VWAP_THRESHOLD) -> dict:
    """
    VWAP Deviation Alert.

    Price significantly above VWAP → potential mean-reversion SHORT.
    Price significantly below VWAP → potential mean-reversion LONG.

    Works best in range-bound intraday sessions.
    """
    if df.empty or len(df) < 5:
        return {"pattern": "VWAP", "signal": None}
    try:
        vwap = compute_vwap(df)
        curr = float(df["Close"].iloc[-1])
        curr_vwap = float(vwap.iloc[-1]) if not vwap.empty else curr
        dev_pct = (curr - curr_vwap) / curr_vwap * 100

        signal = None
        if dev_pct >= threshold_pct:
            signal = "ABOVE_VWAP_REVERT"  # price extended above — possible short
        elif dev_pct <= -threshold_pct:
            signal = "BELOW_VWAP_REVERT"  # price extended below — possible long

        return {
            "pattern": "VWAP",
            "signal": signal,
            "vwap": round(curr_vwap, 2),
            "current": round(curr, 2),
            "deviation%": round(dev_pct, 2),
        }
    except Exception:
        return {"pattern": "VWAP", "signal": None}


def detect_volume_surge(df: pd.DataFrame, surge_factor: float = VOLUME_SURGE_X) -> dict:
    """
    Volume Surge Detection.

    Current bar volume > surge_factor × rolling 10-bar average.
    High volume breakouts are more reliable than low-volume ones.
    Combine with price action (ORB or Darvas) for best results.
    """
    if df.empty or len(df) < 11:
        return {"pattern": "VOL_SURGE", "signal": None}

    vols = df["Volume"].astype(float)
    curr_vol = float(vols.iloc[-1])
    avg_vol = float(vols.iloc[-11:-1].mean())

    if avg_vol == 0:
        return {"pattern": "VOL_SURGE", "signal": None}

    ratio = curr_vol / avg_vol

    return {
        "pattern": "VOL_SURGE",
        "signal": "SURGE" if ratio >= surge_factor else None,
        "curr_vol": int(curr_vol),
        "avg_vol": int(avg_vol),
        "ratio": round(ratio, 2),
        "time": str(df.index[-1]),
    }


def detect_momentum_burst(df: pd.DataFrame, n_bars: int = MOMENTUM_BARS) -> dict:
    """
    Momentum Burst: N consecutive bars closing in the same direction.

    3+ consecutive UP closes → bullish momentum (trade long with momentum).
    3+ consecutive DOWN closes → bearish momentum (avoid longs).

    Combine with VWAP position for confluence:
      Consecutive UP + price above VWAP → strong long signal.
    """
    if df.empty or len(df) < n_bars + 1:
        return {"pattern": "MOMENTUM", "signal": None}

    closes = df["Close"].astype(float)
    recent = closes.iloc[-n_bars:]
    diffs = recent.diff().dropna()

    direction = None
    if all(d > 0 for d in diffs):
        direction = "BULLISH_BURST"
    elif all(d < 0 for d in diffs):
        direction = "BEARISH_BURST"

    return {
        "pattern": "MOMENTUM",
        "signal": direction,
        "bars_checked": n_bars,
        "recent_closes": [round(float(c), 2) for c in recent.tolist()],
    }


def detect_bb_squeeze(
    df: pd.DataFrame,
    period: int = BB_PERIOD,
    num_std: float = BB_STD,
    squeeze_factor: float = SQUEEZE_FACTOR,
) -> dict:
    """
    Bollinger Band Squeeze Breakout.

    Squeeze: BB width (upper-lower) / price < squeeze_factor.
    Breakout: price closes outside the bands after a squeeze.

    Squeezes precede large intraday moves. The direction of the
    breakout determines whether to go long or short.
    """
    if df.empty or len(df) < period + 2:
        return {"pattern": "BB_SQUEEZE", "signal": None}

    close = df["Close"].astype(float)
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    width = (upper - lower) / close  # normalised width

    curr_close = float(close.iloc[-1])
    curr_upper = float(upper.iloc[-1])
    curr_lower = float(lower.iloc[-1])
    curr_width = float(width.iloc[-1]) if not np.isnan(width.iloc[-1]) else 1.0
    prev_widths = width.iloc[-6:-1].dropna()
    in_squeeze = (prev_widths < squeeze_factor).any() if not prev_widths.empty else False

    signal = None
    if in_squeeze:
        if curr_close > curr_upper:
            signal = "SQUEEZE_BREAKOUT_UP"
        elif curr_close < curr_lower:
            signal = "SQUEEZE_BREAKOUT_DOWN"

    return {
        "pattern": "BB_SQUEEZE",
        "signal": signal,
        "bb_upper": round(curr_upper, 2),
        "bb_lower": round(curr_lower, 2),
        "bb_width%": round(curr_width * 100, 2),
        "in_squeeze": in_squeeze,
        "current": round(curr_close, 2),
    }


def detect_intraday_darvas(df: pd.DataFrame, confirm: int = 3) -> dict:
    """
    Darvas Box applied to intraday (15-min) bars.

    Same algorithm as daily Darvas: current bar excluded from box formation.
    Requires ≥ DARVAS_BARS (20) intraday bars (~5 trading days at 15-min).

    Good for identifying intraday consolidation zones and breakouts.
    """
    if df.empty or len(df) < DARVAS_BARS:
        return {
            "pattern": "INTRADAY_DARVAS",
            "signal": None,
            "note": f"need ≥{DARVAS_BARS} bars, have {len(df)}",
        }

    highs = df["High"].astype(float).values
    lows = df["Low"].astype(float).values
    closes = df["Close"].astype(float).values
    n = len(closes)

    current = closes[-1]
    h, lo = highs[:-1], lows[:-1]

    box_top = box_top_idx = None
    for i in range(len(h) - confirm - 1, max(0, len(h) - 30) - 1, -1):
        if h[i] == 0:
            continue
        win = h[i + 1 : i + 1 + confirm]
        if len(win) == confirm and all(x < h[i] for x in win):
            box_top, box_top_idx = h[i], i
            break

    if box_top is None:
        return {"pattern": "INTRADAY_DARVAS", "signal": "NO_BOX", "current": round(current, 2)}

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
            "pattern": "INTRADAY_DARVAS",
            "signal": "NO_BOX",
            "box_top": round(box_top, 2),
            "current": round(current, 2),
        }

    prev = closes[-2] if n > 1 else current
    signal = None
    if current > box_top and prev <= box_top:
        signal = "INTRADAY_BREAKOUT"
    elif current < box_bottom and prev >= box_bottom:
        signal = "INTRADAY_BREAKDOWN"
    elif current > box_top:
        signal = "ABOVE_BOX"
    elif current < box_bottom:
        signal = "BELOW_BOX"
    else:
        signal = "IN_BOX"

    upside = (box_top - current) / current * 100 if current else 0

    return {
        "pattern": "INTRADAY_DARVAS",
        "signal": signal,
        "box_top": round(float(box_top), 2),
        "box_bottom": round(float(box_bottom), 2),
        "current": round(current, 2),
        "upside%": round(upside, 2),
        "time": str(df.index[-1]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# COMBINED SCAN
# ══════════════════════════════════════════════════════════════════════════════


def scan_one(symbol: str, interval: int = 15) -> dict:
    """
    Run all 6 intraday pattern detectors for one stock.
    Returns a signal dict with all pattern results.
    """
    df = fetch_intraday(symbol, interval=interval, days_back=5)
    if df.empty:
        return {"symbol": symbol, "error": "no_data", "signals": []}

    curr_price = round(float(df["Close"].iloc[-1]), 2)
    curr_vol = int(df["Volume"].iloc[-1])
    curr_time = str(df.index[-1])

    patterns = [
        detect_orb(df),
        detect_vwap_deviation(df),
        detect_volume_surge(df),
        detect_momentum_burst(df),
        detect_bb_squeeze(df),
        detect_intraday_darvas(df),
    ]

    # Aggregate active signals
    active = [p for p in patterns if p.get("signal") and p["signal"] != "IN_BOX"]
    confluence = len(active)

    # Score: higher = more patterns agreeing
    bullish = sum(
        1
        for p in active
        if any(
            kw in (p.get("signal") or "")
            for kw in ["LONG", "BREAKOUT", "BURST", "UP", "ABOVE", "BULLISH"]
        )
    )
    bearish = sum(
        1
        for p in active
        if any(
            kw in (p.get("signal") or "")
            for kw in ["SHORT", "BREAKDOWN", "BEARISH", "DOWN", "BELOW", "REVERT"]
        )
    )

    direction = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"

    return {
        "symbol": symbol,
        "time": curr_time,
        "price": curr_price,
        "volume": curr_vol,
        "interval_m": interval,
        "confluence": confluence,
        "direction": direction,
        "bullish_n": bullish,
        "bearish_n": bearish,
        "patterns": patterns,
        "active": active,
    }


def run_scan(symbols: list, interval: int = 15, workers: int = MAX_WORKERS) -> pd.DataFrame:
    """Run intraday scan for all symbols in parallel."""
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_one, s, interval): s for s in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                r = future.result()
                results.append(r)
            except Exception as e:
                results.append({"symbol": sym, "error": str(e), "signals": []})
            if done % 10 == 0 or done == len(symbols):
                active = sum(1 for r in results if r.get("confluence", 0) > 0)
                print(f"    {done}/{len(symbols)} scanned | {active} with signals")

    # Flatten for DataFrame
    rows = []
    for r in results:
        if r.get("error"):
            continue
        row = {
            "Symbol": r["symbol"],
            "Time": r["time"],
            "Price_₹": r["price"],
            "Volume": r["volume"],
            "Direction": r["direction"],
            "Confluence": r["confluence"],
            "Bullish_N": r["bullish_n"],
            "Bearish_N": r["bearish_n"],
        }
        for p in r.get("patterns", []):
            pat = p.get("pattern", "")
            row[f"{pat}_signal"] = p.get("signal")
            if "deviation%" in p:
                row["VWAP_Dev%"] = p["deviation%"]
            if "ratio" in p:
                row["Vol_Ratio"] = p["ratio"]
            if "orb_high" in p:
                row["ORB_High"] = p["orb_high"]
            if "orb_low" in p:
                row["ORB_Low"] = p["orb_low"]
            if "box_top" in p:
                row["Box_Top"] = p["box_top"]
            if "box_bottom" in p:
                row["Box_Bottom"] = p["box_bottom"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Confluence", ascending=False)
    return df


def save_signals(df: pd.DataFrame, interval: int):
    """Save intraday signals to JSON + append to daily Excel."""
    today = date.today().strftime("%Y%m%d")
    ts = datetime.now().strftime("%H%M")

    # JSON (per scan)
    json_path = OUT_DIR / f"intraday_{today}_{interval}m.json"
    records = df.to_dict(orient="records") if not df.empty else []
    existing = json.loads(json_path.read_text()) if json_path.exists() else []
    existing.append({"scan_time": datetime.now().isoformat(), "signals": records})
    json_path.write_text(json.dumps(existing, indent=2, default=str))

    # Excel (end-of-day summary)
    xl_path = OUT_DIR / f"intraday_{today}.xlsx"
    with pd.ExcelWriter(
        xl_path,
        engine="openpyxl",
        mode="a" if xl_path.exists() else "w",
        if_sheet_exists="replace" if xl_path.exists() else None,
    ) as w:
        if not df.empty:
            df.to_excel(w, sheet_name=f"Scan_{ts}_{interval}m", index=False)
            # High-conviction: confluence ≥ 3
            hc = df[df["Confluence"] >= 3]
            if not hc.empty:
                hc.to_excel(w, sheet_name=f"HighConv_{ts}", index=False)

    return json_path, xl_path


def print_summary(df: pd.DataFrame, interval: int):
    """Print a concise terminal summary of current intraday signals."""
    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    print(f"\n{'='*70}")
    print(f"  INTRADAY MONITOR — {now_ist.strftime('%d %b %Y  %H:%M')} IST | {interval}-min bars")
    print(f"  {DISCLAIMER[:70]}…")
    print(f"{'='*70}")

    if df.empty:
        print("  No signals detected.")
        return

    bullish = df[df["Direction"] == "BULLISH"]
    bearish = df[df["Direction"] == "BEARISH"]

    print(f"\n  Scanned: {len(df)} stocks | BULLISH: {len(bullish)} | BEARISH: {len(bearish)}")

    if not bullish.empty:
        print("\n  🟢 TOP BULLISH SIGNALS (by confluence):")
        print(f"  {'Symbol':<12} {'Price':>8} {'Confluence':>11} {'Patterns'}")
        print("  " + "─" * 60)
        for _, r in bullish.head(10).iterrows():
            sigs = [
                col.replace("_signal", "")
                for col in df.columns
                if col.endswith("_signal") and pd.notna(r.get(col)) and r.get(col)
            ]
            print(
                f"  {r['Symbol']:<12} ₹{r['Price_₹']:>8,.2f}  {r['Confluence']:>10}  "
                f"{', '.join(sigs)}"
            )

    if not bearish.empty:
        print("\n  🔴 TOP BEARISH SIGNALS:")
        print(f"  {'Symbol':<12} {'Price':>8} {'Confluence':>11} {'Patterns'}")
        print("  " + "─" * 60)
        for _, r in bearish.head(5).iterrows():
            sigs = [
                col.replace("_signal", "")
                for col in df.columns
                if col.endswith("_signal") and pd.notna(r.get(col)) and r.get(col)
            ]
            print(
                f"  {r['Symbol']:<12} ₹{r['Price_₹']:>8,.2f}  {r['Confluence']:>10}  "
                f"{', '.join(sigs)}"
            )

    # High conviction
    hc = df[df["Confluence"] >= 3]
    if not hc.empty:
        print(f"\n  ⭐ HIGH CONVICTION (3+ patterns): " f"{', '.join(hc['Symbol'].tolist())}")

    print(f"\n  ⚠️  {DISCLAIMER}")
    print(f"{'='*70}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN DAEMON
# ══════════════════════════════════════════════════════════════════════════════


def main(interval: int = 15, symbols: list = None, workers: int = MAX_WORKERS, once: bool = False):

    syms = symbols or NIFTY_50
    print(f"\n{'#'*65}")
    print(f"  INTRADAY MONITOR — {interval}-min bars")
    print(f"  Stocks: {len(syms)} | Workers: {workers}")
    print("  Market hours: 09:15–15:30 IST (Mon–Fri)")
    print(f"{'#'*65}")
    print(f"\n  {DISCLAIMER}\n")

    scan_count = 0

    while True:
        if not is_market_open_nse():
            wait_min = minutes_to_next_open()
            if once:
                print("  Market closed. Run with --once during market hours.")
                break
            print(f"  Market closed — sleeping {wait_min} minutes until open …")
            time.sleep(min(wait_min * 60, 3600))  # sleep max 1 hour at a time
            continue

        # Market is open — run scan
        scan_count += 1
        now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        print(f"\n  [{now.strftime('%H:%M')} IST] Scan #{scan_count} — {len(syms)} stocks …")

        df = run_scan(syms, interval=interval, workers=workers)

        if not df.empty:
            json_p, xl_p = save_signals(df, interval)
            print_summary(df, interval)
            print(f"  Saved: {json_p.name} | {xl_p.name}")

        if once:
            break

        # Sleep until next interval
        sleep_sec = interval * 60
        print(f"  Next scan in {interval} minutes …")
        time.sleep(sleep_sec)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Intraday pattern monitor: ORB, VWAP, Volume, Momentum, Darvas, BB.",
        epilog="⚠️  Intraday signals are noisy. NOT trading advice.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        choices=[15, 30],
        default=15,
        help="Bar interval in minutes (default: 15)",
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None, help="Space-separated NSE tickers (default: Nifty 50)"
    )
    parser.add_argument(
        "--all-fno",
        action="store_true",
        default=False,
        help="Monitor all F&O eligible stocks (~180 stocks)",
    )
    parser.add_argument(
        "--workers", type=int, default=MAX_WORKERS, help=f"Parallel threads (default {MAX_WORKERS})"
    )
    parser.add_argument(
        "--once", action="store_true", default=False, help="Run one scan and exit (no loop)"
    )
    args = parser.parse_args()

    syms = args.symbols
    if args.all_fno:
        try:
            from nsepython import fnolist

            syms = list(fnolist())
            print(f"  F&O stocks: {len(syms)}")
        except Exception:
            print("  ⚠️  fnolist unavailable — using Nifty 50")
            syms = NIFTY_50

    main(interval=args.interval, symbols=syms, workers=args.workers, once=args.once)
