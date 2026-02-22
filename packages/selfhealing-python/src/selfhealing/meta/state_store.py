"""
Meta-Watchdog 상태 저장소.

Pod 재시작 시에도 상태 유지를 위한 Redis 기반 저장소.

기능:
- consecutive_failures: 컴포넌트별 연속 실패 횟수 저장
- escalation_cooldowns: 에스컬레이션 쿨다운 시각 저장
- last_loop_timestamp: 마지막 루프 타임스탬프 (Liveness용)
- 분산 락: 멀티 인스턴스 환경에서 중복 에스컬레이션 방지
"""

from __future__ import annotations

import structlog
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger()


class WatchdogStateStore:
    """
    Watchdog 상태 저장소.

    Redis 기반으로 다음 상태를 영속화:
    - consecutive_failures: 컴포넌트별 연속 실패 횟수
    - escalation_cooldowns: 에스컬레이션 쿨다운 시각
    - last_loop_timestamp: 마지막 루프 타임스탬프

    Pod 재시작 후에도 상태를 이어받아 즉시 에스컬레이션 가능합니다.

    사용 예시:
        store = WatchdogStateStore()

        # 실패 횟수 증가
        count = store.increment_failure_count("dlq")

        # 에스컬레이션 기록
        store.record_escalation("dlq")

        # 쿨다운 확인
        if store.can_escalate("dlq", cooldown_seconds=3600):
            send_escalation()
    """

    # Redis 키 패턴
    KEY_PREFIX = "selfhealing:meta:watchdog"
    FAILURES_KEY = f"{KEY_PREFIX}:failures"  # Hash
    COOLDOWNS_KEY = f"{KEY_PREFIX}:cooldowns"  # Hash
    LAST_CHECK_KEY = f"{KEY_PREFIX}:last_check"  # String
    LAST_LOOP_KEY = f"{KEY_PREFIX}:last_loop_timestamp"  # String (Liveness용)

    # TTL (7일 - 오래된 데이터 자동 정리)
    STATE_TTL_SECONDS = 7 * 24 * 60 * 60

    def __init__(self, redis_client: Any | None = None):
        """
        초기화.

        Args:
            redis_client: Redis 클라이언트 (None이면 자동 획득)
        """
        self._redis = redis_client
        self._local_failures: dict[str, int] = {}  # 폴백용
        self._local_cooldowns: dict[str, float] = {}
        self._local_last_loop: datetime | None = None
        self._lock = threading.RLock()

    def _get_redis(self) -> Any | None:
        """Redis 클라이언트 획득."""
        if self._redis is not None:
            return self._redis

        try:
            from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

            adapter = RedisCacheAdapter()
            return adapter._redis
        except ImportError:
            return None
        except Exception:
            return None

    # =========================================================================
    # Consecutive Failures (연속 실패 횟수)
    # =========================================================================

    def get_failure_count(self, component: str) -> int:
        """
        연속 실패 횟수 조회.

        Args:
            component: 컴포넌트 이름

        Returns:
            연속 실패 횟수
        """
        redis = self._get_redis()
        if redis:
            try:
                count = redis.hget(self.FAILURES_KEY, component)
                if count:
                    if isinstance(count, bytes):
                        count = count.decode("utf-8")
                    return int(count)
            except Exception as e:
                logger.debug(
                    "watchdog_state_store.redis_get_failed",
                    error=e,
                )

        # 폴백: 로컬 메모리
        with self._lock:
            return self._local_failures.get(component, 0)

    def increment_failure_count(self, component: str) -> int:
        """
        연속 실패 횟수 증가.

        Args:
            component: 컴포넌트 이름

        Returns:
            증가 후 횟수
        """
        redis = self._get_redis()
        if redis:
            try:
                new_count = redis.hincrby(self.FAILURES_KEY, component, 1)
                redis.expire(self.FAILURES_KEY, self.STATE_TTL_SECONDS)
                return new_count
            except Exception as e:
                logger.debug(
                    "watchdog_state_store.redis_incr_failed",
                    error=e,
                )

        # 폴백
        with self._lock:
            self._local_failures[component] = self._local_failures.get(component, 0) + 1
            return self._local_failures[component]

    def reset_failure_count(self, component: str) -> None:
        """
        연속 실패 횟수 리셋.

        Args:
            component: 컴포넌트 이름
        """
        redis = self._get_redis()
        if redis:
            try:
                redis.hdel(self.FAILURES_KEY, component)
            except Exception:
                pass

        with self._lock:
            self._local_failures.pop(component, None)

    def reset_all_failure_counts(self) -> None:
        """모든 연속 실패 횟수 리셋."""
        redis = self._get_redis()
        if redis:
            try:
                redis.delete(self.FAILURES_KEY)
            except Exception:
                pass

        with self._lock:
            self._local_failures.clear()

    # =========================================================================
    # Escalation Cooldowns (에스컬레이션 쿨다운)
    # =========================================================================

    def get_last_escalation_time(self, component: str) -> float:
        """
        마지막 에스컬레이션 시각 조회.

        Args:
            component: 컴포넌트 이름

        Returns:
            Unix timestamp (없으면 0)
        """
        redis = self._get_redis()
        if redis:
            try:
                ts = redis.hget(self.COOLDOWNS_KEY, component)
                if ts:
                    if isinstance(ts, bytes):
                        ts = ts.decode("utf-8")
                    return float(ts)
            except Exception:
                pass

        with self._lock:
            return self._local_cooldowns.get(component, 0)

    def record_escalation(self, component: str) -> None:
        """
        에스컬레이션 기록.

        Args:
            component: 컴포넌트 이름
        """
        now = time.time()
        redis = self._get_redis()
        if redis:
            try:
                redis.hset(self.COOLDOWNS_KEY, component, str(now))
                redis.expire(self.COOLDOWNS_KEY, self.STATE_TTL_SECONDS)
            except Exception:
                pass

        with self._lock:
            self._local_cooldowns[component] = now

    def can_escalate(self, component: str, cooldown_seconds: float) -> bool:
        """
        에스컬레이션 쿨다운 확인.

        Args:
            component: 컴포넌트 이름
            cooldown_seconds: 쿨다운 시간 (초)

        Returns:
            에스컬레이션 가능 여부
        """
        last_time = self.get_last_escalation_time(component)
        return time.time() - last_time > cooldown_seconds

    def reset_escalation_cooldown(self, component: str) -> None:
        """
        에스컬레이션 쿨다운 리셋.

        Args:
            component: 컴포넌트 이름
        """
        redis = self._get_redis()
        if redis:
            try:
                redis.hdel(self.COOLDOWNS_KEY, component)
            except Exception:
                pass

        with self._lock:
            self._local_cooldowns.pop(component, None)

    # =========================================================================
    # Last Loop Timestamp (Liveness용)
    # =========================================================================

    def update_last_loop_timestamp(self) -> None:
        """마지막 루프 타임스탬프 갱신."""
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        redis = self._get_redis()
        if redis:
            try:
                redis.set(self.LAST_LOOP_KEY, now_str, ex=300)  # 5분 TTL
            except Exception:
                pass

        with self._lock:
            self._local_last_loop = now

    def get_last_loop_timestamp(self) -> datetime | None:
        """
        마지막 루프 타임스탬프 조회.

        Returns:
            마지막 루프 시각 (없으면 None)
        """
        redis = self._get_redis()
        if redis:
            try:
                ts = redis.get(self.LAST_LOOP_KEY)
                if ts:
                    if isinstance(ts, bytes):
                        ts = ts.decode("utf-8")
                    return datetime.fromisoformat(ts)
            except Exception:
                pass

        with self._lock:
            return self._local_last_loop

    def get_last_loop_age_seconds(self) -> float:
        """
        마지막 루프 이후 경과 시간.

        Returns:
            경과 시간 (초), 기록 없으면 무한대
        """
        last = self.get_last_loop_timestamp()
        if last is None:
            return float("inf")
        return (datetime.now(timezone.utc) - last).total_seconds()

    # =========================================================================
    # 분산 락 (중복 에스컬레이션 방지)
    # =========================================================================

    def acquire_escalation_lock(
        self,
        component: str,
        lock_ttl_seconds: int = 30,
    ) -> bool:
        """
        에스컬레이션 락 획득.

        멀티 인스턴스 환경에서 동일 컴포넌트에 대해
        하나의 인스턴스만 에스컬레이션하도록 보장합니다.

        Args:
            component: 컴포넌트 이름
            lock_ttl_seconds: 락 TTL (초)

        Returns:
            락 획득 성공 여부
        """
        redis = self._get_redis()
        if not redis:
            return True  # Redis 없으면 락 없이 진행

        lock_key = f"{self.KEY_PREFIX}:escalation:lock:{component}"
        try:
            # SET NX EX: 키가 없을 때만 설정 + TTL
            acquired = redis.set(lock_key, "1", nx=True, ex=lock_ttl_seconds)
            return bool(acquired)
        except Exception as e:
            logger.debug(
                "watchdog_state_store.lock_acquire_failed",
                error=e,
            )
            return True  # 실패 시 진행 허용

    def release_escalation_lock(self, component: str) -> None:
        """
        에스컬레이션 락 해제.

        Args:
            component: 컴포넌트 이름
        """
        redis = self._get_redis()
        if redis:
            lock_key = f"{self.KEY_PREFIX}:escalation:lock:{component}"
            try:
                redis.delete(lock_key)
            except Exception:
                pass

    # =========================================================================
    # 유틸리티
    # =========================================================================

    def clear_all(self) -> None:
        """모든 상태 초기화 (테스트용)."""
        redis = self._get_redis()
        if redis:
            try:
                redis.delete(self.FAILURES_KEY)
                redis.delete(self.COOLDOWNS_KEY)
                redis.delete(self.LAST_LOOP_KEY)
            except Exception:
                pass

        with self._lock:
            self._local_failures.clear()
            self._local_cooldowns.clear()
            self._local_last_loop = None


# =============================================================================
# 싱글톤
# =============================================================================

_state_store: WatchdogStateStore | None = None
_store_lock = threading.Lock()


def get_watchdog_state_store() -> WatchdogStateStore:
    """WatchdogStateStore 싱글톤 반환."""
    global _state_store
    if _state_store is None:
        with _store_lock:
            if _state_store is None:
                _state_store = WatchdogStateStore()
    return _state_store


def reset_watchdog_state_store() -> None:
    """WatchdogStateStore 리셋 (테스트용)."""
    global _state_store
    with _store_lock:
        _state_store = None
