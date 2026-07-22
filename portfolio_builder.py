# portfolio_builder.py
# ====================
# Builds a mutual-fund-style portfolio Excel from the latest fundamental scan.
#
# OUTPUT EXCEL (sheets, in this order):
#   1. Triple_Hits        — highest-conviction picks first
#   2. Coffee_Can         — quality compounders
#   3. Other_Picks        — remaining screener hits
#   4. Risk_Metrics       — per-stock beta/alpha/vol/Sharpe vs Nifty 50
#   5. Efficient_Frontier — mean-variance frontier points + Nifty benchmark
#   6. Optimal_Portfolio  — max-Sharpe & min-variance weights (the "fund")
#   7. DISCLAIMER
#
# METHOD (Modern Portfolio Theory, Markowitz 1952):
#   - Daily returns from the 5-yr Parquet cache (last ~2 yrs used).
#   - Beta_i  = Cov(r_i, r_mkt) / Var(r_mkt)   vs Nifty 50 (^NSEI).
#   - Alpha_i = ann_return_i − [Rf + Beta_i·(ann_mkt − Rf)]   (CAPM).
#   - Efficient frontier by maximising Sharpe and minimising variance over the
#     covariance matrix, long-only, weights sum to 1 (SciPy SLSQP).
#
# Usage:
#   python portfolio_builder.py --market IN
#   python portfolio_builder.py --market IN --max-holdings 25 --rf 0.065
#
# ⚠️ Educational/research only. MPT assumes returns are stationary & normally
#    distributed (they are not). Backward-looking optimisation overfits to the
#    sample. NOT investment advice.

from __future__ import annotations

import argparse
import glob
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from scipy.optimize import minimize

CACHE_DIR = Path.home() / "Downloads" / "market_cache" / "ohlc"
INDEX_DIR = Path.home() / "Downloads" / "market_cache" / "index"
REF_DIR = Path.home() / "nse_screener_reference" / "ohlc_cache"
OUT_DIR = Path("./portfolio_results")
OUT_DIR.mkdir(exist_ok=True)

TRADING_DAYS = 252
LOOKBACK = 504  # ~2 years of daily returns for the covariance estimate

DISCLAIMER = (
    "⚠️  Modern Portfolio Theory optimisation on historical returns. "
    "Assumes stationarity & normality (false); mean-variance weights "
    "overfit the sample. Beta is historical. Educational/research only. "
    "NOT investment advice. Consult a SEBI-registered advisor."
)


# ── Load picks (ordered: triple → coffee can → other) ─────────────────────────


def load_picks(market: str) -> dict:
    scan_dir = "indian_full_scan" if market == "IN" else "us_full_scan"
    files = sorted(glob.glob(f"{scan_dir}/*_full_scan_*.xlsx"))
    if not files:
        return {"triple": [], "coffee": [], "other": []}
    f, xl = files[-1], pd.ExcelFile(files[-1])

    triple, coffee, other = [], [], []

    if "Triple_Hits" in xl.sheet_names:
        triple = [
            str(r.get("Symbol", "")).strip()
            for _, r in pd.read_excel(f, sheet_name="Triple_Hits").iterrows()
            if str(r.get("Symbol", "")).strip()
        ]

    # Coffee Can listed independently (may overlap triple — shown in its own sheet)
    if "Coffee_Can" in xl.sheet_names:
        coffee = [
            str(r.get("Symbol", "")).strip()
            for _, r in pd.read_excel(f, sheet_name="Coffee_Can").iterrows()
            if str(r.get("Symbol", "")).strip()
        ]
    elif "Fundamentals" in xl.sheet_names:
        fd = pd.read_excel(f, sheet_name="Fundamentals")
        coffee = [
            str(r.get("Symbol", "")).strip()
            for _, r in fd.iterrows()
            if str(r.get("CoffeeCan", "")).upper() == "PASS" and str(r.get("Symbol", "")).strip()
        ]

    # 'Other' excludes anything already in triple or coffee
    seen = set(triple) | set(coffee)

    # Other: remaining fundamental / multi-screen hits
    for sheet in (
        "Multi_Screen_Hits",
        "Piotroski_Strong",
        "Magic_Formula",
        "Bull_Cartel",
        "Fundamentals",
    ):
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(f, sheet_name=sheet)
        for _, r in df.iterrows():
            s = str(r.get("Symbol", "")).strip()
            strong = str(r.get("Piotroski_Strong", "")).upper() == "YES" or sheet != "Fundamentals"
            if s and s not in seen and strong:
                other.append(s)
                seen.add(s)

    return {"triple": triple, "coffee": coffee, "other": other}


# ── Returns + index ───────────────────────────────────────────────────────────


def _norm_index(s: pd.Series) -> pd.Series:
    """Normalise the index to tz-naive calendar dates so stocks align cleanly."""
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s = s.copy()
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")]


def _load_ohlc(sym: str, market: str) -> pd.Series:
    suffix = ".NS" if market == "IN" else ""
    for d in (CACHE_DIR, REF_DIR):
        for cand in (f"{sym}{suffix}.parquet", f"{sym}.parquet"):
            p = d / cand
            if p.exists():
                try:
                    return _norm_index(pd.read_parquet(p)["Close"].astype(float))
                except Exception:
                    pass
    return pd.Series(dtype=float)


def load_returns(symbols: list, market: str) -> pd.DataFrame:
    # Build the full union frame first, THEN slice a common recent window —
    # never .tail() per-stock (stale parquets ending on different dates would
    # create non-overlapping windows that don't align).
    cols = {}
    for s in symbols:
        c = _load_ohlc(s, market)
        if len(c) >= 250:
            cols[s] = c.pct_change().dropna()
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols).sort_index()
    # Restrict to the most recent LOOKBACK trading dates across the whole frame,
    # then keep only stocks that actually traded through that window.
    df = df.tail(LOOKBACK)
    df = df.loc[:, df.notna().mean() >= 0.90]
    return df


def load_index_returns(market: str) -> pd.Series:
    sym = "NSEI" if market == "IN" else "GSPC"
    p = INDEX_DIR / f"{sym}.parquet"
    if p.exists():
        c = _norm_index(pd.read_parquet(p)["Close"].astype(float))
        return c.pct_change().dropna().tail(LOOKBACK)
    return pd.Series(dtype=float)


# ── Risk metrics (CAPM) ───────────────────────────────────────────────────────


def risk_metrics(rets: pd.DataFrame, mkt: pd.Series, rf: float) -> pd.DataFrame:
    rf / TRADING_DAYS
    rows = []
    var_m = mkt.var()
    ann_m = mkt.mean() * TRADING_DAYS
    for s in rets.columns:
        r = rets[s].dropna()
        aligned = pd.concat([r, mkt], axis=1, join="inner").dropna()
        if len(aligned) < 60:
            continue
        ri, rm = aligned.iloc[:, 0], aligned.iloc[:, 1]
        beta = ri.cov(rm) / var_m if var_m > 0 else np.nan
        ann_r = ri.mean() * TRADING_DAYS
        ann_v = ri.std() * np.sqrt(TRADING_DAYS)
        alpha = ann_r - (rf + beta * (ann_m - rf))  # CAPM alpha
        sharpe = (ann_r - rf) / ann_v if ann_v > 0 else 0
        corr = ri.corr(rm)
        rows.append(
            {
                "Symbol": s,
                "Ann_Return%": round(ann_r * 100, 2),
                "Ann_Vol%": round(ann_v * 100, 2),
                "Beta": round(beta, 3),
                "Alpha%": round(alpha * 100, 2),
                "Sharpe": round(sharpe, 3),
                "Corr_Nifty": round(corr, 3),
            }
        )
    return pd.DataFrame(rows)


# ── Efficient frontier (mean-variance) ────────────────────────────────────────


def _port_perf(w, mu, cov, rf):
    r = float(w @ mu)
    v = float(np.sqrt(w @ cov @ w))
    s = (r - rf) / v if v > 0 else 0
    return r, v, s


def _expected_returns(R: pd.DataFrame, shrink: float = 0.5) -> np.ndarray:
    """
    Robust expected returns ("yield expectations") via James-Stein-style shrinkage:
        E[r_i] = (1−λ)·historical_mean_i + λ·cross_sectional_mean
    Raw historical means are noisy and make naive MPT chase the lucky outlier.
    Shrinking each stock's mean toward the universe average (λ=0.5 default) gives
    expectations the optimiser can trust, dampening overfitting.
    """
    hist = R.mean().values * TRADING_DAYS
    grand = hist.mean()
    return (1 - shrink) * hist + shrink * grand


def optimise(
    rets: pd.DataFrame,
    rf: float,
    max_weight: float = 0.15,
    target_return: float = None,
    shrink: float = 0.5,
) -> dict:
    """
    Long-only mean-variance optimisation with:
      • shrinkage expected returns (robust yield expectations),
      • a per-holding weight cap (diversification realism),
      • Max-Sharpe, Min-Variance, AND a Target-Return portfolio that meets a
        specified yield expectation at minimum risk.
    """
    R = rets.loc[:, rets.notna().mean() >= 0.90].dropna()
    if R.shape[1] < 2 or len(R) < 60:
        return {}
    mu = _expected_returns(R, shrink)  # shrunk expectations
    cov = R.cov().values * TRADING_DAYS
    n = len(mu)
    # Cap can't be below 1/n or the simplex is infeasible
    cap = max(max_weight, 1.0 / n + 1e-9)
    bounds = tuple((0, cap) for _ in range(n))
    cons = ({"type": "eq", "fun": lambda w: np.sum(w) - 1},)
    w0 = np.repeat(1 / n, n)

    def port_var(w):
        return w @ cov @ w

    res_s = minimize(
        lambda w: -_port_perf(w, mu, cov, rf)[2],
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=cons,
    )
    res_v = minimize(port_var, w0, method="SLSQP", bounds=bounds, constraints=cons)

    # Frontier (capped)
    frontier = []
    for tr in np.linspace(mu.min(), mu.max(), 25):
        c2 = cons + ({"type": "eq", "fun": lambda w, t=tr: w @ mu - t},)
        rr = minimize(port_var, w0, method="SLSQP", bounds=bounds, constraints=c2)
        if rr.success:
            r, v, s = _port_perf(rr.x, mu, cov, rf)
            frontier.append(
                {
                    "Target_Return%": round(r * 100, 2),
                    "Volatility%": round(v * 100, 2),
                    "Sharpe": round(s, 3),
                }
            )

    syms = list(R.columns)

    def wtable(w):
        return (
            pd.DataFrame({"Symbol": syms, "Weight%": (w * 100).round(2)})
            .query("`Weight%` > 0.5")
            .sort_values("Weight%", ascending=False)
        )

    def pack(res):
        r, v, s = _port_perf(res.x, mu, cov, rf)
        return {"weights": wtable(res.x), "ret": r * 100, "vol": v * 100, "sharpe": s}

    out = {
        "frontier": pd.DataFrame(frontier),
        "max_sharpe": pack(res_s),
        "min_var": pack(res_v),
        "exp_return_method": f"shrinkage λ={shrink}",
        "max_weight": cap,
    }

    # ── Target-return portfolio: meet the yield expectation at minimum risk ──
    if target_return is not None:
        # Max achievable return under the weight cap = take the cap on the
        # highest-return names until weights sum to 1.
        order = np.argsort(mu)[::-1]
        w_max, remaining = np.zeros(n), 1.0
        for i in order:
            wi = min(cap, remaining)
            w_max[i] = wi
            remaining -= wi
            if remaining <= 0:
                break
        max_achievable = float(w_max @ mu)
        tgt = min(target_return, max_achievable)
        c3 = cons + ({"type": "ineq", "fun": lambda w: w @ mu - tgt},)
        rt = minimize(port_var, w0, method="SLSQP", bounds=bounds, constraints=c3)
        p = pack(rt if rt.success else type("o", (), {"x": w_max})())
        p["requested%"] = target_return * 100
        p["max_achievable%"] = round(max_achievable * 100, 2)
        p["feasible"] = target_return <= max_achievable + 1e-6
        out["target"] = p
    return out


# ── Excel assembly ────────────────────────────────────────────────────────────


def build(
    market: str,
    max_holdings: int,
    rf: float,
    max_weight: float = 0.15,
    target_return: float = None,
    shrink: float = 0.5,
):
    print(
        f"\n{'#'*70}\n  PORTFOLIO BUILDER — {market} | Rf={rf:.1%} | benchmark Nifty 50\n{'#'*70}\n  {DISCLAIMER}\n"
    )

    picks = load_picks(market)
    print(
        f"  Picks: {len(picks['triple'])} triple, {len(picks['coffee'])} coffee can, "
        f"{len(picks['other'])} other"
    )

    # Optimisation universe: triple + coffee can + top-other, capped
    universe = picks["triple"] + picks["coffee"] + picks["other"]
    universe = list(dict.fromkeys(universe))[:max_holdings]
    print(f"  Optimisation universe: {len(universe)} holdings (cap {max_holdings})")

    rets = load_returns(universe, market)
    mkt = load_index_returns(market)
    print(f"  Loaded returns for {rets.shape[1]} stocks | Nifty bars: {len(mkt)}")
    if rets.shape[1] < 2 or mkt.empty:
        print("  Insufficient data for optimisation.")
        return

    rm = risk_metrics(rets, mkt, rf)
    opt = optimise(
        rets[[c for c in rets.columns if c in rm["Symbol"].values]],
        rf,
        max_weight=max_weight,
        target_return=target_return,
        shrink=shrink,
    )

    # Ordered holding sheets with risk metrics attached
    def order_sheet(syms):
        return rm[rm["Symbol"].isin(syms)].sort_values("Sharpe", ascending=False)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT_DIR / f"portfolio_{market}_{ts}.xlsx"
    ann_m = mkt.mean() * TRADING_DAYS * 100
    vol_m = mkt.std() * np.sqrt(TRADING_DAYS) * 100

    with pd.ExcelWriter(path, engine="openpyxl") as w:
        order_sheet(picks["triple"]).to_excel(w, "1_Triple_Hits", index=False)
        order_sheet(picks["coffee"]).to_excel(w, "2_Coffee_Can", index=False)
        order_sheet(picks["other"]).to_excel(w, "3_Other_Picks", index=False)
        rm.sort_values("Sharpe", ascending=False).to_excel(w, "4_Risk_Metrics", index=False)
        if opt:
            # Frontier + Nifty benchmark row
            fr = opt["frontier"].copy()
            fr.loc[len(fr)] = {
                "Target_Return%": round(ann_m, 2),
                "Volatility%": round(vol_m, 2),
                "Sharpe": round((ann_m / 100 - rf) / (vol_m / 100), 3),
            }
            fr.to_excel(w, "5_Efficient_Frontier", index=False)
            # Optimal portfolios
            ms, mv = opt["max_sharpe"], opt["min_var"]
            summ_rows = [
                {
                    "Portfolio": "Max-Sharpe (growth fund)",
                    "Ann_Return%": round(ms["ret"], 2),
                    "Vol%": round(ms["vol"], 2),
                    "Sharpe": round(ms["sharpe"], 3),
                    "Holdings": len(ms["weights"]),
                },
                {
                    "Portfolio": "Min-Variance (conservative)",
                    "Ann_Return%": round(mv["ret"], 2),
                    "Vol%": round(mv["vol"], 2),
                    "Sharpe": round(mv["sharpe"], 3),
                    "Holdings": len(mv["weights"]),
                },
            ]
            tg = opt.get("target")
            if tg:
                summ_rows.append(
                    {
                        "Portfolio": f"Target {tg['requested%']:.0f}% (min-risk to meet yield)",
                        "Ann_Return%": round(tg["ret"], 2),
                        "Vol%": round(tg["vol"], 2),
                        "Sharpe": round(tg["sharpe"], 3),
                        "Holdings": len(tg["weights"]),
                    }
                )
            summ_rows.append(
                {
                    "Portfolio": "Nifty 50 (benchmark)",
                    "Ann_Return%": round(ann_m, 2),
                    "Vol%": round(vol_m, 2),
                    "Sharpe": round((ann_m / 100 - rf) / (vol_m / 100), 3),
                    "Holdings": 50,
                }
            )
            ms["weights"].to_excel(w, "6_MaxSharpe_Weights", index=False)
            mv["weights"].to_excel(w, "7_MinVar_Weights", index=False)
            if tg:
                tg["weights"].to_excel(w, "8_Target_Return_Weights", index=False)
            pd.DataFrame(summ_rows).to_excel(w, "9_Portfolio_Summary", index=False)
        pd.DataFrame({"DISCLAIMER": [DISCLAIMER]}).to_excel(w, "DISCLAIMER", index=False)

    print(f"\n  📊 → {path}")
    if opt:
        ms, mv = opt["max_sharpe"], opt["min_var"]
        print(f"\n  PORTFOLIO SUMMARY (vs Nifty 50: {ann_m:.1f}% return, {vol_m:.1f}% vol):")
        print(
            f"  Max-Sharpe 'fund':  return {ms['ret']:.1f}%  vol {ms['vol']:.1f}%  "
            f"Sharpe {ms['sharpe']:.2f}  ({len(ms['weights'])} holdings)"
        )
        print(
            f"  Min-Variance 'fund': return {mv['ret']:.1f}%  vol {mv['vol']:.1f}%  "
            f"Sharpe {mv['sharpe']:.2f}  ({len(mv['weights'])} holdings)"
        )
        print(
            f"  Expected returns: {opt.get('exp_return_method')} | "
            f"max weight cap {opt.get('max_weight',0)*100:.0f}%"
        )
        tg = opt.get("target")
        if tg:
            ok = "✓ met" if tg.get("feasible") else "✗ not feasible (capped)"
            print(
                f"  Target-yield 'fund': requested {tg['requested%']:.0f}% → "
                f"achieved {tg['ret']:.1f}% at {tg['vol']:.1f}% vol [{ok}], "
                f"{len(tg['weights'])} holdings"
            )
        print("\n  Top max-Sharpe weights (capped):")
        for _, r in ms["weights"].head(10).iterrows():
            b = rm[rm["Symbol"] == r["Symbol"]]["Beta"].values
            print(f"    {r['Symbol']:<12} {r['Weight%']:>6.1f}%  beta={b[0] if len(b) else '—'}")
    print(f"\n  {DISCLAIMER}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build MPT portfolio Excel from scan picks")
    p.add_argument("--market", choices=["IN", "US"], default="IN")
    p.add_argument("--max-holdings", type=int, default=25)
    p.add_argument("--rf", type=float, default=0.065, help="Risk-free rate (annual)")
    p.add_argument(
        "--max-weight",
        type=float,
        default=0.15,
        help="Max weight per holding (diversification cap, default 15%)",
    )
    p.add_argument(
        "--target-return",
        type=float,
        default=0.20,
        help="Yield expectation: min-risk weights to achieve this annual return (e.g. 0.20=20%)",
    )
    p.add_argument(
        "--shrink",
        type=float,
        default=0.5,
        help="Expected-return shrinkage 0..1 toward universe mean (default 0.5)",
    )
    a = p.parse_args()
    build(
        a.market,
        a.max_holdings,
        a.rf,
        max_weight=a.max_weight,
        target_return=a.target_return,
        shrink=a.shrink,
    )
