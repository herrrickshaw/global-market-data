"""sentiment_pipeline: scoring, providers (HTTP faked), RSS matching, aggregation, cache, CLI."""

import json
import sys
import types
from datetime import datetime, timedelta

import pytest

from tests.w4_helpers import fake_module, fresh_import


class FakeVader:
    """Tiny stand-in for VADER: compound = sum of lexicon hits / 10, clipped to [-1, 1]."""

    def __init__(self):
        self.lexicon = {"good": 1.0}

    def polarity_scores(self, text):
        tot = sum(self.lexicon.get(w, 0.0) for w in text.lower().replace(".", " ").split())
        return {"compound": max(-1.0, min(1.0, tot / 10))}


class FakeFeedparser:
    def __init__(self, by_url=None, boom_on=None):
        self.by_url = by_url or {}
        self.boom_on = boom_on
        self.calls = []

    def parse(self, url, agent=None):
        self.calls.append((url, agent))
        if url == self.boom_on:
            raise RuntimeError("feed down")
        return types.SimpleNamespace(entries=self.by_url.get(url, []))


class Clock:
    def __init__(self, now=1000.0):
        self.now = now
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def _load(monkeypatch, tmp_path, vader=True, feedparser=True, requests_ok=True):
    monkeypatch.chdir(tmp_path)
    if vader:
        v = types.ModuleType("vaderSentiment")
        vv = types.ModuleType("vaderSentiment.vaderSentiment")
        vv.SentimentIntensityAnalyzer = FakeVader
        v.vaderSentiment = vv
        monkeypatch.setitem(sys.modules, "vaderSentiment", v)
        monkeypatch.setitem(sys.modules, "vaderSentiment.vaderSentiment", vv)
    else:
        monkeypatch.setitem(sys.modules, "vaderSentiment", None)
        monkeypatch.setitem(sys.modules, "vaderSentiment.vaderSentiment", None)
    if feedparser:
        fake_module(monkeypatch, "feedparser", parse=lambda *a, **k: None)
    else:
        monkeypatch.setitem(sys.modules, "feedparser", None)
    if not requests_ok:
        monkeypatch.setitem(sys.modules, "requests", None)
    mod = fresh_import(monkeypatch, "sentiment_pipeline")
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "cache" / "sentiment_cache.json")
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()
    for k in ("MARKETAUX_KEY", "ALPHAVANTAGE_KEY", "FINNHUB_KEY", "NEWSDATA_KEY"):
        monkeypatch.delenv(k, raising=False)
    return mod


@pytest.fixture
def sp(monkeypatch, tmp_path):
    return _load(monkeypatch, tmp_path)


@pytest.fixture
def clock(sp, monkeypatch):
    c = Clock()
    monkeypatch.setattr(sp, "time", c)
    return c


def _http(sp, monkeypatch, payload=None, boom=False):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append((url, params, timeout))
        if boom:
            raise RuntimeError("net")
        return Resp(payload)

    monkeypatch.setattr(sp, "requests", types.SimpleNamespace(get=get))
    return seen


# ---------------------------------------------------------------- scoring / labels


def test_finance_lexicon_is_merged_into_vader(sp):
    assert sp._VADER_OK is True
    assert sp._VADER.lexicon["fraud"] == -3.0
    assert sp._VADER.lexicon["surge"] == 2.6
    assert sp._VADER.lexicon["good"] == 1.0  # pre-existing entries survive


def test_score_text_uses_vader_and_guards_empty(sp):
    assert sp.score_text("") == 0.0
    assert sp.score_text(None) == 0.0
    assert sp.score_text("stock will surge") == pytest.approx(0.26)
    assert sp.score_text("fraud fraud fraud fraud") == pytest.approx(-1.0)


def test_score_text_zero_without_vader(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, vader=False)
    assert m._VADER_OK is False and m._VADER is None
    assert m.score_text("huge surge") == 0.0


@pytest.mark.parametrize(
    "s,label",
    [
        (0.15, "POSITIVE"),
        (0.1499, "NEUTRAL"),
        (0.0, "NEUTRAL"),
        (-0.1499, "NEUTRAL"),
        (-0.15, "NEGATIVE"),
        (1.0, "POSITIVE"),
        (-1.0, "NEGATIVE"),
    ],
)
def test_label_of_boundaries(sp, s, label):
    assert sp.label_of(s) == label


def test_module_constants(sp):
    assert sp.CACHE_TTL_HOURS == 6
    assert "NOT investment advice" in sp.DISCLAIMER


# ---------------------------------------------------------------- base provider


def test_provider_available_needs_key_and_requests(sp, monkeypatch):
    p = sp.MarketauxProvider()
    assert p.api_key == "" and p.available is False
    monkeypatch.setenv("MARKETAUX_KEY", "k")
    p = sp.MarketauxProvider()
    assert p.api_key == "k" and p.available is True
    monkeypatch.setattr(sp, "_REQ_OK", False)
    assert p.available is False


def test_requests_missing_flag(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, requests_ok=False)
    assert m._REQ_OK is False


def test_throttle_sleeps_only_for_remaining_interval(sp, clock):
    p = sp.MarketauxProvider()  # min_interval 1.0, _last_call 0.0
    clock.now = 1000.0
    p._throttle()
    assert clock.slept == []  # long since last call
    assert p._last_call == 1000.0
    clock.now = 1000.25
    p._throttle()
    assert clock.slept == [pytest.approx(0.75)]
    clock.now = 1001.25  # exactly min_interval after the previous stamp
    p._throttle()
    assert len(clock.slept) == 1  # elapsed == interval -> no sleep


def test_provider_intervals(sp):
    assert sp.MarketauxProvider.min_interval == 1.0
    assert sp.AlphaVantageProvider.min_interval == 13.0
    assert sp.FinnhubProvider.min_interval == 1.1
    assert sp.NewsDataProvider.min_interval == 2.0
    assert sp.IndianRSSProvider.min_interval == 0.5


def test_providers_return_empty_without_key(sp, monkeypatch):
    seen = _http(sp, monkeypatch, {"data": [{"title": "x"}]})
    for cls in (
        sp.MarketauxProvider,
        sp.AlphaVantageProvider,
        sp.FinnhubProvider,
        sp.NewsDataProvider,
    ):
        assert cls().fetch_news("TCS") == []
    assert seen == []


# ---------------------------------------------------------------- Marketaux


def test_marketaux_parses_native_and_fallback_sentiment(sp, clock, monkeypatch):
    monkeypatch.setenv("MARKETAUX_KEY", "k")
    payload = {
        "data": [
            {
                "title": "A",
                "published_at": "t1",
                "url": "u1",
                "description": "d1",
                "entities": [
                    {"symbol": "OTHER.NS", "sentiment_score": -0.9},
                    {"symbol": "TCS.NS", "sentiment_score": 0.4},
                ],
            },
            {"title": "stock will surge", "entities": [{"symbol": "ZZ", "sentiment_score": 0.9}]},
            {"title": "native zero", "entities": [{"symbol": "TCS", "sentiment_score": 0.0}]},
            {"title": "no entities key"},
        ]
    }
    seen = _http(sp, monkeypatch, payload)
    arts = sp.MarketauxProvider().fetch_news("TCS", "IN")
    assert [a.sentiment for a in arts] == [0.4, pytest.approx(0.26), 0.0, 0.0]
    assert arts[0].source == "Marketaux" and arts[0].url == "u1" and arts[0].summary == "d1"
    url, params, timeout = seen[0]
    assert params["symbols"] == "TCS.NS" and params["api_token"] == "k" and timeout == 15
    assert params["limit"] == 10 and params["filter_entities"] == "true"
    assert clock.slept == []


def test_marketaux_us_symbol_and_errors(sp, clock, monkeypatch):
    monkeypatch.setenv("MARKETAUX_KEY", "k")
    seen = _http(sp, monkeypatch, {"data": []})
    assert sp.MarketauxProvider().fetch_news("AAPL", "US") == []
    assert seen[0][1]["symbols"] == "AAPL"
    _http(sp, monkeypatch, boom=True)
    assert sp.MarketauxProvider().fetch_news("AAPL", "US") == []


# ---------------------------------------------------------------- Alpha Vantage


def test_alphavantage_parses(sp, clock, monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_KEY", "k")
    payload = {
        "feed": [
            {
                "title": "t1",
                "time_published": "tp",
                "url": "u",
                "summary": "s",
                "overall_sentiment_score": "0.1",
                "ticker_sentiment": [
                    {"ticker": "NOPE", "ticker_sentiment_score": "0.9"},
                    {"ticker": "TCS.BSE", "ticker_sentiment_score": "-0.3"},
                ],
            },
            {"title": "t2", "overall_sentiment_score": "0.25", "ticker_sentiment": []},
            {"title": "t3", "overall_sentiment_score": None},
        ]
    }
    seen = _http(sp, monkeypatch, payload)
    arts = sp.AlphaVantageProvider().fetch_news("TCS", "IN")
    assert [a.sentiment for a in arts] == [-0.3, 0.25, 0.0]
    assert arts[0].published == "tp" and arts[0].summary == "s"
    assert seen[0][1]["tickers"] == "TCS.BSE" and seen[0][1]["function"] == "NEWS_SENTIMENT"
    sp.AlphaVantageProvider().fetch_news("AAPL", "US")
    assert seen[1][1]["tickers"] == "AAPL"


def test_alphavantage_error_isolated(sp, clock, monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_KEY", "k")
    _http(sp, monkeypatch, boom=True)
    assert sp.AlphaVantageProvider().fetch_news("X") == []


# ---------------------------------------------------------------- Finnhub


def test_finnhub_scores_headlines_and_caps_at_15(sp, clock, monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", "k")
    data = [{"headline": "surge", "summary": "good", "datetime": 123, "url": "u"}] * 20
    seen = _http(sp, monkeypatch, data)
    arts = sp.FinnhubProvider().fetch_news("TCS", "IN")
    assert len(arts) == 15
    assert arts[0].sentiment == pytest.approx(0.36)
    assert arts[0].published == "123" and arts[0].url == "u"
    params = seen[0][1]
    assert params["symbol"] == "TCS.NS" and params["token"] == "k"
    frm = datetime.strptime(params["from"], "%Y-%m-%d")
    to = datetime.strptime(params["to"], "%Y-%m-%d")
    assert to - frm == timedelta(days=7)
    sp.FinnhubProvider().fetch_news("AAPL", "US")
    assert seen[1][1]["symbol"] == "AAPL"


def test_finnhub_non_list_and_error(sp, clock, monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", "k")
    _http(sp, monkeypatch, {"error": "limit"})
    assert sp.FinnhubProvider().fetch_news("X") == []
    _http(sp, monkeypatch, boom=True)
    assert sp.FinnhubProvider().fetch_news("X") == []


# ---------------------------------------------------------------- NewsData


def test_newsdata_parses_and_handles_none_fields(sp, clock, monkeypatch):
    monkeypatch.setenv("NEWSDATA_KEY", "k")
    res = [
        {"title": "surge", "description": "good", "pubDate": "p", "link": "l"},
        {"title": None, "description": None},
    ] + [{"title": "x"}] * 20
    seen = _http(sp, monkeypatch, {"results": res})
    arts = sp.NewsDataProvider().fetch_news("TCS", "IN")
    assert len(arts) == 15
    assert arts[0].sentiment == pytest.approx(0.36)
    assert arts[0].published == "p" and arts[0].url == "l"
    assert arts[1].summary == "" and arts[1].sentiment == 0.0
    assert seen[0][1]["country"] == "in" and seen[0][1]["q"] == "TCS"
    sp.NewsDataProvider().fetch_news("AAPL", "US")
    assert seen[1][1]["country"] == "us"


def test_newsdata_error(sp, clock, monkeypatch):
    monkeypatch.setenv("NEWSDATA_KEY", "k")
    _http(sp, monkeypatch, boom=True)
    assert sp.NewsDataProvider().fetch_news("X") == []


# ---------------------------------------------------------------- RSS providers

IN_URL = "https://www.moneycontrol.com/rss/business.xml"
LM_URL = "https://www.livemint.com/rss/markets"


def _rss(sp, monkeypatch, by_url, boom_on=None, cls=None, name_lookup=None):
    fp = FakeFeedparser(by_url, boom_on)
    fake_module(monkeypatch, "feedparser", parse=fp.parse)
    fake_module(
        monkeypatch,
        "symbol_master",
        clean_name_for=name_lookup or (lambda t: ""),
    )
    monkeypatch.setattr(sp, "time", Clock())
    return (cls or sp.IndianRSSProvider)(), fp


def test_rss_available_without_key(sp):
    p = sp.IndianRSSProvider()
    assert p._fp_ok is True and p.available is True and p.env_key == ""


def test_rss_unavailable_without_feedparser(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, feedparser=False)
    p = m.IndianRSSProvider()
    assert p.available is False
    assert p.fetch_news("RELIANCE", "IN", "Reliance Industries") == []
    assert p.fetch_market_mood() == {}


def test_rss_all_entries_cached_and_failures_isolated(sp, monkeypatch):
    by = {
        IN_URL: [
            {"title": "T1", "summary": "S1", "link": "l", "published": "p"},
            {"title": "T2", "description": "D2"},
        ],
        LM_URL: [{"title": "T3"}],
    }
    p, fp = _rss(sp, monkeypatch, by, boom_on="https://www.moneycontrol.com/rss/results.xml")
    entries = p._all_entries()
    assert [e["title"] for e in entries] == ["T1", "T2", "T3"]
    assert entries[1]["summary"] == "D2" and entries[0]["outlet"] == "Moneycontrol"
    assert entries[2]["summary"] == "" and entries[2]["published"] == ""
    n_calls = len(fp.calls)
    assert n_calls == sum(len(v) for v in p.FEEDS.values())
    assert all(agent == p.UA for _, agent in fp.calls)
    assert p._feed_cache["https://www.moneycontrol.com/rss/results.xml"] == []
    p._all_entries()
    assert len(fp.calls) == n_calls  # served from cache


def test_rss_fetch_news_non_india_market(sp, monkeypatch):
    p, fp = _rss(sp, monkeypatch, {})
    assert p.fetch_news("RELIANCE", "US", "Reliance Industries") == []
    assert fp.calls == []


def test_rss_matches_company_phrase_not_group_company(sp, monkeypatch):
    by = {
        IN_URL: [
            {"title": "Adani Enterprises Q4 profit surge", "summary": "good", "link": "L"},
            {"title": "Adani Ports expands", "summary": ""},
            {"title": "Unrelated", "summary": "adani   enterprises hidden in summary"},
        ]
    }
    p, _ = _rss(sp, monkeypatch, by)
    arts = p.fetch_news("ADANIENT", "IN", "Adani Enterprises Ltd")
    assert [a.title for a in arts] == ["Adani Enterprises Q4 profit surge", "Unrelated"]
    assert arts[0].source == "IndianRSS:Moneycontrol"
    assert arts[0].sentiment == pytest.approx(0.51)
    assert arts[0].url == "L"


def test_rss_single_token_name_and_short_tokens_dropped(sp, monkeypatch):
    by = {IN_URL: [{"title": "Infosys wins deal", "summary": ""}, {"title": "no"}]}
    p, _ = _rss(sp, monkeypatch, by)
    assert len(p.fetch_news("INFY", "IN", "Infosys")) == 1
    # 2-letter tokens are ignored; falls back to ticker rule (INFY >= 4 chars)
    p2, _ = _rss(sp, monkeypatch, {IN_URL: [{"title": "INFY rises", "summary": ""}]})
    assert len(p2.fetch_news("INFY", "IN", "AB CD")) == 1


def test_rss_summary_truncated_and_results_capped(sp, monkeypatch):
    entries = [{"title": "Reliance news", "summary": "x" * 500} for _ in range(30)]
    p, _ = _rss(sp, monkeypatch, {IN_URL: entries})
    arts = p.fetch_news("RELIANCE", "IN", "Reliance")
    assert len(arts) == 15
    assert len(arts[0].summary) == 200


def test_rss_ticker_fallback_rules(sp, monkeypatch):
    by = {IN_URL: [{"title": "TCS and ABCD and GOLD news", "summary": ""}]}
    p, _ = _rss(sp, monkeypatch, by)
    assert p.fetch_news("TCS", "IN") == []  # < 4 chars
    assert p.fetch_news("GOLD", "IN") == []  # common word
    assert len(p.fetch_news("ABCD", "IN")) == 1
    assert len(p.fetch_news(" abcd ", "IN")) == 1  # stripped + uppercased


def test_rss_name_lookup_used_and_failures_swallowed(sp, monkeypatch):
    by = {IN_URL: [{"title": "Tata Motors jumps", "summary": ""}]}
    p, _ = _rss(sp, monkeypatch, by, name_lookup=lambda t: "Tata Motors")
    assert len(p.fetch_news("TATAMOTORS", "IN")) == 1

    def boom(t):
        raise RuntimeError("no master")

    p2, _ = _rss(sp, monkeypatch, by, name_lookup=boom)
    assert len(p2.fetch_news("TATA", "IN")) == 1  # lookup raised -> ticker word-match fallback
    # symbol_master import itself failing
    monkeypatch.setitem(sys.modules, "symbol_master", None)
    assert len(p2.fetch_news("MOTORS", "IN")) == 1


def test_rss_market_mood(sp, monkeypatch):
    by = {
        IN_URL: [
            {"title": "surge", "summary": "good"},  # 0.36
            {"title": "fraud", "summary": ""},  # -0.3
            {"title": "calm", "summary": ""},  # 0 -> excluded from mean
        ]
    }
    p, _ = _rss(sp, monkeypatch, by)
    m = p.fetch_market_mood()
    assert m["score"] == pytest.approx(0.03)
    assert m["mood"] == "NEUTRAL" and m["n_articles"] == 3
    assert m["by_outlet"]["Moneycontrol"] == 4
    assert set(m["by_outlet"]) == set(p.FEEDS)


def test_rss_market_mood_empty_and_all_zero(sp, monkeypatch):
    p, _ = _rss(sp, monkeypatch, {})
    assert p.fetch_market_mood() == {}
    p2, _ = _rss(sp, monkeypatch, {IN_URL: [{"title": "calm", "summary": ""}] * 2})
    assert p2.fetch_market_mood() == {"mood": "NEUTRAL", "score": 0.0, "n": 2}


def test_us_rss_market_gating_and_mood(sp, monkeypatch):
    cnbc = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
    by = {cnbc: [{"title": "Apple surge", "summary": "good"}]}
    p, _ = _rss(sp, monkeypatch, by, cls=sp.USRSSProvider)
    assert p.name == "USRSS"
    assert p.fetch_news("AAPL", "IN", "Apple") == []
    arts = p.fetch_news("AAPL", "US", "Apple")
    assert len(arts) == 1 and arts[0].source == "USRSS:CNBC"
    m = p.fetch_market_mood()
    assert m["market"] == "US" and m["mood"] == "POSITIVE"
    p_empty, _ = _rss(sp, monkeypatch, {}, cls=sp.USRSSProvider)
    assert p_empty.fetch_market_mood() == {}


# ---------------------------------------------------------------- pipeline


class Stub(object):
    def __init__(self, name, arts=None, boom=False):
        self.name = name
        self.arts = arts or []
        self.calls = []

    def fetch_news(self, ticker, market="IN"):
        self.calls.append((ticker, market))
        return [type(a)(**a.__dict__) for a in self.arts]


def _art(sp, title, sent, source="s"):
    return sp.Article(title=title, source=source, published="p", sentiment=sent)


def test_pipeline_init_active_and_status(sp, monkeypatch):
    pipe = sp.SentimentPipeline()
    names = [p.name for p in pipe.active]
    assert names == ["IndianRSS", "USRSS"]  # keyless only
    assert pipe.status() == "Active providers: IndianRSS, USRSS"
    assert pipe._rss_in.name == "IndianRSS" and pipe._rss_us.name == "USRSS"
    monkeypatch.setenv("FINNHUB_KEY", "k")
    monkeypatch.setenv("MARKETAUX_KEY", "k")
    assert [p.name for p in sp.SentimentPipeline().active][:2] == ["Marketaux", "Finnhub"]
    pipe.active = []
    assert pipe.status().startswith("No providers active")
    assert "FINNHUB_KEY" in pipe.status()


def test_pipeline_without_feedparser_has_no_rss(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, feedparser=False)
    pipe = m.SentimentPipeline()
    assert pipe.active == [] and pipe._rss_in is None and pipe._rss_us is None
    assert pipe.get_market_mood("IN") == {} and pipe.get_market_mood("US") == {}


def test_get_market_mood_routes_by_market(sp):
    pipe = sp.SentimentPipeline()
    pipe._rss_in = types.SimpleNamespace(fetch_market_mood=lambda: {"m": "in"})
    pipe._rss_us = types.SimpleNamespace(fetch_market_mood=lambda: {"m": "us"})
    assert pipe.get_market_mood() == {"m": "in"}
    assert pipe.get_market_mood("IN") == {"m": "in"}
    assert pipe.get_market_mood("US") == {"m": "us"}


def test_cache_load_missing_corrupt_and_valid(sp, tmp_path):
    pipe = sp.SentimentPipeline()
    assert pipe._cache == {}
    sp.CACHE_FILE.write_text("{not json")
    assert sp.SentimentPipeline()._cache == {}
    sp.CACHE_FILE.write_text(json.dumps({"k": 1}))
    assert sp.SentimentPipeline()._cache == {"k": 1}


def test_is_fresh_ttl_boundaries(sp):
    pipe = sp.SentimentPipeline()
    assert pipe._is_fresh("nope") is False
    now = datetime.now()
    pipe._cache["a"] = {"fetched_at": (now - timedelta(hours=5, minutes=59)).isoformat()}
    pipe._cache["b"] = {"fetched_at": (now - timedelta(hours=6, minutes=1)).isoformat()}
    pipe._cache["c"] = {"fetched_at": "garbage"}
    pipe._cache["d"] = {}
    assert pipe._is_fresh("a") is True
    assert pipe._is_fresh("b") is False
    assert pipe._is_fresh("c") is False
    assert pipe._is_fresh("d") is False


def test_ticker_sentiment_weighted_aggregation_and_cache(sp):
    pipe = sp.SentimentPipeline()
    s1 = Stub("Marketaux", [_art(sp, "a", 0.5), _art(sp, "b", 5.0)])  # clamps to 1.0
    s2 = Stub("NewsData", [_art(sp, "c", -0.5)])
    s3 = Stub("Empty", [])
    pipe.active = [s1, s2, s3]
    ts = pipe.get_ticker_sentiment("TCS", "IN")
    # weights 1.2,1.2,0.8 -> (0.6 + 1.2 - 0.4)/3.2
    assert ts.score == pytest.approx(round(1.4 / 3.2, 4))
    assert ts.label == "POSITIVE" and ts.n_articles == 3
    assert ts.providers == ["Marketaux", "NewsData"]
    assert [h["title"] for h in ts.top_headlines] == ["b", "a", "c"]
    assert ts.top_headlines[0]["sentiment"] == 1.0
    assert s1.calls == [("TCS", "IN")]
    # cached on disk and reused
    assert "IN:TCS" in json.loads(sp.CACHE_FILE.read_text())
    again = pipe.get_ticker_sentiment("TCS", "IN")
    assert again.score == ts.score and len(s1.calls) == 1
    assert again.providers == ts.providers and again.fetched_at == ts.fetched_at


def test_ticker_sentiment_unknown_provider_weight_defaults_to_one(sp):
    pipe = sp.SentimentPipeline()
    pipe.active = [Stub("Mystery", [_art(sp, "a", 0.3), _art(sp, "b", 0.1)])]
    assert pipe.get_ticker_sentiment("X").score == pytest.approx(0.2)


def test_ticker_sentiment_headlines_truncated_top5_and_negative_clamp(sp):
    pipe = sp.SentimentPipeline()
    arts = [_art(sp, "T" * 120 + str(i), -0.1 * i, "src") for i in range(1, 8)]
    arts.append(_art(sp, "neg", -9.0))
    pipe.active = [Stub("Finnhub", arts)]
    ts = pipe.get_ticker_sentiment("X", "US")
    assert len(ts.top_headlines) == 5
    assert ts.top_headlines[0]["title"] == "neg" and ts.top_headlines[0]["sentiment"] == -1.0
    assert all(len(h["title"]) <= 90 for h in ts.top_headlines)
    assert ts.top_headlines[1]["sentiment"] == -0.7
    assert ts.label == "NEGATIVE"
    assert "US:X" in pipe._cache


def test_ticker_sentiment_no_data_is_not_cached(sp):
    pipe = sp.SentimentPipeline()
    pipe.active = [Stub("Marketaux", [])]
    ts = pipe.get_ticker_sentiment("Z")
    assert (ts.score, ts.label, ts.n_articles, ts.providers) == (0.0, "NO_DATA", 0, [])
    assert pipe._cache == {} and not sp.CACHE_FILE.exists()


def test_ticker_sentiment_expired_cache_refetches(sp):
    pipe = sp.SentimentPipeline()
    old = (datetime.now() - timedelta(hours=7)).isoformat()
    pipe._cache["IN:T"] = {
        "score": 0.9,
        "label": "POSITIVE",
        "n_articles": 1,
        "providers": [],
        "top_headlines": [],
        "fetched_at": old,
    }
    stub = Stub("Finnhub", [_art(sp, "a", -0.5)])
    pipe.active = [stub]
    ts = pipe.get_ticker_sentiment("T")
    assert ts.score == -0.5 and len(stub.calls) == 1


def test_get_batch_prints_progress_every_ten_and_at_end(sp, capsys):
    pipe = sp.SentimentPipeline()
    pipe.active = [Stub("Finnhub", [_art(sp, "a", 0.5)])]
    tickers = [f"T{i}" for i in range(12)]
    res = pipe.get_batch(tickers, "US")
    assert list(res) == tickers
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "POSITIVE:" in ln]
    assert lines == [
        "  10/12 | POSITIVE:10 NEGATIVE:0",
        "  12/12 | POSITIVE:12 NEGATIVE:0",
    ]


def test_get_batch_counts_negative(sp, capsys):
    pipe = sp.SentimentPipeline()
    pipe.active = [Stub("Finnhub", [_art(sp, "a", -0.5)])]
    pipe.get_batch(["A"])
    assert "  1/1 | POSITIVE:0 NEGATIVE:1" in capsys.readouterr().out


# ---------------------------------------------------------------- CLI


def test_main_demo_mode_without_providers(sp, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["sp", "--tickers", "TCS"])
    monkeypatch.setattr(sp.SentimentPipeline, "__init__", _no_providers)
    sp.main()
    out = capsys.readouterr().out
    assert "Demo mode" in out and "YESBANK" in out and "VADER scoring: available" in out
    assert "Set the API keys" in out


def _no_providers(self):
    self.active = []
    self._cache = {}


def test_main_demo_reports_missing_vader(monkeypatch, tmp_path, capsys):
    m = _load(monkeypatch, tmp_path, vader=False)
    monkeypatch.setattr(sys, "argv", ["sp", "--tickers", "TCS"])
    monkeypatch.setattr(m.SentimentPipeline, "__init__", _no_providers)
    m.main()
    assert "MISSING" in capsys.readouterr().out


def test_main_runs_batch_and_writes_json(sp, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["sp", "--tickers", "AAA", "BBB", "--market", "US"])
    stub = Stub("Finnhub", [_art(sp, "h" * 100, 0.5, "Finnhub")])

    def init(self):
        self.active = [stub]
        self._cache = {}

    monkeypatch.setattr(sp.SentimentPipeline, "__init__", init)
    sp.main()
    out = capsys.readouterr().out
    assert "AAA" in out and "BBB" in out and "POSITIVE" in out
    files = list((tmp_path / "out").glob("sentiment_US_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert set(data) == {"AAA", "BBB"} and data["AAA"]["n_articles"] == 1
    assert stub.calls == [("AAA", "US"), ("BBB", "US")]


def test_main_rejects_bad_market(sp, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sp", "--tickers", "A", "--market", "EU"])
    with pytest.raises(SystemExit):
        sp.main()
