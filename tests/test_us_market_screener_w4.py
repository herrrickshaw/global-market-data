"""us_market_screener: universe parsing, SQLite progress/results store, scans, CSV outputs."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load
from tests.test_scan_scripts_w4 import _box, _good_spec


@pytest.fixture
def um(monkeypatch, tmp_path):
    mod = load(monkeypatch, "us_market_screener", tmp_path)
    monkeypatch.setattr(mod.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(mod, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()
    FakeTicker.spec = _good_spec()
    return mod


NASDAQ_TXT = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc.|Q|N|N|100|N|N\nQQQ|Invesco|Q|N|N|100|Y|N\nTEST|Test|Q|Y|N|100|N|N\nBAD|Bad Co|Q|N|D|100|N|N\n"
    "BRK-A|Berkshire|Q|N|N|100|N|N\nLONGER|Too long|Q|N|N|100|N|N\nFile Creation Time: 1234||||||\n"
)
OTHER_TXT = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    "KO|Coca Cola|N|KO|N|100|N|KO\nAAPL|Dup|N|AAPL|N|100|N|AAPL\nSPY|SPDR|P|SPY|Y|100|N|SPY\nXYZ|Xyz|A|XYZ|N|100|N|XYZ\n"
    "OTH|Other|Q|OTH|N|100|N|OTH\nTST|Tst|N|TST|N|100|Y|TST\nFile Creation Time: 1234||||||\n"
)


class Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_fetch_stock_universe_filters_and_dedups(um, monkeypatch):
    urls = []
    monkeypatch.setattr(um.requests, "get", lambda url, timeout: urls.append(url) or Resp(NASDAQ_TXT if url == um.NASDAQ_URL else OTHER_TXT))
    df = um.fetch_stock_universe()
    assert list(df["Symbol"]) == ["AAPL", "KO", "XYZ", "OTH"]  # ETF/test/deficient/non-alpha/long symbols removed, AAPL deduped
    assert dict(zip(df["Symbol"], df["Exchange"])) == {"AAPL": "NASDAQ", "KO": "NYSE", "XYZ": "AMEX", "OTH": "OTHER"}
    assert urls == [um.NASDAQ_URL, um.OTHER_URL]
    urls.clear()
    assert list(um.fetch_stock_universe("nasdaq")["Symbol"]) == ["AAPL"] and urls == [um.NASDAQ_URL]
    urls.clear()
    assert list(um.fetch_stock_universe("nyse")["Symbol"]) == ["KO", "AAPL", "XYZ", "OTH"] and urls == [um.OTHER_URL]


def test_progress_and_results_store(um, tmp_path):
    con = um.init_db(tmp_path / "scan.db")
    assert um.already_done(con, "darvas") == set()
    um.mark_done(con, "AAA", "darvas")
    um.mark_done(con, "BBB", "fundamentals")
    um.mark_done(con, "AAA", "darvas")  # idempotent
    assert um.already_done(con, "darvas") == {"AAA"} and um.already_done(con, "fundamentals") == {"BBB"}
    um.upsert_result(con, {"symbol": "AAA", "name": "Acme", "darvas_signal": "IN_BOX", "f_score": 7})
    um.upsert_result(con, {"symbol": "AAA", "f_score": 9})  # partial update keeps other columns
    row = con.execute("SELECT name, darvas_signal, f_score FROM results WHERE symbol='AAA'").fetchone()
    assert row == ("Acme", "IN_BOX", 9) and con.execute("SELECT count(*) FROM results").fetchone()[0] == 1
    con2 = um.init_db(tmp_path / "scan.db")  # reopening keeps data
    assert um.already_done(con2, "darvas") == {"AAA"}


def test_run_darvas_batch_paths(um, monkeypatch):
    good = _box(21.0)
    multi = pd.concat({"AAA": good.assign(Open=1.0, Volume=1.0), "BBB": good.assign(Open=1.0, Volume=1.0)}, axis=1)
    calls = []
    monkeypatch.setattr(um.yf, "download", lambda **kw: calls.append(kw) or multi, raising=False)
    ticks = []

    class Pbar:
        def update(self, n):
            ticks.append(n)

    r = um.run_darvas_batch(["AAA", "BBB", "MISSING"], batch_size=500, pbar=Pbar())
    assert r["AAA"]["signal"] == "BREAKOUT_BUY" and r["MISSING"]["signal"] == "NO_DATA"
    assert calls[0]["tickers"] == "AAA BBB MISSING" and calls[0]["period"] == "6mo" and ticks == [1, 1]  # NO_DATA path skips the tick
    calls.clear()
    um.run_darvas_batch(["A", "B", "C"], batch_size=2)
    assert [c["tickers"] for c in calls] == ["A B", "C"]
    flat = good.assign(Open=1.0, Volume=1.0)
    monkeypatch.setattr(um.yf, "download", lambda **kw: flat, raising=False)
    assert um.run_darvas_batch(["ONLY"])["ONLY"]["signal"] == "BREAKOUT_BUY"  # single-ticker flat frame
    monkeypatch.setattr(um.yf, "download", lambda **kw: 1 / 0, raising=False)
    ticks.clear()
    assert um.run_darvas_batch(["A", "B"], pbar=Pbar()) == {} and ticks == [2]  # failed batch is skipped but progress advances


def test_darvas_from_df(um):
    assert um._darvas_from_df(None)["signal"] == "INSUFFICIENT_DATA" and um._darvas_from_df(_box(1.0).iloc[:5])["signal"] == "INSUFFICIENT_DATA"
    assert um._darvas_from_df(_box(21.0))["signal"] == "BREAKOUT_BUY" and um._darvas_from_df(_box(9.0))["signal"] == "BREAKDOWN_SELL"


def test_run_piotroski_and_coffee_can(um):
    t = FakeTicker("AAA")
    p = um.run_piotroski(t)
    assert p["f_score"] == 9 and p["components"]["F1"] == 1 and len(p["components"]) == 9
    c = um.run_coffee_can(t)
    assert c["qualifies"] is True and c["score"] in ("6/6", 6) and set(c["criteria"]) == {f"C{i}" for i in range(1, 7)}
    FakeTicker.spec = {"info": {}, "market_cap": 0}
    assert "error" in um.run_piotroski(FakeTicker("A")) and "error" in um.run_coffee_can(FakeTicker("A"))


def test_scan_fundamentals_one_flattens_scores(um):
    r = um.scan_fundamentals_one("AAA", "Acme", "NASDAQ")
    assert r["symbol"] == "AAA" and r["sector"] == "Tech" and r["f_score"] == 9 and r["f1"] == 1 and r["f9"] == 1
    assert r["cc_qualifies"] == 1 and r["cc_c1"] == 1 and "error" not in r
    FakeTicker.spec = {"info": {}, "market_cap": 0}
    r = um.scan_fundamentals_one("AAA", "Acme", "NASDAQ")
    assert r["cc_qualifies"] == 0 and r["error"]  # both scans failed -> error recorded
    FakeTicker.spec = {"ctor_error": "boom"}
    assert um.scan_fundamentals_one("AAA", "Acme", "NASDAQ")["error"] == "boom"


def test_compile_outputs_files_and_triple_hits(um, tmp_path, capsys):
    con = um.init_db(tmp_path / "s.db")
    rows = [
        {"symbol": "TRI", "name": "Triple Co", "exchange": "NASDAQ", "darvas_signal": "BREAKOUT_BUY", "darvas_upside": 2.0, "f_score": um.PIOTROSKI_STRONG,
         "cc_qualifies": 1, "cc_score": "6/6", "market_cap": 5e9},
        {"symbol": "BRK", "name": "Brk", "exchange": "NYSE", "darvas_signal": "BREAKOUT_BUY", "darvas_upside": 1.0, "f_score": um.PIOTROSKI_STRONG - 1,
         "cc_qualifies": 1, "cc_score": "5/6", "market_cap": 9e9},
        {"symbol": "PIO", "name": "Pio", "exchange": "NYSE", "darvas_signal": "IN_BOX", "f_score": 8, "cc_qualifies": 0, "market_cap": 1e9},
        {"symbol": "NIL", "name": "Nil", "exchange": "NYSE", "darvas_signal": "NO_BOX", "cc_qualifies": 0},
    ]
    for r in rows:
        um.upsert_result(con, r)
    um.compile_outputs(con, "20260101")
    out = tmp_path / "out"
    read = lambda n: pd.read_csv(out / f"{n}_20260101.csv")  # noqa: E731
    assert len(read("scan_summary")) == 4
    assert list(read("darvas_breakouts")["symbol"]) == ["BRK", "TRI"]  # sorted by upside ascending
    assert list(read("strong_piotroski")["symbol"]) == ["PIO", "TRI"]  # f-score >= threshold, best first
    assert list(read("coffee_can")["symbol"]) == ["BRK", "TRI"]  # largest market cap first
    assert list(read("triple_hits")["symbol"]) == ["TRI"]
    txt = capsys.readouterr().out
    assert "Triple hits (1 stocks)" in txt and "Triple Co" in txt
    empty = um.init_db(tmp_path / "e.db")
    um.compile_outputs(empty, "20260102")
    assert "No results in database yet." in capsys.readouterr().out and not (out / "scan_summary_20260102.csv").exists()
