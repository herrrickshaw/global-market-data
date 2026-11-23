"""intraday_monitor: market hours, fetch, six pattern detectors, scan, persistence, daemon, CLI."""

import json
import runpy
import sys
from datetime import datetime as real_datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import

REPO_FILE = Path(__file__).resolve().parent.parent / "intraday_monitor.py"


class FakeTicker:
    spec = {}

    def __init__(self, sym):
        FakeTicker.spec["symbol"] = sym

    def history(self, period, interval):
        FakeTicker.spec["args"] = (period, interval)
        v = FakeTicker.spec.get("df")
        if isinstance(v, Exception):
            raise v
        return v if v is not None else pd.DataFrame()


@pytest.fixture
def im(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    FakeTicker.spec = {}
    fake_module(monkeypatch, "yfinance", Ticker=FakeTicker)
    return fresh_import(monkeypatch, "intraday_monitor")


def make_fake_dt(monkeypatch, mod, utc):
    class FDT(real_datetime):
        @classmethod
        def utcnow(cls):
            return cls(*utc)

        @classmethod
        def now(cls, tz=None):
            return cls(*utc)

    monkeypatch.setattr(mod, "datetime", FDT)
    return FDT


def bars(closes, vols=None, day="2024-01-02", highs=None, lows=None, start="09:15"):
    idx = pd.date_range(f"{day} {start}", periods=len(closes), freq="15min")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": c,
            "High": c if highs is None else np.asarray(highs, dtype=float),
            "Low": c if lows is None else np.asarray(lows, dtype=float),
            "Close": c,
            "Volume": np.full(len(c), 100.0) if vols is None else np.asarray(vols, dtype=float),
        },
        index=idx,
    )


# ---- import / constants -------------------------------------------------------------------


def test_import_exits_without_yfinance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "yfinance", None)
    monkeypatch.delitem(sys.modules, "intraday_monitor", raising=False)
    with pytest.raises(SystemExit) as e:
        __import__("intraday_monitor")
    assert "pip install yfinance" in str(e.value)
    sys.modules.pop("intraday_monitor", None)


def test_constants(im):
    assert len(im.NIFTY_50) == 50 and "RELIANCE" in im.NIFTY_50
    assert (im.ORB_BARS, im.MOMENTUM_BARS, im.BB_PERIOD, im.DARVAS_BARS) == (2, 3, 20, 20)
    assert (im.VWAP_THRESHOLD, im.VOLUME_SURGE_X, im.SQUEEZE_FACTOR) == (1.0, 3.0, 0.015)
    assert im.OUT_DIR.exists()


# ---- market hours -------------------------------------------------------------------------


# utc tuples are (y, m, d, H, M); IST = UTC + 5:30. 2024-01-01 is a Monday.
@pytest.mark.parametrize(
    "utc,expected",
    [
        ((2024, 1, 1, 3, 44), False),  # 09:14 IST
        ((2024, 1, 1, 3, 45), True),  # 09:15 IST opens inclusive
        ((2024, 1, 1, 10, 0), True),  # 15:30 IST closes inclusive
        ((2024, 1, 1, 10, 1), False),  # 15:31 IST
        ((2024, 1, 5, 6, 0), True),  # Friday midday
        ((2024, 1, 6, 6, 0), False),  # Saturday
        ((2024, 1, 7, 6, 0), False),  # Sunday
        ((2024, 1, 5, 20, 0), False),  # Fri 20:00 UTC = Sat 01:30 IST (weekday from IST)
    ],
)
def test_is_market_open(im, monkeypatch, utc, expected):
    make_fake_dt(monkeypatch, im, utc)
    assert im.is_market_open_nse() is expected


@pytest.mark.parametrize(
    "utc,expected",
    [
        ((2024, 1, 1, 2, 30), 75),  # Mon 08:00 IST
        ((2024, 1, 1, 3, 45), 1440),  # Mon 09:15 IST exactly -> next day
        ((2024, 1, 1, 10, 30), 1440 - (16 * 60) + 555),  # Mon 16:00 IST
        ((2024, 1, 5, 4, 30), 3 * 1440 - 600 + 555),  # Fri 10:00 IST -> Monday
        ((2024, 1, 6, 4, 30), 2 * 1440 - 600 + 555 + 1440 - 1440),  # Sat 10:00 IST -> Monday
        ((2024, 1, 6, 2, 30), 2 * 1440 - 480 + 555),  # Sat 08:00 IST (weekend, before open)
        ((2024, 1, 7, 4, 30), 1 * 1440 - 600 + 555),  # Sun 10:00 IST -> Monday
    ],
)
def test_minutes_to_next_open(im, monkeypatch, utc, expected):
    make_fake_dt(monkeypatch, im, utc)
    assert im.minutes_to_next_open() == expected


# ---- fetch_intraday -----------------------------------------------------------------------


def test_fetch_intraday_standardises_and_converts_tz(im):
    idx = pd.date_range("2024-01-02 03:45", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [1, 2, 3.0],
            "High": [2, 3, 4.0],
            "Low": [0, 1, 2.0],
            "Close": [1, 2, np.nan],
            "Volume": [10, 20, 30],
            "Dividends": [0, 0, 0],
        },
        index=idx,
    )
    FakeTicker.spec["df"] = df
    out = im.fetch_intraday("ABC", interval=30, days_back=3)
    assert FakeTicker.spec["symbol"] == "ABC.NS"
    assert FakeTicker.spec["args"] == ("3d", "30m")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(out) == 2  # NaN row dropped
    assert str(out.index.tz) == "Asia/Kolkata"
    assert out.index[0].hour == 9 and out.index[0].minute == 15


def test_fetch_intraday_naive_index_unchanged_and_defaults(im):
    FakeTicker.spec["df"] = bars([1, 2, 3])
    out = im.fetch_intraday("ABC")
    assert FakeTicker.spec["args"] == ("5d", "15m")
    assert out.index.tz is None and len(out) == 3


def test_fetch_intraday_empty_and_errors(im):
    FakeTicker.spec["df"] = pd.DataFrame()
    assert im.fetch_intraday("A").empty
    FakeTicker.spec["df"] = RuntimeError("net")
    assert im.fetch_intraday("A").empty
    FakeTicker.spec["df"] = pd.DataFrame({"Open": [1.0]})  # missing columns
    assert im.fetch_intraday("A").empty


def test_fetch_intraday_when_yf_flag_off(im, monkeypatch):
    monkeypatch.setattr(im, "_YF_OK", False)
    assert im.fetch_intraday("A").empty
    assert "symbol" not in FakeTicker.spec


# ---- compute_vwap -------------------------------------------------------------------------


def test_vwap_resets_each_day(im):
    d1 = bars([100, 110], day="2024-01-01", vols=[1, 1])
    d2 = bars([200, 220], day="2024-01-02", vols=[1, 3])
    v = im.compute_vwap(pd.concat([d2, d1]))  # out-of-order input
    assert v.index.is_monotonic_increasing
    assert list(v.round(4)) == [100.0, 105.0, 200.0, round((200 + 660) / 4, 4)]


def test_vwap_zero_volume_is_nan(im):
    df = bars([100, 100], vols=[0, 0])
    assert im.compute_vwap(df).isna().all()


def test_vwap_uses_typical_price(im):
    df = bars([10.0], highs=[16.0], lows=[2.0])
    assert im.compute_vwap(df).iloc[0] == pytest.approx((16 + 2 + 10) / 3)


# ---- ORB ----------------------------------------------------------------------------------


def test_orb_too_short(im):
    assert im.detect_orb(pd.DataFrame()) == {"pattern": "ORB", "signal": None}
    assert im.detect_orb(bars([1, 2])) == {"pattern": "ORB", "signal": None}  # < n_bars+1
    # many bars total but today has too few
    two_days = pd.concat([bars([1] * 5, day="2024-01-01"), bars([1, 1], day="2024-01-02")])
    assert im.detect_orb(two_days) == {"pattern": "ORB", "signal": None}


def _orb_df(last_close, n=3):
    closes = [100, 100] + [last_close] * (n - 2)
    return bars(
        closes, highs=[105, 103] + [last_close] * (n - 2), lows=[99, 98] + [last_close] * (n - 2)
    )


def test_orb_signals_and_strict_boundaries(im):
    up = im.detect_orb(_orb_df(106))
    assert up["signal"] == "BREAKOUT_LONG"
    assert (up["orb_high"], up["orb_low"], up["orb_range"]) == (105.0, 98.0, 7.0)
    assert up["current"] == 106.0 and up["range_pct"] == round(7 / 106 * 100, 2)
    assert im.detect_orb(_orb_df(97))["signal"] == "BREAKDOWN_SHORT"
    assert im.detect_orb(_orb_df(105))["signal"] is None  # == high is not a breakout
    assert im.detect_orb(_orb_df(98))["signal"] is None  # == low is not a breakdown
    assert im.detect_orb(_orb_df(100))["signal"] is None


def test_orb_uses_only_latest_day_and_n_bars(im):
    old = bars([500] * 4, day="2024-01-01", highs=[900] * 4, lows=[1] * 4)
    today = _orb_df(106)
    r = im.detect_orb(pd.concat([old, today]))
    assert r["orb_high"] == 105.0 and r["signal"] == "BREAKOUT_LONG"
    r3 = im.detect_orb(today, n_bars=1)  # opening range is only the first bar
    assert (r3["orb_high"], r3["orb_low"]) == (105.0, 99.0)
    assert im.detect_orb(bars([1, 2, 3]), n_bars=3)["signal"] is None  # needs 4 bars


# ---- VWAP deviation -----------------------------------------------------------------------


def test_vwap_deviation_short_input(im):
    assert im.detect_vwap_deviation(pd.DataFrame()) == {"pattern": "VWAP", "signal": None}
    assert im.detect_vwap_deviation(bars([1, 2, 3, 4]))["signal"] is None  # < 5 bars


def test_vwap_deviation_signals(im):
    above = im.detect_vwap_deviation(bars([100, 100, 100, 100, 110]))
    assert above["signal"] == "ABOVE_VWAP_REVERT"
    assert above["vwap"] == 102.0 and above["current"] == 110.0
    assert above["deviation%"] == round((110 - 102) / 102 * 100, 2)
    below = im.detect_vwap_deviation(bars([100, 100, 100, 100, 90]))
    assert below["signal"] == "BELOW_VWAP_REVERT" and below["deviation%"] < 0
    flat = im.detect_vwap_deviation(bars([100, 100, 100, 100, 100.5]))
    assert flat["signal"] is None


def test_vwap_deviation_threshold_parameter(im):
    df = bars([100, 100, 100, 100, 101])  # vwap 100.2 -> dev ~0.8%
    assert im.detect_vwap_deviation(df)["signal"] is None
    assert im.detect_vwap_deviation(df, threshold_pct=0.5)["signal"] == "ABOVE_VWAP_REVERT"
    assert im.detect_vwap_deviation(df, threshold_pct=0.9)["signal"] is None


def test_vwap_deviation_exception_swallowed(im):
    df = bars([100] * 5)
    df["Volume"] = 0.0  # vwap NaN -> NaN comparisons give no signal
    r = im.detect_vwap_deviation(df)
    assert r["signal"] is None
    broken = df.drop(columns=["High"])
    assert im.detect_vwap_deviation(broken) == {"pattern": "VWAP", "signal": None}


# ---- volume surge -------------------------------------------------------------------------


def test_volume_surge_boundaries(im):
    assert im.detect_volume_surge(pd.DataFrame())["signal"] is None
    assert im.detect_volume_surge(bars([1] * 10))["signal"] is None  # needs 11 bars
    v = [100] * 10
    hit = im.detect_volume_surge(bars([1] * 11, vols=v + [300]))
    assert hit["signal"] == "SURGE" and hit["ratio"] == 3.0  # >= factor
    assert (hit["curr_vol"], hit["avg_vol"]) == (300, 100)
    miss = im.detect_volume_surge(bars([1] * 11, vols=v + [299]))
    assert miss["signal"] is None and miss["ratio"] == 2.99
    assert (
        im.detect_volume_surge(bars([1] * 11, vols=v + [150]), surge_factor=1.5)["signal"]
        == "SURGE"
    )


def test_volume_surge_window_excludes_current_and_zero_avg(im):
    # average uses the 10 bars BEFORE the last; the 11th-from-last bar is ignored
    vols = [10_000] + [100] * 10 + [400]
    r = im.detect_volume_surge(bars([1] * 12, vols=vols))
    assert r["avg_vol"] == 100 and r["ratio"] == 4.0
    z = im.detect_volume_surge(bars([1] * 11, vols=[0] * 10 + [500]))
    assert z == {"pattern": "VOL_SURGE", "signal": None}


# ---- momentum -----------------------------------------------------------------------------


def test_momentum_bursts(im):
    assert im.detect_momentum_burst(pd.DataFrame())["signal"] is None
    assert im.detect_momentum_burst(bars([1, 2, 3]))["signal"] is None  # needs n+1 = 4
    up = im.detect_momentum_burst(bars([5, 1, 2, 3]))
    assert up["signal"] == "BULLISH_BURST" and up["recent_closes"] == [1.0, 2.0, 3.0]
    assert up["bars_checked"] == 3
    assert im.detect_momentum_burst(bars([0, 3, 2, 1]))["signal"] == "BEARISH_BURST"
    assert im.detect_momentum_burst(bars([0, 1, 2, 2]))["signal"] is None  # flat step breaks it
    assert im.detect_momentum_burst(bars([0, 1, 3, 2]))["signal"] is None  # mixed
    assert im.detect_momentum_burst(bars([0, 1, 2, 3, 4]), n_bars=4)["signal"] == "BULLISH_BURST"


# ---- Bollinger squeeze --------------------------------------------------------------------


def test_bb_too_short(im):
    assert im.detect_bb_squeeze(pd.DataFrame())["signal"] is None
    assert im.detect_bb_squeeze(bars([100] * 21))["signal"] is None  # needs period + 2
    assert "bb_upper" in im.detect_bb_squeeze(bars([100] * 22))


def test_bb_squeeze_breakout_up_down_and_none(im):
    up = im.detect_bb_squeeze(bars([100.0] * 29 + [110.0]))
    assert up["signal"] == "SQUEEZE_BREAKOUT_UP" and bool(up["in_squeeze"]) is True
    assert up["current"] == 110.0 and up["bb_upper"] < 110.0
    down = im.detect_bb_squeeze(bars([100.0] * 29 + [90.0]))
    assert down["signal"] == "SQUEEZE_BREAKOUT_DOWN" and down["bb_lower"] > 90.0
    flat = im.detect_bb_squeeze(bars([100.0] * 30))
    assert flat["signal"] is None and bool(flat["in_squeeze"]) is True and flat["bb_width%"] == 0.0


def test_bb_no_squeeze_means_no_signal(im):
    rng = np.random.default_rng(0)
    noisy = 100 + np.cumsum(rng.normal(0, 3, 40))
    noisy[-1] = noisy[-2] + 80  # huge jump outside bands, but bands were wide
    r = im.detect_bb_squeeze(bars(noisy))
    assert bool(r["in_squeeze"]) is False and r["signal"] is None


def test_bb_squeeze_factor_and_period_parameters(im):
    closes = [100.0] * 29 + [110.0]
    assert im.detect_bb_squeeze(bars(closes), period=10)["signal"] == "SQUEEZE_BREAKOUT_UP"
    # num_std large enough that 110 stays inside the bands
    assert im.detect_bb_squeeze(bars(closes), num_std=10)["signal"] is None
    # tiny squeeze factor: flat history has width 0 which is still < any positive factor
    assert bool(im.detect_bb_squeeze(bars(closes), squeeze_factor=1e-9)["in_squeeze"])
    gentle = 100 + np.sin(np.arange(40)) * 1.0  # width ~ 3% of price: not a 1.5% squeeze
    gentle[-1] = 140
    assert not im.detect_bb_squeeze(bars(gentle))["in_squeeze"]
    assert bool(im.detect_bb_squeeze(bars(gentle), squeeze_factor=0.5)["in_squeeze"])


# ---- intraday Darvas ----------------------------------------------------------------------


def _darvas_df(n=25, peak=10, trough=12, last=8.0, prev=None, peak_val=12.0, trough_val=4.0):
    highs = np.full(n, 10.0)
    lows = np.full(n, 5.0)
    highs[peak] = peak_val
    lows[trough] = trough_val
    closes = np.full(n, 8.0)
    closes[-1] = last
    if prev is not None:
        closes[-2] = prev
    return bars(closes, highs=highs, lows=lows)


def test_darvas_short_input_note(im):
    r = im.detect_intraday_darvas(bars([1] * 19))
    assert r["signal"] is None and r["note"] == "need ≥20 bars, have 19"
    assert im.detect_intraday_darvas(pd.DataFrame())["note"] == "need ≥20 bars, have 0"
    assert im.detect_intraday_darvas(_darvas_df(20))["signal"] == "IN_BOX"  # 20 is enough


def test_darvas_signals(im):
    base = im.detect_intraday_darvas(_darvas_df())
    assert base["signal"] == "IN_BOX"
    assert (base["box_top"], base["box_bottom"]) == (12.0, 4.0)
    assert base["upside%"] == round((12 - 8) / 8 * 100, 2)
    assert im.detect_intraday_darvas(_darvas_df(last=13, prev=11))["signal"] == "INTRADAY_BREAKOUT"
    assert im.detect_intraday_darvas(_darvas_df(last=13, prev=12))["signal"] == "INTRADAY_BREAKOUT"
    assert im.detect_intraday_darvas(_darvas_df(last=13, prev=13))["signal"] == "ABOVE_BOX"
    assert im.detect_intraday_darvas(_darvas_df(last=12, prev=11))["signal"] == "IN_BOX"
    assert im.detect_intraday_darvas(_darvas_df(last=3, prev=6))["signal"] == "INTRADAY_BREAKDOWN"
    assert im.detect_intraday_darvas(_darvas_df(last=3, prev=4))["signal"] == "INTRADAY_BREAKDOWN"
    assert im.detect_intraday_darvas(_darvas_df(last=3, prev=3))["signal"] == "BELOW_BOX"
    assert im.detect_intraday_darvas(_darvas_df(last=4, prev=6))["signal"] == "IN_BOX"


def test_darvas_no_box_when_highs_never_confirm(im):
    n = 25
    df = bars(np.full(n, 8.0), highs=np.arange(n) + 10.0, lows=np.full(n, 5.0))
    r = im.detect_intraday_darvas(df)
    assert r == {"pattern": "INTRADAY_DARVAS", "signal": "NO_BOX", "current": 8.0}


def test_darvas_confirm_parameter(im):
    df = _darvas_df()
    # peak at 10 is followed by 13 equal bars; confirm=1 finds an earlier/later top differently
    assert im.detect_intraday_darvas(df, confirm=2)["box_top"] == 12.0
    # equal-highs never form a box when the top bar is unique-lower-neighbours only after it
    flat = bars(np.full(25, 8.0), highs=np.full(25, 10.0), lows=np.full(25, 5.0))
    assert im.detect_intraday_darvas(flat)["signal"] == "NO_BOX"


def test_darvas_lookback_window_is_30_bars(im):
    n = 50
    # h has 49 entries; loop floors at index 19 (inclusive)
    inside = _darvas_df(n, peak=19, trough=30)
    assert im.detect_intraday_darvas(inside)["box_top"] == 12.0
    outside = _darvas_df(n, peak=18, trough=30)
    assert im.detect_intraday_darvas(outside)["signal"] == "NO_BOX"


def test_darvas_zero_highs_skipped(im):
    df = _darvas_df()
    df.iloc[12, df.columns.get_loc("High")] = 0.0
    df.iloc[11, df.columns.get_loc("High")] = 0.0
    r = im.detect_intraday_darvas(df)
    assert r["box_top"] == 12.0


def test_darvas_bottom_fallbacks(im):
    n = 25
    highs = np.full(n, 10.0)
    highs[10] = 12.0
    # no confirmed low: strictly decreasing lows -> minimum of positive lows
    lows = np.linspace(9.0, 1.0, n)
    df = bars(np.full(n, 8.0), highs=highs, lows=lows)
    assert im.detect_intraday_darvas(df)["box_bottom"] == round(lows[-2], 2)
    # lows of exactly zero from the top onwards -> no usable bottom
    lows0 = np.full(n, 5.0)
    lows0[10:] = 0.0
    df0 = bars(np.full(n, 8.0), highs=highs, lows=lows0)
    r = im.detect_intraday_darvas(df0)
    assert r == {
        "pattern": "INTRADAY_DARVAS",
        "signal": "NO_BOX",
        "box_top": 12.0,
        "current": 8.0,
    }


def test_darvas_zero_current_price_has_zero_upside(im):
    df = _darvas_df(last=0.0, prev=0.0)
    r = im.detect_intraday_darvas(df)
    assert r["upside%"] == 0 and r["signal"] == "BELOW_BOX"


# ---- scan_one -----------------------------------------------------------------------------


def _patch_patterns(im, monkeypatch, sigs):
    names = [
        "detect_orb",
        "detect_vwap_deviation",
        "detect_volume_surge",
        "detect_momentum_burst",
        "detect_bb_squeeze",
        "detect_intraday_darvas",
    ]
    for name, s in zip(names, sigs):
        monkeypatch.setattr(im, name, lambda df, _n=name, _s=s: {"pattern": _n, "signal": _s})


def test_scan_one_no_data(im, monkeypatch):
    monkeypatch.setattr(im, "fetch_intraday", lambda *a, **k: pd.DataFrame())
    assert im.scan_one("ABC") == {"symbol": "ABC", "error": "no_data", "signals": []}


def test_scan_one_aggregation(im, monkeypatch):
    calls = []
    monkeypatch.setattr(
        im,
        "fetch_intraday",
        lambda s, interval, days_back: calls.append((s, interval, days_back))
        or bars([100.0, 101.5], vols=[7, 9]),
    )
    _patch_patterns(
        im, monkeypatch, ["BREAKOUT_LONG", None, "SURGE", "BULLISH_BURST", None, "IN_BOX"]
    )
    r = im.scan_one("ABC", interval=30)
    assert calls == [("ABC", 30, 5)]
    assert r["price"] == 101.5 and r["volume"] == 9 and r["interval_m"] == 30
    assert r["confluence"] == 3  # IN_BOX and None excluded; SURGE counts but is directionless
    assert (r["bullish_n"], r["bearish_n"], r["direction"]) == (2, 0, "BULLISH")
    assert [p["signal"] for p in r["active"]] == ["BREAKOUT_LONG", "SURGE", "BULLISH_BURST"]
    assert len(r["patterns"]) == 6 and r["symbol"] == "ABC"


def test_scan_one_bearish_and_neutral(im, monkeypatch):
    monkeypatch.setattr(im, "fetch_intraday", lambda *a, **k: bars([100.0, 99.0]))
    _patch_patterns(im, monkeypatch, ["BREAKDOWN_SHORT", None, None, None, None, "BELOW_BOX"])
    r = im.scan_one("ABC")
    assert (r["bullish_n"], r["bearish_n"], r["direction"]) == (0, 2, "BEARISH")
    _patch_patterns(im, monkeypatch, ["BREAKOUT_LONG", "BELOW_VWAP_REVERT", None, None, None, None])
    r = im.scan_one("ABC")
    # a bare LONG vs a REVERT/BELOW signal -> tie
    assert (r["bullish_n"], r["bearish_n"], r["direction"]) == (1, 1, "NEUTRAL")
    _patch_patterns(im, monkeypatch, [None] * 6)
    r = im.scan_one("ABC")
    assert (r["confluence"], r["direction"]) == (0, "NEUTRAL")


def test_scan_one_keyword_overlap_counts_both_sides(im, monkeypatch):
    # "ABOVE_VWAP_REVERT" matches a bullish keyword (ABOVE) and a bearish one (REVERT)
    monkeypatch.setattr(im, "fetch_intraday", lambda *a, **k: bars([100.0, 99.0]))
    _patch_patterns(im, monkeypatch, [None, "ABOVE_VWAP_REVERT", None, None, None, None])
    r = im.scan_one("ABC")
    assert (r["bullish_n"], r["bearish_n"]) == (1, 1)


def test_scan_one_real_detectors_end_to_end(im, monkeypatch):
    closes = [100.0] * 28 + [101.0, 102.0, 103.0]
    df = bars(closes, vols=[100.0] * 30 + [900.0])
    monkeypatch.setattr(im, "fetch_intraday", lambda *a, **k: df)
    r = im.scan_one("ABC")
    sigs = {p["pattern"]: p["signal"] for p in r["patterns"]}
    assert sigs["MOMENTUM"] == "BULLISH_BURST" and sigs["VOL_SURGE"] == "SURGE"
    assert sigs["ORB"] == "BREAKOUT_LONG"
    assert r["confluence"] >= 3 and r["direction"] == "BULLISH"


# ---- run_scan -----------------------------------------------------------------------------


def _fake_scan(table):
    def scan(sym, interval):
        v = table[sym]
        if isinstance(v, Exception):
            raise v
        return v

    return scan


def _row(sym, conf, direction="BULLISH", patterns=None):
    return {
        "symbol": sym,
        "time": "t",
        "price": 10.0 + conf,
        "volume": 5,
        "direction": direction,
        "confluence": conf,
        "bullish_n": conf,
        "bearish_n": 0,
        "patterns": patterns or [],
    }


def test_run_scan_flattens_sorts_and_skips_errors(im, monkeypatch, capsys):
    pats = [
        {"pattern": "ORB", "signal": "BREAKOUT_LONG", "orb_high": 5.0, "orb_low": 4.0},
        {"pattern": "VWAP", "signal": None, "deviation%": 1.5},
        {"pattern": "VOL_SURGE", "signal": "SURGE", "ratio": 3.2},
        {"pattern": "INTRADAY_DARVAS", "signal": "IN_BOX", "box_top": 12.0, "box_bottom": 4.0},
    ]
    table = {
        "A": _row("A", 1),
        "B": _row("B", 3, patterns=pats),
        "C": {"symbol": "C", "error": "no_data", "signals": []},
        "D": RuntimeError("worker crash"),
    }
    monkeypatch.setattr(im, "scan_one", _fake_scan(table))
    df = im.run_scan(["A", "B", "C", "D"], interval=15, workers=2)
    assert list(df["Symbol"]) == ["B", "A"]  # errors skipped; sorted by confluence desc
    b = df.iloc[0]
    assert b["Price_₹"] == 13.0 and b["Confluence"] == 3
    assert (b["ORB_High"], b["ORB_Low"], b["VWAP_Dev%"], b["Vol_Ratio"]) == (5.0, 4.0, 1.5, 3.2)
    assert (b["Box_Top"], b["Box_Bottom"]) == (12.0, 4.0)
    assert b["ORB_signal"] == "BREAKOUT_LONG" and b["INTRADAY_DARVAS_signal"] == "IN_BOX"
    out = capsys.readouterr().out
    assert "4/4 scanned | 2 with signals" in out
    assert "10/" not in out  # progress only at multiples of 10 or the final symbol


def test_run_scan_progress_every_ten(im, monkeypatch, capsys):
    syms = [f"S{i}" for i in range(12)]
    monkeypatch.setattr(im, "scan_one", _fake_scan({s: _row(s, 0) for s in syms}))
    df = im.run_scan(syms, workers=1)
    lines = [ln.strip() for ln in capsys.readouterr().out.splitlines()]
    assert lines == ["10/12 scanned | 0 with signals", "12/12 scanned | 0 with signals"]
    assert len(df) == 12


def test_run_scan_all_errors_returns_empty(im, monkeypatch):
    monkeypatch.setattr(im, "scan_one", _fake_scan({"A": RuntimeError("x")}))
    assert im.run_scan(["A"]).empty


# ---- save_signals -------------------------------------------------------------------------


class XlWriter:
    created = []

    def __init__(self, path, **kw):
        self.path, self.kw, self.sheets = path, kw, []
        XlWriter.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        Path(self.path).write_bytes(b"xl")  # emulate file creation
        return False


@pytest.fixture
def saver(im, monkeypatch, tmp_path):
    XlWriter.created = []
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(im, "OUT_DIR", out)
    monkeypatch.setattr(im.pd, "ExcelWriter", XlWriter)
    sheets = []
    monkeypatch.setattr(
        im.pd.DataFrame,
        "to_excel",
        lambda self, w, sheet_name, index: sheets.append((sheet_name, index, len(self))),
    )

    class FDate(real_datetime):
        @classmethod
        def today(cls):
            return cls(2024, 3, 5)

        @classmethod
        def now(cls, tz=None):
            return cls(2024, 3, 5, 9, 45, 7)

    monkeypatch.setattr(im, "date", FDate)
    monkeypatch.setattr(im, "datetime", FDate)
    return out, sheets


def test_save_signals_first_and_second_scan(im, saver):
    out, sheets = saver
    df = pd.DataFrame({"Symbol": ["A", "B", "C"], "Confluence": [3, 2, 4]})
    jp, xp = im.save_signals(df, 15)
    assert jp == out / "intraday_20240305_15m.json" and xp == out / "intraday_20240305.xlsx"
    data = json.loads(jp.read_text())
    assert len(data) == 1 and data[0]["scan_time"].startswith("2024-03-05T09:45:07")
    assert [r["Symbol"] for r in data[0]["signals"]] == ["A", "B", "C"]
    w = XlWriter.created[0]
    assert w.kw == {"engine": "openpyxl", "mode": "w", "if_sheet_exists": None}
    assert sheets == [("Scan_0945_15m", False, 3), ("HighConv_0945", False, 2)]  # confluence >= 3
    # second scan appends to the JSON and opens the workbook in append mode
    sheets.clear()
    im.save_signals(df.iloc[[1]], 15)
    assert len(json.loads(jp.read_text())) == 2
    w2 = XlWriter.created[1]
    assert w2.kw == {"engine": "openpyxl", "mode": "a", "if_sheet_exists": "replace"}
    assert sheets == [("Scan_0945_15m", False, 1)]  # no high-conviction sheet


def test_save_signals_confluence_boundary_and_empty(im, saver):
    out, sheets = saver
    im.save_signals(pd.DataFrame({"Symbol": ["A"], "Confluence": [2]}), 30)
    assert [s[0] for s in sheets] == ["Scan_0945_30m"]
    sheets.clear()
    jp, _ = im.save_signals(pd.DataFrame(), 30)
    assert sheets == []
    assert json.loads(jp.read_text())[-1]["signals"] == []


# ---- print_summary ------------------------------------------------------------------------


def _summary_df():
    rows = []
    for i in range(12):
        rows.append(
            {
                "Symbol": f"BULL{i:02d}",
                "Price_₹": 1234.5,
                "Confluence": 12 - i,
                "Direction": "BULLISH",
                "ORB_signal": "BREAKOUT_LONG",
                "VWAP_signal": None,
            }
        )
    for i in range(7):
        rows.append(
            {
                "Symbol": f"BEAR{i:02d}",
                "Price_₹": 99.0,
                "Confluence": 1,
                "Direction": "BEARISH",
                "ORB_signal": None,
                "VWAP_signal": "BELOW_VWAP_REVERT",
            }
        )
    rows.append(
        {
            "Symbol": "FLAT",
            "Price_₹": 1.0,
            "Confluence": 0,
            "Direction": "NEUTRAL",
            "ORB_signal": None,
            "VWAP_signal": None,
        }
    )
    return pd.DataFrame(rows)


def test_print_summary_empty(im, monkeypatch, capsys):
    make_fake_dt(monkeypatch, im, (2024, 1, 1, 4, 0))
    im.print_summary(pd.DataFrame(), 15)
    out = capsys.readouterr().out
    assert "INTRADAY MONITOR — 01 Jan 2024  09:30 IST | 15-min bars" in out
    assert "No signals detected." in out and "BULLISH:" not in out


def test_print_summary_full(im, monkeypatch, capsys):
    make_fake_dt(monkeypatch, im, (2024, 1, 1, 4, 0))
    im.print_summary(_summary_df(), 30)
    out = capsys.readouterr().out
    assert "30-min bars" in out
    assert "Scanned: 20 stocks | BULLISH: 12 | BEARISH: 7" in out
    assert out.count("BULL") >= 10
    assert "BULL09" in out and "BULL10" not in out  # bullish list capped at 10
    assert "BEAR04" in out and "BEAR05" not in out  # bearish list capped at 5
    assert "₹1,234.50" in out and "ORB" in out and "VWAP" in out
    hc = [ln for ln in out.splitlines() if "HIGH CONVICTION" in ln]
    assert len(hc) == 1 and "BULL00" in hc[0] and "BULL09" in hc[0]  # confluence >= 3
    assert "BULL10" not in hc[0]  # confluence 2
    assert "INTRADAY DISCLAIMER" in out


def test_print_summary_no_bullish_or_high_conviction(im, monkeypatch, capsys):
    make_fake_dt(monkeypatch, im, (2024, 1, 1, 4, 0))
    df = _summary_df()
    df = df[df["Direction"] == "BEARISH"]
    im.print_summary(df, 15)
    out = capsys.readouterr().out
    assert "TOP BULLISH" not in out and "TOP BEARISH" in out and "HIGH CONVICTION" not in out
    df2 = pd.DataFrame(
        [
            {
                "Symbol": "X",
                "Price_₹": 1.0,
                "Confluence": 2,
                "Direction": "BULLISH",
                "ORB_signal": "S",
            }
        ]
    )
    im.print_summary(df2, 15)
    out = capsys.readouterr().out
    assert "TOP BEARISH" not in out and "HIGH CONVICTION" not in out


# ---- main daemon --------------------------------------------------------------------------


class Stop(Exception):
    pass


@pytest.fixture
def daemon(im, monkeypatch):
    make_fake_dt(monkeypatch, im, (2024, 1, 1, 4, 0))
    log = {"sleep": [], "scan": [], "save": [], "summary": []}
    monkeypatch.setattr(im.time, "sleep", log["sleep"].append)
    monkeypatch.setattr(
        im,
        "run_scan",
        lambda syms, interval, workers: log["scan"].append((syms, interval, workers))
        or log.get("df", pd.DataFrame()),
    )
    monkeypatch.setattr(
        im,
        "save_signals",
        lambda df, interval: log["save"].append(interval) or (Path("a.json"), Path("b.xlsx")),
    )
    monkeypatch.setattr(im, "print_summary", lambda df, i: log["summary"].append(i))
    return log


def test_main_closed_once_exits(im, daemon, monkeypatch, capsys):
    monkeypatch.setattr(im, "is_market_open_nse", lambda: False)
    monkeypatch.setattr(im, "minutes_to_next_open", lambda: 10)
    im.main(once=True)
    out = capsys.readouterr().out
    assert "Market closed. Run with --once during market hours." in out
    assert daemon["scan"] == [] and daemon["sleep"] == []
    assert "Stocks: 50 | Workers: 6" in out


def test_main_closed_sleeps_capped_then_scans(im, daemon, monkeypatch, capsys):
    states = iter([False, False, True])
    waits = iter([10, 100])
    monkeypatch.setattr(im, "is_market_open_nse", lambda: next(states))
    monkeypatch.setattr(im, "minutes_to_next_open", lambda: next(waits))
    daemon["df"] = pd.DataFrame({"Symbol": ["A"]})

    def sleep(sec):
        daemon["sleep"].append(sec)
        if len(daemon["sleep"]) == 3:
            raise Stop

    monkeypatch.setattr(im.time, "sleep", sleep)
    with pytest.raises(Stop):
        im.main(interval=30, symbols=["A", "B"], workers=3)
    assert daemon["sleep"] == [600, 3600, 1800]  # min(wait*60, 3600), then interval*60
    assert daemon["scan"] == [(["A", "B"], 30, 3)]
    assert daemon["save"] == [30] and daemon["summary"] == [30]
    out = capsys.readouterr().out
    assert "sleeping 10 minutes" in out and "sleeping 100 minutes" in out
    assert "Scan #1 — 2 stocks" in out and "Saved: a.json | b.xlsx" in out


def test_main_empty_df_not_saved_and_default_symbols(im, daemon, monkeypatch):
    monkeypatch.setattr(im, "is_market_open_nse", lambda: True)
    im.main(symbols=[], once=True)
    assert daemon["scan"][0][0] is im.NIFTY_50 and daemon["scan"][0][2] == im.MAX_WORKERS
    assert daemon["save"] == [] and daemon["summary"] == []


def test_main_loops_with_interval_sleep(im, daemon, monkeypatch, capsys):
    monkeypatch.setattr(im, "is_market_open_nse", lambda: True)

    def sleep(sec):
        daemon["sleep"].append(sec)
        if len(daemon["sleep"]) == 2:
            raise Stop

    monkeypatch.setattr(im.time, "sleep", sleep)
    with pytest.raises(Stop):
        im.main(interval=30, symbols=["A"])
    assert daemon["sleep"] == [1800, 1800] and len(daemon["scan"]) == 2
    out = capsys.readouterr().out
    assert "Scan #2" in out and "Next scan in 30 minutes" in out


# ---- CLI ----------------------------------------------------------------------------------


def _cli(monkeypatch, argv, utc=(2024, 1, 6, 6, 0)):
    """Run the module as a script on a closed-market Saturday so --once exits at once."""
    import datetime as dt_mod

    class FDT(real_datetime):
        @classmethod
        def utcnow(cls):
            return cls(*utc)

    monkeypatch.setattr(dt_mod, "datetime", FDT)
    monkeypatch.setattr(sys, "argv", ["intraday_monitor.py", *argv])
    runpy.run_path(str(REPO_FILE), run_name="__main__")


def test_cli_once_closed(im, monkeypatch, capsys):
    _cli(monkeypatch, ["--once", "--interval", "30", "--symbols", "AAA", "BBB", "--workers", "2"])
    out = capsys.readouterr().out
    assert "INTRADAY MONITOR — 30-min bars" in out
    assert "Stocks: 2 | Workers: 2" in out
    assert "Market closed. Run with --once during market hours." in out


def test_cli_all_fno_uses_nsepython(im, monkeypatch, capsys):
    fake_module(monkeypatch, "nsepython", fnolist=lambda: ["X", "Y", "Z"])
    _cli(monkeypatch, ["--once", "--all-fno"])
    out = capsys.readouterr().out
    assert "F&O stocks: 3" in out and "Stocks: 3 |" in out


def test_cli_all_fno_fallback(im, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "nsepython", None)
    _cli(monkeypatch, ["--once", "--all-fno"])
    out = capsys.readouterr().out
    assert "fnolist unavailable — using Nifty 50" in out and "Stocks: 50 |" in out


def test_cli_rejects_bad_interval(im, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _cli(monkeypatch, ["--interval", "20"])
    assert e.value.code == 2


def test_darvas_leading_zero_low_skipped(im):
    n = 25
    highs = np.full(n, 10.0)
    highs[10] = 12.0
    lows = np.full(n, 5.0)
    lows[10] = 0.0
    lows[12] = 4.0
    df = bars(np.full(n, 8.0), highs=highs, lows=lows)
    assert im.detect_intraday_darvas(df)["box_bottom"] == 4.0


def test_bb_nan_width_defaults_to_one(im):
    closes = [100.0] * 29 + [float("nan")]
    r = im.detect_bb_squeeze(bars(closes))
    assert r["bb_width%"] == 100.0 and r["signal"] is None


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="scan_one counts BEARISH_BURST as bullish too: 'BURST' is in the bullish keyword list",
)
def test_scan_one_bearish_burst_should_not_count_as_bullish(im, monkeypatch):
    monkeypatch.setattr(im, "fetch_intraday", lambda *a, **k: bars([100.0, 99.0]))
    _patch_patterns(im, monkeypatch, [None, None, None, "BEARISH_BURST", None, None])
    r = im.scan_one("ABC")
    assert (r["bullish_n"], r["bearish_n"], r["direction"]) == (0, 1, "BEARISH")
