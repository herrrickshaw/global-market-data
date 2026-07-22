# Exchange Coverage — validated vs. Wikipedia "major stock exchanges"

Validated against
[List of major stock exchanges](https://en.wikipedia.org/wiki/List_of_major_stock_exchanges)
(top 20 by market cap) and
[countries by market capitalization](https://en.wikipedia.org/wiki/List_of_countries_by_stock_market_capitalization).
Key factor: **number of listed companies covered**. Counts are tickers in our
universe providers (`universe_sources.py`); ✅ = seed already fetched.

| # | Exchange (Wikipedia) | MIC | Market | Universe | Source | Seed |
|---|----------------------|-----|--------|---------:|--------|------|
| 1 | Nasdaq | XNAS | US | 9,278 | SEC EDGAR | ✅ |
| 2 | New York Stock Exchange | XNYS | US | (in US) | SEC EDGAR | ✅ |
| 3 | Shanghai | XSHG | CN | 5,188 | Eastmoney | ✅ |
| 4 | Japan Exchange (Tokyo) | XJPX | JP | 3,083 | JPX | ✅ |
| 5 | Euronext (7 venues) | XAMS… | EU | 852 | **Euronext live** | ✅ |
| 6 | Shenzhen | XSHE | CN | (in CN) | Eastmoney | ✅ |
| 7 | Hong Kong | XHKG | HK | 1,313 | Damodaran master | ⏳ fetching |
| 8 | Taiwan | XTAI | TW | 2,224 | Damodaran master | ⏳ |
| 9 | Korea Exchange | XKOS | KR | 2,597 | KRX KIND | ✅ |
| 10 | Bombay (BSE) | XBOM | IN | ~8,900 | NSE+BSE bhavcopy | ✅ |
| 11 | National (NSE) | XNSE | IN | (in IN) | bhavcopy | ✅ |
| 12 | Toronto (TSX/TSXV) | XTSE | CA | 2,372 | Damodaran master | ⏳ |
| 13 | London | XLON | UK | 897 | Damodaran master | ⏳ |
| 14 | Deutsche Börse (Xetra) | XFRA | DE | 535 | Damodaran master | ⏳ |
| 15 | Saudi Exchange | XSAU | SA | 376 | Damodaran master | ⏳ |
| 16 | Australian (ASX) | XASX | AU | 1,536 | Damodaran master | ⏳ |
| 17 | SIX Swiss | XSWX | CH | 195 | Damodaran master | ⏳ |
| 18 | Nasdaq Nordic/Baltic | XSTO… | SE/DK/FI | 852/154/180 | Damodaran master | ⏳ |
| 19 | Johannesburg | XJSE | ZA | 185 | Damodaran master | ⏳ |
| 20 | B3 (Brazil) | BVMF | BR | 249 | Damodaran master | ⏳ |

**All 20 major exchanges are now mapped to a universe provider.** Singapore
(SGX, 602) is also covered though outside the top 20.

## Coverage summary
- **Before this pass:** 7 markets (US, IN, JP, KR, CN, SG, EU) — Europe was only
  index-level (93) until the Euronext-live integration lifted it to 852.
- **After:** the 8 missing top-20 exchanges (HK, TW, CA, UK, DE, SA, AU, CH) plus
  the Nordic trio (SE/DK/FI), South Africa and Brazil now have providers, sourced
  from **Damodaran's company master** (48,156 firms with Country + Exchange:Ticker)
  mapped to yfinance suffixes (`.HK .TW .TWO .TO .V .CN .AX .SA .JO .SR .ST .CO
  .HE .L .DE .F .SW`).
- **Total universe across all markets: ~40,000 listed companies.**

## How tickers are derived (auditable)
`universe_sources.from_damodaran(country, allowed_exchanges)` filters the master
by country, keeps the requested exchange codes, and appends the yfinance suffix
(Hong Kong codes are zero-padded to 4 digits). Prices are then fetched via the
redundant `data_sources` chain (yahoo → stooq); tickers yfinance can't resolve
are simply dropped, so universe size ≥ fetched size.

## Notes
- Some Damodaran tickers (e.g. Canadian dotted symbols `A.H`, Taiwanese warrant
  codes) don't map cleanly to yfinance and will be dropped at fetch time — the
  fetched seed is the reliable count.
- Mainland-China official feed (Eastmoney) and India (bhavcopy) need no Damodaran
  fallback; they're already full official universes.
