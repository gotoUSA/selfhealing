# Stage Organization & Classification Guide

> **Version**: 1.0
> **Created**: 2025-12-13
> **Purpose**: 테스트 스테이지 분류 체계 및 관리 가이드

---

## 📊 Stage 분류 체계

### Layer 구조

```
┌─────────────────────────────────────────────────────────────┐
│  L4: Edge/Advanced (Stage 23-36)                            │
│  → 프로덕션급 엣지케이스                                      │
│  → "실제 운영에서 만나는 극한 상황을 견디는가?"                 │
├─────────────────────────────────────────────────────────────┤
│  L3: Self-Healing Layer (Stage 10-22)                       │
│  → L1/L2 장애 시 자동 복구 검증                               │
│  → "장애가 발생해도 스스로 회복하는가?"                        │
├─────────────────────────────────────────────────────────────┤
│  L2: Extension/Chaos (Stage 6-9)                            │
│  → 정합성 + 장기 안정성                                       │
│  → "경계 조건에서 깨지지 않는가?"                             │
├─────────────────────────────────────────────────────────────┤
│  L1: Foundation Tests (Stage 0-5)                           │
│  → 모든 시스템이 반드시 통과해야 함                            │
│  → "서비스가 기본적으로 동작하는가?"                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 실행 Profile 정의

### CI/CD Pipeline별 실행 범위

| Profile | Stages | 실행 시점 | 예상 시간 |
|---------|--------|----------|----------|
| **smoke** | 0 | 매 커밋 | 30초 |
| **ci** | 0, 2, 5 | 매 PR | 5분 |
| **nightly** | 0-9 | 매일 밤 | 30분 |
| **weekly** | 0-22 | 주 1회 | 2시간 |
| **release** | 0-36 전체 | 릴리스 전 | 4시간 |
| **exploratory** | 선택적 | 이슈 발생 시 | - |

### Profile YAML 정의

```yaml
profiles:
  # 매 커밋 (필수)
  smoke:
    stages: [stage0_smoke]
    timeout: 1m
    fail_fast: true

  # PR 머지 전 (필수)
  ci:
    stages:
      - stage0_smoke
      - stage2_idempotent
      - stage5_rollback
    timeout: 10m
    fail_fast: true

  # 매일 밤 자동 실행
  nightly:
    stages:
      - stage0_smoke
      - stage1_happy
      - stage2_idempotent
      - stage3_latency
      - stage4_cancel
      - stage5_rollback
      - stage6_chaos
      - stage7_race
      - stage8_webhook
      - stage9_soak
    timeout: 45m
    fail_fast: false
    notify_on_failure: true

  # 주간 전체 검증
  weekly:
    stages: [stage0 through stage22]
    timeout: 3h
    generate_report: true

  # 릴리스 전 풀 검증
  release:
    stages: [stage0 through stage36]
    timeout: 6h
    require_all_pass: true
    generate_report: true

  # Self-Healing 집중
  self_healing:
    stages:
      - stage0_smoke
      - stage10_self_healing
      - stage14_dlq_replay
      - stage15_cb_transitions
      - stage16_db_lock_recovery
      - stage19_rollback_failure
      - stage21_false_positive
    timeout: 1h

  # Cache 집중
  cache:
    stages:
      - stage17_cache_ttl_race
      - stage35_cache_stampede
      - stage35_redis_stampede
      - stage35_distributed_test_v2
    timeout: 30m
```

---

## 📁 Variant 관리 (Stage 35/36 정리)

### Primary + Variants 구조

```
load_tests/scenarios/
├── stage35_cache_stampede.py              # PRIMARY
├── stage35_variants/
│   ├── README.md                          # Variant 설명
│   ├── redis_stampede.py                  # Variant: Redis 클러스터
│   ├── distributed_v2.py                  # Variant: 분산 환경 (최신)
│   └── _archived/
│       └── distributed_v1.py              # Deprecated
│
├── stage36_memory_pressure.py             # PRIMARY
├── stage36_variants/
│   └── real_http.py                       # Variant: 실제 HTTP 메모리
```

### Variant README 템플릿

```markdown
# Stage 35 Variants

## Primary: stage35_cache_stampede.py
- 목적: 단일 인스턴스 캐시 스탬피드 방지 검증
- 환경: Local Redis / Django cache

## Variants

### redis_stampede.py
- 추가 검증: Redis 클러스터 환경에서의 스탬피드
- Primary와 차이: 클러스터 노드 간 레이스 컨디션
- 실행 조건: Redis Cluster 환경 필요

### distributed_v2.py
- 추가 검증: 멀티 인스턴스 분산 환경
- Primary와 차이: 인스턴스 간 락 경합
- v1 대비 개선: 분산 락 검증 추가
- 실행 조건: docker-compose scale=3

## Archived

### distributed_v1.py
- 상태: Deprecated (2025-12-01)
- 대체: distributed_v2.py
- 사유: 분산 락 검증 누락
```

### Config에서 Variant 관리

```yaml
stage35_cache_stampede:
  file: scenarios/stage35_cache_stampede.py
  description: "Cache stampede prevention (단일 인스턴스)"
  variants:
    redis_stampede:
      file: scenarios/stage35_variants/redis_stampede.py
      requires: ["redis-cluster"]
    distributed_v2:
      file: scenarios/stage35_variants/distributed_v2.py
      requires: ["docker-compose-scale-3"]

  # CI에서는 primary만
  ci_mode: primary_only

  # Release에서는 환경 맞으면 variant도
  release_mode: all_matching
```

---

## 🏷️ Suite 기반 그룹핑

### 추천 Suite 구조

| Suite | 포함 Stages | 목적 |
|-------|------------|------|
| **suite_load_latency** | 0, 1, 3, 9, 11, 12, 13 | 부하/레이턴시 |
| **suite_integrity_concurrency** | 2, 7, 16, 17, 34 | 정합성/동시성 |
| **suite_webhooks** | 8, 20 | 웹훅 |
| **suite_resilience** | 10, 15, 18, 21, 22, 31, 32, 33, 4 | 복원력 |
| **suite_rollback** | 5, 19 | 롤백 |
| **suite_queue_dlq** | 14, 29 | 큐/DLQ |
| **suite_infra_network** | 24, 25, 27, 28, 6 | 인프라/네트워크 |
| **suite_resource_limits** | 26, 36 | 리소스 한계 |
| **suite_cache** | 17, 35 variants | 캐시 |

### Suite 파일 예시

```python
# load_tests/suites/suite_integrity_concurrency.py
"""
Integrity & Concurrency Suite
- 데이터 정합성
- 멱등성
- 레이스 컨디션
- 데드락
"""

from locust import events
from scenarios.stage2_idempotent import IdempotencyUser
from scenarios.stage7_race_conflict import RaceConditionUser
from scenarios.stage16_db_lock_recovery import DBLockUser
from scenarios.stage17_cache_ttl_race import CacheTTLUser
from scenarios.stage34_db_deadlock import DeadlockUser

class IntegritySuiteUser(IdempotencyUser, RaceConditionUser):
    """Combined user for integrity testing"""
    weight = 1

# Suite-level invariants
SUITE_INVARIANTS = [
    "duplicates_total == 0",
    "data_inconsistency_count == 0",
    "deadlock_unrecovered == 0",
]
```

---

## 📈 Stage 상태 관리

### 상태 정의

| Status | 의미 | 조건 |
|--------|------|------|
| **active** | 현재 사용 중 | 기본 |
| **experimental** | 실험 중 | 결과 불안정 |
| **deprecated** | 곧 제거 예정 | 대체 존재 |
| **archived** | 보관용 | 더 이상 실행 안 함 |

### Stage 메타데이터

```yaml
# 각 stage 파일 상단 또는 config.yaml에 명시
stage35_distributed_test:
  status: deprecated
  superseded_by: stage35_distributed_test_v2
  deprecated_date: 2025-12-01
  reason: "v2에서 분산 락 검증 추가됨"
  archive_date: 2026-01-01  # 이후 _archived로 이동
```

---

## 🔄 Stage 진화 프로세스

### 새 Variant 추가 시

```
1. 기존 Primary에서 발견된 Gap 문서화
2. Variant 파일 생성 (scenarios/stageXX_variants/)
3. README.md에 차이점 명시
4. config.yaml에 variant 등록
5. Release profile에 추가 (조건부)
```

### Primary 교체 시

```
1. 기존 Primary → Variant로 이동
2. 새 파일을 Primary로 승격
3. 기존 테스트 결과와 비교
4. 6개월 후 기존 파일 archive
```

### Archive 시

```
1. status: deprecated로 변경
2. 3개월 대기 (하위 호환성)
3. _archived/ 폴더로 이동
4. git history 유지
```

---

## 📊 Priority Matrix

### 핵심 Stage (삭제 불가)

| Priority | Stages | 근거 |
|----------|--------|------|
| **P0** | 0, 2, 5, 7 | 기본 동작 + 정합성 |
| **P1** | 1, 15, 16, 19 | 성능 + Self-Healing |
| **P2** | 3, 6, 14, 21 | 장애 시나리오 |
| **P3** | 나머지 | 확장 검증 |

### 최소 실행 세트 (18개)

```
필수 (12개): 0, 1, 2, 3, 5, 7, 10, 14, 15, 16, 19, 21
권장 (6개):  4, 6, 9, 12, 18, 22
선택 (나머지): 필요 시 활성화
```
