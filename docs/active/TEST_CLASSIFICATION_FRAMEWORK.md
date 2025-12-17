# 힐링시스템 테스트 분류 프레임워크
**Self-Healing Test Classification Framework**

> **Version**: 1.0
> **Last Updated**: 2025-12-17
> **Author**: SRE Team

---

## 📋 목차

1. [프레임워크 개요](#프레임워크-개요)
2. [Phase 1: Boundary Identification](#phase-1-boundary-identification)
3. [Phase 2: Internal Classification Axes](#phase-2-internal-classification-axes)
4. [의사결정 플로우차트](#의사결정-플로우차트)
5. [분류 매트릭스 템플릿](#분류-매트릭스-템플릿)

---

## 프레임워크 개요

이 프레임워크는 코드베이스에 혼재된 테스트들을 분류하기 위한 **2단계 필터링 체계**입니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ALL TESTS IN REPOSITORY                         │
└─────────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   PHASE 1: BOUNDARY   │
                    │   IDENTIFICATION      │
                    │   (힐링시스템 여부)    │
                    └───────────┬───────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
              ▼                 ▼                 ▼
        ┌─────────┐       ┌─────────┐       ┌─────────┐
        │   OUT   │       │ HEALING │       │ HYBRID  │
        │ OF SCOPE│       │  TESTS  │       │ (요주의) │
        └─────────┘       └────┬────┘       └────┬────┘
                               │                 │
                    ┌──────────▼─────────────────▼──────────┐
                    │         PHASE 2: INTERNAL             │
                    │         CLASSIFICATION AXES           │
                    │         (힐링시스템 내부 분류)          │
                    └───────────────────────────────────────┘
```

---

## Phase 1: Boundary Identification

> **목적**: "이 테스트가 힐링시스템을 검증하는가?" 판별

### B1. Primary Subject Test (주요 대상 테스트)

| 항목 | 내용 |
|------|------|
| **ID** | `B1-PRIMARY-SUBJECT` |
| **판별 질문** | "이 테스트의 **주요 검증 대상(SUT: System Under Test)**이 힐링시스템 컴포넌트인가?" |
| **판별 기준** | SUT가 다음 중 하나 이상인가?<br>• Circuit Breaker 상태 전이<br>• DLQ 저장/재처리<br>• Retry/Backoff 정책<br>• Fallback 메커니즘<br>• Recovery Timeout<br>• Self-Healing Control API |
| **YES 예시** | `test_circuit_breaker.py` → CB 상태 전이 검증<br>`stage14_dlq_replay.py` → DLQ 재처리 정확성 검증<br>`stage15_cb_transitions.py` → CB CLOSED→OPEN→HALF_OPEN 검증 |
| **NO 예시** | `test_edge_cases_payment.py` → 결제 로직 엣지 케이스<br>`stage1_happy_load.py` → 비즈니스 플로우 성능 측정 |
| **왜 필요한가** | 힐링시스템 테스트의 핵심 정의. SUT가 힐링 컴포넌트가 아니면 애초에 분류 대상 아님 |
| **잘못 분류 시 리스크** | FALSE POSITIVE: 비즈니스 테스트를 힐링테스트로 오분류 → 힐링시스템 커버리지 왜곡<br>FALSE NEGATIVE: 실제 힐링테스트 누락 → 회귀 버그 탐지 실패 |
| **중복 분류** | 불허 — 반드시 하나의 Primary Subject만 존재 |

---

### B2. Failure Injection Intent (장애 주입 의도 테스트)

| 항목 | 내용 |
|------|------|
| **ID** | `B2-FAILURE-INJECTION` |
| **판별 질문** | "이 테스트가 **의도적으로 장애를 주입**하고, 그 **복구 과정**을 검증하는가?" |
| **판별 기준** | 다음 패턴 존재 여부:<br>• 명시적 `inject_failure`, `force_error` 호출<br>• Chaos 환경변수(`CHAOS_ENABLED`) 사용<br>• Mock으로 Exception 강제 발생<br>• Toxiproxy/Fault Injector 사용 |
| **YES 예시** | `stage6_chaos_random.py` → 랜덤 실패 주입 후 시스템 안정성 검증<br>`test_recovery_during_chaos.py` → 카오스 중 복구 검증 |
| **NO 예시** | `stage1_happy_load.py` → 정상 부하만 측정<br>`test_edge_cases_cart_stock.py` → 비즈니스 경계값 테스트 |
| **왜 필요한가** | 힐링시스템의 존재 이유가 장애 대응. 장애 없는 테스트는 힐링 검증이 아님 |
| **잘못 분류 시 리스크** | FALSE POSITIVE: 에러 핸들링 단위테스트를 힐링테스트로 오분류<br>FALSE NEGATIVE: 암묵적 장애 주입 테스트 누락 |
| **중복 분류** | 허용 — B1과 함께 만족해야 진정한 힐링테스트 |

---

### B3. Recovery Observable (복구 관측 가능 테스트)

| 항목 | 내용 |
|------|------|
| **ID** | `B3-RECOVERY-OBSERVABLE` |
| **판별 질문** | "이 테스트가 **장애 후 정상 복구 상태**를 명시적으로 검증(assert)하는가?" |
| **판별 기준** | 검증 assertion 중 다음 포함 여부:<br>• `circuit_state == CLOSED` (복구 후)<br>• `dlq_replayed == dlq_inserted`<br>• `recovery_latency_ms < SLA`<br>• `error_rate < threshold after recovery` |
| **YES 예시** | `stage15_cb_transitions.py` → Phase 4에서 CB가 CLOSED로 복귀 검증<br>`test_failure_recovery_cycle.py` → 장애→복구→정상 사이클 전체 검증 |
| **NO 예시** | `test_cascading_failures.py` (만약 장애 발생만 확인하고 복구는 미확인) |
| **왜 필요한가** | "장애 주입"만으로는 Chaos Engineering. 복구 검증까지 있어야 Self-Healing 테스트 |
| **잘못 분류 시 리스크** | 복구 미검증 테스트를 힐링테스트로 분류 → 실제 복구 능력 보장 없이 통과 |
| **중복 분류** | 필수 — B1 + B2 + B3 모두 만족해야 "Pure Healing Test" |

---

### B4. Healing-as-Vehicle Test (힐링은 수단 테스트)

| 항목 | 내용 |
|------|------|
| **ID** | `B4-HEALING-AS-VEHICLE` |
| **판별 질문** | "힐링시스템이 테스트에 등장하지만, **주 목적은 비즈니스 로직 검증**인가?" |
| **판별 기준** | • 비즈니스 결과(결제 성공, 재고 일관성)가 최종 assertion<br>• 힐링 컴포넌트는 중간 과정으로만 사용<br>• 테스트 실패 시 비즈니스 버그로 분류 |
| **YES 예시** | `test_payment_tasks.py` → 결제 태스크 성공 여부 (CB는 배경)<br>`stage1_happy_load.py` → 전체 플로우 성능 (힐링 비활성) |
| **NO 예시** | `stage10_self_healing.py` → Control API 자체가 검증 대상 |
| **왜 필요한가** | Hybrid 테스트 식별. 비즈니스 팀 소유인지 SRE 팀 소유인지 결정 |
| **잘못 분류 시 리스크** | 테스트 실패 시 책임 소재 불명확 → 장애 대응 지연 |
| **중복 분류** | 불허 — HYBRID로 분류 시 별도 태그 부여 |

---

### 📊 Boundary Decision Matrix

| B1 (Subject) | B2 (Injection) | B3 (Observable) | B4 (Vehicle) | **분류 결과** |
|:------------:|:--------------:|:---------------:|:------------:|:--------------|
| ✅ YES | ✅ YES | ✅ YES | ❌ NO | **PURE HEALING** |
| ✅ YES | ✅ YES | ❌ NO | ❌ NO | CHAOS (Not Healing) |
| ✅ YES | ❌ NO | ✅ YES | ❌ NO | UNIT HEALING |
| ❌ NO | ✅ YES | ✅ YES | ✅ YES | **HYBRID** |
| ❌ NO | ❌ NO | ❌ NO | - | **OUT OF SCOPE** |

---

## Phase 2: Internal Classification Axes

> **전제조건**: Phase 1에서 PURE HEALING 또는 UNIT HEALING으로 분류된 테스트만 대상

---

### Axis 1: Component Under Test (C-Axis)

| 항목 | 내용 |
|------|------|
| **ID** | `C-AXIS` |
| **판별 질문** | "이 테스트가 검증하는 힐링 컴포넌트는 무엇인가?" |
| **분류 값** | `C1-CIRCUIT-BREAKER` : 서킷 브레이커<br>`C2-DLQ` : Dead Letter Queue<br>`C3-RETRY-BACKOFF` : 재시도/백오프<br>`C4-FALLBACK` : 폴백/그레이스풀 디그레이드<br>`C5-CONTROL-API` : 운영 제어 API<br>`C6-OBSERVABILITY` : 메트릭/로그/트레이싱<br>`C7-MULTI-COMPONENT` : 복합 컴포넌트 |
| **왜 필요한가** | 컴포넌트별 커버리지 측정. 특정 컴포넌트 변경 시 영향받는 테스트 식별 |
| **잘못 분류 시 리스크** | 컴포넌트 리팩토링 시 관련 테스트 누락 → 회귀 버그 |
| **중복 분류** | 허용 — `C7-MULTI-COMPONENT`와 개별 컴포넌트 동시 태깅 가능 |

**코드베이스 매핑 예시**:
```
C1-CIRCUIT-BREAKER:
  - stage15_cb_transitions.py
  - stage26_connection_pool.py
  - test_circuit_breaker.py
  - test_circuit_breaker_service.py

C2-DLQ:
  - stage14_dlq_replay.py
  - stage29_bulk_dlq_replay.py
  - test_dlq_storage_and_replay.py
  - test_dlq_retention.py

C3-RETRY-BACKOFF:
  - test_backoff_policy.py
  - test_backoff_jitter_distribution.py
  - test_retry_configuration.py
  - stage32_retry_storm_extended.py

C5-CONTROL-API:
  - stage10_self_healing.py
  - test_control_api.py
  - test_manual_override_policy.py
```

---

### Axis 2: Test Granularity (G-Axis)

| 항목 | 내용 |
|------|------|
| **ID** | `G-AXIS` |
| **판별 질문** | "이 테스트의 범위(scope)와 실행 시간은?" |
| **분류 값** | `G1-UNIT` : 단일 함수/클래스, <1s, Mock 사용<br>`G2-INTEGRATION` : 다중 컴포넌트, 1-30s, 일부 실제 인프라<br>`G3-E2E` : 전체 시스템, 30s-5m, 실제 인프라<br>`G4-LOAD` : 부하 테스트, 5m+, Locust/K6<br>`G5-CHAOS` : 카오스 엔지니어링, 실 장애 주입 |
| **왜 필요한가** | 테스트 실행 파이프라인 구성. CI/CD 단계별 실행 테스트 선별 |
| **잘못 분류 시 리스크** | E2E를 Unit으로 분류 → CI에서 5분 테스트 실행 → 빌드 지연<br>Unit을 E2E로 분류 → 빠른 피드백 기회 상실 |
| **중복 분류** | 불허 — 하나의 granularity만 선택 |

**코드베이스 매핑 예시**:
```
G1-UNIT:
  - tests/_unclassified/test_backoff_policy.py
  - tests/_unclassified/test_circuit_breaker_ttl.py

G2-INTEGRATION:
  - scripts/test_selfhealing_integration.py
  - tests/_unclassified/test_circuit_breaker.py

G3-E2E:
  - tests/e2e/test_stage26_circuit_breaker.py
  - tests/e2e/test_pool_recovery.py

G4-LOAD:
  - load_tests/scenarios/stage*.py (대부분)

G5-CHAOS:
  - load_tests/chaos/test_partial_partition_chaos.py
  - load_tests/scenarios/stage6_chaos_random.py
```

---

### Axis 3: Failure Scenario Type (F-Axis)

| 항목 | 내용 |
|------|------|
| **ID** | `F-AXIS` |
| **판별 질문** | "이 테스트가 시뮬레이션하는 장애 유형은?" |
| **분류 값** | `F1-TRANSIENT` : 일시적 장애 (네트워크 타임아웃, 일시적 503)<br>`F2-PERSISTENT` : 지속적 장애 (서비스 다운, DB 연결 끊김)<br>`F3-CASCADING` : 연쇄 장애 (A→B→C 순차 실패)<br>`F4-RESOURCE` : 리소스 고갈 (메모리, 커넥션 풀, 스레드)<br>`F5-DATA` : 데이터 손상/불일치 (캐시 오염, 스키마 불일치)<br>`F6-TIMING` : 타이밍 장애 (데드락, 레이스 컨디션, 클럭 스큐) |
| **왜 필요한가** | 장애 유형별 커버리지 확인. 실제 운영 장애 패턴과 테스트 매핑 |
| **잘못 분류 시 리스크** | 특정 장애 유형 테스트 부재 인지 실패 → 해당 유형 장애 시 대응력 미검증 |
| **중복 분류** | 허용 — 복합 장애 시나리오 가능 |

**코드베이스 매핑 예시**:
```
F1-TRANSIENT:
  - stage3_latency.py (지연 발생)
  - stage12_spike_recovery.py

F2-PERSISTENT:
  - stage26_connection_pool.py (풀 고갈)
  - test_db_connection_recovery.py

F3-CASCADING:
  - stage31_cascade_extended.py
  - stage18_chain_failure.py
  - test_cascading_failures.py

F4-RESOURCE:
  - stage36_memory_pressure.py
  - stage26_extreme_pool_test.py

F5-DATA:
  - stage35_cache_poison.py
  - stage17_cache_ttl_race.py
  - stage37_schema_compat.py

F6-TIMING:
  - stage16_db_lock_recovery.py (데드락)
  - stage34_db_deadlock.py
  - stage23_clock_skew.py
  - stage40_idempotency_clock_skew.py
```

---

### Axis 4: Recovery Mechanism Tested (R-Axis)

| 항목 | 내용 |
|------|------|
| **ID** | `R-AXIS` |
| **판별 질문** | "이 테스트가 검증하는 복구 메커니즘은?" |
| **분류 값** | `R1-AUTO-RECOVERY` : 자동 복구 (시스템이 개입 없이 회복)<br>`R2-MANUAL-INTERVENTION` : 수동 개입 (운영자 Control API 호출)<br>`R3-GRACEFUL-DEGRADATION` : 우아한 저하 (기능 축소로 서비스 유지)<br>`R4-FAILOVER` : 페일오버 (백업 시스템으로 전환)<br>`R5-REPLAY` : 재처리 (DLQ replay, 보상 트랜잭션) |
| **왜 필요한가** | 복구 전략별 테스트 존재 확인. SRE 런북과 테스트 매핑 |
| **잘못 분류 시 리스크** | 수동 개입 필요 상황을 자동 복구로 오인 → 장애 시 대응 지연 |
| **중복 분류** | 허용 — 하나의 테스트가 여러 복구 메커니즘 검증 가능 |

**코드베이스 매핑 예시**:
```
R1-AUTO-RECOVERY:
  - stage15_cb_transitions.py (CB 자동 HALF_OPEN→CLOSED)
  - stage12_spike_recovery.py (스파이크 후 자동 안정화)

R2-MANUAL-INTERVENTION:
  - stage10_self_healing.py (Control API)
  - test_manual_override_policy.py

R3-GRACEFUL-DEGRADATION:
  - stage24_cache_dead_protection.py (캐시 죽어도 서비스 유지)
  - test_redis_fallback.py

R5-REPLAY:
  - stage14_dlq_replay.py
  - stage29_bulk_dlq_replay.py
  - test_forensic_replay.py
```

---

### Axis 5: Stage/Maturity Level (S-Axis)

| 항목 | 내용 |
|------|------|
| **ID** | `S-AXIS` |
| **판별 질문** | "이 테스트의 성숙도와 스테이지 번호는?" |
| **분류 값** | `S1-FOUNDATION` (Stage 0-9) : 기초 힐링 검증<br>`S2-ADVANCED` (Stage 10-19) : 고급 시나리오<br>`S3-EXTREME` (Stage 20-29) : 극한 조건<br>`S4-CHAOS-PRODUCTION` (Stage 30-39) : 프로덕션급 카오스<br>`S5-GOVERNANCE` (Stage 40-45) : 거버넌스/컴플라이언스 |
| **왜 필요한가** | 스테이지 기반 점진적 테스트 실행. 릴리스 전 필수 통과 스테이지 정의 |
| **잘못 분류 시 리스크** | 고급 테스트 실패 시 기초 문제인지 고급 문제인지 판단 불가 |
| **중복 분류** | 불허 — 스테이지 번호는 고유 |

---

### Axis 6: Execution Environment (E-Axis)

| 항목 | 내용 |
|------|------|
| **ID** | `E-AXIS` |
| **판별 질문** | "이 테스트 실행에 필요한 환경은?" |
| **분류 값** | `E1-LOCAL` : 로컬 (pytest 단독)<br>`E2-DOCKER` : Docker Compose 필요<br>`E3-K8S` : Kubernetes 클러스터 필요<br>`E4-CLOUD` : 클라우드 리소스 필요 (AWS/GCP)<br>`E5-MULTI-REGION` : 멀티 리전 환경 필요 |
| **왜 필요한가** | 테스트 실행 환경 자동 프로비저닝. CI 러너 선택 기준 |
| **잘못 분류 시 리스크** | K8S 필요 테스트를 로컬에서 실행 시도 → 실패 후 디버깅 시간 낭비 |
| **중복 분류** | 허용 — 여러 환경에서 실행 가능한 테스트 존재 |

**코드베이스 매핑 예시**:
```
E1-LOCAL:
  - tests/_unclassified/test_backoff_policy.py

E2-DOCKER:
  - docker-compose.stage*.yml 기반 테스트 대부분
  - load_tests/scenarios/stage*.py

E3-K8S:
  - stage39_k8s_runtime_chaos.py
  - stage39_k8s_governance_boundaries.py

E5-MULTI-REGION:
  - stage28_multi_region.py
  - stage38_multi_region_failover.py
```

---

## 의사결정 플로우차트

```
                              ┌────────────────────────────────┐
                              │     새 테스트 파일 발견         │
                              └───────────────┬────────────────┘
                                              │
                    ┌─────────────────────────▼─────────────────────────┐
                    │ B1: SUT가 힐링 컴포넌트(CB, DLQ, Retry, Control)인가? │
                    └─────────────────────────┬─────────────────────────┘
                                              │
                          ┌───────────────────┼───────────────────┐
                          │ YES              │ PARTIAL           │ NO
                          ▼                   ▼                   ▼
                    ┌──────────┐        ┌──────────┐        ┌──────────┐
                    │ Continue │        │  Check   │        │   OUT    │
                    │ to B2    │        │   B4     │        │ OF SCOPE │
                    └────┬─────┘        └────┬─────┘        └──────────┘
                         │                   │
          ┌──────────────▼──────────────┐    │ B4=YES → HYBRID
          │ B2: 의도적 장애 주입 있는가?  │◄───┘
          └──────────────┬──────────────┘
                         │
              ┌──────────┼──────────┐
              │ YES      │ NO       │
              ▼          ▼          │
        ┌──────────┐ ┌──────────┐   │
        │ Continue │ │  UNIT    │   │
        │ to B3    │ │ HEALING  │   │
        └────┬─────┘ └──────────┘   │
             │                      │
  ┌──────────▼──────────┐          │
  │ B3: 복구 상태를      │          │
  │ 명시적으로 검증하는가?│          │
  └──────────┬──────────┘          │
             │                      │
    ┌────────┼────────┐            │
    │ YES    │ NO     │            │
    ▼        ▼        │            │
┌────────┐ ┌────────┐ │            │
│  PURE  │ │ CHAOS  │ │            │
│HEALING │ │ (NOT   │ │            │
│        │ │HEALING)│ │            │
└───┬────┘ └────────┘ │            │
    │                 │            │
    │                 │            │
    ▼                 │            │
┌────────────────────────────────────────────┐
│          PHASE 2: INTERNAL AXES            │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐  │
│  │C-Axis│ │G-Axis│ │F-Axis│ │R-Axis│ │E-Axis│  │
│  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘  │
└────────────────────────────────────────────┘
```

---

## 분류 매트릭스 템플릿

각 테스트 파일에 대해 아래 형식으로 분류를 기록합니다:

### 예시 1: Pure Healing Test

```yaml
file: load_tests/scenarios/stage15_cb_transitions.py
boundary:
  B1_PRIMARY_SUBJECT: YES  # SUT = Circuit Breaker
  B2_FAILURE_INJECTION: YES  # Phase 2에서 장애 주입
  B3_RECOVERY_OBSERVABLE: YES  # Phase 4에서 CLOSED 복귀 검증
  B4_HEALING_AS_VEHICLE: NO
  classification: PURE_HEALING

internal_axes:
  C_AXIS: [C1-CIRCUIT-BREAKER]
  G_AXIS: G4-LOAD
  F_AXIS: [F1-TRANSIENT]
  R_AXIS: [R1-AUTO-RECOVERY]
  S_AXIS: S2-ADVANCED  # Stage 15
  E_AXIS: [E2-DOCKER]

ownership: SRE
ci_stage: post-merge
required_for_release: true
```

### 예시 2: Out of Scope

```yaml
file: load_tests/scenarios/stage1_happy_load.py
boundary:
  B1_PRIMARY_SUBJECT: NO  # SUT = 비즈니스 플로우
  B2_FAILURE_INJECTION: NO
  B3_RECOVERY_OBSERVABLE: NO
  B4_HEALING_AS_VEHICLE: NO
  classification: OUT_OF_SCOPE

reason: "Baseline 성능 측정. 힐링 컴포넌트 미관여"
ownership: QA
```

### 예시 3: Hybrid

```yaml
file: tests/_unclassified/test_payment_tasks.py
boundary:
  B1_PRIMARY_SUBJECT: PARTIAL  # CB 등장하나 주 대상 아님
  B2_FAILURE_INJECTION: YES
  B3_RECOVERY_OBSERVABLE: PARTIAL
  B4_HEALING_AS_VEHICLE: YES  # 결제 성공이 최종 목표
  classification: HYBRID

internal_axes:
  C_AXIS: [C1-CIRCUIT-BREAKER, C3-RETRY-BACKOFF]
  G_AXIS: G2-INTEGRATION

ownership: SHARED  # QA + SRE 공동
note: "결제 로직 변경 시 QA 검토, 힐링 정책 변경 시 SRE 검토"
```

---

## 부록: 코드베이스 힐링시스템 테스트 현황 (초안)

아래는 현재 식별된 힐링시스템 테스트 후보입니다.

### Pure Healing Tests (확정)

| 파일 | C-Axis | Stage |
|------|--------|-------|
| stage10_self_healing.py | C5-CONTROL-API | S2 |
| stage14_dlq_replay.py | C2-DLQ | S2 |
| stage15_cb_transitions.py | C1-CIRCUIT-BREAKER | S2 |
| stage16_db_lock_recovery.py | C1-CIRCUIT-BREAKER | S2 |
| stage26_connection_pool.py | C1-CIRCUIT-BREAKER | S3 |
| stage29_bulk_dlq_replay.py | C2-DLQ | S3 |
| test_l3_self_healing.py | C7-MULTI | - |
| test_circuit_breaker.py | C1-CIRCUIT-BREAKER | - |
| test_dlq_storage_and_replay.py | C2-DLQ | - |

### Chaos (Not Pure Healing)

| 파일 | 이유 |
|------|------|
| stage6_chaos_random.py | 복구 검증 미흡, 안정성만 확인 |
| test_partial_partition_chaos.py | 네트워크 파티션 주입만 |

### Out of Scope

| 파일 | 이유 |
|------|------|
| stage1_happy_load.py | Baseline 성능 측정 |
| test_edge_cases_*.py | 비즈니스 로직 테스트 |
| shopping/tests/unit/* | 비즈니스 단위 테스트 |
| shopping/tests/api/* | API 기능 테스트 |

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 1.0 | 2025-12-17 | 초안 작성 |
