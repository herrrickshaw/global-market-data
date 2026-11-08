
```markdown
# DATA_SOURCES.md - global-market-data

Last reviewed: 2026-11-08. Every dataset below states publisher, URL, terms, fields, coverage, refresh and caveats. "Collected by" names the script in this repo. Terms marked UNVERIFIED were not read from the publisher's own page.
Not investment advice. Third-party data remain under their owners' terms; this repository's licence covers only original code and derived analyses.

## 1. Price panels

### 1.1 Global equity daily OHLCV (cache_seed/ltm/<MKT>.parquet)
- Publisher: Yahoo Finance via `yfinance` (bulk download); fallback chain in `data_sources.py` (Stooq, EODHD optional).
- URL / terms: https://finance.yahoo.com ; https://legal.yahoo.com/us/en/yahoo/terms/otos/ (prohibits scraping / building competing databases without permission); yfinance project: https://github.com/ranaroussi/yfinance ("research and educational purposes ... personal use only").
- Fields: Date, Open, High, Low, Close, Volume, Symbol (FLOAT32 prices, INT64 volume).
- Coverage (tracked): AU BR CA CH DE DK EU FI HK SA SE SG TW UK ZA; 2025-06-26..2026-06-29; 2,900,809 rows. IN/US/JP/KR/CN panels exist only as gitignored local files.
- Refresh: ad hoc (last 2026-07-13/Aug-15 for IN).
- Caveats: one year, not ten; split/dividend adjustment follows Yahoo (not point-in-time); prices in local currency units (UK pence, ZA cents); zero-volume bars 3-32% by market; superseded by `warehouse/ohlcv/` (untracked).
- Terms status: RESTRICT; keep repository private or replace with exchange-official series.

### 1.2 India exchange bhavcopy (warehouse/bhavcopy/)
- Publisher: NSE (https://nsearchives.nseindia.com) and BSE (https://www.bseindia.com) daily end-of-day files.
- Terms: NSE/BSE data-use terms not retrieved (UNVERIFIED); treat redistribution as restricted.
- Files: `nse_raw.year=YYYY`, `bse_raw.year=YYYY` (raw UDiFF columns: TradDt, BizDt, Sgmt, Src, FinInstrmTp, FinInstrmId, ISIN, TckrSymb, SctySrs, XpryDt, StrkPric, OptnTp, FinInstrmNm, OpnPric, HghPric, LwPric, ClsPric, LastPric, PrvsClsgPric, ...); `cleaned_ohlcv.year=YYYY` (trade_date, symbol, open, high, low, close, volume).
- Coverage: 2025-06-09..2026-07-13/14; NSE 620,332 rows (2,704 ISINs in 2026), BSE 1,284,825 rows, cleaned 2026 605,355 rows / 7,196 symbols.
- Collected by: `bhavcopy_history.py`, `update_bhavcopy_daily.py`, `warehouse_etl_extras.py`.
- Caveats: "cleaned" still includes debt/bond series; unadjusted for splits/bonus (use `warehouse/adjustment_factors.parquet`: 793 factors 2015-10..2026-08; 88 heuristic).

## 2. Fundamentals
### 2.1 US: SEC EDGAR XBRL (cache_seed/fundamentals/US.parquet)
- Publisher: US SEC. API https://data.sec.gov/api/xbrl/companyfacts/ ; fair-access policy (declared User-Agent, rate limit) at https://www.sec.gov/developer (page returned 403 to the audit probe; UNVERIFIED).
- Terms: US government public information; cite SEC EDGAR.
- Fields: net_income, net_income_prev, revenue, revenue_5y_ago, roa, roa_prev, roe, cfo, ebit, current_ratio(+prev), debt_to_assets(+prev), ... 497 rows.
- Collected by: `sec_fundamentals.py`. Caveat: derived ratios, not as-reported filing dates; XBRL concept mismatches nulled (see BLOOMBERG_SOURCES.md).
### 2.2 India: yfinance snapshot (cache_seed/fundamentals/IN.parquet)
- Publisher: Yahoo Finance (see 1.1). 525 rows; fields pe, roe, debt_to_equity, net_income, revenue, eps_ttm, market_cap, gross_margin, profit_growth. Snapshot, not point-in-time. First rows are US tickers - verify before use. RESTRICT.
### 2.3 Screener.in screen outputs (cache_seed/public_screens/, india_ccc_screen.parquet)
- Publisher: Screener.in (https://www.screener.in). Terms https://www.screener.in/guides/terms/ : personal non-commercial transitory viewing only; no copying or mirroring.
- 26 screens + CCC; strings as displayed. RESTRICT - remove before any public release.
- Collected by: `public_screens.py` (polite pagination), `screener_in.py`.

## 3. Reference data
| Dataset | Publisher / URL | Terms | Fields | Coverage | Refresh |
|---|---|---|---|---|---|
| Damodaran industry PE/ROE/beta/margin (96 industries) and company list (48,156) | Aswath Damodaran, NYU Stern, https://pages.stern.nyu.edu/~adamodar/ | no licence stated; credit the author | Industry, Number of firms, PE, ROE, beta, margins; Company Name, Exchange:Ticker, Industry Group, Country | Global, annual (January) | annual |
| Fama-French US 3-factor | Kenneth R. French, https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html | (c) Fama & French; cite | Mkt-RF, SMB, HML, RF | 1926-07..2026-05 | monthly |
| Fama-French regional 5-factor + momentum | same | same | + RMW, CMA, WML; 6 regions | 1990-07..2026-08 (Emerging 1989-07) | monthly; `french_regional.py` |
| ECB euro reference rates | ECB, https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/ | reuse with source acknowledged | 29 currencies vs EUR | last 90 days (2026-07-20..2026-10-16) | daily; `ecb_fx.py` |
| JPX listed issues | Japan Exchange Group (xls) | UNVERIFIED | code, name, section, sector33/17, size tier | TSE | daily |
| EDINET code map | Japan FSA EDINET | Japanese gov. standard terms (UNVERIFIED) | edinet_code, ticker, sec_code, name, fye, industry | 3,828 filers | ad hoc |

## 4. Derived and operational tables
- `warehouse/regimes.parquet`: date, vix, vix_regime, indiavix, indiavix_regime; 4,222 days 2015-01-01..2026-07-23. Provenance of VIX series undocumented (likely Yahoo ^VIX / ^INDIAVIX): RESTRICT until confirmed.
- `warehouse/adjustment_factors*.parquet`: corporate-action factors (exchange announcements + yfinance cross-check).
- `warehouse/cgd/*`, `warehouse/energy/*`: CGD authorised geographical areas (PNGRB), retail outlet geolocations (upstream: sibling repo cng-cgd-retail-outlet-mapping; origin of coordinates UNVERIFIED), PNGRB gas demand projections 2030/2040.
- `cache_seed/{liquidity_index,market_liquidity,market_performance_5y,global_highlights}.parquet`: computed from the Yahoo-derived panels (as_of 2026-06-29..07-14).
- `claims.yaml`: ledger of research conclusions and staleness triggers (original work).

## 5. Known gaps and corrections to documentation
1. README "10 years / 20 markets / ~44M rows" is not what is committed (1 year, 15 markets). 2. LFS objects unreachable. 3. warehouse.duckdb views contain absolute local paths. 4. CHECKSUMS.sha256 out of sync. 5. No delisted-securities table despite "964 delisted" claim. 6. Bhavcopy ends 2026-07-13.

## 6. Collection method and provenance
All collectors are in this repo (`fetch_market_ohlc.py`, `bulk_seed.py`, `sec_fundamentals.py`, `ecb_fx.py`, `french_regional.py`, `public_screens.py`, `bhavcopy_history.py`). Open-source additions are verified by HTTP check before use (DATA_SOURCES entries dated 2026-10-17). News RSS collectors exist but no news text is stored.
```
