"""
Audit Resilience Module.

Provides fault-tolerant mechanisms for audit logging:
- Circuit Breaker: Prevents cascading failures from slow/dead backends
- Degraded Mode: Automatic fallback to local logging
- Syslog Fallback: OS-level logging for critical events
- Metrics: Prometheus-compatible monitoring
"""

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Circuit Breaker
# =============================================================================


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject all calls
    HALF_OPEN = "half_open"  # Testing if backend recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 3  # Failures before opening
    success_threshold: int = 2  # Successes to close from half-open
    timeout_seconds: float = 30.0  # Time before trying half-open
    call_timeout_seconds: float = 5.0  # Timeout for individual calls


@dataclass
class CircuitBreakerState:
    """Current state of a circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_state_change: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreaker:
    """
    Circuit Breaker for audit backends.

    Prevents slow/failing external services from blocking the main application.

    States:
    - CLOSED: Normal operation, calls go through
    - OPEN: Backend is failing, calls are rejected immediately
    - HALF_OPEN: Testing if backend recovered with limited calls

    Usage:
        cb = CircuitBreaker("cloudwatch")

        if cb.can_execute():
            try:
                result = external_call()
                cb.record_success()
            except Exception:
                cb.record_failure()
        else:
            # Use fallback
            local_log(entry)
    """

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Name of the protected resource
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._check_timeout()
            return self._state.state

    def can_execute(self) -> bool:
        """
        Check if a call can be executed.

        Returns:
            True if call should proceed, False if circuit is open
        """
        with self._lock:
            self._check_timeout()

            if self._state.state == CircuitState.CLOSED:
                return True
            elif self._state.state == CircuitState.HALF_OPEN:
                # Allow limited calls in half-open
                return True
            else:  # OPEN
                return False

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            self._state.total_successes += 1

            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            elif self._state.state == CircuitState.CLOSED:
                # Reset failure count on success
                self._state.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            self._state.failure_count += 1
            self._state.total_failures += 1
            self._state.last_failure_time = datetime.now(timezone.utc)

            if self._state.state == CircuitState.HALF_OPEN:
                # Any failure in half-open reopens circuit
                self._transition_to(CircuitState.OPEN)
            elif self._state.state == CircuitState.CLOSED:
                if self._state.failure_count >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def _check_timeout(self) -> None:
        """Check if open circuit should transition to half-open."""
        if self._state.state == CircuitState.OPEN:
            time_since_change = (
                datetime.now(timezone.utc) - self._state.last_state_change
            ).total_seconds()

            if time_since_change >= self.config.timeout_seconds:
                self._transition_to(CircuitState.HALF_OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state.state
        self._state.state = new_state
        self._state.last_state_change = datetime.now(timezone.utc)

        if new_state == CircuitState.CLOSED:
            self._state.failure_count = 0
            self._state.success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._state.success_count = 0

        logger.warning(
            f"[CircuitBreaker:{self.name}] State transition: {old_state.value} -> {new_state.value}"
        )

    def reset(self) -> None:
        """Manually reset circuit breaker to closed state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            logger.info(f"[CircuitBreaker:{self.name}] Manually reset")

    def force_open(self) -> None:
        """Manually open circuit breaker."""
        with self._lock:
            self._transition_to(CircuitState.OPEN)
            logger.warning(f"[CircuitBreaker:{self.name}] Manually opened")

    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.state.value,
                "failure_count": self._state.failure_count,
                "success_count": self._state.success_count,
                "total_failures": self._state.total_failures,
                "total_successes": self._state.total_successes,
                "last_failure_time": (
                    self._state.last_failure_time.isoformat()
                    if self._state.last_failure_time
                    else None
                ),
                "last_state_change": self._state.last_state_change.isoformat(),
                "config": {
                    "failure_threshold": self.config.failure_threshold,
                    "success_threshold": self.config.success_threshold,
                    "timeout_seconds": self.config.timeout_seconds,
                },
            }


# =============================================================================
# Circuit Breaker Registry
# =============================================================================


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers.

    Provides centralized access to all audit backend circuit breakers.
    """

    _instance: Optional["CircuitBreakerRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._registry_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "CircuitBreakerRegistry":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_or_create(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> CircuitBreaker:
        """Get existing or create new circuit breaker."""
        with self._registry_lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]

    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        with self._registry_lock:
            return self._breakers.get(name)

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all circuit breakers."""
        with self._registry_lock:
            return {name: cb.get_stats() for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._registry_lock:
            for cb in self._breakers.values():
                cb.reset()

    def get_open_circuits(self) -> List[str]:
        """Get names of all open circuits."""
        with self._registry_lock:
            return [
                name
                for name, cb in self._breakers.items()
                if cb.state == CircuitState.OPEN
            ]


# =============================================================================
# Audit Metrics (Prometheus-compatible)
# =============================================================================


class AuditMetrics:
    """
    Prometheus-compatible metrics for audit logging.

    Tracks:
    - audit_write_total: Total write attempts (by backend, status)
    - audit_failure_total: Total failures (by backend, error_type)
    - audit_circuit_state: Current circuit breaker state (by backend)
    - audit_degraded_mode: Whether system is in degraded mode

    Usage:
        metrics = AuditMetrics.get_instance()
        metrics.record_write("LocalFile", success=True)
        metrics.record_failure("CloudWatch", "timeout")
    """

    _instance: Optional["AuditMetrics"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._metrics_lock = threading.RLock()

        # Counters
        self._write_total: Dict[str, Dict[str, int]] = {}  # {backend: {status: count}}
        self._failure_total: Dict[str, Dict[str, int]] = {}  # {backend: {error_type: count}}

        # Gauges
        self._circuit_states: Dict[str, str] = {}  # {backend: state}
        self._degraded_mode: bool = False
        self._degraded_since: Optional[datetime] = None

        # Histogram-like data (simplified)
        self._write_durations: Dict[str, List[float]] = {}  # {backend: [durations]}
        
        # WAL 관련 메트릭 (Phase 0: 누락 0 보장)
        self._wal_writes_total: int = 0
        self._wal_write_failures_total: int = 0
        self._central_writes_total: int = 0
        self._sync_lag_entries: int = 0
        self._reconcile_missing_total: int = 0

    @classmethod
    def get_instance(cls) -> "AuditMetrics":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    # =========================================================================
    # WAL 관련 메트릭 (Phase 0: 누락 0 보장)
    # =========================================================================
    
    def record_wal_write(self, success: bool = True) -> None:
        """WAL 기록 메트릭."""
        with self._metrics_lock:
            if success:
                self._wal_writes_total += 1
            else:
                self._wal_write_failures_total += 1
    
    def record_central_write(self, count: int = 1) -> None:
        """중앙 저장소 기록 메트릭."""
        with self._metrics_lock:
            self._central_writes_total += count
    
    def set_sync_lag(self, entries: int) -> None:
        """동기화 지연 엔트리 수 설정."""
        with self._metrics_lock:
            self._sync_lag_entries = entries
    
    def record_reconcile_missing(self, count: int) -> None:
        """Reconciler가 발견한 누락 수 기록."""
        with self._metrics_lock:
            self._reconcile_missing_total += count
    
    def get_wal_metrics(self) -> Dict[str, Any]:
        """WAL 관련 메트릭 조회."""
        with self._metrics_lock:
            return {
                "audit_wal_writes_total": self._wal_writes_total,
                "audit_wal_write_failures_total": self._wal_write_failures_total,
                "audit_central_writes_total": self._central_writes_total,
                "audit_sync_lag_entries": self._sync_lag_entries,
                "audit_reconcile_missing_total": self._reconcile_missing_total,
            }

    def record_write(self, backend: str, success: bool, duration_ms: float = 0) -> None:
        """Record a write attempt."""
        with self._metrics_lock:
            status = "success" if success else "failure"

            if backend not in self._write_total:
                self._write_total[backend] = {"success": 0, "failure": 0}
            self._write_total[backend][status] += 1

            if duration_ms > 0:
                if backend not in self._write_durations:
                    self._write_durations[backend] = []
                # Keep last 100 durations
                self._write_durations[backend].append(duration_ms)
                if len(self._write_durations[backend]) > 100:
                    self._write_durations[backend] = self._write_durations[backend][-100:]

    def record_failure(self, backend: str, error_type: str) -> None:
        """Record a failure with error type."""
        with self._metrics_lock:
            if backend not in self._failure_total:
                self._failure_total[backend] = {}
            if error_type not in self._failure_total[backend]:
                self._failure_total[backend][error_type] = 0
            self._failure_total[backend][error_type] += 1

    def set_circuit_state(self, backend: str, state: str) -> None:
        """Update circuit breaker state."""
        with self._metrics_lock:
            self._circuit_states[backend] = state

    def set_degraded_mode(self, degraded: bool) -> None:
        """Set degraded mode status."""
        with self._metrics_lock:
            was_degraded = self._degraded_mode
            self._degraded_mode = degraded

            if degraded and not was_degraded:
                self._degraded_since = datetime.now(timezone.utc)
                logger.warning("[AuditMetrics] Entered DEGRADED MODE")
            elif not degraded and was_degraded:
                self._degraded_since = None
                logger.info("[AuditMetrics] Exited DEGRADED MODE")

    def is_degraded(self) -> bool:
        """Check if in degraded mode."""
        with self._metrics_lock:
            return self._degraded_mode

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get all metrics in Prometheus-compatible format.

        Returns dict that can be exposed via /metrics endpoint.
        """
        with self._metrics_lock:
            metrics = {
                "audit_write_total": self._write_total.copy(),
                "audit_failure_total": self._failure_total.copy(),
                "audit_circuit_state": self._circuit_states.copy(),
                "audit_degraded_mode": 1 if self._degraded_mode else 0,
                "audit_degraded_since": (
                    self._degraded_since.isoformat() if self._degraded_since else None
                ),
                # WAL 관련 메트릭 (Phase 0: 누락 0 보장)
                "audit_wal_writes_total": self._wal_writes_total,
                "audit_wal_write_failures_total": self._wal_write_failures_total,
                "audit_central_writes_total": self._central_writes_total,
                "audit_sync_lag_entries": self._sync_lag_entries,
                "audit_reconcile_missing_total": self._reconcile_missing_total,
            }

            # Add duration stats
            duration_stats = {}
            for backend, durations in self._write_durations.items():
                if durations:
                    duration_stats[backend] = {
                        "avg_ms": sum(durations) / len(durations),
                        "max_ms": max(durations),
                        "min_ms": min(durations),
                        "count": len(durations),
                    }
            metrics["audit_write_duration"] = duration_stats

            return metrics

    def get_prometheus_format(self) -> str:
        """
        Get metrics in Prometheus text exposition format.

        Can be directly served at /metrics endpoint.
        """
        lines = []
        metrics = self.get_metrics()

        # Write totals
        lines.append("# HELP audit_write_total Total audit write attempts")
        lines.append("# TYPE audit_write_total counter")
        for backend, statuses in metrics["audit_write_total"].items():
            for status, count in statuses.items():
                lines.append(f'audit_write_total{{backend="{backend}",status="{status}"}} {count}')

        # Failure totals
        lines.append("# HELP audit_failure_total Total audit failures by type")
        lines.append("# TYPE audit_failure_total counter")
        for backend, errors in metrics["audit_failure_total"].items():
            for error_type, count in errors.items():
                lines.append(f'audit_failure_total{{backend="{backend}",error_type="{error_type}"}} {count}')

        # Circuit states
        lines.append("# HELP audit_circuit_state Circuit breaker state (0=closed, 1=open, 2=half_open)")
        lines.append("# TYPE audit_circuit_state gauge")
        state_values = {"closed": 0, "open": 1, "half_open": 2}
        for backend, state in metrics["audit_circuit_state"].items():
            value = state_values.get(state, -1)
            lines.append(f'audit_circuit_state{{backend="{backend}"}} {value}')

        # Degraded mode
        lines.append("# HELP audit_degraded_mode Whether audit is in degraded mode")
        lines.append("# TYPE audit_degraded_mode gauge")
        lines.append(f'audit_degraded_mode {metrics["audit_degraded_mode"]}')
        
        # WAL metrics (Phase 0: 누락 0 보장)
        lines.append("# HELP audit_wal_writes_total Total WAL writes")
        lines.append("# TYPE audit_wal_writes_total counter")
        lines.append(f'audit_wal_writes_total {metrics["audit_wal_writes_total"]}')
        
        lines.append("# HELP audit_wal_write_failures_total Total WAL write failures (CRITICAL)")
        lines.append("# TYPE audit_wal_write_failures_total counter")
        lines.append(f'audit_wal_write_failures_total {metrics["audit_wal_write_failures_total"]}')
        
        lines.append("# HELP audit_central_writes_total Total central storage writes")
        lines.append("# TYPE audit_central_writes_total counter")
        lines.append(f'audit_central_writes_total {metrics["audit_central_writes_total"]}')
        
        lines.append("# HELP audit_sync_lag_entries Current WAL to central sync lag")
        lines.append("# TYPE audit_sync_lag_entries gauge")
        lines.append(f'audit_sync_lag_entries {metrics["audit_sync_lag_entries"]}')
        
        lines.append("# HELP audit_reconcile_missing_total Total missing entries found by reconciler")
        lines.append("# TYPE audit_reconcile_missing_total counter")
        lines.append(f'audit_reconcile_missing_total {metrics["audit_reconcile_missing_total"]}')

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        with self._metrics_lock:
            self._write_total.clear()
            self._failure_total.clear()
            self._circuit_states.clear()
            self._write_durations.clear()
            self._degraded_mode = False
            self._degraded_since = None
            # WAL 메트릭 초기화
            self._wal_writes_total = 0
            self._wal_write_failures_total = 0
            self._central_writes_total = 0
            self._sync_lag_entries = 0
            self._reconcile_missing_total = 0


# =============================================================================
# Syslog Fallback
# =============================================================================


class SyslogFallback:
    """
    OS-level syslog fallback for critical audit events.

    When all else fails, critical security events are logged
    directly to the OS syslog. This provides a "last resort"
    audit trail that survives application crashes.

    On Windows, uses Windows Event Log via win32 API or falls back to stderr.
    On Linux/macOS, uses standard syslog.
    """

    # Critical event types that always go to syslog
    CRITICAL_EVENTS = frozenset([
        "security_policy_change",
        "authentication_config_change",
        "encryption_key_change",
        "admin_privilege_change",
        "audit_config_change",
        "circuit_breaker_open",
        "all_backends_failed",
    ])

    _instance: Optional["SyslogFallback"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._syslog_available = False
        self._stderr_fallback = True
        self._init_syslog()

    def _init_syslog(self) -> None:
        """Initialize syslog connection."""
        try:
            if sys.platform == "win32":
                # Windows doesn't have syslog, use stderr
                self._syslog_available = False
                logger.debug("[SyslogFallback] Windows detected, using stderr fallback")
            else:
                import syslog
                syslog.openlog(
                    ident="selfhealing-audit",
                    logoption=syslog.LOG_PID | syslog.LOG_CONS,
                    facility=syslog.LOG_AUTH,  # Security/auth facility
                )
                self._syslog_available = True
                logger.debug("[SyslogFallback] Syslog initialized")
        except Exception as e:
            logger.warning(f"[SyslogFallback] Failed to init syslog: {e}")
            self._syslog_available = False

    @classmethod
    def get_instance(cls) -> "SyslogFallback":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def is_critical_event(self, event_type: str) -> bool:
        """Check if event type is critical."""
        return event_type in self.CRITICAL_EVENTS

    def log_critical(
        self,
        event_type: str,
        message: str,
        config_type: Optional[str] = None,
        user: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Log a critical event to syslog.

        Args:
            event_type: Type of event (should be in CRITICAL_EVENTS)
            message: Human-readable message
            config_type: Configuration type being changed
            user: User making the change
            details: Additional details

        Returns:
            True if logged successfully
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        user_str = user or "system"
        config_str = config_type or "unknown"

        # Format: [AUDIT] type=X config=Y user=Z message
        log_line = (
            f"[AUDIT] timestamp={timestamp} "
            f"type={event_type} config={config_str} "
            f"user={user_str} msg={message}"
        )

        if details:
            # Add key details (limit size)
            for key, value in list(details.items())[:5]:
                log_line += f" {key}={value}"

        return self._write_to_syslog(log_line, event_type)

    def _write_to_syslog(self, message: str, event_type: str) -> bool:
        """Write message to syslog or fallback."""
        success = False

        # Try syslog first
        if self._syslog_available:
            try:
                import syslog

                # Use appropriate priority
                if event_type in ["security_policy_change", "all_backends_failed"]:
                    priority = syslog.LOG_CRIT
                elif event_type in ["authentication_config_change", "encryption_key_change"]:
                    priority = syslog.LOG_WARNING
                else:
                    priority = syslog.LOG_NOTICE

                syslog.syslog(priority, message)
                success = True
            except Exception as e:
                logger.error(f"[SyslogFallback] Syslog write failed: {e}")

        # Always also write to stderr for visibility
        if self._stderr_fallback:
            try:
                print(f"AUDIT_CRITICAL: {message}", file=sys.stderr, flush=True)
                success = True
            except Exception:
                pass

        return success

    def log_backend_failure(self, backend_name: str, error: str) -> None:
        """Log backend failure as critical event."""
        self.log_critical(
            event_type="all_backends_failed",
            message=f"Audit backend {backend_name} failed",
            details={"backend": backend_name, "error": error[:100]},
        )

    def log_circuit_open(self, backend_name: str) -> None:
        """Log circuit breaker opening."""
        self.log_critical(
            event_type="circuit_breaker_open",
            message=f"Circuit breaker opened for {backend_name}",
            details={"backend": backend_name},
        )


# =============================================================================
# Degraded Mode Manager
# =============================================================================


class DegradedModeManager:
    """
    Manages degraded mode operation for audit logging.

    When primary backends fail (circuit breakers open), the system
    automatically switches to degraded mode using only local fallbacks.

    Degraded Mode:
    - LocalFileBackend only
    - stderr output
    - Syslog for critical events
    - Metrics still collected for monitoring
    """

    _instance: Optional["DegradedModeManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._degraded = False
        self._degraded_since: Optional[datetime] = None
        self._degraded_reason: Optional[str] = None
        self._auto_recovery_enabled = True
        self._check_interval_seconds = 60
        self._manager_lock = threading.RLock()

        self._metrics = AuditMetrics.get_instance()
        self._syslog = SyslogFallback.get_instance()
        self._registry = CircuitBreakerRegistry.get_instance()

    @classmethod
    def get_instance(cls) -> "DegradedModeManager":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def is_degraded(self) -> bool:
        """Check if in degraded mode."""
        with self._manager_lock:
            return self._degraded

    def enter_degraded_mode(self, reason: str) -> None:
        """Enter degraded mode."""
        with self._manager_lock:
            if not self._degraded:
                self._degraded = True
                self._degraded_since = datetime.now(timezone.utc)
                self._degraded_reason = reason

                self._metrics.set_degraded_mode(True)

                logger.warning(
                    f"[DegradedMode] ENTERED degraded mode: {reason}"
                )

                # Log to syslog
                self._syslog.log_critical(
                    event_type="audit_config_change",
                    message=f"Audit entered degraded mode: {reason}",
                )

    def exit_degraded_mode(self) -> None:
        """Exit degraded mode."""
        with self._manager_lock:
            if self._degraded:
                duration = None
                if self._degraded_since:
                    duration = (
                        datetime.now(timezone.utc) - self._degraded_since
                    ).total_seconds()

                self._degraded = False
                self._degraded_since = None
                self._degraded_reason = None

                self._metrics.set_degraded_mode(False)

                logger.info(
                    f"[DegradedMode] EXITED degraded mode after {duration:.1f}s"
                    if duration
                    else "[DegradedMode] EXITED degraded mode"
                )

    def check_and_update(self) -> None:
        """
        Check circuit breakers and update degraded mode status.

        Call this periodically or after backend operations.
        """
        open_circuits = self._registry.get_open_circuits()

        with self._manager_lock:
            # Count how many external backends have open circuits
            external_backends = {"CloudWatch", "Datadog", "S3WORM", "RemoteAudit"}
            open_external = [c for c in open_circuits if c in external_backends]

            # Enter degraded if any external backend circuit is open
            if open_external and not self._degraded:
                self.enter_degraded_mode(
                    f"Circuit breakers open: {', '.join(open_external)}"
                )
            # Exit degraded if all circuits closed
            elif not open_external and self._degraded:
                if self._auto_recovery_enabled:
                    self.exit_degraded_mode()

    def get_status(self) -> Dict[str, Any]:
        """Get degraded mode status."""
        with self._manager_lock:
            duration = None
            if self._degraded and self._degraded_since:
                duration = (
                    datetime.now(timezone.utc) - self._degraded_since
                ).total_seconds()

            return {
                "degraded": self._degraded,
                "since": self._degraded_since.isoformat() if self._degraded_since else None,
                "duration_seconds": duration,
                "reason": self._degraded_reason,
                "auto_recovery_enabled": self._auto_recovery_enabled,
                "open_circuits": self._registry.get_open_circuits(),
            }

    def set_auto_recovery(self, enabled: bool) -> None:
        """Enable or disable automatic recovery from degraded mode."""
        with self._manager_lock:
            self._auto_recovery_enabled = enabled

    def force_degraded(self, reason: str = "Manual override") -> None:
        """Manually force degraded mode."""
        self.enter_degraded_mode(reason)
        self._auto_recovery_enabled = False

    def force_normal(self) -> None:
        """Manually force normal mode."""
        self._auto_recovery_enabled = True
        self.exit_degraded_mode()


# =============================================================================
# Convenience Functions
# =============================================================================


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker by name."""
    return CircuitBreakerRegistry.get_instance().get_or_create(name)


def get_audit_metrics() -> AuditMetrics:
    """Get the audit metrics instance."""
    return AuditMetrics.get_instance()


def get_syslog_fallback() -> SyslogFallback:
    """Get the syslog fallback instance."""
    return SyslogFallback.get_instance()


def get_degraded_mode_manager() -> DegradedModeManager:
    """Get the degraded mode manager instance."""
    return DegradedModeManager.get_instance()


def log_critical_to_syslog(
    event_type: str,
    message: str,
    **kwargs,
) -> bool:
    """Log a critical event to syslog."""
    return SyslogFallback.get_instance().log_critical(
        event_type=event_type,
        message=message,
        **kwargs,
    )
