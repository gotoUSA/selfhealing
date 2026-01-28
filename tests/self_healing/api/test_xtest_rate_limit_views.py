"""
Unit Tests for X-Test-Mode Rate Limit Views.

X-Test-Mode 환경에서 Rate Limiter 동작을 테스트하기 위한 API 테스트.

Tests:
- RateLimitStatusView: 전체 Rate Limit 상태 조회
- RateLimitClientView: 클라이언트별 상태 조회
- RateLimitHistoryView: Rate Limit 히스토리 조회
- RateLimitConfigXTestView: 설정 조회
- RateLimitResetView: 카운터 초기화 (테스트용)
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.xtest.rate_limit import (
    RateLimitStatusView,
    RateLimitClientView,
    RateLimitHistoryView,
    RateLimitConfigXTestView,
    RateLimitResetView,
)
from selfhealing.api.django.rate_limit import RedisHealthState


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
        "selfhealing.api.django.views.xtest.rate_limit.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture
def mock_system_snapshot():
    """Mock system snapshot collection."""
    with patch(
        "selfhealing.api.django.views.xtest.rate_limit.collect_system_snapshot",
        return_value={
            "timestamp": "2026-01-26T12:00:00Z",
            "cpu_percent": 25.0,
            "memory_percent": 50.0,
        },
    ):
        yield


def create_mock_health_checker(state=RedisHealthState.HEALTHY):
    """Create a mock health checker with configurable state."""
    mock_checker = MagicMock()
    mock_checker.is_healthy = state == RedisHealthState.HEALTHY
    mock_checker.is_degraded = state != RedisHealthState.HEALTHY
    mock_checker.ping_interval = 5
    mock_checker.failure_threshold = 3
    mock_checker.recovery_jitter_max = 10
    mock_checker.state = state
    return mock_checker


def create_mock_local_limiter():
    """Create a mock local memory rate limiter."""
    mock_limiter = MagicMock()
    mock_limiter.get_all_clients.return_value = ["client1", "client2"]
    mock_limiter.get_client_status.return_value = {
        "client_key": "test_client",
        "current_count": 5,
        "limit": 100,
        "remaining": 95,
        "reset_at": 1706270400,
        "blocked": False,
        "window_seconds": 60,
    }
    mock_limiter.reset_client.return_value = True
    return mock_limiter


# =============================================================================
# RateLimitStatusView Tests
# =============================================================================


class TestRateLimitStatusView:
    """RateLimitStatusView 테스트."""

    def test_status_returns_success(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
        mock_system_snapshot,
    ):
        """전체 상태 조회 성공."""
        mock_checker = create_mock_health_checker(RedisHealthState.HEALTHY)
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
                with patch(
                    "selfhealing.api.django.rate_limit.get_rate_limit_config",
                    return_value={
                        "control_api_rate_limit": 100,
                        "control_api_window_seconds": 60,
                        "emergency_rate_limit": 10,
                        "emergency_window_seconds": 60,
                    },
                ):
                    with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=10):
                        with patch(
                            "selfhealing.api.django.rate_limit.get_client_stats",
                            return_value={"client1": {"total": 5, "exceeded": 1}},
                        ):
                            view = RateLimitStatusView.as_view()
                            request = request_factory.get("/xtest/rate-limit/status/", **chaos_headers)
                            response = view(request)

                            assert response.status_code == status.HTTP_200_OK
                            assert response.data["status"] == "success"
                            assert response.data["mode"] == "normal"
                            assert response.data["redis_healthy"] is True
                            assert response.data["fallback_active"] is False
                            assert "current_config" in response.data
                            assert "global_stats" in response.data

    def test_status_with_client_key(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
        mock_system_snapshot,
    ):
        """특정 클라이언트 키로 조회."""
        mock_checker = create_mock_health_checker(RedisHealthState.HEALTHY)
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
                with patch(
                    "selfhealing.api.django.rate_limit.get_rate_limit_config",
                    return_value={
                        "control_api_rate_limit": 100,
                        "control_api_window_seconds": 60,
                        "emergency_rate_limit": 10,
                        "emergency_window_seconds": 60,
                    },
                ):
                    with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=10):
                        with patch(
                            "selfhealing.api.django.rate_limit.get_client_stats",
                            return_value={"client1": {"total": 5, "exceeded": 1}},
                        ):
                            view = RateLimitStatusView.as_view()
                            request = request_factory.get(
                                "/xtest/rate-limit/status/",
                                {"client_key": "test_client"},
                                **chaos_headers,
                            )
                            response = view(request)

                            assert response.status_code == status.HTTP_200_OK
                            assert "client_status" in response.data
                            assert response.data["client_status"]["client_key"] == "test_client"

    def test_status_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = RateLimitStatusView.as_view()
        request = request_factory.get("/xtest/rate-limit/status/")
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# RateLimitClientView Tests
# =============================================================================


class TestRateLimitClientView:
    """RateLimitClientView 테스트."""

    def test_client_status_success(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """클라이언트별 상태 조회 성공."""
        mock_checker = create_mock_health_checker(RedisHealthState.HEALTHY)
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
                view = RateLimitClientView.as_view()
                request = request_factory.get(
                    "/xtest/rate-limit/client/",
                    {"client_key": "192.168.1.1:user123"},
                    **chaos_headers,
                )
                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert response.data["status"] == "success"
                assert "current_count" in response.data
                assert "limit" in response.data

    def test_client_status_missing_key(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """client_key 없이 요청 시 오류."""
        view = RateLimitClientView.as_view()
        request = request_factory.get(
            "/xtest/rate-limit/client/",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_parameter"

    def test_client_status_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = RateLimitClientView.as_view()
        request = request_factory.get(
            "/xtest/rate-limit/client/",
            {"client_key": "test"},
        )
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# RateLimitHistoryView Tests
# =============================================================================


class TestRateLimitHistoryView:
    """RateLimitHistoryView 테스트."""

    def test_history_default_limit(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """기본 히스토리 조회."""
        mock_events = [
            {"timestamp": "2026-01-26T12:00:00Z", "client_key": "client1", "allowed": True},
        ]

        with patch("selfhealing.api.django.rate_limit.get_rate_limit_events", return_value=mock_events):
            with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=10):
                with patch(
                    "selfhealing.api.django.rate_limit.get_client_stats", return_value={"client1": {"total": 5, "exceeded": 1}}
                ):
                    view = RateLimitHistoryView.as_view()
                    request = request_factory.get("/xtest/rate-limit/history/", **chaos_headers)
                    response = view(request)

                    assert response.status_code == status.HTTP_200_OK
                    assert response.data["status"] == "success"
                    assert "recent_events" in response.data
                    assert "total_events" in response.data
                    assert "by_client" in response.data

    def test_history_custom_limit(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """커스텀 limit으로 조회."""
        mock_events = [{"timestamp": "2026-01-26T12:00:00Z", "client_key": "client1", "allowed": True}]

        with patch("selfhealing.api.django.rate_limit.get_rate_limit_events", return_value=mock_events):
            with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=100):
                with patch("selfhealing.api.django.rate_limit.get_client_stats", return_value={}):
                    view = RateLimitHistoryView.as_view()
                    request = request_factory.get(
                        "/xtest/rate-limit/history/",
                        {"limit": 50},
                        **chaos_headers,
                    )
                    response = view(request)

                    assert response.status_code == status.HTTP_200_OK

    def test_history_filter_by_client(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """클라이언트별 필터링."""
        mock_events = [{"timestamp": "2026-01-26T12:00:00Z", "client_key": "client1", "allowed": True}]

        with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_by_client", return_value=mock_events):
            with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=10):
                with patch(
                    "selfhealing.api.django.rate_limit.get_client_stats", return_value={"client1": {"total": 5, "exceeded": 1}}
                ):
                    view = RateLimitHistoryView.as_view()
                    request = request_factory.get(
                        "/xtest/rate-limit/history/",
                        {"client_key": "client1"},
                        **chaos_headers,
                    )
                    response = view(request)

                    assert response.status_code == status.HTTP_200_OK

    def test_history_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = RateLimitHistoryView.as_view()
        request = request_factory.get("/xtest/rate-limit/history/")
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# RateLimitConfigXTestView Tests
# =============================================================================


class TestRateLimitConfigXTestView:
    """RateLimitConfigXTestView 테스트."""

    def test_config_success(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """설정 조회 성공."""
        mock_checker = create_mock_health_checker(RedisHealthState.HEALTHY)

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch(
                "selfhealing.api.django.rate_limit.get_rate_limit_config",
                return_value={
                    "control_api_rate_limit": 100,
                    "control_api_window_seconds": 60,
                    "emergency_rate_limit": 10,
                    "emergency_window_seconds": 60,
                },
            ):
                view = RateLimitConfigXTestView.as_view()
                request = request_factory.get("/xtest/rate-limit/config/", **chaos_headers)
                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert response.data["status"] == "success"
                assert "source" in response.data
                assert "normal_config" in response.data
                assert "emergency_config" in response.data
                assert "redis_config" in response.data

    def test_config_has_path_prefix(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """설정에 path_prefix 포함."""
        mock_checker = create_mock_health_checker(RedisHealthState.HEALTHY)

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch(
                "selfhealing.api.django.rate_limit.get_rate_limit_config",
                return_value={
                    "control_api_rate_limit": 100,
                    "control_api_window_seconds": 60,
                    "emergency_rate_limit": 10,
                    "emergency_window_seconds": 60,
                },
            ):
                view = RateLimitConfigXTestView.as_view()
                request = request_factory.get("/xtest/rate-limit/config/", **chaos_headers)
                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert "path_prefix" in response.data

    def test_config_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = RateLimitConfigXTestView.as_view()
        request = request_factory.get("/xtest/rate-limit/config/")
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# RateLimitResetView Tests
# =============================================================================


class TestRateLimitResetView:
    """RateLimitResetView 테스트."""

    def test_reset_specific_client(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """특정 클라이언트 초기화."""
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
            with patch("selfhealing.api.django.rate_limit.reset_rate_limit_events", return_value=5):
                view = RateLimitResetView.as_view()
                request = request_factory.post(
                    "/xtest/rate-limit/reset/",
                    {"client_key": "test_client", "reset_events": True},
                    format="json",
                    **chaos_headers,
                )
                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert response.data["status"] == "success"
                assert response.data["reset_count"] == 1

    def test_reset_all(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """전체 초기화."""
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
            with patch("selfhealing.api.django.rate_limit.reset_rate_limit_state"):
                with patch("selfhealing.api.django.rate_limit.reset_rate_limit_events", return_value=10):
                    view = RateLimitResetView.as_view()
                    request = request_factory.post(
                        "/xtest/rate-limit/reset/",
                        {"reset_all": True, "reset_events": True},
                        format="json",
                        **chaos_headers,
                    )
                    response = view(request)

                    assert response.status_code == status.HTTP_200_OK
                    assert response.data["status"] == "success"

    def test_reset_missing_params(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
    ):
        """파라미터 누락 시 오류."""
        view = RateLimitResetView.as_view()
        request = request_factory.post(
            "/xtest/rate-limit/reset/",
            {},
            format="json",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_parameter"

    def test_reset_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = RateLimitResetView.as_view()
        request = request_factory.post(
            "/xtest/rate-limit/reset/",
            {"reset_all": True},
            format="json",
        )
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# Emergency Mode Tests
# =============================================================================


class TestEmergencyMode:
    """Emergency Mode 상태 테스트."""

    def test_status_shows_emergency_mode_when_redis_unhealthy(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
        mock_system_snapshot,
    ):
        """Redis 장애 시 emergency 모드 표시."""
        mock_checker = create_mock_health_checker(RedisHealthState.UNHEALTHY)
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
                with patch(
                    "selfhealing.api.django.rate_limit.get_rate_limit_config",
                    return_value={
                        "control_api_rate_limit": 100,
                        "control_api_window_seconds": 60,
                        "emergency_rate_limit": 10,
                        "emergency_window_seconds": 60,
                    },
                ):
                    with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=0):
                        with patch("selfhealing.api.django.rate_limit.get_client_stats", return_value={}):
                            view = RateLimitStatusView.as_view()
                            request = request_factory.get("/xtest/rate-limit/status/", **chaos_headers)
                            response = view(request)

                            assert response.status_code == status.HTTP_200_OK
                            assert response.data["mode"] == "emergency"
                            assert response.data["redis_healthy"] is False
                            assert response.data["fallback_active"] is True

    def test_status_shows_degraded_mode_during_recovery(
        self,
        request_factory,
        chaos_headers,
        mock_chaos_allowed,
        mock_system_snapshot,
    ):
        """복구 중 degraded 모드 표시."""
        mock_checker = create_mock_health_checker(RedisHealthState.RECOVERING)
        mock_limiter = create_mock_local_limiter()

        with patch("selfhealing.api.django.rate_limit.get_redis_health_checker", return_value=mock_checker):
            with patch("selfhealing.api.django.rate_limit.get_local_limiter", return_value=mock_limiter):
                with patch(
                    "selfhealing.api.django.rate_limit.get_rate_limit_config",
                    return_value={
                        "control_api_rate_limit": 100,
                        "control_api_window_seconds": 60,
                        "emergency_rate_limit": 10,
                        "emergency_window_seconds": 60,
                    },
                ):
                    with patch("selfhealing.api.django.rate_limit.get_rate_limit_events_count", return_value=0):
                        with patch("selfhealing.api.django.rate_limit.get_client_stats", return_value={}):
                            view = RateLimitStatusView.as_view()
                            request = request_factory.get("/xtest/rate-limit/status/", **chaos_headers)
                            response = view(request)

                            assert response.status_code == status.HTTP_200_OK
                            assert response.data["mode"] == "degraded"
