# Data sources — open, keyless additions

Existing sources are listed in [README.md](README.md#data-sources-all-free--official)
and [EXCHANGE_COVERAGE.md](EXCHANGE_COVERAGE.md). This file records sources added or
rejected on 2026-10-17. Each was checked with `curl` for a real response before a
collector was written.

## Added

| Source | Gap it fills | Verified | Collector | Output |
|---|---|---|---|---|
| **Kenneth French Data Library, regional 5-factor + momentum** (`mba.tuck.dartmouth.edu/.../ken.french/ftp/<Region>_5_Factors_CSV.zip`, `<Region>_MOM_Factor_CSV.zip`) | `reference_seed/` holds only the US FF3 series (`french_ff3.parquet`, 1,199 rows) although the panel spans 20 markets; no regional factors for non-US alpha benchmarks | HTTP 200 for Developed, Developed_ex_US, Europe, Japan, Asia_Pacific_ex_Japan, Emerging; 2,616 rows total, 1990-07..2026-08 (Emerging from 1989-07) | [`french_regional.py`](french_regional.py) | `reference_open/french_regional_monthly.parquet` (54 KB) |
| **ECB euro reference rates** (`ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml`) | `liquidity.py` converts to USD with a live yfinance pull; no official, dated rate history is committed | HTTP 200, 1,885 rows, 65 dates (2026-07-20..2026-10-16), 29 currencies incl. USD/JPY/KRW/INR/CNY/GBP/ZAR/BRL | [`ecb_fx.py`](ecb_fx.py) | `reference_open/ecb_fx_hist_90d.csv` (42 KB) |

Notes:
- Outputs go to a new `reference_open/` directory on purpose: `integrity.py --verify`
  flags any tracked file under `cache_seed/` or `reference_seed/` that is absent from
  the manifest, and the manifest was not regenerated (it already reports mismatches
  for existing files, unrelated to this change).
- French values are percent per month as published; `-99.99` is converted to NaN.
  Momentum (`WML`) starts 1990-11 (Emerging 1990-01), and no momentum file was fetched
  for Developed_ex_US, so its `WML` is NaN. RMW/CMA have some missing months (24 / 36
  NaNs across regions).
- ECB rates are EUR-based reference rates on TARGET business days, not tradable
  quotes; the ECB set has no TWD or SAR (checked), so those two of the 20 markets still
  need another source.
- Neither collector is wired into the backtest or `liquidity.py`; they were run once at
  low volume and no existing data was modified. Nothing was backtested.

## Candidates checked and rejected
- **Nager.Date public holidays** (`date.nager.at/api/v3/PublicHolidays/{year}/{CC}`):
  keyless and responds for US/JP/KR/DE/GB, but returned no parseable JSON for IN, and
  these are public holidays, not exchange calendars, so it cannot replace
  `market_holidays.py`. `market_holidays.py` header states its holiday sets cover
  2025-2026 only; an exchange-official 2027 source was not found in this pass.
- **SEC XBRL frames** (`data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2025Q4I.json`):
  responded HTTP 200 (823 KB) but `sec_fundamentals.py` already uses SEC `companyfacts`,
  so it was not added.
