"""Regression tests from the 2026-10-25 static-analysis sweep (offline, no network)."""

import sys
import types

import numpy as np
import pandas as pd
import pytest


# ── walk_forward_backtest.compute_filing_trend: q_val called with 4 args ──────


def _qdf(rows: dict, n_cols: int = 8) -> pd.DataFrame:
    cols = pd.date_range("2025-12-31", periods=n_cols, freq="-1QE")
    return pd.DataFrame({c: [rows[k][i] for k in rows] for i, c in enumerate(cols)}, index=list(rows))


class _FakeTicker:
    """Minimal yfinance.Ticker stand-in: 8 quarters of synthetic statements."""

    def __init__(self, symbol):
        self.symbol = symbol
        # newest quarter first; YoY growth > 10% / > 15% in each of the first 4
        self.quarterly_income_stmt = _qdf(
            {
                "Total Revenue": [200, 190, 180, 170, 100, 100, 100, 100],
                "Net Income": [20, 19, 18, 17, 10, 10, 10, 10],
            }
        )
        self.quarterly_balance_sheet = _qdf({"Long Term Debt": [10, 20, 30, 40, 50, 60, 70, 80]})
        self.quarterly_cash_flow = _qdf({"Operating Cash Flow": [5] * 8})
        # annual statements absent -> piotroski_trend stays 0
        self.income_stmt = None
        self.financials = None
        self.balance_sheet = None
        self.cash_flow = None
        self.cashflow = None


@pytest.fixture
def wf_module(monkeypatch, tmp_path):
    """Import walk_forward_backtest with a stub yfinance and no NSE fetcher, in tmp cwd."""
    monkeypatch.chdir(tmp_path)  # module creates ./wf_backtest at import
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = _FakeTicker
    fake_yf.download = lambda *a, **k: pd.DataFrame()
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    # a bare module without NSEDataFetcher -> ImportError -> fetcher disabled
    monkeypatch.setitem(sys.modules, "nse_data_fetcher", types.ModuleType("nse_data_fetcher"))
    monkeypatch.delitem(sys.modules, "walk_forward_backtest", raising=False)
    import walk_forward_backtest as wf

    return wf


def test_compute_filing_trend_counts_ocf_streak(wf_module):
    res = wf_module.compute_filing_trend("FAKE", ".NS")
    # 8 positive OCF quarters, capped at 3 for scoring
    assert res["ocf_streak"] == 3
    # 4 comparable quarters (min(6, 8-4)), each >10% / >15% YoY
    assert res["rev_streak"] == 4
    assert res["profit_streak"] == 4
    # debt falls every quarter -> min(4, 7) = 4
    assert res["debt_streak"] == 4
    # score = 3 + 3 + 3 + 3 + 0 = 12 -> STRONG
    assert res["filing_score"] == 12
    assert res["filing_class"] == "STRONG"


# ── market_data_cache.MarketCache.get_ohlc: parquet present but no meta entry ─


def _ohlc(start, n):
    idx = pd.date_range(start, periods=n, freq="D")
    v = np.linspace(100.0, 100.0 + n - 1, n)
    return pd.DataFrame(
        {"Open": v, "High": v * 1.01, "Low": v * 0.99, "Close": v, "Volume": 1000.0},
        index=idx,
    )


def test_get_ohlc_with_orphan_parquet_returns_frame(monkeypatch, tmp_path):
    import market_data_cache as mdc

    monkeypatch.setattr(mdc, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(mdc, "OHLC_DIR", tmp_path / "ohlc")
    monkeypatch.setattr(mdc, "META_DIR", tmp_path / "meta")
    monkeypatch.setattr(mdc, "META_FILE", tmp_path / "meta" / "cache_index.json")
    (tmp_path / "ohlc").mkdir()
    (tmp_path / "meta").mkdir()

    cache = mdc.MarketCache(verbose=False)
    existing = _ohlc("2024-01-01", 5)
    existing.to_parquet(cache._ohlc_path("FAKE.NS"))  # parquet on disk, meta index empty

    fresh = _ohlc("2024-01-06", 2)
    calls = []

    def fake_single(self, ticker, start):
        calls.append((ticker, start))
        return fresh

    monkeypatch.setattr(mdc.MarketCache, "_yf_download_single", fake_single)

    out = cache.get_ohlc("FAKE.NS")
    assert isinstance(out, pd.DataFrame), "get_ohlc returned None for an orphan parquet"
    # incremental refresh from the day after the file's last row: 5 + 2 rows
    assert len(out) == 7
    assert calls and calls[0][1] == pd.Timestamp("2024-01-06").date()
    assert "ohlc:FAKE.NS" in cache._meta
