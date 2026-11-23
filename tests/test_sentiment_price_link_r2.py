"""sentiment_price_link: shock detection, linkage aggregation, historical study, monitor, CLI."""

import json
import sys

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import load
from tests.w4_helpers import fake_module


@pytest.fixture
def sp(monkeypatch, tmp_path):
    mod = load(monkeypatch, "sentiment_price_link", tmp_path)
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "ohlc")
    monkeypatch.setattr(mod, "REF_DIR", tmp_path / "ref")
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(mod, "MONITOR_LOG", tmp_path / "out" / "log.json")
    for d in ("ohlc", "ref", "out"):
        (tmp_path / d).mkdir()
    return mod


def _df(n=200, jumps=None, vols=None, base_vol=1000.0):
    """Flat-ish prices with tiny noise; `jumps` = {index: multiplicative move}."""
    rng = np.random.default_rng(7)
    rets = rng.normal(0, 0.002, n)
    for i, j in (jumps or {}).items():
        rets[i] = j
    close = 100 * np.cumprod(1 + rets)
    vol = np.full(n, base_vol)
    for i, v in (vols or {}).items():
        vol[i] = v
    return pd.DataFrame({"Close": close, "Volume": vol})


# ---- detect_shocks_and_returns --------------------------------------------------------------


def test_short_or_none_frame_returns_empty(sp):
    assert sp.detect_shocks_and_returns("X", None) == []
    # minimum length is TRAIL_WIN + 70 = 130 bars; just below is rejected outright
    assert sp.detect_shocks_and_returns("X", _df(129, {80: 0.1}, {80: 9000})) == []


def test_positive_shock_recorded_with_forward_returns(sp):
    df = _df(200, {80: 0.10}, {80: 9000})
    ev = sp.detect_shocks_and_returns("AAA", df)
    assert len(ev) == 1
    e = ev[0]
    assert e["symbol"] == "AAA" and e["direction"] == "POS" and e["z"] > 2
    c = df["Close"].values
    for label, h in sp.HORIZONS.items():
        assert e[f"fwd_{label}"] == pytest.approx((c[80 + h] - c[80]) / c[80] * 100)


def test_negative_shock_direction(sp):
    ev = sp.detect_shocks_and_returns("N", _df(200, {80: -0.10}, {80: 9000}))
    assert [e["direction"] for e in ev] == ["NEG"]
    assert ev[0]["z"] < -2


def test_big_move_without_volume_spike_is_ignored(sp):
    assert sp.detect_shocks_and_returns("X", _df(200, {80: 0.10})) == []


def test_volume_spike_threshold_is_inclusive_at_1_8x(sp):
    # avg_vol at i (20 bars incl. i) = (19*1000 + V)/20 ; V >= 1.8 * avg  <=>  V >= 2000*... solve
    # V = 1.8*(19000+V)/20  ->  V(1 - 0.09) = 1710  -> V = 1879.12...
    hi = sp.detect_shocks_and_returns("X", _df(200, {80: 0.10}, {80: 1880}))
    lo = sp.detect_shocks_and_returns("X", _df(200, {80: 0.10}, {80: 1870}))
    assert len(hi) == 1 and lo == []


def test_small_move_below_two_sigma_is_ignored(sp):
    df = _df(200, {80: 0.002}, {80: 9000})
    assert sp.detect_shocks_and_returns("X", df) == []


def test_late_shock_is_excluded_by_64_bar_horizon_guard(sp):
    # last scanned index is n-65; a shock there is kept, one bar later is not
    n = 200
    assert len(sp.detect_shocks_and_returns("X", _df(n, {n - 65: 0.1}, {n - 65: 9000}))) == 1
    assert sp.detect_shocks_and_returns("X", _df(n, {n - 64: 0.1}, {n - 64: 9000})) == []


def test_forward_return_nan_when_horizon_past_end(sp):
    n = 200
    e = sp.detect_shocks_and_returns("X", _df(n, {n - 65: 0.1}, {n - 65: 9000}))[0]
    assert not np.isnan(e["fwd_3mo"])  # i+63 = n-2 < n
    # i = n-65 -> every horizon (max 63) still fits; no NaN anywhere
    assert not any(np.isnan(v) for k, v in e.items() if k.startswith("fwd_"))


def test_zero_volume_treated_as_missing_and_zero_sigma_skipped(sp):
    df = _df(200, {80: 0.1}, {80: 0})
    assert sp.detect_shocks_and_returns("X", df) == []
    flat = pd.DataFrame({"Close": np.full(200, 100.0), "Volume": np.full(200, 1000.0)})
    assert sp.detect_shocks_and_returns("X", flat) == []


# ---- analyse_linkage ------------------------------------------------------------------------


def _events(direction, n, values):
    rows = []
    for i in range(n):
        rows.append(
            {
                "direction": direction,
                **{f"fwd_{k}": values[i % len(values)] for k in sp_horizons()},
            }
        )
    return rows


def sp_horizons():
    return ["1d", "1wk", "1mo", "3mo"]


def test_analyse_linkage_requires_20_observations(sp):
    ev = pd.DataFrame(_events("POS", 19, [1.0]))
    assert sp.analyse_linkage(ev).empty
    ev = pd.DataFrame(_events("POS", 20, [1.0]))
    out = sp.analyse_linkage(ev)
    assert len(out) == 4 and set(out["N_events"]) == {20}


def test_analyse_linkage_counts_only_non_nan_and_labels(sp):
    rows = _events("NEG", 25, [-2.0, 1.0, 3.0])
    ev = pd.DataFrame(rows)
    ev.loc[:5, "fwd_3mo"] = np.nan  # 19 left -> horizon dropped
    out = sp.analyse_linkage(ev)
    assert set(out["Horizon"]) == {"1d", "1wk", "1mo"}
    assert set(out["Implied_Sentiment"]) == {"NEGATIVE news-shock"}


def test_analyse_linkage_stats_values(sp):
    vals = list(range(1, 21))  # 1..20
    ev = pd.DataFrame({"direction": "POS", **{f"fwd_{k}": vals for k in sp_horizons()}})
    r = sp.analyse_linkage(ev).iloc[0]
    s = pd.Series(vals, dtype=float)
    w = s.clip(s.quantile(0.01), s.quantile(0.99))
    assert r["Implied_Sentiment"] == "POSITIVE news-shock"
    assert r["Median%"] == 10.5
    assert r["Mean_wins%"] == round(w.mean(), 3)
    assert r["Std%"] == round(w.std(), 2)
    assert r["Hit_rate%"] == 100.0


def test_analyse_linkage_winsorises_outliers_but_median_robust(sp):
    vals = [1.0] * 19 + [10_000.0]
    ev = pd.DataFrame({"direction": "POS", **{f"fwd_{k}": vals for k in sp_horizons()}})
    r = sp.analyse_linkage(ev).iloc[0]
    assert r["Median%"] == 1.0
    assert r["Mean_wins%"] < 600  # unwinsorised mean would be ~500.95; clipped at 99th pct


def test_analyse_linkage_hit_rate_counts_strictly_positive(sp):
    vals = [0.0] * 10 + [1.0] * 10
    ev = pd.DataFrame({"direction": "NEG", **{f"fwd_{k}": vals for k in sp_horizons()}})
    assert sp.analyse_linkage(ev).iloc[0]["Hit_rate%"] == 50.0


def test_analyse_linkage_handles_missing_direction(sp):
    ev = pd.DataFrame(_events("NEG", 20, [1.0]))
    out = sp.analyse_linkage(ev)
    assert set(out["Implied_Sentiment"]) == {"NEGATIVE news-shock"}
    assert sp.analyse_linkage(pd.DataFrame({"direction": []})).empty


# ---- run_historical -------------------------------------------------------------------------


class _Writer:
    def __init__(self, path, engine):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def xl(monkeypatch, sp):
    sheets = []
    monkeypatch.setattr(sp.pd, "ExcelWriter", _Writer)
    monkeypatch.setattr(
        pd.DataFrame, "to_excel", lambda self, w, name, **k: sheets.append((name, len(self)))
    )
    return sheets


def _put(path, n=200):
    pd.DataFrame({"Close": np.linspace(1, 2, n), "Volume": np.full(n, 10.0)}).to_parquet(path)


def _fake_events(direction, sign):
    def fn(symbol, df):
        return [
            {
                "symbol": symbol,
                "direction": direction,
                "z": 3.0,
                **{f"fwd_{k}": sign * (1 + i % 3) for k in sp_horizons()},
            }
            for i in range(25)
        ]

    return fn


def test_run_historical_market_filter_min_bars_and_dedup(sp, monkeypatch, xl, capsys):
    _put(sp.CACHE_DIR / "AAA.NS.parquet")
    _put(sp.CACHE_DIR / "BBB.BO.parquet")
    _put(sp.CACHE_DIR / "USA.parquet")
    _put(sp.CACHE_DIR / "USB.parquet")
    _put(sp.CACHE_DIR / "SHORT.NS.parquet", 199)
    _put(sp.REF_DIR / "AAA.NS.parquet")  # duplicate stem
    (sp.CACHE_DIR / "BAD.NS.parquet").write_text("junk")
    seen = []
    monkeypatch.setattr(sp, "detect_shocks_and_returns", lambda s, d: seen.append(s) or [])
    sp.run_historical("IN", 0)
    assert sorted(seen) == ["AAA.NS", "BBB.BO"]
    out = capsys.readouterr().out
    assert "2 stocks with ≥200 bars" in out
    assert "No shock events detected." in out
    seen.clear()
    sp.run_historical("US", 0)
    assert sorted(seen) == ["USA", "USB"]
    seen.clear()
    sp.run_historical("ALL", 0)
    assert sorted(seen) == ["AAA.NS", "BBB.BO", "USA", "USB"]
    seen.clear()
    sp.run_historical("US", 1)
    assert len(seen) == 1


def test_run_historical_full_output_continuation(sp, monkeypatch, xl, capsys):
    for n in ("A.NS", "B.NS"):
        _put(sp.CACHE_DIR / f"{n}.parquet")

    def fn(symbol, df):
        return _fake_events("POS", 1)(symbol, df) + _fake_events("NEG", -1)(symbol, df)

    monkeypatch.setattr(sp, "detect_shocks_and_returns", fn)
    sp.run_historical("IN", 0)
    out = capsys.readouterr().out
    assert "Detected 100 implied-news-shock events (50 positive, 50 negative)" in out
    assert "CONTINUATION (momentum) after good news" in out
    assert "CONTINUED WEAKNESS (drift) after bad news" in out
    assert [s[0] for s in xl] == ["DISCLAIMER", "Linkage_Summary", "All_Events"]
    assert xl[2][1] == 100
    assert "sentiment_link_IN_" in out


def test_run_historical_reversal_text(sp, monkeypatch, xl, capsys):
    _put(sp.CACHE_DIR / "A.NS.parquet")

    def fn(symbol, df):
        return _fake_events("POS", -1)(symbol, df) + _fake_events("NEG", 1)(symbol, df)

    monkeypatch.setattr(sp, "detect_shocks_and_returns", fn)
    sp.run_historical("IN", 0)
    out = capsys.readouterr().out
    assert "FADE/REVERSAL after good news" in out
    assert "REBOUND/REVERSAL after bad news" in out


def test_run_historical_zero_median_counts_as_fade(sp, monkeypatch, xl, capsys):
    _put(sp.CACHE_DIR / "A.NS.parquet")
    monkeypatch.setattr(sp, "detect_shocks_and_returns", lambda s, d: _fake_events("POS", 0)(s, d))
    sp.run_historical("IN", 0)
    out = capsys.readouterr().out
    assert "FADE/REVERSAL after good news" in out
    assert "bad news" not in out


# ---- run_monitor ----------------------------------------------------------------------------


class _Sent:
    def __init__(self, score, label, n):
        self.score, self.label, self.n_articles = score, label, n


def _fake_pipeline(monkeypatch, calls):
    class SP:
        def status(self):
            return "ok-status"

        def get_ticker_sentiment(self, t, market):
            calls.append((t, market))
            return _Sent(0.25 if t == "AAA" else -0.5, "Positive" if t == "AAA" else "Negative", 3)

    fake_module(monkeypatch, "sentiment_pipeline", SentimentPipeline=SP)


def test_run_monitor_import_error_message(sp, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "sentiment_pipeline", None)
    sp.run_monitor(["AAA"], "IN")
    assert "sentiment_pipeline.py not found." in capsys.readouterr().out
    assert not sp.MONITOR_LOG.exists()


def test_run_monitor_creates_log_and_appends(sp, monkeypatch, capsys):
    calls = []
    _fake_pipeline(monkeypatch, calls)
    sp.run_monitor(["AAA", "BBB"], "US")
    log = json.loads(sp.MONITOR_LOG.read_text())
    assert calls == [("AAA", "US"), ("BBB", "US")]
    assert [(r["ticker"], r["sentiment"], r["label"], r["n_articles"]) for r in log] == [
        ("AAA", 0.25, "Positive", 3),
        ("BBB", -0.5, "Negative", 3),
    ]
    assert all(r["market"] == "US" and len(r["date"]) == 10 for r in log)
    out = capsys.readouterr().out
    assert "ok-status" in out and "+0.250 (3 articles)" in out and "-0.500" in out
    assert "Total log: 2 rows." in out
    sp.run_monitor(["AAA"], "US")
    assert len(json.loads(sp.MONITOR_LOG.read_text())) == 3
    assert "Total log: 3 rows." in capsys.readouterr().out


# ---- main -----------------------------------------------------------------------------------


def test_main_defaults_run_historical(sp, monkeypatch, capsys):
    got = []
    monkeypatch.setattr(sys, "argv", ["x"])
    monkeypatch.setattr(sp, "run_historical", lambda m, mx: got.append((m, mx)))
    sp.main()
    assert got == [("IN", 0)]
    assert "SENTIMENT ↔ PRICE-MOVEMENT LINKAGE — IN" in capsys.readouterr().out


def test_main_us_max_passes_through(sp, monkeypatch):
    got = []
    monkeypatch.setattr(sys, "argv", ["x", "--market", "US", "--max", "7"])
    monkeypatch.setattr(sp, "run_historical", lambda m, mx: got.append((m, mx)))
    sp.main()
    assert got == [("US", 7)]


def test_main_monitor_requires_tickers(sp, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["x", "--monitor"])
    monkeypatch.setattr(sp, "run_monitor", lambda *a: pytest.fail("not reached"))
    monkeypatch.setattr(sp, "run_historical", lambda *a: pytest.fail("not reached"))
    sp.main()
    assert "--monitor needs --tickers" in capsys.readouterr().out


def test_main_monitor_with_tickers(sp, monkeypatch):
    got = []
    monkeypatch.setattr(sys, "argv", ["x", "--monitor", "--tickers", "A", "B", "--market", "US"])
    monkeypatch.setattr(sp, "run_monitor", lambda t, m: got.append((t, m)))
    monkeypatch.setattr(sp, "run_historical", lambda *a: pytest.fail("not reached"))
    sp.main()
    assert got == [(["A", "B"], "US")]
