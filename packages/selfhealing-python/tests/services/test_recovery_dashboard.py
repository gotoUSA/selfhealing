"""
Tests for RecoveryDashboardService.

Phase 4.12: recovery_dashboard.py 순수 단위 테스트.

테스트 대상:
- RecoveryDashboardService.get_widget_data()
- RecoveryDashboardService.get_regional_status()
- RecoveryDashboardService.get_recovery_summary()
- Helper functions (get_status_display, get_status_color)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#10.2.4.12
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

from selfhealing.services.coordination.recovery_dashboard import (
    RecoveryDashboardService,
    RecoveryWidgetData,
    RecoverySessionProgress,
    ActiveSessionInfo,
    PendingApprovalsInfo,
    RecoveryStats,
    RegionalStatusInfo,
    RecoveryActionWidget,
    get_status_display,
    get_status_color,
    get_recovery_dashboard_service,
    reset_recovery_dashboard_service,
)
from selfhealing.services.coordination.enums import RecoveryStatus


class TestRecoveryDashboardServiceDataClasses:
    """데이터 클래스 테스트."""

    def test_recovery_session_progress_to_dict(self):
        """RecoverySessionProgress.to_dict() 테스트."""
        progress = RecoverySessionProgress(
            percent=75,
            current_step="HEALTH_CHECK",
            completed_steps=3,
            total_steps=4,
        )
        result = progress.to_dict()

        assert result["percent"] == 75
        assert result["current_step"] == "HEALTH_CHECK"
        assert result["completed_steps"] == 3
        assert result["total_steps"] == 4

    def test_active_session_info_to_dict(self):
        """ActiveSessionInfo.to_dict() 테스트."""
        progress = RecoverySessionProgress(percent=50, total_steps=4)
        session = ActiveSessionInfo(
            session_id="test-session-123",
            progress=progress,
            namespace="global",
            started_at="2026-01-23T10:00:00Z",
        )
        result = session.to_dict()

        assert result["session_id"] == "test-session-123"
        assert result["namespace"] == "global"
        assert result["started_at"] == "2026-01-23T10:00:00Z"
        assert "progress" in result

    def test_pending_approvals_info_to_dict(self):
        """PendingApprovalsInfo.to_dict() 테스트."""
        info = PendingApprovalsInfo(count=5, stale_count=2, urgent=True)
        result = info.to_dict()

        assert result["count"] == 5
        assert result["stale_count"] == 2
        assert result["urgent"] is True

    def test_recovery_stats_to_dict(self):
        """RecoveryStats.to_dict() 테스트."""
        stats = RecoveryStats(
            total_recoveries=100,
            approved=80,
            rejected=10,
            completed=75,
            aborted=5,
        )
        result = stats.to_dict()

        assert result["total_recoveries"] == 100
        assert result["approved"] == 80
        assert result["rejected"] == 10
        assert result["completed"] == 75
        assert result["aborted"] == 5

    def test_regional_status_info_to_dict(self):
        """RegionalStatusInfo.to_dict() 테스트."""
        info = RegionalStatusInfo(
            namespace="seoul",
            circuit_breaker_state="closed",
            require_manual_approval=True,
            priority=100,
        )
        result = info.to_dict()

        assert result["namespace"] == "seoul"
        assert result["circuit_breaker_state"] == "closed"
        assert result["require_manual_approval"] is True
        assert result["priority"] == 100

    def test_recovery_action_to_dict(self):
        """RecoveryActionWidget.to_dict() 테스트."""
        action = RecoveryActionWidget(
            action="start_recovery",
            label="복구 시작",
            enabled=True,
            urgent=False,
        )
        result = action.to_dict()

        assert result["action"] == "start_recovery"
        assert result["label"] == "복구 시작"
        assert result["enabled"] is True
        assert result["urgent"] is False

    def test_recovery_widget_data_to_dict(self):
        """RecoveryWidgetData.to_dict() 테스트."""
        widget = RecoveryWidgetData(
            status="not_started",
            status_display="대기",
            status_color="gray",
            pending_approvals=PendingApprovalsInfo(count=3),
            stats=RecoveryStats(total_recoveries=50),
            timestamp="2026-01-23T10:00:00Z",
        )
        result = widget.to_dict()

        assert result["status"] == "not_started"
        assert result["status_display"] == "대기"
        assert result["status_color"] == "gray"
        assert result["pending_approvals"]["count"] == 3
        assert result["stats"]["total_recoveries"] == 50
        assert result["active_session"] is None


class TestStatusHelpers:
    """상태 헬퍼 함수 테스트."""

    @pytest.mark.parametrize(
        "status,expected",
        [
            (RecoveryStatus.NOT_STARTED, "대기"),
            (RecoveryStatus.IN_PROGRESS, "진행 중"),
            (RecoveryStatus.HEALTH_CHECK, "건강 확인 중"),
            (RecoveryStatus.READY_TO_RESTORE, "복구 대기"),
            (RecoveryStatus.COMPLETED, "완료"),
            (RecoveryStatus.FAILED, "실패"),
            (RecoveryStatus.ABORTED, "중단됨"),
        ],
    )
    def test_get_status_display(self, status, expected):
        """get_status_display() 상태별 표시 문자열 테스트."""
        assert get_status_display(status) == expected

    @pytest.mark.parametrize(
        "status,expected",
        [
            (RecoveryStatus.NOT_STARTED, "gray"),
            (RecoveryStatus.IN_PROGRESS, "yellow"),
            (RecoveryStatus.HEALTH_CHECK, "blue"),
            (RecoveryStatus.READY_TO_RESTORE, "orange"),
            (RecoveryStatus.COMPLETED, "green"),
            (RecoveryStatus.FAILED, "red"),
            (RecoveryStatus.ABORTED, "gray"),
        ],
    )
    def test_get_status_color(self, status, expected):
        """get_status_color() 상태별 색상 테스트."""
        assert get_status_color(status) == expected


class TestRecoveryDashboardService:
    """RecoveryDashboardService 테스트."""

    @pytest.fixture
    def mock_coordinator(self):
        """Mock RecoveryCoordinator."""
        coordinator = Mock()
        coordinator.get_active_session.return_value = None
        return coordinator

    @pytest.fixture
    def mock_circuit_breaker(self):
        """Mock RecoveryCircuitBreaker."""
        breaker = Mock()
        breaker.get_status.return_value = {"state": "closed", "trip_count": 0}
        return breaker

    @pytest.fixture
    def mock_approval_manager(self):
        """Mock PendingRecoveryApprovalManager."""
        manager = Mock()
        manager.list_pending_requests.return_value = []
        manager.list_stale_requests.return_value = []
        manager.get_stats.return_value = {
            "total_requests": 10,
            "approved_count": 8,
            "rejected_count": 2,
            "completed_count": 7,
            "aborted_count": 1,
        }
        return manager

    @pytest.fixture
    def mock_policy_engine(self):
        """Mock RegionalRecoveryPolicyEngine."""
        engine = Mock()
        engine.get_namespaces_by_priority.return_value = ["seoul", "tokyo", "global"]

        # Config mock
        config = Mock()
        config.require_manual_approval = False
        config.priority = 50
        engine.get_config.return_value = config

        return engine

    @pytest.fixture
    def service(
        self,
        mock_coordinator,
        mock_circuit_breaker,
        mock_approval_manager,
        mock_policy_engine,
    ):
        """RecoveryDashboardService with mocks."""
        return RecoveryDashboardService(
            coordinator=mock_coordinator,
            circuit_breaker=mock_circuit_breaker,
            approval_manager=mock_approval_manager,
            policy_engine=mock_policy_engine,
        )

    def test_get_widget_data_no_active_session(self, service):
        """활성 세션 없을 때 위젯 데이터 테스트."""
        result = service.get_widget_data(namespace="global")

        assert isinstance(result, RecoveryWidgetData)
        assert result.status == RecoveryStatus.NOT_STARTED.value
        assert result.active_session is None
        assert result.pending_approvals.count == 0
        assert result.timestamp != ""

    def test_get_widget_data_with_pending_approvals(self, service, mock_approval_manager):
        """대기 중인 승인 있을 때 테스트."""
        # Mock pending requests
        pending_request = Mock()
        pending_request.get_waiting_time_minutes.return_value = 45
        mock_approval_manager.list_pending_requests.return_value = [pending_request]
        mock_approval_manager.list_stale_requests.return_value = [pending_request]

        result = service.get_widget_data()

        assert result.pending_approvals.count == 1
        assert result.pending_approvals.stale_count == 1
        assert result.pending_approvals.urgent is True

    def test_get_regional_status(self, service, mock_policy_engine, mock_circuit_breaker):
        """리전별 상태 조회 테스트."""
        result = service.get_regional_status(limit=3)

        assert len(result) == 3
        assert result[0].namespace == "seoul"
        assert result[1].namespace == "tokyo"
        assert result[2].namespace == "global"

        # Verify get_config was called for each namespace
        assert mock_policy_engine.get_config.call_count >= 3

    def test_get_recovery_summary(self, service, mock_approval_manager):
        """복구 시스템 요약 테스트."""
        result = service.get_recovery_summary()

        assert "active_recovery_sessions" in result
        assert "pending_approvals" in result
        assert "stale_approvals" in result
        assert "has_urgent_approvals" in result
        assert "total_recoveries" in result
        assert "health_status" in result

    def test_determine_recovery_health_healthy(self, service):
        """건강 상태 판단 - healthy."""
        result = service._determine_recovery_health(pending_count=0, stale_count=0, active_sessions=0)
        assert result == "healthy"

    def test_determine_recovery_health_warning(self, service):
        """건강 상태 판단 - warning."""
        result = service._determine_recovery_health(pending_count=6, stale_count=1, active_sessions=0)
        assert result == "warning"

    def test_determine_recovery_health_critical(self, service):
        """건강 상태 판단 - critical."""
        result = service._determine_recovery_health(pending_count=10, stale_count=5, active_sessions=0)
        assert result == "critical"

    def test_get_available_actions_no_session(self, service):
        """세션 없을 때 사용 가능한 액션."""
        actions = service._get_available_actions(
            status=RecoveryStatus.NOT_STARTED,
            has_active_session=False,
            pending_count=0,
            stale_count=0,
        )

        assert len(actions) == 1
        assert actions[0].action == "start_recovery"

    def test_get_available_actions_with_active_session(self, service):
        """세션 있을 때 사용 가능한 액션."""
        actions = service._get_available_actions(
            status=RecoveryStatus.IN_PROGRESS,
            has_active_session=True,
            pending_count=0,
            stale_count=0,
        )

        assert len(actions) == 1
        assert actions[0].action == "abort_recovery"

    def test_get_available_actions_with_pending_approvals(self, service):
        """대기 중인 승인 있을 때 사용 가능한 액션."""
        actions = service._get_available_actions(
            status=RecoveryStatus.READY_TO_RESTORE,
            has_active_session=False,
            pending_count=3,
            stale_count=1,
        )

        # approve_recovery 액션 확인
        approve_action = next((a for a in actions if a.action == "approve_recovery"), None)
        assert approve_action is not None
        assert approve_action.urgent is True


class TestRecoveryDashboardServiceSingleton:
    """싱글톤 테스트."""

    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_recovery_dashboard_service()

    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_recovery_dashboard_service()

    def test_get_recovery_dashboard_service_returns_singleton(self):
        """get_recovery_dashboard_service() 싱글톤 반환 테스트."""
        # 싱글톤은 의존성을 lazy하게 로드하므로 바로 테스트 가능
        service1 = get_recovery_dashboard_service()
        service2 = get_recovery_dashboard_service()

        assert service1 is service2

    def test_reset_recovery_dashboard_service(self):
        """reset_recovery_dashboard_service() 테스트."""
        service1 = get_recovery_dashboard_service()
        reset_recovery_dashboard_service()
        service2 = get_recovery_dashboard_service()

        assert service1 is not service2
