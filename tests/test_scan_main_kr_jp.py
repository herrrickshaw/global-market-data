"""Characterisation of full_korea_market_scan.main and full_japan_market_scan.main (all I/O faked)."""

import sys
import types

import pytest

if "pykrx" not in sys.modules:  # optional dependency; module only needs the import to succeed
    _fake = types.ModuleType("pykrx")
    _fake.stock = types.SimpleNamespace()
    sys.modules["pykrx"] = _fake

import full_japan_market_scan as jp
import full_korea_market_scan as kr
from tests._scan_golden import (
    FrozenDatetime,
    check_golden,
    dump_workbook,
    run_captured,
    synthetic_ohlc,
)


def _fund(*args):
    sym = args[0]
    k = sum(ord(c) for c in sym)
    return {
        "f_score": k % 10,
        "f_strong": k % 3 != 0,
        "cc_qualifies": "PASS" if k % 7 else "FAIL",
        "cc_score": k % 5,
        "cc_rev_cagr": 9.5,
        "cc_roce_avg": 14.0,
        "cc_debt_equity": 0.4,
        "market_cap_b_krw": 1234.0,
        "market_cap_b_jpy": 567.0,
        "error": "",
    }


def _kr_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(kr, "datetime", FrozenDatetime)
    monkeypatch.setattr(kr, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(kr, "fundamental_scan", _fund)
    universe = [
        {"code": f"00{i}0", "name": f"Kr{i}", "market": "KOSPI" if i % 2 else "KOSDAQ",
         "yf_suffix": ".KS" if i % 2 else ".KQ"}
        for i in range(1, 8)
    ]
    monkeypatch.setattr(kr, "build_krx_universe", lambda kospi_only=False: list(universe))
    ohlc = {u["code"]: synthetic_ohlc(i, n=260 if i != 3 else 40) for i, u in enumerate(universe)}
    del ohlc[universe[5]["code"]]
    monkeypatch.setattr(kr, "fetch_krx_ohlc", lambda uni, months=3: ohlc)


def _jp_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(jp, "datetime", FrozenDatetime)
    monkeypatch.setattr(jp, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(jp, "fundamental_scan", _fund)
    universe = [
        {"yf_ticker": f"{7000 + i}.T", "code": f"{7000 + i}", "name": f"Jp{i}", "sector": "Ind"}
        for i in range(7)
    ]
    monkeypatch.setattr(jp, "build_ticker_universe", lambda: list(universe))
    monkeypatch.setattr(
        jp,
        "bulk_download_ohlc",
        lambda tickers, period="3mo": {
            t: synthetic_ohlc(i, n=260 if i != 3 else 40) for i, t in enumerate(tickers)
        },
    )


CASES = [
    ("kr_main", kr, _kr_setup, ["prog", "--workers", "1"], "korea_market_scan_*.xlsx"),
    ("kr_main_top_no_scans", kr, _kr_setup, ["prog", "--top", "4", "--no-scans"], "korea_market_scan_*.xlsx"),
    ("kr_main_capped", kr, _kr_setup, ["prog", "--workers", "1", "--kospi-only"], "korea_market_scan_*.xlsx"),
    ("jp_main", jp, _jp_setup, ["prog", "--workers", "1"], "japan_market_scan_*.xlsx"),
    ("jp_main_top_no_scans", jp, _jp_setup, ["prog", "--top", "4", "--no-scans"], "japan_market_scan_*.xlsx"),
    ("jp_main_capped", jp, _jp_setup, ["prog", "--workers", "1"], "japan_market_scan_*.xlsx"),
]


@pytest.mark.parametrize("name,mod,setup,argv,pattern", CASES)
def test_main_golden(monkeypatch, tmp_path, name, mod, setup, argv, pattern):
    setup(monkeypatch, tmp_path)
    if name.endswith("capped"):
        monkeypatch.setattr(mod, "MAX_FUND_CANDIDATES", 1)
    monkeypatch.setattr(sys, "argv", argv)
    out, _ = run_captured(mod.main)
    xlsx = sorted(tmp_path.glob(pattern))
    assert len(xlsx) == 1
    out = out.replace(str(tmp_path), "<TMP>")
    check_golden(name, {"stdout": out, "workbook": dump_workbook(xlsx[0])})
