"""sec_fundamentals: SEC companyfacts -> strategy fundamentals dict.

All tests use small synthetic companyfacts dicts; nothing touches the network.
Each 10-K re-reports the prior year as a comparative, so the same period-end
appears in several filings with different `filed` dates — the fixtures model
that, because it is exactly where point-in-time correctness is won or lost.
"""

import pytest

import sec_fundamentals as sf


def _fact(end, val, filed, start=None, form="10-K", fp="FY"):
    r = {"end": end, "val": val, "filed": filed, "form": form, "fp": fp}
    if start:
        r["start"] = start
    return r


def _facts(**concepts):
    return {"us-gaap": {c: {"units": {"USD": rows}} for c, rows in concepts.items()}}


# FY2019 net income first reported in the FY2019 10-K (filed 2020-02-15) as 100,
# then RESTATED to 80 in the FY2020 10-K (filed 2021-02-15), which also reports
# FY2020 = 120 for the first time.
NI = [
    _fact("2019-12-31", 100, "2020-02-15", start="2019-01-01"),
    _fact("2019-12-31", 80, "2021-02-15", start="2019-01-01"),
    _fact("2020-12-31", 120, "2021-02-15", start="2020-01-01"),
]
ASSETS = [  # instant (balance-sheet) facts carry no `start`
    _fact("2019-12-31", 1000, "2020-02-15"),
    _fact("2019-12-31", 1000, "2021-02-15"),
    _fact("2020-12-31", 1200, "2021-02-15"),
]


# ── basic extraction ────────────────────────────────────────────────────────────
def test_annual_newest_first_and_dedup_by_period_end():
    vals = sf._annual(_facts(NetIncomeLoss=NI), "NetIncomeLoss")
    assert vals == [120, 80]  # one value per fiscal year, newest first, restated


def test_annual_ignores_10q_and_missing_values():
    rows = NI + [
        _fact("2021-03-31", 999, "2021-05-01", start="2021-01-01", form="10-Q", fp="Q1"),
        {"end": "2018-12-31", "val": None, "filed": "2019-02-01", "form": "10-K", "fp": "FY"},
    ]
    assert sf._annual(_facts(NetIncomeLoss=rows), "NetIncomeLoss") == [120, 80]


def test_annual_missing_concept_is_empty():
    assert sf._annual(_facts(), "NetIncomeLoss") == []
    assert sf._first(_facts(), "NetIncomeLoss", "ProfitLoss") == []


def test_first_falls_back_to_next_concept():
    f = _facts(SalesRevenueNet=[_fact("2020-12-31", 5, "2021-02-01", start="2020-01-01")])
    assert sf._first(f, "Revenues", "SalesRevenueNet") == [5]


def test_helpers_ratio_margin_pct():
    assert sf._ratio(1, 4) == 0.25
    assert sf._ratio(1, 0) is None and sf._ratio(None, 4) is None
    assert sf._margin(40, 100) == 40.0
    assert sf._margin(150, 100) is None  # >100% = concept mismatch, nulled
    assert sf._pct(110, 100) == pytest.approx(10.0)
    assert sf._pct(-50, -100) == pytest.approx(50.0)  # loss shrinking = improvement
    assert sf._pct(1, 0) is None


def _via_ticker(monkeypatch, facts):
    """Drive the public fundamentals(ticker) entry point with canned facts."""
    monkeypatch.setattr(sf, "companyfacts", lambda t: facts)
    return sf.fundamentals("T")


def test_fundamentals_computes_roa(monkeypatch):
    out = _via_ticker(monkeypatch, _facts(NetIncomeLoss=NI, Assets=ASSETS))
    assert out["source"] == "SEC-EDGAR"
    assert out["net_income"] == 120 and out["net_income_prev"] == 80
    assert out["roa"] == pytest.approx(10.0)  # 120 / 1200
    assert out["roa_prev"] == pytest.approx(8.0)  # restated 80 / 1000


def test_fundamentals_unknown_ticker_returns_empty(monkeypatch):
    monkeypatch.setattr(sf, "companyfacts", lambda t: None)
    assert sf.fundamentals("NOPE") == {}


# ── BUG: quarters tagged fp=FY inside a 10-K were treated as fiscal years ──────
def test_quarter_breakouts_in_10k_are_not_annual_values():
    """A 10-K carries quarterly breakouts labelled form=10-K, fp=FY (shape taken
    from ADM 2023, documented in sec_history_collector._is_annual_period). Only the
    duration separates them; a Q4 row shares the annual row's period-end."""
    rows = [
        _fact("2022-12-31", 90.0, "2023-02-15", start="2022-01-01"),
        _fact("2023-12-31", 93.94, "2024-02-15", start="2023-01-01"),  # real annual
        _fact("2023-03-31", 24.07, "2024-02-15", start="2023-01-01"),  # quarters
        _fact("2023-06-30", 25.19, "2024-02-15", start="2023-04-01"),
        _fact("2023-09-30", 21.70, "2024-02-15", start="2023-07-01"),
        _fact("2023-12-31", 22.98, "2024-02-15", start="2023-10-01"),
    ]
    assert sf._annual(_facts(Revenues=rows), "Revenues") == [93.94, 90.0]


# ── BUG: "keep last" depended on JSON row order, not on the filing date ────────
def test_restated_value_chosen_by_filed_date_not_row_order():
    rows = [NI[1], NI[2], NI[0]]  # original 2020 filing listed last
    assert sf._annual(_facts(NetIncomeLoss=rows), "NetIncomeLoss") == [120, 80]


# ── BUG: no point-in-time view — facts filed after as_of leaked in ─────────────
def test_point_in_time_excludes_facts_filed_after_as_of():
    """As of 2020-06-30 only the FY2019 10-K (filed 2020-02-15) was public: the
    FY2020 value (filed 2021) must not exist, and FY2019 must be the ORIGINAL 100,
    not the 80 it was restated to a year later."""
    f = _facts(NetIncomeLoss=NI, Assets=ASSETS)
    assert sf._annual(f, "NetIncomeLoss", as_of="2020-06-30") == [100]
    out = sf.fundamentals_from_facts(f, as_of="2020-06-30")
    assert out["net_income"] == 100
    assert "net_income_prev" not in out
    assert out["roa"] == pytest.approx(10.0)  # 100 / 1000, all as first reported


def test_point_in_time_on_filing_day_and_before_any_filing():
    f = _facts(NetIncomeLoss=NI)
    assert sf._annual(f, "NetIncomeLoss", as_of="2021-02-15") == [120, 80]  # inclusive
    assert sf._annual(f, "NetIncomeLoss", as_of="2021-02-14") == [100]
    assert sf._annual(f, "NetIncomeLoss", as_of="2020-01-01") == []
    assert sf.fundamentals_from_facts(f, as_of="2020-01-01") == {}


def test_point_in_time_accepts_date_objects():
    import datetime as dt

    assert sf._annual(_facts(NetIncomeLoss=NI), "NetIncomeLoss", as_of=dt.date(2020, 6, 30)) == [
        100
    ]


# ── BUG: revenue_5y_ago was the value 3 years back, CAGR'd over 5 years ────────
def test_revenue_5y_ago_is_five_fiscal_years_back(monkeypatch):
    """Revenue compounding at exactly 15%/yr for 6 fiscal years. Coffee Can
    annualises revenue/revenue_5y_ago over 5 years, so the field must be the value
    5 years back; before the fix it was index 3 (3 years back), giving an 8.7% CAGR
    and failing a 15%-grower on the 10% threshold."""
    from strategies import get
    from strategies.base import StockData

    rows = [
        _fact(f"{y}-12-31", 100 * 1.15 ** (y - 2018), f"{y + 1}-02-15", start=f"{y}-01-01")
        for y in range(2018, 2024)
    ]
    out = _via_ticker(monkeypatch, _facts(Revenues=rows))
    assert out["revenue"] == pytest.approx(100 * 1.15**5)
    assert out["revenue_5y_ago"] == pytest.approx(100)
    r = get("coffee_can").screen(
        StockData("T", fundamentals={**out, "roe": 20, "debt_to_equity": 0.2})
    )
    assert r.metrics["RevCAGR5y"] == pytest.approx(15.0, abs=0.1)
    assert r.metrics["RevCAGR>=10"] == 1


def test_revenue_5y_ago_absent_with_short_history(monkeypatch):
    rows = [
        _fact(f"{y}-12-31", 100.0 + y, f"{y + 1}-02-15", start=f"{y}-01-01")
        for y in range(2020, 2024)
    ]
    out = _via_ticker(monkeypatch, _facts(Revenues=rows))
    assert "revenue_5y_ago" not in out  # 4 years of data cannot give a 5-year base
