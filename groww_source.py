#!/usr/bin/env python3
# groww_source.py
# ===============
# Optional Groww API data source (India). Auth uses the key+secret flow (both from
# .env: GROWW_API_KEY, GROWW_API_SECRET) to mint a daily access token via the
# growwapi SDK. Exposes:
#   - instruments()         → full NSE/BSE instrument master (works on basic plan)
#   - fetch(symbols, ...)   → daily OHLC via historical candles (needs the
#                             market-data entitlement; 403s and returns {} without it)
#
# Everything is best-effort/offline-safe: missing SDK / missing keys / 403 → empty
# result, never an exception. Dormant unless keys are present, and NOT in the
# default OHLC chain (opt-in via data_sources.fetch(order=("groww", "yahoo"))).
#
# India-only; IN EOD is already covered by free bhavcopy (see ADR-16/17), so this
# is for the day market-data is enabled (intraday/live, or a bhavcopy cross-check).
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

_ENV = Path(__file__).parent / ".env"
_TOKEN_CACHE: dict = {}  # {"token": str, "day": date} — mint once per day


def _load_env() -> Dict[str, str]:
    if not _ENV.exists():
        return {}
    return dict(
        ln.split("=", 1)
        for ln in _ENV.read_text().splitlines()
        if "=" in ln and not ln.startswith("#")
    )


def _access_token() -> Optional[str]:
    """Mint (and day-cache) a Groww access token from key+secret. None if unavailable."""
    today = _dt.date.today()
    if _TOKEN_CACHE.get("day") == today and _TOKEN_CACHE.get("token"):
        return _TOKEN_CACHE["token"]
    env = _load_env()
    key, sec = env.get("GROWW_API_KEY") or os.environ.get("GROWW_API_KEY"), env.get(
        "GROWW_API_SECRET"
    ) or os.environ.get("GROWW_API_SECRET")
    if not key or not sec:
        return None
    try:
        from growwapi import GrowwAPI

        tok = GrowwAPI.get_access_token(api_key=key, secret=sec)
        _TOKEN_CACHE.update({"token": tok, "day": today})
        return tok
    except Exception:
        return None


def _client():
    tok = _access_token()
    if not tok:
        return None
    try:
        from growwapi import GrowwAPI

        return GrowwAPI(tok)
    except Exception:
        return None


def instruments() -> pd.DataFrame:
    """Full Groww instrument master (NSE/BSE). Empty frame if unavailable."""
    g = _client()
    if g is None:
        return pd.DataFrame()
    try:
        return g.get_all_instruments()
    except Exception:
        return pd.DataFrame()


def _groww_symbol(ticker: str, inst: Optional[pd.DataFrame] = None) -> Optional[str]:
    """Map a yfinance-style IN ticker (RELIANCE.NS / RELIANCE) to a Groww symbol."""
    base = re.sub(r"\.(NS|BO)$", "", ticker).upper()
    return f"NSE-{base}"  # Groww cash symbol convention


def fetch(
    tickers: List[str], period: str = "1y", min_bars: int = 60, verbose: bool = True
) -> Dict[str, pd.DataFrame]:
    """Daily OHLC for IN tickers via Groww historical candles.

    Returns {ticker: DataFrame[Open,High,Low,Close,Volume]} indexed by date.
    Needs the market-data entitlement; without it every call 403s and this
    returns {} (best-effort)."""
    g = _client()
    if g is None:
        return {}
    try:
        from growwapi import GrowwAPI
    except Exception:
        return {}

    m = re.fullmatch(r"(\d+)(d|mo|y)", period or "1y")
    days = {"d": 1, "mo": 31, "y": 366}[m.group(2)] * int(m.group(1)) + 5 if m else 366
    start = (_dt.date.today() - _dt.timedelta(days=days)).strftime("%Y-%m-%d 09:15:00")
    end = _dt.date.today().strftime("%Y-%m-%d 15:30:00")

    out: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        gs = _groww_symbol(t)
        if not gs:
            continue
        try:
            r = g.get_historical_candles(
                exchange=GrowwAPI.EXCHANGE_NSE,
                segment=GrowwAPI.SEGMENT_CASH,
                groww_symbol=gs,
                start_time=start,
                end_time=end,
                candle_interval=GrowwAPI.CANDLE_INTERVAL_DAY,
            )
            candles = r.get("candles") if isinstance(r, dict) else r
            if not candles:
                continue
            # candle rows: [epoch, open, high, low, close, volume]
            df = pd.DataFrame(candles, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
            df["Date"] = pd.to_datetime(df["ts"], unit="s")
            df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
            if len(df) >= min_bars:
                out[t] = df
        except Exception:
            continue
    if verbose:
        print(f"    groww: {len(out)}/{len(tickers)} fetched")
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Groww data source probe")
    ap.add_argument("--instruments", action="store_true")
    ap.add_argument("--ohlc", nargs="*", default=["RELIANCE"])
    a = ap.parse_args()
    tok = _access_token()
    print("auth:", "OK (token minted)" if tok else "no keys / SDK")
    if a.instruments:
        inst = instruments()
        print(f"instruments: {len(inst)} rows")
    h = fetch(a.ohlc, period="1mo", min_bars=1)
    print(f"ohlc: {len(h)}/{len(a.ohlc)} fetched (403 → 0 until market-data enabled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
