# 179. Global Leader Election 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 3-5일
> **예상 코드량**: ~800줄

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

## 6. 구현 체크리스트

- [ ] `coordination/__init__.py` 생성
- [ ] `coordination/config.py` 구현
- [ ] `coordination/base.py` 구현
- [ ] `coordination/redis_elector.py` 구현
- [ ] `coordination/factory.py` 구현
- [ ] DLQ Consumer 연동
- [ ] Scheduler 연동
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성
- [ ] (선택) etcd 구현

---

## 7. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [redis_lock.py](../../packages/selfhealing-python/src/selfhealing/core/redis_lock.py) - 기존 분산 락

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
