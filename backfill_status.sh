#!/bin/bash
# backfill_status.sh — one-shot progress check for a running bulk_seed backfill.
# Run anytime (e.g. every 5 min):  watch -n300 ./backfill_status.sh
# Or a manual loop:  while :; do ./backfill_status.sh; sleep 300; done
set -uo pipefail
cd "$(dirname "$0")"

LOG="$(ls -t results/backfill_*.log 2>/dev/null | head -1)"
echo "===== $(date '+%F %T')  backfill status ====="
if [ -z "${LOG:-}" ]; then echo "  no backfill log found"; else
  echo "  log: $(basename "$LOG")"
  last=$(grep -Eo "Batch [0-9]+/[0-9]+" "$LOG" | tail -1)
  ok=$(grep -c "OK (" "$LOG" 2>/dev/null || echo 0)
  echo "  progress: ${last:-starting} · batches OK: ${ok}"
  grep -E "^result:|fetched" "$LOG" | tail -1
fi
echo "  --- LTM coverage ---"
python3 - <<'PY' 2>/dev/null
import warnings; warnings.filterwarnings("ignore")
import market_memory as mm
print(mm.status(["IN","US"])[["market","tier","symbols","start","end","rows"]].to_string(index=False))
PY
