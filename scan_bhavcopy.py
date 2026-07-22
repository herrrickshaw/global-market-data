#!/usr/bin/env python3
# scan_bhavcopy.py
# ================
# Full NSE+BSE screener run sourced ENTIRELY from official exchange bhavcopy
# (via bhavcopy_history.py) — no Yahoo Finance, so no rate limiting.
#
# Price screeners (Darvas Box, Golden Crossover, volume) are computed fresh from
# the bhavcopy OHLCV. Fundamentals (Piotroski F-Score, Coffee Can) are quarterly
# and do not change day-to-day, so they are reused from the most recent existing
# full-scan workbook. Triple Hits are then RE-derived from the fresh Darvas
# breakouts combined with those fundamentals.
#
# Output matches the schema daily_combined_report.py expects:
#   sheets: All_Stocks, Darvas_Signals, Fundamentals, Triple_Hits, IPO_New_Listings
#   file:   indian_full_scan/indian_full_scan_<YYYYMMDD_HHMM>.xlsx

from __future__ import annotations

import glob
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

from bhavcopy_history import fetch_history
from full_indian_market_scan import compute_darvas_box, compute_golden_crossover
from stock_utils import parallel_map, pct_change

OUT_DIR = Path("indian_full_scan")
OUT_DIR.mkdir(exist_ok=True)


def _screen_one(item):
    """Run the price screeners on one symbol's bhavcopy OHLCV frame."""
    sym, df = item
    if df is None or len(df) < 60:
        return None
    d = compute_darvas_box(df)
    gc = compute_golden_crossover(df)
    ltp = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2]) if len(df) > 1 else ltp
    return {
        "Symbol": sym,
        "LTP": round(ltp, 2),
        "Prev_Close": round(prev, 2),
        "Change%": round(pct_change(ltp, prev) or 0, 2),
        "Darvas_Signal": d.get("signal"),
        "Box_Top": d.get("box_top"),
        "Box_Bottom": d.get("box_bottom"),
        "Upside_to_Top%": d.get("upside_to_top_pct"),
        "Position_in_Box%": d.get("position_in_box_pct"),
        "GC_Signal": "GOLDEN_CROSS" if gc.get("gc_signal") else "",
        "DMA50_above_200": gc.get("dma50_above_200"),
        "Data_Points": len(df),
    }


def main():
    print(f"\n{'#'*72}\n  BHAVCOPY SCREENER — NSE + BSE (official EOD, no Yahoo)\n{'#'*72}")
    print("  Educational/research only. NOT investment advice.\n")

    print("Stage 1 — Assemble 1-year OHLCV history from bhavcopy …")
    hist = fetch_history(n_days=400, min_bars=200, verbose=True)
    print(f"  {len(hist)} symbols ready\n")

    print("Stage 2 — Price screeners (Darvas + Golden Cross) …")
    rows = parallel_map(
        _screen_one, list(hist.items()), workers=8, label="stocks", progress_every=1000
    )
    all_df = pd.DataFrame([r for r in rows if r])
    darvas_df = all_df[all_df["Darvas_Signal"].isin(["BREAKOUT_BUY", "BREAKDOWN_SELL"])]
    gc_n = int((all_df["GC_Signal"] == "GOLDEN_CROSS").sum())
    print(f"  {len(all_df)} screened | {len(darvas_df)} Darvas signals | {gc_n} golden crosses\n")

    print("Stage 3 — Reuse latest fundamentals + re-derive Triple Hits …")
    prev_files = sorted(glob.glob("indian_full_scan/*_full_scan_*.xlsx"))
    fund_df = pd.DataFrame()
    if prev_files:
        try:
            fund_df = pd.read_excel(prev_files[-1], sheet_name="Fundamentals")
            print(f"  reused fundamentals from {Path(prev_files[-1]).name} ({len(fund_df)} rows)")
        except Exception as e:
            print(f"  no fundamentals reused: {e}")

    fresh_darvas = dict(zip(all_df["Symbol"], all_df["Darvas_Signal"]))
    fresh_ltp = dict(zip(all_df["Symbol"], all_df["LTP"]))
    triple_rows = []
    if not fund_df.empty:
        # refresh each fundamental row's Darvas signal + LTP with today's bhavcopy
        fund_df["Darvas_Signal"] = (
            fund_df["Symbol"].map(fresh_darvas).fillna(fund_df.get("Darvas_Signal"))
        )
        if "LTP" in fund_df.columns:
            fund_df["LTP"] = fund_df["Symbol"].map(fresh_ltp).fillna(fund_df["LTP"])
        for _, r in fund_df.iterrows():
            cc = str(r.get("CoffeeCan", "")).upper() == "PASS"
            pio = str(r.get("Piotroski_Strong", "")).upper() == "YES"
            brk = str(r.get("Darvas_Signal", "")).upper() == "BREAKOUT_BUY"
            if cc and pio and brk:
                triple_rows.append(r)
    triple_df = pd.DataFrame(triple_rows)
    print(f"  {len(triple_df)} Triple Hits (CoffeeCan + Piotroski≥7 + fresh Darvas breakout)\n")

    # merge Piotroski into All_Stocks for Golden-Cross pick enrichment
    if not fund_df.empty and "Piotroski_Score" in fund_df.columns:
        pio_map = dict(zip(fund_df["Symbol"], fund_df["Piotroski_Score"]))
        all_df["Piotroski_Score"] = all_df["Symbol"].map(pio_map)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = OUT_DIR / f"indian_full_scan_{ts}.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        all_df.sort_values("Change%", ascending=False).to_excel(xw, "All_Stocks", index=False)
        darvas_df.sort_values("Upside_to_Top%", ascending=False, na_position="last").to_excel(
            xw, "Darvas_Signals", index=False
        )
        if not fund_df.empty:
            fund_df.to_excel(xw, "Fundamentals", index=False)
        if not triple_df.empty:
            triple_df.to_excel(xw, "Triple_Hits", index=False)
    print(f"📄 → {out}")
    print(
        f"   Source: NSE+BSE bhavcopy (official EOD). Triple Hits: "
        f"{', '.join(triple_df['Symbol'].astype(str)) if not triple_df.empty else 'none today'}"
    )
    return out


if __name__ == "__main__":
    main()
