#!/usr/bin/env python3
# validation.py
# =============
# Validation layer: ground the auto-screener's discoveries and the supervised
# anchor against Screener.in's MOST-USED screens (the curated "popular" list +
# the widely-cloned community screens).
#
# WHY: a discovered screen is only trustworthy if it overlaps the patterns the
# market already validates (the popular screens) — while still surfacing something
# new. This module computes the popular screens LOCALLY where they're expressible
# from our precomputed serving features (price/technical/liquidity), registers the
# fundamental-only ones for optional live screener.in fetch, and reports:
#
#   • known_universe(market)  — union of all locally-computable popular screens,
#                               a richer supervised anchor for auto_screener.
#   • validate(picks, market) — grounded % (picks appearing in ≥1 popular screen),
#                               novelty % (picks in none), + per-screen overlap.
#   • report(market)          — counts per popular screen + validation of today's
#                               auto-screener recommendation.
#
#   python3 validation.py --market IN
#
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

import argparse
import warnings
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import serving_layer as sl

LIQ_GOOD = {"High", "Medium"}


# ── locally-computable popular screens (from serving features) ──────────────────
# Each maps a Screener.in popular/community screen to a predicate over the serving
# view. Fundamental-only screens are registered separately (FUNDAMENTAL_SCREENS).
def _P(df):  # convenience
    return df


LOCAL_SCREENS: Dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    # curated "popular" list -----------------------------------------------------
    "golden_crossover":        lambda d: d["GoldenCross"],                       # 50DMA>200DMA
    "bearish_crossover":       lambda d: ~d["GoldenCross"],                      # 50DMA<200DMA
    "companies_creating_new_high": lambda d: d["PctFromHigh"] >= -1,             # at/near 52w high
    "darvas_scan":             lambda d: (d["PctFromHigh"] >= -3) & d["Above200DMA"],
    "rsi_oversold":            lambda d: d["RSI14"] < 30,
    "stocks_near_200dma":      lambda d: (d["Close"] / d["SMA200"] - 1).abs() <= 0.03,
    # community / technical ------------------------------------------------------
    "52w_high_breakout":       lambda d: d["PctFromHigh"] >= 0,
    "all_time_high":           lambda d: d["PctFromHigh"] >= 0,                  # 52w proxy
    "52w_low_contrarian":      lambda d: d["PctFromLow"] <= 5,
    "midcap_momentum":         lambda d: (d["Ret126"] > 20) & d["Above200DMA"],
    "value_plus_momentum":     lambda d: (d["Ret126"] > 15) & (d["PctFromHigh"] > -10),
    "quality_plus_momentum":   lambda d: d["Above200DMA"] & d["GoldenCross"] & (d["Ret252"] > 15),
    "multibagger_momentum":    lambda d: d["Ret252"] > 100,
    "steady_uptrend":          lambda d: d["Above200DMA"] & (d["RSI14"].between(45, 70)),
}

# fundamental-only popular screens — need Screener.in / fundamentals (registered
# for optional live fetch; not computed from the local price cache).
FUNDAMENTAL_SCREENS: Dict[str, str] = {
    "bull_cartel": "good quarterly growth",
    "fii_buying": "FII net buying",
    "magic_formula": "Greenblatt ROCE + earnings yield",
    "growth_stocks_gfactor": "G Factor growth",
    "highest_dividend_yield": "top dividend yield",
    "piotroski_9": "Piotroski score 9",
    "coffee_can": "Saurabh Mukherjea coffee can",
    "graham_low_10y_earnings": "Graham 10y avg earnings",
    "capacity_expansion": "capex / capacity expansion",
    "debt_reduction": "falling debt",
    "growth_without_dilution": "growth, flat equity",
    "loss_to_profit": "turnaround YoY",
    "fcf_yield": "free-cash-flow yield",
    "high_roce": "ROCE > 20-25%",
    "high_roe": "ROE high",
    "consistent_compounders": "ROCE + sales growth + low debt",
    "debt_free": "debt-free",
    "peg_below_1": "PEG < 1",
    "graham_net_net": "net-net bargains",
    "high_promoter_holding": "promoter holding rising",
    "reducing_pledge": "pledge falling",
    "dividend_growth": "growing dividend",
    "margin_expansion": "OPM improving QoQ",
    "sales_growth_20": "3y sales CAGR > 20%",
    "profit_growth_25": "3y profit CAGR > 25%",
    "altman_z": "bankruptcy-risk (Z-score)",
    "cash_bargains": "net cash > market cap",
    "low_debt_equity": "D/E < 0.3",
    "high_fcf": "high free cash flow",
    "buyback_candidates": "buyback",
    # sector/theme screens
    "banking": "NIM/GNPA/CASA", "nbfc": "NIM/ROA/GNPA", "it_services": "margin/growth",
    "pharma": "R&D/margin/USFDA", "fmcg_compounders": "quality FMCG",
    "specialty_chemicals": "chemicals growth", "auto_components": "auto recovery",
    "capital_goods_defence": "order book", "green_energy": "renewables",
    "ev_supply_chain": "EV theme", "textiles_pli": "PLI beneficiaries",
    "psu_undervalued": "cheap PSU", "metals_cement_cyclical": "cyclical bottom",
}


# ── local evaluation ────────────────────────────────────────────────────────────
def _public_fundamental(market: str) -> Dict[str, set]:
    """Cached public-domain Screener.in fundamental screens (India only; populated
    by `python3 public_screens.py --fetch`). Empty until fetched → validation still
    runs on the local price screens."""
    if market != "IN":
        return {}
    try:
        from public_screens import cached_screens

        return {f"pub::{k}": v for k, v in cached_screens().items()}
    except Exception:
        return {}


def local_screens(market: str) -> Dict[str, set]:
    """Every popular screen we can evaluate: price/technical/liquidity computed from
    the serving view + cached public-domain fundamental screens (screener.in)."""
    df = sl.serving(market)
    out: Dict[str, set] = {}
    if not df.empty:
        if "Liquidity" in df:
            df = df[df["Liquidity"].isin(LIQ_GOOD)]
        for name, pred in LOCAL_SCREENS.items():
            try:
                m = pred(df).fillna(False)
                out[name] = set(df.loc[m, "Symbol"])
            except Exception:
                out[name] = set()
    out.update(_public_fundamental(market))
    return out


def known_universe(market: str) -> set:
    """Union of all locally-computable popular screens — a broad validated anchor."""
    return set().union(*local_screens(market).values()) if local_screens(market) else set()


# ── validation of a pick list / discovered screen ────────────────────────────────
def validate(picks: List[str], market: str) -> dict:
    scr = local_screens(market)
    if not picks:
        return {"n": 0, "grounded_pct": 0.0, "novel_pct": 0.0, "per_screen": {}}
    universe = set().union(*scr.values()) if scr else set()
    picks_set = set(picks)
    grounded = picks_set & universe
    per_screen = {
        name: round(len(picks_set & syms) / len(picks_set), 3)
        for name, syms in scr.items()
        if picks_set & syms
    }
    per_screen = dict(sorted(per_screen.items(), key=lambda kv: -kv[1]))
    return {
        "n": len(picks_set),
        "grounded_pct": round(len(grounded) / len(picks_set), 3),   # in ≥1 popular screen
        "novel_pct": round(1 - len(grounded) / len(picks_set), 3),  # in none (new pattern)
        "per_screen": per_screen,
    }


def report(market: str, verbose: bool = True) -> dict:
    scr = local_screens(market)
    rec, val = None, None
    try:
        from auto_screener import recommend

        rec = recommend(market, verbose=False)
        val = validate(rec["picks"], market)
    except Exception as e:  # noqa: BLE001
        val = {"error": str(e)}

    if verbose:
        print(f"\n=== validation vs Screener.in popular screens — {market} ===")
        print(f"  local popular screens computed: {len(scr)} "
              f"(+{len(FUNDAMENTAL_SCREENS)} fundamental registered for live fetch)")
        for name, syms in sorted(scr.items(), key=lambda kv: -len(kv[1])):
            print(f"    {name:26} {len(syms):>5} names")
        if rec is not None and "error" not in val:
            print(f"\n  auto-screener recommendation ({len(rec['picks'])} picks):")
            print(f"    grounded in popular screens: {val['grounded_pct']:.0%}"
                  f"   novel (new pattern): {val['novel_pct']:.0%}")
            if val["per_screen"]:
                top = list(val["per_screen"].items())[:6]
                print("    overlaps: " + ", ".join(f"{k} {v:.0%}" for k, v in top))
    return {"local_screens": {k: len(v) for k, v in scr.items()}, "validation": val}


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate discoveries vs Screener.in popular screens")
    ap.add_argument("--market", default="IN")
    args = ap.parse_args()
    report(args.market)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
