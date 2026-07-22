# DDD Implementation Guide: From Theory to Practice

**Practical step-by-step guide for refactoring global-stock-screener using Domain-Driven Design**

---

## Section 1: Create Domain Model (Week 1)

### Step 1.1: Define Value Objects

Value Objects are immutable, identified by their attributes (not ID). They represent concepts in your domain.

**File: `stock-screening/stock_analysis/domain/models.py`**

```python
from dataclasses import dataclass
from typing import List
from enum import Enum
from decimal import Decimal

@dataclass(frozen=True)
class Price:
    """Value Object: Represents a stock price"""
    value: Decimal
    currency: str = "INR"
    
    def __post_init__(self):
        if self.value < 0:
            raise ValueError("Price cannot be negative")

@dataclass(frozen=True)
class Ratio:
    """Value Object: Financial ratio (P/E, ROE, etc.)"""
    name: str
    value: Decimal
    threshold: Decimal
    
    @property
    def passes_threshold(self) -> bool:
        return self.value < self.threshold

@dataclass(frozen=True)
class Ticker:
    """Value Object: Stock ticker (INFY, TCS, etc.)"""
    symbol: str
    exchange: str  # NSE, BSE, NASDAQ
    
    def __post_init__(self):
        if not self.symbol or len(self.symbol) > 10:
            raise ValueError("Invalid ticker")

@dataclass(frozen=True)
class Criterion:
    """Value Object: A single screening criterion"""
    metric_name: str  # "PE", "ROE", etc.
    operator: str  # "<", ">", "=="
    threshold: Decimal
    
    def matches(self, value: Decimal) -> bool:
        """Business logic: Does value match this criterion?"""
        if self.operator == "<":
            return value < self.threshold
        elif self.operator == ">":
            return value > self.threshold
        elif self.operator == "==":
            return value == self.threshold
        else:
            raise ValueError(f"Unknown operator: {self.operator}")

@dataclass(frozen=True)
class Screen(ValueObject):
    """Value Object: Immutable set of screening criteria"""
    name: str
    criteria: List[Criterion]
    version: int = 1
    
    def __post_init__(self):
        if not self.criteria:
            raise ValueError("Screen must have at least one criterion")
```

### Step 1.2: Define Aggregates

Aggregates are clusters of domain objects bound together. Each has a single root (Aggregate Root).

**File: `stock-screening/stock_analysis/domain/aggregates.py`**

```python
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

@dataclass
class Stock:
    """
    AGGREGATE ROOT: Represents a stock with all its attributes.
    
    Invariants:
    - Ticker must be unique
    - All metrics must be positive
    - Fundamentals must be current (< 1 year old)
    """
    ticker: Ticker
    metrics: dict  # {"PE": 15.5, "ROE": 20.0, ...}
    market_cap: Price
    last_updated: datetime
    
    # Aggregate state
    screening_results: dict = field(default_factory=dict)
    
    def matches_screen(self, screen: Screen) -> bool:
        """
        Business Logic: Evaluate if stock passes all criteria in screen.
        
        This is WHERE THE DOMAIN LOGIC LIVES - not in data loading,
        not in infrastructure, but in the aggregate itself.
        """
        for criterion in screen.criteria:
            metric_value = Decimal(str(self.metrics.get(criterion.metric_name, 0)))
            if not criterion.matches(metric_value):
                return False
        return True
    
    def record_screening_result(self, screen: Screen, passed: bool) -> None:
        """Record that this stock was evaluated against a screen"""
        self.screening_results[screen.name] = {
            "passed": passed,
            "timestamp": datetime.now()
        }
    
    @property
    def is_fresh(self) -> bool:
        """Invariant check: Is data current?"""
        age_days = (datetime.now() - self.last_updated).days
        return age_days < 365

@dataclass
class Portfolio:
    """
    AGGREGATE ROOT: Represents a portfolio of positions.
    
    Invariants:
    - Total allocation must equal 100%
    - No duplicate holdings
    - Each position must have positive weight
    """
    name: str
    positions: List['Position'] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_rebalanced: datetime = field(default_factory=datetime.now)
    
    def add_position(self, stock: Stock, allocation: Decimal) -> None:
        """
        Business Logic: Add a stock to portfolio with given allocation.
        
        Maintains invariants:
        - Checks if position already exists
        - Validates allocation percentage
        """
        if allocation <= 0 or allocation > 100:
            raise ValueError("Allocation must be between 0 and 100")
        
        if any(p.stock.ticker == stock.ticker for p in self.positions):
            raise ValueError(f"Position {stock.ticker} already exists")
        
        self.positions.append(Position(stock, allocation))
        self._ensure_invariants()
    
    def _ensure_invariants(self) -> None:
        """Check that portfolio invariants are maintained"""
        total_allocation = sum(p.allocation for p in self.positions)
        if total_allocation > 100:
            raise ValueError(f"Total allocation {total_allocation}% exceeds 100%")
    
    @property
    def total_allocation(self) -> Decimal:
        return sum(p.allocation for p in self.positions)
    
    def rebalance(self, new_weights: dict) -> None:
        """
        Business Logic: Rebalance portfolio to new target weights.
        
        This is a domain operation, not a technical operation.
        """
        # Validate new weights
        if sum(new_weights.values()) != 100:
            raise ValueError("Weights must sum to 100%")
        
        # Update positions
        for position in self.positions:
            ticker_str = position.stock.ticker.symbol
            if ticker_str in new_weights:
                position.allocation = new_weights[ticker_str]
        
        self.last_rebalanced = datetime.now()

@dataclass
class Position:
    """
    PART OF PORTFOLIO AGGREGATE: A single holding.
    Not an aggregate root - only accessed through Portfolio.
    """
    stock: Stock
    allocation: Decimal  # Percentage (0-100)
    entry_price: Optional[Price] = None
    entry_date: Optional[datetime] = None
```

---

## Section 2: Create Repositories (Week 1-2)

Repositories retrieve aggregates from persistence. They abstract how data is stored.

**File: `stock-screening/stock_analysis/domain/repositories.py`**

```python
from abc import ABC, abstractmethod
from typing import List, Optional

class StockRepository(ABC):
    """
    Repository for Stock aggregates.
    
    This is an interface - implementations handle persistence details.
    """
    
    @abstractmethod
    def get_by_ticker(self, ticker: Ticker) -> Optional[Stock]:
        """Retrieve a stock by ticker"""
        pass
    
    @abstractmethod
    def find_all(self) -> List[Stock]:
        """Retrieve all stocks"""
        pass
    
    @abstractmethod
    def save(self, stock: Stock) -> None:
        """Persist a stock aggregate"""
        pass

class PortfolioRepository(ABC):
    """Repository for Portfolio aggregates"""
    
    @abstractmethod
    def get_by_name(self, name: str) -> Optional[Portfolio]:
        pass
    
    @abstractmethod
    def find_all(self) -> List[Portfolio]:
        pass
    
    @abstractmethod
    def save(self, portfolio: Portfolio) -> None:
        pass
```

**File: `stock-screening/stock_analysis/infrastructure/repositories.py`**

```python
# Implementation details - how data is actually stored
from stock_analysis.domain.repositories import StockRepository, Stock

class InMemoryStockRepository(StockRepository):
    """Simple in-memory implementation for testing"""
    
    def __init__(self):
        self.stocks = {}
    
    def get_by_ticker(self, ticker: Ticker) -> Optional[Stock]:
        return self.stocks.get(ticker.symbol)
    
    def find_all(self) -> List[Stock]:
        return list(self.stocks.values())
    
    def save(self, stock: Stock) -> None:
        self.stocks[stock.ticker.symbol] = stock

class PostgresStockRepository(StockRepository):
    """Production implementation using PostgreSQL"""
    
    def __init__(self, db_connection):
        self.db = db_connection
    
    def get_by_ticker(self, ticker: Ticker) -> Optional[Stock]:
        query = """
            SELECT * FROM stocks 
            WHERE symbol = %s AND exchange = %s
        """
        result = self.db.query(query, (ticker.symbol, ticker.exchange))
        return self._to_domain_object(result) if result else None
    
    def save(self, stock: Stock) -> None:
        # Implementation details of persistence
        pass
    
    @staticmethod
    def _to_domain_object(db_row) -> Stock:
        """Convert DB row to Stock domain object"""
        # This is an anti-corruption layer within the repo
        return Stock(
            ticker=Ticker(db_row['symbol'], db_row['exchange']),
            metrics={...},
            # etc.
        )
```

---

## Section 3: Create Application Services (Week 2)

Application Services orchestrate domain logic. They're the "thin layer" between infrastructure and domain.

**File: `stock-screening/stock_analysis/application/services.py`**

```python
from typing import List
from stock_analysis.domain.repositories import StockRepository, PortfolioRepository
from stock_analysis.domain.aggregates import Stock, Portfolio, Screen

class ScreeningService:
    """
    Application Service: Orchestrates screening operation.
    
    Thin layer that:
    1. Loads aggregates from repositories
    2. Calls domain logic
    3. Saves results
    4. Publishes events
    """
    
    def __init__(self, 
                 stock_repo: StockRepository,
                 portfolio_repo: PortfolioRepository,
                 event_bus):
        self.stock_repo = stock_repo
        self.portfolio_repo = portfolio_repo
        self.event_bus = event_bus
    
    def run_screen(self, screen: Screen) -> ScreeningResult:
        """
        High-level operation: Run screening
        
        Steps:
        1. Get all stocks
        2. Evaluate each stock (DOMAIN LOGIC in Stock.matches_screen)
        3. Record results
        4. Publish event
        """
        # Step 1: Load aggregates
        all_stocks = self.stock_repo.find_all()
        
        # Step 2: Apply domain logic
        matched_stocks = []
        for stock in all_stocks:
            if stock.matches_screen(screen):  # ← DOMAIN LOGIC
                matched_stocks.append(stock)
                stock.record_screening_result(screen, True)
            else:
                stock.record_screening_result(screen, False)
        
        # Step 3: Save updated aggregates
        for stock in all_stocks:
            self.stock_repo.save(stock)
        
        # Step 4: Publish domain event
        result = ScreeningResult(
            screen=screen,
            matched_stocks=matched_stocks,
            total_evaluated=len(all_stocks)
        )
        
        self.event_bus.publish(ScreeningCompleted(
            screen_name=screen.name,
            stocks_matched=len(matched_stocks),
            total_evaluated=len(all_stocks)
        ))
        
        return result

class PortfolioService:
    """Application Service for portfolio operations"""
    
    def __init__(self, 
                 portfolio_repo: PortfolioRepository,
                 event_bus):
        self.portfolio_repo = portfolio_repo
        self.event_bus = event_bus
    
    def rebalance_portfolio(self, 
                          portfolio_name: str,
                          new_weights: dict) -> None:
        """
        Application Service operation: Rebalance portfolio
        
        Orchestrates:
        1. Load portfolio aggregate
        2. Validate new weights
        3. Call domain logic (Portfolio.rebalance)
        4. Save
        5. Publish event
        """
        # Load aggregate
        portfolio = self.portfolio_repo.get_by_name(portfolio_name)
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_name} not found")
        
        # Call domain logic
        portfolio.rebalance(new_weights)  # ← DOMAIN LOGIC
        
        # Persist
        self.portfolio_repo.save(portfolio)
        
        # Publish event
        self.event_bus.publish(PortfolioRebalanced(
            portfolio_name=portfolio_name,
            new_allocation=new_weights
        ))
```

---

## Section 4: Anti-Corruption Layer (Week 2-3)

ACL translates between external systems and your domain.

**File: `stock-screening/stock_analysis/domain/acl/market_data_acl.py`**

```python
"""
Anti-Corruption Layer: Translates Market Data external format
to Stock Analysis domain objects.

This isolates domain logic from external system changes.
"""

from market_data_provider import MarketDataService
from stock_analysis.domain.models import (
    Ticker, Price, Ratio, Stock
)
from decimal import Decimal

class MarketDataACL:
    """
    Translation layer between Market Data Provider context
    and Stock Analysis context.
    
    Example:
    Market Data says: {'pe': 15.5, 'd2e': 0.5, 'market_cap': 1000000}
    Stock Analysis says: {"PE": Decimal(15.5), "DebtToEquity": Decimal(0.5)}
    """
    
    def __init__(self, market_data_service: MarketDataService):
        self.service = market_data_service
    
    def get_stock_from_market_data(self, ticker: str) -> Stock:
        """
        Transform external market data format into domain Stock.
        
        ✅ Stock analysis doesn't know about NSE/yfinance terminology
        ✅ If market data format changes, only this file changes
        ✅ Domain logic stays pure
        """
        # Get raw data from external system
        raw_data = self.service.fetch(ticker)
        
        # Translate to domain model
        stock = Stock(
            ticker=Ticker(
                symbol=raw_data['symbol'],
                exchange=self._map_exchange(raw_data.get('exchange'))
            ),
            metrics=self._extract_metrics(raw_data),
            market_cap=Price(Decimal(str(raw_data['market_cap'])), "INR"),
            last_updated=raw_data['timestamp']
        )
        
        return stock
    
    @staticmethod
    def _extract_metrics(raw_data: dict) -> dict:
        """Extract and translate financial metrics"""
        return {
            "PE": Decimal(str(raw_data.get('pe', 0))),
            "ROE": Decimal(str(raw_data.get('roe', 0))),
            "DebtToEquity": Decimal(str(raw_data.get('d2e', 0))) / 100,
            "CurrentRatio": Decimal(str(raw_data.get('current_ratio', 0))),
        }
    
    @staticmethod
    def _map_exchange(external_exchange: str) -> str:
        """Translate external exchange names to domain names"""
        mapping = {
            'NSE': 'NSE',
            'BSE': 'BSE',
            'NASDAQ': 'NASDAQ',
            'NYSE': 'NYSE',
        }
        return mapping.get(external_exchange, external_exchange)
```

**Usage in Application Service:**

```python
# ✅ Application service uses ACL
class ScreeningService:
    def __init__(self, market_data_service: MarketDataService):
        self.acl = MarketDataACL(market_data_service)
    
    def load_stocks_for_screening(self) -> List[Stock]:
        """Load stocks through ACL"""
        tickers = self.get_all_tickers()
        stocks = []
        
        for ticker in tickers:
            # ACL translates external format → domain model
            stock = self.acl.get_stock_from_market_data(ticker)
            stocks.append(stock)
        
        return stocks
```

---

## Section 5: Domain Events (Week 3)

Domain Events represent things that happened in the business.

**File: `stock-screening/shared/domain_events.py`**

```python
from dataclasses import dataclass
from datetime import datetime
from typing import List

class DomainEvent:
    """Base class for all domain events"""
    timestamp: datetime = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now()

@dataclass
class ScreeningCompleted(DomainEvent):
    """
    Domain Event: Screening was completed
    
    Published by: Stock Analysis Context
    Subscribed to by: Portfolio, Reporting, Risk & Signals
    """
    screen_name: str
    stocks_matched: int
    total_evaluated: int
    timestamp: datetime = None

@dataclass
class PortfolioRebalanced(DomainEvent):
    """
    Domain Event: Portfolio was rebalanced
    
    Published by: Portfolio Strategy Context
    Subscribed to by: Reporting, Risk & Signals, Backtesting
    """
    portfolio_name: str
    new_allocation: dict  # {"INFY": 20, "TCS": 30, ...}
    timestamp: datetime = None

@dataclass
class RiskThresholdExceeded(DomainEvent):
    """
    Domain Event: Risk threshold exceeded
    
    Published by: Risk & Signals Context
    Subscribed to by: Reporting (alerts), Risk & Signals
    """
    portfolio_name: str
    metric_name: str
    threshold: float
    actual_value: float
    timestamp: datetime = None

@dataclass
class StockDataUpdated(DomainEvent):
    """
    Domain Event: Stock metrics were refreshed
    
    Published by: Market Data Context
    Subscribed to by: Stock Analysis (invalidate cache)
    """
    ticker: str
    exchange: str
    update_time: datetime
    timestamp: datetime = None
```

**Event Bus (Simple In-Process):**

```python
from typing import Callable, List, Type
from collections import defaultdict

class EventBus:
    """
    Simple in-process event bus.
    
    For production: Use message broker (RabbitMQ, Kafka)
    """
    
    def __init__(self):
        self.subscribers: dict[Type[DomainEvent], List[Callable]] = defaultdict(list)
    
    def subscribe(self, event_type: Type[DomainEvent], handler: Callable) -> None:
        """Subscribe to events"""
        self.subscribers[event_type].append(handler)
    
    def publish(self, event: DomainEvent) -> None:
        """Publish event to all subscribers"""
        event_type = type(event)
        
        for handler in self.subscribers[event_type]:
            try:
                handler(event)
            except Exception as e:
                # Log error but continue with other subscribers
                print(f"Error in event handler: {e}")
```

**Usage:**

```python
# Portfolio listens for screening
event_bus.subscribe(
    ScreeningCompleted,
    lambda event: portfolio_service.on_screening_complete(event)
)

# Reporting listens for rebalancing
event_bus.subscribe(
    PortfolioRebalanced,
    lambda event: reporting_service.on_portfolio_rebalanced(event)
)

# When screening completes, it publishes event
event_bus.publish(ScreeningCompleted(
    screen_name="Coffee Can",
    stocks_matched=15,
    total_evaluated=2500
))

# All subscribers are automatically notified
```

---

## Section 6: Tests (Week 3-4)

DDD enables isolated, focused tests.

**File: `tests/unit/stock_analysis/test_stock_aggregate.py`**

```python
import pytest
from decimal import Decimal
from stock_analysis.domain.aggregates import Stock
from stock_analysis.domain.models import (
    Ticker, Price, Screen, Criterion
)

class TestStockAggregate:
    """Unit tests for Stock aggregate - pure domain logic"""
    
    @pytest.fixture
    def stock(self):
        return Stock(
            ticker=Ticker("INFY", "NSE"),
            metrics={
                "PE": Decimal("15.5"),
                "ROE": Decimal("25.0"),
            },
            market_cap=Price(Decimal("1000000"), "INR"),
            last_updated=datetime.now()
        )
    
    @pytest.fixture
    def coffee_can_screen(self):
        """Coffee Can criterion: PE < 20, ROE > 20"""
        return Screen(
            name="Coffee Can",
            criteria=[
                Criterion("PE", "<", Decimal("20")),
                Criterion("ROE", ">", Decimal("20")),
            ]
        )
    
    def test_stock_passes_screen(self, stock, coffee_can_screen):
        """Stock with PE=15.5, ROE=25 should pass Coffee Can screen"""
        assert stock.matches_screen(coffee_can_screen) is True
    
    def test_stock_fails_screen_low_pe(self):
        """Stock with PE=25 should fail Coffee Can screen"""
        bad_stock = Stock(
            ticker=Ticker("BAD", "NSE"),
            metrics={"PE": Decimal("25"), "ROE": Decimal("25")},
            market_cap=Price(Decimal("1000000"), "INR"),
            last_updated=datetime.now()
        )
        screen = Screen(
            name="Coffee Can",
            criteria=[Criterion("PE", "<", Decimal("20"))]
        )
        assert bad_stock.matches_screen(screen) is False
    
    def test_portfolio_cannot_exceed_100_allocation(self):
        """Portfolio invariant: total allocation ≤ 100%"""
        portfolio = Portfolio(name="Test")
        
        # Add positions totaling 100%
        portfolio.add_position(Stock(...), Decimal("50"))
        portfolio.add_position(Stock(...), Decimal("50"))
        
        # Trying to add more should fail
        with pytest.raises(ValueError):
            portfolio.add_position(Stock(...), Decimal("10"))
    
    def test_cannot_add_duplicate_position(self):
        """Portfolio invariant: no duplicate holdings"""
        portfolio = Portfolio(name="Test")
        stock = Stock(ticker=Ticker("INFY", "NSE"), ...)
        
        portfolio.add_position(stock, Decimal("20"))
        
        with pytest.raises(ValueError):
            portfolio.add_position(stock, Decimal("20"))
```

**File: `tests/integration/stock_analysis/test_screening_service.py`**

```python
import pytest
from stock_analysis.application.services import ScreeningService
from stock_analysis.infrastructure.repositories import InMemoryStockRepository
from stock_analysis.domain.models import Screen, Criterion
from stock_analysis.domain.aggregates import Stock

class TestScreeningService:
    """Integration tests for screening operation"""
    
    @pytest.fixture
    def screening_service(self):
        repo = InMemoryStockRepository()
        event_bus = SimpleEventBus()
        return ScreeningService(repo, event_bus)
    
    def test_screening_produces_correct_matches(self, screening_service):
        """Screening service correctly identifies matching stocks"""
        # Setup: Add stocks to repository
        stock1 = Stock(...)  # INFY: PE=15, ROE=25 (matches)
        stock2 = Stock(...)  # TCS: PE=25, ROE=20 (doesn't match)
        
        screening_service.stock_repo.save(stock1)
        screening_service.stock_repo.save(stock2)
        
        # Define screen
        screen = Screen(
            name="Coffee Can",
            criteria=[
                Criterion("PE", "<", Decimal("20")),
                Criterion("ROE", ">", Decimal("20")),
            ]
        )
        
        # Execute
        result = screening_service.run_screen(screen)
        
        # Verify
        assert len(result.matched_stocks) == 1
        assert result.matched_stocks[0].ticker.symbol == "INFY"
    
    def test_screening_publishes_event(self, screening_service):
        """Screening service publishes event"""
        # Setup
        events_published = []
        screening_service.event_bus.subscribe(
            ScreeningCompleted,
            lambda e: events_published.append(e)
        )
        
        # Execute
        screening_service.run_screen(screen)
        
        # Verify
        assert len(events_published) == 1
        assert events_published[0].screen_name == "Coffee Can"
```

---

## Section 7: Dependency Injection (Week 4)

Connect everything together without tight coupling.

**File: `stock-screening/container.py`**

```python
"""
Dependency Injection Container
Assembles all services with their dependencies.
"""

from stock_analysis.infrastructure.repositories import (
    PostgresStockRepository,
    PostgresPortfolioRepository
)
from stock_analysis.application.services import (
    ScreeningService,
    PortfolioService
)
from stock_analysis.domain.acl.market_data_acl import MarketDataACL
from market_data_provider import MarketDataService
from shared.event_bus import EventBus

class Container:
    """DI Container - assembles the application"""
    
    def __init__(self, db_connection, market_data_api):
        self.db = db_connection
        self.market_data_api = market_data_api
    
    @property
    def stock_repository(self) -> StockRepository:
        return PostgresStockRepository(self.db)
    
    @property
    def portfolio_repository(self) -> PortfolioRepository:
        return PostgresPortfolioRepository(self.db)
    
    @property
    def event_bus(self) -> EventBus:
        return EventBus()
    
    @property
    def market_data_acl(self) -> MarketDataACL:
        service = MarketDataService(self.market_data_api)
        return MarketDataACL(service)
    
    @property
    def screening_service(self) -> ScreeningService:
        return ScreeningService(
            self.stock_repository,
            self.portfolio_repository,
            self.event_bus
        )
    
    @property
    def portfolio_service(self) -> PortfolioService:
        return PortfolioService(
            self.portfolio_repository,
            self.event_bus
        )

# Usage in main
if __name__ == "__main__":
    container = Container(db_connection, market_data_api)
    
    # Run screening
    result = container.screening_service.run_screen(screen)
    
    # Rebalance portfolio
    container.portfolio_service.rebalance_portfolio("My Portfolio", weights)
```

---

## Section 8: Repository Structure (Week 4)

Final organized structure after refactoring:

```
global-stock-screener/
├── src/
│   ├── stock_analysis/
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── aggregates.py         ← Stock, Portfolio (ARs)
│   │   │   ├── models.py             ← Value Objects
│   │   │   ├── repositories.py       ← Repository interfaces
│   │   │   ├── acl/
│   │   │   │   └── market_data_acl.py ← Anti-Corruption Layer
│   │   │   └── events.py             ← Domain Events
│   │   ├── application/
│   │   │   ├── __init__.py
│   │   │   └── services.py           ← Application Services
│   │   └── infrastructure/
│   │       ├── __init__.py
│   │       └── repositories.py       ← Repository implementations
│   │
│   ├── portfolio_strategy/
│   │   ├── domain/
│   │   │   ├── aggregates.py
│   │   │   ├── models.py
│   │   │   └── repositories.py
│   │   ├── application/
│   │   │   └── services.py
│   │   └── infrastructure/
│   │       └── repositories.py
│   │
│   ├── backtesting/
│   │   └── ...
│   │
│   ├── risk_signals/
│   │   └── ...
│   │
│   ├── reporting/
│   │   └── ...
│   │
│   └── shared/
│       ├── __init__.py
│       ├── event_bus.py
│       └── domain_events.py
│
├── tests/
│   ├── unit/
│   │   ├── stock_analysis/
│   │   │   ├── test_stock_aggregate.py
│   │   │   ├── test_portfolio_aggregate.py
│   │   │   └── test_screening_service.py
│   │   └── ...other contexts...
│   └── integration/
│       ├── test_screening_flow.py
│       └── test_portfolio_rebalance.py
│
├── docs/
│   ├── domain_model.md      ← Ubiquitous Language
│   ├── bounded_contexts.md  ← Context map
│   └── events.md            ← Domain events
│
├── container.py             ← DI Container
├── main.py                  ← Entry point
└── requirements.txt
```

---

## Validation Checklist

After implementing DDD, verify:

```python
# 1. Domain Logic in Aggregates
✅ Stock.matches_screen() - in aggregate, not service
✅ Portfolio.rebalance() - in aggregate, not service

# 2. No Circular Dependencies
✅ Stock Analysis imports Market Data via ACL
❌ NOT Market Data imports Stock Analysis

# 3. Clear Bounded Contexts
✅ Each context in separate package
✅ No imports between domains except through ACL/events

# 4. Application Services are Thin
✅ Services < 20 lines (orchestration only)
✅ Business logic in aggregates

# 5. Repositories Abstract Storage
✅ Can swap Postgres for MySQL without domain changes
✅ Repo interface is domain-focused

# 6. Domain Events Connect Contexts
✅ Stock Analysis publishes ScreeningCompleted
✅ Portfolio subscribes to ScreeningCompleted
✅ Zero direct coupling

# 7. Tests Focus on Domain
✅ Unit tests on aggregates (no DB, no API)
✅ Integration tests on services
❌ NOT testing infrastructure details
```

---

## Timeline Summary

| Week | Phase | Deliverable |
|------|-------|---|
| 1 | Domain Models | Value Objects, Aggregates |
| 2 | Repositories & Services | Repository interfaces, Application Services |
| 3 | Integration | ACL, Domain Events, EventBus |
| 4 | Testing | Unit & Integration Tests |
| 5 | Refactoring | Move existing code to DDD structure |
| 6 | Validation | Ensure all invariants maintained |
| 7 | Documentation | Domain Vision Statement, Context Map |
| 8 | Cleanup | Remove old code, optimize imports |

---

**Reference:** Evans, E. (2003). Domain-Driven Design: Tackling Complexity in the Heart of Software
