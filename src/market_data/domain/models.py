"""
Market Data Context - Domain Models
Represents core concepts in the market data domain
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict

from src.shared.domain.base import ValueObject


@dataclass(frozen=True)
class Ticker(ValueObject):
    """
    Value Object: Stock ticker symbol
    Immutable, identified by symbol and exchange
    """
    symbol: str
    exchange: str  # NSE, BSE, NASDAQ, NYSE

    def __post_init__(self):
        if not self.symbol or len(self.symbol) > 10:
            raise ValueError("Invalid ticker symbol")
        if self.exchange not in ["NSE", "BSE", "NASDAQ", "NYSE", "EURONEXT", "TSE"]:
            raise ValueError(f"Unknown exchange: {self.exchange}")

    def __str__(self) -> str:
        return f"{self.symbol}.{self.exchange}"


@dataclass(frozen=True)
class Price(ValueObject):
    """
    Value Object: A price with currency
    Immutable, always positive
    """
    value: Decimal
    currency: str = "INR"

    def __post_init__(self):
        if self.value < 0:
            raise ValueError("Price cannot be negative")

    def __str__(self) -> str:
        return f"{self.value} {self.currency}"


@dataclass(frozen=True)
class OHLC(ValueObject):
    """
    Value Object: Open, High, Low, Close prices for a day
    """
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int

    def __post_init__(self):
        if not (self.low_price <= self.open_price <= self.high_price):
            raise ValueError("Open price not within high-low range")
        if not (self.low_price <= self.close_price <= self.high_price):
            raise ValueError("Close price not within high-low range")
        if self.volume < 0:
            raise ValueError("Volume cannot be negative")


@dataclass(frozen=True)
class Metric(ValueObject):
    """
    Value Object: A financial metric with value and timestamp
    """
    name: str  # PE, ROE, DebtToEquity, etc.
    value: Decimal
    as_of_date: datetime

    def __post_init__(self):
        if not self.name:
            raise ValueError("Metric name required")


class MarketData:
    """
    AGGREGATE ROOT: Complete market data for a stock on a date

    Responsible for:
    - Storing OHLC data
    - Storing calculated metrics
    - Validating data integrity
    """

    def __init__(
        self,
        ticker: Ticker,
        date: datetime,
        ohlc: OHLC,
        metrics: Dict[str, Decimal] = None,
    ):
        self.ticker = ticker
        self.date = date
        self.ohlc = ohlc
        self.metrics = metrics or {}
        self.fetched_at = datetime.now()
        self._validate()

    def _validate(self) -> None:
        """Validate market data integrity"""
        if self.date > self.fetched_at:
            raise ValueError("Data date cannot be in the future")

    def add_metric(self, name: str, value: Decimal) -> None:
        """Add a financial metric"""
        self.metrics[name] = value

    def is_complete(self) -> bool:
        """Check if market data has essential metrics"""
        required_metrics = {"PE", "ROE", "MarketCap"}
        return all(m in self.metrics for m in required_metrics)

    def is_stale(self, days_threshold: int = 365) -> bool:
        """Check if data is older than threshold"""
        age_days = (datetime.now() - self.date).days
        return age_days > days_threshold

    def __str__(self) -> str:
        return f"MarketData({self.ticker}, {self.date.date()})"


class MarketCalendar:
    """
    VALUE OBJECT: Trading calendar for an exchange
    Defines trading days, holidays, market hours
    """

    def __init__(self, exchange: str, trading_days: set, holidays: set):
        self.exchange = exchange
        self.trading_days = trading_days  # Set of weekdays (0=Monday, 4=Friday)
        self.holidays = holidays  # Set of dates that are holidays

    def is_trading_day(self, date: datetime) -> bool:
        """Check if a date is a trading day"""
        if date.date() in self.holidays:
            return False
        return date.weekday() in self.trading_days

    def next_trading_day(self, date: datetime) -> datetime:
        """Find next trading day"""
        from datetime import timedelta
        current = date + timedelta(days=1)
        while not self.is_trading_day(current):
            current += timedelta(days=1)
        return current

    def previous_trading_day(self, date: datetime) -> datetime:
        """Find previous trading day"""
        from datetime import timedelta
        current = date - timedelta(days=1)
        while not self.is_trading_day(current):
            current -= timedelta(days=1)
        return current
