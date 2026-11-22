"""build_mailer.build (HTML brief assembly) and run.py CLI dispatch."""

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


# ---------------- build_mailer ----------------
def _highlights():
    rows = []
    for i in range(20):
        rows.append({"Market": "US", "Symbol": f"U{i:02d}", "ret_126": 100.0 - i, "rsi14": 50 + i})
    rows += [
        {"Market": "JP", "Symbol": "J1", "ret_126": 12.0, "rsi14": 55},
        {"Market": "JP", "Symbol": "J2", "ret_126": 40.0, "rsi14": 60},
        {"Market": "KR", "Symbol": "K1", "ret_126": -3.0, "rsi14": 40},
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def bm(monkeypatch, tmp_path):
    def annotate(df):
        df = df.copy()
        tmap = {"AAA": 25e6, "BBB": 3e6, "CCC": 0.1e6}
        if "Turnover_USD" not in df:
            df["Turnover_USD"] = df["Symbol"].map(tmap)
        df["Liquidity"] = [
            "High" if (t or 0) > 10e6 else "Medium" if (t or 0) > 1e6 else "Low"
            for t in df["Turnover_USD"]
        ]
        return df

    fake_module(monkeypatch, "liquidity", annotate=annotate)
    fake_module(
        monkeypatch,
        "market_performance",
        load=lambda: pd.DataFrame(
            [{"Market": "US", "Index": "S&P 500", "CAGR%": 12.3, "Return_1y%": 8.1, "Sharpe": 0.9}]
        ),
    )
    fake_module(monkeypatch, "run_global_analysis", load_highlights=_highlights)
    m = fresh_import(monkeypatch, "build_mailer")

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 9)

    monkeypatch.setattr(m._dt, "date", FakeDate)
    monkeypatch.chdir(tmp_path)
    return m


def _write_combined(tmp_path, picks, mood=None, name="combined_IN_20260309.json"):
    d = tmp_path / "combined_report_results"
    d.mkdir(exist_ok=True)
    (d / name).write_text(
        json.dumps(
            {"mood": mood or {"mood": "Bullish", "score": 0.256, "n_articles": 7}, "picks": picks}
        )
    )


def test_tv_formatting(bm):
    assert bm._tv(25_000_000) == "$25.0M"
    assert bm._tv(1_260_000) == "$1.3M"
    assert bm._tv(float("nan")) == "—"
    assert bm._tv(None) == "—"


def test_table_helper(bm):
    t = bm._table(["A", "B"], ["<tr><td>1</td></tr>"])
    assert t.count("<th") == 2 and "<td>1</td>" in t and t.startswith("<table")


def test_build_without_inputs(bm):
    subject, text, html = bm.build()
    assert subject == "📈 Daily Market Brief — 09 Mar 2026"
    assert "India mood: n/a (+0.00)." in text
    assert "<p>no picks</p>" in html and "<p>n/a</p>" in html  # no picks, no CCC
    assert "from 0 articles" in html
    assert "U00" in html and "U14" in html and "U15" not in html  # top 15 only
    assert "S&P 500" in html and "12.3" in html


def test_build_picks_sorted_by_tier_then_liquidity_and_capped(bm, tmp_path):
    picks = [
        {"Symbol": "CCC", "Tier": "Single-Screen", "Screens": 1},
        {"Symbol": "BBB", "Tier": "Triple Hit", "Screens": 3},
        {"Symbol": "AAA", "Tier": "Triple Hit", "Screens": 3},
        {"Symbol": "MULTI", "Tier": "Multi-Screen", "Screens": 2},
        {"Symbol": "WEIRD", "Tier": "Other", "Screens": 0},
    ] + [{"Symbol": f"X{i}", "Tier": "Single-Screen", "Screens": 1} for i in range(10)]
    _write_combined(tmp_path, picks)
    _, text, html = bm.build()
    order = [html.index(f"<b>{s}</b>") for s in ("AAA", "BBB", "MULTI", "CCC")]
    assert order == sorted(order)  # triple (High before Medium), multi, then single
    shown = [
        s
        for s in ["AAA", "BBB", "MULTI", "CCC", "WEIRD"] + [f"X{i}" for i in range(10)]
        if f"<b>{s}</b>" in html
    ]
    assert len(shown) == 12  # head(12)
    assert "WEIRD" not in shown  # unknown tier ranks last, so it is cut by the cap
    assert "$25.0M" in html and "#1b7f37" in html  # High liquidity colour
    assert "Bullish (+0.26)" in html
    assert "from 7 articles" in html
    assert "India mood: Bullish (+0.26)." in text


def test_build_uses_latest_combined_file(bm, tmp_path):
    _write_combined(
        tmp_path,
        [{"Symbol": "OLDX", "Tier": "Triple Hit", "Screens": 3}],
        name="combined_IN_20250101.json",
    )
    _write_combined(
        tmp_path,
        [{"Symbol": "NEWX", "Tier": "Triple Hit", "Screens": 3}],
        name="combined_IN_20260101.json",
    )
    _, _, html = bm.build()
    assert "NEWX" in html and "OLDX" not in html


def test_build_ccc_filters_liquidity_sorts_and_caps(bm, tmp_path):
    (tmp_path / "cache_seed").mkdir()
    rows = [
        {
            "Symbol": f"S{i:02d}",
            "Name": f"n{i}",
            "Cash_Cycle": str(i - 3),
            "ROCE": "9",
            "Liquidity": "High",
        }
        for i in range(12)
    ]
    rows.append(
        {"Symbol": "LOWQ", "Name": "x", "Cash_Cycle": "-99", "ROCE": "1", "Liquidity": "Low"}
    )
    rows.append(
        {"Symbol": "BADCC", "Name": "x", "Cash_Cycle": "oops", "ROCE": "1", "Liquidity": "Medium"}
    )
    pd.DataFrame(rows).to_parquet(tmp_path / "cache_seed" / "india_ccc_screen.parquet")
    _, _, html = bm.build()
    sec = html.split("Cash Conversion Cycle")[1].split("Global Momentum")[0]
    assert "LOWQ" not in sec and "BADCC" not in sec  # low-liquidity out, NaN sorted last & capped
    assert "S00" in sec and "S09" in sec and "S10" not in sec  # 10 rows, ascending CCC
    assert sec.index("S00") < sec.index("S01")
    assert "-3.0" in sec


def test_build_other_markets_top_mover_each(bm):
    _, _, html = bm.build()
    sec = html.split("top tradable mover each")[1].split("Scoreboard")[0]
    assert "Japan" in sec and "<b>J2</b>" in sec and "J1" not in sec  # best 6m return
    assert "+40%" in sec and "Korea" in sec and "-3%" in sec
    assert "Taiwan" not in sec
    assert sec.index("Korea") < sec.index("Japan")  # order of the names mapping, not alphabetical


def test_build_swallow_other_markets_failure(bm, monkeypatch):
    def annotate(df):
        raise RuntimeError("x")

    monkeypatch.setattr(bm.liq, "annotate", annotate)
    _, _, html = bm.build()
    assert "Other Markets" in html and "<p>n/a</p>" in html


# ---------------- run.py ----------------
@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv("BHAV_CACHE", "preset")
    with monkeypatch.context() as ctx:
        ctx.setattr(Path, "mkdir", lambda *a, **k: None)
        m = fresh_import(monkeypatch, "run")
    monkeypatch.setattr(m, "RESULTS", tmp_path)
    return m


class Kit:
    MARKETS = ["IN", "US"]

    def __init__(self):
        self.boot = []
        self.screens = []
        self.result = pd.DataFrame({"Symbol": ["AAA"], "Score": [1]})

    def bootstrap(self, verbose=True):
        self.boot.append(verbose)

    def screen(self, slug, market, top, min_turnover_usd):
        self.screens.append((slug, market, top, min_turnover_usd))
        return self.result


def _setup(monkeypatch, info=None, info_raises=False):
    kit = Kit()
    fake_module(
        monkeypatch, "screener_kit", MARKETS=Kit.MARKETS, bootstrap=kit.bootstrap, screen=kit.screen
    )

    def _info():
        if info_raises:
            raise RuntimeError("no store")
        return info

    fake_module(monkeypatch, "bhavcopy_store", info=_info)
    fake_module(monkeypatch, "strategies", STRATEGIES={"darvas": 1, "garp": 2})
    return kit


def test_import_does_not_override_preset_env(run):
    import os

    assert os.environ["BHAV_CACHE"] == "preset"


def test_ensure_ready_skips_bootstrap_when_store_has_symbols(run, monkeypatch, capsys):
    kit = _setup(monkeypatch, info={"symbols": 1})
    assert run._ensure_ready().screen == kit.screen
    assert kit.boot == []


@pytest.mark.parametrize("info", [{"symbols": 0}, {}])
def test_ensure_ready_bootstraps_on_empty_store(run, monkeypatch, capsys, info):
    kit = _setup(monkeypatch, info=info)
    run._ensure_ready()
    assert kit.boot == [True]
    assert "First run — bootstrapping" in capsys.readouterr().out
    run._ensure_ready(verbose=False)
    assert kit.boot == [True, False] and capsys.readouterr().out == ""


def test_ensure_ready_bootstraps_when_store_errors(run, monkeypatch):
    kit = _setup(monkeypatch, info_raises=True)
    run._ensure_ready(verbose=False)
    assert kit.boot == [False]


def test_main_list(run, monkeypatch, capsys):
    kit = _setup(monkeypatch, info={"symbols": 1})
    monkeypatch.setattr("sys.argv", ["run", "--list"])
    run.main()
    out = capsys.readouterr().out
    assert "Strategies: darvas, garp" in out and "Markets:    IN, US" in out
    assert kit.boot == [] and kit.screens == []


def test_main_strategy_writes_csv_and_defaults(run, monkeypatch, capsys, tmp_path):
    kit = _setup(monkeypatch, info={"symbols": 1})
    monkeypatch.setattr("sys.argv", ["run", "--strategy", "darvas"])
    run.main()
    assert kit.screens == [("darvas", "IN", 20, 1_000_000)]
    assert (tmp_path / "darvas_IN.csv").read_text().splitlines() == ["Symbol,Score", "AAA,1"]
    out = capsys.readouterr().out
    assert "AAA" in out and "NOT investment advice" in out
    monkeypatch.setattr(
        "sys.argv",
        ["run", "--strategy", "garp", "--market", "US", "--top", "5", "--min-turnover", "0"],
    )
    run.main()
    assert kit.screens[-1] == ("garp", "US", 5, 0.0)
    assert (tmp_path / "garp_US.csv").exists()


def test_main_strategy_no_hits(run, monkeypatch, capsys, tmp_path):
    kit = _setup(monkeypatch, info={"symbols": 1})
    kit.result = pd.DataFrame()
    monkeypatch.setattr("sys.argv", ["run", "--strategy", "darvas", "--market", "JP"])
    run.main()
    assert capsys.readouterr().out.strip() == "No hits for darvas on JP."
    assert not list(tmp_path.glob("*.csv"))


def test_main_brief(run, monkeypatch, capsys, tmp_path):
    _setup(monkeypatch, info={"symbols": 1})
    fake_module(monkeypatch, "build_mailer", build=lambda: ("SUBJ", "t", "<html>"))
    monkeypatch.setattr("sys.argv", ["run", "--brief", "--strategy", "darvas"])  # brief wins
    run.main()
    assert (tmp_path / "daily_brief.html").read_text() == "<html>"
    assert capsys.readouterr().out.startswith("SUBJ\nsaved → ")


@pytest.mark.parametrize("argv", [["--global"], []])
def test_main_global_leaderboard(run, monkeypatch, capsys, tmp_path, argv):
    kit = _setup(monkeypatch, info={"symbols": 1})
    seen = {}
    g = pd.DataFrame({"Symbol": [f"S{i}" for i in range(30)], "ret": range(30)})

    def analyse(min_turnover_usd):
        seen["mt"] = min_turnover_usd
        return {"global": g}

    fake_module(monkeypatch, "run_global_analysis", analyse=analyse)
    monkeypatch.setattr("sys.argv", ["run", "--top", "3", "--min-turnover", "7", *argv])
    run.main()
    assert seen["mt"] == 7.0 and kit.screens == []
    assert len(pd.read_csv(tmp_path / "global_momentum.csv")) == 30
    out = capsys.readouterr().out
    assert "S2" in out and "S3 " not in out and "S3\n" not in out  # head(top)


def test_main_global_empty_writes_nothing(run, monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, info={"symbols": 1})
    fake_module(
        monkeypatch,
        "run_global_analysis",
        analyse=lambda min_turnover_usd: {"global": pd.DataFrame()},
    )
    monkeypatch.setattr("sys.argv", ["run", "--global"])
    run.main()
    assert not list(tmp_path.glob("*.csv")) and capsys.readouterr().out == ""
