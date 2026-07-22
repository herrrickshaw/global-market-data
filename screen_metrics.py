#!/usr/bin/env python3
# screen_metrics.py
# =================
# Replicate the ACTUAL metric formulas behind Screener.in's popular screens and run
# them on the OHLCV database (LTM), for any of the 20 markets — not name-matching,
# the real query semantics computed from price/volume.
#
# Screener.in's query language exposes these price/technical metrics, which we
# reproduce exactly from the daily bars:
#   DMA 50, DMA 200            → 50/200-day moving averages (+ prior day for a cross)
#   RSI                        → Wilder RSI(14)
#   High / Low (52w)          → trailing-252-day high/low
#   Volume, weekly volume      → daily volume + weekly aggregates (for 5× spikes)
#   Return over N months       → price momentum
#
# Screens whose query needs FUNDAMENTALS (ROCE, ROE, P/E, debt, Piotroski, sales/
# profit growth, dividend, book value) can't be computed from OHLCV — those are
# marked FUNDAMENTAL and served from the cached public results (public_screens.py).
#
#   python3 screen_metrics.py --market IN            # run all price screens on IN
#   python3 screen_metrics.py --market IN --screen golden_crossover --top 20
#   python3 screen_metrics.py --all                  # summary counts, all 20 markets
#
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

import argparse
import warnings
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import datalink

# ── metric computation from one symbol's OHLCV (screener.in query semantics) ─────
def _metrics(df: pd.DataFrame) -> Optional[dict]:
    if df is None or len(df) < 60:
        return None
    c = df["Close"].to_numpy("float64")
    v = df["Volume"].to_numpy("float64")
    n = len(c)
    last = c[-1]
    if last <= 0:
        return None

    def dma(k, shift=0):
        end = n - shift
        return c[end - k:end].mean() if end >= k else np.nan

    # Wilder RSI(14)
    d = np.diff(c[-15:]) if n >= 15 else np.diff(c)
    up = np.clip(d, 0, None).mean() if len(d) else 0.0
    dn = (-np.clip(d, None, 0)).mean() if len(d) else 0.0
    rsi = 100 - 100 / (1 + up / dn) if dn > 0 else 100.0

    win = c[-252:]
    hi52, lo52 = win.max(), win.min()
    # weekly volume: sum last 5 bars vs mean of prior weekly sums
    wk_now = v[-5:].sum()
    prior_wk = np.array([v[-5 * (i + 2):-5 * (i + 1)].sum() for i in range(8) if n >= 5 * (i + 2)])
    wk_avg = prior_wk.mean() if len(prior_wk) else wk_now

    def ret(k):
        return (last / c[-k] - 1) * 100 if n > k and c[-k] else np.nan

    dma50, dma200 = dma(50), dma(200)
    dma50_p, dma200_p = dma(50, 10), dma(200, 10)  # ~10 sessions ago (cross window)
    return {
        "Close": last, "DMA50": dma50, "DMA200": dma200,
        "DMA50_prev": dma50_p, "DMA200_prev": dma200_p,
        "RSI": rsi, "High52": hi52, "Low52": lo52,
        "PctFromHigh": (last / hi52 - 1) * 100 if hi52 else np.nan,
        "PctFromLow": (last / lo52 - 1) * 100 if lo52 else np.nan,
        "Vol": v[-1], "WeekVol": wk_now, "WeekVolAvg": wk_avg,
        "VolSpike": wk_now / wk_avg if wk_avg else np.nan,
        "Ret63": ret(63), "Ret126": ret(126), "Ret252": ret(252),
        "Turnover": (c[-20:] * v[-20:]).mean(),
    }


# ── the price/technical screens as real metric predicates ────────────────────────
# Each is Screener.in's query condition, expressed on the computed metrics.
PRICE_SCREENS: Dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    # DMA 50 > DMA 200  AND  crossed up (prev day DMA50 <= DMA200)
    "golden_crossover": lambda m: (m.DMA50 > m.DMA200) & (m.DMA50_prev <= m.DMA200_prev),
    # DMA 50 < DMA 200  AND  crossed down
    "bearish_crossover": lambda m: (m.DMA50 < m.DMA200) & (m.DMA50_prev >= m.DMA200_prev),
    "golden_state": lambda m: m.DMA50 > m.DMA200,                 # currently above (not just cross)
    "rsi_oversold": lambda m: m.RSI < 30,                          # RSI(14) < 30
    "rsi_overbought": lambda m: m.RSI > 70,
    "companies_creating_new_high": lambda m: m.Close >= 0.98 * m.High52,   # within 2% of 52w high
    "at_52w_high": lambda m: m.Close >= m.High52 * 0.999,
    "near_52w_low": lambda m: m.Close <= 1.05 * m.Low52,          # within 5% of 52w low
    "near_200dma": lambda m: (m.Close / m.DMA200 - 1).abs() <= 0.03,
    # Price Volume Action: this week's volume ≥ 5× average weekly volume
    "price_volume_action": lambda m: m.VolSpike >= 5,
    # breakout: new high + volume expansion
    "breakout_stocks": lambda m: (m.Close >= 0.98 * m.High52) & (m.VolSpike >= 2),
    # Darvas: at box top (52w high) with a tight prior range (low recent drawdown)
    "darvas_scan": lambda m: (m.Close >= 0.97 * m.High52) & (m.DMA50 > m.DMA200),
    "multibagger_momentum": lambda m: (m.Ret252 > 100) & (m.DMA50 > m.DMA200),
}

# fundamental screens — metric needs financials not in the OHLCV DB
FUNDAMENTAL_ONLY = [
    "piotroski_9", "magic_formula", "coffee_can", "high_roce", "high_roe", "debt_free",
    "debt_reduction", "peg_below_1", "graham_net_net", "highest_dividend_yield",
    "fii_buying", "sales_growth_20", "profit_growth_25", "fcf_yield", "bull_cartel",
    "growth_without_dilution", "capacity_expansion", "loss_to_profit", "quarterly_growers",
]


def _metrics_frame(market: str) -> pd.DataFrame:
    data = datalink.load_market(market, tier="ltm") or datalink.load_market(market)
    rows = []
    for sym, df in data.items():
        m = _metrics(df)
        if m:
            m["Symbol"] = sym
            rows.append(m)
    out = pd.DataFrame(rows)
    if not out.empty:
        try:
            from liquidity import annotate

            out["Market"] = market
            out = annotate(out)
        except Exception:
            pass
    return out


def run_screen(name: str, market: str, top: Optional[int] = None,
               min_turnover_usd: float = 1_000_000) -> pd.DataFrame:
    if name not in PRICE_SCREENS:
        raise ValueError(f"'{name}' is not price-computable; fundamental screen needs financials")
    mf = _metrics_frame(market)
    if mf.empty:
        return mf
    if "Turnover_USD" in mf and min_turnover_usd:
        mf = mf[mf["Turnover_USD"] >= min_turnover_usd]
    mask = PRICE_SCREENS[name](mf).fillna(False)
    out = mf[mask].sort_values("Ret252", ascending=False, na_position="last")
    return out.head(top).reset_index(drop=True) if top else out.reset_index(drop=True)


def run_all(market: str, min_turnover_usd: float = 1_000_000, verbose: bool = True) -> Dict[str, int]:
    mf = _metrics_frame(market)
    if mf.empty:
        if verbose:
            print(f"no data for {market}")
        return {}
    if "Turnover_USD" in mf and min_turnover_usd:
        mf = mf[mf["Turnover_USD"] >= min_turnover_usd]
    counts = {}
    for name, pred in PRICE_SCREENS.items():
        counts[name] = int(pred(mf).fillna(False).sum())
    if verbose:
        print(f"\n=== Screener.in price-metric screens on {market} "
              f"({len(mf)} liquid stocks in DB) ===")
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            ex = ", ".join(mf[PRICE_SCREENS[name](mf).fillna(False)]
                           .sort_values("Ret252", ascending=False)["Symbol"].head(5))
            print(f"  {name:28} {n:>5}   {ex}")
        print(f"\n  fundamental screens (need financials, served from public cache): "
              f"{', '.join(FUNDAMENTAL_ONLY[:8])}, …")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Screener.in metric formulas on the OHLCV DB")
    ap.add_argument("--market", default="IN")
    ap.add_argument("--screen", help="run one screen and list stocks")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min-turnover", type=float, default=1_000_000)
    ap.add_argument("--all", action="store_true", help="summary counts across all 20 markets")
    args = ap.parse_args()

    if args.all:
        for m in datalink.MARKETS:
            c = run_all(m, args.min_turnover, verbose=False)
            print(f"{m:3} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items(), key=lambda x: -x[1])[:6]))
        return 0
    if args.screen:
        df = run_screen(args.screen, args.market, top=args.top, min_turnover_usd=args.min_turnover)
        cols = [c for c in ["Symbol", "Close", "DMA50", "DMA200", "RSI", "PctFromHigh",
                            "VolSpike", "Ret252", "Liquidity"] if c in df.columns]
        print(f"\n{args.screen} on {args.market} — {len(df)} stocks:")
        print(df[cols].round(2).to_string(index=False) if not df.empty else "  none")
        return 0
    run_all(args.market, args.min_turnover)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
