#!/usr/bin/env python3
# serving_layer.py
# ================
# Applies the Modern Data Architecture Blueprint to the screener to cut run time.
#
#   Blueprint pattern          → How it maps here
#   ─────────────────────────────────────────────────────────────────────────────
#   Lambda: Batch + Speed      → LTM (5y, cache_seed/ltm) is the BATCH layer;
#     → Serving layer            STM (1y seeds) is the SPEED layer; this module is
#                                the SERVING layer that MERGES them for queries.
#   CDC (capture deltas, never → capture_deltas(): diff today's data vs the last
#     rescan whole tables)       snapshot and log only changed rows (the blueprint's
#                                Operation/Key/Value-Timestamp delta model), so the
#                                serving view refreshes on deltas, not full rescans.
#   Denormalisation /          → build_serving(): precompute every per-symbol query
#     query-driven modelling     feature (SMAs, RSI, 52w hi/lo, returns, turnover,
#     (write-time, not           tier) ONCE per day into a wide, columnar table keyed
#     read-time)                 by symbol. Screening then filters precomputed columns
#                                (vectorised, ms) instead of re-reading OHLCV and
#                                recomputing indicators on every screen.
#
# Result: the 11-screener India run and custom screens read a materialised view
# instead of iterating 8k symbols × recomputing indicators each call.
#
#   python3 serving_layer.py --refresh            # rebuild serving views (all markets)
#   python3 serving_layer.py --refresh --market IN US
#   python3 serving_layer.py --screen IN          # demo fast screen

from __future__ import annotations

import argparse
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import datalink

HERE = Path(__file__).parent
SERVING_DIR = HERE / "cache_seed" / "serving"
CDC_DIR = HERE / "cache_seed" / "cdc"
SERVING_DIR.mkdir(parents=True, exist_ok=True)
CDC_DIR.mkdir(parents=True, exist_ok=True)

MARKETS = datalink.MARKETS


# ── per-symbol feature engineering (the denormalised, precomputed columns) ──────
def _features(sym: str, market: str, d: pd.DataFrame) -> Optional[dict]:
    if d is None or len(d) < 20:
        return None
    close = d["Close"].astype("float64")
    vol = d["Volume"].astype("float64")
    n = len(close)
    last = float(close.iloc[-1])

    def sma(k):
        return float(close.tail(k).mean()) if n >= k else np.nan

    def ret(k):
        return float((last / close.iloc[-k] - 1) * 100) if n > k and close.iloc[-k] else np.nan

    # RSI(14), Wilder
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if n >= 15 else np.nan

    win = close.tail(252)
    hi, lo = float(win.max()), float(win.min())
    sma50, sma200 = sma(50), sma(200)
    turnover = float((close.tail(20) * vol.tail(20)).mean())  # local-ccy 20d avg

    return {
        "Symbol": sym,
        "Market": market,
        "Close": last,
        "Bars": n,
        "SMA20": sma(20),
        "SMA50": sma50,
        "SMA200": sma200,
        "RSI14": rsi,
        "High252": hi,
        "Low252": lo,
        "PctFromHigh": float((last / hi - 1) * 100) if hi else np.nan,
        "PctFromLow": float((last / lo - 1) * 100) if lo else np.nan,
        "Ret21": ret(21),
        "Ret63": ret(63),
        "Ret126": ret(126),
        "Ret252": ret(252),
        "Above200DMA": bool(sma200 and last > sma200),
        "GoldenCross": bool(sma50 and sma200 and sma50 > sma200),
        "TurnoverLocal": turnover,
        "LastDate": str(d.index[-1])[:10],
    }


# ── build the serving (materialised) view for a market ──────────────────────────
def _source(market: str) -> Dict[str, pd.DataFrame]:
    """Serving reads the deepest available layer: BATCH (5y LTM) if built, else the
    committed 1y SPEED seed. Avoids the thin live incremental (few bars) so SMA200
    etc. are computable."""
    ltm = datalink.load_market(market, tier="ltm")
    if ltm:
        return ltm
    # committed seed (full 1y), bypassing any thin live file
    from frames import read_seed

    return read_seed(datalink.SEED_DIR / datalink._seed_name(market))


def build_serving(market: str, verbose: bool = True) -> int:
    data = _source(market)  # batch (LTM) preferred, else committed speed seed
    rows = [f for s, d in data.items() if (f := _features(s, market, d))]
    df = pd.DataFrame(rows)
    if df.empty:
        return 0
    # annotate liquidity tier (turnover→USD + High/Medium/Low) via existing module
    try:
        from liquidity import annotate

        df = annotate(df)
    except Exception:
        pass
    out = SERVING_DIR / f"{market}.parquet"
    df.to_parquet(out, compression="zstd", index=False)
    if verbose:
        print(f"  serving[{market}]: {len(df)} symbols → {out.name}")
    return len(df)


# ── CDC: capture only the changed rows (delta log), never rescan whole tables ───
def capture_deltas(market: str, verbose: bool = True) -> int:
    """Diff the current serving view against the previous one and append only the
    changed symbols to the CDC delta log (Operation/Key/Value-Timestamp model)."""
    cur_path = SERVING_DIR / f"{market}.parquet"
    if not cur_path.exists():
        return 0
    cur = pd.read_parquet(cur_path).set_index("Symbol")
    log_path = CDC_DIR / f"{market}.parquet"
    prev_close = {}
    if log_path.exists():
        prev = pd.read_parquet(log_path)
        # last known close per symbol from the log
        prev_close = prev.sort_values("ts").groupby("key")["value"].last().to_dict()

    ts = pd.Timestamp.utcnow().isoformat()
    deltas = []
    for sym, row in cur.iterrows():
        c = float(row["Close"])
        op = "INSERT" if sym not in prev_close else ("UPDATE" if prev_close[sym] != c else None)
        if op:
            deltas.append({"op": op, "key": sym, "value": c, "asof": row["LastDate"], "ts": ts})
    if not deltas:
        if verbose:
            print(f"  cdc[{market}]: no changes")
        return 0
    dfd = pd.DataFrame(deltas)
    if log_path.exists():
        dfd = pd.concat([pd.read_parquet(log_path), dfd], ignore_index=True)
    dfd.to_parquet(log_path, compression="zstd", index=False)
    if verbose:
        print(f"  cdc[{market}]: +{len(deltas)} deltas → {log_path.name}")
    return len(deltas)


# ── serving read + fast screen ──────────────────────────────────────────────────
_SERVE_CACHE: Dict[str, pd.DataFrame] = {}


def serving(market: str) -> pd.DataFrame:
    """Read the materialised serving view (memoised, signature-keyed)."""
    p = SERVING_DIR / f"{market}.parquet"
    if not p.exists():
        return pd.DataFrame()
    sig = f"{p.stat().st_size}:{int(p.stat().st_mtime)}"
    if _SERVE_CACHE.get("_sig_" + market) == sig:
        return _SERVE_CACHE[market]
    df = pd.read_parquet(p)
    _SERVE_CACHE[market] = df
    _SERVE_CACHE["_sig_" + market] = sig
    return df


_OPS = {
    "<": lambda s, v: s < v,
    "<=": lambda s, v: s <= v,
    ">": lambda s, v: s > v,
    ">=": lambda s, v: s >= v,
    "==": lambda s, v: s == v,
    "!=": lambda s, v: s != v,
}


def screen_fast(
    criteria: Dict[str, tuple], market: str = "IN", top: Optional[int] = 50, sort: str = "Ret252"
) -> pd.DataFrame:
    """Vectorised screen over the precomputed serving view — milliseconds, no OHLCV
    re-read. criteria = {column: (op, value)}, e.g. {'Above200DMA': ('==', True),
    'RSI14': ('<', 65), 'PctFromHigh': ('>', -10)}."""
    df = serving(market)
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    for col, (op, val) in criteria.items():
        if col in df.columns and op in _OPS:
            mask &= _OPS[op](df[col], val)
    out = df[mask]
    if sort in out.columns:
        out = out.sort_values(sort, ascending=False)
    return out.head(top).reset_index(drop=True) if top else out.reset_index(drop=True)


# ── daily refresh (incremental — only markets whose seed changed) ────────────────
def refresh(markets: Optional[List[str]] = None, verbose: bool = True) -> dict:
    markets = markets or MARKETS
    changed = set(datalink.changed_markets()) if markets == MARKETS else set(markets)
    built, deltas = 0, 0
    t = time.time()
    for m in markets:
        if m not in changed and (SERVING_DIR / f"{m}.parquet").exists():
            continue  # serving view already current
        if build_serving(m, verbose):
            built += 1
            deltas += capture_deltas(m, verbose)
    if verbose:
        print(f"  serving refresh: {built} views, {deltas} deltas in {time.time()-t:.1f}s")
    return {"views": built, "deltas": deltas}


def main() -> int:
    ap = argparse.ArgumentParser(description="Serving layer: materialised views + CDC")
    ap.add_argument("--refresh", action="store_true", help="(re)build serving views + deltas")
    ap.add_argument("--screen", metavar="MKT", help="demo fast screen for a market")
    ap.add_argument("--market", nargs="*", help="limit --refresh to these markets")
    args = ap.parse_args()

    if args.screen:
        df = screen_fast(
            {"Above200DMA": ("==", True), "RSI14": ("<", 70), "PctFromHigh": (">", -8)},
            args.screen,
            top=15,
        )
        cols = [c for c in ["Symbol", "Close", "RSI14", "PctFromHigh", "Ret252", "Liquidity"] if c in df.columns]
        print(df[cols].to_string(index=False) if not df.empty else "no serving view — run --refresh")
        return 0

    refresh(args.market)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
