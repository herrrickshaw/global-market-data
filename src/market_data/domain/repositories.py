"""
Market Data Context - Repository Interfaces
Abstracts persistence details from domain logic
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime

from src.shared.domain.base import Repository
from .models import MarketData, Ticker


class MarketDataRepository(ABC, Repository):
    """
    Repository for MarketData aggregates
    Abstracts how market data is persisted
    """

    @abstractmethod
    def save(self, market_data: MarketData) -> None:
        """Save market data"""
        pass

    @abstractmethod
    def find_by_ticker_and_date(
        self, ticker: Ticker, date: datetime
    ) -> Optional[MarketData]:
        """Retrieve market data for a specific ticker and date"""
        pass

    @abstractmethod
    def find_by_ticker(self, ticker: Ticker, days: int = 252) -> List[MarketData]:
        """Retrieve last N trading days of data for a ticker"""
        pass

    @abstractmethod
    def find_all(self) -> List[MarketData]:
        """Retrieve all market data"""
        pass

    @abstractmethod
    def delete(self, ticker: Ticker, date: datetime) -> None:
        """Delete market data"""
        pass


class MarketDataRepositoryMemory(MarketDataRepository):
    """Simple in-memory implementation for testing"""

    def __init__(self):
        self.data = {}  # key: (ticker_symbol, date) -> MarketData

    def save(self, market_data: MarketData) -> None:
        key = (market_data.ticker.symbol, market_data.date.date())
        self.data[key] = market_data

    def find_by_ticker_and_date(
        self, ticker: Ticker, date: datetime
    ) -> Optional[MarketData]:
        key = (ticker.symbol, date.date())
        return self.data.get(key)

    def find_by_ticker(self, ticker: Ticker, days: int = 252) -> List[MarketData]:
        results = [
            data
            for (symbol, _), data in self.data.items()
            if symbol == ticker.symbol
        ]
        return sorted(results, key=lambda x: x.date, reverse=True)[:days]

    def find_all(self) -> List[MarketData]:
        return list(self.data.values())

    def find_by_id(self, aggregate_id: str):
        """Not implemented for market data (uses date-based key)"""
        raise NotImplementedError("Use find_by_ticker_and_date instead")

    def delete(self, ticker: Ticker, date: datetime) -> None:
        key = (ticker.symbol, date.date())
        if key in self.data:
            del self.data[key]
