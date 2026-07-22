#!/usr/bin/env python3
# Golden Crossover — 50-DMA crossing above 200-DMA.
from __future__ import annotations

from .base import Result, StockData, sma

META = {
    "name": "Golden Crossover",
    "slug": "golden_crossover",
    "category": "technical",
    "description": "50-day moving average crosses above the 200-day moving average.",
    "needs": "price",
}


def screen(s: StockData) -> Result | None:
    c = s.close
    if c is None or len(c) < 205:
        return None
    d50_today, d200_today = sma(c, 50), sma(c, 200)
    d50_prev, d200_prev = sma(c.iloc[:-1], 50), sma(c.iloc[:-1], 200)
    if None in (d50_today, d200_today, d50_prev, d200_prev):
        return None
    crossed = d50_prev <= d200_prev and d50_today > d200_today
    above = d50_today > d200_today
    return Result(
        s.symbol,
        META["slug"],
        passed=crossed,
        score=round((d50_today / d200_today - 1) * 100, 2),
        metrics={
            "DMA50": round(d50_today, 2),
            "DMA200": round(d200_today, 2),
            "DMA50_above_200": above,
            "LTP": s.ltp,
        },
        note="GOLDEN_CROSS" if crossed else ("above" if above else "below"),
    )
