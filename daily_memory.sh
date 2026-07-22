#!/usr/bin/env bash
# daily_memory.sh — one-command daily refresh of the two-tier market memory.
#   1. update_bhavcopy_daily.py : pull today's official NSE+BSE bhavcopy + rebuild LMDB
#   2. market_memory.py --daily : upsert all 20 markets into the 5y LTM, derive 1y STM
# Idempotent; safe to run repeatedly. Exit non-zero if either stage fails.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== $(date '+%F %T') daily market-memory refresh ==="
python3 update_bhavcopy_daily.py --quiet
python3 market_memory.py --daily "$@"
python3 serving_layer.py --refresh          # rebuild materialised serving views + CDC deltas
python3 pipeline.py --all --sync            # CRUD: update filter-clearing watchlists (all markets)
python3 pipeline.py --market IN --refresh   # deep-track promoted IN stocks (fundamentals)
python3 market_memory.py --status
python3 pipeline.py --status
echo "=== done ==="
