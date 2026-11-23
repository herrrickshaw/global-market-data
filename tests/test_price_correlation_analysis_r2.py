"""price_correlation_analysis: synthetic price model, correlations, weight recommendations, report."""

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import

DIMS = [
    "debt_expansion",
    "capex_acceleration",
    "profit_reinvestment",
    "profitability_quality",
    "sustainability",
    "timing_alignment",
    "leverage_health",
    "fcf_generation",
]


def _pearsonr(a, b):
    r = float(np.corrcoef(a, b)[0, 1])
    return r, 1 - abs(r)


def _spearmanr(a, b):
    r = float(pd.Series(a).rank().corr(pd.Series(b).rank()))
    return r, 1 - abs(r)


@pytest.fixture
def pca(monkeypatch):
    stats = fake_module(monkeypatch, "scipy.stats", pearsonr=_pearsonr, spearmanr=_spearmanr)
    fake_module(monkeypatch, "scipy", stats=stats)
    return fresh_import(monkeypatch, "price_correlation_analysis")


def _tier2(n=6):
    return pd.DataFrame({"ticker": [f"T{i}" for i in range(n)], "score": np.arange(n)})


# ---------------------------------------------------------------- init
def test_init_with_scorecard_left_merges_market_cap(pca):
    t2 = _tier2(3)
    sc = pd.DataFrame({"ticker": ["T0", "T2"], "market_cap_b": [10.0, 30.0], "other": [1, 2]})
    a = pca.PriceCorrelationAnalyzer(t2, sc)
    assert list(a.tier2["ticker"]) == ["T0", "T1", "T2"]
    assert a.tier2["market_cap_b"].iloc[0] == 10.0 and a.tier2["market_cap_b"].iloc[2] == 30.0
    assert pd.isna(a.tier2["market_cap_b"].iloc[1])
    assert "other" not in a.tier2.columns
    assert a.correlation_results == {}
    assert "market_cap_b" not in t2.columns  # caller's frame untouched
    assert a.scorecard is not sc


def test_init_without_scorecard_uses_seeded_lognormal(pca):
    a = pca.PriceCorrelationAnalyzer(_tier2(5))
    b = pca.PriceCorrelationAnalyzer(_tier2(5))
    assert a.scorecard is None
    assert len(a.tier2) == 5 and (a.tier2["market_cap_b"] > 0).all()
    assert list(a.tier2["market_cap_b"]) == list(b.tier2["market_cap_b"])
    np.random.seed(42)
    expected = np.random.lognormal(mean=np.log(1000), sigma=1.5, size=5)
    assert np.allclose(a.tier2["market_cap_b"], expected)


# ---------------------------------------------------------------- synthetic price
def _expected_return(big_cap, n):
    np.random.seed(42)
    capex = np.random.uniform(-10, -2) if big_cap else np.random.uniform(-5, 5)
    debt = np.random.uniform(-8, 0)
    profit = np.random.uniform(5, 15)
    growth = np.random.uniform(10, 25)
    noise = np.random.uniform(-5, 5, n)
    return capex / 4 + debt / 2 + profit / 3 + growth / 2 + noise


@pytest.mark.parametrize("cap,big", [(5000.0, True), (1000.0, False), (10.0, False)])
def test_stock_return_formula_and_capex_branch(pca, cap, big):
    t2 = _tier2(8)
    sc = pd.DataFrame({"ticker": t2["ticker"], "market_cap_b": cap})
    a = pca.PriceCorrelationAnalyzer(t2, sc)
    df = a.generate_synthetic_stock_price()
    assert np.allclose(df["stock_return_12m"], _expected_return(big, 8))
    assert np.allclose(df["stock_price"], 100 + df["stock_return_12m"])
    assert len(df) == 8
    assert "stock_return_12m" not in a.tier2.columns  # works on a copy


def test_price_movement_bins_match_thresholds(pca):
    a = pca.PriceCorrelationAnalyzer(_tier2(200))
    df = a.generate_synthetic_stock_price()
    for r, lab in zip(df["stock_return_12m"], df["price_movement"]):
        if r <= -5:
            want = "Down >5%"
        elif r <= 0:
            want = "Down <5%"
        elif r <= 5:
            want = "Flat"
        elif r <= 10:
            want = "Up <10%"
        else:
            want = "Up >10%"
        assert lab == want
    assert list(df["price_movement"].cat.categories) == [
        "Down >5%",
        "Down <5%",
        "Flat",
        "Up <10%",
        "Up >10%",
    ]


def test_market_cap_mean_equal_1000_takes_small_branch(pca):
    # `> 1000` is strict: mean exactly 1000 uses the (-5, 5) capex range
    t2 = _tier2(4)
    sc = pd.DataFrame({"ticker": t2["ticker"], "market_cap_b": [500.0, 1500.0, 500.0, 1500.0]})
    df = pca.PriceCorrelationAnalyzer(t2, sc).generate_synthetic_stock_price()
    assert np.allclose(df["stock_return_12m"], _expected_return(False, 4))


# ---------------------------------------------------------------- dimensions
def test_extract_dimensions_shape_and_range(pca):
    a = pca.PriceCorrelationAnalyzer(_tier2(25))
    d = a.extract_8_dimensions(a.tier2)
    assert list(d) == DIMS
    for v in d.values():
        assert len(v) == 25 and all(20 <= x <= 80 for x in v)
    assert len({tuple(v) for v in d.values()}) == 8  # independent draws per dimension


def test_extract_dimensions_empty(pca):
    a = pca.PriceCorrelationAnalyzer(_tier2(0))
    assert a.extract_8_dimensions(a.tier2) == {k: [] for k in DIMS}


# ---------------------------------------------------------------- correlations
def test_calculate_correlations_fields_and_significance_boundary(pca, monkeypatch):
    seq = iter([0.0499, 0.05, 0.9, 0.0])
    ps = []

    def fake_p(a, b):
        p = next(seq)
        ps.append(p)
        return 0.3, p

    monkeypatch.setattr(pca, "pearsonr", fake_p)
    monkeypatch.setattr(pca, "spearmanr", lambda a, b: (-0.2, 0.01))
    df = pd.DataFrame({"stock_return_12m": [1.0, 2.0, 3.0]})
    dims = {k: [1.0, 2.0, 3.0] for k in ["a", "b", "c", "d"]}
    out = pca.PriceCorrelationAnalyzer(_tier2(3)).calculate_correlations(df, dims)
    assert list(out) == ["a", "b", "c", "d"]
    assert out["a"] == {
        "pearson_r": 0.3,
        "pearson_pval": 0.0499,
        "spearman_r": -0.2,
        "spearman_pval": 0.01,
        "significant": True,
    }
    assert [out[k]["significant"] for k in "abcd"] == [True, False, False, True]


def test_calculate_correlations_perfectly_correlated(pca):
    df = pd.DataFrame({"stock_return_12m": [1.0, 2.0, 3.0, 4.0]})
    out = pca.PriceCorrelationAnalyzer(_tier2(4)).calculate_correlations(
        df, {"up": [10, 20, 30, 40], "down": [4, 3, 2, 1]}
    )
    assert out["up"]["pearson_r"] == pytest.approx(1.0)
    assert out["down"]["spearman_r"] == pytest.approx(-1.0)


# ---------------------------------------------------------------- weights
def _corr(values, pvals=None):
    pvals = pvals or [0.5] * len(values)
    return {
        d: {"spearman_r": v, "spearman_pval": p, "pearson_r": 0, "pearson_pval": 1}
        for d, v, p in zip(DIMS, values, pvals)
    }


def test_weights_equal_correlations_leave_weights_unchanged(pca):
    rec = pca.PriceCorrelationAnalyzer(_tier2(1)).recommend_weight_adjustments(_corr([0.5] * 8))
    assert [rec[d]["current"] for d in DIMS] == [20, 20, 15, 15, 15, 10, 5, 0]
    assert all(rec[d]["adjustment_factor"] == 1.5 for d in DIMS)
    assert [rec[d]["recommended"] for d in DIMS] == [20.0, 20.0, 15.0, 15.0, 15.0, 10.0, 5.0, 0.0]


def test_weights_scaled_and_normalised_to_100(pca):
    rec = pca.PriceCorrelationAnalyzer(_tier2(1)).recommend_weight_adjustments(
        _corr([0.5] + [0.25] * 7)
    )
    assert rec["debt_expansion"]["adjustment_factor"] == 1.5
    assert rec["capex_acceleration"]["adjustment_factor"] == 1.0
    assert [rec[d]["recommended"] for d in DIMS] == [27.3, 18.2, 13.6, 13.6, 13.6, 9.1, 4.5, 0.0]
    assert rec["fcf_generation"]["recommended"] == 0  # current weight 0 stays 0


def test_weights_use_absolute_spearman_and_round_fields(pca):
    c = _corr([-0.5] + [0.123456] * 7, [0.123456] * 8)
    rec = pca.PriceCorrelationAnalyzer(_tier2(1)).recommend_weight_adjustments(c)
    assert rec["debt_expansion"]["adjustment_factor"] == 1.5  # |-0.5| is the max
    assert rec["debt_expansion"]["correlation"] == -0.5
    assert rec["capex_acceleration"]["correlation"] == 0.123
    assert rec["capex_acceleration"]["p_value"] == 0.1235
    assert rec["capex_acceleration"]["adjustment_factor"] == round(0.5 + 0.123456 / 0.5, 2)


# ---------------------------------------------------------------- run + report
def test_run_analysis_end_to_end(pca, capsys):
    recs, df = pca.PriceCorrelationAnalyzer(_tier2(40)).run_analysis()
    assert set(recs) == set(DIMS) and len(df) == 40
    assert sum(r["recommended"] for r in recs.values()) == pytest.approx(100, abs=0.5)
    assert all(-1 <= r["correlation"] <= 1 for r in recs.values())
    out = capsys.readouterr().out
    assert "Generating stock price data" in out and "Calculating correlations" in out


def _recs():
    pv = [0.01, 0.2, 0.0499, 0.05, 0.9, 0.3, 0.4, 0.5]
    cs = [0.4, 0.1, -0.5, 0.2, 0.05, 0.3, 0.15, -0.01]
    out = {}
    for d, c, p, cur in zip(DIMS, cs, pv, [20, 20, 15, 15, 15, 10, 5, 0]):
        out[d] = {
            "current": cur,
            "adjustment_factor": 1.0,
            "recommended": float(cur),
            "correlation": c,
            "p_value": p,
        }
    out["profit_reinvestment"]["adjustment_factor"] = 1.5
    out["fcf_generation"]["adjustment_factor"] = 0.5
    return out


def test_generate_report_content(pca, capsys):
    df = pd.DataFrame(
        {
            "stock_return_12m": [-6.0, 1.0, 2.0, 12.0],
            "price_movement": pd.Categorical(
                ["Down >5%", "Flat", "Flat", "Up >10%"],
                categories=["Down >5%", "Down <5%", "Flat", "Up <10%", "Up >10%"],
                ordered=True,
            ),
        }
    )
    pca.PriceCorrelationAnalyzer(_tier2(1)).generate_report(_recs(), df)
    out = capsys.readouterr().out
    assert "Companies analyzed: 4" in out
    assert "-6.0% to 12.0%" in out
    assert "Average 12M return: 2.2%" in out
    assert "Strongest price predictor: profit_reinvestment" in out
    assert "Recommended weight increase: 1.50x" in out
    assert "Weakest price predictor: fcf_generation" in out
    assert "Recommended weight decrease: 0.50x" in out
    # p < 0.05 strictly: 0.01 and 0.0499 are significant, 0.05 is not
    assert "significant predictors (p<0.05): 2" in out
    assert "✅ debt_expansion" in out and "✅ profit_reinvestment" in out
    assert "✅ profitability_quality" not in out
    assert "TOTAL" in out and "100.0" in out
    assert "Flat" in out and "50.0%" in out
    assert "1.00x multiplier" in out
    # sorted by absolute correlation: profit_reinvestment (-0.5) before debt_expansion (0.4)
    assert out.index("profit_reinvestment   ") < out.index("debt_expansion   ")


def test_export_recommendations_writes_csv(pca, tmp_path, capsys):
    f = tmp_path / "recs.csv"
    pca.PriceCorrelationAnalyzer(_tier2(1)).export_recommendations(_recs(), str(f))
    back = pd.read_csv(f, index_col=0)
    assert list(back.index) == DIMS
    assert list(back.columns) == [
        "current",
        "adjustment_factor",
        "recommended",
        "correlation",
        "p_value",
    ]
    assert back.loc["profit_reinvestment", "adjustment_factor"] == 1.5
    assert f"exported to {f}" in capsys.readouterr().out


def test_generate_report_marks_p_equal_to_threshold_not_significant(pca, capsys):
    df = pd.DataFrame(
        {
            "stock_return_12m": [1.0, 2.0],
            "price_movement": pd.Categorical(
                ["Flat", "Flat"],
                categories=["Down >5%", "Down <5%", "Flat", "Up <10%", "Up >10%"],
                ordered=True,
            ),
        }
    )
    pca.PriceCorrelationAnalyzer(_tier2(1)).generate_report(_recs(), df)
    lines = capsys.readouterr().out.splitlines()
    row = next(ln for ln in lines if ln.strip().startswith("profitability_quality"))
    assert "❌" in row and "✅" not in row  # p == 0.05 exactly
    row = next(ln for ln in lines if ln.strip().startswith("capex_acceleration"))
    assert "❌" in row  # p == 0.2
    row = next(ln for ln in lines if ln.strip().startswith("debt_expansion"))
    assert "✅" in row


def test_import_has_no_side_effects(monkeypatch, capsys):
    stats = fake_module(monkeypatch, "scipy.stats", pearsonr=_pearsonr, spearmanr=_spearmanr)
    fake_module(monkeypatch, "scipy", stats=stats)
    capsys.readouterr()
    fresh_import(monkeypatch, "price_correlation_analysis")
    assert capsys.readouterr().out == ""  # banner only under __main__
