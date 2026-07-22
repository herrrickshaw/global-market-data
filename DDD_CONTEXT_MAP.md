# Domain-Driven Architecture: Context Map & Integration Guide

**Visual representation of bounded contexts and their interactions**

---

## Complete Context Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                     GLOBAL STOCK SCREENING SYSTEM                   │
│                      (6 Bounded Contexts)                           │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 1: DATA FOUNDATION                                             │
│                                                                       │
│  ┌─────────────────────────────┐                                    │
│  │  Market Data Provider        │  (Supporting Subdomain)           │
│  │  ─────────────────────────   │                                   │
│  │  • Fetch OHLC from NSE/BSE   │  Publishes:                       │
│  │  • Validate data quality     │  → MarketDataFetched             │
│  │  • Cache market calendar     │  → DataValidationFailed          │
│  │                              │                                   │
│  │  External APIs:              │  Inputs:                         │
│  │  ├─ NSE Bhavcopy            │  ← Market data requests           │
│  │  ├─ yfinance                │                                   │
│  │  └─ EODHD                   │                                   │
│  └─────────────────────────────┘                                    │
│           ▲                                                          │
│           │ Publishes MarketDataFetched                             │
│           ▼                                                          │
└──────────────────────────────────────────────────────────────────────┘

        ACL: MarketDataACL
        Translates: NSE format → StockMetrics

        ┌────────────────────────────────┐
        │ Anti-Corruption Layer          │
        ├────────────────────────────────┤
        │ External: {'pe': 15.5,        │
        │            'd2e': 0.5}        │
        │           ▼                    │
        │ Internal: StockMetrics(        │
        │            pe=Decimal(15.5),  │
        │            debtToEquity=...)  │
        └────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 2: CORE DOMAIN LOGIC                                           │
│                                                                       │
│  ┌────────────────────────────────┐    ┌────────────────────────────┐
│  │  Stock Analysis Engine         │    │  Portfolio Strategy        │
│  │  (Core Subdomain)              │    │  (Core Subdomain)          │
│  │  ─────────────────────────────  │    │  ─────────────────────────  │
│  │  • Run screening criteria      │    │  • Manage positions        │
│  │  • Score stocks               │    │  • Allocate weights        │
│  │  • Rank by criteria           │    │  • Rebalance portfolio     │
│  │                               │    │  • Maintain invariants     │
│  │  Aggregates:                  │    │                            │
│  │  ├─ Stock (AR)                │    │  Aggregates:               │
│  │  └─ Screen (VO)               │    │  ├─ Portfolio (AR)         │
│  │                               │    │  └─ Position (VO)          │
│  │  Publishes:                   │    │                            │
│  │  → ScreeningCompleted         │    │  Publishes:                │
│  │  → StockScored                │    │  → PortfolioRebalanced     │
│  └────────────────────────────────┘    │  → AllocationChanged       │
│             ▲                          └────────────────────────────┘
│             │                                    ▲
│      Subscribes to:                            │
│      ← MarketDataFetched                       │
│                                   Subscribes to:
│                                   ← ScreeningCompleted
│                                   ← MarketDataFetched
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 3: SUPPORT & EVALUATION                                        │
│                                                                       │
│  ┌────────────────────────────────┐    ┌────────────────────────────┐
│  │  Backtesting & Simulation      │    │  Risk & Signals            │
│  │  (Supporting Subdomain)        │    │  (Supporting Subdomain)    │
│  │  ─────────────────────────────  │    │  ─────────────────────────  │
│  │  • Evaluate strategy performance   │  • Generate trading signals│
│  │  • Walk-forward optimization   │    │  • Monitor risk metrics    │
│  │  • Calculate returns/drawdown  │    │  • Alert on thresholds     │
│  │                               │    │                            │
│  │  Subscribes to:               │    │  Subscribes to:            │
│  │  ← PortfolioRebalanced        │    │  ← PortfolioRebalanced     │
│  │  ← ScreeningCompleted         │    │  ← ScreeningCompleted      │
│  │                               │    │  ← AllocationChanged       │
│  │  Publishes:                   │    │                            │
│  │  → BacktestCompleted          │    │  Publishes:                │
│  │  → PerformanceReport          │    │  → RiskThresholdExceeded   │
│  │                               │    │  → BuySignalGenerated      │
│  └────────────────────────────────┘    │  → SellSignalGenerated     │
│                                        └────────────────────────────┘
│
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 4: PRESENTATION                                                │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Reporting & Communication (Supporting Subdomain)              │  │
│  │  ──────────────────────────────────────────────────────────────  │  │
│  │  • Generate daily reports                                       │  │
│  │  • Send notifications & alerts                                 │  │
│  │  • Prepare dashboard data                                      │  │
│  │  • Track IPO announcements                                     │  │
│  │                                                                 │  │
│  │  Subscribes to:                                                │  │
│  │  ← ScreeningCompleted                                          │  │
│  │  ← PortfolioRebalanced                                         │  │
│  │  ← RiskThresholdExceeded                                       │  │
│  │  ← BacktestCompleted                                           │  │
│  │                                                                 │  │
│  │  Publishes:                                                    │  │
│  │  → ReportGenerated                                             │  │
│  │  → NotificationSent                                            │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Context Relationships (Who Talks to Whom?)

### Direct Communication Paths:

```
Stock Analysis Context
├─ USES ACL: Market Data ACL (imports via anti-corruption layer)
└─ PUBLISHES TO EVENT BUS:
   ├─ ScreeningCompleted  → [Portfolio, Backtesting, Reporting, Risk & Signals]
   └─ StockScored         → [Reporting, Risk & Signals]

Portfolio Strategy Context  
├─ SUBSCRIBES TO:
│  ├─ MarketDataFetched (from Market Data)
│  ├─ ScreeningCompleted (from Stock Analysis)
│  └─ StockDataUpdated (from Market Data)
├─ USES ACL: Screening Results ACL (translates stock scores → portfolio decisions)
└─ PUBLISHES:
   ├─ PortfolioRebalanced  → [Reporting, Risk & Signals, Backtesting]
   └─ AllocationChanged    → [Risk & Signals, Backtesting]

Backtesting Context
├─ SUBSCRIBES TO:
│  ├─ PortfolioRebalanced (from Portfolio)
│  └─ ScreeningCompleted (from Stock Analysis)
└─ PUBLISHES:
   ├─ BacktestCompleted    → [Reporting, Risk & Signals]
   └─ PerformanceReport    → [Reporting]

Risk & Signals Context
├─ SUBSCRIBES TO:
│  ├─ PortfolioRebalanced (from Portfolio)
│  ├─ ScreeningCompleted (from Stock Analysis)
│  ├─ AllocationChanged (from Portfolio)
│  └─ BacktestCompleted (from Backtesting)
└─ PUBLISHES:
   ├─ RiskThresholdExceeded → [Reporting]
   ├─ BuySignalGenerated    → [Reporting]
   └─ SellSignalGenerated   → [Reporting]

Reporting Context
└─ SUBSCRIBES TO: Everything (all other contexts)
   ├─ ScreeningCompleted
   ├─ PortfolioRebalanced
   ├─ RiskThresholdExceeded
   ├─ BuySignalGenerated
   ├─ BacktestCompleted
   └─ Generates reports/alerts
```

---

## Data Flow Example: Full Screening → Reporting Workflow

```
TIME: 08:30 AM (Market opens)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1: Market Data Arrives
┌──────────────────────────┐
│ Market Data Provider     │
├──────────────────────────┤
│ Fetches NSE Bhavcopy     │
│ ├─ INFY: 2500, 0 vol=10M │
│ ├─ TCS: 3500, vol=8M     │
│ └─ HDFC: 2800, vol=5M    │
└──────────────────────────┘
         │ Publishes
         ▼
    MarketDataFetched(
        ticker="INFY", 
        date=2026-07-03,
        ohlc_data={...}
    )

STEP 2: ACL Translates Data
┌──────────────────────────┐
│ MarketDataACL            │
├──────────────────────────┤
│ External format:         │
│ {'pe': 15.5, 'd2e': 0.5} │
│         │                │
│         │ Translate      │
│         ▼                │
│ Domain model:            │
│ StockMetrics(            │
│   pe=Decimal(15.5),      │
│   debt_to_equity=...)    │
└──────────────────────────┘

STEP 3: Stock Analysis Runs
┌──────────────────────────┐
│ Stock Analysis Context   │
├──────────────────────────┤
│ 1. Load all stocks       │
│ 2. For each stock:       │
│    if stock.matches(     │
│       "Coffee Can"       │
│    ):                    │
│      matched.append()    │
│                          │
│ Results:                 │
│ ├─ INFY: MATCH (15 < 20) │
│ ├─ TCS: NO (25 > 20)     │
│ └─ HDFC: MATCH (18 < 20) │
└──────────────────────────┘
         │ Publishes
         ▼
    ScreeningCompleted(
        screen_name="Coffee Can",
        stocks_matched=23,
        total_evaluated=2500,
        timestamp=08:31
    )

STEP 4: Portfolio Reacts
┌──────────────────────────┐
│ Portfolio Context        │
│ (Subscribed to event)    │
├──────────────────────────┤
│ Receives ScreeningComplete│
│         ▼                │
│ Action: Rebalance        │
│ ├─ New INFY: +10%        │
│ ├─ New HDFC: +5%         │
│ └─ Reduce TCS: -15%      │
└──────────────────────────┘
         │ Publishes
         ▼
    PortfolioRebalanced(
        portfolio_name="My Portfolio",
        new_allocation={...},
        timestamp=08:32
    )

STEP 5: Risk Checks & Backtesting Start
┌──────────────────────────────────┐
│ Risk & Signals Context           │
│ (Subscribed to event)            │
├──────────────────────────────────┤
│ Analyzes new allocation          │
│ ├─ Concentration: 35% OK         │
│ ├─ Beta: 1.1 (acceptable)        │
│ └─ Expected Sharpe: 1.8          │
│                                  │
│ Result: No risk threshold exceeded
└──────────────────────────────────┘

┌──────────────────────────────────┐
│ Backtesting Context              │
│ (Subscribed to event)            │
├──────────────────────────────────┤
│ Simulates new allocation         │
│ Period: Last 5 years             │
│ ├─ Avg return: 18% p.a.          │
│ ├─ Max drawdown: -12%            │
│ └─ Sharpe ratio: 1.6             │
│                                  │
│ Conclusion: Beat benchmark       │
└──────────────────────────────────┘
         │ Both Publish
         ▼
    BacktestCompleted(...)
    BuySignalGenerated(
        stock="INFY",
        confidence=0.85
    )

STEP 6: Reporting Aggregates Everything
┌──────────────────────────────────────┐
│ Reporting Context                    │
│ (Subscribed to ALL events)           │
├──────────────────────────────────────┤
│ Has received:                        │
│ ✓ ScreeningCompleted (23 matched)   │
│ ✓ PortfolioRebalanced (weights)     │
│ ✓ BacktestCompleted (returns)       │
│ ✓ BuySignalGenerated (INFY buy)     │
│ → No risk alerts                    │
│                                     │
│ Generates:                          │
│ ├─ Daily Report PDF                │
│ ├─ Email Newsletter                 │
│ ├─ Dashboard Update                 │
│ └─ Slack Notification               │
└──────────────────────────────────────┘
         │ Sends
         ▼
    08:35 AM - Email received:
    "Morning Update: 23 stocks screened,
     portfolio rebalanced for 18% return,
     recommend buying INFY"

TOTAL TIME: 5 minutes
WITHOUT DDD: All tangled, debugging nightmare
WITH DDD: Clear flow, each context responsible
```

---

## Repository Organization After Refactoring

```
herrrickshaw/
│
├── global-stock-screener/           ← DOMAIN REPOSITORY
│   ├── src/
│   │   ├── stock_analysis/          ← Context 1: Core domain
│   │   ├── portfolio_strategy/      ← Context 2: Core domain
│   │   ├── backtesting/             ← Context 3: Support
│   │   ├── risk_signals/            ← Context 4: Support
│   │   ├── reporting/               ← Context 5: Support
│   │   ├── market_data/             ← NO! Moved to separate repo
│   │   ├── shared/                  ← Domain events, event bus
│   │   └── container.py
│   ├── tests/
│   ├── docs/
│   │   ├── domain_model.md          ← Ubiquitous Language
│   │   ├── context_map.md
│   │   └── architecture.md
│   └── README.md (domain-focused)
│
├── market-data-provider/            ← DATA REPOSITORY (Standalone)
│   ├── src/
│   │   ├── fetchers/
│   │   │   ├── nse_fetcher.py
│   │   │   ├── yfinance_fetcher.py
│   │   │   └── eodhd_fetcher.py
│   │   ├── models/
│   │   │   ├── market_data.py
│   │   │   └── cache.py
│   │   └── api.py                   ← Publishes domain events
│   ├── tests/
│   └── README.md
│
└── repository-systems/              ← INFRASTRUCTURE REPOSITORY (Standalone)
    ├── src/
    │   ├── vcrud/
    │   ├── deduplication/
    │   └── indexing/
    ├── tests/
    └── README.md (infrastructure-focused)
```

---

## Anti-Corruption Layer Examples

### Example 1: Market Data → Stock Analysis

```python
# External Data (NSE Format)
{
    "symbol": "INFY",
    "exchange": "NSE",
    "pe": 15.5,           ← NSE term
    "roe": 25.0,          ← NSE term  
    "d2e": 0.5,           ← NSE term
    "market_cap": 1000000,
    "timestamp": "2026-07-03"
}

# ACL Translates To
Stock(
    ticker=Ticker("INFY", "NSE"),
    metrics={
        "PE": Decimal("15.5"),        ← Domain term
        "ROE": Decimal("25.0"),       ← Domain term
        "DebtToEquity": Decimal("0.5"),
    },
    market_cap=Price(Decimal("1000000"), "INR"),
    last_updated=datetime(...)
)

# Stock Analysis sees ONLY domain terms
# Doesn't know about yfinance, NSE, or Bhavcopy
```

### Example 2: Screening Results → Portfolio

```python
# Stock Analysis Publishes
StockScored(
    ticker="INFY",
    score=0.95,                  ← Domain metric
    criteria_met=["PE < 20", "ROE > 20"],  ← Domain language
    confidence=0.85
)

# Portfolio ACL Translates
BuyableStock(
    ticker="INFY",
    allocation_weight=Decimal("5.0"),  ← Portfolio metric
    confidence=0.85,
    entry_price=Price(Decimal("2500"), "INR"),
    rationale="Passes Coffee Can criteria"  ← Domain language
)

# Portfolio doesn't know about "scoring"
# Only knows about allocation and confidence
```

---

## Event Bus Communication Pattern

```python
# Clear, decoupled communication

class StockAnalysisService:
    def run_screen(self, screen):
        # ... run screening logic
        self.event_bus.publish(ScreeningCompleted(...))
        # ✓ Publishes event, doesn't care who listens

class PortfolioService:
    def __init__(self, event_bus):
        # ✓ Subscribe once, forget about it
        event_bus.subscribe(
            ScreeningCompleted, 
            self.on_screening_complete
        )
    
    def on_screening_complete(self, event):
        # React to screening results
        self.rebalance_portfolio(event.stocks_matched)

class ReportingService:
    def __init__(self, event_bus):
        # ✓ Multiple contexts can subscribe to same event
        event_bus.subscribe(
            ScreeningCompleted,
            self.on_screening_complete
        )
    
    def on_screening_complete(self, event):
        # Generate report
        self.generate_daily_report(event)

# Result:
# - Stock Analysis doesn't know about Portfolio
# - Stock Analysis doesn't know about Reporting  
# - Portfolio and Reporting don't know about each other
# - Easy to add new context: just subscribe to events!
```

---

## Current vs. Future: Quick Comparison

| Aspect | Current (Tangled) | After DDD (Clean) |
|--------|---|---|
| **Repository Count** | 1 (52 branches) | 3 (independent repos) |
| **Domain Coupling** | Everything touches everything | Contexts connected via ACL + events |
| **Test Strategy** | Hard to isolate | Unit test in context, mock ACLs |
| **New Feature** | Touches 5+ files randomly | Clear: which context owns it? |
| **Onboarding Time** | 4-6 weeks (whole system) | 1-2 weeks (one context) |
| **Deploy** | All or nothing | Each context independently |
| **Terminology** | Mixed (infrastructure + domain) | Clear per context |
| **Debugging** | Trace through all code | Trace event flow between contexts |
| **Team Growth** | Merge conflicts everywhere | Each context has owner, no conflicts |
| **Regression Risk** | Change anything = test everything | Change in context X = test context X |

---

## Action Items (Next Week)

**Priority 1: Establish Structure**
- [ ] Create three repositories locally
- [ ] Move code to new structure (no behavior changes)
- [ ] Document domain models

**Priority 2: Add Boundaries**
- [ ] Create ACL from Market Data → Stock Analysis
- [ ] Define domain events interface
- [ ] Build simple event bus

**Priority 3: Refactor Key Domain**
- [ ] Move Stock aggregate to domain package
- [ ] Move screening logic to Stock.matches_screen()
- [ ] Create repository interfaces

**Priority 4: Test & Validate**
- [ ] Unit tests on Stock aggregate
- [ ] Integration tests on ScreeningService
- [ ] Verify no circular dependencies

---

## Key Principles Recap

1. **Bounded Contexts:** Each context owns its domain
2. **Ubiquitous Language:** One language per context
3. **Domain Events:** Async communication between contexts
4. **Anti-Corruption Layers:** Translation at boundaries
5. **Aggregates:** Clusters of related objects with clear ownership
6. **Value Objects:** Immutable, identified by attributes
7. **Repositories:** Abstract persistence behind domain interface

---

**Next: Read DDD_IMPLEMENTATION_GUIDE.md for code examples**

**Reference:** Evans, E. (2003). Domain-Driven Design
