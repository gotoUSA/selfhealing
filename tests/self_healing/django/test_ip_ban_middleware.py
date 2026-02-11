"""IPBanMiddleware 단위 테스트."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestIPBanMiddleware:
    """IPBanMiddleware 단위 테스트."""

    def _make_middleware(self, ban_info=None, cache_error=False):
        """테스트용 미들웨어 팩토리.

        Mock 주입 패턴: test_security_violation_service.py와 동일.
        """
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        mock_response = Mock()
        mock_response.status_code = 200
        middleware = IPBanMiddleware(get_response=lambda r: mock_response)

        mock_cache = MagicMock()
        if cache_error:
            mock_cache.get.side_effect = Exception("Redis down")
        else:
            mock_cache.get.return_value = ban_info

        middleware._cache = mock_cache
        middleware._initialized = True
        return middleware, mock_response

    def _make_request(self, path="/api/test/", ip="192.168.1.100"):
        """테스트용 Django HttpRequest Mock 생성."""
        request = MagicMock()
        request.path = path
        request.META = {
            "HTTP_X_FORWARDED_FOR": ip,
            "REMOTE_ADDR": ip,
        }
        return request

    # =================================================================
    # Ban된 IP 차단 테스트
    # =================================================================

    def test_banned_ip_returns_403(self):
        """ban된 IP의 요청이 403으로 거부되는지 확인."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = self._make_request()

        response = middleware(request)

        assert response.status_code == 403
        body = json.loads(response.content)
        assert body["code"] == "IP_BANNED"
        assert body["error"] == "Access denied"

    def test_permanent_ban_returns_403(self):
        """영구 ban된 IP의 요청이 403으로 거부되는지 확인."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request()

        response = middleware(request)

        assert response.status_code == 403

    # =================================================================
    # 정상 통과 테스트
    # =================================================================

    def test_non_banned_ip_passes_through(self):
        """ban되지 않은 IP의 요청이 정상 통과하는지 확인."""
        middleware, mock_response = self._make_middleware(ban_info=None)
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    def test_banned_false_passes_through(self):
        """banned=False인 경우 요청이 정상 통과하는지 확인."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": False, "type": "temporary"})
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    def test_non_dict_cache_value_passes_through(self):
        """cache 값이 dict가 아닌 경우 요청이 정상 통과하는지 확인."""
        middleware, mock_response = self._make_middleware(ban_info="invalid_string")
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    # =================================================================
    # 헬스체크 경로 면제 테스트
    # =================================================================

    def test_health_check_exempt(self):
        """헬스체크 경로가 ban에서 면제되는지 확인."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request(path="/health/l3/")

        response = middleware(request)

        assert response is mock_response

    def test_readiness_check_exempt(self):
        """/readiness/ 경로가 ban에서 면제되는지 확인."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request(path="/readiness/")

        response = middleware(request)

        assert response is mock_response

    def test_liveness_check_exempt(self):
        """/liveness/ 경로가 ban에서 면제되는지 확인."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request(path="/liveness/")

        response = middleware(request)

        assert response is mock_response

    # =================================================================
    # Fail-Open 테스트
    # =================================================================

    def test_redis_failure_fail_open(self):
        """Redis 장애 시 요청이 허용되는지 확인 (Fail-Open)."""
        middleware, mock_response = self._make_middleware(cache_error=True)
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    def test_cache_none_fail_open(self):
        """cache가 None인 경우 요청이 허용되는지 확인 (Fail-Open)."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        mock_response = Mock()
        middleware = IPBanMiddleware(get_response=lambda r: mock_response)
        middleware._cache = None
        middleware._initialized = True

        request = self._make_request()
        response = middleware(request)

        assert response is mock_response

    # =================================================================
    # 보안 테스트
    # =================================================================

    def test_response_does_not_expose_ban_type(self):
        """403 응답에 ban_type이 노출되지 않는지 확인 (보안)."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request()

        response = middleware(request)

        body = json.loads(response.content)
        assert "type" not in body
        assert "ban_type" not in body
        assert "permanent" not in json.dumps(body)
        assert "temporary" not in json.dumps(body)

    def test_ban_type_logged_in_warning(self):
        """ban_type이 로그에는 기록되는지 확인."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = self._make_request(path="/api/test/")

        with patch("selfhealing.api.django.middleware.ip_ban.logger") as mock_logger:
            middleware(request)

        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        assert "type=temporary" in log_message
        assert "Blocked banned IP" in log_message

    # =================================================================
    # IP 추출 테스트
    # =================================================================

    def test_extract_client_ip_called(self):
        """extract_client_ip가 올바르게 호출되는지 확인."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = self._make_request(ip="10.0.0.1")

        response = middleware(request)

        # cache.get이 올바른 키로 호출되었는지 확인
        middleware._cache.get.assert_called_once_with("security:banned_ip:10.0.0.1")

    def test_x_forwarded_for_first_ip_used(self):
        """X-Forwarded-For 헤더의 첫 번째 IP가 사용되는지 확인."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = MagicMock()
        request.path = "/api/test/"
        request.META = {
            "HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8",
            "REMOTE_ADDR": "127.0.0.1",
        }

        middleware(request)

        middleware._cache.get.assert_called_once_with("security:banned_ip:1.2.3.4")

    # =================================================================
    # Lazy Initialization 테스트
    # =================================================================

    def test_lazy_init_only_once(self):
        """lazy init이 한 번만 실행되는지 확인."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._initialized = True

        # _initialized=True이면 _lazy_init 내부 로직이 실행되지 않음
        with patch("selfhealing.api.django.middleware.ip_ban.IPBanMiddleware._get_cache") as mock_get_cache:
            middleware._lazy_init()
            mock_get_cache.assert_not_called()

    def test_cache_retry_on_initial_failure(self):
        """초기 캐시 로드 실패 시 _get_cache()에서 재시도하는지 확인."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._cache = None
        middleware._initialized = True

        mock_cache = MagicMock()
        with patch("selfhealing.factory.ProviderRegistry") as mock_registry:
            mock_registry.get_cache.return_value = mock_cache
            result = middleware._get_cache()

        assert result is mock_cache

    # =================================================================
    # Redis 키 프리픽스 테스트
    # =================================================================

    def test_key_prefix_matches_security_config_default(self):
        """기본 키 프리픽스가 SecurityConfig의 banned_ip_cache_prefix와 일치하는지 확인."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware
        from selfhealing.services.security.models import SecurityConfig

        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._config = None
        middleware._initialized = True

        default_config = SecurityConfig()
        assert middleware._get_banned_ip_prefix() == default_config.banned_ip_cache_prefix

    def test_key_prefix_from_config(self):
        """config가 있을 때 해당 프리픽스를 사용하는지 확인."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        mock_config = MagicMock()
        mock_config.banned_ip_cache_prefix = "custom:prefix:"
        middleware._config = mock_config
        middleware._initialized = True

        assert middleware._get_banned_ip_prefix() == "custom:prefix:"

    # =================================================================
    # EXEMPT_PATH_PREFIXES 검증
    # =================================================================

    def test_non_exempt_path_checks_ban(self):
        """면제 경로가 아닌 경우 ban 확인이 수행되는지 확인."""
        middleware, _ = self._make_middleware(ban_info=None)
        request = self._make_request(path="/api/orders/")

        middleware(request)

        middleware._cache.get.assert_called_once()
