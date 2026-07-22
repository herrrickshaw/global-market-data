#!/usr/bin/env python3
# build_mailer.py
# ===============
# Assemble the Daily Market Brief HTML (+ plain-text summary) from the latest
# cached outputs, so the morning job / any run produces a consistent mailer:
#   1. India screener (liquid picks, with liquidity tier)
#   2. India Cash Conversion Cycle screen (screener.in 228040, low-CCC + liquidity)
#   3. Global momentum top-15 (all markets)
#   4. 20-market 5-year scoreboard
#   + educational-only / NOT-investment-advice disclaimer.
#
#   from build_mailer import build; subj, text, html = build()

from __future__ import annotations

import datetime as _dt
import glob
import json
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

import liquidity as liq
import market_performance as mp
import run_global_analysis as rga

_COL = {"High": "#1b7f37", "Medium": "#b8860b", "Low": "#b00"}


def _tv(x):
    return f"${x/1e6:.1f}M" if pd.notna(x) else "—"


def _table(headers, rows):
    h = "".join(f"<th align='left' style='padding:5px 8px'>{x}</th>" for x in headers)
    return (
        f"<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        f"<tr style='background:#eef'>{h}</tr>{''.join(rows)}</table>"
    )


def build():
    today = _dt.date.today().strftime("%d %b %Y")

    # 1. India screener
    cj = sorted(glob.glob("combined_report_results/combined_IN_*.json"))
    picks_rows, mood = [], {"mood": "n/a", "score": 0, "n_articles": 0}
    if cj:
        d = json.load(open(cj[-1]))
        mood = d["mood"]
        p = pd.DataFrame(d["picks"])
        p["Market"] = "IN"
        p = liq.annotate(p)
        rank = {"Triple Hit": 0, "Multi-Screen": 1, "Single-Screen": 2}
        lr = {"High": 0, "Medium": 1, "Low": 2, "Unknown": 3}
        p["_o"] = p["Tier"].map(rank).fillna(3)
        p["_l"] = p["Liquidity"].map(lr)
        for _, r in p.sort_values(["_o", "_l"]).head(12).iterrows():
            picks_rows.append(
                f"<tr><td style='padding:4px 8px'><b>{r.Symbol}</b></td>"
                f"<td>{r.Tier}</td><td>{r.Screens}</td><td>{_tv(r.get('Turnover_USD'))}</td>"
                f"<td style='color:{_COL.get(r.Liquidity,'#777')};font-weight:600'>{r.Liquidity}</td></tr>"
            )

    # 2. India CCC screen (screener.in)
    ccc_rows = []
    try:
        cdf = pd.read_parquet("cache_seed/india_ccc_screen.parquet")
        cdf["Cash_Cycle"] = pd.to_numeric(cdf["Cash_Cycle"], errors="coerce")
        cdf = cdf[cdf["Liquidity"].isin(["High", "Medium"])].sort_values("Cash_Cycle").head(10)
        for _, r in cdf.iterrows():
            ccc_rows.append(
                f"<tr><td style='padding:4px 8px'><b>{r.Symbol}</b></td>"
                f"<td>{r.get('Name','')}</td><td>{r.Cash_Cycle:.1f}</td>"
                f"<td>{r.get('ROCE','')}</td>"
                f"<td style='color:{_COL.get(r.Liquidity,'#777')};font-weight:600'>{r.Liquidity}</td></tr>"
            )
    except Exception:
        pass

    # 3. Global momentum (top 15 overall)
    allh = rga.load_highlights()
    g = allh.head(15)
    g_rows = [
        f"<tr><td style='padding:4px 8px'>{r.Market}</td><td>{r.Symbol}</td>"
        f"<td>{r.ret_126:+.0f}</td><td>{r.rsi14}</td></tr>"
        for _, r in g.iterrows()
    ]

    # 3b. Other markets — top tradable mover per market (world tour, ex-IN/US)
    other_rows = []
    try:
        h = liq.annotate(allh.copy())
        names = {
            "TW": "Taiwan",
            "KR": "Korea",
            "JP": "Japan",
            "CN": "China",
            "HK": "Hong Kong",
            "CA": "Canada",
            "AU": "Australia",
            "UK": "UK",
            "DE": "Germany",
            "CH": "Switzerland",
            "SE": "Sweden",
            "FI": "Finland",
            "DK": "Denmark",
            "SG": "Singapore",
            "EU": "Euronext",
            "BR": "Brazil",
            "SA": "Saudi",
            "ZA": "S.Africa",
        }
        for m in [x for x in names if x in set(h.Market)]:
            sub = h[h.Market == m].sort_values("ret_126", ascending=False).head(1)
            if sub.empty:
                continue
            r = sub.iloc[0]
            other_rows.append(
                f"<tr><td style='padding:4px 8px'>{names[m]}</td>"
                f"<td><b>{r.Symbol}</b></td><td>{r.ret_126:+.0f}%</td>"
                f"<td style='color:{_COL.get(r.Liquidity,'#777')};font-weight:600'>{r.Liquidity}</td></tr>"
            )
    except Exception:
        pass

    # 4. 5y scoreboard
    p5 = mp.load()
    p_rows = [
        f"<tr><td style='padding:4px 8px'>{r.Market}</td><td>{r.Index}</td>"
        f"<td>{r['CAGR%']}</td><td>{r['Return_1y%']}</td><td>{r.Sharpe}</td></tr>"
        for _, r in p5.iterrows()
    ]

    html = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:740px;color:#1a1a1a">
<h1 style="font-size:21px;margin:0 0 2px">📈 Daily Market Brief — {today}</h1>
<p style="color:#666;font-size:13px;margin:0 0 14px">India screener + cash-conversion-cycle + global momentum + 5-year scoreboard · data as of last close</p>
<h2 style="font-size:16px;border-bottom:2px solid #1a73e8;padding-bottom:3px">🇮🇳 India — Daily Screener (most tradable)</h2>
{_table(["Symbol","Tier","Screens","Turnover","Liquidity"], picks_rows) if picks_rows else "<p>no picks</p>"}
<p style="font-size:11px;color:#666;margin:3px 0">Mood: <b style="color:#2e7d32">{mood['mood']} ({mood['score']:+.2f})</b> from {mood.get('n_articles',0)} articles. Liquidity (India): High ≥$5M/day · Medium $0.5–5M · Low &lt;$0.5M.</p>
<h2 style="font-size:16px;border-bottom:2px solid #1a73e8;padding-bottom:3px;margin-top:22px">💵 India — Cash Conversion Cycle (screener.in 228040)</h2>
<p style="font-size:12px;color:#555">Lowest/negative CCC = collects from customers before paying suppliers. Tradable (High/Medium) only.</p>
{_table(["Symbol","Name","CCC days","ROCE","Liquidity"], ccc_rows) if ccc_rows else "<p>n/a</p>"}
<h2 style="font-size:16px;border-bottom:2px solid #1a73e8;padding-bottom:3px;margin-top:22px">🌍 Global Momentum — Top 15 (20 markets)</h2>
{_table(["Mkt","Symbol","6mo %","RSI"], g_rows)}
<h2 style="font-size:16px;border-bottom:2px solid #1a73e8;padding-bottom:3px;margin-top:22px">🗺️ Other Markets — top tradable mover each</h2>
<p style="font-size:12px;color:#555">Best liquid (≥$1M/day) 6-month performer per market.</p>
{_table(["Market","Symbol","6mo %","Liquidity"], other_rows) if other_rows else "<p>n/a</p>"}
<h2 style="font-size:16px;border-bottom:2px solid #1a73e8;padding-bottom:3px;margin-top:22px">🗓️ 20-Market 5-Year Scoreboard</h2>
{_table(["Mkt","Index","5y CAGR%","1y %","Sharpe"], p_rows)}
<p style="font-size:11px;color:#bf360c;border-top:1px solid #eee;padding-top:10px;margin-top:18px">⚠️ Educational/research only. NOT investment advice. Screener results are mechanical filters, not buy/sell signals. Liquidity/CCC are estimates; index figures price-only, local currency. Past performance does not guarantee future returns. Consult a SEBI-registered investment advisor.</p>
</div>"""

    text = (
        f"Daily Market Brief — {today}\n"
        f"India mood: {mood['mood']} ({mood['score']:+.2f}).\n"
        f"India CCC screen (screener.in 228040) + global momentum + 20-market 5y scoreboard.\n"
        f"Educational/research only. NOT investment advice."
    )
    subject = f"📈 Daily Market Brief — {today}"
    return subject, text, html


if __name__ == "__main__":
    s, t, h = build()
    open("brief_today.html", "w").write(h)
    print(s, "\n", t, "\nhtml bytes:", len(h))
