"""Characterisation of full_indian_market_scan.main (all external I/O faked)."""

import sys
import types
from pathlib import Path

import pytest


def _stub(name, **attrs):
    """Register a stub for an optional dependency that the module only needs importable."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


_stub("nsepython", get_bhavcopy=lambda *a, **k: None)
_stub("bseindia")
_stub("bseindia.libutil", SECURITY_MASTER_URL="http://invalid.test/")
sys.modules["bseindia"].libutil = sys.modules["bseindia.libutil"]

import full_indian_market_scan as m
from tests._scan_golden import FrozenDatetime, check_golden, fake_fund, run_captured, synthetic_ohlc


class FakeFetcher:
    def print_live_context(self):
        print("  NIFTY live context (fake)")


def _fund(sym, suffix):
    res = fake_fund(sym)
    res["ROCE_avg_%"] = 17.5
    res["Net_Profit_Cr"] = 321.0
    return res


@pytest.fixture
def faked(monkeypatch):
    saved = {"cache_saved": []}
    monkeypatch.setattr(m, "datetime", FrozenDatetime)
    monkeypatch.setattr(m, "fundamental_scan", _fund)
    monkeypatch.setattr(m, "_USE_NSE_FETCHER", True)
    monkeypatch.setattr(m, "_NSE_FETCHER", FakeFetcher())
    monkeypatch.setattr(m, "_save_symbol_cache", lambda a, b: saved["cache_saved"].append((a, b)))
    monkeypatch.setattr(
        m,
        "bulk_download_ohlc",
        lambda tickers, period="1y": {
            t: synthetic_ohlc(i, n=260 if i % 4 else 30) for i, t in enumerate(tickers)
        },
    )

    def fake_save(*a, **kw):
        saved["args"] = a
        saved["kwargs"] = kw
        return Path("/tmp/fake_in.xlsx")

    monkeypatch.setattr(m, "save_excel", fake_save)
    return saved


@pytest.mark.parametrize(
    "name,cache,kwargs",
    [
        (
            "in_main_cache_hit",
            (["RELI", "TCS", "INFZ", "HDFC"], ["500001", "500002"]),
            {"workers": 1},
        ),
        (
            "in_main_cache_hit_nse_only",
            (["RELI", "TCS"], ["500001"]),
            {"nse_only": True, "workers": 1},
        ),
        ("in_main_miss_no_scans", None, {"run_scans": False, "top": 3}),
    ],
)
def test_main_golden(faked, monkeypatch, name, cache, kwargs):
    if cache is None:
        monkeypatch.setattr(m, "_load_symbol_cache", lambda: (None, None))
        monkeypatch.setattr(m, "fetch_nse_symbols", lambda: ["AAA", "BBB", "CCC"])
        monkeypatch.setattr(m, "fetch_bse_only_symbols", lambda known: ["500009"])
    else:
        monkeypatch.setattr(m, "_load_symbol_cache", lambda: cache)
    out, res = run_captured(m.main, **kwargs)
    check_golden(name, {"stdout": out, "result": res, "saved": faked})
