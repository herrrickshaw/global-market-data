"""bhavcopy_store: seed ingestion, rebuild, empty-store behaviour, vacuum."""

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fresh_import

pytest.importorskip("lmdb")


@pytest.fixture
def bs(tmp_path, monkeypatch):
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path))
    return fresh_import(monkeypatch, "bhavcopy_store")


def _frame(n=5, start=10.0, first="2024-01-01"):
    idx = pd.date_range(first, periods=n, freq="D")
    c = start + np.arange(n, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": np.arange(n) + 1}, index=idx
    )


def _long(hist):
    import frames

    return frames.to_long(hist)


def test_empty_store_accessors(bs):
    assert bs.get("A") is None and bs.get_many(["A"]) == {}
    assert bs.load_all() == {} and bs.symbols() == [] and bs.info() == {}


def test_build_requires_seed(bs):
    with pytest.raises(FileNotFoundError, match="no cleaned_long"):
        bs.build(verbose=False)


def test_build_from_all_seeds_in_cache(bs, capsys):
    _long({"AAA": _frame(), "BBB": _frame()}).to_parquet(bs.CLEANED)
    _long({"UUU": _frame(3)}).to_parquet(bs.CACHE / "cleaned_long_US.parquet")
    n = bs.build()
    assert n == 3
    assert sorted(bs.symbols()) == ["AAA", "BBB", "UUU"]
    out = capsys.readouterr().out
    assert "ingesting 2 market seed(s): cleaned_long.parquet, cleaned_long_US.parquet" in out
    assert "LMDB store built: 3 symbols" in out and "latest bar 2024-01-05" in out


def test_build_from_market_seed_only(bs):
    _long({"UUU": _frame(3)}).to_parquet(bs.CACHE / "cleaned_long_US.parquet")
    assert bs.build(verbose=False) == 1


def test_build_skips_empty_and_tracks_latest_bar(bs):
    hist = {
        "A": _frame(3, first="2024-01-01"),
        "B": _frame(2, first="2024-03-01"),
        "C": None,
        "D": pd.DataFrame(),
    }
    assert bs.build(hist, verbose=False) == 2
    info = bs.info()
    assert info["symbols"] == 2 and info["latest_bar"].startswith("2024-03-02")
    assert info["size_mb"] >= 0


def test_build_latest_bar_is_max_not_last(bs):
    # later symbol has an EARLIER final bar than the first: max must win
    hist = {"A": _frame(3, first="2024-06-01"), "B": _frame(3, first="2024-01-01")}
    bs.build(hist, verbose=False)
    assert bs.info()["latest_bar"].startswith("2024-06-03")


def test_rebuild_replaces_previous_store(bs):
    bs.build({"OLD": _frame()}, verbose=False)
    bs.build({"NEW": _frame()}, verbose=False)
    assert bs.symbols() == ["NEW"] and bs.get("OLD") is None


def test_build_quiet_with_dict_prints_nothing(bs, capsys):
    bs.build({"A": _frame()}, verbose=False)
    assert capsys.readouterr().out == ""


def test_build_verbose_all_empty_shows_unknown_bar(bs, capsys):
    assert bs.build({"A": None}, verbose=True) == 0
    assert "latest bar ?" in capsys.readouterr().out


def test_roundtrip_values_and_load_all_excludes_meta(bs):
    bs.build({"A": _frame(4), "B": _frame(2)}, verbose=False)
    allv = bs.load_all()
    assert set(allv) == {"A", "B"}
    a = allv["A"]
    assert list(a.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert a["Close"].dtype == np.float64 and list(a["Close"]) == [10.0, 11.0, 12.0, 13.0]
    assert a["Volume"].dtype == np.int64 and a.index.is_monotonic_increasing
    got = bs.get_many(["A", "MISSING"])
    assert set(got) == {"A"}


def test_ser_de_handles_missing_and_bad_volume(bs):
    df = _frame(3)
    df["Volume"] = [np.nan, 5, "x"]
    back = bs._de(bs._ser(df))
    assert list(back["Volume"]) == [0, 5, 0]
    no_vol = _frame(2).drop(columns=["Volume"])
    assert "Volume" not in bs._de(bs._ser(no_vol)).columns


def test_de_uses_first_column_when_no_date_column(bs):
    import io

    import pyarrow as pa
    import pyarrow.feather as feather

    d = pd.DataFrame({"when": pd.date_range("2024-01-01", periods=2), "Close": [2.0, 1.0]})
    buf = io.BytesIO()
    feather.write_feather(pa.Table.from_pandas(d, preserve_index=False), buf)
    out = bs._de(buf.getvalue())
    assert out.index.name == "when" and list(out["Close"]) == [2.0, 1.0]


def test_info_without_meta_key(bs):
    import lmdb

    bs.STORE.mkdir(parents=True)
    env = lmdb.open(str(bs.STORE), map_size=1 << 20, subdir=True)
    env.close()
    assert bs.info()["symbols"] == 0 and bs.info()["latest_bar"] is None


def test_vacuum_deletes_only_raw_csvs(bs, capsys):
    (bs.CACHE / "nse").mkdir()
    (bs.CACHE / "bse").mkdir()
    (bs.CACHE / "nse" / "a.csv").write_bytes(b"x" * 1_000_000)
    (bs.CACHE / "nse" / "keep.txt").write_bytes(b"y")
    (bs.CACHE / "bse" / "b.csv").write_bytes(b"x" * 500_000)
    assert bs.vacuum() == 1_500_000
    assert not (bs.CACHE / "nse" / "a.csv").exists() and (bs.CACHE / "nse" / "keep.txt").exists()
    assert "reclaimed 1.5 MB" in capsys.readouterr().out
    assert bs.vacuum(verbose=False) == 0
    assert capsys.readouterr().out == ""


def test_vacuum_without_dirs(bs):
    assert bs.vacuum(verbose=False) == 0
