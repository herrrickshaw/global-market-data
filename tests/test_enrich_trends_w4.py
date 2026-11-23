"""stock_enricher (PE zones, enrichment, HTML), quarterly_data_analyzer, sentiment_price_link helpers."""

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load


@pytest.fixture
def se(monkeypatch, tmp_path):
    mod = load(monkeypatch, "stock_enricher", tmp_path)
    monkeypatch.setattr(mod, "_YF_OK", True)
    monkeypatch.setattr(mod.yf, "Ticker", FakeTicker)
    return mod


@pytest.mark.parametrize(
    "market,sector,key",
    [
        ("IN", "Software Services", "software"),
        ("IN", "Cement & Cement Products", "cement"),
        ("IN", "", "default"),
        ("IN", None, "default"),
        ("US", "Weird", "default"),
        ("US", "Real Estate", "real estate"),
    ],
)
def test_classify_pe_zone_boundaries(se, market, sector, key):
    table = se._IN_SECTOR_PE if market == "IN" else se._US_SECTOR_PE
    buy, fair, caution = table[key]
    z = lambda pe: se._classify_pe(pe, sector, market)  # noqa: E731
    assert z(buy)["zone"] == "BUY ZONE" and z(buy + 0.01)["zone"] == "FAIR VALUE"
    assert z(fair)["zone"] == "FAIR VALUE" and z(fair + 0.01)["zone"] == "CAUTION"
    assert z(caution)["zone"] == "CAUTION" and z(caution + 0.01)["zone"] == "SELL ZONE"
    r = z(buy)
    assert (
        (r["buy"], r["fair"], r["caution"]) == (buy, fair, caution)
        and r["pe_str"] == f"{buy:.1f}x"
        and r["emoji"] == "🟢"
    )
    assert (
        z(caution + 1)["emoji"] == "🔴"
        and z(fair + 0.01)["emoji"] == "🟠"
        and z(buy + 0.01)["emoji"] == "🟡"
    )


@pytest.mark.parametrize("pe", [None, float("nan"), 0, -5])
def test_classify_pe_not_applicable(se, pe):
    assert se._classify_pe(pe, "software") == {
        "zone": "N/A",
        "emoji": "⚪",
        "pe_str": "N/A",
        "buy": None,
        "fair": None,
        "caution": None,
    }


def _info(**over):
    base = {
        "shortName": " Acme Corp ",
        "sector": "Technology",
        "exchange": "NMS",
        "trailingPE": 20.04,
        "forwardPE": 15.0,
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
    }
    base.update(over)
    return base


def test_fetch_one_enriches_and_classifies(se):
    FakeTicker.spec = {"info": _info()}
    r = se._fetch_one("ACME", "", "US")
    assert (
        r["company_name"] == "Acme Corp"
        and r["exchange"] == "NASDAQ"
        and r["sector"] == "Technology"
    )
    assert r["trailing_pe"] == 20.0 and r["forward_pe"] == 15.0
    assert (
        r["pe_label"] == "BUY ZONE"
        and r["pe_zone"] == "🟢 BUY ZONE"
        and r["pe_thresholds"] == "Buy<22 Fair<35 Caution<50"
    )
    assert (
        se._fetch_one("ACME", ".NS", "IN")["exchange"] == "NSE"
        and se._fetch_one("ACME", ".BO", "IN")["exchange"] == "BSE"
    )
    FakeTicker.spec = {"info": _info(exchange="NYS")}
    assert se._fetch_one("ACME", "", "US")["exchange"] == "NYSE"
    FakeTicker.spec = {"info": _info(shortName="X" * 50, trailingPE=None)}
    r = se._fetch_one("ACME", "", "US")
    assert (
        r["company_name"] == "X" * 38 + "…"
        and r["trailing_pe"] is None
        and r["pe_label"] == "BUY ZONE"
    )  # falls back to forward PE
    FakeTicker.spec = {"info": _info(trailingPE=None, forwardPE=None)}
    r = se._fetch_one("ACME", "", "US")
    assert r["pe_label"] == "N/A" and r["pe_thresholds"] == ""


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="stock_enricher._fetch_one tests `'NYS' in exchange`, but yfinance reports NYSE as 'NYQ', "
    "so real NYSE listings keep the NASDAQ default",
)
def test_fetch_one_recognises_yfinance_nyse_code(se):
    FakeTicker.spec = {"info": _info(exchange="NYQ")}
    assert se._fetch_one("KO", "", "US")["exchange"] == "NYSE"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="stock_enricher._classify_pe matches sector keywords by substring in dict order, so 'Biotechnology' "
    "hits the earlier 'technology' key and never reaches its own 'biotechnology' thresholds",
)
def test_classify_pe_biotech_uses_its_own_thresholds(se):
    assert (
        se._classify_pe(30.0, "Biotechnology", "US")["buy"] == se._US_SECTOR_PE["biotechnology"][0]
    )


def test_fetch_one_defaults_without_network_or_data(se, monkeypatch):
    monkeypatch.setattr(se, "_YF_OK", False)
    assert (
        se._fetch_one("A", ".NS")["company_name"] == "A"
        and se._fetch_one("A", "", "US")["exchange"] == "NASDAQ"
    )
    assert se._fetch_one("A", "", "UK")["exchange"] == "NYSE"  # non-US market without a suffix
    monkeypatch.setattr(se, "_YF_OK", True)
    import time

    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    FakeTicker.spec = {"info": {"x": 1}}  # too sparse: retried 3 times with growing back-off
    r = se._fetch_one("A", ".NS", retries=3)
    assert sleeps == [3, 6, 9] and r["company_name"] == "A"
    FakeTicker.spec = {"ctor_error": "429 Too Many"}
    sleeps.clear()
    assert se._fetch_one("A", ".NS", retries=2)["company_name"] == "A" and sleeps == [5, 10]
    FakeTicker.spec = {"ctor_error": "boom"}
    sleeps.clear()
    assert (
        se._fetch_one("A", ".NS")["sector"] == "" and sleeps == []
    )  # non-rate-limit errors stop immediately


def test_enrich_stocks_maps_columns(se, monkeypatch, capsys):
    monkeypatch.setattr(
        se,
        "_fetch_one",
        lambda s, sfx, mkt: (
            {
                "symbol": s,
                "company_name": s.title(),
                "exchange": "BSE" if sfx == ".BO" else "NSE",
                "sector": "Tech",
                "trailing_pe": 10.0,
                "forward_pe": 9.0,
                "pe_zone": "z",
                "pe_label": "l",
            }
            if s != "BAD"
            else (_ for _ in ()).throw(RuntimeError("x"))
        ),
    )
    df = pd.DataFrame({"Symbol": ["AAA", "BBB", "BAD"], "Suffix": [".NS", ".BO", ".NS"]})
    out = se.enrich_stocks(df, workers=2)
    assert list(out["Company_Name"]) == ["Aaa", "Bbb", "BAD"] and list(out["Exchange"]) == [
        "NSE",
        "BSE",
        "NSE",
    ]  # failed symbol -> defaults
    assert (
        out["Trailing_PE"].iloc[0] == 10.0
        and pd.isna(out["Trailing_PE"].iloc[2])
        and list(out["PE_Zone"])[2] == "⚪ N/A"
    )
    assert "df" not in out.columns and "Company_Name" not in df.columns  # input untouched
    assert "3/3 enriched" in capsys.readouterr().out
    seen = []
    monkeypatch.setattr(
        se, "_fetch_one", lambda s, sfx, mkt: seen.append((s, sfx, mkt)) or {"symbol": s}
    )
    se.enrich_stocks(pd.DataFrame({"Symbol": ["X"]}), market="US")
    assert seen == [("X", "", "US")]
    seen.clear()
    se.enrich_stocks(pd.DataFrame({"Symbol": ["Y"]}))
    assert seen == [("Y", ".NS", "IN")]


def test_format_and_exchange_groups(se):
    assert (
        se.format_stock_display({"Company_Name": "acme", "Symbol": "AC", "Exchange": "BSE"})
        == "ACME [AC · BSE]"
    )
    assert se.format_stock_display({"Symbol": "AC"}) == "AC [AC · NSE]"
    df = pd.DataFrame({"Symbol": list("abcde"), "Exchange": ["NSE", "BSE", "NASDAQ", "LSE", "NSE"]})
    g = se.build_exchange_groups(df)
    assert (
        list(g) == ["NSE", "BSE", "NASDAQ", "OTHER"]
        and list(g["NSE"]["Symbol"]) == ["a", "e"]
        and list(g["OTHER"]["Symbol"]) == ["d"]
    )
    assert list(g["NSE"].index) == [0, 1]
    assert list(se.build_exchange_groups(pd.DataFrame({"Symbol": ["a"]}))) == ["ALL"]
    assert list(se.build_exchange_groups(df[df["Exchange"] == "NSE"])) == ["NSE"]


def test_generate_stock_table_html(se):
    assert "No qualifying stocks" in se.generate_stock_table_html(pd.DataFrame())
    df = pd.DataFrame(
        {
            "Symbol": ["AAA", "BBB", "CCC"],
            "Company_Name": ["Acme", "Beta", "Cee"],
            "Exchange": ["NSE", "BSE", "NSE"],
            "LTP": [1234.5, "—", 10.0],
            "Change%": [1.5, -2.0, None],
            "Upside_to_Top%": [5.0, None, 2.0],
            "Trailing_PE": [12.3, None, float("nan")],
            "PE_Zone": ["🟢 BUY ZONE", "🔴 SELL ZONE", "⚪ N/A"],
            "PE_Label": ["BUY ZONE", "SELL ZONE", "N/A"],
            "Screens_Passed": ["Darvas", "Piotroski", "—"],
        }
    )
    html = se.generate_stock_table_html(df)
    assert (
        "<strong>ACME</strong> [AAA · NSE]" in html
        and "₹1,234.50" in html
        and "▲ +1.50%" in html
        and "▼ -2.00%" in html
    )
    assert (
        '<span class="pass">🟢 BUY ZONE</span>' in html
        and '<span class="fail">🔴 SELL ZONE</span>' in html
        and "12.3x" in html
        and "N/A" in html
    )
    assert html.count("<tr>") == 4 and "+5.0%" in html
    assert "$1,234.50" in se.generate_stock_table_html(df, market="US")
    assert se.generate_stock_table_html(df, max_rows=1).count("<tr>") == 2  # header + one row


# ---------------- quarterly_data_analyzer ----------------
@pytest.fixture
def qa(monkeypatch, tmp_path):
    return load(monkeypatch, "quarterly_data_analyzer", tmp_path).QuarterlyAnalyzer()


def _q(date, revenue, **kw):
    return {"date": date, **{"revenue": revenue}, **kw}


def test_quarterly_trends(qa):
    quarters = [
        _q(
            "2024-12-31",
            200.0,
            capex=20.0,
            total_debt=50.0,
            fcf=10.0,
            ocf=30.0,
            oi_margin=0.30,
            ni_margin=0.20,
            roic=0.15,
            de_ratio=0.5,
            fcf_margin=3.0,
            asset_turnover=1.1,
            interest_expense=2.99,
        ),
        _q(
            "2024-09-30",
            150.0,
            capex=15.0,
            total_debt=60.0,
            fcf=8.0,
            ocf=25.0,
            oi_margin=0.25,
            ni_margin=0.18,
            roic=0.12,
        ),
        _q(
            "2024-06-30",
            120.0,
            capex=12.0,
            total_debt=70.0,
            fcf=6.0,
            ocf=20.0,
            oi_margin=0.22,
            ni_margin=0.15,
            roic=0.10,
        ),
        _q(
            "2024-03-31",
            100.0,
            capex=10.0,
            total_debt=80.0,
            fcf=4.0,
            ocf=15.0,
            oi_margin=0.20,
            ni_margin=0.12,
            roic=0.08,
        ),
    ]
    t = qa._calculate_trends(
        list(reversed(quarters))
    )  # input order must not matter: sorted newest first
    assert t["revenue_cagr"] == pytest.approx(((200 / 100) ** (1 / 5) - 1) * 100) and t[
        "capex_cagr"
    ] == pytest.approx(((20 / 10) ** 0.2 - 1) * 100)
    assert t["debt_cagr"] == pytest.approx(((50 / 80) ** 0.2 - 1) * 100)
    assert t["avg_fcf"] == 7.0 and t["avg_ocf"] == 22.5 and t["avg_capex"] == 14.25
    assert (
        t["oi_margin_change"] == pytest.approx(0.10)
        and t["ni_margin_change"] == pytest.approx(0.08)
        and t["roic_change"] == pytest.approx(0.07)
    )
    assert (
        t["latest_de"] == 0.5
        and t["latest_fcf_margin"] == 3.0
        and t["latest_asset_turnover"] == 1.1
        and t["latest_roic"] == 0.15
    )
    assert t["interest_coverage"] == pytest.approx(30.0 / 3.0)  # ocf / (interest + 0.01)


def test_quarterly_trends_sparse_inputs(qa):
    assert qa._calculate_trends([_q("d", 1.0)] * 3) == {}  # fewer than four quarters
    four = [_q(f"2024-0{i}-01", None) for i in range(1, 5)]
    t = qa._calculate_trends(four)
    assert (
        "revenue_cagr" not in t
        and np.isnan(t["avg_fcf"])
        and t["latest_de"] is None
        and "interest_coverage" not in t
    )
    zero_base = [
        _q("2024-04-01", 5.0),
        _q("2024-03-01", 4.0),
        _q("2024-02-01", 3.0),
        _q("2024-01-01", 0.0),
    ]
    # the zero-revenue quarter is dropped from the series, so the CAGR spans 5 -> 3 instead
    assert qa._calculate_trends(zero_base)["revenue_cagr"] == pytest.approx(
        ((5 / 3) ** 0.2 - 1) * 100
    )


@pytest.mark.parametrize(
    "trends,expected",
    [
        ({"debt_cagr": 14.9}, 10),
        ({"debt_cagr": 15.0}, 0),
        ({"debt_cagr": -1.0}, 0),
        ({"capex_cagr": 15.01}, 20),
        ({"capex_cagr": 15.0}, 0),
        ({"revenue_cagr": 10.01}, 15),
        ({"revenue_cagr": 10.0}, 0),
        ({"ni_margin_change": -1.99}, 15),
        ({"ni_margin_change": -2.0}, 0),
        ({"avg_fcf": 0.1}, 15),
        ({"avg_fcf": 0.0}, 0),
        ({"latest_de": 0.99}, 10),
        ({"latest_de": 1.0}, 0),
        ({"latest_fcf_margin": 2.01}, 5),
        ({"latest_fcf_margin": 2.0}, 0),
        ({"roic_change": 0.01}, 5),
        ({"roic_change": 0.0}, 0),
        (
            {
                "debt_cagr": 5,
                "capex_cagr": 20,
                "revenue_cagr": 20,
                "ni_margin_change": 1,
                "avg_fcf": 5,
                "latest_de": 0.5,
                "latest_fcf_margin": 3,
                "roic_change": 1,
            },
            95,
        ),
    ],
)
def test_score_company_thresholds(qa, trends, expected):
    assert qa.score_company({"trends": trends}) == expected


def test_score_company_empty_and_cap(qa):
    assert (
        qa.score_company({}) == 0
        and qa.score_company({"trends": {}}) == 0
        and qa.score_company(None) == 0
    )


# ---------------- sentiment_price_link ----------------
@pytest.fixture
def spl(monkeypatch, tmp_path):
    return load(monkeypatch, "sentiment_price_link", tmp_path)


def _shock_frame(mod, up=True, vol_mult=10.0, at=None):
    n = mod.TRAIL_WIN + 70 + 40
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.005, n))
    vol = np.full(n, 1000.0)
    i = at or mod.TRAIL_WIN + 10
    close[i:] *= 1.2 if up else 0.8  # a 20% gap on day i
    vol[i] = 1000.0 * vol_mult
    return (
        pd.DataFrame({"Close": close, "Volume": vol}, index=pd.date_range("2022-01-01", periods=n)),
        i,
    )


def test_detect_shocks_direction_and_forward_returns(spl):
    df, i = _shock_frame(spl, up=True)
    ev = spl.detect_shocks_and_returns("AAA", df)
    pos = [e for e in ev if e["direction"] == "POS"]
    assert len(pos) == 1 and pos[0]["symbol"] == "AAA" and pos[0]["z"] >= spl.SHOCK_SIGMA
    c = df["Close"].to_numpy()
    for label, h in spl.HORIZONS.items():
        assert pos[0][f"fwd_{label}"] == pytest.approx((c[i + h] - c[i]) / c[i] * 100)
    down, _ = _shock_frame(spl, up=False)
    assert [e["direction"] for e in spl.detect_shocks_and_returns("B", down)] == ["NEG"]


def test_detect_shocks_needs_volume_and_history(spl):
    df, _ = _shock_frame(spl, vol_mult=1.0)
    assert spl.detect_shocks_and_returns("A", df) == []  # price move without a volume spike
    df, _ = _shock_frame(spl)
    assert spl.detect_shocks_and_returns("A", df.iloc[: spl.TRAIL_WIN + 69]) == []  # too short
    assert spl.detect_shocks_and_returns("A", None) == []
    flat = pd.DataFrame({"Close": np.full(300, 10.0), "Volume": 100.0})
    assert spl.detect_shocks_and_returns("A", flat) == []  # zero sigma is skipped
    late, _ = _shock_frame(spl, at=spl.TRAIL_WIN + 10)
    late2 = late.copy()
    assert all(
        "fwd_" + k in e for e in spl.detect_shocks_and_returns("A", late2) for k in spl.HORIZONS
    )


def test_analyse_linkage_groups_and_thresholds(spl):
    rng = np.random.default_rng(1)
    first = list(spl.HORIZONS)[0]
    rows = []
    for i in range(30):
        rows.append({"direction": "POS", **{f"fwd_{k}": 1.0 + i * 0.1 for k in spl.HORIZONS}})
    for i in range(19):  # below the 20-observation floor
        rows.append({"direction": "NEG", **{f"fwd_{k}": -1.0 for k in spl.HORIZONS}})
    rows.append(
        {"direction": "POS", **{f"fwd_{k}": 500.0 for k in spl.HORIZONS}}
    )  # outlier is winsorised in the mean only
    out = spl.analyse_linkage(pd.DataFrame(rows))
    assert set(out["Implied_Sentiment"]) == {"POSITIVE news-shock"} and list(
        out["Horizon"]
    ) == list(spl.HORIZONS)
    r = out[out["Horizon"] == first].iloc[0]
    vals = pd.Series([1.0 + i * 0.1 for i in range(30)] + [500.0])
    assert (
        r["N_events"] == 31 and r["Median%"] == round(vals.median(), 3) and r["Hit_rate%"] == 100.0
    )
    lo, hi = vals.quantile(0.01), vals.quantile(0.99)
    assert r["Mean_wins%"] == round(vals.clip(lo, hi).mean(), 3) and r["Mean_wins%"] < vals.mean()
    rows.extend(
        {"direction": "NEG", **{f"fwd_{k}": -1.0 for k in spl.HORIZONS}} for _ in range(1)
    )  # 20th NEG observation
    both = spl.analyse_linkage(pd.DataFrame(rows))
    neg = both[both["Implied_Sentiment"] == "NEGATIVE news-shock"]
    assert (
        len(neg) == len(spl.HORIZONS)
        and (neg["Hit_rate%"] == 0.0).all()
        and (neg["N_events"] == 20).all()
    )
    assert spl.analyse_linkage(
        pd.DataFrame({"direction": ["POS"], **{f"fwd_{k}": [np.nan] for k in spl.HORIZONS}})
    ).empty


def test_fetch_one_name_truncation_boundary(se):
    FakeTicker.spec = {"info": _info(shortName="N" * 40)}
    assert se._fetch_one("A", "", "US")["company_name"] == "N" * 40  # exactly 40 chars untouched
    FakeTicker.spec = {"info": _info(shortName="N" * 41)}
    assert se._fetch_one("A", "", "US")["company_name"] == "N" * 38 + "…"
