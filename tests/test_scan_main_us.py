"""Characterisation of full_us_market_scan.main (all external I/O faked)."""

from pathlib import Path

import pandas as pd
import pytest

import full_us_market_scan as m
from tests._scan_golden import FrozenDatetime, check_golden, fake_fund, run_captured, synthetic_ohlc


class FakeML:
    def predict_batch(self, ohlc, max_workers=1):
        rows = [
            {
                "symbol": s,
                "direction": ["BULLISH", "BEARISH", "NEUTRAL"][i % 3],
                "predicted_ret%": i * 1.5,
                "confidence": 0.5 + i / 10,
                "train_rmse": 0.01 * i,
            }
            for i, s in enumerate(ohlc)
        ]
        return pd.DataFrame(rows)


@pytest.fixture
def faked(monkeypatch):
    saved = {}
    monkeypatch.setattr(m, "datetime", FrozenDatetime)
    monkeypatch.setattr(m, "_NSE_OK", False)
    monkeypatch.setattr(m, "_CACHE_OK", True)
    monkeypatch.setattr(m, "_load_symbol_cache", lambda: (["AAA", "BBB", "CCZ"], ["DDD", "AAA"]))
    monkeypatch.setattr(
        m,
        "bulk_download_ohlc",
        lambda syms, period="1y": {s: synthetic_ohlc(i) for i, s in enumerate(syms)},
    )
    monkeypatch.setattr(m, "fundamental_scan", fake_fund)

    def fake_save(*a, **kw):
        saved["args"] = a
        saved["kwargs"] = kw
        return Path("/tmp/fake_us.xlsx")

    monkeypatch.setattr(m, "save_excel", fake_save)
    return saved


def test_main_market_context(faked, monkeypatch):
    """_NSE_OK branch: fake yfinance module, S&P regime + VIX lines."""
    import sys
    import types

    def fake_download(sym, period="1y", auto_adjust=True, progress=False):
        n = 260 if sym == "^GSPC" else 5
        return synthetic_ohlc(7, n=n, start=4000.0 if sym == "^GSPC" else 20.0)

    fake_yf = types.SimpleNamespace(download=fake_download)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    monkeypatch.setattr(m, "_NSE_OK", True)
    monkeypatch.setattr(m, "_ML_OK", False)
    monkeypatch.setattr(m, "_ML_ENGINE", None)
    out, res = run_captured(m.main, run_scans=False, top=2)
    check_golden("us_main_context", {"stdout": out, "result": res, "saved": faked})


@pytest.mark.parametrize(
    "name,ml,kwargs",
    [
        ("us_main_full_ml", True, {"workers": 1}),
        ("us_main_no_scans", False, {"run_scans": False, "top": 3}),
    ],
)
def test_main_golden(faked, monkeypatch, name, ml, kwargs):
    monkeypatch.setattr(m, "_ML_OK", ml)
    monkeypatch.setattr(m, "_ML_ENGINE", FakeML() if ml else None)
    out, res = run_captured(m.main, **kwargs)
    check_golden(name, {"stdout": out, "result": res, "saved": faked})
