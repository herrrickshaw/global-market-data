# us_market_screener.py
# ======================
# Full NASDAQ + NYSE/AMEX equity screener.
# Runs Darvas Box, Piotroski F-Score, and Coffee Can screens on every common
# stock listed on US exchanges (~6,500 after filtering ETFs and test issues).
#
# ── Two-phase design ────────────────────────────────────────────────────────
#   Phase 1 — Darvas (fast):
#     Downloads 6-month OHLC for all stocks in batches of 500 using
#     yf.download(), which makes one bulk HTTP request per batch.
#     Estimated time: ~3-5 minutes for all 6,500 stocks.
#
#   Phase 2 — Fundamentals (slower):
#     Fetches income_stmt + balance_sheet + cash_flow per stock using
#     ThreadPoolExecutor (default 10 workers).
#     Estimated time: ~35-50 minutes for all 6,500 stocks.
#     Use --darvas-only to skip this phase.
#
# ── Resumability ─────────────────────────────────────────────────────────────
#   All results are written to SQLite after each stock.  If the run is
#   interrupted, pass --resume to skip already-processed symbols.
#
# ── Output files (./us_screener_output/) ─────────────────────────────────────
#   stock_universe.csv          — filtered stock list (6,500 stocks)
#   scan_summary_YYYYMMDD.csv   — one row per stock, all scan results
#   darvas_breakouts_YYYYMMDD.csv  — BREAKOUT_BUY signal only
#   strong_piotroski_YYYYMMDD.csv  — F-Score >= 7
#   coffee_can_YYYYMMDD.csv     — Coffee Can qualifiers
#   triple_hits_YYYYMMDD.csv    — pass all 3 scans (rarest, highest conviction)
#   screener.db                 — SQLite checkpoint
#
# ── Install ───────────────────────────────────────────────────────────────────
#   pip install yfinance pandas tqdm requests
#
# ── Usage ─────────────────────────────────────────────────────────────────────
#   python us_market_screener.py                        # full run, all 6,500 stocks
#   python us_market_screener.py --limit 200            # quick test on first 200
#   python us_market_screener.py --resume               # resume interrupted run
#   python us_market_screener.py --darvas-only          # skip fundamentals (~5 min)
#   python us_market_screener.py --exchange nasdaq      # NASDAQ stocks only
#   python us_market_screener.py --workers 20           # more threads (watch rate limits)
#   python us_market_screener.py --batch-size 300       # smaller OHLC batches

# ── Colab quick-start ─────────────────────────────────────────────────────────
# !pip install yfinance pandas tqdm requests
# exec(open('/content/us_market_screener.py').read())
# main(limit=100, darvas_only=True)  # test first

import argparse
import io
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

try:
    from tqdm import tqdm

    TQDM_OK = True
except ImportError:
    TQDM_OK = False
    print("tip: pip install tqdm  for progress bars")

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("screener")


# ── Constants ─────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path("./us_screener_output")
DB_FILE = OUTPUT_DIR / "screener.db"
DARVAS_CONFIRM = 3

# NASDAQ Trader machine-readable stock lists (updated daily by NASDAQ).
NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Piotroski + Coffee Can STRONG thresholds
PIOTROSKI_STRONG = 7  # F-Score >= this → STRONG
COFFEE_CAN_CRITERIA = 6  # total criteria in Coffee Can screen


# ═══════════════════════════════════════════════════════════════════════════════
# STOCK UNIVERSE  (NASDAQ Trader pipe-delimited files)
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_stock_universe(exchange_filter: str = "all") -> pd.DataFrame:
    """
    Download and parse the official NASDAQ Trader stock lists.

    NASDAQ provides two pipe-delimited files updated each trading day:
      nasdaqlisted.txt — all NASDAQ-listed equities
      otherlisted.txt  — NYSE, AMEX, ARCA, BATS equities

    Filters applied:
      • Test Issue == N        (skip fake symbols used for system testing)
      • ETF == N               (equities only, no exchange-traded funds)
      • Financial Status == N  (skip bankrupt/deficient/delisted companies)
      • Symbol regex [A-Z]{1-5} (skip preferred shares like BRK-A, warrants, units)

    Returns a DataFrame with columns: Symbol, Name, Exchange.
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

    combined = pd.concat(frames).drop_duplicates("Symbol").reset_index(drop=True)
    return combined


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 1 — DARVAS BOX  (batch OHLC via yf.download)
# ═══════════════════════════════════════════════════════════════════════════════


def _darvas_from_df(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """
    Compute Darvas Box signal from a flat OHLC DataFrame.

    Algorithm: scan historical bars (all except the last) to find the most
    recent confirmed box top and bottom, then classify today's close.

    KEY RULE: the current bar (last row) is excluded from box formation.
    A breakdown bar would otherwise pull the box bottom to its own low,
    making BREAKDOWN_SELL impossible to detect.

    Returns dict with signal, box_top, box_bottom, current_price, upside_pct.
    """
    if df is None or df.empty or len(df) < confirm + 5:
        return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None}

    # yf.download columns: Open, High, Low, Close, Volume
    try:
        highs = pd.to_numeric(df["High"], errors="coerce").fillna(0).tolist()
        lows = pd.to_numeric(df["Low"], errors="coerce").fillna(0).tolist()
        closes = pd.to_numeric(df["Close"], errors="coerce").fillna(0).tolist()
    except KeyError:
        return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None}

    current = closes[-1]
    highs_h = highs[:-1]  # historical only
    lows_h = lows[:-1]
    n = len(highs_h)

    # Step 1 — find confirmed box top
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
        return {"signal": "NO_BOX", "box_top": None, "box_bottom": None}

    # Step 2 — find confirmed box bottom (historical segment only)
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
        return {"signal": "NO_BOX", "box_top": round(box_top, 2), "box_bottom": None}

    # Step 3 — classify today's close
    if current > box_top:
        signal = "BREAKOUT_BUY"
    elif current < box_bottom:
        signal = "BREAKDOWN_SELL"
    else:
        signal = "IN_BOX"

    rng = box_top - box_bottom
    upside_pct = (box_top - current) / current * 100 if current else 0
    pos_in_box = (current - box_bottom) / rng * 100 if rng else 0

    return {
        "signal": signal,
        "box_top": round(box_top, 2),
        "box_bottom": round(box_bottom, 2),
        "current_price": round(current, 2),
        "upside_pct": round(upside_pct, 2),
        "pos_in_box": round(pos_in_box, 1),
    }


def run_darvas_batch(symbols: list, batch_size: int = 500, pbar=None) -> dict:
    """
    Download 6-month OHLC for all symbols in batches and compute Darvas signals.

    Uses yf.download() which makes a single HTTP request per batch of N symbols
    — far more efficient than one ticker at a time.

    Returns {symbol: darvas_result_dict}.
    """
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
                # MultiIndex when multiple tickers; flat when single ticker
                if isinstance(raw.columns, pd.MultiIndex):
                    if sym not in raw.columns.get_level_values(0):
                        results[sym] = {"signal": "NO_DATA", "box_top": None, "box_bottom": None}
                        continue
                    df = raw[sym].dropna()
                else:
                    df = raw.dropna()  # single-ticker path

                results[sym] = _darvas_from_df(df)
            except Exception as e:
                log.debug("Darvas error %s: %s", sym, e)
                results[sym] = {"signal": "ERROR", "box_top": None, "box_bottom": None}

            if pbar:
                pbar.update(1)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 2 — PIOTROSKI F-SCORE  (per-stock, yfinance financial statements)
# ═══════════════════════════════════════════════════════════════════════════════


def _first_df(ticker, *attrs):
    """
    Return the first non-None, non-empty DataFrame from ticker attributes.

    NEVER use Python `or` between two DataFrame expressions — if the first
    attribute returns a non-empty DataFrame, `or` raises ValueError
    ("ambiguous truth value of a DataFrame").
    """
    for attr in attrs:
        df = getattr(ticker, attr, None)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return None


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


def run_piotroski(ticker: yf.Ticker) -> dict:
    """
    Piotroski F-Score (0–9).

    ── 9 criteria ──────────────────────────────────────────────────────────
    Profitability (4):  ROA>0, OCF>0, ΔROA>0, accruals quality
    Leverage (3):       ΔLT-debt↓, ΔCurrent-ratio↑, no dilution
    Efficiency (2):     ΔGross-margin↑, ΔAsset-turnover↑

    ≥7 → STRONG;  4-6 → MODERATE;  ≤3 → WEAK
    """
    inc = _first_df(ticker, "income_stmt", "financials")
    bal = _first_df(ticker, "balance_sheet")
    cf = _first_df(ticker, "cash_flow", "cashflow")

    if inc is None or inc.empty:
        return {"f_score": None, "error": "no_income_data"}

    s = {}  # individual criterion scores

    # Profitability
    ni0 = _val(inc, "Net Income", col=0)
    ni1 = _val(inc, "Net Income", col=1)
    a0 = _val(bal, "Total Assets", col=0)
    a1 = _val(bal, "Total Assets", col=1)
    roa0 = ni0 / a0 if (ni0 and a0) else None
    roa1 = ni1 / a1 if (ni1 and a1) else None
    s["F1"] = 1 if (roa0 and roa0 > 0) else 0
    ocf0 = _val(cf, "Operating Cash Flow", "Total Cash From Operating Activities", col=0)
    s["F2"] = 1 if (ocf0 and ocf0 > 0) else 0
    s["F3"] = 1 if (roa0 is not None and roa1 is not None and roa0 > roa1) else 0
    s["F4"] = 1 if (ocf0 and a0 and roa0 is not None and (ocf0 / a0) > roa0) else 0

    # Leverage & Liquidity
    ltd0 = _val(bal, "Long Term Debt", col=0) or 0
    ltd1 = _val(bal, "Long Term Debt", col=1) or 0
    lev0 = (ltd0 / a0) if a0 else None
    lev1 = (ltd1 / a1) if a1 else None
    s["F5"] = 1 if (lev0 is not None and lev1 is not None and lev0 < lev1) else 0

    ca0 = _val(bal, "Current Assets", "Total Current Assets", col=0)
    cl0 = _val(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca1 = _val(bal, "Current Assets", "Total Current Assets", col=1)
    cl1 = _val(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    cr0 = (ca0 / cl0) if (ca0 and cl0) else None
    cr1 = (ca1 / cl1) if (ca1 and cl1) else None
    s["F6"] = 1 if (cr0 is not None and cr1 is not None and cr0 > cr1) else 0

    sh0 = _val(bal, "Share Issued", col=0)
    sh1 = _val(bal, "Share Issued", col=1)
    s["F7"] = (1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1

    # Operating Efficiency
    rev0 = _val(inc, "Total Revenue", col=0)
    gp0 = _val(inc, "Gross Profit", col=0)
    rev1 = _val(inc, "Total Revenue", col=1)
    gp1 = _val(inc, "Gross Profit", col=1)
    gm0 = (gp0 / rev0) if (gp0 and rev0) else None
    gm1 = (gp1 / rev1) if (gp1 and rev1) else None
    s["F8"] = 1 if (gm0 is not None and gm1 is not None and gm0 > gm1) else 0

    at0 = (rev0 / a0) if (rev0 and a0) else None
    at1 = (rev1 / a1) if (rev1 and a1) else None
    s["F9"] = 1 if (at0 is not None and at1 is not None and at0 > at1) else 0

    total = sum(s.values())
    return {"f_score": total, "components": s, "error": None}


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 3 — COFFEE CAN SCREEN  (per-stock, yfinance)
# ═══════════════════════════════════════════════════════════════════════════════


def run_coffee_can(ticker: yf.Ticker) -> dict:
    """
    US Coffee Can Portfolio screen — all 6 criteria must pass.

    C1  Revenue CAGR > 10%
    C2  Return on Equity > 15% avg
    C3  Debt/Equity < 1
    C4  Market Cap ≥ $1 Billion
    C5  No loss-making fiscal year
    C6  Free Cash Flow > 0  (US bonus criterion)
    """
    inc = _first_df(ticker, "income_stmt", "financials")
    bal = _first_df(ticker, "balance_sheet")
    cf = _first_df(ticker, "cash_flow", "cashflow")

    info = {}
    try:
        info = ticker.info or {}
    except Exception:
        pass

    if inc is None or inc.empty:
        return {"qualifies": False, "score": "0/6", "error": "no_income_data"}

    def series(df, *rows):
        for name in rows:
            if df is not None and name in df.index:
                return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
        return []

    c = {}

    # C1 — Revenue CAGR > 10%
    revs = series(inc, "Total Revenue")
    if len(revs) >= 2:
        yrs = len(revs) - 1
        cagr = ((revs[0] / revs[-1]) ** (1 / yrs) - 1) * 100 if revs[-1] > 0 else None
        c["C1"] = 1 if (cagr and cagr > 10) else 0
    else:
        c["C1"] = 0

    # C2 — ROE > 15% avg
    ni_s = series(inc, "Net Income")
    eq_s = series(
        bal,
        "Stockholders Equity",
        "Total Stockholder Equity",
        "Total Equity Gross Minority Interest",
    )
    roe_list = [ni_s[i] / eq_s[i] * 100 for i in range(min(len(ni_s), len(eq_s))) if eq_s[i] > 0]
    avg_roe = sum(roe_list) / len(roe_list) if roe_list else None
    c["C2"] = 1 if (avg_roe and avg_roe > 15) else 0

    # C3 — Debt/Equity < 1
    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        # yfinance sometimes returns D/E in percent (45.2 = 0.452×); normalise
        de = de_raw / 100 if de_raw > 10 else de_raw
        c["C3"] = 1 if de < 1 else 0
    else:
        ltd_s = series(bal, "Long Term Debt")
        c["C3"] = 1 if (ltd_s and eq_s and eq_s[0] > 0 and ltd_s[0] / eq_s[0] < 1) else 0

    # C4 — Market Cap ≥ $1B
    mcap = info.get("marketCap")
    if mcap is None:
        try:
            mcap = ticker.fast_info.market_cap
        except Exception:
            pass
    c["C4"] = 1 if (mcap and mcap >= 1e9) else 0

    # C5 — No loss year
    c["C5"] = 1 if (ni_s and all(n > 0 for n in ni_s)) else 0

    # C6 — Free Cash Flow > 0
    fcf_s = series(cf, "Free Cash Flow")
    if fcf_s:
        c["C6"] = 1 if fcf_s[0] > 0 else 0
    else:
        ocf_s = series(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex_s = series(cf, "Capital Expenditure", "Capital Expenditures")
        if ocf_s and capex_s:
            c["C6"] = 1 if (ocf_s[0] - abs(capex_s[0])) > 0 else 0
        else:
            c["C6"] = 0

    total = sum(c.values())
    return {
        "qualifies": total == COFFEE_CAN_CRITERIA,
        "score": f"{total}/{COFFEE_CAN_CRITERIA}",
        "criteria": c,
        "roe_avg": round(avg_roe, 2) if avg_roe else None,
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SQLITE CHECKPOINT  (survives interruptions; enables --resume)
# ═══════════════════════════════════════════════════════════════════════════════


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create (or open existing) SQLite database with results + progress tables."""
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")  # allow concurrent reads while writing
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
            -- Darvas
            darvas_signal   TEXT,
            darvas_box_top  REAL,
            darvas_box_bot  REAL,
            darvas_current  REAL,
            darvas_upside   REAL,
            darvas_pos      REAL,
            -- Piotroski
            f_score         INTEGER,
            f1 INTEGER, f2 INTEGER, f3 INTEGER, f4 INTEGER,
            f5 INTEGER, f6 INTEGER, f7 INTEGER, f8 INTEGER, f9 INTEGER,
            -- Coffee Can
            cc_qualifies    INTEGER,
            cc_score        TEXT,
            cc_c1 INTEGER, cc_c2 INTEGER, cc_c3 INTEGER,
            cc_c4 INTEGER, cc_c5 INTEGER, cc_c6 INTEGER,
            cc_roe_avg      REAL,
            -- Meta
            scanned_at      TEXT,
            error           TEXT
        )
    """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS progress (
            symbol     TEXT PRIMARY KEY,
            phase      TEXT,        -- 'darvas' | 'fundamentals'
            done_at    TEXT
        )
    """
    )
    con.commit()
    return con


def already_done(con: sqlite3.Connection, phase: str) -> set:
    """Return the set of symbols already processed in the given phase."""
    cur = con.execute("SELECT symbol FROM progress WHERE phase = ?", (phase,))
    return {r[0] for r in cur.fetchall()}


def mark_done(con: sqlite3.Connection, symbol: str, phase: str):
    con.execute(
        "INSERT OR REPLACE INTO progress VALUES (?,?,?)",
        (symbol, phase, datetime.now().isoformat()),
    )
    con.commit()


def upsert_result(con: sqlite3.Connection, row: dict):
    """Insert or update a result row (partial updates are fine)."""
    cols = ", ".join(row.keys())
    placeh = ", ".join(["?"] * len(row))
    update = ", ".join([f"{k} = excluded.{k}" for k in row if k != "symbol"])
    con.execute(
        f"INSERT INTO results ({cols}) VALUES ({placeh}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {update}",
        list(row.values()),
    )
    con.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS WORKER  (runs inside a thread pool)
# ═══════════════════════════════════════════════════════════════════════════════


def scan_fundamentals_one(symbol: str, name: str, exchange: str) -> dict:
    """
    Fetch yfinance metadata + run Piotroski + Coffee Can for one stock.
    Called from ThreadPoolExecutor — must be thread-safe (no shared state).

    Returns a flat dict ready for upsert_result().
    """
    row = {
        "symbol": symbol,
        "name": name,
        "exchange": exchange,
        "scanned_at": datetime.now().isoformat(),
    }
    try:
        ticker = yf.Ticker(symbol)

        # Lightweight metadata (sector, industry, price, market cap)
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

        # Piotroski
        piotr = run_piotroski(ticker)
        row["f_score"] = piotr.get("f_score")
        comps = piotr.get("components", {})
        for i in range(1, 10):
            row[f"f{i}"] = comps.get(f"F{i}")

        # Coffee Can
        cc = run_coffee_can(ticker)
        row["cc_qualifies"] = 1 if cc.get("qualifies") else 0
        row["cc_score"] = cc.get("score")
        crit = cc.get("criteria", {})
        for i in range(1, 7):
            row[f"cc_c{i}"] = crit.get(f"C{i}")
        row["cc_roe_avg"] = cc.get("roe_avg")

        if piotr.get("error") and cc.get("error"):
            row["error"] = piotr["error"]

    except Exception as e:
        row["error"] = str(e)[:200]
        log.debug("Error scanning %s: %s", symbol, e)

    return row


# ═══════════════════════════════════════════════════════════════════════════════
# RESULTS COMPILATION
# ═══════════════════════════════════════════════════════════════════════════════


def compile_outputs(con: sqlite3.Connection, today: str):
    """
    Read the SQLite results table and write focused CSV output files.

    Files written:
      scan_summary_YYYYMMDD.csv    — every stock, all columns
      darvas_breakouts_YYYYMMDD.csv — BREAKOUT_BUY only
      strong_piotroski_YYYYMMDD.csv — F-Score >= 7
      coffee_can_YYYYMMDD.csv       — Coffee Can qualifiers
      triple_hits_YYYYMMDD.csv      — all 3 scans pass (highest conviction)

    Triple-hit definition:
      Darvas = BREAKOUT_BUY  →  momentum confirmed
      Piotroski >= 7         →  financially strong
      Coffee Can qualifies   →  quality, growing, cash-generative
    """
    df = pd.read_sql("SELECT * FROM results", con)
    if df.empty:
        print("  No results in database yet.")
        return

    # ── Summary ───────────────────────────────────────────────────────────────
    out_summary = OUTPUT_DIR / f"scan_summary_{today}.csv"
    df.to_csv(out_summary, index=False)
    print(f"  Summary ({len(df):,} rows)       → {out_summary}")

    # ── Darvas breakouts ──────────────────────────────────────────────────────
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

    # ── Strong Piotroski ──────────────────────────────────────────────────────
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

    # ── Coffee Can qualifiers ─────────────────────────────────────────────────
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

    # ── Triple hits ───────────────────────────────────────────────────────────
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


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════


def main(
    exchange: str = "all",
    limit: int = 0,
    resume: bool = False,
    darvas_only: bool = False,
    batch_size: int = 500,
    workers: int = 10,
    delay: float = 0.05,  # seconds between fundamentals calls per worker
):
    """
    Full pipeline:
      1. Fetch stock universe from NASDAQ Trader
      2. Phase 1 — Darvas batch OHLC download
      3. Phase 2 — Fundamentals per-stock (Piotroski + Coffee Can)
      4. Compile output CSVs

    Args:
        exchange:    'all' | 'nasdaq' | 'nyse' | 'amex'
        limit:       process only the first N stocks (0 = all)
        resume:      skip symbols already in the SQLite checkpoint
        darvas_only: skip Phase 2 (fundamentals) — much faster
        batch_size:  tickers per yf.download() call
        workers:     ThreadPoolExecutor workers for fundamentals
        delay:       sleep between fundamentals calls to respect rate limits
    """
    today = datetime.today().strftime("%Y%m%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    con = init_db(DB_FILE)

    # ── Step 1: Stock universe ─────────────────────────────────────────────────
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
        print(f"  Limited to {len(universe):,} stocks (--limit {limit})")

    symbols = universe["Symbol"].tolist()
    name_map = dict(zip(universe["Symbol"], universe["Name"]))
    exch_map = dict(zip(universe["Symbol"], universe["Exchange"]))

    # ── Step 2: Darvas Box (batch OHLC) ───────────────────────────────────────
    print("\n── Phase 1: Darvas Box (batch OHLC download) ─────────────────────────")

    done_darvas = already_done(con, "darvas") if resume else set()
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

    # Write Darvas results to SQLite
    for sym, dr in darvas_results.items():
        upsert_result(
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
                "scanned_at": datetime.now().isoformat(),
            },
        )
        mark_done(con, sym, "darvas")

    print(
        f"  Phase 1 done.  "
        f"Breakouts: {sum(1 for v in darvas_results.values() if v.get('signal')=='BREAKOUT_BUY'):,}  "
        f"In-box: {sum(1 for v in darvas_results.values() if v.get('signal')=='IN_BOX'):,}  "
        f"Breakdown: {sum(1 for v in darvas_results.values() if v.get('signal')=='BREAKDOWN_SELL'):,}"
    )

    if darvas_only:
        print("\n  --darvas-only set. Skipping fundamentals.")
        print("\n── Results ───────────────────────────────────────────────────────────")
        compile_outputs(con, today)
        con.close()
        return

    # ── Step 3: Piotroski + Coffee Can (threaded) ─────────────────────────────
    print(f"\n── Phase 2: Piotroski + Coffee Can ({workers} workers) ──────────────────")
    print(
        f"  Estimated time: {len(symbols) * 4 / workers / 60:.0f}–"
        f"{len(symbols) * 6 / workers / 60:.0f} minutes "
        f"(depends on Yahoo Finance latency)"
    )

    done_fund = already_done(con, "fundamentals") if resume else set()
    todo_fund = [s for s in symbols if s not in done_fund]
    print(f"  Symbols to scan: {len(todo_fund):,}  " f"(skipping {len(done_fund):,} already done)")

    pbar_f = (
        tqdm(total=len(todo_fund), unit="stocks", desc="Fundamentals", ncols=80)
        if TQDM_OK
        else None
    )

    def _worker(sym):
        result = scan_fundamentals_one(sym, name_map.get(sym, ""), exch_map.get(sym, ""))
        time.sleep(delay)  # gentle rate-limit spacing per worker
        return sym, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, sym): sym for sym in todo_fund}
        for future in as_completed(futures):
            sym, row = future.result()
            upsert_result(con, row)
            mark_done(con, sym, "fundamentals")
            if pbar_f:
                pbar_f.update(1)

    if pbar_f:
        pbar_f.close()

    # ── Step 4: Compile outputs ────────────────────────────────────────────────
    print("\n── Results ───────────────────────────────────────────────────────────")
    compile_outputs(con, today)
    con.close()
    print(f"\n  Database: {DB_FILE}")
    print(f"  Output:   {OUTPUT_DIR}/\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full NASDAQ + NYSE equity screener: Darvas, Piotroski, Coffee Can."
    )
    parser.add_argument(
        "--exchange",
        default="all",
        choices=["all", "nasdaq", "nyse", "amex"],
        help="Exchange filter (default: all)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Process only first N stocks (0 = all, default)"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Skip symbols already in screener.db checkpoint"
    )
    parser.add_argument(
        "--darvas-only", action="store_true", help="Run Darvas phase only (fast, ~5 min)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Tickers per yf.download() batch (default 500)"
    )
    parser.add_argument(
        "--workers", type=int, default=10, help="Threads for fundamentals phase (default 10)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Per-worker sleep between API calls in seconds (default 0.05)",
    )

    args = parser.parse_args()
    main(
        exchange=args.exchange,
        limit=args.limit,
        resume=args.resume,
        darvas_only=args.darvas_only,
        batch_size=args.batch_size,
        workers=args.workers,
        delay=args.delay,
    )
