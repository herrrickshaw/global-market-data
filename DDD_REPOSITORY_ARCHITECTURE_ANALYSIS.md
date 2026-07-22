# Domain-Driven Design: Repository Architecture Analysis & Refactoring Strategy

**Analysis of herrrickshaw/global-stock-screener using Domain-Driven Design principles**

*Based on Eric Evans' Domain-Driven Design (2003) and Özkan et al. 2025 Systematic Literature Review*

---

## Executive Summary

Your repository exhibits **critical domain clashes** where infrastructure concerns, data management, financial domain logic, and market analysis systems are tangled together. This analysis identifies **7 distinct bounded contexts** that should be separated, proposes anti-corruption layers, and reveals **5 key synergies** that can be leveraged once domains are properly isolated.

---

## Part I: Current State Analysis

### Current Repository Structure

```
global-stock-screener/
├── 🔴 CLASH: vCRUD System (Infrastructure Domain)
│   ├── vcrud_cli.py
│   ├── vcrud_manager.py
│   ├── db_handler.py
│   └── Documentation (21 files)
│
├── 🔴 CLASH: Data Deduplication (Infrastructure Domain)
│   ├── implement_deduplication.sh
│   ├── DATA_DEDUPLICATION_STRATEGY.md
│   └── setup_shared_data_symlinks.sh
│
├── 🔴 CLASH: Repository Management (Meta-infrastructure)
│   ├── generate_repo_index.py
│   ├── REPO_INDEX.md/json
│   └── Branch tracking
│
├── 🟡 MARKET DATA DOMAIN (Mixed with everything)
│   ├── fetch_market_ohlc.py
│   ├── bhavcopy_store.py
│   ├── nse_data_fetcher.py
│   ├── data_sources.py
│   └── market_calendar.py
│
├── 🟡 FINANCIAL SCREENING DOMAIN (Mixed with everything)
│   ├── full_indian_market_scan.py
│   ├── full_us_market_scan.py
│   ├── batch_analysis.py
│   ├── custom_screener.py
│   └── Multiple country-specific scans
│
├── 🟡 BACKTESTING & STRATEGY DOMAIN (Mixed with everything)
│   ├── backtest_screeners.py
│   ├── dl_strategy_eval.py
│   ├── ml_signal_engine.py
│   └── pattern_discovery.py
│
└── 🟡 REPORTING DOMAIN (Mixed with everything)
    ├── daily_combined_report.py
    ├── build_mailer.py
    ├── intraday_monitor.py
    └── ipo_tracker.py
```

---

## Part II: Domain Clashes Identified

### 🔴 Critical Clash #1: Infrastructure Mixed with Domain Logic

**Problem:** vCRUD system, data deduplication, and repository indexing are fundamentally **infrastructure concerns** (supporting systems) mixed with **domain logic** (stock market analysis).

**Why This Matters (DDD principle):**
- Evans: "The heart of software is its ability to solve domain-related problems"
- Your infrastructure is solving **meta-problems** (how to organize code) not **domain problems** (how to analyze markets)
- **Symptom:** 50+ files dedicated to infrastructure vs. core domain logic scattered

**Impact:**
```
Infrastructure Bloat:
- vCRUD system: 1,300+ lines for versioning
- Data dedup: 600+ lines for symlinks
- Repo index: 300+ lines for metadata
- Setup guides: 600+ lines for procedures

Total: ~2,900 lines of infrastructure

Domain Logic Scattered:
- No clear domain models for "Stock", "Market", "Portfolio"
- Screening logic mixed with data loading
- Backtesting mixed with signal generation
```

---

### 🟠 Clash #2: Polyglot Domain Languages in Single Repository

**Problem:** Each domain speaks a different language but uses the same codebase:

| Domain | Language/Terms | Files | Issues |
|--------|---|---|---|
| **Market Data** | NSE, BSE, Bhavcopy, OHLC, tickers, exchanges | fetch_market_ohlc.py, nse_data_fetcher.py | Terms like "cleaned_long.parquet" are infrastructure jargon, not domain |
| **Screening** | Screens, filters, criteria, Coffee Can, Darvas | batch_analysis.py, custom_screener.py | Mixes business logic with data access |
| **Backtesting** | Weights, returns, F1 scores, walk-forward | backtest_screeners.py, dl_strategy_eval.py | Optimization terminology clashes with screening language |
| **Reporting** | Signals, alerts, portfolio composition | daily_combined_report.py, build_mailer.py | Presentation concerns mixed with analysis |

**DDD Principle Violated:**
- Evans emphasizes **Ubiquitous Language**: one shared language per bounded context
- Your codebase mixes terminology without explicit boundaries
- Team members can't distinguish which domain they're working in

---

### 🟠 Clash #3: Branch Strategy as Domain Proxy

**Problem:** Your branch structure reflects technical solutions, not domain organization:

```
Current Branches (52 total):
├── feature/adr-16-ohlc-provider      ← Technical decision
├── feature/adr-17-yahoo-only         ← Technical decision
├── feature/batch-a                   ← Code organization, not domain
├── feature/batch-b
├── feature/dashboard-schedule        ← Deployment concern
├── dashboard/2026-07-02              ← Date-based, not domain-based
├── archive/legacy-scanners           ← Graveyard pattern
└── karz                               ← Personal/experiment naming
```

**Why This Matters:**
- Branches should represent **bounded contexts**, not technical decisions
- No domain-aligned team structure visible in branch names
- Hard to onboard someone asking: "Which branch implements Coffee Can screening?"

---

### 🟠 Clash #4: Shared Data as Anti-Pattern

**Problem:** `shared_data/` symlink strategy is solving a **symptom**, not the **cause**:

```
Symptom: 52 branches all need cache_seed/, fundamentals/, market_data/
Root Cause: No bounded context boundaries → everything needs everything

If you had proper contexts:
- Market Data Context: owns all market data
- Screening Context: imports Market Data via anti-corruption layer
- Backtesting Context: imports Market Data via anti-corruption layer
- Result: No need for symlink gymnastics
```

**Evans' Insight:**
- When multiple systems need the same data, it indicates **wrong domain boundaries**
- Proper separation → explicit, well-defined data flows

---

## Part III: Seven Bounded Contexts (Proposed DDD Structure)

### Context 1: **Market Data Provider** (Supporting Subdomain)

**Responsibility:** Fetch, validate, cache market data from external sources

**Ubiquitous Language:**
- Market, Ticker, OHLC, Bhavcopy, Exchange, Price, Volume
- NSE/BSE = data sources, not domain models

**Current Files to Move:**
```
fetch_market_ohlc.py
bhavcopy_store.py
bhavcopy_history.py
nse_data_fetcher.py
market_data_cache.py
market_calendar.py
data_sources.py
market_performance.py
```

**Outputs (Published Events):**
```python
# Market Data Context publishes:
- MarketDataFetched(ticker, date, ohlc_data)
- MarketCalendarUpdated(exchanges)
- DataValidationFailed(ticker, date, reason)
```

**Anti-Corruption Layer (ACL):**
- Translates NSE/yfinance/Bhavcopy formats → internal DTO
- Isolates external API changes from core domain

---

### Context 2: **Stock Analysis Engine** (Core Subdomain)

**Responsibility:** Analyze stocks using screening criteria

**Ubiquitous Language:**
- Stock, Criterion, Screen, Score, Ranking
- Coffee Can (pattern), P/E (ratio), ROE (metric)
- Match, Mismatch, Threshold

**Current Files to Move:**
```
custom_screener.py
full_indian_market_scan.py
full_us_market_scan.py
batch_analysis.py (extract screening logic)
pattern_discovery.py
liquidity.py
```

**Domain Model:**
```python
class Stock(AggregateRoot):
    ticker: str
    market: Exchange  # NSE/BSE/NASDAQ
    metrics: Dict[str, float]  # P/E, ROE, etc
    
    def passes_screen(self, criteria: Screen) -> bool:
        """Business logic: Does this stock match the screen?"""
        pass

class Screen(ValueObject):
    """Immutable set of criteria"""
    name: str
    criteria: List[Criterion]
```

**Inputs (Subscribes to):**
- `MarketDataFetched` → loads metrics
- `ScreenUpdated` → re-evaluates portfolio

**Outputs (Publishes):**
- `ScreeningComplete(stocks_matched, timestamp)`
- `StockScored(ticker, score, reasons)`

---

### Context 3: **Portfolio Strategy** (Core Subdomain)

**Responsibility:** Manage portfolio composition based on screening results

**Ubiquitous Language:**
- Portfolio, Position, Allocation, Weight, Return, Risk
- Entry, Exit, Rebalance
- Long/Short, Buy/Sell

**Current Files to Move:**
```
(New - extract from batch_analysis.py)
portfolio_manager.py (create)
position_tracker.py (create)
```

**Interacts With:**
- Imports from Stock Analysis (screening results)
- Publishes to Backtesting (portfolio state)
- Feeds Reporting (portfolio composition)

---

### Context 4: **Backtesting & Simulation** (Supporting Subdomain)

**Responsibility:** Evaluate strategy performance on historical data

**Ubiquitous Language:**
- Backtest, Walk-Forward, Return, Sharpe Ratio, MaxDD
- Weight Optimization, F1 Score
- Training/Validation period

**Current Files to Move:**
```
backtest_screeners.py
dl_strategy_eval.py
backtest_weight_optimization.py
ml_signal_engine.py
```

**Integration Point:**
- Imports historical portfolios from Portfolio Strategy
- Publishes metrics (returns, drawdown) to Reporting

---

### Context 5: **Risk & Signals** (Supporting Subdomain)

**Responsibility:** Generate trading signals and risk alerts

**Ubiquitous Language:**
- Signal, Confidence Level, Risk Alert
- Trend, Momentum, Volatility
- Buy/Sell/Hold recommendation

**Current Files to Move:**
```
(Partially from) ml_signal_engine.py
intraday_monitor.py (refactor)
pipeline_news.py
pipeline_historical.py
```

**Domain Events:**
- `BuySignalGenerated(stock, confidence)`
- `RiskThresholdExceeded(portfolio, metric, value)`

---

### Context 6: **Reporting & Communication** (Supporting Subdomain)

**Responsibility:** Present analysis results to stakeholders

**Ubiquitous Language:**
- Report, Dashboard, Alert, Notification
- Recipients, Channels, Frequency
- Content, Format

**Current Files to Move:**
```
daily_combined_report.py
build_mailer.py
ipo_tracker.py
daily_pipeline.sh (refactor)
```

**Input (Subscribes):**
- `ScreeningComplete` → generate report
- `PortfolioRebalanced` → send notification
- `RiskThresholdExceeded` → send alert

---

### Context 7: **Repository & Code Management** (Infrastructure Subdomain)

**Responsibility:** Track, version, and organize code across branches

**This is the vCRUD system - separate from domain logic**

**Key Insight from DDD Research (Özkan et al.):**
- Infrastructure concerns should be **completely isolated**
- Use separate repositories or strong module boundaries
- Never mix with domain logic

**Refactoring Action:**
```
Move to: repository-systems (standalone repo)
Purpose: Support development process
NOT: part of stock screening system
```

---

## Part IV: Anti-Corruption Layers (ACLs)

Evans defines ACLs as **translation layers** between bounded contexts.

### Example: Market Data → Stock Analysis

```python
# ❌ Current (coupled):
# stock_analysis.py imports fetch_market_ohlc directly
import fetch_market_ohlc
data = fetch_market_ohlc.fetch("INFY")  # Returns dict with NSE field names

# ✅ Refactored (ACL):
# market_data_acl.py (lives in Stock Analysis Context)
from market_data_provider import MarketDataService

class MarketDataACL:
    """Anti-Corruption Layer: Translates external Market Data format"""
    
    def __init__(self, market_data_service: MarketDataService):
        self.service = market_data_service
    
    def get_stock_metrics(self, ticker: str) -> StockMetrics:
        """
        Translates MarketData (external format) 
        → StockMetrics (domain model)
        """
        raw_data = self.service.fetch(ticker)
        return StockMetrics(
            pe_ratio=raw_data['pe'],  # Domain term
            roe_ratio=raw_data['roe'],  # Domain term
            debt_to_equity=raw_data['d2e'],  # Domain term
            market_cap=raw_data['market_cap']  # Domain term
        )
```

**Benefits of ACL:**
1. **Stock Analysis doesn't know about NSE/BSE terminology**
2. **If Market Data format changes, only ACL changes**
3. **Domain logic stays pure business rules**

### Example: Stock Analysis → Portfolio Strategy

```python
# screening_acl.py (lives in Portfolio Strategy Context)
from stock_analysis import ScreeningService

class ScreeningACL:
    """Translates Stock scores into Portfolio decisions"""
    
    def get_buyable_stocks(self, screen: Screen) -> List[BuyableStock]:
        """
        Takes Stock Analysis results → Portfolio-relevant data
        """
        scores = self.service.run_screen(screen)
        return [
            BuyableStock(
                ticker=stock.ticker,
                allocation_weight=self._calculate_weight(stock.score),
                confidence=stock.confidence_level,
                entry_price=stock.current_price
            )
            for stock in scores
        ]
```

---

## Part V: Data Flow with Proper Boundaries

### Current Problematic Flow:
```
fetch_market_ohlc.py
    ↓
cache_seed/ (symlinked to 52 branches)
    ↓
All 52 branches directly import cached data
    ↓
No clear ownership, no ACLs, tangled dependencies
```

### Proposed DDD Flow:
```
Market Data Provider Context
  │
  ├─→ Publishes: MarketDataFetched(ticker, date, ohlc)
  │
  ├─→ ACL → Stock Analysis Context
  │   - Translates NSE format → StockMetrics
  │   - Single point of change
  │
  ├─→ ACL → Backtesting Context
  │   - Translates format → HistoricalPriceData
  │
  └─→ ACL → Risk & Signals Context
      - Translates format → TimeSeriesData

Each context imports ONLY what it needs
Each context handles format translation itself
No shared caches violating boundaries
```

---

## Part VI: Synergies (After Refactoring)

### Synergy #1: **Ubiquitous Language Enforcement**

**Current State:** Terms like "cleaned_long.parquet" expose infrastructure, not domain

**After Refactoring:**
```python
# Each context has consistent terminology:

# Stock Analysis Context
stock.pe_ratio  # Domain term
stock.roe_ratio  # Domain term

# Portfolio Strategy Context
position.allocation_weight  # Domain term
portfolio.target_return  # Domain term

# Backtesting Context
backtest.sharpe_ratio  # Domain term
simulation.walk_forward_results  # Domain term
```

**Synergy Benefit:**
- ✅ New team members understand domain faster
- ✅ Easier to discuss business requirements
- ✅ Code reads like domain specification

---

### Synergy #2: **Event-Driven Communication**

**Current:** Direct coupling via imports and shared caches

**After:** Event-driven via domain events

```python
# Stock Analysis publishes events
event_bus.publish(ScreeningComplete(
    stocks_matched=100,
    screen_name="Coffee Can",
    timestamp=datetime.now()
))

# Multiple contexts can subscribe:
# 1. Portfolio Strategy: "I need to rebalance"
# 2. Reporting: "I need to generate report"  
# 3. Risk & Signals: "I need to check risk"
# 4. Backtesting: "I need to validate performance"

# Each context reacts independently
```

**Synergy Benefit:**
- ✅ Stock Analysis doesn't know about Portfolio
- ✅ Easy to add new contexts (e.g., "Alerts" context)
- ✅ No circular dependencies

---

### Synergy #3: **Repository Hygiene**

**Current:** Repository is bloated with infrastructure (vCRUD, dedup, indexing)

**After:** Three separate, focused repositories

```
Repository 1: stock-screening (Domain)
├── Stock Analysis Context
├── Portfolio Strategy Context  
├── Backtesting Context
├── Risk & Signals Context
└── Reporting Context (delegates to external system)

Repository 2: repository-systems (Infrastructure)
├── vCRUD system
├── Data deduplication
├── Repository indexing
└── Shared data management

Repository 3: market-data-provider (Data)
├── Market Data Context
└── External API integrations
```

**Synergy Benefit:**
- ✅ Each repo has a single reason to change
- ✅ 52 branches can become ~5 focused feature branches
- ✅ Easier to version, release, and deploy independently

---

### Synergy #4: **Team Scalability**

**Current:** One person touching vCRUD, data dedup, market data, screening, backtesting, reporting

**After:** Team roles align with bounded contexts

```
Ideal Team Structure:
│
├─ Market Data Lead
│  └─ Owns: fetch_market_ohlc, data validation, external APIs
│
├─ Core Domain Lead (Stock Analysis)
│  └─ Owns: screening logic, domain models
│
├─ Strategy Lead (Portfolio)
│  └─ Owns: allocation, rebalancing, position management
│
├─ Risk & Signals Lead
│  └─ Owns: alerts, signal generation
│
├─ Backtesting Lead
│  └─ Owns: performance evaluation, optimization
│
├─ Reporting Lead
│  └─ Owns: dashboards, notifications
│
└─ Infrastructure Lead
   └─ Owns: vCRUD, repository systems, deployment
```

**Synergy Benefit:**
- ✅ Each team member has clear ownership
- ✅ Fewer merge conflicts
- ✅ Easier to hire specialists

---

### Synergy #5: **Testing Strategy Clarity**

**Current:** Hard to test domains separately (everything coupled)

**After:** Clear testing strategies per context

```python
# Market Data Context
└─ Unit tests: Data validation
   Integration tests: External API calls

# Stock Analysis Context
└─ Unit tests: Screening logic (pure functions)
   Contract tests: MarketDataACL interface
   
# Portfolio Strategy Context
└─ Unit tests: Allocation algorithms
   Integration tests: Interacts with Stock Analysis via ACL

# Backtesting Context
└─ Integration tests: Replays historical portfolios
   Performance tests: Walk-forward simulations

# Risk & Signals Context
└─ Unit tests: Signal generation
   Property tests: Risk calculations always valid
```

**Synergy Benefit:**
- ✅ Clear which tests to write
- ✅ Fast unit tests (no external deps)
- ✅ Isolated integration tests

---

## Part VII: Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)

**Goal:** Establish bounded context structure without changing behavior

```bash
# 1. Create repo structure
mkdir -p stock-screening/{market_data,stock_analysis,portfolio,backtesting,risk_signals,reporting}

# 2. Move Market Data Context
mkdir -p market-data-provider/src
mv fetch_market_ohlc.py → market-data-provider/src/
mv nse_data_fetcher.py → market-data-provider/src/
mv bhavcopy_store.py → market-data-provider/src/

# 3. Create __init__.py for each context
touch stock-screening/market_data/__init__.py
touch stock-screening/stock_analysis/__init__.py
# etc.

# 4. Setup PYTHONPATH/imports
stock-screening/
├── __init__.py
├── market_data/
│   ├── __init__.py
│   └── acl.py  # Anti-Corruption Layer
└── stock_analysis/
    └── __init__.py
```

**No behavior changes yet** - just reorganization.

---

### Phase 2: Anti-Corruption Layers (Weeks 3-4)

**Goal:** Introduce ACLs, reduce direct coupling

```python
# stock-screening/stock_analysis/market_data_acl.py
from market_data_provider import MarketDataService

class MarketDataACL:
    """Translation layer between contexts"""
    def __init__(self, service: MarketDataService):
        self.service = service
    
    def get_stock(self, ticker: str) -> Stock:
        """Market Data → Stock Analysis"""
        raw = self.service.fetch(ticker)
        return Stock(
            ticker=ticker,
            pe_ratio=raw['pe'],
            roe_ratio=raw['roe'],
            # ... domain fields
        )
```

**Tests:**
```python
def test_acl_translates_formats():
    """Verify NSE data → Stock model conversion"""
    service = MockMarketDataService()
    acl = MarketDataACL(service)
    stock = acl.get_stock("INFY")
    assert isinstance(stock, Stock)
    assert stock.pe_ratio == expected_value
```

---

### Phase 3: Event-Driven Communication (Weeks 5-6)

**Goal:** Replace direct imports with domain events

```python
# Instead of:
screening_result = screener.run(screen)
portfolio.add_stocks(screening_result.stocks)

# Use:
event_bus = EventBus()
event_bus.publish(ScreeningComplete(
    stocks=screening_result.stocks,
    screen=screen
))

# Portfolio subscribes:
event_bus.subscribe(ScreeningComplete, portfolio.on_screening_complete)
```

**Benefits:**
- ✅ No circular dependencies
- ✅ Easy to add new subscribers
- ✅ Each context processes at its own pace

---

### Phase 4: Repository Cleanup (Weeks 7-8)

**Goal:** Remove infrastructure from domain repo

**Delete from global-stock-screener:**
- vcrud_cli.py, vcrud_manager.py, db_handler.py
- implement_deduplication.sh, setup_shared_data_symlinks.sh
- generate_repo_index.py, REPO_INDEX.md, REPO_INDEX.json

**These move to repository-systems** (already created as standalone repo)

**New global-stock-screener structure:**
```
global-stock-screener/
├── src/
│   ├── market_data/
│   ├── stock_analysis/
│   ├── portfolio/
│   ├── backtesting/
│   ├── risk_signals/
│   └── reporting/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
│   └── domain_model.md (Ubiquitous Language)
└── README.md (focuses on domain, not infrastructure)
```

---

### Phase 5: Branch Strategy Update (Week 9)

**Old branches (52 total):**
```
feature/batch-a          ← vague, technical
feature/adr-16           ← infrastructure decision
dashboard/2026-07-02     ← deployment artifact
```

**New branch strategy (context-aligned):**
```
Ongoing feature development:
├── feature/stock-analysis-coffee-can-enhancement
├── feature/portfolio-weight-optimization
├── feature/risk-threshold-alerts
├── feature/backtesting-walk-forward-validation
└── feature/reporting-dashboard-redesign

Bug fixes:
├── fix/stock-analysis-pe-calculation
├── fix/portfolio-rebalancing-edge-case
└── fix/market-data-validation

Infrastructure:
├── infra/add-event-bus-framework
├── infra/setup-ci-cd-per-context
└── infra/add-contract-testing
```

---

## Part VIII: Expected Outcomes

### Before Refactoring
```
Metrics:
- Repositories: 1 (with 52 branches)
- Bounded Contexts: 0 (implicit, tangled)
- Domain Models: None (logic scattered in functions)
- Test Coverage: Unclear (no boundaries)
- Onboarding Time: 4-6 weeks to understand system
- Deploy Coupling: All or nothing
```

### After Refactoring
```
Metrics:
- Repositories: 3 (focused, independent)
- Bounded Contexts: 6 explicit, with clear ACLs
- Domain Models: Clear per context (Stock, Portfolio, Screen, etc.)
- Test Coverage: 80%+ (unit tests per context)
- Onboarding Time: 1-2 weeks (each context is small)
- Deploy Coupling: 0 (each context independent)
```

---

## Part IX: Key DDD Principles Applied

| Evans' Principle | Current State | After Refactoring |
|---|---|---|
| **Bounded Context** | None visible | 6 explicit contexts |
| **Ubiquitous Language** | Mixed terms (infrastructure + domain) | Clean per-context terminology |
| **Domain Model** | Scattered logic in functions | Clear aggregates (Stock, Portfolio, Screen) |
| **Anti-Corruption Layer** | Direct coupling | Explicit ACLs between contexts |
| **Domain Events** | Direct method calls | Event-driven communication |
| **Repository Pattern** | Ad-hoc data loading | Clear repositories per context |
| **Value Objects** | Rare | Screen, Criteria, StockMetrics |
| **Aggregates** | Not identified | Stock (AR), Portfolio (AR) |

---

## Part X: Research Insights (Özkan et al., 2025)

### Finding #1: Implementation Complexity
**Study Result:** DDD implementation significantly depends on team expertise

**Your Situation:** 
- ✅ You understand domain (market analysis)
- ⚠️  Refactoring requires infrastructure discipline
- **Action:** Prioritize Phase 1-2 (establish contexts, add ACLs)

### Finding #2: Stakeholder Involvement
**Study Result:** Key stakeholders (domain experts, architects) critical for success

**Your Situation:**
- You are domain expert (financial modeling background)
- **Action:** Document bounded contexts for your team
- **Deliverable:** Domain Vision Statement per context

### Finding #3: Microservices ≠ Required
**Study Result:** DDD works in monoliths; doesn't require microservices

**Your Situation:**
- ✅ Refactor in single repo first
- Later: Deploy as microservices IF needed
- **Do not:** Assume you need microservices

### Finding #4: Evaluation Metrics Lacking
**Study Result:** 36% of reviewed studies lacked empirical evaluation

**Your Situation:**
- Measure before/after:
  - Build time (should decrease)
  - Test execution time (should increase per-context)
  - Onboarding time (should decrease)
  - Deploy frequency (should increase)

---

## Part XI: Conclusion & Next Steps

### The Core Problem
Your repository conflates **infrastructure** (vCRUD, dedup, indexing) with **domain logic** (screening, backtesting, portfolio management). Evans emphasizes: **focus on the domain model, not the infrastructure.**

### The Solution
**Separate bounded contexts:**
1. Stop treating vCRUD/dedup as domain concerns
2. Create explicit domain models (Stock, Portfolio, Screen)
3. Use anti-corruption layers between contexts
4. Align team structure with bounded contexts

### Immediate Actions (Next Week)

**1. Create domain model diagram** (30 min)
```
Stock Analysis Domain:
  Stock (AR) ← Portfolio (AR)
    ↓
  Screen (VO) & Criteria (VO)
```

**2. Document Ubiquitous Language** (1 hour)
```markdown
# Stock Analysis Context
- Stock: A company traded on an exchange
- Screen: A set of criteria for filtering stocks
- Criterion: A single rule (e.g., PE < 20)
- Score: How well a stock matches a screen
```

**3. Create repository structure** (1 hour)
```bash
mkdir -p global-stock-screener/{market_data,stock_analysis,portfolio,...}
touch {market_data,stock_analysis,...}/__init__.py
```

**4. Move first context** (2 hours)
- Market Data Context (least coupled)
- Creates template for other moves

### Long-term Impact (3-6 Months)

- ✅ 52 branches → 5-7 focused feature branches
- ✅ ~2,900 lines of infrastructure removed from domain
- ✅ 6 explicit bounded contexts with clear ownership
- ✅ 80%+ test coverage (per context)
- ✅ Independent deployment per context
- ✅ Onboarding time: 4-6 weeks → 1-2 weeks

---

## References

1. Evans, E. (2003). *Domain-Driven Design: Tackling Complexity in the Heart of Software*
2. Özkan, O., Babur, Ö., & van den Brand, M. (2025). Domain-Driven Design in Software Development: A Systematic Literature Review on Implementation, Challenges, and Effectiveness
3. Evans, E. Bounded Contexts discussion - https://domainlanguage.com/
4. Fowler, M. (2015). Microservices - https://martinfowler.com/articles/microservices.html

---

**Document Version:** 1.0  
**Date:** 2026-07-03  
**Author:** Domain-Driven Architecture Analysis  
**Status:** Ready for Implementation Planning
