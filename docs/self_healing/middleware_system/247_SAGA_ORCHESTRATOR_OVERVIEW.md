# 247. Saga Orchestrator 총괄 설계

> **Version**: 1.0.0
> **Created**: 2026-02-19
> **Status**: Approved
> **Parent**: [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
> **Related**: [244_COMPENSATE_SLOT_ADDITION.md](244_COMPENSATE_SLOT_ADDITION.md), [245_RECOVERY_DLQ_INTEGRATION.md](245_RECOVERY_DLQ_INTEGRATION.md), [246_STEP_TIMEOUT_MONITOR.md](246_STEP_TIMEOUT_MONITOR.md)
> **Priority**: P1 — Self-Healing "코드 버그 외 전부 자동 복구" 비전의 핵심 빈 구간

## 0. 요약

현재 Self-Healing 시스템은 **인프라 복구**(RecoveryCoordinator)와 **단일 오퍼레이션 재처리**(DLQ → ReplayHandler)만 커버한다.
**다중 서비스에 걸친 비즈니스 트랜잭션이 부분 실패했을 때의 자동 복구**는 빈 구간이며,
이를 메우기 위해 **도메인-프리 Saga Orchestrator**를 설계한다.

기존 인프라(DistributedRecoveryLock, IdempotencyService, DLQ, EventBus, AtomicTransition, RecoveryCircuitBreaker)를 **최대한 재사용**한다.

---

## 1. 비전과 빈 구간 — 코드 근거

### 1.1 현재 복구 스펙트럼

| 복구 영역 | 담당 컴포넌트 | 상태 |
|-----------|-------------|------|
| 인프라 Emergency 복구 | `RecoveryCoordinator` (BUDGET_RESET → HEALTH_CHECK → CANARY_RESUME → GOVERNANCE_NORMAL) | ✅ 있음 |
| 단일 실패 오퍼레이션 재처리 | `DLQService` → `ReplayHandler.replay(FailedOperationData)` | ✅ 있음 |
| **다중 서비스 트랜잭션 복구** | — | **❌ 빈 구간** |
| 코드 레벨 버그 수정 | — | ❌ 범위 밖 (별도 문서 250번 참조) |

### 1.2 현재 DLQ Replay의 한계

**파일**: `services/replay_service/handlers.py` L22-65

```python
class ReplayHandler(ABC):
    @property
    @abstractmethod
    def domain(self) -> str: ...

    @abstractmethod
    def replay(self, failed_op: FailedOperationData) -> ReplayResult: ...

    @abstractmethod
    def can_replay(self, failed_op: FailedOperationData) -> tuple[bool, str]: ...
```

**문제**: `replay()`는 **단일 FailedOperationData**를 받아 **하나의 오퍼레이션**만 다시 실행한다.

예시: "주문 생성 → 결제 → 포인트 차감 → 재고 감소" 트랜잭션에서 결제까지 성공하고 포인트에서 실패하면:
- 포인트 DLQ 엔트리 1개만 생성됨
- 결제를 되돌리는 compensate 경로 없음
- 포인트/재고 각각이 하나의 트랜잭션이라는 연결 정보 없음
- **부분 성공 상태 방치**

### 1.3 RecoveryCoordinator도 인프라 전용

**파일**: `services/coordination/recovery_state.py` L23-65

```python
class RecoveryStepType(str, Enum):
    BUDGET_RESET = "budget_reset"
    HEALTH_CHECK = "health_check"
    CANARY_RESUME = "canary_resume"
    GOVERNANCE_NORMAL = "governance_normal"
```

4개 Step 모두 **인프라 레벨**. 비즈니스 도메인 트랜잭션(결제, 재고 등)과 무관.

---

## 2. 아키텍처 원칙

### 2.1 도메인-프리 원칙 유지

현재 시스템이 이미 확립한 패턴을 따른다:

```
selfhealing 코어 패키지 (도메인-프리)     어댑터 레이어 (사용자 구현)
─────────────────────────────────      ──────────────────────────
ReplayHandler(ABC)                  →  OrderReplayHandler(ReplayHandler)
  .domain: str                           .domain = "order"
  .replay()                              .replay() — PG API 호출
  .can_replay()                          .can_replay() — PG 상태 확인

register_replay_handler(handler)    →  register_replay_handler(OrderReplayHandler())
```

**Saga도 동일한 구조**:

```
selfhealing 코어 패키지 (도메인-프리)     어댑터 레이어 (사용자 구현)
─────────────────────────────────      ──────────────────────────
SagaStep(ABC)                       →  PaymentStep(SagaStep)
  .execute(ctx) -> StepResult              .execute() — PG 결제
  .compensate(ctx) -> StepResult           .compensate() — PG 환불

SagaDefinition(name, steps)         →  saga_registry.register("order_creation",
SagaOrchestrator.execute_saga()           [PaymentStep(), PointStep(), InventoryStep()])
```

### 2.2 기존 인프라 최대 재사용

| 기존 인프라 | Saga에서의 역할 | 근거 코드 |
|------------|----------------|----------|
| `DistributedRecoveryLock.acquire(namespace, session_id)` | Saga 인스턴스 동시성 제어 | `distributed_recovery_lock.py` L172-207 |
| `IdempotencyService.check(key)` | 각 Saga step 멱등성 보장 | `idempotency/service.py` L150-208 |
| `DLQService.store_failure(domain, failure_type, ...)` | compensate 실패 시 3단계 fallback 저장 | `dlq/store_operations.py` L33-82 |
| `EventBus.emit(event_type, data)` | Saga 진행/실패 이벤트 발행 | `event_bus/bus.py` L412+ |
| `AtomicTransition` Lua 스크립트 패턴 | Saga 상태 원자적 전환 | `atomic_transition.py` L171-303 |
| `RecoveryCircuitBreaker.check_and_trip()` | 외부 서비스 불안정 시 Saga 일시 중지 | `recovery_circuit_breaker.py` L260-325 |
| `BlastRadiusService.assess_impact()` | Saga 실패 시 영향 범위 분석 | `blast_radius/service.py` L169-228 |

### 2.3 RecoveryCoordinator와의 관계

| 기준 | RecoveryCoordinator | SagaOrchestrator |
|------|---------------------|------------------|
| **복구 대상** | 인프라 Emergency 레벨 | 비즈니스 트랜잭션 |
| **Step 유형** | `RecoveryStepType` (BUDGET_RESET 등) | `SagaStep`(ABC) — 사용자 정의 |
| **실패 처리** | `_fail_session()` — 세션 실패 기록만 | **역순 compensate** 실행 |
| **병렬 존재** | 둘 다 독립 동작. Saga는 Emergency 상황이 아닌 **비즈니스 실패**에 반응 |

---

## 3. 세부 문서 구성

| 문서 번호 | 제목 | 내용 |
|----------|------|------|
| **248** | Saga 코어 모델 설계 | `SagaStep(ABC)`, `SagaDefinition`, `SagaInstance`, `SagaStatus` 상태 머신 |
| **249** | Saga Orchestrator 엔진 | `SagaOrchestrator` 클래스, 순방향 실행 + 역순 compensate + DLQ fallback |

---

## 4. 상태 머신

```
                    execute_saga()
                         │
                         ▼
    ┌──────────┐    ┌──────────┐    ┌───────────┐
    │ PENDING  │───▶│ RUNNING  │───▶│ COMPLETED │
    └──────────┘    └────┬─────┘    └───────────┘
                         │
                    step 실패
                         │
                         ▼
                  ┌──────────────┐    모든 compensate 성공
                  │ COMPENSATING │──────────────────────▶ COMPENSATED
                  └──────┬───────┘
                         │
                   compensate 실패
                         │
                         ▼
                ┌────────────────────┐
                │ COMPENSATION_FAILED │──▶ DLQ 저장
                └────────────────────┘
```

각 전환은 `AtomicTransition`의 Lua 스크립트 패턴을 활용하여 **원자적**으로 수행.

---

## 5. 실행 흐름 (Happy Path + Failure Path)

### 5.1 Happy Path

```
SagaOrchestrator.execute_saga("order_creation", {order_id: 123})
│
├── Step 1: PaymentStep.execute()     → SUCCESS, ctx.payment_id = "pay_001"
├── Step 2: PointStep.execute()       → SUCCESS, ctx.point_tx_id = "pt_001"
├── Step 3: InventoryStep.execute()   → SUCCESS, ctx.inv_tx_id = "inv_001"
│
└── SagaInstance.status = COMPLETED
```

### 5.2 Failure Path (Step 3 실패)

```
SagaOrchestrator.execute_saga("order_creation", {order_id: 123})
│
├── Step 1: PaymentStep.execute()     → SUCCESS
├── Step 2: PointStep.execute()       → SUCCESS
├── Step 3: InventoryStep.execute()   → FAILED (재고 부족)
│
│   SagaInstance.status = COMPENSATING
│
├── Step 2: PointStep.compensate()    → SUCCESS (포인트 원복)
├── Step 1: PaymentStep.compensate()  → SUCCESS (결제 취소)
│
└── SagaInstance.status = COMPENSATED
```

### 5.3 Compensation Failure Path

```
SagaOrchestrator.execute_saga("order_creation", {order_id: 123})
│
├── Step 1: PaymentStep.execute()     → SUCCESS
├── Step 2: PointStep.execute()       → SUCCESS
├── Step 3: InventoryStep.execute()   → FAILED
│
│   SagaInstance.status = COMPENSATING
│
├── Step 2: PointStep.compensate()    → SUCCESS
├── Step 1: PaymentStep.compensate()  → FAILED (PG 장애)
│
│   SagaInstance.status = COMPENSATION_FAILED
│
├── DLQService.store_failure(
│       domain="saga",
│       failure_type="COMPENSATION_FAILED",
│       entity_type="saga_instance",
│       entity_id=saga_instance.id,
│       snapshot_data={completed_compensations: [...], failed_compensations: [...]}
│   )
│
└── EventBus.emit(SAGA_COMPENSATION_FAILED, {...})
```

---

## 6. 기존 코드와의 통합 포인트

### 6.1 IdempotencyService 통합

각 Saga step은 `IdempotencyKey.for_operation()`으로 멱등성 키를 생성:

```python
# 현재 코드: idempotency/models.py L106-132
key = IdempotencyKey.for_operation(
    entity_type="saga_step",
    entity_id=saga_instance_id,
    operation=f"{step.name}:execute",       # 또는 "compensate"
    domain=IdempotencyDomain.INTERNAL_PROCESS,
)
result = idempotency_service.check(key)
if result.is_duplicate:
    return result.existing_record  # 이미 실행됨, skip
```

### 6.2 DistributedRecoveryLock 통합

Saga 인스턴스별 동시성 제어:

```python
# 현재 코드: distributed_recovery_lock.py L172-207
lock = DistributedRecoveryLock()
acquired = lock.acquire(
    namespace=f"saga:{saga_name}:{saga_instance_id}",
    session_id=worker_id,
)
```

### 6.3 RecoveryCircuitBreaker 통합

외부 서비스별 서킷브레이커 연동:

```python
# 현재 코드: recovery_circuit_breaker.py L231-260
state = circuit_breaker.get_state(namespace=f"saga:{step.domain}")
if state == RecoveryCircuitState.OPEN:
    # 이 step의 외부 서비스가 불안정 → Saga 일시 중지
    saga_instance.status = SagaStatus.SUSPENDED
```

### 6.4 EventBus 통합

`EventType` enum 확장:

```python
# 현재 코드: event_bus/bus.py L57-160
class EventType(str, Enum):
    # ... 기존 이벤트 타입들 ...
    # Saga 이벤트 추가
    SAGA_STARTED = "saga_started"
    SAGA_STEP_COMPLETED = "saga_step_completed"
    SAGA_STEP_FAILED = "saga_step_failed"
    SAGA_COMPLETED = "saga_completed"
    SAGA_COMPENSATING = "saga_compensating"
    SAGA_COMPENSATED = "saga_compensated"
    SAGA_COMPENSATION_FAILED = "saga_compensation_failed"
```

---

## 7. 비-목표 (Non-Goals)

| 항목 | 이유 |
|------|------|
| Choreography 패턴 | 이벤트 발행만으로는 compensate 순서 보장 불가. Orchestrator 패턴 채택 |
| 자체 메시지 브로커 | EventBus가 이미 존재. 외부 Kafka 연동은 [175번 문서](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md) 별도 |
| 코드 레벨 버그 자동 수정 | Self-Healing의 범위 밖. [250번 문서](250_CODE_BUG_AUTO_FIX_ANALYSIS.md)에서 별도 분석 |
| 2PC (Two-Phase Commit) | 분산 환경에서 가용성 감소. Saga의 최종 일관성(eventual consistency) 방식 채택 |

---

## 8. 구현 로드맵

```
Phase 1: 선행 조건 (244-246번 문서)
  ├── Compensate Slot 추가 (244)         ← 이미 설계 완료
  ├── Recovery → DLQ 통합 (245)          ← 이미 설계 완료
  └── Step Timeout Monitor (246)         ← 이미 설계 완료

Phase 2: Saga 코어 (248-249번 문서)
  ├── SagaStep ABC + SagaDefinition + SagaInstance 모델 (248)
  └── SagaOrchestrator 엔진 (249)

Phase 3: 기존 인프라 연결
  ├── IdempotencyService 통합
  ├── DLQ Compensation Failure 저장
  ├── EventBus Saga 이벤트 확장
  └── RecoveryCircuitBreaker Saga 연동
```

---

## 9. 참고: 현재 시스템에서 Saga 없이 부분 실패를 처리하는 경로

현재는 **수동 개입**이 필요하다:

1. 결제 성공, 포인트 실패 → 포인트 DLQ에 1건 저장
2. 운영자가 DLQ 확인 → `ReplayService.replay_single(dlq_id)` 수동 실행
3. 재처리 실패 시 → `FailedOperationStatus.REQUIRES_REVIEW`로 전환
4. 결제 환불은? → **DLQ에 기록되지 않음**. 운영자가 직접 PG 관리자에게 요청

Saga가 있으면:
1. 결제 성공, 포인트 실패 → **자동으로 PaymentStep.compensate() 실행** (결제 환불)
2. compensate 실패 시 → DLQ에 saga 실패 정보 저장 (snapshot_data 포함)
3. 운영자는 DLQ에서 **전체 트랜잭션 컨텍스트**를 확인 가능
