# ETL weekly audit — 2026-11-02 09:30 — **FAIL**

Registry: 179 batches, 320422 file-versions, 29084 current files.

| check | value |
|---|---|
| missing locally | 1901 |
| sha spot-check mismatches (n=20) | 7 |
| dataset-legs failing cloud check | 11 |
| duplicate groups (linked, not re-stored) | 28 |
| wasted bytes if duplicates were copies | 77.3 MB |
| stale sources (no batch in 7d) | bhavcopy, cache_seed, gss_cache |

## Replication matrix (local / Dropbox / Google Drive; GitHub = regular-git leg)

| dataset | local | dropbox | gdrive |
|---|---|---|---|
| bhavcopy | ✅ | ✅ | ❌ |
| ohlcv | ✅ | ❌ | ❌ |
| ohlcv_adj | ✅ | ❌ | ❌ |
| cache_seed | ✅ | ✅ | ❌ |
| pipeline | ✅ | ❌ | ❌ |
| market_cache | ✅ | ❌ | ❌ |
| gss_cache | ✅ | ✅ | ❌ |

## Recent batches

| batch | files | new | changed | dupes | status |
|---|---|---|---|---|---|
| BATCH-market_cache-20261101-01 | 26800 | 0 | 5 | 0 | landed |
| BATCH-ohlcv_adj-20261031-01 | 14 | 0 | 1 | 0 | landed |
| BATCH-pipeline-20261031-01 | 108 | 0 | 6 | 0 | landed |
| BATCH-market_cache-20261031-01 | 26800 | 71 | 7557 | 49 | landed |
| BATCH-ohlcv-20261031-01 | 66 | 0 | 15 | 0 | landed |
| BATCH-ohlcv-20261029-01 | 66 | 0 | 1 | 0 | landed |
| BATCH-ohlcv_adj-20261029-01 | 14 | 0 | 1 | 0 | landed |
| BATCH-pipeline-20261029-01 | 108 | 0 | 3 | 0 | landed |
| BATCH-market_cache-20261029-01 | 26687 | 8 | 6793 | 0 | landed |
| BATCH-ohlcv-20261028-01 | 66 | 0 | 5 | 0 | landed |
| BATCH-ohlcv_adj-20261028-01 | 14 | 0 | 1 | 0 | landed |
| BATCH-pipeline-20261028-01 | 108 | 0 | 6 | 0 | landed |

MISSING: /Users/umashankar/market-pipeline/code/python_files/cache_seed/cleaned_long.parquet

MISSING: /Users/umashankar/market-pipeline/code/python_files/cache_seed/earnings_dates_nse/IN.parquet

MISSING: /Users/umashankar/market-pipeline/market_cache/ohlc_all.parquet

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00143651_2026_OFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00244783_2026_OFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00124504_2025_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/00238199_2025_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/01365038_2026_CFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/01429220_2026_OFS.json

MISSING: /Users/umashankar/market-pipeline/market_cache/dart/fin/01391103_2026_CFS.json
