"""etl_registry: content-addressed batch registry (sqlite) + weekly audit, all in tmp dirs."""

import os
import sqlite3
import subprocess
from datetime import date, timedelta

import pytest

from tests.w4_helpers import fresh_import


@pytest.fixture
def er(monkeypatch, tmp_path):
    m = fresh_import(monkeypatch, "etl_registry")
    root = tmp_path / "src"
    root.mkdir()
    monkeypatch.setattr(m, "DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setattr(m, "REPORT", str(tmp_path / "reports"))
    monkeypatch.setattr(m, "SOURCES", {"alpha": (str(root), "ds/alpha")})
    m.root = root
    return m


def _put(root, name, data=b"x", mtime=None):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))
    return str(p)


def _q(er, sql, *a):
    c = sqlite3.connect(er.DB)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def test_sha256_matches_hashlib(er, tmp_path):
    import hashlib

    p = tmp_path / "f"
    data = b"hello" * 500_000  # spans multiple 1 MiB chunks
    p.write_bytes(data)
    assert er.sha256(str(p)) == hashlib.sha256(data).hexdigest()


def test_scan_new_files_batch_and_filters(er, capsys):
    _put(er.root, "a.parquet", b"1")
    _put(er.root, "sub/b.csv", b"22")
    _put(er.root, "ignored.txt", b"33")
    _put(er.root, ".git/c.json", b"44")
    er.scan()
    today = date.today().strftime("%Y%m%d")
    out = capsys.readouterr().out
    assert f"BATCH-alpha-{today}-01: 2 files, 2 new, 0 changed (0 dupes linked)" in out
    rows = _q(er, "select path, version, superseded from files order by path")
    assert [os.path.basename(r[0]) for r in rows] == ["a.parquet", "b.csv"]
    assert all(r[1] == 1 and r[2] == 0 for r in rows)
    b = _q(er, "select n_files, n_new, bytes_new, status from batches")[0]
    assert b == (2, 2, 3, "landed")


def test_scan_unchanged_makes_no_batch(er, capsys):
    _put(er.root, "a.parquet", b"1")
    er.scan()
    capsys.readouterr()
    er.scan()
    assert "alpha: no changes — no batch created" in capsys.readouterr().out
    assert _q(er, "select count(*) from batches")[0][0] == 1


def test_scan_changed_content_supersedes_and_bumps_version(er, capsys):
    p = _put(er.root, "a.parquet", b"1", mtime=1_700_000_000)
    er.scan()
    _put(er.root, "a.parquet", b"2222", mtime=1_700_000_100)
    er.scan()
    rows = _q(er, "select version, superseded, bytes from files order by id")
    assert rows == [(1, 1, 1), (2, 0, 4)]
    today = date.today().strftime("%Y%m%d")
    assert f"BATCH-alpha-{today}-02: 1 files, 0 new, 1 changed" in capsys.readouterr().out
    b2 = _q(
        er, "select n_new, n_changed, bytes_new from batches order by started_at, batch_id desc"
    )
    assert (0, 1, 0) in b2  # changed files add no "new bytes"


def test_scan_touched_but_identical_updates_mtime_only(er, capsys):
    p = _put(er.root, "a.parquet", b"same", mtime=1_700_000_000)
    er.scan()
    capsys.readouterr()
    os.utime(p, (1_700_005_000, 1_700_005_000))  # same bytes, newer mtime
    er.scan()
    assert "no changes" in capsys.readouterr().out
    rows = _q(er, "select mtime, version from files")
    assert rows == [(1_700_005_000.0, 1)]


def test_scan_mtime_tolerance_under_one_second(er, capsys):
    p = _put(er.root, "a.parquet", b"same", mtime=1_700_000_000)
    er.scan()
    os.utime(p, (1_700_000_000.5, 1_700_000_000.5))
    er.scan()
    assert _q(er, "select count(*) from files")[0][0] == 1  # skipped without re-hash churn


def test_scan_duplicates_linked_not_restored(er, capsys):
    a = _put(er.root, "a.parquet", b"dup")
    _put(er.root, "z/b.parquet", b"dup")
    er.scan()
    out = capsys.readouterr().out
    assert "2 new" in out and "(1 dupes linked)" in out
    rows = dict(_q(er, "select path, duplicate_of from files"))
    dups = [p for p, d in rows.items() if d]
    assert len(dups) == 1 and rows[dups[0]] in rows and rows[dups[0]] != dups[0]


def test_scan_second_batch_same_day_increments_sequence(er):
    _put(er.root, "a.parquet", b"1")
    er.scan()
    _put(er.root, "b.parquet", b"2")
    er.scan()
    ids = [r[0] for r in _q(er, "select batch_id from batches order by batch_id")]
    assert [i[-2:] for i in ids] == ["01", "02"]


def test_scan_skips_missing_roots(er, monkeypatch, capsys):
    monkeypatch.setattr(er, "SOURCES", {"gone": (str(er.root / "nope"), None)})
    er.scan()
    assert capsys.readouterr().out == ""


def test_status_summary(er, capsys):
    _put(er.root, "a.parquet", b"dup")
    _put(er.root, "b.parquet", b"dup")
    er.scan()
    capsys.readouterr()
    er.status()
    out = capsys.readouterr().out
    assert "1 batches" in out and "alpha" in out and "batches=1" in out
    assert "current duplicate links: 1" in out


class _Run:
    def __init__(self, rc_by_label=None, default=0):
        self.calls = []
        self.rc = rc_by_label or {}
        self.default = default

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        base = cmd[3] if len(cmd) > 3 else ""
        rc = self.default
        for label, v in self.rc.items():
            if label in base:
                rc = v
        return subprocess.CompletedProcess(cmd, rc, "", "")


def test_audit_pass_writes_report_and_row(er, monkeypatch, capsys):
    _put(er.root, "a.parquet", b"1")
    er.scan()
    capsys.readouterr()
    run = _Run()
    monkeypatch.setattr(er.subprocess, "run", run)
    assert er.audit() == 0
    rpt = open(os.path.join(er.REPORT, "ETL_AUDIT.md")).read()
    assert "**PASS**" in rpt and "| alpha | ✅ | ✅ | ✅ |" in rpt
    assert "| missing locally | 0 |" in rpt and "stale sources (no batch in 7d) | none |" in rpt
    assert "Registry: 1 batches, 1 file-versions, 1 current files." in rpt
    assert len(run.calls) == 2  # dropbox + gdrive checks; no alert on pass
    assert run.calls[0][1:3] == ["check", str(er.root)] and "--size-only" in run.calls[0]
    assert run.calls[0][3] == "dropbox:market-data-backup/current/ds/alpha"
    assert run.calls[1][3] == "googledrive:market-data-backup/current/ds/alpha"
    row = _q(
        er, "select files_checked, missing_local, sha_mismatch, cloud_missing, result from audits"
    )
    assert row == [(1, 0, 0, 0, "PASS")]
    assert "PASS:" in capsys.readouterr().out


def test_audit_fail_missing_mismatch_cloud_and_alert(er, monkeypatch, capsys):
    a = _put(er.root, "a.parquet", b"1")
    b = _put(er.root, "b.parquet", b"2")
    c = _put(er.root, "c.parquet", b"3")
    er.scan()
    os.remove(a)  # missing locally
    open(b, "wb").write(b"tampered")  # sha mismatch (sample covers all <=20 rows)
    run = _Run(rc_by_label={"googledrive": 1})
    monkeypatch.setattr(er.subprocess, "run", run)
    assert er.audit() == 1
    rpt = open(os.path.join(er.REPORT, "ETL_AUDIT.md")).read()
    assert "**FAIL**" in rpt and "| missing locally | 1 |" in rpt
    assert "| sha spot-check mismatches (n=20) | 1 |" in rpt
    assert "| dataset-legs failing cloud check | 1 |" in rpt
    assert "| alpha | ✅ | ✅ | ❌ |" in rpt and f"MISSING: {a}" in rpt
    alert = run.calls[-1]
    assert alert[0].endswith(".venv/bin/python3") and alert[1].endswith("send_alert.py")
    assert alert[2] == "ETL audit FAIL: 1 missing, 1 sha mismatches, 1 datasets failing cloud check"
    assert _q(er, "select result, cloud_missing from audits") == [("FAIL", 1)]


def test_audit_each_failure_alone_fails(er, monkeypatch):
    p = _put(er.root, "a.parquet", b"1")
    er.scan()
    monkeypatch.setattr(er.subprocess, "run", _Run(default=1))
    assert er.audit() == 1  # only cloud failing
    monkeypatch.setattr(er.subprocess, "run", _Run())
    os.remove(p)
    assert er.audit() == 1  # only a missing file


def test_audit_skips_sources_without_dataset_or_root(er, monkeypatch):
    monkeypatch.setattr(
        er, "SOURCES", {"nods": (str(er.root), None), "noroot": (str(er.root / "x"), "d")}
    )
    run = _Run()
    monkeypatch.setattr(er.subprocess, "run", run)
    assert er.audit() == 0 and run.calls == []


def test_audit_dupe_groups_wasted_bytes_and_stale_sources(er, monkeypatch):
    _put(er.root, "a.parquet", b"abcd")
    _put(er.root, "b.parquet", b"abcd")
    _put(er.root, "c.parquet", b"abcd")
    er.scan()
    old = (date.today() - timedelta(days=8)).strftime("%Y%m%d")
    c = sqlite3.connect(er.DB)
    c.execute("update batches set batch_date=?", (old,))
    c.commit()
    c.close()
    monkeypatch.setattr(er.subprocess, "run", _Run())
    er.audit()
    row = _q(er, "select dupe_groups, wasted_bytes, stale_sources from audits")
    assert row == [(1, 8, "alpha")]  # (3-1 copies) * 4 bytes
    assert (
        "| stale sources (no batch in 7d) | alpha |"
        in open(os.path.join(er.REPORT, "ETL_AUDIT.md")).read()
    )


def test_audit_staleness_boundary(er, monkeypatch):
    _put(er.root, "a.parquet", b"1")
    er.scan()
    monkeypatch.setattr(er.subprocess, "run", _Run())
    c = sqlite3.connect(er.DB)
    c.execute(
        "update batches set batch_date=?", ((date.today() - timedelta(days=7)).strftime("%Y%m%d"),)
    )
    c.commit()
    c.close()
    er.audit()
    assert _q(er, "select stale_sources from audits")[-1] == ("",)  # exactly 7d old is not stale
    c = sqlite3.connect(er.DB)
    c.execute(
        "update batches set batch_date=?", ((date.today() - timedelta(days=8)).strftime("%Y%m%d"),)
    )
    c.commit()
    c.close()
    er.audit()
    assert _q(er, "select stale_sources from audits")[-1] == ("alpha",)


def test_audit_report_lists_recent_batches_capped_at_12(er, monkeypatch):
    for i in range(14):
        _put(er.root, f"f{i}.parquet", str(i).encode())
        er.scan()
    monkeypatch.setattr(er.subprocess, "run", _Run())
    er.audit()
    rpt = open(os.path.join(er.REPORT, "ETL_AUDIT.md")).read()
    assert rpt.count("| BATCH-alpha-") == 12
    assert "Registry: 14 batches" in rpt


def test_audit_missing_listing_capped_at_10(er, monkeypatch):
    paths = [_put(er.root, f"f{i}.parquet", str(i).encode()) for i in range(12)]
    er.scan()
    for p in paths:
        os.remove(p)
    monkeypatch.setattr(er.subprocess, "run", _Run())
    er.audit()
    assert open(os.path.join(er.REPORT, "ETL_AUDIT.md")).read().count("MISSING: ") == 10


def test_main_dispatch_table_exists(er):
    assert er.STALE_DAYS == 7 and er.EXT[0] == ".parquet"


def test_scan_mtime_difference_of_exactly_one_second_is_a_change(er):
    p = _put(er.root, "a.parquet", b"same", mtime=1_700_000_000)
    er.scan()
    os.utime(p, (1_700_000_001, 1_700_000_001))  # same bytes, mtime moved by exactly 1s: re-hashed
    er.scan()
    assert _q(er, "select mtime from files") == [(1_700_000_001.0,)]
