#!/usr/bin/env python3
# backtest.py
# ===========
# F-12 — walk-forward backtest that quantifies a screen's historical EDGE. At each
# monthly as-of date it applies a price screen using ONLY data up to that date (no
# lookahead), holds the picks forward `horizon` days, and compares their forward
# return to the equal-weight market over the same window.
#
#   edge = mean(screen forward return) − mean(market forward return)
#   win rate = share of picks that beat the market over the window
#
#   python3 backtest.py --market IN --screen golden_state
#   python3 backtest.py --market IN --all         # rank every price screen by edge
#
# ⚠️ Research/education only. Past edge need not persist. Not advice.

from __future__ import annotations

import argparse
import warnings
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import datalink


# ── point-in-time features (data up to t only — no lookahead) ────────────────────
def _pit(close: np.ndarray, t: int) -> Optional[dict]:
    if t < 200:
        return None
    c = close[: t + 1]
    last = c[-1]
    if last <= 0:
        return None
    d = np.diff(c[-15:])
    up = np.clip(d, 0, None).mean() if len(d) else 0.0
    dn = (-np.clip(d, None, 0)).mean() if len(d) else 0.0
    rsi = 100 - 100 / (1 + up / dn) if dn > 0 else 100.0
    hi = c[-252:].max()
    return {
        "sma50": c[-50:].mean(),
        "sma200": c[-200:].mean(),
        "sma50_prev": c[-60:-10].mean(),
        "sma200_prev": c[-210:-10].mean(),
        "rsi": rsi,
        "pct_from_high": (last / hi - 1) * 100 if hi else 0.0,
        "ret126": (last / c[-126] - 1) * 100 if len(c) > 126 and c[-126] else 0.0,
    }


# price-screen predicates over the point-in-time feature dict
SCREENS: Dict[str, Callable[[dict], bool]] = {
    "golden_state": lambda f: f["sma50"] > f["sma200"],
    "golden_crossover": lambda f: f["sma50"] > f["sma200"] and f["sma50_prev"] <= f["sma200_prev"],
    "near_high": lambda f: f["pct_from_high"] >= -3,
    "rsi_oversold": lambda f: f["rsi"] < 30,
    "momentum": lambda f: f["ret126"] > 25 and f["sma50"] > f["sma200"],
}


# ── metrics (pure, testable) ─────────────────────────────────────────────────────
def edge_metrics(pick_rets: List[float], mkt_rets: List[float]) -> dict:
    """Aggregate forward returns of picks vs the market (paired per as-of window)."""
    if not pick_rets:
        return {"trades": 0, "avg_ret": 0.0, "mkt_ret": 0.0, "edge": 0.0, "win_rate": 0.0}
    pr, mr = np.array(pick_rets, float), np.array(mkt_rets, float)
    return {
        "trades": len(pr),
        "avg_ret": round(float(np.nanmean(pr)), 2),
        "mkt_ret": round(float(np.nanmean(mr)), 2),
        "edge": round(float(np.nanmean(pr - mr)), 2),
        "win_rate": round(float(np.nanmean(pr > mr)), 3),
    }


# ── the walk-forward loop ────────────────────────────────────────────────────────
def backtest_screen(
    market: str, screen: str, horizon: int = 21, step: int = 21, max_symbols: int = 0
) -> dict:
    if screen not in SCREENS:
        raise ValueError(f"unknown screen; choose from {list(SCREENS)}")
    pred = SCREENS[screen]
    data = datalink.load_market(market, tier="ltm") or datalink.load_market(market)
    syms = list(data)[:max_symbols] if max_symbols else list(data)
    pick_rets: List[float] = []
    mkt_rets: List[float] = []
    for sym in syms:
        df = data.get(sym)
        if df is None or len(df) < 200 + horizon + 1:
            continue
        close = df["Close"].to_numpy("float64")
        n = len(close)
        for t in range(200, n - horizon - 1, step):
            fwd = (close[t + horizon] / close[t] - 1) * 100 if close[t] else np.nan
            if not np.isfinite(fwd) or abs(fwd) > 500:
                continue  # drop Yahoo glitch bars (a 21d return >500% is a data error)
            mkt_rets.append(fwd)  # every eligible name = the equal-weight market sample
            f = _pit(close, t)
            if f and pred(f):
                pick_rets.append(fwd)
    m = edge_metrics(pick_rets, [np.nanmean(mkt_rets)] * len(pick_rets) if mkt_rets else [])
    m["market"], m["screen"], m["horizon"] = market, screen, horizon
    m["mkt_ret"] = round(float(np.nanmean(mkt_rets)), 2) if mkt_rets else 0.0
    m["edge"] = round(m["avg_ret"] - m["mkt_ret"], 2)
    return m


def run_all(market: str, horizon: int = 21, verbose: bool = True) -> pd.DataFrame:
    rows = [backtest_screen(market, s, horizon) for s in SCREENS]
    df = pd.DataFrame(rows).sort_values("edge", ascending=False)
    if verbose:
        print(f"\n=== Walk-forward backtest — {market} ({horizon}d hold) ===")
        print(
            df[["screen", "trades", "avg_ret", "mkt_ret", "edge", "win_rate"]].to_string(
                index=False
            )
        )
        print("\n  edge = screen avg forward return − market; win_rate = share beating market")
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest of price screens")
    ap.add_argument("--market", default="IN")
    ap.add_argument("--screen")
    ap.add_argument("--horizon", type=int, default=21)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all or not args.screen:
        run_all(args.market, args.horizon)
    else:
        m = backtest_screen(args.market, args.screen, args.horizon)
        print(
            f"{args.screen} on {args.market}: {m['trades']} trades, avg {m['avg_ret']}% "
            f"vs mkt {m['mkt_ret']}% → edge {m['edge']}%, win {m['win_rate']:.0%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
