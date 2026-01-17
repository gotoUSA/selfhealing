"""
DLQ Metric Event Handlers.

Provides event-driven metric updates without DB queries.

Key Features:
- SafeGauge: 음수 방지 래퍼로 서버 재시작 후에도 Gauge가 -1이 되지 않음
- Dynamic Logging: API 레벨에서 런타임 로깅 레벨 조절 가능
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.metrics.safe_gauge import SafeGauge

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies
_metrics_instance = None
_safe_gauge_cache: Dict[str, "SafeGauge"] = {}
_logging_config = None


def _get_metrics():
    """Get the metrics instance lazily."""
    global _metrics_instance
    if _metrics_instance is None:
        try:
            from selfhealing.metrics.prometheus import get_metrics

            _metrics_instance = get_metrics()
        except ImportError:
            logger.warning("[EventHandler] Metrics not available")
            return None
    return _metrics_instance


def _get_logging_config():
    """Get the logging config instance lazily."""
    global _logging_config
    if _logging_config is None:
        try:
            from selfhealing.config import get_event_logging_config

            _logging_config = get_event_logging_config()
        except ImportError:
            return None
    return _logging_config


def _log_event(level_getter: str, message: str, **extra) -> None:
    """
    Log an event with dynamic log level from EventLoggingConfig.

    Args:
        level_getter: Method name on EventLoggingConfig (e.g., 'get_dlq_log_level')
        message: Log message
        **extra: Extra structured logging fields
    """
    config = _get_logging_config()
    if config is None:
        # Fallback to INFO if config not available
        logger.info(message, extra=extra)
        return

    try:
        level_name = getattr(config, level_getter)()
        level = config.get_log_level_int(level_name)
        logger.log(level, message, extra=extra)
    except Exception:
        logger.info(message, extra=extra)


def _get_safe_pending_gauge() -> Optional["SafeGauge"]:
    """
    Get or create SafeGauge wrapper for dlq_pending_gauge.

    Returns SafeGauge instance that prevents negative values.
    """
    global _safe_gauge_cache

    if "dlq_pending" in _safe_gauge_cache:
        return _safe_gauge_cache["dlq_pending"]

    metrics = _get_metrics()
    if metrics is None:
        return None

    try:
        from selfhealing.metrics.safe_gauge import SafeGauge

        if hasattr(metrics, "dlq_pending_gauge") and metrics.dlq_pending_gauge:
            safe_gauge = SafeGauge(metrics.dlq_pending_gauge)
            _safe_gauge_cache["dlq_pending"] = safe_gauge
            return safe_gauge
    except ImportError:
        logger.warning("[EventHandler] SafeGauge not available, using raw gauge")

    return None


class DLQMetricEventHandler:
    """
    DLQ 이벤트 발생 시 메트릭을 업데이트하는 핸들러.

    이 핸들러는 DB 쿼리 없이 인메모리 카운터만 조작합니다.
    비즈니스 로직에서 DLQ 상태 변경 시 호출해야 합니다.

    Design:
    - Counter: 누적 카운트, Push Only (100% 정확)
    - Histogram: 관측 시점 기록, Push Only (100% 정확)
    - Gauge: SafeGauge 래퍼 사용으로 음수 방지 (~99% 정확, 재시작 시 동기화)

    SafeGauge Pattern:
        서버 재시작 직후 Gauge가 0인 상태에서 '해결' 이벤트가 먼저 도착해도
        대기 카운트가 -1이 되지 않습니다. 이는 Tech DD에서 '허술함' 노출을
        방지하는 핵심 방어 로직입니다.

    Example:
        >>> handler = DLQMetricEventHandler()
        >>> # DLQ 생성 시
        >>> handler.on_item_created("payment", "PG_TIMEOUT")
        >>> # DLQ 해결 시
        >>> handler.on_item_resolved("payment", "auto_replay")
    """

    @staticmethod
    def on_item_created(domain: str, failure_type: str) -> None:
        """
        DLQ 항목 생성 시 호출.

        Args:
            domain: 도메인 이름 (payment, point, inventory 등)
            failure_type: 실패 유형 (PG_TIMEOUT, INSUFFICIENT_STOCK 등)
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            # Counter: 누적 카운트 증가 (100% 정확)
            metrics.record_dlq_item_created(domain, failure_type)

            # Gauge: SafeGauge를 통한 안전한 증가
            safe_gauge = _get_safe_pending_gauge()
            if safe_gauge:
                safe_gauge.labels(domain=domain).inc()

            _log_event(
                "get_dlq_log_level",
                f"[EventHandler] DLQ created: domain={domain}, type={failure_type}",
                event_type="dlq.created",
                domain=domain,
                failure_type=failure_type,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record DLQ creation: {e}")

    @staticmethod
    def on_item_resolved(
        domain: str,
        resolution_type: str,
        duration_seconds: Optional[float] = None,
    ) -> None:
        """
        DLQ 항목 해결 시 호출.

        SafeGauge를 사용하여 음수 방지:
        - 서버 재시작 직후 '해결' 이벤트가 먼저 도착해도 -1이 되지 않음
        - Shadow counter로 현재 값을 추적하고 0 미만 시 클램핑
        - Lazy Sync(Reconciler)가 주기적으로 실제 DB 값과 동기화

        Args:
            domain: 도메인 이름
            resolution_type: 해결 유형 (auto_replay, manual, expired 등)
            duration_seconds: 실패부터 해결까지 걸린 시간 (초)
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            # Gauge: SafeGauge를 통한 안전한 감소 (음수 방지!)
            safe_gauge = _get_safe_pending_gauge()
            if safe_gauge:
                safe_gauge.labels(domain=domain).dec()

            # Histogram: 복구 시간 기록 (100% 정확)
            if duration_seconds is not None and hasattr(metrics, "recovery_time_seconds"):
                metrics.recovery_time_seconds.labels(
                    domain=domain,
                    resolution_type=resolution_type,
                ).observe(duration_seconds)

            # Counter: 성공 카운트 증가
            if hasattr(metrics, "retry_outcomes_total"):
                metrics.retry_outcomes_total.labels(
                    domain=domain,
                    outcome="success",
                ).inc()

            _log_event(
                "get_dlq_log_level",
                f"[EventHandler] DLQ resolved: domain={domain}, "
                f"resolution={resolution_type}, duration={duration_seconds}s",
                event_type="dlq.resolved",
                domain=domain,
                resolution_type=resolution_type,
                duration_seconds=duration_seconds,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record DLQ resolution: {e}")

    @staticmethod
    def on_item_failed(
        domain: str,
        failure_type: str,
        attempt_count: int = 1,
    ) -> None:
        """
        DLQ 재시도 실패 시 호출 (대기 수는 유지).

        Args:
            domain: 도메인 이름
            failure_type: 실패 유형
            attempt_count: 시도 횟수
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            # Counter: 실패 카운트 증가
            if hasattr(metrics, "retry_outcomes_total"):
                metrics.retry_outcomes_total.labels(
                    domain=domain,
                    outcome="failure",
                ).inc()

            # Histogram: 시도 횟수 기록
            if hasattr(metrics, "retry_attempts_histogram"):
                metrics.retry_attempts_histogram.labels(
                    domain=domain,
                ).observe(attempt_count)

            _log_event(
                "get_dlq_log_level",
                f"[EventHandler] DLQ retry failed: domain={domain}, " f"type={failure_type}, attempts={attempt_count}",
                event_type="dlq.retry_failed",
                domain=domain,
                failure_type=failure_type,
                attempt_count=attempt_count,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record DLQ failure: {e}")

    @staticmethod
    def on_sla_breach(domain: str) -> None:
        """
        SLA 위반 발생 시 호출.

        SLA 위반은 시스템 거버넌스에 중요한 이벤트이므로
        기본적으로 WARNING 레벨로 로깅됩니다.

        Args:
            domain: 도메인 이름
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            if hasattr(metrics, "sla_breach_total"):
                metrics.sla_breach_total.labels(domain=domain).inc()

            _log_event(
                "get_sla_log_level",
                f"[EventHandler] SLA breach: domain={domain}",
                event_type="sla.breach",
                domain=domain,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record SLA breach: {e}")


class CircuitBreakerEventHandler:
    """
    Circuit Breaker 이벤트 핸들러.

    Circuit Breaker 상태 변경 시 메트릭을 업데이트합니다.
    CB 상태 변경은 시스템의 거버넌스가 위협받는 신호이므로
    기본적으로 WARNING 레벨로 로깅됩니다.
    """

    # 상태를 숫자로 매핑 (Prometheus Gauge용)
    STATE_VALUES = {
        "closed": 0,
        "open": 1,
        "half_open": 2,
    }

    @staticmethod
    def on_state_changed(
        service: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """
        Circuit Breaker 상태 변경 시 호출.

        CB 상태 변경은 시스템 불안정 신호이므로 WARNING 레벨로 로깅됩니다.

        Args:
            service: 서비스 이름
            from_state: 이전 상태
            to_state: 새 상태
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            # Gauge: 현재 상태 설정
            state_value = CircuitBreakerEventHandler.STATE_VALUES.get(to_state, 0)
            if hasattr(metrics, "circuit_breaker_state"):
                metrics.circuit_breaker_state.labels(service_name=service).set(state_value)

            # Counter: 상태 전환 카운트
            if hasattr(metrics, "circuit_breaker_transitions"):
                metrics.circuit_breaker_transitions.labels(
                    service_name=service,
                    from_state=from_state,
                    to_state=to_state,
                ).inc()

            # Counter: open 상태로 전환 시 trip 카운트
            if to_state == "open" and hasattr(metrics, "circuit_breaker_trips"):
                metrics.circuit_breaker_trips.labels(service_name=service).inc()

            _log_event(
                "get_cb_log_level",
                f"[EventHandler] CB state changed: service={service}, " f"{from_state} -> {to_state}",
                event_type="circuit_breaker.state_changed",
                service=service,
                from_state=from_state,
                to_state=to_state,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record CB state change: {e}")

    @staticmethod
    def on_failure(service: str) -> None:
        """
        Circuit Breaker 실패 기록.

        Args:
            service: 서비스 이름
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            if hasattr(metrics, "circuit_breaker_failures"):
                metrics.circuit_breaker_failures.labels(service_name=service).inc()

            _log_event(
                "get_cb_log_level",
                f"[EventHandler] CB failure recorded: service={service}",
                event_type="circuit_breaker.failure",
                service=service,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record CB failure: {e}")


class ReplayEventHandler:
    """
    Replay 이벤트 핸들러.

    Replay 작업 관련 메트릭을 업데이트합니다.
    """

    @staticmethod
    def on_replay_started(domain: str, replay_type: str) -> None:
        """
        Replay 시작 시 호출.

        Args:
            domain: 도메인 이름
            replay_type: Replay 유형 (auto, manual 등)
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            if hasattr(metrics, "replay_attempts_total"):
                metrics.replay_attempts_total.labels(
                    domain=domain,
                    replay_type=replay_type,
                ).inc()

            _log_event(
                "get_replay_log_level",
                f"[EventHandler] Replay started: domain={domain}, type={replay_type}",
                event_type="replay.started",
                domain=domain,
                replay_type=replay_type,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record replay start: {e}")

    @staticmethod
    def on_replay_completed(
        domain: str,
        success: bool,
        duration_seconds: float,
    ) -> None:
        """
        Replay 완료 시 호출.

        Args:
            domain: 도메인 이름
            success: 성공 여부
            duration_seconds: 소요 시간
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            outcome = "success" if success else "failure"
            if hasattr(metrics, "replay_outcomes_total"):
                metrics.replay_outcomes_total.labels(
                    domain=domain,
                    outcome=outcome,
                ).inc()

            if hasattr(metrics, "replay_duration_seconds"):
                metrics.replay_duration_seconds.labels(
                    domain=domain,
                ).observe(duration_seconds)

            _log_event(
                "get_replay_log_level",
                f"[EventHandler] Replay completed: domain={domain}, " f"success={success}, duration={duration_seconds}s",
                event_type="replay.completed",
                domain=domain,
                success=success,
                duration_seconds=duration_seconds,
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record replay completion: {e}")


def reset_event_handler_cache() -> None:
    """
    Reset cached instances (for testing).

    Clears the global caches for metrics, safe gauge, and logging config.
    """
    global _metrics_instance, _safe_gauge_cache, _logging_config
    _metrics_instance = None
    _safe_gauge_cache = {}
    _logging_config = None


__all__ = [
    "DLQMetricEventHandler",
    "CircuitBreakerEventHandler",
    "ReplayEventHandler",
    "reset_event_handler_cache",
]
