#!/usr/bin/env python3
"""
vcrud_precheck.py
-----------------
Self-contained cross-branch duplicate scan for the vCRUD mandatory workflow. It
replaces the previously-removed find_duplicates_standalone.py and needs ONLY git — no
PostgreSQL, no LFS smudge, no external files — so it works in any hook.

It compares files across all local branches by their **git blob SHA** (git ls-tree).
Two nice properties fall out of that:
  * identical content on multiple branches -> same blob SHA -> flagged as a duplicate;
  * LFS-tracked files are stored as tiny pointer blobs, so duplicated LFS content shows
    ~0 MB "waste" (correctly reflecting that LFS already dedups by OID) — only genuine
    non-LFS duplicated content reports real megabytes.

Warn-only by design (always exits 0): the hooks report duplication without blocking a
commit/push. Flip WARN_ONLY=False (or set VCRUD_STRICT=1) to make it a hard gate.

Usage:
  python3 vcrud_precheck.py            # print the duplicate report
"""

import collections
import os
import subprocess
import sys

WARN_ONLY = os.environ.get("VCRUD_STRICT") != "1"


def _git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def local_branches() -> list:
    return [b.strip() for b in _git("for-each-ref", "--format=%(refname:short)",
                                    "refs/heads").splitlines() if b.strip()]


def blobs(branch: str):
    """(path, sha, size) for every file blob in a branch's tree."""
    for ln in _git("ls-tree", "-r", "-l", branch).splitlines():
        head, _, path = ln.partition("\t")
        parts = head.split()
        if len(parts) < 4 or parts[1] != "blob":
            continue
        mode, typ, sha, size = parts[0], parts[1], parts[2], parts[3]
        yield path, sha, (int(size) if size.isdigit() else 0)


def scan() -> dict:
    by_sha = collections.defaultdict(list)
    branches = local_branches()
    for b in branches:
        for path, sha, size in blobs(b):
            by_sha[sha].append((b, path, size))
    dups = {sha: locs for sha, locs in by_sha.items() if len(locs) > 1}
    wasted = sum(locs[0][2] * (len(locs) - 1) for locs in dups.values())
    return {"branches": branches, "duplicates": dups, "wasted_bytes": wasted}


def main() -> int:
    r = scan()
    dups = r["duplicates"]
    print(f"vCRUD precheck: {len(dups)} duplicated blobs across {len(r['branches'])} "
          f"local branches; {r['wasted_bytes']/1e6:.1f} MB non-LFS content duplicated "
          f"(LFS-tracked duplicates cost ~0 — deduped by OID).")
    for sha, locs in sorted(dups.items(), key=lambda kv: -kv[1][0][2])[:5]:
        b0, p0, sz = locs[0]
        print(f"   {sz/1e6:6.1f} MB ×{len(locs):<2} {p0}  [{', '.join(sorted({l[0] for l in locs}))}]")
    if not WARN_ONLY and r["wasted_bytes"] > 0:
        print("vCRUD precheck FAILED (VCRUD_STRICT=1 and non-LFS duplicates exist).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
