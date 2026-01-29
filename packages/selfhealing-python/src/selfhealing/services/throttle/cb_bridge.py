"""
Throttle과 Circuit Breaker 간 RTT 데이터 공유 인터페이스.

Throttle의 RTT 데이터를 CB 장애 감지에 활용하고,
CB 상태를 Throttle limit 조정에 반영합니다.

공유 메트릭:
- RTT (Response Time): 응답 시간 데이터
- Gradient: RTT 변화율
- CB State: 서비스별 CB 상태

Usage:
    bridge = get_throttle_cb_bridge()

    # RTT 기록 및 CB 피드백
    bridge.record_rtt("payment_api", rtt_ms=150.0)

    # CB 상태 조회
    state = bridge.get_cb_state("payment_api")
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker.service import CircuitBreakerService
    from selfhealing.services.throttle.registry import ThrottleRegistry

logger = logging.getLogger(__name__)


@dataclass
class RTTMetrics:
    """RTT 관련 메트릭."""

    current_rtt_ms: float | None = None
    smoothed_rtt_ms: float | None = None
    gradient: float = 0.0
    sample_count: int = 0
    last_updated: datetime | None = None


@dataclass
class ServiceHealthMetrics:
    """서비스 건강 상태 통합 메트릭."""

    service_name: str

    # RTT 메트릭
    rtt: RTTMetrics = field(default_factory=RTTMetrics)

    # CB 상태
    cb_state: str = "closed"
    cb_failure_count: int = 0
    cb_success_count: int = 0

    # Throttle 상태
    throttle_current_limit: int | None = None
    throttle_original_limit: int | None = None

    # SLA 위반 플래그
    sla_warning_triggered: bool = False
    sla_critical_triggered: bool = False


class RTTSeverity(str, Enum):
    """RTT 심각도 수준."""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class ThrottleCircuitBreakerBridge:
    """
    Throttle과 Circuit Breaker 간 데이터 공유 브릿지.

    RTT 데이터를 CB 장애 감지에 활용하고,
    CB 상태를 Throttle limit 조정에 반영합니다.
    """

    # RTT 기반 CB 연동 임계값
    SLA_WARNING_MS = 200  # RTT >= 200ms → 경고
    SLA_CRITICAL_MS = 500  # RTT >= 500ms → CB failure 고려
    GRADIENT_WARNING_THRESHOLD = 0.3  # Gradient > 0.3 → 장애 조짐

    def __init__(
        self,
        sla_warning_ms: int | None = None,
        sla_critical_ms: int | None = None,
        gradient_warning_threshold: float | None = None,
    ):
        """
        초기화.

        Args:
            sla_warning_ms: SLA Warning 임계값 (ms)
            sla_critical_ms: SLA Critical 임계값 (ms)
            gradient_warning_threshold: Gradient 경고 임계값
        """
        self.sla_warning_ms = sla_warning_ms or self.SLA_WARNING_MS
        self.sla_critical_ms = sla_critical_ms or self.SLA_CRITICAL_MS
        self.gradient_warning_threshold = (
            gradient_warning_threshold or self.GRADIENT_WARNING_THRESHOLD
        )

        # 서비스별 메트릭 저장
        self._metrics: dict[str, ServiceHealthMetrics] = {}
        self._lock = threading.RLock()

        # 외부 서비스 참조 (lazy loading)
        self._cb_service: CircuitBreakerService | None = None
        self._throttle_registry: ThrottleRegistry | None = None

    def _get_or_create_metrics(self, service_name: str) -> ServiceHealthMetrics:
        """서비스별 메트릭 가져오기 또는 생성."""
        if service_name not in self._metrics:
            self._metrics[service_name] = ServiceHealthMetrics(
                service_name=service_name
            )
        return self._metrics[service_name]

    def record_rtt(
        self,
        service_name: str,
        rtt_ms: float,
        gradient: float | None = None,
    ) -> RTTSeverity:
        """
        RTT 데이터 기록 및 CB 피드백.

        RTT가 임계값을 초과하면 CB에 failure 신호를 보낼 수 있습니다.

        Args:
            service_name: 서비스 이름
            rtt_ms: 응답 시간 (ms)
            gradient: RTT 변화율 (옵션)

        Returns:
            RTT 심각도 수준
        """
        with self._lock:
            metrics = self._get_or_create_metrics(service_name)

            # RTT 메트릭 업데이트
            metrics.rtt.current_rtt_ms = rtt_ms
            metrics.rtt.last_updated = datetime.now(timezone.utc)
            metrics.rtt.sample_count += 1

            if gradient is not None:
                metrics.rtt.gradient = gradient

            # 심각도 판단
            severity = self._evaluate_rtt_severity(rtt_ms, gradient or 0.0)

            # SLA 위반 플래그 업데이트
            metrics.sla_warning_triggered = severity in (
                RTTSeverity.WARNING,
                RTTSeverity.CRITICAL,
            )
            metrics.sla_critical_triggered = severity == RTTSeverity.CRITICAL

            # CB 피드백 (Critical 시 failure 고려)
            if severity == RTTSeverity.CRITICAL:
                self._notify_cb_rtt_critical(service_name, rtt_ms)

            return severity

    def _evaluate_rtt_severity(
        self,
        rtt_ms: float,
        gradient: float,
    ) -> RTTSeverity:
        """RTT 심각도 평가."""
        if rtt_ms >= self.sla_critical_ms:
            return RTTSeverity.CRITICAL
        elif rtt_ms >= self.sla_warning_ms:
            return RTTSeverity.WARNING
        elif gradient > self.gradient_warning_threshold:
            # Gradient 급증도 경고 대상
            return RTTSeverity.WARNING
        return RTTSeverity.NORMAL

    def _notify_cb_rtt_critical(self, service_name: str, rtt_ms: float) -> None:
        """
        RTT Critical 시 CB에 알림.

        RTT가 지속적으로 Critical이면 CB failure로 카운트할 수 있습니다.
        단, 단일 RTT Critical로 바로 CB를 열지는 않습니다.
        """
        logger.warning(
            f"[ThrottleCBBridge] RTT CRITICAL for '{service_name}': "
            f"{rtt_ms:.1f}ms >= {self.sla_critical_ms}ms"
        )

        # 이벤트 발행 (CB에서 구독하여 처리)
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.emit(
                EventType.THROTTLE_SLA_CRITICAL,
                {
                    "service_name": service_name,
                    "current_rtt_ms": rtt_ms,
                    "threshold_ms": self.sla_critical_ms,
                    "source": "throttle_cb_bridge",
                },
                source="throttle_cb_bridge",
            )
        except Exception as e:
            logger.debug(f"[ThrottleCBBridge] Event publish failed: {e}")

    def record_success(self, service_name: str, rtt_ms: float | None = None) -> None:
        """
        성공 요청 기록 (RTT 정상).

        RTT가 정상 범위이면 CB success로 카운트합니다.

        Args:
            service_name: 서비스 이름
            rtt_ms: 응답 시간 (옵션)
        """
        if rtt_ms is not None:
            severity = self.record_rtt(service_name, rtt_ms)

            # RTT가 정상이면 CB success 기록 고려
            if severity == RTTSeverity.NORMAL:
                self._notify_cb_success(service_name)

    def _notify_cb_success(self, service_name: str) -> None:
        """CB에 success 신호."""
        # 현재는 로깅만 수행 (CB에서 직접 record_success 호출 권장)
        logger.debug(f"[ThrottleCBBridge] RTT normal for '{service_name}'")

    def get_cb_state(self, service_name: str) -> str | None:
        """
        서비스의 CB 상태 조회.

        Args:
            service_name: 서비스 이름

        Returns:
            CB 상태 ("closed", "open", "half_open") 또는 None
        """
        try:
            from selfhealing.services.circuit_breaker import get_circuit_breaker_service

            cb_service = get_circuit_breaker_service()
            return cb_service.get_state(service_name)
        except Exception as e:
            logger.debug(f"[ThrottleCBBridge] Failed to get CB state: {e}")
            return None

    def get_cb_info(self, service_name: str) -> dict[str, Any] | None:
        """
        서비스의 CB 정보 조회 (상세).

        Args:
            service_name: 서비스 이름

        Returns:
            CB 상태 정보 딕셔너리 또는 None
        """
        try:
            from selfhealing.services.circuit_breaker import get_circuit_breaker_service

            cb_service = get_circuit_breaker_service()
            state = cb_service.get_or_create_state(service_name)

            return {
                "service_name": service_name,
                "state": state.state,
                "failure_count": state.failure_count,
                "success_count": state.success_count,
                "opened_at": state.opened_at.isoformat() if state.opened_at else None,
            }
        except Exception as e:
            logger.debug(f"[ThrottleCBBridge] Failed to get CB info: {e}")
            return None

    def should_record_as_cb_failure(
        self,
        service_name: str,
        rtt_ms: float,
        consecutive_critical_count: int = 3,
    ) -> bool:
        """
        RTT 기반으로 CB failure로 기록해야 하는지 판단.

        연속 Critical 횟수가 임계값을 초과하면 CB failure로 기록합니다.

        Args:
            service_name: 서비스 이름
            rtt_ms: 응답 시간 (ms)
            consecutive_critical_count: 연속 Critical 횟수 임계값

        Returns:
            CB failure로 기록해야 하는지 여부
        """
        with self._lock:
            metrics = self._get_or_create_metrics(service_name)

            # Critical 횟수 기반 판단 (간단한 구현)
            if rtt_ms >= self.sla_critical_ms:
                # 실제 구현에서는 연속 횟수를 추적해야 함
                # 여기서는 단순히 Critical이면 failure 고려
                return True

            return False

    def get_service_health(self, service_name: str) -> ServiceHealthMetrics:
        """
        서비스 건강 상태 통합 메트릭 조회.

        Args:
            service_name: 서비스 이름

        Returns:
            통합 메트릭
        """
        with self._lock:
            metrics = self._get_or_create_metrics(service_name)

            # CB 상태 업데이트
            cb_info = self.get_cb_info(service_name)
            if cb_info:
                metrics.cb_state = cb_info["state"]
                metrics.cb_failure_count = cb_info["failure_count"]
                metrics.cb_success_count = cb_info["success_count"]

            # Throttle 상태 업데이트
            try:
                from selfhealing.services.throttle.registry import get_throttle_registry

                registry = get_throttle_registry()
                throttle_state = registry.get_service_state(service_name)
                if throttle_state:
                    metrics.throttle_current_limit = throttle_state["current_limit"]
                    metrics.throttle_original_limit = throttle_state["original_limit"]
            except Exception:
                pass

            return metrics

    def get_all_service_health(self) -> list[ServiceHealthMetrics]:
        """
        모든 서비스 건강 상태 조회.

        Returns:
            서비스별 메트릭 리스트
        """
        with self._lock:
            return [
                self.get_service_health(name)
                for name in list(self._metrics.keys())
            ]

    def reset(self) -> None:
        """브릿지 초기화 (테스트용)."""
        with self._lock:
            self._metrics.clear()
            self._cb_service = None
            self._throttle_registry = None

        logger.info("[ThrottleCBBridge] Bridge reset")


# =============================================================================
# Singleton Factory
# =============================================================================

_bridge: ThrottleCircuitBreakerBridge | None = None
_bridge_lock = threading.Lock()


def get_throttle_cb_bridge(
    sla_warning_ms: int | None = None,
    sla_critical_ms: int | None = None,
) -> ThrottleCircuitBreakerBridge:
    """
    전역 ThrottleCircuitBreakerBridge 인스턴스 가져오기.

    Thread-safe 싱글톤 패턴.

    Args:
        sla_warning_ms: SLA Warning 임계값 (최초 생성 시에만 적용)
        sla_critical_ms: SLA Critical 임계값 (최초 생성 시에만 적용)

    Returns:
        ThrottleCircuitBreakerBridge 인스턴스
    """
    global _bridge

    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                _bridge = ThrottleCircuitBreakerBridge(
                    sla_warning_ms=sla_warning_ms,
                    sla_critical_ms=sla_critical_ms,
                )

    return _bridge


def reset_throttle_cb_bridge() -> None:
    """브릿지 초기화 (테스트용)."""
    global _bridge

    with _bridge_lock:
        if _bridge is not None:
            _bridge.reset()
        _bridge = None
