# CLAUDE.md — global-market-data

## What this repo is

Deep, multi-market historical equity data (10 years, 20 markets, ~44M OHLCV rows) plus the
**point-in-time, no-lookahead backtest engine** that turns it into evidence on which screens
actually work, where. Sibling repo to [`global-stock-screener`](https://github.com/herrrickshaw/global-stock-screener)
(same strategy library, ~1yr cached data, optimized for a fast clone and *today's* results) — use
that repo for live screening, this one to check whether a screen has a real historical edge first.

> ⚠️ Educational / research only. NOT investment advice. Backtest edges are historical averages,
> not guarantees.

## Headline finding (`BACKTEST_FINDINGS.md`, 10-year point-in-time, 21-day forward)

1. **`rsi_oversold` (mean-reversion)** is the most robust screen — positive edge in every market
   tested, #1 in US/KR/CN/JP.
2. **`golden_crossover`** is consistently the weakest — negative/near-zero edge everywhere.
3. **India is momentum-friendly** (`near_high` +1.33pp, `momentum` +1.18pp) while the
   **US/KR/CN lean mean-reversion** — the edge is regime/market-dependent, not universal.

Full ranked table and methodology in `BACKTEST_FINDINGS.md`. Data store is LFS-free as of the
2026-07-22 rebuild — regular git objects, not LFS pointers.

## Key docs (read before assuming, don't duplicate here)

| Doc | What |
|---|---|
| `ARCHITECTURE.md` / `DATA_SCHEMA_AND_ARCHITECTURE.md` | system + schema design |
| `DATA_AND_MODULES.md` | module map |
| `MEMORY_ARCHITECTURE.md` | this repo's own PIT/lookahead-safety design (unrelated to Claude memory — see below) |
| `HOW_IT_WORKS.md` / `QUICK_START.md` | onboarding |
| `MODEL_ENHANCEMENT_ANALYSIS.md` / `F1_TUNING_METHODOLOGY_REPORT.md` | scoring methodology |

## Cross-session project memory

A Claude Code session rooted here has no persisted memory of its own — and note `MEMORY_ARCHITECTURE.md`
in this repo is about the *backtest engine's* point-in-time data design, not Claude's memory system.
Decisions, hazards, and credential locations for the broader market-research platform live under
the `~` (home) session instead: `~/.claude/projects/-Users-umashankar/memory/`, indexed by
`MEMORY.md` and mapped by the `reference_memory_map` memory (see `project_piotroski_backtests`,
`reference_deep_10y_market_data` for prior findings on this exact dataset). From here, reach it
with `qmd search "<topic>"` (indexes that memory directory regardless of working directory) rather
than assuming this session has its own history. For fast orientation, a published snapshot of 128
incident-sourced rules from across that memory is browsable at
https://claude.ai/code/artifact/e29ceaeb-099e-4de7-ab43-dcd97a2d8f57 ("Ways of Working") — a
starting point, not authoritative over the live memory files.
