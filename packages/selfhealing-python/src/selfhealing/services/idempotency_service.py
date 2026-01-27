"""
Idempotency Service

Provides idempotency key management for safe retry operations.
Ensures that retried operations produce the same result as the original.

Idempotency Key Format Examples (domain-neutral):
- Operation: `entity_type` + `entity_id` + `action`
- Event: `event_id` or `event_type:entity_id`
- Resource: `resource_type` + `resource_id` + `operation`

Usage:
    service = IdempotencyService()
    key = IdempotencyKey.for_operation("order", 123, "process")
    result = service.check(key, lookup_fn)

멱등성 키 관리로 안전한 재시도 연산을 보장합니다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from selfhealing.core.time_provider import TimeProvider, get_time_provider
from selfhealing.settings import get_config

if TYPE_CHECKING:
    from selfhealing.core.time_provider import TimeProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")


class IdempotencyDomain(Enum):
    """Domains that support idempotency checking (domain-neutral)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # 기존 도메인
    # ═══════════════════════════════════════════════════════════════════════════
    EXTERNAL_SERVICE = "external_service"
    """외부 서비스 호출 (결제, 알림 등)."""

    INTERNAL_PROCESS = "internal_process"
    """내부 프로세스 (재고 차감, 포인트 적립 등)."""

    ASYNC_TASK = "async_task"
    """비동기 작업 (Celery Task 등)."""

    EVENT = "event"
    """이벤트 처리 (Webhook, 메시지 등)."""

    CUSTOM = "custom"
    """커스텀 도메인."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Chaos Engineering 관련 도메인
    # Chaos 실험 실행 및 좀비 헌터 분산 락 관리
    # ═══════════════════════════════════════════════════════════════════════════
    CHAOS_EXPERIMENT = "chaos_experiment"
    """Chaos 실험 실행 (동일 실험 중복 실행 방지)."""

    CHAOS_ZOMBIE_HUNTER = "chaos_zombie_hunter"
    """Zombie Hunter 분산 락 (고아 실험 중복 rollback 방지).

    고아 상태의 실험을 감지하고 안전하게 정리합니다.
    """

    # ═══════════════════════════════════════════════════════════════════════════
    # 설정 관리 관련 (순위 4 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════
    CONFIG_CHANGE = "config_change"
    """설정 변경 (동일 변경 중복 적용 방지)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # 저장소 동기화 관련 (순위 4 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════
    L2_SYNC = "l2_sync"
    """L2 저장소 동기화 (복구 후 재동기화 중복 방지)."""

    WAL_RECOVERY = "wal_recovery"
    """WAL 복구 (동일 엔트리 중복 처리 방지)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Auto Tuning 관련 (순위 4 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════
    AUTO_ADJUSTMENT = "auto_adjustment"
    """자율 조정 (동일 조정 중복 적용 방지)."""


@dataclass
class IdempotencyKey:
    """
    Represents an idempotency key with domain context.

    The key is a combination of domain-specific identifiers that
    uniquely identify an operation.
    """

    domain: IdempotencyDomain
    key: str
    components: dict[str, Any]

    @property
    def cache_key(self) -> str:
        """Get the cache key for Redis/memcached storage."""
        return f"idempotency:{self.domain.value}:{self.key}"

    @property
    def hash(self) -> str:
        """Get a hash of the key for indexing."""
        return hashlib.sha256(self.cache_key.encode()).hexdigest()[:32]

    @classmethod
    def for_operation(
        cls,
        entity_type: str,
        entity_id: int,
        operation: str,
        domain: IdempotencyDomain = IdempotencyDomain.EXTERNAL_SERVICE,
    ) -> IdempotencyKey:
        """
        Create an idempotency key for a generic operation.

        Args:
            entity_type: Type of entity (e.g., "order", "user", "product")
            entity_id: The entity ID
            operation: The operation being performed (e.g., "process", "update")
            domain: The domain category

        Returns:
            IdempotencyKey for the operation
        """
        key = f"{entity_type}:{entity_id}:{operation}"
        return cls(
            domain=domain,
            key=key,
            components={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "operation": operation,
            },
        )

    @classmethod
    def for_event(cls, event_id: str) -> IdempotencyKey:
        """
        Create an idempotency key for event processing.

        Args:
            event_id: The unique event ID

        Returns:
            IdempotencyKey for the event
        """
        return cls(
            domain=IdempotencyDomain.EVENT,
            key=event_id,
            components={"event_id": event_id},
        )

    @classmethod
    def for_resource_action(
        cls,
        resource_type: str,
        resource_id: int,
        action: str,
        amount: int | None = None,
    ) -> IdempotencyKey:
        """
        Create an idempotency key for resource actions.

        Args:
            resource_type: Type of resource
            resource_id: The resource ID
            action: The action being performed
            amount: Optional amount for the action

        Returns:
            IdempotencyKey for the resource action
        """
        if amount is not None:
            key = f"{resource_type}:{resource_id}:{action}:{amount}"
        else:
            key = f"{resource_type}:{resource_id}:{action}"
        return cls(
            domain=IdempotencyDomain.INTERNAL_PROCESS,
            key=key,
            components={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
                "amount": amount,
            },
        )

    @classmethod
    def custom(cls, key: str, **components: Any) -> IdempotencyKey:
        """
        Create a custom idempotency key.

        Args:
            key: The raw key string
            **components: Key components for debugging

        Returns:
            IdempotencyKey with custom domain
        """
        return cls(
            domain=IdempotencyDomain.CUSTOM,
            key=key,
            components=components,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 신규 팩토리 메서드 (순위 5 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def for_chaos_experiment(
        cls,
        schedule_id: str,
        experiment_type: str,
        target_service: str,
    ) -> IdempotencyKey:
        """
        Chaos 실험에 대한 멱등성 키 생성.

        동일 스케줄의 실험이 동시에 실행되는 것을 방지.

        Args:
            schedule_id: 스케줄 ID
            experiment_type: 실험 유형 (예: latency_injection, fault_injection)
            target_service: 대상 서비스

        Returns:
            IdempotencyKey for chaos experiment
        """
        key = f"chaos:{schedule_id}:{experiment_type}:{target_service}"
        return cls(
            domain=IdempotencyDomain.CHAOS_EXPERIMENT,
            key=key,
            components={
                "schedule_id": schedule_id,
                "experiment_type": experiment_type,
                "target_service": target_service,
            },
        )

    @classmethod
    def for_chaos_service_lock(
        cls,
        target_service: str,
    ) -> IdempotencyKey:
        """
        서비스 단위 Chaos 실험 락.

        동일 서비스에 2개 이상의 실험이 동시 실행되는 것을 방지.
        (원인 분석 명확성 확보)

        Schedule 락과 함께 사용하는 이중 락 패턴:
        - Service Lock: 동시성 제어 (실험 종료 시 해제)
        - Schedule Lock: 재실행 방지 (TTL까지 유지)

        Args:
            target_service: 대상 서비스명

        Returns:
            IdempotencyKey for service-level chaos lock

        Usage:
            # 이중 락 패턴 사용 예시
            service_lock = IdempotencyKey.for_chaos_service_lock(target_service)
            schedule_lock = IdempotencyKey.for_chaos_experiment(schedule_id, exp_type, target_service)

            # 1. 서비스 락 획득 (동시성 제어)
            if not idempotency.acquire_lock(service_lock, ttl_seconds=7200):
                return "다른 실험 실행 중"

            # 2. 스케줄 락 획득 (재실행 방지)
            if not idempotency.acquire_lock(schedule_lock, ttl_seconds=86400):
                idempotency.release_lock(service_lock)  # 롤백
                return "이미 실행된 스케줄"

            try:
                # 3. 실험 실행
                execute_experiment()
            finally:
                # 4. 서비스 락만 해제 (스케줄 락은 TTL 유지)
                idempotency.release_lock(service_lock)

        """
        key = f"chaos:service_lock:{target_service}"
        return cls(
            domain=IdempotencyDomain.CHAOS_EXPERIMENT,
            key=key,
            components={
                "lock_type": "service_level",
                "target_service": target_service,
            },
        )

    @classmethod
    def for_config_change(
        cls,
        config_key: str,
        new_value_hash: str,
        changed_by: str,
        request_id: str | None = None,
        window_id: str | None = None,
    ) -> IdempotencyKey:
        """
        설정 변경에 대한 멱등성 키 생성.

        동일 설정 변경이 중복 적용되는 것을 방지.

        Args:
            config_key: 설정 키
            new_value_hash: 새 값의 해시
            changed_by: 변경 주체
            request_id: 요청 ID (동일 요청 재시도만 중복 처리)
            window_id: 슬라이딩 윈도우 ID (시간 창 기반, 권장)

        멱등성 범위 정책:
        - request_id 제공: 동일 요청의 재시도만 중복
        - window_id 제공: 동일 윈도우 내 동일 변경만 중복
        - 둘 다 없음: new_value_hash 기준 (기존 동작)

        Returns:
            IdempotencyKey for config change

        Reference: Architect Review - "의도된 재설정 vs 중복 구분"
        """
        if request_id:
            # 요청 단위 멱등성 (가장 엄격)
            key = f"config:{config_key}:{request_id}"
        elif window_id:
            # 슬라이딩 윈도우 기반 (권장)
            key = f"config:{config_key}:{new_value_hash}:w{window_id}"
        else:
            # 기존 동작 (값 기반)
            key = f"config:{config_key}:{new_value_hash}"

        return cls(
            domain=IdempotencyDomain.CONFIG_CHANGE,
            key=key,
            components={
                "config_key": config_key,
                "new_value_hash": new_value_hash,
                "changed_by": changed_by,
                "request_id": request_id,
                "window_id": window_id,
            },
        )

    @classmethod
    def for_l2_sync(
        cls,
        service_name: str,
        record_id: str,
        intended_state: str,
    ) -> IdempotencyKey:
        """
        L2 동기화에 대한 멱등성 키 생성.

        복구 후 동일 레코드가 중복 동기화되는 것을 방지.

        Args:
            service_name: 서비스 이름
            record_id: 레코드 ID
            intended_state: 목표 상태

        Returns:
            IdempotencyKey for L2 sync
        """
        key = f"l2sync:{service_name}:{record_id}"
        return cls(
            domain=IdempotencyDomain.L2_SYNC,
            key=key,
            components={
                "service_name": service_name,
                "record_id": record_id,
                "intended_state": intended_state,
            },
        )

    @classmethod
    def for_wal_recovery(
        cls,
        wal_entry_id: str,
        operation: str,
    ) -> IdempotencyKey:
        """
        WAL 복구에 대한 멱등성 키 생성.

        동일 WAL 엔트리가 중복 처리되는 것을 방지.

        Args:
            wal_entry_id: WAL 엔트리 ID
            operation: 복구 작업 유형

        Returns:
            IdempotencyKey for WAL recovery
        """
        key = f"wal:{wal_entry_id}:{operation}"
        return cls(
            domain=IdempotencyDomain.WAL_RECOVERY,
            key=key,
            components={
                "wal_entry_id": wal_entry_id,
                "operation": operation,
            },
        )

    @classmethod
    def for_auto_adjustment(
        cls,
        module: str,
        parameter: str,
        target_value: str,
    ) -> IdempotencyKey:
        """
        자율 조정에 대한 멱등성 키 생성.

        동일 조정이 중복 적용되는 것을 방지.

        Args:
            module: 모듈 이름 (circuit_breaker, retry 등)
            parameter: 파라미터 이름
            target_value: 목표 값

        Returns:
            IdempotencyKey for auto adjustment

        Note:
            플래핑 체크는 AntiFlappingWindow를 별도로 사용하세요.
            get_anti_flapping_window().check_and_record(...)
        """
        key = f"adjust:{module}:{parameter}:{target_value}"
        return cls(
            domain=IdempotencyDomain.AUTO_ADJUSTMENT,
            key=key,
            components={
                "module": module,
                "parameter": parameter,
                "target_value": target_value,
            },
        )


@dataclass
class IdempotencyResult(Generic[T]):
    """Result of an idempotency check."""

    is_duplicate: bool
    existing_record: T | None = None
    message: str = ""

    @property
    def should_proceed(self) -> bool:
        """Whether the operation should proceed (not a duplicate)."""
        return not self.is_duplicate


class IdempotencyService:
    """
    Service for checking and managing idempotency of operations.

    Provides both cache-based (fast) and database-based (reliable)
    idempotency checking.

    For framework-agnostic usage, provide lookup callbacks when calling check().

    Example:
        # Framework-agnostic usage with TimeProvider
        from selfhealing.core.time_provider import MockTimeProvider

        service = IdempotencyService(time_provider=MockTimeProvider())
        key = IdempotencyKey.for_operation("order", 123, "process")
        result = service.check(key, lookup_fn=my_lookup)
    """

    def __init__(
        self,
        cache_ttl: int | None = None,
        time_provider: TimeProvider | None = None,
        clock_skew_tolerance_seconds: float | None = None,
    ):
        """
        Initialize the idempotency service.

        Args:
            cache_ttl: Custom cache TTL in seconds
            time_provider: TimeProvider for testable time operations
            clock_skew_tolerance_seconds: Clock skew tolerance for distributed checks
        """

        config = get_config()
        self._default_cache_ttl = config.idempotency.default_cache_ttl
        self._extended_cache_ttl = config.idempotency.extended_cache_ttl
        self.cache_ttl = cache_ttl or self._default_cache_ttl

        # Clock skew tolerance
        self._clock_skew_tolerance = (
            clock_skew_tolerance_seconds
            if clock_skew_tolerance_seconds is not None
            else config.idempotency.clock_skew_tolerance_seconds
        )
        self._time_provider: TimeProvider = time_provider or get_time_provider()
        self._cache = None  # Lazy initialized

    def _get_cache(self):
        """Django cache 인터페이스를 lazy load합니다."""
        if self._cache is None:
            try:
                from django.core.cache import cache

                self._cache = cache
            except ImportError:
                # Django not available - use noop cache
                class NoopCache:
                    def get(self, key):
                        return None

                    def set(self, key, value, timeout=None):
                        pass

                    def delete(self, key):
                        pass

                self._cache = NoopCache()
        return self._cache

    @property
    def DEFAULT_CACHE_TTL(self) -> int:
        """Default TTL for cache-based idempotency."""
        return self._default_cache_ttl

    @property
    def EXTENDED_CACHE_TTL(self) -> int:
        """Extended TTL for operations requiring longer TTL."""
        return self._extended_cache_ttl

    @property
    def clock_skew_tolerance(self) -> float:
        """Clock skew tolerance in seconds for distributed checks."""
        return self._clock_skew_tolerance

    @property
    def time_provider(self) -> TimeProvider:
        """Get the time provider for this service."""
        return self._time_provider

    def now(self) -> datetime:
        """
        Get current time using the configured TimeProvider.

        Returns:
            Current datetime from time provider
        """
        return self._time_provider.now()

    def is_timestamp_valid(
        self,
        timestamp: datetime,
        tolerance_seconds: float | None = None,
    ) -> bool:
        """
        Check if a timestamp is within acceptable clock skew tolerance.

        Useful for validating incoming events or API requests where
        the timestamp may differ due to clock skew between systems.

        Args:
            timestamp: The timestamp to validate
            tolerance_seconds: Override tolerance (uses config default if None)

        Returns:
            True if timestamp is within tolerance of current time
        """

        tolerance = tolerance_seconds if tolerance_seconds is not None else self._clock_skew_tolerance
        return self._time_provider.is_within_tolerance(
            timestamp,
            timedelta(seconds=tolerance),
        )

    def check(
        self,
        key: IdempotencyKey,
        lookup_fn: Callable[..., Any] | None = None,
        cache_ttl: int | None = None,
    ) -> IdempotencyResult:
        """
        Check if an operation has already been processed.

        Args:
            key: The idempotency key to check
            lookup_fn: Optional callback to check database
            cache_ttl: Optional custom TTL for cache

        Returns:
            IdempotencyResult with duplicate status

        Note:
            Gracefully degrades to DB-only check if Redis is unavailable.
        """
        ttl = cache_ttl or self.cache_ttl
        cache = self._get_cache()

        # Check cache first (fast path) with graceful degradation
        try:
            cached_value = cache.get(key.cache_key)
            if cached_value:
                logger.debug(f"[Idempotency] Cache hit: {key.key}")
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_record=cached_value,
                    message="Found in cache",
                )
        except Exception as e:
            logger.warning(f"[Idempotency] Cache unavailable, falling back to DB: {e}")

        # Check database if lookup provided
        if lookup_fn:
            try:
                existing = lookup_fn(**key.components)
                if existing:
                    # Update cache for future lookups (best-effort)
                    try:
                        record_id = getattr(existing, "id", existing)
                        cache.set(key.cache_key, record_id, timeout=ttl)
                    except Exception:
                        pass
                    logger.debug(f"[Idempotency] DB hit: {key.key}")
                    return IdempotencyResult(
                        is_duplicate=True,
                        existing_record=existing,
                        message="Found in database",
                    )
            except Exception as e:
                logger.warning(f"[Idempotency] Lookup failed: {e}")

        return IdempotencyResult(
            is_duplicate=False,
            message="Not found",
        )

    def check_event(self, event_id: str, exists_fn: Callable[[str], bool] | None = None) -> IdempotencyResult:
        """
        Check if an event has already been processed.

        Args:
            event_id: The unique event ID
            exists_fn: Optional callback(event_id) -> bool to check if event exists

        Returns:
            IdempotencyResult with duplicate status
        """
        key = IdempotencyKey.for_event(event_id)
        cache = self._get_cache()

        # Check cache with graceful degradation
        try:
            if cache.get(key.cache_key):
                logger.info(f"[Idempotency] Duplicate event detected (cache): {event_id}")
                return IdempotencyResult(
                    is_duplicate=True,
                    message="Event already processed (cached)",
                )
        except Exception as e:
            logger.warning(f"[Idempotency] Cache unavailable for event check: {e}")

        # Check database if lookup provided
        if exists_fn:
            try:
                exists = exists_fn(event_id)
                if exists:
                    try:
                        cache.set(key.cache_key, True, timeout=self.cache_ttl)
                    except Exception:
                        pass
                    logger.info(f"[Idempotency] Duplicate event detected (DB): {event_id}")
                    return IdempotencyResult(
                        is_duplicate=True,
                        message="Event already processed (database)",
                    )
            except Exception as e:
                logger.warning(f"[Idempotency] Event lookup failed: {e}")

        return IdempotencyResult(
            is_duplicate=False,
            message="Event not yet processed",
        )

    def mark_as_processed(
        self,
        key: IdempotencyKey,
        record_id: int | None = None,
        ttl: int | None = None,
    ) -> bool:
        """
        Mark an operation as processed in the cache.

        Call this after successfully completing an operation.

        Args:
            key: The idempotency key
            record_id: Optional record ID to cache
            ttl: Optional custom TTL

        Returns:
            True if cache was updated, False if cache was unavailable.
            The operation is still considered successful even if cache fails,
            as the DB is the source of truth.
        """
        cache = self._get_cache()
        value = record_id if record_id else True
        try:
            cache.set(key.cache_key, value, timeout=ttl or self.cache_ttl)
            logger.debug(f"[Idempotency] Marked as processed: {key.cache_key}")
            return True
        except Exception as e:
            # Redis unavailable - log but don't fail the operation
            logger.warning(f"[Idempotency] Failed to mark as processed (cache unavailable): {e}")
            return False

    def clear(self, key: IdempotencyKey) -> bool:
        """
        Clear an idempotency key from cache.

        Use with caution - only for cleanup or testing.

        Args:
            key: The idempotency key to clear

        Returns:
            True if cache was cleared, False if cache was unavailable.
        """
        cache = self._get_cache()
        try:
            cache.delete(key.cache_key)
            logger.debug(f"[Idempotency] Cleared: {key.cache_key}")
            return True
        except Exception as e:
            logger.warning(f"[Idempotency] Failed to clear key (cache unavailable): {e}")
            return False


# Singleton instance
_service: IdempotencyService | None = None


def get_idempotency_service() -> IdempotencyService:
    """Get the singleton IdempotencyService instance."""
    global _service
    if _service is None:
        _service = IdempotencyService()
    return _service


# =============================================================================
# AntiFlappingWindow (순위 5, 5.3 - v2.4.0)
# =============================================================================


import time
from collections import defaultdict
from threading import Lock


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
                logger.info("[AntiFlappingWindow] Redis mode enabled (distributed)")
            else:
                logger.info("[AntiFlappingWindow] File backend detected, using memory mode")
        except Exception as e:
            logger.warning(f"[AntiFlappingWindow] Redis init failed, using memory: {e}")

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
            logger.warning(f"[AntiFlappingWindow] Redis error, fallback to memory: {e}")
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
                logger.warning(f"[AntiFlappingWindow] Redis clear failed: {e}")

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
