# pipeline_historical.py
# ======================
# PIPELINE 1 of 2 — HISTORICAL DATA ANALYSIS (offline, price + fundamentals).
#
# Orchestrates all price/fundamental analysis stages over the 5-year Parquet
# cache. No live news — purely historical market data. Runs fully offline once
# the cache is warm.
#
#   Stage 1  Cache check / warm          (market_data_cache)
#   Stage 2  Screeners (6) full universe (full_indian/us_market_scan)
#   Stage 3  Backtest + walk-forward     (backtest_screeners / walk_forward_backtest)
#   Stage 4  Pattern discovery (KMeans)  (pattern_discovery)
#   Stage 5  Sector clustering           (sector_analysis)
#   Stage 6  DL directional strategy     (dl_strategy_eval)
#   Stage 7  Implied sentiment↔price     (sentiment_price_link, Part A proxy)
#
# Each stage is independently runnable; --stages selects a subset.
#
# Usage:
#   python pipeline_historical.py --market IN                 # all stages
#   python pipeline_historical.py --market US --stages pattern,sector,dl
#   python pipeline_historical.py --market IN --stages backtest --max 500
#
# ⚠️ Educational/research only. NOT investment advice.

from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).parent
PY = sys.executable

DISCLAIMER = (
    "⚠️  HISTORICAL pipeline: backtested on past data (survivorship + "
    "look-ahead risk). Patterns need not persist. NOT investment advice."
)

# stage_name → (script, default args builder)
STAGES = {
    "scan": "full_indian_market_scan.py",  # or full_us_market_scan.py for US
    "backtest": "backtest_screeners.py",
    "walkforward": "walk_forward_backtest.py",
    "pattern": "pattern_discovery.py",
    "sector": "sector_analysis.py",
    "dl": "dl_strategy_eval.py",
    "link": "sentiment_price_link.py",
}

DEFAULT_ORDER = ["pattern", "sector", "dl", "link"]  # cache-only analytics (fast, offline)
FULL_ORDER = ["scan", "backtest", "pattern", "sector", "dl", "link"]


def run_stage(name: str, market: str, max_stocks: int, workers: int) -> dict:
    """Run one historical-analysis stage as a subprocess; capture summary."""
    t0 = datetime.now()
    print(f"\n{'─'*78}\n  ▶ STAGE: {name.upper()}  ({market})\n{'─'*78}")

    if name == "scan":
        script = "full_us_market_scan.py" if market == "US" else "full_indian_market_scan.py"
        cmd = [PY, str(HERE / script), "--workers", str(workers)]
        if market == "US":
            cmd += ["--min-price", "2"]
    elif name == "backtest":
        cmd = [
            PY,
            str(HERE / "backtest_screeners.py"),
            "--market",
            market,
            "--workers",
            str(workers),
        ]
    elif name == "walkforward":
        cmd = [PY, str(HERE / "walk_forward_backtest.py"), "--liquid", "--workers", str(workers)]
    elif name == "pattern":
        cmd = [PY, str(HERE / "pattern_discovery.py"), "--market", market]
        if max_stocks:
            cmd += ["--max", str(max_stocks)]
    elif name == "sector":
        cmd = [PY, str(HERE / "sector_analysis.py"), "--market", market]
        if max_stocks:
            cmd += ["--max", str(max_stocks)]
    elif name == "dl":
        cmd = [PY, str(HERE / "dl_strategy_eval.py"), "--market", market, "--max", str(max_stocks)]
    elif name == "link":
        cmd = [PY, str(HERE / "sentiment_price_link.py"), "--market", market]
        if max_stocks:
            cmd += ["--max", str(max_stocks)]
    else:
        print(f"  Unknown stage: {name}")
        return {"stage": name, "ok": False}

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10800)
        # Echo last lines of the stage's own report
        tail = "\n".join(r.stdout.strip().splitlines()[-18:])
        print(tail)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        print("  ⏱  stage timed out")
        ok = False
    except Exception as e:
        print(f"  stage error: {e}")
        ok = False

    dur = (datetime.now() - t0).total_seconds()
    print(f"  ⏱ {name} done in {dur:.0f}s")
    return {"stage": name, "ok": ok, "seconds": round(dur, 1)}


def main():
    p = argparse.ArgumentParser(
        description="PIPELINE 1 — Historical data analysis (offline, price+fundamentals)"
    )
    p.add_argument("--market", choices=["IN", "US"], default="IN")
    p.add_argument(
        "--stages",
        default=",".join(DEFAULT_ORDER),
        help=f"Comma list: {','.join(STAGES)} | or 'full' / 'analytics'",
    )
    p.add_argument("--max", type=int, default=0, help="Cap stocks (0=all)")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()

    if a.stages == "full":
        stages = FULL_ORDER
    elif a.stages == "analytics":
        stages = DEFAULT_ORDER
    else:
        stages = [s.strip() for s in a.stages.split(",")]

    print(f"\n{'#'*78}")
    print(f"  PIPELINE 1 — HISTORICAL DATA ANALYSIS  |  {a.market}")
    print(f"  Stages: {' → '.join(stages)}")
    print(f"  {datetime.now():%d %b %Y %H:%M}")
    print(f"{'#'*78}\n{DISCLAIMER}")

    results = [run_stage(s, a.market, a.max, a.workers) for s in stages]

    print(f"\n{'='*78}\n  HISTORICAL PIPELINE SUMMARY")
    print(f"{'='*78}")
    for r in results:
        status = "✅" if r["ok"] else "❌"
        print(f"  {status} {r['stage']:<14} {r.get('seconds',0):>7.0f}s")
    print("\n  Outputs in: pattern_results/ sector_results/ dl_strategy_results/")
    print("              sentiment_link_results/ backtest_results/ *_full_scan/")
    print(f"  {DISCLAIMER}\n")


if __name__ == "__main__":
    main()
