# packages/selfhealing-python/src/selfhealing/core/state_cache.py
"""
Circuit Breaker 상태 캐시 (Platinum SLA 최적화)

TTL 기반 로컬 캐싱으로 네트워크 호출 최소화
Polling Jitter로 Thundering Herd 방지

설정값은 StateCacheSettings를 통해 환경변수로 오버라이드 가능:
- SELFHEALING_STATE_CACHE_BASE_TTL
- SELFHEALING_STATE_CACHE_JITTER_RANGE
"""

import random
import threading
import time
from collections.abc import Callable
from typing import Any

from selfhealing.settings.state_cache import get_state_cache_settings

__all__ = ["CBStateCache"]


class CBStateCache:
    """
    Circuit Breaker 상태 캐시

    - TTL 기반 로컬 캐싱으로 네트워크 호출 최소화
    - Polling Jitter로 Thundering Herd 방지
    - Thread-Safe 구현

    Usage:
        def fetch_cb_state(service):
            return requests.get(f'http://command-center/cb/{service}').json()

        CBStateCache.configure(fetch_callback=fetch_cb_state)
        state = CBStateCache.get_state('payment')
    """

    _cache: dict[str, dict[str, Any]] = {}
    _lock = threading.RLock()
    _fetch_callback: Callable[[str], dict] | None = None

    @classmethod
    def _get_base_ttl(cls) -> float:
        """기본 TTL (초). StateCacheSettings에서 로드."""
        return get_state_cache_settings().base_ttl

    @classmethod
    def _get_jitter_range(cls) -> float:
        """랜덤 지터 범위 (초). StateCacheSettings에서 로드."""
        return get_state_cache_settings().jitter_range

    @classmethod
    def configure(cls, fetch_callback: Callable[[str], dict]) -> None:
        """
        상태 조회 콜백 설정

        Args:
            fetch_callback: 서비스명을 받아 사령탑에서 CB 상태를 가져오는 함수
        """
        cls._fetch_callback = fetch_callback

    @classmethod
    def get_state(cls, service: str) -> dict | None:
        """
        CB 상태 조회 (캐시 우선)

        캐시 히트: ~0.01ms
        캐시 미스: 사령탑 호출 시간

        Args:
            service: 서비스 식별자

        Returns:
            CB 상태 딕셔너리 또는 None
        """
        with cls._lock:
            cached = cls._cache.get(service)

            if cached and not cls._is_expired(cached):
                return cached["state"]

        # 캐시 미스 → 사령탑 호출
        return cls._refresh(service)

    @classmethod
    def set_state(cls, service: str, state: dict[str, Any]) -> None:
        """
        CB 상태를 캐시에 직접 설정 (테스트 또는 수동 업데이트용)

        Args:
            service: 서비스 식별자
            state: CB 상태 딕셔너리
        """
        ttl = cls._calculate_ttl()

        with cls._lock:
            cls._cache[service] = {
                "state": state,
                "fetched_at": time.time(),
                "expires_at": time.time() + ttl,
            }

    @classmethod
    def invalidate(cls, service: str) -> None:
        """특정 서비스 캐시 무효화"""
        with cls._lock:
            cls._cache.pop(service, None)

    @classmethod
    def invalidate_all(cls) -> None:
        """전체 캐시 무효화"""
        with cls._lock:
            cls._cache.clear()

    @classmethod
    def get_cache_stats(cls) -> dict[str, Any]:
        """캐시 통계 조회"""
        with cls._lock:
            now = time.time()
            total = len(cls._cache)
            expired = sum(1 for v in cls._cache.values() if now >= v["expires_at"])
            return {
                "total_entries": total,
                "expired_entries": expired,
                "active_entries": total - expired,
            }

    @classmethod
    def _is_expired(cls, cached: dict) -> bool:
        """TTL 만료 확인"""
        return time.time() >= cached["expires_at"]

    @classmethod
    def _calculate_ttl(cls) -> float:
        """
        Jitter가 적용된 TTL 계산

        Thundering Herd 방지를 위해 base_ttl ± jitter_range 사이 랜덤
        """
        jitter_range = cls._get_jitter_range()
        jitter = random.uniform(-jitter_range, jitter_range)
        return cls._get_base_ttl() + jitter

    @classmethod
    def _refresh(cls, service: str) -> dict | None:
        """사령탑에서 상태 가져와 캐시 갱신"""
        if not cls._fetch_callback:
            return None

        try:
            state = cls._fetch_callback(service)
            ttl = cls._calculate_ttl()

            with cls._lock:
                cls._cache[service] = {
                    "state": state,
                    "fetched_at": time.time(),
                    "expires_at": time.time() + ttl,
                }

            return state

        except Exception:
            # 사령탑 연결 실패 → DegradedModeHandler 사용
            from selfhealing.core.degraded_mode_handler import DegradedModeHandler

            DegradedModeHandler.enter_degraded_mode()
            return DegradedModeHandler.get_cb_config()

    @classmethod
    def reset(cls) -> None:
        """상태 초기화 (테스트용)"""
        with cls._lock:
            cls._cache.clear()
            cls._fetch_callback = None
