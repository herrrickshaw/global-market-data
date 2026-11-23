"""full_european_market_scan: Darvas details, fundamental/valuation scan, styling, main()."""

import sys
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load
from tests.test_full_japan_market_scan_r2 import _WS, _breakdown, _breakout, _Dim, _ohlc
from tests.test_scan_scripts_w4 import _good_spec


@pytest.fixture
def eu(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_european_market_scan", tmp_path)
    d = tmp_path / "european_scan"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", d)
    return mod


# ---------------- helpers ----------------
def test_helpers(eu):
    class T:
        a = pd.DataFrame()
        b = "str"
        c = pd.DataFrame({"x": [1]})

    assert eu._first_df(T, "a", "b", "zz", "c") is T.c and eu._first_df(T, "a") is None
    df = pd.DataFrame({"y1": [1.0, np.nan], "y0": [2.0, 3.0]}, index=["A", "B"])
    assert eu._row(df, "A") == 1.0 and eu._row(df, "A", col=1) == 2.0
    assert eu._row(df, "B") is None and eu._row(df, "A", col=9) is None
    assert eu._row(df, "Z", "A") == 1.0 and eu._row(None, "A") is None
    assert eu._row(pd.DataFrame(), "A") is None
    assert eu._series(df, "B") == [3.0] and eu._series(df, "Z", "A") == [1.0, 2.0]
    assert eu._series(None, "A") == [] and eu._series(df, "Z") == []


def test_meta_table_is_consistent(eu):
    assert len(eu.EURO_STOXX_50_META) == 50
    assert all(len(v) == 2 and all(v) for v in eu.EURO_STOXX_50_META.values())


# ---------------- darvas ----------------
def test_darvas_degenerate_and_rounding(eu):
    assert eu.compute_darvas_box(None)["signal"] == "INSUFFICIENT_DATA"
    assert eu.compute_darvas_box(pd.DataFrame())["signal"] == "INSUFFICIENT_DATA"
    assert eu.compute_darvas_box(_ohlc([1.0] * 7, [1.0] * 7, [1.0] * 7))["signal"] == (
        "INSUFFICIENT_DATA"
    )
    highs = [5, 5, 10, 20.456, 18, 17, 16, 16]
    lows = [3, 3, 12, 10.123, 11, 12, 13, 13]
    r = eu.compute_darvas_box(_ohlc([4, 4, 11, 15, 15, 15, 15, 25], highs, lows))
    assert r["signal"] == "BREAKOUT_BUY" and r["box_top"] == 20.46 and r["box_bottom"] == 10.12
    assert r["current_price"] == 25.0 and r["upside_pct"] == -18.18 and r["pos_in_box"] == 144.0
    assert eu.compute_darvas_box(_ohlc(list(range(1, 21))))["signal"] == "NO_BOX"


def test_darvas_bottom_fallback_and_none(eu):
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    closes = [4, 4, 11, 15, 15, 15, 15, 15]
    assert eu.compute_darvas_box(_ohlc(closes, highs, [3, 3, 9, 9, 8, 7, 6, 6]))["box_bottom"] == 6
    r0 = eu.compute_darvas_box(_ohlc(closes, highs, [3, 3, 0, 0, 0, 0, 0, 0]))
    assert r0 == {"signal": "NO_BOX", "box_top": 20.0, "box_bottom": None}


def test_darvas_zero_range_and_zero_price(eu):
    # flat box: top == bottom -> position 0 (no division by zero)
    highs = [5, 5, 10, 10, 9, 9, 9, 9]
    lows = [10, 10, 10, 10, 10, 10, 10, 10]  # bottom 10 == top 10
    r = eu.compute_darvas_box(_ohlc([10] * 8, highs, lows))
    assert r["box_top"] == 10.0 and r["box_bottom"] == 10.0 and r["pos_in_box"] == 0
    # closing price zero -> upside 0, breakdown
    r = eu.compute_darvas_box(
        _ohlc(
            [4, 4, 11, 15, 15, 15, 15, 0],
            [5, 5, 10, 20, 18, 17, 16, 16],
            [3, 3, 12, 10, 11, 12, 13, 13],
        )
    )
    assert r["signal"] == "BREAKDOWN_SELL" and r["upside_pct"] == 0


# ---------------- fundamental scan ----------------
def _spec(**info):
    spec = _good_spec()
    spec["info"].update(info)
    FakeTicker.spec = spec
    return spec


def test_scan_full_pass_and_fields(eu):
    spec = _spec(
        marketCap=2e9, trailingPE=12.345, priceToBook=1.234, pegRatio=1.5, dividendYield=0.03
    )
    out = eu.fundamental_scan("ADS.DE")
    assert out["f_score"] == 9 and out["cc_qualifies"] == "PASS" and out["cc_score"] == "6/6"
    assert out["cc_rev_cagr"] == pytest.approx(13.62, abs=0.01)
    assert out["cc_debt_equity"] == 0.4  # info D/E 40 -> percentage -> 0.4
    assert out["market_cap_b"] == 2.0 and out["pe_ratio"] == 12.34 or out["pe_ratio"] == 12.35
    assert out["pb_ratio"] == 1.23 and out["dividend_yield"] == 3.0
    assert out["pegy_ratio"] == 4.5 and out["error"] is None
    assert spec["info"]["marketCap"] == 2e9


def test_scan_market_cap_boundary_and_fast_info(eu):
    _spec(marketCap=1e9)
    assert eu.fundamental_scan("X")["cc_score"] == "6/6"  # exactly 1B counts
    _spec(marketCap=1e9 - 1)
    assert eu.fundamental_scan("X")["cc_score"] == "5/6"
    spec = _spec()
    spec["info"].pop("marketCap")
    spec["market_cap"] = 3e9  # falls back to fast_info
    out = eu.fundamental_scan("X")
    assert out["cc_score"] == "6/6" and out["market_cap_b"] == 3.0


def test_scan_debt_to_equity_branches(eu):
    spec = _spec(debtToEquity=100.0)  # >10 -> /100 = 1.0 not < 1
    assert eu.fundamental_scan("X")["cc_score"] == "5/6"
    spec["info"]["debtToEquity"] = 10.0  # <= 10 is a plain ratio of 10x
    out = eu.fundamental_scan("X")
    assert out["cc_score"] == "5/6" and out["cc_debt_equity"] == 10.0
    spec["info"]["debtToEquity"] = 0.99
    assert eu.fundamental_scan("X")["cc_score"] == "6/6"
    del spec["info"]["debtToEquity"]  # balance-sheet fallback: 100/600
    out = eu.fundamental_scan("X")
    assert out["cc_score"] == "6/6" and out["cc_debt_equity"] == 0.17
    bal = spec["balance_sheet"].copy()
    bal.loc["Stockholders Equity"] = bal.loc["Total Stockholder Equity"] = [0.0] * 5
    spec["balance_sheet"] = bal
    out = eu.fundamental_scan("X")
    assert out["cc_debt_equity"] is None and out["cc_score"] == "5/6"


def test_scan_fcf_fallback_and_profit_variants(eu):
    spec = _spec()
    cf = spec["cash_flow"].drop(index="Free Cash Flow")
    spec["cash_flow"] = spec["cashflow"] = cf
    assert eu.fundamental_scan("X")["cc_score"] == "6/6"
    cf = cf.copy()
    cf.loc["Capital Expenditure"] = [-250e6] * 5
    spec["cash_flow"] = spec["cashflow"] = cf
    assert eu.fundamental_scan("X")["cc_score"] == "5/6"
    spec["cash_flow"] = spec["cashflow"] = cf.drop(index="Capital Expenditure")
    assert eu.fundamental_scan("X")["cc_score"] == "5/6"
    spec = _spec()
    inc = spec["income_stmt"].copy()
    inc.loc["Net Income"] = [120e6, 100e6, -5e6, 60e6, 40e6]
    spec["income_stmt"] = spec["financials"] = inc
    assert eu.fundamental_scan("X")["cc_score"] == "5/6"
    inc.loc["Total Revenue"] = [1000e6, np.nan, np.nan, np.nan, np.nan]
    out = eu.fundamental_scan("X")
    assert out["cc_rev_cagr"] is None and out["cc_score"] == "4/6"


def test_scan_valuation_variants(eu):
    spec = _spec()
    spec["info"].pop("trailingPE")
    spec["info"]["forwardPE"] = 9.0
    assert eu.fundamental_scan("X")["pe_ratio"] == 9.0
    spec["info"].pop("forwardPE")
    spec["info"].update(regularMarketPrice=50.0, trailingEps=5.0)
    assert eu.fundamental_scan("X")["pe_ratio"] == 10.0  # price / eps fallback
    spec["info"].pop("trailingEps")
    assert eu.fundamental_scan("X")["pe_ratio"] == 50.0  # eps missing -> divides by 1
    spec["info"].pop("regularMarketPrice")
    out = eu.fundamental_scan("X")
    assert out["pe_ratio"] is None and out["pb_ratio"] is None  # no usable PE / PB
    assert out["dividend_yield"] == 0.0 and out["pegy_ratio"] is None
    spec["info"]["trailingPE"] = -5.0
    assert eu.fundamental_scan("X")["pe_ratio"] is None  # negative PE dropped


def test_scan_dividend_yield_units_and_pegy(eu):
    spec = _spec(pegRatio=0.0)
    for raw, expect in [(0.03, 3.0), (0.149, 14.9), (0.15, 0.15), (5.0, 5.0), (0.0, 0.0)]:
        spec["info"]["dividendYield"] = raw
        assert eu.fundamental_scan("X")["dividend_yield"] == expect, raw
    spec["info"]["dividendYield"] = 0.02
    out = eu.fundamental_scan("X")
    assert out["pegy_ratio"] == 2.0  # 0.0 + 2.0
    spec["info"]["pegRatio"] = 1.25
    assert eu.fundamental_scan("X")["pegy_ratio"] == 3.25
    spec["info"]["pegRatio"] = 0.0
    spec["info"]["dividendYield"] = 0.0
    assert eu.fundamental_scan("X")["pegy_ratio"] is None  # zero total is reported as None


def test_scan_no_statements_and_errors(eu):
    FakeTicker.spec = {"info": {}}
    out = eu.fundamental_scan("X")
    assert out["error"] == "no_financial_statements" and out["f_score"] is None
    FakeTicker.spec = {"ctor_error": "z" * 200}
    assert eu.fundamental_scan("X")["error"] == "z" * 80
    spec = _spec()
    spec["info"] = Exception  # .info raises -> treated as empty
    out = eu.fundamental_scan("X")
    assert out["error"] is None and out["f_score"] == 9 and out["pe_ratio"] is None


def test_scan_section_failures_are_isolated(eu, monkeypatch):
    _spec()
    monkeypatch.setattr(eu, "_row", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = eu.fundamental_scan("X")
    assert out["f_score"] is None and out["cc_qualifies"] == "PASS"  # CC/valuation still ran
    monkeypatch.undo()


def test_scan_coffee_can_failure_isolated(eu, monkeypatch):
    _spec()

    def boom(df, *names):
        raise RuntimeError("x")

    monkeypatch.setattr(eu, "_series", boom)
    out = eu.fundamental_scan("X")
    assert out["f_score"] == 9 and out["cc_qualifies"] == "FAIL" and "cc_score" not in out
    assert "pe_ratio" not in out  # mcap undefined -> valuation block also bails out


def test_scan_piotroski_partial_data_defaults(eu):
    spec = _spec()
    bal = spec["balance_sheet"].drop(index="Share Issued")
    spec["balance_sheet"] = bal
    assert eu.fundamental_scan("X")["f_score"] == 9  # unknown share count scores 1


# ---------------- style_excel_sheet ----------------
class _WSV(_WS):
    def __init__(self, rows):
        super().__init__(rows)
        self.views = types.SimpleNamespace(sheetView=[types.SimpleNamespace(showGridLines=False)])


def test_style_excel_sheet_formats(eu, monkeypatch):
    monkeypatch.setattr(eu, "get_column_letter", lambda i: "ABCDEFGHIJ"[i - 1])
    hdr = ["Change%", "LTP", "PEGY", "P_E_Ratio", "Symbol", "Market_Cap_B", "Other", "Box_Top"]
    ws = _WSV([hdr, [1.5, 20.0, 1.2, 12.0, "ADS.DE", 3.0, 7.0, 5.0], [2, 3, 4, 5, "B", 6, 8, 9]])
    ws.column_dimensions = {k: _Dim() for k in "ABCDEFGHIJ"}
    eu.style_excel_sheet(ws)
    fmt = [ws.cell(2, c).number_format for c in range(1, 9)]
    assert fmt[:3] == ['0.00"%"', "#,##0.00", "0.00"]
    assert fmt[4:] == ["General", "#,##0.00", "General", "#,##0.00"]
    assert ws.row_dimensions[1].height == 28 and ws.row_dimensions[3].height == 20
    assert ws.column_dimensions["A"].width == 12
    assert ws.column_dimensions["E"].width == max(len("ADS.DE") + 4, 12)
    assert ws.views.sheetView[0].showGridLines is True


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="full_european_market_scan.py:~395 matches 'PE_RATIO'/'PB_RATIO' against upper-cased "
    "headers, but main() writes 'P_E_Ratio'/'P_B_Ratio', so those columns never get 0.00",
)
def test_style_excel_sheet_formats_pe_pb_columns(eu, monkeypatch):
    monkeypatch.setattr(eu, "get_column_letter", lambda i: "ABCDEFGHIJ"[i - 1])
    ws = _WSV([["P_E_Ratio", "P_B_Ratio"], [12.0, 1.5], [3.0, 2.5]])
    ws.column_dimensions = {k: _Dim() for k in "ABCDEFGHIJ"}
    eu.style_excel_sheet(ws)
    assert [ws.cell(2, 1).number_format, ws.cell(2, 2).number_format] == ["0.00", "0.00"]


def test_style_excel_sheet_noop_without_openpyxl(eu, monkeypatch):
    monkeypatch.setattr(eu, "OPENPYXL_OK", False)
    eu.style_excel_sheet(None)


# ---------------- main ----------------
def _raw(frames):
    """group_by='ticker' MultiIndex frame {symbol: ohlc df}."""
    parts = {sym: df.rename(columns=str) for sym, df in frames.items()}
    return pd.concat(parts, axis=1)


@pytest.fixture
def run(eu, monkeypatch):
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

    monkeypatch.setattr(eu.pd, "ExcelWriter", FakeWriter)
    monkeypatch.setattr(pd.DataFrame, "to_excel", to_excel)
    monkeypatch.setattr(eu, "style_excel_sheet", cap["styled"].append)

    def go(argv, raw, fund=None):
        monkeypatch.setattr(sys, "argv", ["prog", *argv])

        def dl(symbols, **kw):
            cap["symbols"], cap["kw"] = list(symbols), kw
            if isinstance(raw, Exception):
                raise raw
            return raw

        monkeypatch.setattr(eu.yf, "download", dl)
        if fund:
            monkeypatch.setattr(eu, "fundamental_scan", fund)
        eu.main()
        return cap

    return go


def _long(df):
    pad = _ohlc([4.0] * 12, [5.0] * 12, [3.0] * 12)
    pad.index = pd.date_range("2023-12-01", periods=12)
    return pd.concat([pad, df])


def _flat(n, price):
    return _ohlc(np.full(n, float(price)))


def test_main_top_symbols_download_args_and_rows(eu, run, capsys):
    syms = list(eu.EURO_STOXX_50_META)
    raw = _raw(
        {
            syms[0]: _ohlc(np.concatenate([np.full(200, 100.0), [111.0]])),  # uptrend
            syms[1]: _ohlc(np.concatenate([np.full(200, 100.0), [89.0]])),  # downtrend
            syms[2]: _ohlc(np.concatenate([np.full(200, 100.0), [102.0]])),  # consolidation
            syms[3]: _flat(30, 50),  # < 200 days
            syms[4]: _flat(19, 50),  # too short: skipped
        }
    )
    cap = run(["--top", "5", "--no-scans"], raw)
    assert cap["symbols"] == syms[:5]
    assert cap["kw"] == {
        "period": "1y",
        "group_by": "ticker",
        "auto_adjust": True,
        "threads": True,
        "progress": False,
    }
    rows = {r["Symbol"]: r for r in cap["sheets"]["All_Stocks"].to_dict("records")}
    assert set(rows) == set(syms[:4])
    assert rows[syms[0]]["Trend_Signal"] == "Above 200MA (Uptrend)"
    assert rows[syms[1]]["Trend_Signal"] == "Below 200MA (Downtrend)"
    assert rows[syms[2]]["Trend_Signal"] == "Near 200MA (Consolidation)"
    assert rows[syms[3]]["Trend_Signal"] == "Insufficient History"
    assert rows[syms[0]]["Name"] == "Adidas" and rows[syms[0]]["Sector"] == "Consumer Cyclical"
    assert rows[syms[0]]["LTP"] == 111.0 and rows[syms[0]]["200_Day_MA"] is not None
    assert rows[syms[3]]["Dividend_Yield%"] == 0.0
    assert cap["engine"] == "openpyxl" and cap["path"].name.startswith("european_market_scan_")
    assert len(cap["styled"]) == 1  # only All_Stocks has content styled
    out = capsys.readouterr().out
    assert "for 5 stocks" in out and "Processed 4 stocks. Found 0 Darvas breakouts." in out
    assert "Stage 4 — Skipped" in out and "No Triple Hit stocks found today." in out


def test_main_all_symbols_default_and_download_failure(eu, run):
    with pytest.raises(SystemExit) as e:
        run([], RuntimeError("net"))
    assert "Failed to download bulk OHLC data: net" in str(e.value)


def test_main_single_frame_not_multiindex(eu, run):
    cap = run(["--top", "1", "--no-scans"], _flat(30, 50))
    assert len(cap["sheets"]["All_Stocks"]) == 1
    assert cap["sheets"]["All_Stocks"].iloc[0]["Symbol"] == next(iter(eu.EURO_STOXX_50_META))


def test_main_missing_symbol_is_isolated(eu, run, capsys):
    syms = list(eu.EURO_STOXX_50_META)
    raw = _raw({syms[0]: _flat(30, 50)})  # syms[1] absent -> KeyError per symbol
    cap = run(["--top", "2", "--no-scans"], raw)
    assert list(cap["sheets"]["All_Stocks"]["Symbol"]) == [syms[0]]
    assert f"Technical error on {syms[1]}" in capsys.readouterr().out


def test_main_prev_close_zero_gives_no_change(eu, run):
    syms = list(eu.EURO_STOXX_50_META)
    closes = np.concatenate([np.full(28, 10.0), [0.0, 5.0]])
    cap = run(["--top", "1", "--no-scans"], _raw({syms[0]: _ohlc(closes)}))
    assert cap["sheets"]["All_Stocks"].iloc[0]["Change%"] is None or pd.isna(
        cap["sheets"]["All_Stocks"].iloc[0]["Change%"]
    )


def test_main_fundamentals_merge_and_triple_hits(eu, run, capsys):
    syms = list(eu.EURO_STOXX_50_META)
    raw = _raw(
        {
            syms[0]: _long(_breakout()),
            syms[1]: _long(_breakout()),
            syms[2]: _long(_breakout()),
            syms[3]: _long(_breakdown()),
            syms[4]: _flat(30, 15),
        }
    )

    def fund(sym):
        if sym == syms[2]:
            raise RuntimeError("crashed")
        if sym == syms[0]:
            return {
                "f_score": 8,
                "cc_qualifies": "PASS",
                "cc_score": "6/6",
                "pe_ratio": 11.0,
                "pb_ratio": 1.5,
                "dividend_yield": 2.5,
                "market_cap_b": 9.0,
                "pegy_ratio": 3.0,
            }
        return {"f_score": 7, "cc_qualifies": "FAIL", "cc_score": "3/6", "error": "e"}

    cap = run(["--top", "5"], raw, fund)
    f = cap["sheets"]["Fundamentals"]
    assert sorted(f["Symbol"]) == sorted([syms[0], syms[1]])  # crash isolated; breakdown unscanned
    r0 = f[f["Symbol"] == syms[0]].iloc[0]
    assert r0["P_E_Ratio"] == 11.0 and r0["CoffeeCan_Class"] == "PASS" and r0["Name"] == "Adidas"
    assert list(cap["sheets"]["Triple_Hits"]["Symbol"]) == [syms[0]]
    allr = cap["sheets"]["All_Stocks"]
    a0 = allr[allr["Symbol"] == syms[0]].iloc[0]
    assert a0["Market_Cap_B"] == 9.0 and a0["P_E_Ratio"] == 11.0 and a0["Dividend_Yield%"] == 2.5
    assert sorted(cap["sheets"]["Darvas_Signals"]["Symbol"]) == sorted(syms[:4])
    out = capsys.readouterr().out
    assert f"Fundamental error on {syms[2]}: crashed" in out
    assert "TRIPLE HITS FOUND:          1" in out and "F-Score: 8/9  CC: 6/6" in out


@pytest.mark.parametrize(
    "score,cc,hits", [(7, "PASS", 1), (6, "PASS", 0), (7, "FAIL", 0), (None, "PASS", 0)]
)
def test_main_triple_hit_boundaries(eu, run, score, cc, hits):
    syms = list(eu.EURO_STOXX_50_META)
    cap = run(
        ["--top", "1"],
        _raw({syms[0]: _long(_breakout())}),
        lambda s: {"f_score": score, "cc_qualifies": cc},
    )
    assert len(cap["sheets"]["Triple_Hits"]) == hits


def test_main_progress_every_five(eu, run, capsys):
    syms = list(eu.EURO_STOXX_50_META)[:6]
    (
        run([], _raw({s: _long(_breakout()) for s in syms}), lambda s: {"f_score": 1})
        if False
        else None
    )
    cap = run(["--top", "6"], _raw({s: _long(_breakout()) for s in syms}), lambda s: {"f_score": 1})
    out = capsys.readouterr().out
    assert "    5/6 processed" in out and "    6/6 processed" in out and "    3/6" not in out
    assert len(cap["sheets"]["Fundamentals"]) == 6
