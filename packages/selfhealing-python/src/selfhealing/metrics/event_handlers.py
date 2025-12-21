"""
DLQ Metric Event Handlers.

Provides event-driven metric updates without DB queries.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependencies
_metrics_instance = None


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


class DLQMetricEventHandler:
    """
    DLQ 이벤트 발생 시 메트릭을 업데이트하는 핸들러.

    이 핸들러는 DB 쿼리 없이 인메모리 카운터만 조작합니다.
    비즈니스 로직에서 DLQ 상태 변경 시 호출해야 합니다.

    Design:
    - Counter: 누적 카운트, Push Only (100% 정확)
    - Histogram: 관측 시점 기록, Push Only (100% 정확)
    - Gauge: 현재 상태, Push + Lazy Sync (~99% 정확)

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

            # Gauge: 현재 대기 수 증가 (~99% 정확, 재시작 시 동기화)
            if hasattr(metrics, 'dlq_pending_gauge') and metrics.dlq_pending_gauge:
                metrics.dlq_pending_gauge.labels(domain=domain).inc()

            logger.debug(
                f"[EventHandler] DLQ created: domain={domain}, type={failure_type}"
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

        Args:
            domain: 도메인 이름
            resolution_type: 해결 유형 (auto_replay, manual, expired 등)
            duration_seconds: 실패부터 해결까지 걸린 시간 (초)
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            # Gauge: 현재 대기 수 감소
            if hasattr(metrics, 'dlq_pending_gauge') and metrics.dlq_pending_gauge:
                metrics.dlq_pending_gauge.labels(domain=domain).dec()

            # Histogram: 복구 시간 기록 (100% 정확)
            if duration_seconds is not None and hasattr(metrics, 'recovery_time_seconds'):
                metrics.recovery_time_seconds.labels(
                    domain=domain,
                    resolution_type=resolution_type,
                ).observe(duration_seconds)

            # Counter: 성공 카운트 증가
            if hasattr(metrics, 'retry_outcomes_total'):
                metrics.retry_outcomes_total.labels(
                    domain=domain,
                    outcome="success",
                ).inc()

            logger.debug(
                f"[EventHandler] DLQ resolved: domain={domain}, "
                f"resolution={resolution_type}, duration={duration_seconds}s"
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
            if hasattr(metrics, 'retry_outcomes_total'):
                metrics.retry_outcomes_total.labels(
                    domain=domain,
                    outcome="failure",
                ).inc()

            # Histogram: 시도 횟수 기록
            if hasattr(metrics, 'retry_attempts_histogram'):
                metrics.retry_attempts_histogram.labels(
                    domain=domain,
                ).observe(attempt_count)

            logger.debug(
                f"[EventHandler] DLQ retry failed: domain={domain}, "
                f"type={failure_type}, attempts={attempt_count}"
            )
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record DLQ failure: {e}")

    @staticmethod
    def on_sla_breach(domain: str) -> None:
        """
        SLA 위반 발생 시 호출.

        Args:
            domain: 도메인 이름
        """
        metrics = _get_metrics()
        if metrics is None:
            return

        try:
            if hasattr(metrics, 'sla_breach_total'):
                metrics.sla_breach_total.labels(domain=domain).inc()

            logger.debug(f"[EventHandler] SLA breach: domain={domain}")
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record SLA breach: {e}")


class CircuitBreakerEventHandler:
    """
    Circuit Breaker 이벤트 핸들러.

    Circuit Breaker 상태 변경 시 메트릭을 업데이트합니다.
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
            if hasattr(metrics, 'circuit_breaker_state'):
                metrics.circuit_breaker_state.labels(service_name=service).set(state_value)

            # Counter: 상태 전환 카운트
            if hasattr(metrics, 'circuit_breaker_transitions'):
                metrics.circuit_breaker_transitions.labels(
                    service_name=service,
                    from_state=from_state,
                    to_state=to_state,
                ).inc()

            # Counter: open 상태로 전환 시 trip 카운트
            if to_state == "open" and hasattr(metrics, 'circuit_breaker_trips'):
                metrics.circuit_breaker_trips.labels(service_name=service).inc()

            logger.debug(
                f"[EventHandler] CB state changed: service={service}, "
                f"{from_state} -> {to_state}"
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
            if hasattr(metrics, 'circuit_breaker_failures'):
                metrics.circuit_breaker_failures.labels(service_name=service).inc()
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
            if hasattr(metrics, 'replay_attempts_total'):
                metrics.replay_attempts_total.labels(
                    domain=domain,
                    replay_type=replay_type,
                ).inc()
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
            if hasattr(metrics, 'replay_outcomes_total'):
                metrics.replay_outcomes_total.labels(
                    domain=domain,
                    outcome=outcome,
                ).inc()

            if hasattr(metrics, 'replay_duration_seconds'):
                metrics.replay_duration_seconds.labels(
                    domain=domain,
                ).observe(duration_seconds)
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record replay completion: {e}")


__all__ = [
    "DLQMetricEventHandler",
    "CircuitBreakerEventHandler",
    "ReplayEventHandler",
]
