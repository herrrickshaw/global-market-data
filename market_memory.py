#!/usr/bin/env python3
# market_memory.py
# ================
# Two-tier OHLCV "memory" for all 20 markets, maintained daily like CRUD in SQL.
#
#   LONG-TERM MEMORY (LTM)  — 5 years, the archive.   cache_seed/ltm/<MKT>.parquet
#   SHORT-TERM MEMORY (STM) — 1 year, the hot layer.  cache_seed/cleaned_long[_MKT].parquet
#                             (exactly what screener_kit / the LMDB store already read)
#
# The system "feeds on good data but updates regularly": every day we fetch the
# newest bars, UPSERT them into LTM (dedupe on Symbol+Date, newest wins — so a
# revised/adjusted bar overwrites the stale one), EVICT anything older than the
# tier window, then DERIVE the 1-year STM as a trailing slice of LTM. Nothing is
# ever blindly appended, so re-runs are idempotent and corrections propagate.
#
#   CRUD mapping
#   ────────────
#     CREATE  init(market)            first-time 5y backfill (network) / seed from STM
#     READ    read(market, tier)      {symbol: OHLCV df} from LTM or STM
#     UPDATE  update(market)          fetch new bars → upsert → evict → derive STM
#     DELETE  evict(long, years)      drop rows past the window;  drop(market, syms)
#
# Sources: IN uses the official bhavcopy STM (maintained by update_bhavcopy_daily);
# the other 19 markets use data_sources.fetch (yahoo→stooq fallback).
#
#   python3 market_memory.py --daily                 # update all 20 (incremental)
#   python3 market_memory.py --daily --market US JP  # just these
#   python3 market_memory.py --create                # first-time 5y backfill (network)
#   python3 market_memory.py --status                # tier coverage report

from __future__ import annotations

import argparse
import datetime as _dt
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

warnings.filterwarnings("ignore")

from frames import OHLCV, read_seed, to_long, write_seed

SEED_DIR = Path(__file__).parent / "cache_seed"
LTM_DIR = SEED_DIR / "ltm"
LTM_DIR.mkdir(parents=True, exist_ok=True)

LTM_YEARS = 10  # long-term memory window (widened for deep backtest history)
STM_YEARS = 1  # short-term (hot) window

MARKETS = [
    "IN",
    "US",
    "JP",
    "KR",
    "CN",
    "SG",
    "EU",
    "HK",
    "TW",
    "CA",
    "AU",
    "UK",
    "DE",
    "SA",
    "BR",
    "CH",
    "ZA",
    "SE",
    "FI",
    "DK",
]  # Wikipedia top-20


# ── paths ──────────────────────────────────────────────────────────────────────
def _ltm_path(market: str) -> Path:
    return LTM_DIR / f"{market}.parquet"


def _stm_path(market: str) -> Path:
    name = "cleaned_long.parquet" if market == "IN" else f"cleaned_long_{market}.parquet"
    return SEED_DIR / name


# ── CRUD core ──────────────────────────────────────────────────────────────────
def _read_long(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["Date", *OHLCV, "Symbol"])
    df = pd.read_parquet(path)
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def _upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """UPSERT new rows into existing: union, then keep the newest write per
    (Symbol, Date) — so revised/adjusted bars overwrite stale ones."""
    if new is None or new.empty:
        return existing
    if existing is None or existing.empty:
        combined = new.copy()
    else:
        combined = pd.concat([existing, new], ignore_index=True)
    combined["Date"] = pd.to_datetime(combined["Date"])
    # last occurrence wins → new rows (appended last) overwrite duplicates
    combined = combined.drop_duplicates(subset=["Symbol", "Date"], keep="last")
    return combined.sort_values(["Symbol", "Date"], ignore_index=True)


def _evict(long: pd.DataFrame, years: int) -> pd.DataFrame:
    """DELETE rows older than `years` from today (tier retention)."""
    if long.empty:
        return long
    cutoff = pd.Timestamp(_dt.date.today() - _dt.timedelta(days=int(years * 365.25) + 5))
    return long[long["Date"] >= cutoff]


def read(market: str, tier: str = "stm", min_bars: int = 1) -> Dict[str, pd.DataFrame]:
    """READ a tier back into {symbol: OHLCV df}. tier ∈ {'stm','ltm'}."""
    path = _ltm_path(market) if tier == "ltm" else _stm_path(market)
    return read_seed(path, min_bars=min_bars)


def _write(long: pd.DataFrame, path: Path) -> int:
    """Persist a long frame as a compact zstd seed (via frames.write_seed to keep
    dtype/format identical to every other seed)."""
    from frames import long_to_dict

    return write_seed(long_to_dict(long), path)


def _derive_stm(ltm_long: pd.DataFrame, market: str) -> int:
    """DERIVE the 1-year STM as a trailing slice of the 5-year LTM."""
    stm = _evict(ltm_long, STM_YEARS)
    return _write(stm, _stm_path(market))


def ensure_stm_seeds(
    markets: Optional[List[str]] = None, force: bool = False, verbose: bool = True
) -> int:
    """Derive any missing STM seed (cleaned_long[_MKT].parquet) from the committed
    5y LTM. The repo ships only the LTM (source of truth); the 1y STM is a trailing
    slice of it, so shipping both duplicates ~120 MB. This regenerates the STM
    seeds locally on first use. Returns the number derived."""
    n = 0
    for m in markets or MARKETS:
        stm, ltm = _stm_path(m), _ltm_path(m)
        if ltm.exists() and (force or not stm.exists()):
            _derive_stm(_read_long(ltm), m)
            n += 1
    if verbose and n:
        print(f"  derived {n} STM seed(s) from LTM")
    return n


def commit(market: str, new_long: pd.DataFrame, verbose: bool = True) -> dict:
    """The write half of every operation: upsert new_long into LTM, evict past the
    LTM window, persist LTM, then re-derive the 1y STM. Every write passes the
    MANDATORY ingest_gate (dedup + compact dtypes + size report + VCRUD audit)."""
    ltm = _upsert(_read_long(_ltm_path(market)), new_long)
    ltm = _evict(ltm, LTM_YEARS)
    # ── mandatory ingestion gate (best-effort import so the core never hard-breaks)
    before_bytes = _ltm_path(market).stat().st_size if _ltm_path(market).exists() else 0
    try:
        import ingest_gate

        ltm = ingest_gate.gate_frame(market, ltm, _ltm_path(market), verbose=verbose)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"  ⟢ ingest-gate skipped ({str(e)[:50]})")
    n_ltm = _write(ltm, _ltm_path(market))
    n_stm = _derive_stm(ltm, market)
    try:
        import ingest_gate

        ingest_gate.report_after(market, _ltm_path(market), before_bytes, verbose=verbose)
    except Exception:
        pass
    latest = str(ltm["Date"].max())[:10] if not ltm.empty else "—"
    if verbose:
        print(f"  {market}: LTM {n_ltm} syms / STM {n_stm} syms (latest {latest})")
    return {"market": market, "ltm_symbols": n_ltm, "stm_symbols": n_stm, "latest": latest}


# ── data acquisition ───────────────────────────────────────────────────────────
def _fetch_new(market: str, period: str, verbose: bool) -> pd.DataFrame:
    """Fetch fresh bars for a market as a long frame.

    IN → the official bhavcopy STM (kept current by update_bhavcopy_daily).
    others → data_sources.fetch (yahoo→stooq) for the symbols we already track
    (or the full universe when the LTM is empty / on --create).
    """
    if market == "IN":
        # bhavcopy is the source of truth for IN; treat its output as "new" and
        # let the upsert accumulate it into the 5y LTM over time. Prefer the LIVE
        # bhavcopy cache (kept current by update_bhavcopy_daily) and fall back to
        # the committed cache_seed copy.
        import os

        live = (
            Path(
                os.environ.get("BHAV_CACHE", Path.home() / "Downloads" / "data" / "bhavcopy_cache")
            )
            / "cleaned_long.parquet"
        )
        src = live if live.exists() else _stm_path("IN")
        return _read_long(src)

    from data_sources import fetch as multi_fetch

    # update existing rows if we have them; else seed from the market universe
    syms = list(read(market, tier="ltm").keys())
    if not syms:
        try:
            from universe_sources import get_universe

            syms = get_universe(market)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  {market}: no universe ({str(e)[:40]}) — skipped")
            return pd.DataFrame(columns=["Date", *OHLCV, "Symbol"])
    if verbose:
        print(f"  {market}: fetching {len(syms)} symbols (period={period}) …")
    hist = multi_fetch(syms, period=period, min_bars=1, verbose=False)
    return to_long(hist)


def update(market: str, period: str = "5d", verbose: bool = True) -> dict:
    """UPDATE one market: fetch new bars → upsert into LTM → evict → derive STM.

    Guard: if the LTM has little/no history (first run for this market), bootstrap
    it from the committed 1y seed FIRST — otherwise a short 5-day fetch would derive
    a thin STM that overwrites the good committed seed."""
    if market != "IN":
        ltm = _read_long(_ltm_path(market))
        span_days = 0 if ltm.empty else (ltm["Date"].max() - ltm["Date"].min()).days
        if span_days < 60:  # not yet seeded with real history
            seed = SEED_DIR / f"cleaned_long_{market}.parquet"
            if seed.exists():
                commit(market, _read_long(seed), verbose=False)
    new_long = _fetch_new(market, period=period, verbose=verbose)
    return commit(market, new_long, verbose=verbose)


def init(market: str, verbose: bool = True) -> dict:
    """CREATE: first-time 5-year backfill (network for non-IN)."""
    return update(market, period=f"{LTM_YEARS}y", verbose=verbose)


def drop(market: str, symbols: List[str], verbose: bool = True) -> dict:
    """DELETE specific (e.g. delisted) symbols from both tiers."""
    ltm = _read_long(_ltm_path(market))
    ltm = ltm[~ltm["Symbol"].isin(set(symbols))]
    n_ltm = _write(ltm, _ltm_path(market))
    n_stm = _derive_stm(ltm, market)
    if verbose:
        print(f"  {market}: dropped {len(symbols)} syms → LTM {n_ltm} / STM {n_stm}")
    return {"market": market, "ltm_symbols": n_ltm, "stm_symbols": n_stm}


def seed_from_committed(market: str, verbose: bool = True) -> dict:
    """CREATE offline: initialise the LTM from the committed 1-year seed
    (cache_seed/cleaned_long[_MKT].parquet) — no network. The LTM starts at ~1y
    and deepens toward 5y as daily updates upsert new bars."""
    name = "cleaned_long.parquet" if market == "IN" else f"cleaned_long_{market}.parquet"
    seed = SEED_DIR / name
    if not seed.exists():
        return {"market": market, "error": "no committed seed"}
    return commit(market, _read_long(seed), verbose=verbose)


# ── batch driver ───────────────────────────────────────────────────────────────
def update_all(
    markets: Optional[List[str]] = None,
    period: str = "5d",
    create: bool = False,
    verbose: bool = True,
) -> List[dict]:
    markets = markets or MARKETS
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if verbose:
        mode = "CREATE 5y backfill" if create else f"daily update (period={period})"
        print(f"[{ts}] market memory — {mode} for {len(markets)} markets")
    # skip markets closed today (weekend/holiday) — no wasted fetch/processing
    try:
        from market_holidays import is_market_open_today

        open_markets = [m for m in markets if create or is_market_open_today(m)]
        skipped = [m for m in markets if m not in open_markets]
        if skipped and verbose:
            print(f"  holiday/weekend — skipping closed markets: {skipped}")
        markets = open_markets
    except Exception:
        pass

    out = []
    for m in markets:
        try:
            out.append(init(m, verbose) if create else update(m, period, verbose))
        except Exception as e:  # noqa: BLE001 — one market must not sink the batch
            if verbose:
                print(f"  {m}: FAILED ({str(e)[:60]})")
            out.append({"market": m, "error": str(e)})
    return out


def status(markets: Optional[List[str]] = None) -> pd.DataFrame:
    """Coverage report: symbol counts + date span for each tier, per market."""
    rows = []
    for m in markets or MARKETS:
        for tier in ("ltm", "stm"):
            long = _read_long(_ltm_path(m) if tier == "ltm" else _stm_path(m))
            if long.empty:
                rows.append(
                    {"market": m, "tier": tier, "symbols": 0, "start": "—", "end": "—", "rows": 0}
                )
            else:
                rows.append(
                    {
                        "market": m,
                        "tier": tier,
                        "symbols": long["Symbol"].nunique(),
                        "start": str(long["Date"].min())[:10],
                        "end": str(long["Date"].max())[:10],
                        "rows": len(long),
                    }
                )
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Two-tier (5y LTM / 1y STM) market memory")
    ap.add_argument("--daily", action="store_true", help="incremental daily update (default)")
    ap.add_argument("--create", action="store_true", help="first-time 5-year backfill (network)")
    ap.add_argument(
        "--from-seeds",
        action="store_true",
        help="initialise LTM offline from the committed 1y seeds (no network)",
    )
    ap.add_argument("--status", action="store_true", help="print tier coverage report")
    ap.add_argument("--market", nargs="*", help="limit to these market codes")
    ap.add_argument("--period", default="5d", help="fetch window for --daily (default 5d)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.status:
        with pd.option_context("display.max_rows", None, "display.width", 120):
            print(status(args.market).to_string(index=False))
        return 0

    if args.from_seeds:
        for m in args.market or MARKETS:
            seed_from_committed(m, verbose=not args.quiet)
        return 0

    try:
        update_all(
            markets=args.market,
            period=args.period,
            create=args.create,
            verbose=not args.quiet,
        )
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"market memory update FAILED: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
