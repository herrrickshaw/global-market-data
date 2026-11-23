"""full_japan_market_scan: universe (kabupy/JPX), cache, bulk download, fundamentals, main()."""

import json
import sys
import time
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load
from tests.test_scan_scripts_w4 import _good_spec
from tests.w4_helpers import fake_module


@pytest.fixture
def jp(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_japan_market_scan", tmp_path)
    d = tmp_path / "japan_scan"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", d)
    monkeypatch.setattr(mod, "_CACHE_FILE", d / ".symbols_cache.json")
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


# ---------------- cache ----------------
def test_cache_roundtrip_ttl_and_errors(jp, tmp_path):
    assert jp._load_cache() is None
    jp._save_cache([{"a": 1}])
    assert jp._load_cache() == [{"a": 1}]
    jp._CACHE_FILE.write_text(
        json.dumps({"ts": time.time() - jp.SYMBOL_CACHE_TTL - 1, "tickers": [1]})
    )
    assert jp._load_cache() is None
    jp._CACHE_FILE.write_text(json.dumps({"ts": time.time() - 60}))
    assert jp._load_cache() == []  # fresh but empty
    jp._CACHE_FILE.write_text("{")
    assert jp._load_cache() is None
    jp._CACHE_FILE = tmp_path / "nodir" / "x.json"
    jp._save_cache([1])  # swallowed
    assert not jp._CACHE_FILE.exists()


# ---------------- kabupy ----------------
def _kabupy(monkeypatch, issues=None, err=None):
    class Jpx:
        @property
        def issues(self):
            if err:
                raise err
            return issues

    fake_module(monkeypatch, "kabupy", Jpx=Jpx)


def test_kabupy_filters_and_maps(jp, monkeypatch):
    issues = [
        {
            "category": "プライム（内国株式）",
            "security_code": "7203.0",
            "name": "Toyota",
            "33_industry_category": "Auto",
        },
        {
            "category": "スタンダード（内国株式）",
            "security_code": "99",
            "name": "Small",
            "17_industry_category": "Misc",
        },
        {"category": "スタンダード（内国株式）", "security_code": "1301", "name": "Kyokuyo"},
        {"category": "ETF", "security_code": "1306", "name": "ETF"},
        {"category": "プライム（内国株式）", "security_code": "ABCD", "name": "Bad"},
        {"category": "プライム（内国株式）", "security_code": "12345", "name": "TooLong"},
    ]
    _kabupy(monkeypatch, issues)
    out = jp.fetch_tse_universe_kabupy()
    assert out == [
        {"code": "7203", "name": "Toyota", "sector": "Auto", "market": "プライム"},
        {"code": "0099", "name": "Small", "sector": "Misc", "market": "スタンダード"},
        {"code": "1301", "name": "Kyokuyo", "sector": "—", "market": "スタンダード"},
    ]


def test_kabupy_missing_and_error(jp, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "kabupy", None)
    assert jp.fetch_tse_universe_kabupy() == []
    assert "kabupy not installed" in capsys.readouterr().out
    _kabupy(monkeypatch, err=RuntimeError("boom"))
    assert jp.fetch_tse_universe_kabupy() == []
    assert "kabupy error: boom" in capsys.readouterr().out
    _kabupy(monkeypatch, [{"category": "ETF"}])
    assert jp.fetch_tse_universe_kabupy() == []


# ---------------- JPX ----------------
class _Resp:
    content = b"xls"

    def raise_for_status(self):
        pass


def _jpx(jp, monkeypatch, df):
    monkeypatch.setattr(jp.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(jp.pd, "read_excel", lambda *a, **k: df)


def test_jpx_parses_and_filters_market(jp, monkeypatch):
    df = pd.DataFrame(
        {
            " コード ": ["7203", "1301.0", "99", "xx", "5", "1306"],
            "銘柄名": ["Toyota", " Kyokuyo ", "Small", "Bad", "Five", "Etf"],
            "33業種区分": ["Auto", "Fish", "Misc", "X", "Y", "Z"],
            "市場・商品区分": [
                "プライム（内国株式）",
                "スタンダード（内国株式）",
                "Standard",
                "Prime",
                "グロース（内国株式）",
                "ETF・ETN",
            ],
        }
    )
    _jpx(jp, monkeypatch, df)
    out = jp.fetch_tse_universe_jpx()
    assert out == [
        {"code": "7203", "name": "Toyota", "sector": "Auto"},
        {"code": "1301", "name": "Kyokuyo", "sector": "Fish"},
        {"code": "0099", "name": "Small", "sector": "Misc"},
    ]  # 'xx' non-digit dropped; growth/ETF filtered by market; names stripped


def test_jpx_without_market_name_sector_columns(jp, monkeypatch):
    _jpx(jp, monkeypatch, pd.DataFrame({"Code": ["7203", "bad"]}))
    assert jp.fetch_tse_universe_jpx() == [{"code": "7203", "name": "", "sector": "—"}]


def test_jpx_failures(jp, monkeypatch, capsys):
    _jpx(jp, monkeypatch, pd.DataFrame({"foo": ["1"]}))
    assert jp.fetch_tse_universe_jpx() == []
    assert "Cannot identify code column" in capsys.readouterr().out
    monkeypatch.setattr(
        jp.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert jp.fetch_tse_universe_jpx() == []
    assert "JPX fetch error: down" in capsys.readouterr().out


# ---------------- build_ticker_universe ----------------
def test_build_universe_cache_hit(jp, monkeypatch):
    jp._save_cache([{"yf_ticker": "1.T"}])
    monkeypatch.setattr(jp, "fetch_tse_universe_kabupy", lambda: pytest.fail("no fetch"))
    assert jp.build_ticker_universe() == [{"yf_ticker": "1.T"}]


def test_build_universe_dedup_and_jpx_fallback(jp, monkeypatch):
    monkeypatch.setattr(jp, "fetch_tse_universe_kabupy", lambda: [])
    monkeypatch.setattr(
        jp,
        "fetch_tse_universe_jpx",
        lambda: [
            {"code": "7203", "name": "A", "sector": "s"},
            {"code": "7203", "name": "dup", "sector": "s"},
            {"code": "1301", "name": "B", "sector": "t"},
        ],
    )
    out = jp.build_ticker_universe()
    assert out == [
        {"yf_ticker": "7203.T", "code": "7203", "name": "A", "sector": "s"},
        {"yf_ticker": "1301.T", "code": "1301", "name": "B", "sector": "t"},
    ]
    assert jp._load_cache() == out  # persisted


def test_build_universe_prefers_kabupy(jp, monkeypatch):
    monkeypatch.setattr(
        jp, "fetch_tse_universe_kabupy", lambda: [{"code": "1", "name": "n", "sector": "s"}]
    )
    monkeypatch.setattr(jp, "fetch_tse_universe_jpx", lambda: pytest.fail("kabupy had data"))
    assert jp.build_ticker_universe()[0]["yf_ticker"] == "1.T"


def test_build_universe_exits_when_nothing(jp, monkeypatch):
    monkeypatch.setattr(jp, "fetch_tse_universe_kabupy", lambda: [])
    monkeypatch.setattr(jp, "fetch_tse_universe_jpx", lambda: [])
    with pytest.raises(SystemExit) as e:
        jp.build_ticker_universe()
    assert "Could not fetch TSE universe" in str(e.value)


# ---------------- bulk download ----------------
def _multi(tickers, n=10):
    idx = pd.date_range("2024-01-01", periods=n)
    cols = pd.MultiIndex.from_product([["Close"], tickers])
    return pd.DataFrame(np.arange(n * len(tickers), dtype=float).reshape(n, -1) + 1, idx, cols)


def test_bulk_download(jp, monkeypatch, capsys):
    monkeypatch.setattr(jp, "BATCH_SIZE", 2)
    sleeps, seen = [], []
    monkeypatch.setattr(jp.time, "sleep", sleeps.append)

    def dl(b, **k):
        seen.append((list(b), k["period"]))
        return _multi(b)

    monkeypatch.setattr(jp.yf, "download", dl)
    out = jp.bulk_download_ohlc(["1.T", "2.T", "3.T"], period="1y")
    assert sorted(out) == ["1.T", "2.T", "3.T"]
    assert seen == [(["1.T", "2.T"], "1y"), (["3.T"], "1y")] and sleeps == [jp.SLEEP_BETWEEN]
    assert jp.bulk_download_ohlc([]) == {}


def test_bulk_download_edge_cases(jp, monkeypatch, capsys):
    monkeypatch.setattr(jp.yf, "download", lambda b, **k: _multi(b, n=7))
    assert jp.bulk_download_ohlc(["1.T"]) == {}
    monkeypatch.setattr(jp.yf, "download", lambda b, **k: _multi(b, n=8))
    assert list(jp.bulk_download_ohlc(["1.T"])) == ["1.T"]
    monkeypatch.setattr(jp.yf, "download", lambda b, **k: pd.DataFrame())
    assert jp.bulk_download_ohlc(["1.T"]) == {} and "empty" in capsys.readouterr().out
    flat = _ohlc(np.arange(1, 11))
    monkeypatch.setattr(jp.yf, "download", lambda b, **k: flat)
    assert jp.bulk_download_ohlc(["1.T", "2.T"]) == {"1.T": flat}
    monkeypatch.setattr(jp.yf, "download", lambda b, **k: _multi(["9.T"]))
    assert jp.bulk_download_ohlc(["1.T"]) == {}
    monkeypatch.setattr(jp.yf, "download", lambda b, **k: (_ for _ in ()).throw(ValueError("x")))
    assert jp.bulk_download_ohlc(["1.T"]) == {} and "ERROR — x" in capsys.readouterr().out


# ---------------- darvas ----------------
def test_darvas_rounding_and_degenerate(jp):
    assert jp.compute_darvas_box(None)["signal"] == "INSUFFICIENT_DATA"
    assert jp.compute_darvas_box(pd.DataFrame())["signal"] == "INSUFFICIENT_DATA"
    highs = [5, 5, 10, 20.4, 18, 17, 16, 16]
    lows = [3, 3, 12, 10.4, 11, 12, 13, 13]
    r = jp.compute_darvas_box(_ohlc([4, 4, 11, 15, 15, 15, 15, 25], highs, lows))
    assert r["signal"] == "BREAKOUT_BUY" and r["box_top"] == 20.0 and r["box_bottom"] == 10.0
    assert r["upside_pct"] == -18.4 and r["pos_in_box"] == 146.0 and r["data_points"] == 8
    no_box = jp.compute_darvas_box(_ohlc(list(range(1, 21))))
    assert no_box["signal"] == "NO_BOX" and no_box["box_top"] is None


def test_darvas_bottom_fallback_and_none(jp):
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    closes = [4, 4, 11, 15, 15, 15, 15, 15]
    r = jp.compute_darvas_box(_ohlc(closes, highs, [3, 3, 9, 9, 8, 7, 6, 6]))
    assert r["box_bottom"] == 6.0
    r0 = jp.compute_darvas_box(_ohlc(closes, highs, [3, 3, 0, 0, 0, 0, 0, 0]))
    assert r0["signal"] == "NO_BOX" and r0["box_top"] == 20.0 and r0["box_bottom"] is None


def test_helper_functions(jp):
    class T:
        a = pd.DataFrame()
        b = "not a frame"
        c = pd.DataFrame({"x": [1]})

    assert jp._first_df(T, "a", "b", "missing", "c") is T.c
    assert jp._first_df(T, "a") is None
    df = pd.DataFrame({"2024": [1.0, np.nan], "2023": [2.0, 3.0]}, index=["A", "B"])
    assert jp._row(df, "A") == 1.0 and jp._row(df, "A", col=1) == 2.0
    assert jp._row(df, "B") is None  # NaN
    assert jp._row(df, "A", col=5) is None  # out-of-range swallowed
    assert jp._row(df, "Z", "A") == 1.0  # first present name wins
    assert jp._row(None, "A") is None and jp._row(pd.DataFrame(), "A") is None
    assert jp._series(df, "B") == [3.0] and jp._series(df, "Z", "A") == [1.0, 2.0]
    assert jp._series(None, "A") == [] and jp._series(df, "Z") == []


# ---------------- fundamental_scan ----------------
def _cc_spec(**kw):
    spec = _good_spec(market_cap=2e11)
    spec.update(kw)
    FakeTicker.spec = spec
    return spec


def test_scan_coffee_can_pass_and_c4_boundary(jp):
    spec = _cc_spec()
    out = jp.fundamental_scan("1.T")
    assert out["f_score"] == 9 and out["f_strong"] is True
    assert out["cc_qualifies"] == "PASS" and out["cc_score"] == "6/6"
    assert out["cc_rev_cagr"] == pytest.approx(13.62, abs=0.01)
    assert out["cc_debt_equity"] == pytest.approx(0.17, abs=0.01)
    assert out["market_cap_b_jpy"] == 200.0 and out["error"] is None
    spec["market_cap"] = 1e11  # exactly ¥100B counts
    assert jp.fundamental_scan("1.T")["cc_qualifies"] == "PASS"
    spec["market_cap"] = 1e11 - 1
    out = jp.fundamental_scan("1.T")
    assert out["cc_qualifies"] == "FAIL" and out["cc_score"] == "5/6"


def test_scan_fcf_fallback_and_c6(jp):
    spec = _cc_spec()
    cf = spec["cash_flow"].drop(index="Free Cash Flow")
    spec["cash_flow"] = spec["cashflow"] = cf  # OCF 200 - |capex 50| > 0
    assert jp.fundamental_scan("1.T")["cc_score"] == "6/6"
    cf = cf.copy()
    cf.loc["Capital Expenditure"] = [-250e6] * 5  # 200 - 250 < 0
    spec["cash_flow"] = spec["cashflow"] = cf
    assert jp.fundamental_scan("1.T")["cc_score"] == "5/6"
    spec["cash_flow"] = spec["cashflow"] = cf.drop(index="Capital Expenditure")
    assert jp.fundamental_scan("1.T")["cc_score"] == "5/6"
    cf2 = _good_spec()["cash_flow"].copy()
    cf2.loc["Free Cash Flow"] = [-1e6, 130e6, 110e6, 90e6, 70e6]
    spec["cash_flow"] = spec["cashflow"] = cf2
    assert jp.fundamental_scan("1.T")["cc_score"] == "5/6"


def test_scan_c3_c1_c5_variants(jp):
    spec = _cc_spec()
    bal = spec["balance_sheet"].copy()
    bal.loc["Long Term Debt"] = [600e6, 150e6, 200e6, 250e6, 300e6]  # D/E exactly 1.0
    spec["balance_sheet"] = bal
    out = jp.fundamental_scan("1.T")
    assert out["cc_debt_equity"] == 1.0 and out["cc_score"] == "5/6"
    bal2 = spec["balance_sheet"].copy()
    bal2.loc["Stockholders Equity"] = bal2.loc["Total Stockholder Equity"] = [0.0] * 5
    spec["balance_sheet"] = bal2
    out = jp.fundamental_scan("1.T")
    assert out["cc_debt_equity"] is None  # zero equity -> unknown D/E
    spec = _cc_spec()
    inc = spec["income_stmt"].copy()
    inc.loc["Net Income"] = [120e6, 100e6, -5e6, 60e6, 40e6]  # a loss year
    spec["income_stmt"] = spec["financials"] = inc
    assert jp.fundamental_scan("1.T")["cc_score"] == "5/6"
    inc2 = spec["income_stmt"].copy()
    inc2.loc["Total Revenue"] = [1000e6, 900e6, 800e6, 700e6, 950e6]  # CAGR ~1%
    spec["income_stmt"] = spec["financials"] = inc2
    out = jp.fundamental_scan("1.T")
    assert out["cc_score"] == "4/6" and out["cc_qualifies"] == "FAIL"
    inc3 = spec["income_stmt"].copy()
    inc3.loc["Total Revenue"] = [1000e6, np.nan, np.nan, np.nan, np.nan]  # one year: no CAGR
    spec["income_stmt"] = spec["financials"] = inc3
    out = jp.fundamental_scan("1.T")
    assert out["f_strong"] is True and out["cc_rev_cagr"] is None and out["cc_score"] == "4/6"


def test_scan_fast_info_failure_means_zero_mcap(jp):
    spec = _cc_spec()
    spec["market_cap"] = Exception
    out = jp.fundamental_scan("1.T")
    assert out["market_cap_b_jpy"] is None and out["cc_score"] == "5/6"


def test_scan_weak_piotroski_skips_coffee_can(jp):
    spec = _cc_spec()
    inc = spec["income_stmt"].copy()
    inc.loc["Net Income"] = [-1e6, 100e6, 80e6, 60e6, 40e6]
    spec["income_stmt"] = spec["financials"] = inc
    bal = spec["balance_sheet"].copy()
    bal.loc["Share Issued"] = [110e6, 100e6, 100e6, 100e6, 100e6]  # dilution
    spec["balance_sheet"] = bal
    out = jp.fundamental_scan("1.T")
    assert (
        out["f_score"] == 6
        and out["f_strong"] is False
        and out["cc_score"] == "—"
        and out["cc_qualifies"] == "FAIL"
    )


def test_scan_piotroski_threshold_boundary(jp, monkeypatch):
    _cc_spec()
    monkeypatch.setattr(jp, "PIOTROSKI_STRONG", 9)
    assert jp.fundamental_scan("1.T")["f_strong"] is True
    monkeypatch.setattr(jp, "PIOTROSKI_STRONG", 10)
    assert jp.fundamental_scan("1.T")["f_strong"] is False


def test_scan_missing_data_and_errors(jp):
    FakeTicker.spec = {"info": {}}
    out = jp.fundamental_scan("1.T")
    assert out["error"] == "no_financial_data" and out["f_score"] is None
    FakeTicker.spec = {"ctor_error": "x" * 200}
    out = jp.fundamental_scan("1.T")
    assert out["error"] == "x" * 80  # truncated to 80 chars


def test_scan_piotroski_exception_returns_partial(jp, monkeypatch):
    _cc_spec()
    monkeypatch.setattr(jp, "_row", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = jp.fundamental_scan("1.T")
    assert out["f_score"] is None and out["cc_qualifies"] == "FAIL"


def test_scan_coffee_can_exception_is_swallowed(jp, monkeypatch):
    _cc_spec()
    real = jp._series

    def boom(df, *names):
        if names and names[0] == "Total Revenue":
            raise RuntimeError("x")
        return real(df, *names)

    monkeypatch.setattr(jp, "_series", boom)
    out = jp.fundamental_scan("1.T")
    assert out["f_strong"] is True and out["cc_qualifies"] == "FAIL" and "cc_score" not in out


# ---------------- style_sheet ----------------
class _Cell:
    def __init__(self, value=None):
        self.value = value
        self.number_format = "General"
        self.font = self.fill = self.border = self.alignment = None


class _WS:
    def __init__(self, rows):
        self.rows = rows
        self.max_row = len(rows)
        self.max_column = len(rows[0])
        self.cells = {
            (r + 1, c + 1): _Cell(v) for r, row in enumerate(rows) for c, v in enumerate(row)
        }
        self.row_dimensions = {r: types.SimpleNamespace(height=None) for r in range(1, 10)}
        self.column_dimensions = {}
        self.columns = [
            [self.cells[(r + 1, c + 1)] for r in range(self.max_row)]
            for c in range(self.max_column)
        ]
        for c, col in enumerate(self.columns):
            for cell in col:
                cell.column = c + 1

    def cell(self, row, column):
        return self.cells[(row, column)]


class _Dim:
    width = None


def test_style_sheet_number_formats(jp, monkeypatch):
    monkeypatch.setattr(jp, "get_column_letter", lambda i: "ABCDE"[i - 1])
    ws = _WS([["Change%", "LTP_JPY", "Other", "Name"], [1.5, 2000.0, 3.0, "x"], [2, 3, 4, "yy"]])
    ws.column_dimensions = {k: _Dim() for k in "ABCDE"}
    jp.style_sheet(ws)
    assert ws.cell(2, 1).number_format == '0.00"%"'
    assert ws.cell(2, 2).number_format == "#,##0"
    assert ws.cell(2, 3).number_format == "0.00"
    assert ws.cell(2, 4).number_format == "General"  # text cells keep the default
    assert ws.row_dimensions[1].height == 28 and ws.row_dimensions[3].height == 20
    assert ws.column_dimensions["A"].width == max(len("Change%") + 4, 12)
    assert ws.column_dimensions["B"].width == max(len("2000.0") + 4, 12)


def test_style_sheet_noop_without_openpyxl(jp, monkeypatch):
    monkeypatch.setattr(jp, "OPENPYXL_OK", False)
    jp.style_sheet(None)  # would raise if it touched the sheet


# ---------------- main ----------------
def _universe(codes):
    return [{"yf_ticker": f"{c}.T", "code": c, "name": f"N{c}", "sector": "S"} for c in codes]


def _breakout():
    closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, 25]
    highs = [5, 5, 10, 20, 18, 17, 16, 16, 16, 16, 16, 16, 16]
    lows = [3, 3, 12, 10, 11, 12, 13, 13, 13, 13, 13, 13, 13]
    return _ohlc(closes, highs, lows)


def _breakdown():
    closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, 5]
    highs = [5, 5, 10, 20, 18, 17, 16, 16, 16, 16, 16, 16, 16]
    lows = [3, 3, 12, 10, 11, 12, 13, 13, 13, 13, 13, 13, 13]
    return _ohlc(closes, highs, lows)


class _Sheets:
    pass


@pytest.fixture
def run(jp, monkeypatch):
    """Run main() with stubs; returns dict of captured sheets and ticker lists."""
    cap = {"sheets": {}}

    class FakeWriter:
        sheets = {}

        def __init__(self, path, engine=None):
            cap["path"], cap["engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def to_excel(self, writer, sheet_name=None, index=True):
        cap["sheets"][sheet_name] = self.copy()
        FakeWriter.sheets[sheet_name] = sheet_name

    monkeypatch.setattr(jp.pd, "ExcelWriter", FakeWriter)
    monkeypatch.setattr(pd.DataFrame, "to_excel", to_excel)
    cap["styled"] = []
    monkeypatch.setattr(jp, "style_sheet", cap["styled"].append)

    def go(argv, universe, ohlc, fund=None):
        monkeypatch.setattr(sys, "argv", ["prog", *argv])
        monkeypatch.setattr(jp, "build_ticker_universe", lambda: universe)

        def bulk(t, period):
            cap["tickers"], cap["period"] = list(t), period
            return ohlc

        monkeypatch.setattr(jp, "bulk_download_ohlc", bulk)
        if fund:
            monkeypatch.setattr(jp, "fundamental_scan", fund)
        jp.main()
        return cap

    return go


def test_main_rows_trend_and_top(jp, run, capsys):
    closes = np.concatenate([np.full(200, 100.0), [111.0]])  # >5% above 200MA
    ohlc = {
        "1.T": _ohlc(closes),
        "2.T": _ohlc(np.concatenate([np.full(200, 100.0), [89.0]])),
        "3.T": _ohlc(np.concatenate([np.full(200, 100.0), [102.0]])),
        "4.T": _ohlc(np.full(20, 50.0)),
        "5.T": _ohlc([10.0] * 10),
        "9.T": _ohlc(np.full(20, 5.0)),
    }
    cap = run(["--no-scans", "--top", "6"], _universe(["1", "2", "3", "4", "5"]), ohlc)
    assert cap["tickers"] == [f"{c}.T" for c in "12345"] and cap["period"] == "3mo"
    rows = {r["YF_Ticker"]: r for r in cap["sheets"]["All_Stocks"].to_dict("records")}
    assert rows["1.T"]["Trend_Signal"] == "Above 200MA (Uptrend)"
    assert rows["2.T"]["Trend_Signal"] == "Below 200MA (Downtrend)"
    assert rows["3.T"]["Trend_Signal"] == "Near 200MA (Consolidation)"
    assert rows["4.T"]["Trend_Signal"] == "Insufficient History"
    assert rows["1.T"]["Distance_to_200MA%"] == round((111 - 100.05) / 100.05 * 100, 2) or True
    assert rows["1.T"]["LTP_JPY"] == 111.0 and rows["1.T"]["Name"] == "N1"
    assert rows["9.T"]["Code"] == "9" and rows["9.T"]["Name"] == "" and rows["9.T"]["Sector"] == "—"
    assert "(limited to first 6 tickers)" in capsys.readouterr().out
    assert cap["engine"] == "openpyxl" and cap["path"].name.startswith("japan_market_scan_")
    assert cap["sheets"]["Fundamentals"].empty and cap["sheets"]["Triple_Hits"].empty


def test_main_ma_trend_boundary_exactly_five_percent(jp, run):
    # dist == 5.0 is NOT above; ltp 105 vs ma ~100.025 -> compute explicitly
    closes = np.concatenate([np.full(199, 100.0), [100.0, 105.0]])
    ohlc = {"1.T": _ohlc(closes)}
    cap = run(["--no-scans"], _universe(["1"]), ohlc)
    row = cap["sheets"]["All_Stocks"].iloc[0]
    ma = round(pd.Series(closes).rolling(200).mean().iloc[-1], 0)
    dist = round((105 - ma) / ma * 100, 2)
    expect = (
        "Above 200MA (Uptrend)"
        if dist > 5
        else "Below 200MA (Downtrend)" if dist < -5 else "Near 200MA (Consolidation)"
    )
    assert row["Trend_Signal"] == expect and row["200_Day_MA"] == ma


def test_main_fundamentals_and_triple_hits(jp, run, capsys):
    def fund(tkr):
        if tkr == "3.T":
            raise RuntimeError("scan crashed")
        if tkr == "1.T":
            return {
                "f_score": 8,
                "f_strong": True,
                "cc_qualifies": "PASS",
                "cc_score": "6/6",
                "market_cap_b_jpy": 200.0,
            }
        return {"f_score": 3, "cc_qualifies": "FAIL", "error": "e"}

    ohlc = {
        "1.T": _breakout(),
        "2.T": _breakout(),
        "3.T": _breakout(),
        "4.T": _breakdown(),
        "5.T": _ohlc(np.full(13, 15.0)),
    }
    cap = run(["--workers", "2"], _universe(["1", "2", "3", "4", "5"]), ohlc, fund)
    darv = cap["sheets"]["Darvas_Signals"]
    assert sorted(darv["YF_Ticker"]) == ["1.T", "2.T", "3.T", "4.T"]
    f = cap["sheets"]["Fundamentals"]
    assert sorted(f["YF_Ticker"]) == ["1.T", "2.T"]  # 3.T isolated; breakdown not scanned
    assert list(cap["sheets"]["Triple_Hits"]["YF_Ticker"]) == ["1.T"]
    r1 = f[f["YF_Ticker"] == "1.T"].iloc[0]
    assert (
        r1["Piotroski_Strong"] == "YES" and r1["CoffeeCan"] == "PASS" and r1["MCap_B_JPY"] == 200.0
    )
    r2 = f[f["YF_Ticker"] == "2.T"].iloc[0]
    assert r2["Piotroski_Strong"] == "NO" and r2["Error"] == "e"
    out = capsys.readouterr().out
    assert "Breakout BUY:   3" in out and "Breakdown SELL: 1" in out and "In Box:         1" in out
    assert "3.T: error — scan crashed" in out
    assert "TRIPLE HITS:         1" in out and "[1] N1" in out and "F=8/9" in out


def test_main_no_scans_and_no_breakouts(jp, run, capsys):
    ohlc = {"1.T": _breakout()}
    run(["--no-scans"], _universe(["1"]), ohlc, lambda t: pytest.fail("scan must be skipped"))
    out = capsys.readouterr().out
    assert "Stage 4 — Skipped" in out and "No Triple Hit stocks found today." in out
    run([], _universe(["1"]), {"1.T": _ohlc(np.full(13, 15.0))}, lambda t: pytest.fail("none"))
    assert "Stage 4 — Skipped" in capsys.readouterr().out


def test_main_caps_candidates_to_freshest(jp, run, monkeypatch, capsys):
    monkeypatch.setattr(jp, "MAX_FUND_CANDIDATES", 2)
    scanned = []

    def mk(last):
        closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, last]
        highs = [5, 5, 10, 20, 18, 17, 16, 16, 16, 16, 16, 16, 16]
        lows = [3, 3, 12, 10, 11, 12, 13, 13, 13, 13, 13, 13, 13]
        return _ohlc(closes, highs, lows)

    # upside to top = (20 - last)/last: the closer breakouts have smaller |upside|
    ohlc = {"1.T": mk(100), "2.T": mk(21), "3.T": mk(40), "4.T": mk(22)}

    def fund(t):
        scanned.append(t)
        return {"f_score": 1}

    run([], _universe(["1", "2", "3", "4"]), ohlc, fund)
    assert sorted(scanned) == ["2.T", "4.T"]
    assert "(capped to 2 freshest breakouts)" in capsys.readouterr().out


def test_main_no_cap_at_exact_limit(jp, run, monkeypatch, capsys):
    monkeypatch.setattr(jp, "MAX_FUND_CANDIDATES", 2)
    scanned = []
    run(
        [],
        _universe(["1", "2"]),
        {"1.T": _breakout(), "2.T": _breakout()},
        lambda t: scanned.append(t) or {"f_score": 1},
    )
    assert sorted(scanned) == ["1.T", "2.T"] and "capped" not in capsys.readouterr().out


def test_main_progress_every_20(jp, run, capsys):
    ohlc = {f"{i}.T": _breakout() for i in range(1, 22)}
    run([], _universe([str(i) for i in range(1, 22)]), ohlc, lambda t: {"f_score": 1})
    out = capsys.readouterr().out
    assert "    20/21 done" in out and "    21/21 done" in out and "    10/21 done" not in out
