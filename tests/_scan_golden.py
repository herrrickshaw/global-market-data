"""Helpers for characterisation tests of the full_*_market_scan ``main`` functions.

Golden files live in tests/golden/. Set UPDATE_GOLDEN=1 to (re)capture them.
No network, no files outside tmp, no mail: every external dependency is faked.
"""

import contextlib
import io
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

GOLDEN_DIR = Path(__file__).parent / "golden"


class FrozenDatetime:
    """Stand-in for the ``datetime`` class: fixed now(), real strftime."""

    @classmethod
    def now(cls, tz=None):
        import datetime as _d

        return _d.datetime(2026, 1, 15, 10, 30, 0)


def synthetic_ohlc(seed: int, n: int = 260, start: float = 50.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(0.1, 1.0, n))
    closes = np.abs(closes) + 5
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(n, 100_000),
        },
        index=idx,
    )


def fake_fund(sym: str) -> dict:
    k = sum(ord(c) for c in sym)
    return {
        "name": f"Name {sym}",
        "sector": "Tech" if k % 2 else "Fin",
        "f_score": k % 10,
        "f_strong": k % 3 == 0,
        "qualifies_cc": k % 2 == 0,
        "cc_score": k % 5,
        "Revenue_CAGR_%": round(k / 7, 2),
        "ROE_avg_%": round(k / 11, 2),
        "qualifies_mf": k % 4 == 0,
        "ROIC_%": round(k / 13, 2),
        "Earnings_Yield_%": round(k / 17, 2),
        "qualifies_bc": k % 5 == 0,
        "Sales_Growth_YoY_%": round(k / 19, 2),
        "Profit_Growth_YoY_%": round(k / 23, 2),
        "Net_Profit_$M": k,
        "error": "boom" if sym.endswith("Z") else "",
    }


def run_captured(fn, *args, **kwargs):
    """Run fn, return (stdout, result)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return buf.getvalue(), result


def check_golden(name: str, payload) -> None:
    text = json.dumps(payload, indent=1, sort_keys=True, default=str)
    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_text(text + "\n")
        return
    assert path.read_text() == text + "\n"
