#!/usr/bin/env python3
# Darvas Scan — price-volume breakout, within ~10% of 52-week high on heavy volume.
from __future__ import annotations

from .base import Result, StockData

META = {
    "name": "Darvas Scan",
    "slug": "darvas",
    "category": "technical",
    "description": "Price-volume breakout: near 52-week high with above-average "
    "volume (Darvas box breakout).",
    "needs": "price",
}

NEAR_HIGH_PCT = 10.0  # within 10% of 52-week high
VOL_MULT = 1.5  # volume >= 1.5x 20-day average


def screen(s: StockData) -> Result | None:
    df = s.ohlcv
    if df is None or len(df) < 60:
        return None
    close = df["Close"]
    win = df.tail(252)
    hi_52w = float(win["High"].max()) if "High" in win else float(win["Close"].max())
    ltp = float(close.iloc[-1])
    if hi_52w <= 0:
        return None
    off_high = (hi_52w - ltp) / hi_52w * 100
    near_high = off_high <= NEAR_HIGH_PCT
    vol_ok, vmult = False, None
    if "Volume" in df.columns and len(df) >= 21:
        v20 = float(df["Volume"].tail(21).iloc[:-1].mean())
        vtoday = float(df["Volume"].iloc[-1])
        if v20 > 0:
            vmult = round(vtoday / v20, 2)
            vol_ok = vtoday >= VOL_MULT * v20
    passed = near_high and vol_ok
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(-off_high, 2),  # closer to high ranks higher
        metrics={
            "LTP": round(ltp, 2),
            "High_52w": round(hi_52w, 2),
            "Off_High%": round(off_high, 2),
            "Vol_x20d": vmult,
        },
        note="BREAKOUT" if passed else ("near_high" if near_high else ""),
    )
