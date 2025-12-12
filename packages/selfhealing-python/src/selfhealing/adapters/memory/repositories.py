"""
In-Memory Repository Implementations

Provides in-memory implementations of repository interfaces for:
- Unit testing without database dependencies
- Standalone usage (no framework)
- Development and prototyping

These implementations are thread-safe and suitable for single-process use.
For multi-process scenarios, use database-backed implementations.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    FailedOperationData,
    FailedOperationStatus,
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
    SecurityIncidentRepository,
    SecurityIncidentData,
    SecurityIncidentStatus,
)


def _now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


class InMemoryFailedOperationRepository(FailedOperationRepository):
    """
    In-memory implementation of FailedOperationRepository.

    Thread-safe storage for DLQ entries in memory.
    Data is lost when the process exits.
    """

    def __init__(self):
        self._storage: Dict[int, FailedOperationData] = {}
        self._next_id = 1
        self._lock = threading.Lock()

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
        """Create a new failed operation record."""
        with self._lock:
            entry = FailedOperationData(
                id=self._next_id,
                domain=domain,
                failure_type=failure_type,
                status=FailedOperationStatus.PENDING.value,
                order_id=order_id,
                payment_id=payment_id,
                user_id=user_id,
                snapshot_data=snapshot_data or {},
                error_code=error_code,
                error_message=error_message,
                retry_count=retry_count,
                max_retries=max_retries,
                request_data=request_data or {},
                response_data=response_data or {},
                metadata=metadata or {},
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
                created_at=_now(),
                updated_at=_now(),
            )
            self._storage[self._next_id] = entry
            self._next_id += 1
            return entry

    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by ID."""
        with self._lock:
            return self._storage.get(id)

    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending operations for a specific domain."""
        with self._lock:
            results = [
                entry
                for entry in self._storage.values()
                if entry.domain == domain and entry.status == FailedOperationStatus.PENDING.value
            ]
            return results[:limit]

    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain."""
        with self._lock:
            return len(
                [
                    entry
                    for entry in self._storage.values()
                    if entry.domain == domain and entry.status == FailedOperationStatus.PENDING.value
                ]
            )

    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Update the status of a failed operation."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            # Create a new entry with updated fields
            updated = FailedOperationData(
                id=entry.id,
                domain=entry.domain,
                failure_type=entry.failure_type,
                status=status,
                order_id=entry.order_id,
                payment_id=entry.payment_id,
                user_id=entry.user_id,
                snapshot_data=entry.snapshot_data,
                error_code=entry.error_code,
                error_message=entry.error_message,
                retry_count=entry.retry_count,
                max_retries=entry.max_retries,
                last_retry_at=entry.last_retry_at,
                request_data=entry.request_data,
                response_data=entry.response_data,
                metadata=entry.metadata,
                resolved_at=_now() if status == FailedOperationStatus.RESOLVED.value else entry.resolved_at,
                resolved_by_id=resolved_by_id or entry.resolved_by_id,
                resolution_type=resolution_type or entry.resolution_type,
                resolution_note=resolution_note or entry.resolution_note,
                next_action_hint=entry.next_action_hint,
                recommended_action=entry.recommended_action,
                created_at=entry.created_at,
                updated_at=_now(),
                expires_at=entry.expires_at,
            )
            self._storage[id] = updated
            return True

    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            updated = FailedOperationData(
                id=entry.id,
                domain=entry.domain,
                failure_type=entry.failure_type,
                status=entry.status,
                order_id=entry.order_id,
                payment_id=entry.payment_id,
                user_id=entry.user_id,
                snapshot_data=entry.snapshot_data,
                error_code=entry.error_code,
                error_message=entry.error_message,
                retry_count=entry.retry_count + 1,
                max_retries=entry.max_retries,
                last_retry_at=_now(),
                request_data=entry.request_data,
                response_data=entry.response_data,
                metadata=entry.metadata,
                resolved_at=entry.resolved_at,
                resolved_by_id=entry.resolved_by_id,
                resolution_type=entry.resolution_type,
                resolution_note=entry.resolution_note,
                next_action_hint=entry.next_action_hint,
                recommended_action=entry.recommended_action,
                created_at=entry.created_at,
                updated_at=_now(),
                expires_at=entry.expires_at,
            )
            self._storage[id] = updated
            return True

    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Mark a failed operation as resolved."""
        return self.update_status(
            id=id,
            status=FailedOperationStatus.RESOLVED.value,
            resolution_type=resolution_type,
            resolution_note=resolution_note,
            resolved_by_id=resolved_by_id,
        )

    def get_expired_operations(
        self,
        before_date: datetime,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get operations that have expired."""
        with self._lock:
            results = [entry for entry in self._storage.values() if entry.expires_at and entry.expires_at < before_date]
            return results[:limit]

    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations."""
        count = 0
        for id in ids:
            if self.update_status(id, status):
                count += 1
        return count

    def find_by_status(
        self,
        status: str,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations by status with optional filters."""
        with self._lock:
            results = []
            for entry in self._storage.values():
                if entry.status != status:
                    continue
                if domain and entry.domain != domain:
                    continue
                if failure_type and entry.failure_type != failure_type:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations that can be replayed."""
        with self._lock:
            results = []
            for entry in self._storage.values():
                if entry.status != FailedOperationStatus.PENDING.value:
                    continue
                if entry.retry_count >= max_retries:
                    continue
                if domain and entry.domain != domain:
                    continue
                if failure_type and entry.failure_type != failure_type:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: dict[str, timedelta],
    ) -> list[FailedOperationData]:
        """Find operations that have breached their SLA."""
        with self._lock:
            results = []
            for entry in self._storage.values():
                if entry.status != FailedOperationStatus.PENDING.value:
                    continue
                threshold = sla_thresholds.get(entry.domain, timedelta(hours=24))
                if entry.created_at and current_time - entry.created_at > threshold:
                    results.append(entry)
            return results

    def find_expired(
        self,
        current_time: datetime,
    ) -> list[FailedOperationData]:
        """Find operations past their retention period."""
        with self._lock:
            return [entry for entry in self._storage.values() if entry.expires_at and entry.expires_at < current_time]

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about failed operations."""
        with self._lock:
            stats = {
                "total": len(self._storage),
                "by_status": {},
                "by_domain": {},
            }
            for entry in self._storage.values():
                stats["by_status"][entry.status] = stats["by_status"].get(entry.status, 0) + 1
                stats["by_domain"][entry.domain] = stats["by_domain"].get(entry.domain, 0) + 1
            return stats

    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """Atomically acquire a DLQ entry for replay."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return None
            if entry.status != FailedOperationStatus.PENDING.value:
                return None
            if entry.retry_count >= max_retries:
                return None

            # Atomically update
            updated = FailedOperationData(
                id=entry.id,
                domain=entry.domain,
                failure_type=entry.failure_type,
                status="replaying",
                order_id=entry.order_id,
                payment_id=entry.payment_id,
                user_id=entry.user_id,
                snapshot_data=entry.snapshot_data,
                error_code=entry.error_code,
                error_message=entry.error_message,
                retry_count=entry.retry_count + 1,
                max_retries=entry.max_retries,
                last_retry_at=_now(),
                request_data=entry.request_data,
                response_data=entry.response_data,
                metadata=entry.metadata,
                resolved_at=entry.resolved_at,
                resolved_by_id=entry.resolved_by_id,
                resolution_type=entry.resolution_type,
                resolution_note=entry.resolution_note,
                next_action_hint=entry.next_action_hint,
                recommended_action=entry.recommended_action,
                created_at=entry.created_at,
                updated_at=_now(),
                expires_at=entry.expires_at,
            )
            self._storage[id] = updated
            return updated

    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Complete a replay operation by updating the final status."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            if success:
                new_status = FailedOperationStatus.RESOLVED.value
                resolved_at = _now()
            else:
                # Revert to pending for retry
                new_status = FailedOperationStatus.PENDING.value
                resolved_at = entry.resolved_at

            updated = FailedOperationData(
                id=entry.id,
                domain=entry.domain,
                failure_type=entry.failure_type,
                status=new_status,
                order_id=entry.order_id,
                payment_id=entry.payment_id,
                user_id=entry.user_id,
                snapshot_data=entry.snapshot_data,
                error_code=entry.error_code,
                error_message=note or entry.error_message,
                retry_count=entry.retry_count,
                max_retries=entry.max_retries,
                last_retry_at=entry.last_retry_at,
                request_data=entry.request_data,
                response_data=entry.response_data,
                metadata={**(entry.metadata or {}), **(error_details or {})},
                resolved_at=resolved_at,
                resolved_by_id=resolved_by_id or entry.resolved_by_id,
                resolution_type=resolution_type or entry.resolution_type,
                resolution_note=note or entry.resolution_note,
                next_action_hint=entry.next_action_hint,
                recommended_action=entry.recommended_action,
                created_at=entry.created_at,
                updated_at=_now(),
                expires_at=entry.expires_at,
            )
            self._storage[id] = updated
            return True

    def release_stale_replaying(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """Release DLQ entries stuck in REPLAYING state."""
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        released = 0

        with self._lock:
            for id, entry in list(self._storage.items()):
                if entry.status == "replaying" and entry.last_retry_at and entry.last_retry_at < cutoff:
                    updated = FailedOperationData(
                        id=entry.id,
                        domain=entry.domain,
                        failure_type=entry.failure_type,
                        status=FailedOperationStatus.PENDING.value,
                        order_id=entry.order_id,
                        payment_id=entry.payment_id,
                        user_id=entry.user_id,
                        snapshot_data=entry.snapshot_data,
                        error_code=entry.error_code,
                        error_message=entry.error_message,
                        retry_count=entry.retry_count,
                        max_retries=entry.max_retries,
                        last_retry_at=entry.last_retry_at,
                        request_data=entry.request_data,
                        response_data=entry.response_data,
                        metadata=entry.metadata,
                        resolved_at=entry.resolved_at,
                        resolved_by_id=entry.resolved_by_id,
                        resolution_type=entry.resolution_type,
                        resolution_note=entry.resolution_note,
                        next_action_hint=entry.next_action_hint,
                        recommended_action=entry.recommended_action,
                        created_at=entry.created_at,
                        updated_at=_now(),
                        expires_at=entry.expires_at,
                    )
                    self._storage[id] = updated
                    released += 1

        return released

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._storage.clear()
            self._next_id = 1


class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    In-memory implementation of CircuitBreakerStateRepository.

    Thread-safe storage for circuit breaker states in memory.
    """

    def __init__(self):
        self._storage: Dict[str, CircuitBreakerStateData] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name."""
        with self._lock:
            return self._storage.get(service_name)

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """Get or create a circuit breaker state."""
        with self._lock:
            if service_name not in self._storage:
                state = CircuitBreakerStateData(
                    id=self._next_id,
                    service_name=service_name,
                    state=CircuitBreakerStateEnum.CLOSED.value,
                    created_at=_now(),
                    updated_at=_now(),
                )
                self._storage[service_name] = state
                self._next_id += 1
            return self._storage[service_name]

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """Update circuit breaker state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=state,
                failure_count=failure_count if failure_count is not None else entry.failure_count,
                success_count=success_count if success_count is not None else entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=opened_at if opened_at is not None else entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def increment_failure_count(
        self,
        service_name: str,
        last_failure_at: Optional[datetime] = None,
    ) -> int:
        """Increment failure count."""
        with self._lock:
            entry = self.get_or_create(service_name)
            new_count = entry.failure_count + 1

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=new_count,
                success_count=entry.success_count,
                last_failure_at=last_failure_at or _now(),
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return new_count

    def reset_counts(self, service_name: str) -> bool:
        """Reset failure and success counts."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=0,
                success_count=0,
                last_failure_at=entry.last_failure_at,
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: int,
        reason: str = "",
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Set manual control override."""
        with self._lock:
            entry = self.get_or_create(service_name)

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=state,
                failure_count=entry.failure_count,
                success_count=entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=_now() if state == CircuitBreakerStateEnum.OPEN.value else entry.opened_at,
                manually_controlled=True,
                controlled_by_id=controlled_by_id,
                control_reason=reason,
                manual_override_expires_at=expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """Clear manual control override."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=entry.last_failure_at,
                opened_at=None,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason=entry.control_reason if preserve_reason else "",
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state."""
        with self._lock:
            entry = self.get_or_create(service_name)
            new_count = entry.failure_count + 1

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=new_count,
                success_count=entry.success_count,
                last_failure_at=_now(),
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return updated

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Record a success and return updated state."""
        with self._lock:
            entry = self.get_or_create(service_name)
            new_count = entry.success_count + 1

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=entry.failure_count,
                success_count=new_count,
                last_failure_at=entry.last_failure_at,
                opened_at=entry.opened_at,
                manually_controlled=entry.manually_controlled,
                controlled_by_id=entry.controlled_by_id,
                control_reason=entry.control_reason,
                manual_override_expires_at=entry.manual_override_expires_at,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return updated

    def get_all_states(self) -> List[CircuitBreakerStateData]:
        """Get all circuit breaker states (alias for get_all)."""
        return self.get_all()

    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=None,
                opened_at=None,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason="",
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """Atomically force open a circuit breaker."""
        with self._lock:
            entry = self.get_or_create(service_name)
            previous_state = entry.state

            expires_at = _now() + timedelta(minutes=ttl_minutes) if ttl_minutes > 0 else None

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.OPEN.value,
                failure_count=entry.failure_count,
                success_count=entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=_now(),
                manually_controlled=True,
                controlled_by_id=controlled_by_id,
                control_reason=reason,
                manual_override_expires_at=expires_at,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return (True, previous_state, CircuitBreakerStateEnum.OPEN.value)

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically force close a circuit breaker."""
        with self._lock:
            entry = self.get_or_create(service_name)
            previous_state = entry.state

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=entry.last_failure_at,
                opened_at=None,
                manually_controlled=True,
                controlled_by_id=controlled_by_id,
                control_reason=reason,
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically reset a circuit breaker to initial state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return (False, "", "")

            previous_state = entry.state

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                last_failure_at=None,
                opened_at=None,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason=reason,
                manual_override_expires_at=None,
                half_open_request_count=0,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

    def get_all_open(self) -> List[CircuitBreakerStateData]:
        """Get all open circuit breakers."""
        with self._lock:
            return [entry for entry in self._storage.values() if entry.state == CircuitBreakerStateEnum.OPEN.value]

    def get_all(self) -> List[CircuitBreakerStateData]:
        """Get all circuit breaker states."""
        with self._lock:
            return list(self._storage.values())

    def delete(self, service_name: str) -> bool:
        """Delete a circuit breaker state."""
        with self._lock:
            if service_name in self._storage:
                del self._storage[service_name]
                return True
            return False

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._storage.clear()
            self._next_id = 1


class InMemorySecurityIncidentRepository(SecurityIncidentRepository):
    """
    In-memory implementation of SecurityIncidentRepository.

    Thread-safe storage for security incidents in memory.
    """

    def __init__(self):
        self._storage: Dict[int, SecurityIncidentData] = {}
        self._next_id = 1
        self._lock = threading.Lock()

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
        """Create a new security incident."""
        with self._lock:
            incident = SecurityIncidentData(
                id=self._next_id,
                incident_type=incident_type,
                severity=severity,
                status=SecurityIncidentStatus.OPEN.value,
                description=description,
                source_ip=source_ip,
                user_agent=user_agent,
                user_id=user_id,
                order_id=order_id,
                payment_id=payment_id,
                raw_payload=raw_payload or {},
                created_at=_now(),
                updated_at=_now(),
            )
            self._storage[self._next_id] = incident
            self._next_id += 1
            return incident

    def get_by_id(self, id: int) -> Optional[SecurityIncidentData]:
        """Get a security incident by ID."""
        with self._lock:
            return self._storage.get(id)

    def update_status(
        self,
        id: int,
        status: str,
        investigation_notes: str = "",
        assigned_to_id: Optional[int] = None,
    ) -> bool:
        """Update incident status."""
        with self._lock:
            entry = self._storage.get(id)
            if entry is None:
                return False

            updated = SecurityIncidentData(
                id=entry.id,
                incident_type=entry.incident_type,
                severity=entry.severity,
                status=status,
                description=entry.description,
                source_ip=entry.source_ip,
                user_agent=entry.user_agent,
                user_id=entry.user_id,
                order_id=entry.order_id,
                payment_id=entry.payment_id,
                raw_payload=entry.raw_payload,
                assigned_to_id=assigned_to_id or entry.assigned_to_id,
                investigation_notes=investigation_notes or entry.investigation_notes,
                resolved_at=_now() if status == SecurityIncidentStatus.RESOLVED.value else entry.resolved_at,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[id] = updated
            return True

    def find_by_type(
        self,
        incident_type: str,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Find incidents by type."""
        with self._lock:
            results = []
            for entry in self._storage.values():
                if entry.incident_type != incident_type:
                    continue
                if status and entry.status != status:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

    def find_by_source_ip(
        self,
        source_ip: str,
        since: Optional[datetime] = None,
    ) -> List[SecurityIncidentData]:
        """Find incidents by source IP."""
        with self._lock:
            results = []
            for entry in self._storage.values():
                if entry.source_ip != source_ip:
                    continue
                if since and entry.created_at and entry.created_at < since:
                    continue
                results.append(entry)
            return results

    def count_by_source_ip(
        self,
        source_ip: str,
        since: datetime,
    ) -> int:
        """Count incidents by source IP since a given time."""
        return len(self.find_by_source_ip(source_ip, since))

    def get_open_incidents(self, limit: int = 100) -> List[SecurityIncidentData]:
        """Get all open incidents."""
        with self._lock:
            results = [entry for entry in self._storage.values() if entry.status == SecurityIncidentStatus.OPEN.value]
            return results[:limit]

    def get_by_type(
        self,
        incident_type: str,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get incidents by type."""
        with self._lock:
            results = [entry for entry in self._storage.values() if entry.incident_type == incident_type]
            return results[:limit]

    def get_by_severity(
        self,
        severity: str,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get incidents by severity."""
        with self._lock:
            results = [entry for entry in self._storage.values() if entry.severity == severity]
            return results[:limit]

    def mark_as_resolved(
        self,
        id: int,
        investigation_notes: str = "",
    ) -> bool:
        """Mark incident as resolved."""
        return self.update_status(
            id=id,
            status=SecurityIncidentStatus.RESOLVED.value,
            investigation_notes=investigation_notes,
        )

    def get_recent_by_ip(
        self,
        source_ip: str,
        hours: int = 24,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get recent incidents from a specific IP."""
        since = _now() - timedelta(hours=hours)
        with self._lock:
            results = []
            for entry in self._storage.values():
                if entry.source_ip != source_ip:
                    continue
                if entry.created_at and entry.created_at < since:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

    def count_by_type_since(
        self,
        incident_type: str,
        since: datetime,
    ) -> int:
        """Count incidents of a type since a given time."""
        with self._lock:
            count = 0
            for entry in self._storage.values():
                if entry.incident_type != incident_type:
                    continue
                if entry.created_at and entry.created_at >= since:
                    count += 1
            return count

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about security incidents."""
        with self._lock:
            stats = {
                "total": len(self._storage),
                "by_type": {},
                "by_severity": {},
                "by_status": {},
            }
            for entry in self._storage.values():
                stats["by_type"][entry.incident_type] = stats["by_type"].get(entry.incident_type, 0) + 1
                stats["by_severity"][entry.severity] = stats["by_severity"].get(entry.severity, 0) + 1
                stats["by_status"][entry.status] = stats["by_status"].get(entry.status, 0) + 1
            return stats

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._storage.clear()
            self._next_id = 1
