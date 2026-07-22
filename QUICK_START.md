# Singapore Stock Analyzer — Quick Start Guide

## 📦 What You Have

```
/Users/umashankar/Downloads/
├── sg_stock_daily_report.py           ← Main analyzer script
├── SG_STOCK_ANALYZER_README.md         ← Full documentation
├── QUICK_START.md                      ← This file
└── sg_stock_data/
    └── sg_stock_report_20260602_144550.xlsx  ← Latest Excel report (15 stocks)
```

---

## 🎯 Start Here (5 Minutes)

### Step 1: View the Excel Report
```
Open: sg_stock_data/sg_stock_report_20260602_144550.xlsx
```

**Contents**:
- **Summary sheet**: 15 rows (one per stock) with CMP, P/E, Darvas signal, Piotroski score
- **Detail sheets**: One sheet per stock (D05, BN4, U11, C6L, etc.)

### Step 2: Run a Single Stock Report
```bash
python3 sg_stock_daily_report.py D05 --scans
```

**Output**: Terminal report with:
- Current price (CMP)
- 52-week range
- P/E, P/B, Dividend Yield
- **Darvas Box**: BREAKOUT_BUY / IN_BOX / BREAKDOWN_SELL
- **Piotroski Score**: 0–9 (financial strength)
- **PEGY Ratio + Breakout**: Value metric + trend signal

### Step 3: Generate Fresh Excel Report
```bash
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

**Time**: 3–5 minutes for 15 stocks  
**Output**: New Excel file in `sg_stock_data/`

---

## 📊 Understanding the Metrics

### CMP (Current Market Price)
- **What**: Live SGD price from Yahoo Finance
- **How to use**: Benchmark for entry/exit decisions

### P/E Ratio (Price-to-Earnings)
- **Formula**: Stock Price ÷ Earnings Per Share
- **Interpretation**:
  - **<15**: Undervalued (if fundamentals are strong)
  - **15–20**: Fair value
  - **>20**: Expensive (unless high growth)

### Darvas Box Signal
- **BREAKOUT_BUY**: Price broke above resistance → momentum entry
- **IN_BOX**: Price consolidating → wait for signal
- **BREAKDOWN_SELL**: Price broke below support → exit/short
- **NO_BOX**: Insufficient data

### Piotroski 0–9 Score
- **≥7**: Strong fundamentals (likely outperformer)
- **4–6**: Moderate (neutral stance)
- **≤3**: Weak (avoid or short)

**What it measures**: 9 accounting metrics
- ROA (profitability)
- Operating cash flow
- Debt levels (leverage)
- Current ratio (liquidity)
- Gross margin (efficiency)

### PEGY Ratio
- **Formula**: `PEG Ratio + (Dividend Yield × 100)`
- **What it means**: Higher = better value (especially for dividend stocks)
- **Example**: PEG=3.6 + DivYield=2.43% → PEGY≈519.6 (very high, not a value stock)

### Breakout Signal
- **Above 200-day MA**: In uptrend (momentum favors bulls)
- **Below 200-day MA**: In downtrend (momentum favors bears)
- **Near 200-day MA**: Consolidating (decide next direction soon)

---

## 🎓 Three Scanning Strategies

### Strategy 1: Value + Fundamentals (Long-term hold)
```bash
# Find strong fundamentals + reasonable valuation
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

**In Excel, filter for**:
- Piotroski Score ≥ 7
- PEGY Ratio ≤ 50 (value metric)
- Dividend Yield ≥ 2%

**Example**: DBS (D05) — Piotroski=4 (not strong), but large-cap, 2.43% yield

### Strategy 2: Momentum + Breakout (3–6 month trades)
```bash
# Find stocks breaking out above resistance
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

**In Excel, filter for**:
- Darvas Signal = "BREAKOUT_BUY"
- Distance from 200MA > 5% (above trend)
- Volume ≥ 1M (confirmation)

**Example**: D05 — Darvas breakout, 16% above 200MA → Momentum trade setup

### Strategy 3: Dividend Income (Passive income)
```bash
# Find consistent dividend payers
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

**In Excel, filter for**:
- Dividend Yield ≥ 3%
- Piotroski Score ≥ 4 (sustainable)
- Market Cap ≥ S$1B (stability)

**Example**: Look for REITs (C38U, ME8U, N2IU) with high yields + stable scores

---

## 🔧 Common Commands

### Single Stock (Text Output)
```bash
python3 sg_stock_daily_report.py D05
```

### Single Stock (with Scans)
```bash
python3 sg_stock_daily_report.py D05 --scans
```

### Top 15 Stocks (Excel Report with All Scans)
```bash
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

### Full STI-30 (All 30 Components)
```bash
python3 sg_stock_daily_report.py --sti30 --scans --output excel
```

### Custom Stock List (via Python)
```python
from sg_stock_daily_report import run_batch

my_stocks = ["D05.SI", "BN4.SI", "U11.SI"]
results = run_batch(symbols=my_stocks, run_scans=True, output_format="excel")
```

---

## 📈 Example: Interpreting DBS (D05)

**Raw Data from Report**:
```
CMP:                    S$64.53
P/E:                    16.85
Dividend Yield:         2.43%
Darvas Signal:          BREAKOUT_BUY
Piotroski Score:        4/9 (MODERATE)
PEGY Ratio:             519.6
Distance from 200MA:    +16.12%
52W High:               S$62.84
52W Low:                S$43.02
```

**Analysis**:

| Metric | Signal | Meaning |
|--------|--------|---------|
| **Darvas: BREAKOUT_BUY** | ✅ Bullish | Price broke above historical resistance |
| **16% above 200MA** | ✅ Bullish | Strong uptrend in place |
| **Piotroski 4/9** | ⚠️ Neutral | Fundamentals are okay, not exceptional |
| **P/E 16.85** | ⚠️ Fair | Not cheap, not expensive for banks |
| **PEGY 519.6** | ❌ High | Not a value stock (expensive for yield) |
| **Div Yield 2.43%** | ✅ Fair | Reasonable income for large-cap |

**Conclusion**:
- **Best for**: Momentum traders (technical breakout)
- **Less ideal for**: Value investors (PEGY too high)
- **Good for**: Income + growth hybrid (stable dividend + trend following)

---

## 📁 File Reference

| File | Purpose | Size |
|------|---------|------|
| `sg_stock_daily_report.py` | Main analyzer (executable) | ~35 KB |
| `SG_STOCK_ANALYZER_README.md` | Full docs + methodology | ~20 KB |
| `QUICK_START.md` | This guide | ~10 KB |
| `sg_stock_report_*.xlsx` | Generated Excel reports | 20–100 KB |

---

## ⚡ Performance Guide

| Action | Time | Notes |
|--------|------|-------|
| Single stock (no scans) | ~5 sec | Just price quote |
| Single stock (with scans) | ~15 sec | Includes 6-month history |
| Batch (15 stocks, scans) | 3–5 min | STI top 15 |
| Batch (30 stocks, scans) | 6–10 min | Full STI-30 |

**Tip**: Run during market close (6:30 PM SGT) or evening for fastest data refresh.

---

## 🚀 Next Steps

1. **Run the example**:
   ```bash
   python3 sg_stock_daily_report.py D05 --scans
   ```

2. **Open the Excel file**:
   ```
   sg_stock_data/sg_stock_report_20260602_144550.xlsx
   ```

3. **Create your own list**:
   ```bash
   python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
   ```

4. **Read the full docs**:
   ```
   SG_STOCK_ANALYZER_README.md
   ```

---

## 🎓 Learn More

- **Darvas Box**: Technical momentum system (3–6 month outlook)
- **Piotroski F-Score**: Fundamental quality score (1–2 year outlook)
- **PEGY Ratio**: Value metric with dividend adjustment
- **Breakout (200MA)**: Trend-following entry signal

See `SG_STOCK_ANALYZER_README.md` for detailed methodology.

---

**Ready to scan? Start with**:
```bash
python3 sg_stock_daily_report.py D05 --scans
```

📊 Happy investing!
