"""bhavcopy_store — LMDB build / get round-trip on an isolated temp cache."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path))
    import importlib

    import bhavcopy_store

    importlib.reload(bhavcopy_store)  # pick up the temp BHAV_CACHE
    return bhavcopy_store


def _frame(n=120, start=100.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = np.linspace(start, start * 1.3, n)
    return pd.DataFrame(
        {"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.full(n, 50_000)},
        index=idx,
    )


def test_build_get_roundtrip(store):
    hist = {"AAA": _frame(), "BBB.NS": _frame(start=50)}
    n = store.build(hist, verbose=False)
    assert n == 2
    info = store.info()
    assert info["symbols"] == 2
    d = store.get("AAA")
    assert d is not None and len(d) == 120
    assert list(d.columns) == ["Open", "High", "Low", "Close", "Volume"]
    # prices restored to float64 and values preserved within float32 tolerance
    assert abs(float(d["Close"].iloc[-1]) - 130.0) < 0.1
    assert store.get("ZZZ") is None  # missing key
    assert set(store.symbols()) == {"AAA", "BBB.NS"}


def test_get_many_only_touches_requested(store):
    store.build({"AAA": _frame(), "BBB": _frame(), "CCC": _frame()}, verbose=False)
    got = store.get_many(["AAA", "CCC", "NOPE"])
    assert set(got) == {"AAA", "CCC"}
