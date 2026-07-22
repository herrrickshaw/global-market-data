# Architecture & Schema

How the code fits together, and what the cached data looks like.

## Layered flow

```
1 · DATA SOURCES (free / official)
   Exchange EOD (NSE/BSE bhavcopy) · Prices (Yahoo→Stooq) · Universes (SEC,
   JPX, KRX, SGX, Eastmoney, Euronext) · Reference (Damodaran, Fama-French,
   screener.in) · News (RSS)
                          │
2 · INGEST
   universe_sources.py   (who is listed, per market)
   bhavcopy_history.py   (India EOD; multi-layer cache + holiday calendar)
   data_sources.py       (OHLC with yahoo→stooq fallback)
   market_calendar.py    (skip weekends/holidays)
   fetch_market_ohlc.py  (full universe → per-market seed)
                          │
3 · CACHE & STORAGE  (committed via Git LFS — the "starter kit")
   cache_seed/*.parquet  (20 market seeds, ~1yr OHLCV)
   bhavcopy_store.py     (LMDB NoSQL: symbol → OHLCV, O(1) reads)
   liquidity_index       (USD turnover per symbol → High/Med/Low)
   reference_seed/       (Damodaran industry tables, Fama-French, company master)
                          │
4 · ENRICH & STRATEGIES
   strategies/           (11: piotroski, coffee_can, magic_formula,
                          bluest_blue_chips, debt_reduction, dividend_yield,
                          golden_crossover, loss_to_profit, garp, darvas,
                          cash_conversion_cycle)
   custom_screener.py    (screen on your own metrics)
   liquidity.py          (tiers + pre-filter)
   sec_fundamentals.py / screener_in.py   (CCC: US via SEC, IN via screener.in)
                          │
5 · FACADE / API
   screener_kit.py       (bootstrap · load · get · update · screen · custom_screen)
   run.py                (one-command CLI; auto-bootstraps)
                          │
6 · OUTPUTS
   run_global_analysis.py (momentum leaderboard)  market_performance.py (5y scoreboard)
   build_mailer.py → send_mailer.py (Daily Market Brief)  results/*.csv  ·  Colab tables
```

## Request → result (what `kit.screen(...)` does)

```
caller → screener_kit.load(market, min_turnover_usd)
           ├─ liquidity.liquid_symbols()      # pre-filter to tradable names
           └─ read cache_seed/cleaned_long_<MKT>.parquet  (or live BHAV_CACHE copy)
        → StockData(symbol, ohlcv, fundamentals)   # IN gets CCC injected
        → strategies.<slug>.screen(stock) → Result(pass, score, metrics)
        → liquidity.annotate()             # add Turnover_USD + Liquidity tier
        → DataFrame (printed / saved to results/)
```

## Data schemas

### `cache_seed/cleaned_long_<MKT>.parquet`  — OHLCV (long format)
| column | type | notes |
|--------|------|-------|
| Symbol | str | yfinance ticker (bhavcopy bare for IN) |
| Date | datetime | trading day |
| Open, High, Low, Close | float32 | adjusted prices |
| Volume | int64 | shares traded |

### `bhavcopy_store` (LMDB)  — fast keyed store
`key = symbol (utf-8)` → `value = zstd Arrow IPC of that symbol's OHLCV frame`.
Plus `__meta__` = `"<n_symbols>|<latest_bar>"`.

### `cache_seed/liquidity_index.parquet`
`Symbol · Market · turnover_usd (20-day median Close×Volume, FX-adjusted) · ltp`
→ tiers: High ≥ $10M/day, Medium $1–10M, Low < $1M (per-market overrides in `liquidity.MARKET_TIERS`).

### `reference_seed/`
- `damodaran_companies.parquet` — 48,156 firms (Company, Exchange, Ticker, Industry, Country)
- `damodaran_{pe,roe,beta,margin}.parquet` — industry benchmark tables
- `french_ff3.parquet` — Fama-French 3-factor monthly returns

### Result row (every screen)
`Symbol · Strategy · Pass · Score · <strategy metrics> · Turnover_USD · Liquidity · Note`

## Shared helpers (DRY)
`frames.py` is the single home for the OHLCV conversions reused everywhere:
`long_to_dict()` (long rows → {symbol: OHLCV}), `to_long()`/`write_seed()` (→ compact
zstd parquet), `read_seed()`. Used by screener_kit, bhavcopy_store, bhavcopy_history,
fetch_market_ohlc and build_market_seeds.

## Key design choices
- **Ships with data** — committed LFS seeds mean clone-and-run, no downloads.
- **Resilient sourcing** — India on official bhavcopy (no Yahoo); others fall back yahoo→stooq.
- **Token-free** — pure Python end to end (sentiment uses VADER, not an LLM).
- **Fast** — LMDB O(1) reads + liquidity pre-filter (≈8× faster screens).

See `DATA_AND_MODULES.md` (module guide) and `BLOOMBERG_SOURCES.md` (source map).
