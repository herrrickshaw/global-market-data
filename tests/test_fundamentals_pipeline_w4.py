"""fundamental_metrics (fetch routing, Piotroski/ROCE maths, screens), validation.report, pipeline CRUD."""

import json
import sys
import time

import numpy as np
import pandas as pd
import pytest

import fundamental_metrics as fm
import pipeline as pl
import validation as val
from tests.w4_helpers import fake_module


# ---------------- fundamental_metrics: pure maths ----------------
def _good_piotroski():
    return {
        "net_income": 5, "roa": 1, "cfo": 9,
        "debt_to_assets": 0.1, "debt_to_assets_prev": 0.2,
        "current_ratio": 2, "current_ratio_prev": 1,
        "shares": 10, "shares_prev": 10,
        "gross_margin": 5, "gross_margin_prev": 4,
        "asset_turnover": 2, "asset_turnover_prev": 1,
    }


def test_piotroski_all_nine_and_each_point_toggles():
    assert fm._piotroski(_good_piotroski()) == 9
    flips = {
        "net_income": 0, "roa": 0, "cfo": 0,  # cfo=0 also kills accrual -> separate check below
        "debt_to_assets": 0.3, "current_ratio": 0, "shares": 11, "gross_margin": 3, "asset_turnover": 0,
    }
    base = _good_piotroski()
    for k, v in flips.items():
        r = dict(base, **{k: v})
        expected = 9 - (2 if k == "cfo" else 1)
        assert fm._piotroski(r) == expected, k
    assert fm._piotroski(dict(base, cfo=4)) == 8  # accrual needs cfo > net_income (4 < 5), cfo still > 0


def test_piotroski_missing_or_nan_never_scores():
    assert fm._piotroski({}) == 0
    r = _good_piotroski()
    r["roa"] = float("nan")
    r["cfo"] = None
    assert fm._piotroski(r) == 9 - 3  # roa, cfo>0, accrual all lost
    assert fm._piotroski(dict(_good_piotroski(), shares=10, shares_prev=None)) == 8
    assert fm._piotroski(dict(_good_piotroski(), shares=10.0, shares_prev=10.0)) == 9  # <= (equal passes)
    assert fm._piotroski({"net_income": 0, "roa": 0}) == 0  # strictly positive


def test_roce_variants():
    assert fm._roce({"ebit": 20, "total_assets": 100}) == 20.0
    assert fm._roce({"ebit": 20, "assets": 50}) == 40.0
    assert fm._roce({"ebit": 20, "total_assets": 100, "assets": 50}) == 20.0  # total_assets first
    assert fm._roce({"ebit": 20, "roa": 7}) == 7  # no capital -> ROA fallback
    assert fm._roce({"ebit": 0, "total_assets": 100, "roa": 3}) == 3
    assert fm._roce({}) is None


def test_first_val_variants():
    assert fm._first_val([3, 4]) == 3 and fm._first_val((5,)) == 5 and fm._first_val(np.array([7, 8])) == 7
    assert fm._first_val([]) is None and fm._first_val(np.array([])) is None
    assert fm._first_val(None) is None and fm._first_val(float("nan")) is None
    assert fm._first_val(9.5) == 9.5 and fm._first_val(0) == 0


def test_yield_helpers():
    assert fm._earnings_yield({"ebit": 8, "market_cap": 100}) == 8.0
    assert fm._earnings_yield({"ebit": 0, "market_cap": 100}) is None
    assert fm._earnings_yield({"ebit": 8}) is None
    assert fm._fcf_yield({"cfo": 30, "capex_history": [10], "market_cap": 100}) == 20.0
    assert fm._fcf_yield({"cfo": 30, "market_cap": 100}) == 30.0  # no capex
    assert fm._fcf_yield({"cfo": 30, "capex_history": [], "market_cap": 100}) == 30.0
    assert fm._fcf_yield({"cfo": -5, "capex_history": [5], "market_cap": 100}) == -10.0  # negative cfo allowed
    for bad in ({"market_cap": 100}, {"cfo": 1, "market_cap": 0}, {"cfo": 1, "market_cap": None}, {"cfo": 1, "market_cap": float("nan")}):
        assert fm._fcf_yield(bad) is None
    assert fm._dividend_yield({"dividend_history": [-3.0], "market_cap": 100}) == 3.0  # abs()
    assert fm._dividend_yield({"dividend_history": [0], "market_cap": 100}) is None
    assert fm._dividend_yield({"dividend_history": [3], "market_cap": float("nan")}) is None
    assert fm._dividend_yield({"dividend_history": [3]}) is None


def test_enrich_adds_columns_without_mutating():
    df = pd.DataFrame([dict(_good_piotroski(), Symbol="A", ebit=20, total_assets=100, market_cap=200, capex_history=[1], dividend_history=[4])])
    out = fm._enrich(df)
    assert out.loc[0, "piotroski"] == 9 and out.loc[0, "roce"] == 20.0
    assert out.loc[0, "earnings_yield"] == 10.0 and out.loc[0, "div_yield"] == 2.0
    assert "piotroski" not in df.columns


def _screen_frame():
    return pd.DataFrame(
        {
            "Symbol": ["A", "B", "C", "D"],
            "piotroski": [9, 8, 7, 0],
            "roce": [25.0, 20.0, 21.0, 16.0],
            "roe": [25.0, 20.0, 16.0, 15.0],
            "debt_to_equity": [0.0, 0.1, 0.29, 0.5],
            "earnings_yield": [9.0, 8.0, 8.1, 20.0],
            "fcf_yield": [6.0, 5.0, 5.1, np.nan],
            "div_yield": [4.0, 3.0, 3.1, 0.0],
            "eps_growth": [20.0, 15.0, 16.0, 0.0],
        }
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("piotroski_9", ["A", "B"]),  # >= 8
        ("high_roce", ["A", "C"]),  # > 20 strict
        ("high_roe", ["A"]),  # > 20 strict
        ("debt_free", ["A"]),  # < 0.1 strict
        ("low_debt_equity", ["A", "B", "C"]),  # < 0.3
        ("magic_formula", ["A", "C", "D"]),  # ey > 8 strict (B is exactly 8) and roce > 15
        ("fcf_yield", ["A", "C"]),
        ("high_dividend", ["A", "C"]),
        ("eps_growth", ["A", "C"]),  # > 15 strict (B is exactly 15)
        ("coffee_can", ["A", "B", "C"]),  # roe>15 & de<0.5
    ],
)
def test_fund_screens_thresholds(name, expected):
    d = _screen_frame()
    got = list(d.loc[fm.FUND_SCREENS[name](d).fillna(False), "Symbol"])
    assert got == expected


def test_fund_screens_missing_columns_select_nothing():
    d = _screen_frame().drop(columns=["roe", "debt_to_equity", "eps_growth"])
    for n in ("high_roe", "debt_free", "low_debt_equity", "eps_growth", "coffee_can"):
        m = fm.FUND_SCREENS[n](d).fillna(False)
        assert len(m) == 0 and int(m.sum()) == 0  # an empty (index-less) mask, never a crash


@pytest.mark.xfail(
    strict=True,
    raises=pd.errors.IndexingError,
    reason="fundamental_metrics.run_screen indexes the frame with the screen mask; for a column the "
    "frame lacks (e.g. roe on SEC data) the mask is an empty Series and pandas raises "
    "'Unalignable boolean Series' instead of returning no rows (run_all guards this, run_screen does not)",
)
def test_run_screen_on_frame_missing_the_screen_column(monkeypatch):
    frame = pd.DataFrame({"Symbol": ["A", "B"], "piotroski": [1, 2], "roce": [1.0, 2.0]})
    monkeypatch.setattr(fm, "load_fundamentals", lambda m, limit: frame)
    assert fm.run_screen("high_roe", "US").empty


# ---------------- fundamental_metrics: fetch routing ----------------
def test_fetch_one_routes_by_market(monkeypatch):
    fake_module(monkeypatch, "sec_fundamentals", fundamentals=lambda s: {"src": "sec", "s": s} if s != "NONE" else None)
    assert fm._fetch_one("US", "AAPL") == {"src": "sec", "s": "AAPL"}
    assert fm._fetch_one("US", "NONE") == {}
    monkeypatch.setattr(fm, "_yf_fundamentals", lambda m, s: {"src": "yf", "m": m})
    assert fm._fetch_one("JP", "7203") == {"src": "yf", "m": "JP"}


def test_fetch_one_india_source_order(monkeypatch):
    order = []
    monkeypatch.setattr(fm, "_in_trendlyne", lambda s: order.append("tl") or {})
    monkeypatch.setattr(fm, "_in_screener", lambda s: order.append("sc") or {"src": "screener"})
    monkeypatch.setattr(fm, "_yf_fundamentals", lambda m, s: order.append("yf") or {"src": "yf"})
    assert fm._fetch_one("IN", "X") == {"src": "screener"} and order == ["tl", "sc"]
    order.clear()
    monkeypatch.setattr(fm, "_in_trendlyne", lambda s: order.append("tl") or {"src": "tl"})
    assert fm._fetch_one("IN", "X") == {"src": "tl"} and order == ["tl"]
    order.clear()
    monkeypatch.setattr(fm, "_in_trendlyne", lambda s: 1 / 0)
    monkeypatch.setattr(fm, "_in_screener", lambda s: {})
    assert fm._fetch_one("IN", "X") == {"src": "yf"}  # both fail/empty -> yfinance


def test_in_trendlyne_and_screener_wrappers(monkeypatch):
    monkeypatch.setattr(fm, "_TL_SESSION", None)
    made = []
    fake_module(monkeypatch, "trendlyne_auth", session=lambda: made.append(1) or "SESS", fundamentals=lambda s, sess: {"s": s, "sess": sess} if s != "E" else None)
    assert fm._in_trendlyne("A") == {"s": "A", "sess": "SESS"} and fm._in_trendlyne("E") == {}
    assert made == [1]  # session minted once
    fake_module(monkeypatch, "screener_in_auth", company_financials=lambda s: {"x": s} if s != "E" else None)
    assert fm._in_screener("A") == {"x": "A"} and fm._in_screener("E") == {}


class _YFT:
    def __init__(self, info):
        self.info = info


def _yf_with(monkeypatch, info, record=None):
    def ticker(sym):
        if record is not None:
            record.append(sym)
        if isinstance(info, Exception):
            raise info
        return _YFT(info)

    fake_module(monkeypatch, "yfinance", Ticker=ticker)


def test_yf_fundamentals_mapping_and_suffix(monkeypatch):
    seen = []
    info = {
        "trailingPE": float("nan"), "forwardPE": 18.0, "returnOnEquity": 0.25, "debtToEquity": 150.0,
        "netIncomeToCommon": 5, "totalRevenue": 100, "trailingEps": 2.0, "marketCap": 1e9,
        "grossMargins": 0.4, "earningsGrowth": 0.12,
    }
    _yf_with(monkeypatch, info, seen)
    out = fm._yf_fundamentals("JP", "7203")
    assert seen == ["7203.T"]
    assert out == {
        "source": "yfinance", "pe": 18.0, "roe": 25.0, "debt_to_equity": 1.5, "net_income": 5,
        "revenue": 100, "eps_ttm": 2.0, "market_cap": 1e9, "gross_margin": 40.0, "profit_growth": 12.0,
    }
    seen.clear()
    fm._yf_fundamentals("ZZ", "X")
    assert seen == ["X"]  # unknown market: no suffix


def test_yf_fundamentals_edge_values(monkeypatch):
    _yf_with(monkeypatch, {"debtToEquity": 5.0, "returnOnEquity": "n/a", "trailingPE": ""})
    out = fm._yf_fundamentals("IN", "X")
    assert out == {"source": "yfinance", "debt_to_equity": 5.0}  # exactly 5 is NOT rescaled; blanks dropped
    _yf_with(monkeypatch, {"debtToEquity": 5.01})
    assert fm._yf_fundamentals("IN", "X")["debt_to_equity"] == pytest.approx(0.0501)
    _yf_with(monkeypatch, {"trailingPE": 0.0})
    assert fm._yf_fundamentals("IN", "X")["pe"] == 0.0  # zero is a real value
    _yf_with(monkeypatch, {})
    assert fm._yf_fundamentals("IN", "X") == {}
    _yf_with(monkeypatch, RuntimeError("down"))
    assert fm._yf_fundamentals("IN", "X") == {}


# ---------------- fundamental_metrics: loading / caching ----------------
@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(fm, "CACHE", tmp_path)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return tmp_path


def _serving(monkeypatch, df):
    fake_module(monkeypatch, "serving_layer", serving=lambda m: df)


def test_liquid_symbols_and_prices(monkeypatch):
    df = pd.DataFrame({"Symbol": ["A", "B", "C"], "Close": [1.0, 2.0, 3.0], "Turnover_USD": [5.0, 9.0, 1.0]})
    _serving(monkeypatch, df)
    assert fm._liquid_symbols("US", 2) == ["B", "A"]
    assert fm._prices("US") == {"A": 1.0, "B": 2.0, "C": 3.0}
    _serving(monkeypatch, df.drop(columns="Turnover_USD"))
    assert fm._liquid_symbols("US", 2) == ["A", "B"]  # no turnover: serving order
    _serving(monkeypatch, pd.DataFrame())
    assert fm._liquid_symbols("US", 2) == [] and fm._prices("US") == {}


def test_load_fundamentals_fetches_only_missing_and_limits(monkeypatch, cache, capsys):
    _serving(monkeypatch, pd.DataFrame({"Symbol": ["A", "B", "C", "D"], "Close": [10.0, 20.0, 30.0, 40.0]}))
    pd.DataFrame({"Symbol": ["A"], "pe": [9.0]}).to_parquet(cache / "US.parquet")
    calls = []

    def fetch(market, sym):
        calls.append(sym)
        return {"pe": 1.0, "shares": 2.0} if sym != "C" else {}

    monkeypatch.setattr(fm, "_fetch_one", fetch)
    out = fm.load_fundamentals("US", limit=2, symbols=["A", "B", "C", "D"])
    assert calls == ["B", "C"]  # A cached; limit applies after filtering the cached ones
    assert set(out["Symbol"]) == {"A", "B"}  # C returned nothing
    b = out.set_index("Symbol").loc["B"]
    assert b["price"] == 20.0 and b["market_cap"] == 40.0  # price * shares
    assert "fundamentals[US]: +1 fetched, 2 total cached" in capsys.readouterr().out


def test_load_fundamentals_default_pool_and_checkpoint(monkeypatch, cache, capsys):
    syms = [f"S{i:02d}" for i in range(30)]
    _serving(monkeypatch, pd.DataFrame({"Symbol": syms, "Close": 1.0}))
    flushes = []
    monkeypatch.setattr(fm, "_fetch_one", lambda m, s: {"pe": 1.0})
    real = pd.DataFrame.to_parquet
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, p, **k: flushes.append(len(self)) or real(self, p, **k))
    out = fm.load_fundamentals("IN", limit=30)
    assert len(out) == 30 and "market_cap" not in out  # no shares column -> no market cap
    assert flushes[0] == 25 and flushes[-1] == 30  # checkpoint at 25, final flush merges the rest
    assert "fetched 25/30" in capsys.readouterr().out


def test_load_fundamentals_us_pauses_politely_and_dedups(monkeypatch, cache):
    _serving(monkeypatch, pd.DataFrame({"Symbol": ["A", "B"], "Close": [1.0, 2.0]}))
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(fm, "_fetch_one", lambda m, s: {"pe": 5.0})
    fm.load_fundamentals("US", symbols=["A", "B"], verbose=False)
    assert sleeps == [0.12, 0.12]
    sleeps.clear()
    fm.load_fundamentals("IN", symbols=["A", "B"], verbose=False)
    assert sleeps == []
    assert len(pd.read_parquet(cache / "US.parquet")) == 2


def test_load_fundamentals_nothing_cached_returns_empty(monkeypatch, cache):
    _serving(monkeypatch, pd.DataFrame())
    out = fm.load_fundamentals("US", verbose=False)
    assert out.empty


def test_run_all_counts_and_empty(monkeypatch, capsys):
    frame = pd.DataFrame(
        [dict(_good_piotroski(), Symbol="A", roe=30, debt_to_equity=0.0, ebit=20, total_assets=50, market_cap=100, eps_growth=40)]
    )
    monkeypatch.setattr(fm, "load_fundamentals", lambda m, limit, verbose: frame)
    counts = fm.run_all("US", limit=5)
    assert counts["piotroski_9"] == 1 and counts["high_roe"] == 1 and counts["debt_free"] == 1
    assert counts["magic_formula"] == 1 and counts["high_dividend"] == 0
    assert set(counts) == set(fm.FUND_SCREENS)
    assert "fundamental screens on US (1 names" in capsys.readouterr().out
    monkeypatch.setattr(fm, "load_fundamentals", lambda m, limit, verbose: pd.DataFrame())
    assert fm.run_all("US", verbose=False) == {} and capsys.readouterr().out == ""
    fm.run_all("US")
    assert "no fundamentals for US" in capsys.readouterr().out


def test_run_all_isolates_screen_errors(monkeypatch):
    frame = pd.DataFrame([{"Symbol": "A", "roe": 30.0}])
    monkeypatch.setattr(fm, "load_fundamentals", lambda m, limit, verbose: frame)
    counts = fm.run_all("US", verbose=False)  # screens needing absent columns fail -> counted as 0
    assert counts["high_roe"] == 1 and counts["piotroski_9"] == 0 and counts["fcf_yield"] == 0


def test_run_screen_sorting_columns_and_top(monkeypatch):
    rows = [
        {"Symbol": "A", "net_income": 1, "roe": 25.0, "ebit": 10, "total_assets": 100, "market_cap": 50},
        {"Symbol": "B", "piotroski": 0, "roe": 30.0, "ebit": 40, "total_assets": 100, "market_cap": 50},
        {"Symbol": "C", "piotroski": 0, "roe": 10.0, "ebit": 90, "total_assets": 100, "market_cap": 50},
    ]
    monkeypatch.setattr(fm, "load_fundamentals", lambda m, limit: pd.DataFrame(rows))
    out = fm.run_screen("high_roe", "US")
    assert list(out["Symbol"]) == ["A", "B"]  # non-roce screens sort by piotroski (A=1, B=0), desc
    mf = fm.run_screen("magic_formula", "US")
    assert list(mf["Symbol"]) == ["C", "B"]  # sorted by roce desc: C 90, B 40 (A: ey 20 but roce 10)
    assert list(mf.columns) == ["Symbol", "piotroski", "roce", "roe", "earnings_yield", "fcf_yield", "div_yield"]
    assert list(fm.run_screen("magic_formula", "US", top=1)["Symbol"]) == ["C"]
    assert len(fm.run_screen("magic_formula", "US", top=None)) == 2
    assert fm.run_screen("bogus", "US").empty


def test_fm_main(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(fm, "run_all", lambda m, limit: seen.update(all=(m, limit)))
    monkeypatch.setattr("sys.argv", ["fm", "--market", "IN", "--limit", "5"])
    assert fm.main() == 0 and seen["all"] == ("IN", 5)
    monkeypatch.setattr(fm, "run_screen", lambda n, m, limit, top: pd.DataFrame({"Symbol": ["A"], "roce": [1.234]}))
    monkeypatch.setattr("sys.argv", ["fm", "--screen", "high_roce"])
    assert fm.main() == 0
    out = capsys.readouterr().out
    assert "high_roce on US — 1 names" in out and "1.23" in out
    monkeypatch.setattr(fm, "run_screen", lambda *a, **k: pd.DataFrame())
    fm.main()
    assert "none / no fundamentals" in capsys.readouterr().out


# ---------------- validation (report / public screens) ----------------
def test_public_fundamental_only_india(monkeypatch):
    fake_module(monkeypatch, "public_screens", cached_screens=lambda: {"magic": {"A"}})
    assert val._public_fundamental("IN") == {"pub::magic": {"A"}}
    assert val._public_fundamental("US") == {}
    fake_module(monkeypatch, "public_screens", cached_screens=lambda: 1 / 0)
    assert val._public_fundamental("IN") == {}


def test_local_screens_quality_filters(monkeypatch):
    df = pd.DataFrame(
        {
            "Symbol": ["A", "B", "C"], "Close": [100.0, 100.0, 100.0], "SMA200": [100.0, 103.0, 104.0],
            "RSI14": [50, 50, 50], "PctFromHigh": [0, 0, 0], "PctFromLow": [10, 10, 10], "Ret126": [0, 0, 0],
            "Ret252": [0, 0, 0], "Above200DMA": [True, True, True], "GoldenCross": [True, True, True],
            "Liquidity": ["High", "Low", "Medium"],
        }
    )
    monkeypatch.setattr(val.sl, "serving", lambda m: df)
    scr = val.local_screens("US")
    assert scr["golden_crossover"] == {"A", "C"}  # Low liquidity excluded
    assert scr["stocks_near_200dma"] == {"A"}  # |close/sma200 - 1| <= 3%: A 0%, C 3.8% out
    assert scr["52w_high_breakout"] == {"A", "C"} and scr["steady_uptrend"] == {"A", "C"}
    df2 = df.assign(Close=[100.0, 100.0, 97.0], SMA200=[100.0, 100.0, 100.0])
    monkeypatch.setattr(val.sl, "serving", lambda m: df2)
    assert val.local_screens("US")["stocks_near_200dma"] == {"A"}  # 3.0% boundary: 97/100-1 = -3% -> abs 0.03 <= 0.03 for C
    # (C has Medium liquidity so it is eligible; boundary is inclusive)


def test_local_screens_bad_predicate_gives_empty_set(monkeypatch):
    df = pd.DataFrame({"Symbol": ["A"], "GoldenCross": [True], "Liquidity": ["High"]})  # missing most columns
    monkeypatch.setattr(val.sl, "serving", lambda m: df)
    scr = val.local_screens("US")
    assert scr["golden_crossover"] == {"A"} and scr["darvas_scan"] == set() and scr["rsi_oversold"] == set()
    monkeypatch.setattr(val.sl, "serving", lambda m: pd.DataFrame())
    assert val.local_screens("US") == {} and val.known_universe("US") == set()


def test_validate_empty_picks_and_per_screen_ordering(monkeypatch):
    monkeypatch.setattr(val, "local_screens", lambda m: {"s1": {"A", "B"}, "s2": {"A"}, "s3": {"Z"}})
    assert val.validate([], "IN") == {"n": 0, "grounded_pct": 0.0, "novel_pct": 0.0, "per_screen": {}}
    r = val.validate(["A", "B", "X", "A"], "IN")  # duplicates collapse
    assert r["n"] == 3 and r["grounded_pct"] == pytest.approx(0.667, abs=1e-3) and r["novel_pct"] == pytest.approx(0.333, abs=1e-3)
    assert list(r["per_screen"].items()) == [("s1", 0.667), ("s2", 0.333)]  # s3 omitted, sorted desc
    monkeypatch.setattr(val, "local_screens", lambda m: {})
    assert val.validate(["A"], "IN")["grounded_pct"] == 0.0


def test_report_prints_and_handles_recommend_errors(monkeypatch, capsys):
    monkeypatch.setattr(val, "local_screens", lambda m: {"s1": {"A", "B"}, "s2": {"A"}})
    fake_module(monkeypatch, "auto_screener", recommend=lambda market, verbose: {"picks": ["A", "Q"]})
    out = val.report("IN")
    assert out["local_screens"] == {"s1": 2, "s2": 1} and out["validation"]["n"] == 2
    txt = capsys.readouterr().out
    assert "local popular screens computed: 2" in txt
    assert txt.index("s1") < txt.index("s2")  # larger screens first
    assert "auto-screener recommendation (2 picks)" in txt and "grounded in popular screens: 50%" in txt
    assert "overlaps: s1 50%, s2 50%" in txt
    fake_module(monkeypatch, "auto_screener", recommend=lambda market, verbose: 1 / 0)
    out = val.report("IN", verbose=False)
    assert out["validation"] == {"error": "division by zero"} and capsys.readouterr().out == ""
    val.report("IN")
    assert "auto-screener recommendation" not in capsys.readouterr().out


def test_validation_main(monkeypatch):
    seen = []
    monkeypatch.setattr(val, "report", lambda m: seen.append(m))
    monkeypatch.setattr("sys.argv", ["v", "--market", "US"])
    assert val.main() == 0 and seen == ["US"]


# ---------------- pipeline ----------------
@pytest.fixture
def pipe(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "STATE", tmp_path / "state.json")
    return pl


def test_state_load_corrupt_and_today(pipe, tmp_path):
    assert pipe._load() == {}
    pipe.STATE.write_text("{bad")
    assert pipe._load() == {}
    assert len(pipe._today()) == 10 and pipe._today()[4] == "-"


def test_filter_clearers_unions_sources_and_isolates_failures(pipe, monkeypatch):
    calls = []

    def run_screen(name, market, min_turnover_usd):
        calls.append((name, market, min_turnover_usd))
        if name == "breakout_stocks":
            raise RuntimeError("boom")  # aborts the remaining screens inside this try
        return pd.DataFrame({"Symbol": ["A", "B"]}) if name == "golden_crossover" else pd.DataFrame()

    fake_module(monkeypatch, "screen_metrics", run_screen=run_screen)
    fake_module(monkeypatch, "auto_screener", fundamental_anchor=lambda m: ["A", "C"])
    fake_module(monkeypatch, "validation", known_universe=lambda m: {"A", "D"})
    hits = pipe.filter_clearers("IN", 5.0)
    assert hits == {"A": ["fundamental", "golden_crossover", "popular"], "B": ["golden_crossover"], "C": ["fundamental"], "D": ["popular"]}
    assert [c[0] for c in calls] == ["golden_crossover", "companies_creating_new_high", "price_volume_action", "breakout_stocks"]
    assert all(c[2] == 5.0 for c in calls)


def test_filter_clearers_all_sources_failing(pipe, monkeypatch):
    fake_module(monkeypatch, "screen_metrics", run_screen=lambda *a, **k: 1 / 0)
    fake_module(monkeypatch, "auto_screener", fundamental_anchor=lambda m: 1 / 0)
    fake_module(monkeypatch, "validation", known_universe=lambda m: 1 / 0)
    assert pipe.filter_clearers("IN") == {}


def test_promote_counts_and_messages(pipe, capsys):
    assert pipe.promote("IN", {"A": ["x"], "B": ["y"]}) == 2
    assert "promote[IN]: +2 new, 2 on watchlist" in capsys.readouterr().out
    assert pipe.promote("IN", {"A": ["x2"], "C": ["z"]}, verbose=False) == 1
    p = pipe.promoted("IN")
    assert p["A"]["filters"] == ["x2"] and p["A"]["refreshed"] is None and set(p) == {"A", "B", "C"}
    assert pipe.promoted("XX") == {}


def test_demote_counts_trim_and_failure(pipe, monkeypatch, capsys):
    pipe.promote("IN", {"A": ["x"], "B": ["y"]}, verbose=False)
    dropped = []
    fake_module(monkeypatch, "market_memory", drop=lambda m, syms, verbose: dropped.append((m, syms, verbose)))
    assert pipe.demote("IN", ["A", "ZZ"], trim_ltm=True) == 1
    assert dropped == [("IN", ["A"], False)]  # only symbols that were actually promoted
    assert "demote[IN]: -1, 1 remain" in capsys.readouterr().out
    assert pipe.demote("IN", ["B"], trim_ltm=False, verbose=False) == 1 and len(dropped) == 1
    pipe.promote("IN", {"C": ["x"]}, verbose=False)
    fake_module(monkeypatch, "market_memory", drop=lambda *a, **k: 1 / 0)
    assert pipe.demote("IN", ["C"], trim_ltm=True, verbose=False) == 1  # trim failure swallowed
    assert pipe.demote("IN", ["nobody"], trim_ltm=True, verbose=False) == 0
    assert len(dropped) == 1  # nothing to trim -> drop never called


def test_sync_records_timestamp_and_min_filters_default(pipe, monkeypatch, capsys):
    monkeypatch.setattr(pipe, "filter_clearers", lambda m, t: {"X": ["a"], "Y": ["a", "b"]})
    r = pipe.sync("IN")
    assert r == {"market": "IN", "watchlist": 1, "promoted": 1, "demoted": 0}
    assert pipe._load()["IN"]["synced"] is not None
    assert "sync[IN]: watchlist 1 (+1 promoted, -0 demoted)" in capsys.readouterr().out
    r = pipe.sync("IN", min_filters=1, verbose=False)
    assert r["watchlist"] == 2 and r["promoted"] == 1


def test_fund_count_and_refresh(pipe, monkeypatch, tmp_path, capsys):
    pipe.promote("IN", {"A": ["x"], "B": ["y"]}, verbose=False)
    fcache = tmp_path / "fund"
    fcache.mkdir()
    seen = {}

    def load_fundamentals(market, limit, verbose, symbols):
        seen.update(market=market, limit=limit, verbose=verbose, symbols=symbols)
        pd.DataFrame({"Symbol": ["A", "B", "C"]}).to_parquet(fcache / "IN.parquet")

    fake_module(monkeypatch, "fundamental_metrics", CACHE=fcache, load_fundamentals=load_fundamentals)
    assert pipe._fund_count("IN") == 0
    out = pipe.refresh("IN", fund_limit=7)
    assert out == {"market": "IN", "promoted": 2, "fundamentals_fetched": 3}
    assert seen == {"market": "IN", "limit": 7, "verbose": False, "symbols": ["A", "B"]}
    assert all(v["refreshed"] == pipe._today() for v in pipe.promoted("IN").values())
    txt = capsys.readouterr().out
    assert "refresh[IN]: 2 promoted stocks" in txt and "+3 promoted names" in txt


def test_refresh_without_fetch_empty_watch_and_failure(pipe, monkeypatch, capsys):
    out = pipe.refresh("IN", verbose=False)
    assert out == {"market": "IN", "promoted": 0, "fundamentals_fetched": 0}
    pipe.promote("IN", {"A": ["x"]}, verbose=False)
    fake_module(monkeypatch, "fundamental_metrics", CACHE=None, load_fundamentals=lambda **k: 1 / 0)
    capsys.readouterr()
    out = pipe.refresh("IN", fetch_fundamentals=False, verbose=False)
    assert out["fundamentals_fetched"] == 0 and pipe.promoted("IN")["A"]["refreshed"] == pipe._today()
    out = pipe.refresh("IN")  # fetch attempted, fails -> message, still marks refreshed
    assert "fundamentals fetch skipped (" in capsys.readouterr().out and out["fundamentals_fetched"] == 0
    assert pipe._fund_count("IN") == 0  # CACHE=None path swallowed


def test_status_table_and_main(pipe, monkeypatch, capsys):
    assert pipe.status().empty
    pipe.promote("US", {"A": ["x"], "B": ["y"]}, verbose=False)
    pipe.promote("IN", {"C": ["z"]}, verbose=False)
    state = pipe._load()
    state["US"]["promoted"]["A"]["refreshed"] = "2026-01-01"
    state["US"]["synced"] = "t"
    pipe._save(state)
    df = pipe.status().set_index("market")
    assert df.loc["US", "watchlist"] == 2 and df.loc["US", "fund_refreshed"] == 1 and df.loc["US", "synced"] == "t"
    assert list(pipe.status(["IN"])["market"]) == ["IN"]
    monkeypatch.setattr("sys.argv", ["p", "--status"])
    assert pipe.main() == 0 and "US" in capsys.readouterr().out
    pipe.STATE.write_text("{}")
    pipe.main()
    assert "no watchlist yet — run --sync" in capsys.readouterr().out


def test_pipeline_main_dispatch(pipe, monkeypatch):
    calls = []
    monkeypatch.setattr(pipe, "sync", lambda m, t, f: calls.append(("sync", m, t, f)))
    monkeypatch.setattr(pipe, "refresh", lambda m: calls.append(("refresh", m)))
    monkeypatch.setattr("sys.argv", ["p"])
    pipe.main()
    assert calls == [("sync", "IN", 1_000_000, 2)]  # default action = sync
    calls.clear()
    monkeypatch.setattr("sys.argv", ["p", "--refresh", "--market", "US"])
    pipe.main()
    assert calls == [("refresh", "US")]
    calls.clear()
    monkeypatch.setattr("sys.argv", ["p", "--sync", "--refresh", "--market", "US", "--min-turnover", "5", "--min-filters", "3"])
    pipe.main()
    assert calls == [("sync", "US", 5.0, 3), ("refresh", "US")]
    calls.clear()
    monkeypatch.setattr("sys.argv", ["p", "--all", "--refresh"])
    pipe.main()
    assert len(calls) == 20 and calls[0] == ("refresh", "IN")
