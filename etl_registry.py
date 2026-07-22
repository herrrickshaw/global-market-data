#!/usr/bin/env python3
"""ETL batch registry — names, dates, batches, versions, weekly audits.

Formalizes the warehouse's ETL model:

  * Every scan is a named, dated BATCH per source:  BATCH-<source>-<YYYYMMDD>-<seq>
  * Every file is content-addressed (sha256). Same path, new content ->
    NEW VERSION (old row marked superseded; the byte-level old copy already
    survives in dropbox:market-data-backup/versions/<date>/ via the daily
    backup's --backup-dir).
  * DUPLICATES (same content at a different path) are LINKED, not re-stored:
    the row records duplicate_of = canonical path. The weekly audit verifies
    these links and reports wasted bytes.
  * Weekly AUDIT verifies: local existence, sha spot-checks, Dropbox
    presence, per-source freshness. Output: reports/ETL_AUDIT.md + audit row.

Usage:
  etl_registry.py scan          # register today's batches (daily cron 20:15)
  etl_registry.py audit         # weekly verification (cron Mon 09:30)
  etl_registry.py status        # quick registry summary

Registry: warehouse/etl_registry.sqlite (single-writer: this script only).
"""
import hashlib
import os
import random
import sqlite3
import subprocess
import sys
from datetime import datetime, date

GMD = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(GMD, "warehouse", "etl_registry.sqlite")
REPORT = os.path.join(GMD, "reports")
RCLONE = "/opt/homebrew/bin/rclone"
REMOTE = "dropbox:market-data-backup/current"

HOME = os.path.expanduser("~")
SOURCES = {  # source name -> (local root, dropbox dataset name or None)
    "ohlcv":       (os.path.join(GMD, "warehouse", "ohlcv"), "gmd-warehouse/ohlcv"),
    "ohlcv_adj":   (os.path.join(GMD, "warehouse", "ohlcv_adj"), "gmd-warehouse/ohlcv_adj"),
    "bhavcopy":    (os.path.join(GMD, "warehouse", "bhavcopy"), "gmd-warehouse/bhavcopy"),
    "cache_seed":  (os.path.join(GMD, "cache_seed"), "gmd-cache_seed"),
    "pipeline":    (os.path.join(HOME, "market-pipeline/code/python_files/cache_seed"),
                    "pipeline-cache_seed"),
    "market_cache": (os.path.join(HOME, "Downloads/market_cache"), None),
}
EXT = (".parquet", ".csv", ".db", ".duckdb", ".sqlite", ".gz", ".json", ".xlsx")
STALE_DAYS = 7


def con():
    c = sqlite3.connect(DB)
    c.executescript("""
    create table if not exists batches(
      batch_id text primary key, source text, batch_date text,
      started_at text, finished_at text,
      n_files int, n_new int, n_changed int, n_dupes int, bytes_new int,
      status text);
    create table if not exists files(
      id integer primary key, path text, sha256 text, bytes int, mtime real,
      version int, batch_id text, duplicate_of text, superseded int default 0);
    create index if not exists idx_files_path on files(path, superseded);
    create index if not exists idx_files_sha on files(sha256);
    create table if not exists audits(
      id integer primary key, run_at text, files_checked int, missing_local int,
      sha_mismatch int, cloud_missing int, dupe_groups int, wasted_bytes int,
      stale_sources text, result text);
    """)
    return c


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan():
    c = con()
    today = date.today().strftime("%Y%m%d")
    for source, (root, _) in SOURCES.items():
        if not os.path.isdir(root):
            continue
        seq = 1 + c.execute(
            "select count(*) from batches where source=? and batch_date=?",
            (source, today)).fetchone()[0]
        batch_id = f"BATCH-{source}-{today}-{seq:02d}"
        started = datetime.now().isoformat(timespec="seconds")
        n_files = n_new = n_changed = n_dupes = bytes_new = 0

        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                if not name.endswith(EXT):
                    continue
                p = os.path.join(dirpath, name)
                st = os.stat(p)
                n_files += 1
                cur = c.execute(
                    "select sha256, bytes, mtime, version from files "
                    "where path=? and superseded=0", (p,)).fetchone()
                if cur and cur[1] == st.st_size and abs(cur[2] - st.st_mtime) < 1:
                    continue  # unchanged (size+mtime cache) — skip hashing
                digest = sha256(p)
                if cur and cur[0] == digest:
                    c.execute("update files set mtime=? where path=? and superseded=0",
                              (st.st_mtime, p))
                    continue
                # duplicate? same content already registered at another path
                dup = c.execute(
                    "select path from files where sha256=? and path!=? "
                    "and superseded=0 order by id limit 1", (digest, p)).fetchone()
                version = (cur[3] + 1) if cur else 1
                if cur:
                    c.execute("update files set superseded=1 "
                              "where path=? and superseded=0", (p,))
                    n_changed += 1
                else:
                    n_new += 1
                    bytes_new += st.st_size
                if dup:
                    n_dupes += 1
                c.execute(
                    "insert into files(path,sha256,bytes,mtime,version,batch_id,"
                    "duplicate_of) values(?,?,?,?,?,?,?)",
                    (p, digest, st.st_size, st.st_mtime, version, batch_id,
                     dup[0] if dup else None))

        if n_new or n_changed:
            c.execute("insert into batches values(?,?,?,?,?,?,?,?,?,?,?)",
                      (batch_id, source, today, started,
                       datetime.now().isoformat(timespec="seconds"),
                       n_files, n_new, n_changed, n_dupes, bytes_new, "landed"))
            print(f"{batch_id}: {n_files} files, {n_new} new, "
                  f"{n_changed} changed ({n_dupes} dupes linked)")
        else:
            print(f"{source}: no changes — no batch created")
    c.commit()


def audit():
    c = con()
    rows = c.execute("select path, sha256, bytes from files "
                     "where superseded=0").fetchall()
    missing = [p for p, _, _ in rows if not os.path.exists(p)]
    # sha spot-check: 20 random current files
    sample = random.sample(rows, min(20, len(rows)))
    mismatch = [p for p, s, _ in sample if os.path.exists(p) and sha256(p) != s]
    # cloud presence per dataset (size-only check, cheap)
    cloud_missing = 0
    for source, (root, dataset) in SOURCES.items():
        if dataset is None or not os.path.isdir(root):
            continue
        r = subprocess.run([RCLONE, "check", root, f"{REMOTE}/{dataset}",
                            "--one-way", "--size-only"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            cloud_missing += 1
    # duplicates: current groups sharing a sha
    dupes = c.execute("""
      select count(*), sum(w) from (
        select sha256, count(*)-1 n, (count(*)-1)*max(bytes) w
        from files where superseded=0 group by sha256 having count(*)>1)
    """).fetchone()
    dupe_groups, wasted = (dupes[0] or 0), (dupes[1] or 0)
    # freshness: sources with no batch in STALE_DAYS
    stale = [s for s, in c.execute(
        f"select source from (select source, max(batch_date) d from batches "
        f"group by source) where d < strftime('%Y%m%d', 'now', '-{STALE_DAYS} days')")]
    ok = not missing and not mismatch and cloud_missing == 0
    result = "PASS" if ok else "FAIL"
    c.execute("insert into audits(run_at,files_checked,missing_local,"
              "sha_mismatch,cloud_missing,dupe_groups,wasted_bytes,"
              "stale_sources,result) values(?,?,?,?,?,?,?,?,?)",
              (datetime.now().isoformat(timespec="seconds"), len(rows),
               len(missing), len(mismatch), cloud_missing, dupe_groups,
               wasted, ",".join(stale), result))
    c.commit()

    nb, nf = c.execute("select (select count(*) from batches), "
                       "(select count(*) from files)").fetchone()
    lines = [
        f"# ETL weekly audit — {datetime.now():%Y-%m-%d %H:%M} — **{result}**",
        "",
        f"Registry: {nb} batches, {nf} file-versions, "
        f"{len(rows)} current files.",
        "",
        "| check | value |",
        "|---|---|",
        f"| missing locally | {len(missing)} |",
        f"| sha spot-check mismatches (n=20) | {len(mismatch)} |",
        f"| datasets failing Dropbox check | {cloud_missing} |",
        f"| duplicate groups (linked, not re-stored) | {dupe_groups} |",
        f"| wasted bytes if duplicates were copies | {wasted/1e6:.1f} MB |",
        f"| stale sources (no batch in {STALE_DAYS}d) | {', '.join(stale) or 'none'} |",
        "",
        "## Recent batches",
        "",
        "| batch | files | new | changed | dupes | status |",
        "|---|---|---|---|---|---|",
    ]
    for b in c.execute("select batch_id,n_files,n_new,n_changed,n_dupes,status "
                       "from batches order by started_at desc limit 12"):
        lines.append("| " + " | ".join(map(str, b)) + " |")
    for p in missing[:10]:
        lines.append(f"\nMISSING: {p}")
    os.makedirs(REPORT, exist_ok=True)
    out = os.path.join(REPORT, "ETL_AUDIT.md")
    open(out, "w").write("\n".join(lines) + "\n")
    print(f"{result}: {out}")
    if not ok:
        alert = (f"ETL audit FAIL: {len(missing)} missing, {len(mismatch)} sha "
                 f"mismatches, {cloud_missing} datasets failing cloud check")
        subprocess.run([os.path.join(HOME, "market-pipeline/code/python_files/"
                        ".venv/bin/python3"),
                        os.path.join(HOME, "market-pipeline/code/python_files/"
                        "send_alert.py"), alert])
    return 0 if ok else 1


def status():
    c = con()
    print(c.execute("select count(*) from batches").fetchone()[0], "batches")
    for r in c.execute("select source, max(batch_date), count(*) from batches "
                       "group by source"):
        print(f"  {r[0]:<14} last={r[1]} batches={r[2]}")
    d = c.execute("select count(*) from files where superseded=0 "
                  "and duplicate_of is not null").fetchone()[0]
    print(f"current duplicate links: {d}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"scan": scan, "audit": lambda: sys.exit(audit()), "status": status}[cmd]()
