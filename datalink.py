#!/usr/bin/env python3
# datalink.py
# ===========
# The data-linkage layer: one index that ties every data asset together and
# removes the repeated reads / re-groups / network calls that dominate run time.
#
# WHY: before this, each screener call re-read a whole cleaned_long parquet and
# re-grouped 8k+ symbols, `store.build()` re-ingested all 20 markets even when one
# changed, and India CCC hit screener.in over the network on every screen. Running
# all 11 India screeners meant ~11× parquet reads + ~11× network fetches.
#
# WHAT this adds (all lazy, all cache-keyed on file signature so they stay fresh):
#   • manifest()            — cache_seed/data_manifest.json: the single index of
#                             every seed/LTM/reference asset (market, tier, symbols,
#                             date span, rows, signature). Consumers check coverage
#                             here instead of opening parquets.
#   • load_market()         — in-process memoized {symbol: OHLCV} grouped dict,
#                             keyed on (path, mtime, min_turnover). Repeat loads in
#                             one process are free.
#   • ccc_map_cached()      — India CCC from the committed parquet; refetches from
#                             screener.in only when missing/stale. No per-screen net.
#   • changed_markets()/build_store_incremental() — rebuild the LMDB only for the
#                             markets whose seed signature changed since last build.
#
#   python3 datalink.py --manifest     # (re)build + print the data manifest
#   python3 datalink.py --status       # coverage table from the manifest

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from frames import long_to_dict

HERE = Path(__file__).parent
SEED_DIR = HERE / "cache_seed"
LTM_DIR = SEED_DIR / "ltm"
REF_DIR = HERE / "reference_seed"
MANIFEST = SEED_DIR / "data_manifest.json"
STORE_STATE = SEED_DIR / ".store_state.json"
# live working cache (updated by the daily jobs); preferred over committed seed
LIVE_CACHE = Path(
    os.environ.get("BHAV_CACHE", Path.home() / "Downloads" / "data" / "bhavcopy_cache")
)

MARKETS = [
    "IN", "US", "JP", "KR", "CN", "SG", "EU", "HK", "TW", "CA",
    "AU", "UK", "DE", "SA", "BR", "CH", "ZA", "SE", "FI", "DK",
]


def _seed_name(market: str) -> str:
    return "cleaned_long.parquet" if market == "IN" else f"cleaned_long_{market}.parquet"


def _stm_path(market: str) -> Path:
    """Prefer the live working cache; else the committed seed; else derive the STM
    seed from the committed LTM (the repo ships only LTM, STM = trailing 1y slice)."""
    live = LIVE_CACHE / _seed_name(market)
    if live.exists():
        return live
    seed = SEED_DIR / _seed_name(market)
    if not seed.exists() and _ltm_path(market).exists():
        try:
            from market_memory import ensure_stm_seeds

            ensure_stm_seeds([market], verbose=False)
        except Exception:
            pass
    return seed


def _ltm_path(market: str) -> Path:
    return LTM_DIR / f"{market}.parquet"


# ── file signature (cheap: size + mtime, no hashing of big parquets) ────────────
def _sig(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


# ── memoized market loader ──────────────────────────────────────────────────────
# key = (path, signature, min_turnover) → grouped {symbol: df}. Screening only
# reads the frames, so sharing the same objects across calls is safe.
_LOAD_CACHE: Dict[tuple, Dict[str, pd.DataFrame]] = {}


def load_market(
    market: str, min_turnover_usd: float = 0.0, tier: str = "stm"
) -> Dict[str, pd.DataFrame]:
    """Memoized {symbol: OHLCV} for a market. Re-reads only when the seed changes."""
    path = _ltm_path(market) if tier == "ltm" else _stm_path(market)
    if not path.exists():
        return {}
    key = (str(path), _sig(path), float(min_turnover_usd))
    hit = _LOAD_CACHE.get(key)
    if hit is not None:
        return hit
    long = pd.read_parquet(path)
    if min_turnover_usd and min_turnover_usd > 0:
        try:
            from liquidity import liquid_symbols

            keep = set(liquid_symbols(market, min_usd=min_turnover_usd))
            long = long[long["Symbol"].isin(keep)]
        except Exception:
            pass
    grouped = long_to_dict(long)
    _LOAD_CACHE[key] = grouped
    return grouped


def clear_cache() -> None:
    _LOAD_CACHE.clear()


# ── cached India CCC (no per-screen network) ────────────────────────────────────
_CCC_CACHE: Optional[dict] = None
CCC_PARQUET = SEED_DIR / "india_ccc_screen.parquet"


def ccc_map_cached(max_age_days: float = 1.0, allow_network: bool = True) -> dict:
    """{symbol: CCC days} from the committed parquet; refetch from screener.in only
    if the cache is missing or older than max_age_days. Memoized per process."""
    global _CCC_CACHE
    if _CCC_CACHE is not None:
        return _CCC_CACHE

    fresh = CCC_PARQUET.exists() and (time.time() - CCC_PARQUET.stat().st_mtime) < max_age_days * 86400
    if not fresh and allow_network:
        try:
            from screener_in import ccc_screen

            df = ccc_screen()
            if not df.empty:
                df.to_parquet(CCC_PARQUET, index=False)
        except Exception:
            pass  # keep whatever committed parquet we have

    out: dict = {}
    if CCC_PARQUET.exists():
        df = pd.read_parquet(CCC_PARQUET)
        if "Cash_Cycle" in df.columns:
            out = {
                k: v
                for k, v in zip(df["Symbol"], pd.to_numeric(df["Cash_Cycle"], errors="coerce"))
                if v == v
            }
    _CCC_CACHE = out
    return out


# ── manifest: the single data index ─────────────────────────────────────────────
def _relpath(path: Path) -> str:
    try:
        return str(path.relative_to(HERE))
    except ValueError:
        return str(path)  # live-cache path lives outside the repo


def _describe(path: Path, market: str, tier: str) -> dict:
    long = pd.read_parquet(path, columns=["Date", "Symbol"])
    long["Date"] = pd.to_datetime(long["Date"])
    return {
        "market": market,
        "tier": tier,
        "path": _relpath(path),
        "symbols": int(long["Symbol"].nunique()),
        "rows": int(len(long)),
        "start": str(long["Date"].min())[:10],
        "end": str(long["Date"].max())[:10],
        "bytes": path.stat().st_size,
        "sig": _sig(path),
    }


def build_manifest(verbose: bool = True) -> dict:
    """Scan all data assets and write cache_seed/data_manifest.json."""
    assets = []
    for m in MARKETS:
        for tier, p in (("stm", _stm_path(m)), ("ltm", _ltm_path(m))):
            if p.exists():
                try:
                    assets.append(_describe(p, m, tier))
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print(f"  skip {p.name}: {str(e)[:40]}")
    refs = [
        {"path": _relpath(p), "bytes": p.stat().st_size, "sig": _sig(p)}
        for p in sorted(REF_DIR.glob("*.parquet"))
    ]
    man = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "markets": assets,
        "reference": refs,
    }
    MANIFEST.write_text(json.dumps(man, indent=2))
    if verbose:
        print(f"  manifest → {MANIFEST.name}: {len(assets)} market assets, {len(refs)} reference")
    return man


def read_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


# ── incremental LMDB store build ────────────────────────────────────────────────
def _stm_signatures() -> Dict[str, str]:
    return {m: _sig(_stm_path(m)) for m in MARKETS if _stm_path(m).exists()}


def changed_markets() -> List[str]:
    """Markets whose STM seed changed since the last store build."""
    prev = json.loads(STORE_STATE.read_text()) if STORE_STATE.exists() else {}
    now = _stm_signatures()
    return [m for m, s in now.items() if prev.get(m) != s]


def build_store_incremental(verbose: bool = True) -> dict:
    """Update the LMDB store ONLY for markets whose seed changed. Falls back to a
    full build the first time (no prior state). Much faster on daily runs where
    typically only IN changed."""
    import bhavcopy_store as store

    changed = changed_markets()
    if not STORE_STATE.exists() or not (store.STORE.exists()):
        n = store.build(verbose=verbose)  # cold: full build
        STORE_STATE.write_text(json.dumps(_stm_signatures(), indent=2))
        return {"mode": "full", "symbols": n, "changed": MARKETS}

    if not changed:
        if verbose:
            print("  store up to date — no markets changed")
        return {"mode": "noop", "changed": []}

    import lmdb

    env = lmdb.open(str(store.STORE), map_size=store._MAP_SIZE, subdir=True)
    written = 0
    with env.begin(write=True) as txn:
        for m in changed:
            for sym, df in long_to_dict(pd.read_parquet(_stm_path(m))).items():
                if df is None or df.empty:
                    continue
                txn.put(sym.encode(), store._ser(df))
                written += 1
    env.sync()
    env.close()
    STORE_STATE.write_text(json.dumps(_stm_signatures(), indent=2))
    if verbose:
        print(f"  store incremental: refreshed {len(changed)} market(s) {changed}, {written} symbols")
    return {"mode": "incremental", "symbols": written, "changed": changed}


def _status_table() -> pd.DataFrame:
    man = read_manifest() or build_manifest(verbose=False)
    return pd.DataFrame(man.get("markets", []))


def main() -> int:
    ap = argparse.ArgumentParser(description="Data linkage layer: manifest / status")
    ap.add_argument("--manifest", action="store_true", help="(re)build the data manifest")
    ap.add_argument("--status", action="store_true", help="print coverage from the manifest")
    ap.add_argument("--store", action="store_true", help="incremental LMDB store build")
    args = ap.parse_args()

    if args.store:
        build_store_incremental()
        return 0
    if args.manifest or not args.status:
        build_manifest()
    if args.status:
        df = _status_table()
        if not df.empty:
            with pd.option_context("display.max_rows", None, "display.width", 140):
                print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
