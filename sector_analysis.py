# sector_analysis.py
# ==================
# Sector classification + KMeans clustering on historical sector returns.
#
# PIPELINE
#   1. CLASSIFY: assign each cached stock a GICS-style sector (yfinance.info),
#      cached to sector_cache.json so the slow fetch runs only once (incremental).
#   2. BUILD SECTOR LISTS: group stocks into smaller per-sector lists.
#   3. SECTOR RETURN SERIES: equal-weighted daily return index per sector.
#   4. KMEANS: cluster sectors by their return-behaviour fingerprint
#      (momentum, volatility, drawdown, correlation-to-market, trend).
#   5. PATTERNS: name the clusters, find lead/lag & co-movement between sectors,
#      rank sectors by risk-adjusted performance, detect rotation.
#
# Usage:
#   python sector_analysis.py --market IN
#   python sector_analysis.py --market US --max 1500
#   python sector_analysis.py --refresh-sectors     # force re-fetch sector labels
#
# ⚠️ Educational/research only. Historical sector patterns need not persist.

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from stock_utils import parallel_map

try:
    import yfinance as yf

    _YF_OK = True
except ImportError:
    _YF_OK = False

try:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    _SK_OK = True
except ImportError:
    _SK_OK = False

CACHE_DIR = Path.home() / "Downloads" / "market_cache" / "ohlc"
REF_DIR = Path.home() / "nse_screener_reference" / "ohlc_cache"
SECTOR_CACHE = Path.home() / "Downloads" / "market_cache" / "sector_map.json"
OUT_DIR = Path("./sector_results")
OUT_DIR.mkdir(exist_ok=True)

DISCLAIMER = (
    "⚠️  Sector classifications from yfinance; return patterns are "
    "historical and need not persist. Educational/research only. NOT advice."
)

# Sector return-fingerprint features for KMeans
SECTOR_FEATURES = [
    "ann_return",
    "ann_vol",
    "sharpe",
    "max_drawdown",
    "beta_to_market",
    "trend_persistence",
    "best_month",
    "worst_month",
    "up_month_ratio",
    "recent_3m_mom",
]


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — SECTOR CLASSIFICATION (cached)
# ══════════════════════════════════════════════════════════════════════════════


def _load_sector_cache() -> dict:
    if SECTOR_CACHE.exists():
        try:
            return json.loads(SECTOR_CACHE.read_text())
        except Exception:
            pass
    return {}


def _save_sector_cache(d: dict):
    SECTOR_CACHE.write_text(json.dumps(d, indent=0))


def fetch_sector(symbol: str) -> tuple:
    """Return (symbol, sector, industry) from yfinance.info."""
    if not _YF_OK:
        return (symbol, "Unknown", "Unknown")
    try:
        info = yf.Ticker(symbol).info or {}
        return (symbol, info.get("sector") or "Unknown", info.get("industry") or "Unknown")
    except Exception:
        return (symbol, "Unknown", "Unknown")


def classify_sectors(symbols: list, refresh: bool = False, workers: int = 8) -> dict:
    """Assign a sector to each symbol, using a persistent cache."""
    print("STEP 1 — Sector Classification")
    cache = {} if refresh else _load_sector_cache()
    missing = [s for s in symbols if s not in cache]
    print(f"  {len(symbols)} symbols | {len(cache)} cached | {len(missing)} to fetch")

    if missing:
        print("  Fetching sectors (cached for next time) …")
        results = parallel_map(
            fetch_sector, missing, workers=workers, progress_every=200, label="sectors"
        )
        for sym, sector, industry in results:
            cache[sym] = {"sector": sector, "industry": industry}
        _save_sector_cache(cache)
        print(f"  Sector cache updated: {len(cache)} symbols")

    return {s: cache[s] for s in symbols if s in cache}


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2-3 — SECTOR LISTS + EQUAL-WEIGHTED RETURN SERIES
# ══════════════════════════════════════════════════════════════════════════════


def load_returns(symbols: list, min_bars: int = 252) -> dict:
    """Load daily return series for each symbol from the Parquet cache."""
    rets = {}
    for s in symbols:
        for d in (CACHE_DIR, REF_DIR):
            # symbol may be stored with or without suffix
            for cand in (f"{s}.parquet",):
                p = d / cand
                if p.exists():
                    try:
                        df = pd.read_parquet(p)
                        if len(df) >= min_bars:
                            r = df["Close"].astype(float).pct_change().dropna()
                            rets[s] = r.tail(504)  # last ~2 years
                    except Exception:
                        pass
                    break
            if s in rets:
                break
    return rets


def build_sector_indices(sector_map: dict, returns: dict) -> pd.DataFrame:
    """Equal-weighted daily return index per sector (mean of member returns)."""
    print("\nSTEP 2-3 — Sector lists + equal-weighted return indices")
    # Group symbols by sector
    groups: dict = {}
    for sym, meta in sector_map.items():
        sec = meta.get("sector", "Unknown")
        if sec in ("Unknown", None) or sym not in returns:
            continue
        groups.setdefault(sec, []).append(sym)

    # Keep sectors with enough members for a stable index
    groups = {k: v for k, v in groups.items() if len(v) >= 3}
    print(f"  {len(groups)} sectors with ≥3 liquid members:")
    for sec, members in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"    {sec:<28} {len(members):>4} stocks")

    # Build equal-weighted return index per sector (aligned on common dates)
    sector_series = {}
    for sec, members in groups.items():
        member_rets = pd.DataFrame({m: returns[m] for m in members}).dropna(how="all")
        # Equal-weight: mean across members each day
        sector_series[sec] = member_rets.mean(axis=1)
    idx = pd.DataFrame(sector_series).dropna(how="all")
    return idx, groups


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — SECTOR RETURN FINGERPRINTS + KMEANS
# ══════════════════════════════════════════════════════════════════════════════


def sector_fingerprint(rets: pd.Series, market_rets: pd.Series) -> dict:
    """Compute the behavioural fingerprint of a sector return series."""
    r = rets.dropna()
    if len(r) < 100:
        return {}
    ann_ret = (1 + r).prod() ** (252 / len(r)) - 1
    ann_vol = r.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cumul = (1 + r).cumprod()
    dd = ((cumul - cumul.cummax()) / cumul.cummax()).min()
    # Beta to equal-weighted market (all sectors)
    aligned = pd.concat([r, market_rets], axis=1).dropna()
    if len(aligned) > 30 and aligned.iloc[:, 1].var() > 0:
        beta = aligned.cov().iloc[0, 1] / aligned.iloc[:, 1].var()
    else:
        beta = 1.0
    monthly = (
        (1 + r).resample("ME").prod() - 1
        if hasattr(r.index, "freq")
        else r.groupby(pd.Grouper(freq="ME")).apply(lambda x: (1 + x).prod() - 1)
    )
    return {
        "ann_return": round(ann_ret * 100, 2),
        "ann_vol": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(dd * 100, 2),
        "beta_to_market": round(beta, 3),
        "trend_persistence": round(np.sign(r).autocorr() * 100, 2) if len(r) > 5 else 0,
        "best_month": round(monthly.max() * 100, 2) if len(monthly) > 0 else 0,
        "worst_month": round(monthly.min() * 100, 2) if len(monthly) > 0 else 0,
        "up_month_ratio": round((monthly > 0).mean() * 100, 1) if len(monthly) > 0 else 0,
        "recent_3m_mom": round((1 + r.tail(63)).prod() * 100 - 100, 2),
    }


def cluster_sectors(idx: pd.DataFrame, n_clusters: int = 4) -> tuple:
    """KMeans on sector return fingerprints."""
    print("\nSTEP 4 — KMeans clustering on sector return patterns")
    market = idx.mean(axis=1)  # equal-weighted "all-sector market"
    rows = []
    for sec in idx.columns:
        fp = sector_fingerprint(idx[sec], market)
        if fp:
            fp["sector"] = sec
            rows.append(fp)
    fdf = pd.DataFrame(rows)
    if len(fdf) < n_clusters:
        n_clusters = max(2, len(fdf) // 2)
    X = StandardScaler().fit_transform(fdf[SECTOR_FEATURES].fillna(0).values)
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    fdf["cluster"] = km.fit_predict(X)
    print(f"  Clustered {len(fdf)} sectors into {n_clusters} groups")
    return fdf, market


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — PATTERN EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════


def name_sector_cluster(profile: pd.Series) -> str:
    ret, vol, beta = profile["ann_return"], profile["ann_vol"], profile["beta_to_market"]
    if ret > 25 and vol > 35:
        return "🚀 High-Growth / High-Risk"
    if ret > 15 and vol < 30:
        return "💎 Quality Compounders (high return, low vol)"
    if beta > 1.2:
        return "📈 Aggressive / High-Beta (amplifies market)"
    if beta < 0.7:
        return "🛡️  Defensive / Low-Beta (cushions market)"
    if ret < 0:
        return "📉 Laggards / Underperformers"
    return "⚖️  Market-Tracking / Cyclical"


def extract_patterns(fdf: pd.DataFrame, idx: pd.DataFrame, market: pd.Series):
    print("\nSTEP 5 — Pattern Extraction")
    print("=" * 82)

    # Sector clusters
    print("\n📊 SECTOR BEHAVIOUR CLUSTERS (KMeans on return fingerprints):")
    print(f"  {'Cluster archetype':<46} Sectors")
    print("  " + "─" * 78)
    for cl in sorted(fdf["cluster"].unique()):
        grp = fdf[fdf["cluster"] == cl]
        name = name_sector_cluster(grp[SECTOR_FEATURES].mean())
        secs = ", ".join(grp["sector"].tolist())
        print(f"  {name:<46} {secs}")

    # Sector ranking by risk-adjusted return
    print("\n🏆 SECTOR RANKING (by Sharpe, last ~2 years):")
    print(
        f"  {'Sector':<28} {'AnnRet%':>8} {'Vol%':>7} {'Sharpe':>7} "
        f"{'MaxDD%':>8} {'Beta':>6} {'3m Mom%':>8}"
    )
    print("  " + "─" * 76)
    for _, r in fdf.sort_values("sharpe", ascending=False).iterrows():
        print(
            f"  {r['sector']:<28} {r['ann_return']:>7.1f} {r['ann_vol']:>7.1f} "
            f"{r['sharpe']:>7.2f} {r['max_drawdown']:>8.1f} "
            f"{r['beta_to_market']:>6.2f} {r['recent_3m_mom']:>8.1f}"
        )

    # Sector co-movement (correlation pairs)
    print("\n🔗 SECTOR CO-MOVEMENT (correlation of return indices):")
    corr = idx.corr()
    pairs = []
    cols = corr.columns.tolist()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            pairs.append((cols[i], cols[j], corr.iloc[i, j]))
    pairs.sort(key=lambda x: -x[2])
    print("  Most correlated (move together — limited diversification):")
    for a, b, c in pairs[:5]:
        print(f"    {a:<24} ↔ {b:<24} ρ={c:.2f}")
    print("  Least correlated (best diversification pairs):")
    for a, b, c in pairs[-5:]:
        print(f"    {a:<24} ↔ {b:<24} ρ={c:.2f}")

    # Sector rotation: recent momentum leaders vs laggards
    print("\n🔄 SECTOR ROTATION (recent 3-month momentum):")
    rot = fdf.sort_values("recent_3m_mom", ascending=False)
    print(
        "  Leaders:  "
        + ", ".join(
            f"{r['sector']} ({r['recent_3m_mom']:+.0f}%)" for _, r in rot.head(3).iterrows()
        )
    )
    print(
        "  Laggards: "
        + ", ".join(
            f"{r['sector']} ({r['recent_3m_mom']:+.0f}%)" for _, r in rot.tail(3).iterrows()
        )
    )

    print(f"\n{'='*82}\n  {DISCLAIMER}\n{'='*82}")
    return corr


def save_excel(fdf, idx, corr, groups, market_name):
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT_DIR / f"sector_analysis_{market_name}_{ts}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"DISCLAIMER": [DISCLAIMER]}).to_excel(w, "DISCLAIMER", index=False)
        fdf.sort_values("sharpe", ascending=False).to_excel(w, "Sector_Fingerprints", index=False)
        corr.round(3).to_excel(w, "Sector_Correlation")
        # Sector membership lists
        mem = pd.DataFrame(
            [{"Sector": s, "N": len(m), "Members": ", ".join(m[:40])} for s, m in groups.items()]
        ).sort_values("N", ascending=False)
        mem.to_excel(w, "Sector_Members", index=False)
    print(f"\n  📊 → {path}")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────


def load_universe(market: str, max_stocks: int) -> list:
    files = list(CACHE_DIR.glob("*.parquet")) + list(REF_DIR.glob("*.parquet"))
    if market == "IN":
        files = [f for f in files if f.stem.endswith(".NS")]
    elif market == "US":
        files = [f for f in files if not (f.stem.endswith(".NS") or f.stem.endswith(".BO"))]
    syms = sorted(set(f.stem for f in files))
    return syms[:max_stocks] if max_stocks else syms


def main(market="IN", n_clusters=4, max_stocks=0, refresh=False):
    print(f"\n{'#'*82}")
    print(f"  SECTOR CLASSIFICATION + KMEANS RETURN-PATTERN ANALYSIS — {market}")
    print(f"  {datetime.now():%d %b %Y %H:%M}")
    print(f"{'#'*82}\n{DISCLAIMER}\n")
    if not (_SK_OK and _YF_OK):
        print("❌ pip install scikit-learn yfinance")
        return

    symbols = load_universe(market, max_stocks)
    print(f"  Universe: {len(symbols)} cached stocks\n")
    sector_map = classify_sectors(symbols, refresh=refresh)
    returns = load_returns(list(sector_map.keys()))
    print(f"  Loaded return series for {len(returns)} stocks")

    idx, groups = build_sector_indices(sector_map, returns)
    if idx.shape[1] < 3:
        print("Not enough sectors with data.")
        return

    fdf, market_idx = cluster_sectors(idx, n_clusters)
    corr = extract_patterns(fdf, idx, market_idx)
    save_excel(fdf, idx, corr, groups, market)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Sector classification + KMeans on sector returns")
    p.add_argument("--market", choices=["IN", "US", "ALL"], default="IN")
    p.add_argument("--clusters", type=int, default=4)
    p.add_argument("--max", type=int, default=0)
    p.add_argument("--refresh-sectors", action="store_true", dest="refresh")
    a = p.parse_args()
    main(market=a.market, n_clusters=a.clusters, max_stocks=a.max, refresh=a.refresh)
