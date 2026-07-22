"""
Unit Tests for Market Data Domain Models
Tests pure domain logic in aggregates and value objects
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.market_data.domain.models import Ticker, Price, OHLC, MarketData


class TestTicker:
    """Test Ticker value object"""

    def test_create_valid_ticker(self):
        """Create valid ticker"""
        ticker = Ticker("INFY", "NSE")
        assert ticker.symbol == "INFY"
        assert ticker.exchange == "NSE"

    def test_invalid_ticker_symbol_empty(self):
        """Empty symbol should fail"""
        with pytest.raises(ValueError):
            Ticker("", "NSE")

    def test_invalid_ticker_symbol_too_long(self):
        """Symbol too long should fail"""
        with pytest.raises(ValueError):
            Ticker("VERYLONGSYMBOL", "NSE")

    def test_invalid_exchange(self):
        """Unknown exchange should fail"""
        with pytest.raises(ValueError):
            Ticker("INFY", "UNKNOWN")

    def test_ticker_equality(self):
        """Equal tickers should be equal"""
        t1 = Ticker("INFY", "NSE")
        t2 = Ticker("INFY", "NSE")
        assert t1 == t2

    def test_ticker_string_representation(self):
        """String representation should be correct"""
        ticker = Ticker("INFY", "NSE")
        assert str(ticker) == "INFY.NSE"


class TestPrice:
    """Test Price value object"""

    def test_create_valid_price(self):
        """Create valid price"""
        price = Price(Decimal("2500"), "INR")
        assert price.value == Decimal("2500")
        assert price.currency == "INR"

    def test_negative_price_fails(self):
        """Negative price should fail"""
        with pytest.raises(ValueError):
            Price(Decimal("-100"), "INR")

    def test_zero_price_allowed(self):
        """Zero price should be allowed"""
        price = Price(Decimal("0"), "INR")
        assert price.value == Decimal("0")


class TestOHLC:
    """Test OHLC value object"""

    def test_create_valid_ohlc(self):
        """Create valid OHLC"""
        ohlc = OHLC(
            open_price=Decimal("2500"),
            high_price=Decimal("2550"),
            low_price=Decimal("2450"),
            close_price=Decimal("2520"),
            volume=1000000,
        )
        assert ohlc.open_price == Decimal("2500")
        assert ohlc.volume == 1000000

    def test_open_outside_range_fails(self):
        """Open price outside high-low fails"""
        with pytest.raises(ValueError):
            OHLC(
                open_price=Decimal("2600"),  # Above high
                high_price=Decimal("2550"),
                low_price=Decimal("2450"),
                close_price=Decimal("2520"),
                volume=1000000,
            )

    def test_close_outside_range_fails(self):
        """Close price outside high-low fails"""
        with pytest.raises(ValueError):
            OHLC(
                open_price=Decimal("2500"),
                high_price=Decimal("2550"),
                low_price=Decimal("2450"),
                close_price=Decimal("2400"),  # Below low
                volume=1000000,
            )

    def test_negative_volume_fails(self):
        """Negative volume should fail"""
        with pytest.raises(ValueError):
            OHLC(
                open_price=Decimal("2500"),
                high_price=Decimal("2550"),
                low_price=Decimal("2450"),
                close_price=Decimal("2520"),
                volume=-1000000,
            )


class TestMarketData:
    """Test MarketData aggregate"""

    @pytest.fixture
    def valid_market_data(self):
        """Create valid market data for testing"""
        ticker = Ticker("INFY", "NSE")
        date = datetime.now()
        ohlc = OHLC(
            open_price=Decimal("2500"),
            high_price=Decimal("2550"),
            low_price=Decimal("2450"),
            close_price=Decimal("2520"),
            volume=1000000,
        )
        return MarketData(
            ticker=ticker,
            date=date,
            ohlc=ohlc,
            metrics={"PE": Decimal("15.5"), "ROE": Decimal("25.0")},
        )

    def test_create_valid_market_data(self, valid_market_data):
        """Create valid market data"""
        assert valid_market_data.ticker.symbol == "INFY"
        assert valid_market_data.metrics["PE"] == Decimal("15.5")

    def test_add_metric(self, valid_market_data):
        """Add metric to market data"""
        valid_market_data.add_metric("DebtToEquity", Decimal("0.5"))
        assert valid_market_data.metrics["DebtToEquity"] == Decimal("0.5")

    def test_is_complete_with_required_metrics(self):
        """Market data is complete with required metrics"""
        ticker = Ticker("INFY", "NSE")
        date = datetime.now()
        ohlc = OHLC(
            open_price=Decimal("2500"),
            high_price=Decimal("2550"),
            low_price=Decimal("2450"),
            close_price=Decimal("2520"),
            volume=1000000,
        )
        data = MarketData(
            ticker=ticker,
            date=date,
            ohlc=ohlc,
            metrics={"PE": Decimal("15.5"), "ROE": Decimal("25.0"), "MarketCap": Decimal("1000000")},
        )
        assert data.is_complete() is True

    def test_is_complete_without_required_metrics(self):
        """Market data is incomplete without all required metrics"""
        ticker = Ticker("INFY", "NSE")
        date = datetime.now()
        ohlc = OHLC(
            open_price=Decimal("2500"),
            high_price=Decimal("2550"),
            low_price=Decimal("2450"),
            close_price=Decimal("2520"),
            volume=1000000,
        )
        data = MarketData(
            ticker=ticker,
            date=date,
            ohlc=ohlc,
            metrics={"PE": Decimal("15.5")},  # Missing ROE, MarketCap
        )
        assert data.is_complete() is False

    def test_is_stale_recent_data(self, valid_market_data):
        """Recent data is not stale"""
        assert valid_market_data.is_stale() is False

    def test_string_representation(self, valid_market_data):
        """String representation"""
        expected = f"MarketData(INFY.NSE, {valid_market_data.date.date()})"
        assert str(valid_market_data) == expected
