"""ml_supervised (features, dataset, train/predict with fake sklearn+joblib) and rl_trader (env, reward, agent)."""

import sys

import numpy as np
import pandas as pd
import pytest

import ml_supervised as ml
import rl_trader as rl
from tests.w4_helpers import fake_module


def _px(n, start=100.0, step=1.0, vol=1000.0):
    c = start + step * np.arange(n, dtype=float)
    return pd.DataFrame({"Close": c, "Volume": np.full(n, vol)}, index=pd.date_range("2020-01-01", periods=n))


# ---------------- ml_supervised ----------------
def test_label_boundaries():
    assert ml._label(12.0, 12, 6) == 3 and ml._label(11.99, 12, 6) == 2
    assert ml._label(6.0, 12, 6) == 2 and ml._label(5.99, 12, 6) == 1
    assert ml._label(0.0, 12, 6) == 1 and ml._label(-0.01, 12, 6) == 0


def test_feature_list_adds_only_present_leverage(monkeypatch):
    monkeypatch.setattr(ml, "LEV_FEATURES", ["lev_de", "lev_da"])
    assert ml._feature_list(["lev_da", "x"]) == ml.FEATURES + ["lev_da"]
    assert ml._feature_list([]) == ml.FEATURES


def test_feat_at_values_and_guards():
    close = np.arange(100.0, 400.0)
    vol = np.arange(1.0, 301.0)
    assert ml._feat_at(close, vol, 199) is None and ml._feat_at(close, vol, 200) is not None  # needs t >= 200
    f = ml._feat_at(close, vol, 299)
    assert f["rsi14"] == 100.0 and f["above_200dma"] == 1.0 and f["golden_cross"] == 1.0
    assert f["ret21"] == pytest.approx((399 / close[299 - 20] - 1) * 100)
    assert f["ret63"] == pytest.approx((399 / close[299 - 62] - 1) * 100)
    assert f["pct_from_high"] == 0.0
    assert f["pct_from_low"] == pytest.approx((399 / close[299 - 251] - 1) * 100)
    assert f["vol_ratio"] == pytest.approx(vol[-20:].mean() / vol[-60:].mean())
    assert f["log_turnover"] == pytest.approx(np.log1p((close[-20:] * vol[-20:]).mean()))
    bad = close.copy()
    bad[299] = 0.0
    assert ml._feat_at(bad, vol, 299) is None  # non-positive last price


def test_feat_at_downtrend_and_flat_flags():
    close = np.linspace(400, 100, 300)
    f = ml._feat_at(close, np.full(300, 5.0), 299)
    assert f["above_200dma"] == 0.0 and f["golden_cross"] == 0.0 and f["rsi14"] == pytest.approx(0.0, abs=1e-9)
    assert f["vol_ratio"] == 1.0
    flat = ml._feat_at(np.full(300, 50.0), np.full(300, 5.0), 299)
    assert flat["above_200dma"] == 0.0 and flat["golden_cross"] == 0.0 and flat["rsi14"] == 100.0  # no down moves


def test_build_dataset_sampling_and_labels(monkeypatch):
    data = {"UP": _px(400), "SHORT": _px(250), "NONE": None}
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": data if tier == "ltm" else {})
    monkeypatch.setattr(ml, "merge_leverage", lambda ds, m: ds)
    ds = ml.build_dataset("IN", horizon=63, n_asof=4)
    assert set(ds["Symbol"]) == {"UP"}  # needs len > 200 + horizon
    assert len(ds) == 4 and ds["asof"].is_monotonic_increasing
    # last as-of = n - horizon - 1 = 336; first = 200
    idx = _px(400).index
    assert list(ds["asof"]) == [idx[t] for t in np.linspace(200, 336, 4).astype(int)]
    assert set(ds["label"]) == {3}  # steadily rising: forward return far above the strong threshold
    assert set(ml.FEATURES) <= set(ds.columns)


def test_build_dataset_label_thresholds_and_edge_lengths(monkeypatch):
    monkeypatch.setattr(ml, "merge_leverage", lambda ds, m: ds)
    down = {"D": _px(400, start=1000.0, step=-1.0)}
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": down if tier == "ltm" else {})
    assert set(ml.build_dataset("IN", n_asof=3)["label"]) == {0}
    # exactly 200 + horizon bars is skipped, one more bar still too short for last_t > 200
    edge = {"E": _px(263)}
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": edge if tier == "ltm" else {})
    assert ml.build_dataset("IN", horizon=63).empty
    edge = {"E": _px(265)}  # last_t = 265-63-1 = 201 > 200 -> usable
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": edge if tier == "ltm" else {})
    assert not ml.build_dataset("IN", horizon=63, n_asof=2).empty
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": {})
    e = ml.build_dataset("IN")
    assert e.empty and set(["Symbol", "asof", "label"]) <= set(e.columns)


def test_build_dataset_falls_back_to_stm(monkeypatch):
    monkeypatch.setattr(ml, "merge_leverage", lambda ds, m: ds)
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": {"S": _px(400)} if tier == "stm" else {})
    assert set(ml.build_dataset("IN", n_asof=2)["Symbol"]) == {"S"}


class FakeModel:
    def __init__(self):
        self.fit_calls = []
        self.feature_importances_ = np.arange(1.0, 11.0)

    def fit(self, X, y):
        self.fit_calls.append((X.shape, len(y)))
        self.classes = sorted(set(y))
        return self

    def predict(self, X):
        return np.zeros(len(X), dtype=int)

    def predict_proba(self, X):
        p = np.zeros((len(X), 4))
        p[:, 3] = X[:, 0] / 100.0  # score driven by the first feature (rsi14)
        p[:, 1] = 1 - p[:, 3]
        return p


def _fake_sklearn(monkeypatch):
    class TSS:
        def __init__(self, n_splits):
            self.n = n_splits

        def split(self, X):
            n = len(X)
            step = n // (self.n + 1)
            for i in range(1, self.n + 1):
                yield np.arange(0, i * step), np.arange(i * step, (i + 1) * step)

    fake_module(monkeypatch, "sklearn")
    fake_module(monkeypatch, "sklearn.metrics", accuracy_score=lambda y, p: float((np.asarray(y) == np.asarray(p)).mean()))
    fake_module(monkeypatch, "sklearn.model_selection", TimeSeriesSplit=TSS)


def _ds(n, labels=(0, 1, 2, 3)):
    rng = np.random.default_rng(0)
    d = pd.DataFrame({f: rng.random(n) for f in ml.FEATURES})
    d["label"] = [labels[i % len(labels)] for i in range(n)]
    d["Symbol"] = "A"
    d["asof"] = pd.date_range("2020-01-01", periods=n)
    return d


def test_train_picks_first_horizon_with_enough_samples(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ml, "MODEL_DIR", tmp_path)
    dumped = {}
    fake_module(monkeypatch, "joblib", dump=lambda obj, p: dumped.update({str(p): obj}))
    _fake_sklearn(monkeypatch)
    monkeypatch.setattr(ml, "_make_model", FakeModel)
    seen = []

    def build(market, horizon, strong, buy):
        seen.append((horizon, strong, buy))
        return _ds(250) if horizon == 126 else _ds(199)  # 252/189 too small, 126 enough

    monkeypatch.setattr(ml, "build_dataset", build)
    r = ml.train("IN", horizon=252)
    assert [h for h, _, _ in seen] == [252, 189, 126]
    assert seen[0][1:] == (30.0, 15.0) and seen[2][1:] == (15.0, 7.5)  # thresholds pro-rated by horizon/252
    assert r["market"] == "IN" and r["samples"] == 250 and r["features"] == ml.FEATURES and r["leverage_used"] == []
    assert 0.0 <= r["cv_acc"] <= 1.0
    bundle = dumped[str(tmp_path / "IN.pkl")]
    assert bundle["horizon"] == 126 and bundle["features"] == ml.FEATURES
    assert bundle["model"].fit_calls[-1] == ((250, 10), 250)  # final fit uses everything
    out = capsys.readouterr().out
    assert "effective horizon: 126d" in out and "walk-forward acc" in out and "top features:" in out
    assert "label mix: Avoid=" in out and "Strong Buy=" in out


def test_train_caps_horizon_and_leverage_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ml, "MODEL_DIR", tmp_path)
    fake_module(monkeypatch, "joblib", dump=lambda obj, p: None)
    _fake_sklearn(monkeypatch)
    monkeypatch.setattr(ml, "_make_model", FakeModel)
    monkeypatch.setattr(ml, "LEV_FEATURES", ["lev_de"])
    seen = []

    def build(market, horizon, strong, buy):
        seen.append(horizon)
        d = _ds(220)
        d["lev_de"] = 1.0
        return d

    monkeypatch.setattr(ml, "build_dataset", build)
    m = FakeModel()
    m.feature_importances_ = np.arange(1.0, 12.0)
    monkeypatch.setattr(ml, "_make_model", lambda: m)
    r = ml.train("IN", horizon=100)
    assert seen == [100]  # first (requested) horizon already has >= 200 samples; larger ones are never tried
    assert r["leverage_used"] == ["lev_de"] and "lev_de" in r["features"]
    assert "leverage importances: lev_de=" in capsys.readouterr().out


def test_train_raises_with_too_few_samples(monkeypatch):
    fake_module(monkeypatch, "joblib", dump=lambda *a: None)
    _fake_sklearn(monkeypatch)
    monkeypatch.setattr(ml, "build_dataset", lambda *a, **k: _ds(199))
    with pytest.raises(RuntimeError, match=r"too few training samples for IN \(199\)"):
        ml.train("IN")


def test_importances_native_and_permutation(monkeypatch):
    m = FakeModel()
    assert list(ml._importances(m, None, None)) == list(np.arange(1.0, 11.0))

    class Bare:
        pass

    fake_module(monkeypatch, "sklearn")
    fake_module(
        monkeypatch,
        "sklearn.inspection",
        permutation_importance=lambda model, X, y, n_repeats, random_state: type("R", (), {"importances_mean": np.array([0.5, 0.25])})(),
    )
    assert list(ml._importances(Bare(), None, None)) == [0.5, 0.25]


def test_make_model_prefers_xgboost_then_sklearn(monkeypatch):
    class XGB:
        def __init__(self, **kw):
            self.kw = kw

    fake_module(monkeypatch, "xgboost", XGBClassifier=XGB)
    m = ml._make_model()
    assert m.kw["n_estimators"] == 300 and m.kw["max_depth"] == 5 and m.kw["eval_metric"] == "mlogloss"
    monkeypatch.setitem(sys.modules, "xgboost", None)

    class HGB:
        def __init__(self, **kw):
            self.kw = kw

    fake_module(monkeypatch, "sklearn")
    fake_module(monkeypatch, "sklearn.ensemble", HistGradientBoostingClassifier=HGB)
    assert ml._make_model().kw == {"max_depth": 5, "learning_rate": 0.05, "max_iter": 300}


def test_predict_current_scores_and_sorts(monkeypatch, tmp_path):
    monkeypatch.setattr(ml, "MODEL_DIR", tmp_path)
    store = {}
    fake_module(monkeypatch, "joblib", load=lambda p: store["bundle"], dump=lambda *a: None)
    assert ml.predict_current("IN").empty  # no model file yet
    (tmp_path / "IN.pkl").write_bytes(b"x")
    store["bundle"] = {"model": FakeModel(), "features": ml.FEATURES + ["missing_feat"]}  # extra feature -> NaN column
    store["bundle"]["model"].predict_proba = lambda X: np.tile([0.1, 0.1, 0.3, 0.5], (len(X), 1)) * np.where(np.isnan(X[:, -1]), 1, 0)[:, None] + 0.0
    data = {"A": _px(300), "B": _px(300, start=500, step=-1.0), "SHORT": _px(100), "NONE": None}
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": data if tier == "ltm" else {})
    monkeypatch.setattr(ml, "merge_leverage", lambda f, m: f)
    out = ml.predict_current("IN")
    assert set(out["Symbol"]) == {"A", "B"}
    assert (out["pred_class"] == 3).all() and (out["pred_name"] == "Strong Buy").all()
    assert (out["score"] == 0.8).all()  # P(Buy)+P(Strong) = 0.3 + 0.5


def test_predict_current_ordering_and_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(ml, "MODEL_DIR", tmp_path)
    (tmp_path / "IN.pkl").write_bytes(b"x")
    fake_module(monkeypatch, "joblib", load=lambda p: {"model": FakeModel()})  # no "features" key -> default list
    data = {"LOW": _px(300, start=500, step=-1.0), "HIGH": _px(300)}
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": data if tier == "ltm" else {})
    monkeypatch.setattr(ml, "merge_leverage", lambda f, m: f)
    out = ml.predict_current("IN")
    assert list(out["Symbol"]) == ["HIGH", "LOW"] and out["score"].is_monotonic_decreasing
    monkeypatch.setattr(ml.datalink, "load_market", lambda m, tier="stm": {"S": _px(50)})
    assert ml.predict_current("IN").empty


def test_known_good_supervised_threshold(monkeypatch):
    cur = pd.DataFrame({"Symbol": ["A", "B", "C"], "score": [0.5, 0.49, 0.9]})
    monkeypatch.setattr(ml, "predict_current", lambda m: cur)
    assert ml.known_good_supervised("IN") == {"A", "C"}  # >= is inclusive
    assert ml.known_good_supervised("IN", min_score=0.9) == {"C"}
    monkeypatch.setattr(ml, "predict_current", lambda m: pd.DataFrame())
    assert ml.known_good_supervised("IN") == set()


def test_ml_main_paths(monkeypatch, capsys):
    called = {}
    monkeypatch.setattr(ml, "train", lambda m, horizon: called.update(m=m, h=horizon))
    monkeypatch.setattr("sys.argv", ["ml", "--train", "--market", "US", "--horizon", "126"])
    assert ml.main() == 0 and called == {"m": "US", "h": 126}
    monkeypatch.setattr(ml, "predict_current", lambda m: pd.DataFrame())
    monkeypatch.setattr("sys.argv", ["ml"])
    assert ml.main() == 0 and "no model — run --train first" in capsys.readouterr().out
    cur = pd.DataFrame({"Symbol": ["A", "B", "C"], "pred_name": ["Buy"] * 3, "score": [0.9, 0.6, 0.4], "rsi14": [1, 2, 3]})
    monkeypatch.setattr(ml, "predict_current", lambda m: cur)
    monkeypatch.setattr("sys.argv", ["ml", "--top", "1"])
    ml.main()
    out = capsys.readouterr().out
    assert "Top 1 supervised" in out and "A" in out and "B " not in out and "C " not in out


# ---------------- rl_trader ----------------
def _state(**over):
    s = {"in_position": 0, "drawdown_from_peak": 0.0, "sector_boom_phase": "mid", "portfolio_exposure": 0.0}
    s.update(over)
    return s


@pytest.mark.parametrize(
    "pnl,expected",
    [(0.2001, 3.0), (0.20, 1.5), (0.1001, 1.5), (0.10, 0.5), (0.0001, 0.5), (0.0, -0.5), (-0.10, -0.5), (-0.1001, -2.0)],
)
def test_reward_entry_pnl_brackets(pnl, expected):
    assert rl.reward_function(_state(), "ENTER_FULL", {"pnl_pct": pnl}) == expected
    assert rl.reward_function(_state(), "ENTER_SMALL", {"pnl_pct": pnl}) == expected


def test_reward_stop_loss_partial_exit_and_skip():
    pos = _state(in_position=1)
    assert rl.reward_function(pos, "STOP_LOSS", {"pnl_pct": -0.079}) == 1.0
    assert rl.reward_function(pos, "STOP_LOSS", {"pnl_pct": -0.08}) == -0.5  # boundary: > -0.08 is strict
    assert rl.reward_function(_state(in_position=1, drawdown_from_peak=0.1501), "PARTIAL_EXIT", {"pnl_pct": 0}) == 1.5
    assert rl.reward_function(_state(in_position=1, drawdown_from_peak=0.15), "PARTIAL_EXIT", {"pnl_pct": 0}) == 0.0
    assert rl.reward_function(_state(), "SKIP", {"pnl_pct": 0, "fwd_return": 0.2001}) == -1.0
    assert rl.reward_function(_state(), "SKIP", {"pnl_pct": 0, "fwd_return": 0.20}) == 0.0
    assert rl.reward_function(_state(), "SKIP", {"pnl_pct": 0}) == 0.0


def test_reward_invalid_actions_and_modifiers():
    assert rl.reward_function(_state(), "HOLD", {"pnl_pct": 1.0}) == -0.2  # no position
    assert rl.reward_function(_state(in_position=1), "ENTER_FULL", {"pnl_pct": 1.0}) == -0.2  # already in
    early = _state(sector_boom_phase="early")
    assert rl.reward_function(early, "ENTER_SMALL", {"pnl_pct": 0.16}) == 1.5 + 1.0  # early-boom bonus > 15%
    assert rl.reward_function(early, "ENTER_SMALL", {"pnl_pct": 0.15}) == 1.5
    hot = _state(portfolio_exposure=0.7001)
    assert rl.reward_function(hot, "ENTER_SMALL", {"pnl_pct": 0.05}) == 0.5 - 1.5  # over-exposure penalty
    assert rl.reward_function(_state(portfolio_exposure=0.70), "ENTER_SMALL", {"pnl_pct": 0.05}) == 0.5
    assert rl.reward_function(_state(in_position=1, portfolio_exposure=0.9), "ADD", {"pnl_pct": 0.0}) == -1.5


def test_bucket_and_encode_state():
    assert rl._bucket(0.33, [0.33, 0.66]) == 1 and rl._bucket(0.3299, [0.33, 0.66]) == 0 and rl._bucket(0.9, [0.33, 0.66]) == 2
    s = {"score": 0.7, "regime": "Bull", "days_held": 20, "pnl": 0.10, "exposure": 0.4, "drawdown_from_peak": 0.15, "in_position": 1}
    assert rl.encode_state(s) == (2, 2, 3, 3, 1, 2, 1)
    s2 = dict(s, regime="Weird", days_held=0, pnl=-0.5, score=0.0, exposure=0.0, drawdown_from_peak=0.0)
    s2.pop("in_position")
    assert rl.encode_state(s2) == (0, 1, 0, 0, 0, 0, 0)
    for k, d in zip(rl.encode_state(s), rl.N_STATE_DIMS):
        assert 0 <= k < d


def _env(close, **kw):
    return rl.StockEnv(np.asarray(close, float), t0=0, score=0.6, regime="Bull", horizon=kw.pop("horizon", 4), **kw)


def test_env_enter_hold_and_exit_flow():
    env = _env([100, 109, 121, 100, 90, 80])
    s = env.reset()
    assert s["in_position"] == 0 and s["regime"] == "Bull" and s["sector_boom_phase"] == "early" and s["score"] == 0.6
    s, r, done = env.step("ENTER_FULL")
    assert env.entry == 100 and env.exposure == 0.7 and env.days_held == 1 and not done
    assert s["pnl"] == pytest.approx(0.09) and s["in_position"] == 1  # now at t=1
    assert r == 0.5  # entry reward is evaluated on the post-step pnl (+9%: 0 < pnl <= 10%)
    s, r, done = env.step("HOLD")
    assert s["pnl"] == pytest.approx(0.21) and s["drawdown_from_peak"] == 0.0
    s, r, done = env.step("HOLD")  # price falls back to 100: drawdown from the 21% peak
    assert s["pnl"] == pytest.approx(0.0) and s["drawdown_from_peak"] == pytest.approx(0.21)
    s, r, done = env.step("FULL_EXIT")
    assert done


def test_env_enter_small_exposure_stoploss_and_horizon_end():
    env = _env([100, 100, 100, 100, 100, 100], horizon=2)
    env.reset()
    env.step("ENTER_SMALL")
    assert env.exposure == 0.35
    env.step("HOLD")
    _, _, done = env.step("HOLD")
    assert done  # t reached end_t = t0 + horizon
    env2 = _env([100, 90, 90, 90], horizon=3)
    env2.reset()
    env2.step("ENTER_FULL")
    _, r, done = env2.step("STOP_LOSS")
    assert done and r == -0.5  # pnl -10% is below the -8% band


def test_env_add_enters_only_when_flat_and_end_clamped_to_data():
    env = _env([100, 101, 102], horizon=50)  # horizon beyond data: end_t clamps to len-1
    env.reset()
    env.step("ADD")
    assert env.entry == 100 and env.exposure == 0.7
    env.step("ADD")  # already in a position: no second entry
    assert env.entry == 100
    _, _, done = env.step("HOLD")
    assert done


def test_env_skip_never_enters_and_reports_forward_return():
    env = _env([100, 130, 130, 130], horizon=3)
    env.reset()
    s, r, done = env.step("SKIP")
    assert env.entry is None and s["in_position"] == 0 and r == -1.0  # missed a >20% forward move


def test_qagent_act_remember_replay(monkeypatch):
    a = rl.QAgent(alpha=0.5, gamma=0.5, eps=0.0)
    s = {"score": 0.5, "regime": "Bull", "days_held": 0, "pnl": 0.0, "exposure": 0.0, "drawdown_from_peak": 0.0, "in_position": 0}
    key = rl.encode_state(s)
    a.q(key)[rl.A["ENTER_FULL"]] = 2.0
    assert a.act(s, explore=False) == "ENTER_FULL" and a.act(s) == "ENTER_FULL"  # eps=0 never explores
    a.replay()  # empty buffer is a no-op
    a.remember(s, "ENTER_FULL", 1.0, s, True)
    assert len(a.buffer) == 1
    a.replay(batch=10)
    # terminal transition: target = r = 1.0; Q += 0.5 * (1.0 - 2.0)
    assert a.q(key)[rl.A["ENTER_FULL"]] == pytest.approx(1.5)
    a.remember(s, "SKIP", 0.0, s, False)
    monkeypatch.setattr(np.random, "randint", lambda lo, hi=None, size=None: np.array([1]) if size is not None else 0)
    a.q(key)[rl.A["SKIP"]] = 0.0
    a.replay(batch=1)  # index 1 = non-terminal: target = 0 + gamma * max Q = 0.5 * 1.5
    assert a.q(key)[rl.A["SKIP"]] == pytest.approx(0.5 * 0.75)


def test_qagent_explores_with_probability_eps(monkeypatch):
    a = rl.QAgent(eps=0.2)
    s = {"score": 0.5, "regime": "Bull", "days_held": 0, "pnl": 0.0, "exposure": 0.0, "drawdown_from_peak": 0.0}
    monkeypatch.setattr(np.random, "random", lambda: 0.19)
    monkeypatch.setattr(np.random, "randint", lambda n: 6)
    assert a.act(s) == "FULL_EXIT"  # random draw < eps -> random action
    monkeypatch.setattr(np.random, "random", lambda: 0.2)
    assert a.act(s) == "SKIP"  # draw == eps is not exploration; zero Q -> argmax 0
    assert a.act(s, explore=False) == "SKIP"


def test_regime_at_rules():
    up = np.linspace(100, 300, 300)
    assert rl._regime_at(up, 199) == "Neutral"  # not enough history
    assert rl._regime_at(up, 299) == "Bull"
    down = np.linspace(300, 100, 300)
    assert rl._regime_at(down, 299) == "Bear"
    assert rl._regime_at(np.full(300, 100.0), 299) == "Neutral"
    assert rl._regime_at(np.concatenate([np.full(250, 100.0), np.linspace(100, 104.9, 50)]), 299) == "Neutral"  # +4.9% < 5%


def test_rl_train_validations_and_saves(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rl, "AGENT_DIR", tmp_path)
    dumped = {}
    fake_module(monkeypatch, "joblib", dump=lambda obj, p: dumped.update({str(p): obj}))
    rng = np.random.default_rng(1)
    data = {f"S{i}": _px(300, start=100 + i, step=0.5) for i in range(12)}
    data["SHORT"] = _px(260)  # needs > 202 + horizon bars
    monkeypatch.setattr(rl.datalink, "load_market", lambda m, tier="stm": data if tier == "ltm" else {})
    fake_module(monkeypatch, "ml_supervised", predict_current=lambda m: pd.DataFrame({"Symbol": ["S0"], "score": [0.9]}))
    out = rl.train("IN", episodes=30, horizon=63)
    assert out["market"] == "IN" and out["episodes"] == 30 and out["states"] > 0
    q = dumped[str(tmp_path / "IN_rl.pkl")]
    assert q["actions"] == rl.ACTIONS and len(q["Q"]) == out["states"]
    assert "trained RL IN: 30 episodes" in capsys.readouterr().out
    # deterministic given the seeded env rng + np.random; second run produces a state table of similar size
    monkeypatch.setattr(rl.datalink, "load_market", lambda m, tier="stm": {f"S{i}": _px(300) for i in range(9)} if tier == "ltm" else {})
    with pytest.raises(RuntimeError, match="too little history to train RL for IN"):
        rl.train("IN", episodes=1)


def test_rl_train_survives_missing_supervised(monkeypatch, tmp_path):
    monkeypatch.setattr(rl, "AGENT_DIR", tmp_path)
    fake_module(monkeypatch, "joblib", dump=lambda *a: None)
    data = {f"S{i}": _px(300) for i in range(10)}
    monkeypatch.setattr(rl.datalink, "load_market", lambda m, tier="stm": data if tier == "ltm" else {})
    fake_module(monkeypatch, "ml_supervised", predict_current=lambda m: 1 / 0)
    assert rl.train("IN", episodes=2, verbose=False)["episodes"] == 2


def test_rl_decide_no_agent_and_with_agent(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rl, "AGENT_DIR", tmp_path)
    fake_module(monkeypatch, "joblib", load=lambda p: {"Q": {}})  # decide() imports joblib before checking the file
    assert rl.decide("IN").empty and "no RL agent" in capsys.readouterr().out
    assert rl.decide("IN", verbose=False).empty and capsys.readouterr().out == ""
    (tmp_path / "IN_rl.pkl").write_bytes(b"x")
    q = {}
    up = _px(300)
    data = {"UP": up, "SHORT": _px(200), "OTHER": _px(300)}
    monkeypatch.setattr(rl.datalink, "load_market", lambda m, tier="stm": data if tier == "ltm" else {})
    key = rl.encode_state({"score": 0.9, "regime": "Bull", "days_held": 0, "pnl": 0.0, "exposure": 0.0, "drawdown_from_peak": 0.0, "in_position": 0})
    q[key] = np.zeros(len(rl.ACTIONS))
    q[key][rl.A["ENTER_FULL"]] = 1.0
    fake_module(monkeypatch, "joblib", load=lambda p: {"Q": q})
    fake_module(monkeypatch, "ml_supervised", predict_current=lambda m: pd.DataFrame({"Symbol": ["UP"], "score": [0.9]}))
    out = rl.decide("IN", shortlist=["UP", "SHORT", "MISSING"])
    assert list(out["Symbol"]) == ["UP"]  # short history and unknown symbols skipped
    assert out.iloc[0]["Action"] == "ENTER_FULL" and out.iloc[0]["Regime"] == "Bull" and out.iloc[0]["Score"] == 0.9
    assert "RL trade decisions — IN" in capsys.readouterr().out
    # unknown score defaults to 0.5; unseen state -> zero Q -> SKIP
    fake_module(monkeypatch, "ml_supervised", predict_current=lambda m: 1 / 0)
    out2 = rl.decide("IN", shortlist=["OTHER"], verbose=False)
    assert out2.iloc[0]["Score"] == 0.5 and out2.iloc[0]["Action"] == "SKIP"


def test_rl_decide_default_shortlist_from_auto_screener(monkeypatch, tmp_path):
    monkeypatch.setattr(rl, "AGENT_DIR", tmp_path)
    (tmp_path / "IN_rl.pkl").write_bytes(b"x")
    fake_module(monkeypatch, "joblib", load=lambda p: {"Q": {}})
    fake_module(monkeypatch, "auto_screener", recommend=lambda market, verbose: {"picks": ["UP"]})
    fake_module(monkeypatch, "ml_supervised", predict_current=lambda m: pd.DataFrame({"Symbol": [], "score": []}))
    monkeypatch.setattr(rl.datalink, "load_market", lambda m, tier="stm": {"UP": _px(300)} if tier == "ltm" else {})
    assert list(rl.decide("IN", verbose=False)["Symbol"]) == ["UP"]
    fake_module(monkeypatch, "auto_screener", recommend=lambda market, verbose: 1 / 0)
    assert rl.decide("IN", verbose=False).empty
