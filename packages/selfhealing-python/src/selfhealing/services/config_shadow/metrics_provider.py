"""
Time Series Metrics Provider.

시뮬레이션 시 과거 Raw 데이터 조회를 위한 Protocol 및 Mock 구현.
기존 MetricsProvider(core/auto_rollback_guard.py)는 "현재값"만 반환하므로
과거 시간 범위의 시계열이 필요한 Config Shadow용으로 별도 정의.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class TimeSeriesMetricsProvider(Protocol):
    """시계열 메트릭 제공자. 시뮬레이션 시 과거 Raw 데이터 조회.

    Implementations:
    - MockTimeSeriesProvider: 테스트 및 개발용
    - PrometheusTimeSeriesProvider: Prometheus PromQL 기반 (향후)
    - DatadogTimeSeriesProvider: Datadog Metrics API 기반 (향후)
    """

    def query_error_rate(
        self,
        service_name: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        """시간 범위의 에러율 시계열을 반환한다.

        Args:
            service_name: 대상 서비스
            start: 조회 시작 시각 (UTC)
            end: 조회 종료 시각 (UTC)
            step_seconds: 시계열 간격 (기본 60초)

        Returns:
            (timestamp, error_rate) 튜플 리스트. error_rate는 0.0 ~ 1.0.
        """
        ...

    def query_request_rate(
        self,
        service_name: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        """시간 범위의 요청률(RPS) 시계열을 반환한다."""
        ...


class MockTimeSeriesProvider:
    """테스트용 시계열 메트릭 제공자.

    임의의 시계열 데이터를 주입하여 시뮬레이터 로직을 검증한다.
    프로덕션에서는 Prometheus/Datadog 어댑터로 교체.
    """

    def __init__(self, data: dict[str, list[tuple[datetime, float]]] | None = None):
        self._data = data or {}

    def query_error_rate(
        self,
        service_name: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        key = f"{service_name}:error_rate"
        return [(ts, val) for ts, val in self._data.get(key, []) if start <= ts < end]

    def query_request_rate(
        self,
        service_name: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        key = f"{service_name}:request_rate"
        return [(ts, val) for ts, val in self._data.get(key, []) if start <= ts < end]
