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

| 컴포넌트 | 경로 | 역할 |
|---------|------|------|
| `WriteAheadLog` | `selfhealing/audit/wal.py` | Degraded Mode 디스크 기록 |
| `RedisHealthChecker` | `selfhealing/api/django/rate_limit.py` | Redis 상태 감시 + Mini CB |
| `ShadowLogger` | `selfhealing/adapters/memory/shadow_logger.py` | Forensic 로깅 |
| `DriftReconciler` | `selfhealing/adapters/memory/drift_reconciliation.py` | 충돌 해결 (Most Restrictive Wins) |
| `RedisCacheAdapter` | `selfhealing/adapters/cache/redis_adapter.py` | Redis 연산 + 분산 락 |
| `InMemoryCircuitBreakerStateRepository` | `selfhealing/adapters/memory/circuit_breaker.py` | Fallback 저장소 |

---

## 5. 신규 구현 컴포넌트

### 5.1 ResilientStorageBackend

**경로**: `selfhealing/adapters/resilient/backend.py`

| 클래스/Enum | 설명 |
|------------|------|
| `StorageMode` | `REDIS`, `DEGRADED`, `RECOVERING` |
| `ResilientStorageConfig` | 설정 (redis_url, wal_dir, key_prefix 등) |
| `ResilientStorageBackend` | 통합 저장소 |

**주요 메서드:**

| 메서드 | 설명 |
|--------|------|
| `get(key)` | 값 조회 |
| `set(key, value)` | 값 저장 |
| `delete(key)` | 값 삭제 |
| `hget(key, field)` | Hash 필드 조회 |
| `hset(key, mapping)` | Hash 필드 설정 |
| `hgetall(key)` | Hash 전체 조회 |
| `check_and_recover()` | Redis 복구 체크 및 동기화 |

### 5.2 RedisCircuitBreakerStateRepository

**경로**: `selfhealing/adapters/redis/circuit_breaker.py`

`CircuitBreakerStateRepository` 인터페이스 구현체.

| 메서드 | 설명 |
|--------|------|
| `get_state(service_name)` | CB 상태 조회 |
| `get_or_create(service_name)` | 조회 또는 기본값 생성 |
| `update_state(...)` | 상태 업데이트 |
| `atomic_force_open(service_name)` | 강제 OPEN |
| `atomic_force_close(service_name)` | 강제 CLOSE |

### 5.3 RedisDLQRepository

**경로**: `selfhealing/adapters/redis/dlq.py`

`FailedOperationRepository` 인터페이스 구현체.

| 메서드 | 설명 |
|--------|------|
| `create(domain, failure_type, ...)` | DLQ 항목 생성 |
| `get_pending(limit)` | 대기 항목 조회 |
| `mark_resolved(entry_id, resolution_type)` | 해결 처리 |
| `try_acquire_for_replay(entry_id)` | 재처리 락 획득 |
| `complete_replay(entry_id, success)` | 재처리 완료 |

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

**경로**: `selfhealing/factory.py`

| 함수 | 반환 타입 |
|------|----------|
| `get_storage_backend()` | `ResilientStorageBackend` |
| `get_circuit_breaker_repo()` | `RedisCircuitBreakerStateRepository` |
| `get_dlq_repo()` | `RedisDLQRepository` |

---

## 8. 테스트

### 8.1 테스트 파일

| 종류 | 파일 경로 | 테스트 수 |
|------|----------|----------|
| 단위 테스트 | `tests/unit/test_resilient_storage.py` | 34개 ✅ |
| 통합 테스트 | `tests/integration/test_resilient_storage_integration.py` | 19개 ✅ |
| Chaos 테스트 | `tests/chaos/test_resilient_storage_chaos.py` | ⏳ 미구현 |

### 8.2 테스트 케이스 목록

**ResilientStorageBackend:**
- `test_set_get_normal_mode` - 정상 모드 set/get
- `test_hset_hgetall_normal_mode` - 정상 모드 Hash 연산
- `test_switch_to_degraded_on_redis_failure` - Redis 장애 시 전환
- `test_degraded_mode_uses_memory` - Degraded 모드 Memory 사용
- `test_degraded_mode_writes_wal` - Degraded 모드 WAL 기록
- `test_wal_survives_memory_clear` - WAL 복구 가능 확인
- `test_recovery_syncs_to_redis` - 복구 시 동기화
- `test_recovery_clears_memory` - 복구 후 Memory 정리

**RedisCircuitBreakerStateRepository:**
- `test_get_or_create_returns_default` - 기본값 생성
- `test_update_state` - 상태 업데이트

**RedisDLQRepository:**
- `test_create_returns_id` - ID 반환
- `test_get_pending_returns_entries` - 대기 항목 조회
- `test_mark_resolved_removes_from_pending` - 해결 처리

### 8.3 통합 테스트 실행

```bash
# Redis 필요 (docker-compose.test.yml)
docker-compose -f docker-compose.test.yml up -d redis

# 통합 테스트 실행
pytest tests/integration/test_resilient_storage_integration.py -v
```

---

## 9. 마이그레이션 가이드

### 9.1 기존 InMemory 사용자

| Before | After |
|--------|-------|
| `InMemoryCircuitBreakerStateRepository()` | `get_circuit_breaker_repo()` |

### 9.2 기존 Django DB 사용자

| Before | After |
|--------|-------|
| `DjangoCircuitBreakerStateRepository()` | `SELFHEALING_STORAGE = "django"` 설정 시 유지 가능 |

### 9.3 기존 Layered 사용자

| Before | After |
|--------|-------|
| `LayeredCircuitBreakerStateRepository(l2_repo=...)` | `get_circuit_breaker_repo()` (Layered 개념 내장)

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
