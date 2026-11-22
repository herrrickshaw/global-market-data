"""bhavcopy_history: NSE/BSE bhavcopy assembly, negative cache, cleaned fast path (all I/O faked)."""

import datetime as dt
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import

TODAY = dt.date(2026, 11, 18)  # a Wednesday: pins the look-back window regardless of the real date


class _FixedDate(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 18)


def _weekdays(n):
    out = []
    d = TODAY
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out


def _window(n_days):
    """Weekdays inside fetch_history's look-back window (today and the n_days-1 days before it)."""
    return [d for d in (TODAY - dt.timedelta(days=i) for i in range(n_days)) if d.weekday() < 5]


@pytest.fixture
def bh(monkeypatch, tmp_path):
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path / "cache"))
    fake_module(monkeypatch, "market_calendar", trading_days=lambda cal: [d for d in cal if d.weekday() < 5])
    built = []
    fake_module(monkeypatch, "bhavcopy_store", build=lambda hist, verbose: built.append(sorted(hist)))
    m = fresh_import(monkeypatch, "bhavcopy_history")
    monkeypatch.setattr(m._dt, "date", _FixedDate)
    m.built = built
    return m


def _nse_csv(rows):
    return "SctySrs,TckrSymb,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n" + "\n".join(
        f"{s},{t},{c},{c},{c},{c},{v}" for s, t, c, v in rows
    )


def _bse_csv(rows):
    return "FinInstrmTp,TckrSymb,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n" + "\n".join(
        f"{tp},{t},{c},{c},{c},{c},{v}" for tp, t, c, v in rows
    )


class FakeNSE:
    """days maps date -> csv text; other days raise (holiday)."""

    days = {}
    calls = []
    exited = False

    def __init__(self, download_folder):
        self.dir = Path(download_folder)

    def equityBhavcopy(self, date):  # noqa: N802
        FakeNSE.calls.append(date.date())
        txt = FakeNSE.days.get(date.date())
        if txt is None:
            raise RuntimeError("no file")
        p = self.dir / f"raw_{date:%Y%m%d}.csv"
        p.write_text(txt)
        return p

    def exit(self):
        FakeNSE.exited = True
        if FakeNSE.exit_raises:
            raise RuntimeError

    exit_raises = False


class FakeSession:
    days = {}
    calls = []

    def __init__(self):
        self.headers = {}

    def get(self, url, timeout):
        FakeSession.calls.append(url)
        for d, txt in FakeSession.days.items():
            if f"{d:%Y%m%d}" in url:
                return type("R", (), {"status_code": 200, "content": txt.encode()})()
        if "ERR" in FakeSession.days:
            raise RuntimeError("net")
        return type("R", (), {"status_code": 404, "content": b""})()


@pytest.fixture
def feeds(monkeypatch, bh):
    FakeNSE.days, FakeNSE.calls, FakeNSE.exited, FakeNSE.exit_raises = {}, [], False, False
    FakeSession.days, FakeSession.calls = {}, []
    fake_module(monkeypatch, "nse", NSE=FakeNSE)
    monkeypatch.setattr(bh.requests, "Session", FakeSession)
    return FakeNSE, FakeSession


def test_nse_day_cache_fetch_filter_and_failure(bh, tmp_path):
    day = dt.date(2024, 3, 1)
    cached = bh.NSE_DIR / "20240301.csv"
    cached.write_text("a,b\n1,2\n")
    assert bh._nse_day(day, object()).to_dict("records") == [{"a": 1, "b": 2}]  # cache hit: client untouched
    cached.unlink()

    class Client:
        def equityBhavcopy(self, date):
            assert date == dt.datetime(2024, 3, 1)
            p = tmp_path / "raw.csv"
            p.write_text(_nse_csv([(" EQ", "AAA", 10, 100), ("BE", "BBB", 5, 50), ("EQ", "CCC", 7, 70)]).replace(" EQ", "EQ"))
            return p

    df = bh._nse_day(day, Client())
    assert list(df["TckrSymb"]) == ["AAA", "CCC"]  # only EQ series kept
    assert list(pd.read_csv(cached)["TckrSymb"]) == ["AAA", "CCC"]  # filtered frame persisted
    assert not (tmp_path / "raw.csv").exists()  # raw download removed

    class Boom:
        def equityBhavcopy(self, date):
            raise RuntimeError

    assert bh._nse_day(dt.date(2024, 3, 2), Boom()) is None


def test_nse_day_strips_header_whitespace(bh, tmp_path):
    class Client:
        def equityBhavcopy(self, date):
            p = tmp_path / "r.csv"
            p.write_text("SctySrs , TckrSymb\nEQ,AAA\n")
            return p

    df = bh._nse_day(dt.date(2024, 3, 4), Client())
    assert "TckrSymb" in df.columns and len(df) == 1


class _Sess:
    def __init__(self, status=200, content=b"", boom=False):
        self.status, self.content, self.boom, self.urls = status, content, boom, []

    def get(self, url, timeout):
        self.urls.append((url, timeout))
        if self.boom:
            raise RuntimeError
        return type("R", (), {"status_code": self.status, "content": self.content})()


def test_bse_day_variants(bh):
    day = dt.date(2024, 3, 1)
    good = _bse_csv([("STK", "AAA", 10, 100), ("MF", "FUND", 1, 1)]).encode().ljust(1001, b" ")
    s = _Sess(content=good)
    df = bh._bse_day(day, s)
    assert list(df["TckrSymb"]) == ["AAA"] and s.urls[0][0].endswith("BhavCopy_BSE_CM_0_0_0_20240301_F_0000.CSV") and s.urls[0][1] == 25
    cached = _Sess(boom=True)
    assert len(bh._bse_day(day, cached)) == 1 and cached.urls == []  # served from csv cache
    assert bh._bse_day(dt.date(2024, 3, 2), _Sess(status=404, content=good)) is None
    assert bh._bse_day(dt.date(2024, 3, 3), _Sess(content=b"x" * 999)) is None  # < 1000 bytes
    exact = _bse_csv([("STK", "AAA", 10, 100)]).encode().ljust(1000, b" ")
    assert bh._bse_day(dt.date(2024, 3, 4), _Sess(content=exact)) is not None  # exactly 1000 bytes is accepted
    assert bh._bse_day(dt.date(2024, 3, 5), _Sess(boom=True)) is None


def _seed_days(feeds, days, nse_syms=("AAA", "BBB"), bse_extra=None):
    nse, bse = feeds
    for i, d in enumerate(days):
        nse.days[d] = _nse_csv([("EQ", s, 100 + i + j, 1000) for j, s in enumerate(nse_syms)])
        if bse_extra:
            bse.days[d] = _bse_csv([("STK", s, 50 + i, 500) for s in bse_extra] + [("STK", "AAA", 1.0, 1)]).ljust(1100)


def test_fetch_history_end_to_end_cold_cache(bh, feeds, capsys):
    days = _weekdays(5)
    _seed_days(feeds, days, bse_extra=["BONLY"])
    out = bh.fetch_history(n_days=9, min_bars=3, workers=2)
    assert set(out) == {"AAA", "BBB", "BONLY"}
    assert len(out["AAA"]) == 5 and len(out["BONLY"]) == 5
    # NSE wins the collision: BSE row for AAA (close 1.0) must not appear; NSE close for AAA on the oldest day is 104? (index 4)
    assert (out["AAA"]["Close"] > 50).all()
    assert out["AAA"].index.is_monotonic_increasing and list(out["AAA"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert bh.ASSEMBLED.exists() and bh.CLEANED.exists()
    assembled = pd.read_parquet(bh.ASSEMBLED)
    assert set(assembled["_exch"]) == {"NSE", "BSE"} and assembled["Date"].nunique() == 5
    cleaned = pd.read_parquet(bh.CLEANED)
    assert set(cleaned["Symbol"]) == {"AAA", "BBB", "BONLY"} and len(cleaned) == 15
    assert bh.built and bh.built[0] == ["AAA", "BBB", "BONLY"]  # LMDB store synced with the full set
    txt = capsys.readouterr().out
    assert "7 new date(s) to fetch" in txt
    assert "NSE: +5 new trading days" in txt and "BSE: +5 new trading days" in txt
    assert "fetch_history complete: 3 symbols with ≥3 bars" in txt
    assert feeds[0].exited


def test_fetch_history_min_bars_filter_and_exchange_selection(bh, feeds):
    days = _weekdays(4)
    _seed_days(feeds, days, nse_syms=("AAA",), bse_extra=["BONLY"])
    out = bh.fetch_history(n_days=7, min_bars=4, exchanges=("NSE",), verbose=False)
    assert set(out) == {"AAA"} and feeds[1].calls == []  # BSE never queried
    assert bh.fetch_history(n_days=7, min_bars=5, exchanges=("NSE",), verbose=False) == {}  # nothing reaches 5 bars


def test_fetch_history_negative_cache_records_dead_days_but_not_today(bh, feeds):
    win = _window(8)
    seeded = win[1:-1]  # the newest and the oldest weekday in the window have no file
    _seed_days(feeds, seeded)
    bh.fetch_history(n_days=8, min_bars=1, exchanges=("NSE",), verbose=False)
    dead = set(json.loads(bh.NODATA_FILE.read_text()))
    expected = {d.isoformat() for d in win if d not in seeded and d != TODAY}
    assert dead == expected and TODAY.isoformat() not in dead
    # second run: dead dates are skipped entirely
    feeds[0].calls.clear()
    bh.fetch_history(n_days=8, min_bars=1, exchanges=("NSE",), verbose=False)
    queried = set(feeds[0].calls)
    assert not (queried & {dt.date.fromisoformat(s) for s in dead})


def test_fetch_history_fast_path_when_everything_cached(bh, feeds, capsys):
    days = _weekdays(5)
    _seed_days(feeds, days)
    bh.fetch_history(n_days=9, min_bars=1, exchanges=("NSE",), verbose=False)
    feeds[0].calls.clear()
    bh.built.clear()
    out = bh.fetch_history(n_days=9, min_bars=1, exchanges=("NSE",))
    assert set(out) == {"AAA", "BBB"} and feeds[0].calls == [] and bh.built == []  # no fetch, no rebuild
    # dates absent from the cache are dead-listed on the first run, so the second sees nothing missing
    assert "fast path (cleaned cache): 2 symbols" in capsys.readouterr().out


def test_fetch_history_no_new_eod_yet_uses_cleaned(bh, feeds, capsys):
    days = _weekdays(4)
    _seed_days(feeds, days[1:])  # today's file is not published yet
    bh.fetch_history(n_days=6, min_bars=1, exchanges=("NSE",), verbose=False)
    feeds[0].calls.clear()
    bh.NODATA_FILE.unlink(missing_ok=True)  # today is never dead-listed; clear any others
    out = bh.fetch_history(n_days=6, min_bars=1, exchanges=("NSE",))
    txt = capsys.readouterr().out
    assert set(out) == {"AAA", "BBB"}
    assert "fast path (no new EOD yet)" in txt and len(bh.built) == 1  # no store rebuild on this pass


def test_fetch_history_seeds_assembled_from_committed_cleaned(bh, feeds, capsys):
    days = _weekdays(3)
    rows = [{"Date": pd.Timestamp(d), "Symbol": "SEED", "Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 10.0 + i, "Volume": 100.0} for i, d in enumerate(days)]
    pd.DataFrame(rows).to_parquet(bh.CLEANED)
    out = bh.fetch_history(n_days=5, min_bars=1, exchanges=("NSE",))
    txt = capsys.readouterr().out
    assert "seeded assembled cache from committed cleaned_long.parquet" in txt
    assert set(out) == {"SEED"} and len(out["SEED"]) == 3
    assert set(pd.read_parquet(bh.ASSEMBLED)["_exch"]) == {"NSE"}  # seed rows are labelled NSE


def test_fetch_history_corrupt_caches_are_rebuilt(bh, feeds):
    win = _window(6)
    _seed_days(feeds, win)
    bh.ASSEMBLED.write_text("junk")
    bh.NODATA_FILE.write_text("not json")
    out = bh.fetch_history(n_days=6, min_bars=1, exchanges=("NSE",), verbose=False)
    assert set(out) == {"AAA", "BBB"} and len(out["AAA"]) == len(win)


def test_fetch_history_no_data_anywhere(bh, feeds, capsys):
    assert bh.fetch_history(n_days=3, min_bars=1, exchanges=("NSE",), verbose=False) == {}
    assert not bh.ASSEMBLED.exists()


def test_fetch_history_weekday_fallback_without_calendar(bh, feeds, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "market_calendar", None)  # ImportError
    days = _weekdays(2)
    _seed_days(feeds, days)
    out = bh.fetch_history(n_days=4, min_bars=1, exchanges=("NSE",), verbose=False)
    assert len(out["AAA"]) == 2
    assert all(d.weekday() < 5 for d in feeds[0].calls)  # weekends never requested


def test_fetch_history_survives_store_and_exit_failures(bh, feeds, monkeypatch, capsys):
    feeds[0].exit_raises = True
    _seed_days(feeds, _weekdays(2))
    fake_module(monkeypatch, "bhavcopy_store", build=lambda hist, verbose: 1 / 0)
    out = bh.fetch_history(n_days=3, min_bars=1, exchanges=("NSE",))
    assert set(out) == {"AAA", "BBB"}
    assert "LMDB store not synced" in capsys.readouterr().out


def test_get_symbol(bh):
    assert bh.get_symbol("AAA") is None  # no assembled cache
    today = pd.Timestamp(TODAY)
    rows = []
    for i in range(3):
        d = today - pd.Timedelta(days=2 - i)
        rows.append({"Date": d, "Symbol": "AAA", "_exch": "NSE", "Open": 10.0 + i, "High": 10.0 + i, "Low": 10.0 + i, "Close": 10.0 + i, "Volume": 10})
        rows.append({"Date": d, "Symbol": "AAA", "_exch": "BSE", "Open": 99.0, "High": 99.0, "Low": 99.0, "Close": 99.0, "Volume": 10})
    rows.append({"Date": today - pd.Timedelta(days=800), "Symbol": "AAA", "_exch": "NSE", "Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 10})
    rows.append({"Date": today, "Symbol": "ZZZ", "_exch": "NSE", "Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 10})
    pd.DataFrame(rows).to_parquet(bh.ASSEMBLED)
    g = bh.get_symbol("AAA", n_days=400)
    assert list(g["Close"]) == [10.0, 11.0, 12.0]  # NSE row wins duplicates; old row outside window dropped
    assert len(bh.get_symbol("AAA", n_days=1000)) == 4
    assert bh.get_symbol("NOPE") is None
    assert len(bh.get_symbol("ZZZ")) == 1


def test_get_symbol_without_cleaner(bh, monkeypatch):
    monkeypatch.setattr(bh, "clean_ohlcv", None)
    pd.DataFrame([{"Date": pd.Timestamp(TODAY), "Symbol": "AAA", "_exch": "NSE", "Open": -1.0, "High": -1.0, "Low": -1.0, "Close": -1.0, "Volume": 1}]).to_parquet(bh.ASSEMBLED)
    assert bh.get_symbol("AAA")["Close"].iloc[0] == -1.0  # not cleaned
