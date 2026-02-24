# 272. Automated Runbook Executor — 아키텍처 개요

## 1. 목적

현재 시스템은 장애 감지→분석→기록까지 자동화되어 있으나, **"임의의 장애 패턴 → 임의의 복구 절차"를 선언적으로 매칭하고 자동 실행하는 통합 오케스트레이션 계층**이 부재하다.

기존 복구 메커니즘은 각각 특정 도메인에 하드코딩되어 있다:

| 기존 컴포넌트 | 커버 영역 | 한계 |
|---|---|---|
| `RecoveryCoordinator` | Emergency LEVEL_1/2/3 → 고정 4-step 복구 | `RecoveryStepType` enum 4개(`BUDGET_RESET`, `HEALTH_CHECK`, `CANARY_RESUME`, `GOVERNANCE_NORMAL`)에 고정 |
| `ProtectionOrchestrator` | `ViolationType` → `ActionPolicy` 보안 대응 | 보안 도메인 전용. 인프라/성능 장애 처리 불가 |
| `AutoRollbackGuard` | error_rate/latency 기반 설정값 롤백 | 5개 파라미터만 커버, MINOR(5~10%)는 알림만 |
| `DecisionEngine` | 메트릭 → `AdjustmentDecision` 제안 | 제안만 생성, 실행 경로 없음 |
| `LearningService` | 패턴 학습 → `Suggestion` 생성 | `action="enable_circuit_breaker"` 문자열만 저장, 디스패처 없음 |

**Runbook Executor**는 이 빈 공간을 채운다 — 기존 컴포넌트를 **호출하는 파사드/오케스트레이터** 역할.

## 2. 핵심 설계 원칙

### 2.1 파사드 패턴 — 기존 컴포넌트를 대체하지 않고 호출

```
런북 Executor
  ├→ step: "emergency_recovery" → RecoveryCoordinator.start_recovery() 호출
  ├→ step: "config_rollback"   → AutoRollbackGuard.trigger_manual_emergency() 호출
  ├→ step: "custom_action"     → ActionExecutor.execute(Action(...)) 호출
  └→ step: "notify"            → UnifiedNotificationManager.notify(...) 호출
```

### 2.2 선언적 정의 — 런북은 데이터

```python
Runbook(
    id="db_pool_recovery",
    trigger=PatternCondition(error_rate__gt=0.05, db_pool_usage__gt=0.9),
    risk_level=RiskLevel.MEDIUM,
    steps=[
        RunbookStep(action="db.kill_idle", params={"threshold": 300}),
        RunbookStep(action="assert.metric", params={"db_pool_usage__lt": 0.8}),
    ],
)
```

### 2.3 기존 인프라 재사용

| 필요 기능 | 재사용 대상 | 코드 위치 |
|---|---|---|
| Step 인터페이스 | `SagaStep` ABC | `services/saga/step.py` — `execute(ctx)` + `compensate(ctx)` |
| 실행 프리미티브 | `ActionExecutor` | `core/action_executor.py` — `ACTIVE/SHADOW/EVALUATION` 모드 |
| 멱등성 | `IdempotentStepHandler` | `services/coordination/idempotent_step_handlers.py` |
| 승인 게이팅 | `GovernanceCheckMixin` | `services/governance/checks.py` — `check_all_governance()` |
| 보상(Compensate) | `SessionPersistenceMixin._fail_session()` | `coordination/recovery_coordinator/_session_persistence.py` |
| 감사 추적 | `CascadeEventAuditor.record()` | `audit/cascade_auditor/_recording.py` |
| 알림 | `UnifiedNotificationManager.notify()` | `services/unified_notification/service.py` |
| 중복 실행 방지 | `IdempotencyDomain.RECOVERY_ACTION` | `services/idempotency/models.py` |
| 이벤트 구독 | `SelfHealingEventBus.subscribe()` | `services/event_bus/bus/__init__.py` |
| 패턴 학습 | `LearningService.get_patterns()` | `services/learning/service.py` |
| 동시 복구 방지 | `DistributedRecoveryLock` | `services/coordination/distributed_recovery_lock.py` |

## 3. 모듈 구조

```
services/
  runbook/
    __init__.py
    pattern_matcher.py    # 현재 증상 → 과거 패턴 매칭 (273번 문서)
    runbook_registry.py   # Step-by-step 조치 등록 (274번 문서)
    executor.py           # 단계별 실행, 중간 검증 (275번 문서)
    approval_gate.py      # 위험도별 자동/수동 승인 (276번 문서)
    playback_recorder.py  # 실행 과정 재생 가능 기록 (277번 문서)
    service.py            # 통합 서비스 (278번 문서)
```

## 4. 전체 실행 흐름

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. 이벤트 감지                                                    │
│    EventBus → PatternMatcher가 구독                               │
│    - CIRCUIT_BREAKER_OPENED, EMERGENCY_ACTIVATED                 │
│    - ERROR_BUDGET_CRITICAL, THROTTLE_SLA_CRITICAL                │
│    * 현재 메트릭 스냅샷 수집                                       │
└────────────┬─────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 2. 패턴 매칭 (pattern_matcher.py)                                 │
│    - 현재 증상(메트릭 조합)을 RunbookRegistry의 trigger 조건과 대조 │
│    - LearningService.get_patterns()으로 과거 유사 패턴 조회        │
│    - PostmortemStore에서 과거 사례 대조                            │
│    - 매칭 결과: 런북 ID + confidence                              │
└────────────┬─────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 3. 승인 게이트 (approval_gate.py)                                 │
│    - GovernanceCheckMixin.check_governance() 통과 확인            │
│    - risk_level에 따라 분기:                                      │
│      LOW    → 즉시 실행                                          │
│      MEDIUM → UnifiedNotificationManager로 알림, 타이머 대기      │
│      HIGH   → 수동 승인 대기 (READY_TO_EXECUTE 상태)             │
└────────────┬─────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 4. 실행 (executor.py)                                            │
│    - DistributedRecoveryLock 획득 (동시 복구 방지)                │
│    - SagaStep 기반 순차 실행                                      │
│    - 각 step: IdempotentStepHandler로 멱등성 보장                 │
│    - 실패 시: compensate() 역순 실행 → DLQ 저장                  │
│    - ActionExecutor.execute(Action(...)) — ExecutionMode 존중     │
└────────────┬─────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────┐
│ 5. 기록 (playback_recorder.py)                                   │
│    - CascadeEventAuditor.record() — 전 과정 인과관계 추적         │
│    - LearningService.learn_pattern() — 성공/실패 패턴 피드백      │
│    - PostmortemStore — 자동 해결 사례 기록                         │
│    - EventBus.emit(RUNBOOK_COMPLETED/FAILED)                     │
└──────────────────────────────────────────────────────────────────┘
```

## 5. 기존 컴포넌트와의 동시 실행 방지

`RecoveryCoordinator`, `AutoRollbackGuard`, `Runbook Executor` 세 컴포넌트가 같은 리소스에 동시 복구를 시도하면 충돌한다.

**해결:** `DistributedRecoveryLock`을 공유 키로 사용.

```python
# RecoveryCoordinator (기존)
if not self._recovery_lock.acquire(namespace, session_id):
    raise ValueError("Failed to acquire recovery lock")

# Runbook Executor (신규) — 동일한 lock 사용
if not self._recovery_lock.acquire(namespace, runbook_session_id):
    # 이미 다른 복구가 진행 중 → 대기 또는 스킵
    ...
```

`AutoRollbackGuard`에는 현재 락 메커니즘이 없으므로, 런북 구현 시 `AutoRollbackGuard._execute_rollback()`에 락 획득 시도를 추가하는 것을 검토한다.

## 6. ExecutionMode 정책

런북 executor는 기존 `ExecutionMode` 체계를 존중한다:

- `ACTIVE` → 런북 step 실제 실행
- `SHADOW` → 런북 step 로그만 남김 (실행 안 함)
- `EVALUATION` → 런북 step 검증 + 로그 (실행 안 함)

환경변수 `SELFHEALING_EXECUTION_MODE`로 제어. 런북만을 위한 별도 모드는 만들지 않는다.

## 7. 설정 통합

```python
# settings/runbook.py
class RunbookSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SELFHEALING_RUNBOOK_")

    enabled: bool = True
    approval_timeout_seconds: int = 300       # MEDIUM risk 자동 승인 대기 시간
    max_concurrent_runbooks: int = 3          # 동시 실행 런북 수 제한
    step_default_timeout_seconds: int = 120   # step 기본 타임아웃
    lock_ttl_seconds: int = 600               # 분산 락 TTL
```

## 8. EventBus 연동 — 신규 EventType

```python
# 기존 EventType enum에 추가
class EventType(str, Enum):
    # ... 기존 이벤트들 ...
    RUNBOOK_TRIGGERED = "runbook_triggered"
    RUNBOOK_STEP_COMPLETED = "runbook_step_completed"
    RUNBOOK_STEP_FAILED = "runbook_step_failed"
    RUNBOOK_COMPLETED = "runbook_completed"
    RUNBOOK_FAILED = "runbook_failed"
    RUNBOOK_APPROVAL_REQUIRED = "runbook_approval_required"
    RUNBOOK_APPROVAL_GRANTED = "runbook_approval_granted"
    RUNBOOK_APPROVAL_REJECTED = "runbook_approval_rejected"
```

## 9. 문서 구성

| 문서 번호 | 제목 | 내용 |
|---|---|---|
| **272** | 아키텍처 개요 (본 문서) | 전체 구조, 설계 원칙, 기존 연동 |
| **273** | Pattern Matcher 설계 | 증상→패턴 매칭, EventBus 구독, 조건 평가 |
| **274** | Runbook Registry 설계 | 런북 정의 모델, 등록/조회, 트리거 조건 |
| **275** | Executor 설계 | SagaStep 기반 실행, 멱등성, 보상, 타임아웃 |
| **276** | Approval Gate 설계 | 위험도 분류, GovernanceCheck 연동, 타이머 |
| **277** | Playback Recorder 설계 | 감사 기록, 학습 피드백, PostmortemStore 연동 |
| **278** | Service 통합 설계 | 전체 파이프라인 오케스트레이션, 진입점 |

## 10. 참조

- `RecoveryCoordinator`: 77번 문서 (`077_RECOVERY_COORDINATOR.md`)
- `SagaStep` ABC: 247-249번 문서
- `CorrelationEngine`: 250-258번 문서
- `CascadeEventAuditor`: 76번 문서 (`076_CASCADE_EVENT_AUDIT.md`)
- `GovernanceCheck`: 26번 문서
- `ActionExecutor`: `core/action_executor.py`
- `AutoRollbackGuard`: `core/auto_rollback_guard.py`
- `LearningService`: `services/learning/service.py`
- `PostmortemStore`: `services/postmortem/store.py`
- `UnifiedNotificationManager`: `services/unified_notification/service.py`
