# DATA_ACCESS — how to get this repo's data

> ⚠️ **Git LFS in this repo is currently unreachable** — the account's LFS budget
> is exhausted, so `git clone` / `git lfs pull` cannot download the data files
> (they arrive as ~130-byte pointer stubs). This is account-wide, not specific to
> this repo. Clone with `GIT_LFS_SKIP_SMUDGE=1 git clone …` to avoid errors.

## This repo's LFS footprint (audit 2026-07-22)

| LFS objects | Total size |
|---|---|
| 107 | 1,310.9 MB |

## Where the data actually comes from

**Rescue-first repo — partly irreplaceable.** 10.5y point-in-time OHLCV including 964 delisted names. Primary local copy: `~/repos/global-market-data/cache_seed/ltm/*.parquet` — verify completeness before trusting. Re-collection covers only listed names (NSE Bhavcopy archive); delisted history cannot be re-collected.

## Price adjustment status (2026-07-23)

| Market | Panel state | Adjusted read path |
|---|---|---|
| IN | RAW bhavcopy (true raw) | `warehouse/ohlcv_adj/IN/` (full copy; 789 CA factors) |
| JP, KR, US | yfinance-adjusted at collection + residual post-assembly breaks | **overlay-first**: `warehouse/ohlcv_adj/<MKT>/corrected_symbols.parquet` supersedes those symbols' `warehouse/ohlcv/<MKT>` rows |
| CN, EU | yfinance-adjusted at collection, zero confirmed residuals | `warehouse/ohlcv/<MKT>` directly |

Residual breaks are detected + calendar-confirmed by
`~/market-pipeline/code/python_files/price_adjuster_global.py` — re-run it after
any full panel re-download. All candidates (confirmed or not) are audited in
`warehouse/adjustment_factors_heuristic.parquet`.

⚠️ yfinance bug found 2026-07-23: `history(auto_adjust=True)` can serve
UNADJUSTED prices for recent JP/KR splits even when `.splits` knows the event —
validate adjustments by series continuity, not yf windowed returns.

## Account-wide context

- Full pointer inventory, dedup plan and audit tooling:
  [`herrrickshaw/repo-data-dedup`](https://github.com/herrrickshaw/repo-data-dedup)
- Source catalogue + re-collection SOP for every dataset:
  [`SOP_DATA_SOURCES.md`](https://github.com/herrrickshaw/repo-data-dedup/blob/main/SOP_DATA_SOURCES.md)
- Migration recipe off LFS:
  [`PLAYBOOK.md`](https://github.com/herrrickshaw/repo-data-dedup/blob/main/PLAYBOOK.md)
- **Policy: do not add new LFS objects** — they would be born unreachable. New data
  goes in as gzipped/parquet regular git objects under 50 MB, one canonical format,
  with its collector script committed alongside.
