"""
Redis 세션 백엔드 전환 설정 검증.

SESSION_ENGINE, SESSION_CACHE_ALIAS 설정과
django.contrib.sessions, SessionMiddleware 유지를 확인한다.
"""

from __future__ import annotations


from django.conf import settings


class TestRedisSessionBackendSettings:
    """Redis 세션 백엔드 설정 검증."""

    def test_session_engine_is_cache(self):
        """SESSION_ENGINE이 cache 백엔드로 설정되었는지 확인."""
        assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.cache"

    def test_session_cache_alias_is_default(self):
        """SESSION_CACHE_ALIAS가 default인지 확인."""
        assert settings.SESSION_CACHE_ALIAS == "default"

    def test_sessions_app_still_installed(self):
        """django.contrib.sessions가 INSTALLED_APPS에 남아있는지 확인.
        admin과 SessionMiddleware, allauth가 이 앱에 의존한다."""
        assert "django.contrib.sessions" in settings.INSTALLED_APPS

    def test_session_middleware_still_active(self):
        """SessionMiddleware가 MIDDLEWARE에 포함되어 있는지 확인."""
        assert any("SessionMiddleware" in m for m in settings.MIDDLEWARE)
