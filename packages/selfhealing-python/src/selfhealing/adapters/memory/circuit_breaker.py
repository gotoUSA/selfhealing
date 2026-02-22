"""
In-Memory Circuit Breaker State Repository Implementation.

Thread-safe in-memory storage for circuit breaker states.
Includes L1+L2 Layered Storage with Drift Reconciliation support.

Note: This module has been refactored for better maintainability:
- DriftReconciler, DriftReconciliationResult → drift_reconciliation.py
- ShadowLogger, L2SyncFailureRecord → shadow_logger.py
- LayeredCircuitBreakerStateRepository → layered_repository.py
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from selfhealing.adapters.memory.base import _now

# Re-export for backward compatibility
from selfhealing.adapters.memory.drift_reconciliation import (
    DriftReconciler,
    DriftReconciliationRecord,
    DriftReconciliationResult,
    get_drift_reconciler,
)
from selfhealing.adapters.memory.layered_repository import (
    LayeredCircuitBreakerStateRepository,
)
from selfhealing.adapters.memory.shadow_logger import (
    L2SyncFailureRecord,
    ShadowLogger,
    get_shadow_logger,
)
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
    CircuitBreakerStateRepository,
)

logger = logging.getLogger(__name__)


class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    In-memory implementation of CircuitBreakerStateRepository.

    Thread-safe storage for circuit breaker states in memory.

    sliding_window_size가 지정되면 record_failure() / record_success()에서
    Ring Buffer 기반 Sliding Window 카운팅을 수행한다.
    failure_count와 success_count는 최근 N건 내의 카운트를 반영한다.
    """

    def __init__(self, sliding_window_size: int = 100):
        self._storage: dict[str, CircuitBreakerStateData] = {}
        self._next_id = 1
        self._lock = threading.RLock()  # RLock for reentrant calls

        # Sliding Window: 서비스별 ring buffer (True=success, False=failure)
        self._sliding_window_size = sliding_window_size
        self._call_windows: dict[str, deque[bool]] = {}

    def get_by_service_name(self, service_name: str) -> CircuitBreakerStateData | None:
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
        failure_count: int | None = None,
        success_count: int | None = None,
        opened_at: datetime | None = None,
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
                failure_count=(failure_count if failure_count is not None else entry.failure_count),
                success_count=(success_count if success_count is not None else entry.success_count),
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
        last_failure_at: datetime | None = None,
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

            self._clear_window(service_name)

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
        expires_at: datetime | None = None,
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
                opened_at=(_now() if state == CircuitBreakerStateEnum.OPEN.value else entry.opened_at),
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
        """Clear manual control override.

        수동 제어 플래그만 해제한다. 상태(state)와 카운터(failure_count, success_count)는
        변경하지 않는다. 상태 전이가 필요하면 호출 측에서 update_state를 먼저 수행해야 한다.
        """
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            self._clear_window(service_name)

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=entry.failure_count,
                success_count=entry.success_count,
                last_failure_at=entry.last_failure_at,
                opened_at=entry.opened_at,
                manually_controlled=False,
                controlled_by_id=None,
                control_reason=entry.control_reason if preserve_reason else "",
                manual_override_expires_at=None,
                half_open_request_count=entry.half_open_request_count,
                created_at=entry.created_at,
                updated_at=_now(),
            )
            self._storage[service_name] = updated
            return True

    def _get_or_create_window(self, service_name: str) -> deque[bool]:
        """서비스별 Sliding Window ring buffer를 가져오거나 생성한다."""
        if service_name not in self._call_windows:
            self._call_windows[service_name] = deque(
                maxlen=self._sliding_window_size,
            )
        return self._call_windows[service_name]

    def _clear_window(self, service_name: str) -> None:
        """서비스별 Sliding Window를 초기화한다."""
        if service_name in self._call_windows:
            self._call_windows[service_name].clear()

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state.

        Sliding Window ring buffer에 실패를 기록하고,
        window 내 failure/success 카운트로 상태를 갱신한다.
        """
        with self._lock:
            entry = self.get_or_create(service_name)

            # Ring buffer에 실패 기록
            window = self._get_or_create_window(service_name)
            window.append(False)  # False = failure

            # Window 기반 카운트 계산
            failure_count = sum(1 for call in window if not call)
            success_count = sum(1 for call in window if call)

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=failure_count,
                success_count=success_count,
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
        """Record a success and return updated state.

        Sliding Window ring buffer에 성공을 기록하고,
        window 내 failure/success 카운트로 상태를 갱신한다.
        """
        with self._lock:
            entry = self.get_or_create(service_name)

            # Ring buffer에 성공 기록
            window = self._get_or_create_window(service_name)
            window.append(True)  # True = success

            # Window 기반 카운트 계산
            failure_count = sum(1 for call in window if not call)
            success_count = sum(1 for call in window if call)

            updated = CircuitBreakerStateData(
                id=entry.id,
                service_name=service_name,
                state=entry.state,
                failure_count=failure_count,
                success_count=success_count,
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

    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states (alias for get_all)."""
        return self.get_all()

    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state."""
        with self._lock:
            entry = self._storage.get(service_name)
            if entry is None:
                return False

            self._clear_window(service_name)

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
        controlled_by_id: int | None = None,
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
        controlled_by_id: int | None = None,
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
        controlled_by_id: int | None = None,
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

    def get_all_open(self) -> list[CircuitBreakerStateData]:
        """Get all open circuit breakers."""
        with self._lock:
            return [entry for entry in self._storage.values() if entry.state == CircuitBreakerStateEnum.OPEN.value]

    def get_all(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states."""
        with self._lock:
            return list(self._storage.values())

    def delete(self, service_name: str) -> bool:
        """Delete a circuit breaker state."""
        with self._lock:
            if service_name in self._storage:
                del self._storage[service_name]
                self._clear_window(service_name)
                return True
            return False

    def delete_state(self, service_name: str) -> bool:
        """Delete circuit breaker state (alias for delete)."""
        return self.delete(service_name)

    def update_metadata(self, service_name: str, metadata: dict[str, Any]) -> bool:
        """RLock 내에서 metadata 필드만 교체. 다른 필드 무영향."""
        with self._lock:
            state = self._storage.get(service_name)
            if state is None:
                return False
            state.metadata = metadata
            return True

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._storage.clear()
            self._call_windows.clear()
            self._next_id = 1


# =============================================================================
# Backward Compatibility Exports
# =============================================================================

__all__ = [
    # Main repository
    "InMemoryCircuitBreakerStateRepository",
    "LayeredCircuitBreakerStateRepository",
    # Drift reconciliation (re-exported from drift_reconciliation.py)
    "DriftReconciliationResult",
    "DriftReconciliationRecord",
    "DriftReconciler",
    "get_drift_reconciler",
    # Shadow logger (re-exported from shadow_logger.py)
    "L2SyncFailureRecord",
    "ShadowLogger",
    "get_shadow_logger",
]
