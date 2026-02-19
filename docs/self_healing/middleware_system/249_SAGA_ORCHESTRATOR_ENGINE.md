# 249. Saga Orchestrator 엔진

> **Version**: 1.0.0
> **Created**: 2026-02-19
> **Status**: Approved
> **Parent**: [247_SAGA_ORCHESTRATOR_OVERVIEW.md](247_SAGA_ORCHESTRATOR_OVERVIEW.md)
> **Depends**: [248_SAGA_CORE_MODELS.md](248_SAGA_CORE_MODELS.md)
> **Priority**: P1 — 다중 서비스 트랜잭션 자동 복구의 핵심 엔진

## 0. 요약

`SagaOrchestrator` 클래스를 설계한다.
**순방향 실행 → 실패 시 역순 compensate → compensate 실패 시 DLQ 저장**의 전체 흐름을 오케스트레이션한다.
기존 인프라 7종(DistributedRecoveryLock, IdempotencyService, DLQService, EventBus, AtomicTransition 패턴, RecoveryCircuitBreaker, BlastRadiusService)을 재사용한다.

---

## 1. 설계 근거 — RecoveryCoordinator.execute_next_step() 분석

**파일**: `services/coordination/recovery_coordinator.py` L470-557

```python
# 현재 RecoveryCoordinator의 Step 실행 패턴:
#
# 1. 세션 조회
session = self.get_active_session(namespace)
step = session.get_current_step()

# 2. 핸들러 실행
if idempotent_registry and idempotent_registry.has_handler(step.step_type):
    result = idempotent_registry.execute(session, step)
else:
    handler = self._step_handlers.get(step.step_type)
    result = handler(session, step)

# 3. 성공: current_step_index += 1
# 4. 실패: _fail_session(session, error)  ← compensate 없이 세션 실패
```

**SagaOrchestrator가 다른 점**:
- Step 3 → 동일 (다음 Step으로 진행)
- Step 4 → `_fail_session()` 대신 **`_start_compensation()`** 실행

---

## 2. SagaOrchestrator 클래스 설계

```python
class SagaOrchestrator:
    """분산 Saga 오케스트레이터.

    기존 RecoveryCoordinator와 동일한 의존성 주입 패턴.
    모든 외부 의존성은 생성자에서 주입받거나 ProviderRegistry로 지연 로드.

    Usage:
        orchestrator = SagaOrchestrator()
        result = orchestrator.execute_saga("order_creation", {
            "order_id": 123,
            "user_id": 456,
            "amount": 50000,
        })

        if result.status == SagaStatus.COMPLETED:
            print("주문 생성 완료")
        elif result.status == SagaStatus.COMPENSATED:
            print("주문 생성 실패, 모든 보상 완료")
        elif result.status == SagaStatus.COMPENSATION_FAILED:
            print("보상 실패, DLQ 확인 필요")
    """

    def __init__(
        self,
        lock: DistributedRecoveryLock | None = None,
        idempotency: IdempotencyService | None = None,
        dlq: DLQService | None = None,
        event_bus: EventBus | None = None,
        circuit_breaker: RecoveryCircuitBreaker | None = None,
        blast_radius: BlastRadiusService | None = None,
    ):
        """의존성 주입.

        None이면 ProviderRegistry 또는 기본 구현 사용.
        RecoveryCoordinator.__init__()과 동일한 패턴.
        """
        self._lock = lock
        self._idempotency = idempotency
        self._dlq = dlq
        self._event_bus = event_bus
        self._circuit_breaker = circuit_breaker
        self._blast_radius = blast_radius
```

---

## 3. 핵심 메서드

### 3.1 execute_saga() — 진입점

```python
def execute_saga(
    self,
    saga_name: str,
    initial_data: dict[str, Any],
    initiated_by: str = "system",
    correlation_id: str | None = None,
) -> SagaInstance:
    """Saga 실행 진입점.

    1. SagaDefinition 조회 (레지스트리에서)
    2. SagaInstance 생성 (PENDING)
    3. 분산 락 획득 (DistributedRecoveryLock 재사용)
    4. Forward 실행 루프 (_execute_forward)
    5. 실패 시 Compensate 루프 (_execute_compensation)
    6. 분산 락 해제
    7. SagaInstance 반환

    Args:
        saga_name: 등록된 SagaDefinition 이름
        initial_data: 초기 데이터 (Step들에게 전달)
        initiated_by: 시작 주체
        correlation_id: EventBus 추적 ID

    Returns:
        SagaInstance — 최종 상태 포함

    Raises:
        ValueError: saga_name이 등록되지 않은 경우
    """
```

**실행 흐름**:

```python
# 의사 코드 (pseudo-code)

def execute_saga(self, saga_name, initial_data, ...):
    # 1. 정의 조회
    definition = get_saga_definition(saga_name)
    if not definition:
        raise ValueError(f"Saga '{saga_name}' not registered")

    # 2. 인스턴스 생성
    instance = self._create_instance(definition, initial_data, ...)

    # 3. 분산 락 획득
    #    기존 코드: distributed_recovery_lock.py L172-207
    lock_namespace = f"saga:{saga_name}:{instance.id}"
    acquired = self._lock.acquire(
        namespace=lock_namespace,
        session_id=instance.id,
    )
    if not acquired:
        instance.status = SagaStatus.COMPENSATION_FAILED
        instance.error_message = "Failed to acquire distributed lock"
        return instance

    try:
        # 4. EventBus 알림
        #    기존 코드: event_bus/bus.py L412+
        self._event_bus.emit(
            EventType.SAGA_STARTED,
            data={"saga_name": saga_name, "instance_id": instance.id},
            source="SagaOrchestrator",
            correlation_id=correlation_id,
        )

        # 5. Forward 실행
        instance = self._execute_forward(instance, definition)

        # 6. 실패 시 Compensation
        if instance.status == SagaStatus.COMPENSATING:
            instance = self._execute_compensation(instance, definition)

    finally:
        # 7. 락 해제
        self._lock.release(
            namespace=lock_namespace,
            session_id=instance.id,
        )

    # 8. 최종 이벤트 발행
    self._emit_final_event(instance)

    return instance
```

### 3.2 _execute_forward() — 순방향 실행 루프

```python
def _execute_forward(
    self,
    instance: SagaInstance,
    definition: SagaDefinition,
) -> SagaInstance:
    """Forward step들을 순차 실행.

    RecoveryCoordinator.execute_next_step()의 패턴을 따르되:
    - 성공: current_step_index 증가, StepResult.data를 context에 merge
    - 실패: _fail_session() 대신 status → COMPENSATING으로 전환

    각 Step 실행 전:
    1. 멱등성 확인 (IdempotencyService.check)
    2. 서킷브레이커 상태 확인 (RecoveryCircuitBreaker.get_state)
    3. 타임아웃 감시 (246번 문서 연동)
    """
```

**실행 흐름**:

```python
def _execute_forward(self, instance, definition):
    instance.status = SagaStatus.RUNNING
    instance.started_at = now().isoformat()

    for i, step_def in enumerate(definition.steps):
        step_instance = instance.step_instances[i]

        # 1. 서킷브레이커 확인
        #    기존 코드: recovery_circuit_breaker.py L231-260
        cb_state = self._circuit_breaker.get_state(
            namespace=f"saga:{step_def.name}"
        )
        if cb_state == RecoveryCircuitState.OPEN:
            instance.status = SagaStatus.SUSPENDED
            instance.error_message = f"Circuit breaker OPEN for step '{step_def.name}'"
            return instance

        # 2. 멱등성 확인
        #    기존 코드: idempotency/service.py L150-208
        idemp_key = IdempotencyKey.for_operation(
            entity_type="saga_step",
            entity_id=instance.id,
            operation=f"{step_def.name}:execute",
            domain=IdempotencyDomain.INTERNAL_PROCESS,
        )
        idemp_result = self._idempotency.check(idemp_key)
        if idemp_result.is_duplicate:
            # 이미 실행된 Step — skip
            step_instance.status = SagaStepStatus.EXECUTED
            instance.current_step_index = i + 1
            continue

        # 3. can_execute 확인
        can_exec, reason = step_def.can_execute(instance.context)
        if not can_exec:
            step_instance.status = SagaStepStatus.EXECUTE_FAILED
            step_instance.error_message = reason
            instance.status = SagaStatus.COMPENSATING
            instance.compensate_step_index = i - 1
            return instance

        # 4. execute 실행 (타임아웃 적용)
        step_instance.status = SagaStepStatus.EXECUTING
        step_instance.execute_started_at = now().isoformat()

        timeout = step_def.timeout_seconds or definition.timeout_seconds
        result = self._execute_with_timeout(step_def.execute, instance.context, timeout)

        if result.success:
            step_instance.status = SagaStepStatus.EXECUTED
            step_instance.execute_completed_at = now().isoformat()
            instance.context.merge_step_result(step_def.name, result.data)
            instance.current_step_index = i + 1

            # EventBus 알림
            self._event_bus.emit(
                EventType.SAGA_STEP_COMPLETED,
                data={"step": step_def.name, "instance_id": instance.id},
                source="SagaOrchestrator",
            )
        else:
            step_instance.status = SagaStepStatus.EXECUTE_FAILED
            step_instance.error_message = result.error
            step_instance.error_code = result.error_code
            step_instance.execute_completed_at = now().isoformat()

            # 재시도 가능?
            if result.retryable and step_instance.retry_count < definition.max_retries_per_step:
                step_instance.retry_count += 1
                # 같은 Step 다시 실행 (i 유지)
                continue

            # Compensation 시작
            instance.status = SagaStatus.COMPENSATING
            instance.compensate_step_index = i - 1  # 직전 성공 Step부터 역순

            self._event_bus.emit(
                EventType.SAGA_STEP_FAILED,
                data={
                    "step": step_def.name,
                    "error": result.error,
                    "instance_id": instance.id,
                },
                source="SagaOrchestrator",
            )
            return instance

    # 모든 Step 성공
    instance.status = SagaStatus.COMPLETED
    instance.completed_at = now().isoformat()
    return instance
```

### 3.3 _execute_compensation() — 역순 보상 루프

```python
def _execute_compensation(
    self,
    instance: SagaInstance,
    definition: SagaDefinition,
) -> SagaInstance:
    """성공한 Step들을 역순으로 compensate.

    RecoveryCoordinator._fail_session()이 하지 않는 것:
    - 현재 코드 (recovery_coordinator.py L1329-1355):
      session.status = FAILED, abort_reason = error, _save_session()
      → 보상 없이 실패 기록만

    SagaOrchestrator는:
    - 마지막 성공 Step부터 첫 Step까지 역순으로 compensate() 호출
    - compensate 실패 시 DLQ에 저장 (3단계 fallback)
    """
```

**실행 흐름**:

```python
def _execute_compensation(self, instance, definition):
    compensation_failures = []

    # 역순 순회: 마지막 성공 Step → 첫 Step
    for i in range(instance.compensate_step_index, -1, -1):
        step_def = definition.steps[i]
        step_instance = instance.step_instances[i]

        # EXECUTED 상태인 Step만 compensate
        if step_instance.status != SagaStepStatus.EXECUTED:
            continue

        # 멱등성 확인
        idemp_key = IdempotencyKey.for_operation(
            entity_type="saga_step",
            entity_id=instance.id,
            operation=f"{step_def.name}:compensate",
            domain=IdempotencyDomain.INTERNAL_PROCESS,
        )
        idemp_result = self._idempotency.check(idemp_key)
        if idemp_result.is_duplicate:
            step_instance.status = SagaStepStatus.COMPENSATED
            continue

        # compensate 실행
        step_instance.status = SagaStepStatus.COMPENSATING
        step_instance.compensate_started_at = now().isoformat()

        try:
            timeout = step_def.timeout_seconds or definition.timeout_seconds
            result = self._execute_with_timeout(
                step_def.compensate, instance.context, timeout
            )

            if result.success:
                step_instance.status = SagaStepStatus.COMPENSATED
                step_instance.compensate_completed_at = now().isoformat()

                self._event_bus.emit(
                    EventType.SAGA_COMPENSATING,
                    data={
                        "step": step_def.name,
                        "instance_id": instance.id,
                        "direction": "compensated",
                    },
                    source="SagaOrchestrator",
                )
            else:
                step_instance.status = SagaStepStatus.COMPENSATE_FAILED
                step_instance.error_message = result.error
                step_instance.compensate_completed_at = now().isoformat()
                compensation_failures.append(step_def.name)

        except Exception as e:
            step_instance.status = SagaStepStatus.COMPENSATE_FAILED
            step_instance.error_message = str(e)
            step_instance.compensate_completed_at = now().isoformat()
            compensation_failures.append(step_def.name)

    if not compensation_failures:
        # 모든 compensate 성공
        instance.status = SagaStatus.COMPENSATED
        instance.completed_at = now().isoformat()
    else:
        # compensate 실패 → DLQ 저장
        instance.status = SagaStatus.COMPENSATION_FAILED
        instance.completed_at = now().isoformat()
        instance.error_message = (
            f"Compensation failed for steps: {compensation_failures}"
        )

        self._store_compensation_failure_to_dlq(instance, compensation_failures)

    return instance
```

### 3.4 _store_compensation_failure_to_dlq() — DLQ 통합

```python
def _store_compensation_failure_to_dlq(
    self,
    instance: SagaInstance,
    failed_steps: list[str],
) -> None:
    """Compensation 실패를 DLQ에 저장.

    기존 코드: dlq/store_operations.py L33-82
    DLQService.store_failure()의 3단계 fallback (DB → LMDB → JSONL → stderr).

    snapshot_data에 전체 Saga 컨텍스트를 포함하여
    운영자가 DLQ에서 복구에 필요한 모든 정보에 접근 가능.
    """
    self._dlq.store_failure(
        domain="saga",
        failure_type="COMPENSATION_FAILED",
        entity_type="saga_instance",
        entity_id=instance.id,
        error_code="SAGA_COMPENSATION_FAILED",
        error_message=instance.error_message or "",
        snapshot_data={
            "saga_name": instance.saga_name,
            "instance": instance.to_dict(),
            "failed_compensation_steps": failed_steps,
            "executed_steps": [
                s.step_name for s in instance.step_instances
                if s.status == SagaStepStatus.EXECUTED
            ],
            "compensated_steps": [
                s.step_name for s in instance.step_instances
                if s.status == SagaStepStatus.COMPENSATED
            ],
        },
        metadata={
            "correlation_id": instance.correlation_id,
            "initiated_by": instance.initiated_by,
        },
        recommended_action=(
            "Manual compensation required for failed steps. "
            "Check snapshot_data for full saga context."
        ),
    )

    # EventBus 알림
    self._event_bus.emit(
        EventType.SAGA_COMPENSATION_FAILED,
        data={
            "saga_name": instance.saga_name,
            "instance_id": instance.id,
            "failed_steps": failed_steps,
        },
        source="SagaOrchestrator",
        priority=EventPriority.CRITICAL,
        correlation_id=instance.correlation_id,
    )

    # BlastRadius 평가
    #    기존 코드: blast_radius/service.py L169-228
    self._blast_radius.assess_impact(
        stage_name=f"saga:{instance.saga_name}",
        trigger_event="SAGA_COMPENSATION_FAILED",
        failing_services=failed_steps,
    )
```

### 3.5 _execute_with_timeout() — 타임아웃 래핑

```python
def _execute_with_timeout(
    self,
    fn: Callable[[SagaContext], StepResult],
    ctx: SagaContext,
    timeout_seconds: int,
) -> StepResult:
    """Step 함수를 타임아웃 내에서 실행.

    246번 문서(STEP_TIMEOUT_MONITOR)와 동일한 접근:
    - concurrent.futures.ThreadPoolExecutor + as_completed(timeout)
    - 타임아웃 초과 시 StepResult.failed() 반환
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, ctx)
        try:
            result = future.result(timeout=timeout_seconds)
            return result
        except TimeoutError:
            return StepResult.failed(
                error=f"Step execution timed out after {timeout_seconds}s",
                error_code="STEP_TIMEOUT",
                retryable=True,
            )
        except Exception as e:
            return StepResult.failed(
                error=f"Step execution error: {type(e).__name__}: {str(e)[:200]}",
                error_code="STEP_EXCEPTION",
                retryable=False,
            )
```

---

## 4. 상태 전환 원자성

### 4.1 AtomicTransition 패턴 재사용

**파일**: `services/coordination/atomic_transition.py` L171-303

```python
# 기존 코드: 3종의 Lua 스크립트
# - ATOMIC_TRANSITION_SCRIPT: expected_level → new_level (optimistic lock)
# - CONDITIONAL_TRANSITION_SCRIPT: required_level일 때만 전환
# - ESCALATE_ONLY_SCRIPT: 상승 전용

# Saga용 Lua 스크립트 추가:
SAGA_TRANSITION_SCRIPT = """
local key = KEYS[1]
local expected_status = ARGV[1]
local new_status = ARGV[2]
local updated_at = ARGV[3]

local current = redis.call("HGET", key, "status")
if current ~= expected_status then
    return {0, "status_mismatch", current or "nil"}
end

redis.call("HMSET", key,
    "status", new_status,
    "updated_at", updated_at
)
return {1, "ok", new_status}
"""
```

Saga 상태 전환마다 Lua 스크립트로 **원자적 CAS (Compare-And-Swap)** 수행:
- `RUNNING → COMPENSATING` (Step 실패 시)
- `COMPENSATING → COMPENSATED` (모든 compensate 성공)
- `COMPENSATING → COMPENSATION_FAILED` (compensate 실패)

---

## 5. 거버넌스 통합

### 5.1 Governance Checks

**기존 코드**: `services/replay_service/service.py` L132-148

```python
governance = check_all_governance(
    check_kill_switch=True,
    check_emergency=True,
    emergency_min_level=2,
    check_error_budget=True,
    operation_name="replay_single",
    service_name="ReplayService",
    domain="dlq",
    audit_on_block=True,
)
```

SagaOrchestrator도 동일한 거버넌스 체크 적용:

```python
# execute_saga() 진입 시
governance = check_all_governance(
    check_kill_switch=True,
    check_emergency=True,
    emergency_min_level=2,
    check_error_budget=True,
    operation_name=f"saga:{saga_name}",
    service_name="SagaOrchestrator",
    domain="saga",
    audit_on_block=True,
)
if not governance.allowed:
    instance.status = SagaStatus.COMPENSATION_FAILED
    instance.error_message = f"Governance blocked: {governance.block_message}"
    return instance
```

---

## 6. EventType 확장

**파일**: `services/event_bus/bus.py` L57-160

기존 EventType enum에 Saga 이벤트 추가:

```python
class EventType(str, Enum):
    # ... 기존 80+ 이벤트 타입 ...

    # Saga Orchestrator
    SAGA_STARTED = "saga_started"
    SAGA_STEP_COMPLETED = "saga_step_completed"
    SAGA_STEP_FAILED = "saga_step_failed"
    SAGA_COMPLETED = "saga_completed"
    SAGA_COMPENSATING = "saga_compensating"
    SAGA_COMPENSATED = "saga_compensated"
    SAGA_COMPENSATION_FAILED = "saga_compensation_failed"
    SAGA_SUSPENDED = "saga_suspended"
    SAGA_RESUMED = "saga_resumed"
    SAGA_TIMED_OUT = "saga_timed_out"
```

---

## 7. 에러 처리 매트릭스

| 실패 유형 | Orchestrator 행동 | DLQ 저장 | EventBus 이벤트 |
|----------|------------------|---------|----------------|
| Step execute 실패 (retryable) | 재시도 (max_retries_per_step까지) | ❌ | SAGA_STEP_FAILED |
| Step execute 실패 (non-retryable) | 즉시 COMPENSATING 전환 | ❌ | SAGA_STEP_FAILED |
| Step execute 타임아웃 | retryable=True로 재시도 | ❌ | SAGA_STEP_FAILED |
| CircuitBreaker OPEN | SUSPENDED 전환, 대기 | ❌ | SAGA_SUSPENDED |
| Step compensate 성공 | 다음 역순 Step으로 진행 | ❌ | SAGA_COMPENSATING |
| Step compensate 실패 | COMPENSATION_FAILED + DLQ | ✅ | SAGA_COMPENSATION_FAILED |
| 전체 Saga 타임아웃 | COMPENSATING 전환 후 역순 보상 | ❌* | SAGA_TIMED_OUT |
| 거버넌스 차단 | 즉시 반환, 실행하지 않음 | ❌ | (audit_on_block) |
| 분산 락 획득 실패 | 즉시 반환 | ❌ | ❌ |

\* compensate 실패 시에만 DLQ 저장

---

## 8. 관찰성 (Observability)

### 8.1 구조적 로깅

```python
# 각 Step 실행 시
logger.info(
    "[Saga] Step execute",
    extra={
        "saga_name": instance.saga_name,
        "instance_id": instance.id,
        "step_name": step_def.name,
        "step_index": i,
        "total_steps": len(definition.steps),
        "correlation_id": instance.correlation_id,
    },
)
```

### 8.2 Prometheus 메트릭 (OTEL 연동)

```python
# 기존 OTEL 인프라 재사용: 156-164번 문서
saga_executions_total = Counter("selfhealing_saga_executions_total", labels=["saga_name", "status"])
saga_step_duration_seconds = Histogram("selfhealing_saga_step_duration_seconds", labels=["saga_name", "step_name", "phase"])
saga_compensation_steps_total = Counter("selfhealing_saga_compensation_steps_total", labels=["saga_name", "step_name", "status"])
```

---

## 9. 테스트 전략

### 9.1 단위 테스트

```python
# Mock SagaStep으로 Orchestrator 로직 검증
class MockPaymentStep(SagaStep):
    @property
    def name(self): return "payment"

    def execute(self, ctx):
        return StepResult.succeeded({"payment_id": "pay_001"})

    def compensate(self, ctx):
        return StepResult.succeeded()


class MockFailingInventoryStep(SagaStep):
    @property
    def name(self): return "inventory"

    def execute(self, ctx):
        return StepResult.failed("INVENTORY_SHORTAGE", retryable=False)

    def compensate(self, ctx):
        return StepResult.succeeded()


def test_compensation_on_step_failure():
    """Step 3 실패 시 Step 2, 1 역순 compensate 실행 확인."""
    definition = SagaDefinition(
        name="test_saga",
        steps=[MockPaymentStep(), MockPointStep(), MockFailingInventoryStep()],
    )
    orchestrator = SagaOrchestrator(
        lock=mock_lock,
        idempotency=mock_idempotency,
        dlq=mock_dlq,
        event_bus=mock_event_bus,
        circuit_breaker=mock_cb,
        blast_radius=mock_br,
    )
    result = orchestrator.execute_saga("test_saga", {"order_id": 123})

    assert result.status == SagaStatus.COMPENSATED
    assert result.step_instances[0].status == SagaStepStatus.COMPENSATED  # payment
    assert result.step_instances[1].status == SagaStepStatus.COMPENSATED  # point
    assert result.step_instances[2].status == SagaStepStatus.EXECUTE_FAILED  # inventory
```

### 9.2 통합 테스트

```python
def test_compensation_failure_stores_to_dlq():
    """compensate 실패 시 DLQ에 전체 컨텍스트 저장 확인."""
    # MockFailingCompensateStep: compensate()가 실패하는 Step
    definition = SagaDefinition(
        name="test_saga",
        steps=[MockFailingCompensateStep(), MockFailingExecuteStep()],
    )
    result = orchestrator.execute_saga("test_saga", {"order_id": 123})

    assert result.status == SagaStatus.COMPENSATION_FAILED
    mock_dlq.store_failure.assert_called_once()
    call_args = mock_dlq.store_failure.call_args
    assert call_args.kwargs["domain"] == "saga"
    assert call_args.kwargs["failure_type"] == "COMPENSATION_FAILED"
    assert "saga_name" in call_args.kwargs["snapshot_data"]
```

---

## 10. 파일 구조 (최종)

```
services/saga/
├── __init__.py          # 패키지 export
├── models.py            # SagaStatus, StepResult, SagaContext,
│                        # SagaStepStatus, SagaStepInstance,
│                        # SagaDefinition, SagaInstance
├── step.py              # SagaStep(ABC)
├── registry.py          # register_saga(), get_saga_definition()
├── orchestrator.py      # SagaOrchestrator
├── events.py            # EventType 확장 (SAGA_*)
└── lua_scripts.py       # SAGA_TRANSITION_SCRIPT (AtomicTransition 패턴)
```
