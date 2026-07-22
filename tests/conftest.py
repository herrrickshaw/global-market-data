"""Shared pytest fixtures + path setup so `import screener_kit` etc. resolve."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# repo root (parent of tests/) on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ohlcv(closes, vols=None):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    closes = np.asarray(closes, dtype=float)
    if vols is None:
        vols = np.full(len(closes), 100_000)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": vols,
        },
        index=idx,
    )


@pytest.fixture
def ohlcv():
    return _ohlcv


@pytest.fixture
def uptrend(ohlcv):
    # 260 bars rising 100 -> ~360; price well above its 200-DMA
    return ohlcv(np.linspace(100, 360, 260))


@pytest.fixture
def breakout(ohlcv):
    # flat box then a final high-volume push to a new 52-week high
    closes = list(np.full(250, 100.0)) + [101, 102, 103, 104, 112]
    vols = list(np.full(250, 100_000)) + [100_000] * 4 + [400_000]
    return ohlcv(closes, vols)
