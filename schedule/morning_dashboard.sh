#!/bin/bash
# morning_dashboard.sh
# ====================
# Daily 08:30 orchestrated job. In one run it:
#   1. STAGE scan (in the main working tree, which has the scan stack + data):
#      incrementally updates market data, runs the combined-report scans, and
#      captures news mood + pipeline-execution status.
#   2. STAGE checks (in a fresh dashboard worktree off origin/mailer): runs
#      lint / tests / governance+integrity / import-smoke to check the code.
#   3. Feeds the scan output + status into the dashboard, builds it, snapshots
#      the HTML onto a NEW dated branch  dashboard/YYYY-MM-DD, pushes it, and
#      emails the dashboard.
#
# Report & continue: every step is best-effort; failures surface as red items in
# the dashboard Challenges/Pipeline/Code-Health cards — the email always goes out.
#
# Install via launchd (schedule/com.screener.dashboard.plist) or cron:
#   30 8 * * * /Users/umashankar/Downloads/code/python_files/schedule/morning_dashboard.sh
#
# Sending needs GMAIL_USER / GMAIL_APP_PASSWORD / MAIL_TO in the repo .env;
# otherwise the HTML is saved and still committed to the dated branch.
set -uo pipefail   # NOT -e: report & continue

REPO="/Users/umashankar/Downloads/code/python_files"   # main tree: scan stack + data
PYTHON="${PYTHON:-python3}"
DATE="$(date +%Y-%m-%d)"
BRANCH="dashboard/${DATE}"
WT="/tmp/dashboard-${DATE}"
BIN="${REPO}/.pipeline_bin"        # dashboard code fetched from mailer for stage-scan
LOG="${HOME}/dashboard-mailer.log"

exec >>"$LOG" 2>&1
echo "===== $(date '+%F %T')  morning dashboard: ${BRANCH} ====="

cd "$REPO" || exit 1
git config user.name  >/dev/null 2>&1 || git config user.name  "herrrickshaw"
git config user.email >/dev/null 2>&1 || git config user.email "umashankartd1991@gmail.com"
git config lfs.locksverify false
git fetch origin --quiet

# make the mailer-branch helpers importable in the scan tree (news_feeds + runner)
mkdir -p "$BIN"
for f in pipeline_runner.py news_feeds.py; do
    git show "origin/mailer:${f}" > "${BIN}/${f}" 2>/dev/null || true
done

# ── STAGE 1: data update + scans + news (in the main tree) ────────────────────
echo "--- stage: scan (data update + combined-report scans) ---"
PYTHONPATH="${REPO}:${BIN}" "$PYTHON" "${BIN}/pipeline_runner.py" \
    --stage scan --market IN US --repo "$REPO" --out "${REPO}/results" \
    || echo "  scan stage reported issues (continuing)"

# ── STAGE 2: dashboard worktree on a new dated branch ─────────────────────────
rm -rf "$WT"
git worktree add -B "$BRANCH" "$WT" origin/mailer
cd "$WT" || exit 1
git config lfs.locksverify false

# carry scan outputs + partial status into the worktree the dashboard reads
mkdir -p results combined_report_results dashboards
cp "${REPO}/results/pipeline_status.json" results/ 2>/dev/null || true
cp "${REPO}"/combined_report_results/combined_*.html combined_report_results/ 2>/dev/null || true

# code-flaw checks run here (mailer branch has the CI gates); merged into status
echo "--- stage: checks (lint / tests / governance / imports) ---"
"$PYTHON" pipeline_runner.py --stage checks --repo "$WT" --out results \
    || echo "  checks stage reported issues (continuing)"

# build + send the dashboard (reads results/pipeline_status.json + fragments)
"$PYTHON" repo_dashboard.py --market IN US || echo "  dashboard build reported an issue (continuing)"

# snapshot onto the dated branch
cp results/repo_dashboard.html "dashboards/dashboard_${DATE}.html" 2>/dev/null || true
cp results/repo_dashboard.txt  "dashboards/dashboard_${DATE}.txt"  2>/dev/null || true
cp results/pipeline_status.json "dashboards/pipeline_status_${DATE}.json" 2>/dev/null || true

# keep integrity manifest + dependency map valid so the branch stays conformant
git add -A
"$PYTHON" dep_map.py >/dev/null 2>&1 || true
"$PYTHON" integrity.py --generate >/dev/null 2>&1 || true
git add -A
git commit -m "chore(dashboard): snapshot ${DATE}" || echo "  nothing to commit"

# push with retries — LFS lock API / network can time out transiently
for attempt in 1 2 3; do
    if git push -u origin "$BRANCH" --force; then
        echo "  pushed on attempt ${attempt}"
        break
    fi
    echo "  push attempt ${attempt} failed; retrying in 20s"
    sleep 20
done

cd "$REPO" || exit 1
git worktree remove --force "$WT"
echo "  done: ${BRANCH}"
