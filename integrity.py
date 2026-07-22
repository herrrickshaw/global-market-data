#!/usr/bin/env python3
# integrity.py
# ============
# Tamper-EVIDENCE for the repository. Git blobs can't be made literally immutable,
# but tampering can be made cryptographically detectable: this records a SHA-256 of
# every committed file in a signed manifest, and verifies them on demand + in CI.
#
# A mismatch means a tracked file changed without the manifest being regenerated —
# i.e. an out-of-band edit, a corrupted Git-LFS object, or a tampered blob.
#
#   python3 integrity.py --generate     # (re)write cache_seed/CHECKSUMS.sha256
#   python3 integrity.py --verify       # recompute + compare; exit 1 on any drift
#   python3 integrity.py --verify --data-only   # only cache_seed/ + reference_seed/
#
# Regenerate whenever you legitimately change tracked files (the pre-commit hook and
# `make` target below do this automatically); CI runs --verify on every push/PR.

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).parent
MANIFEST = HERE / "cache_seed" / "CHECKSUMS.sha256"
DATA_PREFIXES = ("cache_seed/", "reference_seed/")
# volatile derived/ignored files never belong in the manifest
SKIP = {"cache_seed/CHECKSUMS.sha256"}
SKIP_DIRS = ("cache_seed/serving/", "cache_seed/cdc/", "cache_seed/models/",
             "cache_seed/discovered_screens/")


def _tracked_files(data_only: bool = False) -> List[str]:
    """Git-tracked files, optionally restricted to the data dirs."""
    out = subprocess.run(["git", "-C", str(HERE), "ls-files"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    files = []
    for f in out:
        if f in SKIP or any(f.startswith(d) for d in SKIP_DIRS):
            continue
        if data_only and not f.startswith(DATA_PREFIXES):
            continue
        files.append(f)
    return sorted(files)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def generate(data_only: bool = False, verbose: bool = True) -> int:
    lines = []
    for f in _tracked_files(data_only):
        p = HERE / f
        if p.exists():
            lines.append(f"{_sha256(p)}  {f}")
    MANIFEST.write_text("\n".join(lines) + "\n")
    if verbose:
        print(f"  wrote {MANIFEST.name}: {len(lines)} file checksums")
    return len(lines)


def _load_manifest() -> Dict[str, str]:
    if not MANIFEST.exists():
        return {}
    out = {}
    for line in MANIFEST.read_text().splitlines():
        if "  " in line:
            digest, path = line.split("  ", 1)
            out[path] = digest
    return out


def verify(data_only: bool = False, verbose: bool = True) -> int:
    """Recompute and compare. Returns 0 if intact, 1 on any mismatch/missing/new."""
    recorded = _load_manifest()
    if not recorded:
        print("  no CHECKSUMS.sha256 — run --generate first")
        return 1
    current = set(_tracked_files(data_only))
    if data_only:
        recorded = {k: v for k, v in recorded.items() if k.startswith(DATA_PREFIXES)}

    mismatched, missing, added = [], [], []
    for path, digest in recorded.items():
        p = HERE / path
        if not p.exists():
            missing.append(path)
        elif _sha256(p) != digest:
            mismatched.append(path)
    added = [f for f in current if f not in recorded]

    ok = not (mismatched or missing or added)
    if verbose:
        print(f"  verified {len(recorded)} files: "
              f"{'OK ✓' if ok else 'DRIFT DETECTED ✗'}")
        for p in mismatched:
            print(f"    MISMATCH  {p}")
        for p in missing:
            print(f"    MISSING   {p}")
        for p in added:
            print(f"    UNTRACKED-BY-MANIFEST  {p}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Repository tamper-evidence (SHA-256 manifest)")
    ap.add_argument("--generate", action="store_true", help="(re)write the checksum manifest")
    ap.add_argument("--verify", action="store_true", help="verify files against the manifest")
    ap.add_argument("--data-only", action="store_true", help="restrict to cache_seed/ + reference_seed/")
    args = ap.parse_args()

    if args.generate:
        generate(args.data_only)
        return 0
    if args.verify:
        return verify(args.data_only)
    # default: verify
    return verify(args.data_only)


if __name__ == "__main__":
    raise SystemExit(main())
