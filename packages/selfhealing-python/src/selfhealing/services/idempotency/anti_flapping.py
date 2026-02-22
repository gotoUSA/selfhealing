"""
Anti-Flapping Window

Sliding window based anti-flapping detection for parameter auto-tuning.

Canonical location: ``selfhealing.services.idempotency.anti_flapping``
"""

from __future__ import annotations

import structlog
import time
from collections import defaultdict
from threading import Lock

logger = structlog.get_logger()


class AntiFlappingWindow:
    """
    Anti-Flapping 윈도우 (슬라이딩 윈도우 기반).

    동일하거나 유사한 값이 짧은 시간 내 반복되는 것을 감지.

    분산 환경 지원 (v2.4.0):
    - Redis 사용 가능 시: ZSET 기반 분산 슬라이딩 윈도우
    - Redis 미사용 시: 메모리 기반 로컬 윈도우 (기존 동작)

    Reference:
    - Architect Review: "1% 미만의 조정 반복을 중복/루프로 간주"
    - 기존 SlidingWindowThrottle 패턴 재사용
    """

    REDIS_KEY_PREFIX = "selfhealing:anti_flapping:"

    def __init__(
        self,
        window_seconds: int = 60,
        similarity_threshold: float = 0.01,  # 1% 이내 = 유사
        max_similar_changes: int = 3,
        use_redis: bool = True,
    ):
        """
        Initialize AntiFlappingWindow.

        Args:
            window_seconds: 슬라이딩 윈도우 크기 (초)
            similarity_threshold: 유사 판정 임계값 (0.01 = 1%)
            max_similar_changes: 윈도우 내 최대 유사 변경 횟수
            use_redis: Redis 사용 여부 (분산 환경 지원)
        """
        self.window_seconds = window_seconds
        self.similarity_threshold = similarity_threshold
        self.max_similar_changes = max_similar_changes
        self._use_redis = use_redis

        # 메모리 기반 로컬 윈도우 (fallback)
        # key -> [(timestamp, value), ...]
        self._windows: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._lock = Lock()

        # Redis 클라이언트 초기화
        self._redis_client = None
        if use_redis:
            self._init_redis_client()

    def _init_redis_client(self) -> None:
        """Redis 클라이언트 초기화."""
        try:
            from selfhealing.core.state_backend import (
                RedisStateBackend,
                get_state_backend,
            )

            backend = get_state_backend()
            if isinstance(backend, RedisStateBackend):
                self._redis_client = backend._client
                logger.info("anti_flapping_window.redis_mode_enabled_distributed")
            else:
                logger.info("anti_flapping_window.file_backend_detected_using")
        except Exception as e:
            from selfhealing.adapters.resilient.backend import _safe_error_message

            logger.warning(
                "resilient_storage.redis_init_failed",
                _safe_error_message=_safe_error_message(e),
            )

    def check_and_record(
        self,
        key: str,
        new_value: float,
    ) -> tuple[bool, str]:
        """
        새 값이 플래핑인지 확인하고 기록.

        Args:
            key: 파라미터 키 (예: "circuit_breaker:threshold")
            new_value: 새로운 값

        Returns:
            (is_flapping, reason)
        """
        if self._redis_client:
            return self._check_and_record_redis(key, new_value)
        else:
            return self._check_and_record_memory(key, new_value)

    def _check_and_record_redis(
        self,
        key: str,
        new_value: float,
    ) -> tuple[bool, str]:
        """
        Redis ZSET 기반 분산 슬라이딩 윈도우.

        ZSET 활용:
        - score: timestamp
        - member: "timestamp:value" 문자열
        - ZRANGEBYSCORE로 윈도우 내 값들 조회
        - ZREMRANGEBYSCORE로 만료된 엔트리 제거

        순위 5.3 구현
        """
        redis_key = f"{self.REDIS_KEY_PREFIX}{key}"
        now_ts = time.time()
        window_start = now_ts - self.window_seconds

        try:
            pipe = self._redis_client.pipeline()

            # 1. 오래된 엔트리 제거
            pipe.zremrangebyscore(redis_key, "-inf", window_start)

            # 2. 현재 윈도우 내 모든 엔트리 조회
            pipe.zrangebyscore(redis_key, window_start, "+inf", withscores=True)

            results = pipe.execute()
            entries = results[1]  # [(member, score), ...]

            # 3. 유사한 값 변경 횟수 계산
            similar_count = 0
            for member, _ in entries:
                # member 형식: "timestamp:value"
                try:
                    if isinstance(member, bytes):
                        member = member.decode("utf-8")
                    _, val_str = member.split(":", 1)
                    val = float(val_str)
                    if self._is_similar(val, new_value):
                        similar_count += 1
                except (ValueError, AttributeError):
                    continue

            # 4. 플래핑 감지
            if similar_count >= self.max_similar_changes:
                return (
                    True,
                    f"Flapping detected: {similar_count} similar changes in {self.window_seconds}s",
                )

            # 5. 현재 값 기록
            member = f"{now_ts}:{new_value}"
            self._redis_client.zadd(redis_key, {member: now_ts})

            # 6. TTL 설정 (윈도우 * 2로 안전하게)
            self._redis_client.expire(redis_key, self.window_seconds * 2)

            return False, ""

        except Exception as e:
            logger.warning(
                "anti_flapping_window.redis_error_fallback_memory",
                error=e,
            )
            return self._check_and_record_memory(key, new_value)

    def _check_and_record_memory(
        self,
        key: str,
        new_value: float,
    ) -> tuple[bool, str]:
        """메모리 기반 로컬 슬라이딩 윈도우 (기존 로직)."""
        now_ts = time.time()
        window_start = now_ts - self.window_seconds

        with self._lock:
            # 슬라이딩 윈도우: 오래된 엔트리 제거
            self._windows[key] = [(ts, val) for ts, val in self._windows[key] if ts > window_start]

            # 유사한 값 변경 횟수 계산
            similar_count = 0
            for ts, val in self._windows[key]:
                if self._is_similar(val, new_value):
                    similar_count += 1

            # 플래핑 감지
            if similar_count >= self.max_similar_changes:
                return (
                    True,
                    f"Flapping detected: {similar_count} similar changes in {self.window_seconds}s",
                )

            # 현재 값 기록
            self._windows[key].append((now_ts, new_value))

            return False, ""

    def _is_similar(self, val1: float, val2: float) -> bool:
        """두 값이 유사한지 확인 (threshold 이내)."""
        if val1 == 0 and val2 == 0:
            return True
        if val1 == 0 or val2 == 0:
            return False

        diff_ratio = abs(val1 - val2) / max(abs(val1), abs(val2))
        return diff_ratio <= self.similarity_threshold

    def clear_window(self, key: str) -> bool:
        """
        특정 키의 윈도우 클리어 (테스트용).

        Args:
            key: 파라미터 키

        Returns:
            성공 여부
        """
        if self._redis_client:
            try:
                redis_key = f"{self.REDIS_KEY_PREFIX}{key}"
                self._redis_client.delete(redis_key)
                return True
            except Exception as e:
                logger.warning(
                    "anti_flapping_window.redis_clear_failed",
                    error=e,
                )

        with self._lock:
            if key in self._windows:
                del self._windows[key]
        return True


# 전역 Anti-Flapping 윈도우
_anti_flapping_window: AntiFlappingWindow | None = None


def get_anti_flapping_window() -> AntiFlappingWindow:
    """Get singleton AntiFlappingWindow."""
    global _anti_flapping_window
    if _anti_flapping_window is None:
        _anti_flapping_window = AntiFlappingWindow()
    return _anti_flapping_window
