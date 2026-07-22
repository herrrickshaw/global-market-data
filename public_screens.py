#!/usr/bin/env python3
# public_screens.py
# =================
# Fetch the fundamental Screener.in POPULAR screens from their public /screens/<id>/
# URLs (no login) and cache the resulting symbol lists, so validation.py can use
# real public-domain fundamental results alongside the locally-computed price
# screens — offline once cached.
#
# Design is cache-first:
#   python3 public_screens.py --fetch          # pull all mapped public screens → cache
#   python3 public_screens.py --fetch --only piotroski_9 magic_formula
#   → writes cache_seed/public_screens/<key>.parquet (Symbol + key columns)
# validation.py then reads those parquets with NO network.
#
# URL confidence: CCC is verified live; Bull-Cartel/Magic-Formula are Screener.in's
# long-standing official curated screens. Others are best-known public IDs — the
# fetcher validates each returns a non-empty table and SKIPS anything that fails,
# and you can override/add any URL in public_screens.json (same keys).
#
# ⚠️ Respect Screener.in ToS + rate limits (the fetcher paginates politely). If your
# IP is temporarily blocked, rerun later. Research/education only.

from __future__ import annotations

import argparse
import json
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

warnings.filterwarnings("ignore")

import screener_in as si

CACHE = Path(__file__).parent / "cache_seed" / "public_screens"
CACHE.mkdir(parents=True, exist_ok=True)
BASE = "https://www.screener.in/screens"

# fundamental popular-screen key -> public screen URL. Keys match
# validation.FUNDAMENTAL_SCREENS. Edit / extend via public_screens.json.
# Verified public IDs, discovered from Screener.in's official /explore/ + /screens/
# curated lists (the "most-used" screens). All public, no login.
PUBLIC_URLS: Dict[str, str] = {
    "bull_cartel":               f"{BASE}/1/the-bull-cartel/",
    "piotroski_9":               f"{BASE}/2/piotroski-scan/",
    "highest_dividend_yield":    f"{BASE}/3/highest-dividend-yield-shares/",
    "high_growth_high_roe_low_pe": f"{BASE}/18/high-growth-high-roe-low-pe/",  # GARP
    "loss_to_profit":            f"{BASE}/49/loss-to-profit-companies/",
    "magic_formula":             f"{BASE}/59/magic-formula/",
    "quarterly_growers":         f"{BASE}/86/quarterly-growers/",
    "bluest_blue_chips":         f"{BASE}/234/bluest-of-the-blue-chips/",
    "value_stocks":              f"{BASE}/184/value-stocks/",
    "growth_stocks":             f"{BASE}/178/growth-stocks/",
    "graham_buffett":            f"{BASE}/15310/benjamin-graham-and-warren-buffett/",
    "best_of_latest_quarter":    f"{BASE}/50359/best-of-latest-quarter/",
    "coffee_can":                f"{BASE}/57601/coffee-can-portfolio/",
    "multibagger":               f"{BASE}/60880/multibagger-stocks/",
    "capacity_expansion":        f"{BASE}/97687/capacity-expansion/",
    "debt_reduction":            f"{BASE}/126864/debt-reduction/",
    "companies_creating_new_high": f"{BASE}/214283/companies-creating-new-high/",
    "growth_without_dilution":   f"{BASE}/226712/growth-without-dilution/",
    "cash_conversion_cycle":     f"{BASE}/228040/cash-conversion-cycle/",
    "fii_buying":                f"{BASE}/343087/fii-buying/",
    "fcf_yield":                 f"{BASE}/5772/fcf-yield/",
    "graham_10y_earnings":       f"{BASE}/6994/low-on-10-year-average-earnings/",
    "book_value_over_5x_price":  f"{BASE}/276307/book-value-over-5-times-price/",
    "high_mv_investments":       f"{BASE}/171936/high-ratio-of-market-value-of-investments/",
    "breakout_stocks":           f"{BASE}/209239/breakout-stocks/",
    "top_100_stocks":            f"{BASE}/885655/top-100-stocks/",
}


def _load_overrides() -> Dict[str, str]:
    cfg = dict(PUBLIC_URLS)
    f = Path(__file__).parent / "public_screens.json"
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text()))
        except Exception:
            pass
    return cfg


def fetch(only: Optional[List[str]] = None, pause: float = 1.5, verbose: bool = True) -> dict:
    """Fetch mapped public screens → cache. Returns {key: n_symbols}. Skips failures."""
    urls = _load_overrides()
    keys = only or list(urls)
    got: Dict[str, int] = {}
    for k in keys:
        url = urls.get(k)
        if not url:
            continue
        try:
            df = si.fetch_screen(url, verbose=False)
            if df.empty or "Symbol" not in df.columns:
                if verbose:
                    print(f"  {k}: empty/invalid — skipped")
                continue
            df.to_parquet(CACHE / f"{k}.parquet", index=False)
            got[k] = len(df)
            if verbose:
                print(f"  {k}: {len(df)} symbols cached")
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  {k}: fetch failed ({str(e)[:50]})")
        time.sleep(pause)
    return got


# ── offline reader (used by validation) ──────────────────────────────────────────
def cached_screens() -> Dict[str, set]:
    """{key: set(symbols)} from the cached public-screen parquets — no network."""
    out: Dict[str, set] = {}
    for p in sorted(CACHE.glob("*.parquet")):
        try:
            df = pd.read_parquet(p, columns=["Symbol"])
            out[p.stem] = set(df["Symbol"].astype(str))
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch public Screener.in fundamental screens")
    ap.add_argument("--fetch", action="store_true", help="pull mapped public screens into cache")
    ap.add_argument("--only", nargs="*", help="limit to these screen keys")
    ap.add_argument("--list", action="store_true", help="show cached screens")
    args = ap.parse_args()

    if args.list or not args.fetch:
        cached = cached_screens()
        print(f"cached public screens: {len(cached)}")
        for k, s in sorted(cached.items(), key=lambda kv: -len(kv[1])):
            print(f"  {k:26} {len(s):>5} symbols")
        if not args.fetch:
            return 0
    res = fetch(only=args.only)
    print(f"\nfetched {len(res)} screens; total mapped: {len(_load_overrides())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
