"""
Unit tests for AdmissionControlMiddleware (236 작업 2).

테스트 항목:
- TIER_PRIORITY_MAP 계약값 검증
- request 객체에 tier 정보 주입 동작
- TrafficGate 허용/거부 분기 동작
- 거부 시 503 응답 형태
- 의존성 초기화 실패 시 fail-open 동작
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from selfhealing.api.django.admission_control import (
    TIER_PRIORITY_MAP,
    AdmissionControlMiddleware,
)


class TestTierPriorityMapContract:
    """TIER_PRIORITY_MAP 상수 계약값 검증."""

    def test_has_three_tiers(self):
        """3개 tier가 정의되어야 한다."""
        assert len(TIER_PRIORITY_MAP) == 3

    def test_critical_value(self):
        """critical → 0."""
        assert TIER_PRIORITY_MAP["critical"] == 0

    def test_standard_value(self):
        """standard → 50."""
        assert TIER_PRIORITY_MAP["standard"] == 50

    def test_non_essential_value(self):
        """non_essential → 100."""
        assert TIER_PRIORITY_MAP["non_essential"] == 100

    def test_ordering(self):
        """critical < standard < non_essential 순으로 priority가 증가한다."""
        assert TIER_PRIORITY_MAP["critical"] < TIER_PRIORITY_MAP["standard"] < TIER_PRIORITY_MAP["non_essential"]


class TestAdmissionControlMiddlewareBehavior:
    """AdmissionControlMiddleware 동작 검증."""

    @pytest.fixture
    def mock_tier_result(self):
        """TierResult Mock."""
        result = MagicMock()
        result.tier_id = "standard"
        return result

    @pytest.fixture
    def mock_tier_def(self):
        """TierDefinition Mock."""
        td = MagicMock()
        td.priority = 50
        return td

    @pytest.fixture
    def mock_registry(self, mock_tier_result, mock_tier_def):
        """TierRegistry Mock."""
        registry = MagicMock()
        registry.resolve_tier_with_fallback.return_value = mock_tier_result
        registry.get_tier.return_value = mock_tier_def
        return registry

    @pytest.fixture
    def mock_traffic_gate(self):
        """TrafficGate Mock — 허용 결정."""
        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        decision.bulkhead_acquired = False
        decision.bulkhead_name = None
        gate.should_allow.return_value = decision
        return gate

    @pytest.fixture
    def mock_settings(self):
        """AdmissionControlSettings Mock."""
        settings = MagicMock()
        settings.enabled = True
        settings.get_tier_max_concurrent.side_effect = lambda tid: {
            "critical": 100,
            "standard": 50,
            "non_essential": 20,
        }.get(tid, 50)
        settings.get_tier_bulkhead_timeout.side_effect = lambda tid: {
            "critical": 0.05,
            "standard": 0.03,
        }.get(tid)
        return settings

    @pytest.fixture
    def mock_request(self):
        """Django request Mock."""
        request = MagicMock()
        request.path = "/api/self-healing/config/test"
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.user.is_authenticated = False
        return request

    def _create_middleware(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
    ):
        """패치된 AdmissionControlMiddleware 생성."""
        mock_response = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        with (
            patch("selfhealing.api.django.admission_control.AdmissionControlMiddleware._init_dependencies"),
            patch(
                "selfhealing.settings.admission_control.get_admission_control_settings",
                return_value=mock_settings,
            ),
        ):
            middleware = AdmissionControlMiddleware(get_response)
            middleware._enabled = True
            middleware._registry = mock_registry
            middleware._traffic_gate = mock_traffic_gate
            middleware._settings = mock_settings

        return middleware, get_response, mock_response

    def test_allowed_request_passes_through(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
        mock_request,
    ):
        """TrafficGate 허용 시 요청이 다음 미들웨어로 전달된다."""
        middleware, get_response, mock_response = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )

        response = middleware(mock_request)

        get_response.assert_called_once_with(mock_request)
        assert response == mock_response

    def test_tier_info_injected_into_request(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
        mock_request,
    ):
        """허용된 요청의 request 객체에 _selfhealing_tier_id가 주입된다."""
        middleware, get_response, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )

        middleware(mock_request)

        assert mock_request._selfhealing_tier_id == "standard"

    def test_tier_priority_injected_into_request(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
        mock_request,
        mock_tier_def,
    ):
        """허용된 요청의 request 객체에 _selfhealing_tier_priority가 주입된다."""
        middleware, get_response, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )

        middleware(mock_request)

        assert mock_request._selfhealing_tier_priority == mock_tier_def.priority

    def test_traffic_gate_called_with_correct_priority(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
        mock_request,
    ):
        """TrafficGate가 TIER_PRIORITY_MAP의 값으로 호출된다."""
        middleware, _, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )

        middleware(mock_request)

        expected_priority = TIER_PRIORITY_MAP["standard"]
        mock_traffic_gate.should_allow.assert_called_once_with(
            priority=expected_priority,
            bulkhead_name="tier:standard",
            bulkhead_timeout=0.03,
        )

    def test_rejected_request_returns_503(
        self,
        mock_settings,
        mock_registry,
        mock_request,
    ):
        """TrafficGate 거부 시 503 응답을 반환한다."""
        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = False
        decision.gate = "RateController"
        decision.reason = "Rate limit exceeded"
        gate.should_allow.return_value = decision

        middleware, get_response, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            gate,
        )

        response = middleware(mock_request)

        # 503 응답 확인
        assert response.status_code == 503
        get_response.assert_not_called()

    def test_rejected_response_has_retry_after_header(
        self,
        mock_settings,
        mock_registry,
        mock_request,
    ):
        """거부 응답에 Retry-After 헤더가 포함된다."""
        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = False
        decision.gate = "RateController"
        decision.reason = "Rate limit exceeded"
        gate.should_allow.return_value = decision

        middleware, _, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            gate,
        )

        response = middleware(mock_request)

        assert response["Retry-After"] == "5"

    def test_disabled_middleware_passes_through(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
        mock_request,
    ):
        """비활성화된 미들웨어는 요청을 그대로 통과시킨다."""
        middleware, get_response, mock_response = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )
        middleware._enabled = False

        response = middleware(mock_request)

        get_response.assert_called_once_with(mock_request)
        mock_traffic_gate.should_allow.assert_not_called()

    def test_exception_during_processing_allows_request(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
        mock_request,
    ):
        """처리 중 예외 발생 시 요청을 허용한다 (fail-open)."""
        mock_registry.resolve_tier_with_fallback.side_effect = RuntimeError("test error")

        middleware, get_response, mock_response = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )

        response = middleware(mock_request)

        get_response.assert_called_once_with(mock_request)

    def test_bulkhead_released_after_response(
        self,
        mock_settings,
        mock_registry,
        mock_request,
    ):
        """Bulkhead 획득 성공 시 응답 후 release가 호출된다."""
        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        decision.bulkhead_acquired = True
        decision.bulkhead_name = "tier:standard"
        gate.should_allow.return_value = decision

        middleware, get_response, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            gate,
        )

        middleware(mock_request)

        gate.release_bulkhead.assert_called_once_with("tier:standard")

    def test_client_ip_from_x_forwarded_for(
        self,
        mock_settings,
        mock_registry,
        mock_traffic_gate,
    ):
        """X-Forwarded-For 헤더에서 클라이언트 IP를 추출한다."""
        request = MagicMock()
        request.path = "/api/test"
        request.META = {
            "HTTP_X_FORWARDED_FOR": "10.0.0.1, 10.0.0.2",
            "REMOTE_ADDR": "127.0.0.1",
        }
        request.user.is_authenticated = False

        middleware, _, _ = self._create_middleware(
            mock_settings,
            mock_registry,
            mock_traffic_gate,
        )

        middleware(request)

        # resolve_tier_with_fallback에 첫 번째 IP가 전달됨
        call_args = mock_registry.resolve_tier_with_fallback.call_args
        assert call_args.kwargs["client_ip"] == "10.0.0.1"

    def test_critical_path_uses_critical_priority(
        self,
        mock_settings,
        mock_traffic_gate,
        mock_request,
    ):
        """critical tier로 분류된 경로는 priority=0으로 TrafficGate에 전달된다."""
        registry = MagicMock()
        tier_result = MagicMock()
        tier_result.tier_id = "critical"
        registry.resolve_tier_with_fallback.return_value = tier_result

        tier_def = MagicMock()
        tier_def.priority = 100
        registry.get_tier.return_value = tier_def

        middleware, _, _ = self._create_middleware(
            mock_settings,
            registry,
            mock_traffic_gate,
        )

        middleware(mock_request)

        mock_traffic_gate.should_allow.assert_called_once_with(
            priority=TIER_PRIORITY_MAP["critical"],
            bulkhead_name="tier:critical",
            bulkhead_timeout=0.05,
        )
