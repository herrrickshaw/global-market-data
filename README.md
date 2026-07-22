# 📊 Global Market Data — Deep 10-Year Point-in-Time Backtest Engine

Deep, multi-market historical equity data (10 years, 20 markets, ~44M OHLCV rows)
plus the **point-in-time, no-lookahead backtest engine** that turns it into evidence
on which screens actually work, where.

> ⚠️ **Educational / research only. NOT investment advice.** Backtest edges are
> historical averages, not guarantees. Past performance does not guarantee future
> returns. Consult a SEBI-registered (or your jurisdiction's) advisor.

---

## How this differs from [`global-stock-screener`](https://github.com/herrrickshaw/global-stock-screener)

Same strategy library and market coverage, different job:

| | `global-market-data` (this repo) | `global-stock-screener` |
|---|---|---|
| **Data depth** | 10 years, LFS-committed (`cache_seed/ltm/*.parquet`) | ~1 year cached, optimized for a fast clone |
| **Purpose** | Point-in-time backtesting — *did this screen actually beat the market, historically?* | Live screening — *what passes the filter today?* |
| **Output** | Edge/win-rate tables per market (`BACKTEST_FINDINGS.md`) | Ranked stock lists, daily mailer |

If you want today's screen results, use `global-stock-screener`. If you want to know
whether a screen has a real historical edge before trusting it, this is that repo.

---

## Headline finding (`BACKTEST_FINDINGS.md`, 10-year point-in-time, 21-day forward)

1. **`rsi_oversold` (mean-reversion) is the most robust screen** — positive edge in
   every market tested, #1 in US/KR/CN/JP.
2. **`golden_crossover` is consistently the weakest** — negative/near-zero edge
   everywhere; the classic 50/200-DMA cross did not add value out-of-sample.
3. **India is momentum-friendly** (`near_high` +1.33pp, `momentum` +1.18pp) while
   the **US/KR/CN lean mean-reversion** — the edge is regime/market-dependent, not
   universal.

Full ranked table and methodology in [`BACKTEST_FINDINGS.md`](BACKTEST_FINDINGS.md).
Architecture and data schema in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Run it

```bash
git lfs install && git clone https://github.com/herrrickshaw/global-market-data.git
cd global-market-data && git lfs pull
pip install -r requirements.txt
python run.py --list                  # all strategies & markets
python run.py --strategy darvas --market IN
python backtest.py                    # re-run the point-in-time backtest (run_all)
```

Or open [`colab_quickstart.ipynb`](colab_quickstart.ipynb) and Runtime → Run all —
no local setup, no API keys.

`run.py` auto-bootstraps the committed cache into a local store on first use, then
prints results and saves them to `results/`.

---

## What's inside

- **20 markets:** IN, US, JP, KR, CN, HK, TW, CA, AU, UK, DE, SA, BR, CH, ZA, SE, FI, DK, SG, Euronext (`cache_seed/ltm/*.parquet`)
- **11 strategies:** `piotroski, coffee_can, magic_formula, bluest_blue_chips, debt_reduction, dividend_yield, golden_crossover, loss_to_profit, garp, darvas, cash_conversion_cycle`
- **Point-in-time backtest engine** (`backtest.py`) — no-lookahead, equal-weight market benchmark, glitch-bar filtering
- **Reference data**: Damodaran industry multiples, Fama-French factors (`reference_seed/`)
- **Tamper-evidence manifest**: `integrity.py --verify` checks every tracked file against `cache_seed/CHECKSUMS.sha256`

## Data sources (all free / official)

Exchange bhavcopy (NSE/BSE), SEC EDGAR (US fundamentals), JPX/KRX/SGX/Euronext/
Eastmoney (universes), Yahoo→Stooq (prices, with fallback), Damodaran & Fama-French
(reference), screener.in (India CCC).

## Repo layout

```
run.py                 one-command entry (auto-bootstraps, prints + saves results)
backtest.py             point-in-time backtest engine (run_all)
screener_kit.py         facade: bootstrap / load / get / update / screen / custom_screen
strategies/             11 strategy modules (base.py + one file each)
bhavcopy_history.py     India EOD ingest + cache        bhavcopy_store.py  LMDB store
data_sources.py         yahoo→stooq fallback            market_calendar.py trading days
universe_sources.py     per-market listed universe      fetch_market_ohlc.py  seeds
sec_fundamentals.py     US fundamentals (SEC EDGAR)      screener_in.py  India CCC
reference_data.py       Damodaran / Fama-French          integrity.py  manifest verify
cache_seed/  reference_seed/   committed 10-year data (Git LFS)
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full layered data flow and schema.

---

> **Note (2026-07-13):** this README replaces a stray copy of `global-stock-screener`'s
> README that had ended up here via a shared-working-directory mixup. Some
> docs in this repo (`QUICK_START.md`, several `SGX_*`/`DDD_*` files) still describe
> unrelated projects from the same contamination pattern and are pending cleanup.
