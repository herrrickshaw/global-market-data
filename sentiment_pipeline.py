# sentiment_pipeline.py
# =====================
# Multi-source news sentiment ingestion pipeline.
#
# LITERATURE BASIS
#   Sharma et al. (IJIRTM 2025) and the broader DL survey: hybrid models that
#   combine MARKET data (prices) with TEXTUAL sentiment consistently outperform
#   price-only models. This pipeline produces the sentiment feature stream that
#   feeds the screeners / ML models, turning them into hybrid models.
#
# PROVIDERS (free tiers — set API keys via env vars)
#   ┌──────────────┬────────────────┬──────────────────────────┬──────────────────────────┐
#   │ Provider     │ Free quota     │ Key advantage            │ Best for                 │
#   ├──────────────┼────────────────┼──────────────────────────┼──────────────────────────┤
#   │ Marketaux    │ 100 req/day    │ 80+ global markets        │ Global sentiment models  │
#   │ Alpha Vantage│ 25 req/day     │ 200k+ ticker mapping      │ Small algo-trading bots  │
#   │ Finnhub      │ 60 req/minute  │ Generous rate limit       │ Portfolio tracking apps  │
#   │ NewsData.io  │ 1,000 req/month│ Multi-language network    │ Macroeconomic monitoring │
#   └──────────────┴────────────────┴──────────────────────────┴──────────────────────────┘
#
# DESIGN
#   - Provider abstraction: each source implements fetch_news(ticker) → [Article].
#   - Rate-limit aware: per-provider token buckets respect the free-tier quotas.
#   - Sentiment scoring: use the provider's own score when given; else VADER
#     on the headline+summary (finance-tuned lexicon boost).
#   - Aggregation: per-ticker daily sentiment = quota-weighted mean of articles.
#   - Cache: results saved to sentiment_cache.json (TTL 6h) to conserve quota.
#   - Graceful: missing API key → that provider is skipped, others still run.
#
# SETUP (free keys)
#   export MARKETAUX_KEY=...      # https://www.marketaux.com
#   export ALPHAVANTAGE_KEY=...   # https://www.alphavantage.co
#   export FINNHUB_KEY=...        # https://finnhub.io
#   export NEWSDATA_KEY=...       # https://newsdata.io
#
# USAGE
#   python sentiment_pipeline.py --tickers RELIANCE TCS INFY
#   python sentiment_pipeline.py --tickers AAPL MSFT --market US
#   from sentiment_pipeline import SentimentPipeline
#   sp = SentimentPipeline(); score = sp.get_ticker_sentiment("RELIANCE", "IN")
#
# ⚠️ News sentiment is noisy and provider-dependent. Educational use only. NOT advice.

from __future__ import annotations

import argparse
import json
import os
import time
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

warnings.filterwarnings("ignore")

try:
    import requests

    _REQ_OK = True
except ImportError:
    _REQ_OK = False

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    _VADER = SentimentIntensityAnalyzer()
    # Finance-specific lexicon boosts (VADER is general-purpose)
    _VADER.lexicon.update(
        {
            "beat": 2.5,
            "beats": 2.5,
            "surpass": 2.3,
            "upgrade": 2.5,
            "upgraded": 2.5,
            "outperform": 2.4,
            "rally": 2.2,
            "surge": 2.6,
            "soar": 2.8,
            "jump": 1.8,
            "record": 1.8,
            "profit": 1.5,
            "growth": 1.6,
            "bullish": 2.6,
            "buyback": 1.8,
            "dividend": 1.2,
            "expansion": 1.5,
            "miss": -2.5,
            "misses": -2.5,
            "downgrade": -2.5,
            "downgraded": -2.5,
            "underperform": -2.4,
            "plunge": -2.8,
            "slump": -2.4,
            "crash": -3.0,
            "loss": -1.8,
            "losses": -2.0,
            "bearish": -2.6,
            "lawsuit": -2.0,
            "fraud": -3.0,
            "default": -2.8,
            "downturn": -2.2,
            "layoff": -2.0,
            "layoffs": -2.2,
            "probe": -1.8,
            "warning": -1.6,
        }
    )
    _VADER_OK = True
except ImportError:
    _VADER = None
    _VADER_OK = False

CACHE_FILE = Path.home() / "Downloads" / "market_cache" / "sentiment_cache.json"
OUT_DIR = Path("./sentiment_results")
OUT_DIR.mkdir(exist_ok=True)
CACHE_TTL_HOURS = 6

DISCLAIMER = (
    "⚠️  News sentiment is noisy, provider-dependent, and may lag or "
    "lead price. Educational/research only. NOT investment advice."
)


# ══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class Article:
    """One news item with a sentiment score in [-1, +1]."""

    title: str
    source: str  # provider name
    published: str
    sentiment: float  # -1 (very negative) … +1 (very positive)
    url: str = ""
    summary: str = ""


@dataclass
class TickerSentiment:
    """Aggregated sentiment for one ticker."""

    ticker: str
    score: float  # weighted mean sentiment [-1, +1]
    label: str  # POSITIVE / NEUTRAL / NEGATIVE
    n_articles: int
    providers: list = field(default_factory=list)
    top_headlines: list = field(default_factory=list)
    fetched_at: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# SENTIMENT SCORING
# ══════════════════════════════════════════════════════════════════════════════


def score_text(text: str) -> float:
    """Score a headline/summary in [-1, +1]. VADER with finance lexicon boost."""
    if not text or not _VADER_OK:
        return 0.0
    return _VADER.polarity_scores(text)["compound"]


def label_of(score: float) -> str:
    if score >= 0.15:
        return "POSITIVE"
    if score <= -0.15:
        return "NEGATIVE"
    return "NEUTRAL"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER ABSTRACTION (one adapter per news API)
# ══════════════════════════════════════════════════════════════════════════════


class NewsProvider(ABC):
    """Base class — each provider implements fetch_news()."""

    name: str = "base"
    env_key: str = ""
    min_interval: float = 1.0  # seconds between calls (rate limit)

    def __init__(self):
        self.api_key = os.environ.get(self.env_key, "")
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        return bool(self.api_key) and _REQ_OK

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()

    @abstractmethod
    def fetch_news(self, ticker: str, market: str = "IN") -> List[Article]: ...


class MarketauxProvider(NewsProvider):
    """Marketaux — 100 req/day, 80+ global markets, native sentiment."""

    name = "Marketaux"
    env_key = "MARKETAUX_KEY"
    min_interval = 1.0

    def fetch_news(self, ticker: str, market: str = "IN") -> List[Article]:
        if not self.available:
            return []
        self._throttle()
        sym = f"{ticker}.NS" if market == "IN" else ticker
        try:
            r = requests.get(
                "https://api.marketaux.com/v1/news/all",
                params={
                    "symbols": sym,
                    "filter_entities": "true",
                    "language": "en",
                    "api_token": self.api_key,
                    "limit": 10,
                },
                timeout=15,
            )
            data = r.json().get("data", [])
            out = []
            for a in data:
                # Marketaux gives per-entity sentiment_score in [-1,1]
                ent = a.get("entities", [{}])
                sent = next(
                    (
                        e.get("sentiment_score")
                        for e in ent
                        if e.get("symbol", "").startswith(ticker)
                    ),
                    None,
                )
                sent = sent if sent is not None else score_text(a.get("title", ""))
                out.append(
                    Article(
                        title=a.get("title", ""),
                        source=self.name,
                        published=a.get("published_at", ""),
                        sentiment=float(sent),
                        url=a.get("url", ""),
                        summary=a.get("description", ""),
                    )
                )
            return out
        except Exception:
            return []


class AlphaVantageProvider(NewsProvider):
    """Alpha Vantage — 25 req/day, 200k+ tickers, native sentiment scores."""

    name = "AlphaVantage"
    env_key = "ALPHAVANTAGE_KEY"
    min_interval = 13.0  # 25/day ≈ very sparse; also 5/min hard cap

    def fetch_news(self, ticker: str, market: str = "IN") -> List[Article]:
        if not self.available:
            return []
        self._throttle()
        sym = f"{ticker}.BSE" if market == "IN" else ticker
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "tickers": sym,
                    "apikey": self.api_key,
                    "limit": 10,
                },
                timeout=15,
            )
            feed = r.json().get("feed", [])
            out = []
            for a in feed:
                # AV provides ticker_sentiment_score per ticker
                ts = a.get("ticker_sentiment", [])
                sent = next(
                    (
                        float(t["ticker_sentiment_score"])
                        for t in ts
                        if t.get("ticker", "").startswith(ticker)
                    ),
                    None,
                )
                sent = sent if sent is not None else float(a.get("overall_sentiment_score", 0) or 0)
                out.append(
                    Article(
                        title=a.get("title", ""),
                        source=self.name,
                        published=a.get("time_published", ""),
                        sentiment=sent,
                        url=a.get("url", ""),
                        summary=a.get("summary", ""),
                    )
                )
            return out
        except Exception:
            return []


class FinnhubProvider(NewsProvider):
    """Finnhub — 60 req/minute (generous), company news."""

    name = "Finnhub"
    env_key = "FINNHUB_KEY"
    min_interval = 1.1  # 60/min

    def fetch_news(self, ticker: str, market: str = "IN") -> List[Article]:
        if not self.available:
            return []
        self._throttle()
        # Finnhub uses bare US tickers; Indian coverage limited (skip .NS mapping)
        sym = ticker if market == "US" else f"{ticker}.NS"
        today = datetime.today()
        frm = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        to = today.strftime("%Y-%m-%d")
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": sym,
                    "from": frm,
                    "to": to,
                    "token": self.api_key,
                },
                timeout=15,
            )
            data = r.json()
            out = []
            for a in (data if isinstance(data, list) else [])[:15]:
                txt = a.get("headline", "") + ". " + a.get("summary", "")
                out.append(
                    Article(
                        title=a.get("headline", ""),
                        source=self.name,
                        published=str(a.get("datetime", "")),
                        sentiment=score_text(txt),  # Finnhub free = no sentiment
                        url=a.get("url", ""),
                        summary=a.get("summary", ""),
                    )
                )
            return out
        except Exception:
            return []


class NewsDataProvider(NewsProvider):
    """NewsData.io — 1,000 req/month, multi-language, macro coverage."""

    name = "NewsData"
    env_key = "NEWSDATA_KEY"
    min_interval = 2.0

    def fetch_news(self, ticker: str, market: str = "IN") -> List[Article]:
        if not self.available:
            return []
        self._throttle()
        country = "in" if market == "IN" else "us"
        try:
            r = requests.get(
                "https://newsdata.io/api/1/news",
                params={
                    "apikey": self.api_key,
                    "q": ticker,
                    "country": country,
                    "category": "business",
                    "language": "en",
                },
                timeout=15,
            )
            results = r.json().get("results", [])
            out = []
            for a in results[:15]:
                txt = (a.get("title", "") or "") + ". " + (a.get("description", "") or "")
                out.append(
                    Article(
                        title=a.get("title", ""),
                        source=self.name,
                        published=a.get("pubDate", ""),
                        sentiment=score_text(txt),  # score via VADER
                        url=a.get("link", ""),
                        summary=a.get("description", "") or "",
                    )
                )
            return out
        except Exception:
            return []


# Tickers that are also common English/market words — matching them on the
# ticker alone produces false positives (e.g. DOLLAR↔"dollar", STAR↔"star").
# These require a company name to match a headline.
_COMMON_WORD_TICKERS = {
    "DOLLAR",
    "STAR",
    "WORTH",
    "BENCHMARK",
    "SMALLCAP",
    "MARK",
    "WHEELS",
    "NATURAL",
    "ICON",
    "WORLD",
    "POWER",
    "ACE",
    "GOLD",
    "SILVER",
    "ANG",
    "BIL",
    "TIL",
    "MIL",
    "SEL",
    "DEN",
    "RAL",
    "NH",
    "LAL",
    "STEL",
    "WEL",
    "SETL",
    "HIGH",
    "ADVANCE",
    "CONTROL",
    "WORLD",
    "EMERGENT",
    "RESPONSE",
    "DOLLAR",
    "ALPHA",
    "GLOBAL",
    "VECTOR",
    "BENCHMARK",
}


class IndianRSSProvider(NewsProvider):
    """
    Indian financial-news via free RSS feeds — NO API key required.
    Sources: Moneycontrol, Economic Times (ET), BusinessLine / LiveMint.

    Unlike the API providers, RSS feeds carry general market news rather than
    per-ticker streams. We therefore:
      - per ticker: keep entries whose title/summary mention the symbol or a
        cleaned company-name token (e.g. "RELIANCE", "Reliance")
      - score each matched headline with the finance-tuned VADER
    Also exposes market-wide feed sentiment via fetch_market_mood().
    """

    name = "IndianRSS"
    env_key = ""  # no key needed
    min_interval = 0.5

    # Browser UA — several outlets 403 an unidentified client.
    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    )

    FEEDS = {
        "Moneycontrol": [
            "https://www.moneycontrol.com/rss/business.xml",
            "https://www.moneycontrol.com/rss/results.xml",
            "https://www.moneycontrol.com/rss/marketreports.xml",
            "https://www.moneycontrol.com/rss/economy.xml",
        ],
        "EconomicTimes": [
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        ],
        "BusinessLine": [
            "https://www.thehindubusinessline.com/markets/feeder/default.rss",
            "https://www.thehindubusinessline.com/companies/feeder/default.rss",
        ],
        "LiveMint": [
            "https://www.livemint.com/rss/markets",
            "https://www.livemint.com/rss/companies",
        ],
        "NDTVProfit": [
            "https://feeds.feedburner.com/ndtvprofit-latest",
        ],
    }

    def __init__(self):
        super().__init__()
        try:
            import feedparser  # noqa

            self._fp_ok = True
        except ImportError:
            self._fp_ok = False
        self._feed_cache = {}  # url -> entries (within a run)

    @property
    def available(self) -> bool:
        return self._fp_ok  # RSS needs no key, just feedparser

    def _all_entries(self) -> list:
        """Fetch + cache all feed entries once per run (market-wide pool)."""
        if self._feed_cache:
            return [e for v in self._feed_cache.values() for e in v]
        import feedparser

        for outlet, urls in self.FEEDS.items():
            for url in urls:
                try:
                    self._throttle()
                    d = feedparser.parse(url, agent=self.UA)
                    self._feed_cache[url] = [
                        {
                            "title": e.get("title", ""),
                            "summary": e.get("summary", e.get("description", "")),
                            "link": e.get("link", ""),
                            "published": e.get("published", ""),
                            "outlet": outlet,
                        }
                        for e in d.entries
                    ]
                except Exception:
                    self._feed_cache[url] = []
        return [e for v in self._feed_cache.values() for e in v]

    def fetch_news(self, ticker: str, market: str = "IN", company_name: str = "") -> List[Article]:
        """
        Match feed entries by COMPANY NAME (preferred) rather than ticker.

        If a company_name is supplied (from the symbol-master parquet), match the
        full multi-word name root as a phrase — "ADANI ENTERPRISES" matches
        "Adani Enterprises Q4…" but NOT "Adani Ports". This fixes both the
        recall problem (tickers ≠ headline names) and precision (group companies).

        Falls back to ticker word-boundary matching only when no name is given
        and the ticker is neither too short nor a common English word.
        """
        import re

        if not self._fp_ok or market != "IN":
            return []

        # If caller didn't pass a name, look it up from the symbol master.
        if not company_name:
            try:
                from symbol_master import clean_name_for

                company_name = clean_name_for(ticker)
            except Exception:
                company_name = ""

        pattern = None
        if company_name:
            # Match the cleaned multi-word name as a phrase (allow flexible spacing).
            toks = [re.escape(t) for t in company_name.upper().split() if len(t) >= 3]
            if toks:
                # Require the first ≥2 distinctive tokens adjacent (or the single
                # token if that's all there is) — precise company-name phrase match.
                if len(toks) >= 2:
                    phrase = r"\b" + r"\s+".join(toks[:2]) + r"\b"
                else:
                    phrase = r"\b" + toks[0] + r"\b"
                pattern = re.compile(phrase)

        if pattern is None:
            # Fallback: ticker word-boundary (skip short / common-word tickers)
            tk = ticker.upper().strip()
            if len(tk) >= 4 and tk not in _COMMON_WORD_TICKERS:
                pattern = re.compile(r"\b" + re.escape(tk) + r"\b")
            else:
                return []

        out = []
        for e in self._all_entries():
            up = f"{e['title']} {e['summary']}".upper()
            if pattern.search(up):
                out.append(
                    Article(
                        title=e["title"],
                        source=f"{self.name}:{e['outlet']}",
                        published=e["published"],
                        sentiment=score_text(f"{e['title']}. {e['summary']}"),
                        url=e["link"],
                        summary=e["summary"][:200],
                    )
                )
        return out[:15]

    def fetch_market_mood(self) -> dict:
        """Overall sentiment of the whole Indian market news flow (regime gauge)."""
        if not self._fp_ok:
            return {}
        entries = self._all_entries()
        if not entries:
            return {}
        scores = [score_text(f"{e['title']}. {e['summary']}") for e in entries]
        scores = [s for s in scores if s != 0]
        if not scores:
            return {"mood": "NEUTRAL", "score": 0.0, "n": len(entries)}
        avg = sum(scores) / len(scores)
        return {
            "mood": label_of(avg),
            "score": round(avg, 3),
            "n_articles": len(entries),
            "by_outlet": {o: len(self.FEEDS[o]) for o in self.FEEDS},
        }


class USRSSProvider(IndianRSSProvider):
    """
    US financial-news via free RSS feeds — NO API key required.
    Sources: CNBC (top news, markets, finance) + MarketWatch (top stories,
    market pulse, real-time, bulletins). Same name-based matching as the Indian
    provider (company name looked up from the symbol master).
    """

    name = "USRSS"
    FEEDS = {
        "CNBC": [
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",  # top news
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",  # markets
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",  # finance
        ],
        "MarketWatch": [
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
            "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
            "https://feeds.content.dowjones.io/public/rss/mw_bulletins",
        ],
        "WSJ": [
            "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
        ],
        "YahooFinance": [
            "https://finance.yahoo.com/news/rssindex",
        ],
        "SeekingAlpha": [
            "https://seekingalpha.com/market_currents.xml",
        ],
        "Nasdaq": [
            "https://www.nasdaq.com/feed/rssoutbound?category=Markets",
        ],
        "Benzinga": [
            "https://www.benzinga.com/feed",
        ],
        "Investing": [
            "https://www.investing.com/rss/news_25.rss",
        ],
    }

    def fetch_news(self, ticker: str, market: str = "US", company_name: str = "") -> List[Article]:
        # Reuse the parent's name-based matching but for the US market/feeds.
        if market != "US":
            return []
        # Temporarily treat as IN so the parent's market!="IN" guard passes,
        # then run the same matching logic against the US feeds.
        return super().fetch_news(ticker, market="IN", company_name=company_name)

    def fetch_market_mood(self) -> dict:
        m = super().fetch_market_mood()
        if m:
            m["market"] = "US"
        return m


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════


class SentimentPipeline:
    """
    Orchestrates all providers, aggregates per-ticker sentiment, caches results.

    Provider quota weights (more generous/reliable sources weighted higher when
    averaging multiple providers' scores for the same ticker).
    """

    PROVIDER_WEIGHT = {
        "Marketaux": 1.2,  # native sentiment, global
        "AlphaVantage": 1.2,  # native sentiment
        "Finnhub": 1.0,  # headline-scored, generous quota
        "NewsData": 0.8,  # headline-scored, macro
        "IndianRSS": 1.1,  # Moneycontrol/ET/BusinessLine — free, India-native
        "USRSS": 1.1,  # CNBC/MarketWatch — free, US-native
    }

    def __init__(self):
        self.providers = [
            MarketauxProvider(),
            AlphaVantageProvider(),
            FinnhubProvider(),
            NewsDataProvider(),
            IndianRSSProvider(),  # free, no key — India sources
            USRSSProvider(),
        ]  # free, no key — US sources (CNBC/MarketWatch)
        self.active = [p for p in self.providers if p.available]
        self._cache = self._load_cache()
        # Handles to the RSS providers for market-mood queries (by market)
        self._rss_in = next(
            (p for p in self.providers if type(p).__name__ == "IndianRSSProvider" and p.available),
            None,
        )
        self._rss_us = next(
            (p for p in self.providers if type(p).__name__ == "USRSSProvider" and p.available), None
        )

    def get_market_mood(self, market: str = "IN") -> dict:
        """Overall market news sentiment (regime gauge) for the given market."""
        rss = self._rss_us if market == "US" else self._rss_in
        return rss.fetch_market_mood() if rss else {}

    def _load_cache(self) -> dict:
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_cache(self):
        CACHE_FILE.write_text(json.dumps(self._cache, default=str))

    def _is_fresh(self, key: str) -> bool:
        e = self._cache.get(key)
        if not e:
            return False
        try:
            age = datetime.now() - datetime.fromisoformat(e["fetched_at"])
            return age < timedelta(hours=CACHE_TTL_HOURS)
        except Exception:
            return False

    def get_ticker_sentiment(self, ticker: str, market: str = "IN") -> TickerSentiment:
        """Fetch + aggregate sentiment for one ticker across all active providers."""
        key = f"{market}:{ticker}"
        if self._is_fresh(key):
            c = self._cache[key]
            return TickerSentiment(
                ticker,
                c["score"],
                c["label"],
                c["n_articles"],
                c["providers"],
                c["top_headlines"],
                c["fetched_at"],
            )

        all_articles, providers_used = [], []
        for p in self.active:
            arts = p.fetch_news(ticker, market)
            if arts:
                providers_used.append(p.name)
                w = self.PROVIDER_WEIGHT.get(p.name, 1.0)
                for a in arts:
                    a.sentiment = max(-1.0, min(1.0, a.sentiment))
                    all_articles.append((a, w))

        if not all_articles:
            return TickerSentiment(ticker, 0.0, "NO_DATA", 0, [], [], datetime.now().isoformat())

        # Weighted mean sentiment
        num = sum(a.sentiment * w for a, w in all_articles)
        den = sum(w for _, w in all_articles)
        score = round(num / den, 4) if den else 0.0
        top = sorted(all_articles, key=lambda x: abs(x[0].sentiment), reverse=True)[:5]
        top_head = [
            {"title": a.title[:90], "sentiment": round(a.sentiment, 2), "source": a.source}
            for a, _ in top
        ]

        ts = TickerSentiment(
            ticker=ticker,
            score=score,
            label=label_of(score),
            n_articles=len(all_articles),
            providers=providers_used,
            top_headlines=top_head,
            fetched_at=datetime.now().isoformat(),
        )

        self._cache[key] = ts.__dict__
        self._save_cache()
        return ts

    def get_batch(self, tickers: List[str], market: str = "IN") -> Dict[str, TickerSentiment]:
        out = {}
        for i, t in enumerate(tickers, 1):
            out[t] = self.get_ticker_sentiment(t, market)
            if i % 10 == 0 or i == len(tickers):
                pos = sum(1 for s in out.values() if s.label == "POSITIVE")
                neg = sum(1 for s in out.values() if s.label == "NEGATIVE")
                print(f"  {i}/{len(tickers)} | POSITIVE:{pos} NEGATIVE:{neg}")
        return out

    def status(self) -> str:
        if not self.active:
            return (
                "No providers active. Set API keys: MARKETAUX_KEY, "
                "ALPHAVANTAGE_KEY, FINNHUB_KEY, NEWSDATA_KEY"
            )
        return f"Active providers: {', '.join(p.name for p in self.active)}"


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="Multi-source news sentiment pipeline")
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument("--market", choices=["IN", "US"], default="IN")
    args = p.parse_args()

    print(f"\n{'#'*72}\n  NEWS SENTIMENT INGESTION PIPELINE\n{'#'*72}")
    print(f"  {DISCLAIMER}\n")
    print(
        f"  VADER scoring: {'available' if _VADER_OK else 'MISSING (pip install vaderSentiment)'}"
    )

    sp = SentimentPipeline()
    print(f"  {sp.status()}\n")
    if not sp.active:
        print("  Demo mode (VADER on sample headlines, no live API):")
        samples = {
            "RELIANCE": "Reliance beats Q3 profit estimates, announces record buyback",
            "YESBANK": "Yes Bank plunges on fraud probe and rating downgrade",
            "TCS": "TCS reports steady growth amid cautious IT spending outlook",
        }
        for tk, hl in samples.items():
            s = score_text(hl)
            print(f'    {tk:<10} {label_of(s):<9} ({s:+.2f})  "{hl[:55]}…"')
        print("\n  → Set the API keys above to fetch live news. See file header.")
        return

    res = sp.get_batch(args.tickers, args.market)
    print(f"\n  {'Ticker':<12} {'Sentiment':<10} {'Score':>7} {'Articles':>9} {'Sources'}")
    print("  " + "─" * 60)
    for tk, s in res.items():
        print(
            f"  {tk:<12} {s.label:<10} {s.score:>+7.3f} {s.n_articles:>9} "
            f"{','.join(s.providers)}"
        )
        for h in s.top_headlines[:2]:
            print(f"      [{h['sentiment']:+.2f} {h['source']}] {h['title'][:70]}")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = OUT_DIR / f"sentiment_{args.market}_{ts}.json"
    out.write_text(json.dumps({t: s.__dict__ for t, s in res.items()}, default=str, indent=2))
    print(f"\n  📊 → {out}")


if __name__ == "__main__":
    main()
