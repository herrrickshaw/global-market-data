#!/usr/bin/env python3
# market_performance.py
# =====================
# 5-year performance of each of the 20 covered markets, measured at the benchmark
# INDEX level (clean market proxy; our per-stock seeds only hold ~1yr). Computes
# total return, CAGR, annualised volatility, max drawdown, Sharpe (rf=0), plus
# 1-year and YTD returns. Ranks markets and saves a referenceable parquet + xlsx.
#
#   python3 market_performance.py            # all markets, 5y
#   from market_performance import analyse, load
#
# Educational/research only. NOT investment advice. Index returns are price-only
# (ex-dividends) and in local currency unless the index is USD-denominated.

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from data_sources import fetch as multi_fetch

OUT_PARQUET = Path(__file__).parent / "cache_seed" / "market_performance_5y.parquet"

# market -> (benchmark index yfinance ticker, index name)
INDEX = {
    "US": ("^GSPC", "S&P 500"),
    "IN": ("^NSEI", "Nifty 50"),
    "CN": ("000001.SS", "SSE Composite"),
    "JP": ("^N225", "Nikkei 225"),
    "EU": ("^STOXX50E", "EURO STOXX 50"),
    "HK": ("^HSI", "Hang Seng"),
    "KR": ("^KS11", "KOSPI"),
    "TW": ("^TWII", "TAIEX"),
    "CA": ("^GSPTSE", "S&P/TSX"),
    "UK": ("^FTSE", "FTSE 100"),
    "DE": ("^GDAXI", "DAX"),
    "AU": ("^AXJO", "ASX 200"),
    "SG": ("^STI", "Straits Times"),
    "SA": ("^TASI.SR", "Tadawul TASI"),
    "BR": ("^BVSP", "Bovespa"),
    "CH": ("^SSMI", "SMI"),
    "ZA": ("^J203.JO", "JSE All Share"),
    "SE": ("^OMX", "OMX Stockholm 30"),
    "FI": ("^OMXH25", "OMX Helsinki 25"),
    "DK": ("^OMXC25", "OMX Copenhagen 25"),
}


def _metrics(close: pd.Series) -> dict:
    close = close.dropna()
    if len(close) < 60:
        return {}
    first, last = float(close.iloc[0]), float(close.iloc[-1])
    yrs = (close.index[-1] - close.index[0]).days / 365.25
    tot = (last / first - 1) * 100
    cagr = ((last / first) ** (1 / yrs) - 1) * 100 if yrs > 0 and first > 0 else None
    dr = close.pct_change().dropna()
    vol = float(dr.std() * np.sqrt(252) * 100)
    sharpe = float((dr.mean() * 252) / (dr.std() * np.sqrt(252))) if dr.std() else None
    mdd = float(((close - close.cummax()) / close.cummax()).min() * 100)
    # 1y and YTD
    one_y = None
    yr_ago = close.index[-1] - pd.Timedelta(days=365)
    s1 = close[close.index >= yr_ago]
    if len(s1) > 1:
        one_y = (float(s1.iloc[-1]) / float(s1.iloc[0]) - 1) * 100
    ytd = None
    jan = close[close.index >= pd.Timestamp(close.index[-1].year, 1, 1)]
    if len(jan) > 1:
        ytd = (float(jan.iloc[-1]) / float(jan.iloc[0]) - 1) * 100
    return {
        "Total_5y%": round(tot, 1),
        "CAGR%": round(cagr, 1) if cagr else None,
        "Vol_ann%": round(vol, 1),
        "MaxDD%": round(mdd, 1),
        "Sharpe": round(sharpe, 2) if sharpe else None,
        "Return_1y%": round(one_y, 1) if one_y else None,
        "YTD%": round(ytd, 1) if ytd else None,
        "Years": round(yrs, 1),
    }


def analyse(verbose: bool = True) -> pd.DataFrame:
    tickers = {v[0]: m for m, v in INDEX.items()}
    if verbose:
        print("Fetching 5y index history for 20 markets …")
    data = multi_fetch(
        list(tickers), order=("yahoo", "stooq"), period="5y", min_bars=60, verbose=False
    )
    rows = []
    for m, (tkr, name) in INDEX.items():
        df = data.get(tkr)
        if df is None or df.empty:
            if verbose:
                print(f"  {m} ({tkr}): no data")
            continue
        met = _metrics(df["Close"])
        if met:
            rows.append({"Market": m, "Index": name, "Ticker": tkr, **met})
    res = pd.DataFrame(rows)
    if not res.empty:
        res = res.sort_values("CAGR%", ascending=False).reset_index(drop=True)
        res["as_of"] = pd.Timestamp.today().normalize()
        res.to_parquet(OUT_PARQUET, compression="zstd", index=False)
        res.to_excel(OUT_PARQUET.with_suffix(".xlsx"), index=False)
    if verbose and not res.empty:
        print("\n" + "=" * 78)
        print("  20-MARKET 5-YEAR PERFORMANCE (benchmark indices, ranked by CAGR)")
        print("=" * 78)
        print(res.to_string(index=False))
        print(f"\nsaved → {OUT_PARQUET.name} (+ .xlsx)")
        print("Educational/research only. NOT investment advice.")
    return res


def load() -> pd.DataFrame:
    return pd.read_parquet(OUT_PARQUET) if OUT_PARQUET.exists() else pd.DataFrame()


if __name__ == "__main__":
    analyse()
