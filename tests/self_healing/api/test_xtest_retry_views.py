"""
Unit Tests for X-Test-Mode Retry Views.

X-Test-Mode 환경에서 Retry Handler 동작을 테스트하기 위한 API 테스트.

Tests:
- BackoffPreviewView: Backoff 시퀀스 미리보기
- RetrySimulateView: 재시도 시나리오 시뮬레이션
- RetryRateLimitStatusView: Rate Limit 인식 상태
- XTestRetryConfigView: Retry 설정 조회
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.xtest.retry import (
    BackoffPreviewView,
    RetrySimulateView,
    RetryRateLimitStatusView,
    XTestRetryConfigView,
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
        "selfhealing.api.django.views.xtest.retry.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture
def mock_backoff_calculator():
    """Mock BackoffCalculator for testing."""
    mock_config = MagicMock()
    mock_config.base = 4
    mock_config.max_delay = 180
    mock_config.jitter_percent = 25
    mock_config.min_delay = 1

    mock_calculator = MagicMock()
    mock_calculator.config = mock_config
    mock_calculator.calculate.side_effect = lambda attempt, with_jitter=True: min(4**attempt, 180)
    mock_calculator.get_delays_sequence.return_value = [4, 16, 64, 180]

    return mock_calculator


@pytest.fixture
def mock_retry_config():
    """Mock RetryConfig for testing."""
    mock_config = MagicMock()
    mock_config.max_attempts = 3
    mock_config.backoff_base = 4
    mock_config.backoff_max = 180
    mock_config.jitter_percent = 25
    mock_config.enable_dlq = True
    mock_config.rate_limit_aware = True
    mock_config.rate_limit_key = None
    mock_config.domain = "default"
    mock_config.retryable_exceptions = (Exception,)
    mock_config.non_retryable_exceptions = ()

    with patch(
        "selfhealing.services.retry_handler.RetryConfig.from_settings",
        return_value=mock_config,
    ):
        yield mock_config


@pytest.fixture
def mock_rate_limit_coordinator():
    """Mock RateLimitCoordinator for testing."""
    mock_state = MagicMock()
    mock_state.consecutive_429s = 0
    mock_state.is_in_cooldown = False
    mock_state.cooldown_until = None
    mock_state.remaining_cooldown = 0

    mock_coordinator = MagicMock()
    mock_coordinator.storage_type = "memory"
    mock_coordinator.get_state.return_value = mock_state

    mock_config = MagicMock()
    mock_config.base_delay = 1.0
    mock_config.max_delay = 60.0
    mock_config.jitter_percent = 30.0
    mock_config.default_retry_after = 5.0
    mock_config.backoff_multiplier = 2.0

    with patch(
        "selfhealing.services.rate_limit_coordinator.get_rate_limit_coordinator",
        return_value=mock_coordinator,
    ):
        with patch(
            "selfhealing.services.rate_limit_coordinator.RateLimitCoordinatorConfig.from_settings",
            return_value=mock_config,
        ):
            yield mock_coordinator, mock_config


@pytest.fixture
def mock_system_snapshot():
    """Mock system snapshot collection."""
    with patch(
        "selfhealing.api.django.views.xtest.retry.collect_system_snapshot",
        return_value={
            "timestamp": "2026-01-26T12:00:00Z",
            "cpu_percent": 25.0,
            "memory_percent": 50.0,
        },
    ):
        yield


# =============================================================================
# BackoffPreviewView Tests
# =============================================================================


class TestBackoffPreviewView:
    """BackoffPreviewView 테스트."""

    def test_backoff_preview_default_config(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """기본 설정으로 backoff 시퀀스 미리보기."""
        view = BackoffPreviewView.as_view()
        request = request_factory.get("/xtest/retry/backoff-preview/", **chaos_headers)
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "delays" in response.data
        assert "delays_with_jitter" in response.data
        assert "total_max_delay" in response.data
        assert "config" in response.data

    def test_backoff_preview_custom_params(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """커스텀 파라미터로 backoff 시퀀스 미리보기."""
        view = BackoffPreviewView.as_view()
        request = request_factory.get(
            "/xtest/retry/backoff-preview/",
            {"max_attempts": 4, "backoff_base": 2, "backoff_max": 60, "jitter_percent": 10},
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["config"]["max_attempts"] == 4
        assert response.data["config"]["backoff_base"] == 2
        assert response.data["config"]["backoff_max"] == 60
        assert response.data["config"]["jitter_percent"] == 10
        assert len(response.data["delays"]) == 4

    def test_backoff_preview_invalid_params(self, request_factory, chaos_headers, mock_chaos_allowed):
        """잘못된 파라미터 오류 처리."""
        view = BackoffPreviewView.as_view()
        request = request_factory.get(
            "/xtest/retry/backoff-preview/",
            {"max_attempts": "invalid"},
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "invalid_parameters"

    def test_backoff_preview_no_chaos_header(self, request_factory):
        """X-Test-Mode 헤더 없이 요청 시 거부."""
        view = BackoffPreviewView.as_view()
        request = request_factory.get("/xtest/retry/backoff-preview/")
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "chaos_mode_disabled"

    def test_backoff_preview_jitter_range(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """지터 범위 계산 확인."""
        view = BackoffPreviewView.as_view()
        request = request_factory.get(
            "/xtest/retry/backoff-preview/",
            {"max_attempts": 3, "jitter_percent": 25},
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        delays_with_jitter = response.data["delays_with_jitter"]

        # 각 attempt에 대해 min/max 범위 확인
        for item in delays_with_jitter:
            base = item["base"]
            min_val = item["min"]
            max_val = item["max"]
            assert min_val <= base <= max_val


# =============================================================================
# RetrySimulateView Tests
# =============================================================================


class TestRetrySimulateView:
    """RetrySimulateView 테스트."""

    def test_simulate_success_before_max_attempts(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """최대 재시도 전에 성공하는 시나리오."""
        view = RetrySimulateView.as_view()
        request = request_factory.post(
            "/xtest/retry/simulate/",
            {"failure_count": 2, "max_attempts": 5},
            format="json",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert response.data["total_attempts"] == 3  # 2 failures + 1 success
        assert response.data["final_action"] == "success"
        assert response.data["dlq_routed"] is False

    def test_simulate_dlq_routing(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """최대 재시도 초과 후 DLQ 라우팅 시나리오."""
        view = RetrySimulateView.as_view()
        request = request_factory.post(
            "/xtest/retry/simulate/",
            {"failure_count": 5, "max_attempts": 3},
            format="json",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_attempts"] == 3
        assert response.data["final_action"] == "dlq"
        assert response.data["dlq_routed"] is True

    def test_simulate_missing_failure_count(self, request_factory, chaos_headers, mock_chaos_allowed):
        """failure_count 누락 시 오류."""
        view = RetrySimulateView.as_view()
        request = request_factory.post(
            "/xtest/retry/simulate/",
            {},
            format="json",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "missing_required_field"

    def test_simulate_invalid_failure_count(self, request_factory, chaos_headers, mock_chaos_allowed):
        """잘못된 failure_count 오류."""
        view = RetrySimulateView.as_view()
        request = request_factory.post(
            "/xtest/retry/simulate/",
            {"failure_count": -1},
            format="json",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "invalid_failure_count"

    def test_simulate_retry_sequence(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """재시도 시퀀스 구조 확인."""
        view = RetrySimulateView.as_view()
        request = request_factory.post(
            "/xtest/retry/simulate/",
            {"failure_count": 2, "max_attempts": 3},
            format="json",
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        retry_sequence = response.data["retry_sequence"]

        # 첫 번째 시도: 실패, delay 있음
        assert retry_sequence[0]["attempt"] == 1
        assert retry_sequence[0]["result"] == "FAILURE"
        assert retry_sequence[0]["delay_before_next"] is not None

        # 두 번째 시도: 실패, delay 있음
        assert retry_sequence[1]["attempt"] == 2
        assert retry_sequence[1]["result"] == "FAILURE"
        assert retry_sequence[1]["delay_before_next"] is not None

        # 세 번째 시도: 성공, delay 없음
        assert retry_sequence[2]["attempt"] == 3
        assert retry_sequence[2]["result"] == "SUCCESS"
        assert retry_sequence[2]["delay_before_next"] is None

    def test_simulate_with_dlq_creation(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """simulate_dlq=True로 DLQ 항목 생성 시뮬레이션."""
        with patch(
            "selfhealing.api.django.views.xtest.retry.RetrySimulateView._simulate_dlq_entry",
            return_value=999,
        ):
            view = RetrySimulateView.as_view()
            request = request_factory.post(
                "/xtest/retry/simulate/",
                {"failure_count": 5, "max_attempts": 3, "simulate_dlq": True},
                format="json",
                **chaos_headers,
            )
            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["dlq_routed"] is True
            assert response.data["dlq_id"] == 999


# =============================================================================
# RetryRateLimitStatusView Tests
# =============================================================================


class TestRetryRateLimitStatusView:
    """RetryRateLimitStatusView 테스트."""

    def test_rate_limit_status_normal(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_rate_limit_coordinator, mock_system_snapshot
    ):
        """정상 상태에서 rate limit 상태 조회."""
        view = RetryRateLimitStatusView.as_view()
        request = request_factory.get(
            "/xtest/retry/rate-limit-status/",
            {"domain": "payment"},
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert response.data["rate_limit_aware"] is True
        assert response.data["domain"] == "payment"
        assert response.data["throttled"] is False
        assert "state" in response.data

    def test_rate_limit_status_throttled(self, request_factory, chaos_headers, mock_chaos_allowed, mock_system_snapshot):
        """스로틀링 상태에서 rate limit 상태 조회."""
        import time

        mock_state = MagicMock()
        mock_state.consecutive_429s = 3
        mock_state.is_in_cooldown = True
        mock_state.cooldown_until = time.time() + 30
        mock_state.remaining_cooldown = 30.0

        mock_coordinator = MagicMock()
        mock_coordinator.storage_type = "redis"
        mock_coordinator.get_state.return_value = mock_state

        mock_config = MagicMock()
        mock_config.base_delay = 1.0
        mock_config.max_delay = 60.0
        mock_config.jitter_percent = 30.0
        mock_config.default_retry_after = 5.0
        mock_config.backoff_multiplier = 2.0

        with patch(
            "selfhealing.services.rate_limit_coordinator.get_rate_limit_coordinator",
            return_value=mock_coordinator,
        ):
            with patch(
                "selfhealing.services.rate_limit_coordinator.RateLimitCoordinatorConfig.from_settings",
                return_value=mock_config,
            ):
                view = RetryRateLimitStatusView.as_view()
                request = request_factory.get(
                    "/xtest/retry/rate-limit-status/",
                    {"domain": "external"},
                    **chaos_headers,
                )
                response = view(request)

                assert response.status_code == status.HTTP_200_OK
                assert response.data["throttled"] is True
                assert response.data["state"]["consecutive_429s"] == 3
                assert response.data["state"]["is_in_cooldown"] is True

    def test_rate_limit_status_error_handling(self, request_factory, chaos_headers, mock_chaos_allowed, mock_system_snapshot):
        """Rate limit coordinator 오류 시 fail-safe 처리."""
        with patch(
            "selfhealing.services.rate_limit_coordinator.get_rate_limit_coordinator",
            side_effect=Exception("Coordinator unavailable"),
        ):
            view = RetryRateLimitStatusView.as_view()
            request = request_factory.get(
                "/xtest/retry/rate-limit-status/",
                **chaos_headers,
            )
            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["status"] == "error"
            assert response.data["rate_limit_aware"] is False
            assert response.data["throttled"] is False


# =============================================================================
# RetryConfigView Tests
# =============================================================================


class TestXTestRetryConfigView:
    """XTestRetryConfigView 테스트."""

    def test_config_view_default(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """기본 도메인 설정 조회."""
        view = XTestRetryConfigView.as_view()
        request = request_factory.get("/xtest/retry/config/", **chaos_headers)
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert response.data["domain"] == "default"
        assert "config" in response.data
        assert "max_attempts" in response.data["config"]
        assert "backoff_base" in response.data["config"]
        assert "enable_dlq" in response.data["config"]

    def test_config_view_specific_domain(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """특정 도메인 설정 조회."""
        view = XTestRetryConfigView.as_view()
        request = request_factory.get(
            "/xtest/retry/config/",
            {"domain": "payment"},
            **chaos_headers,
        )
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["domain"] == "payment"

    def test_config_view_source_detection(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """설정 소스 감지 확인."""
        view = XTestRetryConfigView.as_view()
        request = request_factory.get("/xtest/retry/config/", **chaos_headers)
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["source"] in ["runtime", "settings", "default"]

    def test_config_view_includes_rate_limit_settings(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """Rate limit 관련 설정 포함 확인."""
        view = XTestRetryConfigView.as_view()
        request = request_factory.get("/xtest/retry/config/", **chaos_headers)
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        config = response.data["config"]
        assert "rate_limit_aware" in config
        assert "rate_limit_key" in config


# =============================================================================
# Integration Tests
# =============================================================================


class TestRetryViewsIntegration:
    """Retry Views 통합 테스트."""

    def test_backoff_matches_simulate(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """backoff-preview와 simulate의 delay 값 일치 확인."""
        # Backoff preview
        backoff_view = BackoffPreviewView.as_view()
        backoff_request = request_factory.get(
            "/xtest/retry/backoff-preview/",
            {"max_attempts": 3},
            **chaos_headers,
        )
        backoff_response = backoff_view(backoff_request)

        # Simulate
        simulate_view = RetrySimulateView.as_view()
        simulate_request = request_factory.post(
            "/xtest/retry/simulate/",
            {"failure_count": 3, "max_attempts": 3},
            format="json",
            **chaos_headers,
        )
        simulate_response = simulate_view(simulate_request)

        assert backoff_response.status_code == status.HTTP_200_OK
        assert simulate_response.status_code == status.HTTP_200_OK

        # Preview의 delays와 simulate의 delay_before_next 비교
        preview_delays = backoff_response.data["delays"]
        simulate_sequence = simulate_response.data["retry_sequence"]

        # 처음 두 개의 delay 비교 (세 번째는 마지막이라 None)
        for i in range(len(simulate_sequence) - 1):
            assert simulate_sequence[i]["delay_before_next"] == preview_delays[i]

    def test_config_reflects_in_simulate(
        self, request_factory, chaos_headers, mock_chaos_allowed, mock_retry_config, mock_system_snapshot
    ):
        """config에서 조회한 설정이 simulate에 반영되는지 확인."""
        # Config view
        config_view = XTestRetryConfigView.as_view()
        config_request = request_factory.get("/xtest/retry/config/", **chaos_headers)
        config_response = config_view(config_request)

        # Simulate view
        simulate_view = RetrySimulateView.as_view()
        simulate_request = request_factory.post(
            "/xtest/retry/simulate/",
            {"failure_count": 10},  # max_attempts보다 크게
            format="json",
            **chaos_headers,
        )
        simulate_response = simulate_view(simulate_request)

        assert config_response.status_code == status.HTTP_200_OK
        assert simulate_response.status_code == status.HTTP_200_OK

        # config의 max_attempts와 simulate의 total_attempts 비교
        config_max = config_response.data["config"]["max_attempts"]
        simulate_total = simulate_response.data["total_attempts"]
        assert simulate_total == config_max
