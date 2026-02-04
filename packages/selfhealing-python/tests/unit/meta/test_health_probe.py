"""
Health Probe 테스트.

HealthProbeManager 및 각종 Probe 테스트.
"""

from datetime import datetime, timezone

import pytest

from selfhealing.meta.config import MetaWatchdogSettings
from selfhealing.meta.health_probe import (
    CircuitBreakerProbe,
    DLQProbe,
    HealthProbe,
    HealthProbeManager,
    HealthStatus,
    ProbeResult,
    RecoveryPipelineProbe,
    RedisProbe,
)


class TestHealthStatus:
    """HealthStatus 열거형 테스트."""

    def test_values(self):
        """값 확인."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.UNKNOWN.value == "unknown"


class TestProbeResult:
    """ProbeResult 데이터클래스 테스트."""

    def test_creation(self):
        """생성 테스트."""
        result = ProbeResult(
            component="test",
            status=HealthStatus.HEALTHY,
            latency_ms=5.0,
            timestamp=datetime.now(timezone.utc),
        )

        assert result.component == "test"
        assert result.status == HealthStatus.HEALTHY
        assert result.latency_ms == 5.0
        assert result.details == {}
        assert result.error is None

    def test_with_error(self):
        """에러 포함 생성 테스트."""
        result = ProbeResult(
            component="test",
            status=HealthStatus.UNHEALTHY,
            latency_ms=10.0,
            timestamp=datetime.now(timezone.utc),
            error="Connection failed",
        )

        assert result.status == HealthStatus.UNHEALTHY
        assert result.error == "Connection failed"


class TestCircuitBreakerProbe:
    """CircuitBreakerProbe 테스트."""

    def test_component_name(self):
        """컴포넌트 이름 확인."""
        probe = CircuitBreakerProbe()
        assert probe.component_name == "circuit_breaker"

    def test_probe_returns_result(self):
        """프로브가 결과 반환."""
        probe = CircuitBreakerProbe()
        result = probe.probe()

        assert isinstance(result, ProbeResult)
        assert result.component == "circuit_breaker"
        assert result.latency_ms >= 0


class TestDLQProbe:
    """DLQProbe 테스트."""

    def test_component_name(self):
        """컴포넌트 이름 확인."""
        probe = DLQProbe()
        assert probe.component_name == "dlq"

    def test_probe_returns_result(self):
        """프로브가 결과 반환."""
        probe = DLQProbe()
        result = probe.probe()

        assert isinstance(result, ProbeResult)
        assert result.component == "dlq"


class TestRecoveryPipelineProbe:
    """RecoveryPipelineProbe 테스트."""

    def test_component_name(self):
        """컴포넌트 이름 확인."""
        probe = RecoveryPipelineProbe()
        assert probe.component_name == "recovery_pipeline"

    def test_probe_returns_result(self):
        """프로브가 결과 반환."""
        probe = RecoveryPipelineProbe()
        result = probe.probe()

        assert isinstance(result, ProbeResult)
        assert result.component == "recovery_pipeline"


class TestRedisProbe:
    """RedisProbe 테스트."""

    def test_component_name(self):
        """컴포넌트 이름 확인."""
        probe = RedisProbe()
        assert probe.component_name == "redis"

    def test_probe_returns_result(self):
        """프로브가 결과 반환 (Redis 없어도)."""
        probe = RedisProbe()
        result = probe.probe()

        assert isinstance(result, ProbeResult)
        assert result.component == "redis"
        # Redis 연결 없으면 UNKNOWN 또는 UNHEALTHY


class DummyHealthyProbe(HealthProbe):
    """테스트용 Healthy 프로브."""

    @property
    def component_name(self) -> str:
        return "dummy_healthy"

    def probe(self) -> ProbeResult:
        return ProbeResult(
            component=self.component_name,
            status=HealthStatus.HEALTHY,
            latency_ms=1.0,
            timestamp=datetime.now(timezone.utc),
        )


class DummyUnhealthyProbe(HealthProbe):
    """테스트용 Unhealthy 프로브."""

    @property
    def component_name(self) -> str:
        return "dummy_unhealthy"

    def probe(self) -> ProbeResult:
        return ProbeResult(
            component=self.component_name,
            status=HealthStatus.UNHEALTHY,
            latency_ms=1.0,
            timestamp=datetime.now(timezone.utc),
            error="Simulated failure",
        )


class DummyDegradedProbe(HealthProbe):
    """테스트용 Degraded 프로브."""

    @property
    def component_name(self) -> str:
        return "dummy_degraded"

    def probe(self) -> ProbeResult:
        return ProbeResult(
            component=self.component_name,
            status=HealthStatus.DEGRADED,
            latency_ms=1.0,
            timestamp=datetime.now(timezone.utc),
        )


class TestHealthProbeManager:
    """HealthProbeManager 테스트."""

    def test_default_probes(self):
        """기본 프로브 생성 테스트."""
        manager = HealthProbeManager()

        # 기본 프로브가 있어야 함
        results = manager.probe_all()
        assert len(results) > 0

    def test_custom_probes(self):
        """커스텀 프로브 테스트."""
        probes = [DummyHealthyProbe(), DummyUnhealthyProbe()]
        manager = HealthProbeManager(probes=probes)

        results = manager.probe_all()

        assert "dummy_healthy" in results
        assert "dummy_unhealthy" in results

    def test_add_probe(self):
        """프로브 추가 테스트."""
        manager = HealthProbeManager(probes=[])

        manager.add_probe(DummyHealthyProbe())

        results = manager.probe_all()
        assert "dummy_healthy" in results

    def test_remove_probe(self):
        """프로브 제거 테스트."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe()])

        removed = manager.remove_probe("dummy_healthy")

        assert removed is True
        results = manager.probe_all()
        assert "dummy_healthy" not in results

    def test_remove_nonexistent_probe(self):
        """존재하지 않는 프로브 제거 테스트."""
        manager = HealthProbeManager(probes=[])

        removed = manager.remove_probe("nonexistent")

        assert removed is False

    def test_probe_all_returns_results(self):
        """probe_all이 결과 반환."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe()])

        results = manager.probe_all()

        assert isinstance(results, dict)
        assert "dummy_healthy" in results
        assert results["dummy_healthy"].status == HealthStatus.HEALTHY

    def test_get_overall_status_healthy(self):
        """전체 상태: 모두 healthy."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe()])
        manager.probe_all()

        status = manager.get_overall_status()

        assert status == HealthStatus.HEALTHY

    def test_get_overall_status_unhealthy(self):
        """전체 상태: 하나라도 unhealthy."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe(), DummyUnhealthyProbe()])
        manager.probe_all()

        status = manager.get_overall_status()

        assert status == HealthStatus.UNHEALTHY

    def test_get_overall_status_degraded(self):
        """전체 상태: degraded."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe(), DummyDegradedProbe()])
        manager.probe_all()

        status = manager.get_overall_status()

        assert status == HealthStatus.DEGRADED

    def test_get_overall_status_unknown(self):
        """전체 상태: 결과 없음."""
        manager = HealthProbeManager(probes=[])

        status = manager.get_overall_status()

        assert status == HealthStatus.UNKNOWN

    def test_get_last_results(self):
        """마지막 결과 조회 테스트."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe()])

        # 프로브 전에는 빈 dict
        assert manager.get_last_results() == {}

        manager.probe_all()

        results = manager.get_last_results()
        assert "dummy_healthy" in results

    def test_get_component_status(self):
        """특정 컴포넌트 상태 조회 테스트."""
        manager = HealthProbeManager(probes=[DummyHealthyProbe()])
        manager.probe_all()

        status = manager.get_component_status("dummy_healthy")

        assert status == HealthStatus.HEALTHY

    def test_get_component_status_not_found(self):
        """존재하지 않는 컴포넌트 상태 조회."""
        manager = HealthProbeManager(probes=[])
        manager.probe_all()

        status = manager.get_component_status("nonexistent")

        assert status is None

    def test_is_running(self):
        """실행 상태 확인 테스트."""
        manager = HealthProbeManager(probes=[])

        assert manager.is_running() is False

        manager.start()
        assert manager.is_running() is True

        manager.stop()
        assert manager.is_running() is False
