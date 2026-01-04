# 20. Audit 통합 구현 계획

> **문서 버전**: 1.0.0
> **생성일**: 2026-01-04
> **목적**: 분산된 Audit 구현을 도메인 중립적인 단일 시스템으로 통합

---

## 📋 목차

1. [개요](#1-개요)
2. [현재 상태 분석](#2-현재-상태-분석)
3. [설계 결정사항 (ADR)](#3-설계-결정사항-adr)
4. [목표 아키텍처](#4-목표-아키텍처)
5. [구현 계획](#5-구현-계획)
6. [마이그레이션 가이드](#6-마이그레이션-가이드)
7. [테스트 계획](#7-테스트-계획)

---

## 1. 개요

### 1.1 배경

Self-Healing 시스템은 **도메인 중립(Domain-Neutral)** 라이브러리로 설계됨:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Self-Healing Package (Domain-Neutral)                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│  • 어떤 도메인에도 독립적으로 동작해야 함                                         │
│  • Shopping API = 테스트베드 (실험 및 검증용일 뿐)                               │
│  • 다른 프로젝트에서도 그대로 사용 가능해야 함                                    │
│  • 도메인 특화 코드는 라이브러리 외부에서 설정으로 주입                           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 문제점

현재 Audit 구현이 **3가지 방식으로 분산**되어 있음:

| 방식 | 사용처 | 문제점 |
|------|--------|--------|
| **RequestAuditBuffer** | DLQService, GovernanceChecks | ✅ 권장 방식 |
| **자체 _audit() 메서드** | ChaosExperiment, EmergencyMode | ❌ 중복 구현, 해시 체인 미연결 |
| **Audit 없음** | RetryHandler, SystemControl 등 | ❌ 감사 추적 누락 |

### 1.3 목표

1. **단일 진입점**: 모든 Audit 기록이 `audit_helpers.py`를 통해 이루어짐
2. **도메인 중립**: 비즈니스 도메인 코드가 Audit 시스템에 포함되지 않음
3. **해시 체인 무결성**: 모든 기록이 단일 해시 체인에 연결됨
4. **기존 코드 호환**: 자체 구현을 점진적으로 마이그레이션

---

## 2. 현재 상태 분석

### 2.1 Audit 기록이 있는 서비스

| 서비스 | 현재 방식 | 해시 체인 | 통합 난이도 |
|--------|----------|:--------:|:-----------:|
| DLQService | `audit_helpers.log_dlq_store_audit` | ✅ | - (이미 통합) |
| ReplayService | `audit_helpers.log_dlq_replay_audit` | ✅ | - (이미 통합) |
| GovernanceChecks | `RequestAuditBuffer` 직접 사용 | ✅ | 🟢 낮음 |
| PoolCBMiddleware | `RequestAuditBuffer` 직접 사용 | ✅ | 🟢 낮음 |
| ChaosExperiment | `self._audit()` 자체 구현 | ❌ | 🟡 중간 |
| EmergencyModeManager | `self._log_audit()` 자체 구현 | ❌ | 🟡 중간 |
| ErrorBudgetGate | `self._audit_block()` 자체 구현 | ❌ | 🟡 중간 |

### 2.2 Audit 기록이 없는 서비스

| 서비스 | 기록해야 할 이벤트 | 우선순위 |
|--------|------------------|:--------:|
| **RetryHandler** | 재시도 횟수, 최종 성공/실패 | 🔴 높음 |
| **RateLimitCoordinator** | Self-DDoS 차단 발동 | 🔴 높음 |
| **SystemControl** | Kill Switch 활성화/비활성화 | 🔴 높음 |
| **RollbackService** | 자동 롤백 수행 | 🔴 높음 |
| **ComplianceService** | 규정 위반 감지 | 🔴 높음 |
| **BlastRadiusService** | 서비스 격리 결정 | 🟡 중간 |
| **FinOpsService** | 비용 임계값 초과 | 🟡 중간 |
| **LearningService** | 패턴 학습 결과 | 🟢 낮음 |

### 2.3 Celery Tasks Audit 현황

| Task | 현재 상태 | 기록해야 할 이벤트 |
|------|----------|------------------|
| `config_apply` | ❌ 없음 | 설정 적용 시작/완료 |
| `chaos_scheduler` | ❌ 없음 | 실험 스케줄링 |
| `governance` | ❌ 없음 | Emergency 만료 체크 |
| `drift_detection` | ❌ 없음 | 드리프트 감지 |
| `compliance_tasks` | ❌ 없음 | 컴플라이언스 검사 |
| `traffic_aware_replay` | ⚠️ `audit_on_block=False` | 배치 리플레이 결과 |

---

## 3. 설계 결정사항 (ADR)

### ADR-001: Audit 이벤트 네이밍 컨벤션

**상태**: ✅ 결정됨 (2026-01-05)

**결정**: **옵션 A (동사_명사)**

| 옵션 | 예시 | 장점 | 단점 |
|------|------|------|------|
| **A. 동사_명사** ✅ | `RETRY_ATTEMPTED`, `ROLLBACK_PERFORMED` | 명확한 액션 표현 | 길어질 수 있음 |
| B. 명사_상태 | `RETRY_SUCCESS`, `ROLLBACK_COMPLETED` | 상태 중심 | 진행 중 표현 어려움 |
| C. 계층형 | `RETRY.ATTEMPTED`, `ROLLBACK.STARTED` | 그룹화 용이 | Enum 표현 복잡 |

**결정 이유**:
- 기존 `DLQ_STORE`, `CB_STATE_CHANGE`와 일관성
- 명확한 액션 의미 전달

---

### ADR-002: 조회(Read) 이벤트 기록 정책

**상태**: ✅ 결정됨 (2026-01-05)

**결정**: **옵션 D (설정 기반)**

| 옵션 | 설명 | 장점 | 단점 |
|------|------|------|------|
| A. 기록 안함 | 모든 조회 무시 | 저장 공간 절약, 성능 | 감사 추적 불가 |
| B. 민감 조회만 | 개인정보, 결제 등 | 균형잡힌 접근 | 경계 정의 어려움 |
| C. Admin 조회만 | 관리자 API만 기록 | 운영 추적 가능 | 일반 민감 조회 누락 |
| **D. 설정 기반** ✅ | 경로 패턴으로 설정 | 유연함 | 설정 관리 필요 |

**결정 이유**:
- 도메인 중립 유지: 라이브러리는 메커니즘만 제공
- 사용자가 자신의 도메인에 맞게 설정

```python
# 사용자 설정 예시 (Django settings.py)
SELFHEALING_AUDIT = {
    "read_paths": [
        "/api/admin/",           # 모든 Admin 조회
        "/api/payments/",        # 결제 관련 조회
        "/api/users/personal/",  # 개인정보 조회
    ],
    "exclude_paths": [
        "/health/",
        "/metrics/",
    ],
}
```

---

### ADR-003: 비-HTTP 컨텍스트 (Celery) Audit 방식

**상태**: ✅ 결정됨 (2026-01-05)

**결정**: **옵션 A (즉시 기록)**

| 옵션 | 설명 | 장점 | 단점 |
|------|------|------|------|
| **A. 즉시 기록** ✅ | 이벤트 발생 시 바로 기록 | 단순함 | 해시 체인 분리될 수 있음 |
| B. Task 단위 버퍼 | Task 시작~종료 버퍼링 | HTTP와 유사 패턴 | 구현 복잡 |
| C. 별도 해시 체인 | Celery용 별도 체인 | 격리됨 | 통합 검증 어려움 |

**결정 이유**:
- 현재 `audit_helpers.py`의 fallback 로직과 일치
- Celery Task는 독립적이므로 버퍼링 이점 적음
- 해시 체인은 시간순으로 연결되므로 문제없음

---

### ADR-004: 자체 구현 마이그레이션 전략

**상태**: ✅ 결정됨 (2026-01-05)

**결정**: **옵션 B (Strangler Fig)**

| 옵션 | 설명 | 장점 | 단점 |
|------|------|------|------|
| A. Big Bang | 한 번에 모두 교체 | 일관성 | 위험, 대규모 변경 |
| **B. Strangler Fig** ✅ | 점진적 교체 | 안전함 | 과도기 복잡성 |
| C. Adapter 래핑 | 기존 구현을 헬퍼로 래핑 | 기존 코드 유지 | 간접 레이어 추가 |

**결정 이유**:
- 서비스별로 점진적 마이그레이션
- 각 단계에서 테스트 검증 가능
- 롤백 용이

---

### ADR-005: Audit 실패 처리 정책

**상태**: ✅ 결정됨 (2026-01-05 보완)

**결정**: **Fail-Open + WAL 기반 누락 0 보장**

기존 Fail-Open 정책을 유지하되, WAL(Write-Ahead Log)을 활용하여 누락 0을 보장:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      Fail-Open + WAL 기반 누락 0 보장                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  이벤트 발생                                                                    │
│      │                                                                          │
│      ▼                                                                          │
│  ┌─────────────────┐                                                            │
│  │ ① WAL에 기록    │ ← 로컬 파일, CRC32 체크섬 (거의 실패 안함)                │
│  │    (동기)       │                                                            │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐     ┌─────────────────┐                                   │
│  │ ② 중앙 저장소   │ ──→ │ 성공: WAL 정리  │                                   │
│  │    기록 시도    │     └─────────────────┘                                   │
│  │   (Best Effort) │                                                            │
│  └────────┬────────┘                                                            │
│           │ 실패                                                                │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ ③ Background    │ ← WAL에서 미처리 항목 재시도 (1초 주기)                   │
│  │    Sync Worker  │                                                            │
│  └────────┬────────┘                                                            │
│           │ 장기 실패                                                           │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ ④ Reconciler    │ ← WAL vs 중앙 비교, 누락 감지 및 재전송 (5분 주기)        │
│  └─────────────────┘                                                            │
│                                                                                  │
│  보장 수준: 디스크 장애 제외 100% (디스크 장애 시 CRITICAL 알림)                │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**기존 컴포넌트 활용**:
- `selfhealing/audit/wal.py` - CRC32 체크섬 WAL (이미 구현됨)
- `selfhealing/audit/ring_buffer.py` - Non-blocking 버퍼 (이미 구현됨)
- `selfhealing/audit/resilient_recorder.py` - WAL + RingBuffer 통합 (이미 구현됨)

**추가 구현 필요**:
1. `audit_helpers.py`에서 WAL.write() 먼저 호출
2. Background Sync Worker (WAL → 중앙 저장소)
3. Reconciler (주기적 정합성 검증)

**모니터링 메트릭**:
```
audit_wal_writes_total          # WAL 기록 수
audit_central_writes_total      # 중앙 저장소 기록 수
audit_sync_lag_seconds          # WAL → 중앙 동기화 지연
audit_reconcile_missing_total   # Reconciler가 발견한 누락 수
audit_wal_write_failures_total  # WAL 기록 실패 (CRITICAL 알림)
```

---

### ADR-006: AuditEventType 확장 방식

**상태**: 결정 필요

**선택지**:

| 옵션 | 설명 | 장점 | 단점 |
|------|------|------|------|
| **A. 단일 Enum 확장** | 기존 Enum에 추가 | 단순함 | Enum이 커짐 |
| **B. 카테고리별 Enum** | `RetryEventType`, `SystemEventType` 등 | 모듈화 | import 복잡 |
| **C. 문자열 기반** | Enum 대신 문자열 상수 | 유연함 | 타입 안정성 저하 |

**권장**: **옵션 A (단일 Enum 확장)**
- 기존 `AuditEventType`에 새 값 추가
- 타입 안정성 유지
- IDE 자동완성 지원

---

## 4. 목표 아키텍처

### 4.1 통합 후 구조

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        통합 Audit 아키텍처                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         audit_helpers.py                                 │   │
│  │                    (단일 진입점 - Domain Neutral)                        │   │
│  ├─────────────────────────────────────────────────────────────────────────┤   │
│  │                                                                          │   │
│  │  # DLQ 관련 (기존)                                                       │   │
│  │  log_dlq_store_audit()                                                   │   │
│  │  log_dlq_replay_audit()                                                  │   │
│  │                                                                          │   │
│  │  # Circuit Breaker 관련 (기존)                                           │   │
│  │  log_cb_state_change_audit()                                             │   │
│  │                                                                          │   │
│  │  # Governance 관련 (기존)                                                │   │
│  │  log_governance_blocked_audit()                                          │   │
│  │  log_rate_limited_audit()                                                │   │
│  │  log_pool_cb_rejection_audit()                                           │   │
│  │                                                                          │   │
│  │  # Phase 1 추가 ──────────────────────────────────────────────────────  │   │
│  │  log_retry_audit()              # RetryHandler용                         │   │
│  │  log_system_control_audit()     # SystemControl용                        │   │
│  │  log_rollback_audit()           # RollbackService용                      │   │
│  │                                                                          │   │
│  │  # Phase 2 추가 ──────────────────────────────────────────────────────  │   │
│  │  log_chaos_experiment_audit()   # ChaosExperiment 통합                   │   │
│  │  log_emergency_mode_audit()     # EmergencyModeManager 통합              │   │
│  │  log_error_budget_audit()       # ErrorBudgetGate 통합                   │   │
│  │                                                                          │   │
│  │  # Phase 3 추가 ──────────────────────────────────────────────────────  │   │
│  │  log_compliance_audit()         # ComplianceService용                    │   │
│  │  log_blast_radius_audit()       # BlastRadiusService용                   │   │
│  │  log_finops_audit()             # FinOpsService용                        │   │
│  │                                                                          │   │
│  │  # 범용 (도메인 중립 확장용)                                              │   │
│  │  log_generic_audit()            # 사용자 정의 이벤트                      │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                           │
│                    ┌────────────────┴────────────────┐                          │
│                    │                                 │                          │
│                    ▼                                 ▼                          │
│  ┌─────────────────────────────┐   ┌─────────────────────────────────────┐     │
│  │ HTTP 컨텍스트               │   │ 비-HTTP 컨텍스트                    │     │
│  │ (request 있음)              │   │ (Celery, Background)               │     │
│  ├─────────────────────────────┤   ├─────────────────────────────────────┤     │
│  │ RequestAuditBuffer에 적재   │   │ 직접 AuditAdapter 호출              │     │
│  │        │                    │   │                                     │     │
│  │        ▼                    │   │                                     │     │
│  │ AuditMiddleware에서         │   │                                     │     │
│  │ 응답 직전 일괄 기록         │   │                                     │     │
│  └─────────────────────────────┘   └─────────────────────────────────────┘     │
│                    │                                 │                          │
│                    └────────────────┬────────────────┘                          │
│                                     ▼                                           │
│                    ┌─────────────────────────────────┐                          │
│                    │    ContinuousAuditRecorder      │                          │
│                    │    (단일 해시 체인)             │                          │
│                    └─────────────────────────────────┘                          │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 확장된 AuditEventType

```python
class AuditEventType(Enum):
    """Audit 이벤트 유형 (도메인 중립)."""
    
    # ═══════════════════════════════════════════════════════════════
    # DLQ 관련 (기존)
    # ═══════════════════════════════════════════════════════════════
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY = "dlq_replay"
    DLQ_ESCALATE = "dlq_escalate"
    
    # ═══════════════════════════════════════════════════════════════
    # Circuit Breaker 관련 (기존)
    # ═══════════════════════════════════════════════════════════════
    CB_STATE_CHANGE = "circuit_breaker_state_change"
    CB_REJECTION = "circuit_breaker_rejection"
    CB_RECOVERY = "circuit_breaker_recovery"
    
    # ═══════════════════════════════════════════════════════════════
    # Governance 관련 (기존)
    # ═══════════════════════════════════════════════════════════════
    GOVERNANCE_BLOCKED = "governance_blocked"
    GOVERNANCE_KILL_SWITCH = "governance_kill_switch"
    
    # ═══════════════════════════════════════════════════════════════
    # Rate Limit / Pool CB 관련 (기존)
    # ═══════════════════════════════════════════════════════════════
    RATE_LIMITED = "rate_limited"
    POOL_CB_REJECTION = "pool_circuit_breaker_rejection"
    POOL_CB_STATE_CHANGE = "pool_circuit_breaker_state_change"
    
    # ═══════════════════════════════════════════════════════════════
    # 시스템 / 설정 관련 (기존)
    # ═══════════════════════════════════════════════════════════════
    ERROR_DETECTED = "error_detected"
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"
    
    # ═══════════════════════════════════════════════════════════════
    # 복구 관련 (기존)
    # ═══════════════════════════════════════════════════════════════
    RECOVERY_EVENT = "recovery_event"
    RECOVERY_CHAIN_STARTED = "recovery_chain_started"
    RECOVERY_CHAIN_COMPLETED = "recovery_chain_completed"
    
    # ═══════════════════════════════════════════════════════════════
    # Phase 1 추가: 재시도 / 시스템 제어 / 롤백
    # ═══════════════════════════════════════════════════════════════
    RETRY_ATTEMPTED = "retry_attempted"
    RETRY_EXHAUSTED = "retry_exhausted"
    SYSTEM_CONTROL_CHANGED = "system_control_changed"
    ROLLBACK_PERFORMED = "rollback_performed"
    
    # ═══════════════════════════════════════════════════════════════
    # Phase 2 추가: Chaos / Emergency / Error Budget
    # ═══════════════════════════════════════════════════════════════
    CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
    CHAOS_EXPERIMENT_COMPLETED = "chaos_experiment_completed"
    CHAOS_INJECTION_APPLIED = "chaos_injection_applied"
    CHAOS_ROLLBACK_TRIGGERED = "chaos_rollback_triggered"
    EMERGENCY_MODE_ACTIVATED = "emergency_mode_activated"
    EMERGENCY_MODE_DEACTIVATED = "emergency_mode_deactivated"
    ERROR_BUDGET_DEPLETED = "error_budget_depleted"
    ERROR_BUDGET_BLOCKED = "error_budget_blocked"
    
    # ═══════════════════════════════════════════════════════════════
    # Phase 3 추가: Compliance / Blast Radius / FinOps
    # ═══════════════════════════════════════════════════════════════
    COMPLIANCE_VIOLATION = "compliance_violation"
    COMPLIANCE_CHECK_PASSED = "compliance_check_passed"
    BLAST_RADIUS_ISOLATION = "blast_radius_isolation"
    FINOPS_THRESHOLD_EXCEEDED = "finops_threshold_exceeded"
    
    # ═══════════════════════════════════════════════════════════════
    # 데이터 접근 (설정 기반 - ADR-002)
    # ═══════════════════════════════════════════════════════════════
    DATA_ACCESS = "data_access"
    
    # ═══════════════════════════════════════════════════════════════
    # 범용 (사용자 확장용)
    # ═══════════════════════════════════════════════════════════════
    GENERIC = "generic"
```

---

## 5. 구현 계획

### 5.0 Phase 0: WAL 기반 누락 0 보장 (1.5일)

**목표**: 기존 WAL 컴포넌트를 활용하여 Audit 누락 0 달성

| 작업 | 파일 | 예상 시간 | 상태 |
|------|------|:--------:|:----:|
| 0-1. `audit_helpers.py`에 WAL 연동 | `audit_helpers.py` | 2h | ✅ 완료 |
| 0-2. Background Sync Worker 구현 | `audit/sync_worker.py` (신규) | 3h | ✅ 완료 |
| 0-3. Reconciler 구현 | `audit/reconciler.py` (신규) | 3h | ✅ 완료 |
| 0-4. 메트릭 추가 | `audit/resilience.py` (AuditMetrics 확장) | 1h | ✅ 완료 |
| 0-5. 단위 테스트 | `tests/unit/test_audit_wal_phase0.py` | 2h | ✅ 완료 (23개 통과) |

**Phase 0 완료 기준**:
- [x] 모든 audit_helpers 함수가 WAL에 먼저 기록
- [x] Sync Worker가 WAL → 중앙 저장소 동기화
- [x] Reconciler가 누락 감지 및 재전송
- [x] 메트릭으로 동기화 상태 모니터링 가능

**구현된 파일**:
- `packages/selfhealing-python/src/selfhealing/services/audit_helpers.py` - WAL 연동 추가
- `packages/selfhealing-python/src/selfhealing/audit/sync_worker.py` - 신규
- `packages/selfhealing-python/src/selfhealing/audit/reconciler.py` - 신규
- `packages/selfhealing-python/src/selfhealing/audit/resilience.py` - WAL 메트릭 추가
- `packages/selfhealing-python/tests/unit/test_audit_wal_phase0.py` - 23개 테스트

---

### 5.1 Phase 1: 신규 서비스 Audit 추가 (2일)

**목표**: Audit이 없는 Critical 서비스에 기록 추가

| 작업 | 파일 | 예상 시간 | 상태 |
|------|------|:--------:|:----:|
| 1-1. AuditEventType 확장 | `event_buffer.py` | 0.5h | ✅ 완료 |
| 1-2. `log_retry_audit()` 추가 | `audit_helpers.py` | 1h | ✅ 완료 |
| 1-3. `log_system_control_audit()` 추가 | `audit_helpers.py` | 1h | ✅ 완료 |
| 1-4. `log_rollback_audit()` 추가 | `audit_helpers.py` | 1h | ✅ 완료 |
| 1-5. RetryHandler에 audit 호출 추가 | `retry_handler.py` | 1h | ✅ 완료 |
| 1-6. SystemControl에 audit 호출 추가 | `system_control.py` | 1h | ✅ 완료 |
| 1-7. RollbackService에 audit 호출 추가 | `rollback/service.py` | 1h | ✅ 완료 |
| 1-8. 단위 테스트 | `tests/` | 2h | ✅ 완료 (26개 통과) |

**Phase 1 완료 기준**:
- [x] 4개 새 AuditEventType 추가 (RETRY_ATTEMPTED, RETRY_EXHAUSTED, SYSTEM_CONTROL_CHANGED, ROLLBACK_PERFORMED)
- [x] 3개 새 헬퍼 함수 구현 (log_retry_audit, log_system_control_audit, log_rollback_audit)
- [x] 3개 서비스에 audit 호출 추가 (RetryHandler, SystemControlManager, RollbackService)
- [x] 단위 테스트 통과 (26개)

**구현된 파일**:
- `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py` - AuditEventType 확장
- `packages/selfhealing-python/src/selfhealing/services/audit_helpers.py` - 3개 헬퍼 함수 추가
- `packages/selfhealing-python/src/selfhealing/services/retry_handler.py` - _log_retry_audit 통합
- `packages/selfhealing-python/src/selfhealing/services/system_control.py` - _log_audit 통합
- `packages/selfhealing-python/src/selfhealing/services/rollback/service.py` - _log_audit 통합
- `packages/selfhealing-python/tests/unit/test_audit_helpers_phase1.py` - 26개 테스트

---

### 5.2 Phase 2: 자체 구현 통합 (3일)

**목표**: 기존 `_audit()` 자체 구현을 헬퍼로 마이그레이션

| 작업 | 대상 서비스 | 현재 방식 | 예상 시간 | 상태 |
|------|-----------|----------|:--------:|:----:|
| 2-1. Chaos 헬퍼 추가 | `audit_helpers.py` | 신규 | 1.5h | ✅ 완료 |
| 2-2. Emergency 헬퍼 추가 | `audit_helpers.py` | 신규 | 1.5h | ✅ 완료 |
| 2-3. ErrorBudget 헬퍼 추가 | `audit_helpers.py` | 신규 | 1.5h | ✅ 완료 |
| 2-4. ChaosExperiment 마이그레이션 | `chaos/base.py` | `self._audit()` | 2h | ✅ 완료 |
| 2-5. EmergencyModeManager 마이그레이션 | `emergency_mode/manager.py` | `self._log_audit()` | 2h | ✅ 완료 |
| 2-6. ErrorBudgetGate 마이그레이션 | `error_budget_gate/gate.py` | `self._audit_block()` | 2h | ✅ 완료 |
| 2-7. 통합 테스트 | `tests/` | - | 3h | ✅ 완료 (31개 통과) |

**Phase 2 완료 기준**:
- [x] 3개 새 헬퍼 함수 구현 (log_chaos_experiment_audit, log_emergency_mode_audit, log_error_budget_blocked_audit)
- [x] 3개 서비스 마이그레이션 완료 (ChaosExperiment, EmergencyModeManager, ErrorBudgetGate)
- [x] 기존 동작 유지 (하위 호환: _audit_records 리스트, log_config_change 호환 호출)
- [x] 해시 체인에 기록됨 확인 (WAL 통합)
- [x] 8개 새 AuditEventType 추가 (Chaos 4개, Emergency 2개, ErrorBudget 2개)

**구현된 파일**:
- `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py` - Phase 2 AuditEventType 추가
- `packages/selfhealing-python/src/selfhealing/services/audit_helpers.py` - 3개 헬퍼 함수 추가
- `packages/selfhealing-python/src/selfhealing/services/chaos/base.py` - _audit 헬퍼 통합
- `packages/selfhealing-python/src/selfhealing/services/emergency_mode/manager.py` - _log_audit 헬퍼 통합
- `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py` - _audit_block 헬퍼 통합
- `packages/selfhealing-python/tests/unit/test_audit_helpers_phase2.py` - 31개 테스트

**마이그레이션 패턴**:

```python
# Before (자체 구현)
class ChaosExperiment:
    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        record_id = f"audit-{uuid.uuid4().hex[:8]}"
        self._audit_records.append(record_id)
        logger.info(f"[ChaosAudit] {event_type}", extra={"audit_data": data})

# After (헬퍼 사용)
class ChaosExperiment:
    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        from selfhealing.services.audit_helpers import log_chaos_experiment_audit
        
        record_id = log_chaos_experiment_audit(
            experiment_id=self.experiment_id,
            event_type=event_type,
            data=data,
        )
        self._audit_records.append(record_id)
```

**Phase 2 완료 기준**:
- [x] 3개 새 헬퍼 함수 구현
- [x] 3개 서비스 마이그레이션 완료
- [x] 기존 동작 유지 (하위 호환)
- [x] 해시 체인에 기록됨 확인

---

### 5.3 Phase 3: 추가 서비스 및 조회 기록 (2일)

**목표**: 나머지 서비스 및 민감 데이터 조회 기록

| 작업 | 예상 시간 | 상태 |
|------|:--------:|:----:|
| 3-1. `log_compliance_audit()` 추가 | 1h | ✅ 완료 |
| 3-2. `log_blast_radius_audit()` 추가 | 1h | ✅ 완료 |
| 3-3. `log_finops_audit()` 추가 | 1h | ✅ 완료 |
| 3-4. `log_data_access_audit()` 추가 | 1h | ✅ 완료 |
| 3-5. 조회 기록 설정 구현 (ADR-002) | 2h | ✅ 완료 |
| 3-6. AuditMiddleware 조회 기록 확장 | 2h | ✅ 완료 |
| 3-7. 서비스 통합 | 2h | ✅ 완료 |
| 3-8. 테스트 | 2h | ✅ 완료 (26개 통과) |

**Phase 3 완료 기준**:
- [x] 4개 새 헬퍼 함수 구현 (log_compliance_audit, log_blast_radius_audit, log_finops_audit, log_data_access_audit)
- [x] 7개 새 AuditEventType 추가 (COMPLIANCE_VIOLATION, COMPLIANCE_CHECK_PASSED, BLAST_RADIUS_ISOLATION, BLAST_RADIUS_VIOLATION, FINOPS_THRESHOLD_EXCEEDED, FINOPS_BUDGET_EXCEEDED, DATA_ACCESS)
- [x] 3개 서비스에 audit 통합 (ComplianceService, BlastRadiusManager, FinOpsService)
- [x] ADR-002 설정 기반 조회 기록 구현 (SELFHEALING_AUDIT["read_paths"])
- [x] AuditMiddleware에 DATA_ACCESS 이벤트 캡처 추가
- [x] 단위 테스트 통과 (26개)

**구현된 파일**:
- `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py` - Phase 3 AuditEventType 추가
- `packages/selfhealing-python/src/selfhealing/services/audit_helpers.py` - 4개 헬퍼 함수 추가
- `packages/selfhealing-python/src/selfhealing/services/compliance/service.py` - _log_compliance_audit 통합
- `packages/selfhealing-python/src/selfhealing/services/chaos/blast_radius.py` - _log_blast_radius_audit 통합
- `packages/selfhealing-python/src/selfhealing/services/finops/service.py` - _log_finops_audit 통합
- `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py` - DATA_ACCESS 캡처 추가
- `packages/selfhealing-python/tests/unit/test_audit_helpers_phase3.py` - 26개 테스트

**ADR-002 설정 예시**:

```python
# Django settings.py
SELFHEALING_AUDIT = {
    "read_paths": [
        "/api/admin/",           # 모든 Admin 조회
        "/api/payments/",        # 결제 관련 조회
        "/api/users/personal/",  # 개인정보 조회
    ],
    "exclude_paths": [
        "/health/",
        "/metrics/",
    ],
}
```

---

### 5.4 Phase 4: Celery Tasks Audit (1일)

**목표**: 주요 Celery Tasks에 Audit 추가

| Task | 기록할 이벤트 |
|------|-------------|
| `config_apply` | `CONFIG_CHANGE` |
| `chaos_scheduler` | `CHAOS_EXPERIMENT_STARTED` |
| `governance` | `EMERGENCY_MODE_*` |
| `drift_detection` | `CONFIG_CHANGE` |
| `traffic_aware_replay` | `DLQ_REPLAY` |

---

## 6. 마이그레이션 가이드

### 6.1 자체 구현 → 헬퍼 전환 체크리스트

```
□ 1. 기존 _audit() 메서드 식별
□ 2. 해당하는 AuditEventType 확인/추가
□ 3. audit_helpers.py에 헬퍼 함수 추가
□ 4. 서비스에서 헬퍼 호출로 교체
□ 5. 기존 동작 테스트 (audit_records 등)
□ 6. 해시 체인 연결 확인
```

### 6.2 도메인 중립 유지 규칙

```
✅ DO:
  - 이벤트 타입은 기술적 개념 사용 (RETRY_ATTEMPTED, CB_STATE_CHANGE)
  - 도메인 정보는 details 파라미터로 전달
  - 설정 기반 동작 (SELFHEALING_AUDIT 설정)

❌ DON'T:
  - 이벤트 타입에 도메인 포함 (PAYMENT_FAILED, ORDER_CREATED)
  - 하드코딩된 도메인 경로
  - Shopping API 특화 로직
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

```python
# tests/unit/test_audit_helpers.py

class TestRetryAudit:
    """log_retry_audit 테스트."""
    
    def test_with_request_adds_to_buffer(self):
        """HTTP 컨텍스트에서 버퍼에 추가됨."""
        
    def test_without_request_logs_directly(self):
        """비-HTTP 컨텍스트에서 직접 기록."""
        
    def test_fail_open_on_error(self):
        """Audit 실패 시 비즈니스 로직 중단 안함."""


class TestMigrationCompatibility:
    """자체 구현 마이그레이션 호환성 테스트."""
    
    def test_chaos_audit_records_preserved(self):
        """ChaosExperiment.audit_records가 유지됨."""
        
    def test_hash_chain_connected(self):
        """마이그레이션 후 해시 체인에 연결됨."""
```

### 7.2 통합 테스트

```python
# tests/integration/test_audit_unification.py

class TestAuditUnification:
    """Audit 통합 시스템 테스트."""
    
    def test_all_events_in_single_hash_chain(self):
        """모든 이벤트가 단일 해시 체인에 기록됨."""
        
    def test_celery_task_audit_recorded(self):
        """Celery Task 이벤트도 기록됨."""
        
    def test_read_audit_configurable(self):
        """조회 기록이 설정에 따라 동작."""
```

---

## 📋 결정 사항 요약

| ADR | 상태 | 결정 | 결정일 |
|-----|:----:|--------|--------|
| ADR-001 네이밍 컨벤션 | ✅ | 옵션 A (동사_명사) | 2026-01-05 |
| ADR-002 조회 기록 정책 | ✅ | 옵션 D (설정 기반) | 2026-01-05 |
| ADR-003 Celery Audit 방식 | ✅ | 옵션 A (즉시 기록) | 2026-01-05 |
| ADR-004 마이그레이션 전략 | ✅ | 옵션 B (Strangler Fig) | 2026-01-05 |
| ADR-005 실패 처리 정책 | ✅ | Fail-Open + WAL | 2026-01-05 |
| ADR-006 EventType 확장 | ✅ | 옵션 A (단일 Enum) | 2026-01-05 |

---

## 📅 일정 요약

| Phase | 기간 | 산출물 | 상태 |
|:-----:|:----:|--------|:----:|
| **Phase 0** | 1.5일 | WAL 기반 누락 0 보장 | ✅ 완료 |
| **Phase 1** | 2일 | 3개 신규 서비스 Audit | ✅ 완료 |
| **Phase 2** | 3일 | 3개 자체 구현 통합 | ✅ 완료 |
| **Phase 3** | 2일 | 추가 서비스 + 조회 기록 | ✅ 완료 |
| **Phase 4** | 1일 | Celery Tasks Audit | |
| **총계** | **9.5일** | | |

---

*작성: Self-Healing Team*
*최종 수정: 2026-01-05*
