"""
Recovery Metrics 단위 테스트.

Phase 5.5: Prometheus 지표 수집 테스트

테스트 항목:
- RecoveryMetricsRecorder 메트릭 기록
- 메트릭 초기화 및 레이블
"""


import pytest

from selfhealing.services.coordination.recovery_metrics import (
    PROMETHEUS_AVAILABLE,
    RecoveryMetricsRecorder,
    get_recovery_metrics_recorder,
    reset_recovery_metrics_recorder,
)

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def metrics_recorder():
    """메트릭 레코더."""
    reset_recovery_metrics_recorder()
    return RecoveryMetricsRecorder()


# =============================================================================
# RecoveryMetricsRecorder Tests
# =============================================================================

class TestRecoveryMetricsRecorder:
    """RecoveryMetricsRecorder 테스트."""

    def test_record_session_started(self, metrics_recorder):
        """세션 시작 메트릭 기록 테스트."""
        # 예외 없이 실행되어야 함
        metrics_recorder.record_session_started(
            namespace="global",
            trigger_level="LEVEL_3",
        )

    def test_record_session_completed_success(self, metrics_recorder):
        """세션 완료 메트릭 기록 테스트."""
        metrics_recorder.record_session_completed(
            namespace="global",
            trigger_level="LEVEL_3",
            status="completed",
            duration_seconds=300.5,
        )

    def test_record_session_completed_failed(self, metrics_recorder):
        """세션 실패 메트릭 기록 테스트."""
        metrics_recorder.record_session_completed(
            namespace="seoul",
            trigger_level="LEVEL_4",
            status="failed",
            duration_seconds=120.0,
        )

    def test_record_step_started(self, metrics_recorder):
        """스텝 시작 메트릭 기록 테스트."""
        metrics_recorder.record_step_started(
            namespace="global",
            step_type="budget_reset",
        )

    def test_record_step_completed_success(self, metrics_recorder):
        """스텝 성공 메트릭 기록 테스트."""
        metrics_recorder.record_step_completed(
            namespace="global",
            step_type="budget_reset",
            success=True,
            duration_seconds=5.5,
        )

    def test_record_step_completed_failure(self, metrics_recorder):
        """스텝 실패 메트릭 기록 테스트."""
        metrics_recorder.record_step_completed(
            namespace="global",
            step_type="health_check",
            success=False,
            duration_seconds=60.0,
        )

    def test_record_step_completed_idempotent_skip(self, metrics_recorder):
        """멱등성 스킵 메트릭 기록 테스트."""
        metrics_recorder.record_step_completed(
            namespace="global",
            step_type="canary_resume",
            success=True,
            duration_seconds=0.1,
            idempotent_skip=True,
        )

    def test_record_step_retry(self, metrics_recorder):
        """재시도 메트릭 기록 테스트."""
        metrics_recorder.record_step_retry(
            namespace="global",
            step_type="governance_normal",
        )

    def test_record_circuit_breaker_trip(self, metrics_recorder):
        """서킷 브레이커 트립 메트릭 기록 테스트."""
        metrics_recorder.record_circuit_breaker_trip(
            namespace="global",
            reason="failure_threshold_exceeded",
        )

    def test_update_pending_approvals(self, metrics_recorder):
        """승인 대기 메트릭 업데이트 테스트."""
        metrics_recorder.update_pending_approvals(
            namespace="global",
            count=3,
            stale_count=1,
        )


# =============================================================================
# Singleton Tests
# =============================================================================

class TestSingleton:
    """싱글톤 테스트."""

    def test_get_singleton(self):
        """싱글톤 획득 테스트."""
        reset_recovery_metrics_recorder()

        recorder1 = get_recovery_metrics_recorder()
        recorder2 = get_recovery_metrics_recorder()

        assert recorder1 is recorder2

    def test_reset_singleton(self):
        """싱글톤 리셋 테스트."""
        recorder1 = get_recovery_metrics_recorder()

        reset_recovery_metrics_recorder()

        recorder2 = get_recovery_metrics_recorder()

        assert recorder1 is not recorder2


# =============================================================================
# Prometheus Availability Tests
# =============================================================================

class TestPrometheusAvailability:
    """Prometheus 사용 가능 여부 테스트."""

    def test_prometheus_available_flag_exists(self):
        """PROMETHEUS_AVAILABLE 플래그 존재 확인."""
        assert isinstance(PROMETHEUS_AVAILABLE, bool)

    def test_recorder_works_without_prometheus(self, metrics_recorder):
        """Prometheus 없이도 레코더 작동 확인."""
        # 모든 메트릭 기록 메서드가 예외 없이 실행되어야 함
        metrics_recorder.record_session_started("ns1", "LEVEL_1")
        metrics_recorder.record_session_completed("ns1", "LEVEL_1", "completed", 100.0)
        metrics_recorder.record_step_started("ns1", "budget_reset")
        metrics_recorder.record_step_completed("ns1", "budget_reset", True, 5.0)
        metrics_recorder.record_circuit_breaker_trip("ns1", "test")
        metrics_recorder.update_pending_approvals("ns1", 1, 0)
        metrics_recorder.record_step_retry("ns1", "health_check")

        # 예외 없이 완료


# =============================================================================
# Edge Cases Tests
# =============================================================================

class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_empty_namespace(self, metrics_recorder):
        """빈 namespace 테스트."""
        metrics_recorder.record_session_started(
            namespace="",
            trigger_level="LEVEL_1",
        )

    def test_zero_duration(self, metrics_recorder):
        """duration이 0일 때 테스트."""
        metrics_recorder.record_session_completed(
            namespace="global",
            trigger_level="LEVEL_3",
            status="completed",
            duration_seconds=0.0,
        )

    def test_negative_count(self, metrics_recorder):
        """음수 count 테스트."""
        # 음수 값도 허용되어야 함 (게이지 감소 시)
        metrics_recorder.update_pending_approvals("global", -1, 0)

    def test_special_characters_in_labels(self, metrics_recorder):
        """레이블에 특수 문자가 있을 때 테스트."""
        metrics_recorder.record_session_started(
            namespace="ns/with/slashes",
            trigger_level="LEVEL_3",
        )
