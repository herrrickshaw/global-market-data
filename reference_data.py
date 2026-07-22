#!/usr/bin/env python3
# reference_data.py
# =================
# Pulls benchmark / factor datasets from authoritative academic & practitioner
# sources, caches them locally, and exposes lookups used by the strategies for
# RELATIVE thresholds (e.g. GARP / Bluest compare a stock's P/E to its industry).
#
# Sources:
#   • Aswath Damodaran (NYU Stern)  — industry-level PE, ROE, beta, margins, D/E
#       https://pages.stern.nyu.edu/~adamodar/  (archived datasets, by region)
#   • Kenneth French (Dartmouth)    — Fama-French factor returns
#       https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
#   • AQR Capital                   — factor datasets (value, momentum, QMJ, BAB)
#       https://www.aqr.com/Insights/Datasets
#   • Prof. Jayanth R. Varma's blog — qualitative Indian-markets commentary
#       https://www.jrvarma.in/blog/  (reference reading; not a structured feed)
#
# Cached under  data/reference_data/.  Industry lookups are used as soft
# benchmarks; if a dataset is unavailable the strategies fall back to absolute
# thresholds, so this layer is enhancement, not a hard dependency.

from __future__ import annotations

import io
import os
import warnings
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

warnings.filterwarnings("ignore")

CACHE = Path(
    os.environ.get("REF_DATA_CACHE", Path.home() / "Downloads" / "data" / "reference_data")
)
CACHE.mkdir(parents=True, exist_ok=True)
_UA = {"User-Agent": "Mozilla/5.0 (academic-research)"}

DAMODARAN = "https://pages.stern.nyu.edu/~adamodar/pc/datasets"
# region suffix: "" = US, "Emerg" = emerging (incl. India), "Europe", "Japan", "China", "Global"
DAMO_FILES = {
    "pe": "pedata{r}.xls",
    "roe": "roe{r}.xls",
    "beta": "beta{r}.xls",
    "margin": "margin{r}.xls",
    "wacc": "wacc{r}.xls",
}
FRENCH_3F = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_CSV.zip"
)


def _cached_get(url: str, fname: str) -> bytes:
    f = CACHE / fname
    if f.exists():
        return f.read_bytes()
    r = requests.get(url, headers=_UA, timeout=45)
    r.raise_for_status()
    f.write_bytes(r.content)
    return r.content


# ── Damodaran industry tables ───────────────────────────────────────────────────
def _read_damodaran(raw: bytes) -> Optional[pd.DataFrame]:
    """Find the sheet+row whose header is 'Industry Name' (data lives on the
    'Industry Averages' sheet after ~7 preamble rows) and parse it."""
    try:
        xl = pd.ExcelFile(io.BytesIO(raw))
    except Exception:
        return None
    for sh in xl.sheet_names:
        try:
            probe = pd.read_excel(io.BytesIO(raw), sheet_name=sh, header=None, nrows=15)
        except Exception:
            continue
        hdr = None
        for i in range(len(probe)):
            row = [str(x).strip().lower() for x in probe.iloc[i].tolist()]
            if any(c == "industry name" for c in row):
                hdr = i
                break
        if hdr is None:
            continue
        df = pd.read_excel(io.BytesIO(raw), sheet_name=sh, header=hdr)
        df.columns = [str(c).strip() for c in df.columns]
        name_col = next((c for c in df.columns if c.lower() == "industry name"), None)
        if not name_col:
            continue
        df = df.rename(columns={name_col: "Industry"})
        df = df[df["Industry"].notna()]
        df["_key"] = df["Industry"].astype(str).str.strip().str.lower()
        return df
    return None


def damodaran(metric: str = "pe", region: str = "emerg") -> Optional[pd.DataFrame]:
    """Return the Damodaran industry table for a metric/region (cached).

    Region codes are lowercase ('emerg', 'europe', 'japan', 'china', ''=US).
    Not every metric exists per region (e.g. no emerging PE file) → fall back to
    the US ('') dataset so a benchmark is always available."""
    tmpl = DAMO_FILES.get(metric)
    if not tmpl:
        return None
    for reg in (region, "") if region else ("",):
        fname = tmpl.format(r=reg)
        try:
            raw = _cached_get(f"{DAMODARAN}/{fname}", f"damodaran_{metric}_{reg or 'US'}.xls")
            df = _read_damodaran(raw)
            if df is not None and len(df):
                return df
        except Exception:
            continue
    return None


def industry_metric(
    industry: str, metric: str = "pe", region: str = "emerg", column_hint: Optional[str] = None
) -> Optional[float]:
    """Look up a single industry's benchmark value (best-effort column match)."""
    df = damodaran(metric, region)
    if df is None or not industry:
        return None
    key = str(industry).strip().lower()
    row = df[df["_key"] == key]
    if row.empty:  # loose contains-match
        row = df[df["_key"].str.contains(key[:8], na=False)]
    if row.empty:
        return None
    hints = {
        "pe": ["current pe", "trailing pe", "pe"],
        "roe": ["roe"],
        "beta": ["beta", "average levered beta"],
        "margin": ["net margin", "margin"],
    }
    cands = [column_hint] if column_hint else []
    cands += hints.get(metric, [metric])
    for h in cands:
        for c in df.columns:
            if h and h in str(c).lower():
                try:
                    return float(row.iloc[0][c])
                except (TypeError, ValueError):
                    continue
    return None


# ── Fama-French factors ─────────────────────────────────────────────────────────
def french_factors() -> Optional[pd.DataFrame]:
    """Fama-French 3-factor monthly returns (Mkt-RF, SMB, HML, RF)."""
    try:
        raw = _cached_get(FRENCH_3F, "FF_3factor.zip")
    except Exception:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        txt = z.read(z.namelist()[0]).decode("latin-1")
        rows = []
        for l in txt.splitlines():
            parts = [p.strip() for p in l.split(",")]
            # monthly rows start with a 6-digit YYYYMM and have 4 numeric cols
            if len(parts) >= 5 and parts[0].isdigit() and len(parts[0]) == 6:
                rows.append(parts[:5])
            elif rows and parts[0] and not parts[0].isdigit():
                break  # reached the annual block / footer
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["YYYYMM", "Mkt-RF", "SMB", "HML", "RF"])
        for c in df.columns[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception:
        return None


def damodaran_companies(refresh: bool = False) -> Optional[pd.DataFrame]:
    """Full list of every company in Damodaran's universe (indname.xls): name,
    Exchange:Ticker, Industry Group, Primary Sector, SIC, Country, Broad/Sub group.
    ~48k firms globally. Downloaded once, cached as a compact parquet."""
    pq = CACHE / "damodaran_companies.parquet"
    if pq.exists() and not refresh:
        return pd.read_parquet(pq)
    try:
        raw = _cached_get(f"{DAMODARAN}/indname.xls", "indname.xls")
        df = pd.read_excel(io.BytesIO(raw), sheet_name="By company name")
    except Exception:
        return None
    df.columns = [str(c).strip() for c in df.columns]
    if "Exchange:Ticker" in df.columns:  # split into exch + ticker
        et = df["Exchange:Ticker"].astype(str).str.split(":", n=1, expand=True)
        df["Exchange"] = et[0].str.strip()
        df["Ticker"] = et[1].str.strip() if et.shape[1] > 1 else None
    df = df[df.get("Company Name").notna()]
    df.to_parquet(pq, compression="zstd", index=False)
    return df


def company_industry(name_or_ticker: str) -> Optional[str]:
    """Resolve a company name or ticker to its Damodaran Industry Group."""
    df = damodaran_companies()
    if df is None:
        return None
    q = str(name_or_ticker).strip().lower()
    for col in ("Ticker", "Company Name"):
        if col in df.columns:
            hit = df[df[col].astype(str).str.lower() == q]
            if not hit.empty:
                return hit.iloc[0].get("Industry Group")
    return None


def aqr_links() -> list[str]:
    """List the downloadable AQR dataset (Excel) links from their datasets page.
    AQR publishes each factor set as a separate .xlsx; this surfaces the current
    set so a specific one can be downloaded with _cached_get()."""
    try:
        r = requests.get("https://www.aqr.com/Insights/Datasets", headers=_UA, timeout=30)
        import re

        links = re.findall(r'href="([^"]+\.xlsx?)"', r.text, flags=re.I)
        return sorted({l if l.startswith("http") else "https://www.aqr.com" + l for l in links})
    except Exception:
        return []


def info() -> dict:
    files = sorted(p.name for p in CACHE.glob("*"))
    return {"cache_dir": str(CACHE), "cached_files": files}


if __name__ == "__main__":
    print("Damodaran emerging-market PE table:")
    df = damodaran("pe", "emerg")
    print(None if df is None else f"  {len(df)} industries; cols sample {list(df.columns)[:6]}")
    for ind in ("Software (System & Application)", "Bank (Money Center)", "Auto & Truck"):
        print(f"    PE[{ind}] =", industry_metric(ind, "pe", "emerg"))
    ff = french_factors()
    print("Fama-French 3F rows:", None if ff is None else len(ff))
    print("cache:", info())
