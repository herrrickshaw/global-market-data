"""Characterisation of full_european_market_scan.main (yfinance, fundamentals, disk faked)."""

import sys

import pandas as pd
import pytest

import full_european_market_scan as m
from tests._scan_golden import (
    FrozenDatetime,
    check_golden,
    dump_workbook,
    run_captured,
    synthetic_ohlc,
)


def _fake_fund(sym):
    return {
        "pe_ratio": 11.5,
        "pb_ratio": 1.2,
        "dividend_yield": 2.5,
        "pegy_ratio": 0.9,
        "market_cap_b": 12.3,
        "f_score": 8 if sym[0] < "M" else 5,
        "f_interpretation": "Strong",
        "cc_score": 4,
        "cc_qualifies": "PASS",
        "cc_rev_cagr": 10.0,
        "cc_roce_avg": 18.0,
        "cc_debt_equity": 0.3,
        "error": None,
    }


def _raw(symbols):
    frames = {}
    for i, s in enumerate(symbols):
        df = synthetic_ohlc(i + 3, n=260 if i != 2 else 10)
        frames[s] = df
    return pd.concat(frames, axis=1)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "datetime", FrozenDatetime)
    monkeypatch.setattr(m, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(m, "fundamental_scan", _fake_fund)
    monkeypatch.setattr(m, "MAX_WORKERS", 1)
    symbols = list(m.EURO_STOXX_50_META.keys())[:5]
    monkeypatch.setattr(m.yf, "download", lambda syms, **kw: _raw(list(syms)))
    return tmp_path


@pytest.mark.parametrize(
    "name,argv",
    [
        ("eu_main_scans", ["prog", "--top", "5"]),
        ("eu_main_no_scans", ["prog", "--top", "5", "--no-scans"]),
    ],
)
def test_main_golden(env, monkeypatch, name, argv):
    monkeypatch.setattr(sys, "argv", argv)
    out, _ = run_captured(m.main)
    xlsx = sorted(env.glob("european_market_scan_*.xlsx"))
    assert len(xlsx) == 1
    out = out.replace(str(env), "<TMP>")
    check_golden(name, {"stdout": out, "workbook": dump_workbook(xlsx[0])})


def test_main_download_failure_exits(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(m.yf, "download", boom)
    monkeypatch.setattr(sys, "argv", ["prog", "--top", "2"])
    with pytest.raises(SystemExit) as ei:
        run_captured(m.main)
    assert "net down" in str(ei.value)
