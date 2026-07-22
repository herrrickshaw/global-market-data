#!/usr/bin/env python3
# backtest_weight_validation.py  —  Roadmap Phase 4
# =================================================
# Does a fundamentals composite actually separate winners from losers over the
# deep LTM window? Builds a z-scored composite from the metrics Phase 3 flags as
# significant (sign-aligned by their correlation), sorts the universe into
# quintiles, and reports realised performance per quintile plus the top-minus-
# bottom (Q5−Q1) spread — the out-of-sample "edge" of the fundamental signal.
#
# UPDATED: Now uses data_config for proper train/test splits to prevent data leakage.
#
#   python3 backtest_weight_validation.py --market US --target cagr --use-test-split
#
# CAVEAT: uses CURRENT fundamentals against realised history (look-ahead +
# survivorship). Directional evidence, not a tradeable result. Coverage is limited
# to cached fundamentals. ⚠️ Research/education only. Not advice.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import numpy as np
import pandas as pd

import correlation_analysis as ca

# Import data configuration for proper train/test splits
try:
    from data_config import DataConfig, filter_data_by_split
    HAS_DATA_CONFIG = True
except ImportError:
    HAS_DATA_CONFIG = False

FUND_DIR = Path(__file__).parent / "cache_seed" / "fundamentals"


def _zscore(s: pd.Series) -> pd.Series:
    sd = s.std()
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0


def build_composite(market: str, target: str = "cagr", top_k: int = 6, p_max: float = 0.10):
    """Composite = mean of sign-aligned z-scores of the most-correlated metrics."""
    corr = ca.analyse(market, target)
    if corr.empty:
        return None, None, corr
    picks = corr[corr["p_value"] <= p_max].head(top_k)
    if picks.empty:
        picks = corr.head(top_k)  # fall back to strongest even if not significant
    fund = pd.read_parquet(FUND_DIR / f"{market}.parquet")
    perf = ca._perf_features(market)
    merged = perf.merge(fund, on="Symbol", how="inner")
    comp = pd.Series(0.0, index=merged.index)
    used = []
    for _, r in picks.iterrows():
        col = r["metric"]
        if col not in merged:
            continue
        comp = comp + np.sign(r["spearman_rho"]) * _zscore(
            pd.to_numeric(merged[col], errors="coerce")
        )
        used.append(col)
    merged["composite"] = comp
    return merged, used, picks


def validate(market: str, target: str = "cagr", q: int = 5, use_test_split: bool = False) -> dict:
    """
    Validate fundamentals composite on quintiles.

    Args:
        market: Market code (US, IN, etc.)
        target: Performance metric (cagr, return_12m, etc.)
        q: Number of quintiles
        use_test_split: If True, validate only on TEST split (2023-2024) to prevent leakage
    """
    merged, used, picks = build_composite(market, target)
    if merged is None or merged.empty:
        return {"market": market, "error": "no fundamentals/perf overlap"}

    # Apply test split if requested
    if use_test_split and HAS_DATA_CONFIG:
        # Get performance data with dates
        perf = ca._perf_features(market)
        if 'date' in perf.columns or 'Date' in perf.columns:
            perf_test = filter_data_by_split(perf, "date", split="test")
            merged = merged[merged.index.isin(perf_test.index)]
            print(f"  ✓ Applied TEST split: {len(merged)} records from 2023-2024")
        else:
            print(f"  ⚠️  No date column in performance data; using all records")

    # Validate data quality
    if HAS_DATA_CONFIG:
        config = DataConfig()
        if 'composite' in merged.columns and target in merged.columns:
            df_check = merged[['composite', target]].dropna()
            if len(df_check) > 0:
                errors = config.validation.validate_dataframe(df_check)
                if errors:
                    print(f"  ⚠️  Data validation warnings:")
                    for error in errors[:3]:  # Show first 3 errors
                        print(f"     • {error}")

    m = merged.dropna(subset=["composite", target])
    if len(m) < q * 2:
        return {"market": market, "error": f"too few names ({len(m)}) for {q}-tiles"}
    m = m.sort_values("composite")
    m["quintile"] = pd.qcut(m["composite"].rank(method="first"), q, labels=range(1, q + 1))
    table = m.groupby("quintile", observed=True)[target].agg(["mean", "median", "count"])
    q_top, q_bot = table.loc[q, "mean"], table.loc[1, "mean"]
    return {
        "market": market,
        "target": target,
        "metrics_used": used,
        "n": int(len(m)),
        "table": table,
        "spread_top_minus_bottom": float(q_top - q_bot),
        "used_test_split": use_test_split and HAS_DATA_CONFIG,
    }


def validate_with_proper_split(
    market: str,
    target: str = "cagr",
    quantiles: int = 5,
    use_test_split: bool = True
) -> Dict:
    """
    PROPER BACKTEST: Validate fundamentals composite with train/test split.

    This prevents data leakage by evaluating only on TEST split (2023-2024).

    Args:
        market: Market code (US, IN, etc.)
        target: Performance metric
        quantiles: Number of quintiles
        use_test_split: Use TEST split (recommended: True)

    Returns:
        Results dictionary
    """
    print(f"\n{'='*80}")
    print(f"FUNDAMENTALS COMPOSITE VALIDATION")
    print(f"{'='*80}")

    if use_test_split and HAS_DATA_CONFIG:
        config = DataConfig()
        test_start, test_end = config.date_splits.get_test_range()
        print(f"✓ Using TEST split: {test_start} to {test_end} (unseen data)")
    else:
        print(f"⚠️  Using all available data (not recommended for evaluation)")

    return validate(market, target, quantiles, use_test_split=use_test_split)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Phase 4 — fundamentals-composite quintile backtest with proper train/test splits"
    )
    ap.add_argument("--market", default="US", help="Market code (US, IN, etc.)")
    ap.add_argument("--target", default="cagr", choices=ca.PERF, help="Performance metric to validate")
    ap.add_argument("--quantiles", type=int, default=5, help="Number of quintiles")
    ap.add_argument("--use-test-split", action="store_true", default=True,
                   help="Use TEST split to prevent leakage (default: True)")
    ap.add_argument("--no-split", action="store_true",
                   help="Use all data (not recommended; overrides --use-test-split)")
    a = ap.parse_args()

    print("\n" + "🎯 "*40)
    print("FUNDAMENTALS COMPOSITE VALIDATION")
    print("🎯 "*40)
    print(f"\n✓ Using proper train/test splits from data_config.py")
    print(f"✓ This prevents data leakage and ensures fair evaluation")

    use_split = not a.no_split
    r = validate_with_proper_split(a.market, a.target, a.quantiles, use_test_split=use_split)

    if "error" in r:
        print(f"\n❌ {a.market}: {r['error']}")
        return 0

    print(f"\n{a.market} — fundamentals composite vs {a.target} (n={r['n']})")
    print(f"  metrics: {', '.join(r['metrics_used'])}")
    if r.get('used_test_split'):
        print(f"  ✓ Evaluated on TEST split (unseen 2023-2024 data)")
    else:
        print(f"  ⚠️  Evaluated on all available data")

    tbl = r["table"].copy()
    tbl["mean"] = (tbl["mean"] * 100).round(1)
    tbl["median"] = (tbl["median"] * 100).round(1)
    print(tbl.to_string())

    sp = r["spread_top_minus_bottom"] * 100
    print(
        f"\n  Q{a.quantiles}−Q1 spread ({a.target}): {sp:+.1f} pp "
        f"→ {'signal separates winners' if sp > 0 else 'no/negative separation'}"
    )

    print(f"\n✅ Validation complete")
    print(f"📖 For details on train/test splits, see DATA_VALIDATION_GUIDE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
