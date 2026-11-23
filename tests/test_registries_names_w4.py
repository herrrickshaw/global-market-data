"""tool_registry, symbol_master (ticker<->name master), reference_data (Damodaran / French)."""

import io
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


# ---------------- tool_registry ----------------
@pytest.fixture
def tr(monkeypatch):
    import tool_registry

    monkeypatch.setattr(tool_registry, "_SCREENERS", {})
    monkeypatch.setattr(tool_registry, "_NEWS_SOURCES", {})
    monkeypatch.setattr(tool_registry, "_ANALYSES", {})
    return tool_registry


def test_screener_decorator_registers_and_keeps_function(tr):
    @tr.screener("k1", "desc", tier_weight=3)
    def f(c):
        """docstring"""
        return c > 1

    assert f(2) is True  # decorator returns the original function
    s = tr._SCREENERS["k1"]
    assert (s.key, s.description, s.tier_weight) == ("k1", "desc", 3)

    @tr.screener("k2")
    def g(c):
        """from doc"""
        return True

    @tr.screener("k3")
    def h(c):
        return True

    assert tr._SCREENERS["k2"].description == "from doc" and tr._SCREENERS["k2"].tier_weight == 1
    assert tr._SCREENERS["k3"].description == "k3"  # falls back to the key
    assert tr.list_screeners() == ["k1", "k2", "k3"]


def test_run_screener_known_unknown_truthiness(tr):
    tr.screener("t")(lambda c: c)
    assert tr.run_screener("t", 5) is True and tr.run_screener("t", 0) is False
    assert tr.run_screener("missing", 5) is False
    tr.screener("boom")(lambda c: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        tr.run_screener("boom", 1)  # run_screener does not isolate errors


def test_run_all_screeners_isolates_failures_and_keeps_order(tr):
    tr.screener("a")(lambda c: True)
    tr.screener("b")(lambda c: 1 / 0)
    tr.screener("c")(lambda c: False)
    tr.screener("d")(lambda c: "yes")
    assert tr.run_all_screeners(object()) == ["a", "d"]
    assert tr._safe(lambda x: x, 0) is False and tr._safe(lambda x: x, 1) is True


def test_news_source_decorator_variants(tr):
    @tr.news_source("rss", feeds=["http://f"], market="US")
    def _(ticker, market):
        return []

    src = tr.get_news_source("rss")
    assert (
        src.feeds == ["http://f"] and src.market == "US" and src.fn is None
    )  # default RSS fetcher

    @tr.news_source("custom", feeds=["http://f"])
    def fetch(ticker, market):
        return [1]

    c = tr.get_news_source("custom")
    assert c.fn is fetch and c.market == "IN"

    @tr.news_source("nofeeds")
    def _(ticker, market):
        return []

    assert (
        tr.get_news_source("nofeeds").feeds == [] and tr.get_news_source("nofeeds").fn is not None
    )
    assert tr.get_news_source("nope") is None
    assert tr.list_news_sources() == ["rss", "custom", "nofeeds"]


def test_analysis_registry(tr):
    @tr.analysis("count", "counts")
    def count(ctx):
        return len(ctx)

    @tr.analysis("nodoc")
    def nodoc(ctx):
        """docs here"""

    assert tr.run_analysis("count", [1, 2]) == 2
    assert tr.run_analysis("missing", 1) is None
    assert (
        tr._ANALYSES["nodoc"].description == "docs here"
        and tr._ANALYSES["count"].description == "counts"
    )
    assert tr.list_analyses() == ["count", "nodoc"]


def test_register_builtin_screeners_wires_specs(tr, monkeypatch):
    class Spec:
        def explain(self):
            return "explained"

        def is_satisfied_by(self, c):
            return True

    names = [
        "BullCartelSpec",
        "CoffeeCanSpec",
        "DarvasBoxSpec",
        "GoldenCrossSpec",
        "MagicFormulaSpec",
        "PiotroskiSpec",
    ]
    specs = fake_module(monkeypatch, "domain.screening.specifications", **{n: Spec for n in names})
    fake_module(monkeypatch, "domain.screening")
    fake_module(monkeypatch, "domain")
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert tr.register_builtin_screeners() == 6
    assert sorted(tr.list_screeners()) == sorted(
        ["darvas", "golden_cross", "piotroski", "coffee_can", "magic_formula", "bull_cartel"]
    )
    assert tr._SCREENERS["darvas"].description == "explained"
    assert tr.run_screener("darvas", None) is True
    assert str(Path(tr.__file__).parent / "stock_ddd") in sys.path


def test_register_builtin_screeners_missing_ddd(tr, monkeypatch):
    monkeypatch.setitem(sys.modules, "domain", None)  # forces ImportError
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert tr.register_builtin_screeners() == 0 and tr.list_screeners() == []


# ---------------- symbol_master ----------------
@pytest.fixture
def sm(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    m = fresh_import(monkeypatch, "symbol_master")
    monkeypatch.setattr(m, "_LOOKUP", {})
    return m


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ADANI ENTERPRISES LIMITED", "ADANI ENTERPRISES"),
        ("MARUTI SUZUKI INDIA LTD.", "MARUTI SUZUKI INDIA"),
        ("Amara Raja Energy & Mobility L", "AMARA RAJA ENERGY"),  # first 3 tokens kept
        ("Tata Motors Pvt Private Limited", "TATA MOTORS"),
        ("THE ST CO", ""),  # nothing >=3 chars survives the stop-list
        ("A1B2 Corp", ""),  # digits stripped; 'A'/'B' too short; CORP is a stop-word
        ("", ""),
        (None, ""),
        (123, ""),
        ("Reliance-Industries Inc.", "RELIANCE INDUSTRIES"),
    ],
)
def test_clean_name(sm, raw, expected):
    assert sm.clean_name(raw) == expected


def test_clean_name_token_length_boundary(sm):
    assert sm.clean_name("AB ABC") == "ABC"  # 2-char tokens dropped, 3-char kept


class _NSE:
    def __init__(self, files, fail_first=0):
        self.files, self.calls, self.fail_first = files, [], fail_first

    def __call__(self, download_folder, server):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def equityBhavcopy(self, d):  # noqa: N802
        self.calls.append(d)
        if len(self.calls) <= self.fail_first:
            raise RuntimeError("holiday")
        return self.files.get(len(self.calls) - 1)


class _Missing:
    def exists(self):
        return False


def test_nse_names_skips_failures_and_takes_first_good_day(sm, tmp_path, monkeypatch):
    csv = tmp_path / "nse.csv"
    pd.DataFrame(
        {
            "SctySrs": ["EQ", "BE", "EQ"],
            "TckrSymb": [" TCS ", "XYZ", "INFY"],
            "FinInstrmNm": ["TATA CONSULTANCY", "X", "INFOSYS LTD"],
        }
    ).to_csv(csv, index=False)
    nse = _NSE({1: csv}, fail_first=1)  # day 0 raises, day 1 returns the file
    fake_module(monkeypatch, "nse", NSE=nse)
    rows = sm._nse_names()
    assert [r["symbol"] for r in rows] == ["TCS", "INFY"]  # EQ only, stripped
    assert rows[0] == {
        "symbol": "TCS",
        "name": "TATA CONSULTANCY",
        "exchange": "NSE",
        "suffix": ".NS",
    }
    assert len(nse.calls) == 2  # stopped at the first success


def test_nse_names_tries_seven_days_then_empty(sm, monkeypatch):
    nse = _NSE({i: _Missing() for i in range(7)})
    fake_module(monkeypatch, "nse", NSE=nse)
    assert sm._nse_names() == []
    assert len(nse.calls) == 7
    assert (
        round((nse.calls[0] - nse.calls[6]).total_seconds() / 86400) == 6
    )  # one day back per attempt


def test_nse_and_bse_names_without_library(sm, monkeypatch):
    monkeypatch.setitem(sys.modules, "nse", None)
    monkeypatch.setitem(sys.modules, "bse", None)
    assert sm._nse_names() == [] and sm._bse_names({"A"}) == []


class _BSE:
    def __init__(self, files):
        self.files = files
        self.calls = 0

    def __call__(self, download_folder):
        return self

    def bhavcopyReport(self, d):  # noqa: N802
        self.calls += 1
        return self.files[0]


def test_bse_names_filters_groups_and_dual_listed(sm, tmp_path, monkeypatch):
    csv = tmp_path / "bse.csv"
    pd.DataFrame(
        {
            "SctySrs": ["A", "ZZ", "B", "A"],
            "TckrSymb": ["500001", "500002", "500003", "TCS"],
            "FinInstrmNm": ["ONE", "TWO", "THREE", "TCS BSE"],
        }
    ).to_csv(csv, index=False)
    bse = _BSE([csv])
    fake_module(monkeypatch, "bse", BSE=bse)
    rows = sm._bse_names({"TCS"})
    assert [r["symbol"] for r in rows] == ["500001", "500003"]  # ZZ group and NSE dual-listing out
    assert rows[0]["exchange"] == "BSE" and rows[0]["suffix"] == ".BO" and bse.calls == 1


def test_bse_names_without_series_column_keeps_all(sm, tmp_path, monkeypatch):
    csv = tmp_path / "bse.csv"
    pd.DataFrame({"TckrSymb": ["1", "2"], "FinInstrmNm": ["a", "b"]}).to_csv(csv, index=False)
    fake_module(monkeypatch, "bse", BSE=_BSE([csv]))
    assert len(sm._bse_names(set())) == 2


def test_bse_names_exhausts_days(sm, monkeypatch):
    bse = _BSE([_Missing()])
    fake_module(monkeypatch, "bse", BSE=bse)
    assert sm._bse_names(set()) == [] and bse.calls == 7


class _Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_us_names_exchange_and_ticker_filters(sm, monkeypatch):
    import requests

    data = {
        "data": [
            [1, "Apple Inc.", "aapl", "Nasdaq"],
            [2, "Ford", "F", "NYSE"],
            [3, "Amex Co", "AMX", "AMEX"],
            [4, "OTC Co", "OTCX", "OTC"],
            [5, "Too Long", "ABCDEF", "Nasdaq"],  # >5 chars
            [6, "Dotted", "BRK.B", "NYSE"],
            [7, "Short", "X"],  # no exchange column
            [8, "Five", "ABCDE", "Nasdaq"],  # exactly 5 is kept
        ]
    }
    seen = {}
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, timeout, headers: seen.update(url=url, t=timeout, h=headers) or _Resp(data),
    )
    rows = sm._us_names()
    assert [(r["symbol"], r["exchange"]) for r in rows] == [
        ("AAPL", "NASDAQ"),
        ("F", "NYSE"),
        ("AMX", "NYSE"),
        ("ABCDE", "NASDAQ"),
    ]
    assert seen["t"] == 30 and "sec.gov" in seen["url"] and "User-Agent" in seen["h"]


def test_us_names_failure_is_empty(sm, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: 1 / 0)
    assert sm._us_names() == []


def test_build_master_merges_dedupes_and_saves(sm, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sm, "MASTER", tmp_path / "m.parquet")
    nse = [
        {
            "symbol": "TCS",
            "name": "TATA CONSULTANCY SERVICES LIMITED",
            "exchange": "NSE",
            "suffix": ".NS",
        }
    ] * 2
    monkeypatch.setattr(sm, "_nse_names", lambda: nse)
    got = {}
    monkeypatch.setattr(
        sm,
        "_bse_names",
        lambda s: got.setdefault("nse", s)
        and [{"symbol": "500001", "name": "X LTD", "exchange": "BSE", "suffix": ".BO"}],
    )
    monkeypatch.setattr(
        sm,
        "_us_names",
        lambda: [{"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "suffix": ""}],
    )
    df = sm.build_master()
    assert got["nse"] == {"TCS"} and len(df) == 3  # NSE duplicate dropped
    assert list(df.columns) == ["symbol", "name", "name_clean", "exchange", "suffix", "yf_symbol"]
    assert dict(zip(df["symbol"], df["yf_symbol"])) == {
        "TCS": "TCS.NS",
        "500001": "500001.BO",
        "AAPL": "AAPL",
    }
    assert df.set_index("symbol").loc["TCS", "name_clean"] == "TATA CONSULTANCY SERVICES"
    assert (tmp_path / "m.parquet").exists()
    assert "NSE: 2 | BSE-only: 1 | US: 1" in capsys.readouterr().out
    sm.build_master(include_us=False)
    assert "US: 0" in capsys.readouterr().out


def test_build_master_nothing_fetched(sm, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sm, "MASTER", tmp_path / "m.parquet")
    for n in ("_nse_names", "_us_names"):
        monkeypatch.setattr(sm, n, lambda: [])
    monkeypatch.setattr(sm, "_bse_names", lambda s: [])
    assert sm.build_master().empty and not (tmp_path / "m.parquet").exists()
    assert "No symbols fetched" in capsys.readouterr().out


def _master_df(name="X LTD"):
    return pd.DataFrame(
        [
            {
                "symbol": "TCS",
                "name": name,
                "name_clean": "X",
                "exchange": "NSE",
                "suffix": ".NS",
                "yf_symbol": "TCS.NS",
            }
        ]
    )


def test_load_master_fresh_stale_missing_corrupt(sm, monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "MASTER", tmp_path / "m.parquet")
    built = []
    monkeypatch.setattr(sm, "build_master", lambda: built.append(1) or _master_df("BUILT"))
    assert sm.load_master()["name"].iloc[0] == "BUILT"  # missing -> build
    _master_df("DISK").to_parquet(sm.MASTER)
    assert sm.load_master()["name"].iloc[0] == "DISK" and len(built) == 1  # fresh -> read
    import os

    old = datetime.now().timestamp() - (sm.STALE_HOURS + 1) * 3600
    os.utime(sm.MASTER, (old, old))
    assert sm.load_master()["name"].iloc[0] == "BUILT" and len(built) == 2  # stale -> rebuild
    assert (
        sm.load_master(auto_refresh=False)["name"].iloc[0] == "DISK"
    )  # stale tolerated when disabled
    sm.MASTER.write_text("corrupt")
    os.utime(sm.MASTER, (datetime.now().timestamp(),) * 2)
    assert sm.load_master()["name"].iloc[0] == "BUILT" and len(built) == 3  # unreadable -> rebuild


def test_load_master_freshness_boundary(sm, monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(sm, "MASTER", tmp_path / "m.parquet")
    monkeypatch.setattr(sm, "build_master", lambda: _master_df("BUILT"))
    _master_df("DISK").to_parquet(sm.MASTER)
    near = datetime.now().timestamp() - (sm.STALE_HOURS - 0.5) * 3600
    os.utime(sm.MASTER, (near, near))
    assert sm.load_master()["name"].iloc[0] == "DISK"


def test_name_lookups(sm, monkeypatch):
    m = pd.concat([_master_df("FIRST CO"), _master_df("DUP CO")], ignore_index=True)
    m.loc[1, "name_clean"] = "DUP"
    monkeypatch.setattr(sm, "load_master", lambda: m)
    assert sm.name_for("TCS") == "FIRST CO"  # keep="first"
    assert sm.clean_name_for("TCS") == "X"
    assert sm.name_for("ZZZ") == "" and sm.clean_name_for("ZZZ") == ""


def test_name_lookup_empty_master_not_cached(sm, monkeypatch):
    monkeypatch.setattr(sm, "load_master", lambda: pd.DataFrame())
    assert sm.name_for("TCS") == "" and sm._LOOKUP == {}


# ---------------- reference_data ----------------
@pytest.fixture
def rd(monkeypatch, tmp_path):
    monkeypatch.setenv("REF_DATA_CACHE", str(tmp_path / "ref"))
    return fresh_import(monkeypatch, "reference_data")


def test_cached_get_downloads_once(rd, monkeypatch):
    calls = []

    class R:
        content = b"payload"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        rd.requests, "get", lambda url, headers, timeout: calls.append((url, timeout)) or R()
    )
    assert rd._cached_get("http://x/f", "f.bin") == b"payload"
    assert rd._cached_get("http://x/f", "f.bin") == b"payload"
    assert calls == [("http://x/f", 45)] and (rd.CACHE / "f.bin").read_bytes() == b"payload"


def test_cached_get_http_error_not_cached(rd, monkeypatch):
    class R:
        content = b"x"

        def raise_for_status(self):
            raise RuntimeError("404")

    monkeypatch.setattr(rd.requests, "get", lambda *a, **k: R())
    with pytest.raises(RuntimeError):
        rd._cached_get("http://x", "bad.bin")
    assert not (rd.CACHE / "bad.bin").exists()


class _FakeXL:
    def __init__(self, sheets):
        self.sheet_names = list(sheets)


def _patch_excel(monkeypatch, rd, sheets, probe_fail=()):
    """sheets: {name: (header_row_index, DataFrame-with-industry)}"""

    def excel_file(buf):
        if sheets is None:
            raise ValueError("bad xls")
        return _FakeXL(sheets)

    def read_excel(buf, sheet_name=None, header=0, nrows=None):
        hdr_idx, df = sheets[sheet_name]
        if header is None:
            if sheet_name in probe_fail:
                raise ValueError("unreadable")
            rows = [[None] * len(df.columns) for _ in range(hdr_idx)] + [list(df.columns)]
            return pd.DataFrame(rows)
        return df.copy()

    monkeypatch.setattr(rd.pd, "ExcelFile", excel_file)
    monkeypatch.setattr(rd.pd, "read_excel", read_excel)


def _ind_df(**cols):
    return pd.DataFrame({"Industry Name": ["Software", "Bank (Money Center)", None], **cols})


def test_read_damodaran_finds_header_sheet(rd, monkeypatch):
    sheets = {
        "Notes": (0, pd.DataFrame({"x": [1]})),
        "Industry Averages": (7, _ind_df(**{"Current PE": [20.0, 8.0, 1.0]})),
    }
    _patch_excel(monkeypatch, rd, sheets)
    df = rd._read_damodaran(b"raw")
    assert list(df["Industry"]) == ["Software", "Bank (Money Center)"]  # blank industry dropped
    assert list(df["_key"]) == ["software", "bank (money center)"]


def test_read_damodaran_failures(rd, monkeypatch):
    _patch_excel(monkeypatch, rd, None)
    assert rd._read_damodaran(b"x") is None
    _patch_excel(monkeypatch, rd, {"S": (0, pd.DataFrame({"x": [1]}))})
    assert rd._read_damodaran(b"x") is None  # no 'Industry Name' header anywhere
    _patch_excel(monkeypatch, rd, {"S": (2, _ind_df(a=[1, 2, 3]))}, probe_fail=("S",))
    assert rd._read_damodaran(b"x") is None  # probe error skipped


def test_damodaran_region_fallback_and_unknown_metric(rd, monkeypatch):
    assert rd.damodaran("nope") is None
    fetched = []

    def cached_get(url, fname):
        fetched.append(fname)
        if "emerg" in fname:
            raise OSError("404")
        return b"raw"

    monkeypatch.setattr(rd, "_cached_get", cached_get)
    good = pd.DataFrame({"Industry": ["a"], "_key": ["a"]})
    monkeypatch.setattr(rd, "_read_damodaran", lambda raw: good)
    assert rd.damodaran("pe", "emerg") is good
    assert fetched == ["damodaran_pe_emerg.xls", "damodaran_pe_US.xls"]  # fell back to the US file
    fetched.clear()
    assert rd.damodaran("beta", "") is good and fetched == ["damodaran_beta_US.xls"]
    monkeypatch.setattr(rd, "_read_damodaran", lambda raw: pd.DataFrame())
    assert rd.damodaran("pe", "emerg") is None  # empty table is not usable


def _table():
    return pd.DataFrame(
        {
            "Industry": ["Software (System & Application)", "Bank (Money Center)", "Auto & Truck"],
            "_key": ["software (system & application)", "bank (money center)", "auto & truck"],
            "Current PE": [30.0, 10.0, 12.0],
            "Trailing PE": [28.0, 9.0, 11.0],
            "ROE": [0.25, 0.1, "n/a"],
            "Beta": [1.2, 1.0, 1.1],
        }
    )


def test_industry_metric_exact_loose_hints(rd, monkeypatch):
    monkeypatch.setattr(rd, "damodaran", lambda m, r: _table())
    assert rd.industry_metric("Software (System & Application)") == 30.0
    assert rd.industry_metric("  bank (money center) ") == 10.0
    assert rd.industry_metric("Software Systems Inc") == 30.0  # loose match on first 8 chars
    assert rd.industry_metric("Bank (Money Center)", "roe") == 0.1
    assert rd.industry_metric("Bank (Money Center)", "beta") == 1.0
    assert rd.industry_metric("Bank (Money Center)", column_hint="trailing pe") == 9.0
    assert rd.industry_metric("Nonexistent Industry") is None
    assert rd.industry_metric("") is None and rd.industry_metric(None) is None


def test_industry_metric_unparseable_and_missing_table(rd, monkeypatch):
    monkeypatch.setattr(rd, "damodaran", lambda m, r: _table())
    assert rd.industry_metric("Auto & Truck", "roe") is None  # 'n/a' cannot be a float
    assert rd.industry_metric("Bank (Money Center)", "margin") is None  # no margin column
    monkeypatch.setattr(rd, "damodaran", lambda m, r: None)
    assert rd.industry_metric("x") is None


def _ff_zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("F-F.CSV", text)
    return buf.getvalue()


def test_french_factors_parses_monthly_block_only(rd, monkeypatch):
    text = (
        "This file was created...\n,Mkt-RF,SMB,HML,RF\n"
        "202401, 1.5, 0.2, -0.3, 0.4\n202402,2.5,0.1,0.0,0.4\n"
        "\n Annual Factors\n2023, 9, 9, 9, 9\n"
    )
    monkeypatch.setattr(rd, "_cached_get", lambda u, f: _ff_zip(text))
    df = rd.french_factors()
    assert list(df["YYYYMM"]) == ["202401", "202402"]
    assert df["Mkt-RF"].tolist() == [1.5, 2.5] and df["HML"].iloc[0] == -0.3
    assert df["RF"].dtype.kind == "f"


def test_french_factors_failures(rd, monkeypatch):
    monkeypatch.setattr(rd, "_cached_get", lambda u, f: 1 / 0)
    assert rd.french_factors() is None
    monkeypatch.setattr(rd, "_cached_get", lambda u, f: b"not a zip")
    assert rd.french_factors() is None
    monkeypatch.setattr(rd, "_cached_get", lambda u, f: _ff_zip("no data rows here\n"))
    assert rd.french_factors() is None


def test_damodaran_companies_builds_and_caches(rd, monkeypatch):
    raw = pd.DataFrame(
        {
            "Company Name": ["Acme", None, "Beta"],
            "Exchange:Ticker": ["NYSE:ACM", "X:Y", "NASDAQ:BTA"],
            "Industry Group": ["Software", "Bank", "Auto"],
        }
    )
    calls = []
    monkeypatch.setattr(rd, "_cached_get", lambda u, f: calls.append(f) or b"raw")
    monkeypatch.setattr(rd.pd, "read_excel", lambda buf, sheet_name: raw.copy())
    df = rd.damodaran_companies()
    assert list(df["Ticker"]) == ["ACM", "BTA"] and list(df["Exchange"]) == ["NYSE", "NASDAQ"]
    assert len(df) == 2  # unnamed company dropped
    assert (rd.CACHE / "damodaran_companies.parquet").exists()
    monkeypatch.setattr(rd, "_cached_get", lambda u, f: 1 / 0)
    assert len(rd.damodaran_companies()) == 2  # served from the parquet
    assert rd.damodaran_companies(refresh=True) is None  # refresh hits the failing download


def test_company_industry_lookup(rd, monkeypatch):
    df = pd.DataFrame(
        {
            "Ticker": ["ACM", "BTA"],
            "Company Name": ["Acme Corp", "Beta Inc"],
            "Industry Group": ["Software", "Auto"],
        }
    )
    monkeypatch.setattr(rd, "damodaran_companies", lambda: df)
    assert rd.company_industry("acm") == "Software" and rd.company_industry(" Beta Inc ") == "Auto"
    assert rd.company_industry("unknown") is None
    monkeypatch.setattr(rd, "damodaran_companies", lambda: None)
    assert rd.company_industry("acm") is None


def test_aqr_links(rd, monkeypatch):
    html = '<a href="/a/one.xlsx">1</a><a href="https://x.com/two.XLS">2</a><a href="/a/one.xlsx">dup</a><a href="/p.pdf">no</a>'

    class R:
        text = html

    monkeypatch.setattr(rd.requests, "get", lambda url, headers, timeout: R())
    assert rd.aqr_links() == ["https://www.aqr.com/a/one.xlsx", "https://x.com/two.XLS"]
    monkeypatch.setattr(rd.requests, "get", lambda *a, **k: 1 / 0)
    assert rd.aqr_links() == []


def test_info_lists_cached_files(rd):
    (rd.CACHE / "b.bin").write_bytes(b"1")
    (rd.CACHE / "a.bin").write_bytes(b"1")
    assert rd.info() == {"cache_dir": str(rd.CACHE), "cached_files": ["a.bin", "b.bin"]}


def test_industry_metric_loose_match_uses_first_eight_chars(rd, monkeypatch):
    df = pd.DataFrame(
        {
            "Industry": ["Softwood Timber", "Software Alpha"],
            "_key": ["softwood timber", "software alpha"],
            "Current PE": [5.0, 30.0],
        }
    )
    monkeypatch.setattr(rd, "damodaran", lambda m, r: df)
    # 'software' (8 chars) matches only the first row; a 4-char prefix 'soft' would also hit the second
    assert rd.industry_metric("Software Systems") == 30.0
