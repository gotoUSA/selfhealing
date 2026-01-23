"""
Governance API Tests.

새로운 통합 거버넌스 API 테스트:
- GET /api/self-healing/metrics/status/ - 통합 상태 조회
- POST /api/self-healing/governance/reconcile/ - 정합성 조정
- POST /api/self-healing/governance/mode/ - 운영 모드 전환

Deprecated API 테스트:
- POST /api/self-healing/metrics/sync/ - Warning 헤더 확인
- GET /api/self-healing/metrics/drift-report/ - Warning 헤더 확인

Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.governance import (
    GovernanceService,
    MetricStatusView,
    GovernanceReconcileView,
    GovernanceModeView,
    get_governance_service,
    reset_governance_service,
)
from selfhealing.metrics.reliability_manager import (
    OperatingMode,
    ReliabilityLevel,
    MetricReliabilityState,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def api_factory():
    """API request factory."""
    return APIRequestFactory()


@pytest.fixture
def admin_user():
    """Admin user mock."""
    user = Mock()
    user.is_authenticated = True
    user.username = "admin"
    user.is_staff = True
    user.is_superuser = True
    return user


@pytest.fixture
def governance_service():
    """Fresh governance service instance."""
    reset_governance_service()
    return get_governance_service()


@pytest.fixture
def mock_reliability_manager():
    """Mock reliability manager."""
    with patch(
        "selfhealing.metrics.reliability_manager.get_reliability_manager"
    ) as mock_get:
        manager = Mock()
        manager.get_all_states.return_value = {
            "payment": MetricReliabilityState(
                domain="payment",
                reliability_level=ReliabilityLevel.HIGH,
                operating_mode=OperatingMode.NORMAL,
                last_sync_time=1703404800.0,  # 2023-12-24 12:00:00 UTC
                last_sync_source="push",
                consecutive_successful_syncs=5,
                current_value=3,
                value_source="push",
            ),
            "point": MetricReliabilityState(
                domain="point",
                reliability_level=ReliabilityLevel.MEDIUM,
                operating_mode=OperatingMode.NORMAL,
                last_sync_time=1703404500.0,
                last_sync_source="db",
                consecutive_successful_syncs=3,
                current_value=1,
                value_source="db",
            ),
        }
        manager.get_global_mode.return_value = OperatingMode.NORMAL
        manager.force_global_mode = Mock()
        mock_get.return_value = manager
        yield manager


# =============================================================================
# GovernanceService Tests
# =============================================================================


class TestGovernanceService:
    """GovernanceService 단위 테스트."""
    
    def test_get_status_returns_complete_structure(
        self, governance_service, mock_reliability_manager
    ):
        """get_status가 완전한 구조를 반환하는지 확인."""
        result = governance_service.get_status()
        
        assert "generated_at" in result
        assert "operating_mode" in result
        assert "overall_health" in result
        assert "sync_status" in result
        assert "snapshot_health" in result
        assert "drift_summary" in result
        assert "domains" in result
        assert "next_sync_expected_at" in result  # 피드백 반영
    
    def test_get_status_operating_mode(
        self, governance_service, mock_reliability_manager
    ):
        """운영 모드가 올바르게 반환되는지 확인."""
        result = governance_service.get_status()
        
        assert result["operating_mode"] == "normal"
    
    def test_get_status_domains_structure(
        self, governance_service, mock_reliability_manager
    ):
        """도메인별 상태가 올바르게 반환되는지 확인."""
        result = governance_service.get_status()
        
        assert "payment" in result["domains"]
        assert "point" in result["domains"]
        
        payment = result["domains"]["payment"]
        assert "reliability_level" in payment
        assert "operating_mode" in payment
        assert "dlq_pending" in payment
    
    def test_set_startup_info(self, governance_service):
        """서버 시작 정보 설정 테스트."""
        now = 1703404800.0
        next_sync = now + 30.0
        
        governance_service.set_startup_info(now, next_sync)
        
        result = governance_service.get_status()
        assert result["next_sync_expected_at"] is not None
    
    @patch("selfhealing.api.django.views.metric_sync.get_metric_sync_service")
    def test_reconcile_calls_sync_service(
        self, mock_get_sync, governance_service
    ):
        """reconcile이 기존 sync 서비스를 호출하는지 확인."""
        mock_sync_service = Mock()
        mock_sync_service.sync_metrics.return_value = {
            "status": "completed",
            "synced_at": "2024-12-24T12:00:00+00:00",
            "actor": "admin",
            "dry_run": False,
            "results": {},
            "summary": {"total_drifts_detected": 0, "total_drifts_corrected": 0},
        }
        mock_get_sync.return_value = mock_sync_service
        
        result = governance_service.reconcile(
            domains=["payment"],
            dry_run=False,
            actor="admin",
            reason="Test reconciliation",
        )
        
        assert result["reconciliation_result"] == "completed"
        assert result["actor"] == "admin"
        mock_sync_service.sync_metrics.assert_called_once()
    
    @patch("selfhealing.metrics.reliability_manager.get_reliability_manager")
    def test_set_mode_valid_modes(
        self, mock_get_manager, governance_service
    ):
        """유효한 모드로 전환 테스트."""
        manager = Mock()
        manager.get_global_mode.return_value = OperatingMode.NORMAL
        manager.force_global_mode = Mock()
        mock_get_manager.return_value = manager
        
        for mode in ["NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"]:
            result = governance_service.set_mode(
                mode=mode,
                actor="admin",
                reason=f"Test {mode}",
            )
            
            assert result["status"] == "mode_changed"
            assert result["current_mode"] == mode.lower()
            assert result["actor"] == "admin"
    
    def test_set_mode_invalid_mode_raises_error(self, governance_service):
        """유효하지 않은 모드는 ValueError를 발생시키는지 확인."""
        with pytest.raises(ValueError) as exc_info:
            governance_service.set_mode(
                mode="INVALID_MODE",
                actor="admin",
            )
        
        assert "Invalid mode" in str(exc_info.value)
    
    @patch("selfhealing.metrics.reliability_manager.get_reliability_manager")
    def test_set_mode_includes_warning_for_strict(
        self, mock_get_manager, governance_service
    ):
        """STRICT 모드 전환 시 경고 메시지 포함 확인."""
        manager = Mock()
        manager.get_global_mode.return_value = OperatingMode.NORMAL
        manager.force_global_mode = Mock()
        mock_get_manager.return_value = manager
        
        result = governance_service.set_mode(
            mode="STRICT",
            actor="admin",
            reason="Emergency situation",
        )
        
        assert result["warning"] is not None
        assert "STRICT" in result["warning"]


# =============================================================================
# MetricStatusView Tests
# =============================================================================


class TestMetricStatusView:
    """GET /metrics/status/ 테스트."""
    
    @patch("selfhealing.metrics.reliability_manager.get_reliability_manager")
    def test_get_status_success(
        self, mock_get_manager, api_factory, admin_user
    ):
        """성공적인 상태 조회."""
        # Mock setup
        manager = Mock()
        manager.get_all_states.return_value = {}
        manager.get_global_mode.return_value = OperatingMode.NORMAL
        mock_get_manager.return_value = manager
        """성공적인 상태 조회."""
        request = api_factory.get("/api/self-healing/metrics/status/")
        request.user = admin_user
        
        view = MetricStatusView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_200_OK
        assert "operating_mode" in response.data
        assert "overall_health" in response.data
        assert "domains" in response.data
    
    def test_get_status_unauthenticated(self, api_factory):
        """인증되지 않은 요청은 실패."""
        request = api_factory.get("/api/self-healing/metrics/status/")
        request.user = Mock(is_authenticated=False)
        
        view = MetricStatusView.as_view()
        response = view(request)
        
        # Permission denied
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# =============================================================================
# GovernanceReconcileView Tests
# =============================================================================


class TestGovernanceReconcileView:
    """POST /governance/reconcile/ 테스트."""
    
    @patch("selfhealing.api.django.views.metric_sync.get_metric_sync_service")
    def test_reconcile_success(
        self, mock_get_sync, api_factory, admin_user
    ):
        """성공적인 정합성 조정."""
        mock_sync_service = Mock()
        mock_sync_service.sync_metrics.return_value = {
            "status": "completed",
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "actor": "admin",
            "dry_run": False,
            "results": {"payment": {"dlq_pending": {"before": 0, "after": 2, "drift": 2}}},
            "summary": {"total_drifts_detected": 1, "total_drifts_corrected": 1},
        }
        mock_get_sync.return_value = mock_sync_service
        
        request = api_factory.post(
            "/api/self-healing/governance/reconcile/",
            data={"domains": ["payment"], "reason": "Test reconciliation"},
            format="json",
        )
        request.user = admin_user
        
        view = GovernanceReconcileView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data["reconciliation_result"] == "completed"
    
    @patch("selfhealing.api.django.views.metric_sync.get_metric_sync_service")
    def test_reconcile_dry_run(
        self, mock_get_sync, api_factory, admin_user
    ):
        """드라이런 정합성 조정."""
        mock_sync_service = Mock()
        mock_sync_service.sync_metrics.return_value = {
            "status": "dry_run",
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "actor": "admin",
            "dry_run": True,
            "results": {},
            "summary": {"total_drifts_detected": 0, "total_drifts_corrected": 0},
        }
        mock_get_sync.return_value = mock_sync_service
        
        request = api_factory.post(
            "/api/self-healing/governance/reconcile/",
            data={"dry_run": True},
            format="json",
        )
        request.user = admin_user
        
        view = GovernanceReconcileView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data["dry_run"] is True


# =============================================================================
# GovernanceModeView Tests
# =============================================================================


class TestGovernanceModeView:
    """POST /governance/mode/ 테스트."""
    
    @patch("selfhealing.metrics.reliability_manager.get_reliability_manager")
    def test_set_mode_success(
        self, mock_get_manager, api_factory, admin_user
    ):
        """성공적인 모드 전환."""
        manager = Mock()
        manager.get_global_mode.return_value = OperatingMode.NORMAL
        manager.force_global_mode = Mock()
        mock_get_manager.return_value = manager
        """성공적인 모드 전환."""
        request = api_factory.post(
            "/api/self-healing/governance/mode/",
            data={"mode": "STRICT", "reason": "Emergency test"},
            format="json",
        )
        request.user = admin_user
        
        view = GovernanceModeView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "mode_changed"
        assert response.data["current_mode"] == "strict"
    
    def test_set_mode_missing_mode(self, api_factory, admin_user):
        """모드 파라미터 누락 시 에러."""
        request = api_factory.post(
            "/api/self-healing/governance/mode/",
            data={"reason": "No mode specified"},
            format="json",
        )
        request.user = admin_user
        
        view = GovernanceModeView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "mode is required" in response.data["error"]
    
    def test_set_mode_invalid_mode(self, api_factory, admin_user):
        """유효하지 않은 모드."""
        request = api_factory.post(
            "/api/self-healing/governance/mode/",
            data={"mode": "INVALID"},
            format="json",
        )
        request.user = admin_user
        
        view = GovernanceModeView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    @patch("selfhealing.metrics.reliability_manager.get_reliability_manager")
    def test_get_current_mode(
        self, mock_get_manager, api_factory, admin_user
    ):
        """현재 모드 조회."""
        manager = Mock()
        manager.get_global_mode.return_value = OperatingMode.NORMAL
        mock_get_manager.return_value = manager
        request = api_factory.get("/api/self-healing/governance/mode/")
        request.user = admin_user
        
        view = GovernanceModeView.as_view()
        response = view(request)
        
        assert response.status_code == status.HTTP_200_OK
        assert "current_mode" in response.data
        assert "valid_modes" in response.data


# =============================================================================
# Integration Tests
# =============================================================================


class TestAPIURLRouting:
    """URL 라우팅 통합 테스트."""
    
    def test_new_endpoints_exist(self):
        """새 엔드포인트가 등록되어 있는지 확인."""
        from django.urls import reverse
        
        # 새 API
        assert reverse("selfhealing:metrics-status")
        assert reverse("selfhealing:governance-reconcile")
        assert reverse("selfhealing:governance-mode")
