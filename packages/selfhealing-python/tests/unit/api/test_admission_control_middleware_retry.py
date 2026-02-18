"""
AdmissionControlMiddleware 동적 Retry-After·Bulkhead Timeout 전달 단위 테스트.

테스트 항목:
- 동작: 거부 응답의 Retry-After가 BackpressureLevel에 따라 변동
- 동작: CRITICAL 레벨은 NONE보다 긴 Retry-After를 반환
- 동작: should_allow() 호출 시 tier별 bulkhead_timeout이 전달
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.api.django.admission_control import (
    TIER_PRIORITY_MAP,
    AdmissionControlMiddleware,
)
from selfhealing.settings.backpressure import (
    BackpressureLevel,
    BackpressureSettings,
    reset_backpressure_settings,
)


class TestDynamicRetryAfterBehavior:
    """거부 응답 Retry-After 레벨 연동 동작 검증."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        reset_backpressure_settings()
        yield
        reset_backpressure_settings()

    def _create_middleware_with_rejection(self, gate_level):
        """지정된 BackpressureLevel로 거부하는 미들웨어+request 생성."""
        mock_response = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        mock_settings = MagicMock()
        mock_settings.enabled = True
        mock_settings.get_tier_max_concurrent.side_effect = lambda tid: 50
        mock_settings.get_tier_bulkhead_timeout.return_value = None

        mock_registry = MagicMock()
        tier_result = MagicMock()
        tier_result.tier_id = "standard"
        mock_registry.resolve_tier_with_fallback.return_value = tier_result
        mock_registry.get_tier.return_value = MagicMock(priority=50)

        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = False
        decision.gate = "RateController"
        decision.reason = "Rate limit exceeded"
        gate.should_allow.return_value = decision
        gate.get_level.return_value = gate_level

        with (
            patch(
                "selfhealing.api.django.admission_control." "AdmissionControlMiddleware._init_dependencies",
            ),
            patch(
                "selfhealing.settings.admission_control." "get_admission_control_settings",
                return_value=mock_settings,
            ),
        ):
            middleware = AdmissionControlMiddleware(get_response)
            middleware._enabled = True
            middleware._registry = mock_registry
            middleware._traffic_gate = gate
            middleware._settings = mock_settings

        request = MagicMock()
        request.path = "/api/test"
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.user.is_authenticated = False

        return middleware, request

    def test_none_level_retry_after_equals_base(self):
        """NONE 레벨 거부 시 Retry-After는 settings base 값과 동일하다."""
        middleware, request = self._create_middleware_with_rejection(
            BackpressureLevel.NONE,
        )

        response = middleware(request)

        bp_settings = BackpressureSettings()
        expected = bp_settings.get_retry_after_for_level(BackpressureLevel.NONE)
        assert response["Retry-After"] == str(expected)

    def test_critical_level_retry_after_greater_than_none(self):
        """CRITICAL 레벨 거부 시 Retry-After가 NONE보다 크다."""
        mw_critical, req_critical = self._create_middleware_with_rejection(
            BackpressureLevel.CRITICAL,
        )
        mw_none, req_none = self._create_middleware_with_rejection(
            BackpressureLevel.NONE,
        )

        resp_critical = mw_critical(req_critical)
        resp_none = mw_none(req_none)

        assert int(resp_critical["Retry-After"]) > int(resp_none["Retry-After"])

    def test_medium_level_retry_after_matches_settings(self):
        """MEDIUM 레벨 거부 시 Retry-After가 settings 계산 결과와 동일하다."""
        middleware, request = self._create_middleware_with_rejection(
            BackpressureLevel.MEDIUM,
        )

        response = middleware(request)

        bp_settings = BackpressureSettings()
        expected = bp_settings.get_retry_after_for_level(BackpressureLevel.MEDIUM)
        assert response["Retry-After"] == str(expected)


class TestBulkheadTimeoutInjectionBehavior:
    """should_allow() 호출 시 tier별 bulkhead_timeout 전달 동작 검증."""

    def test_critical_tier_passes_timeout_to_should_allow(self):
        """critical tier 요청 시 해당 timeout이 should_allow()에 전달된다."""
        mock_response = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        mock_settings = MagicMock()
        mock_settings.enabled = True
        mock_settings.get_tier_max_concurrent.side_effect = lambda tid: 100
        mock_settings.get_tier_bulkhead_timeout.side_effect = lambda tid: {
            "critical": 0.05,
            "standard": 0.03,
        }.get(tid)

        mock_registry = MagicMock()
        tier_result = MagicMock()
        tier_result.tier_id = "critical"
        mock_registry.resolve_tier_with_fallback.return_value = tier_result
        mock_registry.get_tier.return_value = MagicMock(priority=100)

        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        decision.bulkhead_acquired = False
        decision.bulkhead_name = None
        gate.should_allow.return_value = decision

        with (
            patch(
                "selfhealing.api.django.admission_control." "AdmissionControlMiddleware._init_dependencies",
            ),
            patch(
                "selfhealing.settings.admission_control." "get_admission_control_settings",
                return_value=mock_settings,
            ),
        ):
            middleware = AdmissionControlMiddleware(get_response)
            middleware._enabled = True
            middleware._registry = mock_registry
            middleware._traffic_gate = gate
            middleware._settings = mock_settings

        request = MagicMock()
        request.path = "/api/critical"
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.user.is_authenticated = False

        middleware(request)

        gate.should_allow.assert_called_once_with(
            priority=TIER_PRIORITY_MAP["critical"],
            bulkhead_name="tier:critical",
            bulkhead_timeout=0.05,
            metadata={"tier_id": "critical"},
        )
