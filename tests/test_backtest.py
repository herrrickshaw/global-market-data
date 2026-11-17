"""Walk-forward price-screen backtest (.bt_bin/backtest.py) — synthetic data, no I/O.

The engine lives in the hidden .bt_bin/ folder (README says `python backtest.py`),
so it is loaded by path here. datalink.load_market is monkeypatched: nothing reads
cache_seed/ltm/*.parquet.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parent.parent / ".bt_bin" / "backtest.py"
_spec = importlib.util.spec_from_file_location("bt_engine", _PATH)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def _frame(closes, start="2015-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": np.asarray(closes, dtype=float)}, index=idx)


def _universe(monkeypatch, frames):
    monkeypatch.setattr(bt.datalink, "load_market", lambda m, tier=None: dict(frames))


# ── pure pieces ─────────────────────────────────────────────────────────────────
def test_edge_metrics_hand_computed():
    m = bt.edge_metrics([10, 20, -5], [4, 4, 4])
    assert m == {"trades": 3, "avg_ret": 8.33, "mkt_ret": 4.0, "edge": 4.33, "win_rate": 0.667}
    assert bt.edge_metrics([], [])["trades"] == 0


def test_pit_needs_200_bars_and_ignores_the_future():
    c = np.linspace(100, 200, 300)
    assert bt._pit(c, 199) is None
    base = bt._pit(c, 220)
    poisoned = c.copy()
    poisoned[221:] = 1e9  # anything after t must not move a feature at t
    assert bt._pit(poisoned, 220) == base


def test_pit_feature_values_on_a_linear_ramp():
    c = np.arange(1.0, 301.0)  # close[t] = t + 1
    f = bt._pit(c, 299)
    assert f["sma50"] == pytest.approx(np.mean(c[250:300]))
    assert f["sma200"] == pytest.approx(np.mean(c[100:300]))
    assert f["rsi"] == 100.0  # no down days
    assert f["pct_from_high"] == 0.0
    assert f["ret126"] == pytest.approx((300 / c[-126] - 1) * 100)


def test_pit_non_positive_price_returns_none():
    c = np.linspace(100, 200, 260)
    c[250] = 0
    assert bt._pit(c, 250) is None


def test_unknown_screen_raises():
    with pytest.raises(ValueError):
        bt.backtest_screen("XX", "nope")


def test_short_history_and_glitch_bars_are_skipped(monkeypatch):
    short = _frame(np.linspace(100, 110, 150))  # < 200 + horizon + 1 bars
    glitch = np.linspace(100, 200, 400)
    glitch[250:] *= 10  # a 10x jump: every window spanning it is a >500% "return"
    _universe(monkeypatch, {"SHORT": short, "GLITCH": _frame(glitch)})
    r = bt.backtest_screen("XX", "golden_state", horizon=21, step=1)
    assert r["trades"] > 0
    assert r["avg_ret"] < 500


# ── BUG: picks were compared with the market mean pooled over ALL dates ────────
def test_identical_universe_has_zero_edge(monkeypatch):
    """Every symbol follows the same path, so on every as-of date a pick earns
    exactly the equal-weight market return of that window: the true edge is 0.
    The engine averaged the market over ALL windows instead of the pick's own, so
    a trend screen that only fires in up-legs showed a large phantom edge."""
    path = np.concatenate([np.linspace(100, 300, 400), np.linspace(300, 100, 400)])
    _universe(monkeypatch, {s: _frame(path) for s in ("A", "B", "C")})
    r = bt.backtest_screen("XX", "golden_state", horizon=21, step=21)
    assert r["trades"] > 0
    assert r["edge"] == pytest.approx(0.0, abs=0.01)
    assert r["win_rate"] == 0.0  # never strictly beats an identical market


def test_market_is_paired_by_calendar_date(monkeypatch):
    """Two names listed on different dates but priced identically on every shared
    date: measured against the market over the SAME calendar window the edge is 0.
    Pairing by row offset (t) or pooling all windows would not give 0."""
    path = np.concatenate([np.linspace(100, 300, 500), np.linspace(300, 100, 500)])
    early = _frame(path)
    late = pd.DataFrame({"Close": path[300:]}, index=early.index[300:])
    _universe(monkeypatch, {"EARLY": early, "LATE": late})
    r = bt.backtest_screen("XX", "golden_state", horizon=21, step=1)
    assert r["trades"] > 0
    assert r["edge"] == pytest.approx(0.0, abs=0.01)
