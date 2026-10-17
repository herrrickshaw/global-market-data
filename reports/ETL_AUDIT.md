# ETL weekly audit — 2026-10-12 09:30 — **FAIL**

Registry: 134 batches, 234212 file-versions, 27673 current files.

| check | value |
|---|---|
| missing locally | 3062 |
| sha spot-check mismatches (n=20) | 5 |
| dataset-legs failing cloud check | 7 |
| duplicate groups (linked, not re-stored) | 28 |
| wasted bytes if duplicates were copies | 77.0 MB |
| stale sources (no batch in 7d) | bhavcopy, cache_seed, gss_cache |

## Replication matrix (local / Dropbox / Google Drive; GitHub = regular-git leg)

| dataset | local | dropbox | gdrive |
|---|---|---|---|
| bhavcopy | ✅ | ✅ | ✅ |
| ohlcv | ✅ | ✅ | ❌ |
| ohlcv_adj | ✅ | ❌ | ❌ |
| cache_seed | ✅ | ✅ | ✅ |
| pipeline | ✅ | ❌ | ❌ |
| market_cache | ✅ | ❌ | ❌ |
| gss_cache | ✅ | ✅ | ✅ |

## Recent batches

| batch | files | new | changed | dupes | status |
|---|---|---|---|---|---|
| BATCH-pipeline-20261011-01 | 108 | 0 | 1 | 0 | landed |
| BATCH-market_cache-20261011-01 | 24190 | 105 | 6 | 69 | landed |
| BATCH-ohlcv-20261010-01 | 66 | 0 | 5 | 0 | landed |
| BATCH-pipeline-20261010-01 | 108 | 0 | 7 | 0 | landed |
| BATCH-market_cache-20261010-01 | 24085 | 447 | 6588 | 297 | landed |
| BATCH-pipeline-20261009-01 | 108 | 0 | 7 | 0 | landed |
| BATCH-market_cache-20261009-01 | 23638 | 274 | 7550 | 184 | landed |
| BATCH-ohlcv-20261009-01 | 66 | 0 | 15 | 0 | landed |
| BATCH-ohlcv_adj-20261009-01 | 14 | 0 | 1 | 0 | landed |
| BATCH-ohlcv-20261006-01 | 66 | 0 | 14 | 0 | landed |
| BATCH-ohlcv_adj-20261006-01 | 14 | 0 | 3 | 0 | landed |
| BATCH-pipeline-20261006-01 | 108 | 0 | 4 | 0 | landed |

MISSING: /Users/umashankar/market-pipeline/code/python_files/cache_seed/cleaned_long.parquet

MISSING: /Users/umashankar/market-pipeline/code/python_files/cache_seed/earnings_dates_nse/IN.parquet

MISSING: /Users/umashankar/market-pipeline/market_cache/ohlc_all.parquet

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00192499_2025_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00360595_2025_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00143651_2026_OFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00244783_2026_OFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00124504_2025_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00238199_2025_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/01365038_2026_CFS.json
