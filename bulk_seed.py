#!/usr/bin/env python3
# bulk_seed.py
# ============
# One-time / periodic DEEP backfill of the tiered memory (LTM), from two sources:
#
#   yfinance : pull multi-year history for a market's tracked symbols via Yahoo
#              (.NS/.BO for India). Turns the ~1y LTM into the full 5y tier.
#                python3 bulk_seed.py --market IN --source yfinance --period 5y
#
#   csv      : bulk-import a Kaggle/exported CSV (or a folder of them). Fast way to
#              seed 10-20y history without a long crawl. Flexible column mapping.
#                python3 bulk_seed.py --market IN --source csv --path ~/Downloads/nse_eod
#
# Both normalise to the long OHLCV schema and commit via market_memory (upsert →
# evict past LTM window → re-derive STM). Best-effort; safe to re-run (idempotent
# upsert). ⚠️ Research/education only. Not advice.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import market_memory as mm

# common column aliases seen in Kaggle / exchange EOD exports → our schema
_COL_ALIASES = {
    "symbol": "Symbol",
    "sym": "Symbol",
    "ticker": "Symbol",
    "tckrsymb": "Symbol",
    "date": "Date",
    "datetime": "Date",
    "timestamp": "Date",
    "trade_date": "Date",
    "traddt": "Date",
    "open": "Open",
    "opnpric": "Open",
    "high": "High",
    "hghpric": "High",
    "low": "Low",
    "lwpric": "Low",
    "close": "Close",
    "clspric": "Close",
    "last": "Close",
    "volume": "Volume",
    "tottrdqty": "Volume",
    "ttl_trd_qnty": "Volume",
    "trdqty": "Volume",
}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    ren = {
        c: _COL_ALIASES[c.strip().lower()] for c in df.columns if c.strip().lower() in _COL_ALIASES
    }
    df = df.rename(columns=ren)
    need = ["Date", "Open", "High", "Low", "Close", "Volume", "Symbol"]
    if not all(c in df.columns for c in need):
        return pd.DataFrame(columns=need)
    df = df[need].copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Symbol"] = (
        df["Symbol"].astype(str).str.strip().str.upper().str.replace(r"^(NSE|BSE):", "", regex=True)
    )
    return df.dropna(subset=["Date", "Close", "Symbol"])


# ── yfinance deep backfill ────────────────────────────────────────────────────
def from_yfinance(
    market: str,
    symbols: Optional[List[str]] = None,
    period: str = "5y",
    suffix: str = ".NS",
    batch: int = 100,
    verbose: bool = True,
) -> dict:
    import stock_utils

    syms = symbols or list(mm.read(market, tier="ltm").keys())
    if not syms:
        return {"market": market, "error": "no symbols to backfill"}
    tickers = [s + suffix for s in syms]
    if verbose:
        print(f"  yfinance backfill: {len(tickers)} {market} tickers, period={period}")
    hist = stock_utils.bulk_download(
        tickers, period=period, batch_size=batch, min_bars=1, verbose=verbose
    )
    # map Yahoo ticker back to the bare symbol used in the LTM
    clean: Dict[str, pd.DataFrame] = {}
    for t, df in hist.items():
        bare = t[: -len(suffix)] if suffix and t.endswith(suffix) else t
        clean[bare] = df
    if not clean:
        return {"market": market, "fetched": 0}
    long = mm.to_long(clean)
    res = mm.commit(market, long, verbose=verbose)
    res["fetched"] = len(clean)
    return res


# ── CSV / folder bulk import ──────────────────────────────────────────────────
def from_csv(market: str, path: str, verbose: bool = True) -> dict:
    p = Path(path).expanduser()
    files = sorted(p.glob("*.csv")) if p.is_dir() else ([p] if p.exists() else [])
    if not files:
        return {"market": market, "error": f"no CSV at {path}"}
    frames = []
    for f in files:
        try:
            frames.append(_normalise(pd.read_csv(f)))
        except Exception:
            continue
    frames = [f for f in frames if not f.empty]
    if not frames:
        return {"market": market, "error": "no parseable rows (check columns)"}
    long = pd.concat(frames, ignore_index=True).drop_duplicates(["Date", "Symbol"])
    if verbose:
        print(
            f"  csv import: {len(files)} file(s) → {len(long)} rows, {long['Symbol'].nunique()} symbols"
        )
    res = mm.commit(market, long, verbose=verbose)
    res["rows_imported"] = len(long)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Deep-backfill the LTM from yfinance or CSV")
    ap.add_argument("--market", default="IN")
    ap.add_argument("--source", choices=["yfinance", "csv"], required=True)
    ap.add_argument("--period", default="5y", help="yfinance period (5y, 10y, max)")
    ap.add_argument("--suffix", default=".NS", help="yfinance suffix (.NS NSE / .BO BSE)")
    ap.add_argument("--path", help="CSV file or folder (for --source csv)")
    ap.add_argument("--limit", type=int, help="cap symbols (yfinance, for testing)")
    a = ap.parse_args()

    if a.source == "yfinance":
        syms = list(mm.read(a.market, tier="ltm").keys())
        if a.limit:
            syms = syms[: a.limit]
        res = from_yfinance(a.market, symbols=syms, period=a.period, suffix=a.suffix)
    else:
        if not a.path:
            print("--path required for --source csv")
            return 1
        res = from_csv(a.market, a.path)
    print("result:", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
