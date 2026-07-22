# Intraday Trading System - Quick Start Guide

## Complete Workflow

### **Step 1: Daily Screening (8:45 AM - Before Market Open)**

#### On Screener.in:
1. Go to **Screener.in** → **Stock Screener**
2. Click **Advanced Filter** → Apply these 10 filters:
   - Market Cap < 20000 Cr
   - ROE > 20%
   - ROE (5Y) > 15%
   - ROCE > 20%
   - ROCE (5Y) > 15%
   - Promoter Holding > 50%
   - FII Holding < 5%
   - DII Holding < 5%
   - D/E Ratio < 0.2
   - Profit Growth (5Y) > 10%

3. **Export Results** → Copy to `Screener Results` sheet in Excel template

#### In Excel Template:
1. Open `Intraday_Screener_Tracker.xlsx`
2. Go to **Screener Results** sheet
3. Paste stock names in Column A (rows 5-19)
4. Fill metrics in columns B-K from Screener.in

**Scoring (Column L):**
- Count how many filters each stock passes
- Example: If a stock passes 8/10 filters = Score 8
- Focus on stocks with Score ≥ 7

---

### **Step 2: Technical Confirmation (9:00 AM - 10:00 AM)**

#### Before Trading, Check Technical Setup for Top 5 Stocks:

**Use the "Technical Setup Guide" sheet to validate one of 4 patterns:**

| Pattern | Setup | Entry Signal | Stop Loss | Target |
|---------|-------|--------------|-----------|--------|
| **BREAKOUT** | Above resistance | Price > resistance + volume spike | Below resistance | 2-3% above entry |
| **SUPPORT BOUNCE** | At 20-day MA/support | Hammer/engulfing candle | Below support | Previous high |
| **GAP UP** | Opens higher on news | Consolidation breakout | Consolidation low | Gap top + 1% |
| **REVERSAL** | Doji at support | Above reversal candle | Below reversal low | Previous high |

**Validation Checklist (MUST ALL BE YES):**
- ✅ Volume > 200-day average volume
- ✅ Price at key technical level (support/resistance/MA)
- ✅ Confirmation candle visible (1-min or 5-min chart)
- ✅ No major news/earnings disruption
- ✅ Sector momentum confirmed (up/down trend)

---

### **Step 3: Position Sizing & Entry**

#### Risk Management Rules:
```
Risk per trade = 0.5% to 1% of portfolio
Stop Loss distance = 1-2% below entry (technical level)
Position Size = (Portfolio Value × Risk %) / Stop Loss Distance

Example:
- Portfolio = ₹1,00,000
- Risk = 1% = ₹1,000
- Stock Entry = ₹500, Stop Loss = ₹490 (₹10 loss = 2% risk)
- Shares to buy = ₹1,000 / ₹10 = 100 shares
```

#### Entry Rules:
- **Entry Time Window:** 9:15-10:30 AM OR 2:30-3:30 PM
- **Volume Check:** Current volume > 200-day average
- **Technical Confirmation:** Candle close above entry (5-min chart)
- **Trade Only If:** Score ≥ 7 AND technical setup confirmed

---

### **Step 4: Trade Execution & Logging**

#### In "Trade Journal" Sheet:

**Fill these columns at entry:**
- Date
- Stock Name
- Entry Price (exact)
- Entry Time (HH:MM format)
- Quantity (calculated from position sizing)
- Setup Type (Breakout/Support/Gap/Reversal)

**Fill these at exit:**
- Exit Price
- Exit Time
- Charges (brokerage + fees)

**Auto-Calculated Formulas:**
- Gross P&L = (Exit - Entry) × Qty
- Net P&L = Gross P&L - Charges
- Return % = Net P&L / (Entry × Qty)

---

### **Step 5: Exit Rules**

#### Exit Conditions (FIRST ONE TO TRIGGER):

**1. PROFIT TARGET (Best Outcome)**
- Close position at 2-3% profit
- Time: Typically 30 mins to 2 hours after entry
- Example: Bought at ₹500 → Exit at ₹510-515 (2-3% gain)

**2. STOP LOSS (Risk Control)**
- Exit if price hits stop loss
- Time: Can happen within 5 minutes
- Example: Bought at ₹500, SL at ₹490 → Exit at ₹489

**3. TIME-BASED EXIT (Decay)**
- Close position 15 minutes before close (3:45 PM)
- Reason: Liquidity dries up, gaps happen at close
- Profit/Loss: Whatever level at 3:45 PM

**4. NEWS/EVENT EXIT (Immediate)**
- Major news drops → Exit immediately
- Don't wait for loss confirmation
- Reason: Gap risk on next day open

---

### **Step 6: Performance Tracking**

#### In "Monthly Analytics" Sheet:

**Track These Metrics Monthly:**
- Total Trades
- Winning Trades (Net P&L > 0)
- Losing Trades (Net P&L < 0)
- Total P&L (sum of all profits/losses)
- Win Rate % (Winning Trades / Total Trades)

**Monthly Goals:**
- Target Win Rate: > 55% (minimum)
- Target Avg Return/Trade: > 1% (after charges)
- Target Monthly P&L: > 3% of portfolio

**Evaluation:**
- If Win Rate < 50% for 2 months → Review technical setup accuracy
- If Avg Return < 0.5% → Increase position size or focus on high-conviction setups
- If Monthly P&L negative → Reduce trade frequency, focus on quality over quantity

---

## Daily Checklist

**9:00 AM (Before Market Opens)**
- [ ] Run screener on Screener.in
- [ ] Copy results to Excel "Screener Results" sheet
- [ ] Identify top 5 stocks by Score (≥7)
- [ ] Check technical setup on chart

**9:15 AM (Market Open)**
- [ ] Monitor first 15 minutes volume
- [ ] Validate technical setup confirmation
- [ ] Enter trade with exact position size

**9:30 AM - 3:45 PM (During Market)**
- [ ] Monitor positions in real-time
- [ ] Log entry times in Trade Journal
- [ ] Execute stops if triggered
- [ ] Take profits at 2-3% target
- [ ] Exit all positions by 3:45 PM

**After Market Close (4:00 PM)**
- [ ] Log all exit prices and times
- [ ] Update Net P&L in Trade Journal
- [ ] Note technical setup accuracy (did pattern work?)
- [ ] Review any losing trades for lesson

---

## Red Flags - DON'T TRADE IF:

❌ Volume is below 200-day average  
❌ Stock is in consolidation (no clear pattern)  
❌ Major news/earnings scheduled same day  
❌ Market-wide selloff in progress  
❌ Technical setup shows rejection of resistance  
❌ Sector is weak while stock is strong (isolation risk)  
❌ You haven't slept well (emotional trading risk)  

---

## Sample Trade Example

### **Setup:**
```
Date: May 4, 2026
Stock: ABC Ltd (Score: 8/10)
Price: ₹450
20-Day MA: ₹442
Resistance: ₹460
Support: ₹440

Technical Setup: BREAKOUT
- Price consolidating between 448-452
- Volume spike above 200-day avg
- Confirmation candle closes above 452
```

### **Entry Decision:**
```
✅ Filter Score = 8/10
✅ Technical Setup Confirmed (Breakout above 452)
✅ Volume > 200-day avg
✅ Entry Time: 9:45 AM (within best window)

Position Size Calculation:
- Portfolio = ₹1,00,000
- Risk = 1% = ₹1,000
- Stop Loss = 440 (support)
- Entry = 453
- Risk per share = 453 - 440 = ₹13
- Shares = 1000 / 13 = 76 shares
- Capital needed = 453 × 76 = ₹34,428
```

### **Trade Execution:**
```
Entry: 9:45 AM at ₹453, 76 shares
Target: ₹453 × 1.03 = ₹466.59 (3% gain)
Stop: ₹440

Outcome at 11:15 AM:
- Price reaches ₹465 (within target range)
- Exit at ₹465
- Gross P&L = (465 - 453) × 76 = ₹912
- Charges = ₹150 (brokerage + fees)
- Net P&L = ₹912 - ₹150 = ₹762
- Return = 762 / (453 × 76) = 2.21%
```

---

## Success Metrics (First Month)

| Metric | Target | How to Achieve |
|--------|--------|----------------|
| Win Rate | > 50% | Focus on high-score stocks only (≥7) |
| Avg Win | > 1.5% | Take profits early (2-3% target) |
| Avg Loss | < 1% | Strict stop loss discipline |
| Trades/Day | 1-3 | Quality over quantity |
| Monthly Return | > 2% | Consistency matters more than size |

---

## Quick Formula Reference (Excel)

**Screener Results Sheet:**
- Score = Count of filters passed (0-10)

**Trade Journal Sheet:**
- Gross P&L = (Exit Price - Entry Price) × Quantity
- Net P&L = Gross P&L - Charges
- Return % = Net P&L / (Entry Price × Quantity)
- Total Trades = COUNTA(B4:B23)
- Total P&L = SUM(J4:J23)
- Win Rate = COUNTIF(J4:J23,">0") / COUNTA(J4:J23)

**Monthly Analytics:**
- Update monthly to track progress
- Identify patterns in your trading

---

## Common Mistakes to Avoid

1. **Over-Trading**: Trade only when all conditions align (don't force trades)
2. **Ignoring Stop Loss**: Honor stops religiously - it's your risk management
3. **Chasing Momentum**: Wait for consolidation + breakout, not already-moving stocks
4. **Trading After 3:45 PM**: Liquidity and gap risk too high
5. **Averaging Down**: Never add to losing position (increases risk)
6. **News Trades**: Skip trades on earnings/major announcements
7. **Leverage**: Trade with capital you can afford to lose
8. **Emotional Decisions**: Stick to plan; don't override stop/target

---

## Monthly Review Template

**At month-end, ask yourself:**
- How many trades hit profit target? (tracking setup accuracy)
- How many hit stop loss? (tracking risk management)
- Which setup type worked best? (Breakout/Support/Gap/Reversal)
- What sector had best performance? (concentration opportunity)
- Did I respect time-based exits? (discipline check)
- What was the biggest win? Biggest loss? (outlier analysis)

**Adjust for next month:**
- Focus on best-performing setup type
- Increase filters threshold if win rate < 50%
- Decrease position size if monthly loss > 5%
- Skip weak sectors identified in review
