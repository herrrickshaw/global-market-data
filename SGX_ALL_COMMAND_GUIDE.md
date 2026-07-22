# Complete SGX Analysis - Command Guide

## 🚀 New Feature: `--sgx-all` Flag

Run analysis on **ALL 175 SGX-listed stocks** with comprehensive scans.

---

## 📊 Commands

### **Complete SGX Scan (All 175 Stocks)**
```bash
# With all scans + Excel output (30–35 min)
python3 sg_stock_daily_report.py --sgx-all --scans --output excel

# Just price quotes + Excel (10–15 min) 
python3 sg_stock_daily_report.py --sgx-all --output excel

# Text output only (no Excel)
python3 sg_stock_daily_report.py --sgx-all --scans
```

### **Batch Options (Existing)**
```bash
# STI-30 (full Straits Times Index)
python3 sg_stock_daily_report.py --sti30 --scans --output excel

# STI Top 15 (quick scan)
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

### **Single Stock (Existing)**
```bash
# DBS (D05) with scans
python3 sg_stock_daily_report.py D05 --scans

# UOB (BN4) text output
python3 sg_stock_daily_report.py BN4
```

---

## ⏱️ Expected Duration

| Command | Stocks | Time | File Size |
|---------|--------|------|-----------|
| `--sgx-all --scans` | 175 | **30–35 min** | 40–60 KB |
| `--sgx-all` (no scans) | 175 | 10–15 min | 30–40 KB |
| `--sti30 --scans` | 30 | 6–10 min | 25–35 KB |
| `--sti-top15 --scans` | 15 | 3–5 min | 20–25 KB |
| Single stock `--scans` | 1 | 15 sec | — |

---

## 📈 Stock Categories Covered (175 Total)

- **STI-30 Blue Chips** (29) — DBS, UOB, Singtel, Keppel, etc.
- **REITs** (15+) — Mapletree, Ascendas, etc.
- **Banks & Finance** (8+)
- **Utilities & Infrastructure** (8+)
- **Technology & Semiconductors** (12+)
- **Manufacturing & Engineering** (10+)
- **Retail & Consumer** (8+)
- **Marine & Shipping** (5+)
- **Oil & Gas** (3+)
- **Healthcare & Pharma** (5+)
- **Properties & Real Estate** (8+)
- **Growth & Cyclicals** (60+)
- **Micro-caps & SMEs** (40+)

---

## 📊 What You Get

### Summary Sheet (1 row per stock)
```
Symbol | Company | Sector | CMP | Change% | P/E | P/B | Div Yield
52W High | 52W Low | Volume | Darvas Signal | Piotroski Score
PEGY Ratio | Breakout Signal
```

### Detail Sheets (Per-Stock Analysis)
Each stock gets its own sheet with:
- **Quote & Valuation**: CMP, P/E, P/B, Dividend Yield, Market Cap
- **Darvas Box**: Signal (BUY/SELL), Box Top, Box Bottom
- **Piotroski F-Score**: 0–9 score + interpretation
- **PEGY Ratio**: Value metric + Breakout signal

---

## 🔍 Analysis Metrics

For **each stock**, you get:

| Metric | Purpose | Scale | Example |
|--------|---------|-------|---------|
| **CMP** | Current price | SGD | S$64.53 |
| **Darvas Signal** | Technical momentum | BUY/SELL/IN_BOX | BREAKOUT_BUY |
| **Piotroski Score** | Fundamental strength | 0–9 | 4/9 (MODERATE) |
| **PEGY Ratio** | Value metric | Higher = better | 519.6 |
| **Div Yield** | Income component | % | 2.43% |
| **200-day MA** | Trend indicator | Distance % | +16.12% (uptrend) |

---

## 🎯 How to Filter

Once you have the Excel file:

### Find Value Stocks
```excel
Filter by:
  Piotroski Score ≥ 7  ← Strong fundamentals
  PEGY Ratio < 50       ← Good value
  Dividend Yield ≥ 2%   ← Reasonable income
  Market Cap > S$500M   ← Liquidity
```

### Find Momentum Breakouts
```excel
Filter by:
  Darvas Signal = "BREAKOUT_BUY"  ← Price > resistance
  Distance from 200MA > 5%         ← Above trend
  Volume > 1M shares               ← Confirmation
```

### Find Dividend Income
```excel
Sort by: Dividend Yield (descending)

Filter by:
  Dividend Yield ≥ 3%    ← Good income
  Piotroski Score ≥ 4    ← Sustainable
  Market Cap > S$1B      ← Stability
```

---

## 💡 Tips for Using the Output

1. **Start with Summary Sheet**
   - Sort by P/E, Dividend Yield, or Piotroski Score
   - Get a quick overview of the best candidates

2. **Click into Detail Sheets**
   - Examine the Darvas Box chart visually
   - Review the 9 Piotroski criteria
   - Check recent dividend history

3. **Cross-Reference Multiple Metrics**
   - High Piotroski + BREAKOUT_BUY = strong buy setup
   - High Div Yield + Above 200MA = income + momentum
   - Low PEGY + Strong Fundamentals = value opportunity

4. **Monitor Over Time**
   - Run weekly to track Darvas breakouts
   - Track Piotroski changes quarterly
   - Monitor dividend announcements

---

## ❌ Expected Issues & Fixes

### Issue: "Too many requests" Error
**Solution**: Try `--sti-top15` instead of `--sgx-all`
```bash
# Quick test first
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel

# Then retry full scan later (wait 1 hour)
python3 sg_stock_daily_report.py --sgx-all --scans --output excel
```

### Issue: Some Stocks Missing from Excel
**Expected**: 5–10 stocks are delisted or unavailable
- Script logs these and continues
- Check final summary: "Done. 160 OK, 10 failed" ← This is normal!

### Issue: Excel File Looks Incomplete
**Solution**: 
1. Close and reopen the file
2. Check Summary sheet (should have 160–170 rows)
3. If still issues, re-run: `python3 sg_stock_daily_report.py --sgx-all --scans --output excel`

---

## 📚 Reference: What Each Scan Measures

### **Darvas Box (Technical)**
- **Detects**: Momentum breakouts (Nicolas Darvas, 1960)
- **Signal**: BUY when price breaks above resistance
- **Outlook**: 3–6 months

### **Piotroski F-Score (Fundamental)**
- **Measures**: Financial strength (0–9 scale)
- **Components**: ROA, OCF, Debt, Liquidity, Margins, Turnover
- **Outlook**: 1–2 years

### **PEGY Ratio (Value + Dividend)**
- **Formula**: PEG Ratio + (Dividend Yield × 100)
- **Insight**: Higher = better value, especially for income
- **Outlook**: 2–5 years (buy & hold)

---

## 🚀 Quick Start

```bash
# 1. Run the complete SGX scan (30 min)
python3 sg_stock_daily_report.py --sgx-all --scans --output excel

# 2. Wait for completion (notification will arrive)

# 3. Open the Excel file
open sg_stock_data/sg_stock_report_20260602_*.xlsx

# 4. Apply one of the 3 strategies above to filter

# 5. Deep dive into promising stocks
```

---

## 📞 Questions?

**Q: Which should I use—`--sgx-all` or `--sti-top15`?**
- `--sti-top15`: Quick scan, 3–5 min, blue chips only
- `--sgx-all`: Complete universe, 30 min, 175 stocks

**Q: Can I run this in Google Colab?**
Yes! Just use the Python API:
```python
from sg_stock_daily_report import run_batch, SGX_ALL
results = run_batch(symbols=SGX_ALL, run_scans=True, output_format="excel")
```

**Q: How often should I refresh the data?**
- **Daily**: Darvas breakouts (technical)
- **Weekly**: Price changes, volume, breakout signals
- **Quarterly**: Piotroski F-Score (financials)

**Q: Which stocks should I prioritize?**
Start with the 3 strategies above based on your investment style:
- Value: Piotroski ≥ 7 + PEGY < 50
- Momentum: Darvas BUY + Above 200MA
- Income: Div Yield ≥ 3% + Sustainable

---

## 📊 Example Output Preview

**Summary Sheet (First 5 rows)**:

| Symbol | Company | Sector | CMP | Change% | P/E | PEGY | Darvas | Piotroski | Div Yield |
|--------|---------|--------|-----|---------|-----|------|--------|-----------|-----------|
| D05 | DBS | Finance | 64.53 | +3.0% | 16.85 | 519.6 | BUY | 4/9 | 2.43% |
| BN4 | UOB | Finance | 32.10 | +1.2% | 12.50 | 45.2 | IN_BOX | 6/9 | 3.15% |
| U11 | Singtel | Telecom | 3.02 | -0.5% | 14.20 | 38.9 | SELL | 5/9 | 4.80% |
| C6L | Keppel | Conglom | 5.48 | +2.1% | 11.30 | 42.1 | BUY | 7/9 | 3.22% |
| C38U | Map Com | REIT | 2.15 | +0.3% | 8.90 | 25.6 | IN_BOX | 6/9 | 5.67% |

---

**Generated**: 2 June 2026  
**Last Updated**: For use with sg_stock_daily_report.py v1.1+

Happy analyzing! 📈
