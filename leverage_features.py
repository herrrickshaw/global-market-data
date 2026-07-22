#!/usr/bin/env python3
# leverage_features.py
# ====================
# Point-in-time-ish leverage features for the supervised model, so it can learn
# how forward returns relate to a company's debt profile — i.e. whether the
# market rewards or punishes debt-driven balance sheets.
#
# Source: the cached fundamentals frame (fundamental_metrics → cache_seed/
# fundamentals/<MKT>.parquet), which carries debt_to_equity, debt_to_assets,
# debt_to_assets_prev and a debt_history series.
#
# CAVEAT: fundamentals are the latest snapshot, not a full historical panel, so
# merging them onto historical as-of points is an approximation (mild lookahead
# on the leverage columns). Documented in ADR-18. Technical features remain
# strictly point-in-time. Coverage is thin (only cached symbols); everything else
# is NaN, which the gradient-boosted model handles natively.
#
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent / "cache_seed" / "fundamentals"

# Feature columns contributed to the supervised model.
LEV_FEATURES: List[str] = ["lev_de", "lev_da", "lev_da_delta", "lev_debt_trend"]


def _debt_trend(hist) -> float:
    """Normalised slope of the debt_history series (last vs first, scaled)."""
    try:
        vals = [float(x) for x in (hist if isinstance(hist, (list, tuple, np.ndarray)) else [])]
        vals = [v for v in vals if np.isfinite(v)]
        if len(vals) < 2 or vals[0] == 0:
            return np.nan
        return (vals[-1] - vals[0]) / abs(vals[0])
    except Exception:
        return np.nan


def leverage_frame(market: str) -> pd.DataFrame:
    """Per-symbol leverage features. Empty frame if no fundamentals cached."""
    path = CACHE / f"{market}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["Symbol", *LEV_FEATURES])
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame(columns=["Symbol", *LEV_FEATURES])
    if df.empty or "Symbol" not in df.columns:
        return pd.DataFrame(columns=["Symbol", *LEV_FEATURES])

    out = pd.DataFrame({"Symbol": df["Symbol"]})
    out["lev_de"] = pd.to_numeric(df.get("debt_to_equity"), errors="coerce")
    da = pd.to_numeric(df.get("debt_to_assets"), errors="coerce")
    da_prev = pd.to_numeric(df.get("debt_to_assets_prev"), errors="coerce")
    out["lev_da"] = da
    out["lev_da_delta"] = da - da_prev  # >0 == rising leverage
    if "debt_history" in df.columns:
        out["lev_debt_trend"] = df["debt_history"].map(_debt_trend)
    else:
        out["lev_debt_trend"] = np.nan
    return out.drop_duplicates("Symbol", keep="last").reset_index(drop=True)


def merge_leverage(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    """Left-merge leverage features onto a frame that has a 'Symbol' column."""
    lev = leverage_frame(market)
    if lev.empty or "Symbol" not in frame.columns:
        for c in LEV_FEATURES:
            if c not in frame.columns:
                frame[c] = np.nan
        return frame
    return frame.merge(lev, on="Symbol", how="left")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Leverage features from cached fundamentals")
    ap.add_argument("--market", default="IN")
    a = ap.parse_args()
    lev = leverage_frame(a.market)
    print(f"{a.market}: {len(lev)} symbols with leverage features")
    if not lev.empty:
        print(lev.head(20).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
