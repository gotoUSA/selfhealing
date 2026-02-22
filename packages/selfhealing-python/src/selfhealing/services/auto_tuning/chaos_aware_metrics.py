"""
Chaos-Aware Metrics Adapter

Chaos 실험 인식 메트릭 어댑터.
실험 트래픽을 메트릭 수집에서 제외하여 AutoTuning 학습 오염 방지.
"""

from __future__ import annotations

from typing import Protocol

import structlog

logger = structlog.get_logger()


class ChaosAwareMetricsProtocol(Protocol):
    """메트릭 어댑터 프로토콜."""

    def collect_metrics(self, service: str, window_seconds: int) -> dict[str, float]:
        """메트릭 수집."""
        ...


class ChaosAwareMetricsAdapter:
    """
    Chaos 실험 인식 메트릭 어댑터.

    실험 트래픽을 메트릭 수집에서 제외하여 AutoTuning 학습 오염 방지.
    Delegate 패턴을 사용하여 기존 메트릭 어댑터를 래핑합니다.

    Usage:
        original_adapter = PrometheusMetricsAdapter()
        chaos_aware = ChaosAwareMetricsAdapter(original_adapter)

        # Chaos 실험 중에는 빈 메트릭 반환 → 조정 없음
        metrics = chaos_aware.collect_metrics("payment", window_seconds=60)
    """

    def __init__(
        self,
        delegate: ChaosAwareMetricsProtocol,
        skip_during_chaos: bool = True,
    ):
        """
        초기화.

        Args:
            delegate: 실제 메트릭 수집을 위임할 어댑터
            skip_during_chaos: Chaos 실험 중 메트릭 수집 스킵 여부 (기본: True)
        """
        self._delegate = delegate
        self._skip_during_chaos = skip_during_chaos

    def collect_metrics(
        self,
        service: str,
        window_seconds: int,
    ) -> dict[str, float]:
        """
        메트릭 수집 (Chaos 실험 중 스킵).

        Args:
            service: 대상 서비스
            window_seconds: 수집 윈도우 (초)

        Returns:
            메트릭 딕셔너리. Chaos 실험 중이면 빈 딕셔너리 반환.
        """
        if self._skip_during_chaos and self._is_chaos_experiment_running():
            logger.info(
                "chaos_aware_metrics.skipping_metrics_chaos_experiment",
                service=service,
            )
            return {}  # 빈 메트릭 반환 → 조정 없음

        return self._delegate.collect_metrics(service, window_seconds)

    def _is_chaos_experiment_running(self) -> bool:
        """Chaos 실험 실행 중인지 확인."""
        try:
            from selfhealing.services.chaos.scheduler import get_chaos_scheduler

            scheduler = get_chaos_scheduler()
            running = scheduler.get_running_experiments()
            return len(running) > 0
        except Exception as e:
            logger.debug(
                "chaos_aware_metrics.check_chaos_status",
                error=e,
            )
            return False

    def is_chaos_active(self) -> bool:
        """
        외부에서 Chaos 상태 확인용.

        Returns:
            True if any chaos experiment is currently running.
        """
        return self._is_chaos_experiment_running()

    def get_delegate(self) -> ChaosAwareMetricsProtocol:
        """
        원본 delegate 어댑터 반환.

        Chaos 상태와 무관하게 메트릭을 수집해야 할 때 사용.
        """
        return self._delegate

    def force_collect_metrics(
        self,
        service: str,
        window_seconds: int,
    ) -> dict[str, float]:
        """
        Chaos 상태와 무관하게 메트릭 강제 수집.

        디버깅/모니터링 목적으로만 사용.
        """
        return self._delegate.collect_metrics(service, window_seconds)


def wrap_with_chaos_awareness(
    adapter: ChaosAwareMetricsProtocol,
    skip_during_chaos: bool = True,
) -> ChaosAwareMetricsAdapter:
    """
    기존 메트릭 어댑터를 ChaosAware 버전으로 래핑.

    Args:
        adapter: 원본 메트릭 어댑터
        skip_during_chaos: Chaos 중 스킵 여부

    Returns:
        ChaosAwareMetricsAdapter 인스턴스

    Usage:
        prometheus = PrometheusMetricsAdapter()
        chaos_aware = wrap_with_chaos_awareness(prometheus)
    """
    return ChaosAwareMetricsAdapter(adapter, skip_during_chaos=skip_during_chaos)
