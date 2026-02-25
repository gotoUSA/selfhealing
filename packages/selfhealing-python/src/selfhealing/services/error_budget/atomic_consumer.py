"""
Atomic Budget Consumer.

Redis Lock 기반으로 버짓 소진의 원자성을 보장합니다.
동시 요청 시 Race Condition을 방지하여 정확한 버짓 관리를 지원합니다.

Features:
- Redis Lock 기반 원자적 소진
- Degraded Mode 지원 (Lock 실패 시)
- 소진 결과 반환

Usage:
    from selfhealing.services.error_budget.atomic_consumer import (
        AtomicBudgetConsumer,
        AtomicConsumeResult,
    )

    consumer = AtomicBudgetConsumer(redis_client=redis)
    result = consumer.consume_atomic(
        namespace="seoul",
        raw_minutes=1.0,
        multiplier=5.0,
        budget_key="budget:seoul",
    )

    if result.success:
        print(f"Consumed: {result.consumed_minutes} minutes")

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (7번)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from selfhealing.core.timezone import now as utc_now

logger = structlog.get_logger()


# =============================================================================
# Constants
# =============================================================================

DEFAULT_LOCK_TIMEOUT_SECONDS: float = 5.0
"""기본 Lock 타임아웃 (5초)."""

DEFAULT_LOCK_RETRY_COUNT: int = 3
"""Lock 획득 재시도 횟수."""

DEFAULT_LOCK_RETRY_DELAY_SECONDS: float = 0.1
"""Lock 재시도 대기 시간."""


# =============================================================================
# Consume Result
# =============================================================================


@dataclass
class AtomicConsumeResult:
    """
    원자적 버짓 소진 결과.

    Attributes:
        success: 소진 성공 여부
        lock_acquired: Lock 획득 여부
        consumed_minutes: 소진된 버짓 (분)
        remaining_budget: 남은 버짓 (분)
        consume_id: 소진 ID
        consumed_at: 소진 시각
        error_message: 실패 시 오류 메시지
    """

    success: bool = False
    """소진 성공 여부."""

    lock_acquired: bool = False
    """Lock 획득 여부."""

    consumed_minutes: float = 0.0
    """소진된 버짓 (분)."""

    remaining_budget: float | None = None
    """남은 버짓 (분)."""

    consume_id: str = field(default_factory=lambda: f"consume_{uuid.uuid4().hex[:12]}")
    """소진 ID."""

    consumed_at: datetime = field(default_factory=utc_now)
    """소진 시각."""

    error_message: str | None = None
    """실패 시 오류 메시지."""

    degraded_mode: bool = False
    """Degraded Mode 사용 여부."""


# =============================================================================
# Atomic Budget Consumer
# =============================================================================


class AtomicBudgetConsumer:
    """
    원자적 버짓 소진기.

    Redis Lock을 사용하여 동시 요청 시에도
    Race Condition 없이 정확한 버짓 소진을 보장합니다.

    Features:
    - Redis Lock 기반 원자적 연산
    - Lock 실패 시 Degraded Mode 지원
    - 재시도 로직

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (7번)
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        lock_retry_count: int = DEFAULT_LOCK_RETRY_COUNT,
        lock_retry_delay: float = DEFAULT_LOCK_RETRY_DELAY_SECONDS,
        allow_degraded_mode: bool = True,
    ):
        """
        AtomicBudgetConsumer 초기화.

        Args:
            redis_client: Redis 클라이언트 (None이면 lazy loading)
            lock_timeout: Lock 타임아웃 (초)
            lock_retry_count: Lock 획득 재시도 횟수
            lock_retry_delay: Lock 재시도 대기 시간 (초)
            allow_degraded_mode: Lock 실패 시 Degraded Mode 허용 여부
        """
        self._redis_client = redis_client
        self._lock_timeout = lock_timeout
        self._lock_retry_count = lock_retry_count
        self._lock_retry_delay = lock_retry_delay
        self._allow_degraded_mode = allow_degraded_mode

    def _get_redis_client(self) -> Any | None:
        """Redis 클라이언트 획득 (lazy loading)."""
        if self._redis_client is None:
            try:
                from selfhealing.adapters.cache import get_redis_client

                self._redis_client = get_redis_client()
            except ImportError:
                logger.warning("atomic_consumer.redis_client_available")
        return self._redis_client

    def consume_atomic(
        self,
        namespace: str,
        raw_minutes: float,
        multiplier: float,
        budget_key: str,
    ) -> AtomicConsumeResult:
        """
        원자적 버짓 소진.

        Args:
            namespace: 네임스페이스
            raw_minutes: 원시 소진량 (분)
            multiplier: 적용할 가중치
            budget_key: 버짓 Redis 키

        Returns:
            AtomicConsumeResult: 소진 결과
        """
        weighted_minutes = raw_minutes * multiplier
        lock_key = f"{budget_key}:lock"

        # Redis 클라이언트 확인
        redis = self._get_redis_client()
        if redis is None:
            # Redis 없음: Degraded Mode로 진행
            if self._allow_degraded_mode:
                return self._consume_degraded(
                    weighted_minutes=weighted_minutes,
                    reason="Redis client not available",
                )
            else:
                return AtomicConsumeResult(
                    success=False,
                    error_message="Redis client not available and degraded mode disabled",
                )

        # Lock 획득 시도
        lock_acquired = False
        lock_value = uuid.uuid4().hex

        for attempt in range(self._lock_retry_count):
            try:
                # SET NX EX 패턴으로 Lock 획득
                acquired = redis.set(
                    lock_key,
                    lock_value,
                    nx=True,
                    ex=int(self._lock_timeout),
                )

                if acquired:
                    lock_acquired = True
                    break

                # 재시도 대기
                time.sleep(self._lock_retry_delay)

            except Exception as e:
                logger.warning(
                    "atomic_consumer.lock_attempt_failed",
                    attempt_number=attempt + 1,
                    error=e,
                )

        if not lock_acquired:
            if self._allow_degraded_mode:
                return self._consume_degraded(
                    weighted_minutes=weighted_minutes,
                    reason="Lock acquisition failed",
                )
            else:
                return AtomicConsumeResult(
                    success=False,
                    lock_acquired=False,
                    error_message="Failed to acquire lock and degraded mode disabled",
                )

        try:
            # 원자적 버짓 소진
            result = self._execute_consume(
                redis=redis,
                budget_key=budget_key,
                weighted_minutes=weighted_minutes,
            )
            result.lock_acquired = True
            return result

        finally:
            # Lock 해제 (본인이 획득한 경우만)
            self._release_lock(redis, lock_key, lock_value)

    def _execute_consume(
        self,
        redis: Any,
        budget_key: str,
        weighted_minutes: float,
    ) -> AtomicConsumeResult:
        """실제 버짓 소진 실행."""
        try:
            # 현재 소진량 조회
            current = redis.get(budget_key)
            current_consumed = float(current) if current else 0.0

            # 소진량 증가
            new_consumed = current_consumed + weighted_minutes
            redis.set(budget_key, str(new_consumed))

            logger.debug(
                "atomic_consumer.consumed",
                current_consumed=current_consumed,
                weighted_minutes=weighted_minutes,
                new_consumed=new_consumed,
            )

            return AtomicConsumeResult(
                success=True,
                consumed_minutes=weighted_minutes,
                remaining_budget=None,  # 별도 조회 필요
            )

        except Exception as e:
            logger.exception(
                "atomic_consumer.consume_failed",
                error=e,
            )
            return AtomicConsumeResult(
                success=False,
                error_message=str(e),
            )

    def _release_lock(
        self,
        redis: Any,
        lock_key: str,
        lock_value: str,
    ) -> None:
        """Lock 해제 (본인이 획득한 경우만)."""
        try:
            # Lua 스크립트로 원자적 해제
            lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            redis.eval(lua_script, 1, lock_key, lock_value)
        except Exception as e:
            logger.warning(
                "atomic_consumer.lock_release_failed",
                error=e,
            )

    def _consume_degraded(
        self,
        weighted_minutes: float,
        reason: str,
    ) -> AtomicConsumeResult:
        """
        Degraded Mode 소진.

        Lock 없이 진행하되, 결과에 표시합니다.
        """
        logger.warning(
            "atomic_consumer.degraded_mode_consuming_minutes",
            reason=reason,
            weighted_minutes=weighted_minutes,
        )

        return AtomicConsumeResult(
            success=True,
            lock_acquired=False,
            consumed_minutes=weighted_minutes,
            degraded_mode=True,
            error_message=f"Degraded mode: {reason}",
        )


# =============================================================================
# Singleton
# =============================================================================

_atomic_consumer: AtomicBudgetConsumer | None = None


def get_atomic_budget_consumer() -> AtomicBudgetConsumer:
    """AtomicBudgetConsumer 싱글톤 반환."""
    global _atomic_consumer
    if _atomic_consumer is None:
        _atomic_consumer = AtomicBudgetConsumer()
    return _atomic_consumer


def reset_atomic_budget_consumer() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _atomic_consumer
    _atomic_consumer = None
