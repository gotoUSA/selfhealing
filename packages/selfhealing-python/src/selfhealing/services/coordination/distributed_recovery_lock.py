"""
Distributed Recovery Lock.

복구 프로세스의 분산 환경 동시성 제어를 위한 Redis 기반 락.

주요 기능:
- 네임스페이스당 하나의 복구 세션만 진행 보장
- 세션 ID 기반 소유자 식별
- 자동 만료로 좀비 락 방지
- 원자적 락 해제 (Lua 스크립트)

Code reference:
    canary/locking.py (CanaryConfigLock 패턴)
    adapters/cache/redis_adapter.py#L34-155 (RedisDistributedLock)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.3
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from selfhealing.settings import DistributedLockSettings, get_layered_settings

logger = logging.getLogger(__name__)


class RecoveryLockError(Exception):
    """
    복구 락 획득 실패 예외.

    동일 네임스페이스에서 이미 복구가 진행 중일 때 발생.

    Attributes:
        namespace: 네임스페이스
        current_owner: 현재 락 소유자 (세션 ID)
        message: 에러 메시지
    """

    def __init__(
        self,
        message: str,
        namespace: str = "",
        current_owner: str | None = None,
    ):
        self.namespace = namespace
        self.current_owner = current_owner
        super().__init__(message)


class DistributedRecoveryLock:
    """
    분산 복구 락.

    복구 프로세스의 동시 실행을 방지하기 위한 Redis 기반 분산 락.
    네임스페이스당 하나의 복구 세션만 진행되도록 보장합니다.

    Features:
        - Redis SET NX PX 기반 분산 락
        - 세션 ID 기반 소유자 식별
        - 자동 만료 (기본 30분)
        - TTL 연장 (하트비트)
        - 원자적 해제 (Lua 스크립트)

    Usage:
        lock = DistributedRecoveryLock(redis_client)

        # 기본 사용
        if lock.acquire("global", "recovery-abc123"):
            try:
                # 복구 수행
                perform_recovery()
            finally:
                lock.release("global", "recovery-abc123")

        # 컨텍스트 매니저 사용
        with lock.lock_for_recovery("global", "recovery-abc123") as acquired:
            if acquired:
                perform_recovery()

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.3
    """

    # Redis 키 패턴: {prefix}recovery:lock:{namespace}
    LOCK_KEY_TEMPLATE = "selfhealing:{namespace}:recovery:lock"

    # Lua 스크립트: 소유자 확인 후 삭제 (원자적)
    RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    # Lua 스크립트: 소유자 확인 후 TTL 연장 (원자적)
    EXTEND_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("pexpire", KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    @staticmethod
    def _get_default_lock_timeout() -> timedelta:
        """
        LayeredSettings에서 기본 락 타임아웃 가져오기.

        92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [17] DistributedLockSettings 참조.
        """
        settings = get_layered_settings(DistributedLockSettings, "distributed_lock")
        return timedelta(minutes=settings.timeout_minutes)

    def __init__(
        self,
        redis_client: Any | None = None,
        lock_timeout: timedelta | None = None,
    ):
        """
        DistributedRecoveryLock 초기화.

        Args:
            redis_client: Redis 클라이언트 인스턴스 (None이면 자동 획득)
            lock_timeout: 락 자동 만료 시간 (기본: DistributedLockSettings.timeout_minutes)
        """
        self._redis = redis_client
        self._lock_timeout = lock_timeout or self._get_default_lock_timeout()
        self._acquired_locks: dict[str, str] = {}  # namespace -> session_id
        self._local_lock = threading.Lock()

    def _get_redis(self) -> Any:
        """Redis 클라이언트 획득."""
        if self._redis is not None:
            return self._redis

        # StateBackend에서 Redis 클라이언트 획득 시도
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()

            # RedisStateBackend인 경우 _client 사용
            if hasattr(backend, "_client"):
                return backend._client
        except Exception:
            pass

        raise RuntimeError(
            "Redis client not available. "
            "Please provide redis_client or configure SELFHEALING_STATE_BACKEND=redis"
        )

    def _make_key(self, namespace: str) -> str:
        """락 키 생성."""
        return self.LOCK_KEY_TEMPLATE.format(namespace=namespace)

    def acquire(
        self,
        namespace: str,
        session_id: str,
        blocking: bool = False,
        timeout_seconds: int | None = None,
    ) -> bool:
        """
        복구 락 획득.

        Args:
            namespace: 네임스페이스 (예: "global", "seoul")
            session_id: 복구 세션 ID (소유자 식별용)
            blocking: True면 락 획득까지 대기 (기본 False, 권장하지 않음)
            timeout_seconds: blocking=True일 때 최대 대기 시간

        Returns:
            락 획득 성공 여부

        Note:
            blocking=True는 권장하지 않습니다.
            복구는 즉시 실패하고 운영자에게 알리는 것이 바람직합니다.
        """
        redis = self._get_redis()
        lock_key = self._make_key(namespace)
        timeout_ms = int(self._lock_timeout.total_seconds() * 1000)

        with self._local_lock:
            # Redis SET NX PX: 키가 없을 때만 설정 + 만료시간
            acquired = redis.set(
                lock_key,
                session_id,
                nx=True,
                px=timeout_ms,
            )

            if acquired:
                self._acquired_locks[namespace] = session_id
                logger.info(
                    f"[RecoveryLock] Acquired: namespace={namespace}, "
                    f"session={session_id}"
                )
                return True

        # 락 획득 실패
        current_owner = self.get_lock_owner(namespace)
        logger.warning(
            f"[RecoveryLock] Failed to acquire: namespace={namespace}, "
            f"session={session_id}, current_owner={current_owner}"
        )
        return False

    def release(
        self,
        namespace: str,
        session_id: str,
    ) -> bool:
        """
        복구 락 해제.

        락 소유자 확인 후 해제합니다 (Lua 스크립트로 원자적 처리).

        Args:
            namespace: 네임스페이스
            session_id: 복구 세션 ID (소유자 확인용)

        Returns:
            락 해제 성공 여부
        """
        redis = self._get_redis()
        lock_key = self._make_key(namespace)

        try:
            result = redis.eval(self.RELEASE_SCRIPT, 1, lock_key, session_id)

            with self._local_lock:
                if result == 1:
                    self._acquired_locks.pop(namespace, None)
                    logger.info(
                        f"[RecoveryLock] Released: namespace={namespace}, "
                        f"session={session_id}"
                    )
                    return True
                else:
                    logger.warning(
                        f"[RecoveryLock] Release failed (not owner or expired): "
                        f"namespace={namespace}, session={session_id}"
                    )
                    return False
        except Exception as e:
            logger.error(
                f"[RecoveryLock] Release error: namespace={namespace}, "
                f"session={session_id}, error={e}"
            )
            return False

    def extend(
        self,
        namespace: str,
        session_id: str,
        additional_seconds: int | None = None,
    ) -> bool:
        """
        락 TTL 연장 (하트비트).

        장기 실행 복구에서 락이 만료되지 않도록 TTL을 연장합니다.

        Args:
            namespace: 네임스페이스
            session_id: 복구 세션 ID (소유자 확인용)
            additional_seconds: 추가 TTL (None이면 기본 타임아웃)

        Returns:
            TTL 연장 성공 여부
        """
        redis = self._get_redis()
        lock_key = self._make_key(namespace)

        if additional_seconds is None:
            extend_ms = int(self._lock_timeout.total_seconds() * 1000)
        else:
            extend_ms = additional_seconds * 1000

        try:
            result = redis.eval(
                self.EXTEND_SCRIPT,
                1,
                lock_key,
                session_id,
                str(extend_ms),
            )

            if result == 1:
                logger.debug(
                    f"[RecoveryLock] Extended: namespace={namespace}, "
                    f"session={session_id}, ttl_ms={extend_ms}"
                )
                return True
            else:
                logger.warning(
                    f"[RecoveryLock] Extend failed (not owner or expired): "
                    f"namespace={namespace}, session={session_id}"
                )
                return False
        except Exception as e:
            logger.error(
                f"[RecoveryLock] Extend error: namespace={namespace}, "
                f"session={session_id}, error={e}"
            )
            return False

    def get_lock_owner(self, namespace: str) -> str | None:
        """
        현재 락 소유자 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            락 소유자 세션 ID 또는 None
        """
        try:
            redis = self._get_redis()
            lock_key = self._make_key(namespace)
            return redis.get(lock_key)
        except Exception as e:
            logger.error(
                f"[RecoveryLock] Get owner error: namespace={namespace}, " f"error={e}"
            )
            return None

    def is_locked(self, namespace: str) -> bool:
        """
        락 상태 확인.

        Args:
            namespace: 네임스페이스

        Returns:
            True if 락이 존재
        """
        return self.get_lock_owner(namespace) is not None

    def get_lock_ttl(self, namespace: str) -> int | None:
        """
        락 남은 TTL 조회 (초).

        Args:
            namespace: 네임스페이스

        Returns:
            남은 TTL (초) 또는 None (락 없음)
        """
        try:
            redis = self._get_redis()
            lock_key = self._make_key(namespace)
            ttl = redis.ttl(lock_key)
            return ttl if ttl > 0 else None
        except Exception as e:
            logger.error(
                f"[RecoveryLock] Get TTL error: namespace={namespace}, " f"error={e}"
            )
            return None

    @contextmanager
    def lock_for_recovery(
        self,
        namespace: str,
        session_id: str,
    ) -> Generator[bool, None, None]:
        """
        복구용 컨텍스트 매니저.

        락 획득 및 해제를 자동으로 처리합니다.

        Args:
            namespace: 네임스페이스
            session_id: 복구 세션 ID

        Yields:
            락 획득 성공 여부

        Example:
            with lock.lock_for_recovery("global", "recovery-abc") as acquired:
                if acquired:
                    perform_recovery()
                else:
                    # 다른 복구가 진행 중
                    pass
        """
        acquired = self.acquire(namespace, session_id)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(namespace, session_id)


# =============================================================================
# In-Memory Implementation (테스트용)
# =============================================================================


class InMemoryRecoveryLock:
    """
    인메모리 복구 락 (테스트용).

    Redis 없이 테스트할 때 사용합니다.
    주의: 프로세스 내에서만 유효하며, 분산 환경에서는 사용 불가.
    """

    def __init__(self):
        self._locks: dict[str, str] = {}  # namespace -> session_id
        self._lock = threading.Lock()

    def acquire(
        self,
        namespace: str,
        session_id: str,
        **kwargs,
    ) -> bool:
        """락 획득."""
        with self._lock:
            if namespace not in self._locks:
                self._locks[namespace] = session_id
                return True
            return False

    def release(
        self,
        namespace: str,
        session_id: str,
    ) -> bool:
        """락 해제."""
        with self._lock:
            if self._locks.get(namespace) == session_id:
                del self._locks[namespace]
                return True
            return False

    def extend(
        self,
        namespace: str,
        session_id: str,
        additional_seconds: int | None = None,
    ) -> bool:
        """락 연장 (테스트용, 항상 성공)."""
        with self._lock:
            return self._locks.get(namespace) == session_id

    def get_lock_owner(self, namespace: str) -> str | None:
        """락 소유자 조회."""
        with self._lock:
            return self._locks.get(namespace)

    def is_locked(self, namespace: str) -> bool:
        """락 상태 확인."""
        with self._lock:
            return namespace in self._locks

    def get_lock_ttl(self, namespace: str) -> int | None:
        """TTL 조회 (테스트용, 항상 None)."""
        return None

    @contextmanager
    def lock_for_recovery(
        self,
        namespace: str,
        session_id: str,
    ) -> Generator[bool, None, None]:
        """컨텍스트 매니저."""
        acquired = self.acquire(namespace, session_id)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(namespace, session_id)

    def clear(self) -> None:
        """모든 락 클리어 (테스트용)."""
        with self._lock:
            self._locks.clear()


# =============================================================================
# Factory
# =============================================================================

_lock_instance: DistributedRecoveryLock | None = None
_lock_instance_lock = threading.Lock()


def get_distributed_recovery_lock() -> DistributedRecoveryLock:
    """
    DistributedRecoveryLock 싱글톤 반환.

    Returns:
        DistributedRecoveryLock 인스턴스
    """
    global _lock_instance

    if _lock_instance is not None:
        return _lock_instance

    with _lock_instance_lock:
        if _lock_instance is None:
            _lock_instance = DistributedRecoveryLock()
        return _lock_instance


def reset_distributed_recovery_lock() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _lock_instance
    with _lock_instance_lock:
        _lock_instance = None
