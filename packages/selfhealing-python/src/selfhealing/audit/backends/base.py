"""
Audit Backend Interfaces.

Defines the abstract interface for audit log backends.
Allows pluggable storage (local file, cloud services, WORM storage).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class BackendStatus(Enum):
    """Status of an audit backend."""

    ACTIVE = "active"
    DEGRADED = "degraded"  # Partial functionality
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


@dataclass
class BackendHealth:
    """Health status of a backend."""

    status: BackendStatus
    message: str
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    retry_count: int = 0


class AuditBackend(ABC):
    """
    Abstract base class for audit log backends.

    Implement this interface to add new storage backends
    (e.g., CloudWatch, Datadog, S3 WORM, remote audit server).

    All backends must be:
    - Thread-safe
    - Fail-safe (never throw exceptions that break logging)
    - Self-healing (retry on transient failures)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the backend name for logging/debugging."""
        pass

    @abstractmethod
    def write(self, entry: Dict[str, Any]) -> bool:
        """
        Write an audit log entry.

        Args:
            entry: The audit log entry to write

        Returns:
            True if successful, False otherwise

        Note:
            This method must never raise exceptions.
            On failure, log the error and return False.
        """
        pass

    @abstractmethod
    def health_check(self) -> BackendHealth:
        """
        Check the health of this backend.

        Returns:
            BackendHealth with current status
        """
        pass

    def flush(self) -> bool:
        """
        Flush any buffered entries.

        Override if the backend uses buffering.

        Returns:
            True if successful
        """
        return True

    def close(self) -> None:
        """
        Close the backend and release resources.

        Override if the backend needs cleanup.
        """
        pass

    def query(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        config_type: Optional[str] = None,
        user: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Query audit logs (optional).

        Not all backends support querying.
        Returns empty list by default.

        Args:
            start_time: Filter by start time
            end_time: Filter by end time
            config_type: Filter by config type
            user: Filter by user
            limit: Maximum entries to return

        Returns:
            List of matching audit entries
        """
        return []


class AsyncAuditBackend(AuditBackend):
    """
    Async-capable audit backend.

    Extend this for backends that benefit from async I/O
    (e.g., HTTP-based cloud services).
    """

    @abstractmethod
    async def write_async(self, entry: Dict[str, Any]) -> bool:
        """
        Async version of write.

        Args:
            entry: The audit log entry to write

        Returns:
            True if successful
        """
        pass

    async def flush_async(self) -> bool:
        """Async version of flush."""
        return True

    async def close_async(self) -> None:
        """Async version of close."""
        pass


class BufferedBackend(AuditBackend):
    """
    Mixin for backends that buffer writes.

    Provides automatic batching and periodic flushing.
    """

    def __init__(self, buffer_size: int = 100, flush_interval_seconds: float = 5.0):
        """
        Initialize buffered backend.

        Args:
            buffer_size: Number of entries to buffer before auto-flush
            flush_interval_seconds: Seconds between auto-flushes
        """
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval_seconds

    def _add_to_buffer(self, entry: Dict[str, Any]) -> bool:
        """Add entry to buffer, flush if needed."""
        self._buffer.append(entry)

        if len(self._buffer) >= self._buffer_size:
            return self.flush()

        return True

    @abstractmethod
    def _flush_buffer(self, entries: List[Dict[str, Any]]) -> bool:
        """Flush buffered entries to storage."""
        pass

    def flush(self) -> bool:
        """Flush buffer to storage."""
        if not self._buffer:
            return True

        entries = self._buffer.copy()
        self._buffer.clear()

        return self._flush_buffer(entries)


class CompositeBackend(AuditBackend):
    """
    Backend that writes to multiple backends with resilience features.

    Features:
    - Circuit breakers per backend to prevent cascading failures
    - Automatic degraded mode when external backends fail
    - Metrics collection for monitoring
    - Syslog fallback for critical events
    """

    def __init__(
        self,
        backends: List[AuditBackend],
        require_all: bool = False,
        enable_circuit_breaker: bool = True,
        enable_metrics: bool = True,
    ):
        """
        Initialize composite backend.

        Args:
            backends: List of backends to write to
            require_all: If True, fail if any backend fails
            enable_circuit_breaker: Enable circuit breakers per backend
            enable_metrics: Enable Prometheus metrics collection
        """
        self._backends = backends
        self._require_all = require_all
        self._enable_circuit_breaker = enable_circuit_breaker
        self._enable_metrics = enable_metrics

        # Lazy imports to avoid circular dependencies
        self._circuit_registry = None
        self._metrics = None
        self._degraded_manager = None
        self._syslog = None

    def _get_circuit_registry(self):
        """Lazy load circuit breaker registry."""
        if self._circuit_registry is None and self._enable_circuit_breaker:
            from selfhealing.audit.resilience import CircuitBreakerRegistry
            self._circuit_registry = CircuitBreakerRegistry.get_instance()
        return self._circuit_registry

    def _get_metrics(self):
        """Lazy load metrics."""
        if self._metrics is None and self._enable_metrics:
            from selfhealing.audit.resilience import AuditMetrics
            self._metrics = AuditMetrics.get_instance()
        return self._metrics

    def _get_degraded_manager(self):
        """Lazy load degraded mode manager."""
        if self._degraded_manager is None:
            from selfhealing.audit.resilience import DegradedModeManager
            self._degraded_manager = DegradedModeManager.get_instance()
        return self._degraded_manager

    def _get_syslog(self):
        """Lazy load syslog fallback."""
        if self._syslog is None:
            from selfhealing.audit.resilience import SyslogFallback
            self._syslog = SyslogFallback.get_instance()
        return self._syslog

    @property
    def name(self) -> str:
        """Return composite name."""
        names = [b.name for b in self._backends]
        return f"Composite({', '.join(names)})"

    def _check_circuit_breaker(self, backend_name: str) -> tuple[bool, Any]:
        """Check if circuit breaker allows execution. Returns (can_execute, circuit_breaker)."""
        registry = self._get_circuit_registry()
        if not registry:
            return True, None
        cb = registry.get_or_create(backend_name)
        return cb.can_execute(), cb

    def _record_metrics(self, backend_name: str, success: bool, duration_ms: float = 0, failure_type: str = None):
        """Record metrics for a backend operation."""
        if not self._enable_metrics:
            return
        metrics = self._get_metrics()
        if not metrics:
            return
        metrics.record_write(backend_name, success=success, duration_ms=duration_ms)
        if failure_type:
            metrics.record_failure(backend_name, failure_type)

    def _update_circuit_state(self, backend_name: str, success: bool, cb: Any) -> None:
        """Update circuit breaker state and record metrics."""
        if not cb:
            return
        if success:
            cb.record_success()
        else:
            cb.record_failure()
            # Check if circuit just opened
            if cb.state.value == "open":
                syslog = self._get_syslog()
                if syslog:
                    syslog.log_circuit_open(backend_name)
        # Update circuit state metric
        if self._enable_metrics:
            metrics = self._get_metrics()
            if metrics:
                metrics.set_circuit_state(backend_name, cb.state.value)

    def _write_single_backend(self, backend, entry: Dict[str, Any]) -> bool:
        """Write to a single backend with circuit breaker and metrics."""
        import time
        backend_name = backend.name
        start_time = time.time()

        # Check circuit breaker
        can_execute, cb = self._check_circuit_breaker(backend_name)
        if not can_execute:
            self._record_metrics(backend_name, success=False)
            if cb:
                metrics = self._get_metrics()
                if metrics:
                    metrics.set_circuit_state(backend_name, cb.state.value)
            return False

        try:
            result = backend.write(entry)
            duration_ms = (time.time() - start_time) * 1000
            self._record_metrics(backend_name, success=result, duration_ms=duration_ms)
            self._update_circuit_state(backend_name, result, cb)
            if not result:
                self._record_metrics(backend_name, success=False, failure_type="write_failed")
            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self._record_metrics(backend_name, success=False, duration_ms=duration_ms, failure_type=type(e).__name__)
            if self._enable_circuit_breaker:
                registry = self._get_circuit_registry()
                if registry:
                    cb = registry.get_or_create(backend_name)
                    self._update_circuit_state(backend_name, False, cb)
            return False

    def _handle_all_failed(self, entry: Dict[str, Any]) -> None:
        """Handle the case when all backends fail."""
        syslog = self._get_syslog()
        if syslog:
            syslog.log_backend_failure("ALL", "All backends failed to write")

        # Write to stderr as last resort
        import sys
        import json
        try:
            print(f"AUDIT_FALLBACK: {json.dumps(entry, default=str)}", file=sys.stderr, flush=True)
        except Exception:
            pass

    def write(self, entry: Dict[str, Any]) -> bool:
        """Write to all backends with circuit breaker protection."""
        results = []
        all_failed = True

        for backend in self._backends:
            result = self._write_single_backend(backend, entry)
            results.append(result)
            if result:
                all_failed = False

        # Check if all backends failed
        if all_failed and results:
            self._handle_all_failed(entry)

        # Update degraded mode status
        degraded_manager = self._get_degraded_manager()
        if degraded_manager:
            degraded_manager.check_and_update()

        if self._require_all:
            return all(results)
        return any(results) if results else False

    def health_check(self) -> BackendHealth:
        """Check health of all backends including circuit states."""
        healths = [b.health_check() for b in self._backends]

        active_count = sum(1 for h in healths if h.status == BackendStatus.ACTIVE)

        # Also check circuit breakers
        registry = self._get_circuit_registry()
        if registry:
            open_circuits = registry.get_open_circuits()
            if open_circuits:
                return BackendHealth(
                    status=BackendStatus.DEGRADED,
                    message=f"Circuit breakers open: {', '.join(open_circuits)}",
                )

        if active_count == len(healths):
            return BackendHealth(status=BackendStatus.ACTIVE, message="All backends healthy")
        elif active_count > 0:
            return BackendHealth(
                status=BackendStatus.DEGRADED,
                message=f"{active_count}/{len(healths)} backends healthy",
            )
        else:
            return BackendHealth(status=BackendStatus.UNAVAILABLE, message="All backends unavailable")

    def flush(self) -> bool:
        """Flush all backends."""
        return all(b.flush() for b in self._backends)

    def close(self) -> None:
        """Close all backends."""
        for backend in self._backends:
            backend.close()
