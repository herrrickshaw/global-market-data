"""liquidity tiers + custom_screener metrics/evaluation."""

import numpy as np
import pandas as pd
import pytest

import custom_screener as cs
import liquidity as liq
from strategies.base import StockData


def test_liquidity_tiers_global_and_per_market():
    assert liq.tier(20_000_000) == "High"
    assert liq.tier(5_000_000) == "Medium"
    assert liq.tier(100_000) == "Low"
    assert liq.tier(None) == "Unknown"
    # per-market override: US bar is higher than the global default
    assert liq.tier(12_000_000, "US") == "Medium"  # < US High(20M)
    assert liq.tier(12_000_000, "DK") == "High"  # > DK High(1M)


def test_annotate_tolerates_missing_symbol_col():
    df = pd.DataFrame({"x": [1]})
    assert liq.annotate(df).equals(df)  # no Symbol -> unchanged


def test_compute_metrics(uptrend):
    m = cs.compute_metrics(StockData("T", "IN", ohlcv=uptrend))
    assert m["above_200dma"] is True
    assert m["ret_252"] > 0 and 0 <= m["rsi14"] <= 100
    assert "max_drawdown" in m and "atr14_pct" in m


def test_evaluate_dict_and_callable(uptrend):
    sd = StockData("T", "IN", ohlcv=uptrend)
    ok, m = cs.evaluate(sd, {"above_200dma": ("==", True), "ret_252": (">", 0)})
    assert ok
    bad, _ = cs.evaluate(sd, {"ret_252": ("<", 0)})
    assert not bad
    okc, _ = cs.evaluate(sd, lambda mm: mm["above_200dma"])
    assert okc


def test_evaluate_unknown_operator(uptrend):
    with pytest.raises(ValueError):
        cs.evaluate(StockData("T", "IN", ohlcv=uptrend), {"rsi14": ("≈", 50)})


def test_screen_ranks_and_limits(ohlcv):
    stocks = [
        StockData("A", "IN", ohlcv=ohlcv(np.linspace(100, 300, 260))),
        StockData("B", "IN", ohlcv=ohlcv(np.linspace(100, 150, 260))),
        StockData("C", "IN", ohlcv=ohlcv(np.linspace(100, 90, 260))),  # downtrend
    ]
    out = cs.screen(stocks, {"above_200dma": ("==", True)}, rank_by="ret_252", top=2)
    assert list(out["Symbol"]) == ["A", "B"]  # C excluded, A ranked first
