# pattern_discovery.py
# ====================
# Unsupervised + supervised AI pattern discovery across all cached markets.
#
# Follows the standard 4-step AI pattern-discovery process:
#   1. DATA COLLECTION & CLEANSING — load Parquet cache, dedupe, fill gaps,
#      drop illiquid/short-history stocks, winsorise outliers.
#   2. FEATURE EXTRACTION — engineer ~25 price/metric features per stock
#      (momentum, volatility, trend, volume, drawdown, seasonality, shape).
#   3. MODEL SELECTION & TRAINING —
#        Unsupervised: KMeans + DBSCAN clustering, PCA, correlation pairs.
#        Supervised:   GradientBoosting to predict forward 21-day return,
#                      surfacing the features that actually drive returns.
#   4. INSIGHT EXTRACTION — name the discovered clusters ("behavioural archetypes"),
#      rank feature importances, flag anomalies and co-moving pairs.
#
# Usage:
#   python pattern_discovery.py                 # all cached stocks
#   python pattern_discovery.py --market IN     # NSE only (.NS)
#   python pattern_discovery.py --clusters 6
#   python pattern_discovery.py --max 1000      # cap for speed
#
# Output: pattern_results/patterns_DATE.xlsx + printed insight report
#
# ⚠️ Educational/research use only. Discovered patterns are historical
#    associations, NOT predictions. Past structure need not persist. NOT advice.

from __future__ import annotations

import argparse
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from stock_utils import pct_change

try:
    from sklearn.cluster import DBSCAN, KMeans
    from sklearn.decomposition import PCA
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler

    _SK_OK = True
except ImportError:
    _SK_OK = False

CACHE_DIR = Path.home() / "Downloads" / "market_cache" / "ohlc"
REF_DIR = Path.home() / "nse_screener_reference" / "ohlc_cache"
OUT_DIR = Path("./pattern_results")
OUT_DIR.mkdir(exist_ok=True)

DISCLAIMER = (
    "⚠️  Patterns are historical associations discovered by unsupervised/"
    "supervised ML. They are NOT predictions and need not persist. "
    "Educational/research use only. NOT investment advice."
)

FEATURE_NAMES = [
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_12m",  # momentum
    "vol_20d",
    "vol_60d",
    "vol_ratio",  # volatility
    "dist_52w_high",
    "dist_52w_low",  # position in range
    "dma50_gap",
    "dma200_gap",
    "dma_trend",  # trend
    "rsi_14",
    "above_200dma_pct_days",  # technical state
    "max_drawdown",
    "calmar_proxy",  # risk
    "avg_dollar_vol",
    "vol_trend",  # liquidity
    "skew_60d",
    "kurt_60d",  # return shape
    "up_day_ratio",
    "gap_freq",  # behaviour
    "trend_strength",
    "mean_reversion",  # regime tendency
]


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — DATA COLLECTION & CLEANSING
# ══════════════════════════════════════════════════════════════════════════════


def load_and_clean(market: str = "ALL", max_stocks: int = 0, min_bars: int = 252) -> dict:
    """Load Parquet cache, clean each frame, return {symbol: clean_df}."""
    print("STEP 1 — Data Collection & Cleansing")
    files = list(CACHE_DIR.glob("*.parquet")) + list(REF_DIR.glob("*.parquet"))

    # Market filter: .NS = India, no suffix = US
    if market == "IN":
        files = [f for f in files if f.stem.endswith(".NS")]
    elif market == "US":
        files = [f for f in files if not (f.stem.endswith(".NS") or f.stem.endswith(".BO"))]

    if max_stocks:
        files = files[:max_stocks]
    print(f"  Candidate files: {len(files)}")

    cleaned, dropped = {}, 0
    seen_symbols = set()
    for f in files:
        sym = f.stem
        if sym in seen_symbols:  # dedupe across cache + reference dirs
            continue
        try:
            df = pd.read_parquet(f)
            if df.empty or len(df) < min_bars:
                dropped += 1
                continue
            # Cleanse: sort, dedupe index, forward/back-fill gaps, drop zero-price
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df = df[["Open", "High", "Low", "Close", "Volume"]].ffill().bfill()
            if (df["Close"] <= 0).any() or df["Close"].isnull().all():
                dropped += 1
                continue
            # Liquidity floor: median dollar volume > 0
            if (df["Close"] * df["Volume"]).median() <= 0:
                dropped += 1
                continue
            cleaned[sym] = df
            seen_symbols.add(sym)
        except Exception:
            dropped += 1

    print(
        f"  Loaded {len(cleaned)} clean stocks | dropped {dropped} "
        f"(short history / illiquid / corrupt)"
    )
    return cleaned


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — FEATURE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

FWD = 21  # forward horizon for the supervised target (trading days)


def extract_features(symbol: str, df_full: pd.DataFrame) -> dict:
    """Engineer ~25 features capturing the stock's price/metric behaviour.

    LEAKAGE FIX: features are computed ONLY on data up to the as-of cutoff
    (df_full minus the last FWD bars). The supervised target is the genuinely
    out-of-sample forward return over those held-out last FWD bars. No feature
    window can see the target period.
    """
    if len(df_full) < 252 + FWD:
        return {}

    # As-of split: features see [:-FWD], target measures the held-out tail
    df = df_full.iloc[:-FWD]
    fut_close = float(df_full["Close"].iloc[-1])  # price FWD days after as-of

    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float)
    rets = c.pct_change().dropna()
    n = len(c)
    if n < 252 or rets.empty:
        return {}

    def safe(x, d=0.0):
        return float(x) if x is not None and np.isfinite(x) else d

    last = c.iloc[-1]
    feat = {"symbol": symbol}

    # Momentum (price return over windows)
    feat["ret_1m"] = safe(pct_change(last, c.iloc[-21])) if n > 21 else 0
    feat["ret_3m"] = safe(pct_change(last, c.iloc[-63])) if n > 63 else 0
    feat["ret_6m"] = safe(pct_change(last, c.iloc[-126])) if n > 126 else 0
    feat["ret_12m"] = safe(pct_change(last, c.iloc[-252])) if n > 252 else 0

    # Volatility
    feat["vol_20d"] = safe(rets.tail(20).std() * np.sqrt(252) * 100)
    feat["vol_60d"] = safe(rets.tail(60).std() * np.sqrt(252) * 100)
    feat["vol_ratio"] = safe(feat["vol_20d"] / feat["vol_60d"]) if feat["vol_60d"] else 1

    # Position in 52-week range
    h52, l52 = h.tail(252).max(), l.tail(252).min()
    feat["dist_52w_high"] = safe((last - h52) / h52 * 100)
    feat["dist_52w_low"] = safe((last - l52) / l52 * 100)

    # Trend
    dma50 = c.rolling(50).mean().iloc[-1]
    dma200 = c.rolling(200).mean().iloc[-1]
    feat["dma50_gap"] = safe((last - dma50) / dma50 * 100)
    feat["dma200_gap"] = safe((last - dma200) / dma200 * 100)
    feat["dma_trend"] = safe((dma50 - dma200) / dma200 * 100)

    # RSI(14)
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rs = gain / loss if loss else 0
    feat["rsi_14"] = safe(100 - 100 / (1 + rs)) if loss else 100

    # % of days above 200 DMA (trend persistence)
    dma200_series = c.rolling(200).mean()
    above = (c > dma200_series).tail(252)
    feat["above_200dma_pct_days"] = safe(above.mean() * 100)

    # Risk
    cumul = (1 + rets).cumprod()
    dd = (cumul - cumul.cummax()) / cumul.cummax()
    feat["max_drawdown"] = safe(dd.min() * 100)
    feat["calmar_proxy"] = (
        safe(feat["ret_12m"] / abs(feat["max_drawdown"])) if feat["max_drawdown"] else 0
    )

    # Liquidity
    dvol = c * v
    feat["avg_dollar_vol"] = safe(np.log1p(dvol.tail(60).mean()))
    feat["vol_trend"] = safe(pct_change(v.tail(20).mean(), v.tail(60).mean()))

    # Return shape
    feat["skew_60d"] = safe(rets.tail(60).skew())
    feat["kurt_60d"] = safe(rets.tail(60).kurt())

    # Behaviour
    feat["up_day_ratio"] = safe((rets > 0).tail(252).mean() * 100)
    feat["gap_freq"] = safe((abs(df["Open"] / c.shift(1) - 1) > 0.02).tail(252).mean() * 100)

    # Regime tendency: trend strength (autocorrelation of sign)
    sign = np.sign(rets.tail(120))
    feat["trend_strength"] = safe(sign.autocorr() * 100) if len(sign) > 5 else 0
    # Mean reversion: negative lag-1 autocorrelation of returns
    feat["mean_reversion"] = safe(-rets.tail(120).autocorr() * 100) if len(rets) > 5 else 0

    # Supervised target: TRUE out-of-sample forward return.
    # `last` is the as-of close (end of feature window); `fut_close` is the
    # price FWD trading days later (held out from ALL features above).
    feat["_target_fwd21"] = safe((fut_close - last) / last * 100)

    return feat


def build_feature_matrix(cleaned: dict) -> pd.DataFrame:
    print("\nSTEP 2 — Feature Extraction")
    from stock_utils import parallel_map

    rows = parallel_map(
        lambda kv: extract_features(kv[0], kv[1]) or None,
        list(cleaned.items()),
        workers=8,
        progress_every=500,
        label="stocks",
        verbose=True,
    )
    df = pd.DataFrame([r for r in rows if r and len(r) > 5])
    df = df.dropna(subset=FEATURE_NAMES, how="any")
    print(f"  Feature matrix: {df.shape[0]} stocks × {len(FEATURE_NAMES)} features")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — MODEL SELECTION & TRAINING
# ══════════════════════════════════════════════════════════════════════════════


def discover_clusters(fm: pd.DataFrame, n_clusters: int = 6) -> tuple:
    """Unsupervised: KMeans behavioural archetypes + PCA + DBSCAN anomalies."""
    print("\nSTEP 3a — Unsupervised: Clustering (KMeans + DBSCAN + PCA)")
    X = fm[FEATURE_NAMES].replace([np.inf, -np.inf], 0).fillna(0).values
    Xs = StandardScaler().fit_transform(X)

    # KMeans archetypes
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    fm = fm.copy()
    fm["cluster"] = km.fit_predict(Xs)

    # PCA for variance explained + 2D structure
    pca = PCA(n_components=min(5, len(FEATURE_NAMES)))
    pcs = pca.fit_transform(Xs)
    fm["pc1"], fm["pc2"] = pcs[:, 0], pcs[:, 1]
    evr = pca.explained_variance_ratio_
    print(
        f"  KMeans: {n_clusters} clusters | PCA top-2 explain "
        f"{(evr[0]+evr[1])*100:.0f}% of variance"
    )

    # DBSCAN for anomaly/outlier detection
    db = DBSCAN(eps=3.0, min_samples=5).fit(Xs)
    fm["is_anomaly"] = db.labels_ == -1
    n_anom = int(fm["is_anomaly"].sum())
    print(f"  DBSCAN: {n_anom} anomalous stocks (don't fit any dense cluster)")

    return fm, km, pca, evr


def supervised_drivers(fm: pd.DataFrame) -> pd.DataFrame:
    """Supervised: which features predict forward 21-day return?"""
    print("\nSTEP 3b — Supervised: GradientBoosting return drivers")
    sub = fm.dropna(subset=["_target_fwd21"])
    if len(sub) < 100:
        print("  Insufficient labelled data for supervised model")
        return pd.DataFrame()

    X = sub[FEATURE_NAMES].replace([np.inf, -np.inf], 0).fillna(0).values
    y = sub["_target_fwd21"].values
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42)

    model = GradientBoostingRegressor(
        n_estimators=150, max_depth=3, learning_rate=0.05, random_state=42
    )
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)
    r2 = r2_score(yte, pred)
    mae = mean_absolute_error(yte, pred)
    print(f"  Model fit: R²={r2:.3f} | MAE={mae:.2f}% (forward 21-day return)")
    if r2 < 0.05:
        print(
            "  ⚠️  Low R² — forward returns are weakly predictable from these "
            "features (consistent with semi-efficient markets)"
        )

    imp = pd.DataFrame(
        {
            "Feature": FEATURE_NAMES,
            "Importance": model.feature_importances_,
        }
    ).sort_values("Importance", ascending=False)
    imp["Importance%"] = (imp["Importance"] * 100).round(1)
    return imp


def find_comoving_pairs(cleaned: dict, fm: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    """Find highly correlated stock pairs (pairs-trading / sector co-movement)."""
    print("\nSTEP 3c — Co-movement: correlated pairs (within clusters)")
    rows = []
    # Limit to most liquid for tractability
    liquid = fm.nlargest(min(120, len(fm)), "avg_dollar_vol")["symbol"].tolist()
    rets = {}
    for s in liquid:
        if s in cleaned:
            r = cleaned[s]["Close"].pct_change().dropna().tail(252)
            if len(r) >= 200:
                rets[s] = r
    if len(rets) < 2:
        return pd.DataFrame()
    rdf = pd.DataFrame(rets).dropna()
    corr = rdf.corr()
    syms = corr.columns.tolist()
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            c = corr.iloc[i, j]
            if c > 0.80:
                rows.append({"Stock_A": syms[i], "Stock_B": syms[j], "Correlation": round(c, 3)})
    pairs = pd.DataFrame(rows).sort_values("Correlation", ascending=False).head(top_n)
    print(f"  Found {len(pairs)} pairs with correlation > 0.80")
    return pairs


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — INSIGHT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════


def name_archetype(profile: pd.Series) -> str:
    """Translate a cluster's mean feature profile into a human-readable archetype."""
    mom = profile["ret_12m"]
    vol = profile["vol_60d"]
    trend = profile["dma200_gap"]
    dd = profile["max_drawdown"]
    rsi = profile["rsi_14"]
    mr = profile["mean_reversion"]

    if mom > 40 and trend > 10:
        return "🚀 High-Momentum Leaders"
    if mom < -20 and trend < -10:
        return "📉 Sustained Downtrenders"
    if vol > 50:
        return "⚡ High-Volatility Movers"
    if abs(trend) < 5 and vol < 25:
        return "😴 Low-Vol Range-Bound"
    if mr > 10:
        return "🔄 Mean-Reverters"
    if rsi > 65 and mom > 15:
        return "🔥 Overbought Momentum"
    if dd < -50:
        return "🩹 Deep-Drawdown Recovery"
    return "⚖️  Balanced / Mixed"


def extract_insights(fm: pd.DataFrame, importances: pd.DataFrame, pairs: pd.DataFrame, evr) -> dict:
    print("\nSTEP 4 — Insight Extraction")
    print("=" * 78)
    insights = {}

    # Cluster archetypes
    print("\n📊 BEHAVIOURAL ARCHETYPES (unsupervised KMeans clusters):")
    print(
        f"  {'Archetype':<32} {'N':>5} {'Ret12m':>8} {'Vol60d':>8} "
        f"{'200DMA':>8} {'MaxDD':>8} {'RSI':>6}"
    )
    print("  " + "─" * 78)
    cluster_summaries = []
    for cl in sorted(fm["cluster"].unique()):
        grp = fm[fm["cluster"] == cl]
        prof = grp[FEATURE_NAMES].mean()
        name = name_archetype(prof)
        cluster_summaries.append(
            {
                "Cluster": cl,
                "Archetype": name,
                "Count": len(grp),
                "Avg_Ret12m": round(prof["ret_12m"], 1),
                "Avg_Vol60d": round(prof["vol_60d"], 1),
                "Avg_200DMA_gap": round(prof["dma200_gap"], 1),
                "Avg_MaxDD": round(prof["max_drawdown"], 1),
                "Avg_RSI": round(prof["rsi_14"], 0),
                "Examples": ", ".join(grp["symbol"].head(4).tolist()),
            }
        )
        print(
            f"  {name:<32} {len(grp):>5} {prof['ret_12m']:>7.1f}% "
            f"{prof['vol_60d']:>7.1f}% {prof['dma200_gap']:>7.1f}% "
            f"{prof['max_drawdown']:>7.1f}% {prof['rsi_14']:>6.0f}"
        )
    insights["clusters"] = pd.DataFrame(cluster_summaries)

    # Feature importances
    if not importances.empty:
        print("\n🎯 RETURN DRIVERS (supervised — predicts forward 21d return):")
        for _, r in importances.head(8).iterrows():
            bar = "█" * int(r["Importance%"] / 2)
            print(f"  {r['Feature']:<22} {r['Importance%']:>5.1f}%  {bar}")
        insights["importances"] = importances

    # Anomalies
    anom = fm[fm["is_anomaly"]]
    if not anom.empty:
        print(f"\n🔍 ANOMALIES ({len(anom)} stocks not fitting any pattern):")
        top_anom = anom.nlargest(min(10, len(anom)), "vol_60d")
        print(f"  {', '.join(top_anom['symbol'].tolist()[:12])}")
        insights["anomalies"] = anom[["symbol"] + FEATURE_NAMES]

    # Co-moving pairs
    if not pairs.empty:
        print("\n🔗 CO-MOVING PAIRS (correlation > 0.80 — pairs-trade candidates):")
        for _, r in pairs.head(8).iterrows():
            print(f"  {r['Stock_A']:<14} ↔ {r['Stock_B']:<14} ρ={r['Correlation']}")
        insights["pairs"] = pairs

    print(f"\n{'='*78}")
    print(f"  {DISCLAIMER}")
    print(f"{'='*78}")
    return insights


def save_excel(insights: dict, fm: pd.DataFrame, market: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT_DIR / f"patterns_{market}_{ts}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"DISCLAIMER": [DISCLAIMER]}).to_excel(w, "DISCLAIMER", index=False)
        if "clusters" in insights:
            insights["clusters"].to_excel(w, "Archetypes", index=False)
        if "importances" in insights:
            insights["importances"].to_excel(w, "Return_Drivers", index=False)
        if "pairs" in insights:
            insights["pairs"].to_excel(w, "CoMoving_Pairs", index=False)
        if "anomalies" in insights:
            insights["anomalies"].head(100).to_excel(w, "Anomalies", index=False)
        fm[["symbol", "cluster", "is_anomaly"] + FEATURE_NAMES].to_excel(
            w, "All_Features", index=False
        )
    print(f"\n  📊 Pattern Excel → {path}")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────


def main(market="ALL", n_clusters=6, max_stocks=0):
    print(f"\n{'#'*78}")
    print(f"  AI PATTERN DISCOVERY — {market} market | {datetime.now():%d %b %Y %H:%M}")
    print(f"{'#'*78}\n{DISCLAIMER}\n")
    if not _SK_OK:
        print("❌ pip install scikit-learn")
        return

    cleaned = load_and_clean(market, max_stocks)
    if len(cleaned) < 50:
        print("Insufficient data.")
        return
    fm = build_feature_matrix(cleaned)
    if len(fm) < 50:
        print("Insufficient features.")
        return

    fm, km, pca, evr = discover_clusters(fm, n_clusters)
    importances = supervised_drivers(fm)
    pairs = find_comoving_pairs(cleaned, fm)
    insights = extract_insights(fm, importances, pairs, evr)
    save_excel(insights, fm, market)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="AI pattern discovery across cached markets")
    p.add_argument("--market", choices=["IN", "US", "ALL"], default="ALL")
    p.add_argument("--clusters", type=int, default=6)
    p.add_argument("--max", type=int, default=0, help="Cap stocks for speed")
    a = p.parse_args()
    main(market=a.market, n_clusters=a.clusters, max_stocks=a.max)
