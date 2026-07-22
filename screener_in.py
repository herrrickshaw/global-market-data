#!/usr/bin/env python3
# screener_in.py
# ==============
# Fetch a screener.in public screen as a DataFrame of NSE symbols + its columns.
# screener.in computes Indian fundamentals (incl. Cash Conversion Cycle) that the
# bhavcopy feed doesn't carry, so this fills the India fundamentals gap for screens
# like the Cash Conversion Cycle screen (screens/228040).
#
#   from screener_in import fetch_screen, ccc_screen
#   df = ccc_screen()          # India stocks passing the low-CCC screen
#
# Returns the symbols (from /company/<SYMBOL>/ links) aligned to the table rows.

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore")
_UA = {"User-Agent": "Mozilla/5.0 (research)"}
CCC_SCREEN = "https://www.screener.in/screens/228040/cash-conversion-cycle/"


_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _header(html: str) -> list:
    """Column names from the first table's <th> cells (handles nested markup)."""
    th = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
    names = []
    for t in th:
        txt = re.sub(r"\s+", " ", _TAG.sub("", t)).strip()
        if txt:
            names.append(txt)
        if len(names) >= 15:
            break
    return names


def fetch_screen(url: str, max_pages: int = 40, verbose: bool = True) -> pd.DataFrame:
    """Paginate a screener.in screen → DataFrame with a Symbol column.

    Parsed row-by-row (each <tr> pairs its /company/<SYMBOL>/ link with its own
    cells), which is robust to repeated header rows / median rows across pages."""
    rows, cols, seen = [], None, set()
    for page in range(1, max_pages + 1):
        r = requests.get(f"{url}?page={page}", headers=_UA, timeout=25)
        if r.status_code != 200:
            break
        if cols is None:
            cols = _header(r.text)
        page_syms = set()
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
            sym = re.search(r"/company/([A-Z0-9&._-]+)/", tr)
            if not sym:
                continue
            cells = [_TAG.sub("", c).replace("&amp;", "&").strip() for c in _CELL.findall(tr)]
            if not cells:
                continue
            s = sym.group(1)
            if s in seen:
                continue
            rows.append([s] + cells)
            seen.add(s)
            page_syms.add(s)
        if not page_syms:
            break
    if not rows:
        return pd.DataFrame()
    ncol = max(len(r) for r in rows)
    header = ["Symbol"] + (cols or [])
    header = (header + [f"c{i}" for i in range(len(header), ncol)])[:ncol]
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    df = pd.DataFrame(rows, columns=header)
    if verbose:
        print(f"  screener.in: {len(df)} stocks from {url.split('/')[-2]}")
    return df


def ccc_screen() -> pd.DataFrame:
    """The Cash Conversion Cycle screen → Symbol + Cash Cycle (+ key columns)."""
    df = fetch_screen(CCC_SCREEN)
    if df.empty:
        return df
    ren = {c: c for c in df.columns}
    for c in df.columns:
        if "cash cycle" in c.lower():
            ren[c] = "Cash_Cycle"
        elif c.lower().startswith("cmp"):
            ren[c] = "CMP"
        elif c.lower().startswith("mar cap"):
            ren[c] = "MarCap_Cr"
        elif c.lower().startswith("roce"):
            ren[c] = "ROCE"
    df = df.rename(columns=ren)
    keep = [
        c
        for c in ["Symbol", "Name", "CMP", "MarCap_Cr", "P/E", "ROCE", "Cash_Cycle"]
        if c in df.columns
    ]
    return df[keep]


def ccc_map() -> dict:
    """{NSE symbol: cash conversion cycle (days)} from the screen."""
    df = ccc_screen()
    if df.empty or "Cash_Cycle" not in df.columns:
        return {}
    return dict(zip(df["Symbol"], pd.to_numeric(df["Cash_Cycle"], errors="coerce")))


if __name__ == "__main__":
    df = ccc_screen()
    print(df.to_string(index=False))
    out = Path(__file__).parent / "cache_seed" / "india_ccc_screen.parquet"
    df.to_parquet(out, index=False)
    print(f"\nsaved → {out.name}")
