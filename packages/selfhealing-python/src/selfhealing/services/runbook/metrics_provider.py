"""
런북 패턴 매칭을 위한 범용 메트릭 제공자 Protocol.

기존 MetricsProvider(고정 메서드 3개), MetricSourceAdapter(DLQ 특화),
MetricsAdapterProtocol(라벨 없음)과 공존하는 런북 전용 범용 Protocol이다.

Reference:
    docs/self_healing/middleware_system/273_RUNBOOK_PATTERN_MATCHER.md §5
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RunbookMetricsProvider(Protocol):
    """런북 패턴 매칭을 위한 범용 메트릭 제공자.

    기존 MetricsProvider(core/auto_rollback_guard.py)는 고정 메서드 3개만 제공하고,
    MetricSourceAdapter(adapters/metrics/base.py)는 DLQ 특화되어 있으며,
    MetricsAdapterProtocol(services/auto_tuning/metrics_provider.py)은 라벨 필터가 없다.

    런북은 임의의 metric_name을 문자열로 받아서 평가해야 하므로,
    범용 get_metric(name, labels) 인터페이스가 필요하다.
    """

    def get_metric(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
    ) -> float | None:
        """단일 메트릭 값 조회.

        Args:
            metric_name: 메트릭 이름 (예: "error_rate", "db_pool_usage")
            labels: 라벨 필터 (예: {"service": "payment", "endpoint": "/checkout"})

        Returns:
            메트릭 값, 조회 실패 시 None
        """
        ...

    def get_metrics_snapshot(
        self,
        metric_names: list[str],
        labels: dict[str, str] | None = None,
    ) -> dict[str, float]:
        """여러 메트릭을 한 번에 조회 (배치).

        Proactive 경로에서 매 tick마다 N개 메트릭을 개별 호출하면
        Prometheus에 N번 쿼리가 발생하므로, 배치 조회가 필수.

        Args:
            metric_names: 조회할 메트릭 이름 목록
            labels: 공통 라벨 필터

        Returns:
            metric_name → value 딕셔너리 (조회 실패한 메트릭은 제외)
        """
        ...
