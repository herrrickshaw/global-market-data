"""market_performance, screener_in, send_mailer, india_public, public_screens, build_mailer, run."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


# ---------------- market_performance ----------------
@pytest.fixture
def mp(monkeypatch):
    fake_module(monkeypatch, "data_sources", fetch=lambda *a, **k: {})
    return fresh_import(monkeypatch, "market_performance")


def _series(vals, start="2020-01-01"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="D"), dtype=float)


def test_metrics_too_short(mp):
    assert mp._metrics(_series(np.arange(1, 60))) == {}
    assert mp._metrics(_series([np.nan] * 100)) == {}
    assert mp._metrics(_series(np.arange(1, 61))) != {}  # exactly 60 bars is enough


def test_metrics_values_against_independent_calc(mp):
    rng = np.random.default_rng(7)
    close = _series(100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 800)), "2022-03-05")
    m = mp._metrics(close)
    yrs = (close.index[-1] - close.index[0]).days / 365.25
    assert m["Total_5y%"] == round((close.iloc[-1] / close.iloc[0] - 1) * 100, 1)
    assert m["CAGR%"] == round(((close.iloc[-1] / close.iloc[0]) ** (1 / yrs) - 1) * 100, 1)
    dr = close.pct_change().dropna()
    assert m["Vol_ann%"] == round(dr.std() * np.sqrt(252) * 100, 1)
    assert m["Sharpe"] == round(dr.mean() * 252 / (dr.std() * np.sqrt(252)), 2)
    assert m["MaxDD%"] == round(((close - close.cummax()) / close.cummax()).min() * 100, 1)
    assert m["MaxDD%"] <= 0
    assert m["Years"] == round(yrs, 1)
    last = close.index[-1]
    s1 = close[close.index >= last - pd.Timedelta(days=365)]
    assert m["Return_1y%"] == round((s1.iloc[-1] / s1.iloc[0] - 1) * 100, 1)
    jan = close[close.index >= pd.Timestamp(last.year, 1, 1)]
    assert m["YTD%"] == round((jan.iloc[-1] / jan.iloc[0] - 1) * 100, 1)


def test_metrics_flat_series_has_no_sharpe_or_cagr(mp):
    m = mp._metrics(_series([50.0] * 100))
    assert m["Vol_ann%"] == 0.0 and m["Sharpe"] is None
    assert m["CAGR%"] is None  # falsy 0.0 -> None
    assert m["Total_5y%"] == 0.0


def test_metrics_dropna_and_ytd_none_when_single_point_in_year(mp):
    idx = list(pd.date_range("2023-10-01", periods=90, freq="D"))
    close = pd.Series(np.linspace(100, 120, 91), index=pd.date_range("2023-10-01", periods=91))
    # last point is the only one of its year only if it's Jan 1 -> craft such a series
    s = pd.Series(
        np.linspace(100, 130, 100),
        index=pd.date_range(end="2024-01-01", periods=100, freq="D"),
    )
    m = mp._metrics(s)
    assert m["YTD%"] is None  # one bar in 2024
    assert m["Return_1y%"] is not None


@pytest.mark.xfail(
    strict=True,
    raises=ZeroDivisionError,
    reason="market_performance._metrics: `last / first` is evaluated before the first > 0 guard "
    "that protects CAGR, so a leading 0.0 close raises instead of degrading to CAGR=None",
)
def test_metrics_zero_first_price_gives_no_cagr(mp):
    close = _series([0.0] + list(np.linspace(1, 10, 99)))
    m = mp._metrics(close)
    assert m["CAGR%"] is None


def _perf_data():
    rng = np.random.default_rng(1)
    out = {}
    for tkr, drift in (("^GSPC", 0.0006), ("^NSEI", 0.0009)):
        c = 100 * np.cumprod(1 + rng.normal(drift, 0.008, 500))
        out[tkr] = pd.DataFrame({"Close": c}, index=pd.date_range("2024-01-01", periods=500))
    out["^N225"] = pd.DataFrame()  # empty -> skipped
    return out


def test_analyse_ranks_by_cagr_and_saves(mp, monkeypatch, tmp_path, capsys):
    seen = {}

    def fetch(tickers, **kw):
        seen["tickers"], seen["kw"] = tickers, kw
        return _perf_data()

    monkeypatch.setattr(mp, "multi_fetch", fetch)
    monkeypatch.setattr(mp, "OUT_PARQUET", tmp_path / "perf.parquet")
    monkeypatch.setattr(
        pd.DataFrame, "to_excel", lambda self, p, index=False: Path(p).write_text("x")
    )
    res = mp.analyse()
    assert seen["kw"] == {
        "order": ("yahoo", "stooq"),
        "period": "5y",
        "min_bars": 60,
        "verbose": False,
    }
    assert len(seen["tickers"]) == 20
    assert list(res["Market"]) == sorted(
        res["Market"], key=lambda m: -res.set_index("Market").loc[m, "CAGR%"]
    )
    assert set(res["Market"]) == {"US", "IN"}
    assert (tmp_path / "perf.parquet").exists() and (tmp_path / "perf.xlsx").exists()
    out = capsys.readouterr().out
    assert "JP (^N225): no data" in out and "US (^GSPC): no data" not in out
    assert "20-MARKET 5-YEAR PERFORMANCE" in out
    assert mp.load().shape[0] == 2


def test_analyse_quiet_and_empty(mp, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mp, "multi_fetch", lambda *a, **k: {})
    monkeypatch.setattr(mp, "OUT_PARQUET", tmp_path / "perf.parquet")
    res = mp.analyse(verbose=False)
    assert res.empty and not (tmp_path / "perf.parquet").exists()
    assert capsys.readouterr().out == ""
    assert mp.load().empty


def test_analyse_too_short_history_is_dropped(mp, monkeypatch, tmp_path):
    short = {
        "^GSPC": pd.DataFrame(
            {"Close": np.arange(1, 30.0)}, index=pd.date_range("2024-01-01", periods=29)
        )
    }
    monkeypatch.setattr(mp, "multi_fetch", lambda *a, **k: short)
    monkeypatch.setattr(mp, "OUT_PARQUET", tmp_path / "p.parquet")
    assert mp.analyse(verbose=False).empty


# ---------------- screener_in ----------------
import screener_in as si


class Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


HEAD = "<table><tr><th>S.No.</th><th>Name</th><th>CMP <span>Rs.</span></th><th>Cash Cycle</th></tr>"


def _row(sym, sno, *rest):
    """A screener.in-style row: S.No. cell, then the name cell holding the /company/ link."""
    tds = "".join(f"<td>{c}</td>" for c in rest)
    return f'<tr><td>{sno}</td><td><a href="/company/{sym}/">{sym}</a></td>{tds}</tr>'


def test_header_cleans_markup_and_caps_at_15():
    html = (
        "<tr><th> A  <b>b</b> </th><th></th>"
        + "".join(f"<th>h{i}</th>" for i in range(20))
        + "</tr>"
    )
    names = si._header(html)
    assert names[0] == "A b" and len(names) == 15  # blank skipped, capped
    assert names[1] == "h0"


def test_fetch_screen_pages_dedup_and_padding(monkeypatch, capsys):
    pages = {
        1: HEAD + _row("AAA", "1", "10", "5") + _row("M&M", "2") + "</table>",
        2: HEAD + _row("AAA", "1", "1", "1") + _row("CCC", "3", "30", "-4", "extra") + "</table>",
        3: HEAD + _row("AAA", "x") + "</table>",  # nothing new -> stop
        4: HEAD + _row("ZZZ", "never reached") + "</table>",
    }
    calls = []

    def get(url, headers, timeout):
        calls.append((url, timeout, "User-Agent" in headers))
        return Resp(pages[int(url.rsplit("=", 1)[1])])

    monkeypatch.setattr(si.requests, "get", get)
    df = si.fetch_screen("https://x/screens/9/foo/")
    assert [c[0] for c in calls] == [f"https://x/screens/9/foo/?page={i}" for i in (1, 2, 3)]
    assert all(c[1] == 25 and c[2] for c in calls)
    assert list(df["Symbol"]) == ["AAA", "M&M", "CCC"]
    assert list(df.columns[:5]) == ["Symbol", "S.No.", "Name", "CMP Rs.", "Cash Cycle"]
    assert (
        df.shape[1] == 6 and df.columns[5] == "c5"
    )  # widest row (sym + 5 cells) -> extra col named
    assert df.loc[df.Symbol == "AAA", "Cash Cycle"].iloc[0] == "5"
    assert df.loc[df.Symbol == "AAA", "c5"].iloc[0] == ""  # short rows padded
    assert df.loc[df.Symbol == "M&M", "CMP Rs."].iloc[0] == ""
    assert df.loc[df.Symbol == "CCC", "c5"].iloc[0] == "extra"
    assert "3 stocks from foo" in capsys.readouterr().out


def test_fetch_screen_stops_on_non_200_and_empty(monkeypatch, capsys):
    monkeypatch.setattr(si.requests, "get", lambda *a, **k: Resp("", 404))
    assert si.fetch_screen("https://x/screens/1/a/").empty
    monkeypatch.setattr(si.requests, "get", lambda *a, **k: Resp(HEAD + "</table>"))
    assert si.fetch_screen("https://x/screens/1/a/", verbose=False).empty


def test_fetch_screen_max_pages_and_no_header(monkeypatch):
    n = {"i": 0}

    def get(url, headers, timeout):
        n["i"] += 1
        return Resp(_row(f"S{n['i']}", "v"))  # no <th> -> header is just Symbol

    monkeypatch.setattr(si.requests, "get", get)
    df = si.fetch_screen("https://x/screens/1/a/", max_pages=3, verbose=False)
    assert n["i"] == 3 and len(df) == 3
    assert list(df.columns) == ["Symbol", "c1", "c2"]


def test_row_without_cells_or_symbol_skipped(monkeypatch):
    html = '<tr><td>nolink</td></tr><tr><a href="/company/NOCELL/"></a></tr>' + _row("OK", "1")
    seq = iter([Resp(html), Resp("")])
    monkeypatch.setattr(si.requests, "get", lambda *a, **k: next(seq))
    assert list(si.fetch_screen("https://x/screens/1/a/", verbose=False)["Symbol"]) == ["OK"]


def _ccc_frame():
    return pd.DataFrame(
        {
            "Symbol": ["A", "B", "C"],
            "Name": ["a", "b", "c"],
            "CMP Rs.": [1, 2, 3],
            "Mar Cap Rs.Cr.": [10, 20, 30],
            "ROCE %": [5, 6, 7],
            "Cash Cycle days": ["-10", "x", "20"],
            "Other": [0, 0, 0],
        }
    )


def test_ccc_screen_renames_and_filters(monkeypatch):
    monkeypatch.setattr(si, "fetch_screen", lambda url: _ccc_frame())
    out = si.ccc_screen()
    assert list(out.columns) == ["Symbol", "Name", "CMP", "MarCap_Cr", "ROCE", "Cash_Cycle"]


def test_ccc_screen_empty_and_map(monkeypatch):
    monkeypatch.setattr(si, "fetch_screen", lambda url: pd.DataFrame())
    assert si.ccc_screen().empty and si.ccc_map() == {}
    monkeypatch.setattr(si, "fetch_screen", lambda url: _ccc_frame())
    m = si.ccc_map()
    assert m["A"] == -10 and m["C"] == 20 and np.isnan(m["B"])
    monkeypatch.setattr(si, "fetch_screen", lambda url: pd.DataFrame({"Symbol": ["A"]}))
    assert si.ccc_map() == {}  # no cash cycle column


# ---------------- send_mailer ----------------
@pytest.fixture
def sm(monkeypatch):
    fake_module(monkeypatch, "build_mailer", build=lambda: ("subj", "txt", "<b>h</b>"))
    return fresh_import(monkeypatch, "send_mailer")


def test_send_without_creds_saves_html(sm, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")  # user missing is enough to bail
    assert sm.send("s", "t", "<h>") is False
    assert (tmp_path / "brief_today.html").read_text() == "<h>"
    assert "saved brief_today.html" in capsys.readouterr().out
    monkeypatch.setenv("GMAIL_USER", "me@x")
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    assert sm.send("s", "t", "<h2>") is False
    assert (tmp_path / "brief_today.html").read_text() == "<h2>"


def test_send_sends_to_each_recipient(sm, monkeypatch, capsys):
    log = {}

    class SMTP:
        def __init__(self, host, port):
            log["conn"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            log["login"] = (u, p)

        def sendmail(self, frm, to, msg):
            log["mail"] = (frm, to, msg)

    monkeypatch.setattr(sm.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setenv("GMAIL_USER", "me@x")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_TO", "a@x, b@y ")
    assert sm.send("Hello", "plain", "<p>html</p>") is True
    assert log["conn"] == ("smtp.gmail.com", 465) and log["login"] == ("me@x", "pw")
    assert log["mail"][:2] == ("me@x", ["a@x", "b@y"])
    assert "Subject: Hello" in log["mail"][2] and "text/html" in log["mail"][2]
    assert "sent 'Hello' → a@x, b@y" in capsys.readouterr().out


def test_send_defaults_recipient_to_user(sm, monkeypatch):
    got = {}

    class SMTP:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def sendmail(self, frm, to, msg):
            got["to"] = to

    monkeypatch.setattr(sm.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setenv("GMAIL_USER", "me@x")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.delenv("MAIL_TO", raising=False)
    sm.send("s", "t", "h")
    assert got["to"] == ["me@x"]


# ---------------- india_public ----------------
@pytest.fixture
def ip(monkeypatch, tmp_path):
    calls = {"screen": [], "fetch": []}

    def screen(slug, mkt, top, min_turnover_usd):
        calls["screen"].append((slug, mkt, top, min_turnover_usd))
        if slug == "golden_crossover":
            raise RuntimeError("no bhav " * 10)
        return pd.DataFrame({"Symbol": ["AAA", "BBB"]})

    fake_module(monkeypatch, "screener_kit", screen=screen)
    fake_module(monkeypatch, "liquidity", annotate=lambda df: df.assign(Liquidity="High"))
    fake_module(monkeypatch, "screener_in", fetch_screen=lambda url, verbose: pd.DataFrame())
    m = fresh_import(monkeypatch, "india_public")
    monkeypatch.setattr(m, "__file__", str(tmp_path / "india_public.py"))
    m.calls = calls
    return m


def test_load_config_override_and_bad_json(ip, tmp_path):
    assert ip._load_config() == ip.PUBLIC_SCREENS
    (tmp_path / "public_screens.json").write_text(
        json.dumps({"x": "http://u", "cash_conversion_cycle": "http://v"})
    )
    cfg = ip._load_config()
    assert cfg["x"] == "http://u" and cfg["cash_conversion_cycle"] == "http://v"
    assert ip.PUBLIC_SCREENS["cash_conversion_cycle"].startswith("https://www.screener.in")
    (tmp_path / "public_screens.json").write_text("{bad")
    assert ip._load_config() == ip.PUBLIC_SCREENS


def test_run_price_failure_isolated_and_fundamental_annotated(ip, monkeypatch, capsys):
    monkeypatch.setattr(
        ip.si, "fetch_screen", lambda url, verbose: pd.DataFrame({"Symbol": ["X", "Y"]})
    )
    out = ip.run(min_turnover=5, top=3)
    assert ip.calls["screen"] == [("darvas", "IN", 3, 5), ("golden_crossover", "IN", 3, 5)]
    assert list(out["darvas"]["Symbol"]) == ["AAA", "BBB"]
    assert out["golden_crossover"].empty
    ccc = out["cash_conversion_cycle"]
    assert set(ccc["Market"]) == {"IN"} and set(ccc["Liquidity"]) == {"High"}
    txt = capsys.readouterr().out
    assert "golden_crossover: price-screen failed (" in txt
    assert (
        "darvas" in txt and "AAA, BBB" in txt and "—" not in txt.split("darvas")[1].split("\n")[0]
    )
    assert "=== India — all screeners (public data) ===" in txt


def test_run_fetch_failure_and_empty_frames(ip, monkeypatch, capsys):
    def boom(url, verbose):
        raise ValueError("404")

    monkeypatch.setattr(ip.si, "fetch_screen", boom)
    out = ip.run(verbose=False)
    assert out["cash_conversion_cycle"].empty
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(ip.si, "fetch_screen", lambda url, verbose: pd.DataFrame())
    out = ip.run(verbose=True)
    txt = capsys.readouterr().out
    assert "cash_conversion_cycle" in txt and "public-screen fetch failed" not in txt
    assert out["cash_conversion_cycle"].empty


def test_run_failure_message_is_truncated(ip, monkeypatch, capsys):
    ip.run()
    line = [l for l in capsys.readouterr().out.splitlines() if "price-screen failed" in l][0]
    assert line == "  golden_crossover: price-screen failed (" + ("no bhav " * 10)[:40] + ")"


# ---------------- public_screens ----------------
@pytest.fixture
def ps(monkeypatch, tmp_path):
    fake_module(monkeypatch, "screener_in", fetch_screen=lambda url, verbose: pd.DataFrame())
    with monkeypatch.context() as ctx:  # importing creates cache_seed/public_screens in the repo
        ctx.setattr(Path, "mkdir", lambda *a, **k: None)
        m = fresh_import(monkeypatch, "public_screens")
    monkeypatch.setattr(m, "CACHE", tmp_path)
    monkeypatch.setattr(m, "__file__", str(tmp_path / "public_screens.py"))
    monkeypatch.setattr(m.time, "sleep", lambda s: m.__dict__.setdefault("_slept", []).append(s))
    return m


def test_ps_overrides(ps, tmp_path):
    assert ps._load_overrides() == ps.PUBLIC_URLS
    (tmp_path / "public_screens.json").write_text('{"extra": "u"}')
    assert ps._load_overrides()["extra"] == "u"
    (tmp_path / "public_screens.json").write_text("nope")
    assert "extra" not in ps._load_overrides()


def test_ps_fetch_caches_skips_and_sleeps(ps, monkeypatch, tmp_path, capsys):
    def fs(url, verbose):
        if "magic" in url:
            return pd.DataFrame({"Symbol": ["A", "B"], "x": [1, 2]})
        if "coffee" in url:
            return pd.DataFrame({"x": [1]})  # no Symbol -> invalid
        if "multibagger" in url:
            raise OSError("down")
        return pd.DataFrame()

    monkeypatch.setattr(ps.si, "fetch_screen", fs)
    got = ps.fetch(
        only=["magic_formula", "coffee_can", "multibagger", "value_stocks", "bogus"], pause=0.25
    )
    assert got == {"magic_formula": 2}
    assert (tmp_path / "magic_formula.parquet").exists()
    assert not (tmp_path / "coffee_can.parquet").exists()
    assert (
        ps._slept == [0.25] * 2
    )  # pause only after a cached or raised key; empty `continue`s skip it
    out = capsys.readouterr().out
    assert "magic_formula: 2 symbols cached" in out
    assert "coffee_can: empty/invalid — skipped" in out
    assert "multibagger: fetch failed (down)" in out


def test_ps_fetch_quiet_default_all_keys(ps, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        ps.si, "fetch_screen", lambda url, verbose: seen.append(url) or pd.DataFrame()
    )
    assert ps.fetch(verbose=False) == {}
    assert len(seen) == len(ps.PUBLIC_URLS)
    assert capsys.readouterr().out == ""


def test_ps_cached_screens_skips_corrupt(ps, tmp_path):
    pd.DataFrame({"Symbol": [1, 2, 2]}).to_parquet(tmp_path / "a.parquet")
    (tmp_path / "bad.parquet").write_text("junk")
    pd.DataFrame({"Other": [1]}).to_parquet(tmp_path / "c.parquet")
    assert ps.cached_screens() == {"a": {"1", "2"}}


def test_ps_main_list_and_fetch(ps, monkeypatch, tmp_path, capsys):
    pd.DataFrame({"Symbol": ["A", "B", "C"]}).to_parquet(tmp_path / "big.parquet")
    pd.DataFrame({"Symbol": ["A"]}).to_parquet(tmp_path / "small.parquet")
    monkeypatch.setattr("sys.argv", ["ps"])
    assert ps.main() == 0
    out = capsys.readouterr().out
    assert "cached public screens: 2" in out
    assert out.index("big") < out.index("small")  # largest first
    monkeypatch.setattr(ps, "fetch", lambda only=None: {"k": 1})
    monkeypatch.setattr("sys.argv", ["ps", "--fetch", "--only", "k"])
    assert ps.main() == 0
    out = capsys.readouterr().out
    assert "cached public screens" not in out
    assert f"fetched 1 screens; total mapped: {len(ps.PUBLIC_URLS)}" in out
    monkeypatch.setattr("sys.argv", ["ps", "--fetch", "--list"])
    ps.main()
    assert "cached public screens: 2" in capsys.readouterr().out
