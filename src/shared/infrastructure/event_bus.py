"""
Event Bus - Enables communication between bounded contexts
Simple in-process implementation (production would use message broker)
"""

from typing import Callable, List, Type, Dict
from collections import defaultdict
import logging

from src.shared.domain.base import DomainEvent


logger = logging.getLogger(__name__)


class EventBus:
    """
    Simple event bus for inter-context communication.

    For production: Replace with RabbitMQ, Kafka, or similar message broker.
    This implementation is suitable for monolithic or small-scale deployments.
    """

    def __init__(self):
        self.subscribers: Dict[Type[DomainEvent], List[Callable]] = defaultdict(list)
        self.event_history: List[DomainEvent] = []

    def subscribe(self, event_type: Type[DomainEvent], handler: Callable) -> None:
        """
        Subscribe to domain events.

        Usage:
            def on_screening_complete(event: ScreeningCompleted):
                portfolio_service.rebalance(event.stocks_matched)

            event_bus.subscribe(ScreeningCompleted, on_screening_complete)
        """
        self.subscribers[event_type].append(handler)
        logger.info(f"Subscribed {handler.__name__} to {event_type.__name__}")

    def publish(self, event: DomainEvent) -> None:
        """
        Publish a domain event to all subscribers.

        Guarantees:
        - All subscribers are notified synchronously
        - If a handler fails, other handlers still execute
        - Event is recorded in history

        For production with async handlers, use message broker instead.
        """
        event_type = type(event)
        self.event_history.append(event)

        logger.info(f"Publishing {event_type.__name__} (ID: {event.event_id})")

        handlers = self.subscribers.get(event_type, [])

        if not handlers:
            logger.warning(f"No subscribers for {event_type.__name__}")
            return

        for handler in handlers:
            try:
                logger.debug(f"Calling handler: {handler.__name__}")
                handler(event)
            except Exception as e:
                logger.error(
                    f"Error in event handler {handler.__name__}: {str(e)}",
                    exc_info=True
                )
                # Continue with other handlers even if one fails
                # For critical errors, implement dead-letter queue in production

    def get_event_history(self) -> List[DomainEvent]:
        """Get all published events (useful for debugging/testing)"""
        return self.event_history.copy()

    def clear_history(self) -> None:
        """Clear event history (for testing)"""
        self.event_history = []

    def get_subscriber_count(self, event_type: Type[DomainEvent]) -> int:
        """Get number of subscribers for an event type"""
        return len(self.subscribers.get(event_type, []))


# Global event bus instance
# In a real application, this would be injected via dependency container
_event_bus: EventBus = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus() -> None:
    """Reset event bus (for testing)"""
    global _event_bus
    _event_bus = EventBus()
