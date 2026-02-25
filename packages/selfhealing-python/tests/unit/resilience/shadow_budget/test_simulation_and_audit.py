"""
Simulation Stats, Pending Freeze, Audit 이벤트 테스트.
"""

from datetime import datetime, timezone

import selfhealing.services.circuit_breaker.freeze_mode
import selfhealing.services.unified_notification


class TestSimulationBridge:
    """Simulation Stats Callback 테스트."""

    def test_service_uses_simulation_callback_when_no_stats_provided(self):
        """get_failed_operation_stats가 None일 때 시뮬레이션 콜백 사용."""
        from selfhealing.services.error_budget.service import ErrorBudgetService

        service = ErrorBudgetService()

        # 시뮬레이션 에러 기록
        service.record_error(error_count=50)

        stats = service.get_simulated_stats()
        assert stats["simulated_errors"] == 50

    def test_service_reset_simulated_stats(self):
        """시뮬레이션 통계 초기화."""
        from selfhealing.services.error_budget.service import ErrorBudgetService

        service = ErrorBudgetService()
        service.record_error(error_count=100)

        result = service.reset_simulated_stats()
        assert result["reset"] is True
        assert result["previous_stats"]["simulated_errors"] == 100

        current = service.get_simulated_stats()
        assert current["simulated_errors"] == 0


class TestPendingReconciliationFreeze:
    """Pending Freeze 테스트."""

    def test_notify_pending_freeze_skipped_for_small_adjustment(self, shadow_calculator):
        """5% 이하 조정은 freeze를 트리거하지 않음."""
        from selfhealing.services.error_budget.reconciliation.enums import (
            ReconciliationStatus,
        )
        from selfhealing.services.error_budget.reconciliation.models import ShadowBudget

        shadow = ShadowBudget(
            calculation_id="test-1",
            calculated_at=datetime.now(timezone.utc),
            failsafe_period_id="period-1",
            failsafe_period_start=datetime.now(timezone.utc),
            failsafe_period_end=datetime.now(timezone.utc),
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.0,
            shadow_remaining_percent=72.0,
            shadow_consumed_minutes=12.0,
            adjustment_percent=3.0,  # 5% 이하
            adjustment_minutes=2.0,
            status=ReconciliationStatus.CALCULATED,
        )

        # freeze 트리거되지 않음 (예외 없이 완료)
        shadow_calculator._notify_pending_freeze(shadow)

    def test_notify_pending_freeze_triggered_for_large_adjustment(self, shadow_calculator, monkeypatch):
        """5% 초과 조정은 freeze를 트리거함."""
        from selfhealing.services.error_budget.reconciliation.enums import (
            ReconciliationStatus,
        )
        from selfhealing.services.error_budget.reconciliation.models import ShadowBudget

        freeze_activated = {"called": False, "reason": None}

        class MockFreezeModeManager:
            def activate(self, reason, activated_by):
                freeze_activated["called"] = True
                freeze_activated["reason"] = reason

        monkeypatch.setattr(
            selfhealing.services.circuit_breaker.freeze_mode,
            "FreezeModeManager",
            MockFreezeModeManager,
        )

        shadow = ShadowBudget(
            calculation_id="test-2",
            calculated_at=datetime.now(timezone.utc),
            failsafe_period_id="period-2",
            failsafe_period_start=datetime.now(timezone.utc),
            failsafe_period_end=datetime.now(timezone.utc),
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.0,
            shadow_remaining_percent=65.0,
            shadow_consumed_minutes=15.0,
            adjustment_percent=10.0,  # 5% 초과
            adjustment_minutes=5.0,
            status=ReconciliationStatus.CALCULATED,
        )

        shadow_calculator._notify_pending_freeze(shadow)

        assert freeze_activated["called"] is True
        assert "10.00%" in freeze_activated["reason"]


class TestAuditEvents:
    """Audit 이벤트 테스트."""

    def test_audit_event_types_defined(self):
        """Reconciliation 관련 AuditEventType이 정의되어 있어야 함."""
        from selfhealing.audit.event_buffer import AuditEventType

        assert hasattr(AuditEventType, "FAILSAFE_PERIOD_STARTED")
        assert hasattr(AuditEventType, "FAILSAFE_PERIOD_ENDED")
        assert hasattr(AuditEventType, "SHADOW_BUDGET_CALCULATED")
        assert hasattr(AuditEventType, "RECONCILIATION_APPROVED")
        assert hasattr(AuditEventType, "RECONCILIATION_REJECTED")
        assert hasattr(AuditEventType, "RECONCILIATION_ACCURACY_VERIFIED")
        assert hasattr(AuditEventType, "PENDING_RECONCILIATION_FREEZE")

    def test_excluded_period_has_transparency_fields(self):
        """ExcludedPeriod에 투명성 강화 필드가 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.models import (
            ExcludedPeriod,
        )

        exclusion = ExcludedPeriod(
            exclusion_id="test-1",
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            reason="Test reason",
            excluded_by="test_user",
            excluded_at=datetime.now(timezone.utc),
            original_estimated_errors=100,
            original_log_source="prometheus",
            original_adjustment_percent=5.5,
        )

        assert exclusion.original_estimated_errors == 100
        assert exclusion.original_log_source == "prometheus"
        assert exclusion.original_adjustment_percent == 5.5

        # to_dict에도 포함되어야 함
        data = exclusion.to_dict()
        assert data["original_estimated_errors"] == 100
        assert data["original_log_source"] == "prometheus"
        assert data["original_adjustment_percent"] == 5.5


class TestAccuracyAudit:
    """Accuracy Audit 테스트."""

    def test_shadow_budget_has_verification_fields(self):
        """ShadowBudget에 정확도 검증 필드가 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.enums import (
            ReconciliationStatus,
        )
        from selfhealing.services.error_budget.reconciliation.models import ShadowBudget

        shadow = ShadowBudget(
            calculation_id="test-1",
            calculated_at=datetime.now(timezone.utc),
            failsafe_period_id="period-1",
            failsafe_period_start=datetime.now(timezone.utc),
            failsafe_period_end=datetime.now(timezone.utc),
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.0,
            shadow_remaining_percent=70.0,
            shadow_consumed_minutes=13.0,
            adjustment_percent=5.0,
            adjustment_minutes=3.0,
            status=ReconciliationStatus.CALCULATED,
            verified_at=datetime.now(timezone.utc),
            accuracy_variance_percent=8.5,
        )

        assert shadow.verified_at is not None
        assert shadow.accuracy_variance_percent == 8.5

    def test_verify_task_class_exists(self):
        """VerifyReconciliationAccuracyTask 클래스가 존재해야 함."""
        from selfhealing.tasks.intelligence_tasks import (
            INTELLIGENCE_TASKS,
            VerifyReconciliationAccuracyTask,
        )

        assert VerifyReconciliationAccuracyTask is not None
        assert VerifyReconciliationAccuracyTask in INTELLIGENCE_TASKS
        assert VerifyReconciliationAccuracyTask.name == "selfhealing.verify_reconciliation_accuracy"


class TestNotificationIntegration:
    """알림 연동 테스트."""

    def test_notify_shadow_budget_calculated_method_exists(self, shadow_calculator):
        """_notify_shadow_budget_calculated 메서드가 존재해야 함."""
        assert hasattr(shadow_calculator, "_notify_shadow_budget_calculated")
        assert callable(shadow_calculator._notify_shadow_budget_calculated)

    def test_notification_sent_for_calculated_shadow(self, shadow_calculator, monkeypatch):
        """Shadow Budget 계산 완료 시 알림 발송."""
        from selfhealing.services.error_budget.reconciliation.enums import (
            ReconciliationStatus,
        )
        from selfhealing.services.error_budget.reconciliation.models import ShadowBudget

        notification_sent = {"called": False, "payload": None}

        class MockNotificationPayload:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class MockNotificationManager:
            def send(self, payload):
                notification_sent["called"] = True
                notification_sent["payload"] = payload

        def mock_get_manager():
            return MockNotificationManager()

        # Mock 설정
        monkeypatch.setattr(
            selfhealing.services.unified_notification,
            "get_unified_notification_manager",
            mock_get_manager,
        )
        monkeypatch.setattr(
            selfhealing.services.unified_notification,
            "NotificationPayload",
            MockNotificationPayload,
        )
        monkeypatch.setattr(
            selfhealing.services.unified_notification,
            "NotificationPriority",
            type("NotificationPriority", (), {"HIGH": "high", "MEDIUM": "medium"}),
        )
        monkeypatch.setattr(
            selfhealing.services.unified_notification,
            "NotificationCategory",
            type("NotificationCategory", (), {"APPROVAL": "approval"}),
        )

        shadow = ShadowBudget(
            calculation_id="test-notify",
            calculated_at=datetime.now(timezone.utc),
            failsafe_period_id="period-1",
            failsafe_period_start=datetime.now(timezone.utc),
            failsafe_period_end=datetime.now(timezone.utc),
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.0,
            shadow_remaining_percent=65.0,
            shadow_consumed_minutes=15.0,
            adjustment_percent=10.0,
            adjustment_minutes=5.0,
            estimated_errors=50,
            log_source="prometheus",
            status=ReconciliationStatus.CALCULATED,
        )

        shadow_calculator._notify_shadow_budget_calculated(shadow)

        assert notification_sent["called"] is True
        assert notification_sent["payload"].priority == "high"  # 10% > 5%
        assert "50개 에러 추정" in notification_sent["payload"].message
