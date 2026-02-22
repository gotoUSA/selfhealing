"""
Celery Signal Hooks for Self-Healing System.

This module provides automatic integration with Celery through signal handlers.
When installed, it automatically:
- Records task failures to Circuit Breaker
- Stores failed tasks in DLQ
- Captures forensic context
- Updates metrics

Usage:
    Simply import and call setup_selfhealing_signals() in your celery app:

    # In your celery.py or __init__.py
    from selfhealing.adapters.celery.signal_hooks import setup_selfhealing_signals
    setup_selfhealing_signals()

    That's it! No code changes required in your tasks.

Configuration via environment variables:
    SELFHEALING_ENABLED=true                    # Enable/disable all hooks
    SELFHEALING_CB_ENABLED=true                 # Enable circuit breaker recording
    SELFHEALING_DLQ_ENABLED=true                # Enable DLQ storage
    SELFHEALING_METRICS_ENABLED=true            # Enable metrics recording
    SELFHEALING_FORENSICS_ENABLED=true          # Enable forensic context capture
    SELFHEALING_CB_FAILURE_THRESHOLD=5          # Failures before CB opens
    SELFHEALING_CB_RECOVERY_TIMEOUT=60          # Seconds before half-open
    SELFHEALING_CB_SUCCESS_THRESHOLD=2          # Successes to close from half-open
    SELFHEALING_TASK_DOMAIN_MAPPING='{"task_name": "domain"}'  # Task to domain mapping
"""

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any

import structlog
from celery.signals import (
    before_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
)

logger = structlog.get_logger()

# =============================================================================
# Configuration
# =============================================================================


def _get_bool_env(key: str, default: bool = True) -> bool:
    """Get boolean from environment variable."""
    value = os.environ.get(key, str(default)).lower()
    return value in ("true", "1", "yes", "on")


def _get_int_env(key: str, default: int) -> int:
    """Get integer from environment variable."""
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _get_task_domain_mapping() -> dict[str, str]:
    """Get task name to domain mapping from environment."""
    mapping_str = os.environ.get("SELFHEALING_TASK_DOMAIN_MAPPING", "{}")
    try:
        return json.loads(mapping_str)
    except json.JSONDecodeError:
        return {}


class SignalHooksConfig:
    """Configuration for signal hooks."""

    def __init__(self):
        self.reload()

    def reload(self):
        """Reload configuration from environment."""
        self.enabled = _get_bool_env("SELFHEALING_ENABLED", True)
        self.cb_enabled = _get_bool_env("SELFHEALING_CB_ENABLED", True)
        self.dlq_enabled = _get_bool_env("SELFHEALING_DLQ_ENABLED", True)
        self.metrics_enabled = _get_bool_env("SELFHEALING_METRICS_ENABLED", True)
        self.forensics_enabled = _get_bool_env("SELFHEALING_FORENSICS_ENABLED", True)

        # Circuit Breaker settings
        self.cb_failure_threshold = _get_int_env("SELFHEALING_CB_FAILURE_THRESHOLD", 5)
        self.cb_recovery_timeout = _get_int_env("SELFHEALING_CB_RECOVERY_TIMEOUT", 60)
        self.cb_success_threshold = _get_int_env("SELFHEALING_CB_SUCCESS_THRESHOLD", 2)

        # Task domain mapping
        self.task_domain_mapping = _get_task_domain_mapping()

        # Domain patterns for task name matching (can be overridden via env)
        self.domain_patterns: dict[str, list[str]] | None = None

        # Excluded tasks (never process these)
        self.excluded_tasks: set[str] = {
            "celery.backend_cleanup",
            "celery.chord_unlock",
            "selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery",
            "selfhealing.adapters.celery.tasks.expire_manual_overrides",
            "selfhealing.adapters.celery.tasks.collect_self_healing_metrics",
            "selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries",
        }


# Global configuration instance
_config = SignalHooksConfig()


def get_signal_hooks_config() -> SignalHooksConfig:
    """Get the current signal hooks configuration."""
    return _config


def reload_signal_hooks_config():
    """Reload signal hooks configuration from environment."""
    _config.reload()


# =============================================================================
# Domain Resolution
# =============================================================================


def _extract_domain_from_task_name(task_name: str) -> str:
    """
    Extract domain from task name.

    Priority:
    1. Explicit mapping in SELFHEALING_TASK_DOMAIN_MAPPING
    2. Task name pattern matching (e.g., 'myapp.tasks.process_order' -> 'order')
    3. First segment of task name
    """
    # Check explicit mapping first
    if task_name in _config.task_domain_mapping:
        return _config.task_domain_mapping[task_name]

    # Pattern matching for common task names
    # NOTE: These are example patterns. Override via config.task_domain_mapping
    # for your specific domain vocabulary.
    name_lower = task_name.lower()
    domain_patterns = _config.domain_patterns or {
        # Example patterns (can be overridden via configuration)
        "payment": ["payment", "pay", "checkout", "billing"],
        "order": ["order", "purchase", "buy"],
        "inventory": ["inventory", "stock", "warehouse"],
        "notification": ["notification", "email", "sms", "push", "notify"],
        "user": ["user", "auth", "login", "register"],
        "cart": ["cart", "basket"],
        "shipping": ["shipping", "delivery", "shipment"],
        "refund": ["refund", "return", "cancel"],
    }

    for domain, patterns in domain_patterns.items():
        if any(pattern in name_lower for pattern in patterns):
            return domain

    # Fallback: use first meaningful segment
    parts = task_name.split(".")
    if len(parts) >= 2:
        # Skip 'tasks' or 'celery' prefix
        for part in parts:
            if part not in ("tasks", "celery", "app"):
                return part

    return "unknown"


def _extract_service_name(task_name: str, exception: Exception | None = None) -> str:
    """
    Extract service name for circuit breaker tracking.

    Attempts to identify the external service that failed.
    Override service_name_patterns in config for custom mappings.
    """
    if exception:
        # Check for common external service indicators in exception
        exc_str = str(exception).lower()

        # Use configurable patterns or defaults
        service_patterns = getattr(_config, "service_name_patterns", None) or {
            "redis": ["redis"],
            "external_timeout": ["timeout"],
            "external_connection": ["connection"],
            "payment_gateway": ["pg", "payment", "gateway"],
        }

        for service_name, keywords in service_patterns.items():
            if any(keyword in exc_str for keyword in keywords):
                return service_name

    # Use domain as service name
    return _extract_domain_from_task_name(task_name)


# =============================================================================
# Signal Handlers
# =============================================================================

_signals_connected = False


def _should_store_to_dlq(sender) -> bool:
    """Determine if failed task should be stored to DLQ."""
    request = sender.request if sender else None
    retries = getattr(request, "retries", 0) if request else 0
    max_retries = getattr(sender, "max_retries", None) if sender else None

    # Store to DLQ if:
    # 1. max_retries is None or 0 (no retry configured)
    # 2. retries >= max_retries (all retries exhausted)
    return max_retries is None or max_retries == 0 or retries >= max_retries


def _handle_task_failure_internal(
    sender,
    task_id: str,
    exception: Exception,
    args: tuple,
    kwargs: dict,
    einfo: Any,
) -> None:
    """Internal handler for task failure - separated for complexity reduction."""
    task_name = sender.name if sender else "unknown"
    domain = _extract_domain_from_task_name(task_name)
    service_name = _extract_service_name(task_name, exception)

    # 1. Circuit Breaker: Record failure
    if _config.cb_enabled:
        _record_circuit_breaker_failure(service_name, task_name, exception)

    # 2. DLQ: Store failed operation (if max retries exceeded)
    if _config.dlq_enabled and _should_store_to_dlq(sender):
        _store_to_dlq(
            domain=domain,
            task_name=task_name,
            task_id=task_id,
            exception=exception,
            args=args,
            kwargs=kwargs,
            einfo=einfo,
        )

    # 3. Metrics: Record failure
    if _config.metrics_enabled:
        _record_failure_metrics(domain, task_name, exception)

    # 4. Forensics: Capture context
    if _config.forensics_enabled:
        _capture_forensic_context(
            task_name=task_name,
            task_id=task_id,
            exception=exception,
            args=args,
            kwargs=kwargs,
            einfo=einfo,
        )


@task_failure.connect
def on_task_failure(
    sender=None,
    task_id: str = None,
    exception: Exception = None,
    args: tuple = None,
    kwargs: dict = None,
    traceback: Any = None,
    einfo: Any = None,
    **kw,
):
    """
    Handle Celery task failure signal.

    This is called when a task raises an exception and fails.
    We use this to:
    1. Record failure in Circuit Breaker
    2. Store failed operation in DLQ (if max retries exceeded)
    3. Capture forensic context
    4. Update metrics
    """
    if not _config.enabled:
        return

    task_name = sender.name if sender else "unknown"

    # Skip excluded tasks
    if task_name in _config.excluded_tasks:
        return

    logger.info(
        "selfhealing_signal_task_failed",
        task_name=task_name,
        task_id=task_id,
        value=type(exception).__name__,
        error=exception,
    )

    try:
        _handle_task_failure_internal(sender, task_id, exception, args, kwargs, einfo)
    except Exception as e:
        # Never let signal handler crash affect task execution
        logger.exception(
            "selfhealing_signal_error_failure",
            error=e,
        )


@task_success.connect
def on_task_success(
    sender=None,
    result=None,
    **kw,
):
    """
    Handle Celery task success signal.

    Used to:
    1. Record success in Circuit Breaker (for half-open -> closed transition)
    2. Update success metrics
    """
    if not _config.enabled:
        return

    task_name = sender.name if sender else "unknown"

    # Skip excluded tasks
    if task_name in _config.excluded_tasks:
        return

    try:
        service_name = _extract_service_name(task_name)

        # Circuit Breaker: Record success
        if _config.cb_enabled:
            _record_circuit_breaker_success(service_name, task_name)

        # Metrics: Record success
        if _config.metrics_enabled:
            _record_success_metrics(service_name, task_name)

    except Exception as e:
        logger.exception(
            "selfhealing_signal_error_success",
            error=e,
        )


@task_retry.connect
def on_task_retry(
    sender=None,
    reason=None,
    einfo=None,
    **kw,
):
    """
    Handle Celery task retry signal.

    Used to track retry attempts for metrics.
    """
    if not _config.enabled or not _config.metrics_enabled:
        return

    task_name = sender.name if sender else "unknown"

    if task_name in _config.excluded_tasks:
        return

    try:
        domain = _extract_domain_from_task_name(task_name)
        _record_retry_metrics(domain, task_name)
    except Exception as e:
        logger.exception(
            "selfhealing_signal_error_retry",
            error=e,
        )


# =============================================================================
# Auto-Causation Propagation (Q9)
# before_task_publish: Celery Task 호출 시 causation 헤더 자동 주입
# =============================================================================


@before_task_publish.connect
def on_before_task_publish(
    sender=None,
    body=None,
    exchange=None,
    routing_key=None,
    headers=None,
    properties=None,
    declare=None,
    retry_policy=None,
    **kw,
):
    """
    Celery Task 발행 전 Causation Context 헤더 자동 주입.

    현재 CausationContext가 설정되어 있으면 Celery 메시지 헤더에
    cascade_id, parent_event_id, chain_depth, namespace를 자동 추가합니다.

    이를 통해:
    - API 요청 → Celery Task 인과관계 자동 연결
    - 개발자가 수동으로 headers=get_causation_for_celery() 호출 불필요
    - 모든 비동기 작업에서 causation chain 추적 가능
    """
    if not _config.enabled:
        return

    try:
        from selfhealing.context.causation_context import (
            CELERY_HEADER_CASCADE_ID,
            CELERY_HEADER_CHAIN_DEPTH,
            CELERY_HEADER_NAMESPACE,
            CELERY_HEADER_PARENT_EVENT,
            CausationContext,
        )

        # 현재 CausationContext 확인
        if not CausationContext.is_set():
            return

        info = CausationContext.get_current()
        if not info:
            return

        # headers 딕셔너리가 없으면 생략 (발행 시점에 headers 설정 불가)
        if headers is None:
            logger.debug("celery_signal.headers_unavailable")
            return

        # 이미 causation 헤더가 있으면 덮어쓰지 않음 (명시적 설정 우선)
        if headers.get(CELERY_HEADER_CASCADE_ID):
            logger.debug("celery_signal.causation_headers_present")
            return

        # Causation 헤더 주입
        headers[CELERY_HEADER_CASCADE_ID] = info.cascade_id
        headers[CELERY_HEADER_PARENT_EVENT] = info.parent_event_id
        headers[CELERY_HEADER_CHAIN_DEPTH] = str(info.chain_depth)
        headers[CELERY_HEADER_NAMESPACE] = info.namespace

        logger.debug(
            "celery_signal.causation_headers_injected",
            cascade_id=info.cascade_id,
            chain_depth=info.chain_depth,
        )

    except ImportError:
        # causation_context 모듈 없음 - 생략
        pass
    except Exception as e:
        # 시그널 핸들러가 태스크 발행에 영향을 주지 않도록 함
        logger.debug(
            "selfhealing_signal_causation_header",
            error=e,
        )


# =============================================================================
# Phase 6: Causation Context 자동 복원/정리
# =============================================================================


# Causation 컨텍스트 token 저장용 (하위 호환 — celery_context_utils로 이관됨)
_CAUSATION_TOKEN_ATTR = "_selfhealing_causation_token"


def _setup_causation_context(sender: Any, task_id: str, task_name: str) -> None:
    """Deprecated: Use celery_context_utils._setup_causation_context instead."""
    import warnings

    warnings.warn(
        "_setup_causation_context has moved to " "selfhealing.context.celery_context_utils._setup_causation_context",
        DeprecationWarning,
        stacklevel=2,
    )
    from selfhealing.context.celery_context_utils import (
        _setup_causation_context as _impl,
    )

    return _impl(sender, task_id, task_name)


def _detect_causation_source(task_name: str) -> str:
    """Deprecated: Use celery_context_utils._detect_causation_source instead."""
    import warnings

    warnings.warn(
        "_detect_causation_source has moved to " "selfhealing.context.celery_context_utils._detect_causation_source",
        DeprecationWarning,
        stacklevel=2,
    )
    from selfhealing.context.celery_context_utils import (
        _detect_causation_source as _impl,
    )

    return _impl(task_name)


def _cleanup_causation_context(sender: Any) -> None:
    """Deprecated: Use celery_context_utils._cleanup_causation_context instead."""
    import warnings

    warnings.warn(
        "_cleanup_causation_context has moved to " "selfhealing.context.celery_context_utils._cleanup_causation_context",
        DeprecationWarning,
        stacklevel=2,
    )
    from selfhealing.context.celery_context_utils import (
        _cleanup_causation_context as _impl,
    )

    return _impl(sender)


# =============================================================================
# Celery Task trace_id 표준화 - Prerun/Postrun 핸들러
# =============================================================================


@task_prerun.connect
def on_task_prerun(
    sender=None,
    task_id: str = None,
    task=None,
    args: tuple = None,
    kwargs: dict = None,
    **kw,
):
    """
    Celery Task 시작 전 TraceContext 자동 주입.

    모든 Celery Task에 자동으로 trace_id를 주입합니다.

    동작:
    1. kwargs에 trace_info가 있으면 HTTP에서 전파된 것으로 간주 → 원본 trace_id 사용
    2. 없으면 CELERY_{task_id} 형식으로 생성

    이를 통해:
    - 개발자가 수동으로 trace_id 설정 불필요
    - 모든 Audit 로그에 자동으로 Celery Task ID 포함
    - Flower UI에서 직접 검색 가능
    """
    if not _config.enabled:
        return

    task_name = sender.name if sender else "unknown"

    # Skip excluded tasks
    if task_name in _config.excluded_tasks:
        return

    try:
        from selfhealing.context.celery_context_utils import (
            SelfHealingContextError,
            restore_all_task_context,
        )

        restore_all_task_context(sender, task_id, task_name, kwargs)

        logger.debug(
            "selfhealing_signal_task_prerun",
            task_name=task_name,
            task_id=task_id,
        )

    except SelfHealingContextError:
        # R5: CRITICAL 컨텍스트 복원 실패 → Fail-Fast. 재시도 차단은
        # setup_selfhealing_signals()의 dont_autoretry_for로 보장.
        raise
    except Exception as e:
        # Never let signal handler crash affect task execution
        logger.exception(
            "selfhealing_signal_error_prerun",
            error=e,
        )


@task_postrun.connect
def on_task_postrun(
    sender=None,
    task_id: str = None,
    task=None,
    args: tuple = None,
    kwargs: dict = None,
    retval=None,
    state: str = None,
    **kw,
):
    """
    Celery Task 완료 후 TraceContext 정리.

    Worker 재사용 시 이전 Task의 trace_id/celery_context 잔존 방지.
    """
    if not _config.enabled:
        return

    task_name = sender.name if sender else "unknown"

    if task_name in _config.excluded_tasks:
        return

    try:
        from selfhealing.context.celery_context_utils import cleanup_all_task_context

        cleanup_all_task_context(sender)

        logger.debug(
            "selfhealing_signal_task_postrun",
            task_name=task_name,
            task_id=task_id,
            state=state,
        )

    except Exception as e:
        logger.exception(
            "selfhealing_signal_error_postrun",
            error=e,
        )


# =============================================================================
# Circuit Breaker Integration
# =============================================================================


def _record_circuit_breaker_failure(service_name: str, task_name: str, exception: Exception):
    """Record failure in circuit breaker."""
    try:
        from selfhealing.services.circuit_breaker.convenience import (
            get_circuit_breaker_service,
        )

        cb_service = get_circuit_breaker_service()

        # record_failure handles threshold checking internally
        cb_service.record_failure(service_name=service_name)

        logger.debug(
            "selfhealing_cb_recorded_failure",
            service_name=service_name,
        )

    except ImportError as e:
        logger.debug(
            "selfhealing_cb_service_available",
            error=e,
        )
    except Exception as e:
        logger.exception(
            "selfhealing_cb_failed_record",
            error=e,
        )


def _record_circuit_breaker_success(service_name: str, task_name: str):
    """Record success in circuit breaker."""
    try:
        from selfhealing.services.circuit_breaker.convenience import (
            get_circuit_breaker_service,
        )

        cb_service = get_circuit_breaker_service()

        # record_success handles half-open -> closed transition internally
        cb_service.record_success(service_name=service_name)

        logger.debug(
            "selfhealing_cb_recorded_success",
            service_name=service_name,
        )

    except ImportError as e:
        logger.debug(
            "selfhealing_cb_service_available",
            error=e,
        )
    except Exception as e:
        logger.exception(
            "selfhealing_cb_failed_record",
            error=e,
        )


def _trigger_conditional_replay(service_name: str):
    """Trigger conditional replay when circuit closes."""
    try:
        from selfhealing.adapters.celery.tasks import (
            conditional_replay_on_circuit_close,
        )

        # Enqueue replay task
        conditional_replay_on_circuit_close.delay(service_name=service_name, max_items=50)
        logger.info(
            "selfhealing_cb_triggered_conditional",
            service_name=service_name,
        )

    except Exception as e:
        logger.exception(
            "selfhealing_cb_failed_trigger",
            error=e,
        )


# =============================================================================
# DLQ Integration
# =============================================================================


def _store_to_dlq(
    domain: str,
    task_name: str,
    task_id: str,
    exception: Exception,
    args: tuple,
    kwargs: dict,
    einfo: Any,
):
    """Store failed operation to DLQ."""
    try:
        from selfhealing.services.dlq_service import store_to_dlq

        # Determine failure type from exception
        failure_type = _classify_failure_type(exception)

        # Build snapshot data for replay
        snapshot_data = {
            "task_name": task_name,
            "task_id": task_id,
            "args": list(args) if args else [],
            "kwargs": kwargs or {},
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Build request data
        request_data = {
            "task_name": task_name,
            "task_id": task_id,
            "args": list(args) if args else [],
            "kwargs": kwargs or {},
        }

        # Extract entity references from kwargs
        entity_refs = _extract_entity_refs(kwargs)

        # Use store_to_dlq convenience function
        # Extract entity references generically
        entity_type = entity_refs.get("entity_type", "")
        entity_id = entity_refs.get("entity_id", "")

        result = store_to_dlq(
            domain=domain,
            failure_type=failure_type,
            error_message=str(exception),
            error_code=type(exception).__name__,
            snapshot_data=snapshot_data,
            request_data=request_data,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else "",
            user_id=entity_refs.get("user_id"),
            metadata={
                "source": "celery_signal_hook",
                "task_name": task_name,
                "traceback": str(einfo) if einfo else None,
            },
            recommended_action=_get_recommended_action(failure_type),
        )

        logger.info(
            "selfhealing_dlq_stored_failed",
            domain=domain,
            failure_type=failure_type,
            result=result.dlq_id,
        )

        # Record DLQ metric
        try:
            from selfhealing.services.metrics.recorders import record_dlq_item_created

            record_dlq_item_created(domain=domain, failure_type=failure_type)
        except Exception:
            pass

    except ImportError as e:
        logger.debug(
            "selfhealing_dlq_service_available",
            error=e,
        )
    except Exception as e:
        logger.exception(
            "selfhealing_dlq_failed_store",
            error=e,
        )


# =============================================================================
# Failure Classification (Complexity Reduction via Pattern Matching)
# =============================================================================

# Exception type name patterns → failure type
_EXCEPTION_TYPE_PATTERNS = {
    "NETWORK_ERROR": ["connection", "timeout", "network", "socket"],
}

# Exception message patterns → failure type (order matters, first match wins)
_EXCEPTION_MESSAGE_PATTERNS = [
    (["rate limit", "too many requests", "429"], "RATE_LIMITED"),
    (["auth", "unauthorized", "401", "403"], "AUTH_ERROR"),
    (["validation", "invalid", "400"], "VALIDATION_ERROR"),
    (["502", "503", "504", "bad gateway"], "EXTERNAL_SERVICE_ERROR"),
    (["gateway", "provider", "external"], "GATEWAY_ERROR"),
    (["timeout"], "TIMEOUT"),
    (["connection"], "CONNECTION_ERROR"),
]


def _classify_failure_type(exception: Exception) -> str:
    """Classify exception into failure type using pattern matching."""
    exc_type = type(exception).__name__.lower()
    exc_str = str(exception).lower()

    # Check exception type name patterns
    for failure_type, keywords in _EXCEPTION_TYPE_PATTERNS.items():
        if any(keyword in exc_type for keyword in keywords):
            return failure_type

    # Check exception message patterns
    for keywords, failure_type in _EXCEPTION_MESSAGE_PATTERNS:
        if any(keyword in exc_str for keyword in keywords):
            return failure_type

    return "UNKNOWN_ERROR"


def _extract_entity_refs(kwargs: dict | None) -> dict[str, str]:
    """
    Extract entity references from task kwargs.

    Returns generic entity_type and entity_id for DLQ storage.
    """
    if not kwargs:
        return {}

    entity_refs = {}

    # Check for explicit entity_type/entity_id first
    if "entity_type" in kwargs and "entity_id" in kwargs:
        entity_refs["entity_type"] = str(kwargs["entity_type"])
        entity_refs["entity_id"] = str(kwargs["entity_id"])
        if "user_id" in kwargs:
            entity_refs["user_id"] = kwargs["user_id"]
        return entity_refs

    # Fallback: infer from common ID patterns
    id_priority = [
        ("order_id", "order"),
        ("payment_id", "payment"),
        ("transaction_id", "transaction"),
        ("subscription_id", "subscription"),
        ("user_id", "user"),
        ("product_id", "product"),
        ("cart_id", "cart"),
        ("shipment_id", "shipment"),
    ]

    for key, entity_type in id_priority:
        if key in kwargs and kwargs[key] is not None:
            entity_refs["entity_type"] = entity_type
            entity_refs["entity_id"] = str(kwargs[key])
            break

    # Always include user_id if present
    if "user_id" in kwargs and kwargs["user_id"] is not None:
        entity_refs["user_id"] = kwargs["user_id"]

    return entity_refs


def _get_recommended_action(failure_type: str) -> str:
    """Get recommended action based on failure type."""
    actions = {
        "NETWORK_ERROR": "Wait for network recovery, then auto-replay",
        "TIMEOUT": "Increase timeout or retry with backoff",
        "CONNECTION_ERROR": "Check external service availability",
        "RATE_LIMITED": "Wait for rate limit window, then retry",
        "AUTH_ERROR": "Check credentials and permissions",
        "VALIDATION_ERROR": "Manual review required - data may be invalid",
        "EXTERNAL_SERVICE_ERROR": "Wait for external service recovery",
        "GATEWAY_ERROR": "Check gateway status, manual review may be needed",
        "UNKNOWN_ERROR": "Manual review recommended",
    }
    return actions.get(failure_type, "Review and retry manually")


# =============================================================================
# Metrics Integration
# =============================================================================


def _record_failure_metrics(domain: str, task_name: str, exception: Exception):
    """Record failure metrics."""
    try:
        from selfhealing.services.metrics.recorders import record_retry_attempt

        # API: record_retry_attempt(domain: str, attempt_count: int, outcome: str)
        record_retry_attempt(
            domain=domain,
            attempt_count=1,
            outcome="failure",
        )
    except ImportError:
        pass
    except Exception as e:
        logger.debug(
            "selfhealing_metrics_failed_record",
            error=e,
        )


def _record_success_metrics(service_name: str, task_name: str):
    """Record success metrics."""
    try:
        from selfhealing.services.metrics.recorders import record_retry_attempt

        domain = _extract_domain_from_task_name(task_name)
        record_retry_attempt(
            domain=domain,
            attempt_count=1,
            outcome="success",
        )
    except ImportError:
        pass
    except Exception as e:
        logger.debug(
            "selfhealing_metrics_failed_record",
            error=e,
        )


def _record_retry_metrics(domain: str, task_name: str):
    """Record retry attempt metrics."""
    try:
        from selfhealing.services.metrics.recorders import record_retry_attempt

        record_retry_attempt(
            domain=domain,
            attempt_count=1,
            outcome="retry",
        )
    except ImportError:
        pass
    except Exception as e:
        logger.debug(
            "selfhealing_metrics_failed_record",
            error=e,
        )


# =============================================================================
# Forensic Context
# =============================================================================


def _capture_forensic_context(
    task_name: str,
    task_id: str,
    exception: Exception,
    args: tuple,
    kwargs: dict,
    einfo: Any,
):
    """Capture forensic context for failed task."""
    try:
        from selfhealing.services.forensic_context import (
            capture_forensic_context,  # type: ignore[import-not-found]  # noqa: E501 — 미구현 모듈, ImportError fallback으로 보호
        )

        # API: capture_forensic_context(task_id, task_name, order, payment, user, request)
        context = capture_forensic_context(
            task_id=task_id,
            task_name=task_name,
        )

        logger.debug(
            "selfhealing_forensics_captured_context",
            task_name=task_name,
        )
        return context

    except ImportError:
        pass
    except Exception as e:
        logger.debug(
            "selfhealing_forensics_failed_capture",
            error=e,
        )
        return None


# =============================================================================
# Setup Functions
# =============================================================================


def setup_selfhealing_signals(
    app=None,
    enabled: bool | None = None,
    cb_enabled: bool | None = None,
    dlq_enabled: bool | None = None,
    metrics_enabled: bool | None = None,
    forensics_enabled: bool | None = None,
    excluded_tasks: list[str] | None = None,
    task_domain_mapping: dict[str, str] | None = None,
):
    """
    Setup self-healing signal hooks for Celery.

    This function connects the signal handlers to Celery signals.
    Call this once during application initialization.

    Args:
        app: Celery application instance (optional). When provided, registers
            SelfHealingContextError in dont_autoretry_for to prevent infinite retries.
        enabled: Master switch for all hooks (default: True)
        cb_enabled: Enable circuit breaker recording
        dlq_enabled: Enable DLQ storage
        metrics_enabled: Enable metrics recording
        forensics_enabled: Enable forensic context capture
        excluded_tasks: List of task names to exclude from processing
        task_domain_mapping: Dict mapping task names to domains

    Example:
        # In your celery.py:
        from celery import Celery
        from selfhealing.adapters.celery.signal_hooks import setup_selfhealing_signals

        app = Celery('myapp')

        # Simple setup - uses environment variables
        setup_selfhealing_signals()

        # Or with explicit configuration
        setup_selfhealing_signals(
            enabled=True,
            cb_enabled=True,
            dlq_enabled=True,
            task_domain_mapping={
                'myapp.tasks.process_payment': 'payment',
                'myapp.tasks.send_notification': 'notification',
            },
        )
    """
    global _signals_connected

    if _signals_connected:
        logger.warning("self_healing.signal_hooks_already_connected")
        return

    # Apply configuration overrides
    if enabled is not None:
        _config.enabled = enabled
    if cb_enabled is not None:
        _config.cb_enabled = cb_enabled
    if dlq_enabled is not None:
        _config.dlq_enabled = dlq_enabled
    if metrics_enabled is not None:
        _config.metrics_enabled = metrics_enabled
    if forensics_enabled is not None:
        _config.forensics_enabled = forensics_enabled
    if excluded_tasks:
        _config.excluded_tasks.update(excluded_tasks)
    if task_domain_mapping:
        _config.task_domain_mapping.update(task_domain_mapping)

    # CRITICAL 컨텍스트 복원 실패 시 재시도 방지: dont_autoretry_for 등록
    try:
        from selfhealing.context.celery_context_utils import SelfHealingContextError

        # Celery app이 제공된 경우 base task 클래스에 등록
        if app is not None:
            base_task = app.Task
            existing = getattr(base_task, "dont_autoretry_for", ()) or ()
            if SelfHealingContextError not in existing:
                base_task.dont_autoretry_for = (*existing, SelfHealingContextError)
    except Exception as e:
        logger.debug(
            "self_healing.failed_register",
            error=e,
        )

    # Signals are connected via decorators, just mark as connected
    _signals_connected = True

    logger.info(
        "self_healing.signal_hooks_configured",
        _config=_config.enabled,
        _config_1=_config.cb_enabled,
        _config_2=_config.dlq_enabled,
        _config_3=_config.metrics_enabled,
        _config_4=_config.forensics_enabled,
    )


def disconnect_selfhealing_signals():
    """
    Disconnect self-healing signal hooks.

    Useful for testing or when you need to temporarily disable hooks.
    """
    global _signals_connected

    try:
        task_failure.disconnect(on_task_failure)
        task_success.disconnect(on_task_success)
        task_retry.disconnect(on_task_retry)
        task_prerun.disconnect(on_task_prerun)  # trace_id 자동 주입
        task_postrun.disconnect(on_task_postrun)  # trace_id 정리
        before_task_publish.disconnect(on_before_task_publish)  # causation 자동 전파
        _signals_connected = False
        logger.info("self_healing.signal_hooks_disconnected")
    except Exception as e:
        logger.exception(
            "self_healing.error_disconnecting_signals",
            error=e,
        )


def is_signals_connected() -> bool:
    """Check if signal hooks are currently connected."""
    return _signals_connected


# =============================================================================
# Decorator for Manual Integration
# =============================================================================


def selfhealing_task(
    domain: str | None = None,
    service_name: str | None = None,
    track_cb: bool = True,
    track_dlq: bool = True,
):
    """
    Decorator to add self-healing tracking to a Celery task.

    Use this for fine-grained control over which tasks are tracked.

    Args:
        domain: Domain for DLQ classification
        service_name: Service name for circuit breaker
        track_cb: Whether to track circuit breaker state
        track_dlq: Whether to store failures in DLQ

    Example:
        @app.task
        @selfhealing_task(domain='order', service_name='order_service')
        def process_order(order_id):
            # Your task logic
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            task_name = func.__name__
            resolved_domain = domain or _extract_domain_from_task_name(task_name)
            resolved_service = service_name or _extract_service_name(task_name)

            try:
                result = func(*args, **kwargs)

                # Record success
                if track_cb:
                    _record_circuit_breaker_success(resolved_service, task_name)

                return result

            except Exception as e:
                # Record failure
                if track_cb:
                    _record_circuit_breaker_failure(resolved_service, task_name, e)

                if track_dlq:
                    _store_to_dlq(
                        domain=resolved_domain,
                        task_name=task_name,
                        task_id=str(id(func)),
                        exception=e,
                        args=args,
                        kwargs=kwargs,
                        einfo=None,
                    )

                raise

        return wrapper

    return decorator
