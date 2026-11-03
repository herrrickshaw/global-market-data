# CLAUDE.md

Technical guidance for working in this repository (global-market-data: 10-year point-in-time backtest engine). Educational / research
use only; not investment advice. Paths are repo-relative.

## Commands

```bash
pip install -r requirements.txt
python run.py --list                      # strategies and markets
python run.py --strategy darvas --market IN
python -m pytest tests                    # test suite
ruff check . && black --check .           # lint, as in .github/workflows/tests.yml
```
python backtest_screeners.py             # point-in-time screen backtest (see BACKTEST_FINDINGS.md)

Large data under `cache_seed/` is stored with Git LFS (`git lfs pull` to fetch).

## Layout

| Path | Purpose |
|---|---|
| `run.py`, `screener_kit.py` | CLI and facade: bootstrap, load, screen |
| `strategies/` | One module per screen (`darvas`, `piotroski`, `coffee_can`, `garp`, ...) returning `Result` |
| `universe_sources.py`, `data_sources.py`, `fetch_market_ohlc.py` | Universe lists and OHLC ingest (Yahoo with Stooq fallback) |
| `bhavcopy_history.py`, `bhavcopy_store.py` | India EOD ingest and LMDB store |
| `liquidity.py`, `market_calendar.py` | Turnover tiers and trading-day calendars |
| `cache_seed/` | Per-market parquet seeds (`cleaned_long_<MKT>.parquet`) |
| `reference_seed/` | Reference tables (industry benchmarks, factor data) |
| `tests/` | pytest suite |
| `walk_forward_backtest.py`, `backtest_screeners.py` | Point-in-time backtests |
| `warehouse/` | Adjustment-factor tables |

See `ARCHITECTURE.md` for the layered data flow and schemas.

## Data flow

sources -> ingest -> parquet/LMDB cache -> `screener_kit.load()` (liquidity pre-filter)
-> `strategies.<slug>.screen(stock)` -> `Result(pass, score, metrics)` -> `results/*.csv`.

## Technical hazards

- **Freshness is judged by trading session**, not file mtime, filename date or row count.
  A file written today can hold a stale session; compare the last bar date to the
  market's last trading day (see `market_calendar.py`).
- **India price data comes from the cleaned long-format cache only**
  (`cache_seed/cleaned_long_IN.parquet`). Do not substitute raw vendor tables or
  unreviewed exports; they have carried corporate-action and EPS-scaling errors.
- **yfinance split caveat**: `history(auto_adjust=True)` can return unadjusted prices for
  recent Japan/Korea splits even when `.splits` lists the event. Validate adjustments by
  series continuity, not by comparing windowed returns.
- **Never use `or` between DataFrames** (`a or b` raises on truthiness of a DataFrame).
  Use `a if a is not None and not a.empty else b`.
- **Darvas box excludes the current bar.** Form the box from prior bars only, otherwise
  the breakout bar defines its own ceiling and the signal is look-ahead.
- Backtests must be point-in-time: no fundamentals or prices dated after the decision day.
- Tickers must be exchange-qualified; never infer the exchange from the ticker shape.
- Secrets (API keys, cookies, logins) belong in environment variables or untracked local
  files, never in the repository.
