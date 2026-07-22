# Complete SGX Stock Market Scan
## All Singapore Exchange Listed Securities (175+ stocks)

**Scan Started**: 2 June 2026, ~14:55  
**Expected Duration**: 15-25 minutes (175 stocks with all scans)  
**Status**: ⏳ Running...

---

## 📊 Scope

This is a **comprehensive scan of the entire Singapore Exchange** covering:

### Stock Categories Included

| Category | Count | Examples |
|----------|-------|----------|
| **STI-30 Blue Chips** | 29 | DBS, UOB, Singtel, Keppel, Singapore Airlines |
| **REITs** | 15+ | Mapletree Commercial, Ascendas, etc. |
| **Banks & Finance** | 8+ | UOB, DBS, OCBC, etc. |
| **Utilities & Infrastructure** | 8+ | Keppel, Power assets, etc. |
| **Technology & Semiconductors** | 12+ | Various tech stocks |
| **Manufacturing & Engineering** | 10+ | Industrial & SMEs |
| **Retail & Consumer** | 8+ | Consumer goods, retail |
| **Marine & Shipping** | 5+ | Shipping companies |
| **Oil & Gas** | 3+ | Energy sector |
| **Properties & Real Estate** | 8+ | Property developers |
| **Healthcare & Pharma** | 5+ | Healthcare companies |
| **Additional Active Stocks** | 60+ | Growth stocks, cyclicals, micro-caps |

**Total**: ~175 active SGX-listed securities (as of June 2026)

---

## 📈 Expected Output

### Excel File Structure

**File**: `sg_stock_data/sg_stock_report_YYYYMMDD_HHMMSS.xlsx`

**Sheets**:
1. **Summary** (1 row per stock, ~160-170 successful stocks)
   - Columns: Symbol | Company | Sector | CMP | Change% | P/E | P/B | Div Yield | 52W High | 52W Low | Volume | Darvas Signal | Piotroski Score | PEGY Ratio | Breakout Signal

2. **Per-Stock Detail Sheets** (one per successful stock)
   - Quote & Valuation section
   - Darvas Box scan results
   - Piotroski F-Score (0–9)
   - PEGY Ratio & Breakout Analysis

### Data Integrity

**Expected Success Rate**: ~90-95% (150-165 stocks)
- ✅ Successful: Active SGX-listed companies with yfinance data
- ⚠️ Failed: Delisted stocks, IPO wait-list, suspended trading

**Delisted/Unavailable** (handled gracefully):
- TSM.SI, 5ZB.SI, S51.SI, VOID.SI, F3L.SI, CCHT.SI (flagged in summary)
- Script skips these and continues with remaining stocks

---

## 🎯 Analysis Metrics (All 175 Stocks)

For each successfully analyzed stock:

✅ **CMP** — Current Market Price (SGD)
✅ **Darvas Box** — Technical breakout signal
✅ **Piotroski F-Score** — Fundamental strength (0–9)
✅ **PEGY Ratio** — Value metric (PEG + dividend yield)
✅ **Breakout Signal** — Trend vs 200-day MA
✅ **Dividend Yield** — Income component
✅ **Valuation** — P/E, P/B, PEG ratios
✅ **Corporate Actions** — Recent dividends, ex-dates

---

## 🔍 How to Use the Results

### Filter for Value Opportunities (Long-term)
```excel
Filter by:
  Piotroski Score ≥ 7
  PEGY Ratio < 50
  Dividend Yield ≥ 2%
  Market Cap > S$500M
```

### Find Momentum Breakouts (3–6 months)
```excel
Filter by:
  Darvas Signal = "BREAKOUT_BUY"
  Distance from 200MA > 5%
  Volume > 1M shares
```

### Identify Dividend Income (2–5 years)
```excel
Sort by Dividend Yield (descending)
Filter by:
  Piotroski ≥ 4 (sustainable)
  Div Yield ≥ 3%
  Market Cap > S$1B
```

---

## ⚡ Performance Notes

**Total Stocks**: 175  
**Average Time/Stock**: ~5–7 seconds (with scans)  
**Total Expected Time**: 15–25 minutes  
**Network**: yfinance API rate limits (~2000 requests/hour)  
**Graceful Handling**: Failed stocks logged, report continues

---

## 📊 Expected Results Preview

**Sample Output (Hypothetical)**:

| Symbol | Company | Sector | CMP | Change% | P/E | Darvas | Piotroski | Div Yield |
|--------|---------|--------|-----|---------|-----|--------|-----------|-----------|
| D05 | DBS Group | Finance | 64.53 | +3.0% | 16.85 | BUY | 4/9 | 2.43% |
| BN4 | UOB | Finance | 32.10 | +1.2% | 12.50 | IN_BOX | 6/9 | 3.15% |
| U11 | Singtel | Telecom | 3.02 | -0.5% | 14.20 | SELL | 5/9 | 4.80% |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |

---

## ✅ Completion Checklist

- [ ] Excel file generated (sg_stock_report_*.xlsx)
- [ ] Summary sheet: ~160-170 stocks
- [ ] Detail sheets: One per successful stock
- [ ] All scans included (Darvas, Piotroski, PEGY)
- [ ] File saved to sg_stock_data/
- [ ] Report ready for analysis

---

## 🚀 Next Steps (After Scan Completes)

1. **Open Excel file** → sg_stock_data/sg_stock_report_*.xlsx
2. **Review Summary sheet** → 160-170 stocks analyzed
3. **Apply filters** → Use one of the 3 strategies above
4. **Deep dive** → Click into detail sheets for specific stocks
5. **Take action** → Buy, hold, or monitor selected stocks

---

## 📚 Reference

- **Darvas Box**: Nicolas Darvas (1960) — Technical momentum
- **Piotroski F-Score**: Joseph Piotroski (2000) — Fundamental strength
- **PEGY Ratio**: PEG + Dividend Yield variant — Value metric
- **200-day MA**: Trend-following entry signal

---

## 📞 Support

**If the scan fails**:
- Check internet connection (yfinance requires live data)
- Reduce stock list (use --sti-top15 for quick test)
- Check for yfinance rate limiting (wait 1 hour, retry)

**If Excel looks incomplete**:
- Reload the file (Excel cache issue)
- Check summary sheet for failed stocks
- Use --sti-top15 to verify script works

---

**Generated**: 2 June 2026  
**Scan Type**: Complete SGX Equity Universe  
**Expected Completion**: ~15:10–15:25 SGT

Happy analyzing! 📊
