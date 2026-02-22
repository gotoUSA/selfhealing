"""
UserSessionRegistry - user_id → session_key 역방향 매핑 관리.

Django 세션은 session_key → session_data 단방향 저장만 지원한다.
이 모듈은 user_id로 해당 유저의 session_key를 찾을 수 있도록
Redis 리스트 구조로 역방향 인덱스를 관리한다.

Redis 키 구조:
    security:user_sessions:{user_id} → list[session_key]

다중 세션 지원:
    한 유저가 여러 기기에서 로그인할 수 있으므로 단일 값이 아닌 리스트를 사용.

시그널 연결:
    SelfHealingConfig.ready()가 adapters/django/signal_hooks.py의
    connect_session_signals()를 호출하여 user_logged_in / user_logged_out
    시그널에 자동 연결된다. 호스트 앱에서 별도 코드 불필요.
"""

from __future__ import annotations

import structlog
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.interfaces.cache_provider import CacheProviderInterface

logger = structlog.get_logger()

# Django 기본값: SESSION_COOKIE_AGE = 1209600 (2주)
_DEFAULT_SESSION_TTL_SECONDS = 1209600


class UserSessionRegistry:
    """
    user_id → session_key 리스트 역방향 매핑 관리.

    Django cache 세션 백엔드는 session_key로부터 session_data를 조회하지만,
    user_id로 해당 유저의 모든 session_key를 찾는 역방향 조회는 불가능하다.
    이 클래스가 그 역방향 인덱스를 CacheProviderInterface 기반으로 관리한다.
    """

    KEY_PREFIX = "security:user_sessions:"

    def __init__(self, cache: CacheProviderInterface | None = None):
        self._cache = cache

    @property
    def cache(self) -> CacheProviderInterface:
        if self._cache is None:
            from selfhealing.factory import ProviderRegistry

            try:
                self._cache = ProviderRegistry.get_cache()
            except (ValueError, ImportError):
                from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

                self._cache = InMemoryCacheAdapter()
        return self._cache

    def _key(self, user_id: int) -> str:
        return f"{self.KEY_PREFIX}{user_id}"

    def register(self, user_id: int, session_key: str) -> None:
        """
        로그인 시 user_id → session_key 매핑 등록.

        기존 리스트에 session_key를 추가한다 (중복 방지).
        TTL은 Django SESSION_COOKIE_AGE와 동기화.
        """
        key = self._key(user_id)
        try:
            existing: list[str] = self.cache.get(key) or []
            if session_key not in existing:
                existing.append(session_key)
            ttl = self._get_session_ttl()
            self.cache.set(key, existing, ttl=timedelta(seconds=ttl))
            logger.debug(
                f"[UserSessionRegistry] Registered session for user {user_id}: "
                f"{session_key[:8]}... (total: {len(existing)})"
            )
        except Exception as e:
            logger.warning(
                "user_session_registry.failed_register_session",
                error=e,
            )

    def unregister(self, user_id: int, session_key: str) -> None:
        """
        로그아웃 시 user_id → session_key 매핑 제거.

        리스트에서 해당 session_key만 제거하고, 리스트가 비면 키 자체를 삭제한다.
        """
        key = self._key(user_id)
        try:
            existing: list[str] = self.cache.get(key) or []
            if session_key in existing:
                existing.remove(session_key)
            if existing:
                ttl = self._get_session_ttl()
                self.cache.set(key, existing, ttl=timedelta(seconds=ttl))
            else:
                self.cache.delete(key)
            logger.debug(
                "user_session_registry.unregistered_session_user",
                user_id=user_id,
                session_key=session_key[:8],
            )
        except Exception as e:
            logger.warning(
                "user_session_registry.failed_unregister_session",
                error=e,
            )

    def get_session_keys(self, user_id: int) -> list[str]:
        """user_id에 연결된 모든 session_key 조회."""
        key = self._key(user_id)
        try:
            return self.cache.get(key) or []
        except Exception:
            return []

    def invalidate_all(self, user_id: int) -> int:
        """
        user_id의 모든 세션을 무효화.

        1. 레지스트리에서 session_key 목록 조회
        2. Django cache 세션 키 삭제 시도
        3. 레지스트리 키 자체 삭제

        Returns:
            삭제된 세션 수
        """
        session_keys = self.get_session_keys(user_id)
        deleted = 0
        for sk in session_keys:
            try:
                # Django cache 세션 키 형식으로 삭제 시도
                self.cache.delete(sk)
                self.cache.delete(f"django.contrib.sessions.cache{sk}")
                deleted += 1
            except Exception:
                pass
        # 레지스트리 키 삭제
        self.cache.delete(self._key(user_id))
        logger.info(
            "user_session_registry.invalidated_sessions_user",
            deleted=deleted,
            user_id=user_id,
        )
        return deleted

    @staticmethod
    def _get_session_ttl() -> int:
        """Django SESSION_COOKIE_AGE 설정값 조회."""
        try:
            from django.conf import settings as django_settings

            return getattr(django_settings, "SESSION_COOKIE_AGE", _DEFAULT_SESSION_TTL_SECONDS)
        except Exception:
            return _DEFAULT_SESSION_TTL_SECONDS


# =============================================================================
# Singleton
# =============================================================================

_registry: UserSessionRegistry | None = None


def get_user_session_registry() -> UserSessionRegistry:
    """UserSessionRegistry 싱글톤 반환."""
    global _registry
    if _registry is None:
        _registry = UserSessionRegistry()
    return _registry


def reset_user_session_registry() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _registry
    _registry = None
