"""stock_daily_report_improved: NSE/BSE fetch wrappers, bulk-deal normalisation, scans, display and run flows."""

import json
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load
from tests.test_scan_scripts_w4 import _box, _good_spec


class FakeNSE:
    def __init__(self, **kw):
        self.kw = kw
        self.bulk = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def quote(self, symbol, section):
        return {"trade_info": {"totalTradedVolume": 1_234_567, "totalTradedValue": 5e9, "priceBandHigh": 120, "priceBandLow": 80}}

    def equityQuote(self, symbol):  # noqa: N802
        return {
            "priceInfo": {"lastPrice": 110.0, "previousClose": 100.0, "change": 10.0, "pChange": 10.0, "open": 101.0,
                          "intraDayHighLow": {"max": 111.0, "min": 99.0}, "weekHighLow": {"max": 150.0, "min": 70.0},
                          "vwap": 105.0, "priceBand": {"upper": 121.0, "lower": 99.0}},
            "metadata": {"companyName": "Acme Ltd", "isin": "INE000", "series": "EQ", "industry": "Tech"},
            "securityInfo": {"faceValue": 10},
        }

    def equityMetaInfo(self, symbol):  # noqa: N802
        return {"isin": "INE000"}

    def actions(self, symbol):
        return [{"exDate": "2026-01-05", "purpose": "Dividend"}] * 7

    def boardMeetings(self, symbol):  # noqa: N802
        return [{"meetingDate": "2026-02-01", "purpose": "Results"}] * 5

    def equityHistoricalData(self, symbol, start, end):  # noqa: N802
        return [{"CH_CLOSING_PRICE": 100 + i} for i in range(10)]

    def optionChain(self, symbol):  # noqa: N802
        return {"chain": 1}

    def maxpain(self, oc):
        return 105.0

    def compileOptionChain(self, oc):  # noqa: N802
        return {"pcr": 1.25}

    def bulkdeals(self, option_type, fromdate, todate):
        return self.bulk


class FakeBSE:
    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getScripCode(self, t):  # noqa: N802
        return "500325" if t != "NOCODE" else None

    def quote(self, code):
        return {"LastRate": 111.0, "OpenRate": 101.0, "High": 112.0, "Low": 98.0, "PrevRate": 100.0, "Volume": 5000, "Mktcap": 2e10}

    def actions(self, scripcode):
        return [{"exDate": "2026-01-06", "purpose": "Bonus"}] * 6


@pytest.fixture
def nr(monkeypatch, tmp_path):
    mod = load(monkeypatch, "stock_daily_report_improved", tmp_path)
    monkeypatch.setattr(mod, "NSE", FakeNSE)
    monkeypatch.setattr(mod, "BSE", FakeBSE)
    monkeypatch.setattr(mod.yf, "Ticker", FakeTicker)
    FakeTicker.spec = _good_spec()
    return mod


def test_formatters(nr):
    assert nr.fmt(1234.5) == "₹1,234.50" and nr.fmt(None) == "N/A" and nr.fmt("abc") == "abc" and nr.fmt(5, decimals=0) == "₹5"
    assert "▲ 1.00%" in nr.pct(1) and "▼ 1.00%" in nr.pct(-1) and nr.pct(None) == "N/A"


def test_nse_wrappers_trim_and_swallow_errors(nr, capsys):
    n = FakeNSE()
    assert nr.fetch_nse_quote(n, "A")["trade_info"]["totalTradedVolume"] == 1_234_567
    assert nr.fetch_nse_equity_quote(n, "A")["metadata"]["isin"] == "INE000"
    assert nr.fetch_nse_meta(n, "A") == {"isin": "INE000"}
    assert len(nr.fetch_nse_actions(n, "A")) == 5 and len(nr.fetch_nse_board_meetings(n, "A")) == 3
    assert len(nr.fetch_nse_historical(n, "A", days=10)) == 10
    oc = nr.fetch_nse_option_chain(n, "A")
    assert oc["maxpain"] == 105.0 and oc["pcr"] == 1.25 and oc["raw"] == {"chain": 1}

    class Broken:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise RuntimeError("down")

            return boom

    b = Broken()
    assert nr.fetch_nse_quote(b, "A") == {} and nr.fetch_nse_equity_quote(b, "A") == {} and nr.fetch_nse_meta(b, "A") == {}
    assert nr.fetch_nse_actions(b, "A") == [] and nr.fetch_nse_board_meetings(b, "A") == [] and nr.fetch_nse_option_chain(b, "A") == {}
    assert nr.fetch_nse_historical(b, "A") == []
    out = capsys.readouterr().out
    assert "[NSE quote error] down" in out and "[NSE historical via equityHistoricalData] down" in out
    n.optionChain = lambda s: None
    assert nr.fetch_nse_option_chain(n, "A") == {}


def test_historical_falls_back_to_second_method(nr):
    class OnlyOld:
        def fetch_equity_historical_data(self, symbol, start, end):
            return [{"close": 1}]

    assert nr.fetch_nse_historical(OnlyOld(), "A") == [{"close": 1}]
    assert nr.fetch_nse_historical(object(), "A") == []  # neither method exists


def test_normalize_bulk_df_keyword_matching(nr, capsys):
    df = pd.DataFrame(
        {" Symbol ": ["AAA"], "Client Name": ["X Fund"], "Quantity Traded": ["1,26,922"], "Trade Price / Wght. Avg. Price": ["10.5"], "Buy/Sell": ["BUY"]}
    )
    rows = nr._normalize_bulk_df(df)
    assert rows == [{"symbol": "AAA", "clientname": "X Fund", "quantity": "126922", "price": 10.5, "buysell": "BUY"}]
    assert nr._normalize_bulk_df(pd.DataFrame({"symbol": ["A"], "price": [1]})) == []
    assert "Missing columns after rename" in capsys.readouterr().out
    bad_price = df.assign(**{"Trade Price / Wght. Avg. Price": ["n/a"]})
    assert np.isnan(nr._normalize_bulk_df(bad_price)[0]["price"])


def test_fetch_bulk_deals_priority_chain(nr, monkeypatch, tmp_path, capsys):
    good = pd.DataFrame({"symbol": ["AAA"], "client": ["F"], "quantity": ["1,000"], "price": [5], "buy/sell": ["SELL"]})
    monkeypatch.setattr(nr, "NSEPYTHON_OK", True)
    monkeypatch.setattr(nr.nspy, "get_bulkdeals", lambda: good)
    nse = FakeNSE()
    assert nr.fetch_bulk_deals(nse)[0]["quantity"] == "1000"
    monkeypatch.setattr(nr.nspy, "get_bulkdeals", lambda: pd.DataFrame())  # empty -> next source
    nse.bulk = [{"symbol": "API"}]
    assert nr.fetch_bulk_deals(nse) == [{"symbol": "API"}]
    monkeypatch.setattr(nr.nspy, "get_bulkdeals", lambda: 1 / 0)
    nse.bulk = []
    (tmp_path / "x").mkdir(exist_ok=True)
    monkeypatch.setattr(nr, "DOWNLOAD_DIR", tmp_path / "x")
    assert nr.fetch_bulk_deals(nse) == []
    out = capsys.readouterr().out
    assert "[nsepython bulk deals error]" in out and "No local fallback found" in out
    good.rename(columns={"buy/sell": "buysell"}).to_csv(tmp_path / "x" / "bulk_deals.csv", index=False)
    assert nr.fetch_bulk_deals(nse)[0]["symbol"] == "AAA"  # csv fallback
    (tmp_path / "x" / "bulk_deals.csv").write_text("")  # empty file -> parse error handled
    assert nr.fetch_bulk_deals(nse) == []
    monkeypatch.setattr(nse, "bulkdeals", lambda **k: 1 / 0)
    nr.fetch_bulk_deals(nse)
    assert "[NSE bulk deals API error]" in capsys.readouterr().out


def test_fetch_bse_data(nr, capsys):
    d = nr.fetch_bse_data(FakeBSE(), "RELIANCE")
    assert d["scrip_code"] == "500325" and d["quote"]["LastRate"] == 111.0 and len(d["actions"]) == 5
    assert nr.fetch_bse_data(FakeBSE(), "NOCODE") == {} and "Scrip code not found" in capsys.readouterr().out

    class Boom(FakeBSE):
        def getScripCode(self, t):  # noqa: N802
            raise RuntimeError("bse down")

    assert nr.fetch_bse_data(Boom(), "A") == {} and "[BSE error] bse down" in capsys.readouterr().out


def test_fetch_ohlc_history(nr, monkeypatch):
    seen = {}
    df = pd.DataFrame({"CH_TIMESTAMP": ["2024-01-03", "2024-01-01", "2024-01-02"], "CH_CLOSING_PRICE": [3, 1, 2]})
    monkeypatch.setattr(nr, "NSEPYTHON_OK", True)
    monkeypatch.setattr(nr.nspy, "equity_history", lambda s, series, start, end: seen.update(a=(s, series)) or df)
    out = nr.fetch_ohlc_history("AAA", days=30)
    assert seen["a"] == ("AAA", "EQ") and list(out["CH_CLOSING_PRICE"]) == [1, 2, 3]  # sorted by the timestamp column
    monkeypatch.setattr(nr.nspy, "equity_history", lambda *a: pd.DataFrame({"x": [1]}))
    assert list(nr.fetch_ohlc_history("AAA").columns) == ["x"]  # no date column: returned unsorted
    monkeypatch.setattr(nr.nspy, "equity_history", lambda *a: "junk")
    assert nr.fetch_ohlc_history("AAA").empty
    monkeypatch.setattr(nr.nspy, "equity_history", lambda *a: 1 / 0)
    assert nr.fetch_ohlc_history("AAA").empty
    monkeypatch.setattr(nr, "NSEPYTHON_OK", False)
    assert nr.fetch_ohlc_history("AAA").empty


def test_scans(nr):
    assert nr.compute_darvas_box(_box(21.0))["signal"] == "BREAKOUT_BUY"
    assert nr.compute_darvas_box(_box(9.0))["signal"] == "BREAKDOWN_SELL"
    FakeTicker.spec = {"info": {}, "market_cap": 0}  # no statements at all -> a clean "no data" error
    assert "error" in nr.compute_piotroski_score("X") and "error" in nr.compute_coffee_can("X")
    assert nr._row(None, "a") is None and nr._row(pd.DataFrame(), "a") is None
    inc = pd.DataFrame({"2024": [5.0, np.nan]}, index=["Net Income", "Other"])
    assert nr._row(inc, "Missing", "Net Income") == 5.0 and nr._row(inc, "Other") is None and nr._row(inc, "Net Income", col=3) is None
    t = types.SimpleNamespace(income_stmt=None, financials=inc, balance_sheet=None, cash_flow=None, cashflow=None)
    assert nr._yf_financials(t)[0] is inc


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="stock_daily_report_improved._yf_financials uses `getattr(t, 'income_stmt') or getattr(t, 'financials')`, "
    "which evaluates DataFrame truthiness (ValueError: truth value is ambiguous), so compute_piotroski_score / "
    "compute_coffee_can always return a 'yfinance error' for real statements",
)
def test_scans_score_real_statements(nr):
    assert nr.compute_piotroski_score("RELIANCE")["f_score"] == 9
    assert "error" not in nr.compute_coffee_can("RELIANCE")


def test_displays(nr, capsys):
    n = FakeNSE()
    nr.display_nse_price(n.equityQuote("A"), n.quote("A", "trade_info"))
    nr.display_nse_price({}, {})
    nr.display_bse_price({"quote": FakeBSE().quote("1"), "scrip_code": "500325"})
    nr.display_bse_price({})
    nr.display_corporate_actions(nr.fetch_nse_actions(n, "A"), [{"Ex_Date": "d", "Purpose": "p"}])
    nr.display_corporate_actions([], [])
    nr.display_board_meetings(nr.fetch_nse_board_meetings(n, "A"))
    nr.display_board_meetings([])
    nr.display_option_chain({"maxpain": 105.0, "pcr": 1.25})
    nr.display_option_chain({"maxpain": 0, "pcr": 0.5})
    nr.display_option_chain({})
    nr.display_historical_summary(nr.fetch_nse_historical(n, "A"))
    nr.display_historical_summary([])
    nr.display_historical_summary([{"junk": 1}])
    nr.display_bulk_deals([{"symbol": " aaa ", "clientname": "F", "quantity": "10", "price": 5, "buysell": "BUY "}], "AAA")
    nr.display_bulk_deals([], "AAA")
    nr.display_darvas_box(nr.compute_darvas_box(_box(15.0)))
    nr.display_piotroski_score({"f_score": 9, "interpretation": "Strong", "component_scores": {"F1_ROA_positive": 1}, "details": {"roa": 0.1}})
    nr.display_coffee_can({"qualifies": True, "score": "6/6", "criteria": {"C1_Revenue_CAGR_gt10": 1}, "details": {"cagr": 12}})
    out = capsys.readouterr().out.lower()
    for needle in ("acme ltd", "₹110.00", "▲ 10.00%", "1,234,567", "₹500.00 cr", "500325", "₹2,000.00 cr",
                   "no bse data available", "dividend", "no upcoming board meetings", "bullish (pcr > 1)", "bearish (pcr ≤ 1)",
                   "not an f&o stock", "days of data", "no historical data returned", "sample record keys: ['junk']",
                   "buy", "no bulk deals for aaa today", "in box", "9/9", "strong", "qualifies"):
        assert needle in out, needle


def test_run_text_json_batch_and_summary(nr, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(nr, "NSEPYTHON_OK", False)
    monkeypatch.setattr(nr, "DOWNLOAD_DIR", tmp_path)
    d = nr.run("reliance ", show_fno=True, run_scans=True)
    assert d["symbol"] == "RELIANCE"
    out = capsys.readouterr().out
    assert "DAILY STOCK REPORT — RELIANCE" in out and "F&O — OPTION CHAIN SUMMARY" in out.upper() and "darvas" in out.lower()
    j = nr.run("tcs", show_fno=True, output_format="json", run_scans=True)
    payload = json.loads((tmp_path / f"TCS_report_{pd.Timestamp.today():%Y%m%d}.json").read_text())
    assert payload["nse"]["equityQuote"]["priceInfo"]["lastPrice"] == 110.0 and "raw" not in payload["nse"]["optionChain"]
    assert payload["bse"]["scrip_code"] == "500325" and "error" in payload["scans"]["piotroski"]
    assert j["scans"]["coffee_can"]
    nr._write_summary_csv([j], include_scans=True)
    nr._write_summary_csv([j])
    nr._write_summary_csv([])
    df = pd.read_csv(next(tmp_path.glob("nifty50_scan_summary_*.csv")))
    assert df["LTP"].iloc[0] == 110.0 and df["DayHigh"].iloc[0] == 111.0 and pd.isna(df["Piotroski_Score"].iloc[0])
    assert list(tmp_path.glob("nifty50_summary_*.csv"))
    s = nr.run_scans_only("infy")
    assert set(s) == {"darvas", "piotroski", "coffee_can"} and "error" in s["piotroski"]
