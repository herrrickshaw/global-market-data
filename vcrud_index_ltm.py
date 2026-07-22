#!/usr/bin/env python3
# vcrud_index_ltm.py
# ==================
# Register the deep 10-year multi-geography LTM parquet dataset into VCRUD
# (PostgreSQL file-tracking). Indexes cache_seed/ltm/*.parquet with checksum,
# size, gzip-compression stats, and reports per-geography analytics + dedup.
#
#   DB=postgresql://umashankar@localhost/vcrud python3 vcrud_index_ltm.py
#
# ⚠️ Research/education only. Not advice.

from __future__ import annotations

import os
from pathlib import Path

from db_handler import DatabaseHandler
from vcrud_manager import LocalFileIndexer

REPO = Path(__file__).parent
DB_URL = os.environ.get("DB", "postgresql://umashankar@localhost/vcrud")
BRANCH = os.environ.get("BRANCH", "karz")


def main() -> int:
    db = DatabaseHandler(DB_URL)
    scanner = LocalFileIndexer(str(REPO))
    records = scanner.scan_directory(BRANCH, patterns=["cache_seed/ltm/*.parquet"])
    if not records:
        print("no LTM parquet files found to index")
        return 1

    inserted = 0
    for rec in records:
        try:
            db.create_file(rec)
            inserted += 1
        except Exception as e:  # noqa: BLE001
            print(f"  skip {rec.path}: {str(e)[:60]}")

    raw = sum(r.size_bytes for r in records)
    comp = sum(r.compressed_size for r in records)
    print(f"\n✓ VCRUD indexed {inserted}/{len(records)} LTM files on branch '{BRANCH}'")
    print(
        f"  raw: {raw/1e6:.1f} MB | gzip-on-parquet: {comp/1e6:.1f} MB "
        f"| extra gzip ratio: {100*(raw-comp)/raw:.1f}%"
    )

    print("\n  per-geography (10-year LTM):")
    rows = sorted(records, key=lambda r: -r.size_bytes)
    for r in rows:
        mkt = Path(r.path).stem
        print(
            f"    {mkt:4}  {r.size_bytes/1e6:7.1f} MB  gzip {r.compressed_size/1e6:6.1f} MB  "
            f"({r.compression_ratio*100:4.1f}%)  {r.checksum[:12]}"
        )

    # cross-file dedup (identical checksums = wasted space)
    seen: dict = {}
    dups = 0
    for r in records:
        if r.checksum in seen:
            dups += 1
            print(f"    ⚠ dup: {r.path} == {seen[r.checksum]}")
        else:
            seen[r.checksum] = r.path
    print(f"\n  distinct checksums: {len(seen)} | duplicate files: {dups}")

    try:
        stats = db.get_branch_stats(BRANCH)
        print(f"\n  VCRUD branch stats: {stats}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
