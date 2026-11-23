"""Fundamental/price strategy modules: thresholds, boundaries and registry helpers."""

import numpy as np
import pandas as pd
import pytest

import strategies as st
from strategies import (
    bluest_blue_chips,
    coffee_can,
    darvas,
    debt_reduction,
    dividend_yield,
    garp,
    golden_crossover,
    loss_to_profit,
    magic_formula,
)
from strategies.base import Result, StockData, cagr, pct_change, safe, sma


def sd(**f):
    return StockData("T", "IN", fundamentals=f)


# ---------------- base ----------------
def test_stockdata_accessors():
    assert sd(a=1).f("a") == 1 and sd().f("a", 5) == 5
    assert StockData("T").close is None and StockData("T").ltp is None
    assert StockData("T", ohlcv=pd.DataFrame()).close is None
    df = pd.DataFrame({"Close": [1.0, 2.5]})
    s = StockData("T", ohlcv=df)
    assert s.ltp == 2.5 and list(s.close) == [1.0, 2.5]


def test_result_row_layout():
    r = Result("A", "slug", True, 1.5, {"X": 1}, "n")
    assert r.row() == {
        "Symbol": "A",
        "Strategy": "slug",
        "Pass": "YES",
        "Score": 1.5,
        "X": 1,
        "Note": "n",
    }
    assert Result("A", "slug", False).row()["Pass"] == "NO"


def test_base_math_helpers():
    assert pct_change(110, 100) == pytest.approx(10.0)
    assert pct_change(90, -100) == pytest.approx(190.0)
    assert pct_change(None, 1) is None and pct_change(1, None) is None and pct_change(1, 0) is None
    assert cagr(400, 100, 2) == pytest.approx(100.0)
    assert (
        cagr(0, 100, 2) is None
        and cagr(100, 0, 2) is None
        and cagr(100, -1, 2) is None
        and cagr(1, 1, 0) is None
    )
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert sma(s, 2) == 3.5 and sma(s, 4) == 2.5 and sma(s, 5) is None and sma(None, 2) is None
    assert (
        safe("3.5") == 3.5
        and safe(float("nan")) is None
        and safe(None) is None
        and safe("x") is None
    )


def test_run_all_filters_by_need_and_isolates_failures(monkeypatch):
    class Mod:
        def __init__(self, needs, result="r", boom=False):
            self.META = {"slug": f"m_{needs}_{result}", "needs": needs}
            self.result, self.boom = result, boom

        def screen(self, s):
            if self.boom:
                raise RuntimeError
            return self.result if self.result != "None" else None

    mods = {
        "p": Mod("price", "pr"),
        "f": Mod("fundamentals", "fr"),
        "x": Mod("price", "boom", True),
        "n": Mod("price", "None"),
    }
    monkeypatch.setattr(st, "STRATEGIES", mods)
    assert st.run_all(sd()) == {"p": "pr", "f": "fr"}  # failure and None results omitted
    assert st.run_all(sd(), only_needs="price") == {"p": "pr"}
    # only 'price' restricts; any other value means "at most fundamentals" = everything
    assert st.run_all(sd(), only_needs="fundamentals") == {"p": "pr", "f": "fr"}


def test_get_returns_module():
    assert st.get("darvas") is darvas and st.get("garp") is garp


# ---------------- bluest_blue_chips ----------------
def _bc(**over):
    f = {
        "market_cap_cr": 3000.0,
        "roe": 15.0,
        "profit_growth": 10.0,
        "pe": 20.0,
        "industry_pe": 20.0,
    }
    f.update(over)
    return bluest_blue_chips.screen(sd(**f))


def test_blue_chip_boundaries_all_inclusive():
    r = _bc()
    assert r.passed and r.note == "blue chip" and r.score == 15.0
    assert not _bc(market_cap_cr=2999.9).passed
    assert not _bc(roe=14.99).passed
    assert not _bc(profit_growth=9.99).passed
    assert not _bc(pe=20.01).passed  # richer than industry
    assert _bc(industry_pe=None).passed  # no benchmark: positive PE suffices
    assert not _bc(pe=0).passed and not _bc(pe=-5, industry_pe=None).passed
    assert not _bc(pe=None).passed


def test_blue_chip_mcap_fallback_and_missing_data():
    r = bluest_blue_chips.screen(sd(market_cap=3.0e10, roe=20, eps_growth=12, pe=10))
    assert r.metrics["MCap_cr"] == 3000.0 and r.passed  # raw INR / 1e7, eps_growth fallback
    assert bluest_blue_chips.screen(sd(roe=20)) is None
    assert bluest_blue_chips.screen(sd(market_cap_cr=5000)) is None
    assert bluest_blue_chips.screen(sd(market_cap=0, roe=20)) is None
    r = _bc(profit_growth=None)
    assert not r.passed and r.metrics["ProfitGrowth%"] is None


# ---------------- debt_reduction ----------------
def test_debt_reduction_pass_and_metrics():
    r = debt_reduction.screen(sd(debt_history=[50, 80, 100], capex_history=[9, 5]))
    assert r.passed and r.note == "deleveraging+capex"
    assert r.metrics == {"DebtFalling": 1, "DebtChange%": -50.0, "CapacityExpanding": 1}
    assert r.score == 50.0  # bigger drop ranks higher


def test_debt_reduction_requires_falling_and_expanding():
    assert not debt_reduction.screen(
        sd(debt_history=[100, 80], capex_history=[9, 5])
    ).passed  # rising debt
    assert not debt_reduction.screen(
        sd(debt_history=[50, 80], capex_history=[5, 9])
    ).passed  # capex shrinking
    assert not debt_reduction.screen(
        sd(debt_history=[50, 80], capex_history=[5, 5])
    ).passed  # flat capex
    assert not debt_reduction.screen(sd(debt_history=[80, 80])).passed  # flat debt is not falling
    r = debt_reduction.screen(sd(debt_history=[50, 80], gross_block_history=[200, 100]))
    assert r.passed and r.metrics["CapacityExpanding"] == 1  # gross block alternative
    assert not debt_reduction.screen(
        sd(debt_history=[50, 80], gross_block_history=[100, 100])
    ).passed


def test_debt_reduction_insufficient_and_zero_base():
    assert debt_reduction.screen(sd()) is None
    assert debt_reduction.screen(sd(debt_history=[50])) is None
    assert debt_reduction.screen(sd(debt_history=[float("nan"), "x", 5])) is None
    r = debt_reduction.screen(sd(debt_history=[0, 10], capex_history=[2, 1]))
    assert r.passed and r.metrics["DebtChange%"] == -100.0
    r = debt_reduction.screen(sd(debt_history=[5, 0], capex_history=[2, 1]))
    assert r.metrics["DebtChange%"] is None and r.score == 0  # zero oldest debt: no pct


# ---------------- dividend_yield ----------------
def test_dividend_yield_threshold_and_consistency():
    ok = dividend_yield.screen(sd(dividend_yield=2.0, dividend_pay_years=3))
    assert ok.passed and ok.note == "income" and ok.score == 2.0
    assert not dividend_yield.screen(sd(dividend_yield=1.99, dividend_pay_years=5)).passed
    low = dividend_yield.screen(sd(dividend_yield=5, dividend_pay_years=2))
    assert not low.passed and low.metrics == {"DividendYield%": 5, "PayYears": 2, "Consistent": 0}
    assert dividend_yield.screen(sd()) is None


def test_dividend_yield_counts_history_when_years_missing():
    r = dividend_yield.screen(sd(dividend_yield=3, dividend_history=[1, 2, 0, float("nan"), 3]))
    assert r.metrics["PayYears"] == 3 and r.passed  # zero / NaN years do not count
    r = dividend_yield.screen(sd(dividend_yield=3, dividend_history=[1, 0, 0]))
    assert r.metrics["PayYears"] == 1 and not r.passed
    r = dividend_yield.screen(
        sd(dividend_yield=3, dividend_pay_years=0, dividend_history=[1, 1, 1])
    )
    assert r.metrics["PayYears"] == 0  # explicit zero wins over history
    assert dividend_yield.screen(sd(dividend_yield=3)).metrics["PayYears"] == 0


# ---------------- loss_to_profit ----------------
def test_loss_to_profit_turnaround_window():
    r = loss_to_profit.screen(sd(quarterly_net_income=[10, -5, 3, 4]))
    assert r.passed and r.note == "turnaround" and r.score == 10
    assert r.metrics == {
        "LatestQ_NI": 10.0,
        "PriorQ_NI": -5.0,
        "WasLoss": 1,
        "NowProfit": 1,
        "Swing%": 300.0,
    }
    assert loss_to_profit.screen(
        sd(quarterly_net_income=[10, 5, 3, -4])
    ).passed  # loss 3 quarters back
    assert not loss_to_profit.screen(
        sd(quarterly_net_income=[10, 5, 3, 4, -9])
    ).passed  # 4th prior is outside window
    assert not loss_to_profit.screen(sd(quarterly_net_income=[0, -5])).passed  # zero is not profit
    assert not loss_to_profit.screen(sd(quarterly_net_income=[-1, -5])).passed
    assert not loss_to_profit.screen(sd(quarterly_net_income=[5, 3, 2])).passed


def test_loss_to_profit_insufficient_and_swing():
    assert loss_to_profit.screen(sd()) is None
    assert loss_to_profit.screen(sd(quarterly_net_income=[5])) is None
    assert loss_to_profit.screen(sd(quarterly_net_income=[5, float("nan")])) is None
    r = loss_to_profit.screen(sd(quarterly_net_income=[5, 0, -1]))
    assert r.metrics["Swing%"] is None and r.passed  # zero prior quarter: no swing


# ---------------- magic_formula ----------------
def test_magic_formula_boundaries():
    r = magic_formula.screen(sd(earnings_yield=8.0, roc=15.0))
    assert r.passed and r.note == "cheap+quality" and r.score == 23.0
    assert not magic_formula.screen(sd(earnings_yield=7.99, roc=40)).passed
    assert not magic_formula.screen(sd(earnings_yield=20, roc=14.99)).passed


def test_magic_formula_derives_yield_and_falls_back_to_roce():
    r = magic_formula.screen(sd(ebit=90, enterprise_value=1000, roce=20))
    assert r.metrics["EarningsYield%"] == 9.0 and r.passed
    assert magic_formula.screen(sd(ebit=90, enterprise_value=0, roc=20)) is None
    assert magic_formula.screen(sd(earnings_yield=9)) is None
    assert magic_formula.screen(sd(roc=20)) is None
    r = magic_formula.screen(sd(earnings_yield=10, roc=20, ebit=1, enterprise_value=1000))
    assert r.metrics["EarningsYield%"] == 10.0  # explicit yield is not recomputed


# ---------------- coffee_can ----------------
def _cc(**over):
    f = {
        "roe": 15.0,
        "roe_history": [16, 15, 17],
        "revenue": 161.051,
        "revenue_5y_ago": 100.0,
        "debt_to_equity": 1.0,
    }
    f.update(over)
    return coffee_can.screen(sd(**f))


def test_coffee_can_boundaries():
    r = _cc()
    assert r.passed and r.note == "quality compounder" and r.metrics["RevCAGR5y"] == 10.0
    assert r.metrics["ROE_consistent"] == 1
    assert not _cc(roe=14.99).passed
    assert not _cc(debt_to_equity=1.01).passed
    assert not _cc(revenue=160.0).passed  # CAGR just under 10%
    assert not _cc(debt_to_equity=None).passed and not _cc(revenue=None).passed


def test_coffee_can_consistency_flag_and_missing():
    r = _cc(roe_history=[16, 14.9, 17])
    assert r.metrics["ROE_consistent"] == 0
    assert _cc(roe_history=[]).metrics["ROE_consistent"] == 0
    assert coffee_can.screen(sd()) is None
    assert (
        coffee_can.screen(
            sd(roe_history=[16, 17], revenue=200, revenue_5y_ago=100, debt_to_equity=0.5)
        ).metrics["ROE"]
        is None
    )


# ---------------- garp ----------------
def test_garp_boundaries():
    r = garp.screen(sd(eps_growth=15.0, pe=15.0, peg=1.0))
    assert r.passed and r.note == "GARP" and r.score == 1.0
    assert not garp.screen(sd(eps_growth=14.9, pe=10, peg=0.5)).passed
    assert garp.screen(sd(eps_growth=20, pe=40, peg=2.0)).passed  # P/E at cap counts as reasonable
    assert not garp.screen(sd(eps_growth=20, pe=40.01, peg=0.1)).passed  # above PE cap always fails
    assert garp.screen(sd(eps_growth=20, pe=30, peg=1.5, industry_pe=30)).passed


def test_garp_derived_peg_and_guards():
    r = garp.screen(sd(earnings_growth=20, pe=20))
    assert r.metrics["PEG"] == 1.0 and r.passed  # earnings_growth fallback, peg = pe/growth
    assert garp.screen(sd(eps_growth=20)) is None and garp.screen(sd(pe=10)) is None
    assert (
        garp.screen(sd(eps_growth=20, pe=0)) is None
        and garp.screen(sd(eps_growth=20, pe=-3)) is None
    )
    assert (
        garp.screen(sd(eps_growth=0, pe=10)) is None
    )  # zero growth is falsy -> treated as missing
    r = garp.screen(sd(eps_growth=-5, pe=10))
    assert r.metrics["PEG"] == -2.0 and not r.passed  # negative growth never passes


# ---------------- darvas / golden crossover ----------------
def _prices(n=100, vol=1000.0, last_vol=None, close=None):
    c = np.full(n, 100.0) if close is None else np.asarray(close, float)
    v = np.full(n, vol)
    if last_vol is not None:
        v[-1] = last_vol
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": v},
        index=pd.date_range("2024-01-01", periods=len(c)),
    )


def test_darvas_volume_multiple_boundary():
    assert darvas.screen(StockData("T", ohlcv=_prices(last_vol=1500.0))).passed  # exactly 1.5x
    r = darvas.screen(StockData("T", ohlcv=_prices(last_vol=1499.0)))
    assert not r.passed and r.note == "near_high" and r.metrics["Vol_x20d"] == 1.5
    assert darvas.screen(StockData("T", ohlcv=_prices(last_vol=1500.0))).metrics["Vol_x20d"] == 1.5


def test_darvas_near_high_boundary_and_guards():
    close = np.full(100, 100.0)
    close[50] = 110.0  # 52w high 110
    close[-1] = 99.0  # exactly 10% below
    r = darvas.screen(StockData("T", ohlcv=_prices(close=close, last_vol=5000)))
    assert r.passed and r.metrics["Off_High%"] == 10.0 and r.score == -10.0
    close[-1] = 98.9
    r = darvas.screen(StockData("T", ohlcv=_prices(close=close, last_vol=5000)))
    assert not r.passed and r.note == ""
    assert darvas.screen(StockData("T", ohlcv=_prices(n=59))) is None
    assert darvas.screen(StockData("T", ohlcv=_prices(n=60))) is not None
    assert darvas.screen(StockData("T")) is None
    assert darvas.screen(StockData("T", ohlcv=_prices(close=np.zeros(100)))) is None


def test_darvas_without_volume_or_high_columns_and_zero_avg_volume():
    d = _prices()
    r = darvas.screen(StockData("T", ohlcv=d.drop(columns=["Volume"])))
    assert not r.passed and r.metrics["Vol_x20d"] is None
    r = darvas.screen(StockData("T", ohlcv=d.drop(columns=["High"])))
    assert r.metrics["High_52w"] == 100.0
    r = darvas.screen(StockData("T", ohlcv=_prices(vol=0.0, last_vol=10.0)))
    assert r.metrics["Vol_x20d"] is None and not r.passed
    short = _prices(n=60)
    assert darvas.screen(StockData("T", ohlcv=short)) is not None  # >=21 rows for volume check


def _cross_series(n, kind):
    c = np.full(n, 100.0)
    if kind == "cross":
        c[-1] = 100.0 + 200.0 * 200 / 50  # last bar lifts the 50-day above the flat 200-day
    elif kind == "down":
        c = np.linspace(200, 100, n)
    return c


def test_golden_crossover_detection():
    n = 260
    flat = StockData("T", ohlcv=_prices(n, close=_cross_series(n, "flat")))
    r = golden_crossover.screen(flat)
    assert (
        r is not None and not r.passed and r.note == "below" and r.score == 0.0
    )  # 50 == 200: not above
    cross = golden_crossover.screen(
        StockData("T", ohlcv=_prices(n, close=_cross_series(n, "cross")))
    )
    assert (
        cross.passed and cross.note == "GOLDEN_CROSS" and cross.metrics["DMA50_above_200"] is True
    )
    c = np.full(n, 100.0)
    c[-3:] = 400.0  # 50dma already above 200dma yesterday -> not a *fresh* cross
    held = golden_crossover.screen(StockData("T", ohlcv=_prices(n, close=c)))
    assert held.note in ("above", "GOLDEN_CROSS")
    down = golden_crossover.screen(StockData("T", ohlcv=_prices(n, close=_cross_series(n, "down"))))
    assert not down.passed and down.note == "below" and down.score < 0


def test_golden_crossover_bar_requirement_and_already_above():
    assert golden_crossover.screen(StockData("T", ohlcv=_prices(204))) is None
    assert golden_crossover.screen(StockData("T", ohlcv=_prices(205))) is not None
    assert golden_crossover.screen(StockData("T")) is None
    up = np.linspace(100, 400, 300)
    r = golden_crossover.screen(StockData("T", ohlcv=_prices(300, close=up)))
    assert not r.passed and r.note == "above" and r.metrics["LTP"] == 400.0


# ---------------- cash_conversion_cycle / piotroski boundaries ----------------
from strategies import cash_conversion_cycle as ccc_mod  # noqa: E402
from strategies import piotroski  # noqa: E402


def test_ccc_threshold_boundary_and_derivation_guards():
    assert ccc_mod.screen(sd(ccc=45.0)).passed and not ccc_mod.screen(sd(ccc=45.1)).passed
    r = ccc_mod.screen(sd(ccc=45.0))
    assert r.note == "efficient WC" and r.metrics["DIO"] is None and r.score == 45.0
    # no receivables/revenue -> DSO cannot be computed -> no result
    assert ccc_mod.screen(sd(inventory=10, cogs=100)) is None
    assert ccc_mod.screen(sd(receivables=10, revenue=0)) is None
    # DIO/DPO optional: missing inventory counts as 0 days
    r = ccc_mod.screen(sd(receivables=36.5, revenue=365, payables=0, cogs=100))
    assert r.metrics == {"CCC_days": 36.5, "DIO": None, "DSO": 36.5, "DPO": 0.0} and r.passed
    r = ccc_mod.screen(sd(inventory=100, receivables=36.5, payables=36.5, cogs=365, revenue=365))
    assert r.metrics["DIO"] == 100.0 and r.metrics["CCC_days"] == 100.0 and not r.passed


def _pio(n_true):
    flags = [
        ("roa", 1, "roa_prev", 0),
        ("cfo", 5, "net_income", 3),
        ("debt_to_assets", 0.1, "debt_to_assets_prev", 0.2),
        ("current_ratio", 2, "current_ratio_prev", 1),
        ("shares", 10, "shares_prev", 10),
        ("gross_margin", 5, "gross_margin_prev", 4),
        ("asset_turnover", 2, "asset_turnover_prev", 1),
    ]
    f = {"net_income": 3, "roa": 1, "roa_prev": 0, "cfo": 5}
    bad = {
        "debt_to_assets": 0.3,
        "debt_to_assets_prev": 0.2,
        "current_ratio": 1,
        "current_ratio_prev": 2,
        "shares": 11,
        "shares_prev": 10,
        "gross_margin": 3,
        "gross_margin_prev": 4,
        "asset_turnover": 1,
        "asset_turnover_prev": 2,
    }
    good = {
        "debt_to_assets": 0.1,
        "debt_to_assets_prev": 0.2,
        "current_ratio": 2,
        "current_ratio_prev": 1,
        "shares": 10,
        "shares_prev": 10,
        "gross_margin": 5,
        "gross_margin_prev": 4,
        "asset_turnover": 2,
        "asset_turnover_prev": 1,
    }
    groups = [
        ("debt_to_assets", "debt_to_assets_prev"),
        ("current_ratio", "current_ratio_prev"),
        ("shares", "shares_prev"),
        ("gross_margin", "gross_margin_prev"),
        ("asset_turnover", "asset_turnover_prev"),
    ]
    for i, (a, b) in enumerate(groups):
        src = good if i < n_true else bad
        f[a], f[b] = src[a], src[b]
    return f


def test_piotroski_strong_threshold_is_seven():
    # base facts give ROA>0, CFO>0, dROA>0, Accrual = 4 points; each good group adds 1
    six = piotroski.screen(sd(**_pio(2)))
    seven = piotroski.screen(sd(**_pio(3)))
    assert six.score == 6 and not six.passed and six.note == ""
    assert seven.score == 7 and seven.passed and seven.note == "strong"
    assert piotroski.screen(sd(**_pio(5))).score == 9
    assert piotroski.screen(sd(**_pio(0))).metrics["F_Score"] == 4


def test_piotroski_missing_inputs_score_zero_not_crash():
    r = piotroski.screen(sd(net_income=1, roa=-1))
    assert r.score == 0 and r.metrics["ROA>0"] == 0 and r.metrics["CFO>0"] == 0
    assert piotroski.screen(sd(roa=1)) is None and piotroski.screen(sd(net_income=1)) is None
    r = piotroski.screen(sd(net_income=1, roa=0))
    assert r.metrics["ROA>0"] == 0  # strictly positive required
