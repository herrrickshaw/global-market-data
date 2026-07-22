#!/usr/bin/env python3
# correlation_analysis.py  —  Roadmap Phase 3
# ============================================
# Do fundamental metrics (ROE, ROCE, leverage, FCF …) actually correlate with
# realised price performance over the deep LTM window? For each symbol we compute
# performance features from the 10y LTM (CAGR, 12m momentum, Sharpe, max drawdown,
# volatility) and Spearman-correlate each cached fundamental against them, with a
# significance test (p-value). Answers "which fundamentals move with returns".
#
#   python3 correlation_analysis.py --market IN --target cagr
#
# Reads only local data (LTM + fundamentals cache); coverage is limited to the
# symbols with cached fundamentals. ⚠️ Research/education only. Not advice.

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import market_memory as mm

FUND_DIR = Path(__file__).parent / "cache_seed" / "fundamentals"
PERF = ["cagr", "mom_12m", "sharpe", "max_drawdown", "vol"]


def _perf_features(market: str) -> pd.DataFrame:
    """Realised performance per symbol from the LTM."""
    data = mm.read(market, tier="ltm")
    rows = []
    for sym, df in data.items():
        if df is None or len(df) < 120:
            continue
        c = df["Close"].to_numpy("float64")
        if c[0] <= 0:
            continue
        rets = np.diff(c) / c[:-1]
        yrs = len(c) / 252.0
        cagr = (c[-1] / c[0]) ** (1 / yrs) - 1 if yrs > 0 and c[0] > 0 else np.nan
        mom = c[-1] / c[-252] - 1 if len(c) > 252 and c[-252] > 0 else np.nan
        sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else np.nan
        peak = np.maximum.accumulate(c)
        mdd = float(((c - peak) / peak).min())
        rows.append(
            {
                "Symbol": sym,
                "cagr": cagr,
                "mom_12m": mom,
                "sharpe": sharpe,
                "max_drawdown": mdd,
                "vol": rets.std() * np.sqrt(252),
            }
        )
    return pd.DataFrame(rows)


def _spearman(x: pd.Series, y: pd.Series):
    """Spearman rho + p-value (SciPy if present, else numpy rank-corr, p=NaN)."""
    m = x.notna() & y.notna()
    if m.sum() < 5:
        return np.nan, np.nan, int(m.sum())
    try:
        from scipy.stats import spearmanr

        rho, p = spearmanr(x[m], y[m])
        return float(rho), float(p), int(m.sum())
    except Exception:
        rx, ry = x[m].rank(), y[m].rank()
        rho = float(np.corrcoef(rx, ry)[0, 1])
        return rho, np.nan, int(m.sum())


def analyse(market: str, target: str = "cagr") -> pd.DataFrame:
    """Correlate each numeric fundamental against a performance target."""
    fpath = FUND_DIR / f"{market}.parquet"
    if not fpath.exists():
        return pd.DataFrame()
    fund = pd.read_parquet(fpath)
    perf = _perf_features(market)
    if fund.empty or perf.empty:
        return pd.DataFrame()
    merged = perf.merge(fund, on="Symbol", how="inner")
    if merged.empty:
        return pd.DataFrame()
    y = merged[target]
    metric_cols = [
        c
        for c in fund.columns
        if c != "Symbol" and pd.api.types.is_numeric_dtype(merged[c]) and c not in PERF
    ]
    out = []
    for c in metric_cols:
        rho, p, n = _spearman(merged[c], y)
        if not np.isnan(rho):
            out.append({"metric": c, "spearman_rho": round(rho, 3), "p_value": p, "n": n})
    res = pd.DataFrame(out)
    if res.empty:
        return res
    res["abs_rho"] = res["spearman_rho"].abs()
    res["significant"] = res["p_value"].apply(lambda p: bool(p == p and p < 0.05))
    return (
        res.sort_values("abs_rho", ascending=False).drop(columns="abs_rho").reset_index(drop=True)
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Phase 3 — fundamentals ↔ price-performance correlation"
    )
    ap.add_argument("--market", default="IN")
    ap.add_argument("--target", default="cagr", choices=PERF)
    a = ap.parse_args()
    res = analyse(a.market, a.target)
    if res.empty:
        print(f"{a.market}: no overlap between fundamentals cache and LTM performance")
        return 0
    print(f"\n{a.market} — fundamentals vs {a.target} (Spearman, deep LTM):")
    print(res.to_string(index=False))
    sig = res[res["significant"]]
    print(f"\n  {len(sig)} metric(s) significant at p<0.05 (n up to {int(res['n'].max())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
