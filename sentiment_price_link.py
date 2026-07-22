# sentiment_price_link.py
# ========================
# Historical sentiment ↔ price-movement linkage analysis + forward news monitor.
#
# HONEST DATA CONSTRAINT
#   Free news APIs (Marketaux, Finnhub, NewsData) only return days-to-weeks of
#   lookback — NOT 5 years of history. A true 5-year *news*-sentiment study needs
#   paid historical news. So this module does TWO things:
#
#   PART A — HISTORICAL (5 years, works now with cached prices):
#     Uses a transparent PRICE-DERIVED "implied sentiment" proxy: a news-shock
#     detector. Days with a large standardised move on high volume are flagged as
#     implied positive- or negative-news events (the market's own reaction reveals
#     the sentiment). We then measure forward returns at 1d / 1wk / 1mo / 3mo after
#     each shock to answer: does sentiment lead to CONTINUATION (momentum) or
#     REVERSAL (mean-reversion)? This is a well-established event-study proxy.
#
#   PART B — FORWARD (going-forward, real news):
#     A monitoring harness that polls the live news sentiment pipeline at
#     1-day / 1-week / 1-month / 3-month frequencies and logs the score alongside
#     the subsequent price move — so that, over time, a TRUE news-sentiment vs
#     price dataset accumulates for genuine correlation analysis.
#
# Usage:
#   python sentiment_price_link.py --market IN          # historical proxy study
#   python sentiment_price_link.py --market US --max 1000
#   python sentiment_price_link.py --monitor --tickers RELIANCE TCS  # forward log
#
# ⚠️ The historical analysis uses an IMPLIED sentiment proxy from price action,
#    not actual news text. Educational/research only. NOT investment advice.

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from stock_utils import parallel_map

CACHE_DIR = Path.home() / "Downloads" / "market_cache" / "ohlc"
REF_DIR = Path.home() / "nse_screener_reference" / "ohlc_cache"
OUT_DIR = Path("./sentiment_link_results")
OUT_DIR.mkdir(exist_ok=True)
MONITOR_LOG = OUT_DIR / "forward_sentiment_log.json"

# Forward-return horizons matching the user's frequencies
HORIZONS = {"1d": 1, "1wk": 5, "1mo": 21, "3mo": 63}

# News-shock detection thresholds
SHOCK_SIGMA = 2.0  # |return| must exceed 2σ of trailing 60-day vol
SHOCK_VOL_X = 1.8  # volume must exceed 1.8× trailing 20-day average
TRAIL_WIN = 60  # trailing window for volatility baseline

DISCLAIMER = (
    "⚠️  Historical analysis uses a PRICE-DERIVED implied-sentiment proxy "
    "(news-shock events), not actual news text — true 5yr news history "
    "needs paid data. Forward monitor logs real news. NOT advice."
)


# ══════════════════════════════════════════════════════════════════════════════
# PART A — HISTORICAL: implied-sentiment proxy → forward return event study
# ══════════════════════════════════════════════════════════════════════════════


def detect_shocks_and_returns(symbol: str, df: pd.DataFrame) -> list:
    """Detect implied-news shocks and record forward returns at each horizon.

    A 'shock' = a day where the standardised return exceeds SHOCK_SIGMA AND
    volume exceeds SHOCK_VOL_X × its 20-day average — the fingerprint of a
    news-driven move. Sign of the move = implied sentiment (POS / NEG).
    """
    if df is None or len(df) < TRAIL_WIN + 70:
        return []
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float).replace(0, np.nan)
    rets = c.pct_change()
    roll_sigma = rets.rolling(TRAIL_WIN).std()
    avg_vol = v.rolling(20).mean()
    closes = c.values

    events = []
    n = len(c)
    for i in range(TRAIL_WIN, n - 64):
        sigma = roll_sigma.iloc[i]
        if not sigma or np.isnan(sigma) or sigma == 0:
            continue
        z = rets.iloc[i] / sigma
        vol_ok = (
            avg_vol.iloc[i]
            and not np.isnan(avg_vol.iloc[i])
            and v.iloc[i] >= avg_vol.iloc[i] * SHOCK_VOL_X
        )
        if abs(z) >= SHOCK_SIGMA and vol_ok:
            direction = "POS" if z > 0 else "NEG"
            entry = closes[i]
            rec = {"symbol": symbol, "direction": direction, "z": round(float(z), 2)}
            for label, h in HORIZONS.items():
                if i + h < n:
                    rec[f"fwd_{label}"] = (closes[i + h] - entry) / entry * 100
                else:
                    rec[f"fwd_{label}"] = np.nan
            events.append(rec)
    return events


def analyse_linkage(events_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate forward returns by implied-sentiment direction × horizon."""
    rows = []
    for direction in ["POS", "NEG"]:
        sub = events_df[events_df["direction"] == direction]
        if sub.empty:
            continue
        for label in HORIZONS:
            vals = sub[f"fwd_{label}"].dropna()
            if len(vals) < 20:
                continue
            # Winsorise at 1st/99th pct so a few micro-cap moonshots don't
            # corrupt the mean (median is robust regardless).
            lo, hi = vals.quantile(0.01), vals.quantile(0.99)
            wins = vals.clip(lo, hi)
            rows.append(
                {
                    "Implied_Sentiment": (
                        "POSITIVE news-shock" if direction == "POS" else "NEGATIVE news-shock"
                    ),
                    "Horizon": label,
                    "N_events": len(vals),
                    "Mean_wins%": round(wins.mean(), 3),  # winsorised mean (robust)
                    "Median%": round(vals.median(), 3),  # primary metric
                    "Hit_rate%": round((vals > 0).mean() * 100, 1),  # % positive forward
                    "Std%": round(wins.std(), 2),
                }
            )
    return pd.DataFrame(rows)


def run_historical(market: str, max_stocks: int):
    print("PART A — HISTORICAL implied-sentiment → price linkage (5-yr proxy)")
    files = list(CACHE_DIR.glob("*.parquet")) + list(REF_DIR.glob("*.parquet"))
    if market == "IN":
        files = [f for f in files if f.stem.endswith(".NS") or f.stem.endswith(".BO")]
    elif market == "US":
        files = [f for f in files if not (f.stem.endswith(".NS") or f.stem.endswith(".BO"))]
    if max_stocks:
        files = files[:max_stocks]

    seen, stocks = set(), {}
    for f in files:
        if f.stem in seen:
            continue
        try:
            df = pd.read_parquet(f)
            if len(df) >= 200:
                stocks[f.stem] = df
                seen.add(f.stem)
        except Exception:
            pass
    print(f"  {len(stocks)} stocks with ≥200 bars")

    all_events = parallel_map(
        lambda kv: detect_shocks_and_returns(kv[0], kv[1]) or None,
        list(stocks.items()),
        workers=8,
        progress_every=500,
        label="stocks",
    )
    # flatten
    flat = [e for sub in all_events if sub for e in sub]
    ev = pd.DataFrame(flat)
    if ev.empty:
        print("  No shock events detected.")
        return
    print(
        f"  Detected {len(ev):,} implied-news-shock events "
        f"({(ev['direction']=='POS').sum():,} positive, "
        f"{(ev['direction']=='NEG').sum():,} negative)"
    )

    link = analyse_linkage(ev)
    print(f"\n{'='*80}")
    print("  SENTIMENT → FORWARD PRICE LINKAGE (event study)")
    print(f"{'='*80}")
    print(
        f"\n  {'Implied Sentiment':<24} {'Horizon':<8} {'N':>7} "
        f"{'Median%':>9} {'WinsMean%':>10} {'Hit%':>7}   (Median = primary, robust)"
    )
    print("  " + "─" * 76)
    for direction in ["POSITIVE news-shock", "NEGATIVE news-shock"]:
        for _, r in link[link["Implied_Sentiment"] == direction].iterrows():
            print(
                f"  {r['Implied_Sentiment']:<24} {r['Horizon']:<8} "
                f"{r['N_events']:>7,} {r['Median%']:>+8.2f}% "
                f"{r['Mean_wins%']:>+9.2f}% {r['Hit_rate%']:>6.1f}%"
            )
        print()

    # Interpretation: continuation vs reversal
    print("  ── INTERPRETATION ──")
    pos_1d = link[(link["Implied_Sentiment"] == "POSITIVE news-shock") & (link["Horizon"] == "1d")]
    pos_1mo = link[
        (link["Implied_Sentiment"] == "POSITIVE news-shock") & (link["Horizon"] == "1mo")
    ]
    neg_1d = link[(link["Implied_Sentiment"] == "NEGATIVE news-shock") & (link["Horizon"] == "1d")]
    neg_1mo = link[
        (link["Implied_Sentiment"] == "NEGATIVE news-shock") & (link["Horizon"] == "1mo")
    ]
    if not pos_1d.empty and not pos_1mo.empty:
        p1, pm = pos_1d.iloc[0]["Median%"], pos_1mo.iloc[0]["Median%"]
        print(f"  • Positive-news shocks: {p1:+.2f}% next day → {pm:+.2f}% over 1 month")
        print(f"    {'CONTINUATION (momentum)' if pm>0 else 'FADE/REVERSAL'} after good news")
    if not neg_1d.empty and not neg_1mo.empty:
        n1, nm = neg_1d.iloc[0]["Median%"], neg_1mo.iloc[0]["Median%"]
        print(f"  • Negative-news shocks: {n1:+.2f}% next day → {nm:+.2f}% over 1 month")
        print(f"    {'REBOUND/REVERSAL' if nm>0 else 'CONTINUED WEAKNESS (drift)'} after bad news")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT_DIR / f"sentiment_link_{market}_{ts}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"DISCLAIMER": [DISCLAIMER]}).to_excel(w, "DISCLAIMER", index=False)
        link.to_excel(w, "Linkage_Summary", index=False)
        ev.head(50000).to_excel(w, "All_Events", index=False)
    print(f"\n  📊 → {path}")
    print(f"\n{'='*80}\n  {DISCLAIMER}\n{'='*80}")


# ══════════════════════════════════════════════════════════════════════════════
# PART B — FORWARD: real-news monitor at 1d / 1wk / 1mo / 3mo
# ══════════════════════════════════════════════════════════════════════════════


def run_monitor(tickers: list, market: str):
    """Poll live news sentiment and append to the forward log with a timestamp.

    Run this on a schedule (daily). The log accumulates (sentiment, date) rows;
    a later pass joins each entry to the realised 1d/1wk/1mo/3mo price move to
    build a TRUE news-sentiment vs price dataset over time.
    """
    print("PART B — FORWARD news monitor (logs real sentiment for future linkage)")
    try:
        from sentiment_pipeline import SentimentPipeline
    except ImportError:
        print("  sentiment_pipeline.py not found.")
        return
    sp = SentimentPipeline()
    print(f"  {sp.status()}")

    log = json.loads(MONITOR_LOG.read_text()) if MONITOR_LOG.exists() else []
    today = datetime.today().strftime("%Y-%m-%d")
    for t in tickers:
        s = sp.get_ticker_sentiment(t, market)
        log.append(
            {
                "date": today,
                "ticker": t,
                "market": market,
                "sentiment": s.score,
                "label": s.label,
                "n_articles": s.n_articles,
            }
        )
        print(f"    {t:<10} {s.label:<9} {s.score:+.3f} ({s.n_articles} articles)")
    MONITOR_LOG.write_text(json.dumps(log, indent=2))
    print(f"\n  Logged {len(tickers)} tickers for {today}. Total log: {len(log)} rows.")
    print("  Schedule daily; realised price moves join later for true correlation.")
    print(f"  📊 → {MONITOR_LOG}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="Sentiment↔price linkage + news monitor")
    p.add_argument("--market", choices=["IN", "US"], default="IN")
    p.add_argument("--max", type=int, default=0)
    p.add_argument("--monitor", action="store_true", help="Forward news-monitor mode")
    p.add_argument("--tickers", nargs="+", default=None)
    a = p.parse_args()

    print(f"\n{'#'*80}")
    print(f"  SENTIMENT ↔ PRICE-MOVEMENT LINKAGE — {a.market}")
    print(f"  Horizons: 1d / 1wk / 1mo / 3mo | {datetime.now():%d %b %Y %H:%M}")
    print(f"{'#'*80}\n{DISCLAIMER}\n")

    if a.monitor:
        if not a.tickers:
            print("  --monitor needs --tickers")
            return
        run_monitor(a.tickers, a.market)
    else:
        run_historical(a.market, a.max)


if __name__ == "__main__":
    main()
