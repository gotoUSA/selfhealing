# 174. 누락된 시스템 구현 마스터 플랜

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md), [173_UNIFIED_CHECKPOINT_STRATEGY.md](173_UNIFIED_CHECKPOINT_STRATEGY.md)
> **범위**: 누락된 시스템 전체 개요 및 구현 로드맵

---

## 0. 문서 목적

이 문서는 Self-Healing 시스템에서 **"절대 터지지 않는 시스템"**을 구현하기 위해 필요한 누락된 시스템들을 분석하고, 구현 로드맵을 제시합니다.

**핵심 목표**: 브로커 장애, Pod 재시작, 리전 전체 장애에도 데이터 손실 0%를 보장하는 시스템 구축

---

## 1. 현재 시스템 분석 (코드 근거)

### 1.1 현재 아키텍처 진단

```
┌─────────────────────────────────────────────────────────┐
│                    Self-Healing System                   │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────┐ │
│  │  Redis  │→→│ Replica │→→│  Local  │→→│Memory Buffer│ │
│  │ Primary │  │  (Read) │  │  File   │  │ (VOLATILE!) │ │
│  └─────────┘  └─────────┘  └─────────┘  └─────────────┘ │
│                                              ↓          │
│                                    ⚠️ Pod 재시작 = 손실  │
└─────────────────────────────────────────────────────────┘
```

### 1.2 4-Tier Fallback Chain 현황

**파일**: `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/fallback.py`

```python
class HashChainFallbackChain:
    """
    Multi-tier fallback chain for hash chain operations.

    Fallback order:
    1. Redis Primary - Full distributed functionality
    2. Redis Replica - Read-only, degraded writes to local
    3. Local File - Persistent but not distributed
    4. Memory Buffer - Last resort, volatile

    Each fallback level marks entries as degraded for later reconciliation.
    """
```

**라인 284-305**: Memory Buffer 휘발성 문제
```python
def _add_integrity_memory(self, entry: dict[str, Any]) -> dict[str, Any]:
    """Add integrity using memory buffer (last resort)."""
    # ...
    entry["integrity"] = {
        # ...
        "volatile": True,  # ⚠️ Warning: will be lost on restart
    }
```

---

## 2. 누락된 시스템 목록

### 2.1 즉시 구현 필요 (Phase 1)

| 시스템 | 현재 상태 | 필요한 이유 | 예상 코드량 | 문서 |
|--------|----------|------------|-------------|------|
| Kafka Producer/Consumer | Header만 준비됨 | Memory Buffer 휘발성 해결 | ~2,000줄 | [175](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md) |
| Disk-Persistent Buffer | ❌ 없음 | Pod 재시작 시 데이터 보존 | ~500줄 | [176](176_DISK_PERSISTENT_BUFFER.md) |

### 2.2 1개월 내 구현 (Phase 2)

| 시스템 | 현재 상태 | 필요한 이유 | 예상 코드량 | 문서 |
|--------|----------|------------|-------------|------|
| Self-Healing Meta-Watchdog | ❌ 없음 | "치료사가 아플 때" 대비 | ~1,500줄 | [177](177_SELF_HEALING_META_WATCHDOG.md) |
| Circuit Breaker for Self-Healing | ❌ 없음 | 과부하 시 자기 보호 | 포함 | [177](177_SELF_HEALING_META_WATCHDOG.md) |

### 2.3 2개월 내 구현 (Phase 3)

| 시스템 | 현재 상태 | 필요한 이유 | 예상 코드량 | 문서 |
|--------|----------|------------|-------------|------|
| Multi-Region Active-Active | ❌ 없음 | 리전 전체 장애 대응 | ~3,000줄 | [178](178_MULTI_REGION_ACTIVE_ACTIVE.md) |
| Conflict Resolver | ❌ 없음 | Active-Active 충돌 해결 | 포함 | [178](178_MULTI_REGION_ACTIVE_ACTIVE.md) |

### 2.4 3개월 내 구현 (Phase 4)

| 시스템 | 현재 상태 | 필요한 이유 | 예상 코드량 | 문서 |
|--------|----------|------------|-------------|------|
| Global Leader Election | ❌ 없음 | 전 세계 단일 Coordinator | ~800줄 | [179](179_GLOBAL_LEADER_ELECTION.md) |
| HPA/Auto-Scaling | ❌ 없음 | 부하 대응 자동 스케일링 | ~300줄 | [180](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md) |
| Rate-aware Backpressure | 부분 구현 | 요청 유입량 기반 배압 | ~400줄 | [180](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md) |

---

## 3. 현재 코드 근거 상세 분석

### 3.1 Kafka 관련 현황

#### 3.1.1 Kafka Header만 준비됨

**파일**: `packages/selfhealing-python/tests/unit/audit/test_causation_context.py`
**라인**: 23-29

```python
from selfhealing.audit.cascade_chain import (
    get_causation_for_kafka,
    restore_causation_from_kafka,
)
from selfhealing.audit.cascade_config import (
    KAFKA_HEADER_PREFIX,
)
```

**문제점**: Header 전파 유틸리티만 존재하고, 실제 Kafka Producer/Consumer 구현이 없음

#### 3.1.2 KafkaCheckpointManager 존재

**파일**: `packages/selfhealing-python/src/selfhealing/audit/kafka_checkpoint.py`
**라인**: 100-200

```python
class KafkaCheckpointManager:
    """
    WAL-Kafka 체크포인트 관리자.

    WAL 시퀀스와 Kafka 오프셋을 원자적으로 기록하여
    정확한 복구 지점을 보장합니다.
    """
```

**현황**: Checkpoint 관리만 존재, 실제 Kafka 연결 없음

---

### 3.2 Memory Buffer 휘발성 문제

**파일**: `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/fallback.py`
**라인**: 284-305

```python
entry["integrity"] = {
    "sequence": sequence,
    "previous_hash": previous_hash,
    "timestamp": timestamp,
    "pod_id": pod_id,
    "tier": "memory",
    "degraded": True,
    "degraded_reason": "all_persistent_storage_unavailable",
    "degraded_at": timestamp,
    "volatile": True,  # ⚠️ Warning: will be lost on restart
}
```

**파일**: `packages/selfhealing-python/src/selfhealing/audit/resilience/buffer.py`
**라인**: 30-50

```python
class InMemoryAuditBuffer:
    """
    WAL 실패 시 메모리 폴백 버퍼.

    디스크 장애 시 중요 로그를 메모리에 임시 보관하고,
    시스템 정상화 시 파일로 플러시합니다.
    """
```

**문제점**:
- `InMemoryAuditBuffer`는 순수 메모리 기반
- Pod 재시작 시 모든 버퍼 데이터 손실

---

### 3.3 Load Shedding은 존재하나 Backpressure Controller 없음

**파일**: `packages/selfhealing-python/src/selfhealing/audit/cascade_load_shedding.py`
**라인**: 100-130

```python
class CascadeLoadShedding:
    """
    Cascade Event Load Shedding 관리자.

    버퍼 사용률에 따라 우선순위가 낮은 이벤트를 드롭합니다.
    CRITICAL 이벤트는 절대 드롭하지 않습니다.
    """
```

**현황**: Load Shedding은 존재하나, 외부 트래픽 유입을 조절하는 Rate-aware Backpressure Controller가 없음

---

### 3.4 ClusterIdentity는 식별만 (Coordination 없음)

**파일**: `packages/selfhealing-python/src/selfhealing/core/cluster_identity.py`
**라인**: 30-50

```python
@dataclass(frozen=True)
class ClusterIdentity:
    """
    클러스터 식별 정보 (Immutable).

    Attributes:
        cluster_id: 클러스터 고유 ID (필수)
        region: 리전 식별자 (예: seoul, tokyo)
        environment: 환경 (dev, staging, prod)
        tenant: SaaS 테넌트 ID (옵션)
        pod_id: 현재 Pod ID
    """

    cluster_id: str
    region: str | None = None
    environment: str = "production"
```

**문제점**: 자신이 누구인지만 알고, 클러스터 간 Coordination 메커니즘 없음

---

### 3.5 TieredRedisProvider 존재 (단일 클러스터 내)

**파일**: `packages/selfhealing-python/src/selfhealing/core/tiered_redis.py`
**라인**: 1-50

```python
class TieredRedisProvider:
    """
    계층화된 Redis 제공자.

    LOCAL: 각 클러스터 내부 Redis (CB, 메트릭, DLQ)
    GLOBAL: 리전 간 복제 Redis (설정, 앵커, Error Budget)
    """
```

**현황**: LOCAL/GLOBAL 분리는 있으나, Multi-Region Active-Active 동기화 로직 없음

---

### 3.6 Watchdog 존재 (Canary용, Self-Healing용 아님)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/audit_watchdog.py`
**라인**: 1-50

```python
class AuditWatchdog:
    """
    Audit Watchdog - Dead Man's Switch Pattern.

    감사 시스템 생존 확인:
    - 주기적으로 heartbeat 전송
    - 외부 모니터링 시스템이 heartbeat 감시
    """
```

**현황**: Audit 시스템 heartbeat 전용, Self-Healing 시스템 자체의 건강 상태 모니터링은 없음

---

## 4. "절대 터지지 않는" 시스템 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ┌──────────────┐     ┌─────────────────────────────────────┐   │
│  │ Meta-Monitor │────→│     Self-Healing of Self-Healing     │   │
│  │  (Watchdog)  │     │  (자기 자신도 복구 가능해야 함)         │   │
│  └──────────────┘     └─────────────────────────────────────┘   │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    Self-Healing System                   │    │
│  │  ┌─────────┐  ┌─────────┐  ┌───────┐  ┌──────────────┐  │    │
│  │  │  Redis  │→→│ Replica │→→│ Kafka │→→│ Disk-Persist │  │    │
│  │  │ Cluster │  │ Cluster │  │ Queue │  │ Memory Buffer│  │    │
│  │  └─────────┘  └─────────┘  └───────┘  └──────────────┘  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                            │                                     │
│                            ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │               Multi-Region Active-Active                 │    │
│  │   [Seoul] ←─── Sync ───→ [Tokyo] ←─── Sync ───→ [US]    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 구현 체크리스트

### 5.1 현재 완료된 기능

| 기능 | 상태 | 코드 위치 |
|------|------|-----------|
| 4-Tier Fallback Chain | ✅ | `audit/graceful_degradation/fallback.py` |
| Kafka Header Preparation | ✅ | `audit/cascade_chain.py` |
| Priority Queue (P0-P3) | ✅ | `utils/async_logger.py` |
| Load Shedding Framework | ✅ | `audit/cascade_load_shedding.py` |
| Cluster Identity | ✅ | `core/cluster_identity.py` |
| RingBuffer with Backpressure | ✅ | `audit/ring_buffer.py` |
| WAL (Write-Ahead Log) | ✅ | `audit/wal.py` |
| Redis Batch Lua Scripts | ✅ | `audit/redis_batch_lua.py` |
| Tiered Redis (LOCAL/GLOBAL) | ✅ | `core/tiered_redis.py` |
| KafkaCheckpointManager | ✅ | `audit/kafka_checkpoint.py` |
| Audit Watchdog (Heartbeat) | ✅ | `audit/audit_watchdog.py` |

### 5.2 구현 필요한 기능

| 기능 | 상태 | 문서 | 우선순위 |
|------|------|------|----------|
| Kafka Producer/Consumer | ❌ | [175](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md) | P0 |
| Disk-Persistent Buffer | ❌ | [176](176_DISK_PERSISTENT_BUFFER.md) | P0 |
| Self-Healing Meta-Watchdog | ❌ | [177](177_SELF_HEALING_META_WATCHDOG.md) | P1 |
| Multi-Region Active-Active | ❌ | [178](178_MULTI_REGION_ACTIVE_ACTIVE.md) | P2 |
| Global Leader Election | ❌ | [179](179_GLOBAL_LEADER_ELECTION.md) | P3 |
| HPA/Backpressure Controller | ❌ | [180](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md) | P3 |

---

## 6. 구현 로드맵

### Phase 1 (즉시, ~2주)

**목표**: Memory Buffer 휘발성 문제 해결

1. **Kafka Producer/Consumer 실제 구현** [175](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md)
   - `confluent-kafka` 기반 Producer/Consumer
   - Idempotent Producer 설정
   - Exactly-once 의미론 지원
   - 기존 `KafkaCheckpointManager`와 통합

2. **Disk-Persistent Buffer** [176](176_DISK_PERSISTENT_BUFFER.md)
   - `mmap` 또는 `LMDB` 기반 로컬 버퍼
   - Pod 재시작 시에도 데이터 보존
   - 기존 `InMemoryAuditBuffer` 대체

### Phase 2 (1개월, ~3주)

**목표**: Self-Healing 시스템 자체의 안정성 확보

3. **Self-Healing Meta-Watchdog** [177](177_SELF_HEALING_META_WATCHDOG.md)
   - Circuit Breaker 상태 모니터링
   - Recovery Pipeline stuck 감지
   - DLQ Consumer 생존 확인
   - 인간 개입 에스컬레이션 (PagerDuty)

### Phase 3 (2개월, ~4주)

**목표**: 리전 전체 장애 대응

4. **Multi-Region Active-Active** [178](178_MULTI_REGION_ACTIVE_ACTIVE.md)
   - 리전 간 데이터 동기화
   - Conflict Resolution 전략
   - Cross-region 이벤트 발행

### Phase 4 (3개월, ~2주)

**목표**: 글로벌 조율 및 자동 스케일링

5. **Global Leader Election** [179](179_GLOBAL_LEADER_ELECTION.md)
   - etcd 또는 Zookeeper 기반
   - 전 세계 단일 Coordinator 선출

6. **Kubernetes Auto-Scaling & Backpressure** [180](180_KUBERNETES_AUTOSCALING_BACKPRESSURE.md)
   - HorizontalPodAutoscaler 설정
   - Rate-aware Backpressure Controller

---

## 7. 의존성 그래프

```
                    ┌───────────────────┐
                    │ 174. Master Plan  │
                    └─────────┬─────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
    ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
    │ 175. Kafka    │ │ 176. Disk     │ │ 177. Meta-    │
    │ Event Bus     │ │ Buffer        │ │ Watchdog      │
    └───────┬───────┘ └───────┬───────┘ └───────────────┘
            │                 │
            └────────┬────────┘
                     ▼
            ┌───────────────┐
            │ 178. Multi-   │
            │ Region A-A    │
            └───────┬───────┘
                    │
            ┌───────┴───────┐
            ▼               ▼
    ┌───────────────┐ ┌───────────────┐
    │ 179. Leader   │ │ 180. HPA &    │
    │ Election      │ │ Backpressure  │
    └───────────────┘ └───────────────┘
```

---

## 8. 위험 요소 및 완화 전략

### 8.1 기술적 위험

| 위험 | 영향도 | 완화 전략 |
|------|--------|-----------|
| Kafka 브로커 장애 | 높음 | 3+ 브로커 클러스터, replication.factor=3 |
| mmap 파일 손상 | 중간 | CRC32 체크섬, 주기적 무결성 검사 |
| 리전 간 네트워크 단절 | 높음 | 비동기 동기화 + 충돌 해결 |
| Leader 선출 실패 | 중간 | Fencing Token으로 Split-brain 방지 |

### 8.2 운영 위험

| 위험 | 영향도 | 완화 전략 |
|------|--------|-----------|
| 복잡도 증가 | 중간 | 단계별 구현, 충분한 테스트 |
| 인프라 비용 증가 | 중간 | 비용 모니터링, 적정 규모 산정 |
| 학습 곡선 | 낮음 | 상세 문서화, 팀 교육 |

---

## 9. 관련 문서

- [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md) - Kafka 어댑터 설계
- [173_UNIFIED_CHECKPOINT_STRATEGY.md](173_UNIFIED_CHECKPOINT_STRATEGY.md) - Checkpoint 통합
- [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md) - 비동기 파이프라인
- [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md) - RingBuffer 통합
- [70_MULTI_CLUSTER_ARCHITECTURE.md](70_MULTI_CLUSTER_ARCHITECTURE.md) - 멀티 클러스터 아키텍처

---

## 10. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
