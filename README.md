# Global Market Data

Multi-market equity price and fundamentals warehouse plus a point-in-time (no-lookahead) screening and
backtest toolkit. It holds roughly 10 years of daily OHLCV for about 20 markets (IN, US, JP, KR, CN, HK, TW,
CA, AU, UK, DE, SA, BR, CH, ZA, SE, FI, DK, SG, Euronext), a bhavcopy store for India, reference tables
(Damodaran, Fama-French) and 11 strategy screens. Outputs are CSV result tables, a Daily Market Brief
(HTML) and an ETL audit report (`reports/ETL_AUDIT.md`).

> Status: active (research data repo). Educational / research use only; not investment advice. Backtest
> edges are historical averages, not guarantees. No licence file yet — all rights reserved. Large data
> files are Git LFS objects.

## What it does
- Runs 11 built-in screens (`strategies/`: piotroski, coffee_can, magic_formula, bluest_blue_chips, debt_reduction, dividend_yield, golden_crossover, loss_to_profit, garp, darvas, cash_conversion_cycle) on any cached market via `run.py`.
- Keeps a long-term (`cache_seed/ltm/<MKT>.parquet`) and short-term (`cache_seed/cleaned_long*.parquet`) OHLCV store, updated by `market_memory.py` with idempotent upserts (newest bar wins).
- Ingests India EOD from official NSE/BSE bhavcopy (`bhavcopy_history.py`, `update_bhavcopy_daily.py`) into an LMDB store (`bhavcopy_store.py`).
- Fetches non-India prices through an ordered source chain (`data_sources.py`; default Yahoo-only, Stooq / EODHD / Groww opt-in).
- Gates every write through `ingest_gate.py` (drop duplicate `(Symbol, Date)` rows, compact float32/int32 dtypes, size report).
- Registers files as content-addressed, versioned batches and runs a weekly replication audit (`etl_registry.py`).
- Verifies tracked data against a SHA-256 manifest (`integrity.py --verify`).
- Headline result (`BACKTEST_FINDINGS.md`, 10-year point-in-time, 21-day forward): `rsi_oversold` is the most robust screen (positive edge in every market tested); `golden_crossover` is consistently weakest; India is momentum-friendly while US/KR/CN lean mean-reversion.

## Quick start
```bash
git lfs install && git clone https://github.com/herrrickshaw/global-market-data.git
cd global-market-data && git lfs pull
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py --list                    # strategies and markets
python run.py --strategy darvas --market IN
```
`run.py` bootstraps the committed seeds into a local store on first use, prints the result and saves it
to `results/`. `colab_quickstart.ipynb` does the same without local setup.

## How it works
```
stage            module(s)                                   output
ingest           universe_sources, bhavcopy_history,         raw bars per market
                 data_sources, fetch_market_ohlc
gate             ingest_gate (dedup, dtypes, size delta)     clean long frame
store            market_memory (LTM 5y / STM 1y),            cache_seed/*.parquet,
                 bhavcopy_store (LMDB)                       warehouse/ (parquet + DuckDB)
screen           screener_kit -> strategies/*, liquidity     result DataFrame / CSV
report           build_mailer -> send_mailer, run.py --brief Daily Market Brief (HTML)
audit            etl_registry, integrity                     warehouse/etl_registry.sqlite,
                                                             reports/ETL_AUDIT.md
```
Key decisions: Parquet (zstd) plus LMDB for fast keyed reads instead of CSV; every write passes
`ingest_gate`; ETL batches are named `BATCH-<source>-<YYYYMMDD>-<seq>` and a changed file becomes a new
version (old row marked superseded) while identical content at another path is linked as a duplicate.
`etl_registry.audit()` takes an injectable subprocess runner and `data_sources` fetchers take an
injectable HTTP session, so tests never touch the network. See `ARCHITECTURE.md` for the layered flow and
schemas.

## Repository layout
| Path | Purpose |
|---|---|
| `run.py`, `screener_kit.py` | CLI entry and facade (bootstrap / load / get / update / screen / custom_screen) |
| `strategies/` | 11 screen modules (`base.py` + one file each) |
| `market_memory.py`, `ingest_gate.py`, `bhavcopy_*.py`, `data_sources.py`, `market_calendar.py` | ingest, gate and store |
| `etl_registry.py`, `integrity.py`, `warehouse_etl_extras.py` | ETL registry, audits, non-OHLCV warehouse domains (cgd / energy / audit) |
| `backtest_screeners.py`, `walk_forward_backtest.py`, `backtest_*` | backtests |
| `cache_seed/`, `reference_seed/`, `warehouse/` | tracked data (Git LFS; not hand-edited) |
| `tests/` | pytest suite (synthetic frames, no network) |
| `schedule/` | launchd plist and morning dashboard script |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| NSE / BSE bhavcopy | India EOD | official files | exchange terms apply |
| SEC EDGAR | US fundamentals | public API | public |
| JPX / KRX / SGX / Euronext / Eastmoney | listed universes | public lists, partly scraped | check each site's terms |
| Yahoo Finance (yfinance), Stooq | prices outside India | API / CSV (Stooq is rate-limited) | unofficial, personal use |
| EODHD, Groww | optional prices | API key / entitlement required | dormant unless configured |
| Damodaran, Fama-French | industry multiples, factors | public downloads | cite the authors |
| screener.in | India cash-conversion cycle | scraped (login in `screener_in_auth.py`) | terms apply |

## Configuration
- `BHAV_CACHE`: bhavcopy cache directory (defaults to `~/Downloads/data/bhavcopy_cache`, `/content/cache` on Colab).
- `EODHD_API_KEY`: enables the EODHD source (unset = source returns nothing).
- `VCRUD_DB`: optional audit database used by `ingest_gate` (best-effort; failures are skipped).
- Mailer credentials (`GMAIL_USER`, `GMAIL_APP_PASSWORD`, `MAIL_TO`) come from the environment and are never committed.

## Testing
```bash
pip install pytest ruff black
pytest -q          # 63 tests, synthetic data only
ruff check . && black --check .
```

## Limitations and known issues
- **Freshness is judged by trading session, not file mtime.** Never grade a dataset by modified time, filename date or row count; compare the last bar to the last expected trading session (`market_calendar.py`).
- **India prices come from `cleaned_ohlcv` only** (the collision-safe NSE+BSE merge in `warehouse/bhavcopy/`); do not mix in raw NSE or BSE frames or other India panels.
- **yfinance `auto_adjust=True` misses some recent JP/KR splits.** Several scanners still call it (`full_us_market_scan.py`, `us_stocks_colab.py`); validate adjusted closes for JP/KR before trusting returns.
- **Never combine DataFrames with `or`** (`df_a or df_b` raises on truthiness); use explicit `is None` / `.empty` checks.
- Stooq returns an HTML limit page on bulk requests, so the default chain is Yahoo-only.
- Some docs (`QUICK_START.md`, several `SGX_*` / `DDD_*` files) describe unrelated projects and are pending cleanup; `CHANGELOG.md` records removals. Earlier docs mention `backtest.py`, which does not exist: use `backtest_screeners.py` / `walk_forward_backtest.py`.
- Several scripts hard-code maintainer paths (`~/market-pipeline`, `~/Downloads/...`); see the Phase 2 list in the refactor PR.

## Related repositories
- [`global-stock-screener`](https://github.com/herrrickshaw/global-stock-screener): same strategy library, about 1 year of cache, for live daily screening. This repo is the 10-year point-in-time side.

## Licence
No licence file yet — all rights reserved.
