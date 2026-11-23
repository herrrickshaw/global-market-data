"""fetch_market_ohlc, scan_bhavcopy, correlation_analysis, run_global_analysis."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import frames
from tests.w4_helpers import fake_module, fresh_import


def _px(n, start=100.0, step=1.0, vol=1000.0):
    c = start + step * np.arange(n, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": vol},
        index=pd.date_range("2023-01-01", periods=n),
    )


class _FakeWriter:
    """Stand-in for pd.ExcelWriter (openpyxl is not installed in CI)."""

    sheets = []

    def __init__(self, path, engine=None):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def no_excel(monkeypatch):
    _FakeWriter.sheets = []
    monkeypatch.setattr(pd, "ExcelWriter", _FakeWriter)
    monkeypatch.setattr(
        pd.DataFrame,
        "to_excel",
        lambda self, xw, sheet=None, index=False: _FakeWriter.sheets.append((sheet, len(self))),
    )
    return _FakeWriter


# ---------------- fetch_market_ohlc ----------------
@pytest.fixture
def fmo(monkeypatch, tmp_path):
    fake_module(monkeypatch, "data_sources", fetch=lambda *a, **k: {})
    fake_module(
        monkeypatch,
        "universe_sources",
        PROVIDERS={"JP": lambda: [], "US": lambda: []},
        get_universe=lambda m: [],
    )
    m = fresh_import(monkeypatch, "fetch_market_ohlc")
    monkeypatch.setattr(m, "SEED_DIR", tmp_path)
    return m


def test_tickers_per_market(fmo, monkeypatch):
    fake_module(
        monkeypatch, "full_european_market_scan", EURO_STOXX_50_META={"SAP.DE": 1, "MC.PA": 2}
    )
    assert fmo._tickers("EU") == ["SAP.DE", "MC.PA"]
    fake_module(
        monkeypatch,
        "full_japan_market_scan",
        fetch_tse_universe_jpx=lambda: [{"code": "7203"}, {"yf_ticker": "6758.T"}, {"x": 1}],
        fetch_tse_universe_kabupy=lambda: [{"code": "9984"}],
    )
    assert fmo._tickers("JP") == ["7203.T", "6758.T"]
    fake_module(
        monkeypatch,
        "full_japan_market_scan",
        fetch_tse_universe_jpx=lambda: 1 / 0,
        fetch_tse_universe_kabupy=lambda: [{"code": "9984"}],
    )
    assert fmo._tickers("JP") == ["9984.T"]
    fake_module(
        monkeypatch,
        "full_korea_market_scan",
        build_krx_universe=lambda: [{"code": "005930"}, {"code": "1", "yf_suffix": ".KQ"}, {}],
    )
    assert fmo._tickers("KR") == ["005930.KS", "1.KQ"]
    fake_module(monkeypatch, "sg_stock_daily_report", SGX_ALL=["D05.SI", "O39.SI", "D05.SI"])
    assert fmo._tickers("SG") == ["D05.SI", "O39.SI"]
    cn = fmo._tickers("CN")
    assert (
        len(cn) == len(set(cn))
        and cn[0] == "600519.SS"
        and all(t.endswith((".SS", ".SZ")) for t in cn)
    )
    with pytest.raises(ValueError, match="unknown market ZZ"):
        fmo._tickers("ZZ")


def test_write_seed_cleans_and_summarises(fmo, tmp_path):
    good = _px(80)
    bad = _px(80)
    bad["Close"] = -1.0
    short = _px(59)
    out = fmo._write_seed("JP", {"A": good, "B": bad, "C": short})
    assert (
        out["market"] == "JP"
        and out["symbols"] == 1
        and out["rows"] == 80
        and out["file"] == "cleaned_long_JP.parquet"
    )
    assert set(frames.read_seed(tmp_path / "cleaned_long_JP.parquet")) == {
        "A"
    }  # <60 bars and bad prices dropped
    assert fmo._write_seed("KR", {"C": short}) == {"market": "KR", "symbols": 0}
    assert not (tmp_path / "cleaned_long_KR.parquet").exists()
    assert fmo._write_seed("KR", {}) == {"market": "KR", "symbols": 0}
    exact = fmo._write_seed("SG", {"D": _px(60)})
    assert exact["symbols"] == 1  # min_bars=60 is inclusive


def test_run_uses_provider_when_available_else_local_list(fmo, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(fmo, "PROVIDERS", {"US": None})
    monkeypatch.setattr(fmo, "get_universe", lambda m: ["AAPL", "MSFT"])
    monkeypatch.setattr(fmo, "_tickers", lambda m: ["LOCAL1"])
    monkeypatch.setattr(
        fmo,
        "multi_fetch",
        lambda tickers, **k: seen.setdefault(tickers[0], k) and {t: _px(80) for t in tickers},
    )
    summary = fmo.run(["US", "CN"])
    assert (
        set(summary) == {"US", "CN"}
        and summary["US"]["symbols"] == 2
        and summary["CN"]["symbols"] == 1
    )
    assert seen["AAPL"] == {
        "order": ("yahoo", "stooq"),
        "period": "1y",
        "min_bars": 60,
        "verbose": True,
    }
    assert "LOCAL1" in seen  # CN not in PROVIDERS -> local list
    out = capsys.readouterr().out
    assert "2 tickers; downloading 1y OHLC" in out and "DONE:" in out


def test_run_universe_failure_skips_market_and_flag(fmo, monkeypatch, capsys):
    monkeypatch.setattr(fmo, "PROVIDERS", {"US": None})
    monkeypatch.setattr(fmo, "get_universe", lambda m: 1 / 0)
    monkeypatch.setattr(fmo, "multi_fetch", lambda *a, **k: pytest.fail("must not fetch"))
    assert fmo.run(["US"]) == {} and "universe fetch failed" in capsys.readouterr().out
    monkeypatch.setattr(fmo, "USE_FULL_UNIVERSE", False)
    monkeypatch.setattr(fmo, "_tickers", lambda m: [])
    monkeypatch.setattr(fmo, "multi_fetch", lambda *a, **k: {})
    assert fmo.run(["US"]) == {"US": {"market": "US", "symbols": 0}}  # flag off: provider ignored


# ---------------- scan_bhavcopy ----------------
@pytest.fixture
def sb(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake_module(monkeypatch, "bhavcopy_history", fetch_history=lambda **k: {})
    fake_module(
        monkeypatch,
        "full_indian_market_scan",
        compute_darvas_box=lambda df: {
            "signal": "BREAKOUT_BUY",
            "box_top": 12.5,
            "box_bottom": 9.0,
            "upside_to_top_pct": 3.2,
            "position_in_box_pct": 80.0,
        },
        compute_golden_crossover=lambda df: {"gc_signal": True, "dma50_above_200": True},
    )
    return fresh_import(monkeypatch, "scan_bhavcopy")


def test_screen_one_row_and_guards(sb):
    df = _px(60, start=100.0, step=1.0)
    row = sb._screen_one(("ABC", df))
    assert row == {
        "Symbol": "ABC",
        "LTP": 159.0,
        "Prev_Close": 158.0,
        "Change%": 0.63,
        "Darvas_Signal": "BREAKOUT_BUY",
        "Box_Top": 12.5,
        "Box_Bottom": 9.0,
        "Upside_to_Top%": 3.2,
        "Position_in_Box%": 80.0,
        "GC_Signal": "GOLDEN_CROSS",
        "DMA50_above_200": True,
        "Data_Points": 60,
    }
    assert sb._screen_one(("X", _px(59))) is None and sb._screen_one(("X", None)) is None
    assert sb._screen_one(("X", _px(60))) is not None  # exactly 60 bars qualifies


def test_screen_one_no_cross_and_flat_change(sb, monkeypatch):
    monkeypatch.setattr(
        sb, "compute_golden_crossover", lambda df: {"gc_signal": False, "dma50_above_200": False}
    )
    monkeypatch.setattr(sb, "compute_darvas_box", lambda df: {})
    row = sb._screen_one(("X", _px(60, step=0.0)))
    assert (
        row["GC_Signal"] == ""
        and row["Change%"] == 0.0
        and row["Darvas_Signal"] is None
        and row["DMA50_above_200"] is False
    )


def _hist_for_main():
    return {"A": _px(80), "B": _px(80, start=50), "SHORT": _px(10)}


def test_scan_main_writes_workbook_and_reports(sb, monkeypatch, no_excel, capsys):
    monkeypatch.setattr(
        sb,
        "fetch_history",
        lambda **k: _hist_for_main() | {"_kw": k} if False else _hist_for_main(),
    )
    seen = {}
    monkeypatch.setattr(sb, "fetch_history", lambda **k: seen.update(k) or _hist_for_main())
    monkeypatch.setattr(sb, "parallel_map", lambda fn, items, **k: [fn(i) for i in items])
    out = sb.main()
    assert seen == {"n_days": 400, "min_bars": 200, "verbose": True}
    assert (
        out.parent == sb.OUT_DIR
        and out.name.startswith("indian_full_scan_")
        and out.suffix == ".xlsx"
    )
    names = [s for s, _ in no_excel.sheets]
    assert names == [
        "All_Stocks",
        "Darvas_Signals",
    ]  # no fundamentals reused -> no Fundamentals / Triple_Hits sheets
    assert dict(no_excel.sheets)["All_Stocks"] == 2  # SHORT filtered by _screen_one
    txt = capsys.readouterr().out
    assert (
        "2 screened | 2 Darvas signals | 2 golden crosses" in txt
        and "Triple Hits: none today" in txt
    )


def test_scan_main_reuses_fundamentals_and_finds_triple_hits(sb, monkeypatch, no_excel, capsys):
    monkeypatch.setattr(sb, "fetch_history", lambda **k: _hist_for_main())
    monkeypatch.setattr(sb, "parallel_map", lambda fn, items, **k: [fn(i) for i in items])
    Path("indian_full_scan").mkdir(exist_ok=True)
    (Path("indian_full_scan") / "a_full_scan_20260101.xlsx").write_text("x")
    (Path("indian_full_scan") / "a_full_scan_20260201.xlsx").write_text("x")  # latest sorts last
    read = []
    fund = pd.DataFrame(
        {
            "Symbol": ["A", "B"],
            "CoffeeCan": ["pass", "PASS"],
            "Piotroski_Strong": ["yes", "NO"],
            "Darvas_Signal": ["x", "x"],
            "LTP": [1.0, 1.0],
            "Piotroski_Score": [8, 5],
        }
    )
    monkeypatch.setattr(pd, "read_excel", lambda p, sheet_name: read.append(str(p)) or fund.copy())
    sb.main()
    assert read[0].endswith("a_full_scan_20260201.xlsx")
    names = [s for s, _ in no_excel.sheets]
    assert names == ["All_Stocks", "Darvas_Signals", "Fundamentals", "Triple_Hits"]
    assert (
        dict(no_excel.sheets)["Triple_Hits"] == 1
    )  # only A: CC pass + Piotroski YES + fresh BREAKOUT_BUY
    txt = capsys.readouterr().out
    assert (
        "reused fundamentals from a_full_scan_20260201.xlsx (2 rows)" in txt
        and "1 Triple Hits" in txt
        and "Triple Hits: A" in txt
    )


def test_scan_main_fundamentals_read_failure(sb, monkeypatch, no_excel, capsys):
    monkeypatch.setattr(sb, "fetch_history", lambda **k: _hist_for_main())
    monkeypatch.setattr(sb, "parallel_map", lambda fn, items, **k: [fn(i) for i in items])
    Path("indian_full_scan").mkdir(exist_ok=True)
    (Path("indian_full_scan") / "z_full_scan_1.xlsx").write_text("x")
    monkeypatch.setattr(pd, "read_excel", lambda *a, **k: 1 / 0)
    sb.main()
    assert "no fundamentals reused" in capsys.readouterr().out
    assert [s for s, _ in no_excel.sheets] == ["All_Stocks", "Darvas_Signals"]


# ---------------- correlation_analysis ----------------
@pytest.fixture
def corr(monkeypatch, tmp_path):
    import correlation_analysis as ca

    monkeypatch.setattr(ca, "FUND_DIR", tmp_path)
    return ca


def _ltm(corr, monkeypatch, data):
    monkeypatch.setattr(corr.mm, "read", lambda m, tier: data)


def test_perf_features_values_and_guards(corr, monkeypatch):
    close = 100 * 1.001 ** np.arange(300)
    data = {
        "OK": _px(300, start=100.0, step=0.0).assign(Close=close),
        "SHORT": _px(119),
        "ZERO": _px(150, start=0.0, step=0.0),
        "NONE": None,
    }
    _ltm(corr, monkeypatch, data)
    f = corr._perf_features("IN").set_index("Symbol")
    assert list(f.index) == ["OK"]
    r = f.loc["OK"]
    assert r["cagr"] == pytest.approx((close[-1] / close[0]) ** (252 / 300) - 1)
    assert r["mom_12m"] == pytest.approx(close[-1] / close[-252] - 1)
    assert r["max_drawdown"] == 0.0
    assert np.isnan(r["sharpe"]) or r["sharpe"] > 100  # constant returns: std ~ 0
    rng = np.random.default_rng(5)
    noisy = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 300))
    _ltm(corr, monkeypatch, {"N": _px(300).assign(Close=noisy)})
    n = corr._perf_features("IN").iloc[0]
    rets = np.diff(noisy) / noisy[:-1]
    assert n["sharpe"] == pytest.approx(rets.mean() / rets.std() * np.sqrt(252))
    assert n["vol"] == pytest.approx(rets.std() * np.sqrt(252))
    peak = np.maximum.accumulate(noisy)
    assert (
        n["max_drawdown"] == pytest.approx(((noisy - peak) / peak).min()) and n["max_drawdown"] < 0
    )


def test_perf_features_min_bars_and_short_momentum(corr, monkeypatch):
    _ltm(corr, monkeypatch, {"E": _px(120)})
    f = corr._perf_features("IN")
    assert len(f) == 1 and np.isnan(f["mom_12m"].iloc[0])  # <= 252 bars: no 12m momentum
    _ltm(corr, monkeypatch, {"E": _px(253)})
    assert not np.isnan(corr._perf_features("IN")["mom_12m"].iloc[0])  # needs len > 252


def test_spearman_scipy_and_numpy_paths(corr, monkeypatch):
    x = pd.Series([1.0, 2, 3, 4, 5, 6])
    y = pd.Series([2.0, 1, 4, 3, 6, 5])
    rho, p, n = corr._spearman(x, y)
    assert n == 6 and rho == pytest.approx(0.8286, abs=1e-3)
    short = corr._spearman(x.iloc[:4], y.iloc[:4])
    assert np.isnan(short[0]) and np.isnan(short[1]) and short[2] == 4
    assert corr._spearman(pd.Series([1.0, np.nan, 3, 4, 5, 6]), y)[2] == 5  # NaNs dropped pairwise
    fake_module(monkeypatch, "scipy")
    fake_module(
        monkeypatch,
        "scipy.stats",
        spearmanr=lambda a, b: (_ for _ in ()).throw(RuntimeError("no scipy")),
    )
    rho2, p2, n2 = corr._spearman(x, y)
    assert (
        rho2 == pytest.approx(0.8286, abs=1e-3) and np.isnan(p2) and n2 == 6
    )  # rank-corr fallback


def test_analyse_ranks_metrics_and_flags_significance(corr, monkeypatch, tmp_path):
    rng = np.random.default_rng(2)
    n = 40
    syms = [f"S{i}" for i in range(n)]
    growth = np.linspace(0.0, 0.002, n)  # higher metric -> higher drift
    data = {
        s: _px(300).assign(Close=100 * np.cumprod(1 + np.full(300, g)))
        for s, g in zip(syms, growth)
    }
    _ltm(corr, monkeypatch, data)
    fund = pd.DataFrame({"Symbol": syms, "roe": growth * 1000, "noise": rng.random(n), "name": "x"})
    fund.to_parquet(tmp_path / "IN.parquet")
    fake_module(monkeypatch, "scipy")
    fake_module(
        monkeypatch,
        "scipy.stats",
        spearmanr=lambda a, b: (0.99, 0.001) if a.name == "roe" else (0.1, 0.5),
    )
    res = corr.analyse("IN")
    assert list(res["metric"]) == [
        "roe",
        "noise",
    ]  # strongest |rho| first; non-numeric & PERF columns excluded
    assert list(res["significant"]) == [True, False] and list(res["n"]) == [40, 40]
    assert "abs_rho" not in res.columns
    other = corr.analyse("IN", target="sharpe")
    assert "roe" in set(other["metric"])


def test_analyse_empty_cases(corr, monkeypatch, tmp_path):
    assert corr.analyse("IN").empty  # no fundamentals file
    pd.DataFrame({"Symbol": []}).to_parquet(tmp_path / "IN.parquet")
    _ltm(corr, monkeypatch, {"A": _px(300)})
    assert corr.analyse("IN").empty
    pd.DataFrame({"Symbol": ["ZZZ"], "roe": [1.0]}).to_parquet(tmp_path / "IN.parquet")
    assert corr.analyse("IN").empty  # no overlap with LTM symbols
    pd.DataFrame({"Symbol": ["A"], "roe": [1.0]}).to_parquet(tmp_path / "IN.parquet")
    assert corr.analyse("IN").empty  # < 5 paired observations -> NaN rho -> nothing reported
    _ltm(corr, monkeypatch, {})
    assert corr.analyse("IN").empty


def test_correlation_main(corr, monkeypatch, capsys):
    monkeypatch.setattr(corr, "analyse", lambda m, t: pd.DataFrame())
    monkeypatch.setattr("sys.argv", ["c"])
    assert corr.main() == 0 and "IN: no overlap" in capsys.readouterr().out
    res = pd.DataFrame(
        {
            "metric": ["roe", "pe"],
            "spearman_rho": [0.5, 0.1],
            "p_value": [0.01, 0.9],
            "n": [30, 28],
            "significant": [True, False],
        }
    )
    seen = {}
    monkeypatch.setattr(corr, "analyse", lambda m, t: seen.update(m=m, t=t) or res)
    monkeypatch.setattr("sys.argv", ["c", "--market", "US", "--target", "sharpe"])
    assert corr.main() == 0 and seen == {"m": "US", "t": "sharpe"}
    out = capsys.readouterr().out
    assert (
        "US — fundamentals vs sharpe" in out
        and "1 metric(s) significant at p<0.05 (n up to 30)" in out
    )


# ---------------- run_global_analysis ----------------
@pytest.fixture
def rga(monkeypatch, tmp_path, no_excel):
    monkeypatch.chdir(tmp_path)
    m = fresh_import(monkeypatch, "run_global_analysis")
    monkeypatch.setattr(m, "HIGHLIGHTS_PARQUET", tmp_path / "hl.parquet")
    return m


def _momentum_px(step):
    """Up-trend with a zig-zag so RSI stays below the 75 cap, liquid enough for the volume floor."""
    df = _px(300, start=100.0, step=step, vol=1_000_000.0)
    zig = (np.arange(300) % 2) * 2.0
    for c in ("Open", "High", "Low", "Close"):
        df[c] = df[c] + zig
    return df


def _data(trend):
    return {f"S{i}": _momentum_px(trend + 0.1 * i) for i in range(3)}


def test_analyse_builds_global_ranking_and_saves(rga, monkeypatch, capsys):
    loads = []
    monkeypatch.setattr(
        rga.kit,
        "load",
        lambda m, t: loads.append((m, t))
        or (_data(0.5) if m == "IN" else _data(0.2) if m == "US" else {}),
    )
    monkeypatch.setattr(
        rga.kit,
        "screen",
        lambda slug, m, min_turnover_usd: (
            pd.DataFrame({"Symbol": ["A"]}) if slug == "darvas" else pd.DataFrame()
        ),
    )
    out = rga.analyse(["IN", "US", "JP"], top_per_market=2, min_turnover_usd=5.0)
    assert loads == [("IN", 5.0), ("US", 5.0), ("JP", 5.0)]
    assert set(out["per_market"]) == {"IN", "US"}  # JP had no data
    g = out["global"]
    assert g["ret_126"].is_monotonic_decreasing and set(g["Market"]) == {"IN", "US"}
    assert len(g) == 4 and g.groupby("Market").size().max() == 2  # top_per_market respected
    assert list(g.columns[:2]) == ["Market", "Symbol"]
    saved = pd.read_parquet(rga.HIGHLIGHTS_PARQUET)
    assert (
        "as_of" in saved.columns and len(saved) == 4 and rga.load_highlights().shape == saved.shape
    )
    txt = capsys.readouterr().out
    assert (
        "■ IN: 3 stocks | Darvas 1 | GoldenCross 0 | momentum 2" in txt
        and "GLOBAL TOP 20 MOMENTUM" in txt
    )
    assert "saved → global_highlights.xlsx and hl.parquet" in txt


def test_analyse_quiet_empty_and_default_markets(rga, monkeypatch, capsys, no_excel):
    monkeypatch.setattr(rga.kit, "markets", lambda: ["IN"])
    monkeypatch.setattr(rga.kit, "load", lambda m, t: {})
    out = rga.analyse(verbose=False)
    assert out["per_market"] == {} and out["global"].empty and capsys.readouterr().out == ""
    assert not rga.HIGHLIGHTS_PARQUET.exists() and no_excel.sheets == []


def test_analyse_filters_momentum_criteria(rga, monkeypatch):
    weak = {"W": _px(300, start=300.0, step=-0.5)}  # downtrend: fails ret_126 / 200dma
    monkeypatch.setattr(rga.kit, "load", lambda m, t: weak)
    monkeypatch.setattr(rga.kit, "screen", lambda *a, **k: pd.DataFrame())
    out = rga.analyse(["IN"], verbose=False)
    assert out["per_market"]["IN"].empty and out["global"].empty
    thin = {"T": _momentum_px(0.5).assign(Volume=100.0)}  # momentum ok but volume below 50k
    monkeypatch.setattr(rga.kit, "load", lambda m, t: thin)
    assert rga.analyse(["IN"], verbose=False)["global"].empty
    assert rga.MOMENTUM["avg_vol_20"] == (">", 50000) and rga.MOMENTUM["dist_52w_high"] == ("<", 8)


def test_load_highlights_missing(rga):
    assert rga.load_highlights().empty
