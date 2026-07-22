# Market Data & Strategy Modules — User Guide

A plain-English guide to **where the data comes from**, **how it's cached**, and
**what each module does**. Everything here is for educational / research use only —
**not investment advice**. Screener output is a mechanical filter, not a buy/sell
signal. Past results do not guarantee future returns. Consult a SEBI-registered
(or your jurisdiction's) advisor before investing.

---

## 1. The big picture

```
 OFFICIAL UNIVERSE  ──►  PRICE DATA (with backups)  ──►  CACHE  ──►  STRATEGIES
 universe_sources       data_sources / bhavcopy_history   *.parquet    strategies/
 (who is listed)        (what they traded at)             + LMDB store (what to buy-watch)
        │                                                      ▲
        └──────────────►  ipo_monitor (new listings)  ─────────┘
 REFERENCE BENCHMARKS:  reference_data  (Damodaran / Fama-French / AQR)
```

The design goal is **resilience**: if Yahoo Finance rate-limits us, the system
falls back to other sources, and India is sourced from the exchanges' own
official end-of-day files (no Yahoo at all).

---

## 0. Google Colab (zero setup)

Open **`colab_quickstart.ipynb`** in Colab and run top-to-bottom. It installs deps,
clones the repo *with the cached data* (Git LFS), bootstraps the store, and runs
example screens. Minimal cells:

```python
!pip install -q lmdb xlrd yfinance nsepython bseindia nse feedparser vaderSentiment investpy
!git lfs install && git clone --depth 1 https://github.com/herrrickshaw/global-stock-screener.git
%cd global-stock-screener
!git lfs pull
import os; os.environ['BHAV_CACHE'] = '/content/cache'
import screener_kit as kit; kit.bootstrap()
kit.screen('darvas', 'IN', top=15, min_turnover_usd=1_000_000)
```

> Standalone repo: **github.com/herrrickshaw/global-stock-screener** (toolkit at root).

Local install: `pip install -r requirements.txt`.

---

## 1a. Simplest usage — `screener_kit` (start here)

The committed seeds are a **starter kit**: after cloning, one call gives you ~1yr
of OHLCV for every market, then you pull fresh data on top.

```python
import screener_kit as kit

kit.bootstrap()                      # ONE TIME: committed seeds → cache + NoSQL store
kit.update("IN")                     # real-time: pull new official bhavcopy days
kit.update("US")                     # real-time: pull recent bars (yahoo→stooq)

kit.get("RELIANCE")                  # OHLCV DataFrame (fast, O(1))
kit.markets()                        # ['IN','US','JP','KR','CN','SG','EU']

kit.screen("darvas", "IN", top=20)   # run a built-in strategy across a market
kit.custom_screen(                   # YOUR OWN parameters (no coding a strategy)
    {"above_200dma": ("==", True), "rsi14": ("<", 65), "dist_52w_high": ("<", 10)},
    market="US", rank_by="ret_126", top=15)
```

That's the whole workflow. The sections below explain what's underneath.

---

## 2. Where the data comes from

### 2.1 Universe — *which stocks exist in each market* (`universe_sources.py`)
Prefers **official / government / exchange** sources over scraping:

| Market | Source | Type | ~Count |
|--------|--------|------|-------|
| US | **SEC EDGAR** `company_tickers.json` | US government | 10,433 |
| India | **NSE + BSE bhavcopy** symbols | Exchange official | ~8,900 |
| Japan | **JPX** listed-issue master (`.T`) | Exchange official | 3,084 |
| Korea | **KRX KIND** KOSPI+KOSDAQ (`.KS/.KQ`) | Exchange official | 2,606 |
| China | **Eastmoney** A-share board list (`.SS/.SZ`) | Market data | 5,534 |
| Singapore | **SGX** securities API (`.SI`) | Exchange official | 619 |
| Europe | STOXX/DAX/CAC/AEX/FTSE/SIX large-caps | Index constituents | 94 |

> Europe has no single free official all-share feed (LSE/Euronext/Xetra are
> separate), so it currently ships index-level large-caps. Everything else is the
> full official universe.

### 2.2 Prices — *what they traded at*, with redundancy

| Layer | Module | Notes |
|-------|--------|-------|
| **India primary** | `bhavcopy_history.py` | Official **NSE + BSE bhavcopy** EOD files. No Yahoo → no rate limits. Builds ~1yr OHLCV for the whole market. |
| **Other markets** | `data_sources.py` | Fallback chain: **Yahoo → Stooq**. Each source only retries the tickers the previous one missed, so a Yahoo 429 no longer halts collection. |
| **Calendar** | `market_calendar.py` | NSE/BSE trading-holiday calendar (from `nsepython.nse_holidays`) so fetchers skip weekends **and** holidays instead of probing dead dates. |

### 2.3 New listings / IPOs (`ipo_monitor.py`)
Source-agnostic: each run compares the **current official universe** to the last
saved snapshot — newly appearing tickers are reported as new listings and logged
(dated) to `new_listings.json`. As reliable as each exchange's own listed-issue
feed.

### 2.4 Reference benchmarks (`reference_data.py`)
Academic / practitioner datasets, downloaded once and cached:

| Source | What | Used for |
|--------|------|----------|
| **Aswath Damodaran (NYU Stern)** | Industry PE / ROE / beta / margin tables; full company→industry list (`indname.xls`, **48,156 firms**) | Relative-valuation thresholds in GARP & Bluest Blue Chips; sector tagging |
| **Kenneth French (Dartmouth)** | Fama-French 3-factor monthly returns | Factor attribution / backtest benchmarking |
| **AQR Capital** | Factor datasets (value, momentum, QMJ, BAB) | Reference (page is JS-rendered; direct file URLs needed per set) |
| **Prof. J.R. Varma's blog** | Indian-markets commentary | Qualitative reference reading |

---

## 3. How the data is cached (fast + small)

`bhavcopy_history.py` keeps several layers, fastest-first:

1. **Raw daily CSVs** — one per exchange/day (can be vacuumed; redundant once assembled).
2. **`assembled_long.parquet`** — consolidated raw rows; only *new* dates are fetched & appended.
3. **`cleaned_long.parquet`** — post-cleaning frame; warm runs skip the ~30s pivot/clean (the **fast path**).
4. **`no_data_dates.json`** — negative cache of holidays / unpublished dates.
5. **`ohlcv.lmdb`** (`bhavcopy_store.py`) — embedded **NoSQL key-value store**, `symbol → zstd-Arrow OHLCV`, for O(1) single-symbol reads (~12 ms) without loading the whole parquet.

A warm full run drops from ~50s to ~6s. All seeds are float32 + zstd parquet,
tracked in **Git LFS**.

---

## 4. Module reference (what each file does)

| Module | One-line purpose |
|--------|------------------|
| `screener_kit.py` | **Start here.** Simple facade: `bootstrap/update/get/load/screen/custom_screen` |
| `custom_screener.py` | Evaluate stocks on YOUR parameters (technical metrics computed from OHLCV) |
| `universe_sources.py` | Full tradable universe per market from official sources |
| `bhavcopy_history.py` | Build/refresh ~1yr OHLCV for India from NSE+BSE bhavcopy; multi-layer cache |
| `data_sources.py` | Redundant OHLC fetch with `yahoo → stooq` fallback chain |
| `market_calendar.py` | NSE/BSE trading-holiday calendar (skip non-trading days) |
| `bhavcopy_store.py` | LMDB NoSQL store: `get/get_many/load_all/symbols/info`, `build`, `vacuum` |
| `fetch_market_ohlc.py` | Pull full universe + multi-source OHLC → per-market seed parquet |
| `build_market_seeds.py` | Build per-market seeds from the existing per-ticker OHLC cache |
| `ipo_monitor.py` | Detect new listings by diffing universe snapshots |
| `reference_data.py` | Damodaran / Fama-French / AQR benchmark & company data |
| `scan_bhavcopy.py` | Full price-screener run on bhavcopy data (no Yahoo) |
| `strategies/` | The 10 screening strategies (see §5) |

### Cached / committed data files
| Path | Contents |
|------|----------|
| `cache_seed/cleaned_long.parquet` | India (~1yr OHLCV, bhavcopy) |
| `cache_seed/cleaned_long_<MKT>.parquet` | US / JP / KR / CN / SG / EU seeds |
| `reference_seed/damodaran_companies.parquet` | 48,156-firm global company→industry list |
| `reference_seed/damodaran_{pe,roe,beta,margin}.parquet` | Industry benchmark tables |
| `reference_seed/french_ff3.parquet` | Fama-French 3-factor returns |

---

## 5. The 10 strategies (`strategies/`)

Each is a self-contained module exposing `META` + `screen(StockData) -> Result`.
`needs` = `"price"` (OHLCV only) or `"fundamentals"`.

| # | Strategy | Idea | Needs |
|---|----------|------|-------|
| 1 | **Piotroski Score** | 9-point fundamental quality (profitability/leverage/efficiency); ≥7 = strong | fundamentals |
| 2 | **Coffee Can** | Mukherjea: consistent high-ROE compounders, low debt, steady revenue growth | fundamentals |
| 3 | **Magic Formula** | Greenblatt: high earnings yield (EBIT/EV) + high return on capital | fundamentals |
| 4 | **Bluest of the Blue Chips** | >₹3000cr, high profit growth + ROE, valuation ≤ industry PE | fundamentals |
| 5 | **Debt Reduction** | Falling debt across years + capacity expansion (capex/gross block rising) | fundamentals |
| 6 | **Highest Dividend Yield** | Consistent payers ranked by current yield | fundamentals |
| 7 | **Golden Crossover** | 50-DMA crosses above 200-DMA | price |
| 8 | **Loss to Profit Turnaround** | Prior loss-making, latest quarter swings to profit | fundamentals |
| 9 | **GARP** | High earnings growth at a non-inflated P/E (PEG≤1 or PE≤industry) | price+fund |
| 10 | **Darvas Scan** | Price-volume breakout within 10% of 52-week high on heavy volume | price |

```python
import strategies as st, bhavcopy_store as store
df = store.get("RELIANCE")                     # OHLCV from the NoSQL store
sd = st.StockData("RELIANCE", market="IN", ohlcv=df,
                  fundamentals={"roe": 22, "pe": 18, "eps_growth": 25, ...})
results = st.run_all(sd)                        # {slug: Result}
print(results["darvas"].passed, results["garp"].metrics)
```

---

## 6. Quick start

```bash
# refresh India from official bhavcopy (fast path if already cached)
python3 bhavcopy_history.py 400

# full price screener on bhavcopy (no Yahoo)
python3 scan_bhavcopy.py

# fetch a market's full universe with source fallback → seed parquet
python3 fetch_market_ohlc.py US

# detect new listings / IPOs across all markets
python3 ipo_monitor.py

# reference data
python3 -c "import reference_data as r; print(r.industry_metric('Bank (Money Center)','pe'))"
```

### Docker
```bash
docker build -t bhavcopy-screener -f Dockerfile .   # builds the LMDB store at image time
docker run --rm bhavcopy-screener                   # full bhavcopy scan, offline-ready
```

---

## 7. Reliability notes (read before relying on it)

- **India is the most robust** — official exchange EOD, no Yahoo dependency.
- **Other markets depend on Yahoo first**; Stooq is the free backup but is
  geo/rate-limited in some environments. For price-level redundancy beyond Stooq,
  exchange EOD files per market can be added to `data_sources.py`.
- **Fundamentals** for strategies 1–6, 8–9 require a fundamentals feed (yfinance
  financials or the scan's Fundamentals sheet); price strategies (7, 10) run on
  the cache directly.
- **AQR** datasets are JavaScript-rendered — pulling a specific set needs its
  direct `.xlsx` URL.
- Bulk caches (LMDB, raw CSVs) are regenerable and kept **local**; only the
  compact seed/reference parquets are committed (via **Git LFS**).
