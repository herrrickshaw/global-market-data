#!/usr/bin/env python3
# run.py — one command to get results from the repo.
# ===================================================
# Colab / terminal users: after cloning, just run this. It bootstraps the cached
# data (committed seeds → fast store) on first use, then runs a screen and prints
# + saves the results. No tokens, no manual setup.
#
#   python run.py                                  # default: global momentum leaderboard
#   python run.py --strategy darvas --market IN    # a built-in strategy on a market
#   python run.py --market US --min-turnover 5e6   # liquid US momentum (custom)
#   python run.py --brief                          # build the full Daily Market Brief (HTML)
#   python run.py --list                           # list strategies & markets
#
# Output is printed and saved to results/<name>.csv (HTML for --brief).

from __future__ import annotations

import argparse
import os
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# default cache location (override with BHAV_CACHE). Colab → /content/cache.
os.environ.setdefault(
    "BHAV_CACHE",
    (
        "/content/cache"
        if Path("/content").exists()
        else str(Path.home() / "Downloads" / "data" / "bhavcopy_cache")
    ),
)

RESULTS = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)


def _ensure_ready(verbose=True):
    """Bootstrap the store from committed seeds if it isn't built yet."""
    import screener_kit as kit

    try:
        import bhavcopy_store as store

        if store.info().get("symbols", 0) > 0:
            return kit
    except Exception:
        pass
    if verbose:
        print("First run — bootstrapping cached data into the store …")
    kit.bootstrap(verbose=verbose)
    return kit


def main():
    ap = argparse.ArgumentParser(description="Multi-market stock screener — run & get results")
    ap.add_argument(
        "--market", default="IN", help="market code (IN US JP KR CN HK TW CA AU UK DE … )"
    )
    ap.add_argument("--strategy", help="built-in strategy slug (see --list)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument(
        "--min-turnover",
        type=float,
        default=1_000_000,
        help="liquidity pre-filter in USD/day (0 = all)",
    )
    ap.add_argument("--brief", action="store_true", help="build the full Daily Market Brief (HTML)")
    ap.add_argument(
        "--global",
        dest="glob",
        action="store_true",
        help="global momentum leaderboard across all markets",
    )
    ap.add_argument("--list", action="store_true", help="list strategies & markets, then exit")
    a = ap.parse_args()

    import strategies as st

    if a.list:
        print("Strategies:", ", ".join(st.STRATEGIES))
        import screener_kit as kit

        print("Markets:   ", ", ".join(kit.MARKETS))
        return

    kit = _ensure_ready()

    if a.brief:
        from build_mailer import build

        subject, _, html = build()
        out = RESULTS / "daily_brief.html"
        out.write_text(html)
        print(f"{subject}\nsaved → {out}")
        return

    if a.glob or not a.strategy:
        import run_global_analysis as rga

        res = rga.analyse(min_turnover_usd=a.min_turnover)
        g = res["global"]
        if not g.empty:
            out = RESULTS / "global_momentum.csv"
            g.to_csv(out, index=False)
            print(g.head(a.top).to_string(index=False))
            print(f"\nsaved → {out}")
        return

    # a built-in strategy on one market
    df = kit.screen(a.strategy, a.market, top=a.top, min_turnover_usd=a.min_turnover)
    if df.empty:
        print(f"No hits for {a.strategy} on {a.market}.")
        return
    out = RESULTS / f"{a.strategy}_{a.market}.csv"
    df.to_csv(out, index=False)
    print(df.to_string(index=False))
    print(f"\nsaved → {out}")
    print("\nEducational/research only. NOT investment advice.")


if __name__ == "__main__":
    main()
