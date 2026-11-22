"""sec_fundamentals: network wrappers (ticker->CIK, companyfacts) and ratio helpers."""

import pytest

import sec_fundamentals as sf


class Resp:
    def __init__(self, data=None, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _clear_caches():
    sf._ticker_cik.cache_clear()
    sf.companyfacts.cache_clear()
    yield
    sf._ticker_cik.cache_clear()
    sf.companyfacts.cache_clear()


def test_ticker_cik_zero_pads_uppercases_and_is_cached(monkeypatch):
    calls = []
    data = {"0": {"ticker": "aapl", "cik_str": 320193}, "1": {"ticker": "MSFT", "cik_str": "789019"}}
    monkeypatch.setattr(sf.requests, "get", lambda url, headers, timeout: calls.append((url, timeout)) or Resp(data))
    m = sf._ticker_cik()
    assert m == {"AAPL": "0000320193", "MSFT": "0000789019"}
    sf._ticker_cik()
    assert len(calls) == 1 and calls[0][1] == 30 and "company_tickers.json" in calls[0][0]


def test_ticker_cik_http_error_propagates(monkeypatch):
    monkeypatch.setattr(sf.requests, "get", lambda *a, **k: Resp({}, 403))
    with pytest.raises(RuntimeError, match="403"):
        sf._ticker_cik()


def _cik(monkeypatch, mapping):
    monkeypatch.setattr(sf, "_ticker_cik", lambda: mapping)


def test_companyfacts_unknown_ticker_and_http_failures(monkeypatch):
    _cik(monkeypatch, {"AAPL": "0000320193"})
    boom = lambda *a, **k: 1 / 0  # noqa: E731
    monkeypatch.setattr(sf.requests, "get", boom)
    assert sf.companyfacts("ZZZZ") is None  # unknown ticker never hits the network
    assert sf.companyfacts("AAPL") is None  # network error swallowed
    sf.companyfacts.cache_clear()
    monkeypatch.setattr(sf.requests, "get", lambda *a, **k: Resp({"facts": {"x": 1}}, 404))
    assert sf.companyfacts("AAPL") is None  # non-200 status


def test_companyfacts_success_url_and_case(monkeypatch):
    _cik(monkeypatch, {"AAPL": "0000320193"})
    seen = []
    monkeypatch.setattr(sf.requests, "get", lambda url, headers, timeout: seen.append(url) or Resp({"facts": {"us-gaap": {}}}))
    assert sf.companyfacts("aapl") == {"us-gaap": {}}  # case-insensitive lookup
    assert seen == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]
    assert sf.companyfacts("aapl") == {"us-gaap": {}} and len(seen) == 1  # lru-cached
    monkeypatch.setattr(sf.requests, "get", lambda *a, **k: Resp({}))
    sf.companyfacts.cache_clear()
    assert sf.companyfacts("AAPL") == {}  # payload without "facts"


def test_fundamentals_wrapper_routes_as_of(monkeypatch):
    monkeypatch.setattr(sf, "companyfacts", lambda t: None)
    assert sf.fundamentals("AAPL") == {}
    monkeypatch.setattr(sf, "companyfacts", lambda t: {})
    assert sf.fundamentals("AAPL") == {}
    seen = {}
    monkeypatch.setattr(sf, "companyfacts", lambda t: {"us-gaap": {}})
    monkeypatch.setattr(sf, "fundamentals_from_facts", lambda f, as_of: seen.update(f=f, as_of=as_of) or {"ok": 1})
    assert sf.fundamentals("AAPL", as_of="2024-01-01") == {"ok": 1}
    assert seen == {"f": {"us-gaap": {}}, "as_of": "2024-01-01"}


def test_is_annual_period_boundaries():
    d = lambda a, b: {"start": a, "end": b}  # noqa: E731
    assert sf._is_annual_period({"end": "2024-12-31"}) is True  # instant fact
    assert sf._is_annual_period(d("2024-01-01", "2024-10-26")) is False  # 299 days
    assert sf._is_annual_period(d("2024-01-01", "2024-10-27")) is True  # 300 days
    assert sf._is_annual_period(d("2024-01-01", "2025-02-04")) is True  # 400 days
    assert sf._is_annual_period(d("2024-01-01", "2025-02-05")) is False  # 401 days
    assert sf._is_annual_period(d("2024-01-01", "garbage")) is False
    assert sf._is_annual_period({"start": "bad", "end": "2024-12-31"}) is False


def test_roe_history_skips_zero_equity_and_truncates():
    assert sf._roe_history([10, 20, 30], [100, 0, 150]) == [10.0, 20.0]
    assert sf._roe_history([10, 20], [100]) == [10.0]  # zipped to the shorter list
    assert sf._roe_history([], [100]) is None and sf._roe_history([5], [0]) is None
    assert sf._roe_history([1], [3]) == [33.3]  # rounded to 1 dp


def test_margin_bounds_and_pct():
    assert sf._margin(100, 100) == 100.0 and sf._margin(0, 100) == 0.0
    assert sf._margin(101, 100) is None and sf._margin(-1, 100) is None
    assert sf._margin(None, 100) is None and sf._margin(5, 0) is None and sf._margin(5, None) is None
    assert sf._pct(110, 100) == pytest.approx(10.0) and sf._pct(-90, -100) == pytest.approx(10.0)
    assert sf._pct(5, 0) is None and sf._pct(None, 5) is None and sf._pct(5, None) is None
