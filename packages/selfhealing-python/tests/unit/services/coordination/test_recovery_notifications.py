"""
Recovery Notifications 단위 테스트.

Phase 5.6: 알림 템플릿 테스트

테스트 항목:
- recovery_started_notification
- recovery_completed_notification
- recovery_failed_notification
- recovery_aborted_notification
- recovery_approval_required_notification
- recovery_stale_approval_reminder
- recovery_circuit_breaker_trip_notification
"""


from selfhealing.services.coordination.recovery_notifications import (
    recovery_aborted_notification,
    recovery_approval_required_notification,
    recovery_circuit_breaker_trip_notification,
    recovery_completed_notification,
    recovery_failed_notification,
    recovery_stale_approval_reminder,
    recovery_started_notification,
)

# =============================================================================
# recovery_started_notification Tests
# =============================================================================


class TestRecoveryStartedNotification:
    """recovery_started_notification 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_started_notification(
            session_id="session-123",
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="system",
            total_steps=4,
            step_names=["budget_reset", "health_check", "canary_resume", "governance_normal"],
        )

        assert result is not None
        assert isinstance(result, dict)
        assert "title" in result
        assert "severity" in result
        assert "message" in result
        assert "details" in result

    def test_contains_session_info(self):
        """세션 정보 포함 확인."""
        result = recovery_started_notification(
            session_id="session-abc123",
            namespace="production",
            trigger_level="LEVEL_4",
            initiated_by="admin@example.com",
            total_steps=5,
            step_names=["step1", "step2", "step3", "step4", "step5"],
        )

        assert result["details"]["session_id"] == "session-abc123"
        assert result["details"]["namespace"] == "production"
        assert result["details"]["trigger_level"] == "LEVEL_4"


# =============================================================================
# recovery_completed_notification Tests
# =============================================================================


class TestRecoveryCompletedNotification:
    """recovery_completed_notification 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_completed_notification(
            session_id="session-456",
            namespace="global",
            trigger_level="LEVEL_3",
            duration_seconds=300,
            steps_completed=4,
            total_steps=4,
        )

        assert result is not None
        assert isinstance(result, dict)
        assert "SUCCESS" in result["title"]

    def test_duration_formatting_hours(self):
        """시간 단위 소요 시간 포맷팅 테스트."""
        result = recovery_completed_notification(
            session_id="session-789",
            namespace="seoul",
            trigger_level="LEVEL_2",
            duration_seconds=3661,  # 1시간 1분 1초
            steps_completed=5,
            total_steps=5,
        )

        # 시간 정보가 어떤 형태로든 포함
        assert "1" in result["message"]

    def test_duration_formatting_minutes(self):
        """분 단위 소요 시간 포맷팅 테스트."""
        result = recovery_completed_notification(
            session_id="session-abc",
            namespace="global",
            trigger_level="LEVEL_1",
            duration_seconds=125,  # 2분 5초
            steps_completed=3,
            total_steps=3,
        )

        assert "2" in result["message"]


# =============================================================================
# recovery_failed_notification Tests
# =============================================================================


class TestRecoveryFailedNotification:
    """recovery_failed_notification 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_failed_notification(
            session_id="session-fail-1",
            namespace="global",
            trigger_level="LEVEL_3",
            failed_step="health_check",
            error_message="Health check timeout after 300 seconds",
            steps_completed=2,
            total_steps=5,
            retry_count=3,
        )

        assert result is not None
        assert isinstance(result, dict)
        assert result["severity"] == "critical"

    def test_contains_error_info(self):
        """에러 정보 포함 확인."""
        result = recovery_failed_notification(
            session_id="session-fail-2",
            namespace="production",
            trigger_level="LEVEL_4",
            failed_step="canary_resume",
            error_message="Canary deployment failed: pods not ready",
            steps_completed=3,
            total_steps=6,
            retry_count=2,
        )

        assert result["details"]["failed_step"] == "canary_resume"
        assert "canary" in result["message"].lower() or "pods" in result["message"].lower()


# =============================================================================
# recovery_aborted_notification Tests
# =============================================================================


class TestRecoveryAbortedNotification:
    """recovery_aborted_notification 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_aborted_notification(
            session_id="session-abort-1",
            namespace="global",
            trigger_level="LEVEL_3",
            abort_reason="Manual abort requested by operator",
            aborted_by="admin@example.com",
            steps_completed=2,
            total_steps=5,
        )

        assert result is not None
        assert isinstance(result, dict)
        assert "ABORTED" in result["title"]

    def test_contains_abort_info(self):
        """중단 정보 포함 확인."""
        result = recovery_aborted_notification(
            session_id="session-abort-2",
            namespace="seoul",
            trigger_level="LEVEL_4",
            abort_reason="New incident detected, need different recovery approach",
            aborted_by="sre-team@example.com",
            steps_completed=1,
            total_steps=4,
        )

        assert result["details"]["aborted_by"] == "sre-team@example.com"
        assert result["details"]["status"] == "aborted"


# =============================================================================
# recovery_approval_required_notification Tests
# =============================================================================


class TestRecoveryApprovalRequiredNotification:
    """recovery_approval_required_notification 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_approval_required_notification(
            request_id="approval-req-123",
            namespace="global",
            trigger_level="LEVEL_4",
            requested_by="system",
            timeout_minutes=60,
        )

        assert result is not None
        assert isinstance(result, dict)
        assert "ACTION REQUIRED" in result["title"]

    def test_with_stability_info(self):
        """안정화 정보 포함 알림 테스트."""
        stability_info = {
            "error_rate": 0.05,
            "stable_duration_minutes": 30,
        }

        result = recovery_approval_required_notification(
            request_id="approval-req-456",
            namespace="production",
            trigger_level="LEVEL_3",
            requested_by="admin@example.com",
            timeout_minutes=30,
            stability_info=stability_info,
        )

        assert result["details"]["stability_info"] is not None

    def test_contains_actions(self):
        """액션 버튼 포함 확인."""
        result = recovery_approval_required_notification(
            request_id="approval-req-789",
            namespace="global",
            trigger_level="LEVEL_3",
            requested_by="system",
            timeout_minutes=45,
        )

        assert "actions" in result
        assert len(result["actions"]) >= 2  # 승인, 거부 최소 2개


# =============================================================================
# recovery_stale_approval_reminder Tests
# =============================================================================


class TestRecoveryStaleApprovalReminder:
    """recovery_stale_approval_reminder 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_stale_approval_reminder(
            request_id="stale-req-123",
            namespace="global",
            waiting_minutes=45,
            timeout_minutes=60,
            reminder_count=1,
        )

        assert result is not None
        assert isinstance(result, dict)
        assert "REMINDER" in result["title"]

    def test_urgency_levels(self):
        """긴급도 레벨별 테스트."""
        # 낮은 긴급도 (timeout 30분 이상)
        result_low = recovery_stale_approval_reminder(
            request_id="stale-req-low",
            namespace="global",
            waiting_minutes=30,
            timeout_minutes=60,
            reminder_count=1,
        )
        assert result_low["severity"] == "warning"

        # 높은 긴급도 (timeout 30분 미만)
        result_high = recovery_stale_approval_reminder(
            request_id="stale-req-high",
            namespace="global",
            waiting_minutes=50,
            timeout_minutes=15,
            reminder_count=3,
        )
        assert result_high["severity"] == "critical"


# =============================================================================
# recovery_circuit_breaker_trip_notification Tests
# =============================================================================


class TestRecoveryCircuitBreakerTripNotification:
    """recovery_circuit_breaker_trip_notification 테스트."""

    def test_basic_notification(self):
        """기본 알림 생성 테스트."""
        result = recovery_circuit_breaker_trip_notification(
            namespace="global",
            trip_count=5,
            error_rate=0.15,
            threshold=0.10,
            is_permanent=False,
            should_re_escalate=False,
        )

        assert result is not None
        assert isinstance(result, dict)
        assert result["severity"] == "warning"  # is_permanent=False

    def test_permanent_trip(self):
        """영구 차단 알림 테스트."""
        result = recovery_circuit_breaker_trip_notification(
            namespace="production",
            trip_count=10,
            error_rate=0.25,
            threshold=0.10,
            is_permanent=True,
            should_re_escalate=True,
        )

        assert result["severity"] == "critical"
        assert result["details"]["is_permanent"] is True
        assert result["details"]["should_re_escalate"] is True


# =============================================================================
# Common Structure Tests
# =============================================================================


class TestCommonNotificationStructure:
    """공통 알림 구조 테스트."""

    def test_all_notifications_have_title(self):
        """모든 알림에 title 존재 확인."""
        notifications = [
            recovery_started_notification("s1", "ns1", "L1", "system", 1, ["step1"]),
            recovery_completed_notification("s1", "ns1", "L1", 60, 1, 1),
            recovery_failed_notification("s1", "ns1", "L1", "step1", "error", 0, 1),
            recovery_aborted_notification("s1", "ns1", "L1", "reason", "user", 0, 1),
            recovery_approval_required_notification("r1", "ns1", "L1", "system", 60),
            recovery_stale_approval_reminder("r1", "ns1", 30, 60, 1),
            recovery_circuit_breaker_trip_notification("ns1", 3, 0.15, 0.10, False, False),
        ]

        for notif in notifications:
            assert "title" in notif
            assert isinstance(notif["title"], str)

    def test_all_notifications_have_severity(self):
        """모든 알림에 severity 존재 확인."""
        notifications = [
            recovery_started_notification("s1", "ns1", "L1", "system", 1, ["step1"]),
            recovery_completed_notification("s1", "ns1", "L1", 60, 1, 1),
            recovery_failed_notification("s1", "ns1", "L1", "step1", "error", 0, 1),
            recovery_aborted_notification("s1", "ns1", "L1", "reason", "user", 0, 1),
            recovery_approval_required_notification("r1", "ns1", "L1", "system", 60),
            recovery_stale_approval_reminder("r1", "ns1", 30, 60, 1),
            recovery_circuit_breaker_trip_notification("ns1", 3, 0.15, 0.10, False, False),
        ]

        for notif in notifications:
            assert "severity" in notif
            assert notif["severity"] in ["info", "warning", "critical"]


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_empty_session_id(self):
        """빈 session_id 테스트."""
        result = recovery_started_notification(
            session_id="",
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="system",
            total_steps=1,
            step_names=["step1"],
        )

        assert result is not None

    def test_very_long_error_message(self):
        """매우 긴 에러 메시지 테스트."""
        long_error = "Error: " + "x" * 5000

        result = recovery_failed_notification(
            session_id="long-error-test",
            namespace="global",
            trigger_level="LEVEL_3",
            failed_step="budget_reset",
            error_message=long_error,
            steps_completed=0,
            total_steps=3,
            retry_count=1,
        )

        assert result is not None

    def test_zero_duration(self):
        """duration이 0일 때 테스트."""
        result = recovery_completed_notification(
            session_id="zero-duration",
            namespace="global",
            trigger_level="LEVEL_1",
            duration_seconds=0,
            steps_completed=0,
            total_steps=0,
        )

        assert result is not None

    def test_empty_step_names(self):
        """빈 step_names 목록 테스트."""
        result = recovery_started_notification(
            session_id="empty-steps",
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="system",
            total_steps=0,
            step_names=[],
        )

        assert result is not None
