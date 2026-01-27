"""
Unit Tests for X-Test-Mode DLQ Views.

X-Test-Mode 환경에서 DLQ 동작을 테스트하기 위한 API 테스트.

Tests:
- InjectDLQEntryView: DLQ 테스트 항목 생성
- DLQXTestStatusView: DLQ 현황 조회
- ForceStatusView: DLQ 상태 강제 변경
- ResetDLQXTestView: X-Test-Mode 생성 항목 초기화
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.xtest.dlq import (
    InjectDLQEntryView,
    DLQXTestStatusView,
    ForceStatusView,
    ResetDLQXTestView,
    XTEST_SOURCE,
    MAX_INJECT_COUNT,
)


@pytest.fixture
def request_factory():
    """API request factory for creating test requests."""
    return APIRequestFactory()


@pytest.fixture
def chaos_headers():
    """X-Test-Mode required headers."""
    return {"HTTP_X_TEST_MODE": "chaos-monkey"}


@pytest.fixture
def mock_chaos_allowed():
    """Mock XTestModeMixin.is_chaos_allowed to always return True."""
    with patch(
        "selfhealing.api.django.views.xtest.dlq.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture
def mock_dlq_service():
    """Mock DLQ service for testing."""
    mock_service = MagicMock()

    # Mock store_failure result
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.dlq_id = 123
    mock_service.store_failure.return_value = mock_result

    # Mock get_stats result
    mock_service.get_stats.return_value = {
        "total": 100,
        "by_status": {"pending": 80, "resolved": 20},
        "by_domain": {"external_service": 50, "internal_process": 50},
    }

    # Mock list_entries result
    mock_list_result = MagicMock()
    mock_list_result.results = [
        {
            "id": 1,
            "status": "pending",
            "domain": "external_service",
            "failure_type": "TIMEOUT",
            "created_at": "2025-01-26T10:00:00+09:00",
            "error_message": "Test error",
            "metadata": {"source": XTEST_SOURCE},
        }
    ]
    mock_service.list_entries.return_value = mock_list_result

    # Mock get_entry result
    mock_service.get_entry.return_value = {
        "id": 123,
        "status": "pending",
        "domain": "external_service",
        "failure_type": "TIMEOUT",
    }

    # Mock repository
    mock_service.repository = MagicMock()
    mock_service.repository.update_status.return_value = True
    mock_service.repository.delete_by_id.return_value = True

    with patch("selfhealing.services.dlq.get_dlq_service", return_value=mock_service):
        yield mock_service


class TestInjectDLQEntryView:
    """Tests for InjectDLQEntryView - DLQ 테스트 항목 생성."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert InjectDLQEntryView is not None
        assert hasattr(InjectDLQEntryView, "post")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인 (헤더 기반 보안)."""
        assert InjectDLQEntryView.authentication_classes == []

    def test_inject_success(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """DLQ 항목 생성 성공 테스트."""
        view = InjectDLQEntryView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/inject/",
            {
                "domain": "external_service",
                "failure_type": "TIMEOUT",
                "count": 1,
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] == "success"
        assert response.data["created_count"] == 1
        assert response.data["domain"] == "external_service"
        assert "dlq_ids" in response.data
        assert "xtest_session" in response.data

    def test_inject_missing_domain(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """필수 필드 누락 시 에러 테스트 - domain."""
        view = InjectDLQEntryView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/inject/",
            {"failure_type": "TIMEOUT"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"

    def test_inject_missing_failure_type(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """필수 필드 누락 시 에러 테스트 - failure_type."""
        view = InjectDLQEntryView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/inject/",
            {"domain": "external_service"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"

    def test_inject_count_limit_exceeded(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """최대 주입 횟수 초과 테스트."""
        view = InjectDLQEntryView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/inject/",
            {
                "domain": "external_service",
                "failure_type": "TIMEOUT",
                "count": MAX_INJECT_COUNT + 1,
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "injection_limit_exceeded"

    def test_inject_multiple_entries(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """다중 항목 생성 테스트."""
        view = InjectDLQEntryView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/inject/",
            {
                "domain": "external_service",
                "failure_type": "TIMEOUT",
                "count": 5,
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["created_count"] == 5
        assert len(response.data["dlq_ids"]) == 5

    def test_inject_without_chaos_header_denied(self, request_factory, mock_dlq_service):
        """X-Test-Mode 헤더 없이 요청 시 거부 테스트."""
        view = InjectDLQEntryView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/inject/",
            {
                "domain": "external_service",
                "failure_type": "TIMEOUT",
            },
            format="json",
        )

        response = view(request)

        # 헤더 없으면 403 Forbidden
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestDLQXTestStatusView:
    """Tests for DLQXTestStatusView - DLQ 현황 조회."""

    def test_view_exists_and_has_get_method(self):
        """View 클래스가 존재하고 get 메서드가 있는지 확인."""
        assert DLQXTestStatusView is not None
        assert hasattr(DLQXTestStatusView, "get")

    def test_status_query_success(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """DLQ 현황 조회 성공 테스트."""
        view = DLQXTestStatusView.as_view()
        request = request_factory.get("/api/self-healing/xtest/dlq/status/")

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "total_count" in response.data
        assert "by_status" in response.data
        assert "by_domain" in response.data
        assert "recent_entries" in response.data

    def test_status_with_domain_filter(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """도메인 필터 적용 테스트."""
        view = DLQXTestStatusView.as_view()
        request = request_factory.get(
            "/api/self-healing/xtest/dlq/status/",
            {"domain": "external_service"},
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["filters_applied"]["domain"] == "external_service"

    def test_status_with_status_filter(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """상태 필터 적용 테스트."""
        view = DLQXTestStatusView.as_view()
        request = request_factory.get(
            "/api/self-healing/xtest/dlq/status/",
            {"status": "pending"},
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["filters_applied"]["status"] == "pending"

    def test_status_without_chaos_header_denied(self, request_factory, mock_dlq_service):
        """X-Test-Mode 헤더 없이 요청 시 거부 테스트."""
        view = DLQXTestStatusView.as_view()
        request = request_factory.get("/api/self-healing/xtest/dlq/status/")

        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestForceStatusView:
    """Tests for ForceStatusView - DLQ 상태 강제 변경."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert ForceStatusView is not None
        assert hasattr(ForceStatusView, "post")

    def test_force_status_success(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """상태 강제 변경 성공 테스트."""
        view = ForceStatusView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/force-status/",
            {
                "dlq_id": 123,
                "new_status": "reviewing",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert response.data["dlq_id"] == 123
        assert response.data["new_status"] == "reviewing"

    def test_force_status_to_resolved(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """resolved 상태로 변경 테스트."""
        view = ForceStatusView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/force-status/",
            {
                "dlq_id": 123,
                "new_status": "resolved",
                "reason": "Test resolution",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        # resolve_entry 메서드가 호출되어야 함
        mock_dlq_service.resolve_entry.assert_called_once()

    def test_force_status_missing_dlq_id(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """필수 필드 누락 - dlq_id."""
        view = ForceStatusView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/force-status/",
            {"new_status": "resolved"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"

    def test_force_status_invalid_status(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """유효하지 않은 상태값 테스트."""
        view = ForceStatusView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/force-status/",
            {
                "dlq_id": 123,
                "new_status": "invalid_status",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "invalid_status"

    def test_force_status_entry_not_found(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """존재하지 않는 항목 테스트."""
        mock_dlq_service.get_entry.return_value = None

        view = ForceStatusView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/force-status/",
            {
                "dlq_id": 999,
                "new_status": "resolved",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "not_found"

    def test_force_status_without_chaos_header_denied(self, request_factory, mock_dlq_service):
        """X-Test-Mode 헤더 없이 요청 시 거부 테스트."""
        view = ForceStatusView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/force-status/",
            {
                "dlq_id": 123,
                "new_status": "resolved",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestResetDLQXTestView:
    """Tests for ResetDLQXTestView - X-Test-Mode 생성 항목 초기화."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert ResetDLQXTestView is not None
        assert hasattr(ResetDLQXTestView, "post")

    def test_reset_success(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """초기화 성공 테스트."""
        view = ResetDLQXTestView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/reset/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "deleted_count" in response.data
        assert response.data["xtest_only"] is True

    def test_reset_with_domain_filter(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """도메인 필터 적용 초기화 테스트."""
        view = ResetDLQXTestView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/reset/",
            {"domain": "external_service"},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["domain_filter"] == "external_service"

    def test_reset_all_entries(self, request_factory, mock_chaos_allowed, mock_dlq_service):
        """X-Test 외 모든 항목 초기화 테스트."""
        # xtest_only=False 일 때는 모든 항목 삭제
        mock_list_result = MagicMock()
        mock_list_result.results = [
            {"id": 1, "metadata": {}},
            {"id": 2, "metadata": {}},
        ]
        mock_dlq_service.list_entries.return_value = mock_list_result

        view = ResetDLQXTestView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/reset/",
            {"created_by_xtest": False},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["xtest_only"] is False

    def test_reset_without_chaos_header_denied(self, request_factory, mock_dlq_service):
        """X-Test-Mode 헤더 없이 요청 시 거부 테스트."""
        view = ResetDLQXTestView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/dlq/reset/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestXTestModeConstants:
    """Tests for X-Test-Mode DLQ constants."""

    def test_xtest_source_constant(self):
        """XTEST_SOURCE 상수 확인."""
        assert XTEST_SOURCE == "x-test-mode"

    def test_max_inject_count_constant(self):
        """MAX_INJECT_COUNT 상수 확인."""
        assert MAX_INJECT_COUNT == 20


class TestImportCompatibility:
    """Tests for backward compatibility imports."""

    def test_import_from_xtest_package(self):
        """xtest 패키지에서 import 가능한지 확인."""
        from selfhealing.api.django.views.xtest import (
            InjectDLQEntryView,
            DLQXTestStatusView,
            ForceStatusView,
            ResetDLQXTestView,
        )

        assert InjectDLQEntryView is not None
        assert DLQXTestStatusView is not None
        assert ForceStatusView is not None
        assert ResetDLQXTestView is not None

    def test_import_from_views_package(self):
        """views 패키지 __init__.py에서 import 가능한지 확인."""
        from selfhealing.api.django.views import (
            InjectDLQEntryView,
            DLQXTestStatusView,
            ForceStatusView,
            ResetDLQXTestView,
        )

        assert InjectDLQEntryView is not None
        assert DLQXTestStatusView is not None
        assert ForceStatusView is not None
        assert ResetDLQXTestView is not None
