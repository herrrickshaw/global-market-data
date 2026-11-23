"""generate_repo_index (real throw-away git repo) and inventory_all_data (tmp tree)."""

import json
import shutil
import subprocess

import pytest

from tests.scan_loader_w4 import load


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"], "HOME": str(cwd)})


@pytest.fixture
def gri(monkeypatch, tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    return load(monkeypatch, "generate_repo_index", tmp_path)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "app.py").write_text("print(1)\n")
    (r / "cache_seed").mkdir()
    (r / "cache_seed" / "a.parquet").write_text("x")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "feat: base")
    _git(r, "checkout", "-q", "-b", "feature/alpha")
    (r / "model_alpha.py").write_text("m")
    (r / "notes").write_text("n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "feat: alpha thing")
    _git(r, "checkout", "-q", "main")
    return r


@pytest.mark.parametrize(
    "branch,msg,expected",
    [
        ("feature/x", "m", "Feature: x"), ("fix/y", "m", "Bug fix: y"), ("refactor/z", "m", "Refactor: z"),
        pytest.param("hotfix/h", "m", "Hotfix: h", marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason="generate_repo_index._determine_branch_purpose tests 'fix/' before 'hotfix/', and 'fix/' is a substring of 'hotfix/h', so hotfix branches are labelled 'Bug fix'")), ("release/1.0", "m", "Release: 1.0"), ("main", "m", "Main production branch"),
        ("develop", "m", "Development integration branch"), ("misc", "feat: shiny thing", "Feature: shiny"),
        ("misc", "fix: crash now", "Fix: crash"), ("misc", "chore", "misc"),
    ],
)
def test_branch_purpose(gri, branch, msg, expected):
    g = gri.RepositoryIndexGenerator(".")
    assert g._determine_branch_purpose(branch, msg) == expected


def test_pure_helpers(gri):
    g = gri.RepositoryIndexGenerator(".")
    assert g._analyze_file_types(["a.py", "b.py", "c.csv", "Makefile"]) == {".py": 2, ".csv": 1, "no_ext": 1}
    assert list(g._analyze_file_types(["a.py", "b.csv", "c.csv"])) == [".csv", ".py"]  # most common first
    assert g._find_dependencies(["x/cache_seed/a", "fundamentals_x.py", "ohlc.py", "ModelA.py"]) == ["cache_seed", "fundamentals", "market_data", "ml_models"]
    assert g._find_dependencies(["readme.md"]) == []
    assert g._get_merge_targets("main") == [] and g._get_merge_targets("feature/a") == ["develop", "main"]
    assert g._get_merge_targets("hotfix/a") == ["main", "develop"] and g._get_merge_targets("other") == ["main"]
    assert g._find_main_branch(["dev", "master", "trunk"]) == "master" and g._find_main_branch(["dev"]) == "dev" and g._find_main_branch([]) == "unknown"
    assert g._find_main_branch(["main", "master"]) == "main"


def test_index_against_real_git_repo(gri, repo, tmp_path):
    g = gri.RepositoryIndexGenerator(str(repo))
    assert sorted(g.get_all_branches()) == ["feature/alpha", "main"]
    info = g.get_branch_info("feature/alpha")
    assert info["file_count"] == 4 and info["latest_commit"]["message"] == "feat: alpha thing" and info["purpose"] == "Feature: alpha"
    assert sorted(info["unique_files"]) == ["model_alpha.py", "notes"] and info["has_code"] and info["has_models"]
    assert info["file_types"][".py"] == 2 and "ml_models" in info["dependencies"]
    assert g.get_branch_info("main")["unique_files"] == []
    assert "error" in g.get_branch_info("no-such-branch") or g.get_branch_info("no-such-branch")["file_count"] == 0
    idx = g.generate_index()
    assert idx["main_branch"] == "main" and set(idx["branches"]) == {"main", "feature/alpha"}
    assert idx["file_index"]["model_alpha.py"] == ["feature/alpha"] and idx["feature_matrix"] == {"alpha": ["feature/alpha"]}
    assert idx["dependencies"]["feature/alpha"]["can_be_merged_to"] == ["develop", "main"] and idx["dependencies"]["main"]["can_be_merged_to"] == []
    md = g._generate_markdown()
    for needle in ("# Repository Index", "**Main Branch:** `main`", "`feature/alpha` | Feature: alpha", "### alpha", "## Unique Files by Branch", "- model_alpha.py", "`feature/alpha` → develop → main"):
        assert needle in md, needle
    g.save_json_index(str(tmp_path / "i.json"))
    g.save_markdown_index(str(tmp_path / "i.md"))
    assert json.loads((tmp_path / "i.json").read_text())["main_branch"] == "main" and (tmp_path / "i.md").read_text() == md


def test_markdown_truncates_unique_files_and_skips_errors(gri):
    g = gri.RepositoryIndexGenerator(".")
    g.index["branches"] = {"b": {"purpose": "p", "file_count": 1, "latest_commit": {"message": "m"}, "unique_files": [f"f{i}" for i in range(8)], "dependencies": []},
                           "bad": {"error": "x"}}
    g.index["dependencies"] = {"b": {"depends_on_data": ["cache_seed"], "can_be_merged_to": ["main"]}}
    md = g._generate_markdown()
    assert "... and 3 more" in md and "`bad`" not in md and "| `b` | cache_seed |" in md


def test_main_writes_outputs(gri, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    real = gri.RepositoryIndexGenerator
    monkeypatch.setattr(gri, "RepositoryIndexGenerator", lambda path: real(str(repo)))
    gri.main()
    assert (tmp_path / "REPO_INDEX.json").exists() and (tmp_path / "REPO_INDEX.md").exists()
    assert "Main branch: main" in capsys.readouterr().out


# ---------------- inventory_all_data ----------------
@pytest.fixture
def inv(monkeypatch, tmp_path):
    mod = load(monkeypatch, "inventory_all_data", tmp_path)
    root = tmp_path / "tree"
    (root / "cache_seed").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "__pycache__").mkdir()
    (root / "cache_seed" / "a.parquet").write_bytes(b"P" * 2048)
    (root / "other_a.parquet").write_bytes(b"P" * 2048)  # duplicate of the cache_seed copy
    (root / "x.csv").write_bytes(b"1,2\n")
    (root / "c.py").write_text("x=1")
    (root / "README.md").write_text("doc")
    (root / "db.sqlite").write_bytes(b"s")
    (root / "screening_1.json").write_text("{}")
    (root / "noext").write_text("n")
    (root / ".git" / "ignored.txt").write_text("g")
    (root / "__pycache__" / "ignored.pyc").write_bytes(b"g")
    return mod, root


def test_scan_hashes_and_ignores_git(inv, capsys):
    mod, root = inv
    a = mod.DataInventoryAnalyzer(str(root))
    rep = a.scan_repository()
    s = rep["summary"]
    assert s["total_files"] == 8 and s["duplicate_files"] == 1
    assert a.total_size == 2048 * 2 + 4 + 3 + 3 + 1 + 2 + 1 and a.duplicate_size == 2048
    assert s["potential_savings_mb"] == s["duplicate_size_mb"] == 2048 / 1024**2
    assert ".parquet" in rep["by_file_type"] and rep["by_file_type"][".parquet"]["count"] == 2 and rep["by_file_type"][".parquet"]["avg_size_kb"] == 2.0
    assert "Scanned 8 files total" in capsys.readouterr().out


def test_hash_and_categories(inv):
    mod, root = inv
    a = mod.DataInventoryAnalyzer(str(root))
    import hashlib

    assert a.compute_hash(root / "c.py") == hashlib.sha256(b"x=1").hexdigest() and a.compute_hash(root / "missing") is None
    cats = a.scan_repository()["data_categories"]
    assert cats["parquet_files"]["count"] == 2 and cats["csv_data"]["count"] == 1 and cats["json_config"]["count"] == 1
    assert cats["code_files"]["count"] == 1 and cats["documentation"]["count"] == 1 and cats["binary_data"]["count"] == 1
    assert cats["other"]["count"] == 1 and cats["parquet_files"]["size_mb"] == 4096 / 1024**2 and len(cats["parquet_files"]["files"]) == 2


def test_duplicates_recommendations_and_redundancy(inv):
    mod, root = inv
    rep = mod.DataInventoryAnalyzer(str(root)).scan_repository()
    (d,) = rep["duplicates"]
    assert d["copies"] == 2 and d["hash"].endswith("...") and len(d["hash"]) == 11 and sorted(d["paths"]) == ["cache_seed/a.parquet", "other_a.parquet"]
    assert d["total_wasted_mb"] == 2048 / 1024**2
    a = mod.DataInventoryAnalyzer(str(root))
    assert a._get_dup_recommendation([{"path": "cache_seed/x"}]) == "Keep in cache_seed/, symlink from other branches"
    assert a._get_dup_recommendation([{"path": "z/x.parquet"}]) == "Move to Git LFS, symlink locally"
    assert a._get_dup_recommendation([{"path": "z/x.csv"}]) == "Keep original, delete/symlink others"
    kinds = {r["type"]: r for r in rep["redundant_data"]}
    assert kinds["Cache Data"]["priority"] == "HIGH" and kinds["Parquet Data"]["count"] == 2 and kinds["Screening Results"] == {
        "type": "Screening Results", "count": 1, "size_mb": 2 / 1024**2, "recommendation": "Archive to results/ directory, reference by timestamp", "priority": "MEDIUM"}
    empty = mod.DataInventoryAnalyzer(str(root / "cache_seed" / "nothing"))
    assert empty.scan_repository()["redundant_data"] == []


def test_strategy_savings_and_empty_repo(inv, tmp_path):
    mod, root = inv
    rep = mod.DataInventoryAnalyzer(str(root)).scan_repository()
    st = rep["deduplication_strategy"]
    assert [p["phase"] for p in st["phases"]] == [1, 2, 3, 4, 5]
    assert st["expected_savings"]["git_repo_reduction_percent"] == pytest.approx(2048 / (2048 * 2 + 14) * 100)
    empty = mod.DataInventoryAnalyzer(str(tmp_path / "void")).scan_repository()
    assert empty["deduplication_strategy"]["expected_savings"]["git_repo_reduction_percent"] == 0
    assert empty["summary"]["total_files"] == 0


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="inventory_all_data.generate_report accumulates self.duplicate_size on every call, so "
    "scan_repository() followed by save_report() (which calls generate_report again) reports double the wasted space",
)
def test_generate_report_is_idempotent(inv):
    mod, root = inv
    a = mod.DataInventoryAnalyzer(str(root))
    first = a.scan_repository()["summary"]["duplicate_size_mb"]
    assert a.generate_report()["summary"]["duplicate_size_mb"] == first


def test_save_report_prints_and_writes(inv, tmp_path, capsys):
    mod, root = inv
    a = mod.DataInventoryAnalyzer(str(root))
    a.scan_repository()
    out = a.save_report(str(tmp_path / "r.json"))
    assert json.loads((tmp_path / "r.json").read_text())["summary"]["total_files"] == 8 and out["summary"]["total_files"] == 8
    assert "DATA INVENTORY SUMMARY" in capsys.readouterr().out
