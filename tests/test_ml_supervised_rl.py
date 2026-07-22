# Tests for the supervised classifier helpers and the RL trade agent.
# Pure logic, no network, no training.
import numpy as np


def test_return_labels():
    import ml_supervised as m

    assert m._label(40, 12, 6) == 3   # Strong Buy
    assert m._label(8, 12, 6) == 2    # Buy
    assert m._label(2, 12, 6) == 1    # Hold
    assert m._label(-5, 12, 6) == 0   # Avoid


def test_feat_at_needs_history_and_is_causal():
    import ml_supervised as m

    close = np.linspace(100, 200, 260)
    vol = np.full(260, 1000.0)
    assert m._feat_at(close, vol, 150) is None      # <200 bars → None
    f = m._feat_at(close, vol, 259)
    assert f is not None and f["above_200dma"] == 1.0   # rising series
    # causal: features at t must not depend on data after t
    f2a = m._feat_at(close, vol, 220)
    close2 = close.copy()
    close2[221:] = 0  # corrupt the future
    f2b = m._feat_at(close2, vol, 220)
    assert f2a["ret63"] == f2b["ret63"]


def test_reward_no_position_actions_penalised():
    import rl_trader as rl

    s = {"in_position": 0, "portfolio_exposure": 0.0}
    # STOP_LOSS / HOLD with no position must be penalised (anti-reward-hack)
    assert rl.reward_function(s, "STOP_LOSS", {"pnl_pct": 0.0, "fwd_return": 0}) == -0.2
    assert rl.reward_function(s, "HOLD", {"pnl_pct": 0.0, "fwd_return": 0}) == -0.2


def test_reward_good_entry_and_concentration():
    import rl_trader as rl

    s = {"in_position": 0, "portfolio_exposure": 0.0}
    assert rl.reward_function(s, "ENTER_FULL", {"pnl_pct": 0.25, "fwd_return": 0.25}) == 3.0
    # concentration penalty when overweight
    s2 = {"in_position": 0, "portfolio_exposure": 0.8}
    r = rl.reward_function(s2, "ENTER_SMALL", {"pnl_pct": 0.25, "fwd_return": 0.25})
    assert r == 3.0 - 1.5


def test_env_entry_then_forward():
    import rl_trader as rl

    close = np.linspace(100, 130, 300)  # +30% over the window
    env = rl.StockEnv(close, t0=200, score=0.8, regime="Bull", horizon=50)
    s = env.reset()
    assert s["in_position"] == 0
    s2, r, done = env.step("ENTER_FULL")
    assert s2["in_position"] == 1 and env.entry is not None


def test_encode_state_shape():
    import rl_trader as rl

    s = {"score": 0.7, "regime": "Bull", "days_held": 3, "pnl": 0.05,
         "exposure": 0.5, "drawdown_from_peak": 0.02, "in_position": 1}
    enc = rl.encode_state(s)
    assert len(enc) == len(rl.N_STATE_DIMS)
    assert all(0 <= enc[i] < rl.N_STATE_DIMS[i] for i in range(len(enc)))
