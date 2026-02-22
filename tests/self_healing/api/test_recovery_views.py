"""
Unit tests for Recovery Views.

Tests:
- RecoveryStatusView
- RecoveryStartView
- RecoveryAbortView
- RecoveryPendingApprovalsView
- RecoveryApproveView
- RecoveryRejectView

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#10.2.4
"""

from unittest.mock import MagicMock

from selfhealing.services.coordination.enums import RecoveryStatus


class TestRecoveryStatusView:
    """RecoveryStatusView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryStatusView
        
        assert RecoveryStatusView is not None
        assert hasattr(RecoveryStatusView, "get")

    def test_permission_classes(self):
        """권한 클래스 확인."""
        from selfhealing.api.django.views.recovery import RecoveryStatusView
        from rest_framework.permissions import IsAuthenticated
        
        assert IsAuthenticated in RecoveryStatusView.permission_classes


class TestRecoveryStartView:
    """RecoveryStartView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryStartView
        
        assert RecoveryStartView is not None
        assert hasattr(RecoveryStartView, "post")


class TestRecoveryAbortView:
    """RecoveryAbortView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryAbortView
        
        assert RecoveryAbortView is not None
        assert hasattr(RecoveryAbortView, "post")


class TestRecoveryPendingApprovalsView:
    """RecoveryPendingApprovalsView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryPendingApprovalsView
        
        assert RecoveryPendingApprovalsView is not None
        assert hasattr(RecoveryPendingApprovalsView, "get")


class TestRecoveryApproveView:
    """RecoveryApproveView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryApproveView
        
        assert RecoveryApproveView is not None
        assert hasattr(RecoveryApproveView, "post")


class TestRecoveryRejectView:
    """RecoveryRejectView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryRejectView
        
        assert RecoveryRejectView is not None
        assert hasattr(RecoveryRejectView, "post")


class TestRecoveryHistoryView:
    """RecoveryHistoryView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryHistoryView
        
        assert RecoveryHistoryView is not None
        assert hasattr(RecoveryHistoryView, "get")


class TestRecoveryDashboardWidgetView:
    """RecoveryDashboardWidgetView 테스트."""

    def test_view_exists(self):
        """View 클래스 존재 확인."""
        from selfhealing.api.django.views.recovery import RecoveryDashboardWidgetView
        
        assert RecoveryDashboardWidgetView is not None
        assert hasattr(RecoveryDashboardWidgetView, "get")


class TestHelperFunctions:
    """헬퍼 함수 테스트."""

    def test_get_status_display(self):
        """상태 표시 함수."""
        from selfhealing.api.django.views.recovery import _get_status_display
        
        assert _get_status_display(RecoveryStatus.NORMAL) == "정상"
        assert _get_status_display(RecoveryStatus.EMERGENCY) == "비상"
        assert _get_status_display(RecoveryStatus.RECOVERING) == "복구 중"

    def test_get_status_color(self):
        """상태 색상 함수."""
        from selfhealing.api.django.views.recovery import _get_status_color
        
        assert _get_status_color(RecoveryStatus.NORMAL) == "green"
        assert _get_status_color(RecoveryStatus.EMERGENCY) == "red"
        assert _get_status_color(RecoveryStatus.RECOVERING) == "yellow"

    def test_get_session_progress_none(self):
        """세션 없을 때 진행률."""
        from selfhealing.api.django.views.recovery import _get_session_progress
        
        result = _get_session_progress(None)
        
        assert result["percent"] == 0
        assert result["current_step"] is None

    def test_get_session_progress_with_session(self):
        """세션 있을 때 진행률."""
        from selfhealing.api.django.views.recovery import _get_session_progress
        
        mock_step1 = MagicMock()
        mock_step1.name = "step1"
        mock_step1.completed = True
        
        mock_step2 = MagicMock()
        mock_step2.name = "step2"
        mock_step2.completed = False
        
        mock_session = MagicMock()
        mock_session.steps = [mock_step1, mock_step2]
        mock_session.current_step_index = 1
        
        result = _get_session_progress(mock_session)
        
        assert result["percent"] == 50
        assert result["current_step"] == "step2"
        assert result["completed_steps"] == 1
        assert result["total_steps"] == 2


class TestURLConfiguration:
    """URL 설정 테스트."""

    def test_urls_registered(self):
        """URL 패턴 등록 확인."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [p.name for p in urlpatterns if hasattr(p, 'name')]
        
        assert "recovery-status" in url_names
        assert "recovery-start" in url_names
        assert "recovery-abort" in url_names
        assert "recovery-pending-approvals" in url_names
        assert "recovery-approve" in url_names
        assert "recovery-reject" in url_names
        assert "recovery-history" in url_names
        assert "recovery-widget" in url_names
