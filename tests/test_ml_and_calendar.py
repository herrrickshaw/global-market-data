# Tests for the multi-market holiday calendar and the auto-screener helpers.
# Pure logic, no network.
import datetime as _dt

import pandas as pd
import pytest


def test_weekend_rules():
    import market_holidays as mh

    # Saudi rests Fri/Sat; everyone else Sat/Sun
    assert mh.weekend("SA") == {4, 5}
    assert mh.weekend("US") == {5, 6}
    assert mh.weekend("IN") == {5, 6}


def test_is_trading_day_weekend_and_holiday():
    import market_holidays as mh

    # 2025-12-25 (Thu) is a US holiday; 2025-12-26 (Fri) trades
    assert mh.is_trading_day("US", _dt.date(2025, 12, 25)) is False
    assert mh.is_trading_day("US", _dt.date(2025, 12, 26)) is True
    # a Sunday never trades
    assert mh.is_trading_day("US", _dt.date(2025, 6, 1)) is False
    # Saudi trades Sunday, rests Friday
    assert mh.is_trading_day("SA", _dt.date(2025, 6, 1)) is True   # Sunday
    assert mh.is_trading_day("SA", _dt.date(2025, 6, 6)) is False  # Friday


def test_trading_days_filter_and_next():
    import market_holidays as mh

    days = [_dt.date(2025, 12, 25), _dt.date(2025, 12, 26), _dt.date(2025, 12, 27)]
    got = mh.trading_days("US", days)
    assert got == [_dt.date(2025, 12, 26)]  # 25 holiday, 27 Saturday
    nxt = mh.next_trading_day("US", _dt.date(2025, 12, 25))
    assert nxt == _dt.date(2025, 12, 26)


def test_auto_screener_rule_application():
    import auto_screener as a

    df = pd.DataFrame({
        "Symbol": ["A", "B", "C"],
        "RSI14": [60, 40, 80],
        "PctFromHigh": [-2, -30, -1],
        "Ret252": [50, -10, 120],
        "Ret126": [20, -5, 60],
        "Above200DMA": [True, False, True],
        "GoldenCross": [True, False, True],
        "Liquidity": ["High", "Low", "High"],
    })
    # monkeypatch the serving read to our frame
    a.sl.serving = lambda m: df  # type: ignore
    rule = {"RSI14": [(">=", 50)], "Above200DMA": [("==", True)]}
    out = a.apply_rule(rule, "IN")
    assert set(out["Symbol"]) == {"A", "C"}  # B fails RSI + trend + illiquid


def test_auto_screener_reward_penalises_deviation():
    import auto_screener as a

    df = pd.DataFrame({
        "Symbol": [f"S{i}" for i in range(10)],
        "RSI14": [55] * 10,
        "PctFromHigh": [-3] * 10,
        "Ret252": [40] * 10,
        "Ret126": [20] * 10,
        "Above200DMA": [True] * 10,
        "GoldenCross": [True] * 10,
        "Liquidity": ["High"] * 10,
    })
    a.sl.serving = lambda m: df  # type: ignore
    good = {f"S{i}" for i in range(10)}
    rule = {"RSI14": [(">=", 50)]}
    ev = a.evaluate(rule, "IN", good)
    # full overlap + liquid → strong reward, low deviation
    assert ev["overlap"] == 1.0
    assert ev["reward"] > 1.0
    assert ev["deviation"] < 0.3
