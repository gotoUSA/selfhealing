"""
Redis-based Circuit Breaker State Repository.

Implements CircuitBreakerStateRepository interface using ResilientStorageBackend.
Provides zero data loss guarantees through WAL-First protocol.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
    CircuitBreakerStateRepository,
)

if TYPE_CHECKING:
    from selfhealing.adapters.resilient.backend import ResilientStorageBackend

logger = structlog.get_logger()


class RedisCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    Redis-based Circuit Breaker State Repository.

    Uses ResilientStorageBackend for:
    - Normal mode: Redis storage
    - Degraded mode: Memory + WAL (zero data loss)

    Redis Key Structure:
    - cb:{service_name} → Hash with state fields
    - cb:{service_name}:history → List of state change events

    Multi-Cluster Support:
    - Namespace-aware key prefixing via NamespaceSettings
    - Key pattern: {namespace}:cb:{service_name} (when enabled)

    Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
    """

    _BASE_PREFIX = "cb"
    HISTORY_SUFFIX = ":history"
    MAX_HISTORY_ENTRIES = 100

    def __init__(self, backend: ResilientStorageBackend):
        """
        Initialize Redis Circuit Breaker Repository.

        Args:
            backend: ResilientStorageBackend instance
        """
        self._backend = backend
        self._key_prefix = self._build_key_prefix()

    def _build_key_prefix(self) -> str:
        """
        Build component key prefix.

        Note: Namespace prefixing is handled by ResilientStorageBackend.config.key_prefix.
        This method returns only the component-level prefix (e.g., "cb:").

        Final key format in Redis:
        - Backend.key_prefix + CB.key_prefix + service_name
        - e.g., "selfhealing:seoul:" + "cb:" + "payment-api"

        Returns:
            Component key prefix like "cb:"
        """
        # CB는 항상 base prefix만 반환
        # namespace prefixing은 ResilientStorageBackend에서 처리
        return f"{self._BASE_PREFIX}:"

    @property
    def KEY_PREFIX(self) -> str:
        """
        Backward compatible property for KEY_PREFIX.

        Returns dynamically built prefix for namespace support.
        """
        return self._key_prefix

    def _make_key(self, service_name: str) -> str:
        """Generate storage key for service."""
        return f"{self._key_prefix}{service_name}"

    def _make_history_key(self, service_name: str) -> str:
        """Generate history key for service."""
        return f"{self._key_prefix}{service_name}{self.HISTORY_SUFFIX}"

    # =========================================================================
    # Interface Implementation
    # =========================================================================

    def get_state(self, service_name: str) -> CircuitBreakerStateData | None:
        """
        Get circuit breaker state for a service.

        Args:
            service_name: Service identifier

        Returns:
            CircuitBreakerStateData if exists, None otherwise
        """
        data = self._backend.hgetall(self._make_key(service_name))
        if not data:
            return None
        return self._to_data(service_name, data)

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """
        Get existing state or create with defaults.

        Args:
            service_name: Service identifier

        Returns:
            CircuitBreakerStateData (existing or newly created)
        """
        existing = self.get_state(service_name)
        if existing:
            return existing

        # Create with default CLOSED state
        now = datetime.now(timezone.utc).isoformat()
        default_data = {
            "state": CircuitBreakerStateEnum.CLOSED.value,
            "failure_count": "0",
            "success_count": "0",
            "half_open_request_count": "0",
            "manually_controlled": "False",
            "control_reason": "",
            "created_at": now,
            "updated_at": now,
        }

        self._backend.hset(self._make_key(service_name), default_data)

        return self._to_data(service_name, default_data)

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: int | None = None,
        success_count: int | None = None,
        opened_at: datetime | None = None,
        last_failure_at: datetime | None = None,
        half_open_request_count: int | None = None,
    ) -> bool:
        """
        Update circuit breaker state.

        Args:
            service_name: Service identifier
            state: New state (closed, open, half_open)
            failure_count: Optional failure count
            success_count: Optional success count
            opened_at: Optional time when circuit was opened
            last_failure_at: Optional last failure time
            half_open_request_count: Optional half-open request count

        Returns:
            True on success
        """
        now = datetime.now(timezone.utc)

        updates: dict[str, str] = {
            "state": state,
            "updated_at": now.isoformat(),
        }

        if failure_count is not None:
            updates["failure_count"] = str(failure_count)
        if success_count is not None:
            updates["success_count"] = str(success_count)
        if opened_at is not None:
            updates["opened_at"] = opened_at.isoformat()
        if last_failure_at is not None:
            updates["last_failure_at"] = last_failure_at.isoformat()
        if half_open_request_count is not None:
            updates["half_open_request_count"] = str(half_open_request_count)

        # Store state update
        result = self._backend.hset(self._make_key(service_name), updates)

        # Record in history
        self._record_history(service_name, state, now)

        return result

    def reset_state(self, service_name: str) -> bool:
        """
        Reset circuit breaker to CLOSED state.

        Args:
            service_name: Service identifier

        Returns:
            True on success
        """
        now = datetime.now(timezone.utc)

        reset_data = {
            "state": CircuitBreakerStateEnum.CLOSED.value,
            "failure_count": "0",
            "success_count": "0",
            "half_open_request_count": "0",
            "opened_at": "",
            "last_failure_at": "",
            "updated_at": now.isoformat(),
        }

        result = self._backend.hset(self._make_key(service_name), reset_data)
        self._record_history(service_name, "closed", now, note="manual_reset")

        return result

    def increment_failure(self, service_name: str) -> int:
        """
        Increment failure count.

        Args:
            service_name: Service identifier

        Returns:
            New failure count
        """
        # Get current count
        current = self.get_or_create(service_name)
        new_count = current.failure_count + 1

        now = datetime.now(timezone.utc)
        self._backend.hset(
            self._make_key(service_name),
            {
                "failure_count": str(new_count),
                "last_failure_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )

        return new_count

    def increment_success(self, service_name: str) -> int:
        """
        Increment success count.

        Args:
            service_name: Service identifier

        Returns:
            New success count
        """
        current = self.get_or_create(service_name)
        new_count = current.success_count + 1

        now = datetime.now(timezone.utc)
        self._backend.hset(
            self._make_key(service_name),
            {
                "success_count": str(new_count),
                "updated_at": now.isoformat(),
            },
        )

        return new_count

    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """
        Get all circuit breaker states.

        Note: This is an expensive operation in Redis.
        Consider using a set to track service names for efficiency.

        Returns:
            List of all CircuitBreakerStateData
        """
        # In degraded mode, we can only return what's in memory
        if self._backend.is_degraded:
            return self._get_all_from_memory()

        # In Redis mode, scan for keys
        try:
            pattern = f"{self._backend.config.key_prefix}{self.KEY_PREFIX}*"
            keys = self._backend._redis._redis.keys(pattern)

            results = []
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode()

                # Skip history keys
                if key.endswith(self.HISTORY_SUFFIX):
                    continue

                # Extract service name
                prefix = f"{self._backend.config.key_prefix}{self.KEY_PREFIX}"
                service_name = key[len(prefix) :]

                data = self.get_state(service_name)
                if data:
                    results.append(data)

            return results

        except Exception as e:
            logger.exception(
                "redis_cb_repo.error",
                error=e,
            )
            return self._get_all_from_memory()

    def _get_all_from_memory(self) -> list[CircuitBreakerStateData]:
        """Get all states from memory (degraded mode)."""
        results = []
        for key, value in self._backend._memory.items():
            if key.startswith(self.KEY_PREFIX) and not key.endswith(
                self.HISTORY_SUFFIX
            ):
                service_name = key[len(self.KEY_PREFIX) :]
                if isinstance(value, dict):
                    results.append(self._to_data(service_name, value))
        return results

    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: int | None = None,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> bool:
        """
        Set manual control on circuit breaker.

        Args:
            service_name: Service identifier
            state: State to set (e.g., 'open', 'closed')
            controlled_by_id: User ID who set control
            reason: Reason for manual control
            expires_at: When manual control expires

        Returns:
            True on success
        """
        now = datetime.now(timezone.utc)

        updates = {
            "state": state,
            "manually_controlled": "True",
            "control_reason": reason,
            "updated_at": now.isoformat(),
        }

        if controlled_by_id is not None:
            updates["controlled_by_id"] = str(controlled_by_id)

        if expires_at is not None:
            updates["manual_override_expires_at"] = expires_at.isoformat()

        return self._backend.hset(self._make_key(service_name), updates)

    def delete_state(self, service_name: str) -> bool:
        """
        Delete circuit breaker state.

        Args:
            service_name: Service identifier

        Returns:
            True on success
        """
        return self._backend.delete(self._make_key(service_name))

    def update_metadata(self, service_name: str, metadata: dict[str, Any]) -> bool:
        """metadata 필드만 HSET — 다른 Hash 필드(state, failure_count 등) 무영향."""
        return self._backend.hset(
            self._make_key(service_name),
            {"metadata": json.dumps(metadata)},
        )

    # =========================================================================
    # History Operations
    # =========================================================================

    def _record_history(
        self,
        service_name: str,
        state: str,
        timestamp: datetime,
        note: str = "",
    ) -> None:
        """Record state change in history."""
        history_entry = {
            "state": state,
            "timestamp": timestamp.isoformat(),
            "note": note,
        }

        history_key = self._make_history_key(service_name)
        self._backend.lpush(history_key, history_entry)

        # Trim history to max entries
        self._backend.ltrim(history_key, 0, self.MAX_HISTORY_ENTRIES - 1)

    def get_history(
        self,
        service_name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Get state change history.

        Args:
            service_name: Service identifier
            limit: Maximum entries to return

        Returns:
            List of history entries (newest first)
        """
        history_key = self._make_history_key(service_name)
        return self._backend.lrange(history_key, 0, limit - 1)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _to_data(
        self,
        service_name: str,
        data: dict[str, Any],
    ) -> CircuitBreakerStateData:
        """Convert dict to CircuitBreakerStateData."""
        raw_metadata = data.get("metadata", "{}")
        try:
            metadata = (
                json.loads(raw_metadata)
                if isinstance(raw_metadata, str)
                else raw_metadata
            )
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        return CircuitBreakerStateData(
            service_name=service_name,
            id=None,  # Redis doesn't use numeric IDs
            state=data.get("state", CircuitBreakerStateEnum.CLOSED.value),
            failure_count=self._parse_int(data.get("failure_count", 0)),
            success_count=self._parse_int(data.get("success_count", 0)),
            last_failure_at=self._parse_datetime(data.get("last_failure_at")),
            opened_at=self._parse_datetime(data.get("opened_at")),
            manually_controlled=self._parse_bool(
                data.get("manually_controlled", "False")
            ),
            controlled_by_id=self._parse_int(data.get("controlled_by_id")),
            control_reason=data.get("control_reason", ""),
            manual_override_expires_at=self._parse_datetime(
                data.get("manual_override_expires_at")
            ),
            half_open_request_count=self._parse_int(
                data.get("half_open_request_count", 0)
            ),
            metadata=metadata if isinstance(metadata, dict) else {},
            created_at=self._parse_datetime(data.get("created_at")),
            updated_at=self._parse_datetime(data.get("updated_at")),
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not value or value == "":
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_int(value: Any) -> int:
        """Parse integer value."""
        if value is None or value == "":
            return 0
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        """Parse boolean value."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    # =========================================================================
    # Additional Interface Methods (Aliases and Required Abstract Methods)
    # =========================================================================

    def get_by_service_name(self, service_name: str) -> CircuitBreakerStateData | None:
        """
        Get circuit breaker state by service name (alias for get_state).

        Args:
            service_name: Service identifier

        Returns:
            CircuitBreakerStateData if exists, None otherwise
        """
        return self.get_state(service_name)

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """
        Record a failure and return updated state.

        Args:
            service_name: Service identifier

        Returns:
            Updated CircuitBreakerStateData
        """
        self.increment_failure(service_name)
        return self.get_or_create(service_name)

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """
        Record a success and return updated state.

        Args:
            service_name: Service identifier

        Returns:
            Updated CircuitBreakerStateData
        """
        self.increment_success(service_name)
        return self.get_or_create(service_name)

    def clear_manual_control(
        self, service_name: str, preserve_reason: bool = False
    ) -> bool:
        """
        Clear manual control from circuit breaker.

        Args:
            service_name: Service identifier
            preserve_reason: If True, keep existing control_reason

        Returns:
            True on success
        """
        now = datetime.now(timezone.utc)

        updates = {
            "manually_controlled": "False",
            "controlled_by_id": "",
            "manual_override_expires_at": "",
            "updated_at": now.isoformat(),
        }

        if not preserve_reason:
            updates["control_reason"] = ""

        return self._backend.hset(self._make_key(service_name), updates)

    def reset(self, service_name: str) -> bool:
        """
        Reset circuit breaker to initial closed state (alias for reset_state).

        Args:
            service_name: Service identifier

        Returns:
            True on success
        """
        return self.reset_state(service_name)

    # =========================================================================
    # Atomic Operations for Concurrency Safety
    # =========================================================================

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: int | None = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """
        Atomically force open a circuit breaker.

        Note: Redis provides atomicity at command level. For full atomic
        operations, consider using Lua scripts.

        Args:
            service_name: Service identifier
            reason: Reason for opening
            controlled_by_id: User ID who initiated
            ttl_minutes: TTL for manual override

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        # Get current state
        current = self.get_or_create(service_name)
        previous_state = current.state
        new_state = CircuitBreakerStateEnum.OPEN.value

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)

        updates = {
            "state": new_state,
            "manually_controlled": "True",
            "control_reason": reason,
            "opened_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "manual_override_expires_at": expires_at.isoformat(),
        }

        if controlled_by_id is not None:
            updates["controlled_by_id"] = str(controlled_by_id)

        success = self._backend.hset(self._make_key(service_name), updates)

        if success:
            self._record_history(
                service_name, new_state, now, note=f"force_open: {reason}"
            )

        return (success, previous_state, new_state)

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: int | None = None,
    ) -> tuple[bool, str, str]:
        """
        Atomically force close a circuit breaker.

        Args:
            service_name: Service identifier
            reason: Reason for closing
            controlled_by_id: User ID who initiated

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        current = self.get_or_create(service_name)
        previous_state = current.state
        new_state = CircuitBreakerStateEnum.CLOSED.value

        now = datetime.now(timezone.utc)

        updates = {
            "state": new_state,
            "failure_count": "0",
            "success_count": "0",
            "half_open_request_count": "0",
            "manually_controlled": "True",
            "control_reason": reason,
            "opened_at": "",
            "updated_at": now.isoformat(),
        }

        if controlled_by_id is not None:
            updates["controlled_by_id"] = str(controlled_by_id)

        success = self._backend.hset(self._make_key(service_name), updates)

        if success:
            self._record_history(
                service_name, new_state, now, note=f"force_close: {reason}"
            )

        return (success, previous_state, new_state)

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: int | None = None,
    ) -> tuple[bool, str, str]:
        """
        Atomically reset a circuit breaker to initial state.

        Args:
            service_name: Service identifier
            reason: Reason for reset
            controlled_by_id: User ID who initiated

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        current = self.get_or_create(service_name)
        previous_state = current.state
        new_state = CircuitBreakerStateEnum.CLOSED.value

        now = datetime.now(timezone.utc)

        updates = {
            "state": new_state,
            "failure_count": "0",
            "success_count": "0",
            "half_open_request_count": "0",
            "manually_controlled": "False",
            "control_reason": "",
            "controlled_by_id": "",
            "opened_at": "",
            "manual_override_expires_at": "",
            "updated_at": now.isoformat(),
        }

        success = self._backend.hset(self._make_key(service_name), updates)

        if success:
            self._record_history(
                service_name, new_state, now, note=f"atomic_reset: {reason}"
            )

        return (success, previous_state, new_state)


# Singleton
_redis_cb_repo: RedisCircuitBreakerStateRepository | None = None


def get_redis_circuit_breaker_repo(
    backend: ResilientStorageBackend | None = None,
) -> RedisCircuitBreakerStateRepository:
    """
    Get singleton Redis Circuit Breaker Repository.

    Args:
        backend: ResilientStorageBackend (uses default if not provided)

    Returns:
        RedisCircuitBreakerStateRepository instance
    """
    global _redis_cb_repo

    if _redis_cb_repo is None:
        if backend is None:
            from selfhealing.adapters.resilient.backend import get_storage_backend

            backend = get_storage_backend()
        _redis_cb_repo = RedisCircuitBreakerStateRepository(backend)

    return _redis_cb_repo
