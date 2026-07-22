# Tests for the popular-screens validation module. Pure logic, no network.
import pandas as pd


_DF = pd.DataFrame({
    "Symbol": ["A", "B", "C", "D"],
    "Close": [100.0, 50.0, 200.0, 80.0],
    "SMA200": [90.0, 55.0, 199.0, 100.0],
    "RSI14": [60, 25, 80, 45],
    "PctFromHigh": [-1, -40, 0, -25],
    "PctFromLow": [40, 2, 60, 5],
    "Ret126": [30, -10, 120, 5],
    "Ret252": [50, -20, 150, 10],
    "Above200DMA": [True, False, True, False],
    "GoldenCross": [True, False, True, False],
    "Liquidity": ["High", "High", "Medium", "High"],
})


def _patch(monkeypatch):
    import validation as v
    monkeypatch.setattr(v.sl, "serving", lambda m: _DF)
    return v


def test_local_screens_compute(monkeypatch):
    v = _patch(monkeypatch)
    scr = v.local_screens("IN")
    assert scr["golden_crossover"] == {"A", "C"}
    assert scr["rsi_oversold"] == {"B"}
    assert "A" in scr["companies_creating_new_high"]      # PctFromHigh -1 ≥ -1
    assert scr["multibagger_momentum"] == {"C"}           # Ret252 120 > 100


def test_known_universe_is_union(monkeypatch):
    v = _patch(monkeypatch)
    uni = v.known_universe("IN")
    # A and C are in several screens; union is non-empty and includes them
    assert {"A", "C"} <= uni


def test_validate_grounded_and_novel(monkeypatch):
    v = _patch(monkeypatch)
    res = v.validate(["A", "C"], "IN")
    assert res["n"] == 2
    assert res["grounded_pct"] == 1.0     # both appear in popular screens
    assert res["novel_pct"] == 0.0
    # a symbol not in the frame → novel
    res2 = v.validate(["A", "ZZZ"], "IN")
    assert res2["grounded_pct"] == 0.5
    assert res2["novel_pct"] == 0.5


def test_fundamental_registry_present():
    import validation as v
    assert "piotroski_9" in v.FUNDAMENTAL_SCREENS
    assert "coffee_can" in v.FUNDAMENTAL_SCREENS
    assert len(v.FUNDAMENTAL_SCREENS) >= 30
