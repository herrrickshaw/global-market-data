"""full_us_market_scan: symbol caches, NCDS/FTP/SEC symbol fetch, bulk download, main(), Excel."""

import json
import time
import types

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import load


@pytest.fixture
def us(monkeypatch, tmp_path):
    mod = load(monkeypatch, "full_us_market_scan", tmp_path)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", tmp_path / "us_full_scan")
    (tmp_path / "us_full_scan").mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "_SYMBOL_CACHE", tmp_path / "us_full_scan" / ".symbols_cache.json")
    monkeypatch.setattr(mod, "_ncds_symbol_cache", {})
    monkeypatch.setattr(
        mod, "time", types.SimpleNamespace(**{**vars(time), "sleep": lambda s: None})
    )
    return mod


def _ohlc(closes, highs=None, lows=None):
    idx = pd.date_range("2024-01-01", periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "High": c * 1.01 if highs is None else highs,
            "Low": c * 0.99 if lows is None else lows,
            "Close": c,
        },
        index=idx,
    )


# ---------------- symbol cache ----------------
def test_symbol_cache_roundtrip_and_ttl(us):
    assert us._load_symbol_cache() == (None, None)  # no file
    us._save_symbol_cache(["A"], ["B"])
    assert us._load_symbol_cache() == (["A"], ["B"])
    us._SYMBOL_CACHE.write_text(
        json.dumps({"ts": time.time() - us.SYMBOL_CACHE_TTL - 1, "nasdaq": ["A"], "nyse": []})
    )
    assert us._load_symbol_cache() == (None, None)  # expired
    us._SYMBOL_CACHE.write_text(
        json.dumps({"ts": time.time() - us.SYMBOL_CACHE_TTL + 60, "nasdaq": ["A"]})
    )
    assert us._load_symbol_cache() == (["A"], [])  # still fresh; missing key defaults empty
    us._SYMBOL_CACHE.write_text("{not json")
    assert us._load_symbol_cache() == (None, None)


def test_save_symbol_cache_swallows_errors(us, tmp_path):
    us._SYMBOL_CACHE = tmp_path / "missing_dir" / "c.json"
    us._save_symbol_cache(["A"], [])  # must not raise
    assert not us._SYMBOL_CACHE.exists()


# ---------------- NCDS ----------------
def test_load_ncds_creds_paths(us, tmp_path, monkeypatch):
    p = tmp_path / "auth.json"
    monkeypatch.setattr(us, "_NCDS_CREDS_PATH", p)
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", False)
    p.write_text('{"topic": "t"}')
    assert us._load_ncds_creds() is None  # SDK missing
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    assert us._load_ncds_creds() == {"topic": "t"}
    p.write_text("garbage")
    assert us._load_ncds_creds() is None
    p.unlink()
    assert us._load_ncds_creds() is None  # no file


class _Msg:
    def __init__(self, val, call=True):
        self._v = val
        self._call = call

    def value(self):
        return self._v

    @property
    def val_attr(self):
        return self._v


class _Consumer:
    def __init__(self, batches):
        self.batches = list(batches)
        self.closed = False

    def consume(self, num_messages, timeout):
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


def _dir(sym, mkt):
    return {"schema_name": "SeqDirectoryMessage", "symbol": sym, "marketClass": mkt}


def test_fetch_symbols_from_ncds_no_creds(us, monkeypatch):
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: None)
    assert us.fetch_symbols_from_ncds() == ([], [])


def test_fetch_symbols_from_ncds_classifies_and_dedups(us, monkeypatch):
    attr_msg = types.SimpleNamespace(value=_dir("ATTR", "Q"))  # non-callable .value
    cons = _Consumer(
        [
            [
                _Msg(_dir("aapl", "Q")),
                _Msg(_dir("AAPL", "Q")),  # dup
                _Msg(_dir("msft", "G")),
                _Msg(_dir("goog", "")),  # empty class -> NASDAQ
                _Msg(_dir("ibm", "N")),
                _Msg(_dir("ba", "A")),
                _Msg(_dir("BRK.B", "N")),  # special char -> skipped
                _Msg(_dir("", "Q")),  # blank
                _Msg(_dir("XX/Y", "Q")),
                _Msg({"schema_name": "Other", "symbol": "ZZ", "marketClass": "Q"}),
                _Msg("not a dict"),
                attr_msg,
            ],
            [],
            [_Msg(_dir("spy", "P"))],  # empty poll resets the counter
        ]
    )
    captured = {}

    class Client:
        def __init__(self, sec, kafka):
            captured["sec"], captured["kafka"] = sec, kafka

        def ncds_kafka_consumer(self, topic):
            captured["topic"] = topic
            return cons

    monkeypatch.setattr(us, "_NCDSClient", Client, raising=False)
    monkeypatch.setattr(
        us,
        "_load_ncds_creds",
        lambda: {
            "oauth": {"endpoint": "e", "client_id": "i", "client_secret": "s"},
            "kafka": {"bootstrap_servers": "b"},
        },
    )
    nasdaq, nyse = us.fetch_symbols_from_ncds()
    assert nasdaq == ["AAPL", "MSFT", "GOOG", "ATTR"]
    assert nyse == ["IBM", "BA", "SPY"]
    assert cons.closed
    assert captured["topic"] == "NLSPLUS-CTA-V4"  # default topic
    assert captured["kafka"] == {"bootstrap.servers": "b", "auto.offset.reset": "earliest"}
    assert captured["sec"]["oauth.token.endpoint.uri"] == "e"


def test_fetch_symbols_from_ncds_error_returns_empty(us, monkeypatch, capsys):
    class Client:
        def __init__(self, *a):
            raise RuntimeError("kafka down")

    monkeypatch.setattr(us, "_NCDSClient", Client, raising=False)
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: {"topic": "t"})
    assert us.fetch_symbols_from_ncds() == ([], [])
    assert "NCDS SDK error: kafka down" in capsys.readouterr().out


# ---------------- file parsing / fetch ----------------
NASDAQ = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF\n"
    "AAPL|Apple|Q|N|N|100|N\nQQQ|Invesco|Q|N|N|100|Y\nTST|Test|Q|Y|N|100|N\n"
    "BRK.A|x|Q|N|N|100|N\nLONGER|x|Q|N|N|100|N\n|blank|Q|N|N|100|N\nA^B|x|Q|N|N|100|N\n"
    "MSFT|Microsoft|Q|N|N|100|N\nFile Creation Time: 1234|||||\n"
)
OTHER = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue\n"
    "KO|Coca|N|KO|N|100|N\nXYZ|x|A|XYZ|N|100|N\nSPY|SPDR|P|SPY|Y|100|N\n"
)


def test_parse_nasdaq_file_filters(us):
    assert us._parse_nasdaq_file(NASDAQ) == ["AAPL", "MSFT"]
    assert us._parse_nasdaq_file("") == []
    assert us._parse_nasdaq_file(OTHER) == ["KO", "XYZ"]  # ETF skipped
    assert us._parse_nasdaq_file(OTHER, exchange_filter="n") == ["KO"]
    assert us._parse_nasdaq_file(OTHER, exchange_filter="A") == ["XYZ"]
    short = "Symbol|Name\nAAA\nBBB|x\n"
    assert us._parse_nasdaq_file(short) == ["AAA", "BBB"]


def test_parse_nasdaq_file_short_rows_skipped(us):
    text = "Name|Symbol\nonlyone\nfoo|BAR\n"
    assert us._parse_nasdaq_file(text) == ["BAR"]  # row shorter than sym_idx+1 skipped


class _Resp:
    def __init__(self, text="", status_ok=True, payload=None):
        self.text = text
        self._ok = status_ok
        self._payload = payload

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("http")

    def json(self):
        return self._payload


def test_fetch_nasdaq_file_https_then_http(us, monkeypatch):
    calls = []

    def get(url, timeout, headers):
        calls.append(url)
        if url.startswith("https"):
            return _Resp(status_ok=False)
        return _Resp("body")

    monkeypatch.setattr(us.requests, "get", get)
    assert us._fetch_nasdaq_file("f.txt") == "body"
    assert calls == [f"{us.NASDAQ_FTP_BASE}/f.txt", f"{us.NASDAQ_FTP_BASE_HTTP}/f.txt"]


def test_fetch_nasdaq_file_empty_everywhere(us, monkeypatch):
    seq = iter([_Resp(""), RuntimeError("x")])

    def get(url, timeout, headers):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(us.requests, "get", get)
    assert us._fetch_nasdaq_file("f.txt") == ""


SEC = {
    "data": [
        [1, "A", "aapl", "Nasdaq"],
        [2, "B", "ko", "NYSE"],
        [3, "C", "spy", "NYSE Arca"],
        [4, "D", "xx", "amex"],
        [5, "E", "bad.x", "Nasdaq"],
        [6, "F", "", "Nasdaq"],
        [7, "G", "otc", "OTC"],
        [8, "H"],
        [9, "I", "zz"],
        [10, "J", "sp ce", "NYSE"],
    ]
}


def test_sec_symbols(us, monkeypatch):
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: _Resp(payload=SEC))
    assert us.fetch_all_us_symbols_from_sec() == (["AAPL"], ["KO", "SPY", "XX"])


def test_sec_symbols_failure(us, monkeypatch, capsys):
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: _Resp(status_ok=False))
    assert us.fetch_all_us_symbols_from_sec() == ([], [])
    assert "SEC EDGAR fetch failed" in capsys.readouterr().out
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: _Resp(payload={}))
    assert us.fetch_all_us_symbols_from_sec() == ([], [])  # no "data" key


# ---------------- fetch_nasdaq/nyse priority ----------------
def test_fetch_nasdaq_symbols_prefers_ncds(us, monkeypatch):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: {"x": 1})
    monkeypatch.setattr(us, "fetch_symbols_from_ncds", lambda: (["N1"], ["Y1"]))
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: pytest.fail("FTP must not be hit"))
    assert us.fetch_nasdaq_symbols() == ["N1"]
    assert us._ncds_symbol_cache == {"nasdaq": ["N1"], "nyse": ["Y1"]}
    assert us.fetch_nyse_symbols() == ["Y1"]  # served from the cache


def test_fetch_nasdaq_symbols_ncds_empty_falls_to_ftp(us, monkeypatch):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: {"x": 1})
    monkeypatch.setattr(us, "fetch_symbols_from_ncds", lambda: ([], []))
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: NASDAQ)
    assert us.fetch_nasdaq_symbols() == ["AAPL", "MSFT"]


def test_fetch_nasdaq_and_nyse_fall_back_to_sec(us, monkeypatch):
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: "")
    monkeypatch.setattr(us, "fetch_all_us_symbols_from_sec", lambda: (["SN"], ["SY"]))
    assert us.fetch_nasdaq_symbols() == ["SN"]
    assert us.fetch_nyse_symbols() == ["SY"]
    # text present but parses to nothing -> still SEC
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: "Symbol|Name\n")
    assert us.fetch_nasdaq_symbols() == ["SN"]
    assert us.fetch_nyse_symbols() == ["SY"]


def test_fetch_nyse_symbols_ftp(us, monkeypatch):
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: OTHER)
    assert us.fetch_nyse_symbols() == ["KO", "XYZ"]


# ---------------- bulk_download_ohlc ----------------
def test_bulk_download_uses_cache_when_ok(us, monkeypatch):
    cache = types.SimpleNamespace(get_ohlc_bulk=lambda t, force: {"seen": (list(t), force)})
    monkeypatch.setattr(us, "_CACHE_OK", True)
    monkeypatch.setattr(us, "_US_CACHE", cache)
    assert us.bulk_download_ohlc(["A", "B"]) == {"seen": (["A", "B"], False)}


def _multi(tickers, n=10):
    idx = pd.date_range("2024-01-01", periods=n)
    cols = pd.MultiIndex.from_product([["Close"], tickers])
    return pd.DataFrame(np.arange(n * len(tickers), dtype=float).reshape(n, -1) + 1, idx, cols)


def test_bulk_download_multiindex_and_batching(us, monkeypatch):
    monkeypatch.setattr(us, "_CACHE_OK", False)
    monkeypatch.setattr(us, "BATCH_SIZE", 2)
    seen = []

    def dl(batch, **kw):
        seen.append(list(batch))
        assert kw["period"] == "1y" and kw["auto_adjust"] is True
        return _multi(batch)

    monkeypatch.setattr(us.yf, "download", dl)
    out = us.bulk_download_ohlc(["A", "B", "C"], period="1y")
    assert seen == [["A", "B"], ["C"]]
    assert sorted(out) == ["A", "B", "C"]


def test_bulk_download_short_history_dropped(us, monkeypatch):
    monkeypatch.setattr(us, "_CACHE_OK", False)
    # confirm+5 = 8 rows required: 7 is dropped, 8 kept
    monkeypatch.setattr(us.yf, "download", lambda b, **k: _multi(b, n=7))
    assert us.bulk_download_ohlc(["A"]) == {}
    monkeypatch.setattr(us.yf, "download", lambda b, **k: _multi(b, n=8))
    assert list(us.bulk_download_ohlc(["A"])) == ["A"]


def test_bulk_download_single_frame_empty_and_keyerror(us, monkeypatch):
    monkeypatch.setattr(us, "_CACHE_OK", False)
    flat = _ohlc(np.arange(1, 11))
    monkeypatch.setattr(us.yf, "download", lambda b, **k: flat)
    assert us.bulk_download_ohlc(["A", "B"]) == {"A": flat}
    monkeypatch.setattr(us.yf, "download", lambda b, **k: pd.DataFrame())
    assert us.bulk_download_ohlc(["A"]) == {}
    # MultiIndex that lacks the ticker -> KeyError swallowed
    monkeypatch.setattr(us.yf, "download", lambda b, **k: _multi(["Z"]))
    assert us.bulk_download_ohlc(["A"]) == {}


def test_bulk_download_rate_limit_retries_then_other_error(us, monkeypatch, capsys):
    monkeypatch.setattr(us, "_CACHE_OK", False)
    sleeps = []
    monkeypatch.setattr(us.time, "sleep", sleeps.append)
    state = {"n": 0}

    def dl(b, **k):
        state["n"] += 1
        if state["n"] <= 2:
            raise RuntimeError("Too Many Requests 429")
        return _multi(b)

    monkeypatch.setattr(us.yf, "download", dl)
    assert list(us.bulk_download_ohlc(["A"])) == ["A"]
    assert sleeps == [30, 60]  # linear back-off
    sleeps.clear()
    monkeypatch.setattr(us.yf, "download", lambda b, **k: (_ for _ in ()).throw(ValueError("bad")))
    assert us.bulk_download_ohlc(["A", "B"]) == {}
    assert "ERROR — bad" in capsys.readouterr().out
    assert sleeps == []  # non-rate errors do not retry or sleep (single batch)


def test_bulk_download_rate_limit_exhausts_three_attempts(us, monkeypatch):
    monkeypatch.setattr(us, "_CACHE_OK", False)
    sleeps = []
    monkeypatch.setattr(us.time, "sleep", sleeps.append)
    calls = []

    def dl(b, **k):
        calls.append(1)
        raise RuntimeError("Rate limited")

    monkeypatch.setattr(us.yf, "download", dl)
    assert us.bulk_download_ohlc(["A"]) == {}
    assert len(calls) == 3 and sleeps == [30, 60, 90]


def test_bulk_download_sleeps_between_batches(us, monkeypatch):
    monkeypatch.setattr(us, "_CACHE_OK", False)
    monkeypatch.setattr(us, "BATCH_SIZE", 1)
    sleeps = []
    monkeypatch.setattr(us.time, "sleep", sleeps.append)
    monkeypatch.setattr(us.yf, "download", lambda b, **k: pd.DataFrame())
    us.bulk_download_ohlc(["A", "B", "C"])
    assert sleeps == [us.SLEEP_BETWEEN] * 2  # between batches, not after the last


# ---------------- darvas edge cases ----------------
def test_darvas_missing_columns(us):
    df = pd.DataFrame({"Close": range(20)})
    assert us.compute_darvas_box(df) == {
        "signal": "INSUFFICIENT_DATA",
        "box_top": None,
        "box_bottom": None,
    }


def test_darvas_box_bottom_fallback_and_none(us):
    highs = [5, 5, 10, 20, 18, 17, 16, 16]
    # lows never confirm (strictly falling): bottom falls back to the lowest positive low
    lows = [3, 3, 9, 9, 8, 7, 6, 6]
    df = _ohlc([4, 4, 11, 15, 15, 15, 15, 15], highs, lows)
    r = us.compute_darvas_box(df)
    assert r["box_bottom"] == 6.0 and r["box_top"] == 20.0
    # lows all zero in the box segment -> no valid bottom
    df0 = _ohlc([4, 4, 11, 15, 15, 15, 15, 15], highs, [3, 3, 0, 0, 0, 0, 0, 0])
    r0 = us.compute_darvas_box(df0)
    assert r0["signal"] == "NO_BOX" and r0["box_top"] == 20.0 and r0["box_bottom"] is None


# ---------------- save_excel ----------------
@pytest.fixture
def fake_excel(monkeypatch, us):
    sheets = {}

    class FakeWriter:
        def __init__(self, path, engine=None):
            sheets["path"], sheets["engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def to_excel(self, writer, sheet_name=None, index=True):
        sheets[sheet_name] = self.copy()

    monkeypatch.setattr(us.pd, "ExcelWriter", FakeWriter)
    monkeypatch.setattr(pd.DataFrame, "to_excel", to_excel)
    return sheets


def test_save_excel_sheets_and_sorting(us, fake_excel):
    all_rows = [
        {"Symbol": "A", "Change%": 1.0, "GC_Signal": "GOLDEN_CROSS", "DMA_Gap%": 2},
        {"Symbol": "B", "Change%": 5.0, "GC_Signal": "ABOVE_200DMA", "DMA_Gap%": 9},
        {"Symbol": "C", "Change%": 3.0, "GC_Signal": "GOLDEN_CROSS", "DMA_Gap%": 7},
    ]
    fund = [
        {
            "Symbol": "A",
            "Piotroski_Score": 5,
            "Piotroski_Strong": "NO",
            "CoffeeCan": "PASS",
            "Revenue_CAGR_%": 1,
            "MagicFormula": "FAIL",
            "ROIC_%": 1,
            "BullCartel": "PASS",
            "Profit_Growth_YoY_%": 3,
        },
        {
            "Symbol": "B",
            "Piotroski_Score": 8,
            "Piotroski_Strong": "YES",
            "CoffeeCan": "PASS",
            "Revenue_CAGR_%": 9,
            "MagicFormula": "PASS",
            "ROIC_%": 7,
            "BullCartel": "FAIL",
            "Profit_Growth_YoY_%": 1,
        },
    ]
    ml = {
        "A": {"ML_Direction": "BULLISH", "ML_Pred_Ret%": 1},
        "B": {"ML_Direction": "BULLISH", "ML_Pred_Ret%": 4},
        "C": {"ML_Direction": "BEARISH", "ML_Pred_Ret%": -2},
        "D": {"ML_Direction": "NEUTRAL", "ML_Pred_Ret%": 0},
    }
    p = us.save_excel(all_rows, [], fund, [], None, tag="zz", ml_signal_map=ml)
    assert p.parent == us.DOWNLOAD_DIR and p.name.startswith("zz_full_scan_")
    assert fake_excel["engine"] == "openpyxl"
    assert list(fake_excel["All_Stocks"]["Symbol"]) == ["B", "C", "A"]  # Change% desc
    assert fake_excel["Darvas_Signals"].empty
    assert list(fake_excel["All_Fundamentals"]["Symbol"]) == ["B", "A"]
    assert list(fake_excel["Piotroski_Strong"]["Symbol"]) == ["B"]
    assert list(fake_excel["Coffee_Can"]["Symbol"]) == ["B", "A"]
    assert list(fake_excel["Magic_Formula"]["Symbol"]) == ["B"]
    assert list(fake_excel["Bull_Cartel"]["Symbol"]) == ["A"]
    assert list(fake_excel["Golden_Crossover"]["Symbol"]) == ["C", "A"]
    assert fake_excel["Multi_Screen_Hits"].empty and fake_excel["Triple_Hits"].empty
    assert list(fake_excel["ML_Bullish"]["Symbol"]) == ["B", "A"]
    assert list(fake_excel["ML_Bearish"]["Symbol"]) == ["C"]


def test_save_excel_without_ml_has_no_ml_sheets(us, fake_excel):
    us.save_excel([], [], [], [])
    assert "ML_Bullish" not in fake_excel and "All_Stocks" in fake_excel
    assert fake_excel["path"].name.startswith("us_full_scan_")


# ---------------- main ----------------
def _trend(n=260, start=100.0, step=1.0):
    return _ohlc(start + step * np.arange(n))


def _stub_main(us, monkeypatch, ohlc, fund=None, **extra):
    monkeypatch.setattr(us, "_load_symbol_cache", lambda: (["AAA", "BBB"], ["NYY", "AAA"]))
    monkeypatch.setattr(us, "_NSE_OK", False)
    monkeypatch.setattr(us, "bulk_download_ohlc", lambda syms, period: ohlc)
    saved = {}

    def save(*a, **k):
        saved["a"], saved["k"] = a, k
        return "OUT.xlsx"

    monkeypatch.setattr(us, "save_excel", save)
    monkeypatch.setattr(us, "fundamental_scan", fund or (lambda s: {"name": s}))
    for k, v in extra.items():
        monkeypatch.setattr(us, k, v)
    return saved


def test_main_scan_rows_and_filters(us, monkeypatch):
    penny = _ohlc(np.full(260, 0.5))
    nan = pd.DataFrame({"High": [1] * 10, "Low": [1] * 10, "Close": [np.nan] * 10})
    one = _ohlc([50.0] * 9)
    ohlc = {"AAA": _trend(), "BBB": penny, "NYY": nan, "ONE": one.iloc[:1]}
    saved = _stub_main(us, monkeypatch, ohlc)
    res = us.main(run_scans=False, min_price=1.0)
    rows = saved["a"][0]
    assert [r["Symbol"] for r in rows] == ["AAA", "ONE"]  # penny + all-NaN dropped
    aaa, one_row = rows
    assert aaa["LTP"] == 359.0 and aaa["Prev_Close"] == 358.0
    assert aaa["Change%"] == round((359 - 358) / 358 * 100, 2)
    assert aaa["GC_Signal"] == "ABOVE_200DMA"
    assert one_row["Prev_Close"] is None and one_row["Change%"] is None
    assert res["total_scanned"] == 2 and res["excel_path"] == "OUT.xlsx"
    assert res["breakout_count"] == 0 and saved["a"][2] == []  # no fundamentals when skipped


def test_main_min_price_boundary(us, monkeypatch):
    at = _ohlc(np.full(20, 5.0))
    saved = _stub_main(us, monkeypatch, {"AAA": at})
    us.main(run_scans=False, min_price=5.0)
    assert len(saved["a"][0]) == 1  # ltp == min_price is kept (strict <)
    saved = _stub_main(us, monkeypatch, {"AAA": at})
    us.main(run_scans=False, min_price=5.01)
    assert saved["a"][0] == []


def test_main_symbol_dedup_top_and_nasdaq_only(us, monkeypatch):
    seen = {}
    saved = _stub_main(us, monkeypatch, {})
    monkeypatch.setattr(us, "bulk_download_ohlc", lambda s, period: seen.update(s=list(s)) or {})
    us.main(run_scans=False)
    assert seen["s"] == ["AAA", "BBB", "NYY"]
    us.main(run_scans=False, nasdaq_only=True)
    assert seen["s"] == ["AAA", "BBB"]
    us.main(run_scans=False, top=2)
    assert seen["s"] == ["AAA", "BBB"]
    assert saved == {} or True


def test_main_fetches_symbols_when_cache_misses(us, monkeypatch):
    _stub_main(us, monkeypatch, {})
    monkeypatch.setattr(us, "_load_symbol_cache", lambda: (None, None))
    fetched = []
    monkeypatch.setattr(us, "fetch_nasdaq_symbols", lambda: fetched.append("n") or ["N1"])
    monkeypatch.setattr(us, "fetch_nyse_symbols", lambda: fetched.append("y") or ["Y1"])
    cached = []
    monkeypatch.setattr(us, "_save_symbol_cache", lambda n, y: cached.append((n, y)))
    seen = {}
    monkeypatch.setattr(us, "bulk_download_ohlc", lambda s, period: seen.update(s=list(s)) or {})
    us.main(run_scans=False)
    assert fetched == ["n", "y"] and cached == [(["N1"], ["Y1"])] and seen["s"] == ["N1", "Y1"]
    fetched.clear()
    us.main(run_scans=False, nasdaq_only=True)
    assert fetched == ["n"] and cached[-1] == (["N1"], [])


def test_main_fundamentals_multi_screen_and_triple(us, monkeypatch):
    # a rising series with a clean breakout on the last bar
    highs = [5, 5, 10, 20, 18, 17, 16, 16] + [16] * 4 + [16]
    lows = [3, 3, 12, 10, 11, 12, 13, 13] + [13] * 4 + [13]
    closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, 25]
    brk = _ohlc(closes, highs, lows)
    good = {
        "name": "Good",
        "sector": "S",
        "f_score": 8,
        "f_strong": True,
        "qualifies_cc": True,
        "qualifies_mf": False,
        "qualifies_bc": False,
        "cc_score": "6/6",
    }
    weak = {"name": "Weak", "f_score": 2, "error": "x"}

    def fund(sym):
        if sym == "BOOM":
            raise RuntimeError("scan crashed")
        return good if sym == "AAA" else weak

    ohlc = {"AAA": brk, "BBB": _ohlc(np.full(13, 20.0)), "BOOM": brk}
    saved = _stub_main(us, monkeypatch, ohlc, fund=fund)
    res = us.main(run_scans=True, workers=2)
    all_rows, darvas_rows, fund_rows, triple, multi = saved["a"][:5]
    assert [r["Symbol"] for r in darvas_rows] == ["AAA", "BOOM"]
    assert {r["Symbol"] for r in fund_rows} == {"AAA", "BBB"}  # BOOM isolated
    by = {r["Symbol"]: r for r in fund_rows}
    assert by["AAA"]["Piotroski_Strong"] == "YES" and by["AAA"]["CoffeeCan"] == "PASS"
    assert by["AAA"]["MagicFormula"] == "FAIL" and by["BBB"]["Piotroski_Strong"] == "NO"
    assert by["BBB"]["Error"] == "x" and by["AAA"]["Error"] == ""
    # AAA: breakout + above 200dma? (13 bars only -> below) so screens = breakout, f, cc = 3
    assert [r["Symbol"] for r in triple] == ["AAA"]
    assert [r["Symbol"] for r in multi] == ["AAA"] and multi[0]["Screens_Passed"] == 3
    assert res["triple_hits"] == triple and res["breakout_count"] == 2


def test_main_multi_screen_needs_three(us, monkeypatch):
    brk_closes = [4, 4, 11, 15, 15, 15, 15, 15, 15, 15, 15, 15, 25]
    brk = _ohlc(
        brk_closes,
        [5, 5, 10, 20, 18, 17, 16, 16, 16, 16, 16, 16, 16],
        [3, 3, 12, 10, 11, 12, 13, 13, 13, 13, 13, 13, 13],
    )
    two = {"f_strong": True}  # breakout + piotroski = 2 screens
    saved = _stub_main(us, monkeypatch, {"AAA": brk}, fund=lambda s: two)
    us.main(run_scans=True, workers=1)
    assert saved["a"][4] == []
    three = {"f_strong": True, "qualifies_bc": True}
    saved = _stub_main(us, monkeypatch, {"AAA": brk}, fund=lambda s: three)
    us.main(run_scans=True, workers=1)
    assert len(saved["a"][4]) == 1


def test_main_ml_stage(us, monkeypatch):
    brk = _trend(20)
    ml_df = pd.DataFrame(
        [
            {"symbol": "AAA", "direction": "BULLISH", "predicted_ret%": 2.5, "confidence": 0.7},
            {"symbol": "BBB", "direction": "BEARISH", "predicted_ret%": -1, "confidence": 0.5},
            {"symbol": "CCC", "predicted_ret%": 0, "confidence": 0.1},
        ]
    )
    engine = types.SimpleNamespace(predict_batch=lambda d, max_workers: ml_df)
    saved = _stub_main(
        us,
        monkeypatch,
        {"AAA": brk},
        fund=lambda s: {"f_strong": True, "qualifies_cc": True, "qualifies_mf": True},
        _ML_OK=True,
        _ML_ENGINE=engine,
    )
    us.main(run_scans=True, workers=1)
    all_rows, _, fund_rows, _, multi = saved["a"][:5]
    assert all_rows[0]["ML_Direction"] == "BULLISH" and all_rows[0]["ML_Pred_Ret%"] == 2.5
    assert fund_rows[0]["ML_Confidence"] == 0.7
    assert multi[0]["ML_Confirmed"] == "YES"
    ml_map = saved["k"]["ml_signal_map"]
    assert set(ml_map) == {"AAA", "BBB", "CCC"} and ml_map["BBB"]["ML_Direction"] == "BEARISH"
    assert pd.isna(ml_map["BBB"]["ML_TrainRMSE"])


def test_main_ml_unconfirmed_and_error(us, monkeypatch, capsys):
    brk = _trend(20)
    ml_df = pd.DataFrame([{"symbol": "AAA", "direction": "BEARISH", "predicted_ret%": -1}])
    engine = types.SimpleNamespace(predict_batch=lambda d, max_workers: ml_df)
    saved = _stub_main(
        us,
        monkeypatch,
        {"AAA": brk},
        fund=lambda s: {"f_strong": True, "qualifies_cc": True, "qualifies_mf": True},
        _ML_OK=True,
        _ML_ENGINE=engine,
    )
    us.main(workers=1)
    assert saved["a"][4][0]["ML_Confirmed"] == "NO"

    def boom(d, max_workers):
        raise RuntimeError("model down")

    engine.predict_batch = boom
    saved = _stub_main(us, monkeypatch, {"AAA": brk}, _ML_OK=True, _ML_ENGINE=engine)
    us.main(run_scans=False)
    assert "ML signal error: model down" in capsys.readouterr().out
    assert saved["k"]["ml_signal_map"] == {}


def test_main_market_context_regime(us, monkeypatch, capsys):
    _stub_main(us, monkeypatch, {})
    monkeypatch.setattr(us, "_NSE_OK", True)

    def dl(sym, **k):
        if sym == "^GSPC":
            cols = pd.MultiIndex.from_product([["Close"], ["^GSPC"]])
            vals = np.concatenate([np.full(200, 100.0), np.full(50, 120.0)])
            return pd.DataFrame(vals.reshape(-1, 1), columns=cols)
        return pd.DataFrame({"Close": [30.0]})

    monkeypatch.setattr(us.yf, "download", dl)
    us.main(run_scans=False)
    out = capsys.readouterr().out
    assert "Regime: BULL" in out and "CBOE VIX: 30.0  [PANIC]" in out


@pytest.mark.parametrize("vix,label", [(17.9, "NORMAL"), (18.0, "ELEVATED"), (25.0, "PANIC")])
def test_main_vix_levels_and_bear(us, monkeypatch, capsys, vix, label):
    _stub_main(us, monkeypatch, {})
    monkeypatch.setattr(us, "_NSE_OK", True)

    def dl(sym, **k):
        if sym == "^GSPC":
            return pd.DataFrame(
                {"Close": np.concatenate([np.full(200, 120.0), np.full(50, 100.0)])}
            )
        return pd.DataFrame({"Close": [vix]})

    monkeypatch.setattr(us.yf, "download", dl)
    us.main(run_scans=False)
    out = capsys.readouterr().out
    assert "Regime: BEAR" in out and f"[{label}]" in out


def test_main_market_context_failures_swallowed(us, monkeypatch, capsys):
    _stub_main(us, monkeypatch, {})
    monkeypatch.setattr(us, "_NSE_OK", True)
    monkeypatch.setattr(
        us.yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net"))
    )
    us.main(run_scans=False)  # must not raise
    assert "S&P 500" not in capsys.readouterr().out
    # S&P ok, VIX raises/empty
    state = {}

    def dl(sym, **k):
        if sym == "^GSPC":
            return pd.DataFrame({"Close": np.full(250, 100.0)})
        if state.get("empty"):
            return pd.DataFrame()
        raise RuntimeError("vix down")

    monkeypatch.setattr(us.yf, "download", dl)
    us.main(run_scans=False)
    out = capsys.readouterr().out
    assert "S&P 500" in out and "VIX" not in out
    state["empty"] = True
    us.main(run_scans=False)
    assert "CBOE VIX" not in capsys.readouterr().out
    monkeypatch.setattr(us.yf, "download", lambda *a, **k: pd.DataFrame())
    us.main(run_scans=False)
    assert "S&P 500" not in capsys.readouterr().out


def test_main_progress_line_every_200(us, monkeypatch, capsys):
    ohlc = {f"S{i}": _ohlc(np.full(12, 10.0)) for i in range(201)}
    _stub_main(us, monkeypatch, ohlc)
    us.main(run_scans=True, workers=4)
    out = capsys.readouterr().out
    assert "    200/201 done" in out and "    201/201 done" in out
    assert "    100/201 done" not in out
