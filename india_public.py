#!/usr/bin/env python3
# india_public.py
# ===============
# Run the 11 screeners for India using ONLY publicly available data — no login:
#   • price screeners (darvas, golden_crossover) computed live from official
#     NSE+BSE bhavcopy (already cached);
#   • fundamental screeners sourced from PUBLIC screener.in screens (each is a
#     public /screens/<id>/ URL that anyone can open) via screener_in.fetch_screen.
#
# Add/adjust public screens in PUBLIC_SCREENS (or a public_screens.json next to
# this file). Only the CCC screen is verified here; drop in the public screen URLs
# you trust for the others — the runner fetches whatever's configured and silently
# skips any that 404 / return nothing, so wrong/empty entries never corrupt output.
#
#   python3 india_public.py            # prints per-screener results for today
#
# Educational/research only. NOT investment advice.

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

import liquidity as liq
import screener_kit as kit
import screener_in as si

# strategy slug -> public screener.in screen URL (no login needed to open these).
PUBLIC_SCREENS = {
    "cash_conversion_cycle": "https://www.screener.in/screens/228040/cash-conversion-cycle/",
    # add the public screens you trust, e.g.:
    # "magic_formula":  "https://www.screener.in/screens/59/magic-formula/",
    # "piotroski":      "https://www.screener.in/screens/<id>/piotroski/",
    # "coffee_can":     "https://www.screener.in/screens/<id>/coffee-can/",
    # "dividend_yield": "https://www.screener.in/screens/<id>/high-dividend-yield/",
    # "debt_reduction": "https://www.screener.in/screens/<id>/debt-reduction/",
    # "garp":           "https://www.screener.in/screens/<id>/garp/",
    # "loss_to_profit": "https://www.screener.in/screens/<id>/turnaround/",
    # "bluest_blue_chips": "https://www.screener.in/screens/<id>/blue-chip/",
}
PRICE_SLUGS = ("darvas", "golden_crossover")


def _load_config() -> dict:
    """Merge PUBLIC_SCREENS with an optional public_screens.json override."""
    cfg = dict(PUBLIC_SCREENS)
    f = Path(__file__).parent / "public_screens.json"
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text()))
        except Exception:
            pass
    return cfg


def run(min_turnover: float = 1_000_000, top: int = 25, verbose: bool = True) -> dict:
    out: dict[str, pd.DataFrame] = {}

    # price screeners — from bhavcopy (public), no screener.in needed
    for slug in PRICE_SLUGS:
        try:
            out[slug] = kit.screen(slug, "IN", top=top, min_turnover_usd=min_turnover)
        except Exception as e:
            out[slug] = pd.DataFrame()
            if verbose:
                print(f"  {slug}: price-screen failed ({str(e)[:40]})")

    # fundamental screeners — from public screener.in screens
    for slug, url in _load_config().items():
        try:
            df = si.fetch_screen(url, verbose=False)
            if not df.empty:
                df = df.assign(Market="IN")
                df = liq.annotate(df)
            out[slug] = df
        except Exception as e:
            out[slug] = pd.DataFrame()
            if verbose:
                print(f"  {slug}: public-screen fetch failed ({str(e)[:40]})")

    if verbose:
        print("\n=== India — all screeners (public data) ===")
        for slug in ("darvas", "golden_crossover", *_load_config()):
            df = out.get(slug)
            n = 0 if df is None or df.empty else len(df)
            names = ", ".join(df["Symbol"].head(6)) if n else "—"
            print(f"  {slug:22} {n:>4}  {names}")
    return out


if __name__ == "__main__":
    run()
