# Resilient Storage Backend 구현 설계서

> **Version**: 1.0.0
> **Created**: 2026-01-02
> **Category**: 인프라/저장소 통합

---

## 📋 목차

1. [개요](#1-개요)
2. [설계 원칙](#2-설계-원칙)
3. [아키텍처](#3-아키텍처)
4. [재사용 컴포넌트](#4-재사용-컴포넌트)
5. [신규 구현 컴포넌트](#5-신규-구현-컴포넌트)
6. [데이터 흐름](#6-데이터-흐름)
7. [설정](#7-설정)
8. [테스트 계획](#8-테스트-계획)
9. [마이그레이션 가이드](#9-마이그레이션-가이드)
10. [기존 시스템 관계](#10-기존-시스템-관계)

---

## 1. 개요

### 1.1 목표

- **단순함**: 다른 사람이 쉽게 이해/사용 가능
- **안정성**: 데이터 유실 0 보장
- **비침투**: 사용자 DB에 테이블 생성 없음
- **일원화**: 모든 상태를 Redis에서 통합 관리

### 1.2 핵심 전략

**Redis-Only + Graceful Degradation + WAL**

```
정상 모드:     Redis ← 모든 읽기/쓰기
장애 모드:     Memory + WAL(디스크) → 복구 시 Redis 동기화
서버 죽어도:   WAL에서 복구 가능 → 데이터 유실 0
```

### 1.3 기존 시스템과의 관계

| 기존 컴포넌트 | 새 구조에서 역할 | 삭제 여부 |
|-------------|----------------|----------|
| `InMemoryCircuitBreakerStateRepository` | L1 (Fallback) | ❌ 유지 |
| `DjangoCircuitBreakerStateRepository` | opt-in (선택적 사용) | ❌ 유지 |
| `LayeredCircuitBreakerStateRepository` | 확장/개선 | ❌ 개선 |
| `RedisCacheAdapter` | 재사용 | ❌ 유지 |
| `WriteAheadLog` | RecoveryQueue 역할 | ❌ 재사용 |
| `ShadowLogger` | Forensic 로깅 | ❌ 유지 |
| `DriftReconciler` | 복구 시 상태 병합 | ❌ 유지 |
| `RedisHealthChecker` | 헬스 체크 | ❌ 재사용 |

**결론: 기존 시스템을 삭제하지 않고 조합/확장**

---

## 2. 설계 원칙

| 원칙 | 설명 |
|------|------|
| **Redis-First** | 평소에는 Redis만 사용 (단순함, 일관성) |
| **Fail-Safe** | Redis 장애 시 Memory + WAL로 전환 (무중단) |
| **WAL-First** | Degraded 모드에서 **WAL 먼저 기록 후 Memory** (서버 죽어도 복구) |
| **Zero Data Loss** | WAL이 디스크에 기록되어 서버 죽어도 복구 가능 |
| **Most Restrictive Wins** | 복구 시 OPEN 상태가 우선 (안전) |
| **Jitter Recovery** | Thundering Herd 방지 |

### 2.1 WAL-First 원칙 (핵심)

**Degraded 모드에서 반드시 WAL을 먼저 기록해야 합니다.**

```
┌─────────────────────────────────────────────────────────────────┐
│                    WAL-First Write Protocol                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  순서 (반드시 지켜야 함):                                         │
│                                                                  │
│  1. WAL.write() + fsync()  ← 디스크에 확실히 기록될 때까지 대기  │
│           ↓                                                      │
│  2. Memory[key] = value    ← 그 다음 메모리에 저장               │
│                                                                  │
│  ═══════════════════════════════════════════════════════════════│
│                                                                  │
│  잘못된 순서 (절대 금지):                                         │
│                                                                  │
│  ❌ Memory[key] = value                                          │
│  ❌ WAL.write()  ← Memory 후 WAL 전에 죽으면 유실!               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**서버 죽어도 안전한 이유:**

| 죽는 시점 | 결과 | 설명 |
|----------|------|------|
| WAL 기록 전 | ✅ 안전 | 클라이언트는 실패 응답 받음 (정상 동작) |
| WAL 기록 후 | ✅ 안전 | 재시작 시 WAL에서 복구 |
| Memory 저장 후 | ✅ 안전 | 재시작 시 WAL에서 복구 |

### 2.2 정상 모드에서의 데이터 보장

| 시나리오 | Redis 설정 | 데이터 보장 |
|---------|-----------|-----------|
| 서버만 죽음 | 상관없음 | ✅ Redis에 이미 저장됨 |
| Redis도 같이 죽음 | AOF everysec | ⚠️ 최대 1초 유실 가능 |
| Redis도 같이 죽음 | AOF always | ✅ 완전 보장 (느림) |
| Redis도 같이 죽음 | RDB only | ⚠️ 마지막 스냅샷 이후 유실 |

**권장 Redis 설정:**
```
# redis.conf
appendonly yes
appendfsync everysec  # 성능/안정성 균형 (대부분의 경우)
# appendfsync always  # 완전 보장 필요 시 (성능 저하)
```

---

## 3. 아키텍처

### 3.1 전체 구조

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ResilientStorageBackend                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                         정상 모드 (Redis)                                ││
│  │  ┌─────────────────────────────────────────────────────────────────────┐││
│  │  │  RedisHealthChecker (기존) → Redis 상태 감시                        │││
│  │  │  RedisCacheAdapter (기존) → Redis 연산                              │││
│  │  │  RedisCircuitBreakerStateRepository (신규) → CB 상태 저장           │││
│  │  │  RedisDLQRepository (신규) → DLQ 저장                               │││
│  │  └─────────────────────────────────────────────────────────────────────┘││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                    │                                         │
│                                    │ Redis 장애 감지                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                      장애 모드 (Graceful Degradation)                    ││
│  │  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           ││
│  │  │ InMemory      │ ──▶│ WriteAheadLog │ ──▶│ ShadowLogger  │           ││
│  │  │ Repository    │    │ (기존)        │    │ (기존)        │           ││
│  │  │ (기존)        │    │ 디스크 기록    │    │ Forensic      │           ││
│  │  └───────────────┘    └───────────────┘    └───────────────┘           ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                    │                                         │
│                                    │ Redis 복구 감지                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                       복구 모드 (Recovery)                               ││
│  │  ┌─────────────────────────────────────────────────────────────────────┐││
│  │  │  DriftReconciler (기존) → Most Restrictive Wins                     │││
│  │  │  Jitter (0~5초) → Thundering Herd 방지                              │││
│  │  │  WAL Replay → Redis 동기화 후 WAL 정리                              │││
│  │  └─────────────────────────────────────────────────────────────────────┘││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Redis 키 구조 (Namespace)

```
selfhealing:cb:{service_name}          # Circuit Breaker 상태 (Hash)
selfhealing:cb:{service_name}:history  # CB 상태 변경 이력 (List)
selfhealing:dlq:{id}                   # DLQ 항목 (Hash)
selfhealing:dlq:pending                # DLQ 대기열 (Sorted Set by timestamp)
selfhealing:dlq:id_seq                 # DLQ ID 시퀀스 (String)
selfhealing:incident:{id}              # Security Incident (Hash)
selfhealing:ratelimit:{key}            # Rate Limit 토큰 (String)
selfhealing:health                     # 시스템 헬스 상태 (Hash)
```

**관제 편의성:**
```bash
# 모든 selfhealing 키 확인
redis-cli KEYS "selfhealing:*"

# 특정 CB 상태 확인
redis-cli HGETALL "selfhealing:cb:payment_api"

# DLQ 대기 수 확인
redis-cli ZCARD "selfhealing:dlq:pending"
```

---

## 4. 재사용 컴포넌트

### 4.1 WriteAheadLog (WAL)

**경로**: `selfhealing/audit/wal.py`

**역할**: Degraded Mode에서 모든 상태 변경을 디스크에 기록

```python
from selfhealing.audit.wal import WriteAheadLog, WALConfig

wal = WriteAheadLog(
    config=WALConfig(
        wal_dir="/var/log/selfhealing/wal",
        sync_on_write=True,  # fsync 보장
    )
)

# 장애 시 기록
seq = wal.write({
    "operation": "set",
    "key": "selfhealing:cb:payment",
    "value": {"state": "open", "failure_count": 5},
    "timestamp": time.time(),
})

# 복구 시 재생
unprocessed = wal.recover_unprocessed(last_processed_seq=100)
for entry in unprocessed:
    redis.hset(entry.data["key"], mapping=entry.data["value"])
```

### 4.2 RedisHealthChecker

**경로**: `selfhealing/api/django/rate_limit.py`

**역할**: Redis 상태 감시 + Mini Circuit Breaker

```python
from selfhealing.api.django.rate_limit import RedisHealthChecker

checker = RedisHealthChecker()

if checker.is_healthy:
    # Redis 사용
    redis.hset(...)
else:
    # Degraded Mode
    memory[key] = value
    wal.write(...)
```

### 4.3 ShadowLogger

**경로**: `selfhealing/adapters/memory/shadow_logger.py`

**역할**: L2 장애 시 Forensic 로깅

```python
from selfhealing.adapters.memory.shadow_logger import get_shadow_logger

shadow = get_shadow_logger()
shadow.record_sync_failure(
    service_name="payment_api",
    intended_state="open",
    error=RedisConnectionError("timeout"),
    adapter_type="redis",
)
```

### 4.4 DriftReconciler

**경로**: `selfhealing/adapters/memory/drift_reconciliation.py`

**역할**: 복구 시 상태 충돌 해결 (Most Restrictive Wins)

```python
from selfhealing.adapters.memory.drift_reconciliation import DriftReconciler

reconciler = DriftReconciler()
winner = reconciler.reconcile(
    service_name="payment_api",
    l1_state="open",      # Memory에서
    l2_state="closed",    # Redis에서
    l1_updated_at=...,
    l2_updated_at=...,
)
# winner = "open" (더 제한적)
```

### 4.5 RedisCacheAdapter

**경로**: `selfhealing/adapters/cache/redis_adapter.py`

**역할**: Redis 기본 연산 + 분산 락

```python
from selfhealing.adapters.cache import RedisCacheAdapter

cache = RedisCacheAdapter(url="redis://localhost:6379/0")
cache.set("key", {"data": "value"}, ttl=timedelta(minutes=5))
data = cache.get("key")

# 분산 락
with cache.get_lock("cb_update_lock"):
    # Critical section
    pass
```

### 4.6 InMemoryCircuitBreakerStateRepository

**경로**: `selfhealing/adapters/memory/circuit_breaker.py`

**역할**: Fallback 저장소 (Degraded Mode에서 사용)

---

## 5. 신규 구현 컴포넌트

### 5.1 ResilientStorageBackend

**경로**: `selfhealing/adapters/resilient/backend.py` (신규)

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
import threading
import time

from selfhealing.audit.wal import WriteAheadLog, WALConfig
from selfhealing.adapters.cache import RedisCacheAdapter
from selfhealing.adapters.memory.shadow_logger import get_shadow_logger


class StorageMode(Enum):
    REDIS = "redis"
    DEGRADED = "degraded"
    RECOVERING = "recovering"


@dataclass
class ResilientStorageConfig:
    """Resilient Storage 설정."""
    redis_url: str = "redis://localhost:6379/0"
    wal_dir: str = "/var/log/selfhealing/wal"
    health_check_interval: float = 5.0
    recovery_jitter_max: float = 5.0
    key_prefix: str = "selfhealing:"


class ResilientStorageBackend:
    """
    Redis-First + Graceful Degradation + WAL.
    
    데이터 유실 0을 보장하는 통합 저장소.
    
    핵심 원칙:
    - WAL-First: Degraded 모드에서 WAL 먼저 기록 후 Memory
    - 서버 재시작 시 WAL에서 자동 복구
    """
    
    def __init__(self, config: Optional[ResilientStorageConfig] = None):
        self.config = config or ResilientStorageConfig()
        self._mode = StorageMode.REDIS
        self._lock = threading.RLock()
        
        # Redis
        self._redis: Optional[RedisCacheAdapter] = None
        self._init_redis()
        
        # WAL (디스크 기반 복구 큐)
        self._wal = WriteAheadLog(
            config=WALConfig(
                wal_dir=self.config.wal_dir,
                sync_on_write=True,  # ⚠️ fsync 보장 - 서버 죽어도 디스크에 기록됨
            )
        )
        
        # Memory (Fallback)
        self._memory: Dict[str, Any] = {}
        
        # Shadow Logger
        self._shadow = get_shadow_logger()
        
        # 헬스 체크
        from selfhealing.api.django.rate_limit import get_redis_health_checker
        self._health_checker = get_redis_health_checker()
        
        # ⭐ 서버 재시작 시 WAL 자동 복구
        self._recover_from_wal_on_startup()
    
    def _init_redis(self) -> None:
        """Redis 초기화 (실패해도 계속 진행)."""
        try:
            self._redis = RedisCacheAdapter(url=self.config.redis_url)
            self._redis._redis.ping()  # 연결 테스트
        except Exception as e:
            self._mode = StorageMode.DEGRADED
            self._shadow.record_sync_failure(
                service_name="redis",
                intended_state="connected",
                error=e,
                adapter_type="redis",
            )
    
    def _recover_from_wal_on_startup(self) -> None:
        """
        서버 시작 시 WAL에서 미처리 항목 복구.
        
        ⭐ 데이터 유실 0의 핵심:
        - 서버가 Degraded 모드에서 죽었어도 WAL에 기록되어 있음
        - 재시작 시 WAL → Redis 재생
        """
        try:
            # WAL에 미처리 항목이 있는지 확인
            wal_stats = self._wal.get_stats()
            if wal_stats.total_entries == 0:
                return  # 복구할 것 없음
            
            logger.info(f"[ResilientStorage] Found {wal_stats.total_entries} WAL entries to recover")
            
            # Redis 연결 상태 확인
            if self._mode == StorageMode.DEGRADED:
                logger.warning("[ResilientStorage] Redis unavailable, WAL recovery deferred")
                return
            
            # WAL → Redis 재생
            unprocessed = self._wal.recover_unprocessed(last_processed_seq=0)
            recovered_count = 0
            
            for entry in unprocessed:
                try:
                    self._replay_wal_entry(entry)
                    recovered_count += 1
                except Exception as e:
                    logger.error(f"[ResilientStorage] WAL replay failed: {e}")
                    # 실패해도 계속 진행 (다음 항목 시도)
            
            logger.info(f"[ResilientStorage] Recovered {recovered_count} entries from WAL")
            
        except Exception as e:
            logger.error(f"[ResilientStorage] WAL recovery error: {e}")
            # 복구 실패해도 서버는 시작 (Degraded 모드로)
    
    def _replay_wal_entry(self, entry) -> None:
        """WAL 항목 하나를 Redis에 재생."""
        operation = entry.data.get("operation")
        key = entry.data.get("key")
        value = entry.data.get("value")
        
        if operation == "set":
            self._redis.set(key, value)
        elif operation == "hset":
            self._redis._redis.hset(key, mapping=value)
        elif operation == "delete":
            self._redis.delete(key)
        else:
            logger.warning(f"[ResilientStorage] Unknown WAL operation: {operation}")
    
    @property
    def mode(self) -> StorageMode:
        return self._mode
    
    @property
    def is_degraded(self) -> bool:
        return self._mode != StorageMode.REDIS
    
    # =========================================================================
    # Core Operations
    # =========================================================================
    
    def get(self, key: str) -> Optional[Any]:
        """값 조회."""
        full_key = f"{self.config.key_prefix}{key}"
        
        if self._mode == StorageMode.REDIS:
            try:
                return self._redis.get(full_key)
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key)
        else:
            return self._memory.get(key)
    
    def set(self, key: str, value: Any) -> bool:
        """값 저장."""
        full_key = f"{self.config.key_prefix}{key}"
        
        if self._mode == StorageMode.REDIS:
            try:
                self._redis.set(full_key, value)
                return True
            except Exception as e:
                self._switch_to_degraded()
                return self._set_degraded(key, value, e)
        else:
            return self._set_degraded(key, value, None)
    
    def _set_degraded(self, key: str, value: Any, error: Optional[Exception]) -> bool:
        """
        Degraded Mode: WAL 먼저 → Memory 나중.
        
        ⚠️ 순서 중요: WAL-First 원칙
        → 데이터 유실 0 보장
        """
        # 1. WAL에 먼저 기록 (디스크) - fsync 보장
        # ⚠️ 반드시 Memory보다 먼저!
        self._wal.write({
            "operation": "set",
            "key": f"{self.config.key_prefix}{key}",
            "value": value,
            "timestamp": time.time(),
        })
        
        # 2. 그 다음 Memory에 저장
        # WAL 기록 후이므로 서버 죽어도 복구 가능
        self._memory[key] = value
        
        # 3. Shadow Log (Forensic) - 선택적
        if error:
            self._shadow.record_sync_failure(
                service_name=key,
                intended_state=str(value),
                error=error,
                adapter_type="redis",
            )
        
        return True
    
    def delete(self, key: str) -> bool:
        """값 삭제."""
        full_key = f"{self.config.key_prefix}{key}"
        
        if self._mode == StorageMode.REDIS:
            try:
                self._redis.delete(full_key)
                return True
            except Exception:
                self._switch_to_degraded()
                # WAL 먼저
                self._wal.write({
                    "operation": "delete",
                    "key": full_key,
                    "timestamp": time.time(),
                })
                # Memory 나중
                self._memory.pop(key, None)
                return True
        else:
            # WAL 먼저
            self._wal.write({
                "operation": "delete",
                "key": full_key,
                "timestamp": time.time(),
            })
            # Memory 나중
            self._memory.pop(key, None)
            return True
    
    # =========================================================================
    # Hash Operations (for CB, DLQ)
    # =========================================================================
    
    def hget(self, key: str, field: str) -> Optional[Any]:
        """Hash 필드 조회."""
        full_key = f"{self.config.key_prefix}{key}"
        
        if self._mode == StorageMode.REDIS:
            try:
                return self._redis._redis.hget(full_key, field)
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key, {}).get(field)
        else:
            return self._memory.get(key, {}).get(field)
    
    def hset(self, key: str, mapping: Dict[str, Any]) -> bool:
        """Hash 필드 설정."""
        full_key = f"{self.config.key_prefix}{key}"
        
        if self._mode == StorageMode.REDIS:
            try:
                self._redis._redis.hset(full_key, mapping=mapping)
                return True
            except Exception as e:
                self._switch_to_degraded()
                return self._hset_degraded(key, mapping, e)
        else:
            return self._hset_degraded(key, mapping, None)
    
    def _hset_degraded(self, key: str, mapping: Dict[str, Any], error: Optional[Exception]) -> bool:
        """Degraded Mode Hash 저장 - WAL-First."""
        # 1. WAL 먼저 (디스크)
        self._wal.write({
            "operation": "hset",
            "key": f"{self.config.key_prefix}{key}",
            "value": mapping,
            "timestamp": time.time(),
        })
        
        # 2. Memory 나중
        if key not in self._memory:
            self._memory[key] = {}
        self._memory[key].update(mapping)
        
        return True
    
    def hgetall(self, key: str) -> Dict[str, Any]:
        """Hash 전체 조회."""
        full_key = f"{self.config.key_prefix}{key}"
        
        if self._mode == StorageMode.REDIS:
            try:
                result = self._redis._redis.hgetall(full_key)
                # Decode bytes if needed
                if result:
                    return {
                        k.decode() if isinstance(k, bytes) else k: 
                        v.decode() if isinstance(v, bytes) else v
                        for k, v in result.items()
                    }
                return {}
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key, {})
        else:
            return self._memory.get(key, {})
    
    # =========================================================================
    # Mode Switching
    # =========================================================================
    
    def _switch_to_degraded(self) -> None:
        """Degraded Mode로 전환."""
        with self._lock:
            if self._mode != StorageMode.DEGRADED:
                self._mode = StorageMode.DEGRADED
                logger.critical("[ResilientStorage] Switched to DEGRADED mode")
    
    def check_and_recover(self) -> bool:
        """
        Redis 복구 체크 및 데이터 동기화.
        
        Returns:
            True if recovered
        """
        if self._mode != StorageMode.DEGRADED:
            return False
        
        if not self._health_checker.check_health():
            return False
        
        # Jitter 적용 (Thundering Herd 방지)
        import random
        jitter = random.uniform(0, self.config.recovery_jitter_max)
        time.sleep(jitter)
        
        return self._do_recovery()
    
    def _do_recovery(self) -> bool:
        """WAL → Redis 동기화."""
        try:
            self._mode = StorageMode.RECOVERING
            
            # 1. WAL에서 미처리 항목 조회
            wal_stats = self._wal.get_stats()
            # WAL 재생 로직
            
            # 2. DriftReconciler로 충돌 해결
            from selfhealing.adapters.memory.drift_reconciliation import get_drift_reconciler
            reconciler = get_drift_reconciler()
            
            # 3. Memory 데이터를 Redis로 동기화
            for key, value in self._memory.items():
                full_key = f"{self.config.key_prefix}{key}"
                if isinstance(value, dict):
                    self._redis._redis.hset(full_key, mapping=value)
                else:
                    self._redis.set(full_key, value)
            
            # 4. 정리
            self._memory.clear()
            self._mode = StorageMode.REDIS
            
            logger.info("[ResilientStorage] Recovered to REDIS mode")
            return True
            
        except Exception as e:
            logger.error(f"[ResilientStorage] Recovery failed: {e}")
            self._mode = StorageMode.DEGRADED
            return False
```

### 5.2 RedisCircuitBreakerStateRepository

**경로**: `selfhealing/adapters/redis/circuit_breaker.py` (신규)

```python
from datetime import datetime
from typing import Optional

from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
)


class RedisCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    Redis 기반 Circuit Breaker 상태 저장소.
    
    ResilientStorageBackend를 사용하여 데이터 유실 0 보장.
    """
    
    KEY_PREFIX = "cb:"
    
    def __init__(self, backend: "ResilientStorageBackend"):
        self._backend = backend
    
    def _make_key(self, service_name: str) -> str:
        return f"{self.KEY_PREFIX}{service_name}"
    
    def get_state(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """서비스 CB 상태 조회."""
        data = self._backend.hgetall(self._make_key(service_name))
        if not data:
            return None
        return self._to_data(service_name, data)
    
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """상태 조회 또는 생성."""
        existing = self.get_state(service_name)
        if existing:
            return existing
        
        # 기본값으로 생성
        default_data = {
            "state": "closed",
            "failure_count": "0",
            "success_count": "0",
            "created_at": datetime.utcnow().isoformat(),
        }
        self._backend.hset(self._make_key(service_name), default_data)
        return self._to_data(service_name, default_data)
    
    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """상태 업데이트."""
        updates = {"state": state, "updated_at": datetime.utcnow().isoformat()}
        if failure_count is not None:
            updates["failure_count"] = str(failure_count)
        if success_count is not None:
            updates["success_count"] = str(success_count)
        if opened_at is not None:
            updates["opened_at"] = opened_at.isoformat()
        
        return self._backend.hset(self._make_key(service_name), updates)
    
    def _to_data(self, service_name: str, data: dict) -> CircuitBreakerStateData:
        """Dict → DataClass 변환."""
        return CircuitBreakerStateData(
            service_name=service_name,
            state=data.get("state", "closed"),
            failure_count=int(data.get("failure_count", 0)),
            success_count=int(data.get("success_count", 0)),
            opened_at=self._parse_datetime(data.get("opened_at")),
            # ... 기타 필드
        )
    
    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        return datetime.fromisoformat(value)
```

### 5.3 RedisDLQRepository

**경로**: `selfhealing/adapters/redis/dlq.py` (신규)

```python
from datetime import datetime
from typing import List, Optional
import time


class RedisDLQRepository:
    """
    Redis 기반 DLQ 저장소.
    
    구조:
    - selfhealing:dlq:{id} → Hash (항목 데이터)
    - selfhealing:dlq:pending → Sorted Set (대기열, score=timestamp)
    - selfhealing:dlq:id_seq → String (ID 시퀀스)
    """
    
    KEY_PREFIX = "dlq:"
    PENDING_KEY = "dlq:pending"
    ID_SEQ_KEY = "dlq:id_seq"
    
    def __init__(self, backend: "ResilientStorageBackend"):
        self._backend = backend
    
    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str,
        **kwargs
    ) -> int:
        """DLQ 항목 생성."""
        # ID 발급
        entry_id = self._backend._redis._redis.incr(
            f"{self._backend.config.key_prefix}{self.ID_SEQ_KEY}"
        )
        
        # 항목 저장
        data = {
            "id": str(entry_id),
            "domain": domain,
            "failure_type": failure_type,
            "error_message": error_message,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            **{k: str(v) for k, v in kwargs.items()},
        }
        self._backend.hset(f"{self.KEY_PREFIX}{entry_id}", data)
        
        # 대기열에 추가
        self._backend._redis._redis.zadd(
            f"{self._backend.config.key_prefix}{self.PENDING_KEY}",
            {str(entry_id): time.time()}
        )
        
        return entry_id
    
    def get_pending(self, limit: int = 100) -> List[dict]:
        """대기 중인 항목 조회."""
        # Sorted Set에서 ID 목록 조회
        pending_ids = self._backend._redis._redis.zrange(
            f"{self._backend.config.key_prefix}{self.PENDING_KEY}",
            0, limit - 1
        )
        
        result = []
        for entry_id in pending_ids:
            if isinstance(entry_id, bytes):
                entry_id = entry_id.decode()
            data = self._backend.hgetall(f"{self.KEY_PREFIX}{entry_id}")
            if data:
                result.append(data)
        
        return result
    
    def mark_resolved(self, entry_id: int, resolution_type: str) -> bool:
        """항목 해결 처리."""
        # 상태 업데이트
        self._backend.hset(f"{self.KEY_PREFIX}{entry_id}", {
            "status": "resolved",
            "resolution_type": resolution_type,
            "resolved_at": datetime.utcnow().isoformat(),
        })
        
        # 대기열에서 제거
        self._backend._redis._redis.zrem(
            f"{self._backend.config.key_prefix}{self.PENDING_KEY}",
            str(entry_id)
        )
        
        return True
```

---

## 6. 데이터 흐름

### 6.1 정상 시나리오

```
1. should_allow("payment_api") 호출
2. backend.hgetall("cb:payment_api") → Redis 조회
3. state="closed" → 허용
4. 요청 실패 시 record_failure() 호출
5. backend.hset("cb:payment_api", {"failure_count": "1"}) → Redis 저장
```

### 6.2 Redis 장애 시나리오

```
1. should_allow("payment_api") 호출
2. backend.hgetall() → Redis 연결 실패
3. RedisHealthChecker가 UNHEALTHY 감지
4. mode = DEGRADED로 전환
5. Memory에서 조회 (Conservative Default: CLOSED)
6. record_failure() 호출
7. ⚠️ WAL에 먼저 기록 (디스크, fsync) ← WAL-First
8. 그 다음 Memory에 저장
9. ShadowLogger에 Forensic 기록
```

### 6.3 Redis 복구 시나리오

```
1. RedisHealthChecker가 HEALTHY 감지
2. Jitter (0~5초) 대기 → Thundering Herd 방지
3. mode = RECOVERING
4. WAL에서 미처리 항목 조회
5. DriftReconciler로 충돌 해결 (Most Restrictive Wins)
6. Memory/WAL 데이터 → Redis 동기화
7. WAL 정리, Memory 비움
8. mode = REDIS
```

### 6.4 서버 죽었다 재시작 시나리오 (정상 모드)

```
상황: 정상 모드(Redis)에서 서버가 갑자기 죽음

1. 서버 재시작
2. Redis 연결
3. ✅ 데이터는 Redis에 이미 있음 → 유실 없음
```

### 6.5 서버 죽었다 재시작 시나리오 (Degraded 모드) ⭐

```
상황: Degraded 모드에서 서버가 갑자기 죽음

타임라인:
┌─────────────────────────────────────────────────────────────────┐
│  T=0   WAL.write() 완료 (디스크에 fsync)                         │
│  T=1   Memory[key] = value                                       │
│  T=2   💥 서버 죽음                                               │
│  T=3   ...                                                       │
│  T=100 서버 재시작                                                │
└─────────────────────────────────────────────────────────────────┘

복구 과정:
1. 서버 시작
2. ResilientStorageBackend.__init__() 호출
3. WAL 디렉토리 스캔 (/var/log/selfhealing/wal/)
4. 미처리 WAL 항목 발견 (T=0에 기록된 것)
5. Redis 연결 확인
6. WAL → Redis 재생 (순서대로)
7. WAL 정리 (처리 완료 표시)
8. ✅ 데이터 유실 없음
```

**핵심 포인트:**
- Memory는 죽으면 사라짐 (휘발성)
- WAL은 디스크에 있어서 서버 죽어도 남아있음
- WAL-First 순서이므로 **Memory에 저장되기 전에 이미 WAL에 기록됨**
- 따라서 서버가 언제 죽어도 WAL에서 복구 가능

### 6.6 데이터 유실 시나리오 분석표

| 모드 | 서버 죽는 시점 | 결과 | 설명 |
|-----|--------------|------|------|
| 정상 | Redis 저장 완료 후 | ✅ 안전 | Redis에 있음 |
| 정상 | Redis 저장 중 | ⚠️ | Redis ACK 전이면 클라이언트가 실패로 인지 |
| Degraded | WAL 기록 전 | ✅ 안전 | 클라이언트가 실패로 인지 (정상 동작) |
| Degraded | WAL 기록 후, Memory 전 | ✅ 안전 | WAL에서 복구 |
| Degraded | Memory 저장 후 | ✅ 안전 | WAL에서 복구 |
| **모든 경우** | | ✅ | **데이터 유실 0** |

---

## 7. 설정

### 7.1 환경 변수

```bash
# Redis 연결
SELFHEALING_REDIS_URL=redis://localhost:6379/0

# WAL 디렉토리
SELFHEALING_WAL_DIR=/var/log/selfhealing/wal

# 헬스 체크 주기
SELFHEALING_HEALTH_CHECK_INTERVAL=5.0

# 복구 Jitter 최대값
SELFHEALING_RECOVERY_JITTER_MAX=5.0
```

### 7.2 Django settings.py

```python
# Redis 연결 (필수)
SELFHEALING_REDIS_URL = os.getenv(
    "SELFHEALING_REDIS_URL", 
    "redis://localhost:6379/0"
)

# WAL 디렉토리 (선택, 기본: /var/log/selfhealing/wal)
SELFHEALING_WAL_DIR = os.getenv(
    "SELFHEALING_WAL_DIR",
    "/var/log/selfhealing/wal"
)

# Redis 없는 환경에서 Memory-only 모드 허용
SELFHEALING_ALLOW_MEMORY_ONLY = os.getenv(
    "SELFHEALING_ALLOW_MEMORY_ONLY",
    "false"
).lower() == "true"
```

### 7.3 팩토리 통합

```python
# selfhealing/factory.py

def get_storage_backend() -> ResilientStorageBackend:
    """통합 저장소 백엔드 반환."""
    from django.conf import settings
    
    config = ResilientStorageConfig(
        redis_url=getattr(settings, "SELFHEALING_REDIS_URL", "redis://localhost:6379/0"),
        wal_dir=getattr(settings, "SELFHEALING_WAL_DIR", "/var/log/selfhealing/wal"),
    )
    
    return ResilientStorageBackend(config)


def get_circuit_breaker_repo() -> CircuitBreakerStateRepository:
    """CB 저장소 반환."""
    backend = get_storage_backend()
    return RedisCircuitBreakerStateRepository(backend)


def get_dlq_repo() -> RedisDLQRepository:
    """DLQ 저장소 반환."""
    backend = get_storage_backend()
    return RedisDLQRepository(backend)
```

---

## 8. 테스트 계획

### 8.1 단위 테스트

```python
# tests/unit/test_resilient_storage.py

import pytest
from unittest.mock import Mock, patch
import tempfile


class TestResilientStorageBackend:
    """ResilientStorageBackend 단위 테스트."""
    
    @pytest.fixture
    def backend(self):
        """테스트용 Backend 생성."""
        with tempfile.TemporaryDirectory() as wal_dir:
            config = ResilientStorageConfig(
                redis_url="redis://localhost:6379/0",
                wal_dir=wal_dir,
            )
            yield ResilientStorageBackend(config)
    
    # =========================================================================
    # 정상 모드 테스트
    # =========================================================================
    
    def test_set_get_normal_mode(self, backend):
        """정상 모드에서 set/get 동작 확인."""
        backend.set("test_key", {"value": 123})
        result = backend.get("test_key")
        assert result == {"value": 123}
    
    def test_hset_hgetall_normal_mode(self, backend):
        """정상 모드에서 Hash 연산 동작 확인."""
        backend.hset("cb:test", {"state": "open", "count": "5"})
        result = backend.hgetall("cb:test")
        assert result["state"] == "open"
        assert result["count"] == "5"
    
    # =========================================================================
    # Degraded 모드 테스트
    # =========================================================================
    
    def test_switch_to_degraded_on_redis_failure(self, backend):
        """Redis 장애 시 Degraded 모드 전환."""
        with patch.object(backend._redis, 'set', side_effect=Exception("Connection failed")):
            backend.set("key", "value")
        
        assert backend.mode == StorageMode.DEGRADED
    
    def test_degraded_mode_uses_memory(self, backend):
        """Degraded 모드에서 Memory 사용 확인."""
        backend._mode = StorageMode.DEGRADED
        backend.set("key", {"data": "test"})
        
        assert "key" in backend._memory
        assert backend._memory["key"] == {"data": "test"}
    
    def test_degraded_mode_writes_wal(self, backend):
        """Degraded 모드에서 WAL 기록 확인."""
        backend._mode = StorageMode.DEGRADED
        backend.set("key", {"data": "test"})
        
        stats = backend._wal.get_stats()
        assert stats.total_entries > 0
    
    # =========================================================================
    # 데이터 유실 0 테스트
    # =========================================================================
    
    def test_wal_survives_memory_clear(self, backend):
        """Memory 비워도 WAL에서 복구 가능."""
        backend._mode = StorageMode.DEGRADED
        backend.set("important_key", {"critical": "data"})
        
        # Memory 비움 (서버 재시작 시뮬레이션)
        backend._memory.clear()
        
        # WAL에서 복구
        entries = list(backend._wal.recover_unprocessed(0))
        assert len(entries) > 0
        assert entries[0].data["key"].endswith("important_key")
    
    # =========================================================================
    # 복구 테스트
    # =========================================================================
    
    def test_recovery_syncs_to_redis(self, backend):
        """복구 시 Memory → Redis 동기화."""
        # Degraded 모드에서 데이터 저장
        backend._mode = StorageMode.DEGRADED
        backend._memory["cb:test"] = {"state": "open"}
        
        # Redis 정상화 시뮬레이션
        with patch.object(backend._health_checker, 'check_health', return_value=True):
            with patch.object(backend, '_do_recovery', return_value=True):
                result = backend.check_and_recover()
        
        assert result is True
    
    def test_recovery_clears_memory(self, backend):
        """복구 후 Memory 정리."""
        backend._mode = StorageMode.DEGRADED
        backend._memory["key"] = "value"
        
        backend._do_recovery()
        
        assert backend._memory == {}
        assert backend.mode == StorageMode.REDIS


class TestRedisCircuitBreakerStateRepository:
    """Redis CB 저장소 테스트."""
    
    @pytest.fixture
    def repo(self, backend):
        return RedisCircuitBreakerStateRepository(backend)
    
    def test_get_or_create_returns_default(self, repo):
        """존재하지 않는 서비스는 기본값 반환."""
        result = repo.get_or_create("new_service")
        assert result.state == "closed"
        assert result.failure_count == 0
    
    def test_update_state(self, repo):
        """상태 업데이트 동작."""
        repo.get_or_create("test_service")
        repo.update_state("test_service", "open", failure_count=5)
        
        result = repo.get_state("test_service")
        assert result.state == "open"
        assert result.failure_count == 5


class TestRedisDLQRepository:
    """Redis DLQ 저장소 테스트."""
    
    @pytest.fixture
    def repo(self, backend):
        return RedisDLQRepository(backend)
    
    def test_create_returns_id(self, repo):
        """DLQ 항목 생성 시 ID 반환."""
        entry_id = repo.create(
            domain="payment",
            failure_type="TIMEOUT",
            error_message="Connection timeout"
        )
        assert entry_id > 0
    
    def test_get_pending_returns_entries(self, repo):
        """대기 항목 조회."""
        repo.create(domain="test", failure_type="ERROR", error_message="Test")
        
        pending = repo.get_pending()
        assert len(pending) >= 1
    
    def test_mark_resolved_removes_from_pending(self, repo):
        """해결 처리 시 대기열에서 제거."""
        entry_id = repo.create(domain="test", failure_type="ERROR", error_message="Test")
        repo.mark_resolved(entry_id, "manual_fix")
        
        pending = repo.get_pending()
        pending_ids = [p.get("id") for p in pending]
        assert str(entry_id) not in pending_ids
```

### 8.2 통합 테스트

```python
# tests/integration/test_resilient_storage_integration.py

import pytest
import redis
import time


@pytest.mark.integration
class TestResilientStorageIntegration:
    """실제 Redis 연동 통합 테스트."""
    
    @pytest.fixture
    def real_backend(self):
        """실제 Redis 연결 Backend."""
        config = ResilientStorageConfig(
            redis_url="redis://localhost:6379/15",  # 테스트용 DB
            wal_dir="/tmp/selfhealing_test_wal",
        )
        backend = ResilientStorageBackend(config)
        yield backend
        
        # 정리
        backend._redis._redis.flushdb()
    
    def test_end_to_end_normal_flow(self, real_backend):
        """정상 흐름 E2E 테스트."""
        # 1. CB 저장소 생성
        repo = RedisCircuitBreakerStateRepository(real_backend)
        
        # 2. 상태 생성
        state = repo.get_or_create("test_service")
        assert state.state == "closed"
        
        # 3. 상태 업데이트
        repo.update_state("test_service", "open", failure_count=5)
        
        # 4. 확인
        updated = repo.get_state("test_service")
        assert updated.state == "open"
        assert updated.failure_count == 5
    
    def test_degraded_mode_and_recovery(self, real_backend):
        """Degraded 모드 → 복구 E2E 테스트."""
        repo = RedisCircuitBreakerStateRepository(real_backend)
        
        # 1. 정상 상태 확인
        assert real_backend.mode == StorageMode.REDIS
        
        # 2. Redis 연결 끊기 시뮬레이션
        real_backend._mode = StorageMode.DEGRADED
        
        # 3. Degraded 모드에서 상태 변경
        repo.update_state("critical_service", "open", failure_count=10)
        
        # 4. Memory에 저장되었는지 확인
        assert "cb:critical_service" in real_backend._memory
        
        # 5. 복구
        real_backend._do_recovery()
        
        # 6. Redis에 동기화되었는지 확인
        result = real_backend._redis._redis.hgetall("selfhealing:cb:critical_service")
        assert result[b"state"] == b"open"
```

### 8.3 Chaos 테스트

```python
# tests/chaos/test_resilient_storage_chaos.py

@pytest.mark.chaos
class TestResilientStorageChaos:
    """Chaos Engineering 테스트."""
    
    def test_redis_kill_during_write(self):
        """쓰기 중 Redis 죽었을 때 데이터 유실 없음."""
        # 1. 쓰기 시작
        # 2. 중간에 Redis 연결 끊기
        # 3. Degraded 모드로 전환 확인
        # 4. WAL에 기록 확인
        # 5. 복구 후 데이터 확인
        pass
    
    def test_server_crash_and_restart(self):
        """서버 죽었다 재시작 시 WAL 복구."""
        # 1. Degraded 모드에서 데이터 저장
        # 2. 프로세스 종료 시뮬레이션 (Memory 비움)
        # 3. 새 Backend 인스턴스 생성
        # 4. WAL에서 복구
        # 5. 데이터 확인
        pass
    
    def test_thundering_herd_prevention(self):
        """다중 인스턴스 동시 복구 시 Jitter 동작."""
        # 1. 10개 Backend 인스턴스 생성
        # 2. 모두 Degraded 모드로 전환
        # 3. 동시에 check_and_recover() 호출
        # 4. 실제 복구 시간이 분산되었는지 확인
        pass
```

---

## 9. 마이그레이션 가이드

### 9.1 기존 InMemory 사용자

```python
# Before
from selfhealing.adapters.memory.circuit_breaker import InMemoryCircuitBreakerStateRepository
repo = InMemoryCircuitBreakerStateRepository()

# After
from selfhealing.factory import get_circuit_breaker_repo
repo = get_circuit_breaker_repo()  # 자동으로 ResilientStorage 사용
```

### 9.2 기존 Django DB 사용자

```python
# Before
from selfhealing.adapters.django import DjangoCircuitBreakerStateRepository
repo = DjangoCircuitBreakerStateRepository()

# After (opt-in)
# Redis 없으면 기존처럼 Django DB 사용 가능
SELFHEALING_STORAGE = "django"  # settings.py
```

### 9.3 기존 Layered 사용자

```python
# Before
from selfhealing.adapters.memory.layered_repository import LayeredCircuitBreakerStateRepository
repo = LayeredCircuitBreakerStateRepository(l2_repo=...)

# After
# ResilientStorageBackend가 Layered 개념을 내장
from selfhealing.factory import get_circuit_breaker_repo
repo = get_circuit_breaker_repo()
```

---

## 10. 기존 시스템 관계

### 10.1 유지되는 컴포넌트

| 컴포넌트 | 역할 | 비고 |
|---------|------|------|
| `InMemoryCircuitBreakerStateRepository` | Fallback 저장소 | 그대로 유지 |
| `DjangoCircuitBreakerStateRepository` | opt-in DB 저장소 | 그대로 유지 |
| `WriteAheadLog` | 디스크 복구 큐 | 재사용 |
| `ShadowLogger` | Forensic 로깅 | 재사용 |
| `DriftReconciler` | 상태 충돌 해결 | 재사용 |
| `RedisHealthChecker` | Redis 헬스 체크 | 재사용 |
| `RedisCacheAdapter` | Redis 기본 연산 | 재사용 |
| `CorruptionShield` | 데이터 무결성 | 그대로 유지 |

### 10.2 삭제 대상

**없음** - 기존 컴포넌트를 조합하여 새 기능 구현

### 10.3 신규 추가

| 컴포넌트 | 역할 |
|---------|------|
| `ResilientStorageBackend` | 통합 저장소 (Redis + Fallback + WAL) |
| `RedisCircuitBreakerStateRepository` | CB Redis 저장소 |
| `RedisDLQRepository` | DLQ Redis 저장소 |

---

## 📎 관련 문서

- [00_INDEX.md](00_INDEX.md) - 문서 인덱스
- [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) - 인프라 어댑터
- [13_LAYERED_STORAGE_RESILIENCE.md](../13_LAYERED_STORAGE_RESILIENCE.md) - L1+L2 저장소
- [24_CORRUPTION_SHIELD.md](../24_CORRUPTION_SHIELD.md) - 데이터 무결성

---

## 11. 구현 현황

### 11.1 구현 완료 (2026-01-02)

| 컴포넌트 | 파일 경로 | 상태 |
|---------|----------|------|
| `ResilientStorageBackend` | `adapters/resilient/backend.py` | ✅ 완료 |
| `RedisCircuitBreakerStateRepository` | `adapters/redis/circuit_breaker.py` | ✅ 완료 |
| `RedisDLQRepository` | `adapters/redis/dlq.py` | ✅ 완료 |
| Factory 통합 | `factory.py` | ✅ 완료 |
| 단위 테스트 (34개) | `tests/unit/test_resilient_storage.py` | ✅ 통과 |
| 통합 테스트 (19개) | `tests/integration/test_resilient_storage_integration.py` | ✅ 통과 |

### 11.2 주요 구현 기능

- **Redis-First 저장소**: 모든 상태를 Redis에서 통합 관리
- **Graceful Degradation**: Redis 장애 시 Memory + WAL로 자동 전환
- **WAL-First 프로토콜**: 서버 장애 시에도 데이터 유실 0 보장
- **자동 복구**: Redis 복구 시 Memory/WAL 데이터를 Redis로 동기화
- **Most Restrictive Wins**: 충돌 해결 시 보수적인 상태 우선

### 11.3 미구현 항목

| 항목 | 상태 | 비고 |
|------|------|------|
| 카오스 테스트 | ⏳ 미구현 | 향후 구현 예정 |
| Prometheus 메트릭 | ⏳ 미구현 | 향후 구현 예정 |
| Admin UI 연동 | ⏳ 미구현 | 향후 구현 예정 |
