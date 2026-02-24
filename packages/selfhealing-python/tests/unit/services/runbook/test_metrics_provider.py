"""
RunbookMetricsProvider Protocol의 런타임 체크 가능 여부 계약 검증.

테스트 대상: selfhealing.services.runbook.metrics_provider.RunbookMetricsProvider
"""

from __future__ import annotations

from selfhealing.services.runbook.metrics_provider import RunbookMetricsProvider


# =============================================================================
# Contract Tests
# =============================================================================


class TestRunbookMetricsProviderContract:
    """RunbookMetricsProvider Protocol 계약 검증."""

    def test_is_runtime_checkable(self):
        """RunbookMetricsProvider는 runtime_checkable Protocol이다."""
        assert hasattr(RunbookMetricsProvider, "__protocol_attrs__") or hasattr(RunbookMetricsProvider, "_is_runtime_protocol")

    def test_conforming_class_passes_isinstance_check(self):
        """Protocol 메서드를 구현한 클래스는 isinstance 체크를 통과한다."""

        class ConcreteProvider:
            def get_metric(
                self,
                metric_name: str,
                labels: dict[str, str] | None = None,
            ) -> float | None:
                return 0.0

            def get_metrics_snapshot(
                self,
                metric_names: list[str],
                labels: dict[str, str] | None = None,
            ) -> dict[str, float]:
                return {}

        provider = ConcreteProvider()
        assert isinstance(provider, RunbookMetricsProvider)

    def test_non_conforming_class_fails_isinstance_check(self):
        """Protocol 메서드가 없는 클래스는 isinstance 체크에 실패한다."""

        class EmptyClass:
            pass

        obj = EmptyClass()
        assert not isinstance(obj, RunbookMetricsProvider)
