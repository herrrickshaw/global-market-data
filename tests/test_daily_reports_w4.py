"""us_stock_daily_report and sg_stock_daily_report: fetch/compute/display/run flows on a rich fake Ticker."""

import json
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load
from tests.test_scan_scripts_w4 import _box, _good_spec


def _hist(n=130, start=100.0, step=0.5):
    c = start + step * np.arange(n) + (np.arange(n) % 2) * 1.0
    return pd.DataFrame(
        {"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0},
        index=pd.date_range("2024-01-01", periods=n),
    )


class RichTicker(FakeTicker):
    rich = {}

    @property
    def fast_info(self):
        return types.SimpleNamespace(**RichTicker.rich.get("fast", {}))

    @property
    def actions(self):
        return RichTicker.rich.get("actions")

    @property
    def calendar(self):
        return RichTicker.rich.get("calendar", {})

    @property
    def options(self):
        return RichTicker.rich.get("options", ())

    def option_chain(self, expiry):
        return types.SimpleNamespace(calls=RichTicker.rich["calls"], puts=RichTicker.rich["puts"])

    @property
    def insider_transactions(self):
        return RichTicker.rich.get("insiders")

    @property
    def institutional_holders(self):
        return RichTicker.rich.get("holders")

    @property
    def news(self):
        return RichTicker.rich.get("news", [])

    def history(self, *a, **k):
        h = RichTicker.rich.get("history")
        if h is Exception:
            raise RuntimeError("hist down")
        return h if h is not None else pd.DataFrame()


@pytest.fixture
def us(monkeypatch, tmp_path):
    mod = load(monkeypatch, "us_stock_daily_report", tmp_path)
    monkeypatch.setattr(mod.yf, "Ticker", RichTicker)
    FakeTicker.spec = _good_spec()
    idx = pd.to_datetime(["2023-05-01", "2023-08-01", "2024-02-01"])
    RichTicker.rich = {
        "fast": {
            "last_price": 110.0,
            "previous_close": 100.0,
            "market_cap": 3e12,
            "year_change": 0.25,
            "currency": "USD",
        },
        "actions": pd.DataFrame(
            {"Dividends": [0.5, 0.0, 0.6], "Stock Splits": [0.0, 4.0, 0.0]}, index=idx
        ),
        "calendar": {
            "Earnings Date": ["2026-02-01"],
            "Earnings Average": 2.0,
            "Earnings Low": 1.5,
            "Earnings High": 2.5,
            "Revenue Average": 5e9,
        },
        "options": ("2026-01-16",),
        "calls": pd.DataFrame({"strike": [100.0, 110.0], "openInterest": [10, 30]}),
        "puts": pd.DataFrame({"strike": [100.0, 110.0], "openInterest": [40, 20]}),
        "insiders": pd.DataFrame(
            {
                "Insider": ["A", "B"],
                "Position": ["CEO", "CFO"],
                "Transaction": ["Sale", "Buy"],
                "Shares": [1000, 200],
                "Value": [2e6, 5e4],
                "Start Date": pd.to_datetime(["2024-01-01", "2024-03-01"]),
            }
        ),
        "holders": pd.DataFrame(
            {"Holder": ["Vanguard"], "pctHeld": [0.08], "Shares": [1.2e9], "pctChange": [0.01]}
        ),
        "news": [
            {"content": {"title": "Headline", "pubDate": "2026-01-02T10:00:00Z"}},
            {"title": "Old", "providerPublishTime": 1_700_000_000},
        ],
        "history": _hist(),
    }
    return mod


def test_formatters(us):
    assert (
        us.fmt(1234.5) == "$1,234.50"
        and us.fmt("x") == "x"
        and us.fmt(None) == "N/A"
        and us.fmt(0) == "$0.00"
    )
    assert us.fmt(2.0, prefix="", decimals=0) == "2"
    assert (
        us.fmt_large(4.48e12) == "$4.48T"
        and us.fmt_large(182.5e9) == "$182.50B"
        and us.fmt_large(3e6) == "$3.00M"
    )
    assert (
        us.fmt_large(5e3) == "$5.00K"
        and us.fmt_large(999) == "$999.00"
        and us.fmt_large(-2e9) == "$-2.00B"
    )
    assert (
        us.fmt_large(1e12) == "$1.00T"
        and us.fmt_large(1e9) == "$1.00B"
        and us.fmt_large("n/a") == "N/A"
    )
    assert (
        "▲ 1.50%" in us.pct(1.5)
        and "▼ 2.00%" in us.pct(-2)
        and "▲ 0.00%" in us.pct(0)
        and us.pct("x") == "N/A"
    )


def test_section_and_row(us, capsys):
    us.section("hello")
    us.row("Label", "v", width=8)
    out = capsys.readouterr().out
    assert "HELLO" in out and "-" * 60 in out and "  Label    v" in out


def test_fetch_quote_prefers_fast_info(us):
    q = us.fetch_quote(RichTicker("AAA"))
    assert (
        q["lastPrice"] == 110.0
        and q["previousClose"] == 100.0
        and q["marketCap"] == 3e12
        and q["ytdChange"] == 0.25
    )
    assert (
        q["name"] == "Acme"
        and q["sector"] == "Tech"
        and q["recommendation"] == "—".upper()
        and q["country"] == "US"
    )
    RichTicker.rich["fast"] = {}
    FakeTicker.spec["info"] = {
        "currentPrice": 9.0,
        "previousClose": 8.0,
        "marketCap": 5,
        "recommendationKey": "buy",
        "country": "FR",
    }
    q = us.fetch_quote(RichTicker("AAA"))
    assert (
        q["lastPrice"] == 9.0
        and q["recommendation"] == "BUY"
        and q["country"] == "FR"
        and q["currency"] == "USD"
    )
    FakeTicker.spec["info"] = Exception  # info endpoint raising is tolerated
    assert us.fetch_quote(RichTicker("AAA"))["lastPrice"] is None


def test_fetch_historical_and_errors(us, capsys):
    assert len(us.fetch_historical(RichTicker("A"))) == 130
    RichTicker.rich["history"] = Exception
    assert (
        us.fetch_historical(RichTicker("A")).empty
        and "[Historical error]" in capsys.readouterr().out
    )
    RichTicker.rich["history"] = "not a frame"
    assert us.fetch_historical(RichTicker("A")).empty


def test_fetch_corporate_actions(us):
    a = us.fetch_corporate_actions(RichTicker("A"))
    assert [d["amount"] for d in a["dividends"]] == [0.5, 0.6] and a["splits"] == [
        {"date": "2023-08-01", "ratio": 4.0}
    ]
    assert a["calendar"]["next_earnings"] == "2026-02-01" and a["calendar"]["eps_est_avg"] == 2.0
    RichTicker.rich["calendar"] = {}
    RichTicker.rich["actions"] = None
    a = us.fetch_corporate_actions(RichTicker("A"))
    assert a["dividends"] == [] and a["calendar"]["next_earnings"] == "—"


def test_fetch_options_pcr_and_max_pain(us):
    o = us.fetch_options_data(RichTicker("A"))
    assert (
        o["expiry_date"] == "2026-01-16"
        and o["call_oi"] == 40
        and o["put_oi"] == 60
        and o["pcr"] == 1.5
    )
    # pain(100)=put 20*10=200? computed: strike 100 -> calls 0 + puts (110-100)*20=200 ; strike 110 -> calls 10*10=100 + 0
    assert o["max_pain"] == 110.0 and o["sentiment"] == "Bullish (PCR>1)"
    RichTicker.rich["puts"] = pd.DataFrame({"strike": [100.0], "openInterest": [10]})
    assert us.fetch_options_data(RichTicker("A"))["sentiment"] == "Bearish (PCR≤1)"
    RichTicker.rich["options"] = ()
    assert us.fetch_options_data(RichTicker("A")) == {}
    RichTicker.rich.pop("calls")
    RichTicker.rich["options"] = ("x",)
    assert us.fetch_options_data(RichTicker("A")) == {}  # chain failure swallowed


def test_fetch_insiders_holders_news(us):
    t = us.fetch_insider_trades(RichTicker("A"))
    assert [r["insider"] for r in t] == ["B", "A"]  # newest first
    assert us.fetch_institutional_holders(RichTicker("A"))[0]["Holder"] == "Vanguard"
    n = us.fetch_news(RichTicker("A"))
    assert (
        n[0] == {"title": "Headline", "published": "2026-01-02T10:00"}
        and n[1]["title"] == "Old"
        and n[1]["published"].startswith("2023-11-14")
    )
    RichTicker.rich.update(insiders=None, holders=pd.DataFrame(), news=None)
    assert (
        us.fetch_insider_trades(RichTicker("A")) == []
        and us.fetch_institutional_holders(RichTicker("A")) == []
    )
    assert us.fetch_news(RichTicker("A")) == []


def test_scans_on_strong_company(us):
    t = RichTicker("AAA")
    d = us.compute_darvas_box(_box(21.0))
    assert d["signal"] == "BREAKOUT_BUY" and d["box_top"] == 20.0 and d["box_bottom"] == 10.0
    assert us.compute_darvas_box(_box(9.0))["signal"] == "BREAKDOWN_SELL"
    assert us.compute_darvas_box(_box(15.0))["signal"] == "IN_BOX"
    assert us.compute_darvas_box(_box(15.0).iloc[:5])["signal"] == "INSUFFICIENT_DATA"
    p = us.compute_piotroski_score(t, "AAA")
    assert (
        p["f_score"] == 9
        and "error" not in p
        and set(p["component_scores"]) >= {"F1_ROA_positive", "F9_AssetTurnover_up"}
    )
    c = us.compute_coffee_can(t, "AAA")
    assert (
        c["qualifies"] is True and c["score"] in ("6/6", 6) and c["criteria"]["C4_MCap_ge1B"] == 1
    )


def test_scans_error_paths(us):
    FakeTicker.spec = {"info": {}, "market_cap": 0}
    t = RichTicker("AAA")
    assert "error" in us.compute_piotroski_score(t, "AAA") and "error" in us.compute_coffee_can(
        t, "AAA"
    )


def test_display_functions(us, capsys):
    us.display_price_summary(us.fetch_quote(RichTicker("A")))
    us.display_corporate_actions(us.fetch_corporate_actions(RichTicker("A")))
    us.display_options_summary(us.fetch_options_data(RichTicker("A")))
    us.display_options_summary({})
    us.display_insider_trades(us.fetch_insider_trades(RichTicker("A")), "AAA")
    us.display_insider_trades([], "AAA")
    us.display_institutional_holders(us.fetch_institutional_holders(RichTicker("A")))
    us.display_institutional_holders([])
    us.display_historical_summary(us.fetch_historical(RichTicker("A")))
    us.display_historical_summary(pd.DataFrame())
    us.display_historical_summary(_hist(1))
    us.display_news(us.fetch_news(RichTicker("A")), "AAA")
    us.display_news([], "AAA")
    for sig in ("BREAKOUT_BUY", "BREAKDOWN_SELL", "IN_BOX", "NO_BOX", "INSUFFICIENT_DATA", "WEIRD"):
        us.display_darvas_box(
            {"signal": sig, "box_top": 20.0, "box_bottom": 10.0, "current_price": 15.0}
        )
    us.display_darvas_box({"signal": "NO_BOX", "note": "n"})
    us.display_piotroski_score(us.compute_piotroski_score(RichTicker("AAA"), "AAA"))
    us.display_piotroski_score({"error": "bad"})
    us.display_coffee_can(us.compute_coffee_can(RichTicker("AAA"), "AAA"))
    us.display_coffee_can({"error": "bad"})
    out = capsys.readouterr().out.lower()
    for needle in (
        "Live Quote & Valuation",
        "Last Price",
        "$110.00",
        "▲ 10.00%",
        "$3.00T",
        "Recent Dividends",
        "4.0:1 split",
        "Put-Call Ratio",
        "1.500",
        "Max Pain Strike",
        "Options data not available",
        "Sale",
        "No recent insider transactions found for AAA",
        "Vanguard",
        "No institutional holder data",
        "Trading days",
        "No historical data available",
        "Insufficient closing-price data",
        "Headline",
        "No news available",
        "BREAKOUT BUY",
        "BREAKDOWN SELL",
        "IN BOX",
        "No confirmed Darvas box",
        "Insufficient data",
        "Note: n",
        "Score:",
        "9/9",
        "QUALIFIES",
        "⚠️  bad",
    ):
        assert needle.lower() in out, needle


def test_run_text_json_batch_and_scans_only(us, tmp_path, capsys):
    data = us.run("aapl ", show_options=True, run_scans=True)
    assert data["symbol"] == "AAPL" and "quote" not in data
    out = capsys.readouterr().out.lower()
    assert (
        "us stock report — aapl" in out
        and "max pain strike" in out
        and "piotroski f-score" in out
        and "coffee can" in out
    )
    j = us.run("MSFT", output_format="json", run_scans=True)
    saved = list(us.DOWNLOAD_DIR.glob("MSFT_report_*.json"))
    assert len(saved) == 1
    payload = json.loads(saved[0].read_text())
    assert (
        payload["quote"]["lastPrice"] == 110.0
        and payload["scans"]["piotroski"]["f_score"] == 9
        and len(payload["historical_30d"]) == 130
    )
    j2 = us.run("MSFT", output_format="json")
    assert j2["scans"] == {} and j2["options"] == {}
    res = us.run_scans_only("nvda")
    assert (
        res["symbol"] == "NVDA"
        and res["piotroski"]["f_score"] == 9
        and res["darvas"]["signal"]
        in ("NO_BOX", "IN_BOX", "BREAKOUT_BUY", "BREAKDOWN_SELL", "INSUFFICIENT_DATA")
    )


def test_run_batch_isolates_failures_and_writes_csv(us, monkeypatch, capsys):
    real = us.run

    def flaky(sym, show_options, output_format, run_scans):
        if sym == "BAD":
            raise RuntimeError("x")
        return real(
            sym, show_options=show_options, output_format=output_format, run_scans=run_scans
        )

    monkeypatch.setattr(us, "run", flaky)
    out = us.run_batch(["AAPL", "BAD", "MSFT"], output_format="json", run_scans=True)
    assert [d["symbol"] for d in out] == ["AAPL", "MSFT"]
    txt = capsys.readouterr().out
    assert "2 OK, 1 failed" in txt and "Failed: BAD" in txt and "BAD failed: x" in txt
    csvs = list(us.DOWNLOAD_DIR.glob("us_scan_summary_*.csv"))
    df = pd.read_csv(csvs[0])
    assert (
        list(df["Symbol"]) == ["AAPL", "MSFT"]
        and df["Price"].iloc[0] == 110.0
        and df["Change%"].iloc[0] == 10.0
    )
    assert set(df["CoffeeCan"]) == {"YES"} and df["Piotroski_Score"].iloc[0] == 9
    us.run_batch(["AAPL"], output_format="json")
    assert list(us.DOWNLOAD_DIR.glob("us_batch_summary_*.csv"))
    monkeypatch.setattr(us, "DOW_JONES_30", ["ONLY"])
    assert [d["symbol"] for d in us.run_batch(output_format="json")] == [
        "ONLY"
    ]  # default universe = Dow 30 list
    us._write_summary_csv([])  # nothing to write


# ---------------- sg_stock_daily_report ----------------
@pytest.fixture
def sg(monkeypatch, tmp_path):
    mod = load(monkeypatch, "sg_stock_daily_report", tmp_path)
    monkeypatch.setattr(mod.yf, "Ticker", RichTicker)
    FakeTicker.spec = _good_spec()
    FakeTicker.spec["info"].update(
        {"pegRatio": 1.2, "dividendYield": 0.03, "trailingPE": 11.0, "twoHundredDayAverage": 100.0}
    )
    RichTicker.rich = {
        "fast": {
            "last_price": 110.0,
            "previous_close": 100.0,
            "market_cap": 3e10,
            "two_hundred_day_average": 100.0,
        },
        "actions": pd.DataFrame(
            {"Dividends": [0.5], "Stock Splits": [0.0]}, index=pd.to_datetime(["2024-02-01"])
        ),
        "calendar": {"Earnings Date": ["2026-02-01"]},
        "history": _hist(),
    }
    return mod


def test_sg_formatters_and_quote(sg):
    assert (
        sg.fmt(1234.5) == "S$1,234.50"
        and sg.fmt(None) == "N/A"
        and sg.fmt_large(2.5e9) == "S$2.50B"
    )
    assert "▲" in sg.pct(1) and "▼" in sg.pct(-1) and sg.pct(None) == "N/A"
    q = sg.fetch_quote(RichTicker("D05.SI"))
    assert q["lastPrice"] == 110.0 and q["previousClose"] == 100.0
    assert len(sg.fetch_historical(RichTicker("D05.SI"))) == 130
    assert sg.fetch_corporate_actions(RichTicker("D05.SI"))["dividends"][0]["amount"] == 0.5


def test_sg_pegy_and_breakout_bands(sg):
    r = sg.compute_pegy_and_breakout(RichTicker("D05.SI"), "D05.SI")
    assert r["cmp"] == 110.0 and r["peg_ratio"] == 1.2 and r["ma_200"] == 100.0
    assert r["dividend_yield_%"] == 3.0 and r["pegy_adjusted"] == pytest.approx(
        4.2
    )  # peg + yield*100
    assert r["breakout"] == {"distance_from_200ma_%": 10.0, "signal": "Above 200-day MA - Uptrend"}
    RichTicker.rich["fast"]["last_price"] = 95.0  # exactly -5% is still consolidation
    assert (
        sg.compute_pegy_and_breakout(RichTicker("D"), "D")["breakout"]["signal"]
        == "Near 200-day MA - Consolidation"
    )
    RichTicker.rich["fast"]["last_price"] = 105.0  # +5% exactly is still consolidation
    assert (
        sg.compute_pegy_and_breakout(RichTicker("D"), "D")["breakout"]["signal"]
        == "Near 200-day MA - Consolidation"
    )
    RichTicker.rich["fast"]["last_price"] = 94.9
    assert "Downtrend" in sg.compute_pegy_and_breakout(RichTicker("D"), "D")["breakout"]["signal"]
    RichTicker.rich["history"] = Exception
    assert sg.compute_pegy_and_breakout(RichTicker("D"), "D") == {
        "symbol": "D",
        "error": "Data fetch failed",
    }


def test_sg_scans_and_displays(sg, capsys):
    t = RichTicker("D05.SI")
    p = sg.compute_piotroski_score(t, "D05.SI")
    assert p["f_score"] == 9
    assert sg.compute_darvas_box(_box(21.0))["signal"] == "BREAKOUT_BUY"
    sg.display_price_summary(sg.fetch_quote(t))
    sg.display_corporate_actions(sg.fetch_corporate_actions(t))
    sg.display_darvas_box(sg.compute_darvas_box(_box(15.0)))
    sg.display_piotroski_score(p)
    sg.display_piotroski_score({"error": "bad"})
    sg.display_pegy_and_breakout(sg.compute_pegy_and_breakout(RichTicker("D"), "D"))
    sg.display_pegy_and_breakout({"error": "bad"})
    out = capsys.readouterr().out.lower()
    for needle in (
        "s$110.00",
        "in box",
        "9/9",
        "pegy ratio",
        "distance from 200ma",
        "10.00%",
        "⚠️  bad",
    ):
        assert needle in out, needle


def test_sg_run_suffix_and_batch_excel(sg, monkeypatch, capsys):
    d = sg.run("d05", run_scans=True)
    assert (
        d["symbol"] == "D05.SI"
        and d["scans"]["piotroski"]["f_score"] == 9
        and set(d["scans"]) == {"darvas", "piotroski", "pegy"}
    )
    assert sg.run("U11.SI", output_format="data")["scans"] == {}
    saved = []
    monkeypatch.setattr(
        sg, "_write_excel_report", lambda results, scans: saved.append((len(results), scans))
    )
    real = sg.run
    monkeypatch.setattr(
        sg,
        "run",
        lambda s, output_format, run_scans: (
            (_ for _ in ()).throw(RuntimeError("x"))
            if s == "BAD.SI"
            else real(s, output_format=output_format, run_scans=run_scans)
        ),
    )
    out = sg.run_batch(["D05.SI", "BAD.SI", "U11"], run_scans=True)
    assert [r["symbol"] for r in out] == ["D05.SI", "U11.SI"] and saved == [(2, True)]
    txt = capsys.readouterr().out
    assert "2 OK, 1 failed" in txt and "Failed: BAD" in txt
    saved.clear()
    sg.run_batch(["D05.SI"], output_format="text")
    assert saved == []  # Excel only for the 'excel' format


def test_sg_write_excel_report_saves_workbook(sg, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    data = sg.run("D05", run_scans=True)
    sg._write_excel_report([data], include_scans=True)
    sg._write_excel_report([sg.run("U11")], include_scans=False)
    wb = sg.Workbook.return_value
    assert wb.save.called and wb.create_sheet.called
    assert "SG" in capsys.readouterr().out.upper()


def test_sg_run_scans_only(sg):
    r = sg.run_scans_only("d05")
    assert r["symbol"] in ("D05", "D05.SI") and "piotroski" in r


def test_us_options_sentiment_boundary_pcr_of_exactly_one(us):
    RichTicker.rich["puts"] = pd.DataFrame(
        {"strike": [100.0, 110.0], "openInterest": [20, 20]}
    )  # 40 put OI vs 40 call OI
    o = us.fetch_options_data(RichTicker("A"))
    assert o["pcr"] == 1.0 and o["sentiment"] == "Bearish (PCR≤1)"
