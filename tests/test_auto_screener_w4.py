"""auto_screener: known-good anchor, regime, cluster discovery, rule evaluation and RL refinement."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


@pytest.fixture
def au(monkeypatch, tmp_path):
    with monkeypatch.context() as ctx:  # imports create cache_seed/{serving,cdc,discovered_screens}
        ctx.setattr(Path, "mkdir", lambda *a, **k: None)
        m = fresh_import(monkeypatch, "auto_screener")
    monkeypatch.setattr(m, "OUT_DIR", tmp_path)
    return m


def _view(n=60, seed=0, liq="High"):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "Symbol": [f"S{i:03d}" for i in range(n)],
            "Close": rng.uniform(10, 100, n),
            "RSI14": rng.uniform(20, 80, n),
            "PctFromHigh": rng.uniform(-40, 0, n),
            "PctFromLow": rng.uniform(0, 80, n),
            "Ret21": rng.normal(0, 5, n),
            "Ret63": rng.normal(0, 10, n),
            "Ret126": rng.normal(5, 15, n),
            "Ret252": rng.normal(10, 25, n),
            "Above200DMA": rng.random(n) > 0.4,
            "GoldenCross": rng.random(n) > 0.5,
            "Liquidity": liq,
        }
    )


# ---------------- known_good / fundamental_anchor ----------------
def _kg_view():
    return pd.DataFrame(
        {
            "Symbol": ["DARV", "GOLD", "QUAL", "NONE", "ILLIQ", "NEAR"],
            "Above200DMA": [True, True, True, False, True, True],
            "PctFromHigh": [-4.99, -30.0, -30.0, -1.0, -1.0, -5.0],
            "Ret126": [1.0, 1.0, 1.0, 50.0, 5.0, 1.0],
            "Ret252": [1.0, 1.0, 15.01, 99.0, 99.0, 1.0],
            "GoldenCross": [False, True, False, False, True, False],
            "Liquidity": ["High", "Medium", "Medium", "High", "Low", "High"],
        }
    )


def test_known_good_gates_and_boundaries(au, monkeypatch):
    monkeypatch.setattr(au.sl, "serving", lambda m: _kg_view())
    fake_module(monkeypatch, "ml_supervised", known_good_supervised=lambda m: {"SUP"})
    fake_module(monkeypatch, "validation", known_universe=lambda m: {"UNIV"})
    monkeypatch.setattr(au, "fundamental_anchor", lambda m: {"FUND"})
    good = au.known_good("US")
    # DARV: near-high breakout (-4.99 > -5); NEAR at exactly -5 fails the strict gate; QUAL: Ret252 > 15 strict
    assert good == {"DARV", "GOLD", "QUAL", "SUP", "UNIV", "FUND"}
    assert "ILLIQ" not in good and "NONE" not in good and "NEAR" not in good


def test_known_good_empty_view_and_failing_anchors(au, monkeypatch):
    monkeypatch.setattr(au.sl, "serving", lambda m: pd.DataFrame())
    assert au.known_good("IN") == set()
    monkeypatch.setattr(au.sl, "serving", lambda m: _kg_view())
    fake_module(monkeypatch, "ml_supervised", known_good_supervised=lambda m: 1 / 0)
    fake_module(monkeypatch, "validation", known_universe=lambda m: 1 / 0)
    monkeypatch.setattr(au, "fundamental_anchor", lambda m: 1 / 0)
    assert au.known_good("US") == {"DARV", "GOLD", "QUAL"}


def test_known_good_india_adds_ccc_and_no_liquidity_column(au, monkeypatch):
    v = _kg_view().drop(columns="Liquidity")
    monkeypatch.setattr(au.sl, "serving", lambda m: v)
    fake_module(monkeypatch, "ml_supervised", known_good_supervised=lambda m: set())
    fake_module(monkeypatch, "validation", known_universe=lambda m: set())
    fake_module(monkeypatch, "datalink", ccc_map_cached=lambda: {"CCC1": 3.0})
    monkeypatch.setattr(au, "fundamental_anchor", lambda m: set())
    assert "CCC1" in au.known_good("IN") and "CCC1" not in au.known_good("US")
    assert {"ILLIQ"} <= au.known_good("US")  # without a Liquidity column nothing is filtered out
    fake_module(monkeypatch, "datalink", ccc_map_cached=lambda: 1 / 0)
    assert "CCC1" not in au.known_good("IN")


def test_fundamental_anchor(au, monkeypatch, tmp_path):
    frame = pd.DataFrame(
        {
            "Symbol": ["A", "B", "C"],
            "roe": [30.0, 10.0, 18.0],
            "debt_to_equity": [0.1, 0.1, 0.9],
            "net_income": [1, 1, 1],
        }
    )
    frame.to_parquet(tmp_path / "IN.parquet")
    import fundamental_metrics as fm

    monkeypatch.setattr(fm, "CACHE", tmp_path)
    got = au.fundamental_anchor("IN")
    assert got == {"A"}  # high_roe (roe > 20) and coffee_can (roe>15 & de<0.5); C fails leverage
    assert au.fundamental_anchor("XX") == set()  # no cached file
    pd.DataFrame({"Symbol": []}).to_parquet(tmp_path / "EU.parquet")
    assert au.fundamental_anchor("EU") == set()


# ---------------- regime / helpers ----------------
def test_ret_prefers_252_falls_back_to_126(au):
    df = pd.DataFrame({"Ret252": [1.0, np.nan], "Ret126": [9.0, 7.0]})
    assert list(au._ret(df)) == [1.0, 7.0]
    only252 = au._ret(df[["Ret252"]])  # no Ret126 column: nothing to fall back to
    assert only252.iloc[0] == 1.0 and np.isnan(only252.iloc[1])


@pytest.mark.parametrize(
    "above,ret,expected",
    [
        (0.56, 1.0, "Bull"),
        (0.55, 1.0, "Neutral"),
        (0.9, 0.0, "Neutral"),
        (0.9, -0.1, "Neutral"),
        (0.34, 10.0, "Bear"),
        (0.35, 10.0, "Neutral"),
        (0.5, -5.1, "Bear"),
        (0.5, -5.0, "Neutral"),
    ],
)
def test_regime_thresholds(au, above, ret, expected):
    n = 100
    df = pd.DataFrame(
        {
            "Above200DMA": [True] * round(above * n) + [False] * (n - round(above * n)),
            "RSI14": 50.0,
            "Ret252": ret,
            "Ret126": ret,
        }
    )
    assert au.regime(df)["tape"] == expected


def test_regime_fields_and_missing_columns(au):
    df = pd.DataFrame(
        {
            "Above200DMA": [True, False, True, True],
            "RSI14": [40, 50, 60, 70],
            "Ret252": [1.0, 2.0, 3.0, 4.0],
            "Liquidity": ["High", "Low", "Medium", "Low"],
        }
    )
    r = au.regime(df)
    assert r == {
        "tape": "Bull",
        "breadth": 0.75,
        "med_rsi": 55.0,
        "med_ret252": 2.5,
        "liquid_share": 0.5,
    }
    bare = au.regime(pd.DataFrame({"RSI14": [50.0], "Ret252": [1.0]}))
    assert (
        bare["breadth"] == 0.0 and bare["liquid_share"] == 0.0 and bare["tape"] == "Bear"
    )  # breadth 0 < 0.35


def test_matrix_drops_incomplete_rows_and_fills(au):
    df = _view(10)
    df.loc[0, "RSI14"] = np.nan  # required -> row dropped
    df.loc[1, "Ret21"] = np.inf  # optional -> inf becomes median
    df.loc[2, "Ret252"] = np.nan
    X, dfx = au._matrix(df)
    assert X.shape == (9, len(au.FEATS) + len(au.BOOLS)) and "S000" not in set(dfx["Symbol"])
    assert np.isfinite(X).all() and X[:, -1].max() <= 1.0 and set(np.unique(X[:, -2])) <= {0.0, 1.0}


# ---------------- discover ----------------
class _KMeans:
    def __init__(self, n_clusters, n_init, random_state):
        self.k = n_clusters

    def fit_predict(self, Z):
        order = np.argsort(Z[:, 0])  # deterministic blocks along the first feature (RSI)
        labels = np.empty(len(Z), dtype=int)
        for rank, i in enumerate(order):
            labels[i] = rank * self.k // len(Z)
        return labels


class _Scaler:
    def fit_transform(self, X):
        return X


def _fake_sklearn(monkeypatch):
    fake_module(monkeypatch, "sklearn")
    fake_module(monkeypatch, "sklearn.cluster", KMeans=_KMeans)
    fake_module(monkeypatch, "sklearn.preprocessing", StandardScaler=_Scaler)


def test_discover_selects_cluster_with_best_overlap(au, monkeypatch):
    _fake_sklearn(monkeypatch)
    view = _view(60)
    monkeypatch.setattr(au.sl, "serving", lambda m: view)
    top_rsi = set(
        view.sort_values("RSI14").tail(10)["Symbol"]
    )  # the highest-RSI block (cluster 5 of 6)
    monkeypatch.setattr(au, "known_good", lambda m: top_rsi)
    d = au.discover("IN", k=6)
    assert (
        d["market"] == "IN"
        and d["cluster_size"] == 10
        and d["cluster_overlap"] == 1.0
        and d["known_good_n"] == 10
    )
    lo, hi = d["rule"]["RSI14"][0][1], d["rule"]["RSI14"][1][1]
    cl = view[view["Symbol"].isin(top_rsi)]
    assert lo == round(cl["RSI14"].quantile(0.10), 2) and hi == round(cl["RSI14"].quantile(0.90), 2)
    assert d["regime"]["tape"] in ("Bull", "Bear", "Neutral")


def test_discover_guards(au, monkeypatch):
    _fake_sklearn(monkeypatch)
    monkeypatch.setattr(au.sl, "serving", lambda m: pd.DataFrame())
    with pytest.raises(RuntimeError, match="no serving view for IN"):
        au.discover("IN")
    monkeypatch.setattr(au.sl, "serving", lambda m: _view(29))
    monkeypatch.setattr(au, "known_good", lambda m: set())
    with pytest.raises(RuntimeError, match=r"too few liquid names in IN \(29\)"):
        au.discover("IN", k=6)  # needs >= k * 5 = 30
    monkeypatch.setattr(au.sl, "serving", lambda m: _view(30))
    assert au.discover("IN", k=6)["cluster_size"] == 5
    mixed = _view(40)
    mixed.loc[:19, "Liquidity"] = "Low"  # illiquid names are excluded from clustering
    monkeypatch.setattr(au.sl, "serving", lambda m: mixed)
    with pytest.raises(RuntimeError, match=r"too few liquid names in IN \(20\)"):
        au.discover("IN", k=5)  # 20 < 25


def test_rule_from_cluster_regime_overrides(au):
    cl = pd.DataFrame(
        {
            "RSI14": np.arange(0, 101.0),
            "PctFromHigh": np.arange(-100, 1.0),
            "Ret126": np.arange(0, 101.0),
        }
    )
    bull = au._rule_from_cluster(cl, {"tape": "Bull"})
    assert bull["RSI14"] == [(">=", 10.0), ("<=", 90.0)] and bull["GoldenCross"] == [("==", True)]
    assert bull["PctFromHigh"] == [(">=", -90.0), ("<=", -10.0)] and "Above200DMA" not in bull
    bear = au._rule_from_cluster(cl, {"tape": "Bear"})
    assert bear["Above200DMA"] == [("==", True)] and bear["PctFromHigh"] == [
        (">=", -20.0)
    ]  # overridden, single cond
    neutral = au._rule_from_cluster(cl, {"tape": "Neutral"})
    assert set(neutral) == {"RSI14", "PctFromHigh", "Ret126"}


# ---------------- apply_rule / evaluate ----------------
def _sel_view():
    return pd.DataFrame(
        {
            "Symbol": list("ABCDE"),
            "RSI14": [30, 40, 50, 60, 70],
            "Ret252": [5.0, 50.0, 20.0, 10.0, 99.0],
            "GoldenCross": [True, True, False, True, True],
            "Liquidity": ["High", "Medium", "High", "Low", "High"],
        }
    )


def test_apply_rule_conditions_liquidity_sort_top(au, monkeypatch):
    monkeypatch.setattr(au.sl, "serving", lambda m: _sel_view())
    out = au.apply_rule({"RSI14": [(">=", 40), ("<=", 70)]}, "IN")
    assert list(out["Symbol"]) == ["E", "B", "C"]  # D is Low liquidity; sorted by Ret252 desc
    assert list(au.apply_rule({"RSI14": [(">=", 40), ("<=", 70)]}, "IN", top=2)["Symbol"]) == [
        "E",
        "B",
    ]
    assert list(au.apply_rule({"GoldenCross": [("==", True)]}, "IN")["Symbol"]) == ["E", "B", "A"]
    assert (
        len(au.apply_rule({"Nope": [(">", 1)], "RSI14": [("~", 1)]}, "IN")) == 4
    )  # unknown column / operator ignored
    assert len(au.apply_rule({}, "IN", top=0)) == 4  # falsy top = all
    monkeypatch.setattr(au.sl, "serving", lambda m: pd.DataFrame())
    assert au.apply_rule({}, "IN").empty


def test_evaluate_reward_components(au, monkeypatch):
    v = _sel_view()
    v["Liquidity"] = "High"
    monkeypatch.setattr(au.sl, "serving", lambda m: v)
    r = au.evaluate({"RSI14": [(">=", 30)]}, "IN", good=set("ABC"), target=(1, 10))
    # n=5 in range; overlap 3/5; liq 1.0; median Ret252 of [99,50,20,10,5] = 20
    assert r == {
        "reward": round(2 * 0.6 + 0.5 + 0.2 - 1.5 * (0.4 * 0.6), 3),
        "n": 5,
        "overlap": 0.6,
        "liq": 1.0,
        "ret": 20.0,
        "deviation": 0.24,
    }
    empty = au.evaluate({"RSI14": [(">", 999)]}, "IN", good=set())
    assert empty == {
        "reward": -3.0,
        "n": 0,
        "overlap": 0.0,
        "liq": 0.0,
        "ret": 0.0,
        "deviation": 1.0,
    }


def test_evaluate_size_deviation_boundaries(au, monkeypatch):
    v = pd.DataFrame(
        {"Symbol": [f"S{i}" for i in range(100)], "RSI14": 50.0, "Ret252": 0.0, "Liquidity": "High"}
    )
    monkeypatch.setattr(au.sl, "serving", lambda m: v)
    good = set(v["Symbol"])
    rule = {"RSI14": [(">=", 0)]}
    in_range = au.evaluate(rule, "IN", good, target=(100, 200))  # n == lo: no size penalty
    assert in_range["deviation"] == 0.0 and in_range["reward"] == 2.5
    below = au.evaluate(rule, "IN", good, target=(101, 200))  # one short of the minimum
    assert below["deviation"] == pytest.approx(0.001, abs=5e-4)  # (101-100)/200 * 0.2
    above = au.evaluate(
        rule, "IN", good, target=(10, 50)
    )  # n=100, hi=50 -> dev (100-50)/50 = 1.0 capped
    assert above["deviation"] == 0.2
    way_above = au.evaluate(rule, "IN", good, target=(5, 20))  # 80/20 = 4 -> capped at 1 * 0.2
    assert way_above["deviation"] == 0.2
    illiquid = v.assign(Liquidity="Low")
    monkeypatch.setattr(au.sl, "serving", lambda m: illiquid)
    assert au.evaluate(rule, "IN", good)["n"] == 0  # apply_rule drops Low-liquidity names entirely


# ---------------- rl_refine / recommend ----------------
def test_rl_refine_only_accepts_improvements(au, monkeypatch, capsys):
    calls = []

    def fake_eval(rule, market, good, target=(15, 60)):
        calls.append(json.dumps(rule))
        return {
            "reward": float(rule["RSI14"][0][1]) * -1.0 if "RSI14" in rule else 0.0,
            "n": 1,
            "overlap": 0,
            "liq": 0,
            "ret": 0,
            "deviation": 0,
        }

    monkeypatch.setattr(au, "evaluate", fake_eval)
    rule = {"RSI14": [(">=", 50.0), ("<=", 70.0)], "Above200DMA": [("==", True)]}
    new_rule, best = au.rl_refine(rule, "IN", set(), iters=25)
    assert best["reward"] >= fake_eval(rule, "IN", set())["reward"]  # never worse
    assert new_rule["Above200DMA"] == [["==", True]]  # boolean thresholds untouched
    assert (
        new_rule["RSI14"][0][1] <= 50.0
    )  # reward = -lower bound, so accepted moves only ever lower it
    assert rule["RSI14"][0] == (">=", 50.0)  # input rule not mutated
    assert "RL correction: reward -50.0 →" in capsys.readouterr().out
    au.rl_refine(rule, "IN", set(), iters=1, verbose=False)
    assert capsys.readouterr().out == ""
    same = au.rl_refine({"RSI14": [(">=", 5.0)]}, "IN", set(), iters=0, verbose=False)
    assert same[0] == {
        "RSI14": [[">=", 5.0]]
    }  # zero iterations returns the deep copy (tuples become lists)


def test_rl_refine_is_deterministic(au, monkeypatch):
    monkeypatch.setattr(
        au,
        "evaluate",
        lambda r, m, g, target=(15, 60): {
            "reward": -abs(r["RSI14"][0][1] - 33.0),
            "n": 1,
            "overlap": 0,
            "liq": 0,
            "ret": 0,
            "deviation": 0,
        },
    )
    a = au.rl_refine({"RSI14": [(">=", 60.0)]}, "IN", set(), iters=30, verbose=False)
    b = au.rl_refine({"RSI14": [(">=", 60.0)]}, "IN", set(), iters=30, verbose=False)
    assert a == b and abs(a[0]["RSI14"][0][1] - 33.0) < 27.0  # moved toward the optimum


def _disc():
    return {
        "market": "IN",
        "regime": {
            "tape": "Bull",
            "breadth": 0.6,
            "med_rsi": 55.0,
            "med_ret252": 12.0,
            "liquid_share": 0.9,
        },
        "cluster_size": 12,
        "cluster_overlap": 0.5,
        "known_good_n": 20,
        "rule": {"RSI14": [(">=", 40.0)]},
    }


def _ev(reward, deviation=0.1):
    return {
        "reward": reward,
        "n": 3,
        "overlap": 0.5,
        "liq": 1.0,
        "ret": 4.0,
        "deviation": deviation,
    }


def test_recommend_skips_rl_when_good_and_writes_json(au, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(au, "discover", lambda m: _disc())
    monkeypatch.setattr(au, "known_good", lambda m: {"A"})
    monkeypatch.setattr(
        au, "evaluate", lambda r, m, g: _ev(0.6, 0.5)
    )  # reward == min_reward and deviation == 0.5: no RL
    monkeypatch.setattr(au, "rl_refine", lambda *a, **k: pytest.fail("RL must not run"))
    monkeypatch.setattr(
        au,
        "apply_rule",
        lambda rule, m, top: pd.DataFrame(
            {"Symbol": ["A", "B"], "Close": [1.0, 2.0], "Liquidity": "High"}
        ),
    )
    rec = au.recommend("IN", top=7)
    assert (
        rec["picks"] == ["A", "B"]
        and rec["rl_correction_applied"] is False
        and rec["final_rule"] == rec["discovered_rule"]
    )
    saved = json.loads((tmp_path / "IN.json").read_text())
    assert saved["picks"] == ["A", "B"] and saved["metrics"]["reward"] == 0.6
    out = capsys.readouterr().out
    assert (
        "auto-screener recommendation: IN" in out
        and "regime: Bull  breadth=60%" in out
        and "RL correction applied: False" in out
    )
    assert "top picks:" in out


@pytest.mark.parametrize("reward,deviation", [(0.59, 0.1), (0.9, 0.51)])
def test_recommend_triggers_rl(au, monkeypatch, reward, deviation):
    monkeypatch.setattr(au, "discover", lambda m: _disc())
    monkeypatch.setattr(au, "known_good", lambda m: set())
    monkeypatch.setattr(au, "evaluate", lambda r, m, g: _ev(reward, deviation))
    monkeypatch.setattr(
        au, "rl_refine", lambda rule, m, g, verbose: ({"RSI14": [[">=", 1.0]]}, _ev(1.5))
    )
    monkeypatch.setattr(au, "apply_rule", lambda rule, m, top: pd.DataFrame())
    rec = au.recommend("IN", verbose=False)
    assert (
        rec["rl_correction_applied"] is True
        and rec["final_rule"] == {"RSI14": [[">=", 1.0]]}
        and rec["picks"] == []
    )
    assert rec["metrics"]["reward"] == 1.5 and rec["discovered_rule"] == _disc()["rule"]


def test_recommend_custom_min_reward_and_main(au, monkeypatch):
    monkeypatch.setattr(au, "discover", lambda m: _disc())
    monkeypatch.setattr(au, "known_good", lambda m: set())
    monkeypatch.setattr(au, "evaluate", lambda r, m, g: _ev(0.5))
    monkeypatch.setattr(au, "rl_refine", lambda *a, **k: pytest.fail("RL must not run"))
    monkeypatch.setattr(au, "apply_rule", lambda rule, m, top: pd.DataFrame())
    assert au.recommend("IN", min_reward=0.5, verbose=False)["rl_correction_applied"] is False
    seen = {}
    monkeypatch.setattr(
        au, "recommend", lambda m, top, min_reward: seen.update(m=m, top=top, mr=min_reward)
    )
    monkeypatch.setattr("sys.argv", ["a", "--market", "US", "--top", "3", "--min-reward", "0.9"])
    assert au.main() == 0 and seen == {"m": "US", "top": 3, "mr": 0.9}
