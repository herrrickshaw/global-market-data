# Hybrid auto-screener + multi-market calendar

Implements the three-layer design from *ML_Stock_Screening_System.docx*, adapted
to run on the precomputed serving views (fast, no heavy training loop).

## Three layers (`auto_screener.py`)

| Layer | Doc | Here |
|-------|-----|------|
| **Supervised anchor** | XGBoost on labelled returns | `known_good(market)` — the "known universe": symbols the existing screens would flag (strategy gates on the deep serving view + live CCC for IN) **plus** the trained forward-return classifier's Buy/Strong-Buy names (`ml_supervised`, see below). These are the labels. |
| **Unsupervised discovery** | UMAP + HDBSCAN + KMeans | `discover()` — StandardScaler + KMeans over the serving features (RSI, drawdown, multi-horizon returns, trend flags) restricted to liquid names. The cluster most **enriched with known-good, liquid, high-return** stocks becomes a *new screen*: its 10th–90th-percentile feature bounds → a rule. Adapts to **market conditions** (regime) and **liquidity**. |
| **Reinforcement correction** | PPO agent | `rl_refine()` — kicks in only when the discovery **deviates too far** from the known universe (reward < threshold or deviation > 0.5). A reward-driven policy search perturbs the thresholds, keeping changes that raise `reward = 2·overlap + 0.5·liquidity + 0.01·return − 1.5·deviation` (the doc's reward shape), pulling the screen back toward the validated universe. |

Regime-aware: **Bear** tape hard-requires `Above200DMA` + caps drawdown (no falling
knives); **Bull** tape requires `GoldenCross` and lets momentum breathe.

```bash
python3 auto_screener.py --market IN --top 15
python3 auto_screener.py --market US
```

Output: printed regime + discovered rule + metrics + top picks, saved to
`cache_seed/discovered_screens/<MKT>.json` (gitignored, regenerated).

Example (IN, Bear tape): discovered a cluster with **100% overlap** vs 530 known-good
names, reward 3.17, no RL needed. When the anchor is thin the RL corrector engages
(observed reward 0.27 → 1.96).

## Multi-market holiday calendar (`market_holidays.py`)

Trading-day calendar for **all 20 markets** — weekend rules (Saudi rests Fri/Sat,
others Sat/Sun) + 2025–2026 exchange holiday sets (IN pulls the live NSE list).

```python
from market_holidays import is_trading_day, trading_days, next_trading_day, is_market_open_today
```

Wired into `market_memory.update_all`: markets **closed today are skipped** (no
wasted fetch/processing). E.g. on 1 Jul, HK (SAR Day) and CA (Canada Day) are
skipped automatically.

## Full doc pipeline: supervised classifier + RL trade agent

Two modules implement the doc's Layer 1 and Layer 3 end-to-end, using only libs
already in the repo (sklearn + numpy — **no XGBoost/torch install required**), with
optional upgrade paths if you install them.

### Layer 1 — supervised forward-return classifier (`ml_supervised.py`)
Trains on the 5y LTM with **walk-forward validation** (TimeSeriesSplit, no lookahead)
to predict the doc's 4 classes (Strong Buy / Buy / Hold / Avoid). Engine:
`HistGradientBoostingClassifier` (same GBT family as XGBoost); uses `xgboost` instead
if installed. Its Buy/Strong-Buy predictions feed `known_good`.

```bash
python3 ml_supervised.py --market IN --train        # → walk-forward acc + importances + model
python3 ml_supervised.py --market IN                # → today's Buy/Strong-Buy names
```
Measured (IN): 22,102 samples, walk-forward accuracy ~0.60, top features
log-turnover / drawdown / RSI / momentum. Horizon is a quarter (63d) today and
widens toward 1y (`--horizon`) as the LTM deepens.

### Layer 3 — RL trade decision engine (`rl_trader.py`)
A trading **environment** (state/action/reward from doc Table 2 + Code 5; 8 actions:
SKIP / ENTER_SMALL / ENTER_FULL / HOLD / ADD / PARTIAL_EXIT / FULL_EXIT / STOP_LOSS)
+ a **tabular Q-learning agent with experience replay** (numpy only). Learns from
historical LTM episodes; anti-reward-hacking guards penalise invalid actions (e.g.
STOP_LOSS with no position). Uses `stable_baselines3` PPO on the same env if installed
(`--ppo`).

```bash
python3 rl_trader.py --market IN --train --episodes 5000   # learn the policy
python3 rl_trader.py --market IN                            # actions on today's shortlist
```
The agent consumes the auto-screener shortlist + supervised score and emits an
ENTER/HOLD/EXIT action per name.

## Validation vs Screener.in popular screens (`validation.py`)

Grounds discoveries against Screener.in's **most-used screens** (the curated
"popular" list + widely-cloned community screens):

- **14 price/technical/liquidity screens computed locally** from the serving view
  (golden/bearish crossover, 52w high/low, near-200DMA, Darvas, RSI-oversold,
  multibagger/quality/value momentum, …).
- **26 fundamental popular screens from public-domain Screener.in data** — pulled
  from the official curated `/explore/` list (verified IDs: Bull Cartel, Piotroski,
  Coffee-Can, Magic Formula, FII Buying, Debt Reduction, Growth-without-Dilution,
  Graham/Buffett, Multibagger, FCF Yield, …). Fetched once, cached, then read offline.

```bash
python3 public_screens.py --fetch      # pull the 26 public screens → cache (needs screener.in)
python3 public_screens.py --list       # show cached screens
python3 validation.py --market IN      # report over all 40 screens
```

The public screens are fetched with **verified official screen IDs discovered from
Screener.in's own explore page** (no guessed IDs); the fetcher validates each returns
a real table and skips any that don't. Override/add URLs via `public_screens.json`.
Reports per-screen name counts and validates today's auto-screener recommendation:
- **grounded %** — picks appearing in ≥1 popular screen (market already validates them)
- **novel %** — picks in none (a genuinely new pattern)

The union of the local popular screens is also folded into `auto_screener.known_good`
as an extra supervised anchor. Measured (IN): the recommendation was **100% grounded**
(golden_crossover / midcap_momentum / quality_momentum overlaps), 0% novel in the
current Bear tape.

> ⚠️ Research/education only. Discovered screens, RL actions and validations are
> historical associations, not predictions or advice. Human review always wins.
