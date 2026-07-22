#!/bin/bash
# daily_pipeline.sh — full token-free Daily Market Brief.
# Runs entirely in local Python (no Claude / no LLM tokens) and emails the brief.
#
# Schedule it with cron or launchd (see daily_pipeline.plist). Set credentials
# in the environment first (GMAIL_USER, GMAIL_APP_PASSWORD, MAIL_TO).
#
#   ./daily_pipeline.sh            # refresh + screen + send
#   ./daily_pipeline.sh --draft    # refresh + screen + save brief_today.html
set -uo pipefail
cd "$(dirname "$0")"
PY=python3
LOG="daily_pipeline_$(date +%Y%m%d).log"
{
  echo "=== Daily pipeline $(date) ==="
  echo "[1/4] India EOD refresh (official bhavcopy, incremental)"
  $PY bhavcopy_history.py 400 || echo "  bhavcopy refresh failed (will use cache)"
  echo "[2/4] India full screener scan"
  $PY scan_bhavcopy.py || echo "  scan failed (will use latest cache)"
  echo "[3/4] India combined report (fundamentals + street talk)"
  $PY daily_combined_report.py --market IN --html || echo "  combined report failed"
  echo "[3b] refresh India CCC screen (screener.in)"
  $PY -c "import screener_in as s; s.ccc_screen().to_parquet('cache_seed/india_ccc_screen.parquet', index=False)" || echo "  CCC refresh skipped"
  echo "[4/4] build + send mailer"
  $PY send_mailer.py "$@"
  echo "=== done $(date) ==="
} >> "$LOG" 2>&1
echo "pipeline complete — see $LOG"
