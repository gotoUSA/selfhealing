"""
Celery Tasks Audit Integration Tests

추가된 Celery task audit 헬퍼 함수 테스트.

테스트 대상:
1. log_config_apply_audit() - 설정 적용 audit
2. log_chaos_scheduler_audit() - Chaos 스케줄러 audit
3. log_governance_task_audit() - Governance task audit
4. log_traffic_aware_replay_audit() - Traffic-Aware Replay audit
5. log_drift_detection_audit() - Drift Detection audit
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_wal():
    """WAL mock fixture."""
    # 싱글톤 인스턴스 초기화
    import selfhealing.services.audit.base as base_module
    original_instance = base_module._wal_instance
    base_module._wal_instance = None

    # InMemoryAuditBuffer 싱글톤도 초기화 (테스트 격리)
    try:
        from selfhealing.audit.resilience import InMemoryAuditBuffer
        InMemoryAuditBuffer._instance = None
    except ImportError:
        pass

    # 실제 함수가 정의된 base 모듈에서 패치해야 함
    with patch("selfhealing.services.audit.base._get_wal") as mock:
        mock_wal = MagicMock()
        mock_wal.write.return_value = 12345
        mock.return_value = mock_wal
        # 테스트 시작 전 mock 상태 초기화
        mock_wal.reset_mock()
        yield mock_wal

    # 복원
    base_module._wal_instance = original_instance


@pytest.fixture
def mock_adapter():
    """Audit adapter mock fixture."""
    # storage_audit에서 import된 위치에서 패치해야 함
    with patch("selfhealing.services.audit.storage_audit._get_audit_adapter") as mock:
        mock_adapter = MagicMock()
        mock.return_value = mock_adapter
        yield mock_adapter


@pytest.fixture
def disable_wal():
    """WAL 비활성화 fixture."""
    from selfhealing.services.audit_helpers import disable_wal, enable_wal
    disable_wal()
    yield
    enable_wal()


# =============================================================================
# log_config_apply_audit Tests
# =============================================================================


class TestLogConfigApplyAudit:
    """log_config_apply_audit 함수 테스트."""

    def test_basic_config_apply_audit(self, mock_wal, mock_adapter):
        """기본 설정 적용 audit 기록."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        result = log_config_apply_audit(
            pending_id="pending-123",
            config_key="error_budget",
            old_value={"threshold": 5.0},
            new_value={"threshold": 3.0},
            status="applied",
            task_id="task-abc",
        )

        assert result == 12345
        mock_wal.write.assert_called_once()

        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "CONFIG_CHANGE"
        assert call_args["source"] == "ConfigApplyTask"
        assert call_args["details"]["pending_id"] == "pending-123"
        assert call_args["details"]["config_key"] == "error_budget"
        assert call_args["details"]["status"] == "applied"

    def test_config_apply_blocked_audit(self, mock_wal, mock_adapter):
        """설정 적용 차단 audit 기록."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        result = log_config_apply_audit(
            pending_id="pending-456",
            config_key="dlq",
            status="blocked",
            task_id="task-def",
            details={"reason": "emergency_mode_active"},
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["success"] is False  # blocked = not success
        assert call_args["details"]["status"] == "blocked"

    def test_config_apply_failed_audit(self, mock_wal, mock_adapter):
        """설정 적용 실패 audit 기록."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        result = log_config_apply_audit(
            pending_id="pending-789",
            config_key="circuit_breaker",
            status="failed",
            error_message="Redis connection failed",
            task_id="task-ghi",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error_message"] == "Redis connection failed"

    def test_config_apply_with_adapter(self, mock_wal, mock_adapter):
        """Adapter를 통한 기록 테스트."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        log_config_apply_audit(
            config_key="test",
            status="applied",
        )

        # Adapter record가 호출되어야 함
        mock_adapter.record.assert_called_once()


# =============================================================================
# log_chaos_scheduler_audit Tests
# =============================================================================


class TestLogChaosSchedulerAudit:
    """log_chaos_scheduler_audit 함수 테스트."""

    def test_chaos_scheduled_audit(self, mock_wal, mock_adapter):
        """Chaos 실험 스케줄링 audit 기록."""
        from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

        result = log_chaos_scheduler_audit(
            experiment_id="exp-123",
            experiment_name="latency-injection",
            action="scheduled",
            status="started",
            target_service="payment-service",
            task_id="task-chaos-1",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "CHAOS_EXPERIMENT_STARTED"
        assert call_args["source"] == "ChaosSchedulerTask"
        assert call_args["details"]["experiment_id"] == "exp-123"
        assert call_args["domain"] == "chaos"

    def test_chaos_completed_audit(self, mock_wal, mock_adapter):
        """Chaos 실험 완료 audit 기록."""
        from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

        result = log_chaos_scheduler_audit(
            experiment_id="exp-456",
            action="executed",
            status="completed",
            task_id="task-chaos-2",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "CHAOS_EXPERIMENT_COMPLETED"

    def test_chaos_cleanup_audit(self, mock_wal, mock_adapter):
        """Chaos 실험 정리 audit 기록."""
        from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

        result = log_chaos_scheduler_audit(
            action="cleanup",
            status="completed",
            task_id="task-chaos-3",
            details={"cleaned_count": 5},
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "CHAOS_ROLLBACK_TRIGGERED"

    def test_chaos_failed_audit(self, mock_wal, mock_adapter):
        """Chaos 실험 실패 audit 기록."""
        from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

        result = log_chaos_scheduler_audit(
            experiment_id="exp-fail",
            action="scheduled",
            status="failed",
            error_message="Target service not found",
            task_id="task-chaos-4",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error_message"] == "Target service not found"


# =============================================================================
# log_governance_task_audit Tests
# =============================================================================


class TestLogGovernanceTaskAudit:
    """log_governance_task_audit 함수 테스트."""

    def test_governance_expiry_check_no_action(self, mock_wal, mock_adapter):
        """Governance 만료 체크 - 조치 없음."""
        from selfhealing.services.audit_helpers import log_governance_task_audit

        result = log_governance_task_audit(
            action="expiry_check",
            emergency_level=0,
            status="no_action",
            task_id="task-gov-1",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "EMERGENCY_MODE_DEACTIVATED"
        assert call_args["source"] == "GovernanceTask"

    def test_governance_warning_sent(self, mock_wal, mock_adapter):
        """Governance 경고 발송 audit 기록."""
        from selfhealing.services.audit_helpers import log_governance_task_audit

        result = log_governance_task_audit(
            action="expiry_check",
            emergency_level=2,
            status="warning",
            notification_sent=True,
            hours_elapsed=4.5,
            task_id="task-gov-2",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "EMERGENCY_MODE_ACTIVATED"
        assert call_args["details"]["notification_sent"] is True
        assert call_args["details"]["hours_elapsed"] == 4.5

    def test_governance_auto_recovered(self, mock_wal, mock_adapter):
        """Governance 자동 복구 audit 기록."""
        from selfhealing.services.audit_helpers import log_governance_task_audit

        result = log_governance_task_audit(
            action="expiry_check",
            emergency_level=0,
            previous_level=2,
            status="auto_recovered",
            auto_recovered=True,
            hours_elapsed=8.1,
            task_id="task-gov-3",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "EMERGENCY_MODE_DEACTIVATED"
        assert call_args["details"]["auto_recovered"] is True
        assert call_args["details"]["previous_level"] == 2


# =============================================================================
# log_traffic_aware_replay_audit Tests
# =============================================================================


class TestLogTrafficAwareReplayAudit:
    """log_traffic_aware_replay_audit 함수 테스트."""

    def test_replay_completed_audit(self, mock_wal, mock_adapter):
        """Traffic-Aware Replay 완료 audit 기록."""
        from selfhealing.services.audit_helpers import log_traffic_aware_replay_audit

        result = log_traffic_aware_replay_audit(
            domain="payment",
            status="completed",
            total=10,
            success_count=8,
            failed_count=2,
            health_checks={"circuit_breaker": True, "error_budget": True},
            task_id="task-replay-1",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "DLQ_REPLAY"
        assert call_args["source"] == "TrafficAwareReplayTask"
        assert call_args["details"]["domain"] == "payment"
        assert call_args["details"]["total"] == 10
        assert call_args["details"]["success_count"] == 8

    def test_replay_skipped_audit(self, mock_wal, mock_adapter):
        """Traffic-Aware Replay 스킵 audit 기록."""
        from selfhealing.services.audit_helpers import log_traffic_aware_replay_audit

        result = log_traffic_aware_replay_audit(
            status="skipped",
            skipped_reason="Circuit breaker is OPEN",
            health_checks={"circuit_breaker": False},
            task_id="task-replay-2",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["success"] is False  # skipped with reason
        assert call_args["error_message"] == "Circuit breaker is OPEN"

    def test_replay_disabled_audit(self, mock_wal, mock_adapter):
        """Traffic-Aware Replay 비활성화 audit 기록."""
        from selfhealing.services.audit_helpers import log_traffic_aware_replay_audit

        result = log_traffic_aware_replay_audit(
            status="disabled",
            task_id="task-replay-3",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["details"]["status"] == "disabled"

    def test_replay_error_audit(self, mock_wal, mock_adapter):
        """Traffic-Aware Replay 오류 audit 기록."""
        from selfhealing.services.audit_helpers import log_traffic_aware_replay_audit

        result = log_traffic_aware_replay_audit(
            status="error",
            error_message="ReplayService not available",
            task_id="task-replay-4",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error_message"] == "ReplayService not available"


# =============================================================================
# log_drift_detection_audit Tests
# =============================================================================


class TestLogDriftDetectionAudit:
    """log_drift_detection_audit 함수 테스트."""

    def test_sla_drift_detected(self, mock_wal, mock_adapter):
        """SLA drift 감지 audit 기록."""
        from selfhealing.services.audit_helpers import log_drift_detection_audit

        result = log_drift_detection_audit(
            check_type="sla_drift",
            status="warning",
            drift_detected=True,
            drift_details={"payment": {"expected": 60, "actual": 120}},
            operations_analyzed=50,
            task_id="task-drift-1",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["event_type"] == "CONFIG_CHANGE"
        assert call_args["source"] == "DriftDetectionTask"
        assert call_args["details"]["drift_detected"] is True
        assert call_args["domain"] == "drift_detection"

    def test_sla_no_drift(self, mock_wal, mock_adapter):
        """SLA drift 없음 audit 기록."""
        from selfhealing.services.audit_helpers import log_drift_detection_audit

        result = log_drift_detection_audit(
            check_type="sla_drift",
            status="completed",
            drift_detected=False,
            operations_analyzed=100,
            task_id="task-drift-2",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["details"]["drift_detected"] is False
        assert call_args["success"] is True

    def test_analyze_pending_audit(self, mock_wal, mock_adapter):
        """Pending 분석 audit 기록."""
        from selfhealing.services.audit_helpers import log_drift_detection_audit

        result = log_drift_detection_audit(
            check_type="analyze_pending",
            status="completed",
            operations_analyzed=25,
            task_id="task-drift-3",
            details={"recommendations_generated": 10},
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["details"]["check_type"] == "analyze_pending"
        assert call_args["details"]["recommendations_generated"] == 10

    def test_chaos_cleanup_audit(self, mock_wal, mock_adapter):
        """Chaos 정리 audit 기록."""
        from selfhealing.services.audit_helpers import log_drift_detection_audit

        result = log_drift_detection_audit(
            check_type="chaos_cleanup",
            status="completed",
            task_id="task-drift-4",
            details={"resolved_count": 3},
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["details"]["check_type"] == "chaos_cleanup"

    def test_drift_detection_error(self, mock_wal, mock_adapter):
        """Drift detection 오류 audit 기록."""
        from selfhealing.services.audit_helpers import log_drift_detection_audit

        result = log_drift_detection_audit(
            check_type="sla_drift",
            status="error",
            error_message="Database connection timeout",
            task_id="task-drift-5",
        )

        assert result == 12345
        call_args = mock_wal.write.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error_message"] == "Database connection timeout"


# =============================================================================
# WAL Disabled Tests
# =============================================================================


class TestAuditWithWALDisabled:
    """WAL 비활성화 시 동작 테스트."""

    def test_config_apply_without_wal(self, disable_wal, mock_adapter, caplog):
        """WAL 비활성화 시에도 로깅은 동작."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        with caplog.at_level(logging.INFO):
            result = log_config_apply_audit(
                config_key="test",
                status="applied",
            )

        # WAL 없으면 None 반환
        assert result is None

        # 로그는 여전히 기록됨
        assert "ConfigApplyAudit" in caplog.text or mock_adapter.record.called

    def test_chaos_scheduler_without_wal(self, disable_wal, mock_adapter, caplog):
        """WAL 비활성화 시 Chaos 스케줄러 audit."""
        from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

        with caplog.at_level(logging.INFO):
            result = log_chaos_scheduler_audit(
                action="scheduled",
                status="completed",
            )

        assert result is None


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestAuditEdgeCases:
    """엣지 케이스 테스트."""

    def test_none_values_filtered(self, mock_wal, mock_adapter):
        """None 값이 details에서 필터링됨."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        log_config_apply_audit(
            pending_id=None,
            config_key="test",
            old_value=None,
            new_value={"key": "value"},
            status="applied",
            error_message=None,
            task_id=None,
        )

        call_args = mock_wal.write.call_args[0][0]
        details = call_args["details"]

        # None 값은 제외됨
        assert "pending_id" not in details
        assert "old_value" not in details
        assert "task_id" not in details

        # 값이 있는 것은 포함됨
        assert details["config_key"] == "test"
        assert details["new_value"] == {"key": "value"}

    def test_adapter_failure_does_not_raise(self, mock_wal, mock_adapter):
        """Adapter 실패 시 예외 발생 안함 (Fail-Open)."""
        from selfhealing.services.audit_helpers import log_config_apply_audit

        mock_adapter.record.side_effect = Exception("Adapter error")

        # 예외 발생하지 않아야 함
        result = log_config_apply_audit(
            config_key="test",
            status="applied",
        )

        # WAL 기록은 성공
        assert result == 12345

    def test_wal_failure_does_not_raise(self, mock_adapter):
        """WAL 실패 시에도 예외 발생 안함 (Fail-Open)."""
        import selfhealing.services.audit.base as base_module
        from selfhealing.services.audit_helpers import log_config_apply_audit

        # 싱글톤 초기화
        original_instance = base_module._wal_instance
        base_module._wal_instance = None

        try:
            with patch("selfhealing.services.audit.base._get_wal") as mock_get_wal:
                mock_wal = MagicMock()
                mock_wal.write.side_effect = Exception("WAL error")
                mock_get_wal.return_value = mock_wal

                # 예외 발생하지 않아야 함
                result = log_config_apply_audit(
                    config_key="test",
                    status="applied",
                )

                # WAL 실패 시 None 반환
                assert result is None
        finally:
            # 싱글톤 복원
            base_module._wal_instance = original_instance

    def test_complex_details_merge(self, mock_wal, mock_adapter):
        """복잡한 details 병합 테스트."""
        from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

        # 메모리 버퍼 초기화 (이전 테스트의 실패한 이벤트 제거)
        try:
            from selfhealing.audit.resilience import InMemoryAuditBuffer
            buffer = InMemoryAuditBuffer.get_instance()
            buffer._entries.clear()
        except Exception:
            pass

        # mock 상태 초기화
        mock_wal.reset_mock()

        result = log_chaos_scheduler_audit(
            experiment_id="exp-1",
            action="scheduled",
            status="completed",
            details={
                "executed_count": 5,
                "skipped_count": 2,
                "custom_field": "custom_value",
            },
        )

        # 이 테스트에서 write가 호출되었는지 확인
        assert mock_wal.write.call_count >= 1, (
            f"mock_wal.write was not called. result: {result}"
        )

        # CHAOS 이벤트 찾기 (메모리 버퍼 flush로 인해 여러 호출이 있을 수 있음)
        chaos_entry = None
        for call in mock_wal.write.call_args_list:
            entry = call[0][0]
            if entry.get("event_type", "").startswith("CHAOS_"):
                chaos_entry = entry
                break

        assert chaos_entry is not None, (
            f"CHAOS event not found in calls: {[c[0][0].get('event_type') for c in mock_wal.write.call_args_list]}"
        )

        details = chaos_entry["details"]

        # 기본 필드와 추가 details가 모두 포함
        assert "experiment_id" in details, f"'experiment_id' not in details: {details}"
        assert details["experiment_id"] == "exp-1"
        assert details["executed_count"] == 5
        assert details["custom_field"] == "custom_value"
