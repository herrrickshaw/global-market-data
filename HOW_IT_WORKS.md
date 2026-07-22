# How it works — step by step

A plain walk-through of what happens from "clone the repo" to "results in hand".
Pair this with `ARCHITECTURE.md` (the layered diagram + schemas).

## A. First run / bootstrap

1. **Clone** the repo. The `cache_seed/*.parquet` files (20 markets, ~1 yr OHLCV)
   and `reference_seed/*` arrive via **Git LFS** — this is the "starter kit", so
   no data download is needed to begin.
2. **`run.py`** (or `screener_kit.bootstrap()`) checks the LMDB store. If empty,
   it copies each `cache_seed/cleaned_long_<MKT>.parquet` into `$BHAV_CACHE`
   (the working dir; `/content/cache` on Colab) and calls `bhavcopy_store.build()`.
3. **`bhavcopy_store.build()`** reads every seed via `frames.long_to_dict()`
   (one shared helper — long rows → `{symbol: OHLCV}`), serialises each symbol to
   zstd Arrow, and writes the **LMDB** key-value store (`symbol → bytes`). Now any
   symbol loads in ~12 ms.

## B. Running a screen — `kit.screen("darvas", "IN", min_turnover_usd=1e6)`

4. **Load** — `screener_kit.load(market, min_turnover_usd)`:
   - if a turnover floor is given, `liquidity.liquid_symbols()` returns the tradable
     tickers (pre-filter → fewer stocks → faster);
   - reads `cleaned_long_<MKT>.parquet`, filters to those tickers, and pivots to
     `{symbol: OHLCV}` via `frames.long_to_dict()`.
5. **Wrap** — each symbol becomes a `StockData(symbol, market, ohlcv, fundamentals)`.
   For India, the Cash-Conversion-Cycle value from `screener_in.ccc_map()`
   (screener.in) is injected into `fundamentals`.
6. **Score** — the chosen `strategies/<slug>.screen(stock)` runs and returns a
   `Result(passed, score, metrics)`. Price strategies (darvas, golden_crossover)
   use only OHLCV; fundamental ones read `fundamentals`.
7. **Annotate** — `liquidity.annotate()` appends `Turnover_USD` + a `Liquidity`
   tier (High/Medium/Low, per-market thresholds).
8. **Return** — a ranked DataFrame; `run.py` prints it and saves `results/*.csv`.

## C. A custom screen — `kit.custom_screen({"rsi14": ("<",35)}, "US")`

9. `custom_screener.compute_metrics()` derives technical metrics from OHLCV
   (returns 1m/3m/6m/1y, vol, SMA50/200, RSI14, ATR%, 52-week distance, drawdown,
   CCC) and merges any fundamentals.
10. `evaluate()` applies your rule dict (via the `OPS` operator-lookup) **or** a
    `lambda`; `screen()` ranks/limits and adds the liquidity tier.

## D. Refreshing for live data — `kit.update("IN")`

11. **India**: `bhavcopy_history.fetch_history()` asks `market_calendar` for the
    trading days, fetches only the **new** NSE+BSE bhavcopy files (skipping
    weekends/holidays and a negative-date cache), appends to the assembled
    parquet, and rebuilds the store. No Yahoo, so no rate limits.
12. **Other markets**: `data_sources.fetch()` pulls recent bars through the
    **yahoo → stooq** fallback chain, merges into the seed via `frames.write_seed()`,
    and rebuilds the store.

## E. Outputs

13. `run_global_analysis.analyse()` runs the price screens across all 20 markets →
    a momentum leaderboard (`cache_seed/global_highlights.parquet`).
14. `market_performance.analyse()` computes each market's 5-year index stats.
15. `build_mailer.build()` assembles the Daily Market Brief (India screener +
    CCC + global momentum + other-markets tour + 5-year scoreboard); `send_mailer.py`
    emails it via SMTP — token-free, runnable from `daily_pipeline.sh` (cron/launchd).

## Why it's fast & resilient (design notes)

- **DRY core** — `frames.py` holds the one implementation of the long↔per-symbol
  pivot and the compact-parquet write (previously duplicated in 5 modules).
- **LMDB** gives O(1) keyed reads; the **liquidity pre-filter** cuts the universe
  before the expensive logic (~8× faster screens).
- **Ships with data** (LFS seeds) → clone-and-run; **official-source-first**
  (bhavcopy/SEC) with Yahoo/Stooq as fallback; **no LLM** anywhere (VADER for news).
- Guarded by **ruff + black + pytest in CI**.
