"""Characterisation of the three compute_darvas_box variants (pure; synthetic OHLC only)."""

import sys
import types

import pandas as pd
import pytest

for _name, _attrs in (("nse", {"NSE": object}), ("bse", {"BSE": object})):
    if _name not in sys.modules:  # optional deps; the report module only needs the import
        _m = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_m, _k, _v)
        sys.modules[_name] = _m

import sg_stock_daily_report as sg
import stock_daily_report_improved as imp
import us_stock_daily_report as us
from tests._scan_golden import check_golden

MODULES = {"us": us, "imp": imp, "sg": sg}


def _frame(highs, lows, closes, names=("High", "Low", "Close")):
    return pd.DataFrame({names[0]: highs, names[1]: lows, names[2]: closes})


def _box_series(last_close, n_tail=6):
    highs = [10, 12, 15, 14, 13, 12.5] + [13] * n_tail
    lows = [9, 10, 11, 11.5, 11, 11.2] + [11.5] * n_tail
    closes = [9.5, 11, 14, 13, 12, 12] + [12.2] * (n_tail - 1) + [last_close]
    return highs, lows, closes


def _cases():
    cases = {
        "none": None,
        "empty": pd.DataFrame(),
        "no_cols": pd.DataFrame({"x": range(12)}),
        "short": _frame([1] * 6, [1] * 6, [1] * 6),
        "rising_no_box": _frame(
            [float(i) for i in range(1, 21)],
            [float(i) - 0.5 for i in range(1, 21)],
            [float(i) for i in range(1, 21)],
        ),
        "zeros": _frame([0.0] * 12, [0.0] * 12, [0.0] * 12),
    }
    for tag, last in (("breakout", 16.0), ("breakdown", 9.0), ("in_box", 12.5)):
        cases[tag] = _frame(*_box_series(last))
    cases["nse_cols"] = _frame(
        *_box_series(16.0), names=("CH_TRADE_HIGH_PRICE", "CH_TRADE_LOW_PRICE", "CH_CLOSING_PRICE")
    )
    cases["all_lows_zero_after_top"] = _frame(
        [10, 12, 15, 14, 13, 12.5, 13, 13, 13, 13, 13, 13], [0] * 12, [12] * 12
    )
    cases["bottom_fallback_min"] = _frame(
        [10, 12, 15, 14, 13, 12.5, 13, 13, 13, 13],
        [9, 10, 11, 10.5, 10, 9.8, 9.5, 9.0, 8.5, 8.0],
        [9.5, 11, 14, 13, 12, 12, 12, 12, 12, 12],
    )
    return cases


@pytest.mark.parametrize("modname", sorted(MODULES))
def test_darvas_golden(modname):
    fn = MODULES[modname].compute_darvas_box
    out = {}

    def call(df, **kw):
        try:
            return fn(df, **kw)
        except Exception as exc:  # pinned: some variants raise on None input
            return {"raised": type(exc).__name__}

    for name, df in _cases().items():
        out[name] = call(df)
        if name in ("breakout", "in_box", "rising_no_box"):
            out[name + "_confirm2"] = call(df, confirm=2)
    check_golden(f"darvas_{modname}", out)
