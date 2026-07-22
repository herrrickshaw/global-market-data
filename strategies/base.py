#!/usr/bin/env python3
# strategies/base.py
# ==================
# Shared contract for all screening strategies. Every strategy module exposes:
#   META   — dict(name, slug, category, description, needs)
#   screen(stock) -> Result | None
# where `stock` is a StockData and Result captures pass/fail + metrics.
#
# `needs` declares what data the strategy requires:
#   "price"        — OHLCV only (Golden Cross, Darvas)
#   "fundamentals" — the fundamentals dict (Piotroski, GARP, …)
# so a runner can skip strategies whose data is unavailable for a given stock.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class StockData:
    """Everything a strategy might inspect for one ticker."""

    symbol: str
    market: str = ""
    ohlcv: Optional[pd.DataFrame] = None  # Date-indexed OHLCV
    # fundamentals (best-effort; any may be None)
    fundamentals: dict = field(default_factory=dict)

    # convenience accessors -----------------------------------------------------
    def f(self, key, default=None):
        return self.fundamentals.get(key, default)

    @property
    def close(self) -> Optional[pd.Series]:
        return None if self.ohlcv is None or self.ohlcv.empty else self.ohlcv["Close"]

    @property
    def ltp(self) -> Optional[float]:
        c = self.close
        return None if c is None else float(c.iloc[-1])


@dataclass
class Result:
    symbol: str
    strategy: str
    passed: bool
    score: Optional[float] = None  # strategy-specific score / rank key
    metrics: dict = field(default_factory=dict)
    note: str = ""

    def row(self) -> dict:
        return {
            "Symbol": self.symbol,
            "Strategy": self.strategy,
            "Pass": "YES" if self.passed else "NO",
            "Score": self.score,
            **self.metrics,
            "Note": self.note,
        }


# ── small shared helpers ────────────────────────────────────────────────────────
def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100


def cagr(latest, earliest, years):
    if not latest or not earliest or earliest <= 0 or years < 1:
        return None
    return ((latest / earliest) ** (1 / years) - 1) * 100


def sma(series: pd.Series, n: int) -> Optional[float]:
    if series is None or len(series) < n:
        return None
    return float(series.tail(n).mean())


def safe(v):
    """Return float or None (filters NaN)."""
    try:
        f = float(v)
        return None if f != f else f  # NaN check
    except (TypeError, ValueError):
        return None
