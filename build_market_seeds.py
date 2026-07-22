#!/usr/bin/env python3
# build_market_seeds.py
# =====================
# Build compact per-market seed caches (cleaned_long_<MKT>.parquet) from the
# existing per-ticker OHLC parquet cache (market_cache/ohlc), so every country
# market the system has data for ships with a ready-to-use seed — same compact
# format as the India bhavcopy seed (float32 OHLC + int volume + zstd, ~1yr).
#
# Ticker → market is inferred from the yfinance suffix:
#   (none) → US     .NS/.BO → IN     .L → UK     .PA → FR     .DE → DE
#   .T → JP         .KS/.KQ → KR     .SI → SG    .HK → HK     .AX → AU
# Only markets actually present in the cache produce a seed.
#
# Usage:  python3 build_market_seeds.py [--days 400] [--out <dir>]

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

from frames import write_seed

try:
    from stock_utils import clean_ohlcv
except ImportError:
    clean_ohlcv = None

OHLC_DIR = Path.home() / "Downloads" / "data" / "market_cache" / "market_cache" / "ohlc"
SEED_DIR = Path(__file__).parent / "cache_seed"

# suffix → market code (None key = no suffix)
SUFFIX_MARKET = {
    None: "US",
    "NS": "IN",
    "BO": "IN",
    "L": "UK",
    "PA": "FR",
    "DE": "DE",
    "T": "JP",
    "KS": "KR",
    "KQ": "KR",
    "SI": "SG",
    "HK": "HK",
    "AX": "AU",
    "TO": "CA",
    "SW": "CH",
    "MI": "IT",
    "AS": "NL",
    "ST": "SE",
}


def _market_of(fname: str) -> str | None:
    base = fname[: -len(".parquet")] if fname.endswith(".parquet") else fname
    parts = base.split(".")
    suf = parts[-1] if len(parts) >= 2 else None
    return SUFFIX_MARKET.get(suf, SUFFIX_MARKET.get(None) if suf is None else None)


def build(days: int = 400, out: Path = SEED_DIR, verbose: bool = True) -> dict:
    if not OHLC_DIR.exists():
        raise FileNotFoundError(f"{OHLC_DIR} not found")
    out.mkdir(parents=True, exist_ok=True)
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)

    files = sorted(OHLC_DIR.glob("*.parquet"))
    if verbose:
        print(f"  scanning {len(files)} cached tickers in {OHLC_DIR.name} …")

    by_market: dict[str, list] = {}
    for f in files:
        mkt = _market_of(f.name)
        if mkt is None:
            continue
        by_market.setdefault(mkt, []).append(f)

    summary = {}
    for mkt, flist in sorted(by_market.items()):
        hist = {}
        for f in flist:
            try:
                d = pd.read_parquet(f)
            except Exception:
                continue
            if d.empty or "Close" not in d.columns:
                continue
            d = d[d.index >= cutoff]
            if clean_ohlcv is not None:
                d = clean_ohlcv(d, ticker=f.stem, min_bars=1)
            if d is not None and not d.empty:
                hist[f.name[: -len(".parquet")]] = d  # keep yfinance suffix
        if not hist:
            continue
        path = out / f"cleaned_long_{mkt}.parquet"
        n = write_seed(hist, path)
        rows = int(sum(len(d) for d in hist.values()))
        mb = path.stat().st_size / 1e6
        summary[mkt] = {"symbols": n, "rows": rows, "MB": round(mb, 1), "file": path.name}
        if verbose:
            print(f"  {mkt}: {n:>5} symbols, {rows:>8,} rows → {path.name} ({mb:.1f} MB)")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--out", type=Path, default=SEED_DIR)
    a = ap.parse_args()
    s = build(days=a.days, out=a.out)
    print("\nseeds built:", {k: v["file"] for k, v in s.items()})
