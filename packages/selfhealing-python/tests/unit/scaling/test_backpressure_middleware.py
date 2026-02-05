"""
BackpressureMiddleware 단위 테스트.

테스트 항목:
- 정상 요청 처리
- 과부하 시 503 응답
- 헤더 추가 (X-SelfHealing-*)
- Backpressure 비활성화 시 동작
- Graceful Degradation 연동

Note: Django 설정 없이 순수 단위 테스트로 진행.
"""

import os

import pytest

# Django 설정 (테스트 전 필수)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import MagicMock, patch

from selfhealing.scaling.config import BackpressureLevel, BackpressureSettings


class TestBackpressureMiddleware:
    """BackpressureMiddleware 테스트."""

    @pytest.fixture
    def mock_settings(self):
        """테스트용 설정."""
        return BackpressureSettings(
            backpressure_enabled=True,
            reject_message="Test overload message",
            reject_retry_after_seconds=10,
        )

    @pytest.fixture
    def mock_controller(self):
        """Mock RateController."""
        controller = MagicMock()
        controller.should_process.return_value = True
        controller.get_state.return_value = MagicMock(level=BackpressureLevel.NONE)
        return controller

    @pytest.fixture
    def mock_degradation(self):
        """Mock GracefulDegradation."""
        degradation = MagicMock()
        degradation.get_disabled_features.return_value = []
        return degradation

    def test_normal_request_passes_through(self, mock_settings, mock_controller, mock_degradation):
        """정상 요청이 통과하는지 확인."""
        from selfhealing.api.django.middleware.backpressure import BackpressureMiddleware

        mock_response = MagicMock()
        mock_response.__setitem__ = MagicMock()
        get_response = MagicMock(return_value=mock_response)
        request = MagicMock()

        with (
            patch(
                "selfhealing.api.django.middleware.backpressure.get_backpressure_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_rate_controller",
                return_value=mock_controller,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_graceful_degradation",
                return_value=mock_degradation,
            ),
        ):
            middleware = BackpressureMiddleware(get_response)
            response = middleware(request)

        get_response.assert_called_once_with(request)
        assert response is mock_response

    def test_overload_returns_503(self, mock_settings, mock_controller, mock_degradation):
        """과부하 시 503 응답 확인."""
        from selfhealing.api.django.middleware.backpressure import BackpressureMiddleware

        # 과부하 상태
        mock_controller.should_process.return_value = False
        mock_controller.get_state.return_value = MagicMock(level=BackpressureLevel.HIGH)

        get_response = MagicMock()
        request = MagicMock()

        with (
            patch(
                "selfhealing.api.django.middleware.backpressure.get_backpressure_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_rate_controller",
                return_value=mock_controller,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_graceful_degradation",
                return_value=mock_degradation,
            ),
        ):
            middleware = BackpressureMiddleware(get_response)
            response = middleware(request)

        # get_response가 호출되지 않음
        get_response.assert_not_called()

        # 503 응답
        assert response.status_code == 503
        assert response.content.decode() == "Test overload message"

    def test_503_includes_retry_after_header(self, mock_settings, mock_controller, mock_degradation):
        """503 응답에 Retry-After 헤더 포함 확인."""
        from selfhealing.api.django.middleware.backpressure import BackpressureMiddleware

        mock_controller.should_process.return_value = False
        mock_controller.get_state.return_value = MagicMock(level=BackpressureLevel.CRITICAL)

        with (
            patch(
                "selfhealing.api.django.middleware.backpressure.get_backpressure_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_rate_controller",
                return_value=mock_controller,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_graceful_degradation",
                return_value=mock_degradation,
            ),
        ):
            middleware = BackpressureMiddleware(MagicMock())
            response = middleware(MagicMock())

        assert response["Retry-After"] == "10"
        assert response["X-SelfHealing-Backpressure-Level"] == "critical"

    def test_disabled_backpressure_passes_all_requests(self, mock_controller, mock_degradation):
        """Backpressure 비활성화 시 모든 요청 통과 확인."""
        from selfhealing.api.django.middleware.backpressure import BackpressureMiddleware

        disabled_settings = BackpressureSettings(backpressure_enabled=False)

        mock_response = MagicMock()
        get_response = MagicMock(return_value=mock_response)
        request = MagicMock()

        with (
            patch(
                "selfhealing.api.django.middleware.backpressure.get_backpressure_settings",
                return_value=disabled_settings,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_rate_controller",
                return_value=mock_controller,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_graceful_degradation",
                return_value=mock_degradation,
            ),
        ):
            middleware = BackpressureMiddleware(get_response)
            response = middleware(request)

        # should_process 호출되지 않음
        mock_controller.should_process.assert_not_called()
        assert response is mock_response

    def test_degraded_features_header_added(self, mock_settings, mock_controller, mock_degradation):
        """비활성화된 기능 헤더 추가 확인."""
        from selfhealing.api.django.middleware.backpressure import BackpressureMiddleware

        mock_degradation.get_disabled_features.return_value = [
            "feature1",
            "feature2",
        ]

        mock_response = MagicMock()
        mock_response.__setitem__ = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        with (
            patch(
                "selfhealing.api.django.middleware.backpressure.get_backpressure_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_rate_controller",
                return_value=mock_controller,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_graceful_degradation",
                return_value=mock_degradation,
            ),
        ):
            middleware = BackpressureMiddleware(get_response)
            middleware(MagicMock())

        # 헤더 설정 확인
        calls = mock_response.__setitem__.call_args_list
        header_calls = {call[0][0]: call[0][1] for call in calls}

        assert "X-SelfHealing-Degraded-Features" in header_calls
        assert header_calls["X-SelfHealing-Degraded-Features"] == "feature1,feature2"

    def test_backpressure_level_header_added_on_success(self, mock_settings, mock_controller, mock_degradation):
        """정상 응답에도 Backpressure 레벨 헤더 추가 확인."""
        from selfhealing.api.django.middleware.backpressure import BackpressureMiddleware

        mock_controller.get_state.return_value = MagicMock(level=BackpressureLevel.LOW)

        mock_response = MagicMock()
        mock_response.__setitem__ = MagicMock()
        get_response = MagicMock(return_value=mock_response)

        with (
            patch(
                "selfhealing.api.django.middleware.backpressure.get_backpressure_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_rate_controller",
                return_value=mock_controller,
            ),
            patch(
                "selfhealing.api.django.middleware.backpressure.get_graceful_degradation",
                return_value=mock_degradation,
            ),
        ):
            middleware = BackpressureMiddleware(get_response)
            middleware(MagicMock())

        calls = mock_response.__setitem__.call_args_list
        header_calls = {call[0][0]: call[0][1] for call in calls}

        assert "X-SelfHealing-Backpressure-Level" in header_calls
        assert header_calls["X-SelfHealing-Backpressure-Level"] == "low"
