"""
Unit Tests for X-Test-Mode Idempotency Views.

X-Test-Mode 환경에서 IdempotencyService 멱등성 보장 동작을 테스트하기 위한 API 테스트.

Tests:
- GenerateKeyView: 멱등성 키 생성 및 해시값 미리보기
- CheckDuplicateView: 중복 요청 감지 동작 테스트
- IdempotencyStatusView: 현재 등록된 키 상태 조회
- RegisterKeyView: 테스트용 키 수동 등록
- ClearKeysView: 테스트 키 삭제
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.xtest.idempotency import (
    GenerateKeyView,
    CheckDuplicateView,
    IdempotencyStatusView,
    RegisterKeyView,
    ClearKeysView,
    XTEST_SOURCE,
    XTEST_METADATA_KEY,
    MAX_STATUS_RESULTS,
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
        "selfhealing.api.django.views.xtest.idempotency.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture
def mock_system_snapshot():
    """Mock system snapshot collection."""
    with patch(
        "selfhealing.api.django.views.xtest.idempotency.collect_system_snapshot",
        return_value={
            "timestamp": "2026-01-26T12:00:00Z",
            "cpu_percent": 25.0,
            "memory_percent": 50.0,
            "metrics_source": "cache",
        },
    ):
        yield


def create_mock_cache():
    """Mock cache 객체 생성."""
    cache_store = {}

    def mock_get(key):
        return cache_store.get(key)

    def mock_set(key, value, timeout=None):
        cache_store[key] = value

    def mock_delete(key):
        cache_store.pop(key, None)

    def mock_ttl(key):
        return 3600 if key in cache_store else None

    mock_cache_obj = MagicMock()
    mock_cache_obj.get = MagicMock(side_effect=mock_get)
    mock_cache_obj.set = MagicMock(side_effect=mock_set)
    mock_cache_obj.delete = MagicMock(side_effect=mock_delete)
    mock_cache_obj.ttl = MagicMock(side_effect=mock_ttl)
    mock_cache_obj._store = cache_store
    return mock_cache_obj


def create_mock_idempotency_service():
    """Mock IdempotencyService 객체 생성."""
    mock_service = MagicMock()
    mock_service.cache_ttl = 3600
    mock_service.DEFAULT_CACHE_TTL = 3600
    mock_service.EXTENDED_CACHE_TTL = 86400
    return mock_service


class TestGenerateKeyView:
    """Tests for GenerateKeyView - 멱등성 키 생성 및 해시 미리보기."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert GenerateKeyView is not None
        assert hasattr(GenerateKeyView, "post")

    def test_view_has_no_authentication(self):
        """View가 인증 없이 접근 가능한지 확인 (헤더 기반 보안)."""
        assert GenerateKeyView.authentication_classes == []

    def test_generate_key_success(self, request_factory, mock_chaos_allowed):
        """키 생성 성공 테스트."""
        mock_service = create_mock_idempotency_service()

        with patch(
            "selfhealing.services.idempotency_service.get_idempotency_service",
            return_value=mock_service,
        ):
            view = GenerateKeyView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/generate-key/",
                {
                    "entity_type": "order",
                    "entity_id": "123",
                    "action": "process",
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert response.data["key_string"] == "order:123:process"
            assert "cache_key" in response.data
            assert "key_hash" in response.data
            assert response.data["domain"] == "EXTERNAL_SERVICE"
            assert response.data["ttl_seconds"] == 3600
            assert "components" in response.data

    def test_generate_key_with_custom_domain(self, request_factory, mock_chaos_allowed):
        """커스텀 도메인으로 키 생성 테스트."""
        mock_service = create_mock_idempotency_service()

        with patch(
            "selfhealing.services.idempotency_service.get_idempotency_service",
            return_value=mock_service,
        ):
            view = GenerateKeyView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/generate-key/",
                {
                    "entity_type": "task",
                    "entity_id": "456",
                    "action": "execute",
                    "domain": "ASYNC_TASK",
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["domain"] == "ASYNC_TASK"

    def test_generate_key_missing_entity_type(self, request_factory, mock_chaos_allowed):
        """entity_type 누락 시 에러 테스트."""
        view = GenerateKeyView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/generate-key/",
            {
                "entity_id": "123",
                "action": "process",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"
        assert "entity_type" in response.data["missing"]

    def test_generate_key_missing_multiple_fields(self, request_factory, mock_chaos_allowed):
        """복수 필수 필드 누락 시 에러 테스트."""
        view = GenerateKeyView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/generate-key/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"
        assert len(response.data["missing"]) == 3

    def test_generate_key_invalid_domain(self, request_factory, mock_chaos_allowed):
        """잘못된 도메인 지정 시 에러 테스트."""
        view = GenerateKeyView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/generate-key/",
            {
                "entity_type": "order",
                "entity_id": "123",
                "action": "process",
                "domain": "INVALID_DOMAIN",
            },
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "invalid_domain"
        assert "valid_domains" in response.data

    def test_generate_key_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = GenerateKeyView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/generate-key/",
            {
                "entity_type": "order",
                "entity_id": "123",
                "action": "process",
            },
            format="json",
        )
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestCheckDuplicateView:
    """Tests for CheckDuplicateView - 중복 요청 감지 테스트."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert CheckDuplicateView is not None
        assert hasattr(CheckDuplicateView, "post")

    def test_check_duplicate_not_found(self, request_factory, mock_chaos_allowed):
        """중복 아닌 경우 테스트."""
        mock_cache = create_mock_cache()
        mock_service = create_mock_idempotency_service()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            with patch(
                "selfhealing.services.idempotency_service.get_idempotency_service",
                return_value=mock_service,
            ):
                view = CheckDuplicateView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/check-duplicate/",
                    {
                        "key": "order:123:process",
                    },
                    format="json",
                )

                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert response.data["status"] == "success"
                assert response.data["is_duplicate"] is False
                assert response.data["registered"] is False

    def test_check_duplicate_with_register(self, request_factory, mock_chaos_allowed):
        """등록과 함께 체크 테스트."""
        mock_cache = create_mock_cache()
        mock_service = create_mock_idempotency_service()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            with patch(
                "selfhealing.services.idempotency_service.get_idempotency_service",
                return_value=mock_service,
            ):
                view = CheckDuplicateView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/check-duplicate/",
                    {
                        "key": "order:456:process",
                        "register": True,
                    },
                    format="json",
                )

                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert response.data["is_duplicate"] is False
                assert response.data["registered"] is True

    def test_check_duplicate_found(self, request_factory, mock_chaos_allowed):
        """중복 발견 테스트."""
        mock_cache = create_mock_cache()
        # 먼저 키 등록
        cache_key = "idempotency:external_service:order:789:process"
        mock_cache._store[cache_key] = {
            "first_seen_at": "2026-01-26T10:00:00Z",
            "source": XTEST_SOURCE,
        }

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = CheckDuplicateView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/check-duplicate/",
                {
                    "key": "order:789:process",
                    "domain": "EXTERNAL_SERVICE",
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["is_duplicate"] is True
            assert response.data["first_seen_at"] == "2026-01-26T10:00:00Z"

    def test_check_duplicate_missing_key(self, request_factory, mock_chaos_allowed):
        """key 파라미터 누락 테스트."""
        view = CheckDuplicateView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/check-duplicate/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"

    def test_check_duplicate_different_domains(self, request_factory, mock_chaos_allowed):
        """다른 도메인에서는 중복 아님 테스트."""
        mock_cache = create_mock_cache()
        # EXTERNAL_SERVICE 도메인에 키 등록
        cache_key = "idempotency:external_service:test:1"
        mock_cache._store[cache_key] = {"first_seen_at": "2026-01-26T10:00:00Z"}

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = CheckDuplicateView.as_view()

            # ASYNC_TASK 도메인으로 체크
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/check-duplicate/",
                {
                    "key": "test:1",
                    "domain": "ASYNC_TASK",
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["is_duplicate"] is False  # 다른 도메인이므로 중복 아님

    def test_check_duplicate_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = CheckDuplicateView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/check-duplicate/",
            {"key": "test:key"},
            format="json",
        )
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestIdempotencyStatusView:
    """Tests for IdempotencyStatusView - 상태 조회 테스트."""

    def test_view_exists_and_has_get_method(self):
        """View 클래스가 존재하고 get 메서드가 있는지 확인."""
        assert IdempotencyStatusView is not None
        assert hasattr(IdempotencyStatusView, "get")

    def test_status_empty(self, request_factory, mock_chaos_allowed, mock_system_snapshot):
        """등록된 키가 없는 경우 테스트."""
        mock_cache = create_mock_cache()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = IdempotencyStatusView.as_view()
            request = request_factory.get(
                "/api/self-healing/xtest/idempotency/status/",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert response.data["total_xtest_keys"] == 0
            assert response.data["by_domain"] == {}
            assert response.data["recent_keys"] == []

    def test_status_with_keys(self, request_factory, mock_chaos_allowed, mock_system_snapshot):
        """등록된 키가 있는 경우 테스트."""
        mock_cache = create_mock_cache()

        # 추적 목록에 키 추가
        tracked_keys = [
            "idempotency:external_service:order:1",
            "idempotency:external_service:order:2",
            "idempotency:async_task:task:1",
        ]
        mock_cache._store[XTEST_METADATA_KEY] = tracked_keys

        # 키별 데이터 추가
        for key in tracked_keys:
            mock_cache._store[key] = {
                "first_seen_at": "2026-01-26T10:00:00Z",
                "source": XTEST_SOURCE,
            }

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = IdempotencyStatusView.as_view()
            request = request_factory.get(
                "/api/self-healing/xtest/idempotency/status/",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["total_xtest_keys"] == 3
            assert response.data["by_domain"]["EXTERNAL_SERVICE"] == 2
            assert response.data["by_domain"]["ASYNC_TASK"] == 1
            assert len(response.data["recent_keys"]) == 3

    def test_status_with_domain_filter(self, request_factory, mock_chaos_allowed, mock_system_snapshot):
        """도메인 필터 적용 테스트."""
        mock_cache = create_mock_cache()

        tracked_keys = [
            "idempotency:external_service:order:1",
            "idempotency:async_task:task:1",
        ]
        mock_cache._store[XTEST_METADATA_KEY] = tracked_keys

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = IdempotencyStatusView.as_view()
            request = request_factory.get(
                "/api/self-healing/xtest/idempotency/status/",
                {"domain": "EXTERNAL_SERVICE"},
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            # 필터 적용으로 EXTERNAL_SERVICE만 조회
            assert response.data["filters_applied"]["domain"] == "EXTERNAL_SERVICE"

    def test_status_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = IdempotencyStatusView.as_view()
        request = request_factory.get("/api/self-healing/xtest/idempotency/status/")
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestRegisterKeyView:
    """Tests for RegisterKeyView - 키 등록 테스트."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert RegisterKeyView is not None
        assert hasattr(RegisterKeyView, "post")

    def test_register_key_success(self, request_factory, mock_chaos_allowed):
        """키 등록 성공 테스트."""
        mock_cache = create_mock_cache()
        mock_service = create_mock_idempotency_service()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            with patch(
                "selfhealing.services.idempotency_service.get_idempotency_service",
                return_value=mock_service,
            ):
                view = RegisterKeyView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/register/",
                    {
                        "key": "order:123:process",
                        "ttl_seconds": 1800,
                    },
                    format="json",
                )

                response = view(request)

                assert response.status_code == status.HTTP_201_CREATED
                assert response.data["status"] == "success"
                assert response.data["registered"] is True
                assert response.data["key_string"] == "order:123:process"
                assert response.data["ttl_seconds"] == 1800
                assert "expires_at" in response.data

    def test_register_key_with_result_data(self, request_factory, mock_chaos_allowed):
        """결과 데이터와 함께 키 등록 테스트."""
        mock_cache = create_mock_cache()
        mock_service = create_mock_idempotency_service()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            with patch(
                "selfhealing.services.idempotency_service.get_idempotency_service",
                return_value=mock_service,
            ):
                view = RegisterKeyView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/register/",
                    {
                        "key": "order:456:process",
                        "result_data": {"order_id": 456, "status": "completed"},
                    },
                    format="json",
                )

                response = view(request)

                assert response.status_code == status.HTTP_201_CREATED
                assert response.data["metadata"]["has_result_data"] is True

    def test_register_key_missing_key(self, request_factory, mock_chaos_allowed):
        """key 파라미터 누락 테스트."""
        view = RegisterKeyView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/register/",
            {},
            format="json",
        )

        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_fields"

    def test_register_key_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = RegisterKeyView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/register/",
            {"key": "test:key"},
            format="json",
        )
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestClearKeysView:
    """Tests for ClearKeysView - 키 삭제 테스트."""

    def test_view_exists_and_has_post_method(self):
        """View 클래스가 존재하고 post 메서드가 있는지 확인."""
        assert ClearKeysView is not None
        assert hasattr(ClearKeysView, "post")

    def test_clear_single_key(self, request_factory, mock_chaos_allowed):
        """단일 키 삭제 테스트."""
        mock_cache = create_mock_cache()

        # 키 등록
        cache_key = "idempotency:external_service:order:123:process"
        mock_cache._store[cache_key] = {"source": XTEST_SOURCE}
        mock_cache._store[XTEST_METADATA_KEY] = [cache_key]

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = ClearKeysView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/clear/",
                {
                    "key": "order:123:process",
                    "domain": "EXTERNAL_SERVICE",
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == "success"
            assert response.data["cleared_count"] == 1
            assert cache_key in response.data["cleared_keys"]

    def test_clear_all_xtest_keys(self, request_factory, mock_chaos_allowed):
        """X-Test 생성 키 전체 삭제 테스트."""
        mock_cache = create_mock_cache()

        # 다수 키 등록
        keys = [
            "idempotency:external_service:order:1",
            "idempotency:external_service:order:2",
            "idempotency:async_task:task:1",
        ]
        for key in keys:
            mock_cache._store[key] = {"source": XTEST_SOURCE}
        mock_cache._store[XTEST_METADATA_KEY] = keys

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = ClearKeysView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/clear/",
                {
                    "clear_all_xtest": True,
                },
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["cleared_count"] == 3

    def test_clear_missing_parameters(self, request_factory, mock_chaos_allowed):
        """파라미터 누락 테스트."""
        mock_cache = create_mock_cache()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            view = ClearKeysView.as_view()
            request = request_factory.post(
                "/api/self-healing/xtest/idempotency/clear/",
                {},
                format="json",
            )

            response = view(request)

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.data["error"] == "missing_parameters"

    def test_clear_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = ClearKeysView.as_view()
        request = request_factory.post(
            "/api/self-healing/xtest/idempotency/clear/",
            {"clear_all_xtest": True},
            format="json",
        )
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestIdempotencyFlowIntegration:
    """Idempotency 플로우 통합 테스트."""

    def test_generate_check_register_flow(self, request_factory, mock_chaos_allowed):
        """키 생성 → 중복 체크 → 등록 → 중복 확인 플로우 테스트."""
        mock_cache = create_mock_cache()
        mock_service = create_mock_idempotency_service()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            with patch(
                "selfhealing.services.idempotency_service.get_idempotency_service",
                return_value=mock_service,
            ):
                # 1. 키 생성
                generate_view = GenerateKeyView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/generate-key/",
                    {
                        "entity_type": "order",
                        "entity_id": "999",
                        "action": "process",
                    },
                    format="json",
                )
                response = generate_view(request)
                assert response.status_code == status.HTTP_200_OK
                key_string = response.data["key_string"]

                # 2. 첫 번째 중복 체크 (등록과 함께)
                check_view = CheckDuplicateView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/check-duplicate/",
                    {
                        "key": key_string,
                        "register": True,
                    },
                    format="json",
                )
                response = check_view(request)
                assert response.status_code == status.HTTP_200_OK
                assert response.data["is_duplicate"] is False
                assert response.data["registered"] is True

                # 3. 두 번째 중복 체크 (이미 등록됨)
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/check-duplicate/",
                    {
                        "key": key_string,
                    },
                    format="json",
                )
                response = check_view(request)
                assert response.status_code == status.HTTP_200_OK
                assert response.data["is_duplicate"] is True

    def test_register_and_clear_flow(self, request_factory, mock_chaos_allowed, mock_system_snapshot):
        """등록 → 상태 확인 → 삭제 플로우 테스트."""
        mock_cache = create_mock_cache()
        mock_service = create_mock_idempotency_service()

        with patch("selfhealing.api.django.views.xtest.idempotency.cache", mock_cache):
            with patch(
                "selfhealing.services.idempotency_service.get_idempotency_service",
                return_value=mock_service,
            ):
                # 1. 키 등록
                register_view = RegisterKeyView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/register/",
                    {
                        "key": "test:flow:key",
                    },
                    format="json",
                )
                response = register_view(request)
                assert response.status_code == status.HTTP_201_CREATED

                # 2. 상태 확인
                status_view = IdempotencyStatusView.as_view()
                request = request_factory.get(
                    "/api/self-healing/xtest/idempotency/status/",
                )
                response = status_view(request)
                assert response.status_code == status.HTTP_200_OK
                assert response.data["total_xtest_keys"] >= 1

                # 3. 전체 삭제
                clear_view = ClearKeysView.as_view()
                request = request_factory.post(
                    "/api/self-healing/xtest/idempotency/clear/",
                    {
                        "clear_all_xtest": True,
                    },
                    format="json",
                )
                response = clear_view(request)
                assert response.status_code == status.HTTP_200_OK

                # 4. 삭제 후 상태 확인
                request = request_factory.get(
                    "/api/self-healing/xtest/idempotency/status/",
                )
                response = status_view(request)
                assert response.status_code == status.HTTP_200_OK
                assert response.data["total_xtest_keys"] == 0
