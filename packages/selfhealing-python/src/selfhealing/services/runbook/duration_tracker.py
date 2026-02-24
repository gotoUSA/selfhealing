"""
min_duration_seconds 추적 — Redis 또는 인메모리 상태 관리.

조건이 최초 충족된 시점을 기록하고, 지속 시간이 min_duration_seconds를
초과하는지 확인한다. Redis 사용 시 SET NX 패턴으로 원자적 기록을 보장하고,
Redis가 없는 환경에서는 인메모리 딕셔너리로 대체한다.

DistributedRecoveryLock의 Redis SET NX PX 패턴과 동일한 원리.

Reference:
    docs/self_healing/middleware_system/273_RUNBOOK_PATTERN_MATCHER.md §4.4
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger()


@runtime_checkable
class RedisLike(Protocol):
    """Redis 클라이언트 최소 인터페이스."""

    def set(self, name: str, value: Any, *, nx: bool = False, ex: int | None = None) -> Any: ...
    def get(self, name: str) -> Any: ...
    def delete(self, *names: str) -> int: ...


class DurationTracker:
    """min_duration_seconds 추적.

    Redis 사용 가능 시 분산 환경에서 상태를 공유하고,
    Redis 미사용 시 인메모리 딕셔너리로 단일 프로세스 내에서 동작한다.
    """

    KEY_TEMPLATE = "selfhealing:pattern:{runbook_id}:condition_met_at"
    """Redis 키 템플릿. TTL을 포함하여 자동 정리된다."""

    EXTRA_TTL_SECONDS = 30
    """자동 만료까지 여유 시간 (초)"""

    def __init__(self, redis_client: RedisLike | None = None) -> None:
        """
        Args:
            redis_client: Redis 클라이언트. None이면 인메모리 모드로 동작.
        """
        self._redis = redis_client
        self._memory_store: dict[str, float] = {}
        self._lock = threading.Lock()

    def record_condition_met(self, runbook_id: str, duration_seconds: int) -> None:
        """조건 최초 충족 시점 기록.

        이미 기록이 있으면 무시하여 최초 충족 시점을 보존한다.

        Args:
            runbook_id: 런북 ID
            duration_seconds: 필요한 지속 시간 (TTL 계산에 사용)
        """
        now = time.time()

        if self._redis is not None:
            key = self.KEY_TEMPLATE.format(runbook_id=runbook_id)
            ttl = duration_seconds * 2 + self.EXTRA_TTL_SECONDS
            self._redis.set(key, str(now), nx=True, ex=ttl)
            logger.debug(
                "duration_tracker.recorded",
                runbook_id=runbook_id,
                ttl=ttl,
            )
        else:
            with self._lock:
                if runbook_id not in self._memory_store:
                    self._memory_store[runbook_id] = now
                    logger.debug(
                        "duration_tracker.recorded_memory",
                        runbook_id=runbook_id,
                    )

    def check_duration_met(self, runbook_id: str, min_duration: int) -> bool:
        """지속 시간 충족 여부 확인.

        Args:
            runbook_id: 런북 ID
            min_duration: 필요한 최소 지속 시간 (초)

        Returns:
            True이면 지속 시간 충족
        """
        if self._redis is not None:
            key = self.KEY_TEMPLATE.format(runbook_id=runbook_id)
            raw = self._redis.get(key)
            if raw is None:
                return False
            first_met_at = float(raw)
        else:
            with self._lock:
                first_met_at = self._memory_store.get(runbook_id)  # type: ignore[assignment]
                if first_met_at is None:
                    return False

        elapsed = time.time() - first_met_at
        return elapsed >= min_duration

    def clear_condition(self, runbook_id: str) -> None:
        """조건 해소 시 기록 삭제 (grace_period 후 호출).

        Args:
            runbook_id: 런북 ID
        """
        if self._redis is not None:
            key = self.KEY_TEMPLATE.format(runbook_id=runbook_id)
            self._redis.delete(key)
            logger.debug("duration_tracker.cleared", runbook_id=runbook_id)
        else:
            with self._lock:
                self._memory_store.pop(runbook_id, None)
                logger.debug("duration_tracker.cleared_memory", runbook_id=runbook_id)
