#!/usr/bin/env python3
# custom_screener.py
# ==================
# Evaluate stocks on YOUR OWN parameters — no need to write a strategy module.
# You pass simple rules like {"rsi14": ("<", 35), "ret_63": (">", 10), "roe": (">=", 15)}
# and the screener computes the metrics (technical ones straight from OHLCV,
# fundamentals from whatever you supply) and returns the stocks that pass, ranked.
#
#   from custom_screener import screen, compute_metrics, METRICS
#   df = screen(stocks, {"above_200dma": ("==", True), "rsi14": ("<", 60),
#                        "dist_52w_high": ("<", 8)}, rank_by="ret_126", top=25)
#
# `stocks` is an iterable of strategies.base.StockData. Metric names you can use
# are listed in METRICS (technical) plus any key present in a stock's fundamentals.

from __future__ import annotations

import operator
from typing import Callable, Dict, Iterable, List, Optional, Union

import numpy as np
import pandas as pd

from strategies.base import StockData

OPS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

# technical metrics derived from OHLCV (documented for users)
METRICS = {
    "ltp": "last close price",
    "ret_21": "% return over ~1 month (21 trading days)",
    "ret_63": "% return over ~1 quarter",
    "ret_126": "% return over ~6 months",
    "ret_252": "% return over ~1 year",
    "vol_ann": "annualised volatility (%)",
    "sma50": "50-day simple moving average",
    "sma200": "200-day simple moving average",
    "above_200dma": "True if price > 200-DMA",
    "above_50dma": "True if price > 50-DMA",
    "rsi14": "14-day Relative Strength Index",
    "dist_52w_high": "% below the 52-week high",
    "dist_52w_low": "% above the 52-week low",
    "avg_vol_20": "20-day average volume",
    "vol_ratio": "today's volume / 20-day average",
    "atr14_pct": "14-day Average True Range as % of price",
    "max_drawdown": "worst peak-to-trough decline over the window (%)",
}


def _rsi(close: pd.Series, n: int = 14) -> Optional[float]:
    if len(close) < n + 1:
        return None
    d = close.diff().dropna()
    up = d.clip(lower=0).rolling(n).mean().iloc[-1]
    dn = (-d.clip(upper=0)).rolling(n).mean().iloc[-1]
    if dn == 0:
        return 100.0
    rs = up / dn
    return float(100 - 100 / (1 + rs))


def _ret(close: pd.Series, n: int) -> Optional[float]:
    if len(close) <= n:
        return None
    return float((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100)


def _ccc(f: dict):
    """Cash Conversion Cycle (days) = DIO + DSO - DPO, if inputs present."""
    inv, rec, pay = f.get("inventory"), f.get("receivables"), f.get("payables")
    cogs, rev = f.get("cogs"), f.get("revenue")

    def d(n, dn):
        return (n / dn * 365.0) if (n is not None and dn not in (None, 0)) else 0

    if all(x is None for x in (inv, rec, pay)):
        return f.get("ccc")
    return d(inv, cogs) + d(rec, rev) - d(pay, cogs)


def compute_metrics(stock: StockData) -> Dict[str, float]:
    """All technical metrics from OHLCV + the stock's fundamentals merged in."""
    m: Dict[str, float] = dict(stock.fundamentals)  # fundamentals first
    ccc = _ccc(stock.fundamentals)  # expose CCC as a metric
    if ccc is not None:
        m["ccc"] = round(ccc, 1)
    df = stock.ohlcv
    if df is None or df.empty:
        return m
    close = df["Close"].astype(float)
    ltp = float(close.iloc[-1])
    m["ltp"] = round(ltp, 2)
    for n, key in [(21, "ret_21"), (63, "ret_63"), (126, "ret_126"), (252, "ret_252")]:
        v = _ret(close, n)
        if v is not None:
            m[key] = round(v, 2)
    if len(close) > 5:
        dr = close.pct_change().dropna()
        m["vol_ann"] = round(float(dr.std() * np.sqrt(252) * 100), 2)
        roll_max = close.cummax()
        m["max_drawdown"] = round(float(((close - roll_max) / roll_max).min() * 100), 2)
    if len(close) >= 50:
        s50 = float(close.tail(50).mean())
        m["sma50"] = round(s50, 2)
        m["above_50dma"] = bool(ltp > s50)
    if len(close) >= 200:
        s200 = float(close.tail(200).mean())
        m["sma200"] = round(s200, 2)
        m["above_200dma"] = bool(ltp > s200)
    r = _rsi(close)
    if r is not None:
        m["rsi14"] = round(r, 1)
    win = df.tail(252)
    hi = float(win["High"].max()) if "High" in win else float(win["Close"].max())
    lo = float(win["Low"].min()) if "Low" in win else float(win["Close"].min())
    if hi > 0:
        m["dist_52w_high"] = round((hi - ltp) / hi * 100, 2)
    if lo > 0:
        m["dist_52w_low"] = round((ltp - lo) / lo * 100, 2)
    if "Volume" in df.columns and len(df) >= 21:
        v20 = float(df["Volume"].tail(21).iloc[:-1].mean())
        m["avg_vol_20"] = round(v20, 0)
        if v20 > 0:
            m["vol_ratio"] = round(float(df["Volume"].iloc[-1]) / v20, 2)
    if {"High", "Low", "Close"}.issubset(df.columns) and len(df) >= 15:
        h, l, c = df["High"], df["Low"], df["Close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        if ltp:
            m["atr14_pct"] = round(atr / ltp * 100, 2)
    return m


Criteria = Union[Dict[str, tuple], Callable[[Dict], bool]]


def evaluate(stock: StockData, criteria: Criteria) -> tuple[bool, Dict]:
    """Return (passed, metrics). criteria is either a callable(metrics)->bool, or
    a dict {metric: (op, value)} where op is one of >, >=, <, <=, ==, !=."""
    m = compute_metrics(stock)
    if callable(criteria):
        try:
            return bool(criteria(m)), m
        except Exception:
            return False, m
    for field, rule in criteria.items():
        val = m.get(field)
        if val is None:
            return False, m
        op, target = rule
        fn = OPS.get(op)
        if fn is None:
            raise ValueError(f"unknown operator {op!r}; use one of {list(OPS)}")
        try:
            if not fn(val, target):
                return False, m
        except TypeError:
            return False, m
    return True, m


def screen(
    stocks: Iterable[StockData],
    criteria: Criteria,
    rank_by: Optional[str] = None,
    top: Optional[int] = None,
    ascending: bool = False,
    show: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Evaluate every stock; return a DataFrame of the ones that pass.

    rank_by — metric to sort by (default: keep input order)
    top     — keep only the best N
    show    — metric columns to include (default: the ones used in criteria + rank_by)
    """
    rows = []
    for s in stocks:
        ok, m = evaluate(s, criteria)
        if ok:
            rows.append({"Symbol": s.symbol, "Market": s.market, **m})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if rank_by and rank_by in df.columns:
        df = df.sort_values(rank_by, ascending=ascending)
    if top:
        df = df.head(top)
    if show:
        cols = ["Symbol", "Market"] + [c for c in show if c in df.columns]
        df = df[cols]
    return df.reset_index(drop=True)


if __name__ == "__main__":
    # demo on real cached data: oversold-but-uptrend momentum screen
    import bhavcopy_store as store

    syms = store.symbols()[:800]
    stocks = [StockData(s, "IN", ohlcv=d) for s, d in store.get_many(syms).items()]
    print(f"loaded {len(stocks)} stocks")
    rules = {
        "above_200dma": ("==", True),
        "rsi14": ("<", 60),
        "dist_52w_high": ("<", 12),
        "ret_126": (">", 5),
    }
    out = screen(
        stocks,
        rules,
        rank_by="ret_126",
        top=15,
        show=["ltp", "ret_126", "rsi14", "dist_52w_high", "above_200dma"],
    )
    print(out.to_string(index=False))
