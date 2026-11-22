"""integrity manifest, pipeline_historical orchestration, full_report (India screeners), bt run_all/main."""

import hashlib
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import integrity
from tests.w4_helpers import fake_module, fresh_import


# ---------------- integrity ----------------
@pytest.fixture
def repo(monkeypatch, tmp_path):
    (tmp_path / "cache_seed" / "serving").mkdir(parents=True)
    (tmp_path / "reference_seed").mkdir()
    files = {
        "a.txt": b"alpha",
        "cache_seed/data.parquet": b"data",
        "cache_seed/serving/volatile.parquet": b"volatile",
        "cache_seed/CHECKSUMS.sha256": b"manifest-itself",
        "reference_seed/ref.csv": b"ref",
    }
    for rel, data in files.items():
        (tmp_path / rel).write_bytes(data)
    tracked = list(files)  # what `git ls-files` reports; tests may append phantom entries

    def run(cmd, capture_output, text, check):
        assert cmd[:3] == ["git", "-C", str(tmp_path)] and cmd[3] == "ls-files"
        return types.SimpleNamespace(stdout="\n".join(tracked) + "\n")

    monkeypatch.setattr(integrity, "HERE", tmp_path)
    monkeypatch.setattr(integrity, "MANIFEST", tmp_path / "cache_seed" / "CHECKSUMS.sha256")
    monkeypatch.setattr(integrity.subprocess, "run", run)
    integrity.tracked = tracked  # noqa: attribute only used by these tests
    yield tmp_path
    del integrity.tracked


def test_tracked_files_skips_manifest_and_volatile_dirs(repo):
    got = integrity._tracked_files()
    assert "cache_seed/CHECKSUMS.sha256" not in got and "cache_seed/serving/volatile.parquet" not in got
    assert got == sorted(got) and "a.txt" in got and "reference_seed/ref.csv" in got
    assert integrity._tracked_files(data_only=True) == ["cache_seed/data.parquet", "reference_seed/ref.csv"]


def test_sha256_streams_large_files(repo):
    big = repo / "big.bin"
    data = b"x" * (3 * (1 << 20) + 17)
    big.write_bytes(data)
    assert integrity._sha256(big) == hashlib.sha256(data).hexdigest()


def test_generate_skips_files_deleted_on_disk(repo):
    integrity.tracked.append("deleted.txt")  # tracked by git, absent from the working tree
    assert integrity.generate(verbose=False) == 3
    assert "deleted.txt" not in integrity.MANIFEST.read_text()
    assert integrity.verify(verbose=False) == 1  # ...which then shows up as drift (not in the manifest)


def test_generate_roundtrips(repo, capsys):
    n = integrity.generate()
    lines = integrity.MANIFEST.read_text().splitlines()
    assert n == len(lines) == 3 and lines == sorted(lines, key=lambda l: l.split("  ", 1)[1])  # sorted by path
    assert lines[0] == f"{hashlib.sha256(b'alpha').hexdigest()}  a.txt"
    assert "wrote CHECKSUMS.sha256: 3 file checksums" in capsys.readouterr().out
    assert integrity.generate(verbose=False) == 3 and capsys.readouterr().out == ""
    assert integrity.generate(data_only=True, verbose=False) == 2


def test_load_manifest_missing_and_malformed(repo):
    assert integrity._load_manifest() == {}
    integrity.MANIFEST.write_text("abc  a.txt\nno-double-space-line\n\ndef  path with  spaces.txt\n")
    assert integrity._load_manifest() == {"a.txt": "abc", "path with  spaces.txt": "def"}  # splits on the first gap only


def test_verify_ok_and_each_failure_kind(repo, capsys):
    assert integrity.verify() == 1 and "run --generate first" in capsys.readouterr().out
    integrity.generate(verbose=False)
    assert integrity.verify() == 0 and "verified 3 files: OK" in capsys.readouterr().out
    (repo / "a.txt").write_bytes(b"tampered")
    assert integrity.verify() == 1
    out = capsys.readouterr().out
    assert "DRIFT DETECTED" in out and "MISMATCH  a.txt" in out
    (repo / "a.txt").write_bytes(b"alpha")
    (repo / "reference_seed" / "ref.csv").unlink()
    assert integrity.verify() == 1 and "MISSING   reference_seed/ref.csv" in capsys.readouterr().out


def test_verify_flags_untracked_by_manifest_and_quiet_mode(repo, capsys):
    integrity.generate(verbose=False)
    (repo / "new.txt").write_bytes(b"new")
    integrity.tracked.append("new.txt")  # tracked by git but not in the manifest
    assert integrity.verify(verbose=False) == 1 and capsys.readouterr().out == ""
    integrity.verify()
    assert "UNTRACKED-BY-MANIFEST  new.txt" in capsys.readouterr().out


def test_verify_data_only_ignores_other_drift(repo):
    integrity.generate(verbose=False)
    (repo / "a.txt").write_bytes(b"tampered")
    assert integrity.verify(data_only=True, verbose=False) == 0  # a.txt is outside the data dirs
    (repo / "cache_seed" / "data.parquet").write_bytes(b"changed")
    assert integrity.verify(data_only=True, verbose=False) == 1


def test_integrity_main_dispatch(repo, monkeypatch):
    monkeypatch.setattr("sys.argv", ["integrity", "--generate"])
    assert integrity.main() == 0 and integrity.MANIFEST.exists()
    monkeypatch.setattr("sys.argv", ["integrity", "--verify"])
    assert integrity.main() == 0
    (repo / "a.txt").write_bytes(b"tampered")
    assert integrity.main() == 1
    monkeypatch.setattr("sys.argv", ["integrity"])
    assert integrity.main() == 1  # default action is verify
    monkeypatch.setattr("sys.argv", ["integrity", "--verify", "--data-only"])
    assert integrity.main() == 0


# ---------------- pipeline_historical ----------------
@pytest.fixture
def ph(monkeypatch):
    return fresh_import(monkeypatch, "pipeline_historical")


class _Run:
    def __init__(self, rc=0, out="", exc=None):
        self.rc, self.out, self.exc, self.cmds = rc, out, exc, []

    def __call__(self, cmd, capture_output, text, timeout):
        self.cmds.append((cmd, timeout))
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(returncode=self.rc, stdout=self.out)


@pytest.mark.parametrize(
    "stage,market,max_stocks,expected_tail",
    [
        ("scan", "IN", 0, ["full_indian_market_scan.py", "--workers", "4"]),
        ("scan", "US", 0, ["full_us_market_scan.py", "--workers", "4", "--min-price", "2"]),
        ("backtest", "US", 0, ["backtest_screeners.py", "--market", "US", "--workers", "4"]),
        ("walkforward", "IN", 0, ["walk_forward_backtest.py", "--liquid", "--workers", "4"]),
        ("pattern", "IN", 0, ["pattern_discovery.py", "--market", "IN"]),
        ("pattern", "IN", 50, ["pattern_discovery.py", "--market", "IN", "--max", "50"]),
        ("sector", "US", 10, ["sector_analysis.py", "--market", "US", "--max", "10"]),
        ("dl", "IN", 0, ["dl_strategy_eval.py", "--market", "IN", "--max", "0"]),  # dl always passes --max
        ("link", "IN", 0, ["sentiment_price_link.py", "--market", "IN"]),
        ("link", "IN", 7, ["sentiment_price_link.py", "--market", "IN", "--max", "7"]),
    ],
)
def test_run_stage_builds_command(ph, monkeypatch, stage, market, max_stocks, expected_tail):
    run = _Run()
    monkeypatch.setattr(ph.subprocess, "run", run)
    res = ph.run_stage(stage, market, max_stocks, 4)
    cmd, timeout = run.cmds[0]
    assert cmd[0] == sys.executable and Path(cmd[1]).name == expected_tail[0] and cmd[2:] == expected_tail[1:]
    assert Path(cmd[1]).parent == ph.HERE and timeout == 10800
    assert res["stage"] == stage and res["ok"] is True and res["seconds"] >= 0


def test_run_stage_outcomes(ph, monkeypatch, capsys):
    out = "\n".join(f"line{i}" for i in range(30))
    monkeypatch.setattr(ph.subprocess, "run", _Run(rc=0, out=out))
    assert ph.run_stage("pattern", "IN", 0, 1)["ok"] is True
    txt = capsys.readouterr().out
    assert "line29" in txt and "line12" in txt and "line11" not in txt  # last 18 lines only
    assert "▶ STAGE: PATTERN  (IN)" in txt and "pattern done in" in txt
    monkeypatch.setattr(ph.subprocess, "run", _Run(rc=1, out="boom"))
    assert ph.run_stage("sector", "IN", 0, 1)["ok"] is False
    monkeypatch.setattr(ph.subprocess, "run", _Run(exc=subprocess.TimeoutExpired("x", 1)))
    assert ph.run_stage("dl", "IN", 0, 1)["ok"] is False and "stage timed out" in capsys.readouterr().out
    monkeypatch.setattr(ph.subprocess, "run", _Run(exc=OSError("nope")))
    assert ph.run_stage("link", "IN", 0, 1)["ok"] is False and "stage error: nope" in capsys.readouterr().out
    monkeypatch.setattr(ph.subprocess, "run", lambda *a, **k: pytest.fail("unknown stages must not run"))
    assert ph.run_stage("bogus", "IN", 0, 1) == {"stage": "bogus", "ok": False}


@pytest.mark.parametrize(
    "argv,expected",
    [([], ["pattern", "sector", "dl", "link"]), (["--stages", "full"], ["scan", "backtest", "pattern", "sector", "dl", "link"]),
     (["--stages", "analytics"], ["pattern", "sector", "dl", "link"]), (["--stages", "scan, link"], ["scan", "link"])],
)
def test_pipeline_historical_main_stage_selection(ph, monkeypatch, capsys, argv, expected):
    seen = []
    monkeypatch.setattr(ph, "run_stage", lambda s, m, mx, w: seen.append((s, m, mx, w)) or {"stage": s, "ok": s != "dl", "seconds": 1.4})
    monkeypatch.setattr("sys.argv", ["ph", "--market", "US", "--max", "9", "--workers", "3", *argv])
    ph.main()
    assert [s[0] for s in seen] == expected and all(s[1:] == ("US", 9, 3) for s in seen)
    out = capsys.readouterr().out
    assert "HISTORICAL PIPELINE SUMMARY" in out and ph.DISCLAIMER in out
    if "dl" in expected:
        assert "❌ dl" in out
    assert "✅ " + expected[0] in out


# ---------------- full_report ----------------
@pytest.fixture
def fr(monkeypatch, tmp_path):
    fake_module(monkeypatch, "screener_kit", MARKETS=["IN", "US", "JP"], load=lambda m, t: {}, screen=None)
    return fresh_import(monkeypatch, "full_report")


def test_others_excludes_india(fr):
    assert fr.OTHERS == ["US", "JP"]


def _fundamentals_sheet(path_glob_result, monkeypatch, fr, sheets):
    monkeypatch.setattr(fr.glob, "glob", lambda p: path_glob_result)

    class XL:
        def __init__(self, path):
            if path not in sheets:
                raise OSError("bad file")
            self.sheet_names = sheets[path][0]

    monkeypatch.setattr(fr.pd, "ExcelFile", XL)
    monkeypatch.setattr(fr.pd, "read_excel", lambda path, sheet_name: sheets[path][1])


def test_india_fundamentals_picks_newest_valid_sheet_and_ccc(fr, monkeypatch):
    sheet = pd.DataFrame(
        {"Symbol": [" TCS ", "", "INFY"], "Piotroski_Score": [8, 9, 3], "Piotroski_Strong": ["yes", "YES", "no"], "CoffeeCan": ["pass", "PASS", "fail"]}
    )
    _fundamentals_sheet(
        ["/old.xlsx", "/new_nosheet.xlsx", "/bad.xlsx"], monkeypatch, fr,
        {"/old.xlsx": (["Fundamentals"], sheet), "/new_nosheet.xlsx": (["Other"], None)},
    )
    fake_module(monkeypatch, "screener_in", ccc_map=lambda: {"TCS": 12.0, "NEW": 5.0})
    f = fr._india_fundamentals()
    assert f["TCS"] == {"piotroski_score": 8, "piotroski_strong": True, "coffee_can": True, "ccc": 12.0}
    assert f["INFY"]["piotroski_strong"] is False and f["INFY"]["coffee_can"] is False
    assert f["NEW"] == {"ccc": 5.0} and "" not in f  # blank symbol skipped, CCC-only symbol added
    # glob results are visited newest first (reverse-sorted names)
    seen = []
    monkeypatch.setattr(fr.glob, "glob", lambda p: seen.append(p) or ["/a.xlsx", "/b.xlsx"])
    monkeypatch.setattr(fr.pd, "ExcelFile", lambda p: (_ for _ in ()).throw(OSError(p)))
    fr._india_fundamentals()
    assert "indian_full_scan" in seen[0]


def test_india_fundamentals_no_files_and_ccc_failure(fr, monkeypatch):
    monkeypatch.setattr(fr.glob, "glob", lambda p: [])
    fake_module(monkeypatch, "screener_in", ccc_map=lambda: 1 / 0)
    assert fr._india_fundamentals() == {}


def test_enrich_authenticated_merges_or_skips(fr, monkeypatch, capsys):
    seen = {}
    auth = fake_module(
        monkeypatch, "screener_in_auth", session=lambda: None,
        fundamentals_batch=lambda syms, verbose: seen.update(syms=syms) or {"A": {"roe": 20}, "Z": {"roe": 5}},
    )
    fmap = {"A": {"ccc": 1.0}}
    out = fr._enrich_authenticated(fmap, ["A", "B", "C"], limit=2)
    assert seen["syms"] == ["A", "B"] and out["A"] == {"ccc": 1.0, "roe": 20} and out["Z"] == {"roe": 5}
    assert "enriched 2 India symbols" in capsys.readouterr().out

    class AuthError(Exception):
        pass

    auth.session = lambda: (_ for _ in ()).throw(AuthError("no creds"))
    assert fr._enrich_authenticated({"A": {}}, ["A"]) == {"A": {}}
    assert "skipped (AuthError)" in capsys.readouterr().out


def _stock_frame(n=60):
    return pd.DataFrame({"Close": np.linspace(10, 20, n)}, index=pd.date_range("2024-01-01", periods=n))


class _Mod:
    def __init__(self, passing):
        self.passing = passing

    def screen(self, sd):
        class R:
            passed = sd.symbol in self.passing
            score = 5.0 if sd.symbol == "A" else 9.0

            def row(self_inner):
                return {"Symbol": sd.symbol, "Score": self_inner.score}

        return R() if sd.symbol != "NONE" else None


def test_india_all_screeners_runs_every_strategy(fr, monkeypatch):
    monkeypatch.setattr(fr.kit, "load", lambda m, t: {"A": _stock_frame(), "B": _stock_frame(), "NONE": _stock_frame()})
    monkeypatch.setattr(fr, "_india_fundamentals", lambda: {"A": {"piotroski_strong": True, "coffee_can": False}, "B": {"piotroski_strong": False, "coffee_can": True}})
    monkeypatch.setattr(fr, "_enrich_authenticated", lambda fmap, syms, limit: fmap)
    monkeypatch.setattr(fr.st, "STRATEGIES", {"s1": _Mod({"A", "B"}), "s2": _Mod(set())})
    fake_module(monkeypatch, "liquidity", liquid_symbols=lambda m, t: ["B", "A"])
    monkeypatch.setattr(fr.liq, "annotate", lambda df: df.assign(Liquidity="High"))
    out = fr.india_all_screeners()
    assert list(out["s1"]["Symbol"]) == ["B", "A"] and set(out["s1"]["Market"]) == {"IN"}  # sorted by score desc, tagged
    assert out["s2"].empty
    assert out["_scan_piotroski_strong"] == ["A"] and out["_scan_coffee_can"] == ["B"]


def test_india_all_screeners_auth_options(fr, monkeypatch):
    data = {"A": _stock_frame(), "B": _stock_frame()}
    monkeypatch.setattr(fr.kit, "load", lambda m, t: data)
    monkeypatch.setattr(fr, "_india_fundamentals", lambda: {})
    calls = []
    monkeypatch.setattr(fr, "_enrich_authenticated", lambda fmap, syms, limit: calls.append((list(syms), limit)) or fmap)
    monkeypatch.setattr(fr.st, "STRATEGIES", {})
    fake_module(monkeypatch, "liquidity", liquid_symbols=lambda m, t: ["B", "ZZZ", "A"])
    out = fr.india_all_screeners(auth_limit=7)
    assert calls == [(["B", "A"], 7)] and out == {}  # liquid order kept, symbols without data dropped
    calls.clear()
    fr.india_all_screeners(use_auth=False)
    assert calls == []
    fake_module(monkeypatch, "liquidity", liquid_symbols=lambda m, t: 1 / 0)
    fr.india_all_screeners()
    assert calls == [(["A", "B"], 300)]  # liquidity index unavailable -> universe order


def test_top150_others_filters_artifacts_and_limits(fr, monkeypatch, capsys):
    def load(m, t):
        n = 300
        up = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 100 + 0.5 * np.arange(n) + (np.arange(n) % 2) * 2.0, "Volume": 1e6}, index=pd.date_range("2023-01-01", periods=n))
        return {f"{m}{i}": up * (1 + 0.1 * i) for i in range(3)} if m == "US" else {}

    monkeypatch.setattr(fr.kit, "load", load)
    monkeypatch.setattr(fr.liq, "annotate", lambda df: df.assign(Liquidity="High"))
    out = fr.top150_others(top=2)
    assert len(out) == 2 and out["ret_126"].is_monotonic_decreasing and set(out["Liquidity"]) == {"High"}
    txt = capsys.readouterr().out
    assert "US: 3 pass momentum" in txt and "JP: 0 pass momentum" in txt
    monkeypatch.setattr(fr.kit, "load", lambda m, t: {})
    assert fr.top150_others().empty


def test_top150_others_drops_bad_prints(fr, monkeypatch):
    n = 300
    base = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 100 + 0.5 * np.arange(n) + (np.arange(n) % 2) * 2.0, "Volume": 1e6}, index=pd.date_range("2023-01-01", periods=n))
    glitch = base.copy()
    glitch["Close"] = glitch["Close"].where(np.arange(n) < n - 126, glitch["Close"] * 20)  # 126-bar return > 900%
    glitch["High"] = glitch["Close"]
    monkeypatch.setattr(fr.kit, "load", lambda m, t: {"OK": base, "GLITCH": glitch} if m == "US" else {})
    monkeypatch.setattr(fr.liq, "annotate", lambda df: df)
    out = fr.top150_others()
    assert "GLITCH" not in set(out["Symbol"])


# ---------------- .bt_bin/backtest.py: run_all + main ----------------
_BT = Path(__file__).resolve().parent.parent / ".bt_bin" / "backtest.py"


@pytest.fixture
def bt():
    spec = importlib.util.spec_from_file_location("bt_engine_w4", _BT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bt_run_all_sorts_by_edge(bt, monkeypatch, capsys):
    edges = {"golden_state": 1.0, "golden_crossover": 5.0, "near_high": -2.0, "rsi_oversold": 3.0, "momentum": 0.0}
    monkeypatch.setattr(bt, "backtest_screen", lambda m, s, h: {"screen": s, "trades": 1, "avg_ret": 1.0, "mkt_ret": 0.0, "edge": edges[s], "win_rate": 0.5})
    df = bt.run_all("IN", horizon=10)
    assert list(df["screen"]) == ["golden_crossover", "rsi_oversold", "golden_state", "momentum", "near_high"]
    out = capsys.readouterr().out
    assert "Walk-forward backtest — IN (10d hold)" in out and "edge = screen avg forward return" in out
    bt.run_all("IN", verbose=False)
    assert capsys.readouterr().out == ""


def test_bt_main_single_screen_and_all(bt, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(bt, "backtest_screen", lambda m, s, h: seen.update(a=(m, s, h)) or {"trades": 4, "avg_ret": 3.5, "mkt_ret": 1.0, "edge": 2.5, "win_rate": 0.75})
    monkeypatch.setattr("sys.argv", ["bt", "--screen", "near_high", "--market", "US", "--horizon", "5"])
    assert bt.main() == 0 and seen["a"] == ("US", "near_high", 5)
    assert "near_high on US: 4 trades, avg 3.5% vs mkt 1.0% → edge 2.5%, win 75%" in capsys.readouterr().out
    called = []
    monkeypatch.setattr(bt, "run_all", lambda m, h: called.append((m, h)))
    monkeypatch.setattr("sys.argv", ["bt", "--all", "--screen", "near_high"])
    assert bt.main() == 0 and called == [("IN", 21)]  # --all wins over --screen
    monkeypatch.setattr("sys.argv", ["bt"])
    bt.main()
    assert called[-1] == ("IN", 21)  # no screen -> run everything
