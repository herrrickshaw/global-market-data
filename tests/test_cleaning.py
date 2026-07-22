"""stock_utils.clean_ohlcv — the data hygiene gate."""

import numpy as np
import pandas as pd

from stock_utils import cagr, clean_ohlcv, normalise_debt_to_equity, pct_change


def test_dedup_sort_and_drop_nonpositive():
    idx = pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02", "2024-01-02", "2024-01-04"])
    df = pd.DataFrame(
        {
            "Open": [100, 99, 102, 102, -5],
            "High": [101, 100, 103, 103, 106],
            "Low": [99, 98, 101, 101, 104],
            "Close": [100, 99, 101, 102.5, 105],
            "Volume": [1, 1, 1, 1, 1],
        },
        index=idx,
    )
    out = clean_ohlcv(df, ticker="T", min_bars=1)
    assert out.index.is_monotonic_increasing
    assert not out.index.duplicated().any()
    assert (out["Close"] > 0).all()  # negative-price row dropped
    assert out.loc["2024-01-02", "Close"] == 102.5  # dedup keeps last


def test_ohlc_integrity_repaired(ohlcv):
    df = ohlcv([10, 11, 12])
    df.iloc[0, df.columns.get_loc("High")] = 1  # High below the others
    out = clean_ohlcv(df, min_bars=1)
    assert (out["High"] >= out[["Open", "Low", "Close"]].max(axis=1)).all()


def test_bad_print_neutralised():
    closes = list(np.full(30, 100.0)) + [100000.0]  # +1000x spike
    vols = list(np.full(30, 100_000)) + [0]  # on zero volume
    idx = pd.date_range("2024-01-01", periods=31, freq="D")
    df = pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": vols}, index=idx
    )
    out = clean_ohlcv(df, min_bars=1)
    assert out["Close"].iloc[-1] < 1000  # spike removed/ffilled


def test_empty_and_helpers():
    assert clean_ohlcv(pd.DataFrame()) is None
    assert normalise_debt_to_equity(45.2) == 0.452  # %-format normalised
    assert normalise_debt_to_equity(0.8) == 0.8
    assert pct_change(110, 100) == 10
    assert pct_change(1, 0) is None
    assert round(cagr(200, 100, 1), 1) == 100.0
