"""
Region Replicator - 리전 간 데이터 복제.

로컬 Redis 변경사항을 피어 리전으로 복제합니다.

복제 모드:
- sync: 동기 복제 (강한 일관성, 높은 지연)
- async: 비동기 복제 (약한 일관성, 낮은 지연)
- eventual: 최종 일관성 (Kafka 기반)

복제 필터링:
- cb:*, idempotency:*, selfhealing:*:emergency* 등 필수 키만 복제
- 메트릭, 캐시 등 로컬 전용 데이터는 복제 제외
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import structlog

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
)

logger = structlog.get_logger()


class ReplicationEventType(str, Enum):
    """
    복제 이벤트 타입.

    Redis 명령에 대응하는 복제 이벤트 타입입니다.
    """

    SET = "set"
    """키-값 설정."""

    DELETE = "delete"
    """키 삭제."""

    HSET = "hset"
    """Hash 필드 설정."""

    HDEL = "hdel"
    """Hash 필드 삭제."""

    LPUSH = "lpush"
    """List 왼쪽 추가."""

    RPUSH = "rpush"
    """List 오른쪽 추가."""

    EXPIRE = "expire"
    """TTL 설정."""


@dataclass
class ReplicationEvent:
    """
    복제 이벤트.

    Redis 변경사항을 캡처하여 다른 리전으로 복제할 때 사용합니다.

    Attributes:
        event_type: 이벤트 타입
        key: Redis 키
        value: 값 (SET/HSET)
        field: Hash 필드 (HSET/HDEL)
        timestamp: 이벤트 타임스탬프
        source_region: 원본 리전
        region_priority: 리전 우선순위 (Tie-breaking용)
        cluster_id: 클러스터 ID (Tie-breaking용)
    """

    event_type: ReplicationEventType
    """이벤트 타입."""

    key: str
    """Redis 키."""

    value: Any = None
    """값 (SET/HSET)."""

    field: str | None = None
    """Hash 필드 (HSET/HDEL)."""

    timestamp: float = 0.0
    """이벤트 타임스탬프 (Unix timestamp)."""

    source_region: str = ""
    """원본 리전."""

    region_priority: int = 100
    """리전 우선순위 (1=최고, 100=기본)."""

    cluster_id: str = ""
    """클러스터 ID (예: prod-kr-1)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "event_type": self.event_type.value,
            "key": self.key,
            "value": self.value,
            "field": self.field,
            "timestamp": self.timestamp,
            "source_region": self.source_region,
            "region_priority": self.region_priority,
            "cluster_id": self.cluster_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplicationEvent:
        """딕셔너리에서 생성."""
        return cls(
            event_type=ReplicationEventType(data["event_type"]),
            key=data["key"],
            value=data.get("value"),
            field=data.get("field"),
            timestamp=data.get("timestamp", 0.0),
            source_region=data.get("source_region", ""),
            region_priority=data.get("region_priority", 100),
            cluster_id=data.get("cluster_id", ""),
        )

    def to_json(self) -> str:
        """JSON 문자열 변환."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> ReplicationEvent:
        """JSON 문자열에서 생성."""
        return cls.from_dict(json.loads(json_str))


class ReplicationFilter:
    """
    복제 대상 키 필터.

    전역 공유가 필수적인 키 패턴만 복제하여 트래픽을 최적화합니다.

    복제 대상:
    - cb:* (Circuit Breaker 상태)
    - idempotency:* (멱등성 키)
    - selfhealing:*:emergency* (Emergency 상태)
    - selfhealing:*:recovery:* (복구 세션)
    - selfhealing:governance:* (Governance 상태)

    복제 제외:
    - selfhealing:*:metrics:* (메트릭)
    - selfhealing:*:cache:* (캐시)
    - rate_limit:* (Rate Limit)
    - *:history (이력)
    - celery* (Celery 내부)
    """

    # 복제 필수 패턴 (정규식)
    REPLICATE_PATTERNS = [
        r"^cb:.*",  # Circuit Breaker 상태
        r"^idempotency:.*",  # 멱등성 키
        r"^selfhealing:.*:emergency.*",  # Emergency 상태
        r"^selfhealing:.*:recovery:.*",  # 복구 세션
        r"^selfhealing:governance:.*",  # Governance 상태
    ]

    # 복제 제외 패턴 (우선 적용)
    EXCLUDE_PATTERNS = [
        r"^selfhealing:.*:metrics:.*",  # 메트릭 (로컬 전용)
        r"^selfhealing:.*:cache:.*",  # 캐시 (로컬 성능용)
        r"^rate_limit:.*",  # Rate Limit (리전 로컬)
        r".*:history$",  # 이력 데이터 (너무 큼)
        r"^celery.*",  # Celery 내부 키
    ]

    def __init__(
        self,
        replicate_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ):
        """
        초기화.

        Args:
            replicate_patterns: 복제 대상 패턴 (None이면 기본값)
            exclude_patterns: 복제 제외 패턴 (None이면 기본값)
        """
        patterns = replicate_patterns or self.REPLICATE_PATTERNS
        excludes = exclude_patterns or self.EXCLUDE_PATTERNS

        self._replicate_compiled = [re.compile(p) for p in patterns]
        self._exclude_compiled = [re.compile(p) for p in excludes]

    def should_replicate(self, key: str) -> bool:
        """
        복제 여부 판단.

        Args:
            key: Redis 키

        Returns:
            True: 복제 필요
            False: 복제 불필요 (로컬 전용)
        """
        # 1. 제외 패턴 먼저 확인 (우선)
        for pattern in self._exclude_compiled:
            if pattern.match(key):
                return False

        # 2. 포함 패턴 확인
        for pattern in self._replicate_compiled:
            if pattern.match(key):
                return True

        # 3. 기본: 복제 안함
        return False


class ReplicationTarget(Protocol):
    """복제 대상 인터페이스."""

    def apply_event(self, event: ReplicationEvent) -> bool:
        """이벤트 적용."""
        ...


class RedisReplicationTarget:
    """
    Redis 복제 대상.

    피어 리전의 Redis에 복제 이벤트를 적용합니다.
    """

    def __init__(self, endpoint: RegionEndpoint):
        """
        초기화.

        Args:
            endpoint: 리전 엔드포인트
        """
        self.endpoint = endpoint
        self._client: Any = None

    def _get_client(self) -> Any:
        """Redis 클라이언트 반환."""
        if self._client is None:
            try:
                import redis

                self._client = redis.Redis.from_url(
                    self.endpoint.redis_url,
                    decode_responses=True,
                )
            except ImportError:
                logger.exception("replicator.redis_package_installed")
                raise
        return self._client

    def apply_event(self, event: ReplicationEvent) -> bool:
        """
        이벤트 적용.

        Args:
            event: 복제 이벤트

        Returns:
            True if 성공
        """
        try:
            client = self._get_client()

            if event.event_type == ReplicationEventType.SET:
                client.set(event.key, event.value)
            elif event.event_type == ReplicationEventType.DELETE:
                client.delete(event.key)
            elif event.event_type == ReplicationEventType.HSET:
                if event.field:
                    client.hset(event.key, event.field, event.value)
            elif event.event_type == ReplicationEventType.HDEL:
                if event.field:
                    client.hdel(event.key, event.field)
            elif event.event_type == ReplicationEventType.LPUSH:
                client.lpush(event.key, event.value)
            elif event.event_type == ReplicationEventType.RPUSH:
                client.rpush(event.key, event.value)
            elif event.event_type == ReplicationEventType.EXPIRE:
                if isinstance(event.value, int):
                    client.expire(event.key, event.value)

            return True
        except Exception as e:
            logger.exception(
                "replicator.apply_event_error",
                region=self.endpoint.region,
                error=e,
            )
            return False

    def is_connected(self) -> bool:
        """연결 상태 확인."""
        try:
            client = self._get_client()
            client.ping()
            return True
        except Exception:
            return False


class RegionReplicator:
    """
    Region Replicator.

    로컬 Redis 변경사항을 피어 리전으로 복제합니다.

    복제 모드:
    - sync: 동기 복제 (강한 일관성, 높은 지연)
    - async: 비동기 복제 (약한 일관성, 낮은 지연)
    - eventual: 최종 일관성 (Kafka 기반)

    사용 예:
        replicator = RegionReplicator()

        # 시작
        replicator.start()

        # 이벤트 복제
        event = ReplicationEvent(
            event_type=ReplicationEventType.SET,
            key="cb:payment_api",
            value="OPEN",
        )
        replicator.enqueue(event)

        # 종료
        replicator.stop()
    """

    def __init__(
        self,
        settings: MultiRegionSettings | None = None,
    ):
        """
        초기화.

        Args:
            settings: Multi-Region 설정 (None이면 기본 설정 사용)
        """
        self._settings = settings or get_multiregion_settings()
        self._targets: list[RedisReplicationTarget] = []
        self._filter = ReplicationFilter()

        # 비동기 복제 큐
        self._queue: queue.Queue[ReplicationEvent] = queue.Queue(maxsize=self._settings.replication_queue_size)
        self._running = False
        self._workers: list[threading.Thread] = []

        # 통계
        self._stats = {
            "enqueued": 0,
            "replicated": 0,
            "filtered": 0,
            "failed": 0,
            "dropped": 0,
        }
        self._stats_lock = threading.Lock()

        # 타겟 초기화
        for endpoint in self._settings.get_peer_endpoints():
            self._targets.append(RedisReplicationTarget(endpoint))

    def enqueue(self, event: ReplicationEvent) -> bool:
        """
        복제 이벤트 큐에 추가.

        Args:
            event: 복제 이벤트

        Returns:
            True if 큐 추가 성공
        """
        # 필터링
        if not self._filter.should_replicate(event.key):
            logger.debug(
                "replicator.filtered_out",
                replication_event_key=event.key,
            )
            with self._stats_lock:
                self._stats["filtered"] += 1
            return True  # 필터링됨 (성공으로 처리)

        # 메타데이터 설정
        event.source_region = self._settings.current_region
        event.timestamp = time.time()

        if self._settings.replication_mode == "sync":
            return self._replicate_sync(event)
        else:
            try:
                self._queue.put_nowait(event)
                with self._stats_lock:
                    self._stats["enqueued"] += 1
                return True
            except queue.Full:
                logger.warning("replicator.queue_full_dropping_event")
                with self._stats_lock:
                    self._stats["dropped"] += 1
                return False

    def _replicate_sync(self, event: ReplicationEvent) -> bool:
        """
        동기 복제.

        모든 타겟에 동시에 복제합니다.
        """
        success = True
        for target in self._targets:
            if not target.apply_event(event):
                success = False
                with self._stats_lock:
                    self._stats["failed"] += 1
            else:
                with self._stats_lock:
                    self._stats["replicated"] += 1
        return success

    def _replicate_async_worker(self) -> None:
        """비동기 복제 워커."""
        batch: list[ReplicationEvent] = []

        while self._running:
            try:
                # 배치 수집
                try:
                    event = self._queue.get(timeout=self._settings.replication_interval_seconds)
                    batch.append(event)
                except queue.Empty:
                    pass

                # 배치 크기 도달 또는 타임아웃
                while len(batch) < self._settings.replication_batch_size:
                    try:
                        event = self._queue.get_nowait()
                        batch.append(event)
                    except queue.Empty:
                        break

                if batch:
                    self._replicate_batch(batch)
                    batch = []

            except Exception as e:
                logger.exception(
                    "replicator.worker_error",
                    error=e,
                )

    def _replicate_batch(self, events: list[ReplicationEvent]) -> None:
        """배치 복제."""
        for target in self._targets:
            for event in events:
                try:
                    if target.apply_event(event):
                        with self._stats_lock:
                            self._stats["replicated"] += 1
                    else:
                        with self._stats_lock:
                            self._stats["failed"] += 1
                except Exception as e:
                    logger.exception(
                        "replicator.batch_apply_error",
                        target_region=target.endpoint.region,
                        error=e,
                    )
                    with self._stats_lock:
                        self._stats["failed"] += 1

    def receive_event(self, event: ReplicationEvent) -> bool:
        """
        피어 리전으로부터 이벤트 수신.

        충돌 해결 후 로컬에 적용합니다.

        Args:
            event: 수신 이벤트

        Returns:
            True if 적용 성공
        """
        # 자기 리전 이벤트 무시
        if event.source_region == self._settings.current_region:
            return True

        # 충돌 해결
        from selfhealing.multiregion.conflict import get_conflict_resolver

        resolver = get_conflict_resolver()
        resolved_event = resolver.resolve(event)

        if resolved_event is None:
            logger.debug(
                "replicator.event_dropped_conflict_resolver",
                replication_event_key=event.key,
            )
            return True

        # 로컬 적용
        try:
            # Redis 클라이언트 획득 시도
            try:
                from selfhealing.adapters.cache import get_redis_client

                client = get_redis_client()
            except ImportError:
                import redis

                client = redis.Redis()

            if resolved_event.event_type == ReplicationEventType.SET:
                client.set(resolved_event.key, resolved_event.value)
            elif resolved_event.event_type == ReplicationEventType.DELETE:
                client.delete(resolved_event.key)
            elif resolved_event.event_type == ReplicationEventType.HSET:
                if resolved_event.field:
                    client.hset(
                        resolved_event.key,
                        resolved_event.field,
                        resolved_event.value,
                    )
            elif resolved_event.event_type == ReplicationEventType.HDEL:
                if resolved_event.field:
                    client.hdel(resolved_event.key, resolved_event.field)

            return True
        except Exception as e:
            logger.exception(
                "replicator.local_apply_error",
                error=e,
            )
            return False

    def start(self) -> None:
        """복제 시작."""
        if not self._settings.enabled:
            logger.info("replicator.multi_region_disabled")
            return

        if self._running:
            return

        self._running = True

        # 비동기 모드일 때 워커 시작
        if self._settings.replication_mode in ("async", "eventual"):
            for i in range(self._settings.replication_worker_count):
                worker = threading.Thread(
                    target=self._replicate_async_worker,
                    name=f"RegionReplicator-{i}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

        logger.info(
            "replicator.started",
            replication_mode=self._settings.replication_mode,
            workers_count=len(self._workers),
        )

    def stop(self) -> None:
        """복제 중지."""
        self._running = False
        for worker in self._workers:
            worker.join(timeout=5.0)
        self._workers.clear()
        logger.info("replicator.stopped")

    def get_queue_size(self) -> int:
        """큐 크기 반환."""
        return self._queue.qsize()

    def refresh_targets(self) -> int:
        """
        피어 엔드포인트 목록 갱신.

        동적 피어 레지스트리(Redis) 또는 환경변수에서
        최신 엔드포인트 목록을 다시 로드하고,
        추가/제거된 리전을 _targets에 반영합니다.

        Returns:
            갱신 후 타겟 수
        """
        new_endpoints = self._settings.get_peer_endpoints()
        current_regions = {t.endpoint.region for t in self._targets}
        new_regions = {e.region for e in new_endpoints}

        # 추가된 리전
        for endpoint in new_endpoints:
            if endpoint.region not in current_regions:
                self._targets.append(RedisReplicationTarget(endpoint))
                logger.info(
                    "replicator.added_target",
                    endpoint=endpoint.region,
                )

        # 제거된 리전
        removed = current_regions - new_regions
        if removed:
            self._targets = [t for t in self._targets if t.endpoint.region in new_regions]
            for region in removed:
                logger.info(
                    "replicator.removed_target",
                    target_region=region,
                )

        return len(self._targets)

    def get_stats(self) -> dict[str, int]:
        """통계 반환."""
        with self._stats_lock:
            return dict(self._stats)

    def is_running(self) -> bool:
        """실행 중인지 확인."""
        return self._running

    def get_targets_status(self) -> list[dict[str, Any]]:
        """타겟 상태 반환."""
        return [
            {
                "region": target.endpoint.region,
                "connected": target.is_connected(),
                "priority": target.endpoint.priority,
            }
            for target in self._targets
        ]
