"""
Repository interfaces for the self-healing system.

These abstract base classes define the contract for data access,
allowing different implementations (Django ORM, SQLAlchemy, in-memory, etc.)
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any

from selfhealing.core.types import (
    FailedOperationData,
    CircuitBreakerStateData,
    SecurityIncidentData,
    OperationStatus,
    CircuitState,
)


class FailedOperationRepository(ABC):
    """
    Repository interface for failed operations (DLQ).

    Implementations should handle persistence of failed operations
    for later retry or manual review.
    """

    @abstractmethod
    def create(
        self,
        domain: str,
        failure_type: str,
        context: Dict[str, Any],
        error_message: str,
        max_retries: int = 3,
    ) -> FailedOperationData:
        """
        Create a new failed operation record.

        Args:
            domain: The business domain (e.g., 'payment', 'order')
            failure_type: Type of failure
            context: Operation context for replay
            error_message: The error message
            max_retries: Maximum retry attempts

        Returns:
            The created FailedOperationData
        """
        pass

    @abstractmethod
    def get_by_id(self, operation_id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by its ID."""
        pass

    @abstractmethod
    def get_pending(
        self,
        domain: Optional[str] = None,
        limit: int = 10,
    ) -> List[FailedOperationData]:
        """
        Get pending operations ready for retry.

        Args:
            domain: Optional domain filter
            limit: Maximum number of operations to return

        Returns:
            List of pending operations
        """
        pass

    @abstractmethod
    def get_by_status(
        self,
        status: OperationStatus,
        domain: Optional[str] = None,
    ) -> List[FailedOperationData]:
        """Get operations by status."""
        pass

    @abstractmethod
    def update_status(
        self,
        operation_id: int,
        status: OperationStatus,
        error_message: Optional[str] = None,
    ) -> Optional[FailedOperationData]:
        """Update the status of an operation."""
        pass

    @abstractmethod
    def increment_retry(
        self,
        operation_id: int,
        error_message: str,
        next_retry_at: Optional[datetime] = None,
    ) -> Optional[FailedOperationData]:
        """Increment retry count and update error message."""
        pass

    @abstractmethod
    def mark_completed(
        self,
        operation_id: int,
    ) -> Optional[FailedOperationData]:
        """Mark an operation as successfully completed."""
        pass

    @abstractmethod
    def mark_failed(
        self,
        operation_id: int,
        error_message: str,
    ) -> Optional[FailedOperationData]:
        """Mark an operation as permanently failed."""
        pass

    @abstractmethod
    def count_by_status(
        self,
        status: Optional[OperationStatus] = None,
        domain: Optional[str] = None,
    ) -> int:
        """Count operations by status and optionally domain."""
        pass

    @abstractmethod
    def delete_expired(
        self,
        older_than: datetime,
    ) -> int:
        """Delete expired operations. Returns count of deleted."""
        pass


class CircuitBreakerStateRepository(ABC):
    """
    Repository interface for circuit breaker state.

    Implementations should handle persistence of circuit breaker state,
    allowing state sharing across processes/instances.
    """

    @abstractmethod
    def get_state(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Get the current state of a circuit breaker."""
        pass

    @abstractmethod
    def get_or_create(
        self,
        service_name: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> CircuitBreakerStateData:
        """Get existing state or create a new one with defaults."""
        pass

    @abstractmethod
    def update_state(
        self,
        service_name: str,
        state: CircuitState,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
    ) -> Optional[CircuitBreakerStateData]:
        """Update the state of a circuit breaker."""
        pass

    @abstractmethod
    def record_failure(
        self,
        service_name: str,
    ) -> CircuitBreakerStateData:
        """Record a failure and update failure count."""
        pass

    @abstractmethod
    def record_success(
        self,
        service_name: str,
    ) -> CircuitBreakerStateData:
        """Record a success and update success count."""
        pass

    @abstractmethod
    def reset(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Reset a circuit breaker to closed state."""
        pass

    @abstractmethod
    def open_circuit(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Open a circuit breaker."""
        pass

    @abstractmethod
    def half_open_circuit(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Transition a circuit breaker to half-open state."""
        pass

    @abstractmethod
    def list_all(self) -> List[CircuitBreakerStateData]:
        """List all circuit breaker states."""
        pass

    @abstractmethod
    def list_open(self) -> List[CircuitBreakerStateData]:
        """List all open circuit breakers."""
        pass


class SecurityIncidentRepository(ABC):
    """
    Repository interface for security incidents.

    Implementations should handle persistence of security-related
    incidents for monitoring and response.
    """

    @abstractmethod
    def create(
        self,
        incident_type: str,
        severity: str,
        description: str,
        context: Optional[Dict[str, Any]] = None,
        source_ip: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> SecurityIncidentData:
        """Create a new security incident record."""
        pass

    @abstractmethod
    def get_by_id(
        self,
        incident_id: int,
    ) -> Optional[SecurityIncidentData]:
        """Get an incident by ID."""
        pass

    @abstractmethod
    def get_recent(
        self,
        hours: int = 24,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get recent incidents."""
        pass

    @abstractmethod
    def get_unresolved(
        self,
        severity: Optional[str] = None,
    ) -> List[SecurityIncidentData]:
        """Get unresolved incidents."""
        pass

    @abstractmethod
    def resolve(
        self,
        incident_id: int,
    ) -> Optional[SecurityIncidentData]:
        """Mark an incident as resolved."""
        pass

    @abstractmethod
    def count_by_type(
        self,
        hours: int = 24,
    ) -> Dict[str, int]:
        """Count incidents by type in the given time window."""
        pass

    @abstractmethod
    def count_by_source_ip(
        self,
        source_ip: str,
        hours: int = 1,
    ) -> int:
        """Count incidents from a specific IP in the given time window."""
        pass
