"""
Safe-Open 폴백 모듈.

Redis 다운 시 완전 Fail-Open(제한 없음) 또는 완전 Fail-Closed(모두 차단)가 아닌
보수적인 Safe-Open 전략을 제공합니다.

전략:
- Redis 정상: 분산 limit 동기화
- Redis 다운: 마지막으로 알려진 안전한 limit 유지
- Cold Start: Redis에서 마지막 안전 limit 복구
"""

from __future__ import annotations

import structlog
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = structlog.get_logger()


class RedisConnectionState(str, Enum):
    """Redis 연결 상태."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECOVERING = "recovering"


@dataclass
class SafeOpenConfig:
    """Safe-Open 폴백 설정."""

    # Safe-Open 활성화
    enabled: bool = True

    # Redis 헬스체크 간격 (초)
    health_check_interval_seconds: float = 5.0

    # 연속 실패 횟수 → 연결 끊김 판단
    failure_threshold: int = 3

    # 마지막 안전 limit 저장 간격 (초)
    safe_limit_save_interval_seconds: float = 60.0

    # 안전 limit의 최대 유효 시간 (초)
    safe_limit_max_age_seconds: int = 3600

    # 기본 폴백 limit (안전 limit도 없을 때)
    default_fallback_limit: int = 50


@dataclass
class ServiceSafeLimitState:
    """서비스별 Safe Limit 상태."""

    service_name: str
    last_known_safe_limit: int
    last_saved_at: float = field(default_factory=time.time)
    redis_state: RedisConnectionState = RedisConnectionState.CONNECTED
    consecutive_failures: int = 0


class SafeOpenFallbackManager:
    """
    Safe-Open 폴백 관리자.

    Redis 장애 시 마지막으로 알려진 안전한 limit을 사용하여
    서비스 연속성을 유지합니다.

    LocalMemoryRateLimiter 패턴을 참고하여 구현되었습니다.
    """

    def __init__(
        self,
        config: SafeOpenConfig | None = None,
        redis_client: Any = None,
    ):
        """
        초기화.

        Args:
            config: Safe-Open 설정
            redis_client: Redis 클라이언트 (옵션)
        """
        self.config = config or SafeOpenConfig()
        self._redis = redis_client

        # 서비스별 상태
        self._service_states: dict[str, ServiceSafeLimitState] = {}
        self._lock = threading.RLock()

        # 글로벌 Redis 상태
        self._global_redis_state = RedisConnectionState.CONNECTED
        self._last_health_check = 0.0

        # Limit 관리자 (lazy loading)
        self._limit_manager = None

    def _get_limit_manager(self):
        """RedisThrottleLimitManager 인스턴스 가져오기."""
        if self._limit_manager is None and self._redis is not None:
            from selfhealing.services.throttle.redis_lua import (
                RedisThrottleLimitManager,
            )

            self._limit_manager = RedisThrottleLimitManager(self._redis)
        return self._limit_manager

    def set_redis_client(self, redis_client: Any) -> None:
        """Redis 클라이언트 설정."""
        self._redis = redis_client
        self._limit_manager = None  # 재생성 필요

    def check_redis_health(self) -> bool:
        """Redis 연결 상태 확인."""
        if self._redis is None:
            return False

        now = time.time()
        if now - self._last_health_check < self.config.health_check_interval_seconds:
            return self._global_redis_state == RedisConnectionState.CONNECTED

        self._last_health_check = now

        try:
            self._redis.ping()
            self._on_redis_connected()
            return True
        except Exception as e:
            logger.warning(
                "safe_open_fallback.redis_health_check_failed",
                error=e,
            )
            self._on_redis_disconnected()
            return False

    def _on_redis_connected(self) -> None:
        """Redis 연결 복구 시 호출."""
        previous_state = self._global_redis_state

        with self._lock:
            self._global_redis_state = RedisConnectionState.CONNECTED

            # 모든 서비스 상태 업데이트
            for state in self._service_states.values():
                state.redis_state = RedisConnectionState.CONNECTED
                state.consecutive_failures = 0

        if previous_state != RedisConnectionState.CONNECTED:
            logger.info("safe_open_fallback.redis_connection_restored")

    def _on_redis_disconnected(self) -> None:
        """Redis 연결 끊김 시 호출."""
        with self._lock:
            self._global_redis_state = RedisConnectionState.DISCONNECTED

            for state in self._service_states.values():
                state.redis_state = RedisConnectionState.DISCONNECTED

        logger.warning("safe_open_fallback.redis_connection_lost_using")

    def get_or_create_service_state(
        self,
        service_name: str,
        initial_limit: int,
    ) -> ServiceSafeLimitState:
        """서비스별 상태 가져오기 또는 생성."""
        with self._lock:
            if service_name not in self._service_states:
                self._service_states[service_name] = ServiceSafeLimitState(
                    service_name=service_name,
                    last_known_safe_limit=initial_limit,
                )
            return self._service_states[service_name]

    def update_safe_limit(
        self,
        service_name: str,
        limit: int,
        force_save: bool = False,
    ) -> bool:
        """
        안전 limit 업데이트.

        주기적으로 또는 중요한 변경 시 Redis에 저장합니다.

        Args:
            service_name: 서비스 이름
            limit: 새 limit
            force_save: 강제 저장 여부

        Returns:
            Redis 저장 성공 여부
        """
        if not self.config.enabled:
            return False

        now = time.time()

        with self._lock:
            state = self.get_or_create_service_state(service_name, limit)
            state.last_known_safe_limit = limit

            # 저장 간격 확인
            should_save = force_save or (now - state.last_saved_at >= self.config.safe_limit_save_interval_seconds)

            if not should_save:
                return True

        # Redis에 저장 시도
        if self._redis is not None:
            try:
                limit_manager = self._get_limit_manager()
                if limit_manager:
                    success = limit_manager.save_safe_limit(service_name, limit)
                    if success:
                        with self._lock:
                            state.last_saved_at = now
                        return True
            except Exception as e:
                logger.error(
                    "safe_open_fallback.save_limit_failed",
                    error=e,
                )
                self._record_failure(service_name)

        return False

    def get_safe_limit(
        self,
        service_name: str,
        default_limit: int,
    ) -> tuple[int, str]:
        """
        현재 사용할 limit 가져오기.

        Redis가 정상이면 Redis에서, 아니면 로컬 캐시에서 가져옵니다.

        Args:
            service_name: 서비스 이름
            default_limit: 기본 limit

        Returns:
            (limit 값, 소스: "redis", "local_cache", "default")
        """
        if not self.config.enabled:
            return (default_limit, "default")

        state = self.get_or_create_service_state(service_name, default_limit)

        # Redis 상태 확인
        if self.check_redis_health():
            # Redis에서 로드 시도
            try:
                limit_manager = self._get_limit_manager()
                if limit_manager:
                    limit, source = limit_manager.load_safe_limit(
                        service_name,
                        default_limit,
                        self.config.safe_limit_max_age_seconds,
                    )
                    # 로컬 캐시 업데이트
                    with self._lock:
                        state.last_known_safe_limit = limit
                    return (limit, f"redis_{source.lower()}")
            except Exception as e:
                logger.warning(
                    "safe_open_fallback.redis_load_failed",
                    error=e,
                )
                self._record_failure(service_name)

        # Redis 장애 시 로컬 캐시 사용
        with self._lock:
            if state.last_known_safe_limit > 0:
                logger.debug(
                    f"[SafeOpenFallback] Using local cache: " f"service={service_name}, limit={state.last_known_safe_limit}"
                )
                return (state.last_known_safe_limit, "local_cache")

        # 최후의 폴백
        return (self.config.default_fallback_limit, "default")

    def _record_failure(self, service_name: str) -> None:
        """실패 기록."""
        with self._lock:
            state = self._service_states.get(service_name)
            if state:
                state.consecutive_failures += 1

                if state.consecutive_failures >= self.config.failure_threshold:
                    state.redis_state = RedisConnectionState.DISCONNECTED

    def get_redis_state(self) -> RedisConnectionState:
        """글로벌 Redis 상태 조회."""
        return self._global_redis_state

    def get_service_state(self, service_name: str) -> dict[str, Any] | None:
        """서비스별 상태 조회."""
        with self._lock:
            state = self._service_states.get(service_name)
            if state is None:
                return None

            return {
                "service_name": state.service_name,
                "last_known_safe_limit": state.last_known_safe_limit,
                "last_saved_at": state.last_saved_at,
                "redis_state": state.redis_state.value,
                "consecutive_failures": state.consecutive_failures,
            }

    def get_all_service_states(self) -> list[dict[str, Any]]:
        """모든 서비스 상태 조회."""
        with self._lock:
            return [self.get_service_state(name) for name in self._service_states if self.get_service_state(name) is not None]

    def force_disconnect(self) -> None:
        """강제 연결 끊김 시뮬레이션 (테스트용)."""
        self._on_redis_disconnected()

    def force_connect(self) -> None:
        """강제 연결 복구 시뮬레이션 (테스트용)."""
        self._on_redis_connected()

    def reset(self) -> None:
        """모든 상태 초기화 (테스트용)."""
        with self._lock:
            self._service_states.clear()
            self._global_redis_state = RedisConnectionState.CONNECTED
            self._last_health_check = 0.0


# =============================================================================
# Singleton
# =============================================================================

_safe_open_fallback_manager: SafeOpenFallbackManager | None = None
_fallback_lock = threading.Lock()


def get_safe_open_fallback_manager(
    config: SafeOpenConfig | None = None,
    redis_client: Any = None,
) -> SafeOpenFallbackManager:
    """전역 SafeOpenFallbackManager 인스턴스."""
    global _safe_open_fallback_manager

    if _safe_open_fallback_manager is None:
        with _fallback_lock:
            if _safe_open_fallback_manager is None:
                _safe_open_fallback_manager = SafeOpenFallbackManager(config, redis_client)

    return _safe_open_fallback_manager


def reset_safe_open_fallback_manager() -> None:
    """테스트용 리셋."""
    global _safe_open_fallback_manager

    with _fallback_lock:
        if _safe_open_fallback_manager:
            _safe_open_fallback_manager.reset()
        _safe_open_fallback_manager = None
