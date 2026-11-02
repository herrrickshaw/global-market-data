"""Characterisation tests for etl_registry: scan -> batches/versions/dupes, audit, status.

Uses tiny synthetic files in tmp_path; rclone and the alert script are faked, no network.
"""
from __future__ import annotations

import sqlite3
import subprocess

import pytest

import etl_registry as er


class _Run:
    """Fake subprocess.run recording calls; rclone 'check' returns `rc`."""

    def __init__(self, rc=0):
        self.rc, self.calls = rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc if cmd[0] == er.RCLONE else 0, "", "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "ohlcv"
    root.mkdir()
    (root / "a.csv").write_text("x\n1\n")
    (root / "b.csv").write_text("x\n1\n")  # same content as a.csv -> duplicate
    (root / "skip.txt").write_text("ignored")  # extension not tracked
    monkeypatch.setattr(er, "DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setattr(er, "REPORT", str(tmp_path / "reports"))
    monkeypatch.setattr(er, "SOURCES", {"ohlcv": (str(root), "gmd-warehouse/ohlcv")})
    return root


def _rows(sql):
    c = sqlite3.connect(er.DB)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_scan_registers_batch_and_links_duplicates(env, capsys):
    er.scan()
    out = capsys.readouterr().out
    assert "2 files, 2 new, 0 changed (1 dupes linked)" in out
    assert _rows("select count(*) from files where superseded=0") == [(2,)]
    assert _rows("select count(*) from files where duplicate_of is not null") == [(1,)]
    (batch,) = _rows("select batch_id, n_files, n_new, status from batches")
    assert batch[0].startswith("BATCH-ohlcv-") and batch[0].endswith("-01")
    assert batch[1:] == (2, 2, "landed")


def test_second_scan_without_changes_creates_no_batch(env, capsys):
    er.scan()
    capsys.readouterr()
    er.scan()
    assert "ohlcv: no changes" in capsys.readouterr().out
    assert _rows("select count(*) from batches") == [(1,)]


def test_changed_file_supersedes_old_version(env, capsys):
    er.scan()
    (env / "a.csv").write_text("x\n2\n22\n")
    er.scan()
    assert "1 changed" in capsys.readouterr().out
    assert _rows("select version, superseded from files where path like '%a.csv' order by id") == [
        (1, 1),
        (2, 0),
    ]
    assert _rows("select batch_id from batches order by started_at")[-1][0].endswith("-02")


def test_audit_pass_writes_report(env, monkeypatch):
    er.scan()
    run = _Run(rc=0)
    monkeypatch.setattr(er.subprocess, "run", run)
    assert er.audit() == 0
    report = (env.parent / "reports" / "ETL_AUDIT.md").read_text()
    assert "**PASS**" in report
    assert "| missing locally | 0 |" in report
    assert "| ohlcv | ✅ | ✅ | ✅ |" in report
    assert all(c[0] == er.RCLONE for c in run.calls)  # no alert on PASS


def test_audit_fail_on_missing_file_and_cloud_leg_sends_alert(env, monkeypatch):
    er.scan()
    (env / "a.csv").unlink()
    run = _Run(rc=1)
    monkeypatch.setattr(er.subprocess, "run", run)
    assert er.audit() == 1
    report = (env.parent / "reports" / "ETL_AUDIT.md").read_text()
    assert "**FAIL**" in report and "MISSING:" in report
    assert "| ohlcv | ✅ | ❌ | ❌ |" in report
    alert = [c for c in run.calls if c[0] != er.RCLONE]
    assert len(alert) == 1
    assert alert[0][-1] == (
        "ETL audit FAIL: 1 missing, 0 sha mismatches, 2 datasets failing cloud check"
    )


def test_status_prints_summary(env, capsys):
    er.scan()
    capsys.readouterr()
    er.status()
    out = capsys.readouterr().out
    assert out.startswith("1 batches") and "current duplicate links: 1" in out
