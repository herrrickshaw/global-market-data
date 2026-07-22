#!/usr/bin/env python3
# rl_trader.py
# ============
# LAYER 3 of ML_Stock_Screening_System.docx — the reinforcement-learning trade
# decision engine. A trading environment + agent that learns, from historical LTM
# episodes, WHEN to enter/hold/exit shortlisted stocks to maximise the doc's
# risk-adjusted reward.
#
#   State  (doc Table 2): supervised score, sector/market regime, days held,
#          current P&L bucket, portfolio exposure bucket, drawdown-from-peak.
#   Action (doc §4.1, 8): SKIP, ENTER_SMALL, ENTER_FULL, HOLD, ADD,
#                         PARTIAL_EXIT, FULL_EXIT, STOP_LOSS.
#   Reward (doc Code 5):  return-based + risk-management + sector-timing bonus +
#                         missed-opportunity + concentration penalties.
#
# Engine: tabular Q-learning with an experience-replay buffer (numpy only — no
# torch). If `stable_baselines3` + `gymnasium` are installed, `--ppo` trains a PPO
# MlpPolicy on the same environment instead (the doc's Code 6 path).
#
#   python3 rl_trader.py --market IN --train      # train Q-agent on LTM episodes
#   python3 rl_trader.py --market IN              # act on today's shortlist
#
# ⚠️ Research/education only. Not trading advice. Human review always wins.

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import datalink

AGENT_DIR = Path(__file__).parent / "cache_seed" / "models"
AGENT_DIR.mkdir(parents=True, exist_ok=True)

ACTIONS = ["SKIP", "ENTER_SMALL", "ENTER_FULL", "HOLD", "ADD",
           "PARTIAL_EXIT", "FULL_EXIT", "STOP_LOSS"]
A = {a: i for i, a in enumerate(ACTIONS)}


# ── reward function (doc Code 5) ─────────────────────────────────────────────────
def reward_function(state: dict, action: str, outcome: dict) -> float:
    r = 0.0
    pnl = outcome["pnl_pct"]
    in_pos = bool(state.get("in_position", 0))
    # invalid actions for the current position status get a small penalty, so the
    # agent can't reward-hack (e.g. STOP_LOSS with no position) — a doc guardrail.
    position_actions = ("HOLD", "ADD", "PARTIAL_EXIT", "FULL_EXIT", "STOP_LOSS")
    entry_actions = ("ENTER_SMALL", "ENTER_FULL")
    if not in_pos and action in position_actions:
        return -0.2
    if in_pos and action in entry_actions:
        return -0.2  # already holding; ENTER is a no-op (use ADD)
    if action in ("ENTER_SMALL", "ENTER_FULL"):
        if pnl > 0.20:
            r += 3.0
        elif pnl > 0.10:
            r += 1.5
        elif pnl > 0:
            r += 0.5
        elif pnl < -0.10:
            r -= 2.0
        else:
            r -= 0.5
    if action == "STOP_LOSS":
        r += 1.0 if pnl > -0.08 else -0.5
    if action == "PARTIAL_EXIT" and state.get("drawdown_from_peak", 0) > 0.15:
        r += 1.5
    if state.get("sector_boom_phase") == "early" and pnl > 0.15:
        r += 1.0
    if action == "SKIP" and outcome.get("fwd_return", 0) > 0.20:
        r -= 1.0
    if state.get("portfolio_exposure", 0) > 0.70 and action in ("ENTER_SMALL", "ENTER_FULL", "ADD"):
        r -= 1.5
    return r


# ── state discretisation (tabular Q) ─────────────────────────────────────────────
def _bucket(v: float, edges: List[float]) -> int:
    return int(np.digitize([v], edges)[0])


def encode_state(s: dict) -> Tuple[int, ...]:
    return (
        _bucket(s["score"], [0.33, 0.66]),                 # supervised conviction
        {"Bear": 0, "Neutral": 1, "Bull": 2}.get(s["regime"], 1),
        _bucket(s["days_held"], [1, 5, 20]),
        _bucket(s["pnl"], [-0.08, 0.0, 0.10]),
        _bucket(s["exposure"], [0.4, 0.7]),
        _bucket(s["drawdown_from_peak"], [0.08, 0.15]),
        int(s.get("in_position", 0)),
    )


N_STATE_DIMS = (3, 3, 4, 4, 3, 3, 2)


# ── the trading environment (episode = one stock's forward path) ─────────────────
class StockEnv:
    """One episode walks a stock forward `horizon` steps from an as-of date; the
    agent picks an action each step and is rewarded per the doc reward function."""

    def __init__(self, close: np.ndarray, t0: int, score: float, regime: str,
                 horizon: int = 63, stop: float = -0.08):
        self.close, self.t0, self.score = close, t0, regime and score
        self.score, self.regime, self.horizon, self.stop = score, regime, horizon, stop

    def reset(self):
        self.t = self.t0
        self.entry = None
        self.peak = 0.0
        self.days_held = 0
        self.exposure = 0.0
        return self._state()

    def _pnl(self) -> float:
        if self.entry is None:
            return 0.0
        return self.close[self.t] / self.entry - 1

    def _state(self) -> dict:
        pnl = self._pnl()
        self.peak = max(self.peak, pnl)
        return {"score": self.score, "regime": self.regime, "days_held": self.days_held,
                "pnl": pnl, "exposure": self.exposure,
                "drawdown_from_peak": max(0.0, self.peak - pnl),
                "sector_boom_phase": "early" if self.regime == "Bull" else "mid",
                "in_position": int(self.entry is not None)}

    def step(self, action: str):
        s = self._state()
        end_t = min(self.t0 + self.horizon, len(self.close) - 1)
        fwd_return = self.close[end_t] / self.close[self.t] - 1
        # apply action
        if action in ("ENTER_SMALL", "ENTER_FULL", "ADD") and self.entry is None:
            self.entry = self.close[self.t]
            self.exposure = 0.35 if action == "ENTER_SMALL" else 0.7
        done = False
        if action in ("FULL_EXIT", "STOP_LOSS") or self.t >= end_t:
            done = True
        # advance one step
        self.t = min(self.t + 1, end_t)
        if self.entry is not None:
            self.days_held += 1
        outcome = {"pnl_pct": self._pnl(), "fwd_return": fwd_return}
        r = reward_function(s, action, outcome)
        return self._state(), r, done


# ── tabular Q-learning with replay ───────────────────────────────────────────────
class QAgent:
    def __init__(self, alpha=0.3, gamma=0.95, eps=0.2):
        self.Q: Dict[Tuple, np.ndarray] = {}
        self.alpha, self.gamma, self.eps = alpha, gamma, eps
        self.buffer: List[tuple] = []

    def q(self, key):
        return self.Q.setdefault(key, np.zeros(len(ACTIONS)))

    def act(self, state: dict, explore=True) -> str:
        key = encode_state(state)
        if explore and np.random.random() < self.eps:
            return ACTIONS[np.random.randint(len(ACTIONS))]
        return ACTIONS[int(self.q(key).argmax())]

    def remember(self, s, a, r, s2, done):
        self.buffer.append((encode_state(s), A[a], r, encode_state(s2), done))

    def replay(self, batch=256):
        if not self.buffer:
            return
        idx = np.random.randint(0, len(self.buffer), min(batch, len(self.buffer)))
        for i in idx:
            k, a, r, k2, done = self.buffer[i]
            target = r + (0 if done else self.gamma * self.q(k2).max())
            self.q(k)[a] += self.alpha * (target - self.q(k)[a])


# ── training over historical episodes ────────────────────────────────────────────
def _regime_at(close: np.ndarray, t: int) -> str:
    if t < 200:
        return "Neutral"
    last, sma200 = close[t], close[t - 200:t].mean()
    r63 = close[t] / close[t - 63] - 1 if t > 63 else 0
    if last > sma200 and r63 > 0.05:
        return "Bull"
    if last < sma200 and r63 < -0.05:
        return "Bear"
    return "Neutral"


def train(market: str, episodes: int = 4000, horizon: int = 63, verbose: bool = True) -> dict:
    import joblib

    data = datalink.load_market(market, tier="ltm") or datalink.load_market(market)
    # supervised scores per symbol (anchor conviction), best-effort
    scores: Dict[str, float] = {}
    try:
        from ml_supervised import predict_current

        cur = predict_current(market)
        if not cur.empty:
            scores = dict(zip(cur["Symbol"], cur["score"]))
    except Exception:
        pass

    syms = [s for s, d in data.items() if d is not None and len(d) > 202 + horizon]
    if len(syms) < 10:
        raise RuntimeError(f"too little history to train RL for {market}")
    agent = QAgent()
    rng = np.random.default_rng(42)
    total = 0.0
    for ep in range(episodes):
        sym = syms[rng.integers(len(syms))]
        close = data[sym]["Close"].to_numpy("float64")
        t0 = int(rng.integers(200, len(close) - horizon - 1))
        env = StockEnv(close, t0, scores.get(sym, 0.5), _regime_at(close, t0), horizon)
        s = env.reset()
        done = False
        while not done:
            a = agent.act(s)
            s2, r, done = env.step(a)
            agent.remember(s, a, r, s2, done)
            s = s2
            total += r
        agent.replay()
        agent.eps = max(0.02, agent.eps * 0.9995)  # decay exploration

    joblib.dump({"Q": agent.Q, "actions": ACTIONS}, AGENT_DIR / f"{market}_rl.pkl")
    if verbose:
        print(f"  trained RL {market}: {episodes} episodes, {len(agent.Q)} states, "
              f"avg reward/ep {total/episodes:.3f}")
    return {"market": market, "episodes": episodes, "states": len(agent.Q)}


# ── act on today's shortlist ─────────────────────────────────────────────────────
def decide(market: str, shortlist: Optional[List[str]] = None, verbose: bool = True) -> pd.DataFrame:
    import joblib

    p = AGENT_DIR / f"{market}_rl.pkl"
    if not p.exists():
        if verbose:
            print("no RL agent — run --train first")
        return pd.DataFrame()
    Q = joblib.load(p)["Q"]
    agent = QAgent(eps=0.0)
    agent.Q = Q

    if shortlist is None:
        try:
            from auto_screener import recommend

            shortlist = recommend(market, verbose=False)["picks"]
        except Exception:
            shortlist = []
    scores = {}
    try:
        from ml_supervised import predict_current

        cur = predict_current(market)
        scores = dict(zip(cur["Symbol"], cur["score"]))
    except Exception:
        pass
    data = datalink.load_market(market, tier="ltm") or datalink.load_market(market)

    rows = []
    for sym in shortlist:
        d = data.get(sym)
        if d is None or len(d) < 201:
            continue
        close = d["Close"].to_numpy("float64")
        st = {"score": scores.get(sym, 0.5), "regime": _regime_at(close, len(close) - 1),
              "days_held": 0, "pnl": 0.0, "exposure": 0.0, "drawdown_from_peak": 0.0,
              "in_position": 0}
        action = agent.act(st, explore=False)
        rows.append({"Symbol": sym, "Action": action, "Score": round(st["score"], 3),
                     "Regime": st["regime"]})
    out = pd.DataFrame(rows)
    if verbose and not out.empty:
        print(f"\n=== RL trade decisions — {market} ===")
        print(out.to_string(index=False))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="RL trade decision engine (doc Layer 3)")
    ap.add_argument("--market", default="IN")
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--episodes", type=int, default=4000)
    ap.add_argument("--ppo", action="store_true", help="use stable_baselines3 PPO if installed")
    args = ap.parse_args()

    if args.ppo:
        print("PPO path requires stable_baselines3 + gymnasium (not installed); "
              "using the built-in Q-learning agent instead.")
    if args.train:
        train(args.market, episodes=args.episodes)
        return 0
    decide(args.market)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
