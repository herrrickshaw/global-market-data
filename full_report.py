#!/usr/bin/env python3
# full_report.py — all-11-screener India report + top-150 across the other markets.
# Educational/research only. NOT investment advice.
from __future__ import annotations

import glob
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

import custom_screener as cs
import liquidity as liq
import screener_kit as kit
import strategies as st
from strategies.base import StockData

OTHERS = [m for m in kit.MARKETS if m != "IN"]
MOMENTUM = {"above_200dma": ("==", True), "dist_52w_high": ("<", 10), "ret_126": (">", 10)}


def _india_fundamentals() -> dict:
    """Per-symbol fundamentals for India from the latest full scan sheet + CCC."""
    f: dict[str, dict] = {}
    files = sorted(
        glob.glob("/Users/umashankar/Downloads/data/indian_full_scan/indian_full_scan/*.xlsx"),
        reverse=True,
    )
    fd = None
    for path in files:  # newest file that actually has a Fundamentals sheet
        try:
            if "Fundamentals" in pd.ExcelFile(path).sheet_names:
                fd = pd.read_excel(path, sheet_name="Fundamentals")
                break
        except Exception:
            continue
    if fd is not None:
        for _, r in fd.iterrows():
            sym = str(r.get("Symbol", "")).strip()
            if sym:
                f[sym] = {
                    "piotroski_score": r.get("Piotroski_Score"),
                    "piotroski_strong": str(r.get("Piotroski_Strong", "")).upper() == "YES",
                    "coffee_can": str(r.get("CoffeeCan", "")).upper() == "PASS",
                }
    try:
        from screener_in import ccc_map

        for s, v in ccc_map().items():
            f.setdefault(s, {})["ccc"] = v
    except Exception:
        pass
    return f


def _enrich_authenticated(fmap: dict, symbols: list, limit: int = 300) -> dict:
    """If screener.in creds are set, pull real fundamentals for the most-liquid
    India symbols and merge them in (so piotroski/garp/etc. can run). No-op with
    a clear note when creds are absent — the bhavcopy+scan path still stands."""
    try:
        import screener_in_auth as auth

        auth.session()  # raises AuthError when SCREENER_EMAIL/PASSWORD unset
        af = auth.fundamentals_batch(symbols[:limit], verbose=False)
        for s, fd in af.items():
            fmap.setdefault(s, {}).update(fd)
        print(f"  screener.in auth: enriched {len(af)} India symbols with fundamentals")
    except Exception as e:
        print(f"  screener.in auth: skipped ({type(e).__name__}) — bhavcopy+scan fallback")
    return fmap


def india_all_screeners(min_turnover=1_000_000, use_auth=True, auth_limit=300) -> dict:
    """Run every one of the 11 strategies on India; return {slug: DataFrame}.

    If use_auth and screener.in creds are set, fundamental screeners run on real
    financials (top `auth_limit` most-liquid names); otherwise they fall back to
    the scan-derived Piotroski/Coffee-Can results."""
    data = kit.load("IN", min_turnover)
    fmap = _india_fundamentals()
    if use_auth:
        # most-liquid first, so a capped auth fetch covers the tradable names
        try:
            from liquidity import liquid_symbols

            ordered = [s for s in liquid_symbols("IN", min_turnover) if s in data]
        except Exception:
            ordered = list(data)
        fmap = _enrich_authenticated(fmap, ordered or list(data), auth_limit)
    stocks = [StockData(s, "IN", ohlcv=d, fundamentals=fmap.get(s, {})) for s, d in data.items()]
    out = {}
    for slug, mod in st.STRATEGIES.items():
        rows = [r.row() for sd in stocks if (r := mod.screen(sd)) and r.passed]
        df = pd.DataFrame(rows)
        if not df.empty:
            df = liq.annotate(df.assign(Market="IN"))
            if "Score" in df.columns:
                df = df.sort_values("Score", ascending=False)
        out[slug] = df.reset_index(drop=True)
    # scan-sheet fundamental screeners India can't compute from bhavcopy
    if fmap:
        piostrong = sorted(s for s, v in fmap.items() if v.get("piotroski_strong"))
        coffee = sorted(s for s, v in fmap.items() if v.get("coffee_can"))
        out["_scan_piotroski_strong"] = piostrong
        out["_scan_coffee_can"] = coffee
    return out


def top150_others(min_turnover=1_000_000, top=150) -> pd.DataFrame:
    """Top stocks passing the momentum filter across the 20 non-India markets."""
    rows = []
    for m in OTHERS:
        data = kit.load(m, min_turnover)
        stocks = [StockData(s, m, ohlcv=d) for s, d in data.items()]
        df = cs.screen(
            stocks,
            MOMENTUM,
            rank_by="ret_126",
            show=["ltp", "ret_126", "ret_252", "rsi14", "dist_52w_high"],
        )
        if not df.empty:
            rows.append(df)
        print(f"  {m}: {0 if df.empty else len(df)} pass momentum")
    allm = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if allm.empty:
        return allm
    allm = allm[allm["ret_126"] <= 900]  # drop split/bad-print artifacts
    allm = liq.annotate(allm).sort_values("ret_126", ascending=False).head(top)
    return allm.reset_index(drop=True)


if __name__ == "__main__":
    print("=== INDIA — all 11 screeners ===")
    ind = india_all_screeners()
    for slug, df in ind.items():
        if slug.startswith("_scan_"):
            print(f"  [scan] {slug[6:]}: {len(df)} names")
            continue
        n = 0 if df is None or df.empty else len(df)
        top = ", ".join(df["Symbol"].head(5)) if n else "—"
        print(f"  {slug:22} {n:>4}  {top}")
    print("\n=== TOP 150 across other 20 markets (momentum) ===")
    g = top150_others()
    print(f"  total: {len(g)}")
    g.to_csv("results_top150_global.csv", index=False)
    print(g.head(20).to_string(index=False) if not g.empty else "none")
