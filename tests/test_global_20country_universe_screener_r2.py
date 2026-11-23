"""global_20country_universe_screener: country config, universe synthesis, filters, scoring, reports."""

import re

import pandas as pd
import pytest

from global_20country_universe_screener import Global20CountryScreener

SMALL = {
    "Aland": {
        "region": "Europe",
        "companies": 30,
        "sectors": ["Finance", "Energy"],
        "major_exchanges": ["XA"],
    },
    "China": {
        "region": "Asia-Pacific",
        "companies": 20,
        "sectors": ["Technology"],
        "major_exchanges": ["SSE", "SZSE"],
    },
    "Brazil": {
        "region": "Emerging Markets",
        "companies": 10,
        "sectors": ["Energy"],
        "major_exchanges": ["B3"],
    },
}


@pytest.fixture
def scr():
    return Global20CountryScreener()


@pytest.fixture
def small(scr):
    scr.countries_config = SMALL
    return scr


def _good(**kw):
    c = {
        "ticker": "AAA",
        "name": "Aaa",
        "country": "Aland",
        "region": "Europe",
        "sector": "Finance",
        "debt_to_equity": 0.5,
        "capex_to_revenue": 0.05,
        "avg_fcf": 2e9,
        "ni_margin_change": 1,
        "debt_service_coverage": 2.0,
        "interest_coverage": 6,
        "payout_ratio": 0.3,
        "revenue_cagr": 12,
        "capex_cagr": 12,
        "price_5yr_cagr": 5,
    }
    c.update(kw)
    return c


# ---------------------------------------------------------------- config
def test_init_state(scr):
    assert scr.total_companies == 0 and scr.total_candidates == 0
    assert scr.start_time > 0
    assert scr.countries_config is not None


def test_country_config_shape(scr):
    cfg = scr.countries_config
    for name, c in cfg.items():
        assert set(c) >= {"region", "companies", "sectors", "major_exchanges", "description"}
        assert c["companies"] > 0 and c["sectors"] and c["major_exchanges"]
    assert {c["region"] for c in cfg.values()} == {
        "North America",
        "Europe",
        "Asia-Pacific",
        "Emerging Markets",
    }
    assert cfg["USA"]["companies"] == 5800
    assert sum(c["companies"] for c in cfg.values()) > 25000


# ---------------------------------------------------------------- universe
def test_generate_universe(small, capsys):
    cs = small.generate_global_universe()
    assert len(cs) == 60 and small.total_companies == 60
    by_country = pd.Series([c["country"] for c in cs]).value_counts().to_dict()
    assert by_country == {"Aland": 30, "China": 20, "Brazil": 10}
    assert cs[0]["ticker"] == "ALAND00000" and cs[29]["ticker"] == "ALAND00029"
    assert cs[30]["ticker"] == "CHINA00000"
    assert cs[0]["name"] == "Aland Company 0"
    for c in cs:
        assert c["region"] == SMALL[c["country"]]["region"]
        assert c["sector"] in SMALL[c["country"]]["sectors"]
        assert c["exchange"] in SMALL[c["country"]]["major_exchanges"]
        assert 0.05 <= c["wc_ratio"] <= 0.3
        assert 0.01 <= c["capex_to_revenue"] <= 0.18
        assert 0 <= c["payout_ratio"] <= 0.8
        assert c["market_cap_b"] > 0
    out = capsys.readouterr().out
    assert "Total Companies: 60" in out and "Countries: 3" in out
    assert re.search(r"Brazil\s+Emerging Markets\s+10\s+", out)


def test_generate_universe_seeded_and_accumulates(small):
    a = small.generate_global_universe()
    b = small.generate_global_universe()
    assert [c["market_cap_b"] for c in a] == [c["market_cap_b"] for c in b]
    assert small.total_companies == 120  # accumulates across calls


# ---------------------------------------------------------------- filters
@pytest.mark.parametrize(
    "kw,passes",
    [
        ({}, True),
        ({"debt_to_equity": 2.0}, True),
        ({"debt_to_equity": 2.01}, False),
        ({"capex_to_revenue": 0.005}, True),
        ({"capex_to_revenue": 0.0049}, False),
        ({"avg_fcf": 0}, True),
        ({"avg_fcf": None}, True),
        ({"avg_fcf": -1}, False),
        ({"ni_margin_change": -5}, True),
        ({"ni_margin_change": -5.01}, False),
        ({"ni_margin_change": None}, True),
    ],
)
def test_stage1_boundaries(scr, kw, passes):
    assert (len(scr._stage1_filter([_good(**kw)])) == 1) is passes


def test_stage1_missing_capex_rejected_and_order_kept(scr):
    assert scr._stage1_filter([{"ticker": "X"}]) == []
    out = scr._stage1_filter([_good(ticker="1"), _good(ticker="2", avg_fcf=-1), _good(ticker="3")])
    assert [c["ticker"] for c in out] == ["1", "3"]
    assert scr._stage1_filter([]) == []


@pytest.mark.parametrize(
    "kw,passes",
    [
        ({}, True),
        ({"debt_service_coverage": 1.0}, True),
        ({"debt_service_coverage": 0.99}, False),
        ({"debt_service_coverage": 0}, True),
        ({"interest_coverage": 2.0}, True),
        ({"interest_coverage": 1.99}, False),
        ({"interest_coverage": 0}, True),
        ({"payout_ratio": 0.8}, True),
        ({"payout_ratio": 0.8001}, False),
        ({"payout_ratio": None}, True),
    ],
)
def test_stage2_boundaries(scr, kw, passes):
    assert (len(scr._stage2_filter([{"ticker": "T", **kw}])) == 1) is passes


def test_stage2_missing_fields_pass(scr):
    assert scr._stage2_filter([{"ticker": "T"}]) == [{"ticker": "T"}]
    assert scr._stage2_filter([]) == []


# ---------------------------------------------------------------- scoring
# Empty company: dsc 1.5 -> 10, wc 0.1 -> 4, plus the interest-coverage point (see xfail below)
BASE = 14.0


def _s(scr, **kw):
    return scr._calculate_11d_score(kw)


def test_score_empty_and_none(scr):
    assert _s(scr, interest_coverage=0) == pytest.approx(BASE)
    assert _s(scr, interest_coverage=0, capex_cagr=None, avg_fcf=None) == pytest.approx(BASE)


@pytest.mark.parametrize("cagr,delta", [(10.01, 24), (10, 24.0), (8, 19.2), (5, 0), (-3, 0)])
def test_score_capex(scr, cagr, delta):
    assert _s(scr, interest_coverage=0, capex_cagr=cagr) - BASE == pytest.approx(delta)


@pytest.mark.parametrize("cagr,delta", [(10.01, 19), (10, 19.0), (8, 15.2), (5, 0), (0, 0)])
def test_score_revenue(scr, cagr, delta):
    assert _s(scr, interest_coverage=0, revenue_cagr=cagr) - BASE == pytest.approx(delta)


@pytest.mark.parametrize("roic,delta", [(2.01, 10), (2, 10.0), (1, 5.0), (0.0, 0), (-1, 0)])
def test_score_roic(scr, roic, delta):
    assert _s(scr, interest_coverage=0, roic_change=roic) - BASE == pytest.approx(delta)


def test_score_fcf(scr):
    assert _s(scr, interest_coverage=0, avg_fcf=2.5e8) - BASE == pytest.approx(5.5)
    assert _s(scr, interest_coverage=0, avg_fcf=1e9) - BASE == pytest.approx(22)
    assert _s(scr, interest_coverage=0, avg_fcf=4e9) - BASE == pytest.approx(22)
    assert _s(scr, interest_coverage=0, avg_fcf=-4e9) == pytest.approx(BASE)


@pytest.mark.parametrize(
    "dsc,expected",
    [(1.6, 10 + 8 + 4), (1.5, 10 + 4), (1.2, 8 + 4), (1.0, 4), (0.5, 4)],
)
def test_score_dsc(scr, dsc, expected):
    assert _s(scr, interest_coverage=0, debt_service_coverage=dsc) == pytest.approx(expected)


@pytest.mark.parametrize("de,delta", [(0, 0), (1.0, 10 / 3), (1.5, 0), (4, 0), (-1, 0)])
def test_score_debt(scr, de, delta):
    assert _s(scr, interest_coverage=0, debt_to_equity=de) - BASE == pytest.approx(delta)


def test_score_asset_wc_timing(scr):
    k = {"interest_coverage": 0}
    assert _s(scr, **k, asset_turnover=1) == pytest.approx(BASE)
    assert _s(scr, **k, asset_turnover=1.5) - BASE == pytest.approx(5.25)
    assert _s(scr, **k, asset_turnover=9) - BASE == pytest.approx(7)
    assert _s(scr, **k, wc_ratio=0.2) - BASE == pytest.approx(-4)
    assert _s(scr, **k, wc_ratio=0.19) == pytest.approx(BASE)
    # timing: 0 < capex < 1.5 * revenue
    assert _s(scr, **k, capex_cagr=3, revenue_cagr=2) == pytest.approx(BASE)
    assert _s(scr, **k, capex_cagr=2.99, revenue_cagr=2) == pytest.approx(BASE + 4)


def test_score_cap_100(scr):
    c = _good(
        capex_cagr=20,
        revenue_cagr=20,
        roic_change=5,
        asset_turnover=4,
        wc_ratio=0.05,
        debt_to_equity=0.1,
    )
    assert scr._calculate_11d_score(c) == 100


def test_score_interest_coverage_nonzero_is_not_awarded_point_unless_above_5(scr):
    # Correct behaviour: only coverage > 5 earns the +2 (as in deploy_expansion_screener).
    assert _s(scr, interest_coverage=6) - _s(scr, interest_coverage=0) == pytest.approx(2)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="line `if company.get('interest_coverage', 5) or 5 > 5` parses as `x or (5 > 5)`; any non-zero coverage earns +2",
)
def test_score_low_interest_coverage_gets_no_point(scr):
    assert _s(scr, interest_coverage=1) == pytest.approx(BASE)


@pytest.mark.parametrize(
    "score,tier",
    [
        (75, "Tier 1"),
        (74.99, "Tier 2"),
        (50, "Tier 2"),
        (49.99, "Tier 3"),
        (25, "Tier 3"),
        (24.99, "Tier 4"),
    ],
)
def test_classify_tier(scr, score, tier):
    assert scr._classify_tier(score) == tier


# ---------------------------------------------------------------- stage 3 + run
def test_stage3_sorted_with_fields(scr):
    cs = [_good(ticker="LOW", capex_cagr=0, revenue_cagr=0, avg_fcf=0), _good(ticker="HI")]
    df = scr._stage3_score(cs)
    assert list(df["ticker"]) == ["HI", "LOW"]
    assert df.iloc[0]["region"] == "Europe" and df.iloc[0]["de_ratio"] == 0.5
    assert df.iloc[0]["tier"] == scr._classify_tier(df.iloc[0]["expansion_score"])
    assert df.iloc[0]["market_cap_b"] == 0  # default when absent


def test_run_global_screening_counts(scr, capsys):
    cs = [
        _good(ticker="A"),
        _good(ticker="B", debt_to_equity=9),
        _good(ticker="C", payout_ratio=0.99),
        _good(ticker="D"),
    ]
    scr.total_companies = 4
    res = scr.run_global_screening(cs)
    assert set(res["ticker"]) == {"A", "D"}
    assert scr.total_candidates == 2
    out = capsys.readouterr().out
    assert "Input: 4 | Output: 3" in out and "Input: 3 | Output: 2" in out
    assert "Input: 2 | Output: 2" in out


# ---------------------------------------------------------------- reports
def _results():
    return pd.DataFrame(
        {
            "ticker": ["A1", "B1", "B2", "C1"],
            "country": ["Aland", "Bland", "Bland", "Cland"],
            "region": ["Europe", "Europe", "Asia-Pacific", "Asia-Pacific"],
            "expansion_score": [90.0, 60.0, 55.0, 10.0],
            "tier": ["Tier 1", "Tier 2", "Tier 2", "Tier 4"],
            "revenue_cagr": [10.0, 0.0, 5.0, None],
            "capex_cagr": [4.0, 4.0, 0.0, 1.0],
            "price_5yr_cagr": [10.0, 20.0, 30.0, 40.0],
        }
    )


def test_regional_analysis_stats(scr, capsys):
    stats = scr.generate_regional_analysis(_results())
    assert stats["Europe"] == {"total": 2, "tier1": 1, "tier2": 1, "avg_score": 75.0}
    assert stats["Asia-Pacific"]["total"] == 2
    assert stats["Asia-Pacific"]["tier1"] == 0 and stats["Asia-Pacific"]["tier2"] == 1
    assert stats["Asia-Pacific"]["avg_score"] == pytest.approx(32.5)
    out = capsys.readouterr().out
    # Bland has most companies -> listed first in the country table; avg price (20+30)/2 = 25
    assert re.search(r"Bland\s+2\s+0\s+57\.5\s+25\.0\s*%", out)


def test_final_report_summary_and_top15(scr, capsys):
    scr.total_companies = 40
    scr.total_candidates = 4
    r = _results()
    out = scr.generate_final_report(r)
    assert out is r
    text = capsys.readouterr().out
    assert "Total companies screened: 40" in text
    assert "Pass rate: 10.0%" in text
    assert "Tier 1 (Aggressive): 1" in text and "Tier 2 (Strong): 2" in text
    assert "Tier 3 (Moderate): 0" in text and "Tier 4 (Passive): 1" in text
    assert "10.0%" in text and "N/A" in text


def test_final_report_limits_to_15(scr, capsys):
    scr.total_companies = scr.total_candidates = 20
    n = 20
    r = pd.DataFrame(
        {
            "ticker": [f"T{i:02d}" for i in range(n)],
            "country": "X",
            "expansion_score": [float(100 - i) for i in range(n)],
            "tier": "Tier 1",
            "revenue_cagr": 1.0,
            "capex_cagr": 1.0,
        }
    )
    scr.generate_final_report(r)
    text = capsys.readouterr().out
    assert "T14" in text and "T15" not in text


# ---------------------------------------------------------------- deploy
def test_deploy_global_saves_overall_and_per_country(small, monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(
        pd.DataFrame,
        "to_csv",
        lambda self, path, index=True: saved.append((path, len(self), index)),
    )
    results, stats = small.deploy_global()
    assert small.total_companies == 60
    assert small.total_candidates == len(results)
    assert set(stats) <= {"Europe", "Asia-Pacific", "Emerging Markets"}
    assert sum(v["total"] for v in stats.values()) == len(results)
    overall = [s for s in saved if "global_screening_20countries_" in s[0]]
    assert len(overall) == 1 and overall[0][1] == len(results) and overall[0][2] is False
    per_country = {
        re.search(r"screening_([A-Za-z]+)_\d", p).group(1): n
        for p, n, _ in saved
        if p not in (overall[0][0],)
    }
    assert per_country == results["country"].value_counts().to_dict()
    assert all(s[2] is False for s in saved)
    assert "GLOBAL DEPLOYMENT COMPLETE" in capsys.readouterr().out
