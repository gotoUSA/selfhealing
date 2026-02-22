"""
Error Rate Provider for Load Shedding.

서비스별 에러율을 추적하고 제공하는 인터페이스입니다.
기본 구현은 메모리 기반이며, 실제 환경에서는 메트릭 시스템과 연동합니다.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


class ErrorRateProvider:
    """
    서비스별 에러율을 제공하는 인터페이스.

    기본 구현은 메모리 기반. 실제 환경에서는 메트릭 시스템 연동.
    """

    def __init__(self):
        self._error_rates: dict[str, float] = {}
        self._success_counts: dict[str, int] = {}
        self._failure_counts: dict[str, int] = {}

    def get_error_rate(self, service_id: str) -> float:
        """
        서비스의 현재 에러율 조회 (0~100%).

        Args:
            service_id: 서비스 ID

        Returns:
            에러율 (0~100)
        """
        return self._error_rates.get(service_id, 0.0)

    def set_error_rate(self, service_id: str, error_rate: float) -> None:
        """
        서비스의 에러율 설정 (테스트용).

        Args:
            service_id: 서비스 ID
            error_rate: 에러율 (0~100)
        """
        if not (0.0 <= error_rate <= 100.0):
            raise ValueError(f"error_rate must be between 0 and 100, got {error_rate}")
        self._error_rates[service_id] = error_rate

    def record_success(self, service_id: str) -> None:
        """성공 기록."""
        self._success_counts[service_id] = self._success_counts.get(service_id, 0) + 1
        self._update_error_rate(service_id)

    def record_failure(self, service_id: str) -> None:
        """실패 기록."""
        self._failure_counts[service_id] = self._failure_counts.get(service_id, 0) + 1
        self._update_error_rate(service_id)

    def _update_error_rate(self, service_id: str) -> None:
        """에러율 재계산."""
        success = self._success_counts.get(service_id, 0)
        failure = self._failure_counts.get(service_id, 0)
        total = success + failure
        if total > 0:
            self._error_rates[service_id] = (failure / total) * 100.0

    def reset(self, service_id: str | None = None) -> None:
        """에러율 초기화."""
        if service_id:
            self._error_rates.pop(service_id, None)
            self._success_counts.pop(service_id, None)
            self._failure_counts.pop(service_id, None)
        else:
            self._error_rates.clear()
            self._success_counts.clear()
            self._failure_counts.clear()
