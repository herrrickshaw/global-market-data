# pipeline_news.py
# ================
# PIPELINE 2 of 2 — NEWS-BASED ANALYSIS (live, text sentiment).
#
# Orchestrates the news/sentiment side, fully separate from the historical
# price pipeline. Online — pulls live headlines and scores them.
#
#   Stage 1  Market mood        — overall Indian news sentiment (regime gauge)
#   Stage 2  Per-ticker scoring — sentiment for a watchlist (RSS + APIs)
#   Stage 3  Forward monitor    — log sentiment at 1d/1wk/1mo/3mo cadence
#   Stage 4  Sentiment join     — match logged sentiment to realised price moves
#                                 (builds the true news↔price dataset over time)
#
# Sources:
#   Free (no key):  Moneycontrol, Economic Times, BusinessLine, LiveMint (RSS)
#   API (set keys): Marketaux, Alpha Vantage, Finnhub, NewsData.io
#
# Usage:
#   python pipeline_news.py --mood                              # market mood only
#   python pipeline_news.py --tickers RELIANCE TCS ADANIENT    # score watchlist
#   python pipeline_news.py --monitor --tickers RELIANCE TCS   # append to log
#   python pipeline_news.py --join --market IN                 # link log↔prices
#   python pipeline_news.py --watchlist nifty50                # preset lists
#
# ⚠️ News sentiment is noisy and may lead or lag price. NOT investment advice.

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from sentiment_pipeline import SentimentPipeline
from stock_utils import pct_change

OUT_DIR = Path("./news_pipeline_results")
OUT_DIR.mkdir(exist_ok=True)
MONITOR_LOG = OUT_DIR / "news_sentiment_log.json"
CACHE_DIR = Path.home() / "Downloads" / "market_cache" / "ohlc"

HORIZONS = {"1d": 1, "1wk": 5, "1mo": 21, "3mo": 63}

DISCLAIMER = (
    "⚠️  NEWS pipeline: sentiment is noisy, provider-dependent, may lead "
    "or lag price. Educational/research only. NOT investment advice."
)

NIFTY50 = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "HINDUNILVR",
    "ITC",
    "SBIN",
    "BHARTIARTL",
    "KOTAKBANK",
    "LT",
    "AXISBANK",
    "BAJFINANCE",
    "ASIANPAINT",
    "MARUTI",
    "TITAN",
    "SUNPHARMA",
    "ADANIENT",
    "ADANIPORTS",
    "NTPC",
]


# ── Stage 1: market mood ──────────────────────────────────────────────────────


def stage_mood(sp: SentimentPipeline):
    print(f"\n{'─'*70}\n  ▶ STAGE 1 — MARKET MOOD (Indian news regime gauge)\n{'─'*70}")
    mood = sp.get_market_mood()
    if not mood:
        print("  No RSS mood available (feedparser missing?)")
        return mood
    emoji = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "🟡"}.get(mood.get("mood"), "⚪")
    print(
        f"  {emoji} Indian market news mood: {mood.get('mood')} "
        f"(score {mood.get('score'):+.3f}) from {mood.get('n_articles')} live articles"
    )
    print("  Sources: Moneycontrol, Economic Times, BusinessLine, LiveMint")
    return mood


# ── Stage 2: per-ticker scoring ───────────────────────────────────────────────


def stage_score(sp: SentimentPipeline, tickers: list, market: str) -> dict:
    print(f"\n{'─'*70}\n  ▶ STAGE 2 — PER-TICKER SENTIMENT ({len(tickers)} stocks)\n{'─'*70}")
    res = sp.get_batch(tickers, market)
    print(f"\n  {'Ticker':<12} {'Sentiment':<9} {'Score':>7} {'Articles':>9} {'Sources'}")
    print("  " + "─" * 58)
    for tk, s in sorted(res.items(), key=lambda x: -x[1].score):
        if s.label == "NO_DATA":
            continue
        print(
            f"  {tk:<12} {s.label:<9} {s.score:>+7.3f} {s.n_articles:>9} "
            f"{','.join(p.split(':')[0] for p in set(s.providers))}"
        )
    return res


# ── Stage 3: forward monitor ──────────────────────────────────────────────────


def stage_monitor(sp: SentimentPipeline, tickers: list, market: str):
    print(f"\n{'─'*70}\n  ▶ STAGE 3 — FORWARD MONITOR (log for 1d/1wk/1mo/3mo join)\n{'─'*70}")
    log = json.loads(MONITOR_LOG.read_text()) if MONITOR_LOG.exists() else []
    today = datetime.today().strftime("%Y-%m-%d")
    res = sp.get_batch(tickers, market)
    added = 0
    for tk, s in res.items():
        if s.label == "NO_DATA":
            continue
        log.append(
            {
                "date": today,
                "ticker": tk,
                "market": market,
                "sentiment": s.score,
                "label": s.label,
                "n_articles": s.n_articles,
            }
        )
        added += 1
    MONITOR_LOG.write_text(json.dumps(log, indent=2))
    print(f"  Logged {added} tickers for {today}. Total log: {len(log)} rows.")
    print("  Schedule daily; run --join later to link sentiment to realised moves.")


# ── Stage 4: join logged sentiment to realised price moves ────────────────────


def _load_prices(ticker: str, market: str) -> pd.DataFrame:
    suffix = ".NS" if market == "IN" else ""
    for cand in (f"{ticker}{suffix}.parquet", f"{ticker}.parquet"):
        p = CACHE_DIR / cand
        if p.exists():
            try:
                return pd.read_parquet(p)
            except Exception:
                pass
    return pd.DataFrame()


def stage_join(market: str):
    print(f"\n{'─'*70}\n  ▶ STAGE 4 — SENTIMENT ↔ PRICE JOIN (true news linkage)\n{'─'*70}")
    if not MONITOR_LOG.exists():
        print("  No monitor log yet. Run --monitor daily first.")
        return
    log = pd.DataFrame(json.loads(MONITOR_LOG.read_text()))
    log = log[log["market"] == market]
    if log.empty:
        print(f"  No {market} entries in log.")
        return

    rows = []
    for _, e in log.iterrows():
        px = _load_prices(e["ticker"], market)
        if px.empty:
            continue
        idx = px.index
        try:
            sig_dt = pd.Timestamp(e["date"])
            pos = idx.searchsorted(sig_dt)
            if pos >= len(px):
                continue
            entry = float(px["Close"].iloc[pos])
            rec = {
                "ticker": e["ticker"],
                "date": e["date"],
                "sentiment": e["sentiment"],
                "label": e["label"],
            }
            for h, n in HORIZONS.items():
                if pos + n < len(px):
                    rec[f"fwd_{h}"] = pct_change(float(px["Close"].iloc[pos + n]), entry)
                else:
                    rec[f"fwd_{h}"] = np.nan
            rows.append(rec)
        except Exception:
            continue

    if not rows:
        print("  No matured entries yet (need price bars after the logged date).")
        return
    df = pd.DataFrame(rows)
    print(f"  Joined {len(df)} logged sentiments to realised prices.")
    # Correlation of sentiment with each forward horizon
    print("\n  Sentiment → forward-return correlation (true news):")
    print(f"  {'Horizon':<8} {'Corr':>8} {'N':>6}")
    print("  " + "─" * 26)
    for h in HORIZONS:
        col = f"fwd_{h}"
        sub = df[["sentiment", col]].dropna()
        if len(sub) >= 10:
            c = sub["sentiment"].corr(sub[col])
            print(f"  {h:<8} {c:>+8.3f} {len(sub):>6}")
        else:
            print(f"  {h:<8} {'(need ≥10 matured)':>16}")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    df.to_csv(OUT_DIR / f"news_price_join_{market}_{ts}.csv", index=False)
    print(f"\n  📊 → news_pipeline_results/news_price_join_{market}_{ts}.csv")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="PIPELINE 2 — News-based analysis (live sentiment)")
    p.add_argument("--market", choices=["IN", "US"], default="IN")
    p.add_argument("--tickers", nargs="+", default=None)
    p.add_argument("--watchlist", choices=["nifty50"], default=None)
    p.add_argument("--mood", action="store_true", help="Market mood only")
    p.add_argument("--monitor", action="store_true", help="Append to forward log")
    p.add_argument("--join", action="store_true", help="Join log to realised prices")
    a = p.parse_args()

    print(f"\n{'#'*70}")
    print(f"  PIPELINE 2 — NEWS-BASED ANALYSIS  |  {a.market}")
    print(f"  {datetime.now():%d %b %Y %H:%M}")
    print(f"{'#'*70}\n{DISCLAIMER}")

    sp = SentimentPipeline()
    print(f"\n  {sp.status()}")

    tickers = a.tickers or (NIFTY50 if a.watchlist == "nifty50" else None)

    if a.join:
        stage_join(a.market)
        return
    if a.mood or (not tickers and not a.monitor):
        stage_mood(sp)
        if not tickers:
            return
    if a.monitor:
        if not tickers:
            tickers = NIFTY50
        stage_monitor(sp, tickers, a.market)
    elif tickers:
        stage_score(sp, tickers, a.market)

    print(f"\n  {DISCLAIMER}\n")


if __name__ == "__main__":
    main()
