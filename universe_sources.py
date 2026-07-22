#!/usr/bin/env python3
# universe_sources.py
# ===================
# Full tradable-universe providers per market, preferring OFFICIAL / government /
# exchange sources over scraping. Each provider returns yfinance-style tickers.
#
#   US — SEC EDGAR company_tickers.json          (US government, ~10k filers)
#   IN — NSE+BSE bhavcopy symbols                (exchange official)
#   JP — JPX listed-issue master (.T)            (exchange official, ~3.8k)
#   KR — KRX KIND KOSPI+KOSDAQ (.KS/.KQ)         (exchange official, ~2.6k)
#   SG — SGX securities API (.SI)                (exchange official, ~620)
#   CN — Eastmoney A-share list (.SS/.SZ)        (full A-share board, ~5.5k)
#   EU — STOXX-large-cap curated (multi-venue)   (index constituents)
#
# Government/official endpoints are documented inline so they can be audited.

from __future__ import annotations

import warnings
from typing import List, Optional

import pandas as pd
import requests

warnings.filterwarnings("ignore")
_UA = {"User-Agent": "Mozilla/5.0 (market-research)"}
_SEC_UA = {"User-Agent": "market-research umashankartd1991@gmail.com"}  # SEC requires contact


def us_sec() -> List[str]:
    """US: SEC EDGAR official ticker registry (government source)."""
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=_SEC_UA, timeout=30)
    r.raise_for_status()
    return sorted(
        {v["ticker"].upper().replace(".", "-") for v in r.json().values() if v.get("ticker")}
    )


def sg_sgx() -> List[str]:
    """SG: SGX official securities API → stocks + reits + business trusts."""
    r = requests.get(
        "https://api.sgx.com/securities/v1.1?excludetypes=bonds&params=nc%2Ctype",
        headers=_UA,
        timeout=30,
    )
    r.raise_for_status()
    keep = {"stocks", "reits", "businesstrusts"}
    return sorted(
        {
            f"{d['nc']}.SI"
            for d in r.json()["data"]["prices"]
            if d.get("nc") and d.get("type") in keep
        }
    )


def cn_eastmoney() -> List[str]:
    """CN: full A-share universe via Eastmoney push2 list API.
    fs boards: m:1 t:2/t:23 (SSE A/STAR), m:0 t:6/t:80 (SZSE A/ChiNext)."""
    out, pn, total = [], 1, None
    fs = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80"
    hosts = [
        "https://push2.eastmoney.com",
        "https://82.push2.eastmoney.com",
        "http://80.push2.eastmoney.com",
        "https://push2delay.eastmoney.com",
    ]
    while True:
        data = None
        for host in hosts:
            try:
                r = requests.get(
                    f"{host}/api/qt/clist/get",
                    params={"pn": pn, "pz": 200, "fs": fs, "fields": "f12,f13"},
                    headers=_UA,
                    timeout=30,
                )
                data = r.json().get("data") or {}
                break
            except Exception:
                continue
        if not data:
            break
        total = total or data.get("total")
        diff = data.get("diff") or []
        if isinstance(diff, dict):  # some hosts key diff by index
            diff = list(diff.values())
        if not diff:
            break
        for d in diff:
            code, mkt = d.get("f12"), d.get("f13")  # f13: 1=Shanghai, 0=Shenzhen
            if code:
                out.append(f"{code}.SS" if mkt == 1 else f"{code}.SZ")
        if total and len(out) >= total:
            break
        pn += 1
    return sorted(set(out))


def jp_jpx() -> List[str]:
    from full_japan_market_scan import fetch_tse_universe_jpx, fetch_tse_universe_kabupy

    uni = []
    try:
        uni = fetch_tse_universe_jpx()
    except Exception:
        uni = []
    if not uni:
        uni = fetch_tse_universe_kabupy()
    return sorted(
        {u.get("yf_ticker") or f"{u['code']}.T" for u in uni if u.get("code") or u.get("yf_ticker")}
    )


def kr_krx() -> List[str]:
    from full_korea_market_scan import build_krx_universe

    return sorted(
        {f"{u['code']}{u.get('yf_suffix', '.KS')}" for u in build_krx_universe() if u.get("code")}
    )


def in_bhavcopy() -> List[str]:
    """IN: symbols present in the bhavcopy assembled/cleaned cache (exchange EOD)."""
    try:
        import bhavcopy_store as s

        syms = s.symbols()
        if syms:
            return sorted(syms)
    except Exception:
        pass
    from bhavcopy_history import fetch_history

    return sorted(fetch_history(verbose=False).keys())


# Euronext live (official) → yfinance suffix by listing venue.
_EURONEXT_SUFFIX = {
    "Euronext Paris": ".PA",
    "Euronext Amsterdam": ".AS",
    "Euronext Brussels": ".BR",
    "Euronext Lisbon": ".LS",
    "Euronext Dublin": ".IR",
    "Oslo Børs": ".OL",
    "Euronext Milan": ".MI",
    "Borsa Italiana": ".MI",
}


def euronext_equities(mics: str = "XPAR,XAMS,XBRU,XLIS,XMSM,XDUB,XOSL,XMIL") -> List[str]:
    """Full Euronext equity list from live.euronext.com (official CSV export),
    mapped to yfinance tickers (Symbol + venue suffix). Covers Paris, Amsterdam,
    Brussels, Lisbon, Dublin, Oslo and Milan."""
    import io as _io

    url = f"https://live.euronext.com/en/pd_es/data/stocks/download?mics={mics}"
    h = {**_UA, "Referer": "https://live.euronext.com/en/products/equities/list"}
    r = requests.get(url, headers=h, timeout=40)
    r.raise_for_status()
    df = pd.read_csv(_io.BytesIO(r.content), sep=";", encoding="utf-8-sig")
    df = df[df["ISIN"].notna() & df["Symbol"].notna()]
    out = []
    for _, row in df.iterrows():
        venue = str(row["Market"]).split(",")[0].strip()  # primary listing
        suf = _EURONEXT_SUFFIX.get(venue)
        if suf:
            out.append(f"{str(row['Symbol']).strip()}{suf}")
    return sorted(set(out))


def investpy_universe(
    countries=(
        "france",
        "germany",
        "united kingdom",
        "italy",
        "spain",
        "netherlands",
        "switzerland",
        "sweden",
        "belgium",
        "norway",
        "denmark",
        "finland",
        "portugal",
        "ireland",
        "austria",
        "poland",
    )
):
    """European stock LISTINGS via investpy (Investing.com bundled lists): returns
    a DataFrame of name / full_name / isin / symbol / currency / country.

    NOTE: investpy's *price* endpoint is 403-blocked by Investing.com and its
    symbols are Investing.com codes (not yfinance), so this is a DISCOVERY /
    cross-reference source — prices are still fetched via yfinance/Euronext."""
    try:
        import investpy
    except ImportError:
        return None
    frames = []
    for c in countries:
        try:
            frames.append(investpy.get_stocks(country=c))
        except Exception:
            continue
    import pandas as _pd

    return _pd.concat(frames, ignore_index=True) if frames else None


# EU venues outside Euronext (London/Xetra/SIX) have no single free official feed;
# Euronext is pulled live, the rest are curated large/mid-caps.
def eu_curated() -> List[str]:
    from full_european_market_scan import EURO_STOXX_50_META

    extra = [
        # FTSE (London .L)
        "HSBA.L",
        "BP.L",
        "SHEL.L",
        "AZN.L",
        "ULVR.L",
        "GSK.L",
        "RIO.L",
        "GLEN.L",
        "BATS.L",
        "DGE.L",
        "LSEG.L",
        "REL.L",
        "NG.L",
        "BARC.L",
        "LLOY.L",
        "NWG.L",
        "VOD.L",
        "TSCO.L",
        "PRU.L",
        "AAL.L",
        "RR.L",
        "BA.L",
        "IMB.L",
        "CPG.L",
        "NXT.L",
        "STAN.L",
        "AV.L",
        # DAX (Xetra .DE) beyond stoxx50
        "SAP.DE",
        "SIE.DE",
        "DTE.DE",
        "MBG.DE",
        "VOW3.DE",
        "BMW.DE",
        "BAS.DE",
        "BAYN.DE",
        "RWE.DE",
        "EOAN.DE",
        "DB1.DE",
        "DBK.DE",
        "ADS.DE",
        "MUV2.DE",
        "IFX.DE",
        "HEN3.DE",
        # CAC (Paris .PA) beyond stoxx50
        "MC.PA",
        "OR.PA",
        "RMS.PA",
        "TTE.PA",
        "SAN.PA",
        "BNP.PA",
        "AIR.PA",
        "SU.PA",
        "CS.PA",
        "EL.PA",
        "DG.PA",
        "BN.PA",
        "KER.PA",
        "SAF.PA",
        "STLAP.PA",
        "ENGI.PA",
        "VIE.PA",
        # AEX (Amsterdam .AS) / SIX (.SW)
        "ASML.AS",
        "PRX.AS",
        "INGA.AS",
        "AD.AS",
        "PHIA.AS",
        "WKL.AS",
        "HEIA.AS",
        "NESN.SW",
        "ROG.SW",
        "NOVN.SW",
        "UBSG.SW",
        "ZURN.SW",
        "ABBN.SW",
        "CFR.SW",
    ]
    return sorted(set(EURO_STOXX_50_META.keys()) | set(extra))


def eu() -> List[str]:
    """Full EU universe = live Euronext (official) ∪ curated non-Euronext majors
    (London .L, Xetra .DE, SIX .SW)."""
    tickers = set(eu_curated())
    try:
        tickers |= set(euronext_equities())
    except Exception:
        pass
    return sorted(tickers)


# ── Damodaran-derived universes (for major exchanges without a free official feed) ─
# His company master (indname.xls) carries Country + Exchange:Ticker for ~48k firms;
# we map each exchange code to the yfinance suffix and reconstruct tickers.
_DAMO_EXCH_SUFFIX = {
    "SEHK": ".HK",
    "TWSE": ".TW",
    "TPEX": ".TWO",
    "TSX": ".TO",
    "TSXV": ".V",
    "CNSX": ".CN",
    "ASX": ".AX",
    "BOVESPA": ".SA",
    "JSE": ".JO",
    "SASE": ".SR",
    "LSE": ".L",
    "AIM": ".L",
    "XTRA": ".DE",
    "DB": ".F",
    "HMSE": ".F",
    "SWX": ".SW",
}
# Nordic exchange codes collide (all "OM"), so resolve those by country instead.
_COUNTRY_SUFFIX = {"Sweden": ".ST", "Denmark": ".CO", "Finland": ".HE", "Norway": ".OL"}


def from_damodaran(country: str, allowed_exch: Optional[set] = None) -> List[str]:
    """Build a yfinance-ticker universe for a country from Damodaran's master."""
    from reference_data import damodaran_companies

    df = damodaran_companies()
    if df is None:
        return []
    sub = df[df["Country"] == country]
    csuf = _COUNTRY_SUFFIX.get(country)
    out = []
    for _, r in sub.iterrows():
        exch, tkr = str(r.get("Exchange", "")).strip(), r.get("Ticker")
        if not tkr or (allowed_exch and exch not in allowed_exch):
            continue
        suf = csuf or _DAMO_EXCH_SUFFIX.get(exch)
        if not suf:
            continue
        t = str(tkr).strip()
        if suf == ".HK":  # HK codes are zero-padded to 4
            t = t.zfill(4)
        out.append(t + suf)
    return sorted(set(out))


PROVIDERS = {
    # official / exchange feeds
    "US": us_sec,
    "IN": in_bhavcopy,
    "JP": jp_jpx,
    "KR": kr_krx,
    "SG": sg_sgx,
    "CN": cn_eastmoney,
    "EU": eu,
    # major exchanges via Damodaran master (key factor: # listed companies)
    "HK": lambda: from_damodaran("Hong Kong", {"SEHK"}),
    "TW": lambda: from_damodaran("Taiwan", {"TWSE", "TPEX"}),
    "CA": lambda: from_damodaran("Canada", {"TSX", "TSXV", "CNSX"}),
    "AU": lambda: from_damodaran("Australia", {"ASX"}),
    "BR": lambda: from_damodaran("Brazil", {"BOVESPA"}),
    "ZA": lambda: from_damodaran("South Africa", {"JSE"}),
    "SA": lambda: from_damodaran("Saudi Arabia", {"SASE"}),
    "SE": lambda: from_damodaran("Sweden"),
    "DK": lambda: from_damodaran("Denmark"),
    "FI": lambda: from_damodaran("Finland"),
    "UK": lambda: from_damodaran("United Kingdom", {"LSE", "AIM"}),
    "DE": lambda: from_damodaran("Germany", {"XTRA", "DB", "HMSE"}),
    "CH": lambda: from_damodaran("Switzerland", {"SWX"}),
}


def get_universe(market: str) -> List[str]:
    return PROVIDERS[market.upper()]()


if __name__ == "__main__":
    import sys

    mkts = [m.upper() for m in sys.argv[1:]] or list(PROVIDERS)
    for m in mkts:
        try:
            u = get_universe(m)
            print(f"  {m}: {len(u):>6} tickers   e.g. {u[:4]}")
        except Exception as e:
            print(f"  {m}: ERROR {str(e)[:70]}")
