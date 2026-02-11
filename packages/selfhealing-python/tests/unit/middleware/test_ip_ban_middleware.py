"""
IPBanMiddleware 단위 테스트.

Django 설정 없이 importlib으로 ip_ban.py만 직접 로드하여 테스트합니다.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from unittest.mock import MagicMock, Mock, patch

import pytest

# ============================================================
# ip_ban.py 모듈 직접 로드 (Django 의존성 우회)
# ============================================================
# selfhealing.api.django.middleware.__init__.py가 다른 미들웨어를
# import하며 Django를 초기화하기 때문에, ip_ban.py만 단독 로드합니다.
# test_response_meta_region.py와 동일한 패턴입니다.


def _load_ip_ban_module():
    """Django 의존성 없이 ip_ban.py 모듈만 직접 로드."""
    ip_ban_path = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "src",
            "selfhealing",
            "api",
            "django",
            "middleware",
            "ip_ban.py",
        )
    )

    spec = spec_from_file_location("selfhealing.api.django.middleware.ip_ban", ip_ban_path)
    module = module_from_spec(spec)
    sys.modules["selfhealing.api.django.middleware.ip_ban"] = module
    spec.loader.exec_module(module)
    return module


try:
    _ip_ban_module = _load_ip_ban_module()
    IPBanMiddleware = _ip_ban_module.IPBanMiddleware
    _MODULE_LOADED = True
except Exception as e:
    _MODULE_LOADED = False
    _LOAD_ERROR = str(e)


@pytest.mark.skipif(
    not _MODULE_LOADED,
    reason=f"ip_ban module load failed: {_LOAD_ERROR if not _MODULE_LOADED else ''}",
)
class TestIPBanMiddlewareBehavior:
    """IPBanMiddleware 동작 검증 테스트."""

    def _make_middleware(self, ban_info=None, cache_error=False):
        """테스트용 미들웨어 팩토리.

        Mock 직접 주입 패턴 사용.
        """
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
        """테스트용 request Mock 생성."""
        request = MagicMock()
        request.path = path
        request.META = {
            "HTTP_X_FORWARDED_FOR": ip,
            "REMOTE_ADDR": ip,
        }
        return request

    # =================================================================
    # Ban된 IP 차단 동작 검증
    # =================================================================

    def test_temporary_banned_ip_returns_403(self):
        """임시 ban된 IP → 403 응답 + IP_BANNED 코드."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = self._make_request()

        response = middleware(request)

        assert response.status_code == 403
        body = json.loads(response.content)
        assert body["code"] == "IP_BANNED"
        assert body["error"] == "Access denied"

    def test_permanent_banned_ip_returns_403(self):
        """영구 ban된 IP → 403 응답."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request()

        response = middleware(request)

        assert response.status_code == 403

    # =================================================================
    # 정상 통과 동작 검증
    # =================================================================

    def test_non_banned_ip_passes_through(self):
        """ban되지 않은 IP → get_response 결과 반환."""
        middleware, mock_response = self._make_middleware(ban_info=None)
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    def test_banned_false_passes_through(self):
        """banned=False → get_response 결과 반환."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": False, "type": "temporary"})
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    def test_non_dict_cache_value_passes_through(self):
        """cache 값이 dict가 아닌 경우 → get_response 결과 반환."""
        middleware, mock_response = self._make_middleware(ban_info="invalid_string")
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    # =================================================================
    # 헬스체크 경로 면제 동작 검증
    # =================================================================

    def test_health_path_exempt_from_ban(self):
        """/health/ 경로 → ban 여부 무관하게 통과."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request(path="/health/l3/")

        response = middleware(request)

        assert response is mock_response
        middleware._cache.get.assert_not_called()

    def test_readiness_path_exempt_from_ban(self):
        """/readiness/ 경로 → ban 여부 무관하게 통과."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request(path="/readiness/")

        response = middleware(request)

        assert response is mock_response
        middleware._cache.get.assert_not_called()

    def test_liveness_path_exempt_from_ban(self):
        """/liveness/ 경로 → ban 여부 무관하게 통과."""
        middleware, mock_response = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request(path="/liveness/")

        response = middleware(request)

        assert response is mock_response
        middleware._cache.get.assert_not_called()

    # =================================================================
    # Fail-Open 동작 검증
    # =================================================================

    def test_redis_exception_allows_request(self):
        """Redis 장애(예외 발생) → 요청 허용 (Fail-Open)."""
        middleware, mock_response = self._make_middleware(cache_error=True)
        request = self._make_request()

        response = middleware(request)

        assert response is mock_response

    def test_cache_none_allows_request(self):
        """cache가 None → 요청 허용 (Fail-Open)."""
        mock_response = Mock()
        middleware = IPBanMiddleware(get_response=lambda r: mock_response)
        middleware._cache = None
        middleware._initialized = True

        request = self._make_request()
        response = middleware(request)

        assert response is mock_response

    # =================================================================
    # 보안 동작 검증
    # =================================================================

    def test_403_response_hides_ban_type(self):
        """403 응답 본문에 ban_type 미포함 (공격자 정보 노출 방지)."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "permanent"})
        request = self._make_request()

        response = middleware(request)

        body = json.loads(response.content)
        assert "type" not in body
        assert "ban_type" not in body
        assert "permanent" not in json.dumps(body)
        assert "temporary" not in json.dumps(body)

    def test_ban_type_written_to_log(self):
        """ban_type은 logger.warning으로 기록됨."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = self._make_request(path="/api/test/")

        with patch.object(_ip_ban_module, "logger") as mock_logger:
            middleware(request)

        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        assert "type=temporary" in log_message
        assert "Blocked banned IP" in log_message

    # =================================================================
    # IP 추출 동작 검증
    # =================================================================

    def test_cache_key_uses_extracted_ip(self):
        """extract_client_ip로 추출된 IP가 cache 키에 사용됨."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = self._make_request(ip="10.0.0.1")

        middleware(request)

        middleware._cache.get.assert_called_once_with("security:banned_ip:10.0.0.1")

    def test_x_forwarded_for_first_ip_used(self):
        """X-Forwarded-For 다중 IP → 첫 번째 IP가 cache 키에 사용됨."""
        middleware, _ = self._make_middleware(ban_info={"banned": True, "type": "temporary"})
        request = MagicMock()
        request.path = "/api/test/"
        request.META = {
            "HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8",
            "REMOTE_ADDR": "127.0.0.1",
        }

        middleware(request)

        middleware._cache.get.assert_called_once_with("security:banned_ip:1.2.3.4")

    def test_non_exempt_path_triggers_ban_check(self):
        """면제 경로가 아닌 경우 → cache.get 호출됨."""
        middleware, _ = self._make_middleware(ban_info=None)
        request = self._make_request(path="/api/orders/")

        middleware(request)

        middleware._cache.get.assert_called_once()

    # =================================================================
    # Lazy Initialization 동작 검증
    # =================================================================

    def test_lazy_init_skips_when_already_initialized(self):
        """_initialized=True → _lazy_init 내부 로직 미실행."""
        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._initialized = True
        middleware._cache = MagicMock()
        middleware._config = MagicMock()

        # _lazy_init 호출해도 _initialized 상태에서는 config/cache 로딩 재시도 없음
        original_cache = middleware._cache
        original_config = middleware._config
        middleware._lazy_init()

        assert middleware._cache is original_cache
        assert middleware._config is original_config

    def test_get_cache_retries_on_none(self):
        """_cache=None → _get_cache()에서 ProviderRegistry 재시도."""
        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._cache = None
        middleware._initialized = True

        mock_cache = MagicMock()
        with patch("selfhealing.factory.ProviderRegistry") as mock_registry:
            mock_registry.get_cache.return_value = mock_cache
            result = middleware._get_cache()

        assert result is mock_cache

    def test_get_cache_returns_existing_cache(self):
        """_cache가 이미 존재 → 즉시 반환 (ProviderRegistry 미호출)."""
        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        existing_cache = MagicMock()
        middleware._cache = existing_cache

        result = middleware._get_cache()

        assert result is existing_cache

    # =================================================================
    # 키 프리픽스 동작 검증
    # =================================================================

    def test_prefix_fallback_matches_security_config_default(self):
        """config=None → fallback 프리픽스가 SecurityConfig 기본값과 동일."""
        from selfhealing.services.security.models import SecurityConfig

        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._config = None

        default_config = SecurityConfig()
        assert middleware._get_banned_ip_prefix() == default_config.banned_ip_cache_prefix

    def test_prefix_from_injected_config(self):
        """config 존재 → config.banned_ip_cache_prefix 사용."""
        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        mock_config = MagicMock()
        mock_config.banned_ip_cache_prefix = "custom:prefix:"
        middleware._config = mock_config

        assert middleware._get_banned_ip_prefix() == "custom:prefix:"


@pytest.mark.skipif(
    not _MODULE_LOADED,
    reason=f"ip_ban module load failed: {_LOAD_ERROR if not _MODULE_LOADED else ''}",
)
class TestIPBanMiddlewareContract:
    """IPBanMiddleware 설계 계약 검증 테스트."""

    def test_exempt_path_prefixes_includes_health(self):
        """/health/ 가 면제 경로에 포함."""
        assert "/health/" in IPBanMiddleware.EXEMPT_PATH_PREFIXES

    def test_exempt_path_prefixes_includes_readiness(self):
        """/readiness/ 가 면제 경로에 포함."""
        assert "/readiness/" in IPBanMiddleware.EXEMPT_PATH_PREFIXES

    def test_exempt_path_prefixes_includes_liveness(self):
        """/liveness/ 가 면제 경로에 포함."""
        assert "/liveness/" in IPBanMiddleware.EXEMPT_PATH_PREFIXES

    def test_exempt_path_prefixes_count(self):
        """면제 경로는 정확히 3개."""
        assert len(IPBanMiddleware.EXEMPT_PATH_PREFIXES) == 3

    def test_default_prefix_value(self):
        """config=None fallback 프리픽스는 'security:banned_ip:'."""
        middleware = IPBanMiddleware(get_response=lambda r: Mock())
        middleware._config = None
        assert middleware._get_banned_ip_prefix() == "security:banned_ip:"

    def test_403_response_code_is_ip_banned(self):
        """403 응답의 code 필드는 'IP_BANNED'."""
        mock_response = Mock()
        middleware = IPBanMiddleware(get_response=lambda r: mock_response)
        mock_cache = MagicMock()
        mock_cache.get.return_value = {"banned": True, "type": "temporary"}
        middleware._cache = mock_cache
        middleware._initialized = True

        request = MagicMock()
        request.path = "/api/test/"
        request.META = {"REMOTE_ADDR": "1.2.3.4"}

        response = middleware(request)
        body = json.loads(response.content)
        assert body["code"] == "IP_BANNED"
        assert body["error"] == "Access denied"
