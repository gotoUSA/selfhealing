"""
Canary Config Lock.

동시성 제어를 위한 Config Lock 메커니즘.
동일 config_type에 대해 하나의 롤아웃만 진행되도록 보장합니다.

Reference:
    - adapters/cache/redis_adapter.py#L34-155 (RedisDistributedLock)
    - docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

Usage:
    lock = CanaryConfigLock(redis_client)

    if not lock.acquire("circuit_breaker", "rollout-123"):
        raise ConfigLockError("Already in rollout")

    try:
        # 롤아웃 수행
        pass
    finally:
        lock.release("circuit_breaker", "rollout-123")
"""

from datetime import timedelta
from typing import Any

import structlog

logger = structlog.get_logger()


class ConfigLockError(Exception):
    """
    설정 락 획득 실패.

    동일 config_type에 대해 이미 롤아웃이 진행 중일 때 발생.

    Attributes:
        config_type: 설정 유형
        current_owner: 현재 락 소유자 (롤아웃 ID)
    """

    def __init__(
        self,
        message: str,
        config_type: str = "",
        current_owner: str | None = None,
    ):
        self.config_type = config_type
        self.current_owner = current_owner
        super().__init__(message)


class CanaryConfigLock:
    """
    Canary 롤아웃을 위한 설정 락.

    특정 config_type에 대해 하나의 롤아웃만 진행되도록 보장합니다.
    RedisDistributedLock을 활용하여 분산 환경에서도 안전하게 동작합니다.

    Features:
        - 분산 락 (Redis SET NX PX)
        - 롤아웃 ID 기반 소유자 식별
        - 자동 만료 (좀비 락 방지)
        - 락 상태 및 소유자 조회

    Example:
        lock = CanaryConfigLock(redis_client)

        # 락 획득
        if lock.acquire("circuit_breaker", "rollout-abc"):
            try:
                # 롤아웃 수행
                perform_rollout()
            finally:
                lock.release("circuit_breaker", "rollout-abc")
    """

    # Redis 키 패턴: {prefix}canary:lock:{config_type}
    LOCK_KEY_TEMPLATE = "{prefix}canary:lock:{config_type}"

    # 락 타임아웃: 롤아웃 최대 예상 시간
    DEFAULT_LOCK_TIMEOUT = timedelta(minutes=30)

    def __init__(
        self,
        redis_client: Any,
        lock_timeout: timedelta = None,
    ):
        """
        CanaryConfigLock 초기화.

        Args:
            redis_client: Redis 클라이언트 인스턴스
            lock_timeout: 락 자동 만료 시간 (기본 30분)
        """
        self._redis = redis_client
        self._lock_timeout = lock_timeout or self.DEFAULT_LOCK_TIMEOUT
        self._acquired_locks: dict[str, str] = {}  # config_type -> owner_id

    def acquire(
        self,
        config_type: str,
        rollout_id: str,
        blocking: bool = False,
    ) -> bool:
        """
        설정 락 획득.

        Args:
            config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
            rollout_id: 롤아웃 ID (소유자 식별용)
            blocking: True면 락 획득까지 대기 (기본 False)

        Returns:
            락 획득 성공 여부

        Note:
            blocking=True는 권장하지 않습니다.
            Canary 롤아웃은 즉시 실패하고 사용자에게 알리는 것이 바람직합니다.
        """
        from selfhealing.settings.namespace import get_key_prefix

        lock_key = self.LOCK_KEY_TEMPLATE.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        timeout_ms = int(self._lock_timeout.total_seconds() * 1000)

        # Redis SET NX PX: 키가 없을 때만 설정 + 만료시간
        acquired = self._redis.set(
            lock_key,
            rollout_id,
            nx=True,
            px=timeout_ms,
        )

        if acquired:
            self._acquired_locks[config_type] = rollout_id
            logger.info(
                "canary_lock.acquired",
                config_type=config_type,
                rollout_id=rollout_id,
            )
            return True

        # 락 획득 실패
        current_owner = self.get_lock_owner(config_type)
        logger.warning(
            "canary_lock.failed_acquire",
            config_type=config_type,
            rollout_id=rollout_id,
            current_owner=current_owner,
        )
        return False

    def release(
        self,
        config_type: str,
        rollout_id: str,
    ) -> bool:
        """
        설정 락 해제.

        락 소유자 확인 후 해제합니다 (Lua 스크립트로 원자적 처리).

        Args:
            config_type: 설정 유형
            rollout_id: 롤아웃 ID (소유자 확인용)

        Returns:
            락 해제 성공 여부
        """
        from selfhealing.settings.namespace import get_key_prefix

        lock_key = self.LOCK_KEY_TEMPLATE.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        # Lua 스크립트: 소유자 확인 후 삭제 (원자적)
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            result = self._redis.eval(lua_script, 1, lock_key, rollout_id)

            if result == 1:
                self._acquired_locks.pop(config_type, None)
                logger.info(
                    "canary_lock.released",
                    config_type=config_type,
                    rollout_id=rollout_id,
                )
                return True
            else:
                logger.warning(
                    "canary_lock.release_failed_owner_expired",
                    config_type=config_type,
                    rollout_id=rollout_id,
                )
                return False

        except Exception as e:
            logger.exception(
                "canary_lock.release_error",
                error=e,
            )
            return False

    def is_locked(self, config_type: str) -> bool:
        """
        락 상태 확인.

        Args:
            config_type: 설정 유형

        Returns:
            락이 걸려있으면 True
        """
        from selfhealing.settings.namespace import get_key_prefix

        lock_key = self.LOCK_KEY_TEMPLATE.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        return self._redis.exists(lock_key) > 0

    def get_lock_owner(self, config_type: str) -> str | None:
        """
        현재 락 소유자 조회.

        Args:
            config_type: 설정 유형

        Returns:
            락 소유자 (롤아웃 ID) 또는 None
        """
        from selfhealing.settings.namespace import get_key_prefix

        lock_key = self.LOCK_KEY_TEMPLATE.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        owner = self._redis.get(lock_key)

        if owner and isinstance(owner, bytes):
            owner = owner.decode("utf-8")

        return owner

    def extend(
        self,
        config_type: str,
        rollout_id: str,
        additional_time: timedelta = None,
    ) -> bool:
        """
        락 TTL 연장.

        롤아웃이 예상보다 오래 걸릴 때 락 만료를 방지합니다.

        Args:
            config_type: 설정 유형
            rollout_id: 롤아웃 ID (소유자 확인용)
            additional_time: 연장할 시간 (기본값: 초기 타임아웃과 동일)

        Returns:
            연장 성공 여부
        """
        from selfhealing.settings.namespace import get_key_prefix

        lock_key = self.LOCK_KEY_TEMPLATE.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        extend_time = additional_time or self._lock_timeout
        extend_ms = int(extend_time.total_seconds() * 1000)

        # Lua 스크립트: 소유자 확인 후 TTL 연장 (원자적)
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("pexpire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """

        try:
            result = self._redis.eval(lua_script, 1, lock_key, rollout_id, extend_ms)

            if result == 1:
                logger.info(
                    "canary_lock.extended",
                    config_type=config_type,
                    rollout_id=rollout_id,
                    extend_time=extend_time,
                )
                return True
            else:
                logger.warning(
                    "canary_lock.extend_failed",
                    config_type=config_type,
                    rollout_id=rollout_id,
                )
                return False

        except Exception as e:
            logger.exception(
                "canary_lock.extend_error",
                error=e,
            )
            return False

    def force_release(self, config_type: str) -> bool:
        """
        강제 락 해제 (관리자 전용).

        좀비 롤아웃 정리용. 소유자 확인 없이 락을 삭제합니다.

        Args:
            config_type: 설정 유형

        Returns:
            삭제 성공 여부

        Warning:
            이 메서드는 관리자 권한이 있는 경우에만 사용해야 합니다.
            일반적인 상황에서는 release()를 사용하세요.
        """
        from selfhealing.settings.namespace import get_key_prefix

        lock_key = self.LOCK_KEY_TEMPLATE.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        try:
            result = self._redis.delete(lock_key)
            logger.warning(
                "canary_lock.force_released",
                config_type=config_type,
                result=result,
            )
            return result > 0

        except Exception as e:
            logger.exception(
                "canary_lock.force_release_error",
                error=e,
            )
            return False
