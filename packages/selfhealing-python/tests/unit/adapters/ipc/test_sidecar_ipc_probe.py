"""
SidecarIPCProbe 단위 테스트.

테스트 항목:
- 헬스 체크 수행
- 상태 판단 로직
- 메트릭 수집
"""

from __future__ import annotations

from selfhealing.adapters.ipc.sidecar_ipc_probe import (
    HealthStatus,
    IPCHealthMetrics,
    SidecarIPCProbe,
    SidecarProbeResult,
    get_sidecar_ipc_probe,
    reset_sidecar_ipc_probe,
)


class TestHealthStatus:
    """HealthStatus enum 테스트."""

    def test_status_values(self):
        """상태 값 확인."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.UNKNOWN.value == "unknown"


class TestSidecarProbeResult:
    """SidecarProbeResult 테스트."""

    def test_create_result(self):
        """결과 생성."""
        result = SidecarProbeResult(
            status=HealthStatus.HEALTHY,
            message="All systems operational",
        )

        assert result.status == HealthStatus.HEALTHY
        assert result.message == "All systems operational"
        assert result.timestamp is not None
        assert result.latency_ms == 0.0

    def test_to_dict(self):
        """딕셔너리 변환."""
        result = SidecarProbeResult(
            status=HealthStatus.DEGRADED,
            message="High latency",
            latency_ms=150.5,
            details={"avg_latency": 150.5},
        )

        d = result.to_dict()

        assert d["status"] == "degraded"
        assert d["message"] == "High latency"
        assert d["latency_ms"] == 150.5
        assert d["details"]["avg_latency"] == 150.5


class TestIPCHealthMetrics:
    """IPCHealthMetrics 테스트."""

    def test_initial_metrics(self):
        """초기 메트릭."""
        metrics = IPCHealthMetrics()

        assert metrics.uds_active_connections == 0
        assert metrics.grpc_active_connections == 0
        assert metrics.total_requests == 0
        assert metrics.error_count == 0

    def test_error_rate_zero(self):
        """요청 없을 때 에러율 0."""
        metrics = IPCHealthMetrics()

        assert metrics.error_rate == 0.0

    def test_error_rate_calculation(self):
        """에러율 계산."""
        metrics = IPCHealthMetrics(
            total_requests=100,
            error_count=5,
        )

        assert metrics.error_rate == 0.05

    def test_to_dict(self):
        """딕셔너리 변환."""
        metrics = IPCHealthMetrics(
            uds_active_connections=3,
            grpc_active_connections=2,
            total_requests=1000,
            error_count=10,
            avg_latency_ms=5.5,
            cache_hit_ratio=0.85,
        )

        d = metrics.to_dict()

        assert d["uds_active_connections"] == 3
        assert d["grpc_active_connections"] == 2
        assert d["total_requests"] == 1000
        assert d["error_count"] == 10
        assert d["error_rate"] == 0.01
        assert d["avg_latency_ms"] == 5.5
        assert d["cache_hit_ratio"] == 0.85


class TestSidecarIPCProbe:
    """SidecarIPCProbe 테스트."""

    def test_init(self):
        """프로브 초기화."""
        probe = SidecarIPCProbe()

        assert probe.check_interval_seconds == 30.0
        assert probe.history_size == 100

    def test_init_custom_interval(self):
        """커스텀 체크 간격."""
        probe = SidecarIPCProbe(check_interval_seconds=60.0)

        assert probe.check_interval_seconds == 60.0

    def test_check_returns_result(self):
        """check() 메서드가 SidecarProbeResult 반환."""
        probe = SidecarIPCProbe()

        result = probe.check()

        assert isinstance(result, SidecarProbeResult)
        assert result.status in [
            HealthStatus.HEALTHY,
            HealthStatus.DEGRADED,
            HealthStatus.UNHEALTHY,
            HealthStatus.UNKNOWN,
        ]

    def test_check_includes_latency(self):
        """체크 결과에 지연 시간 포함."""
        probe = SidecarIPCProbe()

        result = probe.check()

        assert result.latency_ms >= 0

    def test_collect_metrics(self):
        """메트릭 수집."""
        probe = SidecarIPCProbe()

        metrics = probe.collect_metrics()

        assert isinstance(metrics, IPCHealthMetrics)

    def test_get_history(self):
        """히스토리 조회."""
        probe = SidecarIPCProbe()

        probe.check()
        probe.check()

        history = probe.get_history()

        assert len(history) == 2
        assert "status" in history[0]

    def test_get_summary(self):
        """요약 정보 조회."""
        probe = SidecarIPCProbe()

        probe.check()

        summary = probe.get_summary()

        assert "current_status" in summary
        assert "metrics" in summary
        assert "healthy_count" in summary

    def test_evaluate_health_returns_status(self):
        """헬스 평가가 상태와 메시지 반환."""
        probe = SidecarIPCProbe()

        metrics = IPCHealthMetrics(
            total_requests=1000,
            error_count=5,  # 0.5% error rate
            avg_latency_ms=5.0,
        )

        status, message = probe._evaluate_health(metrics)

        assert isinstance(status, HealthStatus)
        assert isinstance(message, str)

    def test_evaluate_health_unhealthy_high_error_rate(self):
        """높은 에러율로 인한 비정상 상태."""
        probe = SidecarIPCProbe()

        metrics = IPCHealthMetrics(
            total_requests=100,
            error_count=10,  # 10% error rate > 5% threshold
            avg_latency_ms=5.0,
        )

        status, message = probe._evaluate_health(metrics)

        assert status == HealthStatus.UNHEALTHY
        assert "error rate" in message.lower()

    def test_evaluate_health_unhealthy_very_high_latency(self):
        """매우 높은 지연 시간으로 인한 비정상 상태."""
        probe = SidecarIPCProbe()

        metrics = IPCHealthMetrics(
            total_requests=100,
            error_count=0,
            avg_latency_ms=600.0,  # > 500ms threshold
        )

        status, message = probe._evaluate_health(metrics)

        assert status == HealthStatus.UNHEALTHY
        assert "latency" in message.lower()


class TestGlobalSidecarIPCProbe:
    """싱글톤 인스턴스 테스트."""

    def teardown_method(self):
        """테스트 후 싱글톤 리셋."""
        reset_sidecar_ipc_probe()

    def test_singleton(self):
        """싱글톤 인스턴스 반환."""
        probe1 = get_sidecar_ipc_probe()
        probe2 = get_sidecar_ipc_probe()

        assert probe1 is probe2

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        probe1 = get_sidecar_ipc_probe()

        reset_sidecar_ipc_probe()

        probe2 = get_sidecar_ipc_probe()

        assert probe1 is not probe2
