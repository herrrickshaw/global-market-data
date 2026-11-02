"""Characterisation tests for ingest_gate (dedup/dtypes/report) and data_sources (fallback chain)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import data_sources as ds
import ingest_gate as ig


def _long():
    return pd.DataFrame(
        {
            "Symbol": ["A", "A", "A", "B"],
            "Date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-01"]),
            "Open": [1.0, 1.5, 2.0, 3.0],
            "High": [1.0, 1.5, 2.0, 3.0],
            "Low": [1.0, 1.5, 2.0, 3.0],
            "Close": [1.0, 1.5, 2.0, 3.0],
            "Volume": [10, 20, 30, 2**40],
        }
    )


def test_dedup_keeps_newest_row():
    clean, dropped = ig.dedup(_long())
    assert dropped == 1 and len(clean) == 3
    assert clean[(clean.Symbol == "A") & (clean.Date == "2024-01-01")].Close.item() == 1.5


def test_dedup_passthrough_for_empty_or_missing_keys():
    assert ig.dedup(pd.DataFrame({"x": [1]}))[1] == 0
    assert ig.dedup(None)[1] == 0


def test_enforce_dtypes_float32_and_volume_width():
    out = ig.enforce_dtypes(_long())
    assert out.Close.dtype == np.float32
    assert out.Volume.dtype == np.int64  # 2**40 does not fit int32
    small = ig.enforce_dtypes(_long().head(2))
    assert small.Volume.dtype == np.int32


def test_gate_frame_and_report_after(tmp_path, capsys):
    path = tmp_path / "seed.parquet"
    path.write_bytes(b"x" * 1000)
    clean = ig.gate_frame("IN", _long(), path)
    assert len(clean) == 3 and "dropped 1 dup rows" in capsys.readouterr().out
    rep = ig.report_after("IN", path, before_bytes=0, verbose=False)
    assert rep["market"] == "IN" and rep["after_mb"] == 0.0
    assert rep["audit"] in ("audited", "audit-skipped")


def _frame(n=3):
    idx = pd.date_range("2024-01-01", periods=n)
    return pd.DataFrame({"Close": range(n)}, index=idx)


def test_fetch_falls_back_only_for_missing_tickers(monkeypatch):
    calls = []

    def first(tickers, **kw):
        calls.append(("first", list(tickers)))
        return {"A": _frame()}

    def second(tickers, **kw):
        calls.append(("second", list(tickers)))
        return {t: _frame() for t in tickers}

    monkeypatch.setitem(ds.SOURCES, "first", first)
    monkeypatch.setitem(ds.SOURCES, "second", second)
    out = ds.fetch(["A", "B", "A"], order=("first", "second", "unknown"), verbose=False)
    assert set(out) == {"A", "B"}
    assert calls == [("first", ["A", "B"]), ("second", ["B"])]
    assert ds.get_last_stats() == {
        "requested": 2,
        "filled": 2,
        "by_source": {"first": 1, "second": 1},
    }


def test_stooq_symbol_mapping():
    assert ds._stooq_symbol("7203.T") == "7203.jp"
    assert ds._stooq_symbol("AAPL") == "aapl.us"
    assert ds._stooq_symbol("RELIANCE.NS") is None


def test_eodhd_dormant_without_key(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    assert ds.eodhd_fetch(["AAPL"], verbose=False) == {}
