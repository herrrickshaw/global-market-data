"""strategy modules — pure logic on synthetic StockData."""

import strategies as st
from strategies.base import StockData


def test_registry_has_11():
    assert len(st.STRATEGIES) == 11
    for slug in ("piotroski", "darvas", "golden_crossover", "cash_conversion_cycle", "garp"):
        assert slug in st.STRATEGIES
        assert st.STRATEGIES[slug].META["needs"] in ("price", "fundamentals")


def test_darvas_breakout(breakout):
    r = st.get("darvas").screen(StockData("T", "IN", ohlcv=breakout))
    assert r is not None and r.passed
    assert r.metrics["Off_High%"] <= 10 and r.note == "BREAKOUT"


def test_golden_cross_above_200(uptrend):
    r = st.get("golden_crossover").screen(StockData("T", "IN", ohlcv=uptrend))
    assert r is not None and r.metrics["DMA50_above_200"] is True


def test_piotroski_strong():
    f = {
        "net_income": 100,
        "net_income_prev": 80,
        "roa": 12,
        "roa_prev": 9,
        "cfo": 130,
        "debt_to_assets": 0.2,
        "debt_to_assets_prev": 0.3,
        "current_ratio": 2.1,
        "current_ratio_prev": 1.8,
        "shares": 100,
        "shares_prev": 100,
        "gross_margin": 40,
        "gross_margin_prev": 38,
        "asset_turnover": 1.1,
        "asset_turnover_prev": 1.0,
    }
    r = st.get("piotroski").screen(StockData("T", fundamentals=f))
    assert r.score >= 7 and r.passed


def test_ccc_precomputed_and_derived():
    # precomputed value
    r = st.get("cash_conversion_cycle").screen(StockData("T", fundamentals={"ccc": -20}))
    assert r.passed and r.metrics["CCC_days"] == -20.0
    # derived from balance-sheet inputs: DIO+DSO-DPO
    f = {"inventory": 50, "receivables": 30, "payables": 200, "cogs": 365, "revenue": 365}
    r2 = st.get("cash_conversion_cycle").screen(StockData("T", fundamentals=f))
    assert round(r2.metrics["CCC_days"], 0) == round(50 - 200 + 30, 0)  # 50+30-200 days


def test_garp_pass_and_fail():
    good = {"eps_growth": 25, "pe": 18}
    assert st.get("garp").screen(StockData("T", fundamentals=good)).passed
    pricey = {"eps_growth": 5, "pe": 60}
    assert not st.get("garp").screen(StockData("T", fundamentals=pricey)).passed


def test_returns_none_without_data():
    # price strategy with no ohlcv, fundamental strategy with no fundamentals
    assert st.get("darvas").screen(StockData("T")) is None
    assert st.get("piotroski").screen(StockData("T", fundamentals={})) is None


def test_run_all_price_only(uptrend):
    res = st.run_all(StockData("T", "IN", ohlcv=uptrend), only_needs="price")
    assert set(res).issubset({"darvas", "golden_crossover"})
