#!/usr/bin/env python3
# update_bhavcopy_daily.py
# ========================
# Daily refresh of the India bhavcopy OHLCV cache + LMDB store.
#
# What it does (idempotent — safe to run any number of times per day):
#   1. fetch_history() pulls only the trading days missing from the local cache
#      (typically just today's/yesterday's official NSE+BSE bhavcopy) and appends
#      them to cleaned_long.parquet / assembled_long.parquet. Weekends, holidays
#      and not-yet-published days are skipped via the calendar + negative cache,
#      so a run on a closed day is a cheap no-op.
#   2. store.build() rebuilds the LMDB key-value store from every market seed
#      (the freshly-updated IN cache + the committed cleaned_long_<MKT>.parquet
#      seeds) so screeners see the new bar immediately.
#
# Run manually:
#   python3 update_bhavcopy_daily.py            # ~400-day window (default)
#   python3 update_bhavcopy_daily.py --days 30  # only need the recent tail
#
# Schedule it — see the launchd / cron snippets at the bottom of this file.
#
# Exit code 0 on success, 1 on failure (so cron/launchd can alert).

from __future__ import annotations

import argparse
import datetime as _dt
import sys

import bhavcopy_history as bh
import bhavcopy_store as store


def update(n_days: int = 400, verbose: bool = True) -> int:
    """Refresh the IN bhavcopy cache and rebuild the LMDB store.

    Returns the number of symbols in the rebuilt store.
    """
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if verbose:
        print(f"[{ts}] bhavcopy daily update — fetching up to {n_days} days back")

    hist = bh.fetch_history(n_days=n_days, verbose=verbose)
    if verbose:
        print(f"  cache refreshed: {len(hist)} India symbols")

    # refresh the LMDB store — incrementally (only markets whose seed changed,
    # typically just IN) via datalink; fall back to a full rebuild if unavailable.
    try:
        from datalink import build_store_incremental

        res = build_store_incremental(verbose=verbose)
        n = res.get("symbols", 0)
    except Exception:
        n = store.build(verbose=verbose)
    if verbose:
        print(f"  LMDB store refreshed ({n} symbols touched)")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily bhavcopy cache + LMDB refresh")
    ap.add_argument(
        "--days",
        type=int,
        default=400,
        help="calendar days to look back (default 400 ≈ 1 trading year)",
    )
    ap.add_argument("--quiet", action="store_true", help="suppress progress output")
    args = ap.parse_args()

    try:
        update(n_days=args.days, verbose=not args.quiet)
        return 0
    except Exception as e:  # noqa: BLE001 — top-level guard so the scheduler gets exit 1
        print(f"bhavcopy daily update FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


# ── Scheduling ────────────────────────────────────────────────────────────────
#
# NSE/BSE publish the day's bhavcopy after market close (~18:30 IST). Schedule
# the run for the evening (or next morning) on trading days; closed-day runs are
# harmless no-ops.
#
# cron (Linux / macOS) — 7:30 PM IST every weekday:
#   30 19 * * 1-5  cd /path/to/global-stock-screener && \
#     /usr/bin/python3 update_bhavcopy_daily.py --quiet >> ~/bhavcopy_update.log 2>&1
#
# macOS launchd — save as ~/Library/LaunchAgents/com.user.bhavcopy.plist then
#   launchctl load ~/Library/LaunchAgents/com.user.bhavcopy.plist
#   <?xml version="1.0" encoding="UTF-8"?>
#   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
#     "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
#   <plist version="1.0"><dict>
#     <key>Label</key><string>com.user.bhavcopy</string>
#     <key>WorkingDirectory</key><string>/path/to/global-stock-screener</string>
#     <key>ProgramArguments</key>
#       <array>
#         <string>/usr/bin/python3</string>
#         <string>update_bhavcopy_daily.py</string>
#         <string>--quiet</string>
#       </array>
#     <key>StartCalendarInterval</key>
#       <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
#     <key>StandardOutPath</key><string>/tmp/bhavcopy_update.log</string>
#     <key>StandardErrorPath</key><string>/tmp/bhavcopy_update.err</string>
#   </dict></plist>
