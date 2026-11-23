"""price_criterion_correlation_tracker: criterion correlations, weight validation, signal vs noise."""

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import

TARGET = "price_5yr_cagr"


@pytest.fixture
def table():
    """Per-column (r, p) lookup the fake scipy functions read from."""
    return {}


@pytest.fixture
def pcc(monkeypatch, table):
    def spearmanr(x, y):
        return table[x.name][:2]

    def pearsonr(x, y):
        return table[x.name][2:]

    stats = fake_module(monkeypatch, "scipy.stats", pearsonr=pearsonr, spearmanr=spearmanr)
    fake_module(monkeypatch, "scipy", stats=stats)
    return fresh_import(monkeypatch, "price_criterion_correlation_tracker")


def _frame(n=12, cols=("revenue_cagr", "capex_cagr"), **extra):
    d = {"ticker": [f"T{i}" for i in range(n)], TARGET: np.arange(n, dtype=float)}
    for c in cols:
        d[c] = np.arange(n, dtype=float) * 2
    d.update(extra)
    return pd.DataFrame(d)


def _corr_df(rows):
    return pd.DataFrame(
        [
            {
                "criterion": c,
                "category": "cat",
                "spearman_r": r,
                "spearman_p": p,
                "pearson_r": r,
                "pearson_p": p,
                "is_significant": p < 0.05,
                "n_samples": 100,
            }
            for c, r, p in rows
        ]
    )


# ---------------------------------------------------------------- calculate_correlations
def test_init_copies_input(pcc):
    src = _frame()
    t = pcc.PriceCriterionCorrelationTracker(src)
    t.results["revenue_cagr"] = 0
    assert src["revenue_cagr"].sum() != 0
    assert t.correlations == {}


def test_correlations_fields_sorting_and_direction(pcc, table, capsys):
    table.update(
        {
            "revenue_cagr": (0.10, 0.20, 0.11, 0.21),
            "capex_cagr": (-0.50, 0.01, -0.51, 0.02),
            "debt_cagr": (0.0, 0.5, 0.01, 0.6),
        }
    )
    t = pcc.PriceCriterionCorrelationTracker(
        _frame(cols=("revenue_cagr", "capex_cagr", "debt_cagr"))
    )
    out = t.calculate_correlations()
    assert out is t.correlations
    assert list(out["criterion"]) == [
        "Capex Acceleration CAGR",
        "Revenue Growth CAGR",
        "Debt Growth CAGR",
    ]
    cap = out.iloc[0]
    assert cap["category"] == "Capex Acceleration"
    assert (cap["spearman_r"], cap["spearman_p"]) == (-0.50, 0.01)
    assert (cap["pearson_r"], cap["pearson_p"]) == (-0.51, 0.02)
    assert bool(cap["is_significant"]) is True and cap["n_samples"] == 12
    assert bool(out.iloc[1]["is_significant"]) is False
    text = capsys.readouterr().out
    assert "Analyzing 12 companies" in text
    assert text.count("✓ SIGNIFICANT") == 1 and text.count("✗ Not significant") == 2
    cap_line = next(l for l in text.splitlines() if l.startswith("Capex Acceleration CAGR"))
    assert "Negative" in cap_line
    zero_line = next(l for l in text.splitlines() if l.startswith("Debt Growth CAGR"))
    assert "Negative" in zero_line  # r == 0 is not > 0


def test_correlation_significance_is_strictly_below_005(pcc, table):
    table["revenue_cagr"] = (0.3, 0.05, 0.3, 0.05)
    table["capex_cagr"] = (0.2, 0.0499, 0.2, 0.0499)
    out = pcc.PriceCriterionCorrelationTracker(_frame()).calculate_correlations()
    sig = dict(zip(out["criterion"], out["is_significant"]))
    assert sig == {"Revenue Growth CAGR": False, "Capex Acceleration CAGR": True}


def test_correlation_min_sample_boundary_and_nan_drop(pcc, table):
    table["revenue_cagr"] = (0.3, 0.01, 0.3, 0.01)
    table["capex_cagr"] = (0.4, 0.01, 0.4, 0.01)
    df = _frame(n=12)
    df.loc[[0, 1], "revenue_cagr"] = np.nan  # leaves 10 valid -> kept
    df.loc[[0, 1, 2], "capex_cagr"] = np.nan  # leaves 9 valid -> skipped
    out = pcc.PriceCriterionCorrelationTracker(df).calculate_correlations()
    assert list(out["criterion"]) == ["Revenue Growth CAGR"]
    assert out.iloc[0]["n_samples"] == 10


def test_correlation_skips_missing_columns(pcc, table):
    table["revenue_cagr"] = (0.3, 0.01, 0.3, 0.01)
    out = pcc.PriceCriterionCorrelationTracker(
        _frame(cols=("revenue_cagr",))
    ).calculate_correlations()
    assert list(out["criterion"]) == ["Revenue Growth CAGR"]


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="calculate_correlations sorts an empty DataFrame by 'spearman_r' when no criterion has >=10 rows",
)
def test_correlation_with_too_few_rows_returns_empty_frame(pcc):
    out = pcc.PriceCriterionCorrelationTracker(_frame(n=5)).calculate_correlations()
    assert len(out) == 0


# ---------------------------------------------------------------- validate_model_weights
def _tracker_with(pcc, rows):
    t = pcc.PriceCriterionCorrelationTracker(_frame())
    t.correlations = _corr_df(rows)
    return t


@pytest.mark.parametrize(
    "crit,r,weights,expected",
    [
        ("Capex Acceleration CAGR", 0.0401, {}, "✓ Good"),  # weight 24 default, > 0.04
        ("Capex Acceleration CAGR", 0.04, {}, "✓ Good"),  # falls to 2nd rule: 24>10 and >0.02
        ("Capex Acceleration CAGR", 0.02, {}, "⚠ Question"),  # not > 0.02
        ("Capex Acceleration CAGR", 0.0, {}, "✗ Mismatch"),
        ("FCF Margin", -0.05, {}, "✓ Good"),  # absolute value
        ("Revenue Growth CAGR", 0.021, {}, "✓ Good"),  # weight 19 > 10
        ("Debt Growth CAGR", 0.5, {}, "⚠ Question"),  # weight 10 is not > 10
        ("Debt Growth CAGR", 0.5, {"debt_expansion": 11}, "✓ Good"),
        ("Debt-to-Equity Ratio", 0.3, {}, "⚠ Question"),  # weight 2
        ("Debt-to-Equity Ratio", 0.3, {"leverage_health": 0}, "✗ Mismatch"),
        ("Asset Turnover (NEW)", 0.5, {"asset_efficiency": 21}, "✓ Good"),
    ],
)
def test_validate_match_rules(pcc, crit, r, weights, expected, capsys):
    t = _tracker_with(pcc, [(crit, r, 0.01)])
    v = t.validate_model_weights(weights)
    assert list(v["match"]) == [expected]
    assert v.iloc[0]["effectiveness"] == abs(r)


def test_validate_skips_unmapped_and_reports_weights(pcc, capsys):
    t = _tracker_with(
        pcc,
        [
            ("Capex Acceleration CAGR", 0.1, 0.01),
            ("Mystery Criterion", 0.9, 0.01),
            ("Profitability Quality (NEW)", 0.03, 0.2),
        ],
    )
    v = t.validate_model_weights({"profitability_quality": 15})
    assert list(v["criterion"]) == ["Capex Acceleration CAGR", "Profitability Quality (NEW)"]
    assert list(v["weight"]) == [24, 15]
    assert list(v["match"]) == ["✓ Good", "✓ Good"]
    assert "Mystery" not in capsys.readouterr().out


def test_validate_empty_correlations_returns_empty_frame(pcc):
    t = pcc.PriceCriterionCorrelationTracker(_frame())
    t.correlations = _corr_df([])
    assert len(t.validate_model_weights({})) == 0


# ---------------------------------------------------------------- signal vs noise
def test_signal_noise_buckets_and_boundaries(pcc, capsys):
    t = _tracker_with(
        pcc,
        [
            ("S1", 0.0401, 0.01),  # strong
            ("S2", -0.30, 0.049),  # strong, negative
            ("NS", 0.30, 0.05),  # >0.04 but not significant -> in no bucket
            ("W1", 0.04, 0.001),  # weak (<= 0.04)
            ("W2", -0.0201, 0.9),  # weak
            ("N1", 0.02, 0.9),  # noise (<= 0.02)
            ("N2", 0.0, 0.9),
        ],
    )
    out = t.identify_signal_vs_noise()
    assert list(out["strong_signal"]["criterion"]) == ["S1", "S2"]
    assert list(out["weak_signal"]["criterion"]) == ["W1", "W2"]
    assert list(out["noise"]["criterion"]) == ["N1", "N2"]
    text = capsys.readouterr().out
    assert text.count("Count: 2") == 3


def test_signal_noise_all_empty_buckets(pcc, capsys):
    t = _tracker_with(pcc, [("NS", 0.5, 0.5)])
    out = t.identify_signal_vs_noise()
    assert all(len(v) == 0 for v in out.values())
    assert capsys.readouterr().out.count("Count: 0") == 3


# ---------------------------------------------------------------- tier performance
def _tier_frame(prices):
    rows = []
    for tier, vals in prices.items():
        for v in vals:
            rows.append({"tier": tier, TARGET: v})
    return pd.DataFrame(rows)


T1, T2, T3, T4 = (
    "Tier 1 (Aggressive Expander)",
    "Tier 2 (Strong Expander)",
    "Tier 3 (Moderate Expander)",
    "Tier 4 (Passive/Mature)",
)


@pytest.mark.parametrize(
    "p1,p4,msg",
    [
        (16.0, 10.0, "Model VALIDATES"),
        (15.0, 10.0, "Weak validation"),  # exactly 5 is not > 5
        (10.5, 10.0, "Weak validation"),
        (10.0, 10.0, "Model FAILS"),  # exactly 0 is not > 0
        (5.0, 10.0, "Model FAILS"),
    ],
)
def test_tier_validation_message(pcc, capsys, p1, p4, msg):
    t = pcc.PriceCriterionCorrelationTracker(_tier_frame({T1: [p1], T4: [p4]}))
    t.tier_price_performance()
    out = capsys.readouterr().out
    assert msg in out
    for other in ("Model VALIDATES", "Weak validation", "Model FAILS"):
        if other != msg:
            assert other not in out


def test_tier_performance_frame_in_tier_order(pcc, capsys):
    df = _tier_frame({T3: [1.0, 3.0, 8.0], T1: [20.0, 40.0], T4: [-5.0]})
    out = pcc.PriceCriterionCorrelationTracker(df).tier_price_performance()
    assert list(out["tier"]) == [T1, T3, T4]  # T2 absent, skipped
    assert list(out["count"]) == [2, 3, 1]
    assert list(out["avg_price_cagr"]) == [30.0, 4.0, -5.0]
    assert list(out["median_price_cagr"]) == [30.0, 3.0, -5.0]
    assert "Tier 1 vs Tier 4 outperformance: +35.0%" in capsys.readouterr().out


def test_tier_performance_single_tier_has_no_verdict(pcc, capsys):
    out = pcc.PriceCriterionCorrelationTracker(_tier_frame({T2: [1.0]})).tier_price_performance()
    assert len(out) == 1
    assert "outperformance" not in capsys.readouterr().out


def test_tier_performance_unknown_tiers_give_empty_frame(pcc):
    out = pcc.PriceCriterionCorrelationTracker(
        _tier_frame({"Tier 1": [1.0]})
    ).tier_price_performance()
    assert len(out) == 0


# ---------------------------------------------------------------- R squared
@pytest.mark.parametrize(
    "corr,needle",
    [
        (0.45, "STRONG: Model explains >20% of price variance"),
        (0.44, "MODERATE: Model explains 19.4% of price variance"),
        (0.32, "MODERATE: Model explains 10.2% of price variance"),
        (0.31, "WEAK: Model explains only 9.6% of price variance"),
        (0.23, "WEAK: Model explains only 5.3% of price variance"),
        (0.22, "VERY WEAK: Model explains <4.8% of price variance"),
        (-0.45, "STRONG"),
    ],
)
def test_r_squared_bands(pcc, monkeypatch, capsys, corr, needle):
    monkeypatch.setattr(pd.Series, "corr", lambda self, other, *a, **k: corr)
    df = pd.DataFrame({"expansion_score": [1.0, 2.0, 3.0], TARGET: [3.0, 1.0, 2.0]})
    res = pcc.PriceCriterionCorrelationTracker(df).coefficient_of_determination()
    assert res == {"correlation": corr, "r_squared": corr**2}
    assert needle in capsys.readouterr().out


def test_r_squared_perfect_fit_from_real_corr(pcc):
    df = pd.DataFrame({"expansion_score": [1.0, 2.0, 3.0, 4.0], TARGET: [2.0, 4.0, 6.0, 8.0]})
    res = pcc.PriceCriterionCorrelationTracker(df).coefficient_of_determination()
    assert res["r_squared"] == pytest.approx(1.0)


def test_r_squared_needs_more_than_two_valid_rows(pcc):
    df = pd.DataFrame(
        {"expansion_score": [1.0, 2.0, 3.0, np.nan], TARGET: [3.0, 1.0, np.nan, 5.0]}
    )  # only 2 complete rows
    assert pcc.PriceCriterionCorrelationTracker(df).coefficient_of_determination() is None
    df3 = pd.DataFrame({"expansion_score": [1.0, 2.0, 3.0], TARGET: [3.0, 1.0, 2.0]})
    assert pcc.PriceCriterionCorrelationTracker(df3).coefficient_of_determination() is not None


# ---------------------------------------------------------------- recommendations
def test_generate_recommendations_sections(pcc, capsys):
    t = _tracker_with(
        pcc,
        [
            ("Capex Acceleration CAGR", 0.30, 0.001),
            ("FCF Margin", 0.20, 0.001),
            ("Revenue Growth CAGR", 0.10, 0.001),
            ("Debt Growth CAGR", 0.03, 0.4),
            ("Leverage", 0.01, 0.9),
        ],
    )
    t.generate_recommendations()
    out = capsys.readouterr().out
    assert "✓ Capex Acceleration CAGR" in out and "Already well-weighted" in out
    assert "✓ FCF Margin" in out
    assert "⚡ Revenue Growth CAGR" in out and "Consider +5pp" in out
    assert "⚠ Debt Growth CAGR" in out
    assert "✗ Leverage" in out and "Consider removing" in out
    assert "All criteria are either strong or noise" not in out
    assert "No clear noise criteria identified" not in out


def test_generate_recommendations_empty_buckets(pcc, capsys):
    t = _tracker_with(pcc, [("Gap", 0.5, 0.5)])
    t.generate_recommendations()
    out = capsys.readouterr().out
    assert "All criteria are either strong or noise" in out
    assert "No clear noise criteria identified" in out
    assert "⚡" not in out
