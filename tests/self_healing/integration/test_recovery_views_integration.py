"""
Integration tests for Recovery Views with real Django/Redis connections.

Tests run in Docker Compose environment with:
- Real Django test client
- Real Redis connections
- Real database connections

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#Phase4
    
Run with:
    docker-compose -f docker-compose.test.yml run --rm test-global \
        python -m pytest tests/self_healing/integration/test_recovery_views_integration.py -v
"""

import pytest
import os

from django.urls import reverse
from rest_framework.test import APIClient

# Skip if not in Docker environment
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("TEST_DB_AVAILABLE") != "true",
        reason="Database not available - run in Docker Compose"
    ),
]


@pytest.fixture
def api_client():
    """DRF APIClient 인스턴스."""
    return APIClient()


@pytest.fixture
def authenticated_client(api_client, django_user_model):
    """인증된 API 클라이언트."""
    user = django_user_model.objects.create_user(
        username="recovery_test_user",
        email="recovery_test@example.com",
        password="testpass123"
    )
    api_client.force_authenticate(user=user)
    return api_client


class TestRecoveryStatusViewIntegration:
    """RecoveryStatusView 통합 테스트."""

    def test_status_endpoint_exists(self, authenticated_client):
        """상태 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-status")
        except Exception:
            url = "/api/self-healing/recovery/status/"
        
        response = authenticated_client.get(url)
        
        # Should not be 404
        assert response.status_code != 404, f"Endpoint not found: {url}"

    def test_status_returns_valid_response(self, authenticated_client):
        """상태 API 응답 형식 확인."""
        try:
            url = reverse("selfhealing:recovery-status")
        except Exception:
            url = "/api/self-healing/recovery/status/"
        
        response = authenticated_client.get(url)
        
        if response.status_code == 200:
            data = response.json()
            # Should have expected fields
            assert "status" in data or "current_status" in data


class TestRecoveryStartViewIntegration:
    """RecoveryStartView 통합 테스트."""

    def test_start_endpoint_exists(self, authenticated_client):
        """복구 시작 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-start")
        except Exception:
            url = "/api/self-healing/recovery/start/"
        
        response = authenticated_client.post(url, data={}, format="json")
        
        # Should not be 404
        assert response.status_code != 404, f"Endpoint not found: {url}"

    def test_start_requires_authentication(self, api_client):
        """인증 없이 시작 불가."""
        try:
            url = reverse("selfhealing:recovery-start")
        except Exception:
            url = "/api/self-healing/recovery/start/"
        
        response = api_client.post(url, data={}, format="json")
        
        # Should require authentication
        assert response.status_code in [401, 403]


class TestRecoveryAbortViewIntegration:
    """RecoveryAbortView 통합 테스트."""

    def test_abort_endpoint_exists(self, authenticated_client):
        """복구 중단 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-abort")
        except Exception:
            url = "/api/self-healing/recovery/abort/"
        
        response = authenticated_client.post(url, data={}, format="json")
        
        # 404 is expected when no active session exists (business logic)
        # But response should have our expected error message, not Django's default 404
        if response.status_code == 404:
            data = response.json()
            # Our API returns {"error": "No active recovery session..."}
            assert "error" in data, f"Unexpected 404 response: {data}"
        else:
            # Any other status means endpoint exists and processed request
            assert response.status_code in [200, 400, 404, 500]


class TestRecoveryPendingApprovalsViewIntegration:
    """RecoveryPendingApprovalsView 통합 테스트."""

    def test_pending_approvals_endpoint_exists(self, authenticated_client):
        """대기 목록 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-pending-approvals")
        except Exception:
            url = "/api/self-healing/recovery/pending-approvals/"
        
        response = authenticated_client.get(url)
        
        # Should not be 404
        assert response.status_code != 404, f"Endpoint not found: {url}"

    def test_pending_approvals_returns_list(self, authenticated_client):
        """대기 목록 응답 형식 확인."""
        try:
            url = reverse("selfhealing:recovery-pending-approvals")
        except Exception:
            url = "/api/self-healing/recovery/pending-approvals/"
        
        response = authenticated_client.get(url)
        
        if response.status_code == 200:
            data = response.json()
            # Should return list or have pending_requests field
            assert isinstance(data, (list, dict))


class TestRecoveryApproveViewIntegration:
    """RecoveryApproveView 통합 테스트."""

    def test_approve_endpoint_exists(self, authenticated_client):
        """승인 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-approve")
        except Exception:
            url = "/api/self-healing/recovery/approve/"
        
        response = authenticated_client.post(
            url, 
            data={"request_id": "test-request-123"}, 
            format="json"
        )
        
        # 404 is expected when request_id doesn't exist (business logic)
        if response.status_code == 404:
            data = response.json()
            # Our API returns {"error": "...not found..."}
            assert "error" in data, f"Unexpected 404 response: {data}"
        else:
            assert response.status_code in [200, 400, 404, 500]


class TestRecoveryRejectViewIntegration:
    """RecoveryRejectView 통합 테스트."""

    def test_reject_endpoint_exists(self, authenticated_client):
        """거부 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-reject")
        except Exception:
            url = "/api/self-healing/recovery/reject/"
        
        response = authenticated_client.post(
            url, 
            data={"request_id": "test-request-123", "reason": "Test rejection"}, 
            format="json"
        )
        
        # 404 is expected when request_id doesn't exist (business logic)
        if response.status_code == 404:
            data = response.json()
            # Our API returns {"error": "...not found..."}
            assert "error" in data, f"Unexpected 404 response: {data}"
        else:
            assert response.status_code in [200, 400, 404, 500]


class TestRecoveryHistoryViewIntegration:
    """RecoveryHistoryView 통합 테스트."""

    def test_history_endpoint_exists(self, authenticated_client):
        """이력 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-history")
        except Exception:
            url = "/api/self-healing/recovery/history/"
        
        response = authenticated_client.get(url)
        
        # Should not be 404
        assert response.status_code != 404, f"Endpoint not found: {url}"

    def test_history_returns_list(self, authenticated_client):
        """이력 응답 형식 확인."""
        try:
            url = reverse("selfhealing:recovery-history")
        except Exception:
            url = "/api/self-healing/recovery/history/"
        
        response = authenticated_client.get(url)
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (list, dict))


class TestRecoveryDashboardWidgetViewIntegration:
    """RecoveryDashboardWidgetView 통합 테스트."""

    def test_widget_endpoint_exists(self, authenticated_client):
        """위젯 엔드포인트 존재 확인."""
        try:
            url = reverse("selfhealing:recovery-widget")
        except Exception:
            url = "/api/self-healing/recovery/widget/"
        
        response = authenticated_client.get(url)
        
        # Should not be 404
        assert response.status_code != 404, f"Endpoint not found: {url}"

    def test_widget_returns_dashboard_data(self, authenticated_client):
        """위젯 데이터 응답 확인."""
        try:
            url = reverse("selfhealing:recovery-widget")
        except Exception:
            url = "/api/self-healing/recovery/widget/"
        
        response = authenticated_client.get(url)
        
        if response.status_code == 200:
            data = response.json()
            # Widget should return some dashboard data
            assert isinstance(data, dict)


class TestRecoveryEndToEndFlow:
    """Recovery 엔드투엔드 플로우 테스트."""

    def test_full_recovery_api_flow(self, authenticated_client):
        """전체 복구 API 플로우."""
        # 1. Check status
        try:
            status_url = reverse("selfhealing:recovery-status")
        except Exception:
            status_url = "/api/self-healing/recovery/status/"
        
        status_response = authenticated_client.get(status_url)
        assert status_response.status_code != 404
        
        # 2. Get pending approvals
        try:
            pending_url = reverse("selfhealing:recovery-pending-approvals")
        except Exception:
            pending_url = "/api/self-healing/recovery/pending-approvals/"
        
        pending_response = authenticated_client.get(pending_url)
        assert pending_response.status_code != 404
        
        # 3. Get history
        try:
            history_url = reverse("selfhealing:recovery-history")
        except Exception:
            history_url = "/api/self-healing/recovery/history/"
        
        history_response = authenticated_client.get(history_url)
        assert history_response.status_code != 404
        
        # 4. Get widget data
        try:
            widget_url = reverse("selfhealing:recovery-widget")
        except Exception:
            widget_url = "/api/self-healing/recovery/widget/"
        
        widget_response = authenticated_client.get(widget_url)
        assert widget_response.status_code != 404
