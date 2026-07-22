# warm_india_cache.py
# ===================
# Warm the 5-year Parquet cache for the FULL NSE + BSE universe.
#
#   NSE: all EQ symbols via nsepython (~2,372) → .NS suffix
#   BSE: all equity symbols via BSE bhavcopy, BSE-ONLY (not on NSE) → .BO suffix
#
# Dual-listed stocks use NSE (.NS) — more liquid, cleaner data. Only stocks
# unique to BSE get fetched with .BO so we don't duplicate.
#
# Run once (~20-30 min). Subsequent scans read instantly from Parquet.

import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

import pandas as pd

from market_data_cache import warm_cache

# ── NSE symbols ───────────────────────────────────────────────────────────────
print("Fetching NSE EQ universe …")
from nsepython import nse_eq_symbols

nse_syms = set(nse_eq_symbols())
print(f"  NSE EQ: {len(nse_syms)}")

# ── BSE symbols (BSE-only) ────────────────────────────────────────────────────
print("Fetching BSE equity universe …")
bse_syms = set()
try:
    from bse import BSE

    b = BSE(download_folder="/tmp")
    today = datetime.today()
    for off in range(7):
        d = today - timedelta(days=off)
        try:
            path = b.bhavcopyReport(d)
            if hasattr(path, "exists") and path.exists():
                df = pd.read_csv(path)
                # Keep tradeable equity groups (exclude debt/illiquid Z-penny if desired)
                eq_groups = {"A", "B", "T", "X", "XT", "M", "MT", "E"}
                if "SctySrs" in df.columns:
                    df = df[df["SctySrs"].isin(eq_groups)]
                bse_syms = set(df["TckrSymb"].dropna().str.strip().tolist())
                print(f"  BSE equity ({d.date()}): {len(bse_syms)}")
                break
        except Exception:
            continue
except Exception as e:
    print(f"  BSE fetch failed: {e}")

bse_only = sorted(bse_syms - nse_syms)
print(f"  BSE-only (not on NSE): {len(bse_only)}")

# ── Build ticker list ─────────────────────────────────────────────────────────
tickers = [f"{s}.NS" for s in sorted(nse_syms)]
tickers += [f"{s}.BO" for s in bse_only]
print(
    f"\nTotal tickers to warm: {len(tickers)} " f"({len(nse_syms)} NSE + {len(bse_only)} BSE-only)"
)

# ── Warm cache (5-year OHLC) ──────────────────────────────────────────────────
warm_cache(tickers, period_years=5, index_tickers=["^NSEI", "^BSESN"])
print("\n✅ India cache warming complete.")
