# Two-tier market memory (5y LTM / 1y STM)

The screener keeps price history in two tiers, refreshed daily like CRUD in SQL —
so it *feeds on good, deep data* but *stays current*.

| Tier | Window | File | Role |
|------|--------|------|------|
| **Long-term memory (LTM)** | **5 years** | `cache_seed/ltm/<MKT>.parquet` | the archive — deep history for backtests / 5y performance |
| **Short-term memory (STM)** | **1 year** | `cache_seed/cleaned_long[_MKT].parquet` | the hot layer — exactly what `screener_kit` + the LMDB store read |

The STM is always a **trailing 1-year slice derived from the LTM**, so the two
never drift apart.

## CRUD mapping (`market_memory.py`)

| SQL | Function | What it does |
|-----|----------|--------------|
| CREATE | `init(market)` | first-time 5-year backfill (network for non-IN) |
| READ   | `read(market, tier)` | `{symbol: OHLCV df}` from LTM or STM |
| UPDATE | `update(market)` | fetch new bars → **upsert** into LTM → **evict** >5y → **derive** STM |
| DELETE | `evict(long, years)` / `drop(market, syms)` | trim by age / remove delisted symbols |

**Upsert semantics:** new bars are unioned with the LTM and de-duplicated on
`(Symbol, Date)` keeping the **newest write** — so a revised/adjusted bar
overwrites the stale one and re-runs are idempotent (no double-appends).

## Data sources

- **IN** — the official NSE+BSE **bhavcopy** (maintained by
  `update_bhavcopy_daily.py`); its output is upserted into the IN LTM, which
  accumulates toward the full 5 years over time.
- **The other 19 markets** — `data_sources.fetch` (yahoo → stooq fallback),
  refreshing the symbols already tracked (or the full universe on `--create`).

## Daily operation

```bash
python3 daily_memory.sh                    # bhavcopy → all-20 memory update
# or the pieces:
python3 update_bhavcopy_daily.py           # refresh IN bhavcopy + LMDB
python3 market_memory.py --daily           # upsert all 20 into LTM, derive STM
python3 market_memory.py --status          # tier coverage report
python3 market_memory.py --create          # one-time 5y backfill (network)
```

Schedule `daily_memory.sh` in cron/launchd after market close (see the footer of
`update_bhavcopy_daily.py` for ready-to-use snippets). Closed-day runs are cheap
idempotent no-ops.
