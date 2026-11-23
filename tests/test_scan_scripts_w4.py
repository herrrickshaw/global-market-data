"""Shared logic in the full_*_market_scan scripts: Darvas box, golden cross, fundamental screens."""

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load

SCANS = [
    "full_us_market_scan",
    "full_indian_market_scan",
    "full_japan_market_scan",
    "full_korea_market_scan",
    "full_european_market_scan",
]
DARVAS = [*SCANS, "us_market_screener"]


def _ohlc(highs, lows, closes):
    idx = pd.date_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes}, index=idx)


def _box(c):
    """Box top 20 / bottom 10 confirmed by three lower highs / three higher lows; `c` is today's bar."""
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    lows = [3, 3, 12, 10, 11, 12, 13, 13]
    closes = [4, 4, 11, 15, 15, 15, 15, c]
    return _ohlc(highs, lows, closes)


def _darvas_fn(mod):
    return getattr(mod, "compute_darvas_box", None) or getattr(mod, "_darvas_from_df")


@pytest.mark.parametrize("name", DARVAS)
def test_darvas_signals(monkeypatch, tmp_path, name):
    mod = load(monkeypatch, name, tmp_path)
    fn = _darvas_fn(mod)
    frame = _box
    up = fn(frame(21.0))
    assert up["signal"] == "BREAKOUT_BUY" and up["box_top"] == 20.0 and up["box_bottom"] == 10.0
    assert fn(frame(20.0))["signal"] == "IN_BOX"  # at the top is not a breakout
    assert fn(frame(10.0))["signal"] == "IN_BOX"  # at the bottom is not a breakdown
    assert fn(frame(9.0))["signal"] == "BREAKDOWN_SELL"
    assert fn(frame(15.0))["signal"] == "IN_BOX"


@pytest.mark.parametrize("name", ["full_us_market_scan", "full_indian_market_scan"])
def test_darvas_extended_metrics(monkeypatch, tmp_path, name):
    mod = load(monkeypatch, name, tmp_path)
    up = mod.compute_darvas_box(_box(21.0))
    assert up["upside_to_top_pct"] == pytest.approx(-4.76, abs=0.01) and up["position_in_box_pct"] == pytest.approx(110.0)
    mid = mod.compute_darvas_box(_box(15.0))
    assert mid["position_in_box_pct"] == 50.0 and mid["box_range"] == 10.0 and mid["data_points"] == 8


@pytest.mark.parametrize("name", DARVAS)
def test_darvas_degenerate_inputs(monkeypatch, tmp_path, name):
    mod = load(monkeypatch, name, tmp_path)
    fn = _darvas_fn(mod)
    short = _ohlc([1.0] * 7, [1.0] * 7, [1.0] * 7)
    assert fn(short)["signal"] == "INSUFFICIENT_DATA" and fn(short)["box_top"] is None
    rising = _ohlc(list(range(1, 21)), list(range(1, 21)), list(range(1, 21)))
    assert fn(rising)["signal"] == "NO_BOX"  # highs only rise: no confirmed top
    assert fn(_ohlc([0.0] * 20, [0.0] * 20, [1.0] * 20))["signal"] == "NO_BOX"  # zero highs are ignored


@pytest.mark.parametrize("name", DARVAS)
def test_darvas_current_bar_excluded_from_box(monkeypatch, tmp_path, name):
    mod = load(monkeypatch, name, tmp_path)
    fn = _darvas_fn(mod)
    base = _box(15.0)
    spike = base.copy()
    spike.iloc[-1, spike.columns.get_loc("High")] = 99.0  # today's spike must not become the box top
    assert fn(spike)["box_top"] == fn(base)["box_top"] == 20.0


@pytest.mark.parametrize("name", [n for n in SCANS if n in ("full_us_market_scan", "full_indian_market_scan")])
def test_golden_crossover(monkeypatch, tmp_path, name):
    mod = load(monkeypatch, name, tmp_path)
    empty = {"gc_signal": False, "dma50_above_200": False, "dma50": None, "dma200": None}
    assert mod.compute_golden_crossover(None) == empty and mod.compute_golden_crossover(pd.DataFrame()) == empty
    short = pd.DataFrame({"Close": np.arange(200.0)})
    assert mod.compute_golden_crossover(short) == empty  # needs >= 201 closes
    n = 260
    close = np.concatenate([np.full(n - 59, 100.0), np.full(58, 99.0), [400.0]])
    r = mod.compute_golden_crossover(pd.DataFrame({"Close": close}))
    assert bool(r["gc_signal"]) is True  # 50-DMA below the 200-DMA yesterday, above it today
    assert r["dma50_above_200"] and r["dma50"] > r["dma200"]
    steady = mod.compute_golden_crossover(pd.DataFrame({"Close": np.linspace(50, 300, n)}))
    assert not steady["gc_signal"] and steady["dma50_above_200"]
    down = mod.compute_golden_crossover(pd.DataFrame({"Close": np.linspace(300, 50, n)}))
    assert not down["dma50_above_200"] and down["dma_gap_%"] < 0


# ---------------- fundamental_scan (shared yfinance statement screens) ----------------
def _stmt(rows, ncols=5):
    cols = pd.to_datetime([f"{2024 - i}-03-31" for i in range(ncols)])
    return pd.DataFrame({r: list(v)[:ncols] for r, v in rows.items()}, index=cols).T


def _good_spec(market_cap=2e9):
    inc = _stmt(
        {
            "Net Income": [120e6, 100e6, 80e6, 60e6, 40e6],
            "Total Revenue": [1000e6, 900e6, 800e6, 700e6, 600e6],
            "Gross Profit": [500e6, 420e6, 380e6, 330e6, 280e6],
            "EBIT": [300e6, 250e6, 200e6, 150e6, 100e6],
            "Operating Income": [300e6, 250e6, 200e6, 150e6, 100e6],
            "Pretax Income": [160e6, 130e6, 100e6, 80e6, 50e6],
            "Interest Expense": [10e6, 10e6, 10e6, 10e6, 10e6],
            "Tax Provision": [40e6, 30e6, 20e6, 20e6, 10e6],
        }
    )
    bal = _stmt(
        {
            "Total Assets": [1000e6, 950e6, 900e6, 850e6, 800e6],
            "Long Term Debt": [100e6, 150e6, 200e6, 250e6, 300e6],
            "Current Assets": [600e6, 500e6, 450e6, 400e6, 350e6],
            "Total Current Assets": [600e6, 500e6, 450e6, 400e6, 350e6],
            "Current Liabilities": [200e6, 220e6, 230e6, 240e6, 250e6],
            "Total Current Liabilities": [200e6, 220e6, 230e6, 240e6, 250e6],
            "Share Issued": [100e6, 100e6, 100e6, 100e6, 100e6],
            "Stockholders Equity": [600e6, 550e6, 500e6, 450e6, 400e6],
            "Total Stockholder Equity": [600e6, 550e6, 500e6, 450e6, 400e6],
            "Total Debt": [150e6, 200e6, 250e6, 300e6, 350e6],
            "Cash And Cash Equivalents": [200e6, 150e6, 100e6, 80e6, 60e6],
            "Retained Earnings": [300e6, 250e6, 200e6, 150e6, 100e6],
            "Total Liabilities Net Minority Interest": [400e6, 400e6, 400e6, 400e6, 400e6],
        }
    )
    cf = _stmt(
        {
            "Operating Cash Flow": [200e6, 180e6, 160e6, 140e6, 120e6],
            "Total Cash From Operating Activities": [200e6, 180e6, 160e6, 140e6, 120e6],
            "Free Cash Flow": [150e6, 130e6, 110e6, 90e6, 70e6],
            "Capital Expenditure": [-50e6, -50e6, -50e6, -50e6, -50e6],
            "Capital Expenditures": [-50e6, -50e6, -50e6, -50e6, -50e6],
        }
    )
    q = _stmt(
        {
            "Total Revenue": [300e6, 280e6, 260e6, 250e6, 200e6],
            "Net Income": [60e6, 50e6, 45e6, 40e6, 30e6],
            "Gross Profit": [150e6, 140e6, 130e6, 120e6, 100e6],
            "EBIT": [90e6, 80e6, 70e6, 60e6, 50e6],
            "Operating Income": [90e6, 80e6, 70e6, 60e6, 50e6],
        }
    )
    return {
        "income_stmt": inc, "financials": inc, "balance_sheet": bal, "cash_flow": cf, "cashflow": cf,
        "quarterly_income_stmt": q, "quarterly_financials": q, "market_cap": market_cap,
        "info": {
            "shortName": "Acme", "sector": "Tech", "debtToEquity": 40.0, "totalDebt": 100e6, "totalCash": 200e6,
            "bookValue": 20.0, "marketCap": market_cap, "trailingPE": 12.0, "sharesOutstanding": 100e6,
            "currentPrice": 20.0, "returnOnEquity": 0.2,
        },
    }


def _scan_call(mod, name):
    if name == "full_korea_market_scan":
        return mod.fundamental_scan("005930", ".KS")
    if name == "full_indian_market_scan":
        return mod.fundamental_scan("RELIANCE")
    return mod.fundamental_scan("AAA")


@pytest.mark.parametrize("name", SCANS)
def test_fundamental_scan_strong_company(monkeypatch, tmp_path, name):
    mod = load(monkeypatch, name, tmp_path)
    FakeTicker.spec = _good_spec()
    out = _scan_call(mod, name)
    assert not out["error"]
    assert out["f_score"] == 9  # profitable, deleveraging, improving margins/turnover/liquidity
    if "f_strong" in out:
        assert out["f_strong"] is True
    cagr = out.get("Revenue_CAGR_%", out.get("cc_rev_cagr"))
    assert cagr == pytest.approx(13.62, abs=0.01)  # (1000/600)^(1/4) - 1
    if name == "full_european_market_scan":
        assert out["cc_qualifies"] == "PASS" and out["cc_score"] == "6/6" and out["pe_ratio"] == 12.0


def test_us_fundamental_scan_screeners_exact(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_us_market_scan", tmp_path)
    FakeTicker.spec = _good_spec()
    out = mod.fundamental_scan("AAA")
    assert out["name"] == "Acme" and out["sector"] == "Tech"
    assert out["f_score"] == 9 and out["f_strong"] is True  # every Piotroski test passes on this fixture
    assert out["Revenue_CAGR_%"] == pytest.approx(((1000 / 600) ** (1 / 4) - 1) * 100, abs=0.01)
    assert out["qualifies_cc"] is True and out["cc_score"] == "6/6"
    assert out["ROIC_%"] == pytest.approx(37.5) and out["Earnings_Yield_%"] == pytest.approx(15.79, abs=0.01)
    assert out["qualifies_mf"] is True
    assert out["Sales_Growth_YoY_%"] == 50.0 and out["Profit_Growth_YoY_%"] == 100.0 and out["Net_Profit_$M"] == 60.0
    assert out["qualifies_bc"] is True


def test_us_fundamental_scan_thresholds(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_us_market_scan", tmp_path)
    FakeTicker.spec = _good_spec(market_cap=0.99e9)
    out = mod.fundamental_scan("AAA")
    assert out["cc_score"] == "5/6" and out["qualifies_cc"] is False  # market cap just under $1B
    spec = _good_spec()
    spec["info"]["debtToEquity"] = 100.0  # de = 1.0 is not < 1
    FakeTicker.spec = spec
    assert mod.fundamental_scan("AAA")["qualifies_cc"] is False
    spec["info"]["debtToEquity"] = 0.99  # values <= 10 are ratios already, not percentages
    assert mod.fundamental_scan("AAA")["qualifies_cc"] is True
    spec["info"]["debtToEquity"] = 10.0  # ratio of 10x: far above the limit (only > 10 is rescaled)
    assert mod.fundamental_scan("AAA")["qualifies_cc"] is False
    spec = _good_spec()
    q = spec["quarterly_income_stmt"].copy()
    q.loc["Net Income"] = [0.9e6, 50e6, 45e6, 40e6, 0.4e6]  # profit +125% but only $0.9M
    spec["quarterly_income_stmt"] = spec["quarterly_financials"] = q
    FakeTicker.spec = spec
    out = mod.fundamental_scan("AAA")
    assert out["Net_Profit_$M"] == 0.9 and out["qualifies_bc"] is False


def test_us_fundamental_scan_missing_data_and_errors(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_us_market_scan", tmp_path)
    FakeTicker.spec = {"info": {}, "market_cap": 0}
    out = mod.fundamental_scan("AAA")
    assert out["f_score"] is None and out["cc_score"] == "N/A" and out["qualifies_bc"] is False and out["ROIC_%"] is None
    FakeTicker.spec = {"ctor_error": "boom"}
    out = mod.fundamental_scan("AAA")
    assert out["error"] == "boom" and out["f_score"] is None and out["qualifies_mf"] is False
    spec = _good_spec()
    spec["info"], spec["market_cap"] = Exception, Exception  # info / fast_info raise -> tolerated
    FakeTicker.spec = spec
    out = mod.fundamental_scan("AAA")
    assert out["error"] == "" and out["name"] == "" and out["qualifies_cc"] is False
    spec = _good_spec()
    spec["quarterly_income_stmt"] = spec["quarterly_financials"] = spec["quarterly_income_stmt"].iloc[:, :4]
    FakeTicker.spec = spec
    assert mod.fundamental_scan("AAA")["Sales_Growth_YoY_%"] is None  # needs 5 quarters
