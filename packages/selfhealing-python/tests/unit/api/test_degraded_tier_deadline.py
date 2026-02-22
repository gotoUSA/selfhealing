"""
단위 테스트 — Degraded Tier Forced Deadline.

작업 D: backpressure HIGH 이상 + non_essential tier → 1초 deadline 강제 주입.
Heavy Query가 critical/standard tier 자원을 점유하는 것을 방지하기 위한 안전장치.

테스트 항목:
- HIGH + non_essential → 1000ms deadline 설정
- CRITICAL + non_essential → 1000ms deadline 설정
- MEDIUM + non_essential → deadline 미설정
- 기존 더 짧은 deadline → 보존 (set_deadline 호출 안 함)
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import MagicMock, patch

from selfhealing.api.django.admission_control import (
    AdmissionControlMiddleware,
)
from selfhealing.settings.backpressure import BackpressureLevel


class TestDegradedTierDeadlineBehavior:
    """backpressure HIGH 이상 + non_essential tier에 강제 deadline 주입 동작 검증."""

    def _make_middleware(self, bp_level: BackpressureLevel):
        """TrafficGate의 get_level()이 주어진 bp_level을 반환하는 미들웨어 생성."""
        mock_response = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        with (
            patch("selfhealing.api.django.admission_control." "AdmissionControlMiddleware._init_dependencies"),
            patch(
                "selfhealing.settings.admission_control." "get_admission_control_settings",
                return_value=MagicMock(enabled=True),
            ),
        ):
            middleware = AdmissionControlMiddleware(get_response)
            middleware._enabled = True

        # mock TierRegistry
        mock_registry = MagicMock()
        tier_result = MagicMock()
        tier_result.tier_id = "non_essential"
        mock_registry.resolve_tier_with_fallback.return_value = tier_result
        mock_registry.get_tier.return_value = MagicMock(priority=100)
        middleware._registry = mock_registry

        # mock TrafficGate — bp_level 반환
        mock_gate = MagicMock()
        mock_gate.get_level.return_value = bp_level
        mock_gate.should_allow.return_value = MagicMock(
            allowed=True,
            gate="rate",
            reason="ok",
            bulkhead_acquired=False,
            bulkhead_name=None,
        )
        middleware._traffic_gate = mock_gate

        # mock settings
        mock_settings = MagicMock()
        mock_settings.get_tier_bulkhead_timeout.return_value = None
        mock_settings.get_tier_max_concurrent.return_value = 20
        mock_settings.enabled = True
        middleware._settings = mock_settings

        return middleware

    def _make_request(self):
        """Django-like request mock 생성."""
        request = MagicMock()
        request.method = "GET"
        request.path = "/api/dashboard/metrics"
        request.META = {}
        return request

    def test_forced_deadline_on_high_level(self):
        """HIGH + non_essential → 1000ms deadline이 설정된다."""
        middleware = self._make_middleware(BackpressureLevel.HIGH)
        request = self._make_request()

        with (
            patch("selfhealing.scaling.deadline_context.set_deadline") as mock_set,
            patch(
                "selfhealing.scaling.deadline_context.get_remaining_ms",
                return_value=None,
            ),
        ):
            middleware._process_request(request)
            mock_set.assert_called_once_with(1000)

    def test_forced_deadline_on_critical_level(self):
        """CRITICAL + non_essential → 1000ms deadline이 설정된다."""
        middleware = self._make_middleware(BackpressureLevel.CRITICAL)
        request = self._make_request()

        with (
            patch("selfhealing.scaling.deadline_context.set_deadline") as mock_set,
            patch(
                "selfhealing.scaling.deadline_context.get_remaining_ms",
                return_value=None,
            ),
        ):
            middleware._process_request(request)
            mock_set.assert_called_once_with(1000)

    def test_no_deadline_below_high(self):
        """MEDIUM + non_essential → deadline 미설정."""
        middleware = self._make_middleware(BackpressureLevel.MEDIUM)
        request = self._make_request()

        with (
            patch("selfhealing.scaling.deadline_context.set_deadline") as mock_set,
            patch(
                "selfhealing.scaling.deadline_context.get_remaining_ms",
                return_value=None,
            ),
        ):
            middleware._process_request(request)
            mock_set.assert_not_called()

    def test_existing_shorter_deadline_kept(self):
        """기존 500ms deadline < 1000ms → set_deadline 호출 안 함."""
        middleware = self._make_middleware(BackpressureLevel.HIGH)
        request = self._make_request()

        with (
            patch("selfhealing.scaling.deadline_context.set_deadline") as mock_set,
            patch(
                "selfhealing.scaling.deadline_context.get_remaining_ms",
                return_value=500.0,
            ),
        ):
            middleware._process_request(request)
            mock_set.assert_not_called()
