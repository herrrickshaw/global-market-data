#!/usr/bin/env python3
# auto_screener.py
# ================
# A hybrid ML engine that INVENTS new screens tuned to current market conditions
# and liquidity — the three-layer design from ML_Stock_Screening_System.docx,
# adapted to run on the precomputed serving views (no heavy training loop):
#
#   SUPERVISED anchor  — the existing 11 screener strategies define the "known
#                        universe" of good stocks (the labels). known_good(market).
#   UNSUPERVISED       — cluster stocks in feature space (KMeans over the serving
#     discovery          features + liquidity). The cluster most enriched with
#                        known-good, liquid, high-return names becomes a NEW screen:
#                        its per-feature bounds → a rule usable by screen_fast().
#                        This "finds new patterns based on the existing patterns".
#   REINFORCEMENT      — evaluate the discovered screen vs the known universe. If it
#     correction         DEVIATES too far (low overlap / illiquid / wrong size), an
#                        RL corrector kicks in: a reward-driven policy search over the
#                        thresholds (reward = overlap + liquidity + return − deviation,
#                        mirroring the doc's reward function) pulls it back toward the
#                        validated universe. Otherwise the discovery is accepted as-is.
#
# Adapts to regime: in a Bear tape it hard-requires trend/low-drawdown; in a Bull
# tape it lets momentum breathe. Recommendations are saved + explained.
#
#   python3 auto_screener.py --market IN            # recommend a new screen for IN
#   python3 auto_screener.py --market US --top 15
#
# ⚠️ Research/education only. Discovered screens are historical associations, not
#    predictions or advice.

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import serving_layer as sl

OUT_DIR = Path(__file__).parent / "cache_seed" / "discovered_screens"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# features the clustering + screens operate on
FEATS = ["RSI14", "PctFromHigh", "PctFromLow", "Ret21", "Ret63", "Ret126", "Ret252"]
BOOLS = ["Above200DMA", "GoldenCross"]
LIQ_GOOD = {"High", "Medium"}


# ── SUPERVISED anchor: the known universe from existing screens ──────────────────
def known_good(market: str) -> set:
    """The validated 'labels' — symbols the existing screens would flag, computed
    from the deep serving view using each strategy's published gates (a faithful,
    fast proxy for darvas / golden_crossover / coffee_can-momentum). For IN it also
    folds in the live screener.in CCC screen. This is the supervised anchor the
    unsupervised discovery is measured against.

    If a trained supervised model (ml_supervised, doc Layer 1) exists for the market,
    its predicted Buy/Strong-Buy names are folded in as additional labels."""
    df = sl.serving(market)
    if df.empty:
        return set()
    supervised: set = set()
    try:
        from ml_supervised import known_good_supervised

        supervised = known_good_supervised(market)
    except Exception:
        pass
    liq = df["Liquidity"].isin(LIQ_GOOD) if "Liquidity" in df else True
    darvas = df["Above200DMA"] & (df["PctFromHigh"] > -5) & (df["Ret126"] > 0)  # near-high breakout
    golden = df["GoldenCross"] & df["Above200DMA"]                              # golden crossover
    quality = (df["Ret252"] > 15) & df["Above200DMA"]                          # coffee-can momentum
    good = set(df[liq & (darvas | golden | quality)]["Symbol"])
    good |= supervised
    # fold in Screener.in popular-screen universe (validation module) as labels
    try:
        from validation import known_universe

        good |= known_universe(market)
    except Exception:
        pass
    # fold in names passing the FUNDAMENTAL screen formulas on real financials
    # (cached SEC/screener.in data — no network here). Grounds the anchor in
    # quality/value fundamentals, not just price gates.
    try:
        good |= fundamental_anchor(market)
    except Exception:
        pass
    if market == "IN":
        try:
            from datalink import ccc_map_cached

            good |= set(ccc_map_cached().keys())
        except Exception:
            pass
    return good


def fundamental_anchor(market: str) -> set:
    """Names passing the fundamental screen formulas on cached financials (Piotroski,
    Magic Formula, ROCE, ROE, coffee-can). Empty until fundamentals are fetched
    (fundamental_metrics.py); read-only/offline here."""
    import fundamental_metrics as fmet

    path = fmet.CACHE / f"{market}.parquet"
    if not path.exists():
        return set()
    df = fmet._enrich(pd.read_parquet(path))
    if df.empty:
        return set()
    good: set = set()
    for name in ("piotroski_9", "high_roce", "high_roe", "magic_formula", "coffee_can"):
        try:
            good |= set(df.loc[fmet.FUND_SCREENS[name](df).fillna(False), "Symbol"])
        except Exception:
            continue
    return good


# ── market condition + liquidity regime ─────────────────────────────────────────
def _ret(df: pd.DataFrame) -> pd.Series:
    """Trailing return, Ret252 where available else Ret126 (young seeds)."""
    r = df["Ret252"]
    return r.fillna(df["Ret126"]) if "Ret126" in df else r


def regime(df: pd.DataFrame) -> dict:
    breadth = float((df["Above200DMA"]).mean()) if "Above200DMA" in df else 0.0
    med_rsi = float(df["RSI14"].median())
    med_ret = float(_ret(df).median())
    liq_share = float(df["Liquidity"].isin(LIQ_GOOD).mean()) if "Liquidity" in df else 0.0
    if breadth > 0.55 and med_ret > 0:
        tape = "Bull"
    elif breadth < 0.35 or med_ret < -5:
        tape = "Bear"
    else:
        tape = "Neutral"
    return {"tape": tape, "breadth": breadth, "med_rsi": med_rsi,
            "med_ret252": med_ret, "liquid_share": liq_share}


def _matrix(df: pd.DataFrame) -> Tuple[np.ndarray, pd.DataFrame]:
    x = df[FEATS].copy()
    for b in BOOLS:
        x[b] = df[b].astype(float)
    x = x.replace([np.inf, -np.inf], np.nan)
    # keep rows with at least the short-horizon features; impute the rest (e.g.
    # Ret252 is NaN on markets with <252 bars) with the column median so young
    # seeds still cluster instead of being dropped wholesale.
    x = x.dropna(subset=["RSI14", "PctFromHigh", "Ret63"])
    x = x.fillna(x.median(numeric_only=True)).fillna(0.0)
    return x.values, df.loc[x.index]


# ── UNSUPERVISED discovery: cluster → candidate screen ──────────────────────────
def discover(market: str, k: int = 6, min_turnover_usd: float = 1_000_000) -> dict:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    full = sl.serving(market)
    if full.empty:
        raise RuntimeError(f"no serving view for {market} — run serving_layer --refresh")
    df = full[full["Liquidity"].isin(LIQ_GOOD)] if "Liquidity" in full else full
    reg = regime(full)
    good = known_good(market)

    X, dfx = _matrix(df)
    if len(dfx) < k * 5:
        raise RuntimeError(f"too few liquid names in {market} ({len(dfx)})")
    Z = StandardScaler().fit_transform(X)
    labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(Z)
    dfx = dfx.assign(_cluster=labels)

    # score each cluster: enrichment of known-good × liquidity × forward-return proxy
    best, best_score = None, -1e9
    for c, g in dfx.groupby("_cluster"):
        overlap = len(set(g["Symbol"]) & good) / max(len(g), 1)
        liq = float(g["Liquidity"].isin(LIQ_GOOD).mean()) if "Liquidity" in g else 0.0
        ret = float(_ret(g).median())
        score = 2.0 * overlap + 0.5 * liq + 0.01 * ret
        if score > best_score:
            best, best_score = c, score
    cl = dfx[dfx["_cluster"] == best]

    # candidate screen = the cluster's central feature bounds (10th–90th pct)
    rule = _rule_from_cluster(cl, reg)
    return {"market": market, "regime": reg, "cluster_size": int(len(cl)),
            "cluster_overlap": round(len(set(cl["Symbol"]) & good) / max(len(cl), 1), 3),
            "known_good_n": len(good), "rule": rule}


def _rule_from_cluster(cl: pd.DataFrame, reg: dict) -> Dict[str, list]:
    rule: Dict[str, list] = {}
    for f in ("RSI14", "PctFromHigh", "Ret126"):
        lo, hi = float(cl[f].quantile(0.10)), float(cl[f].quantile(0.90))
        rule[f] = [(">=", round(lo, 2)), ("<=", round(hi, 2))]
    # regime-aware guards (the "market conditions" adaptation)
    if reg["tape"] == "Bear":
        rule["Above200DMA"] = [("==", True)]
        rule["PctFromHigh"] = [(">=", -20.0)]  # avoid deep-drawdown falling knives
    elif reg["tape"] == "Bull":
        rule["GoldenCross"] = [("==", True)]
    return rule


# ── screen application + evaluation ──────────────────────────────────────────────
def apply_rule(rule: Dict[str, list], market: str, top: Optional[int] = None) -> pd.DataFrame:
    df = sl.serving(market)
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    for col, conds in rule.items():
        for op, val in conds:
            if col in df.columns and op in sl._OPS:
                mask &= sl._OPS[op](df[col], val)
    out = df[mask]
    if "Liquidity" in out:
        out = out[out["Liquidity"].isin(LIQ_GOOD)]
    out = out.sort_values("Ret252", ascending=False)
    return out.head(top).reset_index(drop=True) if top else out.reset_index(drop=True)


def evaluate(rule: Dict[str, list], market: str, good: set, target=(15, 60)) -> dict:
    """Reward mirrors the doc: overlap w/ known universe + liquidity + return, minus
    a deviation penalty when the selection strays (wrong size / illiquid / no overlap)."""
    sel = apply_rule(rule, market)
    n = len(sel)
    if n == 0:
        return {"reward": -3.0, "n": 0, "overlap": 0.0, "liq": 0.0, "ret": 0.0, "deviation": 1.0}
    picks = set(sel["Symbol"])
    overlap = len(picks & good) / n                       # precision vs known universe
    liq = float(sel["Liquidity"].isin(LIQ_GOOD).mean())
    ret = float(_ret(sel).median())
    lo, hi = target
    size_dev = 0.0 if lo <= n <= hi else (min(abs(n - lo), abs(n - hi)) / hi)
    deviation = (1 - overlap) * 0.6 + (1 - liq) * 0.2 + min(size_dev, 1.0) * 0.2
    reward = 2.0 * overlap + 0.5 * liq + 0.01 * ret - 1.5 * deviation
    return {"reward": round(reward, 3), "n": n, "overlap": round(overlap, 3),
            "liq": round(liq, 3), "ret": round(ret, 2), "deviation": round(deviation, 3)}


# ── REINFORCEMENT correction: reward-driven policy search over thresholds ─────────
def rl_refine(rule: Dict[str, list], market: str, good: set,
              iters: int = 40, verbose: bool = True) -> Tuple[Dict[str, list], dict]:
    """Kicks in when the discovered screen deviates too far. Perturbs the numeric
    thresholds (cross-entropy / hill-climb), keeping only changes that raise the
    reward — pulling the screen back toward the validated universe."""
    rng = np.random.default_rng(42)
    best_rule = json.loads(json.dumps(rule))            # deep copy
    best = evaluate(best_rule, market, good)
    numeric = [(c, i) for c, conds in rule.items() for i, (op, v) in enumerate(conds)
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for _ in range(iters):
        cand = json.loads(json.dumps(best_rule))
        for c, i in numeric:
            op, v = cand[c][i]
            step = (abs(v) * 0.15 + 1.0) * rng.normal()
            cand[c][i] = [op, round(v + step, 2)]
        r = evaluate(cand, market, good)
        if r["reward"] > best["reward"]:
            best_rule, best = cand, r
    if verbose:
        print(f"  RL correction: reward {evaluate(rule, market, good)['reward']} → {best['reward']}")
    return best_rule, best


# ── orchestration ────────────────────────────────────────────────────────────────
def recommend(market: str, top: int = 15, min_reward: float = 0.6,
              verbose: bool = True) -> dict:
    disc = discover(market)
    good = known_good(market)
    ev = evaluate(disc["rule"], market, good)
    rule, refined = disc["rule"], ev
    used_rl = False
    # RL kicks in when the discovery deviates too far from the known universe
    if ev["reward"] < min_reward or ev["deviation"] > 0.5:
        rule, refined = rl_refine(disc["rule"], market, good, verbose=verbose)
        used_rl = True

    picks = apply_rule(rule, market, top=top)
    rec = {
        "market": market,
        "regime": disc["regime"],
        "discovered_rule": disc["rule"],
        "final_rule": rule,
        "rl_correction_applied": used_rl,
        "metrics": refined,
        "picks": picks["Symbol"].tolist() if not picks.empty else [],
    }
    (OUT_DIR / f"{market}.json").write_text(json.dumps(rec, indent=2, default=str))

    if verbose:
        r = disc["regime"]
        print(f"\n=== auto-screener recommendation: {market} ===")
        print(f"  regime: {r['tape']}  breadth={r['breadth']:.0%}  medRSI={r['med_rsi']:.0f}  "
              f"medRet252={r['med_ret252']:.0f}%  liquid={r['liquid_share']:.0%}")
        print(f"  discovered from cluster ({disc['cluster_size']} names, "
              f"{disc['cluster_overlap']:.0%} overlap w/ {disc['known_good_n']} known-good)")
        print(f"  RL correction applied: {used_rl}")
        print(f"  final rule: {json.dumps(rule)}")
        print(f"  metrics: reward={refined['reward']} n={refined['n']} "
              f"overlap={refined['overlap']} liq={refined['liq']} medRet={refined['ret']}%")
        if not picks.empty:
            cols = [c for c in ["Symbol", "Close", "RSI14", "PctFromHigh", "Ret252", "Liquidity"]
                    if c in picks.columns]
            print("\n  top picks:\n" + picks[cols].to_string(index=False))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Hybrid auto-screener (unsupervised + RL)")
    ap.add_argument("--market", default="IN")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-reward", type=float, default=0.6)
    args = ap.parse_args()
    recommend(args.market, top=args.top, min_reward=args.min_reward)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
