"""Import the big yfinance-driven scan scripts without network, caches or optional deps."""

import sys
import types

from tests.w4_helpers import fake_module, fresh_import


class FakeTicker:
    """Configurable yfinance.Ticker stand-in; set attributes on the class-level `spec`."""

    spec = {}

    def __init__(self, symbol):
        self.symbol = symbol
        s = FakeTicker.spec
        if s.get("ctor_error"):
            raise RuntimeError(s["ctor_error"])
        for k in (
            "income_stmt", "financials", "balance_sheet", "cash_flow", "cashflow",
            "quarterly_income_stmt", "quarterly_financials", "quarterly_balance_sheet",
            "quarterly_cashflow", "dividends", "history_df",
        ):
            setattr(self, k, s.get(k))
        self._info = s.get("info", {})
        self._fast = s.get("market_cap", 0)

    @property
    def info(self):
        if self._info is Exception:
            raise RuntimeError("info down")
        return self._info

    @property
    def fast_info(self):
        if self._fast is Exception:
            raise RuntimeError("fast down")
        return types.SimpleNamespace(market_cap=self._fast)

    def history(self, *a, **k):
        import pandas as pd

        h = FakeTicker.spec.get("history_df")
        return h if h is not None else pd.DataFrame()


_STUBS = [
    "nsepython", "bseindia", "bseindia.libutil", "pykrx", "pykrx.stock", "nse", "bse", "openpyxl", "openpyxl.styles", "openpyxl.utils",
    "openpyxl.chart", "openpyxl.formatting", "openpyxl.formatting.rule", "openpyxl.worksheet",
    "openpyxl.worksheet.table", "scipy", "scipy.stats", "scipy.optimize", "sklearn", "sklearn.ensemble",
    "sklearn.linear_model", "sklearn.preprocessing", "sklearn.metrics", "sklearn.model_selection",
    "duckdb", "tqdm", "feedparser", "vaderSentiment", "vaderSentiment.vaderSentiment",
]


def load(monkeypatch, name, cwd):
    from unittest import mock

    for mod in _STUBS:
        if mod not in sys.modules:
            monkeypatch.setitem(sys.modules, mod, mock.MagicMock())
    monkeypatch.chdir(cwd)
    fake_module(monkeypatch, "yfinance", Ticker=FakeTicker, download=lambda *a, **k: None)
    for opt in ("ncdssdk", "market_data_cache", "ml_signal_engine", "nse_data_fetcher"):
        monkeypatch.setitem(sys.modules, opt, None)
    return fresh_import(monkeypatch, name)
