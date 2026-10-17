#!/usr/bin/env python3
# french_regional.py
# ==================
# Regional Fama-French 5-factor + momentum (WML) monthly returns from the Kenneth French
# Data Library (keyless, public). Fills a gap in reference_seed/: it holds only the US
# FF3 series (french_ff3.parquet), yet the panel spans 20 markets, so non-US factor
# regressions / alpha benchmarks had no regional factors.
#
#   regions: Developed, Developed_ex_US, Europe, Japan, Asia_Pacific_ex_Japan, Emerging
#   file pattern: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/<R>_5_Factors_CSV.zip
#                 https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/<R>_MOM_Factor_CSV.zip
#
# Output (new file only; nothing existing is modified):
#   reference_open/french_regional_monthly.parquet   [region, YYYYMM, Mkt-RF, SMB, HML, RMW, CMA, RF, WML]
#
# Values are in percent per month, as published; -99.99 (missing) becomes NaN.
# Usage:  python french_regional.py [--out reference_open/french_regional_monthly.parquet]

from __future__ import annotations

import argparse
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
REGIONS = ["Developed", "Developed_ex_US", "Europe", "Japan", "Asia_Pacific_ex_Japan", "Emerging"]
_UA = {"User-Agent": "Mozilla/5.0 (academic-research)"}


def parse_monthly(text: str) -> pd.DataFrame:
    """Parse a French-library CSV: header row ',A,B,...' then 'YYYYMM, v1, v2...' rows
    (stops at the first non-monthly row, i.e. before the annual block)."""
    lines = text.splitlines()
    hdr = next(i for i, l in enumerate(lines) if l.startswith(","))
    cols = [c.strip() for c in lines[hdr].split(",")[1:]]
    rows = []
    for l in lines[hdr + 1:]:
        parts = [p.strip() for p in l.split(",")]
        if not re.fullmatch(r"\d{6}", parts[0]):
            break
        rows.append([int(parts[0])] + [float(p) for p in parts[1:len(cols) + 1]])
    df = pd.DataFrame(rows, columns=["YYYYMM"] + cols)
    return df.replace(-99.99, float("nan"))


def _get(name: str) -> str:
    r = requests.get(BASE + name, headers=_UA, timeout=60)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return z.read(z.namelist()[0]).decode("latin-1")


def build() -> pd.DataFrame:
    frames = []
    for reg in REGIONS:
        f5 = parse_monthly(_get(f"{reg}_5_Factors_CSV.zip"))
        if reg == "Developed_ex_US":
            mom = None  # no momentum file published for this region
        else:
            mom = parse_monthly(_get(f"{reg}_MOM_Factor_CSV.zip"))
        df = f5 if mom is None else f5.merge(mom, on="YYYYMM", how="left")
        if "WML" not in df:
            df["WML"] = float("nan")
        df.insert(0, "region", reg)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "reference_open" / "french_regional_monthly.parquet"))
    a = ap.parse_args()
    df = build()
    df.to_parquet(a.out, index=False)
    print(df.groupby("region").YYYYMM.agg(["min", "max", "count"]).to_string())
    print(f"{len(df)} rows -> {a.out}")


if __name__ == "__main__":
    main()
