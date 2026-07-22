#!/usr/bin/env python3
# ingest_gate.py
# ==============
# The MANDATORY gate every data ingestion passes through before it is persisted.
# It enforces the things that actually keep the store small + clean (not VCRUD's
# gzip myth): row-dedup, the compact dtype/codec, a size-delta report, and an
# optional read-only VCRUD audit entry (no git hooks, no "mandatory workflow").
#
# Steps:
#   1. DEDUP    — assert no duplicate (Symbol, Date) rows; drop if any slip through.
#   2. ENFORCE  — float32 OHLC + int32 volume (halves columns; zstd-9 on write).
#   3. REPORT   — rows / symbols / bytes before→after, bytes-per-row.
#   4. AUDIT    — best-effort VCRUD register (checksum/size), never fatal.
#
# market_memory.commit() calls gate_frame() so every write is gated automatically.
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

import pandas as pd

HERE = Path(__file__).parent
KEYS = ["Symbol", "Date"]


def dedup(long: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Drop duplicate (Symbol, Date) rows, newest wins. Returns (clean, n_dropped)."""
    if long is None or long.empty or not set(KEYS).issubset(long.columns):
        return long, 0
    before = len(long)
    clean = long.drop_duplicates(subset=KEYS, keep="last")
    return clean, before - len(clean)


def enforce_dtypes(long: pd.DataFrame) -> pd.DataFrame:
    """Compact dtypes: float32 prices, int32 volume when it fits (else int64)."""
    if long is None or long.empty:
        return long
    out = long.copy()
    for c in ("Open", "High", "Low", "Close"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float32")
    if "Volume" in out:
        v = pd.to_numeric(out["Volume"], errors="coerce").fillna(0)
        out["Volume"] = v.astype("int32") if v.abs().max() <= 2_147_483_647 else v.astype("int64")
    return out


def _audit(market: str, path: Path, report: dict) -> str:
    """Best-effort VCRUD register (needs postgres + vcrud modules on PYTHONPATH)."""
    try:
        import sys

        vb = str(HERE / ".vcrud_bin")
        if vb not in sys.path:
            sys.path.insert(0, vb)
        from db_handler import DatabaseHandler
        from vcrud_manager import LocalFileIndexer

        db = DatabaseHandler(os.environ.get("VCRUD_DB", "postgresql://umashankar@localhost/vcrud"))
        recs = LocalFileIndexer(str(HERE)).scan_directory(
            f"ingest-{market}", patterns=[str(path.relative_to(HERE))]
        )
        for r in recs:
            db.create_file(r)
        return "audited"
    except Exception:
        return "audit-skipped"


def gate_frame(
    market: str, long: pd.DataFrame, out_path: Path, verbose: bool = True
) -> pd.DataFrame:
    """Run the mandatory gate on a long frame *before* it is written. Returns the
    cleaned/enforced frame the caller then persists (zstd-9 handled by write_seed)."""
    before_bytes = out_path.stat().st_size if out_path.exists() else 0
    clean, dropped = dedup(long)
    clean = enforce_dtypes(clean)
    if verbose:
        syms = clean["Symbol"].nunique() if "Symbol" in clean else 0
        bpr = (before_bytes / len(clean)) if len(clean) else 0
        print(
            f"  ⟢ ingest-gate[{market}]: {len(clean):,} rows / {syms} symbols"
            f"{f' · dropped {dropped} dup rows' if dropped else ' · 0 dups'}"
            f" · prev {before_bytes/1e6:.1f}MB (~{bpr:.1f} B/row)"
        )
    return clean


def report_after(market: str, out_path: Path, before_bytes: int, verbose: bool = True) -> dict:
    """Called after the write: size delta + VCRUD audit."""
    after = out_path.stat().st_size if out_path.exists() else 0
    rep = {
        "market": market,
        "before_mb": round(before_bytes / 1e6, 1),
        "after_mb": round(after / 1e6, 1),
        "delta_mb": round((after - before_bytes) / 1e6, 1),
    }
    rep["audit"] = _audit(market, out_path, rep)
    if verbose:
        print(
            f"  ⟢ ingest-gate[{market}]: on-disk {rep['after_mb']}MB "
            f"({rep['delta_mb']:+.1f}MB) · {rep['audit']}"
        )
    return rep


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Ingestion gate self-check")
    ap.add_argument("--market", default="IN")
    a = ap.parse_args()
    import market_memory as mm

    df = mm._read_long(mm._ltm_path(a.market))
    clean, dropped = dedup(df)
    clean = enforce_dtypes(clean)
    print(
        f"{a.market}: {len(df):,} rows, dupes dropped={dropped}, dtypes={dict(clean.dtypes.astype(str))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
