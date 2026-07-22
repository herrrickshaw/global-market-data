#!/usr/bin/env python3
# ipo_monitor.py
# ==============
# Track NEW LISTINGS / IPOs across every market by diffing the current official
# universe against the last stored snapshot. Source-agnostic: whatever
# universe_sources returns (SEC, JPX, KRX, SGX, Eastmoney, bhavcopy) is compared
# to the previous run, and newly-appearing tickers are reported as new listings.
#
# This piggybacks on the official/government universe feeds, so IPO detection is
# as reliable as the exchange's own listed-issue master — no separate scraping.
#
#   python3 ipo_monitor.py                 # all markets, diff vs last snapshot
#   python3 ipo_monitor.py US CN --seed    # initialise snapshots without alerting
#
# Snapshots: data/bhavcopy_cache/universe_snapshots/<MKT>.json
# Report:    data/bhavcopy_cache/new_listings.json  (rolling log, dated)

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from universe_sources import PROVIDERS, get_universe

CACHE = Path(os.environ.get("BHAV_CACHE", Path.home() / "Downloads" / "data" / "bhavcopy_cache"))
SNAP_DIR = CACHE / "universe_snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)
LOG = CACHE / "new_listings.json"


def _load_snapshot(mkt: str) -> set:
    f = SNAP_DIR / f"{mkt}.json"
    if f.exists():
        try:
            return set(json.loads(f.read_text()).get("tickers", []))
        except Exception:
            return set()
    return set()


def _save_snapshot(mkt: str, tickers: set):
    (SNAP_DIR / f"{mkt}.json").write_text(
        json.dumps({"updated": _dt.date.today().isoformat(), "tickers": sorted(tickers)})
    )


def _append_log(entries: list):
    log = []
    if LOG.exists():
        try:
            log = json.loads(LOG.read_text())
        except Exception:
            log = []
    log.extend(entries)
    LOG.write_text(json.dumps(log, indent=2))


def check(markets, seed: bool = False, verbose: bool = True) -> dict:
    today = _dt.date.today().isoformat()
    found = {}
    log_entries = []
    for mkt in markets:
        try:
            current = set(get_universe(mkt))
        except Exception as e:
            if verbose:
                print(f"  {mkt}: universe fetch failed ({str(e)[:50]})")
            continue
        if not current:
            continue
        prev = _load_snapshot(mkt)
        if seed or not prev:
            _save_snapshot(mkt, current)
            if verbose:
                print(
                    f"  {mkt}: snapshot seeded ({len(current)} tickers)"
                    + ("" if not prev else " [reseeded]")
                )
            continue
        new = sorted(current - prev)
        delisted = sorted(prev - current)
        found[mkt] = {"new": new, "delisted": delisted}
        if new:
            log_entries.append({"date": today, "market": mkt, "new_listings": new})
        if verbose:
            print(
                f"  {mkt}: {len(new)} new listing(s), {len(delisted)} delisted "
                f"(universe {len(prev)} → {len(current)})"
            )
            for t in new[:15]:
                print(f"      + {t}")
        _save_snapshot(mkt, current)  # roll snapshot forward
    if log_entries:
        _append_log(log_entries)
        if verbose:
            print(
                f"\n  logged {sum(len(e['new_listings']) for e in log_entries)} "
                f"new listings → {LOG.name}"
            )
    return found


if __name__ == "__main__":
    seed = "--seed" in sys.argv
    mkts = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or list(PROVIDERS)
    print(f"IPO / new-listing monitor — {'SEED' if seed else 'DIFF'} — {mkts}")
    check(mkts, seed=seed)
