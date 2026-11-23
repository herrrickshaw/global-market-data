"""full_korea_market_scan: KIND universe, pykrx OHLC, fundamentals, main()."""

import json
import sys
import time
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load
from tests.test_full_japan_market_scan_r2 import _WS, _breakdown, _breakout, _Dim, _ohlc
from tests.test_scan_scripts_w4 import _good_spec


@pytest.fixture
def kr(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_korea_market_scan", tmp_path)
    d = tmp_path / "korea_scan"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", d)
    monkeypatch.setattr(mod, "_CACHE_FILE", d / ".symbols_cache.json")
    monkeypatch.setattr(
        mod, "time", types.SimpleNamespace(**{**vars(time), "sleep": lambda s: None})
    )
    return mod


# ---------------- cache ----------------
def test_cache_roundtrip_ttl_and_errors(kr, tmp_path):
    assert kr._load_cache() is None
    kr._save_cache([{"code": "1"}])
    assert kr._load_cache() == [{"code": "1"}]
    kr._CACHE_FILE.write_text(
        json.dumps({"ts": time.time() - kr.SYMBOL_CACHE_TTL - 1, "universe": [1]})
    )
    assert kr._load_cache() is None
    kr._CACHE_FILE.write_text(json.dumps({"ts": time.time() - 60}))
    assert kr._load_cache() == []
    kr._CACHE_FILE.write_text("{")
    assert kr._load_cache() is None
    kr._CACHE_FILE = tmp_path / "nodir" / "x.json"
    kr._save_cache([1])
    assert not kr._CACHE_FILE.exists()


# ---------------- KIND ----------------
class _Resp:
    text = "<html/>"

    def raise_for_status(self):
        pass


KIND_DF = pd.DataFrame(
    {
        "회사명": ["삼성전자", " Kakao ", "Bad", "Short", "Long"],
        "종목코드": [5930, "35720", "ABC123", 12, "1234567"],
        "업종": ["반도체", " 서비스 ", "x", "y", "z"],
    }
)


def test_fetch_kind_list_parses_and_pads(kr, monkeypatch):
    seen = {}

    def get(url, params, headers, timeout):
        seen.update(url=url, params=params, timeout=timeout, headers=headers)
        return _Resp()

    monkeypatch.setattr(kr.requests, "get", get)
    monkeypatch.setattr(kr.pd, "read_html", lambda *a, **k: [KIND_DF])
    out = kr._fetch_kind_list("stockMkt", "KOSPI", ".KS")
    assert out == [
        {
            "code": "005930",
            "name": "삼성전자",
            "sector": "반도체",
            "market": "KOSPI",
            "yf_suffix": ".KS",
        },
        {
            "code": "035720",
            "name": "Kakao",
            "sector": "서비스",
            "market": "KOSPI",
            "yf_suffix": ".KS",
        },
        {"code": "000012", "name": "Short", "sector": "y", "market": "KOSPI", "yf_suffix": ".KS"},
    ]  # alphabetic and 7-digit codes dropped; short codes zero-padded to six digits
    assert seen["params"] == {"method": "download", "searchType": "13", "marketType": "stockMkt"}
    assert seen["timeout"] == 20 and seen["url"] == kr._KIND_URL


def test_fetch_kind_list_missing_columns_and_errors(kr, monkeypatch, capsys):
    monkeypatch.setattr(kr.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(kr.pd, "read_html", lambda *a, **k: [pd.DataFrame({"종목코드": [np.nan]})])
    assert kr._fetch_kind_list("m", "KOSDAQ", ".KQ") == []  # NaN code is not numeric
    monkeypatch.setattr(
        kr.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert kr._fetch_kind_list("m", "KOSDAQ", ".KQ") == []
    assert "KIND fetch error (KOSDAQ): down" in capsys.readouterr().out


# ---------------- universe ----------------
def test_build_universe_cache_and_kospi_filter(kr, capsys):
    kr._save_cache([{"code": "1", "market": "KOSPI"}, {"code": "2", "market": "KOSDAQ"}])
    assert [s["code"] for s in kr.build_krx_universe()] == ["1", "2"]
    assert [s["code"] for s in kr.build_krx_universe(kospi_only=True)] == ["1"]
    assert "Symbol cache hit: 1 KRX tickers" in capsys.readouterr().out


def test_build_universe_fetches_and_caches(kr, monkeypatch):
    calls = []

    def kind(param, label, suffix):
        calls.append((param, label, suffix))
        return [{"code": param, "market": label, "yf_suffix": suffix}]

    monkeypatch.setattr(kr, "_fetch_kind_list", kind)
    out = kr.build_krx_universe()
    assert [s["market"] for s in out] == ["KOSPI", "KOSDAQ"]
    assert calls == [("stockMkt", "KOSPI", ".KS"), ("kosdaqMkt", "KOSDAQ", ".KQ")]
    assert kr._load_cache() == out
    kr._CACHE_FILE.unlink()
    calls.clear()
    out = kr.build_krx_universe(kospi_only=True)
    assert calls == [("stockMkt", "KOSPI", ".KS")] and len(out) == 1


def test_build_universe_exits_when_empty(kr, monkeypatch):
    monkeypatch.setattr(kr, "_fetch_kind_list", lambda *a: [])
    with pytest.raises(SystemExit) as e:
        kr.build_krx_universe()
    assert "Could not fetch any KRX tickers" in str(e.value)


# ---------------- pykrx OHLC ----------------
def test_normalise_cols(kr):
    df = pd.DataFrame({"고가": [1], "저가": [2], "종가": [3], "시가": [4], "거래량": [5], "x": [6]})
    assert list(kr._normalise_krx_cols(df).columns) == [
        "High",
        "Low",
        "Close",
        "Open",
        "Volume",
        "x",
    ]
    assert list(kr._normalise_krx_cols(pd.DataFrame({"x": [1]})).columns) == ["x"]


def _kdf(n, korean=True):
    idx = pd.date_range("2024-01-01", periods=n)
    names = ["고가", "저가", "종가"] if korean else ["High", "Low", "Close"]
    return pd.DataFrame(
        {names[0]: np.ones(n), names[1]: np.ones(n), names[2]: np.ones(n)}, index=idx
    )


def test_fetch_krx_ohlc_filters(kr, monkeypatch, capsys):
    frames = {
        "A": _kdf(8),
        "B": _kdf(7),  # one short of confirm + 5
        "C": pd.DataFrame(),
        "D": None,
        "E": pd.DataFrame({"종가": np.ones(10)}),  # missing High/Low
        "F": _kdf(10, korean=False),
    }
    seen = []

    def get(frm, to, code):
        seen.append((frm, to, code))
        if code == "G":
            raise RuntimeError("krx down")
        return frames.get(code)

    monkeypatch.setattr(kr.krx, "get_market_ohlcv_by_date", get)
    uni = [{"code": c} for c in "ABCDEFG"]
    out = kr.fetch_krx_ohlc(uni, months=3)
    assert sorted(out) == ["A", "F"]
    assert {len(f) for f, _, _ in seen} == {8} and {len(t) for _, t, _ in seen} == {8}
    frm, to = seen[0][0], seen[0][1]
    span = (pd.Timestamp(to) - pd.Timestamp(frm)).days
    assert span == 93  # months * 31 days
    assert "→ 2 tickers with usable OHLC data" in capsys.readouterr().out


def test_fetch_krx_ohlc_progress_and_sleep(kr, monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(kr.time, "sleep", sleeps.append)
    monkeypatch.setattr(kr.krx, "get_market_ohlcv_by_date", lambda f, t, c: _kdf(10))
    kr.fetch_krx_ohlc([{"code": str(i)} for i in range(200)])
    out = capsys.readouterr().out
    assert "200/200 fetched (200 usable)" in out and "100/200" not in out
    assert sleeps == [kr.SLEEP_BETWEEN_KRX] * 200


def test_fetch_krx_ohlc_sleeps_after_errors(kr, monkeypatch):
    sleeps = []
    monkeypatch.setattr(kr.time, "sleep", sleeps.append)

    def boom(f, t, c):
        raise RuntimeError("x")

    monkeypatch.setattr(kr.krx, "get_market_ohlcv_by_date", boom)
    assert kr.fetch_krx_ohlc([{"code": "1"}, {"code": "2"}]) == {}
    assert sleeps == [kr.SLEEP_BETWEEN_KRX] * 2


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="full_korea_market_scan.py:188-190 `continue`s on an empty/None response, skipping "
    "both the per-ticker rate-limit sleep and the progress print at the loop bottom",
)
def test_fetch_krx_ohlc_sleeps_after_empty_response(kr, monkeypatch):
    sleeps = []
    monkeypatch.setattr(kr.time, "sleep", sleeps.append)
    monkeypatch.setattr(kr.krx, "get_market_ohlcv_by_date", lambda f, t, c: None)
    kr.fetch_krx_ohlc([{"code": "1"}, {"code": "2"}])
    assert sleeps == [kr.SLEEP_BETWEEN_KRX] * 2


# ---------------- darvas + helpers ----------------
def test_darvas_rounding_and_degenerate(kr):
    assert kr.compute_darvas_box(None)["signal"] == "INSUFFICIENT_DATA"
    assert kr.compute_darvas_box(pd.DataFrame())["signal"] == "INSUFFICIENT_DATA"
    highs = [5, 5, 10, 20.4, 18, 17, 16, 16]
    lows = [3, 3, 12, 10.4, 11, 12, 13, 13]
    r = kr.compute_darvas_box(_ohlc([4, 4, 11, 15, 15, 15, 15, 25], highs, lows))
    assert r["signal"] == "BREAKOUT_BUY" and r["box_top"] == 20.0 and r["box_bottom"] == 10.0
    assert r["upside_pct"] == -18.4 and r["pos_in_box"] == 146.0 and r["data_points"] == 8
    assert kr.compute_darvas_box(_ohlc(list(range(1, 21))))["signal"] == "NO_BOX"
    mid = kr.compute_darvas_box(_ohlc([4, 4, 11, 15, 15, 15, 15, 15], highs, lows))
    assert mid["signal"] == "IN_BOX"
    down = kr.compute_darvas_box(_ohlc([4, 4, 11, 15, 15, 15, 15, 5], highs, lows))
    assert down["signal"] == "BREAKDOWN_SELL"


def test_darvas_bottom_fallback_and_none(kr):
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    closes = [4, 4, 11, 15, 15, 15, 15, 15]
    assert (
        kr.compute_darvas_box(_ohlc(closes, highs, [3, 3, 9, 9, 8, 7, 6, 6]))["box_bottom"] == 6.0
    )
    r0 = kr.compute_darvas_box(_ohlc(closes, highs, [3, 3, 0, 0, 0, 0, 0, 0]))
    assert r0["signal"] == "NO_BOX" and r0["box_bottom"] is None and r0["box_top"] == 20.0


def test_helpers(kr):
    class T:
        a = pd.DataFrame()
        b = "str"
        c = pd.DataFrame({"x": [1]})

    assert kr._first_df(T, "a", "b", "zz", "c") is T.c and kr._first_df(T, "a") is None
    df = pd.DataFrame({"y1": [1.0, np.nan], "y0": [2.0, 3.0]}, index=["A", "B"])
    assert kr._row(df, "A") == 1.0 and kr._row(df, "A", col=1) == 2.0
    assert kr._row(df, "B") is None and kr._row(df, "A", col=9) is None
    assert kr._row(df, "Z", "A") == 1.0 and kr._row(None, "A") is None
    assert kr._series(df, "B") == [3.0] and kr._series(df, "Z", "A") == [1.0, 2.0]
    assert kr._series(None, "A") == [] and kr._series(df, "Z") == []


# ---------------- fundamental_scan ----------------
def _cc_spec(**kw):
    spec = _good_spec(market_cap=2e11)
    spec.update(kw)
    FakeTicker.spec = spec
    return spec


def test_scan_pass_and_market_cap_boundary(kr):
    spec = _cc_spec()
    out = kr.fundamental_scan("005930", ".KS")
    assert out["yf_ticker"] == "005930.KS" and out["code"] == "005930"
    assert out["f_score"] == 9 and out["cc_qualifies"] == "PASS" and out["cc_score"] == "6/6"
    assert out["market_cap_b_krw"] == 2000.0  # 2e11 / 1e8
    assert out["cc_rev_cagr"] == pytest.approx(13.62, abs=0.01)
    assert out["cc_debt_equity"] == pytest.approx(0.17, abs=0.01)
    assert out["cc_roce_avg"] > 15
    spec["market_cap"] = 1e11
    assert kr.fundamental_scan("1", ".KQ")["cc_qualifies"] == "PASS"
    spec["market_cap"] = 1e11 - 1
    out = kr.fundamental_scan("1", ".KQ")
    assert out["cc_qualifies"] == "FAIL" and out["cc_score"] == "5/6"


def test_scan_fcf_fallback_variants(kr):
    spec = _cc_spec()
    cf = spec["cash_flow"].drop(index="Free Cash Flow")
    spec["cash_flow"] = spec["cashflow"] = cf
    assert kr.fundamental_scan("1", ".KS")["cc_score"] == "6/6"
    cf = cf.copy()
    cf.loc["Capital Expenditure"] = [-250e6] * 5
    spec["cash_flow"] = spec["cashflow"] = cf
    assert kr.fundamental_scan("1", ".KS")["cc_score"] == "5/6"
    spec["cash_flow"] = spec["cashflow"] = cf.drop(index="Capital Expenditure")
    assert kr.fundamental_scan("1", ".KS")["cc_score"] == "5/6"
    cf2 = _good_spec()["cash_flow"].copy()
    cf2.loc["Free Cash Flow"] = [-1e6, 130e6, 110e6, 90e6, 70e6]
    spec["cash_flow"] = spec["cashflow"] = cf2
    assert kr.fundamental_scan("1", ".KS")["cc_score"] == "5/6"


def test_scan_debt_cagr_and_profit_variants(kr):
    spec = _cc_spec()
    bal = spec["balance_sheet"].copy()
    bal.loc["Long Term Debt"] = [600e6, 150e6, 200e6, 250e6, 300e6]  # D/E exactly 1
    spec["balance_sheet"] = bal
    out = kr.fundamental_scan("1", ".KS")
    assert out["cc_debt_equity"] == 1.0 and out["cc_score"] == "5/6"
    bal2 = bal.copy()
    bal2.loc["Stockholders Equity"] = bal2.loc["Total Stockholder Equity"] = [0.0] * 5
    spec["balance_sheet"] = bal2
    assert kr.fundamental_scan("1", ".KS")["cc_debt_equity"] is None
    spec = _cc_spec()
    inc = spec["income_stmt"].copy()
    inc.loc["Net Income"] = [120e6, 100e6, -5e6, 60e6, 40e6]
    spec["income_stmt"] = spec["financials"] = inc
    assert kr.fundamental_scan("1", ".KS")["cc_score"] == "5/6"
    inc2 = inc.copy()
    inc2.loc["Total Revenue"] = [1000e6, np.nan, np.nan, np.nan, np.nan]
    spec["income_stmt"] = spec["financials"] = inc2
    out = kr.fundamental_scan("1", ".KS")
    assert out["f_strong"] is True and out["cc_rev_cagr"] is None and out["cc_score"] == "4/6"


def test_scan_fast_info_failure(kr):
    spec = _cc_spec()
    spec["market_cap"] = Exception
    out = kr.fundamental_scan("1", ".KS")
    assert out["market_cap_b_krw"] is None and out["cc_score"] == "5/6"


def test_scan_weak_piotroski(kr):
    spec = _cc_spec()
    inc = spec["income_stmt"].copy()
    inc.loc["Net Income"] = [-1e6, 100e6, 80e6, 60e6, 40e6]
    spec["income_stmt"] = spec["financials"] = inc
    bal = spec["balance_sheet"].copy()
    bal.loc["Share Issued"] = [110e6, 100e6, 100e6, 100e6, 100e6]
    spec["balance_sheet"] = bal
    out = kr.fundamental_scan("1", ".KS")
    assert out["f_score"] == 6 and out["f_strong"] is False and out["cc_score"] == "—"


def test_scan_threshold_boundary(kr, monkeypatch):
    _cc_spec()
    monkeypatch.setattr(kr, "PIOTROSKI_STRONG", 9)
    assert kr.fundamental_scan("1", ".KS")["f_strong"] is True
    monkeypatch.setattr(kr, "PIOTROSKI_STRONG", 10)
    assert kr.fundamental_scan("1", ".KS")["f_strong"] is False


def test_scan_missing_and_errors(kr, monkeypatch):
    FakeTicker.spec = {"info": {}}
    out = kr.fundamental_scan("1", ".KS")
    assert out["error"] == "no_financial_data" and out["f_score"] is None
    FakeTicker.spec = {"ctor_error": "y" * 200}
    assert kr.fundamental_scan("1", ".KS")["error"] == "y" * 80
    _cc_spec()
    monkeypatch.setattr(kr, "_row", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = kr.fundamental_scan("1", ".KS")
    assert out["f_score"] is None and out["cc_qualifies"] == "FAIL"


def test_scan_coffee_can_exception_swallowed(kr, monkeypatch):
    _cc_spec()
    real = kr._series

    def boom(df, *names):
        if names and names[0] == "Total Revenue":
            raise RuntimeError("x")
        return real(df, *names)

    monkeypatch.setattr(kr, "_series", boom)
    out = kr.fundamental_scan("1", ".KS")
    assert out["f_strong"] is True and out["cc_qualifies"] == "FAIL" and "cc_score" not in out


# ---------------- style_sheet ----------------
def test_style_sheet(kr, monkeypatch):
    monkeypatch.setattr(kr, "get_column_letter", lambda i: "ABCDE"[i - 1])
    ws = _WS([["Change%", "LTP_KRW", "Other", "Name"], [1.5, 2000.0, 3.0, "x"], [2, 3, 4, "yy"]])
    ws.column_dimensions = {k: _Dim() for k in "ABCDE"}
    kr.style_sheet(ws)
    assert ws.cell(2, 1).number_format == '0.00"%"'
    assert ws.cell(2, 2).number_format == "#,##0"
    assert ws.cell(2, 3).number_format == "0.00"
    assert ws.cell(2, 4).number_format == "General"
    assert ws.row_dimensions[1].height == 28 and ws.row_dimensions[3].height == 20
    assert ws.column_dimensions["A"].width == 12 and ws.column_dimensions["B"].width == 12


def test_style_sheet_noop_without_openpyxl(kr, monkeypatch):
    monkeypatch.setattr(kr, "OPENPYXL_OK", False)
    kr.style_sheet(None)


# ---------------- main ----------------
def _universe(codes, market="KOSPI", suffix=".KS"):
    return [
        {"code": c, "name": f"N{c}", "market": market, "yf_suffix": suffix, "sector": "S"}
        for c in codes
    ]


@pytest.fixture
def run(kr, monkeypatch):
    cap = {"sheets": {}, "styled": []}

    class FakeWriter:
        sheets = type("S", (dict,), {"__missing__": lambda self, k: k})()

        def __init__(self, path, engine=None):
            cap["path"], cap["engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def to_excel(self, writer, sheet_name=None, index=True):
        cap["sheets"][sheet_name] = self.copy()

    monkeypatch.setattr(kr.pd, "ExcelWriter", FakeWriter)
    monkeypatch.setattr(pd.DataFrame, "to_excel", to_excel)
    monkeypatch.setattr(kr, "style_sheet", lambda ws: cap["styled"].append(ws))

    def go(argv, universe, ohlc, fund=None):
        monkeypatch.setattr(sys, "argv", ["prog", *argv])

        def build(kospi_only=False):
            cap["kospi_only"] = kospi_only
            return universe

        monkeypatch.setattr(kr, "build_krx_universe", build)

        def fetch(uni, months):
            cap["fetched"], cap["months"] = [s["code"] for s in uni], months
            return ohlc

        monkeypatch.setattr(kr, "fetch_krx_ohlc", fetch)
        if fund:
            monkeypatch.setattr(kr, "fundamental_scan", fund)
        kr.main()
        return cap

    return go


def test_main_rows_top_and_flags(kr, run, capsys):
    ohlc = {
        "1": _ohlc(np.concatenate([np.full(200, 100.0), [111.0]])),
        "2": _ohlc(np.concatenate([np.full(200, 100.0), [89.0]])),
        "3": _ohlc(np.concatenate([np.full(200, 100.0), [102.0]])),
        "4": _ohlc(np.full(20, 50.0)),
        "5": _ohlc([10.0] * 10),
    }
    uni = _universe(["1", "2", "3"]) + _universe(["4", "5", "6"], "KOSDAQ", ".KQ")
    cap = run(["--no-scans", "--kospi-only", "--top", "5"], uni, ohlc)
    assert cap["kospi_only"] is True and cap["fetched"] == ["1", "2", "3", "4", "5"]
    assert cap["months"] == 3
    rows = {r["Code"]: r for r in cap["sheets"]["All_Stocks"].to_dict("records")}
    assert set(rows) == {"1", "2", "3", "4", "5"}  # code 6 absent from ohlc / cut by --top
    assert rows["1"]["Trend_Signal"] == "Above 200MA (Uptrend)"
    assert rows["2"]["Trend_Signal"] == "Below 200MA (Downtrend)"
    assert rows["3"]["Trend_Signal"] == "Near 200MA (Consolidation)"
    assert rows["4"]["Trend_Signal"] == "Need 200+ days"
    assert rows["4"]["YF_Ticker"] == "4.KQ" and rows["4"]["Market"] == "KOSDAQ"
    assert rows["1"]["LTP_KRW"] == 111.0 and rows["1"]["Name"] == "N1"
    out = capsys.readouterr().out
    assert "(limited to first 5 tickers)" in out and "Total: 5 tickers (3 KOSPI + 2 KOSDAQ)" in out
    assert cap["engine"] == "openpyxl" and cap["path"].name.startswith("korea_market_scan_")
    assert cap["sheets"]["Fundamentals"].empty and cap["sheets"]["Triple_Hits"].empty


def test_main_skips_universe_without_ohlc_and_defaults(kr, run):
    cap = run(["--no-scans"], _universe(["1"]), {})
    assert cap["sheets"]["All_Stocks"].empty and cap["kospi_only"] is False


def test_main_fundamentals_and_triple_hits(kr, run, capsys):
    def fund(code, sfx):
        assert sfx == ".KS"
        if code == "3":
            raise RuntimeError("crashed")
        if code == "1":
            return {
                "f_score": 8,
                "f_strong": True,
                "cc_qualifies": "PASS",
                "cc_score": "6/6",
                "market_cap_b_krw": 2000.0,
            }
        return {"f_score": 3, "error": "e"}

    ohlc = {
        "1": _breakout(),
        "2": _breakout(),
        "3": _breakout(),
        "4": _breakdown(),
        "5": _ohlc(np.full(13, 15.0)),
    }
    cap = run(["--workers", "2"], _universe(["1", "2", "3", "4", "5"]), ohlc, fund)
    assert sorted(cap["sheets"]["Darvas_Signals"]["Code"]) == ["1", "2", "3", "4"]
    f = cap["sheets"]["Fundamentals"]
    assert sorted(f["Code"]) == ["1", "2"]
    assert list(cap["sheets"]["Triple_Hits"]["Code"]) == ["1"]
    r1 = f[f["Code"] == "1"].iloc[0]
    assert r1["Piotroski_Strong"] == "YES" and r1["MCap_100M_KRW"] == 2000.0
    assert r1["YF_Ticker"] == "1.KS" and r1["Name"] == "N1"
    r2 = f[f["Code"] == "2"].iloc[0]
    assert r2["Piotroski_Strong"] == "NO" and r2["CoffeeCan"] == "FAIL" and r2["Error"] == "e"
    out = capsys.readouterr().out
    assert "Breakout BUY:   3" in out and "Breakdown SELL: 1" in out and "In Box:         1" in out
    assert "3: error — crashed" in out and "TRIPLE HITS:         1" in out
    assert "[1] N1" in out and "F=8/9" in out and "Market: KOSPI" in out


def test_main_no_scans_and_no_breakouts(kr, run, capsys):
    fail = lambda c, s: pytest.fail("scan must be skipped")  # noqa: E731
    run(["--no-scans"], _universe(["1"]), {"1": _breakout()}, fail)
    out = capsys.readouterr().out
    assert "Stage 4 — Skipped" in out and "No Triple Hit stocks found today." in out
    run([], _universe(["1"]), {"1": _ohlc(np.full(13, 15.0))}, fail)
    assert "Stage 4 — Skipped" in capsys.readouterr().out


def test_main_caps_candidates_to_freshest(kr, run, monkeypatch, capsys):
    monkeypatch.setattr(kr, "MAX_FUND_CANDIDATES", 2)
    scanned = []

    def mk(last):
        closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, last]
        highs = [5, 5, 10, 20, 18, 17, 16, 16, 16, 16, 16, 16, 16]
        lows = [3, 3, 12, 10, 11, 12, 13, 13, 13, 13, 13, 13, 13]
        return _ohlc(closes, highs, lows)

    ohlc = {"1": mk(100), "2": mk(21), "3": mk(40), "4": mk(22)}
    run([], _universe(["1", "2", "3", "4"]), ohlc, lambda c, s: scanned.append(c) or {"f_score": 1})
    assert sorted(scanned) == ["2", "4"]
    assert "(capped to 2 freshest breakouts)" in capsys.readouterr().out


def test_main_no_cap_at_exact_limit(kr, run, monkeypatch, capsys):
    monkeypatch.setattr(kr, "MAX_FUND_CANDIDATES", 2)
    scanned = []
    run(
        [],
        _universe(["1", "2"]),
        {"1": _breakout(), "2": _breakout()},
        lambda c, s: scanned.append(c) or {"f_score": 1},
    )
    assert sorted(scanned) == ["1", "2"] and "capped" not in capsys.readouterr().out


def test_main_progress_every_20(kr, run, capsys):
    codes = [str(i) for i in range(1, 22)]
    run([], _universe(codes), {c: _breakout() for c in codes}, lambda c, s: {"f_score": 1})
    out = capsys.readouterr().out
    assert "    20/21 done" in out and "    21/21 done" in out and "    10/21 done" not in out
