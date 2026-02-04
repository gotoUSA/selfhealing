# 178. Multi-Region Active-Active 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 2-3주
> **예상 코드량**: ~3,000줄

---

## 0. 문서 목적

이 문서는 **Multi-Region Active-Active** 아키텍처 구현 가이드입니다.

**핵심 목표**: "리전 전체가 죽어도 다른 리전에서 즉시 서비스 가능"

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 ClusterIdentity - 리전 인식만 존재

**파일**: `packages/selfhealing-python/src/selfhealing/core/cluster_identity.py`
**라인**: 30-55

```python
@dataclass
class ClusterIdentity:
    """클러스터 식별 정보."""

    cluster_id: str
    """클러스터 고유 ID (예: 'prod-kr-1')."""

    region: str
    """리전 (예: 'ap-northeast-2')."""

    environment: str
    """환경 (예: 'production')."""

    zone: str = ""
    """가용 영역 (예: 'ap-northeast-2a')."""
```

**한계점**: 리전 식별만 가능, 리전 간 동기화/복제 로직 없음

### 1.2 TieredRedis - LOCAL/GLOBAL 분리만 존재

**파일**: `packages/selfhealing-python/src/selfhealing/core/tiered_redis.py`
**라인**: 50-90

```python
class TieredRedisClient:
    """
    Tiered Redis Client.

    LOCAL Redis (빠른 읽기) + GLOBAL Redis (크로스 리전 공유).
    """

    def __init__(self, local_client: Redis, global_client: Redis):
        self._local = local_client
        self._global = global_client
```

**한계점**:
- GLOBAL Redis 연결만 존재
- 실제 Active-Active 복제 로직 없음
- Conflict Resolution 없음

### 1.3 누락된 기능

| 기능 | 현재 상태 | 필요 |
|------|----------|------|
| 리전 간 데이터 복제 | ❌ | RegionReplicator |
| Conflict Resolution | ❌ | CRDT 또는 Last-Write-Wins |
| 리전 장애 감지 | ❌ | RegionHealthMonitor |
| 자동 페일오버 | ❌ | RegionFailover |
| 트래픽 라우팅 | ❌ | Route53/GCP Global LB 연동 |

---

## 2. 구현 목표

### 2.1 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Global Traffic Manager                              │
│                    (Route53 / GCP Global LB / Cloudflare)                   │
│                              ↓ Latency-based Routing                        │
└─────────────────────────────────────────────────────────────────────────────┘
                    ↓                                    ↓
┌────────────────────────────────┐    ┌────────────────────────────────┐
│        Region: ap-northeast-2   │    │        Region: us-east-1        │
│            (Primary)            │    │          (Secondary)            │
│  ┌──────────────────────────┐  │    │  ┌──────────────────────────┐  │
│  │    Self-Healing Stack    │  │    │  │    Self-Healing Stack    │  │
│  │  ┌──────┐  ┌─────────┐  │  │    │  │  ┌──────┐  ┌─────────┐  │  │
│  │  │ DLQ  │  │Recovery │  │  │    │  │  │ DLQ  │  │Recovery │  │  │
│  │  └──────┘  └─────────┘  │  │    │  │  └──────┘  └─────────┘  │  │
│  │  ┌──────┐  ┌─────────┐  │  │    │  │  ┌──────┐  ┌─────────┐  │  │
│  │  │  CB  │  │  Audit  │  │  │    │  │  │  CB  │  │  Audit  │  │  │
│  │  └──────┘  └─────────┘  │  │    │  │  └──────┘  └─────────┘  │  │
│  └──────────────────────────┘  │    │  └──────────────────────────┘  │
│                                │    │                                │
│  ┌──────────────────────────┐  │    │  ┌──────────────────────────┐  │
│  │      Redis Cluster       │◄─┼────┼─►│      Redis Cluster       │  │
│  │     (Local + Replica)    │  │    │  │     (Local + Replica)    │  │
│  └──────────────────────────┘  │    │  └──────────────────────────┘  │
│                                │    │                                │
│  ┌──────────────────────────┐  │    │  ┌──────────────────────────┐  │
│  │     Kafka Cluster        │◄─┼────┼─►│     Kafka Cluster        │  │
│  │   (MirrorMaker 2)        │  │    │  │   (MirrorMaker 2)        │  │
│  └──────────────────────────┘  │    │  └──────────────────────────┘  │
└────────────────────────────────┘    └────────────────────────────────┘
                    ↑                                    ↑
                    └──────────── Cross-Region ──────────┘
                                   Replication
```

### 2.2 핵심 컴포넌트

| 컴포넌트 | 역할 |
|----------|------|
| `RegionConfig` | 리전 설정 |
| `RegionHealthMonitor` | 리전 건강 상태 모니터링 |
| `RegionReplicator` | 리전 간 데이터 복제 |
| `ConflictResolver` | 충돌 해결 (CRDT/LWW) |
| `RegionFailover` | 자동 페일오버 |
| `CrossRegionRouter` | 크로스 리전 라우팅 |

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/multiregion/
├── __init__.py
├── config.py              # 리전 설정
├── health_monitor.py      # 리전 건강 모니터링
├── replicator.py          # 데이터 복제
├── conflict.py            # 충돌 해결
├── failover.py            # 페일오버
└── router.py              # 크로스 리전 라우팅
```

### 3.2 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/multiregion/config.py
"""
Multi-Region 설정.

기존 코드 참조:
- core/cluster_identity.py: ClusterIdentity
- core/tiered_redis.py: TieredRedisClient
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class RegionEndpoint:
    """리전 엔드포인트."""

    def __init__(
        self,
        region: str,
        redis_url: str,
        kafka_bootstrap: str,
        api_endpoint: str,
        priority: int = 100,
    ):
        self.region = region
        self.redis_url = redis_url
        self.kafka_bootstrap = kafka_bootstrap
        self.api_endpoint = api_endpoint
        self.priority = priority


class MultiRegionSettings(BaseSettings):
    """
    Multi-Region 설정.

    환경변수:
    - SELFHEALING_MULTIREGION_*
    """

    # 활성화
    enabled: bool = Field(
        default=False,
        description="Multi-Region 활성화",
    )

    # 현재 리전
    current_region: str = Field(
        default="ap-northeast-2",
        description="현재 리전",
    )

    # 리전 역할
    region_role: Literal["primary", "secondary", "readonly"] = Field(
        default="primary",
        description="리전 역할",
    )

    # 피어 리전 목록 (JSON)
    peer_regions: str = Field(
        default="[]",
        description='피어 리전 JSON (예: [{"region": "us-east-1", "redis_url": "...", ...}])',
    )

    # 복제 설정
    replication_mode: Literal["sync", "async", "eventual"] = Field(
        default="async",
        description="복제 모드",
    )
    replication_batch_size: int = Field(
        default=100,
        description="복제 배치 크기",
    )
    replication_interval_seconds: float = Field(
        default=1.0,
        description="비동기 복제 주기 (초)",
    )

    # 충돌 해결
    conflict_resolution: Literal["lww", "crdt", "manual"] = Field(
        default="lww",
        description="충돌 해결 전략 (lww: Last-Write-Wins, crdt: CRDT)",
    )

    # 건강 모니터링
    health_check_interval_seconds: float = Field(
        default=10.0,
        description="리전 건강 체크 주기",
    )
    health_check_timeout_seconds: float = Field(
        default=5.0,
        description="리전 건강 체크 타임아웃",
    )
    unhealthy_threshold: int = Field(
        default=3,
        description="비정상 판정 연속 실패 횟수",
    )

    # 페일오버
    failover_enabled: bool = Field(
        default=True,
        description="자동 페일오버 활성화",
    )
    failover_cooldown_seconds: float = Field(
        default=300.0,
        description="페일오버 쿨다운 (5분)",
    )

    class Config:
        env_prefix = "SELFHEALING_MULTIREGION_"
        env_file = ".env"

    @field_validator("peer_regions", mode="before")
    @classmethod
    def parse_peer_regions(cls, v: str) -> str:
        """JSON 형식 검증."""
        import json
        if v:
            try:
                json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("peer_regions must be valid JSON")
        return v

    def get_peer_endpoints(self) -> list[RegionEndpoint]:
        """피어 리전 엔드포인트 반환."""
        import json

        if not self.peer_regions:
            return []

        try:
            data = json.loads(self.peer_regions)
            return [
                RegionEndpoint(
                    region=r["region"],
                    redis_url=r.get("redis_url", ""),
                    kafka_bootstrap=r.get("kafka_bootstrap", ""),
                    api_endpoint=r.get("api_endpoint", ""),
                    priority=r.get("priority", 100),
                )
                for r in data
            ]
        except Exception:
            return []


@lru_cache(maxsize=1)
def get_multiregion_settings() -> MultiRegionSettings:
    """설정 싱글톤 반환."""
    return MultiRegionSettings()


def reset_multiregion_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_multiregion_settings.cache_clear()
```

### 3.3 Region Health Monitor (health_monitor.py)

```python
# packages/selfhealing-python/src/selfhealing/multiregion/health_monitor.py
"""
Region Health Monitor - 리전 건강 상태 모니터링.

기존 코드 참조:
- meta/health_probe.py: HealthProbe 패턴
- core/connection_health.py: 연결 상태 확인
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
)

logger = logging.getLogger(__name__)


class RegionHealthStatus(Enum):
    """리전 건강 상태."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNREACHABLE = "unreachable"


@dataclass
class RegionHealth:
    """리전 건강 정보."""

    region: str
    """리전 이름."""

    status: RegionHealthStatus
    """건강 상태."""

    latency_ms: float
    """응답 시간 (ms)."""

    last_check: datetime
    """마지막 체크 시각."""

    consecutive_failures: int = 0
    """연속 실패 횟수."""

    details: dict[str, Any] = field(default_factory=dict)
    """상세 정보."""


class RegionHealthMonitor:
    """
    Region Health Monitor.

    모든 피어 리전의 건강 상태를 모니터링.
    """

    def __init__(
        self,
        settings: MultiRegionSettings | None = None,
    ):
        """초기화."""
        self._settings = settings or get_multiregion_settings()
        self._lock = threading.RLock()
        self._health_states: dict[str, RegionHealth] = {}
        self._running = False
        self._worker: threading.Thread | None = None

        # 초기화
        for endpoint in self._settings.get_peer_endpoints():
            self._health_states[endpoint.region] = RegionHealth(
                region=endpoint.region,
                status=RegionHealthStatus.HEALTHY,
                latency_ms=0,
                last_check=datetime.now(timezone.utc),
            )

    def check_region(self, endpoint: RegionEndpoint) -> RegionHealth:
        """
        단일 리전 건강 체크.

        체크 항목:
        1. API Endpoint 응답
        2. Redis 연결
        3. Kafka 연결
        """
        start = time.time()

        try:
            # API 헬스 체크
            health_url = f"{endpoint.api_endpoint}/health/"
            req = urllib.request.Request(
                health_url,
                method="GET",
                headers={"Accept": "application/json"},
            )

            with urllib.request.urlopen(
                req,
                timeout=self._settings.health_check_timeout_seconds,
            ) as resp:
                latency_ms = (time.time() - start) * 1000

                if resp.status == 200:
                    return RegionHealth(
                        region=endpoint.region,
                        status=RegionHealthStatus.HEALTHY,
                        latency_ms=latency_ms,
                        last_check=datetime.now(timezone.utc),
                        consecutive_failures=0,
                    )
                else:
                    return RegionHealth(
                        region=endpoint.region,
                        status=RegionHealthStatus.DEGRADED,
                        latency_ms=latency_ms,
                        last_check=datetime.now(timezone.utc),
                        details={"status_code": resp.status},
                    )
        except urllib.error.URLError as e:
            latency_ms = (time.time() - start) * 1000

            with self._lock:
                prev = self._health_states.get(endpoint.region)
                failures = (prev.consecutive_failures + 1) if prev else 1

            status = (
                RegionHealthStatus.UNREACHABLE
                if failures >= self._settings.unhealthy_threshold
                else RegionHealthStatus.DEGRADED
            )

            return RegionHealth(
                region=endpoint.region,
                status=status,
                latency_ms=latency_ms,
                last_check=datetime.now(timezone.utc),
                consecutive_failures=failures,
                details={"error": str(e)},
            )
        except Exception as e:
            return RegionHealth(
                region=endpoint.region,
                status=RegionHealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                last_check=datetime.now(timezone.utc),
                details={"error": str(e)},
            )

    def check_all_regions(self) -> dict[str, RegionHealth]:
        """모든 피어 리전 건강 체크."""
        results = {}

        for endpoint in self._settings.get_peer_endpoints():
            try:
                health = self.check_region(endpoint)
                results[endpoint.region] = health
            except Exception as e:
                logger.error(f"[RegionHealth] {endpoint.region} check error: {e}")
                results[endpoint.region] = RegionHealth(
                    region=endpoint.region,
                    status=RegionHealthStatus.UNHEALTHY,
                    latency_ms=0,
                    last_check=datetime.now(timezone.utc),
                    details={"error": str(e)},
                )

        with self._lock:
            self._health_states = results

        return results

    def get_healthy_regions(self) -> list[str]:
        """정상 리전 목록 반환."""
        with self._lock:
            return [
                region
                for region, health in self._health_states.items()
                if health.status == RegionHealthStatus.HEALTHY
            ]

    def get_region_health(self, region: str) -> RegionHealth | None:
        """특정 리전 건강 정보 반환."""
        with self._lock:
            return self._health_states.get(region)

    def get_best_region(self) -> str | None:
        """
        가장 좋은 리전 반환.

        기준: HEALTHY 상태 중 latency가 가장 낮은 리전
        """
        with self._lock:
            healthy = [
                (region, health)
                for region, health in self._health_states.items()
                if health.status == RegionHealthStatus.HEALTHY
            ]

            if not healthy:
                return None

            healthy.sort(key=lambda x: x[1].latency_ms)
            return healthy[0][0]

    def is_region_healthy(self, region: str) -> bool:
        """리전이 정상인지 확인."""
        with self._lock:
            health = self._health_states.get(region)
            return health is not None and health.status == RegionHealthStatus.HEALTHY

    def _run_loop(self) -> None:
        """모니터링 루프."""
        while self._running:
            try:
                self.check_all_regions()
            except Exception as e:
                logger.error(f"[RegionHealth] Loop error: {e}")

            time.sleep(self._settings.health_check_interval_seconds)

    def start(self) -> None:
        """모니터링 시작."""
        if self._running:
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="RegionHealthMonitor",
            daemon=True,
        )
        self._worker.start()
        logger.info("[RegionHealth] Started")

    def stop(self) -> None:
        """모니터링 중지."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=5.0)
        logger.info("[RegionHealth] Stopped")
```

### 3.4 Region Replicator (replicator.py)

```python
# packages/selfhealing-python/src/selfhealing/multiregion/replicator.py
"""
Region Replicator - 리전 간 데이터 복제.

기존 코드 참조:
- core/tiered_redis.py: TieredRedisClient
- kafka/producer.py: Kafka Producer (구현 예정)
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
)
from selfhealing.multiregion.conflict import ConflictResolver, get_conflict_resolver

logger = logging.getLogger(__name__)


class ReplicationEventType(Enum):
    """복제 이벤트 타입."""

    SET = "set"
    DELETE = "delete"
    HSET = "hset"
    HDEL = "hdel"
    LPUSH = "lpush"
    RPUSH = "rpush"


@dataclass
class ReplicationEvent:
    """복제 이벤트."""

    event_type: ReplicationEventType
    """이벤트 타입."""

    key: str
    """Redis 키."""

    value: Any
    """값 (SET/HSET)."""

    field: str | None = None
    """Hash 필드 (HSET/HDEL)."""

    timestamp: float = 0.0
    """이벤트 타임스탬프 (Unix timestamp)."""

    source_region: str = ""
    """원본 리전."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "event_type": self.event_type.value,
            "key": self.key,
            "value": self.value,
            "field": self.field,
            "timestamp": self.timestamp,
            "source_region": self.source_region,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplicationEvent:
        """딕셔너리에서 생성."""
        return cls(
            event_type=ReplicationEventType(data["event_type"]),
            key=data["key"],
            value=data["value"],
            field=data.get("field"),
            timestamp=data.get("timestamp", 0.0),
            source_region=data.get("source_region", ""),
        )


class ReplicationTarget(Protocol):
    """복제 대상 인터페이스."""

    def apply_event(self, event: ReplicationEvent) -> bool:
        """이벤트 적용."""
        ...


class RedisReplicationTarget:
    """Redis 복제 대상."""

    def __init__(self, endpoint: RegionEndpoint):
        """초기화."""
        self.endpoint = endpoint
        self._client: Any = None

    def _get_client(self) -> Any:
        """Redis 클라이언트 반환."""
        if self._client is None:
            import redis
            self._client = redis.Redis.from_url(
                self.endpoint.redis_url,
                decode_responses=True,
            )
        return self._client

    def apply_event(self, event: ReplicationEvent) -> bool:
        """이벤트 적용."""
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

            return True
        except Exception as e:
            logger.error(f"[Replicator] Apply event error: {e}")
            return False


class RegionReplicator:
    """
    Region Replicator.

    로컬 Redis 변경사항을 피어 리전으로 복제.

    복제 모드:
    - sync: 동기 복제 (강한 일관성, 높은 지연)
    - async: 비동기 복제 (약한 일관성, 낮은 지연)
    - eventual: 최종 일관성 (Kafka 기반)
    """

    def __init__(
        self,
        settings: MultiRegionSettings | None = None,
        conflict_resolver: ConflictResolver | None = None,
    ):
        """초기화."""
        self._settings = settings or get_multiregion_settings()
        self._conflict_resolver = conflict_resolver or get_conflict_resolver()
        self._targets: list[RedisReplicationTarget] = []

        # 비동기 복제 큐
        self._queue: queue.Queue[ReplicationEvent] = queue.Queue(maxsize=10000)
        self._running = False
        self._workers: list[threading.Thread] = []

        # 타겟 초기화
        for endpoint in self._settings.get_peer_endpoints():
            self._targets.append(RedisReplicationTarget(endpoint))

    def enqueue(self, event: ReplicationEvent) -> bool:
        """
        복제 이벤트 큐에 추가.

        Args:
            event: 복제 이벤트

        Returns:
            큐 추가 성공 여부
        """
        event.source_region = self._settings.current_region
        event.timestamp = time.time()

        if self._settings.replication_mode == "sync":
            return self._replicate_sync(event)
        else:
            try:
                self._queue.put_nowait(event)
                return True
            except queue.Full:
                logger.warning("[Replicator] Queue full, dropping event")
                return False

    def _replicate_sync(self, event: ReplicationEvent) -> bool:
        """동기 복제."""
        success = True
        for target in self._targets:
            if not target.apply_event(event):
                success = False
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
                logger.error(f"[Replicator] Worker error: {e}")

    def _replicate_batch(self, events: list[ReplicationEvent]) -> None:
        """배치 복제."""
        for target in self._targets:
            for event in events:
                try:
                    target.apply_event(event)
                except Exception as e:
                    logger.error(
                        f"[Replicator] Batch apply error to {target.endpoint.region}: {e}"
                    )

    def receive_event(self, event: ReplicationEvent) -> bool:
        """
        피어 리전으로부터 이벤트 수신.

        충돌 해결 후 로컬 적용.

        Args:
            event: 수신 이벤트

        Returns:
            적용 성공 여부
        """
        # 자기 리전 이벤트 무시
        if event.source_region == self._settings.current_region:
            return True

        # 충돌 해결
        resolved_event = self._conflict_resolver.resolve(event)
        if resolved_event is None:
            logger.debug(f"[Replicator] Event dropped by conflict resolver: {event.key}")
            return True

        # 로컬 적용
        try:
            from selfhealing.adapters.cache import get_redis_client

            client = get_redis_client()

            if resolved_event.event_type == ReplicationEventType.SET:
                client.set(resolved_event.key, resolved_event.value)
            elif resolved_event.event_type == ReplicationEventType.DELETE:
                client.delete(resolved_event.key)
            elif resolved_event.event_type == ReplicationEventType.HSET:
                if resolved_event.field:
                    client.hset(resolved_event.key, resolved_event.field, resolved_event.value)
            elif resolved_event.event_type == ReplicationEventType.HDEL:
                if resolved_event.field:
                    client.hdel(resolved_event.key, resolved_event.field)

            return True
        except Exception as e:
            logger.error(f"[Replicator] Local apply error: {e}")
            return False

    def start(self) -> None:
        """복제 시작."""
        if not self._settings.enabled:
            logger.info("[Replicator] Multi-region disabled")
            return

        if self._running:
            return

        self._running = True

        # 비동기 모드일 때 워커 시작
        if self._settings.replication_mode in ("async", "eventual"):
            worker = threading.Thread(
                target=self._replicate_async_worker,
                name="RegionReplicator",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

        logger.info(f"[Replicator] Started (mode={self._settings.replication_mode})")

    def stop(self) -> None:
        """복제 중지."""
        self._running = False
        for worker in self._workers:
            worker.join(timeout=5.0)
        logger.info("[Replicator] Stopped")

    def get_queue_size(self) -> int:
        """큐 크기 반환."""
        return self._queue.qsize()
```

### 3.5 Conflict Resolver (conflict.py)

```python
# packages/selfhealing-python/src/selfhealing/multiregion/conflict.py
"""
Conflict Resolver - 리전 간 충돌 해결.

충돌 해결 전략:
- LWW (Last-Write-Wins): 타임스탬프 기반
- CRDT: Conflict-free Replicated Data Types
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from selfhealing.multiregion.config import get_multiregion_settings

logger = logging.getLogger(__name__)


class ReplicationEvent:
    """복제 이벤트 (전방 선언)."""
    pass


# 실제 import는 순환 참조 방지를 위해 런타임에
def _get_event_class() -> type:
    from selfhealing.multiregion.replicator import ReplicationEvent
    return ReplicationEvent


class ConflictResolver(ABC):
    """충돌 해결 인터페이스."""

    @abstractmethod
    def resolve(self, incoming: Any) -> Any | None:
        """
        충돌 해결.

        Args:
            incoming: 수신 이벤트

        Returns:
            적용할 이벤트 (None이면 무시)
        """
        pass


class LastWriteWinsResolver(ConflictResolver):
    """
    Last-Write-Wins 충돌 해결.

    타임스탬프가 더 최신인 이벤트가 승리.
    """

    def __init__(self):
        """초기화."""
        self._last_timestamps: dict[str, float] = {}

    def resolve(self, incoming: Any) -> Any | None:
        """타임스탬프 기반 충돌 해결."""
        key = incoming.key
        incoming_ts = incoming.timestamp

        last_ts = self._last_timestamps.get(key, 0.0)

        if incoming_ts > last_ts:
            self._last_timestamps[key] = incoming_ts
            return incoming
        else:
            logger.debug(f"[LWW] Dropped stale event: {key} ({incoming_ts} <= {last_ts})")
            return None


class CRDTGCounter:
    """
    G-Counter CRDT.

    Grow-only Counter - 각 노드별 카운터를 유지.
    """

    def __init__(self):
        """초기화."""
        self._counters: dict[str, int] = {}

    def increment(self, node: str, amount: int = 1) -> None:
        """증가."""
        self._counters[node] = self._counters.get(node, 0) + amount

    def merge(self, other: CRDTGCounter) -> None:
        """머지."""
        for node, count in other._counters.items():
            self._counters[node] = max(self._counters.get(node, 0), count)

    def value(self) -> int:
        """현재 값."""
        return sum(self._counters.values())

    def to_dict(self) -> dict[str, int]:
        """직렬화."""
        return dict(self._counters)

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> CRDTGCounter:
        """역직렬화."""
        counter = cls()
        counter._counters = dict(data)
        return counter


class CRDTLWWRegister:
    """
    LWW-Register CRDT.

    Last-Write-Wins Register - 타임스탬프와 함께 값 저장.
    """

    def __init__(self, value: Any = None, timestamp: float = 0.0):
        """초기화."""
        self._value = value
        self._timestamp = timestamp

    def set(self, value: Any, timestamp: float) -> bool:
        """값 설정."""
        if timestamp > self._timestamp:
            self._value = value
            self._timestamp = timestamp
            return True
        return False

    def merge(self, other: CRDTLWWRegister) -> None:
        """머지."""
        if other._timestamp > self._timestamp:
            self._value = other._value
            self._timestamp = other._timestamp

    def value(self) -> Any:
        """현재 값."""
        return self._value

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {"value": self._value, "timestamp": self._timestamp}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CRDTLWWRegister:
        """역직렬화."""
        return cls(value=data["value"], timestamp=data["timestamp"])


class CRDTResolver(ConflictResolver):
    """
    CRDT 기반 충돌 해결.

    키 타입에 따라 적절한 CRDT 적용:
    - counter:* → G-Counter
    - register:* → LWW-Register
    - 기타 → LWW
    """

    def __init__(self):
        """초기화."""
        self._gcounters: dict[str, CRDTGCounter] = {}
        self._lww_registers: dict[str, CRDTLWWRegister] = {}
        self._lww_fallback = LastWriteWinsResolver()

    def resolve(self, incoming: Any) -> Any | None:
        """CRDT 기반 충돌 해결."""
        key = incoming.key

        if key.startswith("counter:"):
            # G-Counter 머지
            if key not in self._gcounters:
                self._gcounters[key] = CRDTGCounter()

            # 값이 dict면 CRDT 데이터
            if isinstance(incoming.value, dict):
                other = CRDTGCounter.from_dict(incoming.value)
                self._gcounters[key].merge(other)

            # 머지 후 새 값으로 이벤트 수정
            incoming.value = self._gcounters[key].value()
            return incoming

        elif key.startswith("register:"):
            # LWW-Register 머지
            if key not in self._lww_registers:
                self._lww_registers[key] = CRDTLWWRegister()

            register = self._lww_registers[key]

            if isinstance(incoming.value, dict) and "timestamp" in incoming.value:
                other = CRDTLWWRegister.from_dict(incoming.value)
                register.merge(other)
            else:
                register.set(incoming.value, incoming.timestamp)

            incoming.value = register.value()
            return incoming

        else:
            # 기본: LWW
            return self._lww_fallback.resolve(incoming)


# =============================================================================
# Factory
# =============================================================================

_resolver: ConflictResolver | None = None


def get_conflict_resolver() -> ConflictResolver:
    """ConflictResolver 싱글톤 반환."""
    global _resolver

    if _resolver is None:
        settings = get_multiregion_settings()

        if settings.conflict_resolution == "crdt":
            _resolver = CRDTResolver()
        else:
            _resolver = LastWriteWinsResolver()

    return _resolver


def reset_conflict_resolver() -> None:
    """리졸버 리셋 (테스트용)."""
    global _resolver
    _resolver = None
```

### 3.6 Region Failover (failover.py)

```python
# packages/selfhealing-python/src/selfhealing/multiregion/failover.py
"""
Region Failover - 리전 장애 시 자동 페일오버.

기존 코드 참조:
- meta/escalation.py: PagerDuty/Slack 알림
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    get_multiregion_settings,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealthMonitor,
    RegionHealthStatus,
)

logger = logging.getLogger(__name__)


class FailoverState(Enum):
    """페일오버 상태."""

    NORMAL = "normal"
    DETECTING = "detecting"
    FAILOVER_IN_PROGRESS = "failover_in_progress"
    FAILED_OVER = "failed_over"
    RECOVERING = "recovering"


@dataclass
class FailoverEvent:
    """페일오버 이벤트."""

    from_region: str
    to_region: str
    timestamp: datetime
    reason: str
    state: FailoverState


class RegionFailover:
    """
    Region Failover Manager.

    리전 장애 감지 시 자동으로 다른 리전으로 페일오버.

    페일오버 프로세스:
    1. 리전 장애 감지 (RegionHealthMonitor)
    2. 페일오버 대상 리전 선정
    3. 트래픽 전환 (Route53/GCP)
    4. 데이터 정합성 확인
    5. 알림 전송
    """

    def __init__(
        self,
        settings: MultiRegionSettings | None = None,
        health_monitor: RegionHealthMonitor | None = None,
        on_failover: Callable[[FailoverEvent], None] | None = None,
    ):
        """초기화."""
        self._settings = settings or get_multiregion_settings()
        self._health_monitor = health_monitor or RegionHealthMonitor(settings=self._settings)
        self._on_failover = on_failover

        self._lock = threading.RLock()
        self._state = FailoverState.NORMAL
        self._last_failover_time: float = 0
        self._current_primary: str = self._settings.current_region
        self._running = False
        self._worker: threading.Thread | None = None

    def get_state(self) -> FailoverState:
        """현재 상태 반환."""
        with self._lock:
            return self._state

    def get_current_primary(self) -> str:
        """현재 Primary 리전 반환."""
        with self._lock:
            return self._current_primary

    def _select_failover_target(self) -> str | None:
        """페일오버 대상 리전 선정."""
        healthy = self._health_monitor.get_healthy_regions()

        if not healthy:
            logger.error("[Failover] No healthy regions available")
            return None

        # 현재 Primary 제외
        candidates = [r for r in healthy if r != self._current_primary]

        if not candidates:
            logger.error("[Failover] No failover candidates")
            return None

        # 우선순위 또는 latency 기준 선정
        return candidates[0]

    def _can_failover(self) -> bool:
        """페일오버 가능 여부 확인."""
        if not self._settings.failover_enabled:
            return False

        elapsed = time.time() - self._last_failover_time
        if elapsed < self._settings.failover_cooldown_seconds:
            logger.debug(f"[Failover] Cooldown ({elapsed:.0f}s < {self._settings.failover_cooldown_seconds}s)")
            return False

        return True

    def trigger_failover(self, reason: str = "manual") -> bool:
        """
        수동 페일오버 트리거.

        Args:
            reason: 페일오버 사유

        Returns:
            성공 여부
        """
        if not self._can_failover():
            return False

        target = self._select_failover_target()
        if target is None:
            return False

        return self._execute_failover(target, reason)

    def _execute_failover(self, target_region: str, reason: str) -> bool:
        """페일오버 실행."""
        with self._lock:
            if self._state == FailoverState.FAILOVER_IN_PROGRESS:
                return False

            self._state = FailoverState.FAILOVER_IN_PROGRESS

        logger.warning(
            f"[Failover] Executing failover: {self._current_primary} → {target_region} ({reason})"
        )

        try:
            # 1. DNS/Load Balancer 전환
            # TODO: Route53 / GCP Global LB API 호출

            # 2. 데이터 정합성 확인
            # TODO: 마지막 복제 오프셋 확인

            # 3. 상태 업데이트
            with self._lock:
                old_primary = self._current_primary
                self._current_primary = target_region
                self._state = FailoverState.FAILED_OVER
                self._last_failover_time = time.time()

            # 4. 이벤트 생성 및 콜백
            event = FailoverEvent(
                from_region=old_primary,
                to_region=target_region,
                timestamp=datetime.now(timezone.utc),
                reason=reason,
                state=FailoverState.FAILED_OVER,
            )

            if self._on_failover:
                try:
                    self._on_failover(event)
                except Exception as e:
                    logger.error(f"[Failover] Callback error: {e}")

            # 5. 알림 전송
            self._send_alert(event)

            logger.warning(f"[Failover] Completed: {old_primary} → {target_region}")
            return True

        except Exception as e:
            logger.error(f"[Failover] Failed: {e}")
            with self._lock:
                self._state = FailoverState.NORMAL
            return False

    def _send_alert(self, event: FailoverEvent) -> None:
        """페일오버 알림 전송."""
        try:
            from selfhealing.meta.escalation import (
                EscalationEvent,
                EscalationLevel,
                EscalationManager,
            )

            manager = EscalationManager()
            manager.escalate(
                EscalationEvent(
                    level=EscalationLevel.CRITICAL,
                    title=f"Region Failover: {event.from_region} → {event.to_region}",
                    description=f"Automatic failover executed.\nReason: {event.reason}",
                    component="multiregion",
                    details={
                        "from_region": event.from_region,
                        "to_region": event.to_region,
                        "reason": event.reason,
                    },
                    timestamp=event.timestamp,
                )
            )
        except Exception as e:
            logger.error(f"[Failover] Alert error: {e}")

    def _check_and_failover(self) -> None:
        """건강 상태 확인 및 자동 페일오버."""
        # Primary 리전 건강 확인
        if self._health_monitor.is_region_healthy(self._current_primary):
            return

        # 장애 감지
        health = self._health_monitor.get_region_health(self._current_primary)
        if health and health.status == RegionHealthStatus.UNREACHABLE:
            logger.warning(f"[Failover] Primary region unreachable: {self._current_primary}")

            if self._can_failover():
                self.trigger_failover(reason="primary_unreachable")

    def _run_loop(self) -> None:
        """모니터링 루프."""
        while self._running:
            try:
                self._check_and_failover()
            except Exception as e:
                logger.error(f"[Failover] Loop error: {e}")

            time.sleep(self._settings.health_check_interval_seconds)

    def start(self) -> None:
        """페일오버 모니터링 시작."""
        if not self._settings.enabled or not self._settings.failover_enabled:
            logger.info("[Failover] Disabled")
            return

        if self._running:
            return

        # Health Monitor 시작
        self._health_monitor.start()

        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="RegionFailover",
            daemon=True,
        )
        self._worker.start()
        logger.info("[Failover] Started")

    def stop(self) -> None:
        """페일오버 모니터링 중지."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=5.0)
        self._health_monitor.stop()
        logger.info("[Failover] Stopped")
```

---

## 4. Kafka MirrorMaker 2 설정

### 4.1 MirrorMaker 2 설정

Multi-Region Kafka 복제를 위한 MirrorMaker 2 설정:

```properties
# mm2.properties

# 클러스터 alias
clusters = kr, us

# 클러스터 연결 정보
kr.bootstrap.servers = kafka-kr.example.com:9092
us.bootstrap.servers = kafka-us.example.com:9092

# 복제 방향
kr->us.enabled = true
us->kr.enabled = true

# 복제 토픽
kr->us.topics = selfhealing\..*
us->kr.topics = selfhealing\..*

# 그룹 복제
sync.group.offsets.enabled = true
sync.group.offsets.interval.seconds = 5

# 체크포인트
emit.checkpoints.enabled = true
emit.checkpoints.interval.seconds = 5

# Heartbeat
emit.heartbeats.enabled = true
emit.heartbeats.interval.seconds = 1

# 복제 설정
replication.factor = 3
offset-syncs.topic.replication.factor = 3
heartbeats.topic.replication.factor = 3
checkpoints.topic.replication.factor = 3
```

### 4.2 Kubernetes 배포

```yaml
# k8s/mirrormaker2.yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaMirrorMaker2
metadata:
  name: selfhealing-mm2
  namespace: selfhealing
spec:
  version: 3.6.0
  replicas: 2

  clusters:
    - alias: kr
      bootstrapServers: kafka-kr.svc.cluster.local:9092
      config:
        config.storage.replication.factor: 3
    - alias: us
      bootstrapServers: kafka-us.svc.cluster.local:9092
      config:
        config.storage.replication.factor: 3

  mirrors:
    - sourceCluster: kr
      targetCluster: us
      topicsPattern: "selfhealing\\..*"
      groupsPattern: ".*"
      sourceConnector:
        config:
          replication.factor: 3
          offset-syncs.topic.replication.factor: 3
          sync.topic.acls.enabled: false
      checkpointConnector:
        config:
          checkpoints.topic.replication.factor: 3
      heartbeatConnector:
        config:
          heartbeats.topic.replication.factor: 3

    - sourceCluster: us
      targetCluster: kr
      topicsPattern: "selfhealing\\..*"
      groupsPattern: ".*"
      sourceConnector:
        config:
          replication.factor: 3

  resources:
    requests:
      memory: 1Gi
      cpu: 500m
    limits:
      memory: 2Gi
      cpu: 1000m
```

---

## 5. 설정 예시

```bash
# .env

# Multi-Region 기본 설정
SELFHEALING_MULTIREGION_ENABLED=true
SELFHEALING_MULTIREGION_CURRENT_REGION=ap-northeast-2
SELFHEALING_MULTIREGION_REGION_ROLE=primary

# 피어 리전
SELFHEALING_MULTIREGION_PEER_REGIONS='[{"region": "us-east-1", "redis_url": "redis://global-redis.us-east-1:6379", "kafka_bootstrap": "kafka.us-east-1:9092", "api_endpoint": "https://api.us-east-1.example.com", "priority": 100}]'

# 복제 설정
SELFHEALING_MULTIREGION_REPLICATION_MODE=async
SELFHEALING_MULTIREGION_CONFLICT_RESOLUTION=lww

# 페일오버
SELFHEALING_MULTIREGION_FAILOVER_ENABLED=true
SELFHEALING_MULTIREGION_FAILOVER_COOLDOWN_SECONDS=300
```

---

## 6. 구현 체크리스트

- [ ] `multiregion/__init__.py` 생성
- [ ] `multiregion/config.py` 구현
- [ ] `multiregion/health_monitor.py` 구현
- [ ] `multiregion/replicator.py` 구현
- [ ] `multiregion/conflict.py` 구현
- [ ] `multiregion/failover.py` 구현
- [ ] Kafka MirrorMaker 2 설정
- [ ] Route53/GCP Global LB 연동
- [ ] 단위 테스트 작성
- [ ] Chaos Engineering 테스트

---

## 7. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [175_KAFKA_EVENT_BUS_IMPLEMENTATION.md](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md) - Kafka 구현
- [cluster_identity.py](../../packages/selfhealing-python/src/selfhealing/core/cluster_identity.py) - 클러스터 식별

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
