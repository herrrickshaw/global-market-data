#!/usr/bin/env python3
# bhavcopy_store.py
# =================
# Embedded NoSQL (LMDB) key-value store for the bhavcopy OHLCV cache.
#
# Why: the long-format parquet must be read whole and re-grouped (~seconds, tens
# of MB) even when you only want a handful of symbols. LMDB is a memory-mapped
# key-value store — symbol → compressed Arrow bytes — so retrieving ONE symbol is
# a single O(1) keyed read (microseconds), bulk reads touch only the keys asked
# for, and the zstd-compressed Arrow values make the whole store smaller than the
# parquet while loading straight back into a pandas DataFrame.
#
#   key   = symbol            (utf-8 bytes)
#   value = zstd Arrow IPC of that symbol's OHLCV frame (Date index + OHLCV)
#
# Build from the cleaned cache:   python3 bhavcopy_store.py --build
# Use:    from bhavcopy_store import get, get_many, load_all, symbols

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.feather as feather

CACHE = Path(os.environ.get("BHAV_CACHE", Path.home() / "Downloads" / "data" / "bhavcopy_cache"))
STORE = CACHE / "ohlcv.lmdb"
CLEANED = CACHE / "cleaned_long.parquet"
_MAP_SIZE = 512 * 1024 * 1024  # 512 MB virtual cap (sparse; grows as used)
_META_KEY = b"__meta__"


def _ser(df: pd.DataFrame) -> bytes:
    """DataFrame (Date index + OHLCV) → zstd Arrow IPC bytes.

    OHLC downcast to float32 (prices need ~7 sig figs at most → float32 is exact
    enough and halves numeric bytes); Volume kept int64. Smaller value, same data.
    """
    d = df.reset_index()
    for c in ("Open", "High", "Low", "Close"):
        if c in d.columns:
            d[c] = d[c].astype("float32")
    if "Volume" in d.columns:
        d["Volume"] = pd.to_numeric(d["Volume"], errors="coerce").fillna(0).astype("int64")
    buf = io.BytesIO()
    feather.write_feather(pa.Table.from_pandas(d, preserve_index=False), buf, compression="zstd")
    return buf.getvalue()


def _de(b: bytes) -> pd.DataFrame:
    """zstd Arrow IPC bytes → DataFrame indexed by Date (float64 prices restored)."""
    df = feather.read_feather(io.BytesIO(b))
    idx = "Date" if "Date" in df.columns else df.columns[0]
    df = df.set_index(idx).sort_index()
    for c in ("Open", "High", "Low", "Close"):
        if c in df.columns:
            df[c] = df[c].astype("float64")
    return df


# ── build ──────────────────────────────────────────────────────────────────────
def build(hist: Optional[Dict[str, pd.DataFrame]] = None, verbose: bool = True) -> int:
    """Build/refresh the LMDB store. Source: a {symbol: df} dict, else the
    cleaned_long parquet. Returns the number of symbols written."""
    import lmdb

    if hist is None:
        # ingest every market seed present (cleaned_long.parquet = IN bhavcopy,
        # cleaned_long_US.parquet, cleaned_long_<MKT>.parquet …) into one store.
        seeds = sorted(
            set([CLEANED] if CLEANED.exists() else []) | set(CACHE.glob("cleaned_long_*.parquet"))
        )
        if not seeds:
            raise FileNotFoundError(f"no cleaned_long*.parquet in {CACHE}")
        from frames import long_to_dict

        hist = {}
        for sd in seeds:
            hist.update(long_to_dict(pd.read_parquet(sd)))
        if verbose:
            print(
                f"  ingesting {len(seeds)} market seed(s): " f"{', '.join(s.name for s in seeds)}"
            )

    # fresh rebuild: remove any prior store so deleted pages aren't carried as
    # free space (LMDB does not shrink its file in place).
    import shutil

    if STORE.exists():
        shutil.rmtree(STORE, ignore_errors=True)

    env = lmdb.open(str(STORE), map_size=_MAP_SIZE, subdir=True)
    n = 0
    with env.begin(write=True) as txn:
        maxd = None
        for sym, df in hist.items():
            if df is None or df.empty:
                continue
            txn.put(sym.encode(), _ser(df))
            n += 1
            last = df.index[-1]
            maxd = last if maxd is None or last > maxd else maxd
        txn.put(_META_KEY, f"{n}|{maxd}".encode())
    env.sync()
    env.close()
    if verbose:
        size = sum(f.stat().st_size for f in STORE.glob("*"))
        print(
            f"  LMDB store built: {n} symbols, {size/1e6:.1f} MB → {STORE.name} "
            f"(latest bar {maxd.date() if maxd is not None else '?'})"
        )
    return n


# ── read ────────────────────────────────────────────────────────────────────────
def _env(readonly=True):
    import lmdb

    return lmdb.open(
        str(STORE), map_size=_MAP_SIZE, subdir=True, readonly=readonly, lock=not readonly
    )


def get(symbol: str) -> Optional[pd.DataFrame]:
    """O(1) retrieval of one symbol's OHLCV DataFrame (or None)."""
    if not STORE.exists():
        return None
    env = _env()
    with env.begin() as txn:
        b = txn.get(symbol.encode())
    env.close()
    return _de(b) if b else None


def get_many(syms: Iterable[str]) -> Dict[str, pd.DataFrame]:
    """Retrieve a chosen set of symbols — touches only those keys."""
    if not STORE.exists():
        return {}
    out = {}
    env = _env()
    with env.begin() as txn:
        for s in syms:
            b = txn.get(s.encode())
            if b:
                out[s] = _de(b)
    env.close()
    return out


def load_all() -> Dict[str, pd.DataFrame]:
    """Load the whole universe as {symbol: df} (cursor scan)."""
    if not STORE.exists():
        return {}
    out = {}
    env = _env()
    with env.begin() as txn:
        for k, v in txn.cursor():
            if k == _META_KEY:
                continue
            out[k.decode()] = _de(v)
    env.close()
    return out


def symbols() -> List[str]:
    """List all stored tickers (no value reads)."""
    if not STORE.exists():
        return []
    env = _env()
    with env.begin() as txn:
        ks = [k.decode() for k, _ in txn.cursor() if k != _META_KEY]
    env.close()
    return ks


def info() -> dict:
    if not STORE.exists():
        return {}
    env = _env()
    with env.begin() as txn:
        meta = txn.get(_META_KEY)
    env.close()
    n, maxd = (meta.decode().split("|") + [None, None])[:2] if meta else (0, None)
    size = sum(f.stat().st_size for f in STORE.glob("*"))
    return {"symbols": int(n), "latest_bar": maxd, "size_mb": round(size / 1e6, 1)}


def vacuum(verbose: bool = True) -> int:
    """Reclaim disk: delete the redundant raw daily bhavcopy CSVs.

    Every row in those CSVs is already consolidated in assembled_long.parquet
    (used for incremental appends) and cleaned into the LMDB store, so the raw
    per-day files are pure duplication. Returns bytes freed. Safe & reversible —
    missing days are simply re-downloaded on the next fetch.
    """
    freed = 0
    for sub in (CACHE / "nse", CACHE / "bse"):
        if not sub.exists():
            continue
        for f in sub.glob("*.csv"):
            freed += f.stat().st_size
            f.unlink()
    if verbose:
        print(f"  vacuum: reclaimed {freed/1e6:.1f} MB of redundant raw CSVs")
    return freed


if __name__ == "__main__":
    import sys

    if "--build" in sys.argv:
        build()
    if "--vacuum" in sys.argv:
        vacuum()
    print("store info:", info())
