"""phased_expansion_screener_11d: stage filters, 11-D score, tiers, pipeline and summary output."""

import pandas as pd
import pytest

from phased_expansion_screener_11d import PhasedExpansionScreener


@pytest.fixture
def scr():
    return PhasedExpansionScreener()


def _good(**kw):
    c = {
        "ticker": "AAA",
        "name": "Aaa Co",
        "country": "USA",
        "sector": "Tech",
        "debt_to_equity": 0.5,
        "capex_to_revenue": 0.05,
        "capex_cagr": 12,
        "avg_fcf": 2e9,
        "net_margin": 5,
        "revenue_cagr": 12,
        "price_5yr_cagr": 10,
    }
    c.update(kw)
    return c


def test_init_state(scr):
    assert scr.stage1_filter == {} and scr.stage2_filter == {} and scr.stage3_results == {}
    assert scr.start_time > 0


def test_weights_stage_split(scr):
    w = scr.define_criteria_weights()
    assert len(w) == 11
    assert sum(c["weight"] for c in w.values() if c["stage"] == 1) == 66
    assert sum(c["weight"] for c in w.values() if c["stage"] == 2) == 29
    assert sum(c["weight"] for c in w.values() if c["stage"] == 3) == 25


# ---------------------------------------------------------------- stage 1
@pytest.mark.parametrize(
    "kw,passes",
    [
        ({"debt_to_equity": 2.0}, True),
        ({"debt_to_equity": 2.01}, False),
        ({"capex_to_revenue": 0.005}, True),
        ({"capex_to_revenue": 0.0049}, False),
        ({}, True),
        ({"capex_cagr": -15}, True),
        ({"capex_cagr": -15.01}, False),
        ({"capex_cagr": 0}, True),
        ({"avg_fcf": 0}, True),
        ({"avg_fcf": -1}, False),
        ({"avg_fcf": None}, False),
        ({"net_margin": -5}, True),
        ({"net_margin": -5.01}, False),
        ({"net_margin": 0}, True),
    ],
)
def test_stage1_boundaries(scr, kw, passes):
    out = scr.stage1_prefilter([_good(**kw)])
    assert (len(out) == 1) is passes


def test_stage1_missing_keys_default_fails_on_capex(scr):
    # capex_to_revenue defaults to 0 (< 0.005) so a bare company is rejected on capex
    out = scr.stage1_prefilter([{"ticker": "X"}])
    assert out == []
    assert scr.stage1_filter == {
        "failed_debt": 0,
        "failed_capex": 1,
        "failed_fcf": 0,
        "failed_profitability": 0,
    }


def test_stage1_counts_only_first_failure_and_keeps_order(scr, capsys):
    a = _good(ticker="A")
    b = _good(ticker="B", debt_to_equity=3, capex_to_revenue=0, avg_fcf=-1, net_margin=-9)
    c = _good(ticker="C", capex_to_revenue=0.0, avg_fcf=-1, net_margin=-9)
    d = _good(ticker="D", avg_fcf=-1, net_margin=-9)
    e = _good(ticker="E", net_margin=-9)
    f = _good(ticker="F")
    out = scr.stage1_prefilter([a, b, c, d, e, f])
    assert [x["ticker"] for x in out] == ["A", "F"]
    assert scr.stage1_filter == {
        "failed_debt": 1,
        "failed_capex": 1,
        "failed_fcf": 1,
        "failed_profitability": 1,
    }
    text = capsys.readouterr().out
    assert "Passed: 2 / 6" in text
    assert "Failed: 4 (66.7%)" in text
    assert "Stage 1 Weight: 66/100" in text


@pytest.mark.xfail(
    strict=True,
    raises=ZeroDivisionError,
    reason="stage1_prefilter divides by len(companies_data) for the failure rate; empty -> ZeroDivisionError",
)
def test_stage1_empty_input_returns_empty(scr):
    assert scr.stage1_prefilter([]) == []


# ---------------------------------------------------------------- stage 2
@pytest.mark.parametrize(
    "kw,passes",
    [
        ({}, True),
        ({"debt_service_coverage": 1.0}, True),
        ({"debt_service_coverage": 0.99}, False),
        ({"debt_service_coverage": 0}, True),  # falsy -> not checked
        ({"interest_coverage": 2.0}, True),
        ({"interest_coverage": 1.99}, False),
        ({"interest_coverage": 0}, True),
        ({"payout_ratio": 0.8}, True),
        ({"payout_ratio": 0.81}, False),
        ({"payout_ratio": 0}, True),
    ],
)
def test_stage2_boundaries(scr, kw, passes):
    out = scr.stage2_midfilter([{"ticker": "T", **kw}])
    assert (len(out) == 1) is passes


def test_stage2_stats_first_failure_only_and_output(scr, capsys):
    cs = [
        {"ticker": "A", "debt_service_coverage": 0.5, "interest_coverage": 1, "payout_ratio": 0.9},
        {"ticker": "B", "interest_coverage": 1, "payout_ratio": 0.9},
        {"ticker": "C", "payout_ratio": 0.9},
        {"ticker": "D"},
    ]
    out = scr.stage2_midfilter(cs)
    assert [x["ticker"] for x in out] == ["D"]
    assert scr.stage2_filter == {
        "failed_sustainability": 1,
        "failed_leverage": 1,
        "failed_reinvestment": 1,
    }
    text = capsys.readouterr().out
    assert "Passed: 1 / 4" in text and "Failed: 3 (75.0%)" in text


# ---------------------------------------------------------------- 11-D score
def test_score_empty_company_is_neutral_defaults(scr):
    # ICR default 5 -> 2*(5/5); DSC 1.5 -> 8 + 10; wc 0.1 -> 4
    assert scr.calculate_11d_score({}) == pytest.approx(24.0)


def test_score_none_values_treated_like_missing(scr):
    c = {k: None for k in ("revenue_cagr", "capex_cagr", "avg_fcf", "debt_to_equity")}
    assert scr.calculate_11d_score(c) == pytest.approx(24.0)


@pytest.mark.parametrize(
    "de,expected_delta",
    [
        (0.0, 0.0),
        (1.0, 10 * 0.5 / 1.5),
        (1.4999, 10 * (1.5 - 1.4999) / 1.5),
        (1.5, 5.0),
        (1.99, 5.0),
        (2.0, 0.0),
        (-0.5, 5.0),  # negative D/E falls through to the < 2.0 branch
    ],
)
def test_score_debt_dimension(scr, de, expected_delta):
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score({"debt_to_equity": de}) - base == pytest.approx(expected_delta)


@pytest.mark.parametrize(
    "cagr,delta",
    [
        (11, 24),
        (10.0, 24 * 10 / 10),  # >5 branch: 24*(10/10) = 24
        (7.5, 24 * 0.75),
        (5.0, 24 * (5 / 5) * 0.5),  # not >5 -> third branch
        (2.5, 24 * 0.5 * 0.5),
        (0, 0),
        (-3, 0),
    ],
)
def test_score_capex_dimension_without_timing(scr, cagr, delta):
    # revenue_cagr absent -> timing bonus (needs both) never applies
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score({"capex_cagr": cagr}) - base == pytest.approx(delta)


@pytest.mark.parametrize(
    "cagr,delta",
    [(11, 19), (10.0, 19.0), (7.5, 19 * 0.75), (5.0, 19 * 0.5), (2.5, 19 * 0.25), (0, 0), (-1, 0)],
)
def test_score_revenue_dimension(scr, cagr, delta):
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score({"revenue_cagr": cagr}) - base == pytest.approx(delta)


@pytest.mark.parametrize(
    "kw,delta",
    [
        ({"roic_change": 2.01}, 10),
        ({"roic_change": 2.0}, 0),
        ({"roic_change": 2.0, "ni_margin_change": 0.1}, 7),
        ({"ni_margin_change": 0.1, "oi_margin_change": 5}, 7),
        ({"ni_margin_change": 0.0, "oi_margin_change": 0.1}, 5),
        ({"ni_margin_change": -1, "oi_margin_change": 0}, 0),
    ],
)
def test_score_profitability_dimension(scr, kw, delta):
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score(kw) - base == pytest.approx(delta)


@pytest.mark.parametrize(
    "dsc,delta",
    [
        (1.51, (18) - (8 + 10)),  # >1.5: 8 + 10 vs the 1.5 default's 8*1 + 10*1 -> same 18
        (1.5, 0),
        (1.2, 18 * (1.2 / 1.5) - 18),
        (1.0, 0 - 18),  # 1.0 is not > 1.0
        (0.5, -18),
    ],
)
def test_score_dsc_dimensions(scr, dsc, delta):
    base = scr.calculate_11d_score({})
    got = scr.calculate_11d_score({"debt_service_coverage": dsc}) - base
    assert got == pytest.approx(delta)


@pytest.mark.parametrize(
    "icr,delta",
    [(6, 0), (5.01, 0), (4.0, 2 * 4 / 5 - 2), (3.01, 2 * 3.01 / 5 - 2), (3.0, -2), (1.0, -2)],
)
def test_score_interest_coverage(scr, icr, delta):
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score({"interest_coverage": icr}) - base == pytest.approx(delta)


def test_score_fcf_normalised_by_billion_and_capped(scr):
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score({"avg_fcf": 5e8}) - base == pytest.approx(11.0)
    assert scr.calculate_11d_score({"avg_fcf": 1e9}) - base == pytest.approx(22.0)
    assert scr.calculate_11d_score({"avg_fcf": 5e9}) - base == pytest.approx(22.0)
    assert scr.calculate_11d_score({"avg_fcf": -5e9}) == pytest.approx(base)


def test_score_timing_alignment_boundaries(scr):
    # capex_cagr=2 gives 24*(2/5)*0.5 and revenue_cagr=2 gives 19*(2/5)*0.5, plus timing 4 when
    # 0 < capex < 1.5*revenue
    def s(cap, rev):
        return scr.calculate_11d_score({"capex_cagr": cap, "revenue_cagr": rev})

    no_timing = (24 * (2 / 5) * 0.5) + (19 * (2 / 5) * 0.5) + 24
    assert s(2, 2) == pytest.approx(no_timing + 4)
    # 3.0 == 1.5 * 2.0 -> strict '<' fails, so no timing bonus at the boundary
    a = scr.calculate_11d_score({"capex_cagr": 2.99, "revenue_cagr": 2.0})
    b = scr.calculate_11d_score({"capex_cagr": 3.0, "revenue_cagr": 2.0})
    step = 24 * (0.01 / 5) * 0.5
    assert a - b == pytest.approx(4 - step)


def test_score_asset_efficiency_and_working_capital(scr):
    base = scr.calculate_11d_score({})
    assert scr.calculate_11d_score({"asset_turnover": 1}) == pytest.approx(base)
    assert scr.calculate_11d_score({"asset_turnover": 1.5}) - base == pytest.approx(7 * 0.75)
    assert scr.calculate_11d_score({"asset_turnover": 4}) - base == pytest.approx(7.0)
    # wc_ratio: <0.2 earns 4; 0.2 does not; falsy (0) is replaced by the 0.1 default
    assert scr.calculate_11d_score({"wc_ratio": 0.2}) - base == pytest.approx(-4)
    assert scr.calculate_11d_score({"wc_ratio": 0.19}) == pytest.approx(base)
    assert scr.calculate_11d_score({"wc_ratio": 0}) == pytest.approx(base)


def test_score_capped_at_100(scr):
    c = {
        "debt_to_equity": 0.1,
        "capex_cagr": 20,
        "revenue_cagr": 20,
        "roic_change": 5,
        "debt_service_coverage": 3,
        "interest_coverage": 10,
        "avg_fcf": 9e9,
        "asset_turnover": 3,
        "wc_ratio": 0.05,
    }
    assert scr.calculate_11d_score(c) == 100


@pytest.mark.parametrize(
    "score,tier",
    [
        (100, "Tier 1 (Aggressive Expander)"),
        (75, "Tier 1 (Aggressive Expander)"),
        (74.99, "Tier 2 (Strong Expander)"),
        (50, "Tier 2 (Strong Expander)"),
        (49.99, "Tier 3 (Moderate Expander)"),
        (25, "Tier 3 (Moderate Expander)"),
        (24.99, "Tier 4 (Passive/Mature)"),
        (0, "Tier 4 (Passive/Mature)"),
    ],
)
def test_classify_tier_boundaries(scr, score, tier):
    assert scr.classify_tier(score) == tier


# ---------------------------------------------------------------- pipeline
def _pipeline_universe():
    return [
        _good(ticker="TOP", name="Top", capex_cagr=12, revenue_cagr=12, price_5yr_cagr=30),
        _good(ticker="MID", name="Mid", capex_cagr=2, revenue_cagr=2, price_5yr_cagr=0),
        _good(ticker="NOPX", name="NoPx", capex_cagr=6, revenue_cagr=6, price_5yr_cagr=None),
        _good(ticker="LEV", debt_to_equity=5),  # stage-1 reject
        _good(ticker="DSC", debt_service_coverage=0.1),  # stage-2 reject
    ]


def test_screen_companies_pipeline(scr, capsys):
    res = scr.screen_companies(_pipeline_universe())
    assert isinstance(res, pd.DataFrame)
    assert set(res["ticker"]) == {"TOP", "MID", "NOPX"}
    assert res["expansion_score"].is_monotonic_decreasing
    assert res.iloc[0]["ticker"] == "TOP"
    assert res.iloc[0]["company_name"] == "Top"
    assert res.iloc[0]["tier"] == scr.classify_tier(res.iloc[0]["expansion_score"])
    assert scr.stage3_results is res
    assert scr.stage1_filter["failed_debt"] == 1
    assert scr.stage2_filter["failed_sustainability"] == 1
    out = capsys.readouterr().out
    assert "Total companies to screen: 5" in out
    assert "Stage 1 rejections: 1" in out and "Stage 2 rejections: 1" in out
    assert "Final candidates: 3" in out
    assert "Overall pass rate: 0.01%" in out
    assert "Correlation (expansion score vs price CAGR)" in out
    assert "N/A" in out  # NOPX has no price CAGR, MID has price 0 -> falsy -> N/A too


def test_screen_companies_defaults_for_missing_fields(scr):
    c = {"capex_to_revenue": 0.05, "avg_fcf": 1.0}
    res = scr.screen_companies([c])
    row = res.iloc[0]
    assert row["ticker"] == "N/A" and row["company_name"] == "N/A"
    assert row["country"] == "N/A" and row["sector"] == "N/A"
    assert row["market_cap_usd_b"] == 0
    assert pd.isna(row["revenue_cagr"]) and pd.isna(row["price_5yr_cagr"])


@pytest.mark.parametrize(
    "p1,p2,label",
    [
        ([0, 10, 20], [0, 10, 20], "Strong"),
        ([0, 10, 20, 30], [1, 3, 0, 2], "Weak"),
    ],
)
def test_summary_correlation_interpretation(scr, capsys, p1, p2, label):
    df = pd.DataFrame(
        {
            "ticker": [f"T{i}" for i in range(len(p1))],
            "expansion_score": p1,
            "price_5yr_cagr": p2,
            "revenue_cagr": 1.0,
            "capex_cagr": 1.0,
            "tier": "Tier 4 (Passive/Mature)",
            "sector": "S",
        }
    )
    scr.stage1_filter, scr.stage2_filter = {"a": 1}, {"b": 2}
    scr.print_screening_summary(df)
    out = capsys.readouterr().out
    assert f"Interpretation: {label} signal" in out
    assert "Stage 1 rejections: 1" in out and "Stage 2 rejections: 2" in out


def test_summary_moderate_interpretation(scr, capsys):
    df = pd.DataFrame(
        {
            "ticker": list("ABCDE"),
            "expansion_score": [1.0, 2.0, 3.0, 4.0, 5.0],
            "price_5yr_cagr": [3.0, 1.0, 5.0, 2.0, 4.0],
            "revenue_cagr": 1.0,
            "capex_cagr": 1.0,
            "tier": "Tier 3 (Moderate Expander)",
            "sector": "S",
        }
    )
    corr = df["expansion_score"].corr(df["price_5yr_cagr"])
    assert 0.15 < abs(corr) <= 0.3  # 0.2
    scr.print_screening_summary(df)
    assert "Interpretation: Moderate signal" in capsys.readouterr().out


def test_summary_tier_percentages_and_sectors(scr, capsys):
    df = pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "D"],
            "expansion_score": [90.0, 80.0, 60.0, 10.0],
            "price_5yr_cagr": [1.0, 2.0, 3.0, 4.0],
            "revenue_cagr": [1.0, 2.0, 3.0, 4.0],
            "capex_cagr": [1.0, 2.0, 3.0, 4.0],
            "tier": [
                "Tier 1 (Aggressive Expander)",
                "Tier 1 (Aggressive Expander)",
                "Tier 2 (Strong Expander)",
                "Tier 4 (Passive/Mature)",
            ],
            "sector": ["Tech", "Tech", "Energy", "Tech"],
        }
    )
    scr.print_screening_summary(df)
    out = capsys.readouterr().out
    assert " 50.0%)" in out and " 25.0%)" in out and "  0.0%)" in out
    assert "Tech" in out and "Energy" in out
    assert "Avg expansion score: 60.0/100" in out


def test_save_results_uses_filename_and_returns_path(scr, capsys):
    class Df:
        calls = []

        def to_csv(self, path, index):
            self.calls.append((path, index))

    d = Df()
    p = scr.save_results(d, "x.csv")
    assert p.endswith("/x.csv")
    assert d.calls == [(p, False)]
    assert "Results saved to: x.csv" in capsys.readouterr().out
    p2 = scr.save_results(d)
    assert p2.endswith("/expansion_screening_results_11d.csv")
