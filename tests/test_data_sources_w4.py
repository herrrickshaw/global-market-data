"""data_sources (multi-source OHLC fallback), universe_sources (ticker universes), groww_source."""

import datetime as dt
import sys

import numpy as np
import pandas as pd
import pytest
import requests

import data_sources as ds
import groww_source as gs
import universe_sources as us
from tests.w4_helpers import fake_module


# ---------------- data_sources ----------------
@pytest.mark.parametrize(
    "t,expected",
    [
        ("7203.T", "7203.jp"),
        ("BP.L", "bp.uk"),
        ("SAP.DE", "sap.de"),
        ("ADS.F", "ads.de"),
        ("MC.PA", "mc.fr"),
        ("ASML.AS", "asml.nl"),
        ("UCB.BR", "ucb.be"),
        ("ENI.MI", "eni.it"),
        ("SAN.MC", "san.es"),
        ("0700.HK", "0700.hk"),
        ("NESN.SW", "nesn.ch"),
        ("VOLV-B.ST", "volv-b.se"),
        ("AAPL", "aapl.us"),
        ("RELIANCE.NS", None),
        ("D05.SI", None),
        ("BRK.B", None),  # dotted non-mapped suffix is unsupported
    ],
)
def test_stooq_symbol(t, expected):
    assert ds._stooq_symbol(t) == expected


@pytest.mark.parametrize(
    "t,expected",
    [
        ("7203.T", "7203.TSE"),
        ("SAP.DE", "SAP.XETRA"),
        ("RELIANCE.NS", "RELIANCE.NSE"),
        ("005930.KS", "005930.KO"),
        ("AAPL", "AAPL.US"),
        ("X.ZZ", None),
    ],
)
def test_eodhd_symbol(t, expected):
    assert ds._eodhd_symbol(t) == expected


def test_period_to_from_padding(monkeypatch):
    class D(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 30)

    monkeypatch.setattr(dt, "date", D)
    assert ds._period_to_from("10d") == "2026-06-15"  # 10 + 5 pad
    assert ds._period_to_from("1mo") == (D(2026, 6, 30) - dt.timedelta(days=36)).isoformat()
    assert ds._period_to_from("2y") == (D(2026, 6, 30) - dt.timedelta(days=2 * 366 + 5)).isoformat()
    assert ds._period_to_from("max") is None and ds._period_to_from(None) is None
    assert ds._period_to_from("1w") is None


def _csv(n=70, close0=10.0, cols="Date,Open,High,Low,Close,Volume"):
    lines = [cols]
    for i in range(n):
        d = (dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat()
        c = close0 + i
        lines.append(f"{d},{c},{c},{c},{c},1000")
    return "\n".join(lines)


class R:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


class Sess:
    def __init__(self, handler):
        self.handler = handler
        self.headers = {}
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.handler(url, kw)


def _install_session(monkeypatch, handler):
    s = Sess(handler)
    monkeypatch.setattr(ds.requests, "Session", lambda: s)
    return s


def test_stooq_fetch_hits_misses_and_min_bars(monkeypatch, capsys):
    def handler(url, kw):
        if "aapl.us" in url:
            return R(_csv(70))
        if "msft.us" in url:
            return R("<html>limit</html>")
        if "nvda.us" in url:
            return R(_csv(70), status=503)
        if "tsla.us" in url:
            return R(_csv(59))  # under min_bars
        if "ibm.us" in url:
            return R("Date,Open\n")  # no Close
        if "amd.us" in url:
            raise RuntimeError("net")
        return R(_csv(60))  # exactly min_bars: kept

    s = _install_session(monkeypatch, handler)
    out = ds.stooq_fetch(["AAPL", "MSFT", "NVDA", "TSLA", "IBM", "AMD", "RELIANCE.NS", "EXA"])
    assert set(out) == {"AAPL", "EXA"}
    assert list(out["AAPL"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(s.calls) == 7  # .NS unsupported -> no request
    assert (
        s.calls[0][0] == "https://stooq.com/q/d/l/?s=aapl.us&i=d" and s.calls[0][1]["timeout"] == 20
    )
    assert s.headers["User-Agent"].startswith("Mozilla")
    assert "stooq: 2/8 fetched" in capsys.readouterr().out
    ds.stooq_fetch(["AAPL"], verbose=False)
    assert capsys.readouterr().out == ""


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="data_sources.stooq_fetch renames columns with str.capitalize AFTER testing "
    "`'Close' not in df.columns`, so the rename can never rescue a lower-case header; the ticker is dropped",
)
def test_stooq_fetch_lowercase_headers_capitalised(monkeypatch):
    _install_session(monkeypatch, lambda u, k: R(_csv(70, cols="Date,open,high,low,close,volume")))
    assert len(ds.stooq_fetch(["AAPL"], verbose=False)["AAPL"]) == 70


def test_stooq_fetch_without_cleaner(monkeypatch):
    monkeypatch.setattr(ds, "clean_ohlcv", None)
    _install_session(monkeypatch, lambda u, k: R(_csv(5)))
    assert ds.stooq_fetch(["AAPL"], min_bars=5, verbose=False)["AAPL"].shape[0] == 5
    assert ds.stooq_fetch(["AAPL"], min_bars=6, verbose=False) == {}


def test_eodhd_dormant_without_key(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    boom = lambda *a, **k: 1 / 0  # noqa: E731
    monkeypatch.setattr(ds.requests, "Session", boom)
    assert ds.eodhd_fetch(["AAPL"]) == {}


def test_eodhd_fetch_params_and_results(monkeypatch, capsys):
    monkeypatch.setenv("EODHD_API_KEY", "k")
    s = _install_session(monkeypatch, lambda u, k: R(_csv(70)) if "AAPL.US" in u else R("", 404))
    out = ds.eodhd_fetch(["AAPL", "MSFT", "X.ZZ"], period="3mo", min_bars=60)
    assert set(out) == {"AAPL"} and len(s.calls) == 2  # unsupported symbol never requested
    url, kw = s.calls[0]
    assert url == "https://eodhd.com/api/eod/AAPL.US" and kw["timeout"] == 20
    assert (
        kw["params"]["api_token"] == "k"
        and kw["params"]["fmt"] == "csv"
        and kw["params"]["period"] == "d"
    )
    assert kw["params"]["from"] == ds._period_to_from("3mo")
    assert "eodhd: 1/3 fetched" in capsys.readouterr().out
    ds.eodhd_fetch(["AAPL"], period="max", verbose=False)
    assert "from" not in s.calls[-1][1]["params"]  # unparseable period -> full history


def test_eodhd_fetch_error_handling(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", "k")

    def handler(url, kw):
        if "BAD" in url:
            raise RuntimeError("x")
        if "EMPTY" in url:
            return R("Date,Open,High,Low,Close,Volume\n")
        if "NOCLOSE" in url:
            return R("Date,Open\n2024-01-01,1")
        return R(_csv(70))

    _install_session(monkeypatch, handler)
    assert set(ds.eodhd_fetch(["BAD", "EMPTY", "NOCLOSE", "OK"], verbose=False)) == {"OK"}


def test_yahoo_fetch_delegates_with_batch_size(monkeypatch):
    seen = {}
    monkeypatch.setattr(ds, "_yahoo_bulk", lambda t, **kw: seen.update(t=t, **kw) or {"A": 1})
    assert ds.yahoo_fetch(["A"], period="6mo", min_bars=7, verbose=False) == {"A": 1}
    assert seen == {"t": ["A"], "period": "6mo", "batch_size": 80, "min_bars": 7, "verbose": False}
    monkeypatch.setattr(ds, "_yahoo_bulk", None)
    assert ds.yahoo_fetch(["A"]) == {}


def test_groww_fetch_wrapper_swallows_errors(monkeypatch):
    fake_module(monkeypatch, "groww_source", fetch=lambda t, period, min_bars, verbose: {"X": 1})
    assert ds.groww_fetch(["X"]) == {"X": 1}
    fake_module(monkeypatch, "groww_source", fetch=lambda *a, **k: 1 / 0)
    assert ds.groww_fetch(["X"]) == {}


def test_fetch_fallback_chain_and_stats(monkeypatch, capsys):
    calls = []

    def make(name, have):
        def fn(tickers, **kw):
            calls.append((name, list(tickers), kw))
            return {t: pd.DataFrame({"Close": [1.0]}) for t in tickers if t in have}

        return fn

    monkeypatch.setattr(
        ds,
        "SOURCES",
        {"a": make("a", {"X"}), "yahoo": make("yahoo", {"Y"}), "stooq": make("stooq", {"Z"})},
    )
    out = ds.fetch(
        ["X", "Y", "Z", "W", "X"], order=("a", "bogus", "yahoo", "stooq"), period="2y", min_bars=9
    )
    assert set(out) == {"X", "Y", "Z"}
    assert [c[1] for c in calls] == [
        ["X", "Y", "Z", "W"],
        ["Y", "Z", "W"],
        ["Z", "W"],
    ]  # deduped, shrinking
    assert calls[0][2] == {"min_bars": 9, "verbose": True}  # 'a' is not period-aware
    assert calls[1][2] == {"min_bars": 9, "verbose": True, "period": "2y"}
    assert calls[2][2] == {"min_bars": 9, "verbose": True}  # stooq: no period kwarg
    assert ds.get_last_stats() == {
        "requested": 4,
        "filled": 3,
        "by_source": {"a": 1, "yahoo": 1, "stooq": 1},
    }
    assert "multi-source total: 3/5 (1 unresolved)" in capsys.readouterr().out


def test_fetch_stops_when_nothing_pending_and_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ds,
        "SOURCES",
        {
            "yahoo": lambda t, **kw: calls.append(kw) or {x: pd.DataFrame() for x in t},
            "stooq": lambda t, **kw: 1 / 0,
        },
    )
    out = ds.fetch(["A"], order=("yahoo", "stooq"), verbose=False)
    assert list(out) == ["A"] and calls == [{"min_bars": 60, "verbose": False, "period": "1y"}]
    assert ds.get_last_stats()["by_source"] == {"yahoo": 1}
    assert ds.fetch([], verbose=False) == {} and ds.get_last_stats()["requested"] == 0
    stats = ds.get_last_stats()
    stats["x"] = 1
    assert "x" not in ds.get_last_stats()  # a copy is returned


def test_fetch_groww_gets_period(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        ds,
        "SOURCES",
        {
            "groww": lambda t, **kw: seen.update(kw) or {},
            "eodhd": lambda t, **kw: seen.setdefault("eod", kw) and {},
        },
    )
    ds.fetch(["A"], order=("groww", "eodhd"), period="5y", verbose=False)
    assert seen["period"] == "5y" and seen["eod"]["period"] == "5y"


# ---------------- universe_sources ----------------
class Resp:
    def __init__(self, data=None, content=b"", text=""):
        self._d, self.content, self.text = data, content, text

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def test_us_sec_uppercases_and_dedups(monkeypatch):
    data = {
        "0": {"ticker": "brk.b"},
        "1": {"ticker": "AAPL"},
        "2": {"ticker": ""},
        "3": {},
        "4": {"ticker": "aapl"},
    }
    seen = {}
    monkeypatch.setattr(
        us.requests,
        "get",
        lambda url, headers, timeout: seen.update(h=headers, t=timeout) or Resp(data),
    )
    assert us.us_sec() == ["AAPL", "BRK-B"]
    assert seen["t"] == 30 and "User-Agent" in seen["h"]


def test_us_sec_http_error_propagates(monkeypatch):
    class Bad(Resp):
        def raise_for_status(self):
            raise requests.HTTPError("403")

    monkeypatch.setattr(us.requests, "get", lambda *a, **k: Bad({}))
    with pytest.raises(requests.HTTPError):
        us.us_sec()


def test_sg_sgx_keeps_equity_types(monkeypatch):
    prices = [
        {"nc": "D05", "type": "stocks"},
        {"nc": "A17U", "type": "reits"},
        {"nc": "BTX", "type": "businesstrusts"},
        {"nc": "WRNT", "type": "warrants"},
        {"nc": None, "type": "stocks"},
        {"type": "stocks"},
        {"nc": "D05", "type": "stocks"},
    ]
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: Resp({"data": {"prices": prices}}))
    assert us.sg_sgx() == ["A17U.SI", "BTX.SI", "D05.SI"]


def test_cn_eastmoney_paginates_and_maps_boards(monkeypatch):
    pages = {
        1: {"total": 3, "diff": [{"f12": "600519", "f13": 1}, {"f12": "000001", "f13": 0}]},
        2: {"total": 3, "diff": {"0": {"f12": "300750", "f13": 0}, "1": {"f12": "", "f13": 1}}},
    }
    seen = []

    def get(url, params, headers, timeout):
        seen.append((url, params["pn"]))
        return Resp({"data": pages[params["pn"]]})

    monkeypatch.setattr(us.requests, "get", get)
    assert us.cn_eastmoney() == ["000001.SZ", "300750.SZ", "600519.SS"]
    assert [p for _, p in seen] == [1, 2]  # stops once total reached


def test_cn_eastmoney_host_fallback_and_stop_conditions(monkeypatch):
    hosts = []

    def get(url, params, headers, timeout):
        hosts.append(url.split("/api")[0])
        if len(hosts) == 1:
            raise OSError("blocked")
        return Resp({"data": {"total": 1, "diff": [{"f12": "600519", "f13": 1}]}})

    monkeypatch.setattr(us.requests, "get", get)
    assert (
        us.cn_eastmoney() == ["600519.SS"]
        and hosts[0] == "https://push2.eastmoney.com"
        and hosts[1] == "https://82.push2.eastmoney.com"
    )
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: 1 / 0)
    assert us.cn_eastmoney() == []  # every host failing -> no data -> stop
    monkeypatch.setattr(
        us.requests, "get", lambda *a, **k: Resp({"data": {"total": 5, "diff": []}})
    )
    assert us.cn_eastmoney() == []  # empty diff stops pagination
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: Resp({"data": None}))
    assert us.cn_eastmoney() == []


def test_jp_and_kr(monkeypatch):
    fake_module(
        monkeypatch,
        "full_japan_market_scan",
        fetch_tse_universe_jpx=lambda: [{"code": "7203"}, {"yf_ticker": "6758.T"}, {"x": 1}],
        fetch_tse_universe_kabupy=lambda: [{"code": "9984"}],
    )
    assert us.jp_jpx() == ["6758.T", "7203.T"]
    fake_module(
        monkeypatch,
        "full_japan_market_scan",
        fetch_tse_universe_jpx=lambda: 1 / 0,
        fetch_tse_universe_kabupy=lambda: [{"code": "9984"}],
    )
    assert us.jp_jpx() == ["9984.T"]  # primary failing -> fallback
    fake_module(
        monkeypatch,
        "full_japan_market_scan",
        fetch_tse_universe_jpx=lambda: [],
        fetch_tse_universe_kabupy=lambda: [{"code": "1111"}],
    )
    assert us.jp_jpx() == ["1111.T"]  # empty primary -> fallback
    fake_module(
        monkeypatch,
        "full_korea_market_scan",
        build_krx_universe=lambda: [
            {"code": "005930"},
            {"code": "035720", "yf_suffix": ".KQ"},
            {"x": 1},
        ],
    )
    assert us.kr_krx() == ["005930.KS", "035720.KQ"]


def test_in_bhavcopy_store_then_history(monkeypatch):
    fake_module(monkeypatch, "bhavcopy_store", symbols=lambda: ["B", "A"])
    assert us.in_bhavcopy() == ["A", "B"]
    fake_module(monkeypatch, "bhavcopy_store", symbols=lambda: [])
    fake_module(monkeypatch, "bhavcopy_history", fetch_history=lambda verbose: {"Z": 1, "Y": 2})
    assert us.in_bhavcopy() == ["Y", "Z"]
    fake_module(monkeypatch, "bhavcopy_store", symbols=lambda: 1 / 0)
    assert us.in_bhavcopy() == ["Y", "Z"]


def test_euronext_equities_maps_venue_suffix(monkeypatch):
    csv = (
        "Name;ISIN;Symbol;Market\n"
        "A;FR1;MC;Euronext Paris\n"
        "B;NL1;ASML;Euronext Amsterdam, Other\n"
        "C;NO1;EQNR;Oslo Børs\n"
        "D;IT1;ENI;Borsa Italiana\n"
        "E;XX1;ZZZ;Unknown Venue\n"
        ";;NOISIN;Euronext Paris\n"
        "F;FR2;;Euronext Paris\n"
        "G;FR1;MC;Euronext Paris\n"
    )
    seen = {}
    monkeypatch.setattr(
        us.requests,
        "get",
        lambda url, headers, timeout: seen.update(u=url, h=headers, t=timeout)
        or Resp(content=csv.encode("utf-8-sig")),
    )
    assert us.euronext_equities() == ["ASML.AS", "ENI.MI", "EQNR.OL", "MC.PA"]
    assert "mics=XPAR,XAMS" in seen["u"] and seen["t"] == 40 and "Referer" in seen["h"]
    us.euronext_equities(mics="XPAR")
    assert seen["u"].endswith("mics=XPAR")


def test_investpy_universe(monkeypatch):
    monkeypatch.setitem(sys.modules, "investpy", None)
    assert us.investpy_universe() is None
    calls = []

    def get_stocks(country):
        calls.append(country)
        if country == "germany":
            raise RuntimeError
        return pd.DataFrame({"symbol": [country[:2]]})

    fake_module(monkeypatch, "investpy", get_stocks=get_stocks)
    df = us.investpy_universe(countries=("france", "germany", "italy"))
    assert calls == ["france", "germany", "italy"] and list(df["symbol"]) == ["fr", "it"]
    fake_module(monkeypatch, "investpy", get_stocks=lambda country: 1 / 0)
    assert us.investpy_universe(countries=("france",)) is None


def test_eu_universe_union(monkeypatch):
    fake_module(
        monkeypatch, "full_european_market_scan", EURO_STOXX_50_META={"ZZZ.PA": 1, "HSBA.L": 2}
    )
    cur = us.eu_curated()
    assert "ZZZ.PA" in cur and cur.count("HSBA.L") == 1 and cur == sorted(cur)
    monkeypatch.setattr(us, "euronext_equities", lambda: ["NEW.PA"])
    assert "NEW.PA" in us.eu() and "ZZZ.PA" in us.eu()
    monkeypatch.setattr(us, "euronext_equities", lambda: 1 / 0)
    assert "NEW.PA" not in us.eu() and "SAP.DE" in us.eu()


def _damo():
    return pd.DataFrame(
        {
            "Country": ["Hong Kong"] * 4 + ["Sweden", "Sweden", "Japan"],
            "Exchange": ["SEHK", "SEHK", "NYSE", "SEHK", "OMX", "OMX", "TSE"],
            "Ticker": ["5", "700", "X", "", "VOLV-B", "VOLV-B", "7203"],
        }
    )


def test_from_damodaran(monkeypatch):
    fake_module(monkeypatch, "reference_data", damodaran_companies=_damo)
    assert us.from_damodaran("Hong Kong", {"SEHK"}) == [
        "0005.HK",
        "0700.HK",
    ]  # zero-padded, filtered
    assert us.from_damodaran("Hong Kong") == ["0005.HK", "0700.HK"]  # NYSE has no suffix mapping
    assert us.from_damodaran("Sweden") == ["VOLV-B.ST"]  # country suffix, deduped
    assert us.from_damodaran("Japan") == []  # unmapped exchange
    assert us.from_damodaran("Atlantis") == []
    fake_module(monkeypatch, "reference_data", damodaran_companies=lambda: None)
    assert us.from_damodaran("Sweden") == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="universe_sources.from_damodaran guards with `if not tkr`, but a missing ticker read "
    "from the Damodaran frame is float NaN (truthy), so it is emitted as a bogus '0nan.HK'",
)
def test_from_damodaran_skips_missing_ticker(monkeypatch):
    df = pd.DataFrame(
        {
            "Country": ["Hong Kong", "Hong Kong"],
            "Exchange": ["SEHK", "SEHK"],
            "Ticker": pd.Series(["5", np.nan], dtype=object),
        }
    )
    fake_module(monkeypatch, "reference_data", damodaran_companies=lambda: df)
    assert us.from_damodaran("Hong Kong", {"SEHK"}) == ["0005.HK"]


def test_get_universe_dispatch(monkeypatch):
    monkeypatch.setitem(us.PROVIDERS, "US", lambda: ["AAPL"])
    assert us.get_universe("us") == ["AAPL"]
    with pytest.raises(KeyError):
        us.get_universe("zz")
    assert len(us.PROVIDERS) == 20


# ---------------- groww_source ----------------
@pytest.fixture
def groww(monkeypatch, tmp_path):
    monkeypatch.setattr(gs, "_ENV", tmp_path / ".env")
    monkeypatch.setattr(gs, "_TOKEN_CACHE", {})
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    return gs


class FakeGroww:
    EXCHANGE_NSE = "NSE"
    SEGMENT_CASH = "CASH"
    CANDLE_INTERVAL_DAY = "1day"
    minted = []
    candles = {}
    fail = set()

    def __init__(self, token):
        self.token = token
        self.calls = []

    @classmethod
    def get_access_token(cls, api_key, secret):
        cls.minted.append((api_key, secret))
        return f"tok-{len(cls.minted)}"

    def get_historical_candles(self, **kw):
        self.calls.append(kw)
        sym = kw["groww_symbol"]
        if sym in FakeGroww.fail:
            raise RuntimeError("403")
        return FakeGroww.candles.get(sym)

    def get_all_instruments(self):
        return pd.DataFrame({"a": [1, 2]})


@pytest.fixture
def api(monkeypatch):
    FakeGroww.minted, FakeGroww.candles, FakeGroww.fail = [], {}, set()
    fake_module(monkeypatch, "growwapi", GrowwAPI=FakeGroww)
    return FakeGroww


def test_load_env_parsing(groww, tmp_path):
    assert groww._load_env() == {}
    (tmp_path / ".env").write_text("# c=1\nA=1\nB=x=y\nbad line\n\nC=\n")
    assert groww._load_env() == {"A": "1", "B": "x=y", "C": ""}


def test_access_token_missing_creds_or_sdk(groww, monkeypatch, tmp_path):
    assert groww._access_token() is None
    (tmp_path / ".env").write_text("GROWW_API_KEY=k\n")
    assert groww._access_token() is None  # secret missing
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    monkeypatch.setitem(sys.modules, "growwapi", None)
    assert groww._access_token() is None  # SDK missing


def test_access_token_mint_cached_per_day(groww, api, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("GROWW_API_KEY=filekey\nGROWW_API_SECRET=filesecret\n")
    assert groww._access_token() == "tok-1" and api.minted == [("filekey", "filesecret")]
    assert groww._access_token() == "tok-1" and len(api.minted) == 1  # cached
    groww._TOKEN_CACHE["day"] = dt.date.today() - dt.timedelta(days=1)
    assert groww._access_token() == "tok-2"  # new day -> re-mint


def test_access_token_env_vars_and_file_precedence(groww, api, monkeypatch, tmp_path):
    monkeypatch.setenv("GROWW_API_KEY", "envk")
    monkeypatch.setenv("GROWW_API_SECRET", "envs")
    groww._access_token()
    assert api.minted == [("envk", "envs")]
    groww._TOKEN_CACHE.clear()
    (tmp_path / ".env").write_text("GROWW_API_KEY=filek\n")
    groww._access_token()
    assert api.minted[-1] == ("filek", "envs")  # file wins per-key, env fills the gap


def test_access_token_mint_failure(groww, api, monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    monkeypatch.setattr(api, "get_access_token", classmethod(lambda cls, api_key, secret: 1 / 0))
    assert groww._access_token() is None and groww._TOKEN_CACHE == {}


def test_client_none_without_token_and_instruments(groww, api, monkeypatch):
    assert groww._client() is None and groww.instruments().empty
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    assert isinstance(groww._client(), FakeGroww)
    assert len(groww.instruments()) == 2
    monkeypatch.setattr(api, "get_all_instruments", lambda self: 1 / 0)
    assert groww.instruments().empty


def test_client_constructor_failure(groww, api, monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    monkeypatch.setattr(groww, "_access_token", lambda: "tok")
    monkeypatch.setattr(api, "__init__", lambda self, t: 1 / 0)
    assert groww._client() is None


@pytest.mark.parametrize(
    "t,expected", [("RELIANCE.NS", "NSE-RELIANCE"), ("tcs.BO", "NSE-TCS"), ("INFY", "NSE-INFY")]
)
def test_groww_symbol(groww, t, expected):
    assert groww._groww_symbol(t) == expected


def _candles(n, t0=1_700_000_000):
    return [[t0 + 86400 * i, 1.0, 2.0, 0.5, 1.5, 100] for i in range(n)]


def test_fetch_candles_period_window_and_results(groww, api, monkeypatch, capsys):
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    api.candles = {
        "NSE-AAA": {"candles": _candles(60)},
        "NSE-BBB": _candles(59),
        "NSE-CCC": None,
        "NSE-DDD": {"candles": []},
    }
    api.fail = {"NSE-EEE"}
    out = groww.fetch(["AAA.NS", "BBB", "CCC", "DDD", "EEE"], period="3mo", min_bars=60)
    assert set(out) == {"AAA.NS"}
    df = out["AAA.NS"]
    assert (
        list(df.columns) == ["Open", "High", "Low", "Close", "Volume"] and df.index.name == "Date"
    )
    assert df.index[0] == pd.Timestamp(1_700_000_000, unit="s")
    assert "groww: 1/5 fetched" in capsys.readouterr().out
    # request window: (3*31 + 5) days back, market hours stamps
    c = groww._client  # noqa: F841
    today = dt.date.today()
    inst = api  # FakeGroww class; instances are created inside fetch
    groww.fetch(["AAA"], period="2d", verbose=False)


def test_fetch_request_arguments(groww, api, monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    created = []
    orig = FakeGroww.__init__

    def init(self, token):
        orig(self, token)
        created.append(self)

    monkeypatch.setattr(FakeGroww, "__init__", init)
    groww.fetch(["AAA.NS"], period="2d", verbose=False)
    kw = created[0].calls[0]
    today = dt.date.today()
    assert kw["start_time"] == (today - dt.timedelta(days=2 + 5)).strftime("%Y-%m-%d 09:15:00")
    assert kw["end_time"] == today.strftime("%Y-%m-%d 15:30:00")
    assert kw["exchange"] == "NSE" and kw["segment"] == "CASH" and kw["groww_symbol"] == "NSE-AAA"
    assert kw["candle_interval"] == "1day"
    created.clear()
    groww.fetch(["AAA"], period="weird", verbose=False)
    assert (
        created[0]
        .calls[0]["start_time"]
        .startswith((today - dt.timedelta(days=366)).strftime("%Y-%m-%d"))
    )


def test_fetch_without_client_or_sdk(groww, api, monkeypatch):
    assert groww.fetch(["AAA"]) == {}
    monkeypatch.setattr(groww, "_client", lambda: object())
    monkeypatch.setitem(sys.modules, "growwapi", None)
    assert groww.fetch(["AAA"]) == {}


def test_groww_main(groww, api, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["g"])
    assert groww.main() == 0
    out = capsys.readouterr().out
    assert "auth: no keys / SDK" in out and "ohlc: 0/1 fetched" in out
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    monkeypatch.setattr("sys.argv", ["g", "--instruments", "--ohlc", "AAA", "BBB"])
    groww.main()
    out = capsys.readouterr().out
    assert (
        "auth: OK (token minted)" in out and "instruments: 2 rows" in out and "0/2 fetched" in out
    )
