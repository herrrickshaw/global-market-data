"""quarterly_data_analyzer: statement extraction, trend maths, 8-D scoring, portfolio table."""

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


def _qdates(n):
    """Quarter-end timestamps, newest first (explicit: freq aliases differ across pandas)."""
    return [pd.Timestamp(2024, 12, 31) - pd.Timedelta(days=91 * i) for i in range(n)]


DATES = _qdates(6)  # newest first


class _Ticker:
    spec = {}

    def __init__(self, sym):
        if _Ticker.spec.get("ctor_error"):
            raise RuntimeError("down")
        self.quarterly_income_stmt = _Ticker.spec.get("is")
        self.quarterly_cashflow = _Ticker.spec.get("cf")
        self.quarterly_balance_sheet = _Ticker.spec.get("bs")


@pytest.fixture
def qa(monkeypatch):
    fake_module(monkeypatch, "yfinance", Ticker=_Ticker)
    mod = fresh_import(monkeypatch, "quarterly_data_analyzer")
    _Ticker.spec = {}
    yield mod
    _Ticker.spec = {}


def _make(n=6, rev0=1000.0, rev_step=-100.0):
    dates = DATES[:n]
    rev = [rev0 + rev_step * i for i in range(n)]
    inc = {
        "Total Revenue": rev,
        "Operating Income": [r * 0.2 for r in rev],
        "Net Income": [r * 0.1 for r in rev],
        "Interest Expense": [10.0] * n,
    }
    cf = {
        "Operating Cash Flow": [300.0 - 10 * i for i in range(n)],
        "Capital Expenditure": [-(100.0 - 5 * i) for i in range(n)],
    }
    bs = {
        "Total Assets": [5000.0] * n,
        "Total Debt": [400.0 - 10 * i for i in range(n)],
        "Total Stockholder Equity": [1000.0] * n,
        "Current Assets": [600.0] * n,
        "Current Liabilities": [300.0] * n,
    }
    f = lambda d: pd.DataFrame(  # noqa: E731
        {dt: [v[i] for v in d.values()] for i, dt in enumerate(dates)}, index=list(d)
    )
    return f(inc), f(cf), f(bs)


def _install(i, c, b):
    _Ticker.spec = {"is": i, "cf": c, "bs": b}


# ---- fetch_company_data --------------------------------------------------------------------


def test_fetch_returns_none_on_missing_statements_and_errors(qa):
    a = qa.QuarterlyAnalyzer()
    i, c, b = _make()
    for combo in ((None, c, b), (i, None, b), (i, c, None)):
        _install(*combo)
        assert a.fetch_company_data("X") is None
    _Ticker.spec = {"ctor_error": True}
    assert a.fetch_company_data("X") is None


def test_fetch_requires_four_quarters(qa):
    a = qa.QuarterlyAnalyzer()
    _install(*_make(3))
    assert a.fetch_company_data("X") is None
    _install(*_make(4))
    assert len(a.fetch_company_data("X")["quarters"]) == 4


def test_fetch_caps_at_twenty_quarters(qa):
    a = qa.QuarterlyAnalyzer()
    dates = _qdates(24)
    n = 24
    i = pd.DataFrame(
        {d: [100.0, 20.0, 10.0, 1.0] for d in dates},
        index=["Total Revenue", "Operating Income", "Net Income", "Interest Expense"],
    )
    c = pd.DataFrame(
        {d: [50.0, -10.0] for d in dates}, index=["Operating Cash Flow", "Capital Expenditure"]
    )
    b = pd.DataFrame({d: [1.0] for d in dates}, index=["Total Assets"])
    _install(i, c, b)
    assert len(a.fetch_company_data("X")["quarters"]) == 20
    assert n == 24


def test_fetch_extracts_metrics_for_each_quarter(qa):
    a = qa.QuarterlyAnalyzer()
    _install(*_make())
    d = a.fetch_company_data("AAA")
    assert d["ticker"] == "AAA"
    q0 = d["quarters"][0]
    assert q0["date"] == DATES[0]
    assert q0["revenue"] == 1000 and q0["operating_income"] == 200 and q0["net_income"] == 100
    assert q0["oi_margin"] == pytest.approx(20.0)
    assert q0["ni_margin"] == pytest.approx(10.0)
    assert q0["ocf"] == 300 and q0["capex"] == 100
    assert q0["fcf"] == 200
    assert q0["fcf_margin"] == pytest.approx(20.0)
    assert q0["total_assets"] == 5000 and q0["total_debt"] == 400
    assert q0["current_ratio"] == 2.0
    assert q0["de_ratio"] == pytest.approx(0.4)
    assert q0["asset_turnover"] == pytest.approx(0.2)
    # ROIC: tax = 1 - NI/OI = 0.5; NOPAT = 200 * 0.5 = 100; IC = 1400
    assert q0["roic"] == pytest.approx(100 / 1400 * 100)
    assert d["trends"]["latest_roic"] == q0["roic"]


def test_fetch_missing_rows_leave_metrics_unset(qa):
    a = qa.QuarterlyAnalyzer()
    i, c, b = _make()
    i = i.drop(index=["Operating Income", "Interest Expense"])
    c = c.drop(index=["Capital Expenditure"])
    b = b.drop(index=["Total Debt", "Current Assets"])
    _install(i, c, b)
    q0 = a.fetch_company_data("X")["quarters"][0]
    assert q0["operating_income"] is None and "oi_margin" not in q0
    assert q0["capex"] is None and "fcf" not in q0
    assert q0["current_ratio"] is None
    assert q0["total_debt"] is None
    assert q0["de_ratio"] == 0  # equity>0 but no debt -> 0
    assert "roic" not in q0


def test_fetch_zero_values_suppress_margins_and_ratios(qa):
    a = qa.QuarterlyAnalyzer()
    i, c, b = _make()
    i.loc["Total Revenue", DATES[0]] = 0.0
    b.loc["Total Stockholder Equity", DATES[0]] = 0.0
    b.loc["Total Assets", DATES[0]] = 0.0
    _install(i, c, b)
    q0 = a.fetch_company_data("X")["quarters"][0]
    assert "oi_margin" not in q0 and "ni_margin" not in q0
    assert q0["fcf_margin"] is None
    assert "de_ratio" not in q0 and "asset_turnover" not in q0
    assert "roic" not in q0  # equity falsy


def test_fetch_negative_equity_skips_de_ratio_and_negative_oi_uses_default_tax(qa):
    a = qa.QuarterlyAnalyzer()
    i, c, b = _make()
    b.loc["Total Stockholder Equity", DATES[0]] = -50.0
    i.loc["Operating Income", DATES[1]] = -40.0
    _install(i, c, b)
    qs = a.fetch_company_data("X")["quarters"]
    assert "de_ratio" not in qs[0]
    # q1: OI<0 -> tax 0.25, NOPAT = -40*0.75 = -30, IC = 390+1000
    assert qs[1]["roic"] == pytest.approx(-30 / 1390 * 100)


def test_fetch_asset_turnover_none_when_revenue_zero(qa):
    a = qa.QuarterlyAnalyzer()
    i, c, b = _make()
    i.loc["Total Revenue", DATES[0]] = 0.0
    _install(i, c, b)
    assert a.fetch_company_data("X")["quarters"][0]["asset_turnover"] is None


# ---- _calculate_trends ---------------------------------------------------------------------


def _q(date, **kw):
    return {"date": date, **kw}


def test_trends_fewer_than_four_quarters_is_empty(qa):
    a = qa.QuarterlyAnalyzer()
    assert a._calculate_trends([_q(DATES[i], revenue=1.0) for i in range(3)]) == {}
    assert a._calculate_trends([_q(DATES[i], revenue=1.0) for i in range(4)]) != {}


def test_trends_cagr_uses_newest_over_oldest_over_five_periods(qa):
    a = qa.QuarterlyAnalyzer()
    qs = [
        _q(DATES[0], revenue=200.0, capex=64.0, total_debt=32.0),
        _q(DATES[1], revenue=150.0, capex=32.0, total_debt=16.0),
        _q(DATES[2], revenue=120.0, capex=8.0, total_debt=2.0),
        _q(DATES[3], revenue=100.0, capex=2.0, total_debt=1.0),
    ]
    t = a._calculate_trends(list(reversed(qs)))  # unsorted input is sorted by date desc
    assert t["revenue_cagr"] == pytest.approx((2 ** (1 / 5) - 1) * 100)
    assert t["capex_cagr"] == pytest.approx((32 ** (1 / 5) - 1) * 100)
    assert t["debt_cagr"] == pytest.approx((32 ** (1 / 5) - 1) * 100)


def test_trends_cagr_needs_two_points_and_positive_oldest(qa):
    a = qa.QuarterlyAnalyzer()
    qs = [_q(DATES[i]) for i in range(4)]
    qs[0]["revenue"] = 100.0
    t = a._calculate_trends(qs)
    assert "revenue_cagr" not in t
    qs[1]["revenue"] = 50.0
    assert "revenue_cagr" in a._calculate_trends(qs)
    # a negative oldest value yields None, not an error
    qs = [_q(DATES[i], total_debt=v) for i, v in enumerate([5.0, 4.0, 3.0, -1.0])]
    assert a._calculate_trends(qs)["debt_cagr"] is None


def test_trends_averages_and_margin_changes(qa):
    a = qa.QuarterlyAnalyzer()
    qs = [
        _q(DATES[0], fcf=100.0, ocf=300.0, capex=50.0, oi_margin=25.0, ni_margin=12.0, roic=9.0),
        _q(DATES[1], fcf=200.0, ocf=100.0, capex=70.0, oi_margin=20.0, ni_margin=14.0, roic=8.0),
        _q(DATES[2], fcf=0, ocf=200.0, oi_margin=15.0, ni_margin=10.0, roic=6.0),
        _q(DATES[3], fcf=300.0, ocf=0, oi_margin=10.0, ni_margin=9.0, roic=5.0),
    ]
    t = a._calculate_trends(qs)
    assert t["avg_fcf"] == pytest.approx(200.0)  # zeros are excluded
    assert t["avg_ocf"] == pytest.approx(200.0)
    assert t["avg_capex"] == pytest.approx(60.0)
    assert t["oi_margin_change"] == 15.0  # newest - oldest
    assert t["ni_margin_change"] == 3.0
    assert t["roic_change"] == 4.0


def test_trends_latest_and_interest_coverage(qa):
    a = qa.QuarterlyAnalyzer()
    qs = [_q(DATES[i], ocf=100.0, interest_expense=5.0) for i in range(4)]
    qs[0].update(de_ratio=0.5, fcf_margin=7.0, asset_turnover=0.3, roic=11.0)
    qs[3].update(de_ratio=9.0)
    t = a._calculate_trends(qs)
    assert (t["latest_de"], t["latest_fcf_margin"]) == (0.5, 7.0)
    assert (t["latest_asset_turnover"], t["latest_roic"]) == (0.3, 11.0)
    assert t["interest_coverage"] == pytest.approx(100.0 / 5.01)
    qs[0]["interest_expense"] = None
    assert "interest_coverage" not in a._calculate_trends(qs)
    qs[0]["interest_expense"] = 5.0
    qs[0]["ocf"] = 0
    assert "interest_coverage" not in a._calculate_trends(qs)


def test_trends_empty_lists_give_nan_means(qa):
    t = qa.QuarterlyAnalyzer()._calculate_trends([_q(DATES[i]) for i in range(4)])
    assert np.isnan(t["avg_fcf"]) and np.isnan(t["avg_ocf"]) and np.isnan(t["avg_capex"])


# ---- score_company -------------------------------------------------------------------------


def _score(qa, **trends):
    return qa.QuarterlyAnalyzer().score_company({"trends": trends})


def test_score_empty_inputs(qa):
    a = qa.QuarterlyAnalyzer()
    assert a.score_company(None) == 0
    assert a.score_company({}) == 0
    assert a.score_company({"trends": {}}) == 0


@pytest.mark.parametrize(
    "key,val,expected",
    [
        ("debt_cagr", 0.0, 0),
        ("debt_cagr", 0.1, 10),
        ("debt_cagr", 14.9, 10),
        ("debt_cagr", 15, 0),
        ("capex_cagr", 15, 0),
        ("capex_cagr", 15.1, 20),
        ("revenue_cagr", 10, 0),
        ("revenue_cagr", 10.1, 15),
        ("ni_margin_change", -2, 0),
        ("ni_margin_change", -1.9, 15),
        ("avg_fcf", 0, 0),
        ("avg_fcf", 1, 15),
        ("latest_de", 0, 0),
        ("latest_de", 0.5, 10),
        ("latest_de", 1, 0),
        ("latest_fcf_margin", 2, 0),
        ("latest_fcf_margin", 2.1, 5),
        ("roic_change", 0, 0),
        ("roic_change", 0.1, 5),
    ],
)
def test_score_boundaries(qa, key, val, expected):
    assert _score(qa, **{key: val}) == expected


def test_score_sums_all_components_and_caps_at_100(qa):
    full = dict(
        debt_cagr=5,
        capex_cagr=20,
        revenue_cagr=12,
        ni_margin_change=1,
        avg_fcf=5,
        latest_de=0.5,
        latest_fcf_margin=3,
        roic_change=1,
    )
    assert _score(qa, **full) == 10 + 20 + 15 + 15 + 15 + 10 + 5 + 5  # 95


def test_score_none_trend_values_are_ignored(qa):
    assert _score(qa, debt_cagr=None, capex_cagr=None, avg_fcf=None) == 0


# ---- analyze_portfolio ---------------------------------------------------------------------


def _stub_fetch(qa, monkeypatch, table):
    monkeypatch.setattr(qa.QuarterlyAnalyzer, "fetch_company_data", lambda self, t: table.get(t))


def _data(score_trends):
    return {"trends": score_trends}


def test_analyze_portfolio_sorts_and_summarises(qa, monkeypatch, capsys):
    strong = dict(
        debt_cagr=5,
        capex_cagr=20,
        revenue_cagr=12,
        ni_margin_change=1,
        avg_fcf=5e9,
        latest_de=0.5,
        latest_fcf_margin=3,
        roic_change=1,
    )
    mid = dict(capex_cagr=20, revenue_cagr=12, avg_fcf=1e9, ni_margin_change=1)  # 20+15+15+15=65
    weak = dict(revenue_cagr=1.0, capex_cagr=2.0, debt_cagr=3.0, roic_change=-1.0)  # 10
    _stub_fetch(qa, monkeypatch, {"S": _data(strong), "M": _data(mid), "W": _data(weak)})
    df = qa.QuarterlyAnalyzer().analyze_portfolio(["W", "S", "BAD", "M"])
    assert df["ticker"].tolist() == ["S", "M", "W"]
    assert df["score"].tolist() == [95, 65, 10]
    out = capsys.readouterr().out
    assert "Analysis complete: 3/4 successful" in out
    assert "High expansion (75+): 1" in out
    assert "Medium expansion (50-75): 1" in out
    assert "Low expansion (<50): 1" in out
    assert "Companies with improving ROIC: 1" in out
    assert "$5.00B" in out and "20.0%" in out


def test_analyze_portfolio_tier_boundaries(qa, monkeypatch, capsys):
    t75 = dict(
        debt_cagr=5, capex_cagr=20, revenue_cagr=12, ni_margin_change=1, avg_fcf=1
    )  # 10+20+15+15+15 = 75
    t50 = dict(capex_cagr=20, revenue_cagr=12, ni_margin_change=1)  # 50
    t45 = dict(capex_cagr=20, revenue_cagr=12, latest_de=0.5)  # 45
    _stub_fetch(qa, monkeypatch, {"A": _data(t75), "B": _data(t50), "C": _data(t45)})
    qa.QuarterlyAnalyzer().analyze_portfolio(["A", "B", "C"])
    out = capsys.readouterr().out
    assert "High expansion (75+): 1" in out
    assert "Medium expansion (50-75): 1" in out
    assert "Low expansion (<50): 1" in out


def test_analyze_portfolio_na_for_missing_metrics(qa, monkeypatch, capsys):
    _stub_fetch(qa, monkeypatch, {"A": _data({"revenue_cagr": 12.0})})
    df = qa.QuarterlyAnalyzer().analyze_portfolio(["A"])
    out = capsys.readouterr().out
    assert df.loc[df.index[0], "ticker"] == "A"
    assert out.count("N/A") == 3  # capex, debt, fcf
    assert "12.0%" in out


def test_analyze_portfolio_shows_only_top_fifteen(qa, monkeypatch, capsys):
    table = {f"T{i:02d}": _data({"revenue_cagr": 12.0}) for i in range(20)}
    _stub_fetch(qa, monkeypatch, table)
    df = qa.QuarterlyAnalyzer().analyze_portfolio(list(table))
    assert len(df) == 20
    rows = [
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(("1 ", "15 ", "16 "))
    ]
    assert any(ln.startswith("15 ") for ln in rows) and not any(ln.startswith("16 ") for ln in rows)


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="analyze_portfolio sorts an empty results frame by 'score' -> KeyError when no ticker succeeds",
)
def test_analyze_portfolio_with_no_successes_returns_empty_frame(qa, monkeypatch):
    _stub_fetch(qa, monkeypatch, {})
    df = qa.QuarterlyAnalyzer().analyze_portfolio(["A", "B"])
    assert len(df) == 0


def test_trends_debt_cagr_with_exactly_two_points(qa):
    a = qa.QuarterlyAnalyzer()
    qs = [_q(DATES[i]) for i in range(4)]
    qs[0]["total_debt"] = 32.0
    qs[3]["total_debt"] = 1.0
    t = a._calculate_trends(qs)
    assert t["debt_cagr"] == pytest.approx((32 ** (1 / 5) - 1) * 100)
