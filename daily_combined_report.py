# daily_combined_report.py
# =========================
# Combines the two pipelines into ONE daily report with two labelled components:
#
#   1. "STOCK PICKS BASED ON FUNDAMENTALS"  ← pipeline_historical (screeners)
#   2. "TALK ON THE STREET"                 ← pipeline_news (live sentiment)
#
# and a CONVERGENCE section highlighting stocks where BOTH agree
# (fundamentally strong AND positive in the news = highest conviction).
#
# THE TWO APPROACHES (see APPROACHES.md for the full write-up):
#   • FUNDAMENTAL/HISTORICAL — objective screeners on 5yr price+financials
#     (offline, deterministic, backtestable). Answers "is this structurally
#     sound by its own numbers?". Backward-looking; misses catalysts.
#   • NEWS/SENTIMENT — live headlines, company-name matched, VADER-scored
#     (online, noisy, forward-looking). Answers "what is the market saying now?".
#     Captures catalysts; provider-dependent and sometimes mis-signed.
#   CONVERGENCE is a CROSS-CHECK (different failure modes), NOT a buy signal.
#
# Efficient design: the fundamental screeners run on the FULL universe (offline,
# from cache); news sentiment then runs ONLY on the fundamental shortlist + a
# market-mood gauge — so we never burn news quota on 7,000 stocks.
#
# Usage:
#   python daily_combined_report.py --market IN          # full NSE+BSE universe
#   python daily_combined_report.py --market US
#   python daily_combined_report.py --market IN --html   # emit HTML fragment
#
# Output: combined_report_results/combined_<market>_<ts>.{json,html}
#
# ⚠️ Educational/research only. Mechanical screeners + noisy sentiment. NOT advice.

from __future__ import annotations

import argparse
import glob
import json
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

OUT_DIR = Path("./combined_report_results")
OUT_DIR.mkdir(exist_ok=True)

DISCLAIMER = (
    "⚠️  Two independent views: mechanical fundamental screeners + noisy "
    "news sentiment. Convergence is NOT a buy signal. Educational/research "
    "only. NOT investment advice. Consult a SEBI-registered advisor."
)


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 1 — STOCK PICKS BASED ON FUNDAMENTALS
# ══════════════════════════════════════════════════════════════════════════════

# Map each screener sheet → the short tag shown in the report
SCREENER_SHEETS = {
    "Triple_Hits": "TripleHit",
    "Multi_Screen_Hits": "MultiScreen",
    "Piotroski_Strong": "Piotroski",
    "Coffee_Can": "CoffeeCan",
    "Magic_Formula": "MagicFormula",
    "Bull_Cartel": "BullCartel",
    "Golden_Crossover": "GoldenCross",
    "Darvas_Signals": "Darvas",
}


def get_fundamental_picks(market: str, run_fresh: bool = False) -> pd.DataFrame:
    """
    Build the COMBINED, DEDUPLICATED set of every stock that passed ANY screener
    in the latest full scan — not just Triple/Multi-Screen hits. Each stock is
    tagged with the list of screeners it appears in and a tier:
        Triple Hit  > Multi-Screen (3+)  > Single-Screen
    This is the universe on which Talk-on-the-Street sentiment is run.
    """
    print("  [Component 1] Fundamental picks — union of ALL screener sheets …")
    scan_dir = "indian_full_scan" if market == "IN" else "us_full_scan"
    pat = f"{scan_dir}/*_full_scan_*.xlsx"
    files = sorted(glob.glob(pat))

    if run_fresh or not files:
        import subprocess
        import sys

        script = "full_indian_market_scan.py" if market == "IN" else "full_us_market_scan.py"
        print(f"  [Component 1] Running fresh scan ({script}) …")
        cmd = [sys.executable, script, "--workers", "10"]
        if market == "US":
            cmd += ["--min-price", "2"]
        subprocess.run(cmd, timeout=14400)
        files = sorted(glob.glob(pat))

    if not files:
        return pd.DataFrame()

    f = files[-1]
    xl = pd.ExcelFile(f)
    agg: dict = {}  # symbol → {screens:set, piotroski, ltp}

    def _add(sym, tag, piotroski=None, ltp=None):
        sym = str(sym or "").strip()
        if not sym:
            return
        e = agg.setdefault(sym, {"screens": set(), "piotroski": None, "ltp": None})
        e["screens"].add(tag)
        if piotroski is not None and pd.notna(piotroski):
            e["piotroski"] = piotroski
        if ltp is not None and pd.notna(ltp):
            e["ltp"] = ltp

    # Layout A — separate per-screener sheets (US scan)
    for sheet, tag in SCREENER_SHEETS.items():
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(f, sheet_name=sheet)
        for _, r in df.iterrows():
            if (
                sheet == "Darvas_Signals"
                and str(r.get("Darvas_Signal", "")).upper() != "BREAKOUT_BUY"
            ):
                continue
            _add(r.get("Symbol"), tag, r.get("Piotroski_Score"), r.get("LTP"))

    # Layout B — single column-based "Fundamentals" sheet (Indian scan)
    if "Fundamentals" in xl.sheet_names:
        fd = pd.read_excel(f, sheet_name="Fundamentals")
        for _, r in fd.iterrows():
            sym, ltp, pio = r.get("Symbol"), r.get("LTP"), r.get("Piotroski_Score")
            if str(r.get("Piotroski_Strong", "")).upper() == "YES":
                _add(sym, "Piotroski", pio, ltp)
            if str(r.get("CoffeeCan", "")).upper() == "PASS":
                _add(sym, "CoffeeCan", pio, ltp)
            if str(r.get("MagicFormula", "")).upper() == "PASS":
                _add(sym, "MagicFormula", pio, ltp)
            if str(r.get("BullCartel", "")).upper() == "PASS":
                _add(sym, "BullCartel", pio, ltp)
            if str(r.get("Darvas_Signal", "")).upper() == "BREAKOUT_BUY":
                _add(sym, "Darvas", pio, ltp)
    # Golden Cross from All_Stocks (column-based, both layouts)
    if "All_Stocks" in xl.sheet_names:
        a = pd.read_excel(f, sheet_name="All_Stocks")
        if "GC_Signal" in a.columns:
            for _, r in a[a["GC_Signal"].astype(str).str.upper() == "GOLDEN_CROSS"].iterrows():
                _add(r.get("Symbol"), "GoldenCross", r.get("Piotroski_Score"), r.get("LTP"))

    picks = []
    for sym, e in agg.items():
        screens = e["screens"]
        n = len([s for s in screens if s not in ("TripleHit", "MultiScreen")])
        if "TripleHit" in screens:
            tier = "Triple Hit"
        elif "MultiScreen" in screens or n >= 3:
            tier = "Multi-Screen"
        else:
            tier = "Single-Screen"
        picks.append(
            {
                "Symbol": sym,
                "Tier": tier,
                "Screens": "+".join(
                    sorted(s for s in screens if s not in ("TripleHit", "MultiScreen"))
                )
                or "—",
                "N_Screens": n,
                "Piotroski": e["piotroski"],
                "LTP": e["ltp"],
            }
        )

    df = pd.DataFrame(picks)
    if not df.empty:
        tier_rank = {"Triple Hit": 0, "Multi-Screen": 1, "Single-Screen": 2}
        df["_r"] = df["Tier"].map(tier_rank)
        df = df.sort_values(["_r", "N_Screens"], ascending=[True, False]).drop("_r", axis=1)
    n_triple = int((df["Tier"] == "Triple Hit").sum()) if not df.empty else 0
    n_multi = int((df["Tier"] == "Multi-Screen").sum()) if not df.empty else 0
    print(
        f"  [Component 1] {len(df)} UNIQUE fundamental picks across all screeners "
        f"({n_triple} triple, {n_multi} multi-screen, "
        f"{len(df)-n_triple-n_multi} single-screen)"
    )
    return df


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 2 — TALK ON THE STREET (news sentiment)
# ══════════════════════════════════════════════════════════════════════════════


def get_street_talk(symbols: list, market: str) -> tuple:
    """
    Market-mood gauge + per-ticker sentiment for the given symbols.
    Returns (market_mood dict, {symbol: sentiment dict}).
    """
    print(
        f"  [Component 2] Talk on the Street — news sentiment for "
        f"{len(symbols)} picks + market mood …"
    )
    try:
        from sentiment_pipeline import SentimentPipeline
    except ImportError:
        return {}, {}
    sp = SentimentPipeline()
    mood = sp.get_market_mood(market)  # IN → Moneycontrol/ET; US → CNBC/MarketWatch
    talk = {}
    if symbols:
        res = sp.get_batch(symbols, market)
        for sym, s in res.items():
            talk[sym] = {
                "score": s.score,
                "label": s.label,
                "n_articles": s.n_articles,
                "headlines": s.top_headlines[:2],
            }
    return mood, talk


# ══════════════════════════════════════════════════════════════════════════════
# CONVERGENCE — where both components agree
# ══════════════════════════════════════════════════════════════════════════════


def find_convergence(picks: pd.DataFrame, talk: dict) -> pd.DataFrame:
    """
    Highlight stocks that are BOTH a fundamental pick AND positive in the news.
    These are the highest-conviction convergence signals.
    """
    rows = []
    for _, p in picks.iterrows():
        sym = p["Symbol"]
        t = talk.get(sym)
        if not t or t["label"] == "NO_DATA":
            continue
        agree = t["label"] == "POSITIVE"  # fundamentals are bullish by construction
        rows.append(
            {
                "Symbol": sym,
                "Tier": p["Tier"],
                "Fundamental": "STRONG",
                "News": t["label"],
                "News_Score": t["score"],
                "N_Articles": t["n_articles"],
                "Convergence": (
                    "✅ BOTH BULLISH"
                    if agree
                    else ("⚠️ NEWS NEGATIVE" if t["label"] == "NEGATIVE" else "— news neutral")
                ),
                "Top_Headline": t["headlines"][0]["title"][:80] if t["headlines"] else "",
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        # Sort: convergent (both bullish) first, then by news score
        df["_rank"] = df["Convergence"].map(
            {"✅ BOTH BULLISH": 0, "— news neutral": 1, "⚠️ NEWS NEGATIVE": 2}
        )
        df = df.sort_values(["_rank", "News_Score"], ascending=[True, False]).drop("_rank", axis=1)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# REPORT ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════════


def build_report(market: str, run_fresh: bool) -> dict:
    print(f"\n{'#'*72}\n  DAILY COMBINED REPORT — {market}\n{'#'*72}\n  {DISCLAIMER}\n")

    picks = get_fundamental_picks(market, run_fresh)
    # Street-talk only needs the symbols actually shown in the report (top 25,
    # Triple Hits first). Scoring all 300 picks via keyless news APIs stalls.
    symbols = picks.head(25)["Symbol"].tolist() if not picks.empty else []
    mood, talk = get_street_talk(symbols, market)
    convergence = find_convergence(picks, talk)

    return {
        "market": market,
        "generated": datetime.now().isoformat(),
        "picks": picks,
        "mood": mood,
        "talk": talk,
        "convergence": convergence,
    }


def print_report(rep: dict):
    market = rep["market"]

    print(f"\n{'='*74}")
    print("  📊 COMPONENT 1 — STOCK PICKS BASED ON FUNDAMENTALS")
    print(f"{'='*74}")
    picks = rep["picks"]
    if picks.empty:
        print("  No fundamental picks today.")
    else:
        print(f"  {'Symbol':<14} {'Tier':<14} {'Screens':<28} {'Piotroski'}")
        print("  " + "─" * 66)
        for _, r in picks.head(25).iterrows():
            print(
                f"  {str(r['Symbol']):<14} {str(r['Tier']):<14} "
                f"{str(r['Screens']):<28} {r.get('Piotroski','—')}"
            )

    print(f"\n{'='*74}")
    print("  🗞️  COMPONENT 2 — TALK ON THE STREET (live news sentiment)")
    print(f"{'='*74}")
    mood = rep["mood"]
    src = "CNBC/MarketWatch" if market == "US" else "Moneycontrol/ET/BusinessLine/LiveMint"
    if mood:
        print(
            f"  Market mood: {mood.get('mood')} ({mood.get('score'):+.2f}) "
            f"from {mood.get('n_articles')} articles ({src})"
        )
    talk = rep["talk"]
    if talk:
        print(f"\n  {'Symbol':<14} {'Sentiment':<10} {'Score':>7} {'Articles':>9}")
        print("  " + "─" * 46)
        for sym, t in sorted(talk.items(), key=lambda x: -x[1]["score"]):
            if t["label"] != "NO_DATA":
                print(f"  {sym:<14} {t['label']:<10} {t['score']:>+7.2f} {t['n_articles']:>9}")

    print(f"\n{'='*74}")
    print("  ⭐ CONVERGENCE — WHERE FUNDAMENTALS & THE STREET AGREE")
    print(f"{'='*74}")
    conv = rep["convergence"]
    if conv.empty:
        print("  No picks had matching news coverage today.")
    else:
        both = conv[conv["Convergence"] == "✅ BOTH BULLISH"]
        print(f"  🎯 {len(both)} HIGH-CONVICTION (fundamentally strong AND positive news):")
        for _, r in both.iterrows():
            print(
                f"    ✅ {r['Symbol']:<12} [{r['Tier']}] news {r['News_Score']:+.2f} "
                f"({r['N_Articles']} art) — {r['Top_Headline'][:55]}"
            )
        warn = conv[conv["Convergence"] == "⚠️ NEWS NEGATIVE"]
        if not warn.empty:
            print(f"\n  ⚠️  {len(warn)} CAUTION (strong fundamentals BUT negative news):")
            for _, r in warn.iterrows():
                print(
                    f"    ⚠️  {r['Symbol']:<12} news {r['News_Score']:+.2f} — {r['Top_Headline'][:55]}"
                )

    print(f"\n  {DISCLAIMER}\n")


def to_html(rep: dict) -> str:
    """Emit an HTML fragment for the daily mailer (two components + convergence)."""
    market = rep["market"]
    picks, talk, conv, mood = rep["picks"], rep["talk"], rep["convergence"], rep["mood"]
    h = []
    # Component 1
    h.append("<h2>📊 Stock Picks Based on Fundamentals</h2>")
    h.append("<table><tr><th>Symbol</th><th>Tier</th><th>Screens</th><th>Piotroski</th></tr>")
    for _, r in (picks.head(25).iterrows() if not picks.empty else []):
        h.append(
            f"<tr><td><b>{r['Symbol']}</b></td><td>{r['Tier']}</td>"
            f"<td>{r['Screens']}</td><td>{r.get('Piotroski','—')}</td></tr>"
        )
    h.append("</table>")
    # Component 2
    h.append("<h2>🗞️ Talk on the Street</h2>")
    if mood:
        col = {"POSITIVE": "#2e7d32", "NEGATIVE": "#c62828"}.get(mood.get("mood"), "#f57f17")
        src = (
            "CNBC · MarketWatch"
            if market == "US"
            else "Moneycontrol · ET · BusinessLine · LiveMint"
        )
        h.append(
            f'<p>Market mood: <b style="color:{col}">{mood.get("mood")} '
            f'({mood.get("score"):+.2f})</b> from {mood.get("n_articles")} live articles '
            f"({src})</p>"
        )
    h.append("<table><tr><th>Symbol</th><th>Sentiment</th><th>Score</th><th>Articles</th></tr>")
    for sym, t in sorted(talk.items(), key=lambda x: -x[1]["score"]):
        if t["label"] == "NO_DATA":
            continue
        col = {"POSITIVE": "#2e7d32", "NEGATIVE": "#c62828"}.get(t["label"], "#f57f17")
        h.append(
            f"<tr><td><b>{sym}</b></td><td style='color:{col}'>{t['label']}</td>"
            f"<td>{t['score']:+.2f}</td><td>{t['n_articles']}</td></tr>"
        )
    h.append("</table>")
    # Convergence
    h.append("<h2>⭐ Convergence — Fundamentals &amp; The Street Agree</h2>")
    if not conv.empty:
        both = conv[conv["Convergence"] == "✅ BOTH BULLISH"]
        if not both.empty:
            h.append(
                "<p><b>🎯 High-conviction (strong fundamentals AND positive news):</b></p><ul>"
            )
            for _, r in both.iterrows():
                h.append(
                    f"<li><b>{r['Symbol']}</b> [{r['Tier']}] — news {r['News_Score']:+.2f} "
                    f"({r['N_Articles']} articles): {r['Top_Headline']}</li>"
                )
            h.append("</ul>")
        warn = conv[conv["Convergence"] == "⚠️ NEWS NEGATIVE"]
        if not warn.empty:
            h.append("<p><b>⚠️ Caution (strong fundamentals BUT negative news):</b></p><ul>")
            for _, r in warn.iterrows():
                h.append(
                    f"<li><b>{r['Symbol']}</b> — news {r['News_Score']:+.2f}: {r['Top_Headline']}</li>"
                )
            h.append("</ul>")
    else:
        h.append("<p>No fundamental picks had news coverage today.</p>")
    h.append(f'<p style="font-size:11px;color:#bf360c">{DISCLAIMER}</p>')
    return "\n".join(h)


def save(rep: dict, emit_html: bool):
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    base = OUT_DIR / f"combined_{rep['market']}_{ts}"
    payload = {
        "market": rep["market"],
        "generated": rep["generated"],
        "mood": rep["mood"],
        "picks": rep["picks"].to_dict("records") if not rep["picks"].empty else [],
        "convergence": (
            rep["convergence"].to_dict("records") if not rep["convergence"].empty else []
        ),
        "talk": rep["talk"],
    }
    base.with_suffix(".json").write_text(json.dumps(payload, default=str, indent=2))
    print(f"  📊 → {base}.json")
    if emit_html:
        base.with_suffix(".html").write_text(to_html(rep))
        print(f"  📄 → {base}.html  (drop into the daily mailer)")


def main():
    p = argparse.ArgumentParser(description="Daily combined report: fundamentals + street talk")
    p.add_argument("--market", choices=["IN", "US"], default="IN")
    p.add_argument("--run-fresh", action="store_true", help="Run full scan fresh")
    p.add_argument("--html", action="store_true", help="Also emit HTML fragment")
    a = p.parse_args()
    rep = build_report(a.market, a.run_fresh)
    print_report(rep)
    save(rep, a.html)


if __name__ == "__main__":
    main()
