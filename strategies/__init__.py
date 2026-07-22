#!/usr/bin/env python3
# strategies/ — the 10 screening strategies, each a self-contained module with a
# common interface (META + screen(StockData) -> Result). See base.py.
#
#   from strategies import STRATEGIES, run_all, get
#   res = STRATEGIES["garp"].screen(stockdata)

from __future__ import annotations

from . import (
    bluest_blue_chips,
    cash_conversion_cycle,
    coffee_can,
    darvas,
    debt_reduction,
    dividend_yield,
    garp,
    golden_crossover,
    loss_to_profit,
    magic_formula,
    piotroski,
)
from .base import Result, StockData

# ordered as the user listed them (top-10) + cash conversion cycle
_MODULES = [
    piotroski,
    coffee_can,
    magic_formula,
    bluest_blue_chips,
    debt_reduction,
    dividend_yield,
    golden_crossover,
    loss_to_profit,
    garp,
    darvas,
    cash_conversion_cycle,
]

STRATEGIES = {m.META["slug"]: m for m in _MODULES}
META = {slug: m.META for slug, m in STRATEGIES.items()}


def get(slug: str):
    return STRATEGIES[slug]


def run_all(stock: StockData, only_needs: str | None = None) -> dict[str, Result]:
    """Run every strategy on one StockData. If only_needs is set ('price' or
    'fundamentals'), run only strategies requiring at most that data."""
    out = {}
    for slug, m in STRATEGIES.items():
        if only_needs == "price" and m.META["needs"] != "price":
            continue
        try:
            r = m.screen(stock)
        except Exception:
            r = None
        if r is not None:
            out[slug] = r
    return out


__all__ = ["STRATEGIES", "META", "get", "run_all", "StockData", "Result"]
