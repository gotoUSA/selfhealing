"""
Repository Interfaces for Self-Healing System

Abstract interfaces that define the contract for data access.
These interfaces allow the self-healing core to be decoupled from
specific ORM implementations (Django, SQLAlchemy, etc.)

Design Principles:
1. Pure Python - no framework dependencies
2. Data classes for transfer objects
3. ABC for repository contracts
4. Optional fields use None, not Django's blank=True

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


# ============================================================================
# Enums (Framework-independent)
# ============================================================================


class FailedOperationDomain(str, Enum):
    """Domain classification for failed operations"""

    PAYMENT = "payment"
    POINT = "point"
    INVENTORY = "inventory"
    WEBHOOK = "webhook"
    NOTIFICATION = "notification"


class FailedOperationStatus(str, Enum):
    """State machine for DLQ item lifecycle"""

    PENDING = "pending"
    REVIEWING = "reviewing"
    REPLAYED = "replayed"
    REQUIRES_REVIEW = "requires_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    EXPIRED = "expired"


class CircuitBreakerStateEnum(str, Enum):
    """Circuit breaker states"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class SecurityIncidentType(str, Enum):
    """Types of security incidents"""

    WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"
    PAYMENT_AMOUNT_TAMPERED = "payment_amount_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"


class SecuritySeverity(str, Enum):
    """Severity levels for security incidents"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class SecurityIncidentStatus(str, Enum):
    """Investigation status for security incidents"""

    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


# ============================================================================
# Data Transfer Objects (DTOs)
# ============================================================================


@dataclass
class FailedOperationData:
    """
    Data transfer object for FailedOperation model.

    Contains all necessary fields for DLQ operations without
    Django model dependencies.
    """

    # Identity
    id: int

    # Domain & Classification
    domain: str
    failure_type: str
    status: str

    # References (IDs only - no model instances)
    order_id: Optional[int] = None
    payment_id: Optional[int] = None
    user_id: Optional[int] = None

    # Snapshot Data
    snapshot_data: dict[str, Any] = field(default_factory=dict)

    # Error Information
    error_code: str = ""
    error_message: str = ""

    # Retry Tracking
    retry_count: int = 0
    max_retries: int = 2
    last_retry_at: Optional[datetime] = None

    # Forensic Context
    request_data: dict[str, Any] = field(default_factory=dict)
    response_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Resolution
    resolved_at: Optional[datetime] = None
    resolved_by_id: Optional[int] = None
    resolution_type: str = ""
    resolution_note: str = ""

    # Recovery Hints
    next_action_hint: str = ""
    recommended_action: str = ""

    # Lifecycle
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    @property
    def is_pending(self) -> bool:
        """Check if operation is pending review"""
        return self.status == FailedOperationStatus.PENDING.value

    @property
    def is_resolved(self) -> bool:
        """Check if operation is resolved"""
        return self.status == FailedOperationStatus.RESOLVED.value

    @property
    def can_retry(self) -> bool:
        """Check if operation can be retried"""
        return self.retry_count < self.max_retries


@dataclass
class CircuitBreakerStateData:
    """
    Data transfer object for CircuitBreakerState model.

    Represents the current state of a circuit breaker for a service.
    """

    # Identity
    service_name: str
    id: Optional[int] = None

    # State
    state: str = CircuitBreakerStateEnum.CLOSED.value
    failure_count: int = 0
    success_count: int = 0

    # Timing
    last_failure_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None

    # Manual Control
    manually_controlled: bool = False
    controlled_by_id: Optional[int] = None
    control_reason: str = ""
    manual_override_expires_at: Optional[datetime] = None

    # Half-Open Tracking
    half_open_request_count: int = 0

    # Lifecycle
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)"""
        return self.state == CircuitBreakerStateEnum.OPEN.value

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (allowing requests)"""
        return self.state == CircuitBreakerStateEnum.CLOSED.value

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing)"""
        return self.state == CircuitBreakerStateEnum.HALF_OPEN.value


@dataclass
class SecurityIncidentData:
    """
    Data transfer object for SecurityIncident model.

    Security incidents are NEVER auto-healed and require human intervention.
    """

    # Identity
    id: int

    # Classification
    incident_type: str
    severity: str
    status: str

    # Source Information
    source_ip: Optional[str] = None
    user_agent: str = ""
    user_id: Optional[int] = None

    # References
    order_id: Optional[int] = None
    payment_id: Optional[int] = None

    # Details
    description: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)

    # Investigation
    assigned_to_id: Optional[int] = None
    investigation_notes: str = ""
    resolved_at: Optional[datetime] = None

    # Lifecycle
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_critical(self) -> bool:
        """Check if incident is critical severity"""
        return self.severity == SecuritySeverity.CRITICAL.value

    @property
    def needs_investigation(self) -> bool:
        """Check if incident needs investigation"""
        return self.status in [
            SecurityIncidentStatus.OPEN.value,
            SecurityIncidentStatus.INVESTIGATING.value,
        ]


# ============================================================================
# Repository Interfaces
# ============================================================================


class FailedOperationRepository(ABC):
    """
    Abstract repository for FailedOperation (DLQ) data access.

    Implementations:
    - DjangoFailedOperationRepository: Uses Django ORM
    - (Future) SQLAlchemyFailedOperationRepository: Uses SQLAlchemy
    """

    @abstractmethod
    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        user_id: Optional[int] = None,
        snapshot_data: Optional[dict[str, Any]] = None,
        request_data: Optional[dict[str, Any]] = None,
        response_data: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
    ) -> FailedOperationData:
        """Create a new failed operation record"""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by ID"""
        ...

    @abstractmethod
    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending operations for a specific domain"""
        ...

    @abstractmethod
    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain"""
        ...

    @abstractmethod
    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Update the status of a failed operation"""
        ...

    @abstractmethod
    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at"""
        ...

    @abstractmethod
    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Mark a failed operation as resolved"""
        ...

    @abstractmethod
    def get_expired_operations(
        self,
        before_date: datetime,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get operations that have expired"""
        ...

    @abstractmethod
    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations"""
        ...

    @abstractmethod
    def find_by_status(
        self,
        status: str,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations by status with optional filters"""
        ...

    @abstractmethod
    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations that can be replayed (pending and retry_count < max_retries)"""
        ...

    @abstractmethod
    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: dict[str, "timedelta"],
    ) -> list[FailedOperationData]:
        """Find operations that have breached their SLA"""
        ...

    @abstractmethod
    def find_expired(
        self,
        current_time: datetime,
    ) -> list[FailedOperationData]:
        """Find operations past their retention period"""
        ...

    @abstractmethod
    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about failed operations"""
        ...

    # =========================================================================
    # Atomic Operations for Concurrency Safety
    # =========================================================================

    @abstractmethod
    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """
        Atomically acquire a DLQ entry for replay.

        This method MUST:
        1. Check if status is PENDING and retry_count < max_retries
        2. If eligible, atomically set status to REPLAYING and increment retry_count
        3. Return the FailedOperationData if acquired, None if not eligible

        Implementation should use row-level locking (SELECT FOR UPDATE) or
        optimistic locking (version/updated_at check) to prevent race conditions.

        Args:
            id: The DLQ entry ID to acquire
            max_retries: Maximum allowed retry attempts

        Returns:
            FailedOperationData if successfully acquired, None otherwise

        Example Django implementation:
            with transaction.atomic():
                entry = FailedOperation.objects.select_for_update().get(id=id)
                if entry.status != 'pending' or entry.retry_count >= max_retries:
                    return None
                entry.status = 'replaying'
                entry.retry_count += 1
                entry.last_retry_at = now()
                entry.save()
                return FailedOperationData.from_model(entry)
        """
        ...

    @abstractmethod
    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Complete a replay operation by updating the final status.

        Should be called after replay execution to set final state:
        - success=True: Mark as RESOLVED with resolution details
        - success=False: Revert to PENDING (for retry) or REQUIRES_REVIEW (if escalated)

        This method is safe to call without transaction wrapper as it only
        updates an already-acquired entry.

        Args:
            id: The DLQ entry ID
            success: Whether the replay succeeded
            resolution_type: Type of resolution (for successful replays)
            note: Resolution note or error message
            resolved_by_id: User ID who resolved (None for system)
            error_details: Additional error context (for failed replays)

        Returns:
            True if update succeeded, False otherwise
        """
        ...

    @abstractmethod
    def release_stale_replaying(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """
        Release DLQ entries stuck in REPLAYING state.

        Entries can get stuck if the replay process crashes after acquiring
        but before completing. This method reverts them to PENDING for retry.

        Args:
            older_than_minutes: Consider entries older than this as stale

        Returns:
            Number of entries released
        """
        ...


class CircuitBreakerStateRepository(ABC):
    """
    Abstract repository for CircuitBreakerState data access.

    Manages circuit breaker state persistence and retrieval.
    """

    @abstractmethod
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """Get existing state or create new one for a service"""
        ...

    @abstractmethod
    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name"""
        ...

    @abstractmethod
    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """Update circuit breaker state"""
        ...

    @abstractmethod
    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state"""
        ...

    @abstractmethod
    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Record a success and return updated state"""
        ...

    @abstractmethod
    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: Optional[int] = None,
        reason: str = "",
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Set manual control on a circuit breaker"""
        ...

    @abstractmethod
    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """Clear manual control from a circuit breaker
        
        Args:
            service_name: Name of the service
            preserve_reason: If True, keep the existing control_reason value
        """
        ...

    @abstractmethod
    def get_all(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states"""
        ...

    @abstractmethod
    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states"""
        ...

    @abstractmethod
    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state"""
        ...

    # =========================================================================
    # Atomic Operations for Concurrency Safety
    # =========================================================================

    @abstractmethod
    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """
        Atomically force open a circuit breaker.

        This method MUST use row-level locking to prevent concurrent modifications.
        Creates the circuit breaker if it doesn't exist.

        Args:
            service_name: Name of the service
            reason: Reason for opening
            controlled_by_id: User ID who initiated the change
            ttl_minutes: TTL for manual override

        Returns:
            Tuple of (success, previous_state, new_state)

        Example Django implementation:
            with transaction.atomic():
                state, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                    service_name=service_name
                )
                previous = state.state
                state.state = 'open'
                state.manually_controlled = True
                state.save()
                return (True, previous, 'open')
        """
        ...

    @abstractmethod
    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """
        Atomically force close a circuit breaker.

        This method MUST use row-level locking to prevent concurrent modifications.

        Args:
            service_name: Name of the service
            reason: Reason for closing
            controlled_by_id: User ID who initiated the change

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        ...

    @abstractmethod
    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """
        Atomically reset a circuit breaker to initial state.

        This method MUST use row-level locking to prevent concurrent modifications.
        Resets all counters and clears manual control.

        Args:
            service_name: Name of the service
            reason: Reason for reset
            controlled_by_id: User ID who initiated the change

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        ...


class SecurityIncidentRepository(ABC):
    """
    Abstract repository for SecurityIncident data access.

    Security incidents are stored separately and NEVER auto-replayed.
    """

    @abstractmethod
    def create(
        self,
        incident_type: str,
        severity: str,
        description: str = "",
        source_ip: Optional[str] = None,
        user_agent: str = "",
        user_id: Optional[int] = None,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        raw_payload: Optional[dict[str, Any]] = None,
    ) -> SecurityIncidentData:
        """Create a new security incident"""
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[SecurityIncidentData]:
        """Get a security incident by ID"""
        ...

    @abstractmethod
    def get_open_incidents(
        self,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get all open (unresolved) incidents"""
        ...

    @abstractmethod
    def get_by_type(
        self,
        incident_type: str,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get incidents by type"""
        ...

    @abstractmethod
    def get_by_severity(
        self,
        severity: str,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get incidents by severity"""
        ...

    @abstractmethod
    def update_status(
        self,
        id: int,
        status: str,
        investigation_notes: str = "",
        assigned_to_id: Optional[int] = None,
    ) -> bool:
        """Update incident status"""
        ...

    @abstractmethod
    def mark_as_resolved(
        self,
        id: int,
        investigation_notes: str = "",
    ) -> bool:
        """Mark incident as resolved"""
        ...

    @abstractmethod
    def get_recent_by_ip(
        self,
        source_ip: str,
        hours: int = 24,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get recent incidents from a specific IP"""
        ...

    @abstractmethod
    def count_by_type_since(
        self,
        incident_type: str,
        since: datetime,
    ) -> int:
        """Count incidents of a type since a given time"""
        ...
