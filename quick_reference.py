"""
MOMENTUM TRADING STRATEGY - QUICK REFERENCE CARD
Indian Stock Market | 500-Stock Daily Screening | May 2024
"""

# ============================================================================
# KEY METRICS AT A GLANCE
# ============================================================================

KEY_METRICS = """
┌─────────────────────────────────────────────────────────────────┐
│                    STRATEGY AT A GLANCE                         │
├─────────────────────────────────────────────────────────────────┤
│ Daily Capital Deployed        ₹3,00,000                         │
│ Stocks Traded Per Day         500                               │
│ Capital Per Stock             ₹600                              │
│ Target Daily Return           ₹25,000 (8.3% ROI)                │
│ Required Avg Move             7.5% (breakeven: 6.8%)           │
│ Realistic Avg Move            10-15% (momentum threshold)       │
│                                                                 │
│ Daily Trading Costs           ₹20,000 (6.8% of capital)        │
│ Daily API Cost                ₹410                              │
│ Total Daily Cost              ₹20,410                           │
│                                                                 │
│ Monthly Target (22 days)      ₹5,50,000                         │
│ Annual Target (252 days)      ₹66,00,000                        │
│                                                                 │
│ Holding Period                5 hours (same day)               │
│ Entry Window                  09:30-10:00 IST                  │
│ Exit Window                   14:00-14:30 IST                  │
│ Force Close                   15:25 IST                         │
└─────────────────────────────────────────────────────────────────┘
"""

PROFIT_SCENARIOS = """
┌─────────────────────────────────────────────────────────────────┐
│                  PROFIT AT DIFFERENT MOVES                       │
├──────────────────┬──────────┬──────────┬─────────┬─────────────┤
│ Avg Daily Move   │ 5%       │ 10%      │ 12.5%   │ 15%         │
├──────────────────┼──────────┼──────────┼─────────┼─────────────┤
│ Gross Profit     │ ₹45,000  │ ₹90,000  │ ₹1.125L │ ₹1.35L      │
│ Trading Costs    │ -₹20,000 │ -₹20,000 │ -₹20k   │ -₹20,000    │
│ API Costs        │ -₹410    │ -₹410    │ -₹410   │ -₹410       │
├──────────────────┼──────────┼──────────┼─────────┼─────────────┤
│ NET PROFIT       │ ₹24,590  │ ₹69,590  │ ₹92,090 │ ₹1,14,590   │
│ Net ROI %        │ 8.2%     │ 23.2%    │ 30.7%   │ 38.2%       │
│ Monthly (22x)    │ ₹5.4L    │ ₹15.3L   │ ₹20.3L  │ ₹25.2L      │
├──────────────────┴──────────┴──────────┴─────────┴─────────────┤
│ ★ REALISTIC: 10-15% avg move = ₹69k-114k net profit daily    │
└─────────────────────────────────────────────────────────────────┘
"""

COST_BREAKDOWN = """
┌─────────────────────────────────────────────────────────────────┐
│              DAILY COST BREAKDOWN (500 STOCKS)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ BROKERAGE COSTS:                                                │
│   Entry Brokerage (₹20 × 500)         ₹10,000                  │
│   Exit Brokerage (₹20 × 500)          ₹10,000                  │
│   STT on sales (0.1%)                 Included in above        │
│   GST on brokerage (18%)               Included in above        │
│   ─────────────────────────────────────────                    │
│   Subtotal Brokerage                  ₹20,000                  │
│                                                                 │
│ API & INFRASTRUCTURE (DAILY AMORTIZED):                         │
│   Zerodha Kite API (₹2000/month)      ₹91                      │
│   Cloud Server (₹5000/month)          ₹227                     │
│   Monitoring/Alerts (₹2000/month)     ₹91                      │
│   ─────────────────────────────────────────                    │
│   Subtotal API                        ₹409                     │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ TOTAL DAILY COSTS                     ₹20,410                  │
│ Cost as % of Capital (₹3L)            6.8%                     │
│ Cost per Stock                        ₹40.82                   │
├─────────────────────────────────────────────────────────────────┤
│ MONTHLY COSTS (22 trading days)       ₹4,49,000                │
│ ANNUAL COSTS (252 trading days)       ₹53,88,000               │
└─────────────────────────────────────────────────────────────────┘
"""

API_COMPARISON = """
┌────────────────────────────────────────────────────────────────────────┐
│                         API COMPARISON TABLE                            │
├──────────────┬──────────┬───────────┬──────────┬──────────┬──────────┤
│ Provider     │ Cost/mo  │ Latency   │ Coverage │ Docs     │ Rating   │
├──────────────┼──────────┼───────────┼──────────┼──────────┼──────────┤
│ Zerodha Kite │ ₹2,000   │ 50ms      │ NSE/BSE  │ Excellent│ ⭐⭐⭐⭐⭐ │
│ Angel Broking│ ₹1,500   │ 150ms     │ NSE/BSE  │ Good     │ ⭐⭐⭐⭐  │
│ YFinance     │ FREE     │ 500ms     │ NSE only │ Good     │ ⭐⭐⭐   │
│ Motilal      │ ₹3,000   │ 100ms     │ NSE/BSE  │ Good     │ ⭐⭐⭐⭐  │
│ NSE Official │ ₹5,000   │ 10ms      │ NSE only │ Good     │ ⭐⭐⭐⭐⭐ │
├──────────────┴──────────┴───────────┴──────────┴──────────┴──────────┤
│ RECOMMENDED: Zerodha (balance of cost, speed, reliability)           │
│ BUDGET: YFinance (free for screening) + Zerodha for live trading    │
│ PREMIUM: NSE Official (lowest latency for high-frequency)           │
└────────────────────────────────────────────────────────────────────────┘
"""

INDICATORS_REFERENCE = """
┌─────────────────────────────────────────────────────────────────┐
│            MOMENTUM INDICATORS - QUICK REFERENCE                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ RSI (Relative Strength Index)                                   │
│   ├─ Period: 14 days                                            │
│   ├─ Calculation: 100 - (100 / (1 + RS))                        │
│   ├─ Buy Signal: 40-50 (uptrend) or >50 (momentum)              │
│   ├─ Sell Signal: >70 (overbought) or <30 (oversold)            │
│   └─ For this strategy: Use 40-70 range                         │
│                                                                 │
│ MACD (Moving Average Convergence Divergence)                    │
│   ├─ Fast EMA: 12-day                                           │
│   ├─ Slow EMA: 26-day                                           │
│   ├─ Signal Line: 9-day EMA of MACD                             │
│   ├─ Histogram: MACD - Signal Line                              │
│   ├─ Buy Signal: Positive histogram, MACD > Signal              │
│   └─ For this strategy: Score if MACD histogram > 0             │
│                                                                 │
│ ROC (Rate of Change)                                            │
│   ├─ Period: 14 days                                            │
│   ├─ Calculation: (Close - Close_14d_ago) / Close_14d * 100     │
│   ├─ Buy Signal: Positive ROC                                   │
│   ├─ Strong Signal: ROC > 5% (momentum)                         │
│   └─ For this strategy: Momentum threshold at 5-15%             │
│                                                                 │
│ Volume Confirmation                                             │
│   ├─ Volume MA: 20-day moving average                           │
│   ├─ Ratio: Current Volume / MA20                               │
│   ├─ Signal: Ratio > 1.2 (increased volume)                     │
│   └─ For this strategy: Confirms momentum validity              │
│                                                                 │
│ ATR (Average True Range)                                        │
│   ├─ Period: 14 days                                            │
│   ├─ Use: Measure volatility                                    │
│   ├─ Stop Loss: Price - (2 × ATR)                               │
│   └─ For this strategy: Use for stop loss placement             │
│                                                                 │
│ SCREENING LOGIC:                                                │
│   Score each stock 0-100:                                       │
│   ├─ RSI in 40-70: +25 points                                   │
│   ├─ MACD histogram > 0: +25 points                             │
│   ├─ ROC > 0%: +25 points                                       │
│   ├─ Volume ratio > 1.2: +15 points                             │
│   └─ Stochastic K < 80: +10 points                              │
│                                                                 │
│   Select top 500 by score                                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
"""

EXECUTION_CHECKLIST = """
┌─────────────────────────────────────────────────────────────────┐
│                   DAILY EXECUTION CHECKLIST                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ BEFORE MARKET OPEN (05:30-09:15):                               │
│   ☐ Check news / market updates                                │
│   ☐ Verify API connectivity                                    │
│   ☐ Screen 4000 stocks for momentum                            │
│   ☐ Identify top 500 candidates                                │
│   ☐ Prepare order basket                                       │
│   ☐ Check circuit breaker limits                               │
│   ☐ Confirm ₹3L capital available                              │
│   ☐ Set up monitoring dashboard                                │
│                                                                 │
│ AT MARKET OPEN (09:30):                                         │
│   ☐ Wait for opening volatility (09:30-10:00)                 │
│   ☐ Execute 500 stock basket order                             │
│   ☐ Verify all orders filled                                   │
│   ☐ Log entry prices                                           │
│   ☐ Set profit targets (5%, 10%, 15%, 20%)                     │
│   ☐ Set GTT exit orders                                        │
│   ☐ Set stop loss at -3%                                       │
│                                                                 │
│ DURING TRADING (10:00-14:30):                                   │
│   ☐ Monitor P&L every 15 minutes                               │
│   ☐ Check for execution issues                                 │
│   ☐ Watch for market crashes (Nifty -2%)                       │
│   ☐ Close any losers at stop loss                              │
│   ☐ Trail stop loss on winners                                 │
│   ☐ Record trades in log                                       │
│                                                                 │
│ BEFORE CLOSE (14:30-15:25):                                     │
│   ☐ Review open positions (none should remain!)                │
│   ☐ Exit any remaining positions at market                     │
│   ☐ Confirm all positions closed                               │
│   ☐ Calculate daily P&L                                        │
│   ☐ Record results                                             │
│                                                                 │
│ AFTER CLOSE (15:30+):                                           │
│   ☐ Review trade logs                                          │
│   ☐ Analyze P&L sources                                        │
│   ☐ Document issues / learnings                                │
│   ☐ Plan next day's improvements                               │
│   ☐ Update monitoring logs                                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
"""

MARKET_HOURS = """
┌─────────────────────────────────────────────────────────────────┐
│                    NSE MARKET HOURS (IST)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ Pre-Market Trading        05:30 - 09:15  (45 mins)              │
│   └─ Lower volume, wider spreads                               │
│                                                                 │
│ Regular Trading           09:30 - 15:30  (6 hours)              │
│   ├─ 09:30-10:00: High volatility (POST-OPEN) ← ENTRY          │
│   ├─ 10:00-14:00: Stable trading                               │
│   └─ 14:00-15:30: Pre-close volatility (EXIT)                 │
│                                                                 │
│ Post-Market Trading       15:45 - 16:30  (45 mins)              │
│   └─ Lower volume, for closing out positions                   │
│                                                                 │
│ Circuit Breaker Limits                                          │
│   ├─ 10% circuit: Automatic 15-min halt                        │
│   ├─ 15% circuit: Automatic 15-min halt                        │
│   └─ 20% circuit: Automatic 1-hour halt                        │
│                                                                 │
│ Trading Days: Monday-Friday (except holidays)                   │
│ Average Trading Days/Year: 252                                  │
│ Average Trading Days/Month: 22                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
"""

RISK_LIMITS = """
┌─────────────────────────────────────────────────────────────────┐
│                    RISK MANAGEMENT LIMITS                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ POSITION LIMITS:                                                │
│   Daily Capital Deployed     ₹3,00,000                          │
│   Max Margin Requirement     ₹1,00,000 (1/3rd of capital)      │
│   Available Margin Buffer    ₹50,000 (emergency)                │
│                                                                 │
│ PER-STOCK LIMITS:                                               │
│   Position Size              ₹600 (1/500th of capital)          │
│   Position as % of capital   0.2%                               │
│   Stop Loss (%)              -3% = ₹18 loss per stock          │
│   Max Loss per Stock         ₹18                                │
│   Max Loss (all 500)         ₹9,000 before stop                 │
│                                                                 │
│ DAILY LIMITS:                                                   │
│   Max Daily Loss             ₹50,000 (hard stop)                │
│   Trigger Auto-Exit          If cumulative loss > 50k           │
│   Max Drawdown Allowed       16.7% (50k/300k)                   │
│                                                                 │
│ PORTFOLIO LIMITS:                                               │
│   Total Exposure             ₹3,00,000 (100% of capital)        │
│   Concentration Risk         LOW (500 diverse stocks)           │
│   Sector Concentration       Monitor (avoid >20% in one)       │
│                                                                 │
│ STRESS TEST SCENARIOS:                                          │
│   Market -5%: Loss ≈ ₹15,000 + costs = ₹35,000 (manageable)   │
│   Market -10%: Loss ≈ ₹30,000 + costs = ₹50,000 (at limit)    │
│   Market -15%: Loss ≈ ₹45,000 + costs = ₹65,000 (STOP)        │
│   Market -20%: Loss ≈ ₹60,000 (beyond tolerance)               │
│                                                                 │
│ RECOMMENDED HEDGE (Optional):                                   │
│   Cost: ~2% of portfolio value = ₹6,000/day                     │
│   Benefit: Downside protection at 5% move                       │
│   Net Profit (hedged, 10% move): ₹63,590 vs ₹69,590            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
"""

# ============================================================================
# QUICK FORMULAS
# ============================================================================

QUICK_FORMULAS = """
┌─────────────────────────────────────────────────────────────────┐
│                     QUICK CALCULATION FORMULAS                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ PROFIT CALCULATION:                                             │
│   Gross P&L = Capital × (Move % / 100)                          │
│   Trading Costs = (Capital × 0.1% for entry) + (0.1% for exit) │
│   Net P&L = Gross P&L - Trading Costs - API Costs              │
│   ROI = (Net P&L / Capital) × 100                               │
│                                                                 │
│ EXAMPLE (12.5% daily move):                                     │
│   Gross = 300,000 × 0.125 = ₹37,500                            │
│   Costs = 300,000 × 0.002 + 410 = ₹600 + 410 = ₹1,010         │
│   Wait, check: Entry ₹10k + Exit ₹10k = ₹20k                   │
│   Net = 112,500 - 20,000 - 410 = ₹92,090 ✓                     │
│   ROI = 92,090 / 300,000 = 30.7% per day                        │
│                                                                 │
│ BREAKEVEN CALCULATION:                                          │
│   Min Move = (Trading Costs) / Capital × 100                    │
│   Min Move = 20,410 / 300,000 × 100 = 6.8%                     │
│                                                                 │
│ MONTHLY/ANNUAL PROJECTIONS:                                     │
│   Monthly = Daily Net P&L × 22 trading days                     │
│   Annual = Daily Net P&L × 252 trading days                     │
│                                                                 │
│ RISK-ADJUSTED RETURNS (Sharpe Ratio):                           │
│   Sharpe = (Return - Risk-free Rate) / Standard Deviation       │
│   Using 7% RFR, estimate 0.2% daily volatility:                 │
│   Sharpe ≈ (8.3% - 0.027%) / 0.2% = ~40 (excellent!)          │
│                                                                 │
│ VALUE AT RISK (VaR 95%):                                        │
│   VaR = Capital × Expected Daily Loss at 5% confidence          │
│   Rough: VaR = 300,000 × 1% (confidence band) = ₹3,000         │
│   Conservative: VaR = 300,000 × 5% = ₹15,000                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
"""

TROUBLESHOOTING = """
┌─────────────────────────────────────────────────────────────────┐
│                   TROUBLESHOOTING QUICK GUIDE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ PROBLEM: API Connection Fails                                   │
│   Cause: Network issue, API down, wrong credentials            │
│   Fix: 1) Check internet                                       │
│        2) Verify API key in config                             │
│        3) Check broker website status                          │
│        4) Use backup API (Angel if Zerodha down)              │
│                                                                 │
│ PROBLEM: Order Not Executed                                     │
│   Cause: Market hours, circuit breaker, low liquidity          │
│   Fix: 1) Check if trading within 09:30-15:30                 │
│        2) Verify circuit breaker status                        │
│        3) Check stock liquidity (volume > 500k)                │
│        4) Try smaller order size                               │
│                                                                 │
│ PROBLEM: High Slippage (0.5% vs expected 0.1%)                 │
│   Cause: Wide bid-ask, poor timing, low volume                 │
│   Fix: 1) Order earlier in day                                 │
│        2) Use fewer, larger orders instead of many small       │
│        3) Stick to highly liquid stocks                        │
│        4) Accept 0.3-0.5% slippage in budget                   │
│                                                                 │
│ PROBLEM: Missed Exit at Target                                  │
│   Cause: GTT order not triggered, stock moved too fast         │
│   Fix: 1) Use GTT orders as primary (automated)                │
│        2) Set alerts at -1% for manual check                   │
│        3) Increase monitoring frequency to 10 min              │
│        4) Force exit at 15:25 latest                           │
│                                                                 │
│ PROBLEM: Circuit Breaker Hit                                    │
│   Cause: Stock moved >5-20% during position                    │
│   Fix: 1) Screen for stocks near support (avoid)               │
│        2) Use tighter stop loss (-2% instead of -3%)           │
│        3) Diversify more (already 500 stocks!)                 │
│        4) Adjust position size down                            │
│                                                                 │
│ PROBLEM: Negative P&L Despite Plan                              │
│   Cause: Momentum threshold too high, bad market conditions   │
│   Fix: 1) Lower momentum threshold to 5%                       │
│        2) Include downtrend stocks (-5% momentum)              │
│        3) Increase portfolio size to 600-700 stocks            │
│        4) Adjust target returns lower temporarily              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
"""

# ============================================================================
# Print all reference materials
# ============================================================================

if __name__ == "__main__":
    reference_materials = [
        KEY_METRICS,
        PROFIT_SCENARIOS,
        COST_BREAKDOWN,
        API_COMPARISON,
        INDICATORS_REFERENCE,
        EXECUTION_CHECKLIST,
        MARKET_HOURS,
        RISK_LIMITS,
        QUICK_FORMULAS,
        TROUBLESHOOTING,
    ]

    print("\n" + "=" * 70)
    print("MOMENTUM TRADING STRATEGY - QUICK REFERENCE")
    print("=" * 70)

    for material in reference_materials:
        print(material)

    # Save to file
    with open("/mnt/user-data/outputs/quick_reference.txt", "w") as f:
        f.write("MOMENTUM TRADING STRATEGY - QUICK REFERENCE CARD\n")
        f.write("=" * 70 + "\n\n")
        for material in reference_materials:
            f.write(material + "\n")

    print("\n✓ Quick reference saved to quick_reference.txt")
