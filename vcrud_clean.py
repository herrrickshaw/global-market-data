#!/usr/bin/env python3
# vcrud_clean.py
# ==============
# Clean local-machine paths and repos by COMPRESSING files in place. This is the
# vCRUD "delete" layer repurposed as a compression step — nothing is destroyed,
# bulky data is just compacted (and always recoverable).
#
# Two compaction strategies, picked automatically per file:
#
#   * NON-PARQUET data (csv/json/txt/log/...) -> byte-level compression to a
#     sibling artifact (``<file>.zst`` via the zstd CLI, else ``<file>.gz``),
#     verified to decompress back to byte-identical content before the original
#     is retired.
#
#   * PARQUET (.parquet) -> RE-ENCODED in place with the zstd column codec (and,
#     with --float16, float downcasting for bounded data like correlation
#     matrices). Byte-wrapping a parquet is pointless (it's already compressed)
#     and would break every reader that expects the .parquet path — re-encoding
#     keeps it a readable .parquet, same schema, same row count (verified), just
#     smaller. Requires pyarrow; the driver auto-re-execs under an interpreter
#     that has it (e.g. /usr/bin/python3) when the current one does not.
#
# SAFE BY DEFAULT: dry-run unless --apply. Skips .git / node_modules / venvs,
# already-compressed files, Git-LFS pointer stubs, symlinks, and files below
# --min-size. Parquet is only touched when you pass --parquet.
#
#   # dry-run across data dirs + a repo (no changes):
#   python3 vcrud_clean.py ~/Downloads/data ~/repos/global-market-data
#
#   # byte-compress loose data only (leaves parquet + repos' schema intact):
#   python3 vcrud_clean.py --apply ~/Downloads/data
#
#   # re-encode parquet in place with zstd (readers keep working):
#   python3 vcrud_clean.py --apply --parquet ~/Downloads/data
#
#   # correlation matrices: zstd + float16 (values in [-1,1], safe to downcast):
#   python3 vcrud_clean.py --apply --parquet --float16 ~/Downloads/data/*_scan
#
# ⚠️ Research/education tooling. Not advice.

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / ".vcrud_bin"))
from db_handler import CompressionManager  # noqa: E402

# Loose data files worth byte-compressing. Extend with --all-ext.
DATA_EXTS = {
    ".csv", ".tsv", ".psv", ".json", ".jsonl", ".ndjson", ".txt", ".log",
    ".xml", ".sql", ".pkl", ".npy", ".tif", ".tiff",
}
PARQUET_EXTS = {".parquet"}
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".idea", ".vscode",
}
# Interpreters to look in for pyarrow when the current one lacks it.
PARQUET_INTERPRETERS = ("/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3")


# ------------------------------- selection -------------------------------

def is_lfs_pointer(p: Path) -> bool:
    """Git-LFS pointer stubs are tiny text files — never compress those."""
    try:
        if p.stat().st_size > 512:
            return False
        with open(p, "rb") as f:
            return f.read(40).startswith(b"version https://git-lfs")
    except OSError:
        return False


def repo_branch_label(root: Path) -> str:
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        ).stdout.strip()
        if top:
            branch = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True,
            ).stdout.strip()
            return f"{Path(top).name}@{branch or 'HEAD'}"
    except Exception:
        pass
    return f"local:{root.name}"


def iter_candidates(root: Path, min_size: int, all_ext: bool, want_parquet: bool):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            if CompressionManager.is_compressed(str(p)):
                continue
            ext = p.suffix.lower()
            is_parq = ext in PARQUET_EXTS
            if is_parq:
                if not want_parquet:
                    continue
            elif not all_ext and ext not in DATA_EXTS:
                continue
            try:
                if p.stat().st_size < min_size:
                    continue
            except OSError:
                continue
            if is_lfs_pointer(p):
                continue
            yield p


# --------------------------- byte compression ----------------------------

def compress_one(p: Path, level: int, algo, remove_original: bool):
    """Byte-compress + verify one file. Returns (status, orig, comp)."""
    try:
        checksum = CompressionManager.compute_checksum(str(p))
        meta = CompressionManager.compress_to_file(str(p), level=level, algo=algo)
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ error compressing {p}: {str(e)[:80]}", file=sys.stderr)
        return "error", 0, 0
    if meta["comp_size"] >= meta["orig_size"]:
        Path(meta["out"]).unlink(missing_ok=True)
        return "no-gain", meta["orig_size"], meta["comp_size"]
    if not CompressionManager.verify_roundtrip(meta["out"], meta["algo"], checksum):
        Path(meta["out"]).unlink(missing_ok=True)
        print(f"   ✗ verify failed, original kept: {p}", file=sys.stderr)
        return "verify-fail", meta["orig_size"], meta["comp_size"]
    if remove_original:
        p.unlink()
    return "ok", meta["orig_size"], meta["comp_size"]


# -------------------------- parquet recompress ---------------------------

def recompress_parquet(path: Path, level: int, float16: bool):
    """
    Re-encode a parquet in place with the zstd column codec, keeping the .parquet
    path and schema (row count + column names verified; exact table equality
    verified for the lossless path). Atomic replace only if the result is smaller.
    ``float16`` downcasts float64/float32 columns to float16 where every value
    fits (|x| <= 65504) — LOSSY, intended for bounded data (e.g. correlation
    matrices in [-1, 1]), never for prices.
    Returns (status, orig, comp).
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    orig_size = path.stat().st_size
    try:
        table = pq.read_table(path)
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ unreadable parquet {path}: {str(e)[:80]}", file=sys.stderr)
        return "error", orig_size, orig_size

    orig_rows = table.num_rows
    orig_names = table.schema.names
    new_table = table
    downcast_applied = False

    if float16:
        cols, changed = [], False
        for i, field in enumerate(table.schema):
            col = table.column(i)
            if pa.types.is_floating(field.type) and field.type.bit_width > 16:
                arr = col.combine_chunks().to_numpy(zero_copy_only=False)
                finite = np.isfinite(arr)
                if finite.any() and float(np.max(np.abs(arr[finite]))) <= 65504.0:
                    col = pa.array(arr.astype(np.float16))
                    changed = True
            cols.append(col)
        if changed:
            new_table = pa.table(cols, names=orig_names)
            downcast_applied = True

    tmp = path.with_name(path.name + ".recompress.tmp")
    try:
        pq.write_table(new_table, tmp, compression="zstd", compression_level=level, use_dictionary=True)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        print(f"   ✗ rewrite failed {path}: {str(e)[:80]}", file=sys.stderr)
        return "error", orig_size, orig_size

    comp_size = tmp.stat().st_size
    if comp_size >= orig_size:
        tmp.unlink(missing_ok=True)
        return "no-gain", orig_size, comp_size

    # verify
    try:
        chk = pq.read_table(tmp)
        ok = chk.num_rows == orig_rows and chk.schema.names == orig_names
        if ok and not downcast_applied:
            ok = chk.equals(table)  # exact for the lossless path
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        tmp.unlink(missing_ok=True)
        print(f"   ✗ verify failed, original kept: {path}", file=sys.stderr)
        return "verify-fail", orig_size, comp_size

    os.replace(tmp, path)  # atomic, same .parquet path
    return "ok", orig_size, comp_size


def ensure_pyarrow_or_reexec(argv_tail) -> None:
    """If the current interpreter lacks pyarrow, re-exec this script under one that
    has it. Exit with guidance if none is found."""
    if importlib.util.find_spec("pyarrow") is not None:
        return
    for cand in PARQUET_INTERPRETERS:
        if os.path.realpath(cand) == os.path.realpath(sys.executable):
            continue
        try:
            r = subprocess.run([cand, "-c", "import pyarrow"], capture_output=True)
        except OSError:
            continue
        if r.returncode == 0:
            script = os.path.abspath(__file__)
            print(f"(--parquet needs pyarrow; re-exec under {cand})\n", file=sys.stderr)
            os.execv(cand, [cand, script, *argv_tail])
    sys.exit(
        "pyarrow is required for --parquet but was not found in any known interpreter "
        f"({', '.join(PARQUET_INTERPRETERS)}). Install it or run with a python that has it."
    )


# -------------------------------- driver ---------------------------------

def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def parse_size(s: str) -> int:
    units = {"B": 1, "KB": 10**3, "KIB": 1024, "MB": 10**6, "MIB": 1024**2, "GB": 10**9, "GIB": 1024**3}
    s = s.upper().strip()
    for u in sorted(units, key=len, reverse=True):
        if s.endswith(u):
            return int(float(s[: -len(u)]) * units[u])
    return int(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="directories to clean (local paths and/or repos)")
    ap.add_argument("--apply", action="store_true", help="actually compress (default: dry-run)")
    ap.add_argument("--keep-original", action="store_true", help="byte-compress: keep the original too")
    ap.add_argument("--parquet", action="store_true", help="also re-encode .parquet files in place (zstd)")
    ap.add_argument("--float16", action="store_true", help="parquet: downcast floats to float16 (bounded data only!)")
    ap.add_argument("--min-size", default="1MiB", help="skip files smaller than this (e.g. 512KiB, 1MiB)")
    ap.add_argument("--level", type=int, default=19, help="zstd/gzip level (default 19)")
    ap.add_argument("--algo", choices=["zstd", "gzip"], default=None, help="byte-compress algorithm")
    ap.add_argument("--all-ext", action="store_true", help="byte-compress: consider all file types")
    ap.add_argument("--index", metavar="DSN", default=None, help="record byte-compactions into vCRUD Postgres")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every file")
    args = ap.parse_args()

    if args.parquet and args.apply:
        ensure_pyarrow_or_reexec(sys.argv[1:])
    if args.float16 and not args.parquet:
        print("--float16 only applies to parquet; add --parquet.", file=sys.stderr)

    min_size = parse_size(args.min_size)
    algo_used = CompressionManager.pick_algo(args.algo)
    db = None
    if args.index and args.apply:
        from db_handler import DatabaseHandler  # noqa: E402

        db = DatabaseHandler(args.index)
    elif args.index:
        print("--index is only meaningful with --apply; ignoring.", file=sys.stderr)

    mode = "APPLY" if args.apply else "DRY-RUN"
    pq_note = "on (zstd" + (", float16)" if args.float16 else ")") if args.parquet else "off"
    print(f"== vCRUD clean ({mode}) · byte-algo={algo_used} level={args.level} · "
          f"parquet={pq_note} · min-size={human(min_size)} ==\n")

    g_files = g_orig = g_comp = g_skip = 0
    for root_str in args.roots:
        root = Path(root_str).expanduser().resolve()
        if not root.exists():
            print(f"  ⚠ missing, skipped: {root}")
            continue
        label = repo_branch_label(root)
        cands = list(iter_candidates(root, min_size, args.all_ext, args.parquet))
        r_files = r_orig = r_comp = r_skip = 0
        print(f"  {root}  [{label}]  — {len(cands)} candidate(s)")

        for p in cands:
            is_parq = p.suffix.lower() in PARQUET_EXTS
            osz = p.stat().st_size
            if not args.apply:
                r_files += 1
                r_orig += osz
                if args.verbose:
                    kind = "recompress" if is_parq else "byte-compress"
                    print(f"     · would {kind:13} {human(osz):>10}  {p}")
                continue

            if is_parq:
                status, o, c = recompress_parquet(p, args.level, args.float16)
            else:
                status, o, c = compress_one(p, args.level, args.algo, remove_original=not args.keep_original)

            if status == "ok":
                r_files += 1
                r_orig += o
                r_comp += c
                if db is not None and not is_parq:
                    out_rel = p.with_name(p.name + CompressionManager.suffix_for(algo_used))
                    try:
                        db.record_compression(
                            orig_path=str(p), comp_path=str(out_rel), branch=label,
                            orig_size=o, comp_size=c, ratio=(c / o if o else 0.0),
                            checksum=CompressionManager.compute_checksum(str(out_rel)),
                        )
                    except Exception as e:  # noqa: BLE001
                        print(f"     (index write skipped: {str(e)[:60]})", file=sys.stderr)
                if args.verbose:
                    kind = "recompressed" if is_parq else "compressed"
                    print(f"     ✓ {kind:13} {human(o):>10} → {human(c):>10}  ({c/o:.0%})  {p}")
            else:
                r_skip += 1
                if args.verbose:
                    print(f"     ↷ {status:12} {human(osz):>10}  {p}")

        if args.apply:
            print(f"     → {r_files} compacted, {r_skip} skipped/no-gain; "
                  f"{human(r_orig)} → {human(r_comp)}  (saved {human(r_orig - r_comp)})\n")
        else:
            print(f"     → {r_files} candidate(s); {human(r_orig)} raw eligible\n")
        g_files += r_files
        g_orig += r_orig
        g_comp += r_comp
        g_skip += r_skip

    print("== total ==")
    if args.apply:
        print(f"   {g_files} files compacted, {g_skip} skipped/no-gain; "
              f"{human(g_orig)} → {human(g_comp)}  (reclaimed {human(g_orig - g_comp)})")
        if db is not None:
            db.close()
    else:
        print(f"   {g_files} candidate(s); {human(g_orig)} raw eligible.\n"
              f"   Re-run with --apply (add --parquet to re-encode parquet in place).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
