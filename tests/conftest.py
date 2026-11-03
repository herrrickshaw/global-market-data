"""Shared pytest fixtures + path setup so `import screener_kit` etc. resolve."""

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# repo root (parent of tests/) on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _no_network(*_args, **_kwargs):
    raise RuntimeError("network access is disabled in tests; inject a fake client")


def _stub_missing_optional_modules(modules):
    """Register a stand-in for each optional dependency that is not installed.

    The report/scan modules only need ``import yfinance`` etc. to succeed at import
    time; every test that exercises them injects its own fake client, so no real
    package (or network) is needed on a clean runner.
    """
    for name, attrs in modules.items():
        if name in sys.modules or importlib.util.find_spec(name) is not None:
            continue
        stub = types.ModuleType(name)
        for attr in attrs:
            setattr(stub, attr, _no_network)
        sys.modules[name] = stub


_stub_missing_optional_modules({"yfinance": ("download", "Ticker")})


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
