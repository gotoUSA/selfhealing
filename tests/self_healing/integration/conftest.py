"""
Shared fixtures for self-healing integration tests.

These fixtures provide common test infrastructure for:
- Mock metrics collection
- In-memory repositories (no DB dependency)
- Circuit breaker service instances
- Sample data objects (not DB entities)
- Multi-tenant test data

Note: Integration tests use Mock repositories to enable parallel execution.
      Only E2E tests should use actual database connections.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Any, Optional
import uuid
import time

import pytest

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    FailedOperationData,
    FailedOperationStatus,
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
)
from selfhealing.core.timezone import now


# ========================================
# In-Memory Repository Implementations
# ========================================


class InMemoryFailedOperationRepository(FailedOperationRepository):
    """
    In-memory implementation of FailedOperationRepository.
    
    Enables fast, parallel testing without database dependencies.
    Thread-safe for concurrent test execution.
    """

    def __init__(self):
        self._store: dict[int, FailedOperationData] = {}
        self._next_id = 1

    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        entity_refs: Optional[dict[str, int]] = None,
        user_id: Optional[int] = None,
        snapshot_data: Optional[dict[str, Any]] = None,
        request_data: Optional[dict[str, Any]] = None,
        response_data: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
        expires_at: Optional[datetime] = None,
    ) -> FailedOperationData:
        """Create a new failed operation record."""
        current_time = now()
        entry = FailedOperationData(
            id=self._next_id,
            domain=domain,
            failure_type=failure_type,
            status=FailedOperationStatus.PENDING.value,
            entity_type=entity_type or "",
            entity_id=entity_id or "",
            entity_refs=entity_refs or {},
            user_id=user_id,
            error_code=error_code,
            error_message=error_message,
            snapshot_data=snapshot_data or {},
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata or {},
            retry_count=retry_count,
            max_retries=max_retries,
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
            created_at=current_time,
            updated_at=current_time,
            expires_at=expires_at or (current_time + timedelta(days=30)),
        )
        self._store[self._next_id] = entry
        self._next_id += 1
        return entry

    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by ID."""
        return self._store.get(id)

    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending operations for a specific domain."""
        result = [
            e for e in self._store.values()
            if e.domain == domain and e.status == FailedOperationStatus.PENDING.value
        ]
        return result[:limit]

    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain."""
        return len(self.get_pending_by_domain(domain, limit=10000))

    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Update the status of a failed operation."""
        if id not in self._store:
            return False
        entry = self._store[id]
        # Create updated entry (dataclass is immutable by convention)
        self._store[id] = FailedOperationData(
            **{
                **entry.__dict__,
                "status": status,
                "resolution_type": resolution_type,
                "resolution_note": resolution_note,
                "resolved_by_id": resolved_by_id,
                "updated_at": now(),
                "resolved_at": now() if status == FailedOperationStatus.RESOLVED.value else entry.resolved_at,
            }
        )
        return True

    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at."""
        if id not in self._store:
            return False
        entry = self._store[id]
        self._store[id] = FailedOperationData(
            **{
                **entry.__dict__,
                "retry_count": entry.retry_count + 1,
                "last_retry_at": now(),
                "updated_at": now(),
            }
        )
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
        result = [
            e for e in self._store.values()
            if e.expires_at and e.expires_at < before_date
        ]
        return result[:limit]

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
        result = [e for e in self._store.values() if e.status == status]
        if domain:
            result = [e for e in result if e.domain == domain]
        if failure_type:
            result = [e for e in result if e.failure_type == failure_type]
        return result[:limit]

    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations that can be replayed."""
        result = [
            e for e in self._store.values()
            if e.status == FailedOperationStatus.PENDING.value
            and e.retry_count < max_retries
        ]
        if domain:
            result = [e for e in result if e.domain == domain]
        if failure_type:
            result = [e for e in result if e.failure_type == failure_type]
        return result[:limit]

    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: dict[str, timedelta],
    ) -> list[FailedOperationData]:
        """Find operations that have breached their SLA."""
        result = []
        for entry in self._store.values():
            if entry.status != FailedOperationStatus.PENDING.value:
                continue
            threshold = sla_thresholds.get(entry.domain, timedelta(hours=1))
            if entry.created_at and (current_time - entry.created_at) > threshold:
                result.append(entry)
        return result

    def find_expired(
        self,
        current_time: datetime,
    ) -> list[FailedOperationData]:
        """Find operations past their retention period."""
        return [
            e for e in self._store.values()
            if e.expires_at and e.expires_at < current_time
        ]

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about failed operations."""
        stats = {
            "total": len(self._store),
            "by_status": {},
            "by_domain": {},
        }
        for entry in self._store.values():
            stats["by_status"][entry.status] = stats["by_status"].get(entry.status, 0) + 1
            stats["by_domain"][entry.domain] = stats["by_domain"].get(entry.domain, 0) + 1
        return stats

    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """Atomically acquire a DLQ entry for replay."""
        entry = self._store.get(id)
        if not entry:
            return None
        if entry.status != FailedOperationStatus.PENDING.value:
            return None
        if entry.retry_count >= max_retries:
            return None
        
        # Atomically update
        self._store[id] = FailedOperationData(
            **{
                **entry.__dict__,
                "status": "replaying",
                "retry_count": entry.retry_count + 1,
                "last_retry_at": now(),
                "updated_at": now(),
            }
        )
        return self._store[id]

    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Complete a replay operation."""
        entry = self._store.get(id)
        if not entry:
            return False
        
        if success:
            new_status = FailedOperationStatus.RESOLVED.value
        else:
            # Check if escalation needed
            if entry.retry_count >= entry.max_retries:
                new_status = FailedOperationStatus.REQUIRES_REVIEW.value
            else:
                new_status = FailedOperationStatus.PENDING.value
        
        self._store[id] = FailedOperationData(
            **{
                **entry.__dict__,
                "status": new_status,
                "resolution_type": resolution_type if success else "",
                "resolution_note": note,
                "resolved_by_id": resolved_by_id,
                "updated_at": now(),
                "resolved_at": now() if success else None,
            }
        )
        return True

    def release_stale_replaying(
        self,
        stale_threshold: timedelta,
    ) -> int:
        """Release entries stuck in replaying state."""
        current_time = now()
        count = 0
        for id, entry in list(self._store.items()):
            if entry.status == "replaying":
                if entry.last_retry_at and (current_time - entry.last_retry_at) > stale_threshold:
                    self._store[id] = FailedOperationData(
                        **{**entry.__dict__, "status": FailedOperationStatus.PENDING.value}
                    )
                    count += 1
        return count

    def get_pending_entries(
        self,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
        max_retry_count: Optional[int] = None,
    ) -> list[FailedOperationData]:
        """Get pending entries with optional filters (compatibility method)."""
        results = self.find_by_status(
            status=FailedOperationStatus.PENDING.value,
            domain=domain,
            failure_type=failure_type,
            limit=limit,
        )
        # Filter by max retry count if specified
        if max_retry_count is not None:
            results = [e for e in results if e.retry_count < max_retry_count]
        return results

    def find_pending(
        self,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Alias for get_pending_entries."""
        return self.get_pending_entries(domain, failure_type, limit)

    def get_pending_by_failure_types(
        self,
        failure_types: list[str],
        max_retry_count: int,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending entries matching specified failure types."""
        results = [
            e for e in self._store.values()
            if e.status == FailedOperationStatus.PENDING.value
            and e.failure_type in failure_types
            and e.retry_count < max_retry_count
        ]
        return results[:limit]

    def archive_old_resolved(
        self,
        older_than: timedelta,
        batch_size: int = 100,
    ) -> int:
        """Archive old resolved entries."""
        current_time = now()
        archived_count = 0
        for id, entry in list(self._store.items()):
            if entry.status == FailedOperationStatus.RESOLVED.value:
                if entry.resolved_at and (current_time - entry.resolved_at) > older_than:
                    self._store[id] = FailedOperationData(
                        **{**entry.__dict__, "status": "archived", "updated_at": now()}
                    )
                    archived_count += 1
                    if archived_count >= batch_size:
                        break
        return archived_count

    def get_cleanup_stats(self) -> dict[str, int]:
        """Get cleanup statistics."""
        stats = {
            "total": len(self._store),
            "pending": 0,
            "resolved": 0,
            "archived": 0,
            "requires_review": 0,
        }
        for entry in self._store.values():
            if entry.status in stats:
                stats[entry.status] += 1
        return stats

    def purge_archived(
        self,
        older_than: timedelta,
        batch_size: int = 100,
    ) -> int:
        """Permanently delete very old archived entries."""
        current_time = now()
        purged_count = 0
        ids_to_delete = []
        for id, entry in self._store.items():
            if entry.status == "archived":
                if entry.updated_at and (current_time - entry.updated_at) > older_than:
                    ids_to_delete.append(id)
                    if len(ids_to_delete) >= batch_size:
                        break
        for id in ids_to_delete:
            del self._store[id]
            purged_count += 1
        return purged_count

    def mark_as_requires_review(
        self,
        id: int,
        note: str = "",
    ) -> bool:
        """Mark entry as requiring human review."""
        if id not in self._store:
            return False
        entry = self._store[id]
        self._store[id] = FailedOperationData(
            **{
                **entry.__dict__,
                "status": FailedOperationStatus.REQUIRES_REVIEW.value,
                "resolution_note": note,
                "updated_at": now(),
            }
        )
        return True

    def clear(self) -> None:
        """Clear all entries (for test cleanup)."""
        self._store.clear()
        self._next_id = 1


class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    In-memory implementation of CircuitBreakerStateRepository.
    
    Enables fast, parallel testing without database dependencies.
    """

    def __init__(self):
        self._store: dict[str, CircuitBreakerStateData] = {}

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """Get or create circuit breaker state for a service."""
        if service_name not in self._store:
            self._store[service_name] = CircuitBreakerStateData(
                service_name=service_name,
                state=CircuitBreakerStateEnum.CLOSED.value,
                failure_count=0,
                success_count=0,
                created_at=now(),
                updated_at=now(),
            )
        return self._store[service_name]

    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name."""
        return self._store.get(service_name)

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        manually_controlled: bool = False,
    ) -> bool:
        """Update circuit breaker state."""
        current = self.get_or_create(service_name)
        self._store[service_name] = CircuitBreakerStateData(
            service_name=service_name,
            id=current.id,
            state=state,
            failure_count=failure_count if failure_count is not None else current.failure_count,
            success_count=success_count if success_count is not None else current.success_count,
            last_failure_at=current.last_failure_at,
            opened_at=now() if state == CircuitBreakerStateEnum.OPEN.value else current.opened_at,
            manually_controlled=manually_controlled,
            controlled_by_id=controlled_by_id,
            control_reason=reason,
            half_open_request_count=current.half_open_request_count,
            created_at=current.created_at,
            updated_at=now(),
        )
        return True

    def increment_failure(self, service_name: str) -> int:
        """Increment failure count and return new value."""
        current = self.get_or_create(service_name)
        new_count = current.failure_count + 1
        self._store[service_name] = CircuitBreakerStateData(
            **{
                **current.__dict__,
                "failure_count": new_count,
                "last_failure_at": now(),
                "updated_at": now(),
            }
        )
        return new_count

    def increment_success(self, service_name: str) -> int:
        """Increment success count and return new value."""
        current = self.get_or_create(service_name)
        new_count = current.success_count + 1
        self._store[service_name] = CircuitBreakerStateData(
            **{
                **current.__dict__,
                "success_count": new_count,
                "updated_at": now(),
            }
        )
        return new_count

    def reset_counts(self, service_name: str) -> bool:
        """Reset failure and success counts."""
        if service_name not in self._store:
            return False
        current = self._store[service_name]
        self._store[service_name] = CircuitBreakerStateData(
            **{
                **current.__dict__,
                "failure_count": 0,
                "success_count": 0,
                "updated_at": now(),
            }
        )
        return True

    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states."""
        return list(self._store.values())

    def get_all(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states."""
        return self.get_all_states()

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state."""
        self.increment_failure(service_name)
        return self.get_or_create(service_name)

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Record a success and return updated state."""
        self.increment_success(service_name)
        return self.get_or_create(service_name)

    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: Optional[int] = None,
        reason: str = "",
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Set manual control on a circuit breaker."""
        self.update_state(
            service_name,
            state=state,
            manually_controlled=True,
            controlled_by_id=controlled_by_id,
            reason=reason,
        )
        return True

    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """Clear manual control from a circuit breaker."""
        if service_name not in self._store:
            return False
        current = self._store[service_name]
        self._store[service_name] = CircuitBreakerStateData(
            **{
                **current.__dict__,
                "manually_controlled": False,
                "controlled_by_id": None,
                "control_reason": current.control_reason if preserve_reason else "",
                "updated_at": now(),
            }
        )
        return True

    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state."""
        if service_name not in self._store:
            return False
        current = self._store[service_name]
        self._store[service_name] = CircuitBreakerStateData(
            **{
                **current.__dict__,
                "state": CircuitBreakerStateEnum.CLOSED.value,
                "failure_count": 0,
                "success_count": 0,
                "manually_controlled": False,
                "controlled_by_id": None,
                "control_reason": "",
                "updated_at": now(),
            }
        )
        return True

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """Atomically force open a circuit breaker."""
        current = self.get_or_create(service_name)
        previous_state = current.state
        self.update_state(
            service_name,
            state=CircuitBreakerStateEnum.OPEN.value,
            manually_controlled=True,
            controlled_by_id=controlled_by_id,
            reason=reason,
        )
        return (True, previous_state, CircuitBreakerStateEnum.OPEN.value)

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically force close a circuit breaker."""
        current = self.get_or_create(service_name)
        previous_state = current.state
        self.update_state(
            service_name,
            state=CircuitBreakerStateEnum.CLOSED.value,
            manually_controlled=True,
            controlled_by_id=controlled_by_id,
            reason=reason,
            failure_count=0,
            success_count=0,
        )
        return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically reset a circuit breaker to initial state."""
        current = self.get_or_create(service_name)
        previous_state = current.state
        self.reset(service_name)
        return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

    def clear(self) -> None:
        """Clear all states (for test cleanup)."""
        self._store.clear()


# ========================================
# Mock Metrics Fixture
# ========================================

class MockMetrics:
    """
    Mock metrics collector for testing observability.
    
    Provides a simple in-memory implementation of
    counter, histogram, and gauge metrics.
    """

    def __init__(self):
        self._counters: dict[str, dict[tuple, int]] = {}
        self._histograms: dict[str, list[dict]] = {}
        self._gauges: dict[str, dict[tuple, float]] = {}
        self._events: dict[str, list[dict]] = {}

    def increment(
        self,
        name: str,
        value: int = 1,
        labels: dict[str, Any] | None = None,
    ) -> None:
        """Increment a counter metric."""
        if name not in self._counters:
            self._counters[name] = {}
        
        label_key = tuple(sorted((labels or {}).items()))
        current = self._counters[name].get(label_key, 0)
        self._counters[name][label_key] = current + value
        
        # Also record as event
        self._record_event(name, {"type": "increment", "value": value, "labels": labels})

    def get_value(
        self,
        name: str,
        labels: dict[str, Any] | None = None,
    ) -> int:
        """Get current counter value."""
        if name not in self._counters:
            return 0
        
        label_key = tuple(sorted((labels or {}).items()))
        return self._counters[name].get(label_key, 0)

    def observe(
        self,
        name: str,
        value: float,
        labels: dict[str, Any] | None = None,
    ) -> None:
        """Record a histogram observation."""
        if name not in self._histograms:
            self._histograms[name] = []
        
        self._histograms[name].append({
            "value": value,
            "labels": labels or {},
        })
        
        self._record_event(name, {"type": "observe", "value": value, "labels": labels})

    def get_histogram(
        self,
        name: str,
        labels: dict[str, Any] | None = None,
    ) -> list[float]:
        """Get histogram observations."""
        if name not in self._histograms:
            return []
        
        observations = self._histograms[name]
        
        if labels:
            # Filter by labels
            filtered = []
            for obs in observations:
                if all(obs["labels"].get(k) == v for k, v in labels.items()):
                    filtered.append(obs["value"])
            return filtered
        
        return [obs["value"] for obs in observations]

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, Any] | None = None,
    ) -> None:
        """Set a gauge value."""
        if name not in self._gauges:
            self._gauges[name] = {}
        
        label_key = tuple(sorted((labels or {}).items()))
        self._gauges[name][label_key] = value
        
        self._record_event(name, {"type": "gauge", "value": value, "labels": labels})

    def gauge(
        self,
        name: str,
        value: float = None,
        labels: dict[str, Any] | None = None,
    ) -> None:
        """Alias for set_gauge for compatibility."""
        self.set_gauge(name, value, labels)

    def get_gauge(
        self,
        name: str,
        labels: dict[str, Any] | None = None,
    ) -> float | None:
        """Get gauge value."""
        if name not in self._gauges:
            return None
        
        label_key = tuple(sorted((labels or {}).items()))
        return self._gauges[name].get(label_key)

    def _record_event(self, name: str, event: dict) -> None:
        """Record a metric event for ordering tests."""
        import time
        if name not in self._events:
            self._events[name] = []
        
        event["timestamp"] = time.time()
        self._events[name].append(event)

    def get_events(self, name: str = None) -> list[dict]:
        """Get all events for a metric, or all events if name is None."""
        if name is None:
            # Return all events from all metrics
            all_events = []
            for events in self._events.values():
                all_events.extend(events)
            return sorted(all_events, key=lambda e: e.get("timestamp", 0))
        return self._events.get(name, [])

    def has_label(self, metric_name: str, label_name: str, label_value: str) -> bool:
        """Check if a metric has a specific label value."""
        # Check counters
        if metric_name in self._counters:
            for label_key in self._counters[metric_name].keys():
                if any(k == label_name and v == label_value for k, v in label_key):
                    return True
        # Check histograms
        if metric_name in self._histograms:
            for obs in self._histograms[metric_name]:
                if obs.get("labels", {}).get(label_name) == label_value:
                    return True
        # Check gauges
        if metric_name in self._gauges:
            for label_key in self._gauges[metric_name].keys():
                if any(k == label_name and v == label_value for k, v in label_key):
                    return True
        return False

    def reset(self) -> None:
        """Reset all metrics."""
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()
        self._events.clear()


@pytest.fixture
def mock_metrics():
    """Provide a fresh mock metrics instance."""
    return MockMetrics()


# ========================================
# In-Memory Repository Fixtures
# ========================================

@pytest.fixture
def failed_operation_repository():
    """Provide an in-memory FailedOperation repository."""
    return InMemoryFailedOperationRepository()


@pytest.fixture
def circuit_breaker_repository():
    """Provide an in-memory CircuitBreaker repository."""
    return InMemoryCircuitBreakerStateRepository()


# ========================================
# Sample Data Fixtures (Mock - No DB)
# ========================================

@dataclass
class MockUser:
    """Mock user object for testing."""
    id: int = field(default_factory=lambda: int(time.time() * 1000) % 1000000)
    username: str = "testuser"
    email: str = "test@example.com"
    is_staff: bool = False
    is_superuser: bool = False
    is_email_verified: bool = True


@dataclass
class MockOrder:
    """Mock order object for testing."""
    id: int = field(default_factory=lambda: int(time.time() * 1000) % 1000000)
    user: Any = None
    status: str = "confirmed"
    total_amount: Decimal = Decimal("10000")


@dataclass
class MockPayment:
    """Mock payment object for testing."""
    id: int = field(default_factory=lambda: int(time.time() * 1000) % 1000000)
    order: Any = None
    status: str = "in_progress"
    amount: Decimal = Decimal("10000")
    payment_key: str = field(default_factory=lambda: f"pay_{uuid.uuid4().hex[:12]}")
    is_paid: bool = False


@pytest.fixture
def sample_user():
    """Create a mock user for tests (no DB)."""
    return MockUser()


@pytest.fixture
def sample_order(sample_user):
    """Create a mock order for tests (no DB)."""
    return MockOrder(user=sample_user)


@pytest.fixture
def sample_payment(sample_order):
    """Create a mock payment for tests (no DB)."""
    return MockPayment(order=sample_order)


# ========================================
# Service Fixtures with Injected Repositories
# ========================================

@pytest.fixture
def circuit_breaker_service(circuit_breaker_repository):
    """Provide a circuit breaker service with mock repository."""
    from selfhealing.services import CircuitBreakerService
    return CircuitBreakerService(repository=circuit_breaker_repository)


@pytest.fixture
def dlq_service(failed_operation_repository):
    """Provide a DLQ service with mock repository."""
    from selfhealing.services import DLQService, DLQConfig
    return DLQService(
        repository=failed_operation_repository,
        config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
    )


@pytest.fixture
def recovery_handler():
    """Provide a mock recovery handler."""
    handler = MagicMock()
    handler.handle = MagicMock(return_value={"success": True})
    handler.can_retry = MagicMock(return_value=True)
    handler.max_retries = 3
    return handler


# ========================================
# Multi-Tenant Fixtures
# ========================================

@dataclass
class MockTenant:
    """Mock tenant for multi-tenancy tests."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Test Tenant"
    settings: dict = field(default_factory=dict)
    admin_user: Any = None  # Will be populated if needed
    sla_timeout_seconds: int = 3600  # Default 1 hour
    
    def __post_init__(self):
        # Create a mock admin user if not provided
        if self.admin_user is None:
            self.admin_user = MagicMock()
            self.admin_user.id = f"admin-{self.id}"
            self.admin_user.username = f"admin_{self.name.lower().replace(' ', '_')}"


@pytest.fixture
def tenant_a():
    """Tenant A for isolation tests."""
    return MockTenant(id="tenant-a", name="Tenant A")


@pytest.fixture
def tenant_b():
    """Tenant B for isolation tests."""
    return MockTenant(id="tenant-b", name="Tenant B")


# ========================================
# Audit Entry Mock
# ========================================

@dataclass
class AuditEntry:
    """Mock audit entry for accountability tests."""
    action_type: str
    dlq_id: int | None = None
    controlled_by: int | None = None
    control_reason: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: "")
    # CB state change fields
    previous_state: str = ""
    new_state: str = ""


# ========================================
# Category Fixture for Parametrized Tests
# ========================================

@pytest.fixture
def category():
    """Provide test category marker."""
    return "dlq"


# ========================================
# Admin User Fixture
# ========================================

@pytest.fixture
def admin_user(db):
    """Create an admin user for audit tests."""
    return UserFactory(is_staff=True, is_superuser=True)


# ========================================
# Audit Log Repository Mock
# ========================================

class MockAuditLogRepository:
    """Mock audit log repository for accountability tests."""

    def __init__(self):
        self._entries: list[AuditEntry] = []

    def log_circuit_breaker_action(
        self,
        service_name: str,
        previous_state: str,
        new_state: str,
        controlled_by: Any,
        reason: str,
        ip_address: str = "",
        user_agent: str = "",
    ) -> AuditEntry:
        """Log a circuit breaker action."""
        from django.utils import timezone
        entry = AuditEntry(
            action_type="circuit_breaker_action",
            controlled_by=getattr(controlled_by, "id", controlled_by),
            control_reason=reason,
            timestamp=timezone.now().isoformat(),
            previous_state=previous_state,
            new_state=new_state,
            metadata={
                "service_name": service_name,
                "previous_state": previous_state,
                "new_state": new_state,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )
        self._entries.append(entry)
        return entry

    def log(self, entry: AuditEntry) -> AuditEntry:
        """Generic log method for any audit entry."""
        self._entries.append(entry)
        return entry

    def log_dlq_action(
        self,
        dlq_id: int,
        action: str,
        performed_by: Any,
        outcome: str = "",
        metadata: dict = None,
    ) -> AuditEntry:
        """Log a DLQ action."""
        from django.utils import timezone
        entry = AuditEntry(
            action_type=f"dlq_{action}",
            dlq_id=dlq_id,
            controlled_by=getattr(performed_by, "id", performed_by),
            timestamp=timezone.now().isoformat(),
            metadata={
                "outcome": outcome,
                **(metadata or {}),
            },
        )
        self._entries.append(entry)
        return entry

    def log_auto_retry(
        self,
        dlq_id: int,
        decision_engine: str,
        policy_version: str,
        outcome: str,
    ) -> AuditEntry:
        """Log an auto-retry decision."""
        from django.utils import timezone
        entry = AuditEntry(
            action_type="auto_retry",
            dlq_id=dlq_id,
            timestamp=timezone.now().isoformat(),
            metadata={
                "decision_engine": decision_engine,
                "policy_version": policy_version,
                "outcome": outcome,
            },
        )
        self._entries.append(entry)
        return entry

    def log_cost_decision(
        self,
        dlq_id: int,
        cost_estimate: Decimal,
        threshold: Decimal,
        action: str,
        rationale: str,
    ) -> AuditEntry:
        """Log a cost-based decision."""
        from django.utils import timezone
        entry = AuditEntry(
            action_type="cost_decision",
            dlq_id=dlq_id,
            timestamp=timezone.now().isoformat(),
            metadata={
                "cost_estimate": str(cost_estimate),
                "threshold": str(threshold),
                "action": action,
                "rationale": rationale,
            },
        )
        self._entries.append(entry)
        return entry

    def log_sla_action(
        self,
        dlq_id: int,
        elapsed_time: float,
        sla_threshold: float,
        action: str,
    ) -> AuditEntry:
        """Log an SLA-based action."""
        from django.utils import timezone
        entry = AuditEntry(
            action_type="sla_action",
            dlq_id=dlq_id,
            timestamp=timezone.now().isoformat(),
            metadata={
                "elapsed_time": elapsed_time,
                "sla_threshold": sla_threshold,
                "action": action,
            },
        )
        self._entries.append(entry)
        return entry

    def log_escalation(
        self,
        dlq_id: int,
        failure_count: int,
        reason: str,
        escalation_level: str = "requires_review",
    ) -> AuditEntry:
        """Log an escalation."""
        from django.utils import timezone
        entry = AuditEntry(
            action_type="escalation",
            dlq_id=dlq_id,
            timestamp=timezone.now().isoformat(),
            metadata={
                "failure_count": failure_count,
                "reason": reason,
                "escalation_level": escalation_level,
            },
        )
        self._entries.append(entry)
        return entry

    def find_by_action(
        self,
        action_type: str,
        service_name: str = None,
    ) -> list[AuditEntry]:
        """Find audit entries by action type."""
        result = [e for e in self._entries if e.action_type == action_type]
        if service_name:
            result = [
                e for e in result
                if e.metadata.get("service_name") == service_name
            ]
        return result

    def find_by_dlq_id(self, dlq_id: int) -> list[AuditEntry]:
        """Find audit entries by DLQ ID."""
        return [e for e in self._entries if e.dlq_id == dlq_id]

    def get_all(self) -> list[AuditEntry]:
        """Get all audit entries."""
        return self._entries.copy()

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()


@pytest.fixture
def audit_log_repository():
    """Provide a mock audit log repository."""
    return MockAuditLogRepository()


# ========================================
# Cost Tracker Fixtures
# ========================================

class MockCostTracker:
    """Mock cost tracker for cost-based decision tests."""

    def __init__(self, default_cost: float = 100.0):
        self._costs: dict[int, float] = {}
        self._default_cost = default_cost

    def get_cost(self, dlq_id: int) -> float:
        """Get estimated cost for a DLQ entry."""
        return self._costs.get(dlq_id, self._default_cost)

    def set_cost(self, dlq_id: int, cost: float) -> None:
        """Set estimated cost for a DLQ entry."""
        self._costs[dlq_id] = cost

    def is_high_cost(self, dlq_id: int, threshold: float = 1000.0) -> bool:
        """Check if a DLQ entry has high estimated cost."""
        return self.get_cost(dlq_id) > threshold


@pytest.fixture
def cost_tracker():
    """Provide a mock cost tracker with default costs."""
    return MockCostTracker(default_cost=100.0)


@pytest.fixture
def high_cost_tracker():
    """Provide a mock cost tracker with high default costs."""
    return MockCostTracker(default_cost=5000.0)


# ========================================
# Replay Service with Registered Handlers
# ========================================

@pytest.fixture
def replay_service(failed_operation_repository):
    """
    Provide a replay service with mock repository.
    
    Uses in-memory repository - no DB dependency.
    Handlers should be registered in tests as needed using mock handlers.
    """
    from selfhealing.services import ReplayService
    
    # Create service with in-memory repository
    service = ReplayService(repository=failed_operation_repository)
    return service
