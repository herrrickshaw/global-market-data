"""Characterisation of us_stock_daily_report.compute_piotroski_score / compute_coffee_can.

_yf_financials and the ticker are faked; no network.
"""

import types

import pandas as pd
import pytest

import us_stock_daily_report as us
from tests._scan_golden import check_golden


def _df(rows):
    """rows: {name: (curr, prev, older...)} -> yfinance-style frame (newest year first)."""
    width = max(len(v) for v in rows.values())
    cols = [pd.Timestamp(2025 - i, 12, 31) for i in range(width)]
    return pd.DataFrame({n: list(v) + [None] * (width - len(v)) for n, v in rows.items()}, index=cols).T


GOOD = dict(
    inc=_df(
        {
            "Net Income": (150.0, 100.0, 80.0, 60.0),
            "Total Revenue": (1500.0, 1200.0, 1000.0, 900.0),
            "Gross Profit": (600.0, 450.0, 380.0, 330.0),
        }
    ),
    bal=_df(
        {
            "Total Assets": (1000.0, 1000.0, 900.0, 800.0),
            "Long Term Debt": (100.0, 200.0, 180.0, 170.0),
            "Current Assets": (500.0, 400.0, 350.0, 300.0),
            "Current Liabilities": (250.0, 260.0, 250.0, 240.0),
            "Share Issued": (1e6, 1.1e6, 1.1e6, 1.1e6),
            "Stockholders Equity": (800.0, 700.0, 600.0, 500.0),
        }
    ),
    cf=_df(
        {
            "Operating Cash Flow": (200.0, 150.0, 120.0, 100.0),
            "Capital Expenditure": (-50.0, -40.0, -30.0, -30.0),
        }
    ),
)
BAD = dict(
    inc=_df(
        {
            "Net Income": (-20.0, 40.0, 50.0),
            "Total Revenue": (800.0, 900.0, 950.0),
            "Gross Profit": (200.0, 300.0, 330.0),
        }
    ),
    bal=_df(
        {
            "Total Assets": (1000.0, 900.0, 900.0),
            "Long Term Debt": (400.0, 200.0, 180.0),
            "Current Assets": (300.0, 400.0, 350.0),
            "Current Liabilities": (350.0, 260.0, 250.0),
            "Share Issued": (2e6, 1e6, 1e6),
            "Total Stockholder Equity": (300.0, 400.0, 450.0),
        }
    ),
    cf=_df({"Total Cash From Operating Activities": (-10.0, 20.0, 30.0)}),
)
SPARSE = dict(inc=_df({"Net Income": (10.0,)}), bal=None, cf=None)
FCF_ROW = dict(GOOD, cf=_df({"Free Cash Flow": (90.0, 80.0)}))

SCENARIOS = {
    "good": (GOOD, {"debtToEquity": 45.0, "marketCap": 5e9}, 7e9),
    "bad": (BAD, {"debtToEquity": 150.0, "marketCap": 5e8}, None),
    "sparse": (SPARSE, {}, None),
    "fcf_row_ltd_de": (FCF_ROW, {}, 2e9),
    "no_income": (dict(inc=None, bal=None, cf=None), {}, None),
}


class _FakeTicker:
    def __init__(self, info, fast_mcap):
        self.info = info
        self._fast = fast_mcap

    @property
    def fast_info(self):
        if self._fast is None:
            raise RuntimeError("no fast_info")
        return types.SimpleNamespace(market_cap=self._fast)


@pytest.mark.parametrize("fn_name", ["compute_piotroski_score", "compute_coffee_can"])
def test_fundamentals_golden(monkeypatch, fn_name):
    out = {}
    for name, (frames, info, fast) in SCENARIOS.items():
        monkeypatch.setattr(us, "_yf_financials", lambda t, f=frames: (f["inc"], f["bal"], f["cf"]))
        out[name] = getattr(us, fn_name)(_FakeTicker(info, fast), "SYM")
    check_golden(f"us_{fn_name}", out)
