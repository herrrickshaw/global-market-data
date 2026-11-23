"""pattern_discovery: cleaning, feature engineering, clustering/drivers/pairs, insights, main."""

import sys

import numpy as np
import pandas as pd
import pytest

from tests import r2_fakes
from tests.w4_helpers import fresh_import


@pytest.fixture
def pdm(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r2_fakes.install_sklearn(monkeypatch)
    mod = fresh_import(monkeypatch, "pattern_discovery")
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mod, "REF_DIR", tmp_path / "ref")
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path / "out")
    for d in ("cache", "ref", "out"):
        (tmp_path / d).mkdir()
    return mod


def ohlcv(closes, vol=1000.0, open_mult=1.0, high_mult=1.0):
    c = np.asarray(closes, dtype=float)
    idx = pd.date_range("2022-01-03", periods=len(c), freq="B")
    return pd.DataFrame(
        {
            "Open": c * open_mult,
            "High": c * high_mult,
            "Low": c,
            "Close": c,
            "Volume": np.full(len(c), vol, dtype=float),
        },
        index=idx,
    )


def walk(seed, n=300, drift=0.0004):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))


# ---------------------------------------------------------------- load_and_clean


def _write(d, name, df):
    df.to_parquet(d / f"{name}.parquet")


def test_load_and_clean_filters_and_cleans(pdm, tmp_path, capsys):
    good = ohlcv(np.linspace(10, 20, 260))
    _write(pdm.CACHE_DIR, "AAA.NS", good)
    _write(pdm.CACHE_DIR, "SHORT", ohlcv(np.linspace(10, 20, 251)))  # < min_bars
    _write(pdm.CACHE_DIR, "EXACT", ohlcv(np.linspace(10, 20, 252)))  # == min_bars kept
    bad_close = good.copy()
    bad_close.iloc[5, bad_close.columns.get_loc("Close")] = 0.0
    _write(pdm.CACHE_DIR, "ZERO", bad_close)
    _write(pdm.CACHE_DIR, "NOVOL", ohlcv(np.linspace(10, 20, 260), vol=0.0))
    (pdm.CACHE_DIR / "CORRUPT.parquet").write_bytes(b"not parquet")
    _write(pdm.CACHE_DIR, "EMPTY", good.iloc[0:0])
    _write(pdm.REF_DIR, "AAA.NS", ohlcv(np.linspace(1, 2, 260)))  # duplicate symbol in ref dir
    _write(pdm.REF_DIR, "REFONLY.BO", good)
    out = pdm.load_and_clean("ALL")
    assert set(out) == {"AAA.NS", "EXACT", "REFONLY.BO"}
    assert out["AAA.NS"]["Close"].iloc[0] == 10  # cache dir copy wins
    assert list(out["EXACT"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    txt = capsys.readouterr().out
    assert "Loaded 3 clean stocks | dropped 5" in txt


def test_load_and_clean_market_filters(pdm):
    g = ohlcv(np.linspace(10, 20, 260))
    for s in ("A.NS", "B.BO", "C", "D.NS"):
        _write(pdm.CACHE_DIR, s, g)
    assert set(pdm.load_and_clean("IN")) == {"A.NS", "D.NS"}
    assert set(pdm.load_and_clean("US")) == {"C"}
    assert set(pdm.load_and_clean("ALL")) == {"A.NS", "B.BO", "C", "D.NS"}


def test_load_and_clean_max_stocks_and_min_bars(pdm, capsys):
    g = ohlcv(np.linspace(10, 20, 260))
    for s in ("A", "B", "C"):
        _write(pdm.CACHE_DIR, s, g)
    assert len(pdm.load_and_clean("ALL", max_stocks=2)) == 2
    assert "Candidate files: 2" in capsys.readouterr().out
    assert len(pdm.load_and_clean("ALL", max_stocks=0)) == 3  # 0 = no cap
    assert pdm.load_and_clean("ALL", min_bars=261) == {}


def test_load_and_clean_dedupes_index_sorts_and_fills(pdm):
    df = ohlcv(np.linspace(10, 20, 260))
    dup = pd.concat([df, df.iloc[[3]].assign(Close=99.0)]).sort_index(kind="stable")
    shuffled = dup.sample(frac=1.0, random_state=1)
    shuffled.iloc[0, shuffled.columns.get_loc("Volume")] = np.nan
    _write(pdm.CACHE_DIR, "X", shuffled)
    out = pdm.load_and_clean("ALL")["X"]
    assert out.index.is_monotonic_increasing and out.index.is_unique
    assert not out.isnull().any().any()
    assert len(out) == 260


# ---------------------------------------------------------------- extract_features


def test_extract_features_length_boundaries(pdm):
    assert pdm.extract_features("S", ohlcv(np.linspace(10, 20, 272))) == {}
    f273 = pdm.extract_features("S", ohlcv(np.linspace(10, 20, 273)))
    assert f273["symbol"] == "S"
    assert f273["ret_12m"] == 0  # needs n > 252; n == 252 here
    f274 = pdm.extract_features("S", ohlcv(np.linspace(10, 20, 274)))
    assert f274["ret_12m"] > 0


def test_extract_features_linear_trend_values(pdm):
    c = 100.0 + np.arange(300)
    f = pdm.extract_features("L", ohlcv(c))
    last = c[278]  # as-of cutoff drops the final FWD=21 bars
    assert f["ret_1m"] == pytest.approx((last - c[258]) / c[258] * 100)
    assert f["ret_3m"] == pytest.approx((last - c[278 - 62]) / c[278 - 62] * 100)
    assert f["ret_6m"] == pytest.approx((last - c[278 - 125]) / c[278 - 125] * 100)
    assert f["ret_12m"] == pytest.approx((last - c[278 - 251]) / c[278 - 251] * 100)
    assert f["_target_fwd21"] == pytest.approx((c[-1] - last) / last * 100)
    assert f["dist_52w_high"] == pytest.approx(0.0)
    l52 = c[278 - 251]
    assert f["dist_52w_low"] == pytest.approx((last - l52) / l52 * 100)
    assert f["rsi_14"] == 100  # no losses at all
    assert f["up_day_ratio"] == 100.0
    assert f["max_drawdown"] == 0 and f["calmar_proxy"] == 0
    assert f["gap_freq"] == 0.0
    assert f["above_200dma_pct_days"] == pytest.approx(80 / 252 * 100)
    assert f["dma50_gap"] > 0 and f["dma200_gap"] > f["dma50_gap"] and f["dma_trend"] > 0
    assert f["vol_trend"] == 0
    assert f["avg_dollar_vol"] == pytest.approx(np.log1p((c[219:279] * 1000).mean()))
    assert set(pdm.FEATURE_NAMES) <= set(f)


def test_extract_features_flat_series_degenerate_values(pdm):
    f = pdm.extract_features("F", ohlcv(np.full(300, 50.0)))
    assert f["vol_20d"] == 0 and f["vol_60d"] == 0
    assert f["vol_ratio"] == 1  # zero 60d vol falls back to 1
    assert f["rsi_14"] == 100  # zero loss
    assert f["skew_60d"] == 0 and f["kurt_60d"] == 0
    assert f["trend_strength"] == 0 and f["mean_reversion"] == 0
    assert f["_target_fwd21"] == 0


def test_extract_features_falling_series_and_gaps(pdm):
    c = 400.0 - np.arange(300)
    f = pdm.extract_features("D", ohlcv(c, open_mult=1.05))
    assert f["rsi_14"] == 0  # all losses
    assert f["up_day_ratio"] == 0
    assert f["max_drawdown"] < 0 and f["calmar_proxy"] < 0
    assert f["gap_freq"] == 100.0  # every open is >2% from prior close
    assert f["_target_fwd21"] < 0
    assert f["above_200dma_pct_days"] == 0


def test_extract_features_future_leakage_guard(pdm):
    c = walk(5, 300)
    base = pdm.extract_features("W", ohlcv(c))
    c2 = c.copy()
    c2[-20:] *= 3  # change only the held-out bars (last 21 minus the target bar)
    alt = pdm.extract_features("W", ohlcv(c2))
    for k in pdm.FEATURE_NAMES:
        assert alt[k] == base[k]
    assert alt["_target_fwd21"] != base["_target_fwd21"] or c2[-1] == c[-1]


def test_extract_features_rsi_midrange(pdm):
    c = 100 + np.cumsum(np.tile([2.0, -1.0], 150))
    f = pdm.extract_features("M", ohlcv(c))
    assert 60 < f["rsi_14"] < 70  # gain 2 : loss 1 -> rs 2 -> 66.7
    assert f["rsi_14"] == pytest.approx(100 - 100 / 3, abs=1.0)


# ---------------------------------------------------------------- build_feature_matrix


def test_build_feature_matrix_drops_short_and_prints(pdm, capsys):
    cleaned = {
        "A": ohlcv(walk(1)),
        "B": ohlcv(walk(2)),
        "SHORT": ohlcv(walk(3, 100)),
    }
    fm = pdm.build_feature_matrix(cleaned)
    assert sorted(fm["symbol"]) == ["A", "B"]
    assert set(pdm.FEATURE_NAMES) <= set(fm.columns)
    assert "Feature matrix: 2 stocks" in capsys.readouterr().out


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="source bug: when no stock yields features the frame has no columns and "
    "dropna(subset=FEATURE_NAMES) raises KeyError (pattern_discovery.py build_feature_matrix)",
)
def test_build_feature_matrix_no_valid_stock_returns_empty(pdm):
    fm = pdm.build_feature_matrix({"SHORT": ohlcv(walk(3, 100))})
    assert len(fm) == 0


# ---------------------------------------------------------------- discover_clusters


def _fm(n, seed=0):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame(rng.normal(size=(n, len(pdm_names()))), columns=pdm_names())
    d["symbol"] = [f"S{i}" for i in range(n)]
    d["avg_dollar_vol"] = np.arange(n, dtype=float)
    d["_target_fwd21"] = rng.normal(size=n)
    return d


def pdm_names():
    return [
        "ret_1m", "ret_3m", "ret_6m", "ret_12m", "vol_20d", "vol_60d", "vol_ratio",
        "dist_52w_high", "dist_52w_low", "dma50_gap", "dma200_gap", "dma_trend", "rsi_14",
        "above_200dma_pct_days", "max_drawdown", "calmar_proxy", "avg_dollar_vol", "vol_trend",
        "skew_60d", "kurt_60d", "up_day_ratio", "gap_freq", "trend_strength", "mean_reversion",
    ]  # fmt: skip


def test_feature_names_match_fixture(pdm):
    assert pdm.FEATURE_NAMES == pdm_names()


def test_discover_clusters_wires_models(pdm, capsys):
    fm = _fm(30)
    fm.loc[3, "ret_1m"] = np.inf
    fm.loc[4, "ret_3m"] = np.nan
    out, km, pca, evr = pdm.discover_clusters(fm, n_clusters=4)
    assert km.n_clusters == 4 and km.random_state == 42 and km.n_init == 10
    assert pca.n_components == 5
    assert pdm.DBSCAN.__name__ == "DBSCAN"
    assert r2_fakes.DBSCAN.last.eps == 3.0 and r2_fakes.DBSCAN.last.min_samples == 5
    assert set(out["cluster"]) == {0, 1, 2, 3}
    assert {"pc1", "pc2", "is_anomaly"} <= set(out.columns)
    assert int(out["is_anomaly"].sum()) == len([i for i in range(30) if i % 7 == 0])
    assert "cluster" not in fm.columns  # input not mutated
    assert len(evr) == 5
    assert "KMeans: 4 clusters | PCA top-2 explain 40%" in capsys.readouterr().out


def test_discover_clusters_default_k(pdm):
    out, km, _, _ = pdm.discover_clusters(_fm(30))
    assert km.n_clusters == 6


# ---------------------------------------------------------------- supervised_drivers


def test_supervised_needs_100_labelled_rows(pdm, capsys):
    fm = _fm(99)
    assert pdm.supervised_drivers(fm).empty
    assert "Insufficient labelled data" in capsys.readouterr().out
    fm100 = _fm(100)
    fm100.loc[0, "_target_fwd21"] = np.nan  # only 99 labelled -> still insufficient
    assert pdm.supervised_drivers(fm100).empty


def test_supervised_importances_sorted_and_params(pdm, capsys):
    imp = pdm.supervised_drivers(_fm(100))
    assert len(imp) == 24
    assert imp["Feature"].iloc[0] == "mean_reversion"  # largest fake importance
    assert imp["Importance"].is_monotonic_decreasing
    assert imp["Importance%"].iloc[0] == round(24 / 300 * 100, 1)
    gb = r2_fakes.GradientBoostingRegressor.last
    assert gb.kw == {
        "n_estimators": 150,
        "max_depth": 3,
        "learning_rate": 0.05,
        "random_state": 42,
    }
    out = capsys.readouterr().out
    assert "Low R²" in out  # mean predictor -> r2 <= 0 < 0.05


def test_supervised_no_low_r2_warning_when_fit_good(pdm, capsys):
    r2_fakes.GradientBoostingRegressor.mode = "perfect"
    fm = _fm(120)
    fm["_target_fwd21"] = fm["ret_1m"]  # fake "perfect" model predicts the first feature
    pdm.supervised_drivers(fm)
    out = capsys.readouterr().out
    assert "Model fit: R²=" in out and "Low R²" not in out


def test_supervised_replaces_inf_features(pdm):
    fm = _fm(100)
    fm.loc[1, "ret_1m"] = np.inf
    fm.loc[2, "ret_3m"] = np.nan
    assert len(pdm.supervised_drivers(fm)) == 24


# ---------------------------------------------------------------- find_comoving_pairs


def test_comoving_pairs_found_sorted_and_capped(pdm, capsys):
    a = walk(1)
    cleaned = {
        "A": ohlcv(a),
        "B": ohlcv(a * 2),
        "C": ohlcv(walk(9)),
        "SHORTR": ohlcv(walk(4, 150)),
    }
    fm = pd.DataFrame(
        {"symbol": ["A", "B", "C", "SHORTR", "GHOST"], "avg_dollar_vol": [5.0, 4, 3, 2, 1]}
    )
    pairs = pdm.find_comoving_pairs(cleaned, fm)
    assert list(pairs["Stock_A"]) == ["A"] and list(pairs["Stock_B"]) == ["B"]
    assert pairs["Correlation"].iloc[0] == pytest.approx(1.0, abs=1e-3)
    assert "Found 1 pairs" in capsys.readouterr().out


def test_comoving_pairs_top_n_and_ordering(pdm):
    a = walk(1)
    rng = np.random.default_rng(3)
    cleaned = {"A": ohlcv(a), "B": ohlcv(a * 2)}
    noisy = a * np.exp(rng.normal(0, 0.002, len(a)))
    cleaned["C"] = ohlcv(noisy)
    fm = pd.DataFrame({"symbol": list(cleaned), "avg_dollar_vol": [3.0, 2, 1]})
    pairs = pdm.find_comoving_pairs(cleaned, fm)
    assert len(pairs) == 3
    assert pairs["Correlation"].is_monotonic_decreasing
    assert len(pdm.find_comoving_pairs(cleaned, fm, top_n=2)) == 2


def test_comoving_pairs_too_few_series(pdm):
    cleaned = {"A": ohlcv(walk(1)), "SHORTR": ohlcv(walk(4, 150))}
    fm = pd.DataFrame({"symbol": ["A", "SHORTR"], "avg_dollar_vol": [2.0, 1.0]})
    assert pdm.find_comoving_pairs(cleaned, fm).empty


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="source bug: no pair above 0.80 -> empty DataFrame has no 'Correlation' column, "
    "sort_values raises KeyError (pattern_discovery.py find_comoving_pairs)",
)
def test_comoving_pairs_no_matches_crashes(pdm):
    cleaned = {"A": ohlcv(walk(1)), "B": ohlcv(walk(2))}
    fm = pd.DataFrame({"symbol": ["A", "B"], "avg_dollar_vol": [2.0, 1.0]})
    res = pdm.find_comoving_pairs(cleaned, fm)
    assert res.empty


# ---------------------------------------------------------------- name_archetype


def _prof(**kw):
    base = dict(
        ret_12m=0.0, vol_60d=30.0, dma200_gap=8.0, max_drawdown=-10.0, rsi_14=50.0,
        mean_reversion=0.0,
    )  # fmt: skip
    base.update(kw)
    return pd.Series(base)


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(ret_12m=41, dma200_gap=11), "High-Momentum Leaders"),
        (dict(ret_12m=40, dma200_gap=11), "Balanced"),  # mom boundary is strict
        (dict(ret_12m=41, dma200_gap=10), "Balanced"),  # trend boundary is strict
        (dict(ret_12m=-21, dma200_gap=-11), "Sustained Downtrenders"),
        (dict(ret_12m=-20, dma200_gap=-11), "Balanced"),
        (dict(ret_12m=-21, dma200_gap=-10), "Balanced"),
        (dict(vol_60d=51), "High-Volatility Movers"),
        (dict(vol_60d=50), "Balanced"),
        (dict(vol_60d=24.9, dma200_gap=4.9), "Low-Vol Range-Bound"),
        (dict(vol_60d=24.9, dma200_gap=-4.9), "Low-Vol Range-Bound"),
        (dict(vol_60d=25, dma200_gap=4.9), "Balanced"),
        (dict(vol_60d=24.9, dma200_gap=5), "Balanced"),
        (dict(mean_reversion=10.1), "Mean-Reverters"),
        (dict(mean_reversion=10), "Balanced"),
        (dict(rsi_14=65.1, ret_12m=15.1), "Overbought Momentum"),
        (dict(rsi_14=65, ret_12m=15.1), "Balanced"),
        (dict(rsi_14=65.1, ret_12m=15), "Balanced"),
        (dict(max_drawdown=-50.1), "Deep-Drawdown Recovery"),
        (dict(max_drawdown=-50), "Balanced"),
        (dict(), "Balanced"),
    ],
)
def test_name_archetype_branches(pdm, kw, expected):
    assert expected in pdm.name_archetype(_prof(**kw))


def test_name_archetype_priority_order(pdm):
    # momentum beats volatility; volatility beats range-bound/mean-reversion
    assert "Leaders" in pdm.name_archetype(_prof(ret_12m=50, dma200_gap=20, vol_60d=80))
    assert "Volatility" in pdm.name_archetype(_prof(vol_60d=80, mean_reversion=50))
    assert "Range-Bound" in pdm.name_archetype(_prof(vol_60d=10, dma200_gap=0, mean_reversion=50))
    assert "Mean-Reverters" in pdm.name_archetype(_prof(mean_reversion=50, rsi_14=70, ret_12m=20))


# ---------------------------------------------------------------- extract_insights / save_excel


def _clustered(pdm, n=30):
    fm, *_ = pdm.discover_clusters(_fm(n), 3)
    return fm


def test_extract_insights_full(pdm, capsys):
    fm = _clustered(pdm)
    imp = pd.DataFrame(
        {"Feature": ["a", "b"], "Importance": [0.5, 0.5], "Importance%": [50.0, 4.0]}
    )
    pairs = pd.DataFrame({"Stock_A": ["X"], "Stock_B": ["Y"], "Correlation": [0.9]})
    ins = pdm.extract_insights(fm, imp, pairs, [0.5, 0.2])
    assert set(ins) == {"clusters", "importances", "anomalies", "pairs"}
    cl = ins["clusters"]
    assert list(cl["Cluster"]) == [0, 1, 2]
    assert cl["Count"].sum() == len(fm)
    assert cl["Examples"].iloc[0].count(",") == 3  # first four symbols
    assert set(ins["anomalies"].columns) == {"symbol"} | set(pdm.FEATURE_NAMES)
    out = capsys.readouterr().out
    assert "X              ↔ Y              ρ=0.9" in out
    assert "█" * 25 in out and "RETURN DRIVERS" in out
    assert "ANOMALIES (5 stocks" in out
    assert pdm.DISCLAIMER in out


def test_extract_insights_omits_empty_sections(pdm):
    fm = _clustered(pdm)
    fm["is_anomaly"] = False
    ins = pdm.extract_insights(fm, pd.DataFrame(), pd.DataFrame(), [0.5, 0.2])
    assert set(ins) == {"clusters"}


def test_save_excel_sheets(pdm, tmp_path):
    xl = r2_fakes.FakeExcel().install(pytest.MonkeyPatch())
    mp = pytest.MonkeyPatch()
    try:
        xl = r2_fakes.FakeExcel().install(mp)
        fm = _clustered(pdm)
        ins = {
            "clusters": pd.DataFrame({"a": [1]}),
            "importances": pd.DataFrame({"b": [1]}),
            "pairs": pd.DataFrame({"c": [1]}),
            "anomalies": pd.DataFrame({"d": range(150)}),
        }
        path = pdm.save_excel(ins, fm, "IN")
        w = xl.writers[0]
        assert w.engine == "openpyxl" and path.parent == pdm.OUT_DIR
        assert path.name.startswith("patterns_IN_") and path.suffix == ".xlsx"
        assert list(w.sheets) == [
            "DISCLAIMER", "Archetypes", "Return_Drivers", "CoMoving_Pairs", "Anomalies",
            "All_Features",
        ]  # fmt: skip
        assert len(w.sheets["Anomalies"]) == 100
        assert list(w.sheets["All_Features"].columns)[:3] == ["symbol", "cluster", "is_anomaly"]
        xl2 = r2_fakes.FakeExcel().install(mp)
        pdm.save_excel({}, fm, "US")
        assert list(xl2.writers[0].sheets) == ["DISCLAIMER", "All_Features"]
    finally:
        mp.undo()


# ---------------------------------------------------------------- main


def _universe(n, bars=300):
    # odd symbols are scaled copies of the previous even one -> guaranteed co-moving pairs
    return {f"S{i}": ohlcv(walk(i - i % 2, bars) * (1.5 if i % 2 else 1.0)) for i in range(n)}


def test_main_without_sklearn(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "sklearn", None)
    for sub in (
        "cluster",
        "decomposition",
        "ensemble",
        "metrics",
        "model_selection",
        "preprocessing",
    ):
        monkeypatch.setitem(sys.modules, f"sklearn.{sub}", None)
    mod = fresh_import(monkeypatch, "pattern_discovery")
    assert mod._SK_OK is False
    monkeypatch.setattr(mod, "load_and_clean", lambda *a: pytest.fail("must not load"))
    mod.main()
    assert "pip install scikit-learn" in capsys.readouterr().out


def test_main_insufficient_data(pdm, monkeypatch, capsys):
    monkeypatch.setattr(pdm, "load_and_clean", lambda m, mx: _universe(49, 60))
    pdm.main()
    assert "Insufficient data." in capsys.readouterr().out


def test_main_insufficient_features(pdm, monkeypatch, capsys):
    monkeypatch.setattr(pdm, "load_and_clean", lambda m, mx: _universe(50))
    monkeypatch.setattr(pdm, "build_feature_matrix", lambda c: pd.DataFrame({"a": range(49)}))
    pdm.main()
    out = capsys.readouterr().out
    assert "Insufficient features." in out and "Insufficient data" not in out


def test_main_end_to_end_passes_args(pdm, monkeypatch, capsys):
    seen = {}

    def load(market, max_stocks):
        seen["args"] = (market, max_stocks)
        return _universe(120)

    monkeypatch.setattr(pdm, "load_and_clean", load)
    pm = pytest.MonkeyPatch()
    try:
        xl = r2_fakes.FakeExcel().install(pm)
        pdm.main(market="IN", n_clusters=3, max_stocks=7)
    finally:
        pm.undo()
    assert seen["args"] == ("IN", 7)
    assert r2_fakes.KMeans.last.n_clusters == 3
    sheets = xl.writers[0].sheets
    assert {"Archetypes", "Return_Drivers", "All_Features"} <= set(sheets)
    assert len(sheets["All_Features"]) == 120
    out = capsys.readouterr().out
    assert "AI PATTERN DISCOVERY — IN market" in out
    assert "CO-MOVING PAIRS" in out
