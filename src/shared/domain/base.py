"""
Shared Domain Base Classes
Foundation for all bounded contexts
"""

from dataclasses import dataclass, field
from datetime import datetime
from abc import ABC, abstractmethod
from typing import Any, List
from uuid import uuid4


class DomainEvent:
    """Base class for all domain events"""

    def __init__(self):
        self.timestamp: datetime = datetime.now()
        self.event_id: str = str(uuid4())


class AggregateRoot(ABC):
    """Base class for aggregate roots"""

    def __init__(self):
        self.id: str = str(uuid4())
        self.created_at: datetime = datetime.now()
        self.updated_at: datetime = datetime.now()
        self.domain_events: List[DomainEvent] = []

    def add_domain_event(self, event: DomainEvent) -> None:
        """Record a domain event"""
        self.domain_events.append(event)

    def clear_domain_events(self) -> List[DomainEvent]:
        """Get and clear all domain events"""
        events = self.domain_events.copy()
        self.domain_events = []
        return events


class ValueObject(ABC):
    """Base class for value objects - immutable by nature"""

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return self.__dict__ == other.__dict__

    def __hash__(self):
        return hash(tuple(sorted(self.__dict__.items())))


class Repository(ABC):
    """Base repository interface"""

    @abstractmethod
    def save(self, aggregate: AggregateRoot) -> None:
        """Persist an aggregate"""
        pass

    @abstractmethod
    def find_by_id(self, aggregate_id: str) -> AggregateRoot:
        """Retrieve an aggregate by ID"""
        pass

    @abstractmethod
    def find_all(self) -> List[AggregateRoot]:
        """Retrieve all aggregates"""
        pass

    @abstractmethod
    def delete(self, aggregate_id: str) -> None:
        """Delete an aggregate"""
        pass


class ApplicationService(ABC):
    """Base application service - orchestrates domain logic"""

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the service operation"""
        pass


class DomainException(Exception):
    """Base domain exception"""
    pass
