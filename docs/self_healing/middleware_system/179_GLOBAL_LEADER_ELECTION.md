# 179. Global Leader Election 구현 가이드

> **버전**: 1.1.0
> **작성일**: 2026-02-04
> **최종 수정**: 2026-02-05
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 3-5일
> **예상 코드량**: ~1,200줄 (고급 기능 포함)

### Changelog

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 1.1.0 | 2026-02-05 | **고급 기능 추가** (6장): Fencing Token, 리전 우선순위, Safe Margin/Self-Fencing, Graceful Shutdown, 비동기 콜백, Recovery Audit, Prometheus 메트릭 |
| 1.0.0 | 2026-02-04 | 최초 작성: 기본 Redis SETNX 기반 리더 선출 |

---

## 0. 문서 목적

이 문서는 **Global Leader Election** 구현 가이드입니다.

**핵심 목표**: "전체 클러스터에서 단 하나의 리더만 특정 작업을 수행하도록 보장"

**사용 사례**:
- DLQ Consumer 중 하나만 처리 담당
- 스케줄러 중 하나만 작업 실행
- Multi-Region에서 Primary 리전 선정

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 ClusterIdentity - 리더 선출 없음

**파일**: `packages/selfhealing-python/src/selfhealing/core/cluster_identity.py`
**라인**: 30-55

```python
@dataclass
class ClusterIdentity:
    """클러스터 식별 정보."""

    cluster_id: str
    region: str
    environment: str
    zone: str = ""
```

**한계점**: 클러스터 식별만 가능, 리더 선출 로직 없음

### 1.2 Redis SETNX 기반 락

**파일**: `packages/selfhealing-python/src/selfhealing/core/redis_lock.py`

```python
class RedisDistributedLock:
    """Redis 기반 분산 락."""
```

**한계점**:
- 단기 락만 지원 (작업 실행 중 락)
- 장기 리더십 유지 패턴 없음
- 리더 탈락 시 자동 재선출 없음

### 1.3 누락된 기능

| 기능 | 현재 상태 | 필요 |
|------|----------|------|
| 리더 선출 | ❌ | GlobalLeaderElector |
| 리더십 갱신 | ❌ | Lease/Heartbeat |
| 리더 탈락 감지 | ❌ | Watcher |
| 리더 콜백 | ❌ | on_become_leader/on_lose_leader |

---

## 2. 구현 목표

### 2.1 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Coordination Backend                          │
│              (etcd / Redis / Zookeeper)                          │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    /selfhealing/leader                       │ │
│  │                                                              │ │
│  │  Key: /selfhealing/leader/dlq-consumer                       │ │
│  │  Value: {"node": "pod-a", "lease": 12345, "ttl": 30}         │ │
│  │                                                              │ │
│  │  Key: /selfhealing/leader/scheduler                          │ │
│  │  Value: {"node": "pod-b", "lease": 12346, "ttl": 30}         │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                    ↑           ↑           ↑
          ┌─────────┘           │           └─────────┐
          │                     │                     │
┌─────────┴─────────┐ ┌─────────┴─────────┐ ┌─────────┴─────────┐
│      Pod A        │ │      Pod B        │ │      Pod C        │
│  ┌─────────────┐  │ │  ┌─────────────┐  │ │  ┌─────────────┐  │
│  │ LeaderElector│  │ │  │ LeaderElector│  │ │  │ LeaderElector│  │
│  │   (LEADER)   │  │ │  │  (FOLLOWER)  │  │ │  │  (FOLLOWER)  │  │
│  └─────────────┘  │ │  └─────────────┘  │ │  └─────────────┘  │
│        ↓          │ │        ↓          │ │        ↓          │
│  ┌─────────────┐  │ │  ┌─────────────┐  │ │  ┌─────────────┐  │
│  │ DLQ Consumer │  │ │  │ DLQ Consumer │  │ │  │ DLQ Consumer │  │
│  │   (ACTIVE)   │  │ │  │  (STANDBY)   │  │ │  │  (STANDBY)   │  │
│  └─────────────┘  │ │  └─────────────┘  │ │  └─────────────┘  │
└───────────────────┘ └───────────────────┘ └───────────────────┘
```

### 2.2 핵심 컴포넌트

| 컴포넌트 | 역할 |
|----------|------|
| `LeaderElectorConfig` | 설정 |
| `LeaderElector` | 리더 선출 인터페이스 |
| `RedisLeaderElector` | Redis 기반 구현 |
| `EtcdLeaderElector` | etcd 기반 구현 (선택) |
| `LeaderCallback` | 리더십 변경 콜백 |

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/coordination/
├── __init__.py
├── config.py              # 설정
├── base.py                # 인터페이스
├── redis_elector.py       # Redis 구현
├── etcd_elector.py        # etcd 구현 (선택)
└── factory.py             # 팩토리
```

### 3.2 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/coordination/config.py
"""
Leader Election 설정.

기존 코드 참조:
- core/cluster_identity.py: ClusterIdentity
- core/redis_lock.py: RedisDistributedLock
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class LeaderElectionSettings(BaseSettings):
    """
    Leader Election 설정.

    환경변수:
    - SELFHEALING_LEADER_*
    """

    # 활성화
    enabled: bool = Field(
        default=True,
        description="Leader Election 활성화",
    )

    # 백엔드
    backend: Literal["redis", "etcd"] = Field(
        default="redis",
        description="Coordination 백엔드",
    )

    # 노드 식별자
    node_id: str = Field(
        default="",
        description="노드 고유 ID (비어있으면 hostname 사용)",
    )

    # Lease 설정
    lease_ttl_seconds: int = Field(
        default=30,
        description="리더십 Lease TTL (초)",
    )
    renew_interval_seconds: float = Field(
        default=10.0,
        description="Lease 갱신 주기 (초)",
    )

    # 재시도 설정
    retry_interval_seconds: float = Field(
        default=5.0,
        description="선출 실패 시 재시도 주기 (초)",
    )
    max_retry_attempts: int = Field(
        default=3,
        description="연속 실패 최대 횟수 (0=무제한)",
    )

    # Redis 설정
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL",
    )
    redis_key_prefix: str = Field(
        default="selfhealing:leader:",
        description="Redis 키 prefix",
    )

    # etcd 설정
    etcd_endpoints: str = Field(
        default="localhost:2379",
        description="etcd 엔드포인트 (쉼표 구분)",
    )
    etcd_key_prefix: str = Field(
        default="/selfhealing/leader/",
        description="etcd 키 prefix",
    )

    class Config:
        env_prefix = "SELFHEALING_LEADER_"
        env_file = ".env"

    def get_node_id(self) -> str:
        """노드 ID 반환."""
        if self.node_id:
            return self.node_id

        # Kubernetes Pod 이름
        pod_name = os.environ.get("HOSTNAME", "")
        if pod_name:
            return pod_name

        # Hostname
        return socket.gethostname()


@lru_cache(maxsize=1)
def get_leader_election_settings() -> LeaderElectionSettings:
    """설정 싱글톤 반환."""
    return LeaderElectionSettings()


def reset_leader_election_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_leader_election_settings.cache_clear()
```

### 3.3 리더 선출 인터페이스 (base.py)

```python
# packages/selfhealing-python/src/selfhealing/coordination/base.py
"""
Leader Election 인터페이스.

기존 코드 참조:
- core/redis_lock.py: 분산 락 패턴
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class LeadershipState(Enum):
    """리더십 상태."""

    NOT_STARTED = "not_started"
    FOLLOWER = "follower"
    LEADER = "leader"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class LeaderInfo:
    """현재 리더 정보."""

    node_id: str
    """리더 노드 ID."""

    elected_at: datetime
    """선출 시각."""

    lease_expires_at: datetime
    """Lease 만료 시각."""

    is_self: bool = False
    """자신이 리더인지 여부."""


class LeaderCallback(Protocol):
    """리더십 변경 콜백 프로토콜."""

    def on_become_leader(self) -> None:
        """리더가 되었을 때 호출."""
        ...

    def on_lose_leader(self) -> None:
        """리더십을 잃었을 때 호출."""
        ...


class LeaderElector(ABC):
    """
    Leader Elector 인터페이스.

    리더 선출 및 리더십 유지.

    Usage:
        elector = RedisLeaderElector("dlq-consumer")

        @elector.on_become_leader
        def start_processing():
            print("Now I'm the leader!")

        @elector.on_lose_leader
        def stop_processing():
            print("No longer the leader")

        elector.start()
        # ...
        elector.stop()
    """

    @property
    @abstractmethod
    def resource_name(self) -> str:
        """리소스 이름 (리더십 대상)."""
        pass

    @property
    @abstractmethod
    def state(self) -> LeadershipState:
        """현재 상태."""
        pass

    @abstractmethod
    def is_leader(self) -> bool:
        """현재 리더인지 확인."""
        pass

    @abstractmethod
    def get_leader(self) -> LeaderInfo | None:
        """현재 리더 정보 조회."""
        pass

    @abstractmethod
    def start(self) -> None:
        """리더 선출 시작."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """리더 선출 중지 (리더십 포기)."""
        pass

    @abstractmethod
    def on_become_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더가 되었을 때 콜백 등록 (데코레이터)."""
        pass

    @abstractmethod
    def on_lose_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더십을 잃었을 때 콜백 등록 (데코레이터)."""
        pass
```

### 3.4 Redis 기반 구현 (redis_elector.py)

```python
# packages/selfhealing-python/src/selfhealing/coordination/redis_elector.py
"""
Redis 기반 Leader Election.

Redis SETNX + EXPIRE를 사용한 리더 선출.

기존 코드 참조:
- core/redis_lock.py: SETNX 패턴
- adapters/cache.py: Redis 클라이언트
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from selfhealing.coordination.base import (
    LeaderCallback,
    LeaderElector,
    LeaderInfo,
    LeadershipState,
)
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
)

logger = logging.getLogger(__name__)


class RedisLeaderElector(LeaderElector):
    """
    Redis 기반 Leader Elector.

    알고리즘:
    1. SETNX로 리더 키 획득 시도
    2. 획득 성공 → 리더
    3. 획득 실패 → 팔로워
    4. 리더는 주기적으로 EXPIRE 갱신
    5. 리더 키 만료 → 재선출

    Lua 스크립트:
    - SET NX EX: 리더 획득
    - GETDEL (조건부): 리더 반납
    """

    # Lua 스크립트: 조건부 키 삭제 (자신의 리더십만 반납)
    LUA_RELEASE = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    # Lua 스크립트: 조건부 TTL 갱신 (자신의 리더십만 갱신)
    LUA_RENEW = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("expire", KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(
        self,
        resource_name: str,
        settings: LeaderElectionSettings | None = None,
        redis_client: Any | None = None,
    ):
        """
        초기화.

        Args:
            resource_name: 리소스 이름 (예: "dlq-consumer")
            settings: 설정
            redis_client: Redis 클라이언트 (None이면 자동 생성)
        """
        self._resource_name = resource_name
        self._settings = settings or get_leader_election_settings()
        self._redis = redis_client

        self._node_id = self._settings.get_node_id()
        self._key = f"{self._settings.redis_key_prefix}{resource_name}"

        self._lock = threading.RLock()
        self._state = LeadershipState.NOT_STARTED
        self._running = False
        self._worker: threading.Thread | None = None

        # 콜백
        self._on_become_callbacks: list[Callable[[], None]] = []
        self._on_lose_callbacks: list[Callable[[], None]] = []

        # Lua 스크립트
        self._release_script: Any = None
        self._renew_script: Any = None

    def _get_redis(self) -> Any:
        """Redis 클라이언트 반환."""
        if self._redis is None:
            import redis
            self._redis = redis.Redis.from_url(
                self._settings.redis_url,
                decode_responses=True,
            )
            self._release_script = self._redis.register_script(self.LUA_RELEASE)
            self._renew_script = self._redis.register_script(self.LUA_RENEW)
        return self._redis

    @property
    def resource_name(self) -> str:
        """리소스 이름."""
        return self._resource_name

    @property
    def state(self) -> LeadershipState:
        """현재 상태."""
        with self._lock:
            return self._state

    def is_leader(self) -> bool:
        """현재 리더인지 확인."""
        with self._lock:
            return self._state == LeadershipState.LEADER

    def get_leader(self) -> LeaderInfo | None:
        """현재 리더 정보 조회."""
        try:
            redis = self._get_redis()

            # 키 조회
            value = redis.get(self._key)
            if not value:
                return None

            # TTL 조회
            ttl = redis.ttl(self._key)
            if ttl < 0:
                return None

            # 파싱
            data = json.loads(value)
            elected_at = datetime.fromisoformat(data["elected_at"])
            expires_at = datetime.now(timezone.utc).replace(
                microsecond=0
            ) + __import__("datetime").timedelta(seconds=ttl)

            return LeaderInfo(
                node_id=data["node_id"],
                elected_at=elected_at,
                lease_expires_at=expires_at,
                is_self=(data["node_id"] == self._node_id),
            )
        except Exception as e:
            logger.error(f"[LeaderElector] Get leader error: {e}")
            return None

    def _try_acquire(self) -> bool:
        """리더 획득 시도."""
        try:
            redis = self._get_redis()

            value = json.dumps({
                "node_id": self._node_id,
                "elected_at": datetime.now(timezone.utc).isoformat(),
            })

            # SET NX EX
            acquired = redis.set(
                self._key,
                value,
                nx=True,
                ex=self._settings.lease_ttl_seconds,
            )

            return acquired is True
        except Exception as e:
            logger.error(f"[LeaderElector] Acquire error: {e}")
            return False

    def _renew_lease(self) -> bool:
        """Lease 갱신."""
        try:
            redis = self._get_redis()

            if self._renew_script is None:
                self._renew_script = redis.register_script(self.LUA_RENEW)

            value = json.dumps({
                "node_id": self._node_id,
                "elected_at": datetime.now(timezone.utc).isoformat(),
            })

            result = self._renew_script(
                keys=[self._key],
                args=[value, self._settings.lease_ttl_seconds],
            )

            return result == 1
        except Exception as e:
            logger.error(f"[LeaderElector] Renew error: {e}")
            return False

    def _release_leadership(self) -> None:
        """리더십 반납."""
        try:
            redis = self._get_redis()

            if self._release_script is None:
                self._release_script = redis.register_script(self.LUA_RELEASE)

            value = json.dumps({
                "node_id": self._node_id,
                "elected_at": datetime.now(timezone.utc).isoformat(),
            })

            self._release_script(
                keys=[self._key],
                args=[value],
            )
        except Exception as e:
            logger.error(f"[LeaderElector] Release error: {e}")

    def _become_leader(self) -> None:
        """리더 됨."""
        with self._lock:
            if self._state == LeadershipState.LEADER:
                return
            self._state = LeadershipState.LEADER

        logger.info(f"[LeaderElector] Became leader for {self._resource_name}")

        for callback in self._on_become_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"[LeaderElector] on_become_leader callback error: {e}")

    def _lose_leader(self) -> None:
        """리더십 상실."""
        with self._lock:
            if self._state != LeadershipState.LEADER:
                return
            self._state = LeadershipState.FOLLOWER

        logger.info(f"[LeaderElector] Lost leadership for {self._resource_name}")

        for callback in self._on_lose_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"[LeaderElector] on_lose_leader callback error: {e}")

    def _run_loop(self) -> None:
        """선출 루프."""
        consecutive_failures = 0

        while self._running:
            try:
                with self._lock:
                    current_state = self._state

                if current_state == LeadershipState.LEADER:
                    # 리더: Lease 갱신
                    if self._renew_lease():
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= self._settings.max_retry_attempts > 0:
                            self._lose_leader()
                            consecutive_failures = 0
                else:
                    # 팔로워: 리더 획득 시도
                    if self._try_acquire():
                        self._become_leader()
                        consecutive_failures = 0
                    else:
                        with self._lock:
                            self._state = LeadershipState.FOLLOWER

                # 대기
                if current_state == LeadershipState.LEADER:
                    time.sleep(self._settings.renew_interval_seconds)
                else:
                    time.sleep(self._settings.retry_interval_seconds)

            except Exception as e:
                logger.error(f"[LeaderElector] Loop error: {e}")
                time.sleep(self._settings.retry_interval_seconds)

    def start(self) -> None:
        """리더 선출 시작."""
        if not self._settings.enabled:
            logger.info(f"[LeaderElector] Disabled for {self._resource_name}")
            return

        if self._running:
            return

        self._running = True
        with self._lock:
            self._state = LeadershipState.FOLLOWER

        self._worker = threading.Thread(
            target=self._run_loop,
            name=f"LeaderElector-{self._resource_name}",
            daemon=True,
        )
        self._worker.start()
        logger.info(f"[LeaderElector] Started for {self._resource_name}")

    def stop(self) -> None:
        """리더 선출 중지."""
        with self._lock:
            self._state = LeadershipState.STOPPING

        self._running = False

        # 리더십 반납
        if self.is_leader():
            self._release_leadership()
            self._lose_leader()

        if self._worker:
            self._worker.join(timeout=5.0)

        with self._lock:
            self._state = LeadershipState.STOPPED

        logger.info(f"[LeaderElector] Stopped for {self._resource_name}")

    def on_become_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더가 되었을 때 콜백 등록."""
        self._on_become_callbacks.append(callback)
        return callback

    def on_lose_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더십을 잃었을 때 콜백 등록."""
        self._on_lose_callbacks.append(callback)
        return callback
```

### 3.5 팩토리 (factory.py)

```python
# packages/selfhealing-python/src/selfhealing/coordination/factory.py
"""
Leader Elector 팩토리.
"""

from __future__ import annotations

import threading
from typing import Any

from selfhealing.coordination.base import LeaderElector
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
)

_electors: dict[str, LeaderElector] = {}
_lock = threading.Lock()


def get_leader_elector(
    resource_name: str,
    settings: LeaderElectionSettings | None = None,
) -> LeaderElector:
    """
    Leader Elector 싱글톤 반환.

    Args:
        resource_name: 리소스 이름
        settings: 설정 (None이면 기본 설정)

    Returns:
        LeaderElector 인스턴스
    """
    global _electors

    if resource_name in _electors:
        return _electors[resource_name]

    with _lock:
        if resource_name in _electors:
            return _electors[resource_name]

        settings = settings or get_leader_election_settings()

        if settings.backend == "redis":
            from selfhealing.coordination.redis_elector import RedisLeaderElector
            elector = RedisLeaderElector(resource_name, settings)
        elif settings.backend == "etcd":
            # etcd 구현 시 추가
            raise NotImplementedError("etcd backend not implemented")
        else:
            raise ValueError(f"Unknown backend: {settings.backend}")

        _electors[resource_name] = elector
        return elector


def reset_leader_electors() -> None:
    """모든 Elector 리셋 (테스트용)."""
    global _electors

    with _lock:
        for elector in _electors.values():
            try:
                elector.stop()
            except Exception:
                pass
        _electors.clear()
```

---

## 4. 사용 예시

### 4.1 DLQ Consumer 리더 선출

```python
# tasks/dlq_consumer.py

from selfhealing.coordination.factory import get_leader_elector

class DLQConsumerTask:
    """DLQ Consumer 태스크."""

    def __init__(self):
        self._elector = get_leader_elector("dlq-consumer")
        self._processing = False

        @self._elector.on_become_leader
        def start_processing():
            self._processing = True
            self._start_consume_loop()

        @self._elector.on_lose_leader
        def stop_processing():
            self._processing = False

    def start(self):
        """시작."""
        self._elector.start()

    def stop(self):
        """중지."""
        self._elector.stop()

    def _start_consume_loop(self):
        """소비 루프 시작."""
        while self._processing:
            # DLQ 처리
            pass
```

### 4.2 스케줄러 리더 선출

```python
# tasks/scheduler.py

from selfhealing.coordination.factory import get_leader_elector

def run_scheduler():
    """스케줄러 실행."""
    elector = get_leader_elector("scheduler")

    @elector.on_become_leader
    def start_scheduler():
        print("Scheduler: I'm the leader now!")
        # 스케줄러 작업 시작

    @elector.on_lose_leader
    def stop_scheduler():
        print("Scheduler: Lost leadership")
        # 스케줄러 작업 중지

    elector.start()

    try:
        while True:
            if elector.is_leader():
                # 스케줄된 작업 실행
                pass
            time.sleep(1)
    finally:
        elector.stop()
```

### 4.3 Kubernetes Deployment

```yaml
# k8s/dlq-consumer.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: selfhealing-dlq-consumer
spec:
  replicas: 3  # 여러 Pod, 하나만 리더
  selector:
    matchLabels:
      app: selfhealing-dlq-consumer
  template:
    metadata:
      labels:
        app: selfhealing-dlq-consumer
    spec:
      containers:
        - name: dlq-consumer
          image: selfhealing:latest
          command: ["python", "-m", "selfhealing.tasks.dlq_consumer"]
          env:
            - name: SELFHEALING_LEADER_ENABLED
              value: "true"
            - name: SELFHEALING_LEADER_BACKEND
              value: "redis"
            - name: SELFHEALING_LEADER_REDIS_URL
              value: "redis://redis:6379/0"
            - name: HOSTNAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
```

---

## 5. 설정 예시

```bash
# .env

# Leader Election 기본 설정
SELFHEALING_LEADER_ENABLED=true
SELFHEALING_LEADER_BACKEND=redis

# Lease 설정
SELFHEALING_LEADER_LEASE_TTL_SECONDS=30
SELFHEALING_LEADER_RENEW_INTERVAL_SECONDS=10
SELFHEALING_LEADER_RETRY_INTERVAL_SECONDS=5

# Redis 설정
SELFHEALING_LEADER_REDIS_URL=redis://localhost:6379/0
SELFHEALING_LEADER_REDIS_KEY_PREFIX=selfhealing:leader:
```

---

## 6. 고급 기능 구현 명세

이 섹션은 코드 리뷰를 통해 식별된 보완 사항들을 다룹니다.

### 6.1 Fencing Token (Stale Leader 방지)

**목적**: Split-brain 상황에서 좀비 리더가 외부 시스템에 쓰기하는 것을 방지

**코드 근거**:
- 현재 리더 정보에 단조 증가 토큰 없음
- Redis INCR 명령으로 구현 가능

**구현**:

```python
# coordination/redis_elector.py 수정

class RedisLeaderElector(LeaderElector):
    """Redis 기반 Leader Elector (Fencing Token 지원)."""

    # Fencing Token 키
    FENCING_TOKEN_KEY = "selfhealing:leader:fencing_token:{resource}"

    # 수정된 Lua 스크립트: 리더 획득 시 fencing token 증가
    LUA_ACQUIRE_WITH_FENCING = """
    local key = KEYS[1]
    local fencing_key = KEYS[2]
    local value = ARGV[1]
    local ttl = tonumber(ARGV[2])

    -- SET NX로 리더 획득 시도
    local acquired = redis.call("set", key, value, "NX", "EX", ttl)
    if acquired then
        -- Fencing Token 증가 (단조 증가)
        local token = redis.call("incr", fencing_key)
        return token
    else
        return 0
    end
    """

    def __init__(self, resource_name: str, ...):
        # ... 기존 코드 ...
        self._fencing_token: int = 0
        self._fencing_key = self.FENCING_TOKEN_KEY.format(resource=resource_name)

    def get_fencing_token(self) -> int:
        """
        현재 Fencing Token 반환.

        외부 시스템에 쓰기 시 이 토큰을 함께 전달하여
        stale leader의 쓰기를 방지합니다.

        Usage:
            if elector.is_leader():
                token = elector.get_fencing_token()
                db.write(data, fencing_token=token)
        """
        return self._fencing_token

    def _try_acquire(self) -> bool:
        """리더 획득 시도 (Fencing Token 포함)."""
        try:
            redis = self._get_redis()

            if self._acquire_script is None:
                self._acquire_script = redis.register_script(self.LUA_ACQUIRE_WITH_FENCING)

            value = json.dumps({
                "node_id": self._node_id,
                "elected_at": datetime.now(timezone.utc).isoformat(),
            })

            result = self._acquire_script(
                keys=[self._key, self._fencing_key],
                args=[value, self._settings.lease_ttl_seconds],
            )

            if result > 0:
                self._fencing_token = int(result)
                logger.info(f"[LeaderElector] Acquired with fencing_token={self._fencing_token}")
                return True
            return False
        except Exception as e:
            logger.error(f"[LeaderElector] Acquire error: {e}")
            return False
```

**수신 측 검증 예시** (외부 시스템):
```python
# 수신 측에서 fencing token 검증
class FencingTokenValidator:
    """Fencing Token 검증기 (수신 측 구현)."""

    def __init__(self):
        self._last_seen_token: int = 0

    def validate_and_update(self, incoming_token: int) -> bool:
        """
        들어온 토큰이 유효한지 검증.

        Returns:
            True: 유효 (처리 진행)
            False: 무효 (stale leader, 거부)
        """
        if incoming_token < self._last_seen_token:
            logger.warning(
                f"[FencingToken] Rejected stale request: "
                f"incoming={incoming_token}, last_seen={self._last_seen_token}"
            )
            return False

        self._last_seen_token = incoming_token
        return True
```

---

### 6.2 리전 우선순위 (Region Priority)

**목적**: 특정 리전의 노드에게 리더십 우선권 부여

**코드 근거**:
- `ClusterIdentity` (cluster_identity.py)에 `region` 필드 존재
- 178번 문서에 `region_priority` 패턴 정의됨

**설정 추가**:

```python
# coordination/config.py 수정

class LeaderElectionSettings(BaseSettings):
    # ... 기존 설정 ...

    # 리전 우선순위 (낮을수록 높은 우선순위, 178번 문서와 일관)
    region_priority: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="리전 우선순위 (낮을수록 높음, 0=Primary)",
    )

    # Primary 리전 설정
    primary_region: str = Field(
        default="",
        description="Primary 리전 (예: ap-northeast-2)",
    )

    class Config:
        env_prefix = "SELFHEALING_LEADER_"
```

**Lua 스크립트 수정**:

```python
# coordination/redis_elector.py

class RedisLeaderElector(LeaderElector):
    # Lua 스크립트: 우선순위 기반 조건부 획득
    LUA_ACQUIRE_WITH_PRIORITY = """
    local key = KEYS[1]
    local fencing_key = KEYS[2]
    local new_value = ARGV[1]
    local ttl = tonumber(ARGV[2])
    local new_priority = tonumber(ARGV[3])

    -- 현재 리더 확인
    local current = redis.call("get", key)
    if current == false then
        -- 리더 없음 → 획득
        redis.call("set", key, new_value, "EX", ttl)
        local token = redis.call("incr", fencing_key)
        return token
    end

    -- 현재 리더 파싱
    local current_data = cjson.decode(current)
    local current_priority = current_data.region_priority or 100

    -- Tie-breaking: priority가 더 낮으면(높은 우선순위) 탈취
    if new_priority < current_priority then
        redis.call("set", key, new_value, "EX", ttl)
        local token = redis.call("incr", fencing_key)
        return token
    end

    return 0
    """
```

---

### 6.3 Lease 갱신 방어 로직 (Safe Margin & Self-Fencing)

**목적**:
1. 네트워크 지연으로 인한 lease 만료 방지
2. Stale leader 자기 보호

**코드 근거**:
- `utils/jitter.py`: Jitter 유틸리티 존재
- 현재 `renew_interval = lease_ttl / 3` 고정값

**설계 결정: 검증 로직 + 기본값 자동 계산 (하이브리드)**

| 접근법 | 장점 | 단점 |
|--------|------|------|
| 자동 계산 | 설정 오류 방지, 단순 | 유연성 부족, 기존 설정 무시 |
| 검증 로직 | 유연성, Use case별 튜닝 | 비최적 설정 가능 |
| **하이브리드** | 안전한 기본값 + 튜닝 가능 | 구현 복잡도 약간 증가 |

**하이브리드 선택 이유**:
1. DLQ Consumer(빠른 failover) vs Scheduler(안정성) 등 요구사항 상이
2. Pydantic `@field_validator`와 자연스럽게 통합
3. 운영팀의 네트워크 환경별 튜닝 허용
4. 잘못된 설정은 ValidationError로 차단

**설정 추가**:

```python
# coordination/config.py

from pydantic import BaseSettings, Field, field_validator, model_validator

class LeaderElectionSettings(BaseSettings):
    # ... 기존 설정 ...

    # Lease TTL
    lease_ttl_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Lease 유효 기간 (초)",
    )

    # 갱신 주기 (None이면 자동 계산)
    renew_interval_seconds: float | None = Field(
        default=None,
        ge=1.0,
        description="Lease 갱신 주기 (초). None이면 자동 계산.",
    )

    # Safe Margin (lease_ttl의 비율)
    lease_safety_margin_ratio: float = Field(
        default=0.1,
        ge=0.05,
        le=0.3,
        description="Lease 갱신 안전 마진 (TTL 대비 비율, 기본 10%)",
    )

    # Retry Jitter (Thundering Herd 방지)
    retry_jitter_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="재시도 간격 Jitter 비율 (0.5 = ±50%)",
    )

    # Self-Fencing 활성화
    self_fencing_enabled: bool = Field(
        default=True,
        description="Lease 갱신 실패 시 즉시 step-down",
    )

    @model_validator(mode="after")
    def validate_timing_constraints(self) -> "LeaderElectionSettings":
        """타이밍 제약 조건 검증."""
        effective_interval = self.get_effective_renew_interval()

        # 검증 1: renew_interval < lease_ttl / 2 (최소 2회 갱신 기회)
        max_allowed = self.lease_ttl_seconds / 2
        if effective_interval >= max_allowed:
            raise ValueError(
                f"renew_interval ({effective_interval}s) must be < "
                f"lease_ttl/2 ({max_allowed}s) for safe renewal"
            )

        # 검증 2: renew_interval > 1초 (너무 빈번한 갱신 방지)
        if effective_interval < 1.0:
            raise ValueError(
                f"renew_interval ({effective_interval}s) must be >= 1s"
            )

        # 경고: 권장 범위 (lease_ttl/4 ~ lease_ttl/3)
        recommended_min = self.lease_ttl_seconds / 4
        recommended_max = self.lease_ttl_seconds / 3
        if not (recommended_min <= effective_interval <= recommended_max):
            import logging
            logging.getLogger(__name__).warning(
                f"renew_interval ({effective_interval}s) outside recommended "
                f"range [{recommended_min}s, {recommended_max}s]"
            )

        return self

    def get_effective_renew_interval(self) -> float:
        """
        실제 사용될 갱신 주기 반환.

        - 사용자 지정 값이 있으면 그 값 사용 (검증됨)
        - 없으면 자동 계산: lease_ttl/3 - safety_margin
        """
        if self.renew_interval_seconds is not None:
            return self.renew_interval_seconds

        # 자동 계산: TTL의 1/3에서 안전 마진 차감
        base = self.lease_ttl_seconds / 3
        margin = self.lease_ttl_seconds * self.lease_safety_margin_ratio
        return max(base - margin, 1.0)
```

**사용 예시**:

```python
# 기본값 사용 (자동 계산)
settings = LeaderElectionSettings(lease_ttl_seconds=30)
print(settings.get_effective_renew_interval())  # 7.0초 (30/3 - 30*0.1)

# 커스텀 설정 (검증됨)
settings = LeaderElectionSettings(
    lease_ttl_seconds=60,
    renew_interval_seconds=15.0,  # 명시적 지정
)

# 잘못된 설정 → ValidationError
settings = LeaderElectionSettings(
    lease_ttl_seconds=30,
    renew_interval_seconds=20.0,  # 30/2=15보다 큼 → 에러!
)
# ValueError: renew_interval (20.0s) must be < lease_ttl/2 (15.0s)
```

**Self-Fencing 구현**:

```python
# coordination/redis_elector.py

class RedisLeaderElector(LeaderElector):
    def is_lease_valid(self) -> bool:
        """
        Self-Fencing: 현재 lease가 유효한지 확인.

        장기 실행 작업 중간에 호출하여 stale leader 감지.

        Usage:
            while processing:
                if not elector.is_lease_valid():
                    logger.warning("Lease invalid, aborting work")
                    break
                do_work()

        Returns:
            True: lease 유효, 작업 계속
            False: lease 무효, 즉시 중단 필요
        """
        if not self.is_leader():
            return False

        try:
            leader = self.get_leader()
            return leader is not None and leader.is_self
        except Exception:
            # 연결 실패 시 안전하게 False
            return False

    def _run_loop(self) -> None:
        """선출 루프 (Jitter + Self-Fencing 적용)."""
        from selfhealing.utils.jitter import calculate_jitter

        consecutive_failures = 0

        while self._running:
            try:
                with self._lock:
                    current_state = self._state

                if current_state == LeadershipState.LEADER:
                    # 리더: Lease 갱신
                    if self._renew_lease():
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1

                        # Self-Fencing: 즉시 step-down
                        if self._settings.self_fencing_enabled:
                            logger.warning(
                                f"[LeaderElector] Lease renew failed, stepping down "
                                f"(self_fencing_enabled=True)"
                            )
                            self._lose_leader()
                            consecutive_failures = 0
                        elif consecutive_failures >= self._settings.max_retry_attempts > 0:
                            self._lose_leader()
                            consecutive_failures = 0

                    # Safe Margin 적용된 갱신 주기
                    time.sleep(self._settings.get_safe_renew_interval())

                else:
                    # 팔로워: 리더 획득 시도
                    if self._try_acquire():
                        self._become_leader()
                        consecutive_failures = 0
                    else:
                        with self._lock:
                            self._state = LeadershipState.FOLLOWER

                    # Jitter 적용 (Thundering Herd 방지)
                    jitter = calculate_jitter(
                        max_delay_seconds=self._settings.retry_interval_seconds * self._settings.retry_jitter_factor,
                        min_delay_seconds=0,
                    )
                    time.sleep(self._settings.retry_interval_seconds + jitter)

            except Exception as e:
                logger.error(f"[LeaderElector] Loop error: {e}")
                time.sleep(self._settings.retry_interval_seconds)
```

---

### 6.4 Graceful Shutdown 통합

**목적**: SIGTERM 수신 시 리더십을 안전하게 반납

**코드 근거**:
- `core/shutdown_coordinator.py`: `GracefulShutdownCoordinator` 존재
- `register_signals()` 메서드로 SIGTERM 핸들링 지원

**구현**:

```python
# coordination/shutdown_integration.py
"""
Leader Elector Graceful Shutdown 통합.

코드 근거:
- core/shutdown_coordinator.py: GracefulShutdownCoordinator
- services/coordination/recovery_shutdown.py: ShutdownHandler 패턴
"""

from __future__ import annotations

import atexit
import logging
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.coordination.base import LeaderElector

logger = logging.getLogger(__name__)

_registered_electors: list[LeaderElector] = []


def register_for_graceful_shutdown(elector: "LeaderElector") -> None:
    """
    LeaderElector를 Graceful Shutdown에 등록.

    SIGTERM/SIGINT 수신 시 자동으로 stop() 호출.

    Usage:
        elector = get_leader_elector("dlq-consumer")
        register_for_graceful_shutdown(elector)
        elector.start()
    """
    global _registered_electors

    if elector not in _registered_electors:
        _registered_electors.append(elector)

    # 최초 등록 시 시그널 핸들러 설정
    if len(_registered_electors) == 1:
        _setup_signal_handlers()
        atexit.register(_shutdown_all_electors)


def _setup_signal_handlers() -> None:
    """SIGTERM/SIGINT 핸들러 설정."""
    if sys.platform == "win32":
        # Windows는 SIGTERM 미지원
        signal.signal(signal.SIGINT, _signal_handler)
    else:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    logger.info("[LeaderElector] Graceful shutdown handlers registered")


def _signal_handler(signum, frame) -> None:
    """시그널 핸들러."""
    logger.info(f"[LeaderElector] Received signal {signum}, initiating shutdown")
    _shutdown_all_electors()


def _shutdown_all_electors() -> None:
    """모든 등록된 Elector 종료."""
    global _registered_electors

    for elector in _registered_electors:
        try:
            logger.info(f"[LeaderElector] Stopping {elector.resource_name}")
            elector.stop()
            logger.info(f"[LeaderElector] Stopped {elector.resource_name}")
        except Exception as e:
            logger.error(f"[LeaderElector] Failed to stop {elector.resource_name}: {e}")

    _registered_electors.clear()


# GracefulShutdownCoordinator 통합 (선택적)
def integrate_with_shutdown_coordinator() -> None:
    """
    GracefulShutdownCoordinator와 통합.

    기존 시스템의 Graceful Shutdown 인프라 활용.

    코드 근거:
    - services/coordination/recovery_shutdown.py#L138-155
    """
    try:
        from selfhealing.core.shutdown_coordinator import (
            GracefulShutdownCoordinator,
            ShutdownHandler,
        )

        class LeaderElectorShutdownHandler(ShutdownHandler):
            def on_shutdown_start(self) -> None:
                logger.info("[LeaderElector] Shutdown start - releasing leadership")
                _shutdown_all_electors()

            def on_drain_complete(self) -> None:
                pass

            def on_force_shutdown(self, pending_requests) -> None:
                _shutdown_all_electors()

        # 핸들러 등록 로직은 앱 초기화 시 수행
        logger.info("[LeaderElector] GracefulShutdownCoordinator integration ready")

    except ImportError:
        logger.debug("[LeaderElector] GracefulShutdownCoordinator not available")
```

---

### 6.5 비동기 콜백 처리

**목적**: 콜백 실행이 Lease 갱신을 블로킹하지 않도록 분리

**코드 근거**:
- 프로젝트에서 `ThreadPoolExecutor` 광범위하게 사용

**구현**:

```python
# coordination/redis_elector.py 수정

from concurrent.futures import ThreadPoolExecutor

class RedisLeaderElector(LeaderElector):
    def __init__(self, resource_name: str, ...):
        # ... 기존 코드 ...

        # 콜백 실행용 스레드 풀 (리더십 루프와 분리)
        self._callback_executor: ThreadPoolExecutor | None = None

    def _get_callback_executor(self) -> ThreadPoolExecutor:
        """콜백 실행용 스레드 풀 (lazy init)."""
        if self._callback_executor is None:
            self._callback_executor = ThreadPoolExecutor(
                max_workers=2,  # become/lose 각 1개
                thread_name_prefix=f"LeaderCallback-{self._resource_name}",
            )
        return self._callback_executor

    def _become_leader(self) -> None:
        """리더 됨 (비동기 콜백)."""
        with self._lock:
            if self._state == LeadershipState.LEADER:
                return
            self._state = LeadershipState.LEADER

        logger.info(f"[LeaderElector] Became leader for {self._resource_name}")

        # 콜백을 별도 스레드에서 실행 (논블로킹)
        executor = self._get_callback_executor()
        for callback in self._on_become_callbacks:
            executor.submit(self._safe_callback, callback, "on_become_leader")

    def _lose_leader(self) -> None:
        """리더십 상실 (비동기 콜백)."""
        with self._lock:
            if self._state != LeadershipState.LEADER:
                return
            self._state = LeadershipState.FOLLOWER

        logger.info(f"[LeaderElector] Lost leadership for {self._resource_name}")

        # 콜백을 별도 스레드에서 실행 (논블로킹)
        executor = self._get_callback_executor()
        for callback in self._on_lose_callbacks:
            executor.submit(self._safe_callback, callback, "on_lose_leader")

    def _safe_callback(self, callback, callback_type: str) -> None:
        """안전한 콜백 실행 (예외 격리)."""
        try:
            callback()
        except Exception as e:
            logger.error(f"[LeaderElector] {callback_type} callback error: {e}")

    def stop(self) -> None:
        """리더 선출 중지."""
        # ... 기존 stop 로직 ...

        # 콜백 스레드 풀 종료
        if self._callback_executor:
            self._callback_executor.shutdown(wait=True, cancel_futures=False)
            self._callback_executor = None

        logger.info(f"[LeaderElector] Stopped for {self._resource_name}")
```

---

### 6.6 Recovery Audit 통합

**목적**: 리더십 변경 이력을 영구 기록하여 사후 분석 지원

**코드 근거**:
- `services/coordination/recovery_audit.py`: `RecoveryAuditRecorder` 존재
- `meta/watchdog.py`에서 동일 패턴 사용

**RecoveryAuditEventType 확장**:

```python
# services/coordination/recovery_audit.py 수정

class RecoveryAuditEventType(str, Enum):
    # ... 기존 타입 ...

    # Leader Election 이벤트 (추가)
    LEADER_ELECTED = "leader_elected"
    """리더로 선출됨."""

    LEADER_LOST = "leader_lost"
    """리더십 상실 (정상)."""

    LEADER_STEPPED_DOWN = "leader_stepped_down"
    """리더십 자진 반납 (self-fencing)."""

    LEADER_ELECTION_FAILED = "leader_election_failed"
    """리더 선출 실패."""
```

**LeaderElector 통합**:

```python
# coordination/redis_elector.py 수정

class RedisLeaderElector(LeaderElector):
    def _get_audit_recorder(self):
        """RecoveryAuditRecorder 획득 (선택적)."""
        try:
            from selfhealing.services.coordination.recovery_audit import (
                get_recovery_audit_recorder,
            )
            return get_recovery_audit_recorder()
        except (ImportError, Exception):
            return None

    def _record_leadership_event(
        self,
        event_type: str,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        """리더십 이벤트 Audit 기록."""
        recorder = self._get_audit_recorder()
        if recorder is None:
            return

        try:
            from selfhealing.services.coordination.recovery_audit import (
                RecoveryAuditEventType,
            )

            recorder.record_recovery_event(
                event_type=RecoveryAuditEventType(event_type),
                session_id=f"leader-{self._resource_name}-{self._node_id}",
                namespace=self._resource_name,
                executed_by=self._node_id,
                success=success,
                error_message=error_message,
                metadata={
                    "fencing_token": self._fencing_token,
                    "region_priority": self._settings.region_priority,
                },
            )
        except Exception as e:
            logger.debug(f"[LeaderElector] Audit record failed: {e}")

    def _become_leader(self) -> None:
        """리더 됨."""
        # ... 기존 로직 ...
        self._record_leadership_event("leader_elected")

    def _lose_leader(self, reason: str = "normal") -> None:
        """리더십 상실."""
        # ... 기존 로직 ...
        event_type = "leader_stepped_down" if reason == "self_fencing" else "leader_lost"
        self._record_leadership_event(event_type)
```

---

### 6.7 Prometheus 메트릭

**목적**: 리더 상태 모니터링을 위한 메트릭 노출

**코드 근거**:
- `services/coordination/recovery_metrics.py`: Prometheus 메트릭 패턴

**메트릭 정의**:

```python
# coordination/metrics.py
"""
Leader Election Prometheus Metrics.

코드 근거:
- services/coordination/recovery_metrics.py: Prometheus 패턴
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# =============================================================================
# Prometheus Metrics
# =============================================================================

try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    class DummyMetric:
        def labels(self, **kwargs):
            return self
        def inc(self, amount=1):
            pass
        def set(self, value):
            pass
        def observe(self, value):
            pass

    def Counter(*args, **kwargs):
        return DummyMetric()

    def Gauge(*args, **kwargs):
        return DummyMetric()

    def Histogram(*args, **kwargs):
        return DummyMetric()


# 리더 여부 (1 = 리더, 0 = 팔로워)
LEADER_ELECTOR_IS_LEADER = Gauge(
    "selfhealing_leader_elector_is_leader",
    "Whether this node is the current leader (1=leader, 0=follower)",
    ["resource_name", "node_id"],
)

# Lease 만료 예정 타임스탬프 (Unix epoch)
LEADER_ELECTOR_LEASE_EXPIRE_TIMESTAMP = Gauge(
    "selfhealing_leader_elector_lease_expire_timestamp",
    "Unix timestamp when the current lease expires",
    ["resource_name"],
)

# Lease 갱신 실패 횟수
LEADER_ELECTOR_RENEW_ERRORS_TOTAL = Counter(
    "selfhealing_leader_elector_renew_errors_total",
    "Total number of lease renewal failures",
    ["resource_name", "error_type"],
)

# 리더 선출 횟수
LEADER_ELECTOR_ELECTIONS_TOTAL = Counter(
    "selfhealing_leader_elector_elections_total",
    "Total number of leader elections",
    ["resource_name", "node_id"],
)

# 리더십 유지 시간
LEADER_ELECTOR_LEADERSHIP_DURATION_SECONDS = Histogram(
    "selfhealing_leader_elector_leadership_duration_seconds",
    "Duration of leadership in seconds",
    ["resource_name"],
    buckets=[10, 30, 60, 300, 600, 1800, 3600, 7200],
)


# =============================================================================
# Metrics Helper
# =============================================================================

class LeaderElectorMetrics:
    """Leader Elector 메트릭 헬퍼."""

    def __init__(self, resource_name: str, node_id: str):
        self._resource_name = resource_name
        self._node_id = node_id
        self._leadership_start_time: float | None = None

    def set_leader(self, is_leader: bool) -> None:
        """리더 상태 설정."""
        LEADER_ELECTOR_IS_LEADER.labels(
            resource_name=self._resource_name,
            node_id=self._node_id,
        ).set(1 if is_leader else 0)

    def set_lease_expire_timestamp(self, expire_timestamp: float) -> None:
        """Lease 만료 시간 설정."""
        LEADER_ELECTOR_LEASE_EXPIRE_TIMESTAMP.labels(
            resource_name=self._resource_name,
        ).set(expire_timestamp)

    def record_renew_error(self, error_type: str = "unknown") -> None:
        """Lease 갱신 실패 기록."""
        LEADER_ELECTOR_RENEW_ERRORS_TOTAL.labels(
            resource_name=self._resource_name,
            error_type=error_type,
        ).inc()

    def record_election(self) -> None:
        """리더 선출 기록."""
        LEADER_ELECTOR_ELECTIONS_TOTAL.labels(
            resource_name=self._resource_name,
            node_id=self._node_id,
        ).inc()
        self._leadership_start_time = time.time()

    def record_leadership_end(self) -> None:
        """리더십 종료 기록."""
        if self._leadership_start_time:
            duration = time.time() - self._leadership_start_time
            LEADER_ELECTOR_LEADERSHIP_DURATION_SECONDS.labels(
                resource_name=self._resource_name,
            ).observe(duration)
            self._leadership_start_time = None
```

---

## 7. 구현 체크리스트

### 7.1 기본 구현
- [ ] `coordination/__init__.py` 생성
- [ ] `coordination/config.py` 구현
- [ ] `coordination/base.py` 구현
- [ ] `coordination/redis_elector.py` 구현
- [ ] `coordination/factory.py` 구현

### 7.2 고급 기능 (6장)
- [ ] Fencing Token 지원 (6.1)
- [ ] 리전 우선순위 (6.2)
- [ ] Safe Margin & Self-Fencing (6.3)
- [ ] Graceful Shutdown 통합 (6.4)
- [ ] 비동기 콜백 처리 (6.5)
- [ ] Recovery Audit 통합 (6.6)
- [ ] Prometheus 메트릭 (6.7)

### 7.3 연동 및 테스트
- [ ] DLQ Consumer 연동
- [ ] Scheduler 연동
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성
- [ ] (선택) etcd 구현

---

## 8. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [redis_lock.py](../../packages/selfhealing-python/src/selfhealing/core/redis_lock.py) - 기존 분산 락

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
