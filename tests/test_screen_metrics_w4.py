"""screen_metrics: per-stock price metrics, screen predicates, runners and CLI."""

import numpy as np
import pandas as pd
import pytest

import screen_metrics as sm
from tests.w4_helpers import fake_module


def _ohlcv(close, vol=100.0):
    c = np.asarray(close, float)
    v = np.broadcast_to(np.asarray(vol, float), c.shape).copy()
    return pd.DataFrame(
        {"Close": c, "Volume": v}, index=pd.date_range("2023-01-01", periods=len(c))
    )


def test_metrics_guards():
    assert sm._metrics(None) is None
    assert sm._metrics(_ohlcv(np.arange(1, 60.0))) is None  # < 60 bars
    assert sm._metrics(_ohlcv(np.arange(1, 61.0))) is not None  # exactly 60
    c = np.arange(1, 61.0)
    c[-1] = 0.0
    assert sm._metrics(_ohlcv(c)) is None  # non-positive last close
    c[-1] = -1.0
    assert sm._metrics(_ohlcv(c)) is None


def test_metrics_values_on_ramp():
    close = np.arange(1.0, 301.0)  # 1..300
    vol = np.full(300, 10.0)
    vol[-5:] = 100.0  # latest week 10x busier
    m = sm._metrics(_ohlcv(close, vol))
    assert m["Close"] == 300.0
    assert m["DMA50"] == close[-50:].mean() and m["DMA200"] == close[-200:].mean()
    assert m["DMA50_prev"] == close[-60:-10].mean() and m["DMA200_prev"] == close[-210:-10].mean()
    assert m["RSI"] == 100.0  # no down moves
    assert m["High52"] == 300.0 and m["Low52"] == close[-252:].min() == 49.0
    assert m["PctFromHigh"] == 0.0 and m["PctFromLow"] == pytest.approx((300 / 49 - 1) * 100)
    assert (
        m["Vol"] == 100.0
        and m["WeekVol"] == 500.0
        and m["WeekVolAvg"] == 50.0
        and m["VolSpike"] == 10.0
    )
    assert m["Ret63"] == pytest.approx((300 / close[-63] - 1) * 100)
    assert m["Ret252"] == pytest.approx((300 / close[-252] - 1) * 100)
    assert m["Turnover"] == (close[-20:] * vol[-20:]).mean()


def test_metrics_short_history_nans_and_rsi_mix():
    c = np.array([100.0, 102.0] * 30)  # 60 bars alternating +2 / -2
    m = sm._metrics(_ohlcv(c))
    assert np.isnan(m["DMA200"]) and np.isnan(m["Ret126"]) and np.isnan(m["Ret252"])
    assert m["RSI"] == pytest.approx(
        50.0, abs=8
    )  # roughly balanced gains/losses over last 14 deltas
    assert np.isnan(m["DMA200_prev"]) and not np.isnan(m["DMA50_prev"])
    # ret(63) needs n > 63
    assert np.isnan(m["Ret63"])


def test_metrics_week_volume_baseline_fallback_and_zero():
    m = sm._metrics(_ohlcv(np.arange(1, 61.0), vol=7.0))
    assert m["WeekVolAvg"] == 35.0 and m["VolSpike"] == 1.0  # 8 prior weeks of 35
    m0 = sm._metrics(_ohlcv(np.arange(1, 61.0), vol=0.0))
    assert np.isnan(m0["VolSpike"])  # zero baseline -> NaN, not inf


def test_rsi_value_with_losses():
    steps = np.tile([3.0, -1.0], 7)  # 14 deltas in last window: mean up 1.5, mean down 0.5
    c = np.concatenate([np.full(46, 100.0), 100.0 + np.cumsum(steps)])
    assert len(c) == 60
    m = sm._metrics(_ohlcv(c))
    assert m["RSI"] == pytest.approx(100 - 100 / (1 + 3.0))  # RS = 1.5/0.5 = 3


def _frame(**cols):
    base = {
        "Close": 100.0,
        "DMA50": 100.0,
        "DMA200": 100.0,
        "DMA50_prev": 100.0,
        "DMA200_prev": 100.0,
        "RSI": 50.0,
        "High52": 100.0,
        "Low52": 100.0,
        "VolSpike": 1.0,
        "Ret252": 0.0,
    }
    base.update(cols)
    return pd.DataFrame([base])


@pytest.mark.parametrize(
    "name,over,expected",
    [
        ("golden_crossover", dict(DMA50=101.0, DMA50_prev=100.0), True),
        ("golden_crossover", dict(DMA50=101.0, DMA50_prev=99.0), True),
        (
            "golden_crossover",
            dict(DMA50=101.0, DMA50_prev=100.5, DMA200_prev=100.0),
            False,
        ),  # already above
        ("golden_crossover", dict(DMA50=100.0), False),  # equal is not above
        ("bearish_crossover", dict(DMA50=99.0, DMA50_prev=100.0), True),
        ("bearish_crossover", dict(DMA50=99.0, DMA50_prev=99.5), False),
        ("golden_state", dict(DMA50=100.1), True),
        ("golden_state", dict(), False),
        ("rsi_oversold", dict(RSI=29.99), True),
        ("rsi_oversold", dict(RSI=30.0), False),
        ("rsi_overbought", dict(RSI=70.01), True),
        ("rsi_overbought", dict(RSI=70.0), False),
        ("companies_creating_new_high", dict(Close=98.0), True),
        ("companies_creating_new_high", dict(Close=97.99), False),
        ("at_52w_high", dict(Close=99.9), True),
        ("at_52w_high", dict(Close=99.89), False),
        ("near_52w_low", dict(Close=105.0, Low52=100.0), True),
        ("near_52w_low", dict(Close=105.1, Low52=100.0), False),
        ("near_200dma", dict(Close=102.9), True),
        ("near_200dma", dict(Close=97.1), True),
        ("near_200dma", dict(Close=103.5), False),
        ("price_volume_action", dict(VolSpike=5.0), True),
        ("price_volume_action", dict(VolSpike=4.99), False),
        ("breakout_stocks", dict(Close=98.0, VolSpike=2.0), True),
        ("breakout_stocks", dict(Close=98.0, VolSpike=1.99), False),
        ("breakout_stocks", dict(Close=97.0, VolSpike=9.0), False),
        ("darvas_scan", dict(Close=97.0, DMA50=101.0), True),
        ("darvas_scan", dict(Close=96.9, DMA50=101.0), False),
        ("darvas_scan", dict(Close=100.0, DMA50=100.0), False),
        ("multibagger_momentum", dict(Ret252=100.1, DMA50=101.0), True),
        ("multibagger_momentum", dict(Ret252=100.0, DMA50=101.0), False),
        ("multibagger_momentum", dict(Ret252=200.0), False),
    ],
)
def test_price_screen_predicates(name, over, expected):
    assert bool(sm.PRICE_SCREENS[name](_frame(**over)).iloc[0]) is expected


def _patch_data(monkeypatch, data_by_tier):
    def load_market(market, tier="stm", **kw):
        return data_by_tier.get(tier, {})

    monkeypatch.setattr(sm.datalink, "load_market", load_market)


def _ramp(n=300, start=1.0, vol=1000.0):
    return _ohlcv(np.arange(start, start + n), vol)


def test_metrics_frame_ltm_then_stm_fallback_and_skips_short(monkeypatch):
    _patch_data(monkeypatch, {"ltm": {"A": _ramp(), "SHORT": _ramp(10)}, "stm": {"X": _ramp()}})
    fake_module(monkeypatch, "liquidity", annotate=lambda df: df.assign(Turnover_USD=1.0))
    mf = sm._metrics_frame("IN")
    assert list(mf["Symbol"]) == ["A"] and mf["Market"].iloc[0] == "IN" and "Turnover_USD" in mf
    _patch_data(monkeypatch, {"ltm": {}, "stm": {"X": _ramp()}})
    assert list(sm._metrics_frame("IN")["Symbol"]) == ["X"]
    _patch_data(monkeypatch, {})
    assert sm._metrics_frame("IN").empty


def test_metrics_frame_annotate_failure_is_swallowed(monkeypatch):
    _patch_data(monkeypatch, {"ltm": {"A": _ramp()}})
    fake_module(monkeypatch, "liquidity", annotate=lambda df: 1 / 0)
    assert list(sm._metrics_frame("IN")["Symbol"]) == ["A"]


def _universe(monkeypatch):
    data = {
        "UP": _ramp(300, 1.0),  # strong ramp: golden state, big 252d return
        "FLAT": _ohlcv(np.full(300, 50.0)),
        "ILLIQ": _ramp(300, 1.0, vol=1.0),
    }
    _patch_data(monkeypatch, {"ltm": data})
    turnover = {"UP": 5e6, "FLAT": 2e6, "ILLIQ": 10.0}
    fake_module(
        monkeypatch,
        "liquidity",
        annotate=lambda df: df.assign(Turnover_USD=df["Symbol"].map(turnover), Liquidity="High"),
    )


def test_run_screen_filters_turnover_sorts_and_tops(monkeypatch):
    _universe(monkeypatch)
    out = sm.run_screen("golden_state", "IN")
    assert list(out["Symbol"]) == ["UP", "ILLIQ"][:1]  # ILLIQ below default 1M turnover
    both = sm.run_screen("golden_state", "IN", min_turnover_usd=0)
    assert list(both["Symbol"])[0] in ("UP", "ILLIQ") and len(both) == 2
    assert len(sm.run_screen("golden_state", "IN", top=1, min_turnover_usd=0)) == 1
    assert sm.run_screen("rsi_oversold", "IN").empty
    with pytest.raises(ValueError, match="not price-computable"):
        sm.run_screen("piotroski_9", "IN")


def test_run_screen_empty_market(monkeypatch):
    _patch_data(monkeypatch, {})
    assert sm.run_screen("golden_state", "IN").empty


def test_run_all_counts_and_report(monkeypatch, capsys):
    _universe(monkeypatch)
    counts = sm.run_all("IN")
    assert set(counts) == set(sm.PRICE_SCREENS)
    assert counts["golden_state"] == 1 and counts["rsi_oversold"] == 0  # ILLIQ filtered by turnover
    out = capsys.readouterr().out
    assert "(2 liquid stocks in DB)" in out and "golden_state" in out and "UP" in out
    assert "fundamental screens (need financials" in out
    quiet = sm.run_all("IN", min_turnover_usd=0, verbose=False)
    assert quiet["golden_state"] == 2 and capsys.readouterr().out == ""
    _patch_data(monkeypatch, {})
    assert sm.run_all("IN") == {} and "no data for IN" in capsys.readouterr().out
    assert sm.run_all("IN", verbose=False) == {} and capsys.readouterr().out == ""


def test_run_all_report_sorted_by_count_desc(monkeypatch, capsys):
    _universe(monkeypatch)
    sm.run_all("IN", min_turnover_usd=0)
    lines = [
        l
        for l in capsys.readouterr().out.splitlines()
        if l.startswith("  ") and "fundamental" not in l
    ]
    nums = [int(l.split()[1]) for l in lines]
    assert nums == sorted(nums, reverse=True)


def test_screen_metrics_main(monkeypatch, capsys):
    _universe(monkeypatch)
    monkeypatch.setattr("sys.argv", ["sm", "--screen", "golden_state", "--market", "IN"])
    assert sm.main() == 0
    out = capsys.readouterr().out
    assert "golden_state on IN — 1 stocks" in out and "UP" in out
    monkeypatch.setattr("sys.argv", ["sm", "--screen", "rsi_oversold"])
    sm.main()
    assert "none" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["sm"])
    assert sm.main() == 0 and "price-metric screens on IN" in capsys.readouterr().out
    monkeypatch.setattr(sm.datalink, "MARKETS", ["IN", "US"])
    monkeypatch.setattr("sys.argv", ["sm", "--all", "--min-turnover", "0"])
    assert sm.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("IN ") and lines[1].startswith("US ")
