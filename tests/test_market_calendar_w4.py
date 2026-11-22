"""market_calendar.py + market_holidays.py (holiday cache, live fetch, trading-day math)."""

import datetime as dt
import json

import pytest

from tests.w4_helpers import fake_module, fresh_import


@pytest.fixture
def mc(monkeypatch, tmp_path):
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path / "bhav"))
    m = fresh_import(monkeypatch, "market_calendar")
    return m


def test_import_creates_cache_dir_in_env_location(mc, tmp_path):
    assert (tmp_path / "bhav").is_dir()
    assert mc.HOLIDAY_CACHE == tmp_path / "bhav" / "nse_holidays.json"


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("15-Jan-2026", "2026-01-15"),
        ("2026-01-15", "2026-01-15"),
        ("15-January-2026", "2026-01-15"),
        ("  2026-01-15  ", "2026-01-15"),
        ("garbage ", "garbage"),
    ],
)
def test_parse(mc, raw, iso):
    assert mc._parse(raw) == iso


def _write_cache(mc, fetched, dates):
    mc.HOLIDAY_CACHE.write_text(json.dumps({"fetched": fetched.isoformat(), "dates": dates}))


def test_load_cache_missing_corrupt_and_fresh(mc):
    assert mc._load_cache() == set()
    mc.HOLIDAY_CACHE.write_text("not json")
    assert mc._load_cache() == set()
    _write_cache(mc, dt.date.today(), ["2030-01-01"])
    assert mc._load_cache() == {"2030-01-01"}


def test_load_cache_staleness_boundary(mc):
    today = dt.date.today()
    _write_cache(mc, today - dt.timedelta(days=mc.REFRESH_AFTER_DAYS), ["2030-01-01"])
    assert mc._load_cache() == {"2030-01-01"}  # exactly 14 days: still fresh
    _write_cache(mc, today - dt.timedelta(days=mc.REFRESH_AFTER_DAYS + 1), ["2030-01-01"])
    assert mc._load_cache() == set()  # 15 days: stale


def test_load_cache_default_fetched_is_stale(mc):
    mc.HOLIDAY_CACHE.write_text(json.dumps({"dates": ["2030-01-01"]}))
    assert mc._load_cache() == set()


def test_fetch_live_uses_cm_then_cbm_and_skips_blank(mc, monkeypatch):
    fake_module(
        monkeypatch,
        "nsepython",
        nse_holidays=lambda: {"CM": [{"tradingDate": "15-Jan-2031"}, {"x": 1}], "CBM": [1]},
    )
    assert mc._fetch_live() == {"2031-01-15"}
    fake_module(
        monkeypatch, "nsepython", nse_holidays=lambda: {"CBM": [{"tradingDate": "2031-02-02"}]}
    )
    assert mc._fetch_live() == {"2031-02-02"}
    fake_module(monkeypatch, "nsepython", nse_holidays=lambda: {})
    assert mc._fetch_live() == set()


def test_fetch_live_failure_is_empty(mc, monkeypatch):
    def boom():
        raise RuntimeError("network")

    fake_module(monkeypatch, "nsepython", nse_holidays=boom)
    assert mc._fetch_live() == set()


def test_get_holidays_live_persisted_and_union_fallback(mc, monkeypatch):
    monkeypatch.setattr(mc, "_fetch_live", lambda: {"2031-05-05"})
    h = mc.get_holidays()
    assert "2031-05-05" in h and "2026-01-26" in h  # live ∪ fallback
    saved = json.loads(mc.HOLIDAY_CACHE.read_text())
    assert saved["dates"] == ["2031-05-05"]  # only live dates persisted (not fallback)
    assert saved["fetched"] == dt.date.today().isoformat()


def test_get_holidays_uses_fresh_cache_without_fetch(mc, monkeypatch):
    _write_cache(mc, dt.date.today(), ["2032-02-02"])

    def boom():
        raise AssertionError("must not fetch")

    monkeypatch.setattr(mc, "_fetch_live", boom)
    h = mc.get_holidays()
    assert "2032-02-02" in h and "2025-12-25" in h


def test_get_holidays_refresh_bypasses_cache(mc, monkeypatch):
    _write_cache(mc, dt.date.today(), ["2032-02-02"])
    monkeypatch.setattr(mc, "_fetch_live", lambda: {"2033-03-03"})
    h = mc.get_holidays(refresh=True)
    assert "2033-03-03" in h and "2032-02-02" not in h


def test_get_holidays_live_failure_does_not_write_cache(mc, monkeypatch):
    monkeypatch.setattr(mc, "_fetch_live", lambda: set())
    h = mc.get_holidays()
    assert h == mc._FALLBACK
    assert not mc.HOLIDAY_CACHE.exists()


def test_get_holidays_cache_write_failure_swallowed(mc, monkeypatch):
    monkeypatch.setattr(mc, "_fetch_live", lambda: {"2031-05-05"})
    monkeypatch.setattr(mc, "HOLIDAY_CACHE", mc.CACHE / "no_such_dir" / "h.json")
    assert "2031-05-05" in mc.get_holidays()


def test_is_trading_day(mc):
    hol = {"2026-01-26"}
    assert mc.is_trading_day(dt.date(2026, 1, 27), hol)  # Tuesday
    assert not mc.is_trading_day(dt.date(2026, 1, 26), hol)  # holiday (Monday)
    assert not mc.is_trading_day(dt.date(2026, 1, 24), set())  # Saturday
    assert not mc.is_trading_day(dt.date(2026, 1, 25), set())  # Sunday
    assert mc.is_trading_day(dt.date(2026, 1, 23), set())  # Friday
    # empty set is a real (falsy) holidays arg and must not trigger a lookup
    assert mc.is_trading_day(dt.date(2026, 1, 26), set())


def test_is_trading_day_default_holidays(mc, monkeypatch):
    monkeypatch.setattr(mc, "get_holidays", lambda: {"2026-01-27"})
    assert not mc.is_trading_day(dt.date(2026, 1, 27))


def test_trading_days_filters_and_preserves_order(mc, monkeypatch):
    monkeypatch.setattr(mc, "get_holidays", lambda: {"2026-01-28"})
    days = [dt.date(2026, 1, d) for d in (30, 29, 28, 27, 26 + 0, 31)]
    out = mc.trading_days(days)
    assert out == [dt.date(2026, 1, 30), dt.date(2026, 1, 29), dt.date(2026, 1, 27), dt.date(2026, 1, 26)]


# ---------------- market_holidays ----------------
@pytest.fixture
def mh():
    import market_holidays

    return market_holidays


def test_weekend_rules(mh):
    assert mh.weekend("SA") == {4, 5}
    assert mh.weekend("US") == {5, 6}
    assert mh.weekend("ZZ") == {5, 6}


def test_saudi_friday_closed_sunday_open(mh):
    assert not mh.is_trading_day("SA", dt.date(2026, 1, 2))  # Friday
    assert mh.is_trading_day("SA", dt.date(2026, 1, 4))  # Sunday
    assert not mh.is_trading_day("SA", dt.date(2026, 1, 3))  # Saturday


def test_holidays_known_unknown_and_in_delegates(mh, monkeypatch):
    assert "2025-12-25" in mh.holidays("US")
    assert mh.holidays("ZZ") == set()
    fake_module(monkeypatch, "market_calendar", get_holidays=lambda: {"2030-01-01"})
    assert mh.holidays("IN") == {"2030-01-01"}


def test_holidays_in_failure_returns_empty(mh, monkeypatch):
    def boom():
        raise RuntimeError

    fake_module(monkeypatch, "market_calendar", get_holidays=boom)
    assert mh.holidays("IN") == set()


def test_is_trading_day_explicit_hol_overrides(mh):
    d = dt.date(2025, 12, 25)  # US holiday, Thursday
    assert not mh.is_trading_day("US", d)
    assert mh.is_trading_day("US", d, set())


def test_trading_days_filter(mh):
    out = mh.trading_days("US", [dt.date(2025, 12, 24), dt.date(2025, 12, 25), dt.date(2025, 12, 26)])
    assert out == [dt.date(2025, 12, 24), dt.date(2025, 12, 26)]


def test_next_trading_day_skips_weekend_and_holiday(mh):
    # Fri 2025-12-26 -> next is Mon 2025-12-29
    assert mh.next_trading_day("US", dt.date(2025, 12, 26)) == dt.date(2025, 12, 29)
    # Wed 2025-12-24 -> Thu is a holiday -> Fri 26
    assert mh.next_trading_day("US", dt.date(2025, 12, 24)) == dt.date(2025, 12, 26)


def test_last_trading_day_same_day_if_open_else_back(mh):
    assert mh.last_trading_day("US", dt.date(2025, 12, 24)) == dt.date(2025, 12, 24)
    assert mh.last_trading_day("US", dt.date(2025, 12, 25)) == dt.date(2025, 12, 24)
    assert mh.last_trading_day("US", dt.date(2025, 12, 28)) == dt.date(2025, 12, 26)  # Sunday


def test_default_dates_use_today(mh, monkeypatch):
    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2025, 12, 24)

    monkeypatch.setattr(mh._dt, "date", FakeDate)
    assert mh.next_trading_day("US") == dt.date(2025, 12, 26)
    assert mh.last_trading_day("US") == dt.date(2025, 12, 24)
    assert mh.is_market_open_today("US") is True
