"""frames.py + ingest_gate.py: compact parquet seeds and the ingestion gate."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import frames
import ingest_gate
from tests.w4_helpers import fake_module


def _hist(n=3, base=100.0, vol=1000):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = base + np.arange(n, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": [vol] * n}, index=idx
    )


# ---------------- frames ----------------
def test_to_long_dtypes_and_order():
    long = frames.to_long({"B": _hist(2), "A": _hist(3)})
    assert list(long["Symbol"]) == ["A", "A", "A", "B", "B"]  # sorted by Symbol, Date
    assert long["Close"].dtype == np.float32
    assert long["Volume"].dtype == np.int32
    assert list(long.columns[:6]) == ["Date", "Open", "High", "Low", "Close", "Volume"]


def test_to_long_skips_empty_none_and_empty_result_schema():
    long = frames.to_long({"A": None, "B": pd.DataFrame()})
    assert long.empty
    assert list(long.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", "Symbol"]


def test_to_long_volume_stays_int64_when_too_big():
    h = _hist(2, vol=3_000_000_000)
    long = frames.to_long({"A": h})
    assert long["Volume"].dtype == np.int64
    assert long["Volume"].max() == 3_000_000_000


def test_to_long_bad_volume_coerced_to_zero():
    h = _hist(2)
    h["Volume"] = ["x", 5]
    long = frames.to_long({"A": h})
    assert list(long["Volume"]) == [0, 5]


def test_long_to_dict_min_bars_boundary_and_empty():
    long = frames.to_long({"A": _hist(3), "B": _hist(2)})
    assert set(frames.long_to_dict(long, min_bars=2)) == {"A", "B"}
    assert set(frames.long_to_dict(long, min_bars=3)) == {"A"}
    assert frames.long_to_dict(None) == {}
    assert frames.long_to_dict(pd.DataFrame()) == {}


def test_long_to_dict_sorts_by_date_and_indexes_date():
    long = frames.to_long({"A": _hist(3)}).iloc[::-1]
    out = frames.long_to_dict(long)["A"]
    assert out.index.is_monotonic_increasing
    assert list(out.columns) == frames.OHLCV
    assert out.index.name == "Date"


def test_write_read_seed_roundtrip(tmp_path):
    p = tmp_path / "sub" / "seed.parquet"
    n = frames.write_seed({"A": _hist(3), "B": _hist(4)}, p)
    assert n == 2 and p.exists()
    back = frames.read_seed(p)
    assert set(back) == {"A", "B"} and len(back["B"]) == 4
    assert frames.read_seed(p, min_bars=4).keys() == {"B"}


def test_write_seed_empty_writes_nothing(tmp_path):
    p = tmp_path / "x.parquet"
    assert frames.write_seed({}, p) == 0
    assert not p.exists()


def test_read_seed_missing_is_empty(tmp_path):
    assert frames.read_seed(tmp_path / "nope.parquet") == {}


def test_recompress_seed_missing_and_empty(tmp_path):
    assert frames.recompress_seed(tmp_path / "no.parquet") == {
        "path": str(tmp_path / "no.parquet"),
        "error": "missing",
    }
    empty = tmp_path / "e.parquet"
    pd.DataFrame(columns=["Date", *frames.OHLCV, "Symbol"]).to_parquet(empty)
    assert frames.recompress_seed(empty)["error"] == "empty"


def test_recompress_seed_reports_sizes(tmp_path):
    p = tmp_path / "s.parquet"
    frames.write_seed({"A": _hist(50)}, p)
    r = frames.recompress_seed(p)
    assert r["path"] == "s.parquet"
    assert set(r) == {"path", "before_mb", "after_mb", "saved_pct"}
    assert r["saved_pct"] == pytest.approx(0.0, abs=100)
    assert frames.read_seed(p)["A"].shape[0] == 50


# ---------------- ingest_gate ----------------
def _long_dupes():
    long = frames.to_long({"A": _hist(3)})
    dup = long.iloc[[0]].copy()
    dup["Close"] = 999.0
    return pd.concat([long, dup], ignore_index=True)


def test_dedup_newest_wins_and_counts():
    clean, dropped = ingest_gate.dedup(_long_dupes())
    assert dropped == 1 and len(clean) == 3
    first = clean[clean["Date"] == clean["Date"].min()].iloc[0]
    assert first["Close"] == 999.0  # keep="last"


def test_dedup_passthrough_cases():
    assert ingest_gate.dedup(None) == (None, 0)
    e = pd.DataFrame()
    assert ingest_gate.dedup(e)[1] == 0
    nokeys = pd.DataFrame({"x": [1, 1]})
    out, d = ingest_gate.dedup(nokeys)
    assert d == 0 and len(out) == 2


def test_enforce_dtypes_int32_boundary():
    df = pd.DataFrame(
        {"Open": [1], "High": [1], "Low": [1], "Close": ["2.5"], "Volume": [2_147_483_647]}
    )
    out = ingest_gate.enforce_dtypes(df)
    assert out["Close"].dtype == np.float32 and out["Close"].iloc[0] == 2.5
    assert out["Volume"].dtype == np.int32
    big = df.assign(Volume=[2_147_483_648])
    assert ingest_gate.enforce_dtypes(big)["Volume"].dtype == np.int64


def test_enforce_dtypes_nan_volume_and_missing_cols():
    df = pd.DataFrame({"Close": [1.0], "Volume": [np.nan]})
    out = ingest_gate.enforce_dtypes(df)
    assert out["Volume"].iloc[0] == 0
    assert "Open" not in out
    assert ingest_gate.enforce_dtypes(None) is None
    e = pd.DataFrame()
    assert ingest_gate.enforce_dtypes(e) is e
    # input not mutated
    assert df["Volume"].dtype == np.float64


def test_gate_frame_output_and_quiet(tmp_path, capsys):
    out_path = tmp_path / "x.parquet"
    out_path.write_bytes(b"\0" * 2_000_000)
    clean = ingest_gate.gate_frame("IN", _long_dupes(), out_path)
    txt = capsys.readouterr().out
    assert len(clean) == 3
    assert "ingest-gate[IN]: 3 rows / 1 symbols" in txt
    assert "dropped 1 dup rows" in txt
    assert "prev 2.0MB" in txt
    ingest_gate.gate_frame("IN", _long_dupes(), out_path, verbose=False)
    assert capsys.readouterr().out == ""


def test_gate_frame_zero_dups_and_no_prev(tmp_path, capsys):
    clean = ingest_gate.gate_frame("US", frames.to_long({"A": _hist(2)}), tmp_path / "nope")
    txt = capsys.readouterr().out
    assert "0 dups" in txt and "prev 0.0MB (~0.0 B/row)" in txt
    assert len(clean) == 2


def test_gate_frame_empty_frame_no_symbol_col(tmp_path, capsys):
    out = ingest_gate.gate_frame("US", pd.DataFrame(), tmp_path / "n")
    assert out.empty
    assert "0 rows / 0 symbols" in capsys.readouterr().out


def test_audit_skipped_without_vcrud(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_gate, "HERE", tmp_path)
    monkeypatch.setattr("sys.path", list(__import__("sys").path))
    assert ingest_gate._audit("IN", tmp_path / "a.parquet", {}) == "audit-skipped"


def test_audit_ok_with_fake_vcrud(monkeypatch, tmp_path):
    created = []

    class DB:
        def __init__(self, url):
            created.append(("url", url))

        def create_file(self, r):
            created.append(("file", r))

    class Idx:
        def __init__(self, root):
            created.append(("root", root))

        def scan_directory(self, name, patterns):
            created.append(("scan", name, patterns))
            return ["r1", "r2"]

    fake_module(monkeypatch, "db_handler", DatabaseHandler=DB)
    fake_module(monkeypatch, "vcrud_manager", LocalFileIndexer=Idx)
    monkeypatch.setattr("sys.path", list(__import__("sys").path))
    monkeypatch.setenv("VCRUD_DB", "postgresql://x/y")
    monkeypatch.setattr(ingest_gate, "HERE", tmp_path)
    assert ingest_gate._audit("IN", tmp_path / "d" / "a.parquet", {}) == "audited"
    assert ("url", "postgresql://x/y") in created
    assert ("scan", "ingest-IN", [str(Path("d") / "a.parquet")]) in created
    assert [c for c in created if c[0] == "file"] == [("file", "r1"), ("file", "r2")]


def test_report_after_deltas(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ingest_gate, "_audit", lambda m, p, r: "audit-skipped")
    p = tmp_path / "f"
    p.write_bytes(b"\0" * 3_000_000)
    rep = ingest_gate.report_after("IN", p, 1_000_000)
    assert rep == {
        "market": "IN",
        "before_mb": 1.0,
        "after_mb": 3.0,
        "delta_mb": 2.0,
        "audit": "audit-skipped",
    }
    assert "+2.0MB" in capsys.readouterr().out
    rep2 = ingest_gate.report_after("IN", tmp_path / "missing", 2_000_000, verbose=False)
    assert rep2["after_mb"] == 0.0 and rep2["delta_mb"] == -2.0
    assert capsys.readouterr().out == ""


def test_main_selfcheck(monkeypatch, capsys):
    long = _long_dupes()
    fake_module(
        monkeypatch,
        "market_memory",
        _read_long=lambda p: long,
        _ltm_path=lambda m: f"/x/{m}",
    )
    monkeypatch.setattr("sys.argv", ["ingest_gate", "--market", "JP"])
    assert ingest_gate.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("JP: 4 rows, dupes dropped=1")
    assert "'Close': 'float32'" in out
