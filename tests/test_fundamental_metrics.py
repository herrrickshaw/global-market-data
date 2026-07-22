# Tests for the fundamental-metric formulas. Pure logic, no network.
import numpy as np
import pandas as pd


def test_piotroski_full_score():
    import fundamental_metrics as fm

    strong = {
        "net_income": 100, "roa": 10, "cfo": 150,          # profitable, cash-backed
        "debt_to_assets": 0.2, "debt_to_assets_prev": 0.3,  # deleveraging
        "current_ratio": 2.0, "current_ratio_prev": 1.5,    # more liquid
        "shares": 100, "shares_prev": 100,                  # no dilution
        "gross_margin": 40, "gross_margin_prev": 35,        # margin up
        "asset_turnover": 1.2, "asset_turnover_prev": 1.0,  # turnover up
    }
    assert fm._piotroski(strong) == 9
    weak = {"net_income": -10, "roa": -2, "cfo": -5}
    assert fm._piotroski(weak) <= 1


def test_first_val_robust():
    import fundamental_metrics as fm

    assert fm._first_val([3, 2, 1]) == 3
    assert fm._first_val(np.array([5.0, 4.0])) == 5.0
    assert fm._first_val(float("nan")) is None
    assert fm._first_val(None) is None
    assert fm._first_val(7) == 7


def test_yield_formulas():
    import fundamental_metrics as fm

    r = {"ebit": 20, "market_cap": 100, "cfo": 15, "capex_history": [5],
         "dividend_history": [-3]}
    assert fm._earnings_yield(r) == 20.0            # 20/100
    assert fm._fcf_yield(r) == 10.0                 # (15-5)/100
    assert round(fm._dividend_yield(r), 1) == 3.0   # |−3|/100


def test_fund_screen_predicates():
    import fundamental_metrics as fm

    df = pd.DataFrame({
        "Symbol": ["A", "B"],
        "piotroski": [9, 4], "roce": [25, 5], "roe": [30, 8],
        "earnings_yield": [10, 2], "fcf_yield": [6, 1], "div_yield": [4, 0],
        "debt_to_equity": [0.05, 1.2], "eps_growth": [20, -5],
    })
    assert set(df[fm.FUND_SCREENS["piotroski_9"](df)]["Symbol"]) == {"A"}
    assert set(df[fm.FUND_SCREENS["high_roce"](df)]["Symbol"]) == {"A"}
    assert set(df[fm.FUND_SCREENS["debt_free"](df)]["Symbol"]) == {"A"}
    assert set(df[fm.FUND_SCREENS["magic_formula"](df)]["Symbol"]) == {"A"}
