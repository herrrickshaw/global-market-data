#!/usr/bin/env python3
# market_calendar.py
# ==================
# NSE/BSE equity trading-holiday calendar, so data fetchers request bhavcopy ONLY
# on actual working days (skip weekends AND exchange holidays) instead of probing
# dead dates and relying on negative caching alone.
#
# Source of truth: nsepython.nse_holidays()["CM"] (Capital Market segment), fetched
# live and cached to JSON. A hardcoded fallback covers the case where the live call
# fails (no network / cookie issues on macOS). The list is the union of both.

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Iterable, List, Set

CACHE = Path(os.environ.get("BHAV_CACHE", Path.home() / "Downloads" / "data" / "bhavcopy_cache"))
CACHE.mkdir(parents=True, exist_ok=True)
HOLIDAY_CACHE = CACHE / "nse_holidays.json"
REFRESH_AFTER_DAYS = 14  # re-pull the live list at most this often

# Hardcoded fallback — NSE equity (CM) trading holidays. Saturdays/Sundays are
# excluded automatically elsewhere, so only weekday holidays matter here.
_FALLBACK = {
    # 2025
    "2025-02-26",
    "2025-03-14",
    "2025-03-31",
    "2025-04-10",
    "2025-04-14",
    "2025-04-18",
    "2025-05-01",
    "2025-08-15",
    "2025-08-27",
    "2025-10-02",
    "2025-10-21",
    "2025-10-22",
    "2025-11-05",
    "2025-12-25",
    # 2026 (from nse_holidays()["CM"])
    "2026-01-15",
    "2026-01-26",
    "2026-02-15",
    "2026-03-03",
    "2026-03-21",
    "2026-03-26",
    "2026-03-31",
    "2026-04-03",
    "2026-04-14",
    "2026-05-01",
    "2026-05-28",
    "2026-06-26",
    "2026-08-15",
    "2026-09-14",
    "2026-10-02",
    "2026-10-20",
    "2026-11-08",
    "2026-11-10",
    "2026-11-24",
    "2026-12-25",
}


def _parse(s: str) -> str:
    """Normalise '15-Jan-2026' or '2026-01-15' → ISO 'YYYY-MM-DD'."""
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%B-%Y"):
        try:
            return _dt.datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s.strip()


def _load_cache() -> Set[str]:
    if not HOLIDAY_CACHE.exists():
        return set()
    try:
        d = json.loads(HOLIDAY_CACHE.read_text())
        fetched = _dt.date.fromisoformat(d.get("fetched", "1970-01-01"))
        if (_dt.date.today() - fetched).days > REFRESH_AFTER_DAYS:
            return set()  # stale → trigger refresh
        return set(d.get("dates", []))
    except Exception:
        return set()


def _fetch_live() -> Set[str]:
    try:
        from nsepython import nse_holidays

        h = nse_holidays()
        cm = h.get("CM") or h.get("CBM") or []
        return {_parse(x["tradingDate"]) for x in cm if x.get("tradingDate")}
    except Exception:
        return set()


def get_holidays(refresh: bool = False) -> Set[str]:
    """Return the set of ISO holiday strings (live ∪ cache ∪ fallback)."""
    cached = set() if refresh else _load_cache()
    if cached:
        return cached | _FALLBACK
    live = _fetch_live()
    dates = live | _FALLBACK
    if live:  # persist only a successful live pull
        try:
            HOLIDAY_CACHE.write_text(
                json.dumps({"fetched": _dt.date.today().isoformat(), "dates": sorted(live)})
            )
        except Exception:
            pass
    return dates


def is_trading_day(d: _dt.date, holidays: Set[str] | None = None) -> bool:
    """True if d is a weekday and not an exchange holiday."""
    if d.weekday() >= 5:  # Sat/Sun
        return False
    h = holidays if holidays is not None else get_holidays()
    return d.strftime("%Y-%m-%d") not in h


def trading_days(dates: Iterable[_dt.date]) -> List[_dt.date]:
    """Filter an iterable of dates down to trading days (one holiday lookup)."""
    h = get_holidays()
    return [d for d in dates if is_trading_day(d, h)]


if __name__ == "__main__":
    h = get_holidays()
    print(f"Loaded {len(h)} NSE/BSE equity holidays (live ∪ fallback)")
    today = _dt.date.today()
    for i in range(7):
        d = today - _dt.timedelta(days=i)
        print(f"  {d}  {d:%a}  trading={is_trading_day(d, h)}")
