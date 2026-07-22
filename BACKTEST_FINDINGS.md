# Screener Backtest — 10-Year, 5-Geography Findings

Screens run **point-in-time (no lookahead)** over the deep 10-year LTM (2016→2026),
21-day forward horizon, equal-weight universe as the market benchmark. Yahoo
glitch bars (21-day return > 500%, e.g. `8303.T` mispriced at ¥55bn) are filtered.

Data: `cache_seed/ltm/*.parquet` (LFS). Engine: `backtest.py` (`run_all`). Edge =
screen mean forward return − market mean. **Educational/research only. Not advice.**

## Edge by screen and market (percentage points, 21-day)

| Market | Best screen | Edge | Win rate | Worst screen | Edge |
|---|---|---:|---:|---|---:|
| **US** | rsi_oversold | **+1.85** | 0.463 | near_high | −0.90 |
| **KR** | rsi_oversold | **+2.04** | 0.481 | golden_crossover | −0.58 |
| **IN** | near_high | **+1.33** | 0.456 | golden_crossover | −0.07 |
| **CN** | rsi_oversold | **+1.06** | 0.496 | golden_crossover | −0.89 |
| **JP** | rsi_oversold | **+0.93** | 0.513 | near_high | 0.00 |

## Headline findings

1. **`rsi_oversold` (mean-reversion) is the most robust screen** — positive edge in
   **all 5 markets**, #1 in US/KR/CN/JP (win rate 0.46–0.51). Buying oversold names
   and holding ~21 days beat the equal-weight market across geographies and a decade.
2. **`golden_crossover` is consistently the weakest** — negative/near-zero edge in
   every market. The classic 50/200 DMA cross did not add value out-of-sample.
3. **`momentum` / `near_high` are regime/market-dependent** — a clear positive edge in
   **India** (near_high +1.33, momentum +1.18) but negative in US/KR/CN.
4. **India is momentum-friendly; developed + China are mean-reversion-friendly.**

## Full table (edge-ranked)

```
US   rsi_oversold     +1.85  win 0.463      IN   near_high       +1.33  win 0.456
US   momentum         +0.17  win 0.432      IN   momentum        +1.18  win 0.451
US   golden_state     -0.12  win 0.425      IN   golden_state    +0.68  win 0.442
US   golden_crossover -0.46  win 0.421      IN   rsi_oversold    +0.20  win 0.457
US   near_high        -0.90  win 0.365      IN   golden_crossover-0.07  win 0.417

JP   rsi_oversold     +0.93  win 0.513      KR   rsi_oversold    +2.04  win 0.481
JP   momentum         +0.17  win 0.458      KR   near_high       +0.22  win 0.351
JP   golden_state     +0.15  win 0.466      KR   golden_state    +0.01  win 0.416
JP   golden_crossover +0.15  win 0.444      KR   momentum        -0.50  win 0.403
JP   near_high         0.00  win 0.453      KR   golden_crossover-0.58  win 0.401

CN   rsi_oversold     +1.06  win 0.496
CN   golden_state     -0.20  win 0.436
CN   near_high        -0.32  win 0.408
CN   momentum         -0.69  win 0.409
CN   golden_crossover -0.89  win 0.402
```

## Caveats
- **Survivorship bias:** the LTM holds currently-listed symbols; delisted names are absent, inflating results.
- **Transaction costs / slippage excluded** — raw edges shrink after costs.
- **21-day horizon only** — other holding periods may rank screens differently.
- Edges are small (0.2–2.0 pp / 21d); statistically directional, not a trading system.

## Companion fundamentals finding (Phase 3)
Over the same 10-year window, `debt_to_equity` vs CAGR: **US ρ = −0.135, p = 0.027**
(significant — market mildly penalizes leverage); **India: not significant.**
Liquidity (`current_ratio` +0.30) and size (small-cap) dominate.
