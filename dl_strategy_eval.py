# dl_strategy_eval.py
# ===================
# Directional classification → mechanical trading strategy → economic backtest.
#
# GROUNDED IN THE DEEP-LEARNING LITERATURE (5 papers):
#
#   Fister et al. (NNW 2019) "Deep Learning for Stock Market Trading: A Superior
#     Trading Strategy?" — evaluate a model as a MECHANICAL TRADING STRATEGY
#     compared against passive (buy-and-hold) and rule-based baselines, not by
#     prediction error. → We build long/flat strategies and benchmark vs buy-hold.
#
#   Olorunnimbe & Viktor (AI Review 2022) "Deep learning in the stock market —
#     systematic survey of practice, BACKTESTING and applications" — domain
#     metrics "returns" and "volatility" matter more than RMSE; backtesting is the
#     test of real-world relevance. → We report cumulative return, Sharpe, max DD.
#
#   Toichatturat (SET/Thammasat 2024) "Stock Forecasting with GANs" — factor
#     models (fundamental + technical) + XGBoost + ENSEMBLE beat single models;
#     evaluate by Sharpe + cumulative return. → We use factor features + gradient
#     boosting ensemble, scored by Sharpe.
#
#   Sharma et al. (IJIRTM 2025) survey — LSTM dominant (73.5%) but non-stationarity
#     demands retraining; classification framing is standard. → We use WALK-FORWARD
#     retraining and frame as up/down classification (not point regression).
#
#   Miao (Stanford CS230) — LSTM hyperparameters (layers/dropout/batch) matter.
#     → Noted as future work; AlQahtani et al. (already in system) showed simpler
#       models beat LSTM on this data, so we use GradientBoosting as the learner.
#
# WHY CLASSIFICATION, NOT REGRESSION:
#   Our earlier pattern_discovery showed regression on forward returns gives
#   R²≈0 (semi-efficient markets). The literature frames the task as DIRECTION
#   (up/down) evaluated economically — a lower bar that can still beat buy-hold
#   via better timing / drawdown avoidance. This script tests exactly that.
#
# Usage:
#   python dl_strategy_eval.py --market IN --max 200
#   python dl_strategy_eval.py --horizon 21 --threshold 0.55
#
# ⚠️ Educational/research only. Backtested strategies suffer survivorship and
#    look-ahead risks. NOT investment advice.

from __future__ import annotations

import argparse
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from stock_utils import parallel_map

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    _SK_OK = True
except ImportError:
    _SK_OK = False

CACHE_DIR = Path.home() / "Downloads" / "market_cache" / "ohlc"
REF_DIR = Path.home() / "nse_screener_reference" / "ohlc_cache"
OUT_DIR = Path("./dl_strategy_results")
OUT_DIR.mkdir(exist_ok=True)

DISCLAIMER = (
    "⚠️  Backtested trading strategy. Survivorship + look-ahead risk. "
    "Economic results are historical, need not persist. NOT advice."
)

# Factor features (technical — fundamental factors optional via fund cache)
FEATURES = [
    "ret_5d",
    "ret_21d",
    "ret_63d",
    "vol_21d",
    "rsi_14",
    "macd_hist",
    "bb_pct",
    "dma50_gap",
    "dma200_gap",
    "vol_ratio",
    "mom_accel",
    "dist_high",
]


# ── Feature engineering (walk-forward safe, computed per-bar) ──────────────────


def build_panel(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Build a per-bar feature panel with a forward-direction label.

    Label = 1 if forward `horizon`-day return > 0 else 0.
    Features at bar t use ONLY data up to t (no leakage); label uses t→t+horizon.
    """
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    v = df["Volume"].astype(float).replace(0, np.nan)
    out = pd.DataFrame(index=df.index)

    out["ret_5d"] = c.pct_change(5) * 100
    out["ret_21d"] = c.pct_change(21) * 100
    out["ret_63d"] = c.pct_change(63) * 100
    out["vol_21d"] = c.pct_change().rolling(21).std() * np.sqrt(252) * 100

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    out["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    macd = ema12 - ema26
    out["macd_hist"] = macd - macd.ewm(span=9).mean()

    bmid = c.rolling(20).mean()
    bstd = c.rolling(20).std()
    out["bb_pct"] = (c - (bmid - 2 * bstd)) / ((4 * bstd).replace(0, np.nan))

    out["dma50_gap"] = (c - c.rolling(50).mean()) / c.rolling(50).mean() * 100
    out["dma200_gap"] = (c - c.rolling(200).mean()) / c.rolling(200).mean() * 100
    out["vol_ratio"] = v / v.rolling(20).mean()
    out["mom_accel"] = c.pct_change(5) * 100 - c.pct_change(21) * 100  # momentum acceleration
    out["dist_high"] = (c - h.rolling(252).max()) / h.rolling(252).max() * 100

    # Forward label (direction of horizon-day return)
    fwd = c.shift(-horizon) / c - 1
    out["_label"] = (fwd > 0).astype(int)
    out["_fwd_ret"] = fwd * 100
    return out.dropna()


# ── Strategy backtest (walk-forward) ──────────────────────────────────────────


def evaluate_stock(args) -> dict:
    """Train classifier walk-forward, build long/flat strategy, compare vs buy-hold."""
    symbol, df, horizon, threshold = args
    if not _SK_OK or df is None or len(df) < 600:
        return None
    panel = build_panel(df, horizon)
    if len(panel) < 400:
        return None

    X = panel[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0).values
    y = panel["_label"].values
    fwd = panel["_fwd_ret"].values / 100  # decimal forward return per bar

    # Walk-forward: train on first 70%, test on last 30% (out-of-sample)
    split = int(len(X) * 0.70)
    if split < 200 or len(X) - split < 60:
        return None
    Xtr, Xte = X[:split], X[split:]
    ytr = y[:split]
    fwd_te = fwd[split:]

    # Single-name models are weak; require both classes present
    if len(np.unique(ytr)) < 2:
        return None
    try:
        clf = GradientBoostingClassifier(
            n_estimators=80, max_depth=3, learning_rate=0.05, random_state=42
        )
        clf.fit(Xtr, ytr)
        proba = clf.predict_proba(Xte)[:, 1]
    except Exception:
        return None

    # Mechanical strategy: long when P(up) >= threshold, else flat (cash)
    # Non-overlapping: take a position every `horizon` bars to match the label horizon
    pos = (proba >= threshold).astype(int)
    step = horizon
    strat_rets, bh_rets = [], []
    for i in range(0, len(fwd_te) - 1, step):
        bh_rets.append(fwd_te[i])
        strat_rets.append(fwd_te[i] if pos[i] == 1 else 0.0)
    if len(strat_rets) < 4:
        return None
    sr = np.array(strat_rets)
    br = np.array(bh_rets)

    def cum(r):
        return (np.prod(1 + r) - 1) * 100

    def sharpe(r):
        return (r.mean() / r.std() * np.sqrt(252 / horizon)) if r.std() > 0 else 0

    def mdd(r):
        eq = np.cumprod(1 + r)
        peak = np.maximum.accumulate(eq)
        return ((eq - peak) / peak).min() * 100

    n_trades = int(pos[::step].sum())
    return {
        "symbol": symbol,
        "n_periods": len(sr),
        "n_long": n_trades,
        "strat_cum%": round(cum(sr), 2),
        "bh_cum%": round(cum(br), 2),
        "alpha%": round(cum(sr) - cum(br), 2),
        "strat_sharpe": round(sharpe(sr), 3),
        "bh_sharpe": round(sharpe(br), 3),
        "strat_maxdd%": round(mdd(sr), 2),
        "bh_maxdd%": round(mdd(br), 2),
        "hit_rate%": round((sr > 0).mean() * 100, 1),
        "beat_bh": cum(sr) > cum(br),
    }


# ── Data loading ──────────────────────────────────────────────────────────────


def load_stocks(market: str, max_stocks: int) -> dict:
    files = list(CACHE_DIR.glob("*.parquet")) + list(REF_DIR.glob("*.parquet"))
    if market == "IN":
        files = [f for f in files if f.stem.endswith(".NS")]
    elif market == "US":
        files = [f for f in files if not (f.stem.endswith(".NS") or f.stem.endswith(".BO"))]
    if max_stocks:
        files = files[:max_stocks]
    out, seen = {}, set()
    for f in files:
        if f.stem in seen:
            continue
        try:
            df = pd.read_parquet(f)
            if len(df) >= 600:
                out[f.stem] = df[["Open", "High", "Low", "Close", "Volume"]].ffill().bfill()
                seen.add(f.stem)
        except Exception:
            pass
    return out


# ── Main ──────────────────────────────────────────────────────────────────────


def main(market="IN", horizon=21, threshold=0.55, max_stocks=300):
    print(f"\n{'#'*78}")
    print(
        f"  DL-INFORMED STRATEGY EVALUATION — {market} | horizon={horizon}d "
        f"| long-threshold P(up)≥{threshold}"
    )
    print("  Framing: directional classification → mechanical long/flat strategy")
    print("  Benchmark: buy-and-hold (Fister 2019; Olorunnimbe 2022; Toichatturat 2024)")
    print(f"{'#'*78}\n{DISCLAIMER}\n")
    if not _SK_OK:
        print("❌ pip install scikit-learn")
        return

    print("STEP 1 — Load data")
    stocks = load_stocks(market, max_stocks)
    print(f"  {len(stocks)} stocks with ≥600 bars")
    if len(stocks) < 20:
        print("Insufficient data.")
        return

    print("\nSTEP 2 — Walk-forward train + strategy backtest (GradientBoosting)")
    args = [(s, df, horizon, threshold) for s, df in stocks.items()]
    rows = parallel_map(
        lambda a: evaluate_stock(a), args, workers=8, progress_every=100, label="stocks"
    )
    res = pd.DataFrame([r for r in rows if r])
    if res.empty:
        print("No valid backtests.")
        return

    # ── STEP 3 — Aggregate economic results ──────────────────────────────────
    print(f"\nSTEP 3 — Economic Evaluation ({len(res)} stocks)")
    print("=" * 78)
    beat = res["beat_bh"].mean() * 100
    print("\n  📊 STRATEGY vs BUY-AND-HOLD (out-of-sample, last 30% of history):")
    print(f"  {'Metric':<28} {'Strategy':>12} {'Buy&Hold':>12} {'Edge':>10}")
    print("  " + "─" * 64)
    print(
        f"  {'Mean cumulative return%':<28} {res['strat_cum%'].mean():>11.2f} "
        f"{res['bh_cum%'].mean():>12.2f} {res['alpha%'].mean():>+9.2f}"
    )
    print(
        f"  {'Median cumulative return%':<28} {res['strat_cum%'].median():>11.2f} "
        f"{res['bh_cum%'].median():>12.2f} "
        f"{res['strat_cum%'].median()-res['bh_cum%'].median():>+9.2f}"
    )
    print(
        f"  {'Mean Sharpe':<28} {res['strat_sharpe'].mean():>11.3f} "
        f"{res['bh_sharpe'].mean():>12.3f} "
        f"{res['strat_sharpe'].mean()-res['bh_sharpe'].mean():>+9.3f}"
    )
    print(
        f"  {'Mean max drawdown%':<28} {res['strat_maxdd%'].mean():>11.2f} "
        f"{res['bh_maxdd%'].mean():>12.2f} "
        f"{res['strat_maxdd%'].mean()-res['bh_maxdd%'].mean():>+9.2f}"
    )
    print(f"\n  % of stocks where strategy BEAT buy-and-hold: {beat:.1f}%")

    # Verdict (honest — per s10462 emphasis on economic significance)
    print("\n  ── VERDICT ──")
    edge = res["alpha%"].mean()
    dd_edge = res["bh_maxdd%"].mean() - res["strat_maxdd%"].mean()  # positive = less DD
    if beat > 55 and edge > 0:
        print(
            f"  Strategy shows a return edge (+{edge:.1f}% avg alpha, beats BH "
            f"{beat:.0f}% of the time)."
        )
    elif dd_edge > 5:
        print(
            f"  Strategy does NOT beat BH on return, but cuts drawdown by "
            f"{dd_edge:.1f}pp — a risk-reduction (timing) benefit, consistent"
        )
        print(
            "  with Fister et al.'s finding that the value is in risk-adjusted, "
            "not raw, returns."
        )
    else:
        print(
            f"  Strategy does NOT reliably beat buy-and-hold (alpha {edge:+.1f}%, "
            f"beats BH {beat:.0f}%)."
        )
        print("  Confirms semi-efficient markets (Olorunnimbe 2022): directional ML")
        print("  on price-only features rarely earns economic alpha out-of-sample.")

    # Top performers
    print("\n  🏆 Top 10 by out-of-sample alpha vs buy-and-hold:")
    print(f"  {'Symbol':<14} {'Strat%':>9} {'BH%':>9} {'Alpha%':>9} {'Sharpe':>8} {'MaxDD%':>9}")
    print("  " + "─" * 62)
    for _, r in res.nlargest(10, "alpha%").iterrows():
        print(
            f"  {r['symbol']:<14} {r['strat_cum%']:>8.1f} {r['bh_cum%']:>9.1f} "
            f"{r['alpha%']:>+8.1f} {r['strat_sharpe']:>8.2f} {r['strat_maxdd%']:>8.1f}"
        )

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT_DIR / f"dl_strategy_{market}_{ts}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"DISCLAIMER": [DISCLAIMER]}).to_excel(w, "DISCLAIMER", index=False)
        res.sort_values("alpha%", ascending=False).to_excel(w, "Strategy_Results", index=False)
    print(f"\n  📊 → {path}")
    print(f"\n{'='*78}\n  {DISCLAIMER}\n{'='*78}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="DL-informed directional strategy backtest")
    p.add_argument("--market", choices=["IN", "US", "ALL"], default="IN")
    p.add_argument("--horizon", type=int, default=21, help="Forward horizon (days)")
    p.add_argument("--threshold", type=float, default=0.55, help="P(up) to go long")
    p.add_argument("--max", type=int, default=300)
    a = p.parse_args()
    main(market=a.market, horizon=a.horizon, threshold=a.threshold, max_stocks=a.max)
