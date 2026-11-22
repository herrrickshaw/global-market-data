"""stock_utils: statement helpers, bulk download with retry/back-off, parallel_map, math helpers."""

import numpy as np
import pandas as pd
import pytest

import stock_utils as su
from tests.w4_helpers import fake_module


def _stmt():
    cols = pd.to_datetime(["2024-03-31", "2023-03-31", "2022-03-31"])
    return pd.DataFrame(
        {cols[0]: [300.0, np.nan], cols[1]: [200.0, 20.0], cols[2]: [np.nan, 10.0]},
        index=["Revenue", "Debt"],
    )


class _T:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_first_df_picks_first_nonempty_dataframe():
    good = pd.DataFrame({"a": [1]})
    t = _T(a=pd.DataFrame(), b=None, c="str", d=good)
    assert su.first_df(t, "missing", "a", "b", "c", "d") is good
    assert su.first_df(t, "a", "b") is None


def test_row_variants():
    df = _stmt()
    assert su.row(df, "Revenue") == 300.0
    assert su.row(df, "Revenue", col=1) == 200.0
    assert su.row(df, "Nope", "Revenue") == 300.0
    assert su.row(df, "Debt") is None  # NaN at col 0
    assert su.row(df, "Revenue", col=9) is None  # out-of-range swallowed
    assert su.row(None, "x") is None and su.row(pd.DataFrame(), "x") is None


def test_series_drops_nan_and_first_match():
    df = _stmt()
    assert su.series(df, "Revenue") == [300.0, 200.0]
    assert su.series(df, "Nope", "Debt") == [20.0, 10.0]
    assert su.series(df, "Nope") == [] and su.series(None, "x") == []
    assert su.series(pd.DataFrame(), "x") == []


def test_fin_metric_default_only_for_none():
    df = _stmt()
    assert su.fin_metric(df, ("Debt",), default=-1.0) == -1.0
    assert su.fin_metric(df, ("Revenue",), default=-1.0) == 300.0
    assert su.fin_metric(df, ("Revenue",), col=1) == 200.0
    assert su.fin_metric(df, ("Zero",)) is None


def _ohlcv(close, vol=1000.0, n=None):
    n = n or len(close)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": vol}, index=idx)


def test_clean_ohlcv_none_and_empty_and_no_close():
    assert su.clean_ohlcv(None) is None and su.clean_ohlcv(pd.DataFrame()) is None
    assert su.clean_ohlcv(pd.DataFrame({"Open": [1.0]}, index=pd.date_range("2024-01-01", periods=1))) is None


def test_clean_ohlcv_integrity_repair_and_numeric_coercion():
    df = _ohlcv([10, 11, 12])
    df["Open"] = ["14", "bad", "10"]  # bad -> NaN -> filled from Close
    df["High"] = [9.0, 9.0, 9.0]  # below close: must be repaired up
    df["Low"] = [20.0, 20.0, 20.0]  # above close: repaired down
    out = su.clean_ohlcv(df)
    # High = max(O,H,L,C), Low = min(O,H,L,C) per bar
    assert list(out["High"]) == [20.0, 20.0, 20.0]
    assert list(out["Low"]) == [9.0, 9.0, 9.0]
    assert out["Open"].iloc[1] == 11.0


def test_clean_ohlcv_non_positive_and_nan_close_dropped():
    df = _ohlcv([10, 0, -3, np.nan, 11])
    out = su.clean_ohlcv(df)
    assert list(out["Close"]) == [10.0, 11.0]
    assert su.clean_ohlcv(_ohlcv([0, -1])) is None


def test_clean_ohlcv_dedup_keeps_last_and_sorts():
    df = _ohlcv([10, 11, 12])
    df.index = pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-03"])
    out = su.clean_ohlcv(df)
    assert list(out.index) == list(pd.to_datetime(["2024-01-01", "2024-01-03"]))
    assert list(out["Close"]) == [11.0, 12.0]


def test_clean_ohlcv_volume_negative_and_nan():
    df = _ohlcv([10, 11, 12])
    df["Volume"] = [-5.0, np.nan, 7.0]
    assert list(su.clean_ohlcv(df)["Volume"]) == [0.0, 0.0, 7.0]


def test_clean_ohlcv_bad_print_only_when_zero_volume():
    jump = [100, 100, 300, 100, 100]
    zero_vol = _ohlcv(jump, vol=0.0)
    out = su.clean_ohlcv(zero_vol)
    assert list(out["Close"]) == [100.0] * 5  # spike neutralised & forward-filled
    with_vol = _ohlcv(jump, vol=1000.0)
    assert list(su.clean_ohlcv(with_vol)["Close"]) == [float(x) for x in jump]  # real move kept


def test_clean_ohlcv_jump_threshold_boundary():
    vol0 = lambda c: _ohlcv(c, vol=0.0)  # noqa: E731
    # exactly +50% is not > max_daily_move; one point more is (exact binary fractions).
    assert su.clean_ohlcv(vol0([100, 150, 150]), max_daily_move=0.5)["Close"].iloc[1] == 150.0
    assert su.clean_ohlcv(vol0([100, 151, 151]), max_daily_move=0.5)["Close"].iloc[1] == 100.0
    assert su.MAX_DAILY_MOVE == 0.60
    assert su.clean_ohlcv(vol0([100, 130, 130]), max_daily_move=0.2)["Close"].iloc[1] == 100.0


def test_clean_ohlcv_min_bars_and_verbose(capsys):
    df = _ohlcv([10, 0, 12])
    assert su.clean_ohlcv(df, min_bars=3) is None
    assert su.clean_ohlcv(df, min_bars=2) is not None
    assert capsys.readouterr().out == ""
    su.clean_ohlcv(df, ticker="ABC", verbose=True)
    out = capsys.readouterr().out
    assert "clean_ohlcv[ABC]: dropped 1 rows, neutralised 0 bad-print bars -> 2 clean bars" in out
    su.clean_ohlcv(_ohlcv([1, 2, 3]), verbose=True)
    assert capsys.readouterr().out == ""  # nothing dropped -> silent
    su.clean_ohlcv(df, verbose=True)
    assert "clean_ohlcv[?]" in capsys.readouterr().out


def test_clean_ohlcv_idempotent():
    once = su.clean_ohlcv(_ohlcv([10, 11, 12]))
    pd.testing.assert_frame_equal(su.clean_ohlcv(once), once)


def test_clean_ohlcv_keeps_only_ohlcv_columns():
    df = _ohlcv([1, 2]).assign(Extra=5)
    assert list(su.clean_ohlcv(df).columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_clean_financials():
    assert su.clean_financials(None) is None and su.clean_financials(pd.DataFrame()) is None
    cols = pd.to_datetime(["2022-03-31", "2024-03-31", "2023-03-31"])
    df = pd.DataFrame({cols[0]: [1, 2], cols[1]: ["3", "x"], cols[2]: [np.nan, np.nan]}, index=["a", "b"])
    out = su.clean_financials(df)
    assert list(out.columns) == list(pd.to_datetime(["2024-03-31", "2022-03-31"]))  # empty col out, newest first
    assert out.iloc[0, 0] == 3.0 and np.isnan(out.iloc[1, 0])
    assert su.clean_financials(pd.DataFrame({"a": [np.nan]})) is None


def test_clean_financials_unsortable_columns_pass_through():
    df = pd.DataFrame({"x": [1.0], 2: [2.0]})  # str vs int columns cannot be sorted
    out = su.clean_financials(df)
    assert list(out.columns) == ["x", 2]


def _bulk_raw(tickers, n=5, bad=()):
    idx = pd.date_range("2024-01-01", periods=n)
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], tickers])
    data = {}
    for f in ["Open", "High", "Low", "Close"]:
        for t in tickers:
            data[(f, t)] = np.nan if t in bad else np.linspace(10, 12, n)
    for t in tickers:
        data[("Volume", t)] = 1000.0 if t not in bad else np.nan
    return pd.DataFrame(data, index=idx, columns=cols)


def test_extract_ticker_df_multi_flat_missing_and_unclean():
    raw = _bulk_raw(["AAA", "BBB"], bad=("BBB",))
    assert len(su.extract_ticker_df(raw, "AAA")) == 5
    assert su.extract_ticker_df(raw, "BBB") is None  # all NaN
    assert su.extract_ticker_df(raw, "ZZZ") is None  # KeyError swallowed
    flat = _ohlcv([1, 2, 3])
    assert len(su.extract_ticker_df(flat, "X")) == 3
    assert su.extract_ticker_df(flat, "X", min_bars=4, clean=False) is None
    assert su.extract_ticker_df(flat, "X", min_bars=3, clean=False) is not None
    dirty = _ohlcv([1, 0, 3])
    assert len(su.extract_ticker_df(dirty, "X", clean=False)) == 3  # no cleaning applied
    assert su.extract_ticker_df(dirty.iloc[0:0], "X") is None
    assert list(su.extract_ticker_df(flat.assign(Z=1), "X", clean=False).columns)[-1] == "Volume"
    only_x = pd.DataFrame({"Z": [1.0]}, index=pd.date_range("2024-01-01", periods=1))
    assert su.extract_ticker_df(only_x, "X", clean=False).equals(only_x)  # no OHLCV cols -> unfiltered


@pytest.mark.parametrize(
    "msg,expected",
    [("Rate limited", True), ("HTTP 429", True), ("Too Many Requests", True), ("Invalid Crumb", True), ("boom", False)],
)
def test_is_rate_limited(msg, expected):
    assert su.is_rate_limited(RuntimeError(msg)) is expected


def test_strip_suffix():
    assert su._strip_suffix("RELIANCE.NS") == "RELIANCE"
    assert su._strip_suffix("X.BO") == "X"
    assert su._strip_suffix("AAPL") == "AAPL"


class _YF:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def download(self, batch, **kw):
        self.calls.append((list(batch), kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def yf_env(monkeypatch):
    sleeps = []
    monkeypatch.setattr(su, "_YF_OK", True)
    monkeypatch.setattr(su.time, "sleep", sleeps.append)

    def install(responses):
        y = _YF(responses)
        monkeypatch.setattr(su, "yf", y, raising=False)
        return y

    return install, sleeps


def test_bulk_download_unavailable(monkeypatch):
    monkeypatch.setattr(su, "_YF_OK", False)
    assert su.bulk_download(["A"]) == {}


def test_bulk_download_batches_period_start_and_suffix(yf_env, capsys):
    install, sleeps = yf_env
    y = install([_bulk_raw(["A.NS", "B.NS"]), _bulk_raw(["C"])])
    out = su.bulk_download(["A.NS", "B.NS", "C"], batch_size=2, sleep_between=0.7, min_bars=5)
    assert set(out) == {"A", "B", "C"}
    assert [c[0] for c in y.calls] == [["A.NS", "B.NS"], ["C"]]
    assert y.calls[0][1] == {"auto_adjust": True, "threads": True, "progress": False, "period": "1y"}
    assert sleeps == [0.7]  # between batches only, not after the last
    txt = capsys.readouterr().out
    assert "3 tickers, 2 batches (size=2, period=1y)" in txt and "OK (2/2)" in txt
    assert "bulk_download complete: 3/3 loaded" in txt
    y2 = install([_bulk_raw(["A"])])
    su.bulk_download(["A"], start="2024-01-01", period="5y", verbose=False)
    assert y2.calls[0][1].get("start") == "2024-01-01" and "period" not in y2.calls[0][1]


def test_bulk_download_min_bars_filters(yf_env):
    install, _ = yf_env
    install([_bulk_raw(["A"], n=5)])
    assert su.bulk_download(["A"], min_bars=6, verbose=False) == {}
    install([_bulk_raw(["A"], n=5)])
    assert "A" in su.bulk_download(["A"], min_bars=5, verbose=False)


def test_bulk_download_empty_none_response(yf_env, capsys):
    install, sleeps = yf_env
    install([pd.DataFrame(), None])
    assert su.bulk_download(["A", "B"], batch_size=1) == {}
    assert "empty" in capsys.readouterr().out and sleeps == [1.5]


def test_bulk_download_rate_limit_retries_with_backoff(yf_env, capsys):
    install, sleeps = yf_env
    install([RuntimeError("429 Too Many"), RuntimeError("Rate limit"), _bulk_raw(["A"])])
    out = su.bulk_download(["A"], max_retries=3, min_bars=1)
    assert "A" in out and sleeps == [20, 40]
    assert "rate-limited, wait 20s" in capsys.readouterr().out


def test_bulk_download_gives_up_on_last_attempt_and_other_errors(yf_env, capsys):
    install, sleeps = yf_env
    y = install([RuntimeError("Rate"), RuntimeError("Rate")])
    assert su.bulk_download(["A"], max_retries=2) == {}
    assert len(y.calls) == 2 and sleeps == [20]  # no sleep after the final attempt
    assert "ERROR: Rate" in capsys.readouterr().out
    y = install([ValueError("bad payload")])
    assert su.bulk_download(["A"], verbose=False) == {}
    assert len(y.calls) == 1  # non-rate-limit errors do not retry


def test_parallel_map_filters_none_and_swallows_errors(capsys):
    def fn(x):
        if x == 3:
            raise RuntimeError("bad")
        return None if x % 2 == 0 else x * 10

    out = su.parallel_map(fn, [1, 2, 3, 4, 5], workers=2, progress_every=2, label="nums")
    assert sorted(out) == [10, 50]
    txt = capsys.readouterr().out
    assert "2/5 nums processed" in txt and "5/5 nums processed, 2 results" in txt
    assert "1/5" not in txt
    assert su.parallel_map(lambda x: x, [1], verbose=False) == [1]
    assert capsys.readouterr().out == ""
    assert su.parallel_map(lambda x: x, [], verbose=False) == []


def test_math_helpers():
    assert su.pct_change(110, 100) == pytest.approx(10.0)
    assert su.pct_change(90, -100) == pytest.approx(190.0)  # abs(old) denominator
    assert su.pct_change(5, 0) is None and su.pct_change(None, 5) is None and su.pct_change(5, None) is None
    assert su.pct_change(0, 5) == -100.0  # zero new is a valid value
    assert su.cagr(200, 100, 1) == pytest.approx(100.0)
    assert su.cagr(400, 100, 2) == pytest.approx(100.0)
    assert su.cagr(100, 0, 3) is None and su.cagr(0, 100, 3) is None and su.cagr(100, -5, 3) is None
    assert su.cagr(200, 100, 0) is None
    assert su.normalise_debt_to_equity(45.2) == pytest.approx(0.452)
    assert su.normalise_debt_to_equity(10) == 10  # boundary: >10 only
    assert su.normalise_debt_to_equity(10.5) == pytest.approx(0.105)
    assert su.normalise_debt_to_equity(None) is None and su.normalise_debt_to_equity(0) == 0
    assert su.market_cap_crores(2e9) == 200.0 and su.market_cap_crores(None) == 0
