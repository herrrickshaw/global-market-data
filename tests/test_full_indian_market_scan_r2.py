"""full_indian_market_scan: symbol caches, NSE/BSE symbol fetch, bulk download, main(), Excel."""

import json
import sys
import time
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import load


@pytest.fixture
def ind(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_indian_market_scan", tmp_path)
    d = tmp_path / "indian_full_scan"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", d)
    monkeypatch.setattr(mod, "_SYMBOL_CACHE", d / ".symbols_cache.json")
    monkeypatch.setattr(
        mod, "time", types.SimpleNamespace(**{**vars(time), "sleep": lambda s: None})
    )
    return mod


def _ohlc(closes, highs=None, lows=None):
    idx = pd.date_range("2024-01-01", periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "High": c * 1.01 if highs is None else highs,
            "Low": c * 0.99 if lows is None else lows,
            "Close": c,
        },
        index=idx,
    )


# ---------------- symbol cache ----------------
def test_symbol_cache_roundtrip_ttl(ind):
    assert ind._load_symbol_cache() == (None, None)
    ind._save_symbol_cache(["A"], ["B"])
    assert ind._load_symbol_cache() == (["A"], ["B"])
    ind._SYMBOL_CACHE.write_text(
        json.dumps({"ts": time.time() - ind.SYMBOL_CACHE_TTL - 1, "nse": ["A"], "bse_only": []})
    )
    assert ind._load_symbol_cache() == (None, None)
    ind._SYMBOL_CACHE.write_text(
        json.dumps({"ts": time.time() - ind.SYMBOL_CACHE_TTL + 60, "nse": ["A"]})
    )
    assert ind._load_symbol_cache() == (["A"], [])
    ind._SYMBOL_CACHE.write_text("nope")
    assert ind._load_symbol_cache() == (None, None)


def test_save_symbol_cache_swallows_errors(ind, tmp_path):
    ind._SYMBOL_CACHE = tmp_path / "nodir" / "c.json"
    ind._save_symbol_cache(["A"], [])
    assert not ind._SYMBOL_CACHE.exists()


# ---------------- NSE symbols ----------------
def _set_nse_eq(monkeypatch, fn):
    monkeypatch.setattr(sys.modules["nsepython"], "nse_eq_symbols", fn, raising=False)


def test_fetch_nse_symbols_primary_sorted(ind, monkeypatch):
    syms = [f"S{i:03d}" for i in range(150)][::-1]
    _set_nse_eq(monkeypatch, lambda: syms)
    out = ind.fetch_nse_symbols()
    assert out == sorted(syms) and len(out) == 150


def test_fetch_nse_symbols_primary_needs_over_100(ind, monkeypatch):
    _set_nse_eq(monkeypatch, lambda: [f"S{i}" for i in range(100)])  # exactly 100: rejected
    monkeypatch.setattr(ind, "_nse_get_bhavcopy", lambda d: None)
    assert ind.fetch_nse_symbols() == []
    _set_nse_eq(monkeypatch, lambda: [f"S{i}" for i in range(101)])
    assert len(ind.fetch_nse_symbols()) == 101


def test_fetch_nse_symbols_bhavcopy_fallback(ind, monkeypatch, capsys):
    def boom():
        raise RuntimeError("api down")

    _set_nse_eq(monkeypatch, boom)
    dates = []

    def bhav(d):
        dates.append(d)
        if len(dates) == 1:
            raise RuntimeError("holiday")
        if len(dates) == 2:
            return pd.DataFrame()
        if len(dates) == 3:
            return None
        if len(dates) == 4:  # no series column -> next
            return pd.DataFrame({"foo": [1]})
        return pd.DataFrame(
            {"SYMBOL": [" ZED ", "ABC", None, "XYZ"], " SERIES": ["EQ", "EQ", "EQ", "BE"]}
        )

    monkeypatch.setattr(ind, "_nse_get_bhavcopy", bhav)
    out = ind.fetch_nse_symbols()
    assert out == ["ABC", "ZED"]  # EQ only, stripped, NaN dropped, sorted
    assert len(dates) == 5 and len(set(dates)) == 5  # walks back a day at a time
    assert "nsepython.nse_eq_symbols failed: api down" in capsys.readouterr().out


def test_fetch_nse_symbols_bhavcopy_no_eq_rows_keeps_walking(ind, monkeypatch, capsys):
    _set_nse_eq(monkeypatch, lambda: [])
    calls = []

    def bhav(d):
        calls.append(d)
        return pd.DataFrame({"SYMBOL": ["A"], "SERIES": ["BE"]})

    monkeypatch.setattr(ind, "_nse_get_bhavcopy", bhav)
    assert ind.fetch_nse_symbols() == []
    assert len(calls) == 7  # exactly one week of look-back
    assert "Could not fetch NSE symbol list" in capsys.readouterr().out


# ---------------- BSE symbols ----------------
class _Resp:
    def __init__(self, text):
        self.text = text


BSE_CSV = (
    "Security Code,Issuer Name,Security Id,Security Name,Status,Group,Face Value,ISIN,Instrument\n"
    "1,A,AAA,A Ltd,Active,A,1,X1,Equity\n"
    "2,B,BBB,B Ltd,Active,A,1,X2,Equity\n"
    "3,C,CCC,C Ltd,Suspended,A,1,X3,Equity\n"
    "4,D,DDD,D Ltd,Active,A,1,X4,Debentures\n"
    "5,E,,E Ltd,Active,A,1,X5,Equity\n"
    "6,F, FFF ,F Ltd,Active,A,1,X6,Equity\n"
)


def test_bse_session_headers_and_warmup_failure(ind, monkeypatch):
    class Sess:
        def __init__(self):
            self.headers = {}
            self.got = []

        def get(self, url, timeout):
            self.got.append((url, timeout))
            raise RuntimeError("blocked")

    s = Sess()
    s.headers = types.SimpleNamespace(update=lambda d: s.headers.__dict__.update(d))
    monkeypatch.setattr(ind.requests, "Session", lambda: s)
    assert ind._bse_session() is s  # warm-up failure swallowed
    assert s.got == [("https://www.bseindia.com/", 15)]
    assert s.headers.__dict__["Referer"] == "https://www.bseindia.com/"


def test_fetch_bse_only_symbols_filters(ind, monkeypatch):
    seen = {}

    class Sess:
        def get(self, url, params=None, timeout=None):
            seen["params"], seen["timeout"] = params, timeout
            return _Resp(BSE_CSV)

    monkeypatch.setattr(ind, "_bse_session", lambda: Sess())
    out = ind.fetch_bse_only_symbols({"AAA"})
    assert out == ["BBB", "FFF"]  # active equity, not NSE, non-blank, stripped
    assert seen["params"]["segment"] == "Equity" and seen["timeout"] == 30


def test_fetch_bse_only_retries_then_gives_up(ind, monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(ind.time, "sleep", sleeps.append)
    attempts = []

    class Sess:
        def get(self, *a, **k):
            attempts.append(1)
            return _Resp("   ")  # empty body -> ValueError

    monkeypatch.setattr(ind, "_bse_session", lambda: Sess())
    assert ind.fetch_bse_only_symbols(set()) == []
    assert len(attempts) == 3 and sleeps == [3, 3]  # no sleep after the last attempt
    assert "Could not fetch BSE symbol list" in capsys.readouterr().out


def test_fetch_bse_only_recovers_on_second_attempt(ind, monkeypatch):
    n = []

    class Sess:
        def get(self, *a, **k):
            n.append(1)
            if len(n) == 1:
                raise RuntimeError("flaky")
            return _Resp(BSE_CSV)

    monkeypatch.setattr(ind, "_bse_session", lambda: Sess())
    assert ind.fetch_bse_only_symbols(set()) == ["AAA", "BBB", "FFF"]
    assert len(n) == 2


# ---------------- bulk_download_ohlc ----------------
def _multi(tickers, n=10):
    idx = pd.date_range("2024-01-01", periods=n)
    cols = pd.MultiIndex.from_product([["Close"], tickers])
    return pd.DataFrame(np.arange(n * len(tickers), dtype=float).reshape(n, -1) + 1, idx, cols)


def test_bulk_download_batches_and_sleeps(ind, monkeypatch):
    monkeypatch.setattr(ind, "BATCH_SIZE", 2)
    sleeps, seen = [], []
    monkeypatch.setattr(ind.time, "sleep", sleeps.append)

    def dl(b, **k):
        seen.append((list(b), k["period"], k["auto_adjust"]))
        return _multi(b)

    monkeypatch.setattr(ind.yf, "download", dl)
    out = ind.bulk_download_ohlc(["A.NS", "B.NS", "C.NS"], period="1y")
    assert sorted(out) == ["A.NS", "B.NS", "C.NS"]
    assert seen == [(["A.NS", "B.NS"], "1y", True), (["C.NS"], "1y", True)]
    assert sleeps == [ind.SLEEP_BETWEEN]


def test_bulk_download_min_rows_empty_error_and_single(ind, monkeypatch, capsys):
    monkeypatch.setattr(ind.yf, "download", lambda b, **k: _multi(b, n=7))
    assert ind.bulk_download_ohlc(["A.NS"]) == {}  # needs confirm+5 = 8 rows
    monkeypatch.setattr(ind.yf, "download", lambda b, **k: _multi(b, n=8))
    assert list(ind.bulk_download_ohlc(["A.NS"])) == ["A.NS"]
    monkeypatch.setattr(ind.yf, "download", lambda b, **k: pd.DataFrame())
    assert ind.bulk_download_ohlc(["A.NS"]) == {}
    assert "empty" in capsys.readouterr().out
    flat = _ohlc(np.arange(1, 11))
    monkeypatch.setattr(ind.yf, "download", lambda b, **k: flat)
    assert ind.bulk_download_ohlc(["A.NS", "B.NS"]) == {"A.NS": flat}
    monkeypatch.setattr(ind.yf, "download", lambda b, **k: _multi(["Z.NS"]))
    assert ind.bulk_download_ohlc(["A.NS"]) == {}  # KeyError swallowed
    monkeypatch.setattr(
        ind.yf, "download", lambda b, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert ind.bulk_download_ohlc(["A.NS"]) == {}
    assert "ERROR — boom" in capsys.readouterr().out


# ---------------- darvas / golden-cross edge cases ----------------
def test_darvas_alt_column_names_and_missing(ind):
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    lows = [3, 3, 12, 10, 11, 12, 13, 13]
    closes = [4, 4, 11, 15, 15, 15, 15, 25]
    df = pd.DataFrame(
        {"CH_TRADE_HIGH_PRICE": highs, "CH_TRADE_LOW_PRICE": lows, "CH_CLOSING_PRICE": closes}
    )
    assert ind.compute_darvas_box(df)["signal"] == "BREAKOUT_BUY"
    assert ind.compute_darvas_box(pd.DataFrame({"Close": range(20)}))["signal"] == (
        "INSUFFICIENT_DATA"
    )


def test_darvas_bottom_fallback_and_none(ind):
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    r = ind.compute_darvas_box(
        _ohlc([4, 4, 11, 15, 15, 15, 15, 15], highs, [3, 3, 9, 9, 8, 7, 6, 6])
    )
    assert r["box_bottom"] == 6.0
    r0 = ind.compute_darvas_box(
        _ohlc([4, 4, 11, 15, 15, 15, 15, 15], highs, [3, 3, 0, 0, 0, 0, 0, 0])
    )
    assert r0["signal"] == "NO_BOX" and r0["box_bottom"] is None and r0["box_top"] == 20.0


# ---------------- save_excel ----------------
@pytest.fixture
def fake_excel(monkeypatch, ind):
    sheets = {}

    class FakeWriter:
        def __init__(self, path, engine=None):
            sheets["path"], sheets["engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def to_excel(self, writer, sheet_name=None, index=True):
        sheets[sheet_name] = self.copy()

    monkeypatch.setattr(ind.pd, "ExcelWriter", FakeWriter)
    monkeypatch.setattr(pd.DataFrame, "to_excel", to_excel)
    return sheets


def test_save_excel_sheets(ind, fake_excel):
    all_rows = [
        {"Symbol": "A", "Change%": 1.0, "GC_Signal": "GOLDEN_CROSS", "DMA_Gap%": 2},
        {"Symbol": "B", "Change%": 5.0, "GC_Signal": "BELOW_200DMA", "DMA_Gap%": 9},
        {"Symbol": "C", "Change%": 3.0, "GC_Signal": "GOLDEN_CROSS", "DMA_Gap%": 7},
    ]
    fund = [
        {
            "Symbol": "A",
            "Piotroski_Score": 5,
            "Piotroski_Strong": "NO",
            "CoffeeCan": "PASS",
            "Revenue_CAGR_%": 1,
            "MagicFormula": "FAIL",
            "ROIC_%": 1,
            "BullCartel": "PASS",
            "Profit_Growth_YoY_%": 3,
        },
        {
            "Symbol": "B",
            "Piotroski_Score": 8,
            "Piotroski_Strong": "YES",
            "CoffeeCan": "PASS",
            "Revenue_CAGR_%": 9,
            "MagicFormula": "PASS",
            "ROIC_%": 7,
            "BullCartel": "FAIL",
            "Profit_Growth_YoY_%": 1,
        },
    ]
    six = [{"Symbol": "A", "Screens_Passed": 3}, {"Symbol": "B", "Screens_Passed": 5}]
    p = ind.save_excel(all_rows, [], fund, [], six)
    assert p.parent == ind.DOWNLOAD_DIR and p.name.startswith("indian_full_scan_")
    assert fake_excel["engine"] == "openpyxl"
    assert list(fake_excel["All_Stocks"]["Symbol"]) == ["B", "C", "A"]
    assert list(fake_excel["Darvas_Signals"]["Note"]) == ["No qualifying stocks today"]
    assert list(fake_excel["All_Fundamentals"]["Symbol"]) == ["B", "A"]
    assert list(fake_excel["Piotroski_Strong"]["Symbol"]) == ["B"]
    assert list(fake_excel["Coffee_Can"]["Symbol"]) == ["B", "A"]
    assert list(fake_excel["Magic_Formula"]["Symbol"]) == ["B"]
    assert list(fake_excel["Bull_Cartel"]["Symbol"]) == ["A"]
    assert list(fake_excel["Golden_Crossover"]["Symbol"]) == ["C", "A"]
    assert list(fake_excel["Multi_Screen_Hits"]["Symbol"]) == ["B", "A"]
    assert "Note" in fake_excel["Triple_Hits"].columns


def test_save_excel_default_tag_and_no_six(ind, fake_excel):
    ind.save_excel([], [], [], [])
    assert "Note" in fake_excel["Multi_Screen_Hits"].columns


# ---------------- main ----------------
def _trend(n=260, start=100.0, step=1.0):
    return _ohlc(start + step * np.arange(n))


def _stub_main(ind, monkeypatch, ohlc, fund=None, cache=(["AAA", "BBB"], ["CODE1", "AAA"])):
    monkeypatch.setattr(ind, "_load_symbol_cache", lambda: cache)
    monkeypatch.setattr(ind, "_USE_NSE_FETCHER", False)
    seen = {}

    def bulk(t, period):
        seen["tickers"], seen["period"] = list(t), period
        return ohlc

    monkeypatch.setattr(ind, "bulk_download_ohlc", bulk)
    saved = {"seen": seen}

    def save(*a, **k):
        saved["a"], saved["k"] = a, k
        return "OUT.xlsx"

    monkeypatch.setattr(ind, "save_excel", save)
    monkeypatch.setattr(ind, "fundamental_scan", fund or (lambda s, suf=".NS": {}))
    return saved


def test_main_ticker_map_top_and_nse_only(ind, monkeypatch):
    saved = _stub_main(ind, monkeypatch, {})
    ind.main(run_scans=False)
    assert saved["seen"]["tickers"] == ["AAA.NS", "BBB.NS", "CODE1.BO", "AAA.BO"]
    assert saved["seen"]["period"] == "1y"
    ind.main(run_scans=False, nse_only=True)
    assert saved["seen"]["tickers"] == ["AAA.NS", "BBB.NS"]
    ind.main(run_scans=False, top=3)
    assert saved["seen"]["tickers"] == ["AAA.NS", "BBB.NS", "CODE1.BO"]


def test_main_fetches_when_cache_misses(ind, monkeypatch):
    saved = _stub_main(ind, monkeypatch, {}, cache=(None, None))
    calls = []
    monkeypatch.setattr(ind, "fetch_nse_symbols", lambda: calls.append("nse") or ["N1", "N2"])
    monkeypatch.setattr(ind, "fetch_bse_only_symbols", lambda s: calls.append(sorted(s)) or ["B1"])
    cached = []
    monkeypatch.setattr(ind, "_save_symbol_cache", lambda n, b: cached.append((n, b)))
    ind.main(run_scans=False)
    assert calls == ["nse", ["N1", "N2"]] and cached == [(["N1", "N2"], ["B1"])]
    assert saved["seen"]["tickers"] == ["N1.NS", "N2.NS", "B1.BO"]


@pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason="full_indian_market_scan.py:736 iterates bse_only, which is None when --nse-only "
    "runs on a symbol-cache miss (main() only assigns it inside `if not nse_only`)",
)
def test_main_nse_only_on_cache_miss(ind, monkeypatch):
    saved = _stub_main(ind, monkeypatch, {}, cache=(None, None))
    monkeypatch.setattr(ind, "fetch_nse_symbols", lambda: ["N1", "N2"])
    monkeypatch.setattr(ind, "_save_symbol_cache", lambda n, b: None)
    ind.main(run_scans=False, nse_only=True)
    assert saved["seen"]["tickers"] == ["N1.NS", "N2.NS"]


def test_main_live_context(ind, monkeypatch, capsys):
    _stub_main(ind, monkeypatch, {})
    ok = types.SimpleNamespace(print_live_context=lambda: print("LIVE-CTX"))
    monkeypatch.setattr(ind, "_USE_NSE_FETCHER", True)
    monkeypatch.setattr(ind, "_NSE_FETCHER", ok)
    ind.main(run_scans=False)
    assert "LIVE-CTX" in capsys.readouterr().out

    def boom():
        raise RuntimeError("no net")

    monkeypatch.setattr(ind, "_NSE_FETCHER", types.SimpleNamespace(print_live_context=boom))
    ind.main(run_scans=False)
    assert "Live context unavailable: no net" in capsys.readouterr().out


def test_main_rows_without_scans(ind, monkeypatch):
    nan = pd.DataFrame({"High": [1] * 10, "Low": [1] * 10, "Close": [np.nan] * 10})
    ohlc = {"AAA.NS": _trend(), "BBB.NS": _ohlc([50.0]), "CODE1.BO": nan}
    saved = _stub_main(ind, monkeypatch, ohlc)
    res = ind.main(run_scans=False)
    rows = saved["a"][0]
    by = {r["Symbol"]: r for r in rows}
    assert set(by) == {"AAA", "BBB", "CODE1"}
    assert by["AAA"]["Suffix"] == ".NS" and by["CODE1"]["Suffix"] == ".BO"
    assert by["AAA"]["LTP"] == 359.0 and by["AAA"]["Prev_Close"] == 358.0
    assert by["AAA"]["Change%"] == round(1 / 358 * 100, 2)
    assert by["AAA"]["GC_Signal"] == "ABOVE_200DMA"
    assert by["BBB"]["Prev_Close"] is None and by["BBB"]["Change%"] is None
    assert by["CODE1"]["LTP"] is None and by["CODE1"]["Change%"] is None
    assert res["total_scanned"] == 3 and res["excel_path"] == "OUT.xlsx"
    assert saved["a"][2] == []


def test_main_unmapped_ticker_falls_back_to_itself(ind, monkeypatch):
    saved = _stub_main(ind, monkeypatch, {"ZZZ.NS": _trend(20)})
    ind.main(run_scans=False)
    assert saved["a"][0][0]["Symbol"] == "ZZZ.NS" and saved["a"][0][0]["Suffix"] == ""


def _breakout():
    closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, 25]
    highs = [5, 5, 10, 20, 18, 17, 16, 16, 16, 16, 16, 16, 16]
    lows = [3, 3, 12, 10, 11, 12, 13, 13, 13, 13, 13, 13, 13]
    return _ohlc(closes, highs, lows)


def test_main_fundamentals_flow(ind, monkeypatch):
    good = {
        "f_score": 8,
        "f_strong": True,
        "qualifies_cc": True,
        "cc_score": "6/6",
        "qualifies_mf": False,
        "qualifies_bc": False,
        "Net_Profit_Cr": 12.5,
    }

    def fund(sym, suffix=".NS"):
        if sym == "BOOM":
            raise RuntimeError("scan crashed")
        assert suffix in (".NS", ".BO")
        return good if sym == "AAA" else {"f_score": 1, "error": "x"}

    ohlc = {
        "AAA.NS": _breakout(),
        "BBB.NS": _ohlc(np.full(13, 20.0)),
        "BOOM.NS": _breakout(),
    }
    saved = _stub_main(ind, monkeypatch, ohlc, fund=fund, cache=(["AAA", "BBB", "BOOM"], []))
    res = ind.main(workers=2)
    _, darvas, fund_rows, triple, six = saved["a"][:5]
    assert [r["Symbol"] for r in darvas] == ["AAA", "BOOM"]
    by = {r["Symbol"]: r for r in fund_rows}
    assert set(by) == {"AAA", "BBB"}  # BOOM's failure is isolated
    assert by["AAA"]["Piotroski_Strong"] == "YES" and by["AAA"]["CoffeeCan"] == "PASS"
    assert by["AAA"]["MagicFormula"] == "FAIL" and by["AAA"]["Net_Profit_Cr"] == 12.5
    assert by["BBB"]["Piotroski_Strong"] == "NO" and by["BBB"]["Error"] == "x"
    assert [r["Symbol"] for r in triple] == ["AAA"]
    assert [r["Symbol"] for r in six] == ["AAA"] and six[0]["Screens_Passed"] == 3
    assert res["breakout_count"] == 2 and res["triple_hits"] == triple


def test_main_screens_threshold_three(ind, monkeypatch):
    saved = _stub_main(
        ind,
        monkeypatch,
        {"AAA.NS": _breakout()},
        fund=lambda s, suf=".NS": {"f_strong": True},
        cache=(["AAA"], []),
    )
    ind.main(workers=1)
    assert saved["a"][4] == []  # breakout + piotroski = 2 < 3
    saved = _stub_main(
        ind,
        monkeypatch,
        {"AAA.NS": _breakout()},
        fund=lambda s, suf=".NS": {"f_strong": True, "qualifies_bc": True},
        cache=(["AAA"], []),
    )
    ind.main(workers=1)
    assert len(saved["a"][4]) == 1


def test_main_progress_every_100(ind, monkeypatch, capsys):
    ohlc = {f"S{i}.NS": _ohlc(np.full(12, 10.0)) for i in range(101)}
    _stub_main(ind, monkeypatch, ohlc, cache=([f"S{i}" for i in range(101)], []))
    ind.main(workers=4)
    out = capsys.readouterr().out
    assert "    100/101 done" in out and "    101/101 done" in out
    assert "    50/101 done" not in out


# ---------------- fundamental_scan branches not hit by the w4 tests ----------------
def _spec(**info):
    from tests.scan_loader_w4 import FakeTicker
    from tests.test_scan_scripts_w4 import _good_spec

    spec = _good_spec()
    spec["info"].update(info)
    FakeTicker.spec = spec
    return spec


def test_india_scan_debt_to_equity_from_info_vs_balance_sheet(ind):
    _spec()  # info D/E = 40 -> >10 is a percentage -> 0.4
    out = ind.fundamental_scan("AAA")
    assert out["cc_score"] == "4/5" and out["qualifies_cc"] is False  # only market cap fails
    spec = _spec()
    del spec["info"]["debtToEquity"]  # fall back to LongTermDebt / Equity = 100/600
    assert ind.fundamental_scan("AAA")["cc_score"] == "4/5"
    spec["info"]["debtToEquity"] = 100.0  # 1.0 is not < 1
    assert ind.fundamental_scan("AAA")["cc_score"] == "3/5"
    spec["info"]["debtToEquity"] = 10.0  # <=10 is already a ratio: 10x
    assert ind.fundamental_scan("AAA")["cc_score"] == "3/5"
    spec["info"]["debtToEquity"] = 0.99
    assert ind.fundamental_scan("AAA")["cc_score"] == "4/5"
    del spec["info"]["debtToEquity"]
    spec["balance_sheet"] = spec["balance_sheet"].drop(index="Long Term Debt")
    assert ind.fundamental_scan("AAA")["cc_score"] == "3/5"  # unknown D/E scores zero


def test_india_scan_market_cap_threshold_and_magic_formula(ind):
    spec = _spec()
    spec["market_cap"] = 5e9  # exactly 500 Cr counts
    out = ind.fundamental_scan("AAA")
    assert out["cc_score"] == "5/5" and out["qualifies_cc"] is True
    spec["market_cap"] = 4.99e9
    assert ind.fundamental_scan("AAA")["qualifies_cc"] is False


def test_india_scan_magic_formula_thresholds(ind):
    spec = _spec()
    out = ind.fundamental_scan("AAA")
    assert out["qualifies_mf"] is True and out["ROIC_%"] == 37.5
    assert out["Earnings_Yield_%"] == pytest.approx(15.79, abs=0.01)
    spec["info"]["bookValue"] = 0
    assert ind.fundamental_scan("AAA")["qualifies_mf"] is False
    spec["info"]["bookValue"] = 20.0
    spec["market_cap"] = 3e9  # earnings yield 300/(3000+100-200)=10.3% < 15
    assert ind.fundamental_scan("AAA")["qualifies_mf"] is False


def test_india_scan_info_and_fast_info_failures(ind):
    spec = _spec()
    spec["info"], spec["market_cap"] = Exception, Exception
    out = ind.fundamental_scan("AAA")
    assert out["error"] == "" and out["qualifies_mf"] is False and out["Earnings_Yield_%"] is None
    assert out["cc_score"] == "4/5"  # market-cap bit lost; balance-sheet D/E still scores


def test_india_scan_missing_statements(ind):
    from tests.scan_loader_w4 import FakeTicker

    FakeTicker.spec = {"info": {}, "market_cap": 0}
    out = ind.fundamental_scan("AAA")
    assert out["f_score"] is None and out["cc_score"] == "N/A" and out["ROCE_avg_%"] is None
    assert out["qualifies_bc"] is False and out["Net_Profit_Cr"] is None
    FakeTicker.spec = {"ctor_error": "boom"}
    out = ind.fundamental_scan("AAA")
    assert out["error"] == "boom" and out["qualifies_mf"] is False and out["cc_score"] == "N/A"


def test_india_scan_bull_cartel_thresholds(ind):
    spec = _spec()
    q = spec["quarterly_income_stmt"].copy()
    q.loc["Total Revenue"] = [300e7, 280e7, 260e7, 250e7, 260e7]  # +15.38% sales
    q.loc["Net Income"] = [60e7, 50e7, 45e7, 40e7, 30e7]  # +100% profit, 60 Cr
    spec["quarterly_income_stmt"] = spec["quarterly_financials"] = q
    out = ind.fundamental_scan("AAA")
    assert out["qualifies_bc"] is True and out["Net_Profit_Cr"] == 60.0
    assert out["Sales_Growth_YoY_%"] == pytest.approx(15.38, abs=0.01)
    q.loc["Total Revenue"] = [300e7, 280e7, 260e7, 250e7, 340e7]
    out = ind.fundamental_scan("AAA")
    assert out["Sales_Growth_YoY_%"] == pytest.approx(-11.76, abs=0.01) and not out["qualifies_bc"]
    q.loc["Net Income"] = [0.9e7, 50e7, 45e7, 40e7, 0.1e7]  # profit +800% but only 0.9 Cr
    q.loc["Total Revenue"] = [300e7, 280e7, 260e7, 250e7, 200e7]
    assert ind.fundamental_scan("AAA")["qualifies_bc"] is False
    q.loc["Net Income"] = [1.0e7, 50e7, 45e7, 40e7, 0.1e7]  # exactly 1 Cr is not > 1
    assert ind.fundamental_scan("AAA")["qualifies_bc"] is False
    q.loc["Net Income"] = [1.01e7, 50e7, 45e7, 40e7, 0.1e7]
    assert ind.fundamental_scan("AAA")["qualifies_bc"] is True


def test_main_counts_magic_formula(ind, monkeypatch, capsys):
    saved = _stub_main(
        ind,
        monkeypatch,
        {"AAA.NS": _ohlc(np.full(12, 10.0))},
        fund=lambda s, suf=".NS": {"qualifies_mf": True, "qualifies_cc": True, "f_strong": True},
        cache=(["AAA"], []),
    )
    ind.main(workers=1)
    out = capsys.readouterr().out
    assert "Magic Formula PASS:       1" in out and "Coffee Can PASS:          1" in out
    assert "Bull Cartel PASS:         0" in out
    assert [r["Screens_Passed"] for r in saved["a"][4]] == [3]
