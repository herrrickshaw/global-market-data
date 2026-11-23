"""quarterly_data_collector: yfinance quarterly statements -> frame, metrics, collection, trends."""

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load


@pytest.fixture
def qdc(monkeypatch, tmp_path):
    mod = load(monkeypatch, "quarterly_data_collector", tmp_path)
    FakeTicker.spec = {}
    return mod


@pytest.fixture
def col(qdc):
    return qdc.QuarterlyDataCollector()


def _stmt(rows, dates):
    """DataFrame indexed by statement line item, columns = quarter-end Timestamps."""
    data = {k: list(v) for k, v in rows.items()}
    return pd.DataFrame(data, index=pd.DatetimeIndex(dates)).T


def _spec(n=6, newest_first=True, **drop):
    dates = list(pd.date_range("2022-03-31", periods=n, freq="QE"))
    if newest_first:
        dates = dates[::-1]
    cf = {"Operating Cash Flow": [100.0 + i for i in range(n)]}
    cf["Capital Expenditure"] = [-(10.0 + i) for i in range(n)]
    inc = {
        "Total Revenue": [1000.0 + i for i in range(n)],
        "Operating Income": [200.0 + i for i in range(n)],
        "Net Income": [150.0 + i for i in range(n)],
    }
    bs = {
        "Total Assets": [5000.0 + i for i in range(n)],
        "Total Debt": [800.0 + i for i in range(n)],
        "Total Stockholder Equity": [2000.0 + i for i in range(n)],
    }
    for k in drop.get("cf", []):
        cf.pop(k)
    for k in drop.get("inc", []):
        inc.pop(k)
    for k in drop.get("bs", []):
        bs.pop(k)
    return {
        "quarterly_cashflow": _stmt(cf, dates),
        "quarterly_income_stmt": _stmt(inc, dates),
        "quarterly_balance_sheet": _stmt(bs, dates),
    }


# ---------------------------------------------------------------- sample companies
def test_select_sample_companies_default_and_slicing(col):
    all_ = col.select_sample_companies()
    assert len(all_) == 60 and all_[0] == "MSFT" and all_[-1] == "BIIB"
    assert len(set(all_)) == len(all_)
    assert col.select_sample_companies(64)[-1] == "GILD"
    assert col.select_sample_companies(3) == ["MSFT", "NVDA", "CRM"]
    assert col.select_sample_companies(0) == []
    assert len(col.select_sample_companies(1000)) == 64


def test_init_state(col):
    assert col.quarterly_data == {} and col.collection_start is not None


# ---------------------------------------------------------------- fetch
def test_fetch_builds_sorted_frame_with_all_fields(qdc, col):
    FakeTicker.spec = _spec(6)
    df = col.fetch_quarterly_data("AAA")
    assert list(df.columns) == [
        "ticker",
        "quarter",
        "date",
        "revenue",
        "operating_income",
        "net_income",
        "ocf",
        "capex",
        "total_assets",
        "total_debt",
        "total_equity",
    ]
    assert len(df) == 6
    assert df["date"].is_monotonic_increasing
    assert list(df.index) == list(range(6))
    first, last = df.iloc[0], df.iloc[-1]
    assert first["ticker"] == "AAA"
    assert str(first["date"].date()) == "2022-03-31"
    # newest-first input: the oldest column carries the largest offset (i = 5)
    assert first["revenue"] == 1005.0 and last["revenue"] == 1000.0
    assert first["capex"] == 15.0  # abs() of the negative capital expenditure
    assert first["ocf"] == 105.0
    assert first["total_equity"] == 2005.0 and first["total_debt"] == 805.0
    assert first["operating_income"] == 205.0 and first["net_income"] == 155.0
    assert first["total_assets"] == 5005.0


@pytest.mark.parametrize(
    "date,label",
    [
        ("2024-01-31", "2024-Q1"),
        ("2024-03-31", "2024-Q1"),
        ("2024-04-30", "2024-Q2"),
        ("2024-06-30", "2024-Q2"),
        ("2024-07-31", "2024-Q3"),
        ("2024-09-30", "2024-Q3"),
        ("2024-10-31", "2024-Q4"),
        ("2024-12-31", "2024-Q4"),
    ],
)
def test_quarter_label_boundaries(qdc, col, date, label):
    spec = _spec(1)
    d = pd.Timestamp(date)
    for k in spec:
        spec[k].columns = pd.DatetimeIndex([d])
    FakeTicker.spec = spec
    assert col.fetch_quarterly_data("X").iloc[0]["quarter"] == label


def test_fetch_caps_at_20_quarters_taking_leading_columns(qdc, col):
    FakeTicker.spec = _spec(22, newest_first=True)
    df = col.fetch_quarterly_data("BIG")
    assert len(df) == 20
    # leading 20 columns of a newest-first frame = the 20 newest quarters
    newest = pd.date_range("2022-03-31", periods=22, freq="QE")[-1]
    assert df["date"].max() == newest
    assert df["date"].min() == pd.date_range("2022-03-31", periods=22, freq="QE")[2]


def test_fetch_exactly_20_quarters_kept(qdc, col):
    FakeTicker.spec = _spec(20)
    assert len(col.fetch_quarterly_data("T")) == 20


def test_fetch_missing_line_items_become_none(qdc, col):
    FakeTicker.spec = _spec(
        5,
        cf=["Operating Cash Flow", "Capital Expenditure"],
        inc=["Operating Income"],
        bs=["Total Debt"],
    )
    # keep at least one row in cashflow so the frame still has columns
    FakeTicker.spec["quarterly_cashflow"] = _stmt(
        {"Other": [1.0] * 5}, FakeTicker.spec["quarterly_cashflow"].columns
    )
    df = col.fetch_quarterly_data("M")
    assert df["ocf"].isna().all() and df["capex"].isna().all()
    assert df["operating_income"].isna().all() and df["total_debt"].isna().all()
    assert df["revenue"].notna().all() and df["net_income"].notna().all()
    assert df["total_assets"].notna().all() and df["total_equity"].notna().all()


def test_fetch_income_statement_with_other_dates_sets_group_to_none(qdc, col):
    spec = _spec(4)
    spec["quarterly_income_stmt"].columns = pd.DatetimeIndex(
        pd.date_range("2000-03-31", periods=4, freq="QE")
    )
    FakeTicker.spec = spec
    df = col.fetch_quarterly_data("D")
    assert len(df) == 4
    assert df[["revenue", "operating_income", "net_income"]].isna().all().all()
    assert df["ocf"].notna().all() and df["total_assets"].notna().all()


def test_fetch_balance_sheet_with_other_dates_sets_group_to_none(qdc, col):
    spec = _spec(4)
    spec["quarterly_balance_sheet"].columns = pd.DatetimeIndex(
        pd.date_range("2000-03-31", periods=4, freq="QE")
    )
    FakeTicker.spec = spec
    df = col.fetch_quarterly_data("D")
    assert df[["total_assets", "total_debt", "total_equity"]].isna().all().all()
    assert df["revenue"].notna().all()


def test_fetch_quarters_come_from_cashflow_columns(qdc, col):
    spec = _spec(4)
    cf = spec["quarterly_cashflow"]
    # quarters come from the cashflow columns; make the income/balance sheet lack one of them
    spec["quarterly_income_stmt"] = spec["quarterly_income_stmt"].iloc[:, :3]
    spec["quarterly_balance_sheet"] = spec["quarterly_balance_sheet"].iloc[:, :3]
    FakeTicker.spec = spec
    df = col.fetch_quarterly_data("P")
    assert len(df) == 4
    assert df["revenue"].isna().sum() == 1 and df["total_assets"].isna().sum() == 1
    assert df["ocf"].notna().all() and len(cf.columns) == 4


@pytest.mark.parametrize(
    "missing", ["quarterly_cashflow", "quarterly_income_stmt", "quarterly_balance_sheet"]
)
def test_fetch_none_statement_returns_empty(qdc, col, missing):
    spec = _spec(4)
    spec[missing] = None
    FakeTicker.spec = spec
    assert col.fetch_quarterly_data("N").empty


@pytest.mark.parametrize(
    "missing", ["quarterly_cashflow", "quarterly_income_stmt", "quarterly_balance_sheet"]
)
def test_fetch_zero_column_statement_returns_empty(qdc, col, missing):
    spec = _spec(4)
    spec[missing] = pd.DataFrame()
    FakeTicker.spec = spec
    assert col.fetch_quarterly_data("N").empty


def test_fetch_constructor_failure_returns_empty(qdc, col):
    FakeTicker.spec = {"ctor_error": "boom"}
    out = col.fetch_quarterly_data("E")
    assert isinstance(out, pd.DataFrame) and out.empty


def test_fetch_statement_access_failure_returns_empty(qdc, col, monkeypatch):
    class Boom:
        def __init__(self, symbol):
            pass

        @property
        def quarterly_cashflow(self):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(qdc.yf, "Ticker", Boom)
    assert col.fetch_quarterly_data("E").empty


# ---------------------------------------------------------------- calculate_metrics
def _qdf(n=8, **over):
    d = {
        "revenue": [100.0] * n,
        "capex": [10.0] * n,
        "total_debt": [50.0] * n,
        "ocf": [30.0] * n,
        "operating_income": [20.0] * n,
        "net_income": [10.0] * n,
        "total_equity": [100.0] * n,
    }
    d.update(over)
    return pd.DataFrame(d)


def test_metrics_empty_and_short_frames(col):
    assert col.calculate_metrics(pd.DataFrame()) == {}
    assert col.calculate_metrics(_qdf(3)) == {}
    assert col.calculate_metrics(_qdf(4)) != {}  # four rows is enough


def test_metrics_flat_series(col):
    m = col.calculate_metrics(_qdf())
    assert m["revenue_cagr"] == pytest.approx(0.0)
    assert m["capex_cagr"] == pytest.approx(0.0)
    assert m["debt_cagr"] == pytest.approx(0.0)
    assert m["avg_ocf"] == 30.0 and m["avg_capex"] == 10.0 and m["avg_fcf"] == 20.0
    assert m["latest_de"] == 0.5
    assert m["oi_margin_trend"] == pytest.approx(0.0)
    assert m["ni_margin_trend"] == pytest.approx(0.0)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="cagr(start=revenue_vals.iloc[-1], end=iloc[0]) on a frame sorted oldest-first inverts growth",
)
def test_metrics_cagr_positive_for_growing_series(col):
    rev = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 200.0]  # oldest -> newest
    m = col.calculate_metrics(_qdf(revenue=rev))
    assert m["revenue_cagr"] > 0


def test_metrics_cagr_is_none_when_start_value_not_positive(col):
    # start value is the last row of the frame
    m = col.calculate_metrics(_qdf(revenue=[100.0] * 7 + [0.0], capex=[10.0] * 7 + [-5.0]))
    assert m["revenue_cagr"] is None and m["capex_cagr"] is None
    assert m["debt_cagr"] == pytest.approx(0.0)


def test_metrics_cagr_keys_absent_with_fewer_than_two_values(col):
    nan = np.nan
    m = col.calculate_metrics(
        _qdf(revenue=[nan] * 7 + [100.0], capex=[nan] * 8, total_debt=[nan] * 6 + [1.0, 2.0])
    )
    assert "revenue_cagr" not in m and "capex_cagr" not in m
    assert "debt_cagr" in m  # exactly two values is enough


def test_metrics_cagr_magnitude_uses_five_year_exponent(col):
    # newest 32 (start), oldest 1 (end): (1/32) ** (1/5) - 1 = -50%
    rev = [1.0] + [5.0] * 6 + [32.0]
    assert col.calculate_metrics(_qdf(revenue=rev))["revenue_cagr"] == pytest.approx(-50.0)


def test_metrics_fcf_and_averages_skip_nans_independently(col):
    nan = np.nan
    ocf = [30.0, 40.0, nan, 50.0] + [30.0] * 4
    capex = [10.0, nan, 5.0, 10.0] + [10.0] * 4
    m = col.calculate_metrics(_qdf(ocf=ocf, capex=capex))
    assert m["avg_ocf"] == pytest.approx(np.nanmean(ocf))
    assert m["avg_capex"] == pytest.approx(np.nanmean(capex))
    fcf = np.array(ocf) - np.array(capex)
    assert m["avg_fcf"] == pytest.approx(np.nanmean(fcf))


@pytest.mark.parametrize(
    "equity,present", [(100.0, True), (0.0, False), (-5.0, False), (np.nan, False)]
)
def test_metrics_latest_de_requires_positive_equity(col, equity, present):
    eq = [100.0] * 7 + [equity]
    debt = [50.0] * 7 + [75.0]
    m = col.calculate_metrics(_qdf(total_equity=eq, total_debt=debt))
    assert ("latest_de" in m) is present
    if present:
        assert m["latest_de"] == 0.75


def test_metrics_margin_trends_oldest_minus_newest(col):
    oi = [10.0, 10, 10, 10, 10, 10, 10, 30.0]  # margin 10% -> 30% on revenue 100
    ni = [5.0, 5, 5, 5, 5, 5, 5, 1.0]
    m = col.calculate_metrics(_qdf(operating_income=oi, net_income=ni))
    assert m["oi_margin_trend"] == pytest.approx(-20.0)
    assert m["ni_margin_trend"] == pytest.approx(4.0)


def test_metrics_margin_needs_two_valid_points(col):
    nan = np.nan
    m = col.calculate_metrics(_qdf(operating_income=[nan] * 7 + [20.0], net_income=[nan] * 8))
    assert "oi_margin_trend" not in m and "ni_margin_trend" not in m


def test_metrics_missing_columns_return_partial_dict(col):
    df = pd.DataFrame({"revenue": [100.0, 110.0, 120.0, 130.0]})
    m = col.calculate_metrics(df)  # KeyError on 'capex' is swallowed
    assert "revenue_cagr" in m and "capex_cagr" not in m and "avg_ocf" not in m


# ---------------------------------------------------------------- collect
def test_collect_data_keeps_only_non_empty(col, monkeypatch, capsys):
    frames = {"A": pd.DataFrame({"x": [1]}), "B": pd.DataFrame(), "C": pd.DataFrame({"x": [2]})}
    monkeypatch.setattr(col, "fetch_quarterly_data", lambda t: frames[t])
    monkeypatch.setattr(col, "calculate_metrics", lambda df: {"n": int(df["x"].iloc[0])})
    out = col.collect_data(["A", "B", "C"])
    assert list(out) == ["A", "C"]
    assert out["C"]["metrics"] == {"n": 2}
    assert out["A"]["quarterly_data"] is frames["A"]
    text = capsys.readouterr().out
    assert "COMPANIES TO ANALYZE: 3" in text
    assert "~60 quarterly records expected" in text
    assert "Collection complete: 2/3 companies" in text
    assert "[ 2/3] Fetching B" in text


def test_collect_data_empty_list(col, capsys):
    assert col.collect_data([]) == {}
    assert "Collection complete: 0/0 companies" in capsys.readouterr().out


# ---------------------------------------------------------------- analyze_trends
def _m(rev=0, capex=0, fcf=0, de=0):
    return {"metrics": {"revenue_cagr": rev, "capex_cagr": capex, "avg_fcf": fcf, "latest_de": de}}


def test_trend_scores_and_buckets(col, capsys):
    collected = {
        "ALL4": _m(11, 16, 1, 0.5),  # 100
        "THREE": _m(11, 16, 1, 1.0),  # 75 (de == 1 does not count)
        "TWO": _m(10.01, 15.01, 0, 0),  # 50
        "ONE": _m(10, 15, 0.1, 0),  # rev/capex at the threshold do not count -> 25
        "ZERO": _m(10, 15, 0, 0),  # 0
        "NOMETRICS": {"metrics": {}},  # skipped
    }
    out = col.analyze_trends(collected)
    assert out is collected
    text = capsys.readouterr().out
    assert "High expansion (≥75): 2 companies" in text
    assert "Medium expansion (50-75): 1 companies" in text
    assert "Low expansion (<50): 2 companies" in text
    assert "NOMETRICS" not in text
    assert "ALL4   - Score: 100/100" in text
    assert "THREE  - Score: 75/100" in text


def test_trend_individual_condition_values(col, capsys):
    collected = {
        "rev": _m(rev=10.01),
        "capex": _m(capex=15.01),
        "fcf": _m(fcf=0.01),
        "de": _m(de=0.99),
        "de_neg": _m(de=-0.5),
        "none": {"metrics": {"revenue_cagr": None, "avg_fcf": None}},
    }
    col.analyze_trends(collected)
    text = capsys.readouterr().out
    assert "Low expansion (<50): 6 companies" in text
    for name in ("rev", "capex", "fcf", "de"):
        assert f"{name:6s} - Score: 25/100" in text
    assert "de_neg - Score: 0/100" in text
    assert "none   - Score: 0/100" in text


def test_trend_top_10_limit(col, capsys):
    collected = {f"T{i:02d}": _m(11) for i in range(12)}
    col.analyze_trends(collected)
    text = capsys.readouterr().out
    assert text.count("- Score: 25/100") == 10


def test_trend_no_scores_prints_no_summary(col, capsys):
    assert col.analyze_trends({}) == {}
    text = capsys.readouterr().out
    assert "EXPANSION SCORE SUMMARY" in text and "High expansion" not in text


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="top-10 list prints `i+1` where i is the pre-sort row label, not the rank",
)
def test_trend_top_list_rank_numbers_are_sequential(col, capsys):
    collected = {"LOW": _m(), "HIGH": _m(11, 16, 1, 0.5)}
    col.analyze_trends(collected)
    lines = [l.strip() for l in capsys.readouterr().out.splitlines() if "- Score:" in l]
    assert lines[0].startswith("1. HIGH")
    assert lines[1].startswith("2. LOW")


def test_fetch_unusable_capex_value_blanks_both_cashflow_fields(qdc, col):
    spec = _spec(4)
    cf = spec["quarterly_cashflow"].astype(object)
    cf.loc["Capital Expenditure"] = "n/a"  # abs() raises TypeError
    spec["quarterly_cashflow"] = cf
    FakeTicker.spec = spec
    df = col.fetch_quarterly_data("C")
    assert len(df) == 4
    assert df["ocf"].isna().all() and df["capex"].isna().all()
    assert df["revenue"].notna().all()
