"""
Self-Healing Services - Public API
===================================

Version: 2.0.0 (Breaking Change)
Updated: 2026-01-04

이 모듈은 Self-Healing 시스템의 **핵심 Public API**만 노출합니다.
기업 사용자는 이 경로를 통해 모든 주요 기능에 접근할 수 있습니다.

Usage:
    from selfhealing.services import (
        get_circuit_breaker_service,
        get_dlq_service,
        get_replay_service,
        DLQService,
        record_sla_breach,
    )

    # Circuit Breaker 사용
    cb = get_circuit_breaker_service("payment_gateway")
    if cb.is_open():
        return handle_fallback()

    # DLQ 사용
    dlq = get_dlq_service()
    dlq.push(failed_operation)

=============================================================================
MIGRATION GUIDE (v1.x → v2.0.0)
=============================================================================

v2.0.0에서 62개의 export가 15개 핵심 API로 축소되었습니다.
삭제된 심볼은 직접 import 경로를 사용하세요.

변경 전 (v1.x):
    from selfhealing.services import RetryHandler, RetryConfig
    from selfhealing.services import IdempotencyService
    from selfhealing.services import ControlAPIService

변경 후 (v2.0.0):
    from selfhealing.services.retry_handler import RetryHandler, RetryConfig
    from selfhealing.services.idempotency_service import IdempotencyService
    from selfhealing.services.control_api_service import ControlAPIService

삭제된 심볼과 새 경로:

    # Retry (→ retry_handler.py)
    RetryHandler         → from selfhealing.services.retry_handler import RetryHandler
    RetryConfig          → from selfhealing.services.retry_handler import RetryConfig
    RetryResult          → from selfhealing.services.retry_handler import RetryResult
    RetryAction          → from selfhealing.services.retry_handler import RetryAction
    MaxRetriesExceededError → from selfhealing.services.retry_handler import MaxRetriesExceededError

    # Idempotency (→ idempotency_service.py)
    IdempotencyService   → from selfhealing.services.idempotency_service import IdempotencyService
    IdempotencyKey       → from selfhealing.services.idempotency_service import IdempotencyKey
    IdempotencyDomain    → from selfhealing.services.idempotency_service import IdempotencyDomain
    get_idempotency_service → from selfhealing.services.idempotency_service import get_idempotency_service

    # Control API (→ control_api_service.py)
    ControlAPIService    → from selfhealing.services.control_api_service import ControlAPIService
    ControlRequest       → from selfhealing.services.control_api_service import ControlRequest
    ControlResponse      → from selfhealing.services.control_api_service import ControlResponse

    # Circuit Breaker 상세 (→ circuit_breaker_service.py)
    CircuitBreakerConfig → from selfhealing.services.circuit_breaker_service import CircuitBreakerConfig
    CircuitBreakerResult → from selfhealing.services.circuit_breaker_service import CircuitBreakerResult
    CircuitState         → from selfhealing.services.circuit_breaker_service import CircuitState
    should_allow_request → from selfhealing.services.circuit_breaker_service import should_allow_request
    force_open_circuit   → from selfhealing.services.circuit_breaker_service import force_open_circuit
    force_close_circuit  → from selfhealing.services.circuit_breaker_service import force_close_circuit

    # Rate Limit (→ circuit_breaker_service.py)
    RateLimitTracker     → from selfhealing.services.circuit_breaker_service import RateLimitTracker
    get_rate_limit_tracker → from selfhealing.services.circuit_breaker_service import get_rate_limit_tracker
    record_rate_limit    → from selfhealing.services.circuit_breaker_service import record_rate_limit
    should_allow_with_protection → from selfhealing.services.circuit_breaker_service import should_allow_with_protection
    get_protection_status → from selfhealing.services.circuit_breaker_service import get_protection_status

    # Metrics 상세 (→ metrics/ subpackage)
    record_dlq_item_created → from selfhealing.services.metrics.recorders import record_dlq_item_created
    record_retry_attempt → from selfhealing.services.metrics.recorders import record_retry_attempt
    record_recovery_time → from selfhealing.services.metrics.recorders import record_recovery_time
    record_circuit_breaker_state_change → from selfhealing.services.metrics.recorders import record_circuit_breaker_state_change
    record_circuit_breaker_open_duration → from selfhealing.services.metrics.recorders import record_circuit_breaker_open_duration
    record_replay_attempt → from selfhealing.services.metrics.recorders import record_replay_attempt
    track_recovery_time  → from selfhealing.services.metrics.updaters import track_recovery_time
    DEFAULT_DOMAINS      → from selfhealing.services.metrics.registry import DEFAULT_DOMAINS

    # Security (→ security_*.py)
    SecurityViolationResult → from selfhealing.services.security import SecurityViolationResult
    SecurityConfig       → from selfhealing.services.security import SecurityConfig
    ViolationType        → from selfhealing.services.security import ViolationType
    Severity             → from selfhealing.services.security import Severity
    SEVERITY_BY_VIOLATION_TYPE → from selfhealing.services.security import SEVERITY_BY_VIOLATION_TYPE
    get_security_violation_service → from selfhealing.services.security import get_security_violation_service
    handle_security_violation → from selfhealing.services.security import handle_security_violation
    SecurityNotificationService → from selfhealing.services.security_notification import SecurityNotificationService
    SecurityNotificationResult → from selfhealing.services.security_notification import SecurityNotificationResult
    NotificationResult   → from selfhealing.services.security_notification import NotificationResult
    NotificationConfig   → from selfhealing.services.security_notification import NotificationConfig
    NotificationChannel  → from selfhealing.services.security_notification import NotificationChannel
    get_security_notification_service → from selfhealing.services.security_notification import get_security_notification_service
    notify_security_incident → from selfhealing.services.security_notification import notify_security_incident

    # DLQ 상세 (→ dlq_service.py)
    DLQConfig            → from selfhealing.services.dlq_service import DLQConfig
    DLQEntryResult       → from selfhealing.services.dlq_service import DLQEntryResult

    # Replay 상세 (→ replay_service.py)
    ReplayService        → from selfhealing.services.replay_service import ReplayService
    ReplayResult         → from selfhealing.services.replay_service import ReplayResult
    BatchReplayResult    → from selfhealing.services.replay_service import BatchReplayResult

=============================================================================
"""

# =============================================================================
# PUBLIC API - 핵심 15개만 노출
# =============================================================================

# --- Configuration ---
from ..core.config import get_sla_thresholds

# --- Core Services (사용 빈도 순) ---
from .circuit_breaker_service import (  # Backward compatibility - commonly used in tests; Convenience functions; Rate limit tracking
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitBreakerService,
    CircuitState,
    RateLimitTracker,
    force_close_circuit,
    force_open_circuit,
    get_circuit_breaker_service,
    get_rate_limit_tracker,
    should_allow_request,
)

# --- Backward Compatibility - Control API ---
from .control_api_service import (
    ControlAPIService,
    ControlRequest,
    ControlResponse,
)
from .dlq_service import (  # Backward compatibility - commonly used in tests
    DLQConfig,
    DLQEntryResult,
    DLQService,
    get_dlq_service,
    store_to_dlq,
)

# --- Backward Compatibility - Idempotency ---
from .idempotency_service import (
    IdempotencyDomain,
    IdempotencyKey,
    IdempotencyService,
    get_idempotency_service,
)
from .metrics.alerting_rules import ALERTING_RULES

# --- Metrics (핵심만) ---
from .metrics.recorders import record_sla_breach
from .metrics.registry import DEFAULT_DOMAINS
from .metrics.updaters import collect_all_metrics
from .replay_service import (
    BatchReplayResult,
    ReplayResult,
    ReplayService,
    get_replay_service,
)

# --- Backward Compatibility - Retry ---
from .retry_handler import (
    MaxRetriesExceededError,
    RetryAction,
    RetryConfig,
    RetryHandler,
    RetryResult,
)

# --- Backward Compatibility - Security ---
from .security import (
    SEVERITY_BY_VIOLATION_TYPE,
    SecurityConfig,
    SecurityViolationResult,
    SecurityViolationService,
    Severity,
    ViolationType,
    get_security_violation_service,
    handle_security_violation,
)

# --- Backward Compatibility - Security Notification ---
from .security_notification import (
    NotificationChannel,
    NotificationConfig,
    NotificationResult,
    SecurityNotificationResult,
    SecurityNotificationService,
    get_security_notification_service,
    notify_security_incident,
)

# Alias for backward compatibility
DOMAINS = DEFAULT_DOMAINS


# =============================================================================
# __all__ - IDE 인텔리센스 최적화
# =============================================================================

__all__ = [
    # === Core Service Getters (가장 많이 사용) ===
    "get_circuit_breaker_service",
    "get_dlq_service",
    "get_replay_service",
    "get_sla_thresholds",
    # === Core Service Classes ===
    "CircuitBreakerService",
    "DLQService",
    "ReplayService",
    "BatchReplayResult",
    # === Backward Compatibility - Circuit Breaker ===
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    "RateLimitTracker",
    "get_rate_limit_tracker",
    # === Backward Compatibility - DLQ ===
    "DLQConfig",
    "DLQEntryResult",
    "ReplayResult",
    "store_to_dlq",
    # === Backward Compatibility - Idempotency ===
    "IdempotencyKey",
    "IdempotencyService",
    "IdempotencyDomain",
    "get_idempotency_service",
    # === Backward Compatibility - Retry ===
    "RetryHandler",
    "RetryConfig",
    "RetryResult",
    "RetryAction",
    "MaxRetriesExceededError",
    # === Backward Compatibility - Control API ===
    "ControlAPIService",
    "ControlRequest",
    "ControlResponse",
    # === Backward Compatibility - Security ===
    "SecurityViolationService",
    "SecurityViolationResult",
    "SecurityConfig",
    "ViolationType",
    "Severity",
    "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service",
    "handle_security_violation",
    # === Backward Compatibility - Security Notification ===
    "SecurityNotificationService",
    "SecurityNotificationResult",
    "NotificationConfig",
    "NotificationChannel",
    "NotificationResult",
    "get_security_notification_service",
    "notify_security_incident",
    # === Metrics ===
    "record_sla_breach",
    "collect_all_metrics",
    "DOMAINS",
    "ALERTING_RULES",
]
