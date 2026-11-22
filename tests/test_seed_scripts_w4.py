"""Seed builders / importers / schedulers: build_market_seeds, bulk_seed, update_bhavcopy_daily,
vcrud_precheck, vcrud_index_ltm, warm_india_cache."""

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import frames
from tests.w4_helpers import fake_module, fresh_import


def _hist(n=5, base=10.0, end=None):
    end = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
    idx = pd.date_range(end=end, periods=n, freq="D")
    c = base + np.arange(n, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 100.0}, index=idx)


# ---------------- build_market_seeds ----------------
@pytest.fixture
def bms(monkeypatch, tmp_path):
    m = fresh_import(monkeypatch, "build_market_seeds")
    monkeypatch.setattr(m, "OHLC_DIR", tmp_path / "ohlc")
    return m


@pytest.mark.parametrize(
    "fname,market",
    [
        ("AAPL.parquet", "US"),
        ("RELIANCE.NS.parquet", "IN"),
        ("X.BO.parquet", "IN"),
        ("BP.L.parquet", "UK"),
        ("MC.PA.parquet", "FR"),
        ("SAP.DE.parquet", "DE"),
        ("7203.T.parquet", "JP"),
        ("005930.KS.parquet", "KR"),
        ("035720.KQ.parquet", "KR"),
        ("D05.SI.parquet", "SG"),
        ("0700.HK.parquet", "HK"),
        ("BHP.AX.parquet", "AU"),
        ("RY.TO.parquet", "CA"),
        ("NESN.SW.parquet", "CH"),
        ("ENI.MI.parquet", "IT"),
        ("ASML.AS.parquet", "NL"),
        ("VOLV-B.ST.parquet", "SE"),
        ("ODD.ZZ.parquet", None),
        ("BRK.B.parquet", None),
        ("AAPL", "US"),
    ],
)
def test_market_of(bms, fname, market):
    assert bms._market_of(fname) == market


def test_build_requires_cache_dir(bms, tmp_path):
    with pytest.raises(FileNotFoundError):
        bms.build(out=tmp_path / "o")


def test_build_groups_by_market_filters_window_and_cleans(bms, tmp_path, capsys):
    bms.OHLC_DIR.mkdir()
    _hist(5, 10.0).to_parquet(bms.OHLC_DIR / "AAPL.parquet")
    _hist(5, 20.0).to_parquet(bms.OHLC_DIR / "MSFT.parquet")
    _hist(3, 30.0).to_parquet(bms.OHLC_DIR / "RELIANCE.NS.parquet")
    _hist(3, 40.0).to_parquet(bms.OHLC_DIR / "ODD.ZZ.parquet")  # unknown suffix -> skipped
    pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex([pd.Timestamp.today()])).to_parquet(
        bms.OHLC_DIR / "NOCLOSE.parquet"
    )
    (bms.OHLC_DIR / "BROKEN.parquet").write_text("junk")
    pd.DataFrame(columns=["Close"]).to_parquet(bms.OHLC_DIR / "EMPTY.parquet")
    old = _hist(5, 50.0, end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1000))
    old.to_parquet(bms.OHLC_DIR / "OLD.parquet")  # entirely outside the window
    out = tmp_path / "seeds"
    summary = bms.build(days=400, out=out)
    assert set(summary) == {"US", "IN"}
    assert (
        summary["US"]["symbols"] == 2
        and summary["US"]["rows"] == 10
        and summary["US"]["file"] == "cleaned_long_US.parquet"
    )
    seed = frames.read_seed(out / "cleaned_long_US.parquet")
    assert set(seed) == {"AAPL", "MSFT"}  # OLD dropped by cutoff
    assert set(frames.read_seed(out / "cleaned_long_IN.parquet")) == {
        "RELIANCE.NS"
    }  # keeps the yfinance suffix
    txt = capsys.readouterr().out
    assert "scanning 8 cached tickers" in txt and "US:     2 symbols" in txt


def test_build_cutoff_boundary_and_quiet(bms, tmp_path, capsys):
    bms.OHLC_DIR.mkdir()
    today = pd.Timestamp.today().normalize()
    d = pd.concat(
        [
            _hist(1, 10.0, end=today - pd.Timedelta(days=11)),
            _hist(1, 11.0, end=today - pd.Timedelta(days=10)),
            _hist(1, 12.0, end=today - pd.Timedelta(days=9)),
        ]
    )
    d.to_parquet(bms.OHLC_DIR / "AAPL.parquet")
    summary = bms.build(days=10, out=tmp_path / "o", verbose=False)
    assert summary["US"]["rows"] == 2  # index >= cutoff is inclusive
    assert capsys.readouterr().out == ""


def test_build_without_cleaner_keeps_rows_as_is(bms, tmp_path, monkeypatch):
    monkeypatch.setattr(bms, "clean_ohlcv", None)
    bms.OHLC_DIR.mkdir()
    bad = _hist(3)
    bad["Close"] = -1.0
    bad.to_parquet(bms.OHLC_DIR / "AAPL.parquet")
    assert bms.build(out=tmp_path / "o", verbose=False)["US"]["rows"] == 3  # nothing cleaned


def test_build_cleaning_can_remove_everything(bms, tmp_path):
    bms.OHLC_DIR.mkdir()
    bad = _hist(3)
    bad["Close"] = -1.0
    bad.to_parquet(bms.OHLC_DIR / "AAPL.parquet")
    assert (
        bms.build(out=tmp_path / "o2", verbose=False) == {}
    )  # negative prices cleaned away -> no seed written
    assert not (tmp_path / "o2" / "cleaned_long_US.parquet").exists()


# ---------------- bulk_seed ----------------
@pytest.fixture
def bulk(monkeypatch, tmp_path):
    import bulk_seed

    mm = bulk_seed.mm
    seed = tmp_path / "seed"
    (seed / "ltm").mkdir(parents=True)
    monkeypatch.setattr(mm, "SEED_DIR", seed)
    monkeypatch.setattr(mm, "LTM_DIR", seed / "ltm")
    return bulk_seed


def test_normalise_renames_cleans_and_prefixes(bulk):
    df = pd.DataFrame(
        {
            "TckrSymb": [" nse:tcs ", "BSE:INFY", "bad", None],
            "TradDt": ["2024-01-02", "2024-01-02", "not a date", "2024-01-02"],
            "OpnPric": ["1", "2", "3", "4"],
            "HghPric": [1, 2, 3, 4],
            "LwPric": [1, 2, 3, 4],
            "ClsPric": ["10", "20", "x", "40"],
            "TtlTradgVol": [5, 6, 7, 8],
            "TotTrdQty": [50, 60, 70, 80],
        }
    )
    out = bulk._normalise(df)
    assert list(out["Symbol"]) == [
        "TCS",
        "INFY",
    ]  # prefixes stripped; bad date / close / symbol rows dropped
    assert list(out["Close"]) == [10.0, 20.0] and list(out["Volume"]) == [50, 60]
    assert list(out.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", "Symbol"]


def test_normalise_missing_columns_returns_empty_schema(bulk):
    out = bulk._normalise(pd.DataFrame({"date": ["2024-01-01"], "close": [1.0]}))
    assert out.empty and list(out.columns) == [
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Symbol",
    ]


def _csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def _row(sym, date, close):
    return {
        "symbol": sym,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 10,
    }


def test_from_csv_dir_dedups_and_commits(bulk, monkeypatch, tmp_path):
    d = tmp_path / "csvs"
    d.mkdir()
    day = pd.Timestamp.today().normalize()
    _csv(
        d / "a.csv",
        [
            _row("tcs", day.strftime("%Y-%m-%d"), 1.0),
            _row("tcs", (day - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), 2.0),
        ],
    )
    _csv(
        d / "b.csv",
        [_row("TCS", day.strftime("%Y-%m-%d"), 9.0), _row("INFY", day.strftime("%Y-%m-%d"), 3.0)],
    )
    (d / "bad.csv").write_text("")  # unparseable file skipped
    _csv(d / "junk.csv", [{"x": 1}])  # no usable columns
    got = {}
    monkeypatch.setattr(
        bulk.mm, "commit", lambda m, long, verbose: got.update(m=m, long=long) or {"market": m}
    )
    res = bulk.from_csv("IN", str(d), verbose=False)
    assert res == {"market": "IN", "rows_imported": 3}
    assert len(got["long"]) == 3 and set(got["long"]["Symbol"]) == {
        "TCS",
        "INFY",
    }  # duplicate (Date, Symbol) dropped, first kept
    assert (
        got["long"]
        .loc[(got["long"]["Symbol"] == "TCS") & (got["long"]["Date"] == day), "Close"]
        .iloc[0]
        == 1.0
    )


def test_from_csv_single_file_real_commit_and_errors(bulk, tmp_path, capsys):
    f = tmp_path / "x.csv"
    day = pd.Timestamp.today().normalize().strftime("%Y-%m-%d")
    _csv(f, [_row("tcs", day, 5.0)])
    res = bulk.from_csv("IN", str(f))
    assert res["rows_imported"] == 1 and res["ltm_symbols"] == 1
    assert "csv import: 1 file(s) → 1 rows, 1 symbols" in capsys.readouterr().out
    assert set(bulk.mm.read("IN", "ltm")) == {"TCS"}
    assert bulk.from_csv("IN", str(tmp_path / "missing")) == {
        "market": "IN",
        "error": f"no CSV at {tmp_path / 'missing'}",
    }
    empty = tmp_path / "e"
    empty.mkdir()
    assert "no CSV" in bulk.from_csv("IN", str(empty))["error"]
    junk = tmp_path / "junk.csv"
    _csv(junk, [{"x": 1}])
    assert bulk.from_csv("IN", str(junk)) == {
        "market": "IN",
        "error": "no parseable rows (check columns)",
    }


def test_from_yfinance_strips_suffix_and_commits(bulk, monkeypatch):
    fake_module(
        monkeypatch,
        "stock_utils",
        bulk_download=lambda tickers, **kw: {t: _hist(3) for t in tickers[:2]} | {"RAW": _hist(2)},
    )
    seen = {}
    monkeypatch.setattr(
        bulk.mm,
        "commit",
        lambda m, long, verbose: seen.update(syms=set(long["Symbol"])) or {"market": m},
    )
    res = bulk.from_yfinance("IN", symbols=["TCS", "INFY", "WIPRO"], suffix=".NS", verbose=False)
    assert seen["syms"] == {"TCS", "INFY", "RAW"}  # suffix removed only where present
    assert res == {"market": "IN", "fetched": 3}


def test_from_yfinance_argument_passing_and_empty_cases(bulk, monkeypatch):
    kw = {}
    fake_module(
        monkeypatch,
        "stock_utils",
        bulk_download=lambda tickers, **k: kw.update(t=tickers, k=k) or {},
    )
    assert bulk.from_yfinance(
        "IN", symbols=["A"], period="10y", batch=7, suffix=".BO", verbose=False
    ) == {"market": "IN", "fetched": 0}
    assert kw["t"] == ["A.BO"] and kw["k"] == {
        "period": "10y",
        "batch_size": 7,
        "min_bars": 1,
        "verbose": False,
    }
    assert bulk.from_yfinance("IN", symbols=[], verbose=False) == {
        "market": "IN",
        "error": "no symbols to backfill",
    }  # no LTM either
    bulk.mm.commit("IN", frames.to_long({"LTMSYM": _hist(3)}), verbose=False)
    kw.clear()
    bulk.from_yfinance("IN", verbose=False)  # default symbol list = tracked LTM symbols
    assert kw["t"] == ["LTMSYM.NS"]


def test_bulk_main(bulk, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["b", "--source", "csv"])
    assert bulk.main() == 1 and "--path required" in capsys.readouterr().out
    seen = {}
    monkeypatch.setattr(bulk, "from_csv", lambda m, p: seen.update(csv=(m, p)) or {"ok": 1})
    monkeypatch.setattr("sys.argv", ["b", "--source", "csv", "--path", "/x", "--market", "US"])
    assert (
        bulk.main() == 0
        and seen["csv"] == ("US", "/x")
        and "result: {'ok': 1}" in capsys.readouterr().out
    )
    monkeypatch.setattr(bulk.mm, "read", lambda m, tier: {"A": 1, "B": 2, "C": 3})
    monkeypatch.setattr(
        bulk,
        "from_yfinance",
        lambda m, symbols, period, suffix: seen.update(yf=(m, symbols, period, suffix)) or {},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["b", "--source", "yfinance", "--limit", "2", "--period", "10y", "--suffix", ".BO"],
    )
    assert bulk.main() == 0 and seen["yf"] == ("IN", ["A", "B"], "10y", ".BO")
    monkeypatch.setattr("sys.argv", ["b", "--source", "yfinance"])
    bulk.main()
    assert seen["yf"][1] == ["A", "B", "C"]


# ---------------- update_bhavcopy_daily ----------------
@pytest.fixture
def ubd(monkeypatch):
    fake_module(
        monkeypatch, "bhavcopy_history", fetch_history=lambda n_days, verbose: {"A": 1, "B": 2}
    )
    fake_module(monkeypatch, "bhavcopy_store", build=lambda verbose: 99)
    return fresh_import(monkeypatch, "update_bhavcopy_daily")


def test_update_uses_incremental_store_then_full_build(ubd, monkeypatch, capsys):
    fake_module(monkeypatch, "datalink", build_store_incremental=lambda verbose: {"symbols": 7})
    assert ubd.update(n_days=10) == 7
    out = capsys.readouterr().out
    assert (
        "fetching up to 10 days back" in out
        and "cache refreshed: 2 India symbols" in out
        and "(7 symbols touched)" in out
    )
    fake_module(monkeypatch, "datalink", build_store_incremental=lambda verbose: {"mode": "noop"})
    assert ubd.update(verbose=False) == 0  # no 'symbols' key -> 0
    fake_module(monkeypatch, "datalink", build_store_incremental=lambda verbose: 1 / 0)
    assert ubd.update(verbose=False) == 99  # incremental failed -> full rebuild
    assert capsys.readouterr().out == ""


def test_update_passes_days_to_history(monkeypatch):
    seen = {}
    fake_module(
        monkeypatch,
        "bhavcopy_history",
        fetch_history=lambda n_days, verbose: seen.update(d=n_days, v=verbose) or {},
    )
    fake_module(monkeypatch, "bhavcopy_store", build=lambda verbose: 0)
    fake_module(monkeypatch, "datalink", build_store_incremental=lambda verbose: {"symbols": 1})
    m = fresh_import(monkeypatch, "update_bhavcopy_daily")
    m.update(n_days=123, verbose=False)
    assert seen == {"d": 123, "v": False}


def test_update_main_exit_codes(ubd, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ubd, "update", lambda n_days, verbose: calls.append((n_days, verbose)) or 1)
    monkeypatch.setattr("sys.argv", ["u"])
    assert ubd.main() == 0 and calls == [(400, True)]
    monkeypatch.setattr("sys.argv", ["u", "--days", "30", "--quiet"])
    ubd.main()
    assert calls[-1] == (30, False)
    monkeypatch.setattr(ubd, "update", lambda n_days, verbose: 1 / 0)
    assert ubd.main() == 1
    assert "bhavcopy daily update FAILED: division by zero" in capsys.readouterr().err


# ---------------- vcrud_precheck ----------------
@pytest.fixture
def pre(monkeypatch):
    return fresh_import(monkeypatch, "vcrud_precheck")


def _git_fake(monkeypatch, pre, branches, trees):
    def run(cmd, capture_output, text):
        assert cmd[0] == "git"
        if cmd[1] == "for-each-ref":
            out = "\n".join(branches) + "\n"
        else:
            out = trees.get(cmd[-1], "")
        return types.SimpleNamespace(stdout=out)

    monkeypatch.setattr(pre.subprocess, "run", run)


def test_branches_and_blobs_parsing(pre, monkeypatch):
    tree = "100644 blob aaa111   1500\ta/b.txt\n040000 tree bbb222       -\tdir\n100644 blob ccc333      -\tc.txt\nshort line\n"
    _git_fake(monkeypatch, pre, [" main ", "", "dev"], {"main": tree})
    assert pre.local_branches() == ["main", "dev"]
    assert list(pre.blobs("main")) == [
        ("a/b.txt", "aaa111", 1500),
        ("c.txt", "ccc333", 0),
    ]  # '-' size -> 0, trees skipped


def test_scan_finds_cross_branch_duplicates_and_waste(pre, monkeypatch):
    t1 = "100644 blob same     100\tkeep.txt\n100644 blob only1     50\tx.txt\n"
    t2 = "100644 blob same     100\tcopy.txt\n"
    t3 = "100644 blob same     100\tthird.txt\n"
    _git_fake(monkeypatch, pre, ["a", "b", "c"], {"a": t1, "b": t2, "c": t3})
    r = pre.scan()
    assert r["branches"] == ["a", "b", "c"] and list(r["duplicates"]) == ["same"]
    assert len(r["duplicates"]["same"]) == 3
    assert r["wasted_bytes"] == 200  # size * (copies - 1)
    _git_fake(monkeypatch, pre, ["a"], {"a": t1})
    assert pre.scan()["duplicates"] == {} and pre.scan()["wasted_bytes"] == 0
    # same blob twice on ONE branch also counts as duplicated content
    _git_fake(monkeypatch, pre, ["a"], {"a": "100644 blob s 10\tp1\n100644 blob s 10\tp2\n"})
    assert pre.scan()["wasted_bytes"] == 10


def test_main_warn_only_vs_strict(pre, monkeypatch, capsys):
    dup = "100644 blob same     3000000\tbig.bin\n"
    _git_fake(monkeypatch, pre, ["a", "b"], {"a": dup, "b": dup})
    assert pre.main() == 0  # warn-only by default
    out = capsys.readouterr().out
    assert (
        "1 duplicated blobs across 2 local branches; 3.0 MB" in out
        and "big.bin" in out
        and "[a, b]" in out
    )
    monkeypatch.setattr(pre, "WARN_ONLY", False)
    assert pre.main() == 1 and "vCRUD precheck FAILED" in capsys.readouterr().err
    _git_fake(monkeypatch, pre, ["a"], {"a": dup})
    assert pre.main() == 0  # strict but nothing duplicated


def test_main_lists_top_five_largest(pre, monkeypatch, capsys):
    lines = "".join(f"100644 blob s{i} {1000 * (i + 1)}\tf{i}\n" for i in range(7))
    _git_fake(monkeypatch, pre, ["a", "b"], {"a": lines, "b": lines})
    pre.main()
    out = capsys.readouterr().out
    assert (
        out.count("×2") == 5 and "f6" in out and "f2" in out and "f1" not in out.split("\n", 1)[1]
    )


def test_warn_only_flag_follows_env(monkeypatch):
    monkeypatch.setenv("VCRUD_STRICT", "1")
    assert fresh_import(monkeypatch, "vcrud_precheck").WARN_ONLY is False
    monkeypatch.delenv("VCRUD_STRICT")
    assert fresh_import(monkeypatch, "vcrud_precheck").WARN_ONLY is True


# ---------------- vcrud_index_ltm ----------------
class Rec:
    def __init__(self, path, size, comp, checksum):
        self.path, self.size_bytes, self.compressed_size, self.checksum = path, size, comp, checksum
        self.compression_ratio = comp / size


def _vcrud(monkeypatch, records, fail_on=(), stats=None):
    created = []

    class DB:
        def __init__(self, url):
            created.append(("db", url))

        def create_file(self, r):
            if r.path in fail_on:
                raise RuntimeError("duplicate key " * 10)
            created.append(("file", r.path))

        def get_branch_stats(self, b):
            if stats is None:
                raise RuntimeError
            return stats

    class Idx:
        def __init__(self, root):
            created.append(("root", root))

        def scan_directory(self, branch, patterns):
            created.append(("scan", branch, patterns))
            return records

    fake_module(monkeypatch, "db_handler", DatabaseHandler=DB)
    fake_module(monkeypatch, "vcrud_manager", LocalFileIndexer=Idx)
    return created


def test_vcrud_index_ltm_report(monkeypatch, capsys):
    recs = [
        Rec("cache_seed/ltm/IN.parquet", 4_000_000, 3_000_000, "a" * 40),
        Rec("cache_seed/ltm/US.parquet", 8_000_000, 6_000_000, "b" * 40),
        Rec("cache_seed/ltm/UK.parquet", 8_000_000, 6_000_000, "b" * 40),
    ]
    created = _vcrud(monkeypatch, recs, fail_on={"cache_seed/ltm/IN.parquet"}, stats={"files": 3})
    monkeypatch.setenv("DB", "postgresql://x/y")
    monkeypatch.setenv("BRANCH", "feature")
    m = fresh_import(monkeypatch, "vcrud_index_ltm")
    assert m.main() == 0
    assert ("db", "postgresql://x/y") in created and (
        "scan",
        "feature",
        ["cache_seed/ltm/*.parquet"],
    ) in created
    out = capsys.readouterr().out
    assert "indexed 2/3 LTM files on branch 'feature'" in out
    assert "skip cache_seed/ltm/IN.parquet: " + ("duplicate key " * 10)[:60] in out
    assert "raw: 20.0 MB | gzip-on-parquet: 15.0 MB | extra gzip ratio: 25.0%" in out
    assert out.index("US  ") < out.index("IN  ")  # largest first
    assert "⚠ dup: cache_seed/ltm/UK.parquet == cache_seed/ltm/US.parquet" in out
    assert (
        "distinct checksums: 2 | duplicate files: 1" in out
        and "VCRUD branch stats: {'files': 3}" in out
    )


def test_vcrud_index_ltm_no_records_and_stats_failure(monkeypatch, capsys):
    _vcrud(monkeypatch, [])
    m = fresh_import(monkeypatch, "vcrud_index_ltm")
    assert m.main() == 1 and "no LTM parquet files found" in capsys.readouterr().out
    _vcrud(monkeypatch, [Rec("p/IN.parquet", 10, 5, "c" * 40)], stats=None)
    m = fresh_import(monkeypatch, "vcrud_index_ltm")
    assert m.main() == 0 and "branch stats" not in capsys.readouterr().out


# ---------------- warm_india_cache (module-level script) ----------------
def test_warm_india_cache_script(monkeypatch, capsys):
    warmed = {}
    fake_module(
        monkeypatch,
        "market_data_cache",
        warm_cache=lambda tickers, period_years, index_tickers: warmed.update(
            t=tickers, p=period_years, i=index_tickers
        ),
    )
    fake_module(monkeypatch, "nsepython", nse_eq_symbols=lambda: ["TCS", "INFY"])

    csv = Path(pytest.importorskip("tempfile").mkdtemp()) / "bse.csv"
    pd.DataFrame({"SctySrs": ["A", "ZZ", "B"], "TckrSymb": [" TCS ", "500001", "500002"]}).to_csv(
        csv, index=False
    )

    class BSE:
        def __init__(self, download_folder):
            pass

        def bhavcopyReport(self, d):
            return csv

    fake_module(monkeypatch, "bse", BSE=BSE)
    fresh_import(monkeypatch, "warm_india_cache")
    assert warmed["t"] == [
        "INFY.NS",
        "TCS.NS",
        "500002.BO",
    ]  # BSE-only: ZZ group excluded, dual-listed TCS dropped
    assert warmed["p"] == 5 and warmed["i"] == ["^NSEI", "^BSESN"]
    out = capsys.readouterr().out
    assert (
        "NSE EQ: 2" in out
        and "BSE-only (not on NSE): 1" in out
        and "Total tickers to warm: 3 (2 NSE + 1 BSE-only)" in out
    )


def test_warm_india_cache_bse_failure_still_warms_nse(monkeypatch, capsys):
    warmed = {}
    fake_module(
        monkeypatch,
        "market_data_cache",
        warm_cache=lambda tickers, period_years, index_tickers: warmed.update(t=tickers),
    )
    fake_module(monkeypatch, "nsepython", nse_eq_symbols=lambda: ["TCS"])
    monkeypatch.setitem(sys.modules, "bse", None)  # ImportError
    fresh_import(monkeypatch, "warm_india_cache")
    assert warmed["t"] == ["TCS.NS"] and "BSE fetch failed" in capsys.readouterr().out
