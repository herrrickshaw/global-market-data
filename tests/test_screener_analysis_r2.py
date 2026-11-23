"""screener_analysis: pre-filter scraping, six screeners, enrichment, Excel export, orchestration."""

import sys
import types

import numpy as np
import pandas as pd
import pytest
import requests

from tests.scan_loader_w4 import FakeTicker, load
from tests.w4_helpers import fake_module


@pytest.fixture
def sa(monkeypatch, tmp_path):
    mod = load(monkeypatch, "screener_analysis", tmp_path)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    FakeTicker.spec = {}
    return mod


def _stmt(rows, n=5, drop=()):
    return pd.DataFrame(
        {f"y{i}": [v[i] for k, v in rows.items() if k not in drop] for i in range(n)},
        index=[k for k in rows if k not in drop],
    )


def _spec(drop=(), info=None, quarterly=True):
    inc = _stmt(
        {
            "Net Income": [120e6, 100e6, 80e6, 60e6, 40e6],
            "Total Revenue": [1000e6, 900e6, 800e6, 700e6, 600e6],
            "Gross Profit": [500e6, 420e6, 380e6, 330e6, 280e6],
            "EBIT": [300e6, 250e6, 200e6, 150e6, 100e6],
        },
        drop=drop,
    )
    bal = _stmt(
        {
            "Total Assets": [1000e6, 950e6, 900e6, 850e6, 800e6],
            "Long Term Debt": [100e6, 150e6, 200e6, 250e6, 300e6],
            "Current Assets": [600e6, 500e6, 450e6, 400e6, 350e6],
            "Total Current Assets": [600e6, 500e6, 450e6, 400e6, 350e6],
            "Current Liabilities": [200e6, 220e6, 230e6, 240e6, 250e6],
            "Share Issued": [100e6, 100e6, 100e6, 100e6, 100e6],
            "Stockholders Equity": [600e6, 550e6, 500e6, 450e6, 400e6],
        },
        drop=drop,
    )
    cf = _stmt(
        {
            "Operating Cash Flow": [200e6, 180e6, 160e6, 140e6, 120e6],
            "Total Cash From Operating Activities": [200e6, 180e6, 160e6, 140e6, 120e6],
        },
        drop=drop,
    )
    spec = {
        "income_stmt": inc,
        "balance_sheet": bal,
        "cashflow": cf,
        "info": {"shortName": "Acme", "marketCap": 2e9, "totalDebt": 150e6, "totalCash": 200e6},
    }
    spec["info"]["bookValue"] = 10.0
    spec["info"].update(info or {})
    if quarterly:
        spec["quarterly_income_stmt"] = _stmt(
            {
                "Total Revenue": [230e6, 100e6, 100e6, 100e6, 100e6],
                "Net Income": [30e6, 10e6, 10e6, 10e6, 10e6],
            }
        )
    return spec


# ---- fetch_screener_in_symbols --------------------------------------------------------------


class _Resp:
    def __init__(self, content=b"x", status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self
            raise err


def _markitdown(monkeypatch, pages):
    """pages: list of markdown strings returned per convert() call."""
    it = iter(pages)

    class MD:
        def convert(self, stream, file_extension):
            assert file_extension == ".html"
            return types.SimpleNamespace(text_content=next(it))

    fake_module(monkeypatch, "markitdown", MarkItDown=MD)


def test_fetch_screener_in_without_markitdown(sa, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "markitdown", None)
    assert sa.fetch_screener_in_symbols(1, "slug") == []
    assert "markitdown not installed" in capsys.readouterr().out


def test_fetch_screener_in_extracts_hrefs_and_table_cells(sa, monkeypatch):
    page = (
        "[Foo](/company/FOO/) text /company/BAR-X/ and /company/a/\n"
        "| NAME | CMP |\n| **TCS** | 100 |\n| lower | 1 |\n| SYMBOL | x |\n| A | 1 |\n"
        "| ABCDEFGHIJKLMNOPQRSTU | 2 |\nno pipes ZZZ\n|  | |\n"
    )
    _markitdown(monkeypatch, [page, ""])
    urls = []
    monkeypatch.setattr(
        sa.requests, "get", lambda url, timeout, headers: urls.append((url, timeout)) or _Resp()
    )
    out = sa.fetch_screener_in_symbols(59, "magic-formula")
    # hrefs first (FOO, BAR-X), then qualifying table cells; header words/short/long/lower skipped
    assert out == ["FOO", "BAR-X", "TCS"]
    assert urls[0] == ("https://www.screener.in/screens/59/magic-formula/?page=1", 20)
    # page 2 yielded nothing new -> loop stops after it
    assert len(urls) == 2


def test_fetch_screener_in_stops_at_max_pages(sa, monkeypatch):
    _markitdown(monkeypatch, [f"/company/S{i}A/" for i in range(10)])
    calls = []
    monkeypatch.setattr(
        sa.requests, "get", lambda url, timeout, headers: calls.append(url) or _Resp()
    )
    out = sa.fetch_screener_in_symbols(1, "s", max_pages=3)
    assert len(calls) == 3 and out == ["S0A", "S1A", "S2A"]


def test_fetch_screener_in_404_http_error_and_generic_error(sa, monkeypatch, capsys):
    _markitdown(monkeypatch, ["/company/AAA/"])
    seq = iter([_Resp(), _Resp(status=404)])
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: next(seq))
    assert sa.fetch_screener_in_symbols(1, "s") == ["AAA"]
    assert "HTTP" not in capsys.readouterr().out
    # non-404 HTTP error is reported and stops
    _markitdown(monkeypatch, [])
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _Resp(status=500))
    assert sa.fetch_screener_in_symbols(1, "s") == []
    assert "HTTP 500 on page 1 — stopping." in capsys.readouterr().out

    def boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(sa.requests, "get", boom)
    assert sa.fetch_screener_in_symbols(1, "s") == []
    assert "Error on page 1: net down" in capsys.readouterr().out


# ---- fetch_all_nse_symbols ------------------------------------------------------------------


def test_nse_symbols_primary_source_threshold(sa, monkeypatch):
    monkeypatch.setattr(sa, "_USE_NSE_FETCHER", True)
    monkeypatch.setattr(sa, "_get_nse_syms", lambda: ["A"] * 101, raising=False)
    assert len(sa.fetch_all_nse_symbols()) == 101
    # exactly 100 is not enough -> falls through (no nse lib => empty + warning)
    monkeypatch.setattr(sa, "_get_nse_syms", lambda: ["A"] * 100, raising=False)
    monkeypatch.setitem(sys.modules, "nse", None)
    assert sa.fetch_all_nse_symbols() == []
    monkeypatch.setattr(sa, "_get_nse_syms", lambda: 1 / 0, raising=False)
    assert sa.fetch_all_nse_symbols() == []


def _fake_nse(monkeypatch, tmp_path, behaviour):
    class NSE:
        def __init__(self, download_folder, server):
            self.folder, self.server = download_folder, server

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def equityBhavcopy(self, d):
            return behaviour(d)

    fake_module(monkeypatch, "nse", NSE=NSE)


def test_nse_symbols_bhavcopy_fallback_sorted_and_filtered(sa, monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_USE_NSE_FETCHER", False)
    f = tmp_path / "bhav.csv"
    pd.DataFrame(
        {"SctySrs": ["EQ", "BE", "EQ", "EQ"], "TckrSymb": [" ZED ", "SKIP", "ABC", None]}
    ).to_csv(f, index=False)
    calls = []

    def beh(d):
        calls.append(d)
        if len(calls) < 3:
            raise RuntimeError("holiday")
        return f

    _fake_nse(monkeypatch, tmp_path, beh)
    assert sa.fetch_all_nse_symbols() == ["ABC", "ZED"]
    assert len(calls) == 3  # first two failing days skipped, third used


def test_nse_symbols_bhavcopy_no_usable_file_returns_empty(sa, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sa, "_USE_NSE_FETCHER", False)
    bad = tmp_path / "nosrs.csv"
    pd.DataFrame({"x": [1]}).to_csv(bad, index=False)
    seen = []
    _fake_nse(
        monkeypatch,
        tmp_path,
        lambda d: seen.append(d)
        or (bad if len(seen) % 2 else types.SimpleNamespace(exists=lambda: False)),
    )
    assert sa.fetch_all_nse_symbols() == []
    assert len(seen) == 7
    assert "NSE symbol fetch failed" in capsys.readouterr().out


# ---- bulk_download_ohlc ---------------------------------------------------------------------


def _multi(tickers, n=12):
    cols = pd.MultiIndex.from_product([["Close", "High"], tickers])
    df = pd.DataFrame(np.arange(n * len(cols), dtype=float).reshape(n, len(cols)), columns=cols)
    return df


def test_bulk_download_multiindex_min_rows_and_missing_ticker(sa, monkeypatch, capsys):
    raw = _multi(["A.NS", "B.NS"])
    raw.loc[:, ("Close", "B.NS")] = np.nan
    raw.loc[:, ("High", "B.NS")] = np.nan
    short = _multi(["C.NS"], n=12)
    short.iloc[10:] = np.nan  # only 10 non-NaN rows -> exactly the minimum
    raw = pd.concat([raw, short], axis=1)
    got = {}
    monkeypatch.setattr(sa.yf, "download", lambda batch, **k: got.update(batch=batch, kw=k) or raw)
    out = sa.bulk_download_ohlc(["A.NS", "B.NS", "C.NS", "D.NS"], period="6mo")
    assert set(out) == {"A.NS", "C.NS"}  # B all-NaN dropped; D missing (KeyError) skipped
    assert len(out["C.NS"]) == 10
    assert got["kw"] == {
        "period": "6mo",
        "auto_adjust": True,
        "threads": True,
        "progress": False,
    }
    assert "OK (2 usable)" in capsys.readouterr().out


def test_bulk_download_nine_rows_rejected(sa, monkeypatch):
    raw = _multi(["A.NS"], n=12)
    raw.iloc[9:] = np.nan
    monkeypatch.setattr(sa.yf, "download", lambda *a, **k: raw)
    assert sa.bulk_download_ohlc(["A.NS"]) == {}


def test_bulk_download_batches_empty_flat_and_error(sa, monkeypatch, capsys):
    monkeypatch.setattr(sa, "BATCH_SIZE", 2)
    flat = pd.DataFrame({"Close": [1.0, 2.0]})
    responses = iter([pd.DataFrame(), flat, RuntimeError("rate limited")])

    def dl(batch, **k):
        r = next(responses)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(sa.yf, "download", dl)
    out = sa.bulk_download_ohlc(["A", "B", "C", "D", "E"])
    assert list(out) == ["C"]  # flat frame is assigned to the batch's first ticker
    text = capsys.readouterr().out
    assert "OHLC batch 1/3 (2 tickers)" in text and "empty" in text
    assert "ERROR — rate limited" in text


# ---- compute_magic_formula ------------------------------------------------------------------


def _mf(sa, spec, **kw):
    FakeTicker.spec = spec
    return sa.compute_magic_formula("AAA", **kw)


def test_magic_formula_qualifies_with_exact_values(sa):
    r = _mf(sa, _spec())
    assert r["qualifies"] is True and r["name"] == "Acme" and r["screen"] == "mf"
    assert r["ROIC_%"] == 37.5  # 300 / (1000 - 200)
    assert r["Earnings_Yield_%"] == pytest.approx(15.38, abs=0.01)  # 300 / (2000 + 150 - 200)
    assert r["Book_Value"] == 10.0 and r["MCap_Cr"] == 200.0 and r["error"] == ""


def test_magic_formula_no_data_and_ctor_error(sa):
    assert _mf(sa, {"info": {}})["error"] == "no_data"
    r = _mf(sa, {"ctor_error": "kaput"})
    assert r["error"] == "kaput" and r["qualifies"] is False


def test_magic_formula_info_failure_tolerated(sa):
    spec = _spec()
    spec["info"] = Exception  # FakeTicker raises on .info
    r = _mf(sa, spec)
    assert r["qualifies"] is False and r["Earnings_Yield_%"] is None and r["MCap_Cr"] is None
    assert r["ROIC_%"] == 37.5


@pytest.mark.parametrize(
    "drop,info,ok",
    [
        ((), {}, True),
        ((), {"bookValue": 0}, False),
        ((), {"bookValue": None}, False),
        ((), {"marketCap": 1.5e8, "totalDebt": 0, "totalCash": 0}, False),  # mcap exactly 15 Cr
        (
            (),
            {"marketCap": 3e9, "totalDebt": 0, "totalCash": 0},
            False,
        ),  # EY = 10%
        ((), {"marketCap": None}, False),
        ((), {"marketCap": 1.9e9, "totalDebt": None, "totalCash": None}, True),
    ],
)
def test_magic_formula_gate_matrix(sa, drop, info, ok):
    spec = _spec(drop=drop, info=info)
    assert _mf(sa, spec)["qualifies"] is ok


def test_magic_formula_roic_threshold_is_strict(sa):
    spec = _spec()
    spec["income_stmt"].loc["EBIT", "y0"] = 200e6  # CE 800e6 -> exactly 25.0
    r = _mf(sa, spec)
    assert r["ROIC_%"] == 25.0 and r["qualifies"] is False
    spec["income_stmt"].loc["EBIT", "y0"] = 200.1e6
    assert _mf(sa, spec)["ROIC_%"] > 25


def test_magic_formula_earnings_yield_threshold_is_strict(sa):
    spec = _spec(info={"marketCap": 2e9, "totalDebt": 0, "totalCash": 0})  # EV 2e9
    spec["income_stmt"].loc["EBIT", "y0"] = 300e6  # EY exactly 15.0
    spec["balance_sheet"].loc["Total Assets", "y0"] = 1000e6
    r = _mf(sa, spec)
    assert r["Earnings_Yield_%"] == 15.0 and r["qualifies"] is False


def test_magic_formula_missing_balance_sheet(sa):
    spec = _spec()
    spec["balance_sheet"] = None
    r = _mf(sa, spec)
    assert r["ROIC_%"] is None and r["qualifies"] is False


def test_magic_formula_nonpositive_capital_employed(sa):
    spec = _spec()
    spec["balance_sheet"].loc["Current Liabilities", "y0"] = 1000e6  # CE == 0
    assert _mf(sa, spec)["ROIC_%"] is None


# ---- compute_golden_crossover ---------------------------------------------------------------


def _cross_series():
    down = np.linspace(200, 100, 260)
    up = np.linspace(100, 400, 120)[1:]
    closes = pd.Series(np.concatenate([down, up]))
    d50, d200 = closes.rolling(50).mean(), closes.rolling(200).mean()
    for k in range(201, len(closes)):
        if d50.iloc[k - 1] < d200.iloc[k - 1] and d50.iloc[k] > d200.iloc[k]:
            return closes, k
    raise AssertionError("no crossing constructed")


def _ohlc(closes):
    return pd.DataFrame({"Close": list(closes)})


def test_golden_cross_strict_on_the_day(sa):
    closes, k = _cross_series()
    r = sa.compute_golden_crossover(_ohlc(closes.iloc[: k + 1]), "XYZ")
    assert r["qualifies"] is True and r["dma50_above_200"] is True
    assert r["recent_crossover"] is True  # prior bars had 50 < 200
    assert r["symbol"] == "XYZ" and r["screen"] == "gc" and r["error"] == ""
    sub = closes.iloc[: k + 1]
    assert r["DMA50"] == round(sub.rolling(50).mean().iloc[-1], 2)
    assert r["DMA200"] == round(sub.rolling(200).mean().iloc[-1], 2)
    assert r["LTP"] == round(sub.iloc[-1], 2)
    gap = (r["DMA50"] - r["DMA200"]) / r["DMA200"] * 100
    assert r["DMA_gap_%"] == pytest.approx(gap, abs=0.05) and r["DMA_gap_%"] > 0


def test_golden_cross_day_after_is_recent_but_not_strict(sa):
    closes, k = _cross_series()
    r = sa.compute_golden_crossover(_ohlc(closes.iloc[: k + 3]), "X")
    assert r["qualifies"] is False and r["recent_crossover"] is True
    assert r["dma50_above_200"] is True


def test_golden_cross_far_after_not_recent(sa):
    closes, k = _cross_series()
    r = sa.compute_golden_crossover(_ohlc(closes.iloc[: k + 12]), "X")
    assert r["recent_crossover"] is False and r["dma50_above_200"] is True


def test_golden_cross_downtrend_and_bar_count_boundary(sa):
    r = sa.compute_golden_crossover(_ohlc(np.linspace(200, 100, 201)), "D")
    assert r["qualifies"] is False and r["dma50_above_200"] is False and r["error"] == ""
    r = sa.compute_golden_crossover(_ohlc(np.linspace(200, 100, 200)), "D")
    assert r["error"] == "need 201 bars, got 200" and r["qualifies"] is False


def test_golden_cross_none_empty_and_nonnumeric(sa):
    assert sa.compute_golden_crossover(None)["error"] == "no_data"
    assert sa.compute_golden_crossover(pd.DataFrame())["error"] == "no_data"
    df = _ohlc(["x"] * 300)
    assert sa.compute_golden_crossover(df, "S")["error"] == "need 201 bars, got 0"


# ---- compute_bull_cartel --------------------------------------------------------------------


def _bc(sa, q=None, info=None, ctor_error=None):
    FakeTicker.spec = {"quarterly_income_stmt": q, "info": info or {"shortName": "Q Co"}}
    if ctor_error:
        FakeTicker.spec["ctor_error"] = ctor_error
    return sa.compute_bull_cartel("QQQ")


def _q(rev0, rev4, ni0, ni4, n=5):
    rev = [rev0] + [100e6] * (n - 2) + [rev4]
    ni = [ni0] + [1e6] * (n - 2) + [ni4]
    return _stmt({"Total Revenue": rev, "Net Income": ni}, n=n)


def test_bull_cartel_qualifies_with_values(sa):
    r = _bc(sa, _q(230e6, 100e6, 30e6, 10e6))
    assert r["qualifies"] is True and r["name"] == "Q Co" and r["screen"] == "bc"
    assert r["Sales_Growth_YoY_%"] == 130.0 and r["Profit_Growth_YoY_%"] == 200.0
    assert r["Net_Profit_Cr"] == 3.0 and r["error"] == ""


def test_bull_cartel_requires_five_quarters_and_data(sa):
    assert _bc(sa, _q(2, 1, 1, 1, n=4))["error"] == "need ≥5 quarters of data"
    assert _bc(sa, None)["error"] == "need ≥5 quarters of data"
    r = _bc(sa, None, ctor_error="dead")
    assert r["error"] == "dead" and r["qualifies"] is False


@pytest.mark.parametrize(
    "rev0,ni0,ok",
    [
        (115e6, 13e6, False),  # sales growth exactly 15 -> strict
        (115.1e6, 13e6, True),
        (200e6, 12e6, False),  # profit growth exactly 20 -> strict
        (200e6, 12.1e6, True),
    ],
)
def test_bull_cartel_threshold_boundaries(sa, rev0, ni0, ok):
    # rev4 = 100e6, ni4 = 10e6 ; second row pair keeps net profit above 1 Cr
    assert _bc(sa, _q(rev0, 100e6, ni0, 10e6))["qualifies"] is ok


def test_bull_cartel_net_profit_floor_is_strict(sa):
    r = _bc(sa, _q(300e6, 100e6, 1e7, 1e6))
    assert r["Net_Profit_Cr"] == 1.0 and r["qualifies"] is False
    r = _bc(sa, _q(300e6, 100e6, 1.01e7, 1e6))
    assert r["qualifies"] is True


def test_bull_cartel_negative_base_uses_absolute_denominator(sa):
    r = _bc(sa, _q(300e6, 100e6, 30e6, -10e6))
    assert r["Profit_Growth_YoY_%"] == 400.0  # (30 - -10) / |-10|


def test_bull_cartel_zero_base_gives_none_growth(sa):
    r = _bc(sa, _q(300e6, 0.0, 30e6, 0.0))
    assert r["Sales_Growth_YoY_%"] is None and r["Profit_Growth_YoY_%"] is None
    assert r["qualifies"] is False


def test_bull_cartel_info_failure_tolerated(sa):
    FakeTicker.spec = {"quarterly_income_stmt": _q(230e6, 100e6, 30e6, 10e6), "info": Exception}
    r = sa.compute_bull_cartel("A")
    assert r["name"] == "" and r["qualifies"] is True


# ---- compute_darvas_box ---------------------------------------------------------------------


def _darvas_df(last_close, n=20, lows_override=None):
    highs = [100.0] * n
    highs[10] = 110.0
    lows = [90.0] * n
    lows[12] = 85.0
    closes = [95.0] * (n - 1) + [last_close]
    if lows_override is not None:
        lows = lows_override
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


def test_darvas_breakout_in_box_and_breakdown(sa):
    r = sa.compute_darvas_box(_darvas_df(120.0), "S")
    assert r["signal"] == "BREAKOUT_BUY" and r["qualifies"] is True
    assert (r["Box_Top"], r["Box_Bottom"], r["LTP"]) == (110.0, 85.0, 120.0)
    assert r["Upside_to_Top_%"] == round((110 - 120) / 120 * 100, 2)
    assert r["Position_in_Box_%"] == round((120 - 85) / 25 * 100, 1)
    r = sa.compute_darvas_box(_darvas_df(100.0), "S")
    assert r["signal"] == "IN_BOX" and r["qualifies"] is False
    assert r["Position_in_Box_%"] == 60.0 and r["Upside_to_Top_%"] == 10.0
    r = sa.compute_darvas_box(_darvas_df(80.0), "S")
    assert r["signal"] == "BREAKDOWN_SELL" and r["qualifies"] is False


def test_darvas_breakout_requires_strictly_above_top_and_below_bottom(sa):
    assert sa.compute_darvas_box(_darvas_df(110.0), "S")["signal"] == "IN_BOX"
    assert sa.compute_darvas_box(_darvas_df(85.0), "S")["signal"] == "IN_BOX"


def test_darvas_current_bar_is_excluded_from_box(sa):
    df = _darvas_df(100.0)
    df.loc[19, "High"] = 500.0  # huge current-bar high must not become the box top
    assert sa.compute_darvas_box(df, "S")["Box_Top"] == 110.0


def test_darvas_insufficient_data_and_missing_columns(sa):
    short = _darvas_df(100.0).iloc[:7]  # confirm + 5 = 8 required
    assert sa.compute_darvas_box(short, "S") == {
        "symbol": "S",
        "screen": "darvas",
        "signal": "INSUFFICIENT_DATA",
    }
    assert sa.compute_darvas_box(_darvas_df(100.0).iloc[:8], "S")["signal"] != "INSUFFICIENT_DATA"
    nohigh = _darvas_df(100.0).drop(columns=["High"])
    assert sa.compute_darvas_box(nohigh, "S")["signal"] == "INSUFFICIENT_DATA"


def test_darvas_no_box_when_highs_never_confirm(sa):
    df = pd.DataFrame(
        {"High": np.linspace(100, 120, 20), "Low": [90.0] * 20, "Close": [100.0] * 20}
    )
    r = sa.compute_darvas_box(df, "S")
    assert r["signal"] == "NO_BOX" and r["current_price"] == 100.0 and "box_top" not in r


def test_darvas_bottom_falls_back_to_min_low_when_unconfirmed(sa):
    lows = list(np.linspace(100, 80, 20))  # strictly falling -> no confirmed low
    r = sa.compute_darvas_box(_darvas_df(95.0, lows_override=lows), "S")
    assert r["Box_Bottom"] == round(min(lows[10:19]), 2)


def test_darvas_zero_lows_give_no_box_with_top(sa):
    df = _darvas_df(95.0, lows_override=[0.0] * 20)
    r = sa.compute_darvas_box(df, "S")
    assert r == {
        "symbol": "S",
        "screen": "darvas",
        "signal": "NO_BOX",
        "box_top": 110.0,
        "current_price": 95.0,
    }


def test_darvas_column_matching_is_case_insensitive_substring(sa):
    df = _darvas_df(120.0).rename(columns={"High": "HIGH", "Low": "low", "Close": "Adj Close"})
    assert sa.compute_darvas_box(df, "S")["signal"] == "BREAKOUT_BUY"


def test_darvas_zero_highs_skipped_and_confirm_param(sa):
    df = _darvas_df(120.0)
    assert sa.compute_darvas_box(df, "S", confirm=3)["Box_Top"] == 110.0
    # with confirm=5 the 4 highs after index 10 cannot both exist before the end
    r = sa.compute_darvas_box(df, "S", confirm=5)
    assert r["Box_Top"] == 110.0  # 5 following equal highs (100) still < 110


# ---- compute_piotroski ----------------------------------------------------------------------


def _pio(sa, drop=(), **kw):
    FakeTicker.spec = _spec(drop=drop)
    return sa.compute_piotroski("AAA", **kw)


def test_piotroski_perfect_score(sa):
    r = _pio(sa)
    assert r["f_score"] == 9 == r["Piotroski_Score"]
    assert r["qualifies"] is True and r["interpretation"] == "STRONG" and r["error"] == ""


@pytest.mark.parametrize(
    "drop,score,qual,label",
    [
        (("Total Revenue",), 7, True, "STRONG"),
        (("Total Revenue", "Long Term Debt"), 6, False, "MODERATE"),
        (
            (
                "Total Revenue",
                "Long Term Debt",
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
            ),
            4,
            False,
            "MODERATE",
        ),
        (
            (
                "Total Revenue",
                "Long Term Debt",
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
                "Current Assets",
                "Total Current Assets",
            ),
            3,
            False,
            "WEAK",
        ),
    ],
)
def test_piotroski_score_bands(sa, drop, score, qual, label):
    r = _pio(sa, drop=drop)
    assert (r["f_score"], r["qualifies"], r["interpretation"]) == (score, qual, label)


def test_piotroski_share_dilution_costs_a_point(sa):
    spec = _spec()
    spec["balance_sheet"].loc["Share Issued", "y0"] = 101e6
    FakeTicker.spec = spec
    assert sa.compute_piotroski("A")["f_score"] == 8
    spec["balance_sheet"].loc["Share Issued", "y0"] = 100e6  # equal -> no dilution
    assert sa.compute_piotroski("A")["f_score"] == 9


def test_piotroski_no_data_and_error(sa):
    FakeTicker.spec = {}
    r = sa.compute_piotroski("A")
    assert r == {
        "symbol": "A",
        "screen": "piotroski",
        "qualifies": False,
        "f_score": None,
        "error": "no_data",
    }
    FakeTicker.spec = {"ctor_error": "x"}
    r = sa.compute_piotroski("A")
    assert r["error"] == "x" and r["f_score"] is None


# ---- compute_coffee_can ---------------------------------------------------------------------


def _cc(sa, spec):
    FakeTicker.spec = spec
    return sa.compute_coffee_can("AAA")


def _cc_spec(**info):
    spec = _spec(info={"marketCap": 5e10, "debtToEquity": 50, **info})
    return spec


def test_coffee_can_all_pass(sa):
    r = _cc(sa, _cc_spec())
    assert r["qualifies"] is True and r["CC_Score"] == "5/5" and r["screen"] == "cc"
    assert r["CC_Criteria"] == {"C1": 1, "C2": 1, "C3": 1, "C4": 1, "C5": 1}


def test_coffee_can_no_data_and_error(sa):
    assert _cc(sa, {})["error"] == "no_data"
    r = _cc(sa, {"ctor_error": "e"})
    assert r["error"] == "e" and r["qualifies"] is False


def test_coffee_can_info_failure_uses_balance_sheet_leverage(sa):
    spec = _cc_spec()
    spec["info"] = Exception
    r = _cc(sa, spec)
    assert r["CC_Criteria"]["C3"] == 1  # 100 / 600 < 1
    assert r["CC_Criteria"]["C4"] == 0  # no market cap


@pytest.mark.parametrize(
    "de,expected",
    [(50, 1), (11, 1), (10, 0), (0.8, 1), (0.99, 1), (1.0, 0), (100, 0), (-5, 1)],
)
def test_coffee_can_debt_to_equity_normalisation(sa, de, expected):
    assert _cc(sa, _cc_spec(debtToEquity=de))["CC_Criteria"]["C3"] == expected


def test_coffee_can_leverage_fallback_edges(sa):
    spec = _cc_spec(debtToEquity=None)
    assert _cc(sa, spec)["CC_Criteria"]["C3"] == 1
    spec["balance_sheet"].loc["Long Term Debt", "y0"] = 600e6  # LTD / equity == 1
    assert _cc(sa, spec)["CC_Criteria"]["C3"] == 0
    spec["balance_sheet"].loc["Stockholders Equity", "y0"] = 0.0
    assert _cc(sa, spec)["CC_Criteria"]["C3"] == 0
    spec["balance_sheet"] = None
    assert _cc(sa, spec)["CC_Criteria"]["C3"] == 0


@pytest.mark.parametrize("mcap,expected", [(5e9, 1), (4.99e9, 0), (None, 0)])
def test_coffee_can_market_cap_floor_inclusive(sa, mcap, expected):
    assert _cc(sa, _cc_spec(marketCap=mcap))["CC_Criteria"]["C4"] == expected


def test_coffee_can_revenue_cagr_boundaries(sa):
    spec = _cc_spec()
    spec["income_stmt"] = _stmt(
        {"Total Revenue": [105.0, 100.0, 0, 0, 0], "Net Income": [1, 1, 1, 1, 1]}, n=2
    )
    assert _cc(sa, spec)["CC_Criteria"]["C1"] == 0
    spec["income_stmt"].loc["Total Revenue", "y0"] = 120.0
    assert _cc(sa, spec)["CC_Criteria"]["C1"] == 1
    spec["income_stmt"].loc["Total Revenue", "y1"] = 0.0
    assert _cc(sa, spec)["CC_Criteria"]["C1"] == 0  # non-positive base
    spec["income_stmt"] = spec["income_stmt"].drop(columns=["y1"])
    assert _cc(sa, spec)["CC_Criteria"]["C1"] == 0  # single point


def test_coffee_can_loss_year_and_roce_fail(sa):
    spec = _cc_spec()
    spec["income_stmt"].loc["Net Income", "y3"] = -1.0
    r = _cc(sa, spec)
    assert r["CC_Criteria"]["C5"] == 0 and r["qualifies"] is False and r["CC_Score"] == "4/5"
    spec = _cc_spec()
    spec["income_stmt"].loc["EBIT"] = 1.0  # ROCE ~0
    assert _cc(sa, spec)["CC_Criteria"]["C2"] == 0


def test_coffee_can_roce_ignores_nonpositive_capital_employed(sa):
    spec = _cc_spec()
    spec["balance_sheet"].loc["Current Liabilities"] = 2000e6  # CE < 0 every year
    assert _cc(sa, spec)["CC_Criteria"]["C2"] == 0


# ---- enrich_symbol --------------------------------------------------------------------------


@pytest.fixture
def stubbed(sa, monkeypatch):
    calls = []
    for name in ("magic_formula", "bull_cartel", "piotroski", "coffee_can"):
        monkeypatch.setattr(
            sa, f"compute_{name}", lambda s, suf, n=name: calls.append(n) or {"id": n}
        )
    monkeypatch.setattr(sa, "compute_darvas_box", lambda df, symbol: {"id": "darvas", "s": symbol})
    monkeypatch.setattr(sa, "compute_golden_crossover", lambda df, symbol: {"id": "gc"})
    return calls


def test_enrich_all_screens(sa, stubbed):
    FakeTicker.spec = _spec()
    out = sa.enrich_symbol("AAA", _ohlc([1.0]), ".NS")
    assert out["symbol"] == "AAA" and out["suffix"] == ".NS"
    assert {k: v["id"] for k, v in out.items() if isinstance(v, dict)} == {
        "darvas": "darvas",
        "gc": "gc",
        "piotroski": "piotroski",
        "cc": "coffee_can",
        "mf": "magic_formula",
        "bc": "bull_cartel",
    }
    assert out["darvas"]["s"] == "AAA"


def test_enrich_ohlc_only_makes_no_fundamental_calls(sa, stubbed, monkeypatch):
    monkeypatch.setattr(sa.yf, "Ticker", lambda s: pytest.fail("no Ticker expected"))
    out = sa.enrich_symbol("AAA", _ohlc([1.0]), active_screens={"darvas", "gc"})
    assert set(out) == {"symbol", "suffix", "darvas", "gc"}
    assert stubbed == []


def test_enrich_missing_ohlc_and_missing_statements(sa, stubbed):
    FakeTicker.spec = {}  # no financial statements at all
    out = sa.enrich_symbol("AAA", None, ".BO")
    assert out == {"symbol": "AAA", "suffix": ".BO"}
    FakeTicker.spec = {"income_stmt": _spec()["income_stmt"]}  # annual only -> no bc
    out = sa.enrich_symbol("AAA", None, active_screens={"bc", "mf"})
    assert "mf" in out and "bc" not in out


def test_enrich_records_fetch_error(sa, stubbed):
    FakeTicker.spec = {"ctor_error": "no net"}
    out = sa.enrich_symbol("AAA", None, active_screens={"mf"})
    assert out["fetch_error"] == "no net" and "mf" not in out


# ---- _flatten_for_sheet / save_results_excel ------------------------------------------------


def test_flatten_for_sheet_filters_and_marks(sa):
    results = [
        {
            "symbol": "A",
            "suffix": ".NS",
            "mf": {
                "qualifies": True,
                "symbol": "A",
                "screen": "mf",
                "ROIC_%": 30,
                "error": "",
                "CC_Criteria": {},
            },
        },
        {"symbol": "B", "mf": {"qualifies": False, "ROIC_%": 5, "error": "no_data"}},
        {"symbol": "C"},
        {"symbol": "D", "mf": {}},
    ]
    df = sa._flatten_for_sheet(results, "mf")
    assert df["Symbol"].tolist() == ["A", "B"]
    assert df["Qualifies"].tolist() == ["✔", "✘"]
    assert df["Suffix"].tolist() == [".NS", ""]
    assert df["ROIC_%"].tolist() == [30, 5]
    assert "Error" in df.columns and df["Error"].isna().tolist() == [True, False]
    assert not ({"symbol", "screen", "error", "CC_Criteria"} & set(df.columns))
    assert sa._flatten_for_sheet([{"symbol": "X"}], "mf").empty


@pytest.fixture
def xl(monkeypatch, sa):
    sheets = {}

    class W:
        def __init__(self, path, engine):
            sheets["_path"], sheets["_engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sa.pd, "ExcelWriter", W)
    monkeypatch.setattr(
        pd.DataFrame,
        "to_excel",
        lambda self, w, sheet_name, index: sheets.update({sheet_name: self}),
    )
    return sheets


def test_save_results_excel_sheets_order_and_sorting(sa, xl):
    results = [
        {"symbol": "LOSE", "suffix": ".NS", "mf": {"qualifies": False, "ROIC_%": 1}},
        {"symbol": "WIN", "suffix": ".NS", "mf": {"qualifies": True, "ROIC_%": 40}},
        {"symbol": "ONLYGC", "suffix": ".NS", "gc": {"qualifies": False}},
    ]
    path = sa.save_results_excel(results, [{"Symbol": "WIN"}], {"mf", "gc", "bc"})
    assert path.parent == sa.DOWNLOAD_DIR and path.name.startswith("screener_analysis_")
    assert xl["_engine"] == "openpyxl"
    names = [k for k in xl if not k.startswith("_")]
    assert names == [
        "DISCLAIMER",
        "Magic_Formula",
        "Golden_Crossover",
        "Multi_Screen_Hits",
        "Screener_Guide",
    ]  # bc active but empty -> skipped; darvas/piotroski/cc not active
    assert sorted(xl["Magic_Formula"]["Symbol"]) == ["LOSE", "WIN"]
    assert len(xl["Screener_Guide"]) == len(sa.SCREENERS) == 3
    assert xl["Screener_Guide"]["Screener"].tolist() == [
        "Magic Formula",
        "Golden Crossover",
        "Bull Cartel",
    ]


def test_save_results_excel_no_hits_sheet_when_empty(sa, xl):
    sa.save_results_excel([], [], {"mf"})
    assert "Multi_Screen_Hits" not in xl and "Magic_Formula" not in xl


# ---- run ------------------------------------------------------------------------------------


@pytest.fixture
def harness(sa, monkeypatch):
    state = types.SimpleNamespace(saved=None, enriched=[], bulk=None, pre=[], nse_calls=0)
    monkeypatch.setattr(sa, "fetch_screener_in_symbols", lambda i, s: state.pre.append(s) or [])
    state.nse = ["N1", "N2"]

    def nse():
        state.nse_calls += 1
        return list(state.nse)

    monkeypatch.setattr(sa, "fetch_all_nse_symbols", nse)

    def bulk(tickers, period):
        state.bulk = (list(tickers), period)
        return {t: _ohlc(np.linspace(1, 2, 250)) for t in tickers}

    monkeypatch.setattr(sa, "bulk_download_ohlc", bulk)
    state.darvas = {}
    state.gc = {}
    monkeypatch.setattr(
        sa, "compute_darvas_box", lambda df, symbol: state.darvas.get(symbol, {"signal": "NO_BOX"})
    )
    monkeypatch.setattr(
        sa,
        "compute_golden_crossover",
        lambda df, symbol: state.gc.get(symbol, {"qualifies": False}),
    )

    def enrich(sym, df, suffix, active):
        state.enriched.append((sym, suffix, frozenset(active)))
        return state.fund.get(sym, {"symbol": sym})

    state.fund = {}
    monkeypatch.setattr(sa, "enrich_symbol", enrich)
    monkeypatch.setattr(
        sa,
        "save_results_excel",
        lambda r, m, a: setattr(state, "saved", (r, m, a)) or sa.DOWNLOAD_DIR / "out.xlsx",
    )
    return state


def test_run_offline_pipeline_and_result_shape(sa, harness, capsys):
    out = sa.run(offline=True, workers=2)
    assert harness.pre == []  # offline never touches screener.in
    assert harness.bulk == (["N1.NS", "N2.NS"], "1y")
    assert [e[0] for e in harness.enriched] == ["N1", "N2"] or sorted(
        e[0] for e in harness.enriched
    ) == ["N1", "N2"]
    assert all(e[1] == ".NS" and e[2] == {"mf", "bc", "piotroski", "cc"} for e in harness.enriched)
    assert [r["symbol"] for r in out["all_results"]] == ["N1", "N2"]
    assert all(r["suffix"] == ".NS" for r in out["all_results"])
    assert out["multi_hits"] == [] and out["excel"].endswith("out.xlsx")
    text = capsys.readouterr().out
    assert "Total symbols analysed:  2" in text
    assert "MULTI-SCREEN HITS (3+ screens): 0" in text
    assert f"  {'Magic Formula':<25} {0:>5} qualifying stocks" in text


def test_run_active_subset_limits_stages(sa, harness, capsys):
    out = sa.run(active_screens={"darvas"}, offline=True)
    assert harness.enriched == []  # no fundamental screens active
    assert [r["symbol"] for r in out["all_results"]] == ["N1", "N2"]
    text = capsys.readouterr().out
    assert "Darvas Breakout" in text and "Magic Formula" not in text
    assert "Golden Crossovers:   0" in text


def test_run_online_uses_screener_in_for_mf_gc_bc_only_when_active(sa, harness, monkeypatch):
    monkeypatch.setattr(
        sa, "fetch_screener_in_symbols", lambda i, s: harness.pre.append(s) or ["PRE1", "N1"]
    )
    sa.run(active_screens={"mf", "bc", "piotroski"})
    assert harness.pre == ["magic-formula", "the-bull-cartel"]  # gc not active
    assert harness.bulk[0] == ["N1.NS", "N2.NS", "PRE1.NS"]


def test_run_full_universe_and_use_screener_in_flag_skip_prefilter(sa, harness):
    sa.run(full_universe=True)
    sa.run(use_screener_in=False)
    assert harness.pre == []


def test_run_online_unavailable_prefilter_falls_back(sa, harness, capsys):
    sa.run(active_screens={"mf", "gc", "darvas"})
    assert harness.pre == ["magic-formula", "golden-crossover"]
    assert "screener.in unavailable" in capsys.readouterr().out
    # fallbacks to the full universe: gc and darvas each ask for NSE symbols at least once
    assert harness.nse_calls >= 2


def test_run_empty_universe_branch(sa, harness, capsys):
    harness.nse = []
    out = sa.run(active_screens={"mf"}, offline=True)
    assert out["all_results"] == []
    text = capsys.readouterr().out
    assert "Full-universe fetch failed" in text
    assert "Total unique symbols to fetch OHLC for: 0" in text


def test_run_golden_cross_needs_201_bars(sa, harness, monkeypatch, capsys):
    def bulk(tickers, period):
        return {
            "N1.NS": _ohlc(np.linspace(1, 2, 201)),
            "N2.NS": _ohlc(np.linspace(1, 2, 200)),
        }

    monkeypatch.setattr(sa, "bulk_download_ohlc", bulk)
    harness.gc = {"N1": {"qualifies": True}, "N2": {"qualifies": True}}
    out = sa.run(active_screens={"gc"}, offline=True)
    assert "Golden Crossovers:   1" in capsys.readouterr().out  # N2 (200 bars) never evaluated
    byname = {r["symbol"]: r for r in out["all_results"]}
    assert "gc" in byname["N1"] and "gc" not in byname["N2"]


def test_run_multi_screen_hits_threshold_and_sorting(sa, harness, capsys):
    harness.nse = ["TWO", "THREE", "FOUR"]
    harness.darvas = {
        "TWO": {"signal": "BREAKOUT_BUY", "qualifies": True},
        "THREE": {"signal": "BREAKOUT_BUY", "qualifies": True},
        "FOUR": {"signal": "BREAKOUT_BUY", "qualifies": True},
    }
    harness.gc = {s: {"qualifies": True, "DMA50": 5.0, "DMA200": 4.0} for s in harness.nse}
    harness.fund = {
        "TWO": {"symbol": "TWO"},
        "THREE": {
            "symbol": "THREE",
            "mf": {"qualifies": True, "ROIC_%": 30.0, "Earnings_Yield_%": 16.0},
        },
        "FOUR": {
            "symbol": "FOUR",
            "mf": {"qualifies": True},
            "piotroski": {"qualifies": True, "f_score": 8},
            "cc": {"qualifies": False, "CC_Score": "3/5"},
        },
    }
    out = sa.run(offline=True)
    hits = out["multi_hits"]
    # TWO passes darvas+gc = 2 screens -> excluded; THREE=3, FOUR=4 -> sorted desc
    assert [h["Symbol"] for h in hits] == ["FOUR", "THREE"]
    assert [h["Screens_Count"] for h in hits] == [4, 3]
    assert hits[0]["Screens_Passed"] == "mf, gc, darvas, piotroski"
    assert hits[0]["Piotroski_Score"] == 8 and hits[0]["CC_Score"] == "3/5"
    assert hits[1]["MF_ROIC_%"] == 30.0 and hits[1]["MF_EY_%"] == 16.0
    assert hits[1]["GC_DMA50"] == 5.0 and hits[1]["Darvas_Signal"] == "BREAKOUT_BUY"
    text = capsys.readouterr().out
    assert "MULTI-SCREEN HITS (3+ screens): 2" in text
    assert "FOUR" in text.split("MULTI-SCREEN HITS")[1]
    # darvas breakouts become fundamental candidates for all active fundamental screens
    assert sorted(e[0] for e in harness.enriched) == ["FOUR", "THREE", "TWO"]


def test_run_enrich_exception_is_isolated(sa, harness, monkeypatch):
    def enrich(sym, df, suffix, active):
        if sym == "N1":
            raise RuntimeError("worker died")
        return {"symbol": sym}

    monkeypatch.setattr(sa, "enrich_symbol", enrich)
    out = sa.run(offline=True)
    byname = {r["symbol"]: r for r in out["all_results"]}
    assert byname["N1"]["error"] == "worker died" and "error" not in byname["N2"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="save_results_excel: sort(ascending=False) on a map of check->0 / cross->1 puts "
    "NON-qualifying rows first, contradicting its 'Qualifying stocks first' comment",
)
def test_save_results_excel_puts_qualifiers_first(sa, xl):
    results = [
        {"symbol": "LOSE", "suffix": ".NS", "mf": {"qualifies": False, "ROIC_%": 1}},
        {"symbol": "WIN", "suffix": ".NS", "mf": {"qualifies": True, "ROIC_%": 40}},
    ]
    sa.save_results_excel(results, [], {"mf"})
    assert xl["Magic_Formula"]["Symbol"].tolist() == ["WIN", "LOSE"]
