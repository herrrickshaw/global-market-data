# Bloomberg-Terminal-equivalent data sources (free / official)

A Bloomberg Terminal is an *aggregator* — its data ultimately comes from
exchanges, regulators, central banks, statistics agencies and news wires. Most of
those underlying feeds have **free, official, public** endpoints. This maps the
Bloomberg data categories to the public equivalents we use (or can use), so the
system approaches terminal-grade coverage without the terminal.

| Bloomberg data category | Public / official equivalent | Status here |
|-------------------------|------------------------------|-------------|
| US equity fundamentals (FA) | **SEC EDGAR XBRL** `companyfacts` / `frames` | ✅ `sec_fundamentals.py` |
| US filings / IPOs (CF, regulatory) | **SEC EDGAR** submissions & full-text search | ✅ universe + IPO monitor |
| Equity prices (EOD) | Exchange bhavcopy (IN), Yahoo, Stooq | ✅ `data_sources.py`, `bhavcopy_history.py` |
| Security master / universe | SEC, JPX, KRX, SGX, Eastmoney, Euronext, Damodaran | ✅ `universe_sources.py` |
| Industry valuation comps (RV) | **Damodaran (NYU Stern)** industry PE/ROE/beta | ✅ `reference_data.py` |
| Factor / risk model data | **Ken French** (Fama-French), AQR | ✅ `reference_data.py` |
| Macro & rates (ECO) | **FRED**, **World Bank**, **US Treasury fiscaldata** | ⚙️ reachable (see below) |
| FX (FXIP) | **ECB** Statistical Data Warehouse | ⚙️ reachable |
| Corporate actions / dividends | SEC EDGAR (`PaymentsOfDividends`), exchange feeds | ✅ via SEC fundamentals |
| News / sentiment (N, NI) | RSS wires (Moneycontrol/ET/CNBC/MarketWatch) | ✅ `sentiment_pipeline.py` |
| Estimates / consensus (EE) | — (proprietary; no free equivalent) | ❌ not available free |
| Supply-chain / SPLC, holdings (PORT) | — (proprietary) | ❌ |

### Verified reachable endpoints (no API key)
- **SEC EDGAR fundamentals** — `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`
  (503 GAAP concepts for AAPL) and `…/frames/us-gaap/{concept}/USD/CY{period}.json`
  (cross-section, ~1,900 companies/period). Requires a descriptive `User-Agent`.
- **World Bank** — `https://api.worldbank.org/v2/country/{c}/indicator/{ind}?format=json`
- **US Treasury** — `https://api.fiscaldata.treasury.gov/...` (yields, rates).
- **ECB** — `https://data-api.ecb.europa.eu/service/data/EXR/...` (FX).
- **FRED** — `https://api.stlouisfed.org/fred/...` (needs a free API key).
- IMF DataMapper returned 403 from this environment (skip / retry elsewhere).

## What this unlocks
`sec_fundamentals.fundamentals(ticker)` returns a strategy-ready dict (net income,
ROA, ROE, current ratio, debt ratios, CFO, EBIT, EPS growth, gross margin,
dividends, debt/capex history) from **audited filings** — so the **fundamental
strategies (Piotroski, Coffee Can, Magic Formula, GARP, Debt Reduction, …) run on
US stocks with official data and no Yahoo dependency**:

```python
import sec_fundamentals as sf, strategies as st, bhavcopy_store as store
from strategies.base import StockData
f  = sf.fundamentals("AAPL")
sd = StockData("AAPL", "US", ohlcv=store.get("AAPL"), fundamentals=f)
st.run_all(sd)          # Piotroski/GARP/etc. on SEC filing data
```

## Honest limits
- SEC XBRL covers **US filers only**. Non-US fundamentals would need each
  jurisdiction's regulator (e.g. UK Companies House, ESMA) or a vendor — there is
  no single free global filings feed.
- A few XBRL concept mismatches produce outliers (e.g. a holding-level vs
  consolidated figure); implausible ratios (gross margin outside 0–100%) are
  nulled, and strategies treat missing inputs as fail-safe.
- **Analyst estimates, consensus, ratings, and holdings** are Bloomberg's genuinely
  proprietary layers — no free equivalent; the system stays on hard fundamentals,
  prices, factors, macro and news.
