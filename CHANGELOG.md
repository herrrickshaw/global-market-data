# Changelog

## [Unreleased] — 2026-07-30 — DDD scaffold + IPO tracker duplicates removed

### Removed
- **`src/`** — a dead DDD scaffold (`market_data`/`backtesting`/`reporting`/
  `stock_analysis`/`portfolio_strategy`/`risk_signals`, one incidental
  commit, no adapters, no tests, nothing in this repo or `market-pipeline`
  imported it). Superseded by the real, working DDD implementation now
  living at `market-pipeline/code/python_files/stock_ddd/` in the
  `global-market-research-platform` repo — that's the canonical location
  going forward, not this one.
- **`ipo_tracker.py`** (839 lines) / **`ipo_monitor.py`** (113 lines) —
  duplicates of the canonical, actively-maintained copies in
  `market-pipeline/code/python_files/`. Diffed line-by-line before removal:
  the only substantive difference was a stale `~/Downloads/market_cache`
  path default (this repo's `ipo_tracker.py`) and a stale
  `~/Downloads/data/bhavcopy_cache` default (`ipo_monitor.py`) — both the
  same deprecated location this repo's own `etl_registry.py` weekly audit
  already flags as gone stale. No unique logic was lost.

See `~/.claude/plans/swift-nibbling-shore.md` (on the machine this repo is
checked out on) for the full architecture-retrofit plan this cleanup was
part of.
