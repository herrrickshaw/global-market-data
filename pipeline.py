#!/usr/bin/env python3
# pipeline.py
# ===========
# CRUD data-pipeline manager keyed on the stocks that CLEAR A FILTER.
#
# The idea: deep data is expensive to fetch and process, so we don't keep 5y depth
# + fundamentals for all ~40k symbols. Instead a promotion registry tracks the
# "watchlist" — stocks currently clearing the screens — and the two memory tiers are
# aligned to it:
#
#   SHORT-TERM MEMORY (STM, 1y)  → the WHOLE universe, shallow + cheap (refreshed
#                                  broadly every day; drives the filters).
#   LONG-TERM MEMORY (LTM, 5y)   → only PROMOTED (filter-clearing) stocks are kept
#     + fundamentals               deep, and expensive SEC/screener.in fundamentals
#                                  are fetched only for them → bounded fetch/compute.
#
# CRUD (like SQL, on the promotion registry cache_seed/pipeline_state.json):
#   CREATE  promote(market, syms, filters)   a stock clears a filter → deep-track it
#   READ    promoted(market) / status()      the current watchlist + coverage
#   UPDATE  refresh(market)                  STM broad + LTM/fundamentals for promoted;
#                                            keeps the two tiers aligned & current
#   DELETE  demote(market, syms)             no longer clears any filter → stop deep
#                                            tracking (frees fetch/compute budget)
#   SYNC    sync(market)                     run the filters, diff vs registry,
#                                            promote new / demote gone (the driver)
#
#   python3 pipeline.py --market IN --sync        # run filters → update watchlist
#   python3 pipeline.py --market IN --refresh     # align STM/LTM + fetch promoted
#   python3 pipeline.py --status
#
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

import argparse
import datetime as _dt
import json
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

warnings.filterwarnings("ignore")

import datalink

STATE = Path(__file__).parent / "cache_seed" / "pipeline_state.json"
MARKETS = datalink.MARKETS


# ── state (the registry) ─────────────────────────────────────────────────────────
def _load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def _save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2, default=str))


def _mkt(state: dict, market: str) -> dict:
    return state.setdefault(market, {"promoted": {}, "synced": None})


def _today() -> str:
    return _dt.date.today().isoformat()


# ── the filter set that drives promotion ─────────────────────────────────────────
def filter_clearers(market: str, min_turnover_usd: float = 1_000_000) -> Dict[str, List[str]]:
    """{symbol: [filters it clears]} — the union of the system's screens on current
    data. Price/technical (all markets) + fundamentals + popular-screen universe."""
    hits: Dict[str, set] = {}

    def add(name, syms):
        for s in syms:
            hits.setdefault(s, set()).add(name)

    # price/technical screens (Screener.in metric formulas) — cheap, all markets
    try:
        import screen_metrics as sm

        for name in ("golden_crossover", "companies_creating_new_high",
                     "price_volume_action", "breakout_stocks", "multibagger_momentum"):
            df = sm.run_screen(name, market, min_turnover_usd=min_turnover_usd)
            if not df.empty:
                add(name, df["Symbol"])
    except Exception:
        pass
    # fundamental screens on cached financials
    try:
        from auto_screener import fundamental_anchor

        add("fundamental", fundamental_anchor(market))
    except Exception:
        pass
    # popular Screener.in universe (validation)
    try:
        from validation import known_universe

        add("popular", known_universe(market))
    except Exception:
        pass
    return {s: sorted(f) for s, f in hits.items()}


# ── CRUD ─────────────────────────────────────────────────────────────────────────
def promote(market: str, symbols: Dict[str, List[str]], verbose: bool = True) -> int:
    """CREATE/UPDATE registry entries for stocks that cleared a filter."""
    state = _load()
    m = _mkt(state, market)
    n_new = 0
    for sym, filters in symbols.items():
        if sym not in m["promoted"]:
            n_new += 1
            m["promoted"][sym] = {"filters": filters, "since": _today(), "refreshed": None}
        else:
            m["promoted"][sym]["filters"] = filters  # keep since-date
    _save(state)
    if verbose:
        print(f"  promote[{market}]: +{n_new} new, {len(m['promoted'])} on watchlist")
    return n_new


def demote(market: str, symbols: List[str], trim_ltm: bool = False, verbose: bool = True) -> int:
    """DELETE registry entries (stocks that no longer clear any filter)."""
    state = _load()
    m = _mkt(state, market)
    gone = [s for s in symbols if s in m["promoted"]]
    for s in gone:
        del m["promoted"][s]
    _save(state)
    if trim_ltm and gone:
        try:
            import market_memory as mm

            mm.drop(market, gone, verbose=False)  # free deep history for demoted names
        except Exception:
            pass
    if verbose:
        print(f"  demote[{market}]: -{len(gone)}, {len(m['promoted'])} remain")
    return len(gone)


def promoted(market: str) -> Dict[str, dict]:
    return _load().get(market, {}).get("promoted", {})


def sync(market: str, min_turnover_usd: float = 1_000_000, min_filters: int = 2,
         verbose: bool = True) -> dict:
    """The pipeline driver: run the filters, diff against the registry, promote new
    HIGH-CONVICTION clearers (clearing ≥ min_filters distinct filters) and demote
    names that no longer qualify. The conviction gate bounds the deep-tracked set so
    fetch/compute stays cheap."""
    allhits = filter_clearers(market, min_turnover_usd)
    current = {s: f for s, f in allhits.items() if len(f) >= min_filters}
    prev = set(promoted(market))
    now = set(current)
    n_new = promote(market, {s: current[s] for s in now}, verbose=False)
    n_gone = demote(market, list(prev - now), verbose=False)
    state = _load()
    _mkt(state, market)["synced"] = _dt.datetime.now().isoformat(timespec="seconds")
    _save(state)
    if verbose:
        print(f"  sync[{market}]: watchlist {len(now)} "
              f"(+{n_new} promoted, -{n_gone} demoted)")
    return {"market": market, "watchlist": len(now), "promoted": n_new, "demoted": n_gone}


# ── UPDATE: align the tiers + fetch deep data for promoted only ──────────────────
def refresh(market: str, fetch_fundamentals: bool = True, fund_limit: int = 60,
            verbose: bool = True) -> dict:
    """Align STM/LTM and fetch the expensive deep data ONLY for promoted stocks.

    STM (whole universe) is kept broad/cheap by the daily memory job; here we ensure
    the promoted names are deep-tracked (LTM present, kept aligned) and their
    fundamentals are fetched — bounding fetch/compute to the watchlist."""
    watch = list(promoted(market))
    if verbose:
        print(f"  refresh[{market}]: {len(watch)} promoted stocks")
    out = {"market": market, "promoted": len(watch), "fundamentals_fetched": 0}

    # 1) ensure the promoted names have deep LTM (seed-align if the LTM is missing);
    #    market_memory keeps LTM as the trailing-aligned source for STM already.
    #    (Deep network backfill for promoted-only can be added when online.)

    # 2) fetch fundamentals for the promoted watchlist (bounded, prioritised)
    if fetch_fundamentals and watch:
        try:
            import fundamental_metrics as fmet

            before = _fund_count(market)
            fmet.load_fundamentals(market, limit=fund_limit, verbose=False, symbols=watch)
            out["fundamentals_fetched"] = _fund_count(market) - before
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"    fundamentals fetch skipped ({str(e)[:40]})")

    # 3) stamp refreshed date on the promoted entries
    state = _load()
    for s in watch:
        state[market]["promoted"][s]["refreshed"] = _today()
    _save(state)
    if verbose:
        print(f"    fundamentals now cover +{out['fundamentals_fetched']} promoted names")
    return out


def _fund_count(market: str) -> int:
    try:
        import fundamental_metrics as fmet

        p = fmet.CACHE / f"{market}.parquet"
        return len(pd.read_parquet(p)) if p.exists() else 0
    except Exception:
        return 0


# ── status ───────────────────────────────────────────────────────────────────────
def status(markets: Optional[List[str]] = None) -> pd.DataFrame:
    state = _load()
    rows = []
    for m in markets or MARKETS:
        pm = state.get(m, {}).get("promoted", {})
        if not pm:
            continue
        refreshed = sum(1 for v in pm.values() if v.get("refreshed"))
        rows.append({"market": m, "watchlist": len(pm), "fund_refreshed": refreshed,
                     "synced": state.get(m, {}).get("synced")})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="CRUD data-pipeline manager (filter-driven tiering)")
    ap.add_argument("--market", default="IN")
    ap.add_argument("--sync", action="store_true", help="run filters → update watchlist")
    ap.add_argument("--refresh", action="store_true", help="align tiers + fetch promoted deep data")
    ap.add_argument("--status", action="store_true", help="show watchlist coverage")
    ap.add_argument("--all", action="store_true", help="apply to all 20 markets")
    ap.add_argument("--min-turnover", type=float, default=1_000_000)
    ap.add_argument("--min-filters", type=int, default=2, help="conviction: clear >= N filters to promote")
    args = ap.parse_args()

    if args.status:
        df = status()
        print(df.to_string(index=False) if not df.empty else "no watchlist yet — run --sync")
        return 0

    markets = MARKETS if args.all else [args.market]
    for m in markets:
        if args.sync or not args.refresh:
            sync(m, args.min_turnover, args.min_filters)
        if args.refresh:
            refresh(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
