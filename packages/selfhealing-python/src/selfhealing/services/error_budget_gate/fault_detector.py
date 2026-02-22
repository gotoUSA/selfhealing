"""
Error Budget Gate - Fault Detector.

Gate 내부 장애 감지기. Error Budget 서비스(Redis/DB)에 반복 접근 실패 시,
매번 타임아웃을 기다리지 않고 즉시 Fail-Open으로 응답합니다.

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class GateFaultState(str, Enum):
    """Gate Fault Detector 상태."""

    HEALTHY = "healthy"  # 정상 - Error Budget 조회 가능
    DEGRADED = "degraded"  # 장애 - 빠른 Fail-Open
    RECOVERING = "recovering"  # 복구 시도 중


class GateFaultDetector:
    """
    Gate 내부 장애 감지기.

    Error Budget Gate가 Error Budget 서비스(Redis/DB)에 반복 접근 실패 시,
    매번 타임아웃을 기다리지 않고 즉시 Fail-Open으로 응답합니다.

    ⚠️ 주의: 이것은 메인 CircuitBreakerService와 다릅니다!
    - GateFaultDetector: Gate 내부용, 메모리 전용 (외부 의존성 없음)
    - CircuitBreakerService: 외부 API 호출용, 분산 환경 지원

    상태 전이:
    - HEALTHY: 정상 동작, 실패 시 카운트 증가
    - DEGRADED: failure_threshold 초과 시, 모든 요청 즉시 Fail-Open
    - RECOVERING: recovery_timeout 후, 한 번 시도하여 성공하면 HEALTHY로 복귀
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 30):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: datetime | None = None
        self._state = GateFaultState.HEALTHY
        self._lock = threading.RLock()

    def update_config(self, failure_threshold: int, recovery_timeout: int) -> None:
        """설정 동적 업데이트."""
        with self._lock:
            self._failure_threshold = failure_threshold
            self._recovery_timeout = recovery_timeout
            logger.info(
                "circuit_breaker.updated",
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )

    def can_execute(self) -> bool:
        """요청 실행 가능 여부."""
        with self._lock:
            if self._state == GateFaultState.HEALTHY:
                return True

            if self._state == GateFaultState.DEGRADED:
                # 복구 시간이 지났는지 확인
                if self._last_failure_time:
                    elapsed = (datetime.now(timezone.utc) - self._last_failure_time).total_seconds()
                    if elapsed >= self._recovery_timeout:
                        self._state = GateFaultState.RECOVERING
                        logger.info("gate_fault_detector.state_degraded_recovering_attempting")
                        return True
                return False

            # RECOVERING: 한 번 시도 허용
            return True

    def record_success(self) -> None:
        """성공 기록."""
        with self._lock:
            if self._state == GateFaultState.RECOVERING:
                logger.info("gate_fault_detector.state_recovering_healthy_recovered")
            self._state = GateFaultState.HEALTHY
            self._failure_count = 0

    def record_failure(self) -> None:
        """실패 기록."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now(timezone.utc)

            if self._state == GateFaultState.RECOVERING:
                # 복구 실패 - 다시 DEGRADED
                self._state = GateFaultState.DEGRADED
                logger.warning("gate_fault_detector.state_recovering_degraded_recovery")
            elif self._failure_count >= self._failure_threshold:
                self._state = GateFaultState.DEGRADED
                logger.warning(
                    "gate_fault_detector.state_healthy_degraded_failures",
                    _self=self._failure_count,
                    self_1=self._failure_threshold,
                )

    def get_status(self) -> dict[str, Any]:
        """현재 상태 조회."""
        with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "last_failure_time": (self._last_failure_time.isoformat() if self._last_failure_time else None),
            }

    def reset(self) -> None:
        """Gate Fault Detector 리셋."""
        with self._lock:
            self._state = GateFaultState.HEALTHY
            self._failure_count = 0
            self._last_failure_time = None
            logger.info("gate_fault_detector.reset_healthy_state")


__all__ = [
    "GateFaultState",
    "GateFaultDetector",
]
