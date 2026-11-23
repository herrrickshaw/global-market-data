"""daily_combined_report: pick aggregation over scan workbooks, news convergence, text/HTML/JSON output."""

import json

import pandas as pd
import pytest

from tests.scan_loader_w4 import load
from tests.w4_helpers import fake_module


@pytest.fixture
def dc(monkeypatch, tmp_path):
    return load(monkeypatch, "daily_combined_report", tmp_path)


def _book(sheets):
    """Fake pd.ExcelFile / read_excel over an in-memory workbook {sheet: DataFrame}."""

    class XL:
        def __init__(self, path):
            self.sheet_names = list(sheets)

    return XL, lambda path, sheet_name: sheets[sheet_name].copy()


def _install(monkeypatch, dc, sheets, files=("indian_full_scan/x_full_scan_1.xlsx",)):
    xl, rd = _book(sheets)
    monkeypatch.setattr(dc.pd, "ExcelFile", xl)
    monkeypatch.setattr(dc.pd, "read_excel", rd)
    monkeypatch.setattr(dc.glob, "glob", lambda pat: list(files))


def test_picks_tiering_and_ordering(dc, monkeypatch, capsys):
    sheets = {
        "Triple_Hits": pd.DataFrame({"Symbol": ["TRI"], "Piotroski_Score": [9], "LTP": [100.0]}),
        "Darvas_Signals": pd.DataFrame(
            {
                "Symbol": ["TRI", "DAR", "NOT"],
                "Darvas_Signal": ["BREAKOUT_BUY", "breakout_buy", "BREAKDOWN_SELL"],
            }
        ),
        "Fundamentals": pd.DataFrame(
            {
                "Symbol": ["TRI", "MUL", "MUL", "MUL", "SIN", " "],
                "LTP": [100.0, 50.0, 50.0, 50.0, 10.0, 1.0],
                "Piotroski_Score": [9, 7, 7, 7, 8, 1],
                "Piotroski_Strong": ["YES", "YES", "no", "no", "YES", "YES"],
                "CoffeeCan": ["PASS", "PASS", "PASS", "no", "no", "no"],
                "MagicFormula": ["no", "PASS", "no", "no", "no", "no"],
                "BullCartel": ["no", "no", "PASS", "no", "no", "no"],
                "Darvas_Signal": ["", "BREAKOUT_BUY", "", "", "", ""],
            }
        ),
        "All_Stocks": pd.DataFrame(
            {
                "Symbol": ["SIN", "GCX"],
                "GC_Signal": ["", "golden_cross"],
                "LTP": [10.0, 20.0],
                "Piotroski_Score": [8, None],
            }
        ),
    }
    _install(monkeypatch, dc, sheets)
    df = dc.get_fundamental_picks("IN").set_index("Symbol")
    assert list(dc.get_fundamental_picks("IN")["Symbol"])[0] == "TRI"  # Triple Hit first
    assert df.loc["TRI", "Tier"] == "Triple Hit"
    assert (
        df.loc["TRI", "N_Screens"] == 3 and df.loc["TRI", "Screens"] == "CoffeeCan+Darvas+Piotroski"
    )  # TripleHit marker not counted
    assert (
        df.loc["MUL", "Tier"] == "Multi-Screen"
        and df.loc["MUL", "N_Screens"] == 5
        and df.loc["MUL", "Piotroski"] == 7
    )
    assert (
        df.loc["SIN", "Tier"] == "Single-Screen"
        and df.loc["DAR", "Screens"] == "Darvas"
        and "NOT" not in df.index
        and " " not in df.index
    )
    assert (
        df.loc["GCX", "Screens"] == "GoldenCross"
        and df.loc["GCX", "LTP"] == 20.0
        and pd.isna(df.loc["GCX", "Piotroski"])
    )
    order = list(dc.get_fundamental_picks("IN")["Tier"])
    assert order == sorted(order, key=["Triple Hit", "Multi-Screen", "Single-Screen"].index)
    assert "UNIQUE fundamental picks" in capsys.readouterr().out


def test_multi_screen_threshold_is_three(dc, monkeypatch):
    fund = pd.DataFrame(
        {
            "Symbol": ["A", "A", "B", "B", "B"],
            "Piotroski_Strong": ["YES", "no", "YES", "no", "no"],
            "CoffeeCan": ["no", "PASS", "no", "PASS", "no"],
            "MagicFormula": ["no", "no", "no", "no", "PASS"],
            "BullCartel": ["no"] * 5,
            "Darvas_Signal": [""] * 5,
            "LTP": [1.0] * 5,
            "Piotroski_Score": [5] * 5,
        }
    )
    _install(monkeypatch, dc, {"Fundamentals": fund})
    df = dc.get_fundamental_picks("IN").set_index("Symbol")
    assert (
        df.loc["A", "Tier"] == "Single-Screen"
        and df.loc["A", "N_Screens"] == 2
        and df.loc["B", "Tier"] == "Multi-Screen"
    )  # exactly 3 screens


def test_picks_no_files_runs_scan_or_returns_empty(dc, monkeypatch):
    ran = []
    monkeypatch.setattr(dc.glob, "glob", lambda pat: [])
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda cmd, timeout: ran.append((cmd, timeout)))
    assert dc.get_fundamental_picks("US").empty
    assert (
        ran[0][0][1] == "full_us_market_scan.py"
        and ran[0][0][-2:] == ["--min-price", "2"]
        and ran[0][1] == 14400
    )
    ran.clear()
    dc.get_fundamental_picks("IN", run_fresh=True)
    assert ran[0][0][1] == "full_indian_market_scan.py" and "--min-price" not in ran[0][0]
    _install(monkeypatch, dc, {})
    assert dc.get_fundamental_picks(
        "IN"
    ).empty  # a workbook without any known sheet yields no picks
    assert len(ran) == 1


def test_street_talk(dc, monkeypatch):
    class S:
        def __init__(self, score, label, n, heads):
            self.score, self.label, self.n_articles, self.top_headlines = score, label, n, heads

    class SP:
        def get_market_mood(self, market):
            return {"mood": "POSITIVE", "market": market}

        def get_batch(self, symbols, market):
            return {
                s: S(0.5, "POSITIVE", 3, [{"title": "a"}, {"title": "b"}, {"title": "c"}])
                for s in symbols
            }

    fake_module(monkeypatch, "sentiment_pipeline", SentimentPipeline=SP)
    mood, talk = dc.get_street_talk(["AAA"], "US")
    assert (
        mood == {"mood": "POSITIVE", "market": "US"}
        and talk["AAA"]["headlines"] == [{"title": "a"}, {"title": "b"}]
        and talk["AAA"]["n_articles"] == 3
    )
    assert dc.get_street_talk([], "IN")[1] == {}
    monkeypatch.setitem(__import__("sys").modules, "sentiment_pipeline", None)
    assert dc.get_street_talk(["A"], "IN") == ({}, {})


def _picks():
    return pd.DataFrame(
        {
            "Symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
            "Tier": ["Triple Hit"] * 5,
            "Screens": ["x"] * 5,
            "N_Screens": [3] * 5,
            "Piotroski": [9] * 5,
            "LTP": [1.0] * 5,
        }
    )


def _talk():
    mk = lambda s, l, h: {"score": s, "label": l, "n_articles": 4, "headlines": h}  # noqa: E731
    return {
        "AAA": mk(0.6, "POSITIVE", [{"title": "T" * 100}]),
        "BBB": mk(0.8, "POSITIVE", []),
        "CCC": mk(-0.4, "NEGATIVE", [{"title": "bad"}]),
        "DDD": mk(0.0, "NEUTRAL", []),
        "EEE": mk(0.0, "NO_DATA", []),
    }


def test_find_convergence_ranking(dc):
    c = dc.find_convergence(_picks(), _talk())
    assert list(c["Symbol"]) == [
        "BBB",
        "AAA",
        "DDD",
        "CCC",
    ]  # both-bullish (by score), neutral, negative; NO_DATA / missing dropped
    assert list(c["Convergence"]) == [
        "✅ BOTH BULLISH",
        "✅ BOTH BULLISH",
        "— news neutral",
        "⚠️ NEWS NEGATIVE",
    ]
    assert (
        c.set_index("Symbol").loc["AAA", "Top_Headline"] == "T" * 80
        and c.set_index("Symbol").loc["BBB", "Top_Headline"] == ""
    )
    assert dc.find_convergence(_picks(), {}).empty


def test_build_report_caps_symbols_and_composes(dc, monkeypatch):
    big = pd.DataFrame({"Symbol": [f"S{i}" for i in range(40)], "Tier": "Single-Screen"})
    monkeypatch.setattr(dc, "get_fundamental_picks", lambda m, f: big)
    seen = {}
    monkeypatch.setattr(
        dc,
        "get_street_talk",
        lambda syms, m: seen.update(n=len(syms), m=m)
        or ({"mood": "NEUTRAL", "score": 0.0, "n_articles": 1}, {}),
    )
    rep = dc.build_report("IN", False)
    assert (
        seen == {"n": 25, "m": "IN"}
        and rep["market"] == "IN"
        and rep["convergence"].empty
        and rep["mood"]["mood"] == "NEUTRAL"
    )
    monkeypatch.setattr(dc, "get_fundamental_picks", lambda m, f: pd.DataFrame())
    seen.clear()
    assert dc.build_report("US", True)["picks"].empty and seen["n"] == 0


def _rep(dc, with_conv=True):
    picks, talk = _picks(), _talk()
    return {
        "market": "IN",
        "generated": "2026-01-01T00:00:00",
        "picks": picks,
        "talk": talk,
        "convergence": dc.find_convergence(picks, talk) if with_conv else pd.DataFrame(),
        "mood": {"mood": "POSITIVE", "score": 0.25, "n_articles": 12},
    }


def test_print_report_sections(dc, capsys):
    dc.print_report(_rep(dc))
    out = capsys.readouterr().out
    for needle in (
        "COMPONENT 1",
        "AAA",
        "COMPONENT 2",
        "Market mood: POSITIVE (+0.25) from 12 articles (Moneycontrol/ET/BusinessLine/LiveMint)",
        "+0.80",
        "2 HIGH-CONVICTION",
        "✅ BBB",
        "1 CAUTION",
        "⚠️  CCC",
        dc.DISCLAIMER,
    ):
        assert needle in out, needle
    assert (
        "EEE " not in out.split("COMPONENT 2")[1].split("CONVERGENCE")[0]
    )  # NO_DATA rows are not listed
    r = _rep(dc, with_conv=False)
    r.update(picks=pd.DataFrame(), talk={}, mood={})
    r["market"] = "US"
    dc.print_report(r)
    out = capsys.readouterr().out
    assert (
        "No fundamental picks today." in out
        and "No picks had matching news coverage today." in out
        and "Market mood" not in out
    )


def test_to_html_and_save(dc, tmp_path, capsys):
    rep = _rep(dc)
    html = dc.to_html(rep)
    for needle in (
        "<b>AAA</b>",
        "Moneycontrol · ET",
        "#2e7d32",
        "+0.80",
        "High-conviction",
        "Caution",
        dc.DISCLAIMER,
    ):
        assert needle in html, needle
    assert "EEE" not in html.split("Talk on the Street")[1].split("Convergence")[0]
    rep["market"] = "US"
    assert "CNBC · MarketWatch" in dc.to_html(rep)
    empty = dict(rep, convergence=pd.DataFrame(), picks=pd.DataFrame(), talk={}, mood={})
    assert "No fundamental picks had news coverage today." in dc.to_html(empty)
    dc.save(rep, emit_html=True)
    js = next(dc.OUT_DIR.glob("combined_US_*.json"))
    payload = json.loads(js.read_text())
    assert (
        payload["market"] == "US"
        and len(payload["picks"]) == 5
        and len(payload["convergence"]) == 4
        and payload["mood"]["score"] == 0.25
    )
    assert js.with_suffix(".html").exists() and "[html]" not in capsys.readouterr().out
    dc.save(empty, emit_html=False)
    assert json.loads(sorted(dc.OUT_DIR.glob("combined_US_*.json"))[-1].read_text())["picks"] == []


def test_main_wires_components(dc, monkeypatch):
    calls = []
    monkeypatch.setattr(dc, "build_report", lambda m, f: calls.append(("build", m, f)) or {"r": 1})
    monkeypatch.setattr(dc, "print_report", lambda rep: calls.append(("print", rep)))
    monkeypatch.setattr(dc, "save", lambda rep, html: calls.append(("save", html)))
    monkeypatch.setattr("sys.argv", ["dc", "--market", "US", "--run-fresh", "--html"])
    dc.main()
    assert calls == [("build", "US", True), ("print", {"r": 1}), ("save", True)]
