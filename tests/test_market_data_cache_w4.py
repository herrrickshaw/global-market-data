"""market_data_cache.MarketCache: parquet OHLC/index/fundamentals cache with a faked yfinance."""

import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


class FakeYF:
    def __init__(self):
        self.download_calls = []
        self.responses = []
        self.tickers = {}

    def download(self, tickers, **kw):
        self.download_calls.append((tickers, kw))
        r = self.responses.pop(0) if self.responses else pd.DataFrame()
        if isinstance(r, Exception):
            raise r
        return r

    def Ticker(self, sym):  # noqa: N802
        t = self.tickers.get(sym)
        if isinstance(t, Exception):
            raise t
        return t


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    yf = FakeYF()
    fake_module(monkeypatch, "yfinance", download=yf.download, Ticker=yf.Ticker)
    mdc = fresh_import(monkeypatch, "market_data_cache")
    monkeypatch.setattr(mdc, "_CACHE_INSTANCE", None)
    sleeps = []
    monkeypatch.setattr(mdc.time, "sleep", sleeps.append)
    mdc._yf = yf
    mdc._sleeps = sleeps
    return mdc


def _ohlc(start, n, base=100.0):
    idx = pd.date_range(start, periods=n, freq="D")
    v = base + np.arange(n, dtype=float)
    return pd.DataFrame(
        {"Open": v, "High": v * 1.01, "Low": v * 0.99, "Close": v, "Volume": 1000.0}, index=idx
    )


def _recent(n, end_days_ago=1, base=100.0):
    end = pd.Timestamp(date.today() - timedelta(days=end_days_ago))
    return _ohlc(end - pd.Timedelta(days=n - 1), n, base)


def _multi(frames_by_ticker):
    parts = {}
    for t, df in frames_by_ticker.items():
        for c in df.columns:
            parts[(c, t)] = df[c]
    return pd.DataFrame(parts)


def test_import_uses_home_cache_dirs(env, tmp_path):
    assert env.CACHE_ROOT == tmp_path / "Downloads" / "market_cache"
    for d in (env.OHLC_DIR, env.FUND_DIR, env.INDEX_DIR, env.META_DIR):
        assert d.is_dir()


def test_meta_roundtrip_and_corrupt(env):
    c = env.MarketCache(verbose=False)
    assert c._meta == {}
    c._update_meta("ohlc:X", env.OHLC_DIR / "X.parquet", _ohlc("2024-01-01", 3))
    assert c._meta["ohlc:X"]["rows"] == 3 and c._meta["ohlc:X"]["from"] == "2024-01-01"
    assert c._meta["ohlc:X"]["to"] == "2024-01-03"
    assert env.MarketCache(verbose=False)._meta["ohlc:X"]["rows"] == 3  # persisted
    c._update_meta("ohlc:E", env.OHLC_DIR / "E.parquet", pd.DataFrame(index=pd.DatetimeIndex([])))
    assert c._meta["ohlc:E"]["from"] is None
    env.META_FILE.write_text("{broken")
    assert env.MarketCache(verbose=False)._meta == {}


def test_is_stale_boundaries(env):
    c = env.MarketCache(verbose=False)
    assert c._is_stale("nope", 20)
    c._meta["k"] = {"updated": (datetime.now() - timedelta(hours=19)).isoformat()}
    assert not c._is_stale("k", 20)
    c._meta["k"] = {"updated": (datetime.now() - timedelta(hours=21)).isoformat()}
    assert c._is_stale("k", 20)
    c._meta["k"] = {"updated": "garbage"}
    assert c._is_stale("k", 20)
    c._meta["k"] = {}
    assert c._is_stale("k", 20)


def test_last_cached_date(env):
    c = env.MarketCache(verbose=False)
    assert c._last_cached_date("k") is None
    c._meta["k"] = {"to": "2024-05-06"}
    assert c._last_cached_date("k") == date(2024, 5, 6)
    c._meta["k"] = {"to": "junk"}
    assert c._last_cached_date("k") is None
    c._meta["k"] = {"to": None}
    assert c._last_cached_date("k") is None


def test_ohlc_path_sanitises(env):
    c = env.MarketCache(verbose=False)
    assert c._ohlc_path("A/B:C").name == "A_B_C.parquet"


def test_get_ohlc_cold_start_downloads_5y_and_caches(env, capsys):
    df = _recent(40)
    env._yf.responses.append(df)
    c = env.MarketCache(verbose=True)
    out = c.get_ohlc("ABC.NS")
    assert len(out) == 40 and c._ohlc_path("ABC.NS").exists()
    tickers, kw = env._yf.download_calls[0]
    assert tickers == "ABC.NS"
    assert kw["start"] == date.today() - timedelta(days=env.OHLC_YEARS * 365 + 30)
    assert kw["auto_adjust"] is True and kw["progress"] is False
    assert "Cache created: ABC.NS 40 rows" in capsys.readouterr().out
    assert c._meta["ohlc:ABC.NS"]["rows"] == 40


def test_get_ohlc_cold_start_empty_not_cached(env):
    c = env.MarketCache(verbose=False)
    assert c.get_ohlc("ABC").empty
    assert not c._ohlc_path("ABC").exists()


def test_get_ohlc_fresh_reads_disk_once_then_memory(env, monkeypatch):
    c = env.MarketCache(verbose=False)
    env._yf.responses.append(_recent(10))
    c.get_ohlc("ABC")
    c._mem.clear()
    reads = []
    real = pd.read_parquet
    monkeypatch.setattr(
        env.pd, "read_parquet", lambda p, *a, **k: reads.append(p) or real(p, *a, **k)
    )
    a = c.get_ohlc("ABC")
    b = c.get_ohlc("ABC")
    assert len(reads) == 1 and a is b
    assert len(env._yf.download_calls) == 1


def test_get_ohlc_stale_appends_incremental(env, capsys):
    c = env.MarketCache(verbose=True)
    old = _recent(10, end_days_ago=5)
    env._yf.responses.append(old)
    c.get_ohlc("ABC")
    c._meta["ohlc:ABC"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    last = date.today() - timedelta(days=5)
    new = _recent(3, end_days_ago=1, base=500.0)  # overlaps nothing, newer
    env._yf.responses.append(new)
    out = c.get_ohlc("ABC")
    _, kw = env._yf.download_calls[-1]
    assert kw["start"] == last + timedelta(days=1)
    assert len(out) == 13 and out.index.is_monotonic_increasing
    assert "Cache updated: ABC +3 rows (total 13)" in capsys.readouterr().out


def test_get_ohlc_stale_but_up_to_date_skips_download(env):
    c = env.MarketCache(verbose=False)
    today_df = _recent(5, end_days_ago=0)
    env._yf.responses.append(today_df)
    c.get_ohlc("ABC")
    c._meta["ohlc:ABC"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    out = c.get_ohlc("ABC")  # start = tomorrow >= today -> return existing
    assert len(out) == 5 and len(env._yf.download_calls) == 1


def test_get_ohlc_stale_boundary_start_equals_today(env):
    c = env.MarketCache(verbose=False)
    env._yf.responses.append(_recent(5, end_days_ago=1))
    c.get_ohlc("ABC")
    c._meta["ohlc:ABC"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    out = c.get_ohlc("ABC")  # last = yesterday, start = today -> start >= today: no download
    assert len(out) == 5 and len(env._yf.download_calls) == 1


def test_get_ohlc_stale_empty_increment_returns_existing(env):
    c = env.MarketCache(verbose=False)
    env._yf.responses.append(_recent(5, end_days_ago=4))
    c.get_ohlc("ABC")
    c._meta["ohlc:ABC"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    out = c.get_ohlc("ABC")  # fake returns empty frame
    assert len(out) == 5 and len(env._yf.download_calls) == 1 + 3  # empty frames are retried 3x


def test_get_ohlc_force_redownloads_everything(env):
    c = env.MarketCache(verbose=False)
    env._yf.responses.append(_recent(5))
    c.get_ohlc("ABC")
    env._yf.responses.append(_recent(8))
    out = c.get_ohlc("ABC", force=True)
    assert len(out) == 8
    _, kw = env._yf.download_calls[-1]
    assert kw["start"] == date.today() - timedelta(days=env.OHLC_YEARS * 365 + 30)


def test_yf_download_single_variants(env, monkeypatch):
    c = env.MarketCache(verbose=False)
    df = _recent(5)
    multi = _multi({"X": df})
    env._yf.responses.append(multi)
    assert list(c._yf_download_single("X", date.today()).columns) == [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]
    withnan = df.copy()
    withnan.iloc[2] = np.nan
    env._yf.responses.append(withnan)
    assert len(c._yf_download_single("X", date.today())) == 4  # all-NaN row dropped


def test_yf_download_single_retries_rate_limits_then_gives_up(env):
    c = env.MarketCache(verbose=False)
    env._yf.responses.extend(
        [RuntimeError("429"), RuntimeError("Rate limited"), RuntimeError("Rate x")]
    )
    assert c._yf_download_single("X", date.today()).empty
    assert env._sleeps == [15, 30, 45] and len(env._yf.download_calls) == 3


def test_yf_download_single_other_error_stops_and_empty_retries(env):
    c = env.MarketCache(verbose=False)
    env._yf.responses.append(ValueError("bad"))
    assert c._yf_download_single("X", date.today()).empty
    assert len(env._yf.download_calls) == 1 and env._sleeps == []
    env._yf.download_calls.clear()
    assert c._yf_download_single("X", date.today()).empty  # empty frame: tries 3 times
    assert len(env._yf.download_calls) == 3


def test_yf_unavailable(env, monkeypatch):
    monkeypatch.setattr(env, "_YF_OK", False)
    c = env.MarketCache(verbose=False)
    assert c._yf_download_single("X", date.today()).empty
    assert c._bulk_yf_download(["X"], date.today()) == {}
    assert c.get_financials("X") == {}


def test_bulk_yf_download_multiindex_flat_and_missing(env, capsys):
    c = env.MarketCache(verbose=False)
    a, b = _recent(5), _recent(5, base=50)
    env._yf.responses.append(_multi({"A": a, "B": b}))
    out = c._bulk_yf_download(["A", "B", "C"], date.today())
    assert set(out) == {"A", "B"}
    assert "OK (2/3)" in capsys.readouterr().out
    env._yf.responses.append(a)  # flat frame: assigned to first ticker of the batch
    out = c._bulk_yf_download(["Z"], date.today())
    assert set(out) == {"Z"}
    env._yf.responses.append(pd.DataFrame())
    assert c._bulk_yf_download(["Q"], date.today()) == {}
    assert "empty" in capsys.readouterr().out


def test_bulk_yf_download_batches_and_sleep(env, monkeypatch):
    monkeypatch.setattr(env, "BATCH_SIZE", 2)
    c = env.MarketCache(verbose=False)
    env._yf.responses.extend(
        [_multi({"A": _recent(3), "B": _recent(3)}), _multi({"C": _recent(3)})]
    )
    out = c._bulk_yf_download(["A", "B", "C"], date.today())
    assert set(out) == {"A", "B", "C"}
    assert [len(call[0]) for call in env._yf.download_calls] == [2, 1]
    assert env._sleeps == [env.BATCH_SLEEP]  # between batches only


def test_bulk_yf_download_rate_limit_and_error(env, capsys):
    c = env.MarketCache(verbose=False)
    env._yf.responses.extend([RuntimeError("Too Many Requests"), _multi({"A": _recent(3)})])
    out = c._bulk_yf_download(["A"], date.today())
    assert "A" in out and env._sleeps == [30]
    env._yf.responses.append(RuntimeError("kaboom"))
    assert c._bulk_yf_download(["B"], date.today()) == {}
    assert "ERROR: kaboom" in capsys.readouterr().out


def test_get_ohlc_bulk_classifies_and_loads(env, capsys):
    c = env.MarketCache(verbose=True)
    # FRESH: cached, up to date. STALE: cached but old meta. NEW: not on disk.
    for t in ("FRESH", "STALE"):
        df = _recent(6, end_days_ago=3)
        df.to_parquet(c._ohlc_path(t))
        c._update_meta(f"ohlc:{t}", c._ohlc_path(t), df)
    c._meta["ohlc:STALE"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    inc = _recent(2, end_days_ago=1, base=900.0)
    full = _recent(7, end_days_ago=1, base=10.0)
    env._yf.responses.extend([_multi({"NEW": full}), _multi({"STALE": inc})])
    res = c.get_ohlc_bulk(["FRESH", "STALE", "NEW"])
    assert set(res) == {"FRESH", "STALE", "NEW"}
    assert len(res["FRESH"]) == 6 and len(res["NEW"]) == 7 and len(res["STALE"]) == 8
    out = capsys.readouterr().out
    assert "1 fresh | 1 incremental | 1 cold-start" in out
    assert "Cache result: 3/3 tickers loaded" in out
    starts = [call[1]["start"] for call in env._yf.download_calls]
    assert starts == [
        date.today() - timedelta(days=env.OHLC_YEARS * 365 + 30),  # cold start window
        date.today() - timedelta(days=5),  # incremental window
    ]


def test_get_ohlc_bulk_force_marks_cached_as_incremental(env):
    c = env.MarketCache(verbose=False)
    df = _recent(6, end_days_ago=3)
    df.to_parquet(c._ohlc_path("A"))
    c._update_meta("ohlc:A", c._ohlc_path("A"), df)
    env._yf.responses.append(_multi({"A": _recent(1, end_days_ago=0, base=300.0)}))
    res = c.get_ohlc_bulk(["A"], force=True)
    assert len(res["A"]) == 7 and len(env._yf.download_calls) == 1


def test_get_ohlc_bulk_incremental_without_new_rows_keeps_existing(env):
    c = env.MarketCache(verbose=False)
    df = _recent(6, end_days_ago=3)
    df.to_parquet(c._ohlc_path("A"))
    c._update_meta("ohlc:A", c._ohlc_path("A"), df)
    c._meta["ohlc:A"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    env._yf.responses.append(pd.DataFrame())
    assert len(c.get_ohlc_bulk(["A"])["A"]) == 6


def test_get_ohlc_bulk_unreadable_fresh_falls_back_to_download(env):
    c = env.MarketCache(verbose=False)
    df = _recent(6, end_days_ago=3)
    df.to_parquet(c._ohlc_path("A"))
    c._update_meta("ohlc:A", c._ohlc_path("A"), df)
    c._ohlc_path("A").write_text("corrupt")
    env._yf.responses.append(_multi({"A": _recent(5, end_days_ago=1)}))
    res = c.get_ohlc_bulk(["A"])
    assert len(res["A"]) == 5


def test_get_ohlc_bulk_fresh_uses_memory(env):
    c = env.MarketCache(verbose=False)
    df = _recent(6, end_days_ago=3)
    df.to_parquet(c._ohlc_path("A"))
    c._update_meta("ohlc:A", c._ohlc_path("A"), df)
    c._mem["A"] = df.iloc[:2]  # in-memory copy wins over disk
    assert len(c.get_ohlc_bulk(["A"])["A"]) == 2


def test_get_ohlc_bulk_incremental_error_falls_back_to_download(env, monkeypatch):
    c = env.MarketCache(verbose=False)
    df = _recent(6, end_days_ago=3)
    df.to_parquet(c._ohlc_path("A"))
    c._update_meta("ohlc:A", c._ohlc_path("A"), df)
    c._meta["ohlc:A"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    inc = _recent(2, end_days_ago=1)
    env._yf.responses.append(_multi({"A": inc}))
    monkeypatch.setattr(env.MarketCache, "_write_parquet", staticmethod(lambda p, d: 1 / 0))
    res = c.get_ohlc_bulk(["A"])
    assert len(res["A"]) == 2  # download result used when the merge/write fails


def test_get_financials_annual_fetch_cache_and_force(env):
    cols = pd.to_datetime(["2024-03-31", "2023-03-31"])
    inc = pd.DataFrame([[1.0, 2.0]], index=["Revenue"], columns=cols)
    bal = pd.DataFrame([[3.0, 4.0]], index=["Debt"], columns=cols)

    class T:
        income_stmt = inc
        balance_sheet = bal
        cash_flow = pd.DataFrame()
        financials = None

    env._yf.tickers["ABC"] = T()
    c = env.MarketCache(verbose=False)
    got = c.get_financials("ABC")
    assert set(got) == {"income_stmt", "balance_sheet"}  # empty cash_flow dropped
    env._yf.tickers["ABC"] = RuntimeError("must not refetch")
    again = c.get_financials("ABC")  # served from parquet
    assert set(again) == {"income_stmt", "balance_sheet"}
    assert again["income_stmt"].loc["Revenue"].tolist() == [1.0, 2.0]
    assert isinstance(again["income_stmt"].columns, pd.DatetimeIndex)
    assert c.get_financials("ABC", force=True) == {}  # forced refetch hits the error -> {}


def test_get_financials_quarterly_and_failure_message(env, capsys):
    cols = pd.to_datetime(["2024-03-31"])
    q = pd.DataFrame([[5.0]], index=["Revenue"], columns=cols)

    class T:
        quarterly_income_stmt = None
        quarterly_financials = q

    env._yf.tickers["Q"] = T()
    c = env.MarketCache(verbose=True)
    got = c.get_financials("Q", freq="quarterly")
    assert list(got) == ["quarterly_income"]
    assert c._meta["fund:Q:quarterly"]["sheets"] == ["quarterly_income"]
    env._yf.tickers["E"] = ValueError("nope")
    assert c.get_financials("E") == {}
    assert "Financial fetch failed for E: nope" in capsys.readouterr().out


def test_get_financials_no_frames_writes_nothing(env):
    class T:
        pass

    env._yf.tickers["N"] = T()
    c = env.MarketCache(verbose=False)
    assert c.get_financials("N") == {}
    assert "fund:N:annual" not in c._meta


def test_get_financials_stale_cache_refetches(env):
    cols = pd.to_datetime(["2024-03-31"])

    class T:
        income_stmt = pd.DataFrame([[1.0]], index=["Revenue"], columns=cols)

    env._yf.tickers["S"] = T()
    c = env.MarketCache(verbose=False)
    c.get_financials("S")
    c._meta["fund:S:annual"]["updated"] = (
        datetime.now() - timedelta(days=env.FUND_STALE_DAYS, hours=1)
    ).isoformat()
    env._yf.tickers["S"] = RuntimeError("refetch attempted")
    assert c.get_financials("S") == {}
    c._meta["fund:S:annual"]["updated"] = (
        datetime.now() - timedelta(days=env.FUND_STALE_DAYS - 1)
    ).isoformat()
    assert "income_stmt" in c.get_financials("S")  # still fresh -> from parquet


def test_get_index_computes_indicators_and_caches(env, capsys):
    n = 260
    df = _ohlc("2023-01-01", n)
    env._yf.responses.append(df)
    c = env.MarketCache(verbose=True)
    out = c.get_index("^NSEI")
    assert {"dma50", "dma200", "dma200_sl", "vol20"} <= set(out.columns)
    assert out["dma50"].iloc[49] == pytest.approx(df["Close"].iloc[:50].mean())
    assert np.isnan(out["dma50"].iloc[48]) and np.isnan(out["dma200"].iloc[198])
    assert out["dma200_sl"].iloc[204] == pytest.approx(
        out["dma200"].iloc[204] - out["dma200"].iloc[199]
    )
    rets = df["Close"].pct_change()
    assert out["vol20"].iloc[40] == pytest.approx(rets.iloc[21:41].std() * np.sqrt(252) * 100)
    assert (env.INDEX_DIR / "NSEI.parquet").exists()
    assert env._yf.download_calls[0][1]["start"] == date.today() - timedelta(
        days=env.INDEX_YEARS * 365 + 30
    )
    assert "Index cached: ^NSEI 260 bars" in capsys.readouterr().out
    again = c.get_index("^NSEI")  # fresh: from disk, no download
    assert len(again) == 260 and len(env._yf.download_calls) == 1
    env._yf.responses.append(_ohlc("2023-01-01", 100))
    assert len(c.get_index("^NSEI", force=True)) == 100


def test_get_index_empty(env):
    c = env.MarketCache(verbose=False)
    assert c.get_index("^GSPC").empty and not (env.INDEX_DIR / "GSPC.parquet").exists()


def test_report_empty_and_populated(env, capsys):
    c = env.MarketCache(verbose=False)
    assert c.report().empty
    assert "Cache is empty" in capsys.readouterr().out
    env._yf.responses.append(_recent(5))
    c.get_ohlc("ABC")
    c._meta["fund:ABC:annual"] = {"updated": (datetime.now() - timedelta(days=3)).isoformat()}
    c._meta["index:^X"] = {"updated": (datetime.now() - timedelta(hours=5)).isoformat()}
    c._meta["ohlc:OLD"] = {
        "updated": (datetime.now() - timedelta(minutes=10)).isoformat(),
        "rows": 1,
    }
    c._meta["bad"] = {"updated": "junk"}
    c._meta["none"] = {}
    rep = c.report().set_index("Key")
    assert rep.loc["fund:ABC:annual", "Age"] == "3d"
    assert rep.loc["index:^X", "Age"] == "5h"
    assert rep.loc["ohlc:OLD", "Age"] == "10m"
    assert rep.loc["bad", "Age"] == "—" and rep.loc["none", "Rows"] == "—"
    assert rep.loc["ohlc:ABC", "Rows"] == 5
    out = capsys.readouterr().out
    assert "2 OHLC | 1 fundamental | 1 index" in out


def test_report_truncates_long_keys(env):
    c = env.MarketCache(verbose=False)
    c._meta["ohlc:" + "X" * 80] = {"rows": 1}
    assert len(c.report()["Key"].iloc[0]) == 50


def test_purge_single_and_all(env, capsys):
    c = env.MarketCache(verbose=False)
    for t in ("AAA", "BBB"):
        env._yf.responses.append(_recent(5))
        c.get_ohlc(t)
    (env.FUND_DIR / "AAA_annual.parquet").write_bytes(b"x")
    (env.FUND_DIR / "AAA_quarterly.parquet").write_bytes(b"x")
    c._meta["fund:AAA:annual"] = {}
    c.purge("AAA")
    assert not c._ohlc_path("AAA").exists() and c._ohlc_path("BBB").exists()
    assert not (env.FUND_DIR / "AAA_annual.parquet").exists()
    assert not (env.FUND_DIR / "AAA_quarterly.parquet").exists()
    assert set(c._meta) == {"ohlc:BBB"}
    assert json.loads(env.META_FILE.read_text()).keys() == {"ohlc:BBB"}
    c.purge()
    assert c._meta == {} and not list(env.OHLC_DIR.glob("*.parquet"))
    assert "Cache wiped" in capsys.readouterr().out


def test_clean_ohlc_variants(env):
    df = _ohlc("2024-01-01", 5)
    df.index = df.index.astype(str)  # string index gets coerced
    df.loc["notadate"] = df.iloc[0]
    out = env.MarketCache._clean_ohlc(df)
    assert isinstance(out.index, pd.DatetimeIndex) and len(out) == 5
    empty = pd.DataFrame()
    assert env.MarketCache._clean_ohlc(empty) is empty
    bad = _ohlc("2024-01-01", 3)
    bad["Close"] = -1.0
    assert env.MarketCache._clean_ohlc(bad).empty  # nothing usable survives


def test_clean_ohlc_fallback_without_central_gate(env, monkeypatch):
    monkeypatch.setattr(env, "_clean_ohlcv_central", None)
    df = _ohlc("2024-01-01", 4)
    df.iloc[1] = np.nan
    dup = pd.concat([df, df.iloc[[0]].assign(Close=999.0)])
    out = env.MarketCache._clean_ohlc(dup)
    assert len(out) == 4 and out["Close"].iloc[0] == 999.0  # dup: keep last
    assert not out.isna().any().any()  # ffill/bfill


def test_write_parquet_atomic_and_cleans_tmp_on_failure(env, tmp_path):
    p = tmp_path / "x.parquet"
    env.MarketCache._write_parquet(p, _ohlc("2024-01-01", 3))
    assert p.exists() and not p.with_suffix(".tmp").exists()
    bad = pd.DataFrame({"a": [object()]})
    with pytest.raises(Exception):
        env.MarketCache._write_parquet(tmp_path / "y.parquet", bad)
    assert not (tmp_path / "y.tmp").exists()


def test_first_df_and_get_cache_singleton(env):
    good = pd.DataFrame({"a": [1]})

    class T:
        a = pd.DataFrame()
        b = good

    assert env._first_df(T, "a", "b", "c") is good
    assert env._first_df(T, "a") is None
    c1 = env.get_cache()
    assert c1 is env.get_cache() and c1.verbose is True


def test_warm_cache_runs_indexes_then_bulk_then_report(env, monkeypatch, capsys):
    calls = []

    class C:
        def get_index(self, s):
            calls.append(("idx", s))

        def get_ohlc_bulk(self, syms):
            calls.append(("bulk", syms))

        def report(self):
            calls.append(("report",))

    monkeypatch.setattr(env, "get_cache", lambda: C())
    env.warm_cache(["A.NS", "B.NS"])
    assert calls == [("idx", "^NSEI"), ("idx", "^GSPC"), ("bulk", ["A.NS", "B.NS"]), ("report",)]
    out = capsys.readouterr().out
    assert "Warming cache: 2 stocks, 5-year window" in out and "Cache warm" in out
    calls.clear()
    env.warm_cache(["A"], index_tickers=["^X"])
    assert calls[0] == ("idx", "^X") and len([c for c in calls if c[0] == "idx"]) == 1
