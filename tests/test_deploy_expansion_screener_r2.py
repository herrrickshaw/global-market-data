"""deploy_expansion_screener: loaders, 3-stage filters, 11-D score, reports and deployment flow."""

import json

import pandas as pd
import pytest

import deploy_expansion_screener as des


@pytest.fixture
def dep():
    return des.ScreenerDeployment()


def _good(**kw):
    c = {
        "ticker": "AAA",
        "name": "Aaa",
        "country": "USA",
        "sector": "Tech",
        "debt_to_equity": 0.5,
        "capex_to_revenue": 0.05,
        "capex_cagr": 12,
        "avg_fcf": 2e9,
        "ni_margin_change": 1,
        "revenue_cagr": 12,
        "price_5yr_cagr": 10,
    }
    c.update(kw)
    return c


# ---------------------------------------------------------------- loading
def test_init_state(dep):
    assert dep.results_summary == {}
    assert dep.deployment_time is not None


def test_load_unknown_source_raises(dep):
    with pytest.raises(ValueError, match="Unknown data source: db"):
        dep.load_company_data("db")


def test_load_csv_is_unimplemented_stub_returning_none(dep):
    assert dep.load_company_data("csv") is None


def test_synthetic_universe_shape_and_determinism(dep):
    a = dep.load_company_data("synthetic")
    assert len(a) == 25000
    assert a[0]["ticker"] == "SYM00000" and a[-1]["ticker"] == "SYM24999"
    assert a[7]["name"] == "Company 7"
    for c in a[:200]:
        assert c["country"] in {"USA", "China", "Japan", "Germany", "UK", "India", "Singapore"}
        assert 0.05 <= c["wc_ratio"] <= 0.25
        assert 0.01 <= c["capex_to_revenue"] <= 0.15
        assert 0 <= c["payout_ratio"] <= 0.8
    # seeded (42) -> identical on a second call
    b = dep._generate_synthetic_universe()
    assert [c["market_cap_b"] for c in a[:50]] == [c["market_cap_b"] for c in b[:50]]
    assert any(c["avg_fcf"] == -100 for c in a)


# ---------------------------------------------------------------- stage 1
@pytest.mark.parametrize(
    "kw,passes",
    [
        ({}, True),
        ({"debt_to_equity": 2.0}, True),
        ({"debt_to_equity": 2.01}, False),
        ({"capex_to_revenue": 0.005}, True),
        ({"capex_to_revenue": 0.0049}, False),
        ({"capex_cagr": -15}, True),
        ({"capex_cagr": -15.5}, False),
        ({"capex_cagr": 0}, True),
        ({"avg_fcf": 0}, True),
        ({"avg_fcf": None}, True),  # `or 0` -> not negative (differs from phased screener)
        ({"avg_fcf": -0.1}, False),
        ({"ni_margin_change": -5}, True),
        ({"ni_margin_change": -5.01}, False),
        ({"ni_margin_change": None}, True),
    ],
)
def test_stage1_boundaries(dep, kw, passes):
    assert (len(dep._stage1_prefilter([_good(**kw)])) == 1) is passes


def test_stage1_output_counts(dep, capsys):
    cs = [
        _good(ticker="A"),
        _good(ticker="B", debt_to_equity=9, capex_to_revenue=0),
        _good(ticker="C", capex_to_revenue=0, avg_fcf=-1),
        _good(ticker="D", avg_fcf=-1, ni_margin_change=-9),
        _good(ticker="E", ni_margin_change=-9),
    ]
    out = dep._stage1_prefilter(cs)
    assert [c["ticker"] for c in out] == ["A"]
    text = capsys.readouterr().out
    assert "Input: 5 companies" in text
    assert "Output: 1 companies (20.0%)" in text
    assert "Rejected: 4 (80.0%)" in text


def test_stage1_missing_capex_defaults_to_reject(dep):
    assert dep._stage1_prefilter([{"ticker": "X"}]) == []


@pytest.mark.xfail(
    strict=True,
    raises=ZeroDivisionError,
    reason="_stage1_prefilter prints len(passed)/len(companies); empty input divides by zero",
)
def test_stage1_empty_input(dep):
    assert dep._stage1_prefilter([]) == []


# ---------------------------------------------------------------- stage 2
@pytest.mark.parametrize(
    "kw,passes",
    [
        ({}, True),
        ({"debt_service_coverage": 1.0}, True),
        ({"debt_service_coverage": 0.99}, False),
        ({"debt_service_coverage": 0}, True),  # `or 1.5`
        ({"interest_coverage": 2.0}, True),
        ({"interest_coverage": 1.99}, False),
        ({"interest_coverage": 0}, True),  # `or 5`
        ({"payout_ratio": 0.8}, True),
        ({"payout_ratio": 0.81}, False),
        ({"payout_ratio": None}, True),
    ],
)
def test_stage2_boundaries(dep, kw, passes):
    assert (len(dep._stage2_midfilter([{"ticker": "T", **kw}])) == 1) is passes


def test_stage2_output_counts(dep, capsys):
    out = dep._stage2_midfilter(
        [
            {"ticker": "A"},
            {"ticker": "B", "payout_ratio": 0.9},
            {"ticker": "C"},
            {"ticker": "D", "interest_coverage": 1},
        ]
    )
    assert [c["ticker"] for c in out] == ["A", "C"]
    text = capsys.readouterr().out
    assert "Output: 2 companies (50.0%)" in text and "Rejected: 2 (50.0%)" in text


@pytest.mark.xfail(
    strict=True,
    raises=ZeroDivisionError,
    reason="_stage2_midfilter prints len(passed)/len(companies); empty input divides by zero",
)
def test_stage2_empty_input(dep):
    assert dep._stage2_midfilter([]) == []


# ---------------------------------------------------------------- scoring
BASE = 14.0  # dsc default 1.5 -> 10*(1.5/1.5); wc default 0.1 -> +4


def test_score_empty_company(dep):
    assert dep._calculate_11d_score({}) == pytest.approx(BASE)


def test_score_none_values_like_missing(dep):
    c = {k: None for k in ("revenue_cagr", "capex_cagr", "avg_fcf", "roic_change")}
    assert dep._calculate_11d_score(c) == pytest.approx(BASE)


@pytest.mark.parametrize(
    "cagr,delta",
    [(10.01, 24), (10, 24.0), (7.5, 18.0), (5.01, 24 * 0.501), (5, 0), (-2, 0)],
)
def test_score_capex(dep, cagr, delta):
    # revenue_cagr = 0 so timing bonus never applies
    assert dep._calculate_11d_score({"capex_cagr": cagr}) - BASE == pytest.approx(delta)


@pytest.mark.parametrize("cagr,delta", [(10.01, 19), (10, 19.0), (6, 11.4), (5, 0), (0, 0)])
def test_score_revenue(dep, cagr, delta):
    assert dep._calculate_11d_score({"revenue_cagr": cagr}) - BASE == pytest.approx(delta)


@pytest.mark.parametrize(
    "roic,delta", [(2.01, 10), (2, 10.0), (1, 5.0), (0.5, 2.5), (0, 0), (-1, 0)]
)
def test_score_roic(dep, roic, delta):
    assert dep._calculate_11d_score({"roic_change": roic}) - BASE == pytest.approx(delta)


def test_score_fcf_normalised_and_capped(dep):
    assert dep._calculate_11d_score({"avg_fcf": 5e8}) - BASE == pytest.approx(11.0)
    assert dep._calculate_11d_score({"avg_fcf": 1e9}) - BASE == pytest.approx(22.0)
    assert dep._calculate_11d_score({"avg_fcf": 9e9}) - BASE == pytest.approx(22.0)
    assert dep._calculate_11d_score({"avg_fcf": -9e9}) == pytest.approx(BASE)


@pytest.mark.parametrize(
    "dsc,expected",
    [
        (1.6, 10 + 8 + 4),  # >1.5: dsc 10 + sustainability 8 + wc 4
        (1.5, 10 + 4),
        (1.2, 10 * (1.2 / 1.5) + 4),
        (1.0, 4),
        (0.4, 4),
    ],
)
def test_score_dsc(dep, dsc, expected):
    assert dep._calculate_11d_score({"debt_service_coverage": dsc}) == pytest.approx(expected)


@pytest.mark.parametrize(
    "de,delta",
    [(0.0, 0), (1.0, 10 * 0.5 / 1.5), (1.49, 10 * 0.01 / 1.5), (1.5, 0), (3, 0), (-1, 0)],
)
def test_score_debt_expansion(dep, de, delta):
    assert dep._calculate_11d_score({"debt_to_equity": de}) - BASE == pytest.approx(delta)


def test_score_asset_efficiency_icr_wc(dep):
    assert dep._calculate_11d_score({"asset_turnover": 1}) == pytest.approx(BASE)
    assert dep._calculate_11d_score({"asset_turnover": 1.5}) - BASE == pytest.approx(5.25)
    assert dep._calculate_11d_score({"asset_turnover": 5}) - BASE == pytest.approx(7.0)
    assert dep._calculate_11d_score({"interest_coverage": 5.01}) - BASE == pytest.approx(2)
    assert dep._calculate_11d_score({"interest_coverage": 5}) == pytest.approx(BASE)
    assert dep._calculate_11d_score({"wc_ratio": 0.2}) - BASE == pytest.approx(-4)
    assert dep._calculate_11d_score({"wc_ratio": 0.19}) == pytest.approx(BASE)
    assert dep._calculate_11d_score({"wc_ratio": 0}) == pytest.approx(BASE)  # `or 0.1`


def test_score_timing_bonus_boundary(dep):
    # capex 3 vs rev 2: 3 < 3.0 is False -> no bonus; 2.99 < 3.0 -> bonus (no other terms)
    assert dep._calculate_11d_score({"capex_cagr": 3, "revenue_cagr": 2}) == pytest.approx(BASE)
    assert dep._calculate_11d_score({"capex_cagr": 2.99, "revenue_cagr": 2}) == pytest.approx(
        BASE + 4
    )
    assert dep._calculate_11d_score({"capex_cagr": 1, "revenue_cagr": 0}) == pytest.approx(BASE)


def test_score_cap_at_100(dep):
    c = {
        "capex_cagr": 20,
        "revenue_cagr": 20,
        "avg_fcf": 1e10,
        "roic_change": 9,
        "debt_service_coverage": 3,
        "debt_to_equity": 0.1,
        "asset_turnover": 3,
        "interest_coverage": 9,
        "wc_ratio": 0.05,
    }
    assert dep._calculate_11d_score(c) == 100


@pytest.mark.parametrize(
    "score,tier",
    [
        (75, "Tier 1 (Aggressive Expander)"),
        (74.9, "Tier 2 (Strong Expander)"),
        (50, "Tier 2 (Strong Expander)"),
        (49.9, "Tier 3 (Moderate Expander)"),
        (25, "Tier 3 (Moderate Expander)"),
        (24.9, "Tier 4 (Passive/Mature)"),
    ],
)
def test_classify_tier(dep, score, tier):
    assert dep._classify_tier(score) == tier


# ---------------------------------------------------------------- stage 3 / run
def test_stage3_sorted_and_fields(dep):
    cs = [
        _good(ticker="LOW", capex_cagr=0, revenue_cagr=0, avg_fcf=0),
        _good(ticker="HI"),
        {"avg_fcf": 1},
    ]
    df = dep._stage3_fullscore(cs)
    assert list(df["ticker"])[0] == "HI"
    assert df["expansion_score"].is_monotonic_decreasing
    na = df[df["ticker"] == "N/A"].iloc[0]
    assert na["name"] == "N/A" and na["country"] == "N/A" and na["market_cap_b"] == 0
    assert pd.isna(na["revenue_cagr"])
    hi = df.iloc[0]
    assert hi["de_ratio"] == 0.5 and hi["tier"] == dep._classify_tier(hi["expansion_score"])


def test_run_screening_summary(dep):
    cs = [
        _good(ticker="A"),
        _good(ticker="B", debt_to_equity=5),
        _good(ticker="C", debt_service_coverage=0.1),
        _good(ticker="D"),
    ]
    df = dep.run_screening(cs)
    assert dep.results_summary == {
        "total_input": 4,
        "stage1_output": 3,
        "stage2_output": 2,
        "final_output": 2,
    }
    assert set(df["ticker"]) == {"A", "D"}


# ---------------------------------------------------------------- reports
def _results():
    return pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "expansion_score": [90.0, 55.0, 5.0],
            "tier": [
                "Tier 1 (Aggressive Expander)",
                "Tier 2 (Strong Expander)",
                "Tier 4 (Passive/Mature)",
            ],
            "sector": ["Tech", "Tech", "Energy"],
            "revenue_cagr": [10.0, 0.0, None],
            "capex_cagr": [5.0, 1.0, 2.0],
            "price_5yr_cagr": [1.5, 2.5, 0.0],
        }
    )


def test_generate_reports_prints_and_saves(dep, monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(
        pd.DataFrame, "to_csv", lambda self, path, index=True: saved.append((path, index))
    )
    r = _results()
    out = dep.generate_reports(r)
    assert out is r
    assert len(saved) == 1
    path, index = saved[0]
    assert index is False
    assert "expansion_screening_results_" in path and path.endswith(".csv")
    text = capsys.readouterr().out
    assert "Tier 1 (Aggressive Expander)" in text
    assert "(" in text and " 33.3%)" in text and "  0.0%)" in text  # Tier 3 absent
    assert "10.0%" in text and "N/A" in text
    assert "Tech" in text and "Energy" in text


def test_generate_reports_top20_limit(dep, monkeypatch, capsys):
    monkeypatch.setattr(pd.DataFrame, "to_csv", lambda self, *a, **k: None)
    n = 30
    r = pd.DataFrame(
        {
            "ticker": [f"T{i:02d}" for i in range(n)],
            "expansion_score": [float(100 - i) for i in range(n)],
            "tier": "Tier 1 (Aggressive Expander)",
            "sector": "S",
            "revenue_cagr": 1.0,
            "capex_cagr": 1.0,
            "price_5yr_cagr": 1.0,
        }
    )
    dep.generate_reports(r)
    text = capsys.readouterr().out
    assert "T19" in text and "T20" not in text


def test_summary_dict(dep):
    dep.results_summary = {"total_input": 8, "final_output": 3}
    s = dep.create_deployment_summary(_results())
    assert s["total_companies_screened"] == 8
    assert s["total_candidates"] == 3
    assert s["pass_rate"] == "37.5%"
    assert s["average_score"] == pytest.approx(50.0)
    assert s["tier_distribution"]["Tier 1 (Aggressive Expander)"] == 1
    assert s["top_10_candidates"][0] == {
        "ticker": "A",
        "expansion_score": 90.0,
        "tier": "Tier 1 (Aggressive Expander)",
    }
    assert s["deployment_time"] == dep.deployment_time.isoformat()


def test_summary_defaults_when_no_run(dep):
    s = dep.create_deployment_summary(_results())
    assert s["total_companies_screened"] == 0 and s["total_candidates"] == 0
    assert s["pass_rate"] == "0.0%"


# ---------------------------------------------------------------- deploy
def test_deploy_end_to_end_without_touching_disk(dep, monkeypatch, capsys):
    csv_paths = []
    monkeypatch.setattr(
        pd.DataFrame, "to_csv", lambda self, path, index=True: csv_paths.append(path)
    )
    small = [
        _good(ticker="A"),
        _good(ticker="B", debt_to_equity=5),
        _good(ticker="C", capex_cagr=3, revenue_cagr=3),
    ]
    monkeypatch.setattr(dep, "load_company_data", lambda source="synthetic": small)

    written = {}

    class FakeFile:
        def __init__(self, path, mode):
            written["path"], written["mode"], written["buf"] = path, mode, []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, s):
            written["buf"].append(s)

    monkeypatch.setattr(des, "open", FakeFile, raising=False)
    results = dep.deploy()
    assert set(results["ticker"]) == {"A", "C"}
    assert len(csv_paths) == 1
    assert written["mode"] == "w"
    assert "deployment_summary_" in written["path"] and written["path"].endswith(".json")
    payload = json.loads("".join(written["buf"]))
    assert payload["total_companies_screened"] == 3
    assert payload["total_candidates"] == 2
    assert payload["pass_rate"] == "66.7%"
    text = capsys.readouterr().out
    assert "Screened: 3 companies" in text and "Qualified: 2 candidates" in text
    assert "Pass rate: 66.7%" in text
    assert "DEPLOYMENT SUCCESSFUL" in text
