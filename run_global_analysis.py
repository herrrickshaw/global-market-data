#!/usr/bin/env python3
# run_global_analysis.py
# ======================
# Run the screeners across every cached market and highlight the standout
# companies. Price/technical strategies (Darvas, Golden Cross) + a momentum
# custom screen work on OHLC alone, so they run uniformly across all markets;
# fundamental strategies additionally run wherever a fundamentals feed is present.
#
#   python3 run_global_analysis.py                 # all cached markets
#   python3 run_global_analysis.py IN US JP        # selected markets
#
# Output: prints per-market highlights + a global leaderboard, and writes
# global_highlights.xlsx (one sheet per market + a Global sheet).
#
# Educational/research only. NOT investment advice. Screener output is a
# mechanical filter, not a buy/sell signal.

from __future__ import annotations

import sys
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

import custom_screener as cs
import screener_kit as kit
from strategies.base import StockData

# "momentum" highlight: established uptrend, near 52-week high, strong 6-month
# return, not over-extended, and liquid enough to be tradable.
MOMENTUM = {
    "above_200dma": ("==", True),
    "dist_52w_high": ("<", 8),
    "ret_126": (">", 15),
    "rsi14": ("<", 75),
    "avg_vol_20": (">", 50000),
}
SHOW = ["ltp", "ret_126", "ret_252", "rsi14", "dist_52w_high"]


def analyse(
    markets=None, top_per_market: int = 5, verbose: bool = True, min_turnover_usd: float = 1_000_000
) -> dict:
    """min_turnover_usd>0 pre-filters to liquid names — far faster and surfaces
    only tradable stocks (default $1M/day)."""
    markets = markets or kit.markets()
    per_market, global_rows = {}, []
    if verbose:
        print("=" * 64)
        print("  GLOBAL MULTI-MARKET ANALYSIS — momentum + breakout highlights")
        print("=" * 64)
        print("  Educational/research only. NOT investment advice.\n")
    for m in markets:
        data = kit.load(m, min_turnover_usd)
        if not data:
            continue
        stocks = [StockData(s, m, ohlcv=d) for s, d in data.items()]
        darvas = kit.screen("darvas", m, min_turnover_usd=min_turnover_usd)
        gcross = kit.screen("golden_crossover", m, min_turnover_usd=min_turnover_usd)
        mom = cs.screen(stocks, MOMENTUM, rank_by="ret_126", top=top_per_market, show=SHOW)
        per_market[m] = mom
        if verbose:
            nb = 0 if darvas is None or darvas.empty else len(darvas)
            ng = 0 if gcross is None or gcross.empty else len(gcross)
            print(
                f"■ {m}: {len(stocks)} stocks | Darvas {nb} | GoldenCross {ng} | "
                f"momentum {0 if mom.empty else len(mom)}"
            )
            if not mom.empty:
                print("   " + mom.to_string(index=False).replace("\n", "\n   ") + "\n")
        if not mom.empty:
            for _, r in mom.iterrows():
                global_rows.append({"Market": m, **{k: r.get(k) for k in ["Symbol"] + SHOW}})

    g = pd.DataFrame(global_rows)
    if not g.empty:
        g = g.sort_values("ret_126", ascending=False).reset_index(drop=True)
    if verbose and not g.empty:
        print("=" * 64)
        print("  ⭐ GLOBAL TOP 20 MOMENTUM (across all markets)")
        print("=" * 64)
        print(g.head(20).to_string(index=False))

    with pd.ExcelWriter("global_highlights.xlsx", engine="openpyxl") as xw:
        if not g.empty:
            g.to_excel(xw, "Global", index=False)
        for m, df in per_market.items():
            if not df.empty:
                df.to_excel(xw, m, index=False)
    # df-referenceable artifact for later: tidy long parquet + dated stamp
    if not g.empty:
        g = g.copy()
        g["as_of"] = pd.Timestamp.today().normalize()
        g.to_parquet(HIGHLIGHTS_PARQUET, compression="zstd", index=False)
    if verbose:
        print(f"\nsaved → global_highlights.xlsx and {HIGHLIGHTS_PARQUET.name}")
    return {"per_market": per_market, "global": g}


HIGHLIGHTS_PARQUET = (
    __import__("pathlib").Path(__file__).parent / "cache_seed" / "global_highlights.parquet"
)


def load_highlights() -> pd.DataFrame:
    """Load the most recent global highlights as a DataFrame (for later reference)."""
    return pd.read_parquet(HIGHLIGHTS_PARQUET) if HIGHLIGHTS_PARQUET.exists() else pd.DataFrame()


if __name__ == "__main__":
    analyse([a.upper() for a in sys.argv[1:]] or None)
