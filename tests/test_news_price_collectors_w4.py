"""pipeline_news (sentiment logging / price join) and daily_price_collector (Phase 2 feature prep)."""

import json
import types

import numpy as np
import pandas as pd
import pytest

from tests.w4_helpers import fake_module, fresh_import


# ---------------- pipeline_news ----------------
class Sent:
    def __init__(self, score, label="POSITIVE", n=3, providers=("rss:a", "rss:b")):
        self.score, self.label, self.n_articles, self.providers = score, label, n, list(providers)


class FakeSP:
    batch = {}
    mood = {"mood": "POSITIVE", "score": 0.25, "n_articles": 12}
    calls = []

    def status(self):
        return "providers ok"

    def get_market_mood(self):
        return FakeSP.mood

    def get_batch(self, tickers, market):
        FakeSP.calls.append((list(tickers), market))
        return {t: FakeSP.batch[t] for t in tickers if t in FakeSP.batch}


@pytest.fixture
def pn(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    FakeSP.batch = {"AAA": Sent(0.5), "BBB": Sent(-0.2, "NEGATIVE", 2), "CCC": Sent(0.0, "NO_DATA", 0, [])}
    FakeSP.mood = {"mood": "POSITIVE", "score": 0.25, "n_articles": 12}
    FakeSP.calls = []
    fake_module(monkeypatch, "sentiment_pipeline", SentimentPipeline=FakeSP)
    m = fresh_import(monkeypatch, "pipeline_news")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "ohlc")
    (tmp_path / "ohlc").mkdir()
    return m


def test_stage_mood_prints_regime(pn, capsys):
    mood = pn.stage_mood(FakeSP())
    out = capsys.readouterr().out
    assert mood["mood"] == "POSITIVE" and "🟢 Indian market news mood: POSITIVE (score +0.250) from 12 live articles" in out
    FakeSP.mood = {"mood": "NEGATIVE", "score": -0.5, "n_articles": 3}
    pn.stage_mood(FakeSP())
    assert "🔴" in capsys.readouterr().out
    FakeSP.mood = {"mood": "WEIRD", "score": 0.0, "n_articles": 1}
    pn.stage_mood(FakeSP())
    assert "⚪" in capsys.readouterr().out
    FakeSP.mood = {}
    assert pn.stage_mood(FakeSP()) == {} and "No RSS mood available" in capsys.readouterr().out


def test_stage_score_sorted_and_skips_no_data(pn, capsys):
    res = pn.stage_score(FakeSP(), ["BBB", "AAA", "CCC"], "IN")
    assert set(res) == {"AAA", "BBB", "CCC"}
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip().startswith(("AAA", "BBB", "CCC"))]
    assert [l.split()[0] for l in lines] == ["AAA", "BBB"]  # best score first, NO_DATA omitted
    assert "+0.500" in lines[0] and "-0.200" in lines[1]


def test_stage_monitor_appends_to_log(pn, capsys):
    pn.stage_monitor(FakeSP(), ["AAA", "BBB", "CCC"], "IN")
    log = json.loads(pn.MONITOR_LOG.read_text())
    assert [(r["ticker"], r["label"], r["n_articles"]) for r in log] == [("AAA", "POSITIVE", 3), ("BBB", "NEGATIVE", 2)]
    assert log[0]["market"] == "IN" and log[0]["sentiment"] == 0.5 and len(log[0]["date"]) == 10
    assert "Logged 2 tickers" in capsys.readouterr().out
    pn.stage_monitor(FakeSP(), ["AAA"], "US")
    assert len(json.loads(pn.MONITOR_LOG.read_text())) == 3  # appended, not overwritten
    assert "Total log: 3 rows" in capsys.readouterr().out


def _prices(n=70):
    idx = pd.date_range("2024-01-01", periods=n)
    return pd.DataFrame({"Close": 100.0 + np.arange(n, dtype=float)}, index=idx)


def test_load_prices_suffix_preference_and_corruption(pn):
    assert pn._load_prices("AAA", "IN").empty
    _prices().to_parquet(pn.CACHE_DIR / "AAA.parquet")
    assert len(pn._load_prices("AAA", "IN")) == 70  # falls back to the bare name
    _prices(10).to_parquet(pn.CACHE_DIR / "AAA.NS.parquet")
    assert len(pn._load_prices("AAA", "IN")) == 10  # .NS preferred for India
    assert len(pn._load_prices("AAA", "US")) == 70  # no suffix for US
    (pn.CACHE_DIR / "AAA.NS.parquet").write_text("junk")
    assert len(pn._load_prices("AAA", "IN")) == 70  # corrupt file skipped, next candidate used


def _log(pn, entries):
    pn.MONITOR_LOG.write_text(json.dumps(entries))


def test_stage_join_messages_without_log_or_market(pn, capsys):
    pn.stage_join("IN")
    assert "No monitor log yet" in capsys.readouterr().out
    _log(pn, [{"date": "2024-01-02", "ticker": "AAA", "market": "US", "sentiment": 0.1, "label": "POSITIVE", "n_articles": 1}])
    pn.stage_join("IN")
    assert "No IN entries in log." in capsys.readouterr().out
    pn.stage_join("US")
    assert "No matured entries yet" in capsys.readouterr().out  # no price file


def test_stage_join_forward_returns_and_correlation(pn, capsys):
    px = _prices(120)
    entries = []
    for i in range(12):
        tk = f"T{i}"
        close = px["Close"].to_numpy().copy()
        close[1 + 5 :] *= 1.0 + 0.01 * i  # forward return rises with i
        pd.DataFrame({"Close": close}, index=px.index).to_parquet(pn.CACHE_DIR / f"{tk}.NS.parquet")
        entries.append({"date": "2024-01-02", "ticker": tk, "market": "IN", "sentiment": 0.1 * i, "label": "POSITIVE", "n_articles": 2})
    _log(pn, entries)
    pn.stage_join("IN")
    out = capsys.readouterr().out
    assert "Joined 12 logged sentiments" in out
    csvs = list(pn.OUT_DIR.glob("news_price_join_IN_*.csv"))
    assert len(csvs) == 1
    df = pd.read_csv(csvs[0])
    base = px["Close"]
    pos = px.index.searchsorted(pd.Timestamp("2024-01-02"))
    row = df[df["ticker"] == "T0"].iloc[0]
    assert row["fwd_1d"] == pytest.approx((base.iloc[pos + 1] / base.iloc[pos] - 1) * 100)
    assert row["fwd_1wk"] == pytest.approx((base.iloc[pos + 5] / base.iloc[pos] - 1) * 100)
    corr_lines = {l.split()[0]: l for l in out.splitlines() if l.strip().startswith(("1d", "1wk", "1mo", "3mo"))}
    assert "+" in corr_lines["1wk"] and "12" in corr_lines["1wk"]  # correlation + N printed
    assert "need ≥10 matured" not in corr_lines["1wk"]


def test_stage_join_horizon_maturity_and_min_sample(pn, capsys):
    px = _prices(30)  # entry at pos 1; 1d/1wk/1mo matured (pos+21=22 < 30) but not 3mo (pos+63)
    entries = []
    for i in range(9):  # only 9 rows: below the 10-sample correlation floor
        pd.DataFrame({"Close": px["Close"] + i}, index=px.index).to_parquet(pn.CACHE_DIR / f"T{i}.NS.parquet")
        entries.append({"date": "2024-01-02", "ticker": f"T{i}", "market": "IN", "sentiment": 0.1 * i, "label": "X", "n_articles": 1})
    entries.append({"date": "2030-01-01", "ticker": "T0", "market": "IN", "sentiment": 0.0, "label": "X", "n_articles": 1})  # after the last bar
    _log(pn, entries)
    pn.stage_join("IN")
    df = pd.read_csv(next(pn.OUT_DIR.glob("news_price_join_IN_*.csv")))
    assert len(df) == 9 and df["fwd_3mo"].isna().all() and df["fwd_1mo"].notna().all()
    assert capsys.readouterr().out.count("(need ≥10 matured)") == 4


def test_stage_join_edge_on_last_bar_horizon(pn):
    px = _prices(8)  # pos 1: pos+5 = 6 < 8 matured; pos+7=8 not < 8
    pd.DataFrame({"Close": px["Close"]}, index=px.index).to_parquet(pn.CACHE_DIR / "T.NS.parquet")
    _log(pn, [{"date": "2024-01-02", "ticker": "T", "market": "IN", "sentiment": 0.1, "label": "X", "n_articles": 1}])
    pn.stage_join("IN")
    df = pd.read_csv(next(pn.OUT_DIR.glob("news_price_join_IN_*.csv")))
    assert df["fwd_1wk"].notna().all() and df["fwd_1mo"].isna().all()


def _run_main(pn, monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["pn", *argv])
    pn.main()


def test_main_dispatch(pn, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(pn, "stage_mood", lambda sp: calls.append("mood"))
    monkeypatch.setattr(pn, "stage_score", lambda sp, t, m: calls.append(("score", t, m)))
    monkeypatch.setattr(pn, "stage_monitor", lambda sp, t, m: calls.append(("monitor", len(t), m)))
    monkeypatch.setattr(pn, "stage_join", lambda m: calls.append(("join", m)))
    _run_main(pn, monkeypatch, [])
    assert calls == ["mood"]  # no tickers, no monitor -> mood only
    calls.clear()
    _run_main(pn, monkeypatch, ["--tickers", "AAA", "BBB", "--market", "US"])
    assert calls == [("score", ["AAA", "BBB"], "US")]
    calls.clear()
    _run_main(pn, monkeypatch, ["--mood", "--tickers", "AAA"])
    assert calls == ["mood", ("score", ["AAA"], "IN")]  # mood first, then per-ticker
    calls.clear()
    _run_main(pn, monkeypatch, ["--monitor"])
    assert calls == [("monitor", len(pn.NIFTY50), "IN")]  # default watchlist
    calls.clear()
    _run_main(pn, monkeypatch, ["--watchlist", "nifty50"])
    assert calls == [("score", pn.NIFTY50, "IN")]
    calls.clear()
    _run_main(pn, monkeypatch, ["--join", "--tickers", "AAA", "--mood"])
    assert calls == [("join", "IN")]  # join short-circuits everything else
    out = capsys.readouterr().out
    assert "PIPELINE 2 — NEWS-BASED ANALYSIS  |  IN" in out and "providers ok" in out


def test_main_mood_with_monitor_and_trailing_disclaimer(pn, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(pn, "stage_mood", lambda sp: calls.append("mood"))
    monkeypatch.setattr(pn, "stage_monitor", lambda sp, t, m: calls.append(("monitor", t, m)))
    _run_main(pn, monkeypatch, ["--mood", "--monitor", "--tickers", "ZZZ"])
    assert calls == ["mood", ("monitor", ["ZZZ"], "IN")]
    assert capsys.readouterr().out.rstrip().endswith("NOT investment advice.")


# ---------------- daily_price_collector ----------------
class FakeTicker:
    frames = {}
    calls = []

    def __init__(self, t):
        self.t = t

    def history(self, start, end):
        FakeTicker.calls.append((self.t, start, end))
        r = FakeTicker.frames.get(self.t)
        if isinstance(r, Exception):
            raise r
        return r if r is not None else pd.DataFrame()


@pytest.fixture
def dpc(monkeypatch):
    FakeTicker.frames, FakeTicker.calls = {}, []
    fake_module(monkeypatch, "yfinance", Ticker=FakeTicker)
    return fresh_import(monkeypatch, "daily_price_collector")


def _hist(n=300, start=100.0, step=1.0, seed=None):
    close = start + step * np.arange(n, dtype=float)
    if seed is not None:
        close = 100 * np.cumprod(1 + np.random.default_rng(seed).normal(0.0005, 0.01, n))
    return pd.DataFrame({"Close": close, "Volume": 1000.0}, index=pd.date_range("2020-01-01", periods=n, name="Date"))


def test_collector_window_and_sample(dpc):
    c = dpc.DailyPriceCollector()
    assert abs(((c.end_date - c.start_date).days) - 5 * 365) <= 1
    t60 = c.select_sample_companies()
    assert len(t60) == 60 and t60[0] == "MSFT" and t60[-1] == "BIIB" and len(set(t60)) == 60
    assert c.select_sample_companies(3) == ["MSFT", "NVDA", "CRM"]
    everything = c.select_sample_companies(1000)  # the hard-coded list is longer than the default cap
    assert len(everything) == 64 and everything[-1] == "GILD" and everything[:60] == t60


def test_fetch_price_data_features_and_thresholds(dpc):
    c = dpc.DailyPriceCollector()
    FakeTicker.frames = {"OK": _hist(150), "SHORT": _hist(99), "EXACT": _hist(100)}
    df = c.fetch_price_data("OK")
    assert FakeTicker.calls[0][1:] == (c.start_date, c.end_date)
    assert list(df.columns[:2]) == ["Date", "Close"] and df["ticker"].iloc[0] == "OK"
    assert df["returns"].iloc[1] == pytest.approx(1.0)  # (101/100 - 1) * 100
    assert df["log_returns"].iloc[1] == pytest.approx(np.log(101 / 100) * 100)
    assert df["volume_ma_20"].iloc[18] != df["volume_ma_20"].iloc[18] and df["volume_ma_20"].iloc[19] == 1000.0
    assert df["price_momentum_20"].iloc[20] == pytest.approx((120.0 / 100.0 - 1) * 100)
    assert df["volatility_20"].iloc[:20].isna().all() and not np.isnan(df["volatility_20"].iloc[21])
    assert c.fetch_price_data("SHORT").empty  # < 100 rows
    assert len(c.fetch_price_data("EXACT")) == 100  # boundary: 100 rows accepted
    assert c.fetch_price_data("MISSING").empty
    FakeTicker.frames["BOOM"] = RuntimeError("down")
    assert c.fetch_price_data("BOOM").empty


def test_collect_prices_counts(dpc, capsys):
    c = dpc.DailyPriceCollector()
    FakeTicker.frames = {"A": _hist(150), "B": _hist(120), "C": _hist(50)}
    out = c.collect_prices(["A", "B", "C"])
    assert set(out) == {"A", "B"}
    txt = capsys.readouterr().out
    assert "Collection complete: 2/3 companies" in txt and "Total records collected: 270" in txt
    assert "Average records per company: 135" in txt
    assert c.collect_prices([]) == {}
    assert "Average records per company: 0" in capsys.readouterr().out
    FakeTicker.frames = {}
    assert c.collect_prices(["Z"]) == {} and "Average records per company: 0" in capsys.readouterr().out


def _enriched(dpc, **frames):
    c = dpc.DailyPriceCollector()
    FakeTicker.frames = frames
    return c, c.collect_prices(list(frames))


def test_analyze_price_metrics_summaries(dpc, capsys):
    c, data = _enriched(dpc, A=_hist(150, seed=1), B=_hist(150, seed=2), C=_hist(150, start=500.0, step=-1.0))
    capsys.readouterr()
    assert c.analyze_price_metrics(data) is data
    txt = capsys.readouterr().out
    assert "Volatility (20-day rolling)" in txt and "Positive days ratio:" in txt
    pos = sum(1 for d in data.values() if d["returns"].mean() > 0)
    assert f"Positive momentum: {sum(1 for d in data.values() if d['price_momentum_20'].mean() > 0)} companies" in txt
    assert f"Negative momentum: {sum(1 for d in data.values() if d['price_momentum_20'].mean() < 0)} companies" in txt
    expect_ratio = pos / 3 * 100
    assert f"Positive days ratio: {expect_ratio:.1f}%" in txt
    c.analyze_price_metrics({})
    assert "Volatility (20-day rolling)" not in capsys.readouterr().out


def test_prepare_for_correlation_values_and_boundaries(dpc, capsys):
    long = _hist(800, seed=3)  # > 756 so every window exists
    mid = _hist(300, seed=4)  # 1y windows only
    short = _hist(120, seed=5)  # none of the multi-year windows
    c, data = _enriched(dpc, LONG=long, MID=mid, SHORT=short)
    capsys.readouterr()
    out = c.prepare_for_correlation(data).set_index("ticker")
    L = data["LONG"]
    assert out.loc["LONG", "latest_price"] == L["Close"].iloc[-1]
    assert out.loc["LONG", "price_5yr_cagr"] == pytest.approx(((L["Close"].iloc[-1] / L["Close"].iloc[0]) ** (1 / 5) - 1) * 100)
    assert out.loc["LONG", "total_return"] == pytest.approx((L["Close"].iloc[-1] / L["Close"].iloc[0] - 1) * 100)
    assert out.loc["LONG", "momentum_1y"] == pytest.approx(L["price_momentum_20"].tail(252).mean())
    assert out.loc["LONG", "momentum_2y"] == pytest.approx(L["price_momentum_20"].tail(504).mean())
    assert out.loc["LONG", "volatility_3y"] == pytest.approx(L["volatility_20"].tail(756).mean())
    assert out.loc["LONG", "sharpe_ratio"] == pytest.approx(L["returns"].mean() / L["returns"].std() * np.sqrt(252))
    dd = (L["Close"] - L["Close"].cummax()) / L["Close"].cummax() * 100
    assert out.loc["LONG", "max_drawdown"] == pytest.approx(dd.min()) and out.loc["LONG", "max_drawdown"] < 0
    assert out.loc["MID", "momentum_2y"] == 0 and out.loc["MID", "volatility_3y"] == 0 and out.loc["MID", "momentum_1y"] != 0
    assert out.loc["SHORT", "momentum_1y"] == 0 and out.loc["SHORT", "volatility_1y"] == 0
    txt = capsys.readouterr().out
    assert "Top performers by 5-year price CAGR" in txt


def test_prepare_for_correlation_skips_empty_and_zero_start(dpc, capsys):
    c = dpc.DailyPriceCollector()
    flat = _hist(150, step=0.0)
    flat["ticker"] = "FLAT"
    zero = _hist(150, start=0.0, step=1.0)
    zero["ticker"] = "ZERO"
    flat = flat.assign(returns=0.0, price_momentum_20=0.0, volatility_20=0.0)
    zero = zero.assign(returns=1.0, price_momentum_20=0.0, volatility_20=0.0)
    out = c.prepare_for_correlation({"E": pd.DataFrame(), "FLAT": flat, "ZERO": zero}).set_index("ticker")
    assert set(out.index) == {"FLAT", "ZERO"}
    assert out.loc["FLAT", "sharpe_ratio"] == 0  # zero return std -> 0, not NaN/inf
    assert out.loc["ZERO", "total_return"] == 0 and out.loc["ZERO", "price_5yr_cagr"] == 0  # non-positive base price
    capsys.readouterr()
    assert c.prepare_for_correlation({}).empty
    assert "Top performers" not in capsys.readouterr().out
