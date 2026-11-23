"""nse_data_fetcher: symbol universe, regime, OHLC, events, institutional score, cache, CLI."""

import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import

NSE_NAMES = [
    "expiry_list",
    "fnolist",
    "get_blockdeals",
    "get_bulkdeals",
    "holiday_master",
    "indiavix",
    "nse_circular",
    "nse_eq_symbols",
    "nse_events",
    "nse_fiidii",
    "nse_get_index_quote",
    "nse_get_top_gainers",
    "nse_get_top_losers",
]


class Handlers:
    """Mutable behaviours the fake nsepython / yfinance / nsepy modules delegate to."""

    def __init__(self):
        self.h = {}
        self.downloads = []

    def call(self, name, *a, **k):
        v = self.h.get(name)
        if isinstance(v, Exception):
            raise v
        if callable(v):
            return v(*a, **k)
        return v


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    hd = Handlers()
    nse = fake_module(monkeypatch, "nsepython")
    for n in NSE_NAMES:
        setattr(nse, n, lambda *a, _n=n, **k: hd.call(_n, *a, **k))
    nse.index_pe_pb_div = lambda *a, **k: hd.call("index_pe_pb_div", *a, **k)

    def download(*a, **k):
        hd.downloads.append((a, k))
        return hd.call("download", *a, **k)

    fake_module(monkeypatch, "yfinance", download=download)
    fake_module(monkeypatch, "nsepy", get_history=lambda **k: hd.call("nsepy", **k))
    mod = fresh_import(monkeypatch, "nse_data_fetcher")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    mod.hd = hd
    return mod


def _ohlc(n=300, start=100.0, step=0.5, mi_ticker=None):
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    c = start + step * np.arange(n)
    df = pd.DataFrame(
        {"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": np.full(n, 1000.0)},
        index=idx,
    )
    if mi_ticker:
        df.columns = pd.MultiIndex.from_product([df.columns, [mi_ticker]])
    return df


# ---- import fallbacks ---------------------------------------------------------------------


def test_import_without_optional_libs(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("yfinance", "nsepython", "nsepy"):
        monkeypatch.setitem(sys.modules, name, None)  # forces ImportError
    mod = fresh_import(monkeypatch, "nse_data_fetcher")
    out = capsys.readouterr().out
    assert "yfinance not installed" in out and "nsepython not installed" in out
    assert (mod._YF_OK, mod._NSEPY_OK, mod._NSEPY_LIB_OK) == (False, False, False)
    f = mod.NSEDataFetcher()
    assert f.get_fno_symbols() == []
    assert f.get_upcoming_events().empty
    assert f.get_bulk_deals().empty and f.get_block_deals().empty and f.get_fii_dii().empty
    assert f.get_ohlc("X").empty
    assert f.get_market_breadth() == {"vix": None, "vix_regime": "NORMAL", "nifty_52w_pct": None}
    assert f.get_nifty_pe_pb() == {"pe": None, "pb": None, "div_yield": None, "date": None}
    r = f.get_regime()
    assert r["regime"] == "SIDEWAYS" and r["vix"] is None  # pct=0 -> sideways
    # no nse library -> Nifty 50 fallback list
    syms = f.get_all_symbols()
    assert len(syms) == 50 and "RELIANCE" in syms and "M&M" in syms


# ---- get_all_symbols ----------------------------------------------------------------------


def test_symbols_primary_and_cache(env):
    syms = [f"S{i}" for i in range(101)]
    calls = []
    env.hd.h["nse_eq_symbols"] = lambda: calls.append(1) or syms
    f = env.NSEDataFetcher()
    assert f.get_all_symbols() == syms
    assert f.get_all_symbols() == syms
    assert len(calls) == 1  # second served from cache


def test_symbols_boundary_100_is_rejected(env, monkeypatch):
    env.hd.h["nse_eq_symbols"] = [f"S{i}" for i in range(100)]
    monkeypatch.setitem(sys.modules, "nse", None)
    out = env.NSEDataFetcher().get_all_symbols()
    assert len(out) == 50  # fell through to the hardcoded list


def test_symbols_primary_error_then_bhavcopy(env, monkeypatch, tmp_path):
    env.hd.h["nse_eq_symbols"] = RuntimeError("down")
    csv = tmp_path / "bhav.csv"
    pd.DataFrame(
        {"SctySrs": ["EQ", "EQ", "BE", "EQ"], "TckrSymb": [" TCS ", "INFY", "JUNK", None]}
    ).to_csv(csv, index=False)
    seen = []

    class FakeNSE:
        def __init__(self, download_folder, server):
            assert server is False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def equityBhavcopy(self, d):
            seen.append(d)
            if len(seen) < 3:
                raise RuntimeError("holiday")
            return csv

    fake_module(monkeypatch, "nse", NSE=FakeNSE)
    f = env.NSEDataFetcher()
    assert f.get_all_symbols() == ["INFY", "TCS"]  # sorted, stripped, NaN dropped, series filtered
    assert len(seen) == 3
    assert f.get_all_symbols(series="EQ") == ["INFY", "TCS"]  # cached
    assert (seen[0] - seen[1]).days == 1


def test_symbols_bhavcopy_series_param_and_missing_columns(env, monkeypatch, tmp_path):
    env.hd.h["nse_eq_symbols"] = None
    good = tmp_path / "g.csv"
    pd.DataFrame({"SctySrs": ["EQ", "BE"], "TckrSymb": ["A", "B"]}).to_csv(good, index=False)
    bad = tmp_path / "b.csv"
    pd.DataFrame({"x": [1]}).to_csv(bad, index=False)
    missing = tmp_path / "nope.csv"

    class FakeNSE:
        n = 0

        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def equityBhavcopy(self, d):
            FakeNSE.n += 1
            return [missing, 5, bad, good][FakeNSE.n - 1]

    fake_module(monkeypatch, "nse", NSE=FakeNSE)
    f = env.NSEDataFetcher()
    assert f.get_all_symbols(series="BE") == ["B"]
    assert FakeNSE.n == 4


def test_symbols_bhavcopy_all_days_fail_uses_fallback(env, monkeypatch):
    env.hd.h["nse_eq_symbols"] = []

    class FakeNSE:
        calls = 0

        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def equityBhavcopy(self, d):
            FakeNSE.calls += 1
            raise RuntimeError("x")

    fake_module(monkeypatch, "nse", NSE=FakeNSE)
    assert len(env.NSEDataFetcher().get_all_symbols()) == 50
    assert FakeNSE.calls == 7


def test_fno_symbols(env):
    f = env.NSEDataFetcher()
    env.hd.h["fnolist"] = ["A", "B"]
    assert f.get_fno_symbols() == ["A", "B"]
    env.hd.h["fnolist"] = pd.Series(["X"])
    assert f.get_fno_symbols() == ["X"]
    env.hd.h["fnolist"] = "string"
    assert f.get_fno_symbols() == []
    env.hd.h["fnolist"] = RuntimeError("x")
    assert f.get_fno_symbols() == []


# ---- regime -------------------------------------------------------------------------------


def _quote(last=100.0, hi=120.0, lo=80.0):
    return {"last": last, "yearHigh": hi, "yearLow": lo}


def test_regime_full_inputs(env):
    h = env.hd.h
    h["nse_get_index_quote"] = _quote(110.0, 120.0, 80.0)
    h["download"] = _ohlc(260, start=100, step=1.0, mi_ticker="^NSEI")
    h["indiavix"] = "13.456"
    fii = pd.DataFrame({"FII Net": [2e7, 1e7, 5e6, 0, -1e6, 9e9], "DII Net": [1, 2, 3, 4, 5, 6]})
    h["nse_fiidii"] = fii
    r = env.NSEDataFetcher().get_regime()
    assert r["nifty_last"] == 110.0  # live quote wins over yfinance
    assert r["nifty_52w_h"] == 120.0 and r["nifty_52w_l"] == 80.0
    assert r["pct_from_52wh"] == round((110 - 120) / 120 * 100, 2)
    assert r["vix"] == 13.46
    # head(5) of FII: 2e7+1e7+5e6+0-1e6 = 3.4e7 -> /1e7
    assert r["fii_net_cr"] == 3.4 and r["fii_sentiment"] == "BUYING"
    assert r["dma200"] > 0 and r["dma200_sl"] > 0
    assert r["regime"] == "BULL"  # well above 200dma, vix < 18


def test_regime_classification_matrix(env):
    f = env.NSEDataFetcher()
    h = env.hd.h
    h["nse_get_index_quote"] = None
    h["fnolist"] = None
    h["nse_fiidii"] = None

    def run(step, vix):
        h["download"] = _ohlc(260, start=1000, step=step)
        h["indiavix"] = vix
        return f.get_regime()

    assert run(5.0, 17.99)["regime"] == "BULL"
    assert run(5.0, 18.0)["regime"] == "BULL_VOLATILE"
    assert run(-5.0, 25.0)["regime"] == "BEAR"
    assert run(-5.0, 25.01)["regime"] == "BEAR_EXTREME"
    assert run(0.0, 40)["regime"] == "SIDEWAYS"
    r = run(-5.0, None)  # missing vix defaults to 15
    assert r["regime"] == "BEAR" and r["vix"] is None


def test_regime_sideways_boundary(env, monkeypatch):
    h = env.hd.h
    h["nse_get_index_quote"] = None
    h["indiavix"] = None
    h["nse_fiidii"] = None
    f = env.NSEDataFetcher()
    # constant series then last bar +x% above 200dma window mean
    df = _ohlc(200, start=100, step=0.0)
    df.iloc[-1, df.columns.get_loc("Close")] = 100.0
    h["download"] = df
    assert f.get_regime()["regime"] == "SIDEWAYS"
    # a last close ~ +2.5% above the DMA (mean rises by 0.0125*... ) => pct > 1.5
    df2 = _ohlc(200, start=100, step=0.0)
    df2.iloc[-1, df2.columns.get_loc("Close")] = 100.0 + 200 * 0.025 / (1 - 0.025 / 1) * 0 + 600
    h["download"] = df2
    r = f.get_regime()
    assert r["pct_from_dma200"] > 1.5 and r["regime"] == "BULL"


def test_regime_error_paths_and_empty_yf(env):
    h = env.hd.h
    h["nse_get_index_quote"] = RuntimeError("x")
    h["download"] = pd.DataFrame()
    h["indiavix"] = RuntimeError("x")
    h["nse_fiidii"] = RuntimeError("x")
    r = env.NSEDataFetcher().get_regime()
    assert r["nifty_last"] is None and r["vix"] is None and r["fii_sentiment"] == "UNKNOWN"
    assert "dma200" not in r and r["regime"] == "SIDEWAYS"


def test_regime_zero_year_high_and_yfinance_fills_last(env):
    h = env.hd.h
    h["nse_get_index_quote"] = {"last": 0, "yearHigh": 0, "yearLow": 0}
    h["download"] = _ohlc(30, start=100, step=1.0)  # non-MultiIndex, short series
    h["indiavix"] = 0
    h["nse_fiidii"] = pd.DataFrame()
    r = env.NSEDataFetcher().get_regime()
    assert r["pct_from_52wh"] is None
    assert r["nifty_last"] == round(129.0, 2)  # falls back to yfinance last close
    assert r["vix"] is None  # 0 is falsy


def test_regime_fii_column_selection_and_selling(env):
    h = env.hd.h
    h["nse_get_index_quote"] = None
    h["download"] = pd.DataFrame()
    h["indiavix"] = 10
    f = env.NSEDataFetcher()
    # only a generic "net" column (no fii in name)
    h["nse_fiidii"] = pd.DataFrame({"Net Value": [-500.0, -100.0, np.nan]})
    r = f.get_regime()
    assert r["fii_net_cr"] == -600.0  # small magnitude kept as-is
    assert r["fii_sentiment"] == "SELLING"
    h["nse_fiidii"] = pd.DataFrame({"buy": [1, 2]})  # no net col
    assert f.get_regime()["fii_sentiment"] == "UNKNOWN"
    h["nse_fiidii"] = pd.DataFrame({"fii net": [1e6]})  # |x| == 1e6 is NOT > 1e6
    r = f.get_regime()
    assert r["fii_net_cr"] == 1e6
    h["nse_fiidii"] = pd.DataFrame({"fii net": [1.1e6]})
    assert f.get_regime()["fii_net_cr"] == round(1.1e6 / 1e7, 2)
    h["nse_fiidii"] = pd.DataFrame({"fii net": [0.0]})
    assert f.get_regime()["fii_sentiment"] == "SELLING"  # zero is not "buying"


# ---- get_ohlc -----------------------------------------------------------------------------


def test_ohlc_nsepy_first_when_dates_given(env):
    df = _ohlc(5).iloc[::-1]
    df["Extra"] = 1
    env.hd.h["nsepy"] = df
    out = env.NSEDataFetcher().get_ohlc("ABC", start=date(2023, 1, 1), end=date(2023, 1, 5))
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out.index.is_monotonic_increasing
    assert env.hd.downloads == []


def test_ohlc_nsepy_needs_both_dates(env):
    env.hd.h["nsepy"] = _ohlc(5)
    env.hd.h["download"] = _ohlc(7)
    out = env.NSEDataFetcher().get_ohlc("ABC", start=date(2023, 1, 1))
    assert len(out) == 7
    assert env.hd.downloads[0][0] == ("ABC.NS",)
    assert env.hd.downloads[0][1]["period"] == "1y"


def test_ohlc_nsepy_failure_or_empty_falls_to_yfinance(env):
    f = env.NSEDataFetcher()
    env.hd.h["download"] = _ohlc(4)
    for bad in (RuntimeError("ssl"), pd.DataFrame(), None):
        env.hd.h["nsepy"] = bad
        env.hd.downloads.clear()
        out = f.get_ohlc("ABC", start=date(2023, 1, 1), end=date(2023, 2, 1))
        assert len(out) == 4
        k = env.hd.downloads[0][1]
        assert k["start"] == date(2023, 1, 1) and k["end"] == date(2023, 2, 1)
        assert "period" not in k


def test_ohlc_yfinance_multiindex_dropna_and_period(env):
    df = _ohlc(6, mi_ticker="ABC.NS")
    df.iloc[2, 0] = np.nan
    env.hd.h["download"] = df
    out = env.NSEDataFetcher().get_ohlc("ABC", period="5y")
    assert len(out) == 5
    assert env.hd.downloads[0][1]["period"] == "5y"


def test_ohlc_yfinance_empty_and_error(env, capsys):
    f = env.NSEDataFetcher()
    env.hd.h["download"] = pd.DataFrame()
    assert f.get_ohlc("ABC").empty
    env.hd.h["download"] = RuntimeError("rate")
    assert f.get_ohlc("ABC").empty
    assert "yfinance failed for ABC: rate" in capsys.readouterr().out


# ---- get_ohlc_bulk ------------------------------------------------------------------------


def _multi(tickers, n=40):
    frames = {}
    for t in tickers:
        d = _ohlc(n)
        for c in d.columns:
            frames[(c, t)] = d[c]
    return pd.DataFrame(frames)


def test_bulk_batches_multiindex_and_min_length(env, capsys):
    sleeps = []
    env.time.sleep = sleeps.append
    short = _multi(["B.NS"], n=29)
    full = _multi(["A.NS"], n=30)
    raw = pd.concat([full, short], axis=1)
    env.hd.h["download"] = raw
    out = env.NSEDataFetcher().get_ohlc_bulk(
        ["A", "B", "C"], period="2y", batch_size=2, sleep_sec=7
    )
    assert set(out) == {"A"}  # 30 rows OK, 29 rejected, C missing -> KeyError ignored
    assert list(out["A"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert sleeps == [7]  # one gap between the two batches, none after the last
    assert [len(c[0][0]) for c in env.hd.downloads] == [2, 1]
    assert env.hd.downloads[0][1] == {
        "period": "2y",
        "auto_adjust": True,
        "threads": True,
        "progress": False,
    }
    text = capsys.readouterr().out
    assert "Bulk OHLC: 3 stocks | 2 batches | period=2y | batch_size=2" in text
    assert "Total: 1/3 tickers downloaded" in text


def test_bulk_single_ticker_flat_frame_and_empty(env, capsys):
    env.hd.h["download"] = _ohlc(3)
    f = env.NSEDataFetcher()
    out = f.get_ohlc_bulk(["A"])
    assert list(out) == ["A"] and len(out["A"]) == 3
    env.hd.h["download"] = pd.DataFrame()
    assert f.get_ohlc_bulk(["A"]) == {}
    assert "empty" in capsys.readouterr().out


def test_bulk_rate_limit_retry_then_success_and_give_up(env, capsys):
    waits = []
    env.time.sleep = waits.append
    state = {"n": 0}

    def dl(*a, **k):
        state["n"] += 1
        if state["n"] <= 2:
            raise RuntimeError("429 Too Many Requests")
        return _ohlc(3)

    env.hd.h["download"] = dl
    out = env.NSEDataFetcher().get_ohlc_bulk(["A"])
    assert "A" in out and waits == [30, 60]
    # always rate-limited -> 3 attempts, waits 30/60/90, nothing returned
    waits.clear()
    env.hd.h["download"] = RuntimeError("Rate limit")
    assert env.NSEDataFetcher().get_ohlc_bulk(["A"]) == {}
    assert waits == [30, 60, 90]
    assert "Rate limited — waiting 30s" in capsys.readouterr().out


def test_bulk_other_error_breaks_without_retry(env, capsys):
    env.hd.h["download"] = RuntimeError("boom")
    out = env.NSEDataFetcher().get_ohlc_bulk(["A"])
    assert out == {} and len(env.hd.downloads) == 1
    assert "ERROR: boom" in capsys.readouterr().out


# ---- events / calendar --------------------------------------------------------------------


def _events():
    today = datetime.today()
    fmt = lambda d: d.strftime("%d-%b-%Y")  # noqa: E731
    return pd.DataFrame(
        {
            "symbol": ["LATE", "SOON", "BAD", "OLD", "GAP"],
            "company": ["l", "s", "b", "o", "g"],
            "purpose": ["Quarterly Results", "Board Meeting - Financial Results", "x", "AGM", None],
            "date": [
                fmt(today + timedelta(days=60)),
                fmt(today + timedelta(days=5)),
                "not-a-date",
                fmt(today - timedelta(days=10)),
                fmt(today + timedelta(days=2)),
            ],
        }
    )


def test_upcoming_events_filters_and_sorts(env):
    env.hd.h["nse_events"] = _events()
    out = env.NSEDataFetcher().get_upcoming_events(days_ahead=30)
    assert list(out["symbol"]) == ["OLD", "GAP", "SOON"]  # sorted by date; LATE and BAD gone
    assert list(out.columns) == ["symbol", "company", "purpose", "date", "days_away"]
    assert out["days_away"].iloc[-1] in (4, 5)
    assert list(out.index) == [0, 1, 2]
    wide = env.NSEDataFetcher().get_upcoming_events(days_ahead=90)
    assert "LATE" in set(wide["symbol"])


def test_upcoming_events_empty_error_and_nonframe(env, capsys):
    f = env.NSEDataFetcher()
    for v in (pd.DataFrame(), None, "x"):
        env.hd.h["nse_events"] = v
        assert f.get_upcoming_events().empty
    env.hd.h["nse_events"] = RuntimeError("down")
    assert f.get_upcoming_events().empty
    assert "nse_events error: down" in capsys.readouterr().out


def test_results_calendar_keyword_filter(env):
    env.hd.h["nse_events"] = _events()
    f = env.NSEDataFetcher()
    out = f.get_results_calendar(days_ahead=90)
    assert set(out["symbol"]) == {"LATE", "SOON"}  # AGM / None purposes excluded
    assert list(out.index) == [0, 1]
    env.hd.h["nse_events"] = None
    assert f.get_results_calendar().empty


# ---- deals / fii --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,key,label", [("get_bulk_deals", "get_bulkdeals", "get_bulkdeals error")]
)
def test_bulk_deals(env, capsys, method, key, label):
    f = env.NSEDataFetcher()
    df = pd.DataFrame({"Symbol": ["A"]})
    env.hd.h[key] = df
    assert getattr(f, method)() is df
    env.hd.h[key] = pd.DataFrame()
    assert getattr(f, method)().empty
    env.hd.h[key] = RuntimeError("bad")
    assert getattr(f, method)().empty
    assert f"{label}: bad" in capsys.readouterr().out


def test_block_deals(env, capsys):
    f = env.NSEDataFetcher()
    df = pd.DataFrame({"Symbol": ["A"]})
    env.hd.h["get_blockdeals"] = df
    assert f.get_block_deals() is df
    env.hd.h["get_blockdeals"] = None
    assert f.get_block_deals().empty
    env.hd.h["get_blockdeals"] = RuntimeError("bad")
    assert f.get_block_deals().empty
    assert "get_blockdeals error: bad" in capsys.readouterr().out


def test_fii_dii(env, capsys):
    f = env.NSEDataFetcher()
    df = pd.DataFrame({"a": [1]})
    env.hd.h["nse_fiidii"] = df
    assert f.get_fii_dii() is df
    env.hd.h["nse_fiidii"] = "junk"
    assert f.get_fii_dii().empty
    env.hd.h["nse_fiidii"] = RuntimeError("bad")
    assert f.get_fii_dii().empty
    assert "nse_fiidii error: bad" in capsys.readouterr().out


# ---- institutional score ------------------------------------------------------------------


def _inst(env, fii=None, dii=None, deals=None):
    cols = {}
    if fii is not None:
        cols["FII Net"] = fii
    if dii is not None:
        cols["DII Net"] = dii
    env.hd.h["nse_fiidii"] = pd.DataFrame(cols) if cols else pd.DataFrame()
    env.hd.h["get_bulkdeals"] = (
        pd.DataFrame({"Buy/Sell": deals}) if deals is not None else pd.DataFrame()
    )
    return env.NSEDataFetcher().get_institutional_activity_score()


def test_inst_score_strong_buy(env):
    r = _inst(env, fii=[2e7], dii=[3e6], deals=["buy", "BUY", "Buy"])
    assert r["score"] == 3 and r["label"] == "STRONG_BUY"
    assert r["fii_5d_net_cr"] == 2.0 and r["dii_5d_net_cr"] == 0.3
    assert (r["bulk_buys"], r["bulk_sells"], r["bulk_buy_ratio"]) == (3, 0, 100.0)


def test_inst_score_strong_sell_and_small_values_unscaled(env):
    r = _inst(env, fii=[-5.0], dii=[-6.0], deals=["SELL", "SELL"])
    assert r["score"] == -3 and r["label"] == "STRONG_SELL"
    assert r["fii_5d_net_cr"] == -5.0  # |x| <= 1e6 left as-is
    assert r["bulk_buy_ratio"] == 0.0


def test_inst_score_sell_label_at_minus_two(env):
    r = _inst(env, fii=[-1.0], dii=[-1.0], deals=["BUY", "SELL"])
    assert r["score"] == -2 and r["label"] == "SELL"  # 0.5 ratio adds 0


def test_inst_score_neutral_and_buy_labels(env):
    assert _inst(env)["score"] == 0 and _inst(env)["label"] == "NEUTRAL"
    r = _inst(env, fii=[1.0], dii=[-1.0])
    assert r["score"] == 0 and r["label"] == "NEUTRAL"
    r = _inst(env, fii=[1.0], dii=[-1.0], deals=["BUY", "BUY"])
    assert r["score"] == 1 and r["label"] == "BUY"
    r = _inst(env, fii=[1.0], dii=[1.0])
    assert r["score"] == 2 and r["label"] == "BUY"


def test_inst_score_buy_ratio_boundaries(env):
    # 3/5 = 0.6 is NOT > 0.6; 2/5 = 0.4 is NOT < 0.4
    assert _inst(env, deals=["BUY"] * 3 + ["SELL"] * 2)["score"] == 0
    assert _inst(env, deals=["BUY"] * 2 + ["SELL"] * 3)["score"] == 0
    assert _inst(env, deals=["BUY"] * 4 + ["SELL"] * 2)["score"] == 1
    assert _inst(env, deals=["BUY"] * 2 + ["SELL"] * 4)["score"] == -1
    assert _inst(env, deals=["HOLD"])["score"] == 0  # no buy/sell rows -> total 0


def test_inst_score_only_fii_or_only_dii_columns_and_errors(env):
    r = _inst(env, fii=[1.0])
    assert "dii_5d_net_cr" not in r and r["score"] == 1
    r = _inst(env, dii=[-1.0])
    assert "fii_5d_net_cr" not in r and r["score"] == -1
    env.hd.h["nse_fiidii"] = pd.DataFrame({"FII Net": ["x", "y"]})
    env.hd.h["get_bulkdeals"] = pd.DataFrame({"Other": [1]})
    r = env.NSEDataFetcher().get_institutional_activity_score()
    assert r["score"] == 0  # unparsable net values swallowed; bulk lacks Buy/Sell


# ---- breadth / pe-pb ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "vix,label",
    [
        (31, "PANIC"),
        (30, "HIGH_FEAR"),
        (22.01, "HIGH_FEAR"),
        (22, "ELEVATED"),
        (17.01, "ELEVATED"),
        (17, "NORMAL"),
        (12.01, "NORMAL"),
        (12, "COMPLACENCY"),
    ],
)
def test_breadth_vix_regime_boundaries(env, vix, label):
    env.hd.h["indiavix"] = vix
    env.hd.h["nse_get_index_quote"] = None
    r = env.NSEDataFetcher().get_market_breadth()
    assert r["vix_regime"] == label and r["vix"] == round(vix, 2)


def test_breadth_quote_fields_and_missing(env):
    f = env.NSEDataFetcher()
    env.hd.h["indiavix"] = None
    env.hd.h["nse_get_index_quote"] = _quote(110.0, 120.0, 80.0)
    r = f.get_market_breadth()
    assert r["vix"] is None and r["vix_regime"] == "NORMAL"
    assert r["nifty_last"] == 110.0 and r["nifty_52w_high"] == 120.0
    assert r["pct_from_52wh"] == -8.33 and r["pct_from_52wl"] == 37.5
    env.hd.h["nse_get_index_quote"] = {"last": 1, "yearHigh": 0, "yearLow": 5}
    assert "nifty_last" not in f.get_market_breadth()
    env.hd.h["nse_get_index_quote"] = {"last": None, "yearHigh": None, "yearLow": None}
    assert "nifty_last" not in f.get_market_breadth()
    env.hd.h["indiavix"] = RuntimeError("x")
    assert f.get_market_breadth()["vix"] is None


def test_nifty_pe_pb_fetch_cache_write_and_reuse(env, tmp_path):
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    df = pd.DataFrame({"PE": [20.0, 22.5], "PB": [3.0, 3.4], "DivYield": [1.1, 1.2]}, index=idx)
    args = []
    env.hd.h["index_pe_pb_div"] = lambda *a: args.append(a) or df
    f = env.NSEDataFetcher()
    r = f.get_nifty_pe_pb()
    assert (r["pe"], r["pb"], r["div_yield"]) == (22.5, 3.4, 1.2)
    assert r["date"].startswith("2024-01-02")
    assert args[0][0] == "NIFTY 50"
    assert (
        datetime.strptime(args[0][2], "%d-%m-%Y") - datetime.strptime(args[0][1], "%d-%m-%Y")
    ).days == 7
    cache = tmp_path / "nse_cache" / "nifty_pe_pb.json"
    assert cache.exists()
    env.hd.h["index_pe_pb_div"] = RuntimeError("must not be called")
    assert f.get_nifty_pe_pb() == r  # fresh cache served
    # expired cache (ttl 0) -> refetch, fails -> None dict
    stale = env.NSEDataFetcher(cache_ttl_sec=0)
    assert stale.get_nifty_pe_pb() == {"pe": None, "pb": None, "div_yield": None, "date": None}


def test_nifty_pe_pb_empty_frame(env):
    env.hd.h["index_pe_pb_div"] = pd.DataFrame()
    assert env.NSEDataFetcher().get_nifty_pe_pb()["pe"] is None


# ---- live context / recommendations -------------------------------------------------------


def test_cache_ttl(env, monkeypatch):
    f = env.NSEDataFetcher(cache_ttl_sec=10)
    now = [1000.0]
    monkeypatch.setattr(env.time, "time", lambda: now[0])
    assert f._get_cache("k") is None
    f._set_cache("k", [1])
    now[0] = 1009.9
    assert f._get_cache("k") == [1]
    now[0] = 1010.0
    assert f._get_cache("k") is None


def test_recommendation_by_regime_and_overlays(env):
    rec = env._get_strategy_recommendation
    assert rec("BULL", 15, 0)[0].startswith("📈 BULL MARKET")
    assert len(rec("BULL", 15, 0)) == 4
    assert rec("BULL_VOLATILE", 15, 0)[0].startswith("⚡")
    assert rec("BEAR", 15, 0)[0].startswith("📉")
    assert rec("BEAR_EXTREME", 15, 0)[0].startswith("🚨")
    assert rec("SIDEWAYS", 15, 0)[0].startswith("⬛")
    assert rec("???", 15, 0) == ["⚠️  Unknown regime — wait for clarity"]
    assert rec("BULL", 25.5, 0)[-1] == "   🔴 VIX=25.5 (PANIC) — halve standard position sizes"
    assert "(ELEVATED)" in rec("BULL", 25, 0)[-1]  # 25 is not > 25
    assert "(ELEVATED)" in rec("BULL", 18.5, 0)[-1]
    assert len(rec("BULL", 18, 0)) == 4  # 18 is not > 18
    assert "(COMPLACENCY)" in rec("BULL", 11.9, 0)[-1]
    assert len(rec("BULL", 12, 0)) == 4
    assert len(rec("BULL", 0, 0)) == 4  # falsy vix: no overlay
    assert (
        rec("BULL", 15, 2)[-1] == "   ✅ Institutional buying (+2) — increase conviction on signals"
    )
    assert len(rec("BULL", 15, 1)) == 4
    assert rec("BULL", 15, -2)[-1].startswith("   ❌ Institutional selling (-2)")
    assert len(rec("BULL", 15, -1)) == 4


def _wire_context(env, vix=14.0, deals=("BUY", "BUY", "SELL")):
    h = env.hd.h
    h["nse_get_index_quote"] = _quote(110.0, 120.0, 80.0)
    h["download"] = _ohlc(260, start=100, step=1.0)
    h["indiavix"] = vix
    h["nse_fiidii"] = pd.DataFrame({"FII Net": [2e7], "DII Net": [1e7]})
    h["get_bulkdeals"] = pd.DataFrame({"Buy/Sell": list(deals)})
    h["nse_events"] = pd.DataFrame(
        {
            "symbol": [f"R{i}" for i in range(10)],
            "company": ["c"] * 10,
            "purpose": ["Financial Results"] * 10,
            "date": [(datetime.today() + timedelta(days=1)).strftime("%d-%b-%Y")] * 10,
        }
    )


def test_live_context_merges_sources(env):
    _wire_context(env)
    ctx = env.NSEDataFetcher().get_live_context()
    assert ctx["regime"] == "BULL"
    assert ctx["vix_regime"] == "NORMAL" and ctx["pct_from_52wh"] == -8.33
    assert ctx["nifty_last"] == 110.0  # regime value kept, breadth did not override
    assert ctx["nifty_52w_high"] == 120.0  # breadth-only key merged
    assert ctx["institutional_score"] == 3 and ctx["institutional_label"] == "STRONG_BUY"
    assert ctx["bulk_buy_ratio"] == 66.7
    assert ctx["results_count_7d"] == 10 and ctx["results_next7d"][0] == "R0"
    assert ctx["recommendations"][0].startswith("📈 BULL MARKET")


def test_live_context_no_events(env):
    _wire_context(env)
    env.hd.h["nse_events"] = pd.DataFrame()
    ctx = env.NSEDataFetcher().get_live_context()
    assert ctx["results_next7d"] == [] and ctx["results_count_7d"] == 0


def test_print_live_context_full(env, capsys):
    _wire_context(env)
    ctx = env.NSEDataFetcher().print_live_context()
    out = capsys.readouterr().out
    assert ctx["regime"] == "BULL"
    assert "Market Regime:  🟢 BULL" in out
    assert "% from 52wH:    -8.33%" in out
    assert "India VIX:      🟢 14.0  [NORMAL]" in out
    assert "Low volatility — trend-following works well" in out
    assert "FII Sentiment:  BUYING" in out
    assert "Inst. Activity: STRONG_BUY (score=+3)" in out
    assert "Bulk deal buy%: 66.7%" in out
    assert "Results next 7d: 10 stocks — R0, R1, R2, R3, R4, R5, R6, R7..." in out
    assert "TODAY'S STRATEGY RECOMMENDATIONS" in out


@pytest.mark.parametrize(
    "vix,colour,text",
    [
        (16.0, "🟡", "Elevated — widen stop-losses"),
        (30.0, "🔴", "HIGH FEAR — reduce position size"),
    ],
)
def test_print_live_context_vix_bands(env, capsys, vix, colour, text):
    _wire_context(env, vix=vix, deals=())
    env.hd.h["nse_events"] = pd.DataFrame()
    env.hd.h["nse_get_index_quote"] = None
    env.NSEDataFetcher().print_live_context()
    out = capsys.readouterr().out
    assert f"India VIX:      {colour} {vix}" in out
    assert text in out
    assert "Bulk deal buy%" not in out
    assert "Results next 7d: 0 stocks — " in out and "..." not in out
    assert "% from 52wH:    —" in out


# ---- module-level helpers -----------------------------------------------------------------


def test_singleton_and_wrappers(env):
    a = env.get_fetcher()
    assert env.get_fetcher() is a
    env.hd.h["nse_eq_symbols"] = [f"S{i}" for i in range(150)]
    assert len(env.get_nse_symbols()) == 150
    env.hd.h["nse_get_index_quote"] = None
    env.hd.h["download"] = pd.DataFrame()
    env.hd.h["indiavix"] = None
    env.hd.h["nse_fiidii"] = None
    assert env.get_live_regime()["regime"] == "SIDEWAYS"
    env.hd.h["nse_events"] = _events()
    out = env.get_upcoming_results(days=90)
    assert set(out["symbol"]) == {"LATE", "SOON"}
    assert set(env.get_upcoming_results().symbol) == {"SOON"}  # default 7d window


def test_institutional_confirmation(env):
    h = env.hd.h
    h["get_bulkdeals"] = pd.DataFrame()
    assert env.get_institutional_confirmation("A") is False
    h["get_bulkdeals"] = pd.DataFrame({"X": [1]})
    assert env.get_institutional_confirmation("A") is False
    h["get_bulkdeals"] = pd.DataFrame(
        {"Symbol": ["abc", "ABC", "XYZ"], "Buy/Sell": ["SELL", "sell", "BUY"]}
    )
    assert bool(env.get_institutional_confirmation("abc")) is False
    h["get_bulkdeals"] = pd.DataFrame(
        {"Symbol": ["abc", "ABC", "XYZ"], "Buy/Sell": ["SELL", "buy", "BUY"]}
    )
    assert bool(env.get_institutional_confirmation("Abc")) is True
    assert env.get_institutional_confirmation("NONE") is False
    assert bool(env.get_institutional_confirmation("xyz")) is True
    h["get_bulkdeals"] = pd.DataFrame({"Symbol": ["ABC"]})  # no Buy/Sell column
    assert env.get_institutional_confirmation("abc") is True


# ---- CLI ----------------------------------------------------------------------------------


def _run_cli(env, monkeypatch, argv):
    import runpy
    from pathlib import Path

    monkeypatch.setattr(sys, "argv", ["nse_data_fetcher.py", *argv])
    repo = Path(__file__).resolve().parent.parent / "nse_data_fetcher.py"
    runpy.run_path(str(repo), run_name="__main__")


def test_cli_symbols_events_bulk(env, monkeypatch, capsys):
    env.hd.h["nse_eq_symbols"] = [f"S{i}" for i in range(120)]
    env.hd.h["nse_events"] = pd.DataFrame()
    env.hd.h["get_bulkdeals"] = pd.DataFrame({"Symbol": ["A"]})
    env.hd.h["nse_get_index_quote"] = None
    env.hd.h["download"] = pd.DataFrame()
    env.hd.h["indiavix"] = None
    env.hd.h["nse_fiidii"] = None
    _run_cli(env, monkeypatch, ["--symbols", "--events", "--bulk"])
    out = capsys.readouterr().out
    assert "NSE EQ Universe: 120 symbols" in out
    assert "Sample: ['S0', 'S1'" in out
    assert "Upcoming Events (30 days):\nNo events" in out
    assert "Today's Bulk Deals (1):" in out
    assert "LIVE MARKET CONTEXT" in out  # --full defaults to True


def test_cli_events_present_and_no_bulk(env, monkeypatch, capsys):
    env.hd.h["nse_events"] = _events()
    env.hd.h["get_bulkdeals"] = pd.DataFrame()
    env.hd.h["nse_get_index_quote"] = None
    env.hd.h["download"] = pd.DataFrame()
    env.hd.h["indiavix"] = None
    env.hd.h["nse_fiidii"] = None
    _run_cli(env, monkeypatch, ["--events", "--bulk"])
    out = capsys.readouterr().out
    assert "SOON" in out and "No events" not in out
    assert "No bulk deals today" in out


def test_regime_yfinance_exception_is_swallowed(env):
    h = env.hd.h
    h["nse_get_index_quote"] = _quote(100.0, 100.0, 50.0)
    h["download"] = RuntimeError("yahoo down")
    h["indiavix"] = 20
    h["nse_fiidii"] = None
    r = env.NSEDataFetcher().get_regime()
    assert "dma200" not in r and r["nifty_last"] == 100.0 and r["vix"] == 20.0
    assert r["pct_from_52wh"] == 0.0 and r["regime"] == "SIDEWAYS"


def test_inst_score_non_string_buy_sell_column_swallowed(env):
    env.hd.h["nse_fiidii"] = pd.DataFrame({"FII Net": [1.0]})
    env.hd.h["get_bulkdeals"] = pd.DataFrame({"Buy/Sell": [1, 2]})
    r = env.NSEDataFetcher().get_institutional_activity_score()
    assert r["score"] == 1 and "bulk_buys" not in r


def test_regime_empty_fii_frame_with_net_column_is_unknown(env):
    h = env.hd.h
    h["nse_get_index_quote"] = {"last": 0, "yearHigh": 0, "yearLow": 0}
    h["download"] = _ohlc(30, start=100, step=1.0)
    h["indiavix"] = 0
    h["nse_fiidii"] = pd.DataFrame({"fii net": []})
    r = env.NSEDataFetcher().get_regime()
    assert r["fii_sentiment"] == "UNKNOWN"
    assert "fii_net_cr" not in r or r["fii_net_cr"] is None
