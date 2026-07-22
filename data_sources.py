#!/usr/bin/env python3
# data_sources.py
# ===============
# Redundant OHLC sourcing with an ordered fallback chain, so a Yahoo Finance
# rate-limit (429 / "Invalid Crumb") no longer breaks data collection.
#
# Default chain is Yahoo-only: a live run showed Stooq filling 0/9278 (it HTML/
# rate-limits bulk EOD), so it added latency without coverage. Yahoo carried 100%.
# Stooq and the optional keyed EODHD provider remain available — pass e.g.
# order=("yahoo", "eodhd") or ("stooq", "yahoo") to re-enable fallbacks.
#   - Yahoo   — yfinance bulk download                                    [default]
#   - EODHD   — optional, one key (EODHD_API_KEY); 20+ exchanges          [opt-in]
#   - Stooq   — free, no key, global EOD CSV                              [opt-in]
#   (India is sourced separately from official NSE/BSE bhavcopy — see
#    bhavcopy_history.py — and does not go through this chain.)
#
# fetch() runs the requested order, collects misses after each source, and tries
# the next source only for the still-missing tickers. Returns {ticker: OHLCV df}.
#
# Government/official EOD-by-exchange endpoints (NSE/BSE bhavcopy, SGX, KRX, JPX)
# are the most reliable primaries; this module focuses on the cross-market price
# backups that need no API key. Add keyed providers (Tiingo/AlphaVantage) below.

from __future__ import annotations

import io
import warnings
from typing import Dict, List

import pandas as pd
import requests

warnings.filterwarnings("ignore")

try:
    from stock_utils import bulk_download as _yahoo_bulk
    from stock_utils import clean_ohlcv
except ImportError:
    _yahoo_bulk = None
    clean_ohlcv = None

_UA = {"User-Agent": "Mozilla/5.0 (market-research)"}


# ── Stooq (free, no key) ───────────────────────────────────────────────────────
def _stooq_symbol(t: str) -> str | None:
    """Map a yfinance ticker to a Stooq symbol (covers the markets Stooq carries)."""
    suffix_map = {
        ".T": ".jp",
        ".L": ".uk",
        ".DE": ".de",
        ".F": ".de",
        ".PA": ".fr",
        ".AS": ".nl",
        ".BR": ".be",
        ".MI": ".it",
        ".MC": ".es",
        ".HK": ".hk",
        ".SW": ".ch",
        ".ST": ".se",
    }
    for yf_suf, st_suf in suffix_map.items():
        if t.endswith(yf_suf):
            return t[: -len(yf_suf)].lower() + st_suf
    if "." not in t:  # bare ticker → US
        return t.lower() + ".us"
    return None  # unsupported on Stooq (e.g. .NS/.SI/.SS/.KS)


def stooq_fetch(
    tickers: List[str], min_bars: int = 60, verbose: bool = True
) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    sess = requests.Session()
    sess.headers.update(_UA)
    for t in tickers:
        s = _stooq_symbol(t)
        if not s:
            continue
        try:
            r = sess.get(f"https://stooq.com/q/d/l/?s={s}&i=d", timeout=20)
            if r.status_code != 200 or not r.text.startswith("Date"):
                continue  # HTML / limit page → treat as miss
            df = pd.read_csv(io.StringIO(r.text))
            if df.empty or "Close" not in df.columns:
                continue
            df = df.rename(columns=str.capitalize)
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
            if clean_ohlcv is not None:
                df = clean_ohlcv(df, ticker=t, min_bars=min_bars)
            if df is not None and len(df) >= min_bars:
                out[t] = df
        except Exception:
            continue
    if verbose:
        print(f"    stooq: {len(out)}/{len(tickers)} fetched")
    return out


# ── EODHD (optional key: $0 if unset) ───────────────────────────────────────────
# One optional paid source that reliably covers 20+ exchanges + fundamentals.
# Dormant unless EODHD_API_KEY is set, so the default stack stays free/keyless.
_EODHD_EXCH = {
    ".T": "TSE",
    ".L": "LSE",
    ".DE": "XETRA",
    ".F": "XETRA",
    ".PA": "PA",
    ".AS": "AS",
    ".BR": "BR",
    ".MI": "MI",
    ".MC": "MC",
    ".HK": "HK",
    ".SW": "SW",
    ".ST": "ST",
    ".KS": "KO",
    ".SS": "SHG",
    ".SZ": "SHE",
    ".NS": "NSE",
    ".BO": "BSE",
    ".SI": "SG",
    ".AX": "AU",
    ".TO": "TO",
    ".SA": "SA",
}


def _eodhd_symbol(t: str) -> str | None:
    for yf_suf, ex in _EODHD_EXCH.items():
        if t.endswith(yf_suf):
            return t[: -len(yf_suf)] + "." + ex
    if "." not in t:  # bare ticker → US
        return t + ".US"
    return None


def _period_to_from(period: str) -> str | None:
    """Map a yfinance-style period to an EODHD `from` date (None = full history)."""
    import datetime as _dt
    import re

    m = re.fullmatch(r"(\d+)(d|mo|y)", period or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    days = {"d": 1, "mo": 31, "y": 366}[unit] * n + 5  # pad for weekends/holidays
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


def eodhd_fetch(
    tickers: List[str], period: str = "1y", min_bars: int = 60, verbose: bool = True
) -> Dict[str, pd.DataFrame]:
    import os

    key = os.environ.get("EODHD_API_KEY", "")
    if not key:
        return {}  # dormant when unkeyed — keeps the stack $0
    out: Dict[str, pd.DataFrame] = {}
    frm = _period_to_from(period)
    sess = requests.Session()
    sess.headers.update(_UA)
    for t in tickers:
        s = _eodhd_symbol(t)
        if not s:
            continue
        try:
            params = {"api_token": key, "fmt": "csv", "period": "d"}
            if frm:
                params["from"] = frm
            r = sess.get(f"https://eodhd.com/api/eod/{s}", params=params, timeout=20)
            if r.status_code != 200 or not r.text.startswith("Date"):
                continue
            df = pd.read_csv(io.StringIO(r.text))
            if df.empty or "Close" not in df.columns:
                continue
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
            if clean_ohlcv is not None:
                df = clean_ohlcv(df, ticker=t, min_bars=min_bars)
            if df is not None and len(df) >= min_bars:
                out[t] = df
        except Exception:
            continue
    if verbose:
        print(f"    eodhd: {len(out)}/{len(tickers)} fetched")
    return out


# ── Yahoo ──────────────────────────────────────────────────────────────────────
def yahoo_fetch(
    tickers: List[str], period: str = "1y", min_bars: int = 60, verbose: bool = True
) -> Dict[str, pd.DataFrame]:
    if _yahoo_bulk is None:
        return {}
    return _yahoo_bulk(tickers, period=period, batch_size=80, min_bars=min_bars, verbose=verbose)


def groww_fetch(
    tickers: List[str], period: str = "1y", min_bars: int = 60, verbose: bool = True
) -> Dict[str, pd.DataFrame]:
    """Groww API (India) daily OHLC — opt-in. Dormant without keys / market-data
    entitlement (returns {} on 403). See groww_source.py."""
    try:
        import groww_source

        return groww_source.fetch(tickers, period=period, min_bars=min_bars, verbose=verbose)
    except Exception:
        return {}


SOURCES = {
    "stooq": stooq_fetch,
    "eodhd": eodhd_fetch,
    "yahoo": yahoo_fetch,
    "groww": groww_fetch,
}


def fetch(
    tickers: List[str],
    order=("yahoo",),
    period: str = "1y",
    min_bars: int = 60,
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Fetch OHLC with fallback. Tries sources in `order`; each source only
    handles the tickers still missing after the previous one."""
    result: Dict[str, pd.DataFrame] = {}
    pending = list(dict.fromkeys(tickers))
    per_source: Dict[str, int] = {}
    for src in order:
        if not pending:
            break
        fn = SOURCES.get(src)
        if fn is None:
            continue
        if verbose:
            print(f"  source '{src}': attempting {len(pending)} tickers …")
        kw = dict(min_bars=min_bars, verbose=verbose)
        if src in ("yahoo", "eodhd", "groww"):
            kw["period"] = period
        got = fn(pending, **kw)
        per_source[src] = len(got)
        result.update(got)
        pending = [t for t in pending if t not in result]
    LAST_STATS.clear()
    LAST_STATS.update(
        {"requested": len(set(tickers)), "filled": len(result), "by_source": per_source}
    )
    if verbose:
        print(f"  multi-source total: {len(result)}/{len(tickers)} " f"({len(pending)} unresolved)")
    return result


# Per-run fill stats from the last fetch() — read by the dashboard's source
# fill-rate line. {requested, filled, by_source: {stooq: n, eodhd: n, yahoo: n}}.
LAST_STATS: Dict[str, object] = {}


def get_last_stats() -> Dict[str, object]:
    return dict(LAST_STATS)


if __name__ == "__main__":
    import sys

    ts = sys.argv[1:] or ["AAPL", "MSFT", "7203.T"]
    h = fetch(ts, order=("yahoo", "stooq"), period="6mo", min_bars=30)
    for t, d in h.items():
        print(f"  {t}: {len(d)} bars, last {str(d.index[-1])[:10]} {d['Close'].iloc[-1]:.2f}")
