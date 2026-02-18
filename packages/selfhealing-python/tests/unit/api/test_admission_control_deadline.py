"""
단위 테스트 — AdmissionControlMiddleware의 Deadline 처리.

테스트 항목:
- Deadline 헤더 수신 시 Fast-Fail (503 응답)
- 충분한 deadline 시 정상 통과
- 헤더 미포함 시 기존 동작 유지
- 잘못된 형식 헤더 → 무시 (기존 동작)
- 503 응답의 Retry-After: 0 확인
- 응답 body의 code == "DEADLINE_FAST_FAIL" 확인
"""

import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.api.django.admission_control import (
    AdmissionControlMiddleware,
)
from selfhealing.scaling.deadline_context import (
    DEADLINE_META_KEY,
    DEFAULT_MINIMUM_USEFUL_TIME_MS,
    _request_deadline,
)


@pytest.fixture(autouse=True)
def _reset_deadline():
    """각 테스트 전후로 deadline ContextVar를 초기화한다."""
    _request_deadline.set(None)
    yield
    _request_deadline.set(None)


class TestAdmissionControlDeadlineBehavior:
    """AdmissionControlMiddleware Deadline 처리 동작 검증."""

    @pytest.fixture
    def mock_tier_result(self):
        result = MagicMock()
        result.tier_id = "standard"
        return result

    @pytest.fixture
    def mock_tier_def(self):
        td = MagicMock()
        td.priority = 50
        return td

    @pytest.fixture
    def mock_registry(self, mock_tier_result, mock_tier_def):
        registry = MagicMock()
        registry.resolve_tier_with_fallback.return_value = mock_tier_result
        registry.get_tier.return_value = mock_tier_def
        return registry

    @pytest.fixture
    def mock_traffic_gate(self):
        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        decision.bulkhead_acquired = False
        decision.bulkhead_name = None
        gate.should_allow.return_value = decision
        return gate

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.enabled = True
        settings.get_tier_max_concurrent.side_effect = lambda tid: {
            "critical": 100,
            "standard": 50,
            "non_essential": 20,
        }.get(tid, 50)
        return settings

    def _create_middleware(self, mock_settings, mock_registry, mock_traffic_gate):
        """패치된 AdmissionControlMiddleware 생성."""
        mock_response = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        with (
            patch("selfhealing.api.django.admission_control." "AdmissionControlMiddleware._init_dependencies"),
            patch(
                "selfhealing.settings.admission_control." "get_admission_control_settings",
                return_value=mock_settings,
            ),
        ):
            middleware = AdmissionControlMiddleware(get_response)
            middleware._enabled = True
            middleware._registry = mock_registry
            middleware._traffic_gate = mock_traffic_gate
            middleware._settings = mock_settings

        return middleware, get_response, mock_response

    def _create_request(self, deadline_header_value=None):
        """Django request Mock 생성."""
        request = MagicMock()
        request.path = "/api/test"
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.user.is_authenticated = False
        if deadline_header_value is not None:
            request.META[DEADLINE_META_KEY] = deadline_header_value
        return request

    def test_deadline_header_fast_fail(self, mock_settings, mock_registry, mock_traffic_gate):
        """남은 30ms → 503 응답 (DEADLINE_FAST_FAIL)."""
        middleware, get_response, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request("30ms")

        response = middleware(request)

        assert response.status_code == 503
        body = json.loads(response.content)
        assert body["code"] == "DEADLINE_FAST_FAIL"
        get_response.assert_not_called()

    def test_deadline_header_allowed(self, mock_settings, mock_registry, mock_traffic_gate):
        """남은 5000ms → 정상 통과."""
        middleware, get_response, mock_response = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request("5000ms")

        response = middleware(request)

        get_response.assert_called_once_with(request)
        assert response == mock_response

    def test_no_deadline_header(self, mock_settings, mock_registry, mock_traffic_gate):
        """헤더 없음 → 기존 동작 유지."""
        middleware, get_response, mock_response = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request()

        response = middleware(request)

        get_response.assert_called_once_with(request)
        assert response == mock_response

    def test_invalid_deadline_header(self, mock_settings, mock_registry, mock_traffic_gate):
        """잘못된 형식 → 무시, 기존 동작."""
        middleware, get_response, mock_response = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request("invalid_header_value")

        response = middleware(request)

        get_response.assert_called_once_with(request)
        assert response == mock_response

    def test_deadline_response_retry_after(self, mock_settings, mock_registry, mock_traffic_gate):
        """503 응답에 Retry-After: 0 포함."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request("10ms")

        response = middleware(request)

        assert response.status_code == 503
        assert response["Retry-After"] == "0"

    def test_deadline_response_body_structure(self, mock_settings, mock_registry, mock_traffic_gate):
        """응답 body에 필수 필드가 포함된다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request("20ms")

        response = middleware(request)

        body = json.loads(response.content)
        assert body["error"] == "Deadline Exceeded"
        assert body["code"] == "DEADLINE_FAST_FAIL"
        assert "remaining_ms" in body
        assert body["retry_after"] == 0

    def test_deadline_at_exact_minimum(self, mock_settings, mock_registry, mock_traffic_gate):
        """최소 유효 시간과 동일한 값 → 통과 (미만만 거절)."""
        middleware, get_response, mock_response = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        # DEFAULT_MINIMUM_USEFUL_TIME_MS는 50.0
        request = self._create_request(f"{DEFAULT_MINIMUM_USEFUL_TIME_MS}ms")

        response = middleware(request)

        # 50ms == 50ms 이므로 미만이 아님 → 통과
        get_response.assert_called_once_with(request)
