"""
Redis 기반 Cooldown 영속 저장소.

Redis TTL을 활용하여 Pod 재시작 이후에도 쿨다운 상태를 유지합니다.
Redis 장애 시 메모리 폴백으로 동작합니다.

키 형식: selfhealing:notification:cooldown:{dedup_key}
값: 마지막 전송 시간 (Unix timestamp)
TTL: cooldown_seconds (자동 만료)
"""

from __future__ import annotations

import time

import structlog

logger = structlog.get_logger()


class RedisCooldownStore:
    """
    Redis 기반 Cooldown 영속 저장소.

    Redis SET + TTL 자동 만료 방식으로 쿨다운을 관리합니다.
    Redis 장애 시 인메모리 dict로 폴백합니다.
    """

    KEY_PREFIX = "selfhealing:notification:cooldown"

    def __init__(self, redis_client=None, cooldown_seconds: int = 1800):
        self._redis = redis_client
        self._cooldown_seconds = cooldown_seconds
        # 메모리 폴백 (Redis 미사용 또는 장애 시)
        self._memory_cache: dict[str, float] = {}

    def is_cooled_down(self, dedup_key: str) -> bool:
        """
        해당 dedup_key가 쿨다운 중인지 확인.

        Returns:
            True이면 쿨다운 중 (알림 억제)
        """
        if self._redis is not None:
            try:
                key = f"{self.KEY_PREFIX}:{dedup_key}"
                return self._redis.exists(key) > 0
            except Exception:
                pass  # Redis 장애 시 메모리 폴백

        # 메모리 폴백
        last_sent = self._memory_cache.get(dedup_key)
        if last_sent is None:
            return False
        return (time.time() - last_sent) < self._cooldown_seconds

    def mark_sent(self, dedup_key: str) -> None:
        """
        알림 전송 완료를 기록 (쿨다운 시작).

        Redis: SET key value EX cooldown_seconds (TTL 자동 만료)
        """
        now = time.time()

        if self._redis is not None:
            try:
                key = f"{self.KEY_PREFIX}:{dedup_key}"
                self._redis.set(key, str(now), ex=self._cooldown_seconds)
                return
            except Exception:
                pass  # Redis 장애 시 메모리 폴백

        self._memory_cache[dedup_key] = now

    def clear(self, dedup_key: str) -> None:
        """특정 dedup_key의 쿨다운을 해제합니다."""
        if self._redis is not None:
            try:
                key = f"{self.KEY_PREFIX}:{dedup_key}"
                self._redis.delete(key)
            except Exception:
                pass

        self._memory_cache.pop(dedup_key, None)
