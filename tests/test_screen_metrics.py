# Tests for the Screener.in metric engine — pure logic, no network.
import numpy as np
import pandas as pd


def test_metrics_from_series():
    import screen_metrics as sm

    # a clean uptrend: DMA50 should be above DMA200, price near the high
    close = np.linspace(100, 300, 300)
    vol = np.full(300, 1000.0)
    df = pd.DataFrame({"Close": close, "High": close, "Low": close,
                       "Open": close, "Volume": vol})
    m = sm._metrics(df)
    assert m is not None
    assert m["DMA50"] > m["DMA200"]              # uptrend
    assert m["PctFromHigh"] >= -0.5              # at the high
    assert 0 <= m["RSI"] <= 100


def test_volume_spike_detected():
    import screen_metrics as sm

    close = np.linspace(100, 120, 300)
    vol = np.full(300, 1000.0)
    vol[-5:] = 10000.0                            # this week 10× normal
    df = pd.DataFrame({"Close": close, "High": close, "Low": close,
                       "Open": close, "Volume": vol})
    m = sm._metrics(df)
    assert m["VolSpike"] >= 5                     # price-volume-action threshold


def test_price_screen_predicates():
    import screen_metrics as sm

    mf = pd.DataFrame({
        "Symbol": ["UP", "DOWN", "OVERSOLD"],
        "Close": [300.0, 80.0, 50.0],
        "DMA50": [280, 90, 60], "DMA200": [250, 100, 70],
        "DMA50_prev": [240, 110, 65], "DMA200_prev": [250, 100, 70],
        "RSI": [70, 45, 25], "High52": [305, 200, 120], "Low52": [100, 78, 48],
        "PctFromHigh": [-1.6, -60, -58], "PctFromLow": [200, 2, 4],
        "VolSpike": [1.0, 1.0, 1.0], "Ret252": [180, -40, -50],
    })
    gc = mf[sm.PRICE_SCREENS["golden_crossover"](mf).fillna(False)]
    assert set(gc["Symbol"]) == {"UP"}           # crossed up in window
    ov = mf[sm.PRICE_SCREENS["rsi_oversold"](mf).fillna(False)]
    assert set(ov["Symbol"]) == {"OVERSOLD"}
    nh = mf[sm.PRICE_SCREENS["companies_creating_new_high"](mf).fillna(False)]
    assert set(nh["Symbol"]) == {"UP"}
