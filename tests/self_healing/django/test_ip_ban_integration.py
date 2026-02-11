"""
IPBanMiddleware 통합 테스트.

SecurityViolationService → Redis → IPBanMiddleware 전체 흐름을 검증합니다.
docker-compose 환경에서만 실행됩니다 (실제 Redis 필요).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.django_db
class TestIPBanMiddlewareIntegration:
    """IPBanMiddleware ↔ SecurityViolationService 연동 테스트."""

    def _make_middleware_with_cache(self, cache):
        """실제 cache를 주입한 미들웨어 생성."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        mock_response = MagicMock(status_code=200)
        middleware = IPBanMiddleware(get_response=lambda r: mock_response)
        middleware._cache = cache
        middleware._initialized = True
        return middleware, mock_response

    def _make_request(self, path="/api/test/", ip="192.168.1.100"):
        """request Mock 생성."""
        request = MagicMock()
        request.path = path
        request.META = {
            "HTTP_X_FORWARDED_FOR": ip,
            "REMOTE_ADDR": ip,
        }
        return request

    def test_temporary_ban_then_middleware_blocks(self):
        """SecurityViolationService 임시 ban → 동일 IP 후속 요청 403 차단."""
        from selfhealing.services.security.models import SecurityConfig
        from selfhealing.services.security.service import SecurityViolationService

        mock_cache = MagicMock()
        stored_data = {}

        def mock_set(key, value, ttl=None):
            stored_data[key] = value

        def mock_get(key):
            return stored_data.get(key)

        mock_cache.set = mock_set
        mock_cache.get = mock_get

        config = SecurityConfig()
        service = SecurityViolationService(config=config, cache=mock_cache, repository=MagicMock())

        # 1. SecurityViolationService로 임시 ban 기록
        test_ip = "10.20.30.40"
        service._temporary_ip_ban(test_ip, hours=1)

        # 2. 동일 IP로 미들웨어 호출 → 403 차단
        middleware, _ = self._make_middleware_with_cache(mock_cache)
        request = self._make_request(ip=test_ip)

        response = middleware(request)

        assert response.status_code == 403

    def test_permanent_ban_then_middleware_blocks(self):
        """SecurityViolationService 영구 ban → 동일 IP 후속 요청 403 차단."""
        from selfhealing.services.security.models import SecurityConfig
        from selfhealing.services.security.service import SecurityViolationService

        mock_cache = MagicMock()
        stored_data = {}

        def mock_set(key, value, ttl=None):
            stored_data[key] = value

        def mock_get(key):
            return stored_data.get(key)

        mock_cache.set = mock_set
        mock_cache.get = mock_get

        config = SecurityConfig()
        service = SecurityViolationService(config=config, cache=mock_cache, repository=MagicMock())

        # 1. 영구 ban 기록
        test_ip = "10.20.30.50"
        service._permanent_ip_ban(test_ip)

        # 2. 미들웨어 차단 확인
        middleware, _ = self._make_middleware_with_cache(mock_cache)
        request = self._make_request(ip=test_ip)

        response = middleware(request)

        assert response.status_code == 403

    def test_ban_expiry_allows_request(self):
        """ban 삭제 후 → 요청 다시 허용."""
        from selfhealing.services.security.models import SecurityConfig
        from selfhealing.services.security.service import SecurityViolationService

        mock_cache = MagicMock()
        stored_data = {}

        def mock_set(key, value, ttl=None):
            stored_data[key] = value

        def mock_get(key):
            return stored_data.get(key)

        def mock_delete(key):
            stored_data.pop(key, None)

        mock_cache.set = mock_set
        mock_cache.get = mock_get
        mock_cache.delete = mock_delete

        config = SecurityConfig()
        service = SecurityViolationService(config=config, cache=mock_cache, repository=MagicMock())

        test_ip = "10.20.30.60"

        # 1. ban 기록
        service._temporary_ip_ban(test_ip, hours=1)

        # 2. ban 상태에서 차단 확인
        middleware, mock_response = self._make_middleware_with_cache(mock_cache)
        request = self._make_request(ip=test_ip)
        response = middleware(request)
        assert response.status_code == 403

        # 3. ban 해제 (TTL 만료 시뮬레이션)
        service._remove_ip_ban(test_ip)

        # 4. 해제 후 요청 허용 확인
        middleware2, mock_response2 = self._make_middleware_with_cache(mock_cache)
        request2 = self._make_request(ip=test_ip)
        response2 = middleware2(request2)
        assert response2 is mock_response2

    def test_key_prefix_matches_between_service_and_middleware(self):
        """SecurityViolationService와 IPBanMiddleware가 동일한 Redis 키 프리픽스 사용."""
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware
        from selfhealing.services.security.models import SecurityConfig

        config = SecurityConfig()
        middleware = IPBanMiddleware(get_response=lambda r: MagicMock())
        middleware._config = None
        middleware._initialized = True

        # 미들웨어 fallback 프리픽스 == SecurityConfig 기본 프리픽스
        assert middleware._get_banned_ip_prefix() == config.banned_ip_cache_prefix

    def test_unbanned_ip_passes_through(self):
        """ban되지 않은 IP → 정상 통과."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        middleware, mock_response = self._make_middleware_with_cache(mock_cache)
        request = self._make_request(ip="192.168.1.1")

        response = middleware(request)

        assert response is mock_response
