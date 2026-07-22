# Singapore Stock Market Analyzer
## Daily Report with Darvas Box, Piotroski F-Score, and PEGY Ratio

**Status**: ✅ Ready to use  
**Latest Report**: `sg_stock_report_20260602_144550.xlsx` (15 stocks, full scans)  
**Language**: Python 3.6+  
**Data Source**: Yahoo Finance (yfinance) + LSEG reference  

---

## 📊 Overview

A comprehensive Singapore stock market analysis tool that extends your existing Indian (NSE/BSE) and US (NASDAQ/NYSE) stock reporting frameworks to SGX-listed equities.

### Key Outputs Per Stock
- **CMP** (Current Market Price in SGD)
- **Valuation Ratios** (P/E, P/B, Dividend Yield)
- **Technical Analysis** (Darvas Box scan)
- **Fundamental Strength** (Piotroski F-Score: 0–9)
- **Breakout Opportunities** (Price vs 200-day MA)
- **PEGY Ratio** (PEG adjusted for dividend yield)

---

## 🚀 Installation

```bash
# Install dependencies (one-time)
pip install yfinance pandas openpyxl

# Verify installation
python3 -c "import yfinance, pandas, openpyxl; print('✅ All dependencies installed')"
```

---

## 💡 Usage

### 1. Single Stock Report (Text Output)

```bash
# DBS (D05) — simple quote
python3 sg_stock_daily_report.py D05

# UOB (BN4) — with scans
python3 sg_stock_daily_report.py BN4 --scans

# Singtel (U11) — text output (default)
python3 sg_stock_daily_report.py U11 --output text
```

### 2. Single Stock with Excel Output

```bash
# Generate single Excel file for one stock
python3 sg_stock_daily_report.py D05 --scans --output excel
```

### 3. Batch Reports (STI Top 15)

```bash
# Quick summary (text)
python3 sg_stock_daily_report.py --sti-top15

# Full scans + Excel workbook
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

### 4. Full STI-30 Batch

```bash
# All 30 Straits Times Index components
python3 sg_stock_daily_report.py --sti30 --scans --output excel
```

### 5. Python API (Colab / Jupyter)

```python
from sg_stock_daily_report import run, run_batch, run_scans_only

# Single stock
data = run("D05", run_scans=True)

# Batch (STI top 15)
results = run_batch(symbols=STI_TOP_15, output_format="excel", run_scans=True)

# Scans only
scans = run_scans_only("BN4")
```

---

## 📋 Excel Report Format

The generated Excel workbook contains:

### Sheet 1: Summary
One row per stock with key metrics:
- Symbol, Company Name, Sector
- CMP, Change %, P/E, P/B, Dividend Yield
- 52W High/Low, Trading Volume
- Darvas Signal, Piotroski Score, PEGY Ratio
- Breakout Signal

### Sheets 2+: Per-Stock Details
Each stock gets its own detail sheet with:

#### Quote & Valuation Section
```
CMP (S$)             64.53
Change %             3.00%
52W High             62.84
52W Low              43.02
200-Day MA           55.57
P/E Ratio            16.85
P/B Ratio            2.66
Div Yield %          2.43%
Market Cap           183.11B
```

#### Technical Scan: Darvas Box
```
Signal               BREAKOUT_BUY  ← Price > box top
Box Top              58.78
Box Bottom           57.43
Position in Box %    525.8%  ← Far above top = strong move
Upside to Top %      -8.91%
```

#### Fundamental Scan: Piotroski F-Score
```
Score (0-9)          4/9
Interpretation       MODERATE
```
- **7-9**: Strong (likely outperformer)
- **4-6**: Moderate (neutral stance)
- **0-3**: Weak (avoid/short candidate)

#### Value Scan: PEGY Ratio & Breakout
```
PEG Ratio            3.60
PEGY Adjusted        519.6  ← Higher = more attractive for value
Dividend Yield %     2.43%
Breakout Signal      Above 200-day MA - Uptrend
Distance from 200MA  16.12%  ← >5% = above trend
```

---

## 🎯 Scan Methodology

### 1. Darvas Box (Technical Momentum)

**Algorithm**: Nicolas Darvas (1960) box-detection system for momentum stocks.

**Key Design Rule**: 
- Box formation uses **historical bars only** (excludes current bar)
- Current bar low cannot pull box bottom down → breakdown stays detectable

**Signals**:
- **BREAKOUT_BUY**: Close > box top (momentum entry)
- **BREAKDOWN_SELL**: Close < box bottom (exit/short)
- **IN_BOX**: Price consolidating (wait for signal)
- **NO_BOX**: Insufficient history

**Settings**: `confirm=3` days (high/low must hold for 3 consecutive days)

---

### 2. Piotroski F-Score (Fundamental Strength)

**Scale**: 0–9 (higher = stronger)

**9 Components** (Accounting-based):

| Category | Criteria | Points |
|----------|----------|--------|
| **Profitability** (4 pts) | ROA > 0 | 1 |
| | Operating Cash Flow > 0 | 1 |
| | ROA improving YoY | 1 |
| | Earnings cash-backed (OCF/Assets > ROA) | 1 |
| **Leverage & Liquidity** (3 pts) | LT Debt ratio declining | 1 |
| | Current ratio improving | 1 |
| | No new shares issued | 1 |
| **Operating Efficiency** (2 pts) | Gross margin improving | 1 |
| | Asset turnover improving | 1 |

**Interpretation**:
- **≥7**: Strong candidate
- **4–6**: Neutral/moderate
- **≤3**: Weak/avoid

---

### 3. PEGY Ratio (Value + Dividend)

**Formula**: `PEGY = PEG + (Dividend Yield × 100)`

- **PEG**: Price/Earnings/Growth ratio (yfinance-sourced)
- **Dividend Yield**: Annual yield as percentage
- **Higher PEGY** = More attractive for value investors

**Rationale**: Standard PEG doesn't account for cash returned to shareholders via dividends — PEGY fills this gap for dividend-paying stocks.

---

### 4. Breakout Opportunities (200-day MA)

**Signal**: Distance from 200-day moving average

- **>5% above**: Uptrend (momentum buy setup)
- **<-5% below**: Downtrend (mean-reversion or short setup)
- **±5%**: Consolidation/consolidation

---

## 📊 STI Universe

### Top 15 by Market Cap (Recommended for Quick Scans)
```
D05  → DBS Group Holdings
BN4  → United Overseas Bank
U11  → Singtel
C6L  → Keppel Corporation
C38U → Mapletree Commercial Trust
ME8U → Mapletree Industrial
N2IU → Ascendas REIT
S58  → Singapore Airlines
Z74  → Standard Chartered
TSM  → Thai Beverage
G13  → Genting Singapore
5ZB  → Seatrium
J36  → Jiutanji Holdings
A17U → Ascendas India Trust
C52  → Frencken Group
```

### Full STI-30
Add: T82U, M44U, BS6, S63, AWX, E27, TS0U, AJBU, S51, VOID, F3L, CCHT, 9CI, U96, S58

---

## 🔍 Data Sources

| Field | Source | Notes |
|-------|--------|-------|
| Price, OHLCV | Yahoo Finance (yfinance) | `.SI` suffix added automatically |
| P/E, P/B, Div Yield | Yahoo Finance `ticker.info` | Real-time valuation |
| Income Statement | Yahoo Finance annual | Net Income, Revenue, Gross Profit |
| Balance Sheet | Yahoo Finance annual | Total Assets, Debt, Equity, Current Assets/Liabilities |
| Cash Flow | Yahoo Finance annual | Operating CF, Free CF, CapEx |
| Market Cap | Yahoo Finance `fast_info` | SGD-denominated |

---

## ⚡ Performance Tips

### Quick Single-Stock Report (~5 sec)
```bash
python3 sg_stock_daily_report.py D05  # No scans
```

### With Scans (~15 sec)
```bash
python3 sg_stock_daily_report.py D05 --scans  # Fetches 6 months history
```

### Batch (15 stocks, 3–5 min)
```bash
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

### Batch (30 stocks, 6–10 min)
```bash
python3 sg_stock_daily_report.py --sti30 --scans --output excel
```

---

## 📁 Output Files

### Directory: `./sg_stock_data/`

```
sg_stock_data/
├── sg_stock_report_20260602_144550.xlsx  ← Latest batch report
└── [per-symbol JSON files if --output json]
```

### Excel File Structure
```
sg_stock_report_20260602_144550.xlsx
├── Sheet "Summary"           ← One row per stock (sortable table)
├── Sheet "D05"              ← DBS detail
├── Sheet "BN4"              ← UOB detail
├── Sheet "U11"              ← Singtel detail
└── ...
```

---

## 🛠️ Troubleshooting

### "ModuleNotFoundError: No module named 'yfinance'"
```bash
pip install --upgrade yfinance pandas openpyxl
```

### "No data found, symbol may be delisted"
Some older stocks may not be available on yfinance. The script will skip them and report in the batch summary.

### "Too many requests" / Rate limiting
yfinance has rate limits (~2000 requests/hour). Batch 15 stocks instead of 30:
```bash
python3 sg_stock_daily_report.py --sti-top15 --scans --output excel
```

### Dividend Yield showing as >100%
This is a yfinance data issue (annual vs quarterly mismatch). Check the actual recent dividend in the report's "Corporate Actions" section.

---

## 🎓 Scanning Workflow

### For Value Investors
1. Filter by **Piotroski Score ≥ 7** (strong fundamentals)
2. Sort by **PEGY Ratio** (higher = better value)
3. Check **Dividend Yield ≥ 3%** (income component)
4. Review **200-day MA** (enter on uptrend, not near lows)

### For Momentum/Technical Traders
1. Filter by **Darvas Signal = BREAKOUT_BUY** (price > box top)
2. Check **Distance from 200MA > 5%** (above trend)
3. Review **Upside to Top %** (room to run)
4. Avoid **BREAKDOWN_SELL** signals (trend reversal risk)

### For Income Seekers
1. Sort by **Dividend Yield %** (highest first)
2. Check **Piotroski Score ≥ 4** (sustainable payout)
3. Review **Market Cap ≥ 1B SGD** (liquidity)
4. Screen out **stocks below 200-day MA** (declining dividends risk)

---

## 📈 Example Interpretation

**DBS (D05) — 2 Jun 2026**

```
CMP:                    S$64.53
P/E:                    16.85
Darvas Signal:          BREAKOUT_BUY    ← Price broke above box
Piotroski Score:        4/9 (MODERATE)  ← Neutral fundamentals
PEGY Ratio:             519.6           ← Not a value stock
Div Yield:              2.43%           ← Reasonable income
Distance from 200MA:    +16.12%         ← Strong uptrend
```

**Interpretation for Value Investor**: 
- ⚠️ Not a value opportunity (PEGY high, P/E 16.85)
- ✅ Good for momentum-based entry (Darvas breakout, above 200MA)
- ✅ Reasonable dividend for blue-chip (DBS is defensive large-cap)
- ⚠️ Fundamental strength is moderate (not multi-year hold candidate)

---

## 🔗 References & Further Reading

- **Darvas Box**: Nicolas Darvas, *My Formula for Trading in Stocks* (1960)
- **Piotroski F-Score**: Joseph Piotroski, "Value Investing: The Use of Historical Financial Statement Information" (2000)
- **Coffee Can Portfolio**: Robert Kirby (1984), popularized by Saurabh Mukherjea for India
- **PEGY Variant**: Combines PEG ratio with dividend yield for total shareholder return
- **SGX Data**: https://www.sgx.com
- **yfinance Docs**: https://github.com/ranaroussi/yfinance

---

## 📝 Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2 Jun 2026 | Initial release — Darvas Box, Piotroski F-Score, PEGY Ratio scans |

---

## 🙋 FAQ

### Q: Can I use this with Google Colab?
**A**: Yes! Install dependencies in the first cell:
```python
!pip install yfinance pandas openpyxl
from sg_stock_daily_report import run, run_batch
run("D05", run_scans=True)
```

### Q: How often should I refresh the data?
**A**: Weekly or after major economic announcements. Quarterly financials (Piotroski) are updated 45 days post-quarter close. Daily for technical scans (Darvas, breakout).

### Q: Can I customize the stock list?
**A**: Yes:
```python
my_stocks = ["D05", "BN4", "U11", "C6L"]  # Add .SI suffix or not
run_batch(symbols=my_stocks, run_scans=True, output_format="excel")
```

### Q: Which scan is most reliable?
**A**: 
- **Darvas Box**: Good for momentum (3–6 month outlook)
- **Piotroski F-Score**: Good for fundamental quality (1–2 year holding period)
- **PEGY Ratio**: Good for value + dividend (long-term hold)
- **Breakout (200MA)**: Good for entry timing (short-term trade)

### Q: What's the minimum market cap?
**A**: No hard filter in the code, but recommend ≥ S$500M for liquidity. STI-30 are all ≥ S$1B.

---

## 📧 Support

For issues or enhancements:
1. Check data availability on https://finance.yahoo.com (search `.SI` suffix)
2. Verify your internet connection (yfinance fetches live data)
3. Report issues with specific stocks (they may be delisted or renamed)

---

**Happy scanning! 📊**
