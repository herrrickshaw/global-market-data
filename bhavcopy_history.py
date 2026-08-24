#!/usr/bin/env python3
# bhavcopy_history.py
# ===================
# Build ~1-year daily OHLCV history for the full NSE + BSE equity universe from
# the exchanges' OFFICIAL end-of-day bhavcopy files — no Yahoo Finance, so no
# rate limiting. Each daily bhavcopy is one CSV of every traded instrument; we
# download the last N calendar days (skipping weekends/holidays/missing), cache
# each raw file, then pivot into {symbol: OHLCV DataFrame} ready for the
# Darvas / Golden-Cross / volume screeners.
#
# Data sources (both use the 2025+ unified "F_0000" schema):
#   NSE  — via the `nse` library: NSE.equityBhavcopy(datetime)
#   BSE  — direct: BhavCopy_BSE_CM_0_0_0_<YYYYMMDD>_F_0000.CSV
#
# Unified columns used: TradDt, TckrSymb, SctySrs, FinInstrmTp,
#                       OpnPric, HghPric, LwPric, ClsPric, TtlTradgVol
#
# Caches raw CSVs under  ~/Downloads/data/bhavcopy_cache/{nse,bse}/<date>.csv
# so re-runs only fetch the newest missing day.

from __future__ import annotations

import datetime as _dt
import io
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

warnings.filterwarnings("ignore")

try:
    from stock_utils import clean_ohlcv
except ImportError:
    clean_ohlcv = None

import os

# 🔴 The default was ~/Downloads/data/bhavcopy_cache until 2026-08-24 — the dead
# pre-migration tree, whose nse/ and bse/ directories still exist and are EMPTY.
# Every other consumer of BHAV_CACHE (ohlcv_cache, bhavcopy_store, screener_kit,
# liquidity, data_catalog, bhavcopy_raw_archive) already defaults to the live
# path below, which holds 296 NSE and 298 BSE day-CSVs. This module was the last
# straggler, so it silently saw an empty cache and re-fetched everything.
# The mkdir on the next lines is what kept it quiet: it CREATES the empty dirs
# rather than failing on a missing one.
CACHE = Path(os.environ.get(
    "BHAV_CACHE", Path.home() / "market-pipeline" / "data" / "bhavcopy_cache"))
(NSE_DIR := CACHE / "nse").mkdir(parents=True, exist_ok=True)
(BSE_DIR := CACHE / "bse").mkdir(parents=True, exist_ok=True)
ASSEMBLED = CACHE / "assembled_long.parquet"  # consolidated raw (Date,Symbol,OHLCV)
CLEANED = CACHE / "cleaned_long.parquet"  # post-clean_ohlcv long frame (fast path)
NODATA_FILE = CACHE / "no_data_dates.json"  # negative cache: holidays / unpublished

OHLC_MAP = {
    "OpnPric": "Open",
    "HghPric": "High",
    "LwPric": "Low",
    "ClsPric": "Close",
    "TtlTradgVol": "Volume",
}
# ── equity filters ────────────────────────────────────────────────────────────
# NSE equity is SctySrs == "EQ" and always was.
#
# 🔴 BSE was filtered on FinInstrmTp == "STK" until 2026-08-24, and that is a
# NO-OP: every BSE bhavcopy row carries FinInstrmTp "STK", including corporate
# bonds, government securities, gold ETFs and InvITs. Measured over the full
# staging table, all 17 BSE groups are "STK".
#
# The consequence was invisible for a long time because every screen requires
# ~200 bars for a moving average and thinly-traded debt never reaches it, so the
# junk was silently dropped at screen time rather than at ingest. What it did
# corrupt: the India panel carried 7,507 symbols against a real equity universe
# of ~2,681, the median symbol had 29 sessions out of 2,625, and liquidity
# quintiles — which ARE computed over the whole eligible population — were being
# ranked against a mix that included bonds.
#
# The giveaway names: 0ANSPL28, 1003IIFL29, 1003NHF30 are group F, where the
# numeric prefix is the coupon and the suffix the maturity year. 1018GS2026 is
# group G, a government security.
#
# ALLOWLIST, not a denylist. A BSE group code that appears later and is not
# listed here is EXCLUDED and logged, so a new instrument class shows up as a
# coverage change we notice rather than as silent pollution. The repo's standing
# rule on instrument identity is the same: allowlist, never infer from the shape
# of the ticker.
BSE_EQUITY_GROUPS = frozenset({
    "A", "B", "T", "Z",        # main board; T = trade-to-trade, Z = non-compliant
    "X", "XT",                 # X group; XT = its trade-to-trade counterpart
    "M", "MT", "MS", "TS",     # SME board and its trade-to-trade variants
    "ZP", "P",                 # Z-permitted and permitted securities
})
# Deliberately excluded, with what each one is:
#   F   corporate bonds / debentures      (2,736 symbols — the largest group)
#   G   government securities             (694)
#   E   ETFs, mostly gold                 (45)   — and the liquidity gate
#                                                  already treats ETFs as
#                                                  non-picks (LIQUIDSBI)
#   IF  InvITs / REITs                    (25)
#   R   rights entitlements, "-RE" suffix (143)  — temporary instruments
BSE_NON_EQUITY_GROUPS = frozenset({"F", "G", "E", "IF", "R"})

_seen_unknown_groups: set = set()

# ── ISIN is the real discriminator, not the exchange's own group code ─────────
# The group allowlist above is an APPROXIMATION and it leaks in both directions:
#   * group B (main equity board) carries 08ABB / 08AGG / 08DPD, which are
#     Nippon India Mutual Fund units, ISIN INF204KB17R6
#   * groups F and IF carry some INE-prefixed rows
#   * NSE's own SctySrs == "EQ" filter still admits 356 INF mutual-fund symbols
#
# Indian ISINs encode the instrument type POSITIONALLY, which is why "the ISIN
# prefix is the instrument type" is a standing rule in this repo. Measured over
# the full BSE staging table:
#
#   INE + "01"  5,006 syms  EQUITY                    <- the only thing we want
#   INE + "07"  2,263       debentures
#   INE + "08"    327       bonds        (0ANSPL28)
#   INE + "A7"     42       bonds        (1015ECL29)
#   INE + "20"    143       rights       (3IINFO-RE)
#   INF + 01/A1/B1/C1  301  mutual funds and ETFs
#   IN0 + 5X/5Y/5Z     132  T-bills      (91TB010825, 364T020426)
#
# So: keep ISIN starting "INE" with characters 8-9 == "01". The group allowlist
# is retained only as a fallback for rows with no usable ISIN.
EQUITY_ISIN_PREFIX = "INE"
EQUITY_ISIN_TYPE = "01"      # characters 8-9, 1-indexed


def _isin_is_equity(isin: pd.Series) -> pd.Series:
    s = isin.astype(str).str.strip().str.upper()
    return (s.str.len() == 12) & s.str.startswith(EQUITY_ISIN_PREFIX) & \
        (s.str.slice(7, 9) == EQUITY_ISIN_TYPE)


def _equity_only(df: pd.DataFrame, exchange: str) -> pd.DataFrame:
    """Keep common-equity rows. Applied to cached CSVs too — see _bse_day."""
    if df is None or df.empty:
        return df

    if "ISIN" in df.columns:
        ok = _isin_is_equity(df["ISIN"])
        # Rows with no parseable ISIN fall back to the exchange's group code so
        # a missing identifier drops a name rather than admitting an unknown
        # instrument: EQ for NSE, the allowlist for BSE.
        blank = df["ISIN"].isna() | (df["ISIN"].astype(str).str.strip() == "")
        if blank.any() and "SctySrs" in df.columns:
            grp = df["SctySrs"].astype(str).str.strip()
            fallback = grp.eq("EQ") if exchange == "NSE" else \
                grp.isin(BSE_EQUITY_GROUPS)
            ok = ok | (blank & fallback)
        return df[ok]

    if "SctySrs" not in df.columns:
        return df
    grp = df["SctySrs"].astype(str).str.strip()
    if exchange == "NSE":
        return df[grp == "EQ"]
    unknown = set(grp.unique()) - BSE_EQUITY_GROUPS - BSE_NON_EQUITY_GROUPS
    for g in sorted(unknown - _seen_unknown_groups):
        _seen_unknown_groups.add(g)
        print(f"  [bse] unknown security group {g!r} — EXCLUDED. Add it to "
              f"BSE_EQUITY_GROUPS if it is equity.")
    return df[grp.isin(BSE_EQUITY_GROUPS)]


def _bse_equity_only(df: pd.DataFrame) -> pd.DataFrame:
    return _equity_only(df, "BSE")


# ── per-day download (cached) ──────────────────────────────────────────────────
def _nse_day(day: _dt.date, nse_client) -> Optional[pd.DataFrame]:
    f = NSE_DIR / f"{day:%Y%m%d}.csv"
    if f.exists():
        # NOT merely idempotent: nse_raw is 100% SctySrs "EQ" yet still contains
        # 356 INF mutual-fund symbols, so the ISIN test below removes names the
        # download-time EQ filter admitted.
        return _equity_only(pd.read_csv(f), "NSE")
    try:
        p = nse_client.equityBhavcopy(date=_dt.datetime(day.year, day.month, day.day))
        df = pd.read_csv(p)
        df.columns = [c.strip() for c in df.columns]
        df = _equity_only(df, "NSE")
        df.to_csv(f, index=False)
        try:
            Path(p).unlink()
        except OSError:
            pass
        return df
    except Exception:
        return None


def _bse_day(day: _dt.date, sess: requests.Session) -> Optional[pd.DataFrame]:
    f = BSE_DIR / f"{day:%Y%m%d}.csv"
    if f.exists():
        # Filter on READ, not only before caching. Every CSV cached before
        # 2026-08-24 was written through the no-op FinInstrmTp filter and still
        # holds bonds and G-Secs; filtering only at download time would leave
        # the entire cached history polluted and fix nothing retroactively.
        return _bse_equity_only(pd.read_csv(f))
    url = (
        "https://www.bseindia.com/download/BhavCopy/Equity/"
        f"BhavCopy_BSE_CM_0_0_0_{day:%Y%m%d}_F_0000.CSV"
    )
    try:
        r = sess.get(url, timeout=25)
        if r.status_code != 200 or len(r.content) < 1000:
            return None
        df = pd.read_csv(io.BytesIO(r.content))
        df.columns = [c.strip() for c in df.columns]
        df = _bse_equity_only(df)
        df.to_csv(f, index=False)
        return df
    except Exception:
        return None


# ── public API ─────────────────────────────────────────────────────────────────
def fetch_history(
    n_days: int = 400,
    workers: int = 8,
    exchanges=("NSE", "BSE"),
    min_bars: int = 60,
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Assemble {symbol: cleaned OHLCV DataFrame} from NSE+BSE bhavcopy.

    n_days = calendar days to look back (~400 ≈ 1 trading year after weekends/
    holidays). NSE symbols keyed bare (RELIANCE); BSE-only keyed with a .BO-style
    suffix is NOT applied here — keys are bare tickers, NSE taking precedence on
    collision (same convention as stock_utils._strip_suffix).
    """
    today = _dt.date.today()
    cal = [today - _dt.timedelta(days=i) for i in range(n_days)]
    # keep only actual trading days (skip weekends AND NSE/BSE holidays)
    try:
        from market_calendar import trading_days as _trading_days

        days = _trading_days(cal)
    except Exception:
        days = [d for d in cal if d.weekday() < 5]  # fallback: weekdays only
    want_dates = {pd.Timestamp(d) for d in days}

    # ── negative cache: dates already confirmed to have NO bhavcopy ─────────────
    # (holidays / not-yet-published). Skipping these avoids re-hitting the network
    # for the same dead dates on every run.
    import json

    no_data = set()
    if NODATA_FILE.exists():
        try:
            no_data = {pd.Timestamp(s) for s in json.loads(NODATA_FILE.read_text())}
        except Exception:
            no_data = set()

    # ── FAST PATH: cleaned-result cache ────────────────────────────────────────
    # If we already produced cleaned per-symbol data and there are no genuinely
    # new trading dates to add, skip the whole pivot+clean (the ~30s cost) and
    # serve the cleaned long parquet directly.
    def _from_cleaned(min_bars):
        from frames import long_to_dict

        cl = pd.read_parquet(CLEANED)
        cl["Date"] = pd.to_datetime(cl["Date"])
        return long_to_dict(cl[cl["Date"].isin(want_dates)], min_bars)

    # ── load the assembled long-format cache (the speed layer) ─────────────────
    # ASSEMBLED holds every (Date, Symbol, OHLCV, _exch) row already collected, so
    # re-runs skip re-reading every daily CSV. We only fetch dates NOT already in
    # it, append, and re-save. Cuts a warm run from re-pivoting 500+ files to a
    # single parquet read + the 1-2 new trading days.
    cached = pd.DataFrame()
    if ASSEMBLED.exists():
        try:
            cached = pd.read_parquet(ASSEMBLED)
            cached["Date"] = pd.to_datetime(cached["Date"])
        except Exception:
            cached = pd.DataFrame()
    elif CLEANED.exists():
        # fresh clone: only the committed cleaned cache is present. Seed the
        # assembled frame from it (NSE precedence already resolved) so incremental
        # date fetches work without re-downloading the whole year.
        try:
            cached = pd.read_parquet(CLEANED)
            cached["Date"] = pd.to_datetime(cached["Date"])
            if "_exch" not in cached.columns:
                cached["_exch"] = "NSE"
            cached.to_parquet(ASSEMBLED, compression="zstd", index=False)
            if verbose:
                print("  seeded assembled cache from committed cleaned_long.parquet")
        except Exception:
            cached = pd.DataFrame()
    have_dates = set(pd.to_datetime(cached["Date"].unique())) if not cached.empty else set()
    missing = sorted(want_dates - have_dates - no_data)  # skip known-dead dates
    if verbose:
        print(
            f"  assembled cache: {len(have_dates)} dates known, "
            f"{len(no_data)} dead dates skipped, {len(missing)} new date(s) to fetch"
        )

    # FAST PATH: nothing new to fetch and a cleaned cache exists → serve it
    if not missing and CLEANED.exists():
        try:
            out = _from_cleaned(min_bars)
            if verbose:
                print(f"  ⚡ fast path (cleaned cache): {len(out)} symbols, no fetch/clean")
            return out
        except Exception as e:
            if verbose:
                print(f"  (cleaned cache unusable, rebuilding: {e})")

    frames = []  # (exchange, day, df) for the missing days only

    if missing and "NSE" in exchanges:
        from nse import NSE

        nse_client = NSE(download_folder=str(NSE_DIR))
        ok = 0
        for ts in missing:
            d = ts.date()
            df = _nse_day(d, nse_client)
            if df is not None and len(df):
                frames.append(("NSE", d, df))
                ok += 1
        try:
            nse_client.exit()
        except Exception:
            pass
        if verbose:
            print(f"    NSE: +{ok} new trading days")

    if missing and "BSE" in exchanges:
        sess = requests.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"})
        ok = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_bse_day, ts.date(), sess): ts for ts in missing}
            for fut in as_completed(futs):
                df = fut.result()
                ts = futs[fut]
                if df is not None and len(df):
                    frames.append(("BSE", ts.date(), df))
                    ok += 1
        if verbose:
            print(f"    BSE: +{ok} new trading days")

    # normalise the newly-fetched daily files to long format
    new_rows = []
    got_dates = {pd.Timestamp(d) for _, d, _ in frames}
    for exch, d, df in frames:
        keep = df[["TckrSymb"] + list(OHLC_MAP)].copy().rename(columns=OHLC_MAP)
        keep["Symbol"] = keep.pop("TckrSymb").astype(str)
        keep["Date"] = pd.Timestamp(d)
        keep["_exch"] = exch
        new_rows.append(keep)

    # record dates we asked for but got nothing back as dead (holiday/unpublished),
    # except today (still publishing) — so we never re-fetch them.
    dead = {ts for ts in missing if ts not in got_dates and ts.date() != today}
    if dead:
        no_data |= dead
        try:
            NODATA_FILE.write_text(json.dumps(sorted(d.strftime("%Y-%m-%d") for d in no_data)))
        except Exception:
            pass

    # POST-FETCH FAST PATH: the only "missing" date was today (not yet published)
    # and nothing new was actually downloaded → serve the cleaned cache, skip the
    # ~30s pivot+clean. This keeps intraday re-runs cheap until today's EOD lands.
    if not new_rows and CLEANED.exists():
        try:
            out = _from_cleaned(min_bars)
            if verbose:
                print(f"  ⚡ fast path (no new EOD yet): {len(out)} symbols, skipped rebuild")
            return out
        except Exception:
            pass

    # merge cache + new, persist the updated assembled parquet
    parts = [p for p in (cached, *new_rows) if p is not None and not p.empty]
    if not parts:
        return {}
    allrows = pd.concat(parts, ignore_index=True)
    allrows = allrows.drop_duplicates(subset=["Date", "_exch", "Symbol"], keep="last")
    if new_rows:
        try:
            allrows.to_parquet(ASSEMBLED, compression="snappy", index=False)
            if verbose:
                print(
                    f"  assembled cache updated → {ASSEMBLED.name} "
                    f"({len(allrows):,} rows, {allrows['Date'].nunique()} dates)"
                )
        except Exception as e:
            if verbose:
                print(f"  (could not persist assembled cache: {e})")

    # restrict to the requested window before pivoting
    allrows = allrows[allrows["Date"].isin(want_dates)]

    # ── pivot + clean to per-symbol OHLCV (built once at min_bars=1, then cached) ─
    if verbose:
        print(f"  Pivoting {allrows['Date'].nunique()} dates → per-symbol series …")
    allrows = allrows.rename(columns={"Symbol": "TckrSymb"})
    out_all: Dict[str, pd.DataFrame] = {}
    # NSE precedence: process NSE group last so it overwrites BSE-only collisions
    for exch in ("BSE", "NSE"):
        sub = allrows[allrows["_exch"] == exch]
        for sym, g in sub.groupby("TckrSymb"):
            g = g.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]
            if clean_ohlcv is not None:
                g = clean_ohlcv(g, ticker=str(sym), min_bars=1)
            if g is not None and len(g):
                out_all[str(sym)] = g

    # persist the cleaned long frame so the next run hits the fast path
    try:
        parts = []
        for sym, g in out_all.items():
            gg = g.reset_index()
            gg.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
            gg["Symbol"] = sym
            parts.append(gg)
        if parts:
            pd.concat(parts, ignore_index=True).to_parquet(
                CLEANED, compression="snappy", index=False
            )
            if verbose:
                print(f"  cleaned cache written → {CLEANED.name}")
    except Exception as e:
        if verbose:
            print(f"  (could not persist cleaned cache: {e})")

    # sync the LMDB NoSQL store (fast keyed retrieval layer) with the fresh data
    try:
        from bhavcopy_store import build as _build_store

        _build_store(out_all, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"  (LMDB store not synced: {e})")

    out = {s: g for s, g in out_all.items() if len(g) >= min_bars}
    if verbose:
        print(f"  fetch_history complete: {len(out)} symbols with ≥{min_bars} bars")
    return out


def get_symbol(symbol: str, n_days: int = 400) -> Optional[pd.DataFrame]:
    """Quick single-symbol retrieval straight from the assembled cache (no fetch).

    Returns a cleaned OHLCV frame for one ticker if it is already in the cache,
    else None. Use fetch_history() first to populate/refresh the cache.
    """
    if not ASSEMBLED.exists():
        return None
    df = pd.read_parquet(ASSEMBLED)
    df = df[df["Symbol"].astype(str) == symbol]
    if df.empty:
        return None
    df["Date"] = pd.to_datetime(df["Date"])
    cutoff = pd.Timestamp(_dt.date.today() - _dt.timedelta(days=n_days))
    df = df[df["Date"] >= cutoff]
    # NSE precedence on collisions
    df = df.sort_values("_exch")  # BSE < NSE alphabetically → NSE rows last
    g = df.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]
    g = g[~g.index.duplicated(keep="last")]
    return clean_ohlcv(g, ticker=symbol, min_bars=1) if clean_ohlcv else g


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    h = fetch_history(n_days=n)
    print(f"\nSymbols: {len(h)}")
    for s in list(h)[:3]:
        df = h[s]
        print(
            f"  {s}: {len(df)} bars, {df.index[0].date()}→{df.index[-1].date()}, "
            f"last close ₹{df['Close'].iloc[-1]:.1f}"
        )
