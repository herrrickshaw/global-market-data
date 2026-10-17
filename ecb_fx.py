#!/usr/bin/env python3
# ecb_fx.py
# =========
# Official keyless FX reference rates (European Central Bank). liquidity.py converts
# turnover to USD with a live yfinance pull and no committed, dated rate history; this
# gives an official, auditable EUR-base series for ~29 currencies (USD, JPY, GBP, CHF,
# SEK, DKK, CNY, KRW, INR, BRL, AUD, CAD, HKD, SGD, ZAR, ...).
#
#   https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml  (last ~90 days)
#
# Output (new file only):  reference_open/ecb_fx_hist_90d.csv  [date, currency, per_eur]
# Quote convention: units of foreign currency per 1 EUR, fixed ~14:15 CET, TARGET days
# only, reference-only (not tradable). Note: no TWD or SAR in the ECB set.
# Usage:  python ecb_fx.py

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"


def parse(xml_text: str) -> pd.DataFrame:
    rows = []
    for el in ET.fromstring(xml_text).iter():
        if el.tag.endswith("Cube") and "time" in el.attrib:
            for c in el:
                rows.append((el.attrib["time"], c.attrib["currency"], float(c.attrib["rate"])))
    return pd.DataFrame(rows, columns=["date", "currency", "per_eur"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "reference_open" / "ecb_fx_hist_90d.csv"))
    a = ap.parse_args()
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (market-research)"}, timeout=60)
    r.raise_for_status()
    df = parse(r.text)
    df.to_csv(a.out, index=False)
    print(f"{len(df)} rows, {df.date.nunique()} dates, {df.currency.nunique()} currencies "
          f"({df.date.min()}..{df.date.max()}) -> {a.out}")


if __name__ == "__main__":
    main()
