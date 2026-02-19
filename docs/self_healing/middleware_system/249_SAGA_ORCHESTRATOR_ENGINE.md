# 249. Saga Orchestrator 엔진

> **Version**: 1.2.0
> **Created**: 2026-02-19
> **Updated**: 2026-02-19 (v1.2.0 — Docker Compose 통합 테스트 추가)
> **Status**: Approved
> **Parent**: [247_SAGA_ORCHESTRATOR_OVERVIEW.md](247_SAGA_ORCHESTRATOR_OVERVIEW.md)
> **Depends**: [248_SAGA_CORE_MODELS.md](248_SAGA_CORE_MODELS.md)
> **Priority**: P1 — 다중 서비스 트랜잭션 자동 복구의 핵심 엔진

## 0. 요약

`SagaOrchestrator` 클래스를 설계한다.
**순방향 실행 → 실패 시 역순 compensate → compensate 실패 시 DLQ 저장**의 전체 흐름을 오케스트레이션한다.
기존 인프라 7종(DistributedRecoveryLock, IdempotencyService, DLQService, EventBus, AtomicTransition 패턴, RecoveryCircuitBreaker, BlastRadiusService)을 재사용한다.

### v1.1.0 변경 요약

| 리뷰 | 반영 내용 | 영향 섹션 |
|------|----------|----------|
| R1: State Persistence | 매 상태 전환 시 `_save_instance()` Checkpoint + Fail-fast (`try-except-reraise`) | §3.2, §3.3, §3.6(신규) |
| R2: Saga Resumption | `resume_saga()` API + `scan_orphan_sagas` Celery Beat 태스크 + GC Pause 방어 (락 선행 획득) | §3.7(신규), §3.8(신규) |
| R3: Async Yield | `apply_async(countdown=delay)` 비동기 대기 + `RETRY_SCHEDULED` 상태 추가 (248번 연동) | §3.2 |
| R4: Partial Execution | 보상 필터 `EXECUTED ∥ (EXECUTE_FAILED ∧ partial_execution)` 반영 + `compensate_step_index` 보정 | §3.2, §3.3 |
| R5: Context Injection | `COMPENSATING` 전환 직전 `set_compensation_context()` 호출 + `can_execute()` 실패 시에도 적용 | §3.2 |
| R6: Lock Heartbeat | `_execute_with_timeout()` 시그니처 확장 + Polling 루프 Lock 연장 + §3.3 호출부 패리티 | §3.5, §3.2, §3.3 |

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

        # ── R1: 초기 Checkpoint ──────────────────────────────
        instance.version += 1
        self._save_instance(instance)

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

    # ── R1: RUNNING 전환 즉시 Checkpoint ─────────────────────
    #    선례: recovery_coordinator.py L522 — session.status = IN_PROGRESS 직후 _save_session()
    instance.version += 1
    self._save_instance(instance)

    # ── R6: lock_namespace 도출 (Forward/Compensation 공용) ──
    #    instance 필드에서 결정적으로 도출 가능. 별도 매개변수 불필요.
    #    선례: distributed_recovery_lock.py L172-207 — namespace 패턴
    lock_namespace = f"saga:{instance.saga_name}:{instance.id}"

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

            # ── R1: SUSPENDED Checkpoint ──
            instance.version += 1
            self._save_instance(instance)

            self._event_bus.emit(
                EventType.SAGA_SUSPENDED,
                data={"step": step_def.name, "instance_id": instance.id},
                source="SagaOrchestrator",
            )
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

            # ── R5: can_execute 실패 시에도 Context Injection ─────
            #    선례: recovery_coordinator.py L1638-1640
            #    session.abort_reason = error (보상 루프 전에 설정)
            instance.context.set_compensation_context(
                failed_step_name=step_def.name,
                abort_reason=f"can_execute rejected: {reason}",
            )

            instance.status = SagaStatus.COMPENSATING
            instance.compensate_step_index = i - 1

            # ── R1: COMPENSATING 전환 Checkpoint ──
            instance.version += 1
            self._save_instance(instance)
            return instance

        # 4. execute 실행 (타임아웃 적용)
        step_instance.status = SagaStepStatus.EXECUTING
        step_instance.execute_started_at = now().isoformat()

        timeout = step_def.timeout_seconds or definition.timeout_seconds

        # ── R6: Lock Heartbeat 매개변수 전달 ──────────────────
        #    선례: 246번 §9.6 — _execute_with_timeout() 내부 Polling 루프
        #    선례: 244번 §2.11 — self._recovery_lock.extend(additional_seconds=300)
        result = self._execute_with_timeout(
            step_def.execute,
            instance.context,
            timeout,
            lock_namespace=lock_namespace,
            instance_id=instance.id,
        )

        if result.success:
            step_instance.status = SagaStepStatus.EXECUTED
            step_instance.execute_completed_at = now().isoformat()
            instance.context.merge_step_result(step_def.name, result.data)
            instance.current_step_index = i + 1

            # ── R1: Step 성공 Checkpoint (OOM 방어) ──────────
            #    선례: recovery_coordinator.py _save_session() 14곳 호출 패턴
            #    선례: 244번 §2.10 — step.compensation_status = COMPENSATED → _save_session()
            instance.version += 1
            self._save_instance(instance)

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

            # ── R4: partial_execution 전파 ───────────────────
            #    248번 R5 합의: StepResult.partial_execution → SagaStepInstance.partial_execution
            step_instance.partial_execution = result.partial_execution

            # ── R1: Step 실패 Checkpoint ──
            instance.version += 1
            self._save_instance(instance)

            # ── R3: Async Yield — 재시도 시 스레드 블로킹 대신 Celery 위임 ──
            #    선례: recovery_tasks.py L351-354
            #    execute_recovery_step_task.apply_async(args=[...], countdown=delay)
            if result.retryable and step_instance.retry_count < definition.max_retries_per_step:
                step_instance.retry_count += 1

                # 백오프 계산 — 기존 BackoffStrategy 프레임워크 재사용
                #    선례: core/backoff.py L249-274 — get_backoff_calculator()
                #    선례: core/backoff.py L44 — ExponentialBackoff(jitter=True, jitter_factor=0.2)
                from selfhealing.core.backoff import get_backoff_calculator
                calculator = get_backoff_calculator(
                    strategy=definition.retry_backoff_strategy,
                )
                delay = calculator.calculate(attempt=step_instance.retry_count)

                step_instance.next_retry_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()

                # R3: RETRY_SCHEDULED 상태로 전환 (관측성 확보)
                #    NOT_STARTED로 돌리면 "처음 시작 전"과 "대기 중" 구분 불가.
                #    248번 v1.2.0 — SagaStepStatus.RETRY_SCHEDULED 추가.
                step_instance.status = SagaStepStatus.RETRY_SCHEDULED

                instance.version += 1
                self._save_instance(instance)

                # Celery countdown으로 미래 시점에 재실행
                #    선례: recovery_tasks.py L385-388
                #    execute_recovery_step_task.apply_async(args=[...], countdown=wait_seconds)
                resume_saga_instance_task.apply_async(
                    args=[instance.id],
                    countdown=delay,
                )
                return instance  # 현재 워커는 즉시 반환 (스레드 해방)

            # ── R5: Context Injection — COMPENSATING 전환 직전 ──
            #    선례: recovery_coordinator.py L1638-1640
            #    session.abort_reason = error → session.status = COMPENSATING → _save_session()
            #    SagaContext.set_compensation_context() (248번 §2.3)
            instance.context.set_compensation_context(
                failed_step_name=step_def.name,
                abort_reason=result.error,
                abort_error_code=result.error_code,
            )

            # ── R4: compensate_step_index 보정 ───────────────
            #    partial_execution=True면 현재 Step도 보상 대상이므로 i부터 시작
            #    False면 직전 성공 Step(i-1)부터 역순
            #    선례: SagaInstance.get_compensation_targets() (248번 §2.7)
            instance.status = SagaStatus.COMPENSATING
            instance.compensate_step_index = i if result.partial_execution else i - 1

            # ── R1: COMPENSATING Checkpoint ──
            instance.version += 1
            self._save_instance(instance)

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

    # ── R1: 최종 COMPLETED Checkpoint ──
    instance.version += 1
    self._save_instance(instance)

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

    # ── R6: lock_namespace 도출 (instance 필드에서 결정적 생성) ──
    #    별도 매개변수 불필요 — _execute_forward()와 동일한 도출 방식.
    lock_namespace = f"saga:{instance.saga_name}:{instance.id}"

    # 역순 순회: 마지막 성공 Step → 첫 Step
    for i in range(instance.compensate_step_index, -1, -1):
        step_def = definition.steps[i]
        step_instance = instance.step_instances[i]

        # ── R4: 보상 대상 필터 (248번 R5 합의 반영) ─────────
        #    기존: step_instance.status != EXECUTED → skip
        #    수정: EXECUTED 또는 (EXECUTE_FAILED + partial_execution) 포함
        #    선례: SagaInstance.get_compensation_targets() (248번 §2.7)
        is_compensation_target = (
            step_instance.status == SagaStepStatus.EXECUTED
            or (
                step_instance.status == SagaStepStatus.EXECUTE_FAILED
                and step_instance.partial_execution
            )
        )
        if not is_compensation_target:
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

        # ── R6: 매 Step 보상 전 Lock TTL 연장 (하트비트) ─────
        #    선례: 244번 §2.11
        #    self._recovery_lock.extend(namespace, session_id, additional_seconds=300)
        try:
            self._lock.extend(
                namespace=lock_namespace,
                session_id=instance.id,
                additional_seconds=300,  # 244번 §2.11 — 5분 연장
            )
        except Exception as e:
            logger.warning(
                "[Saga] Lock extend failed before compensate",
                extra={"instance_id": instance.id, "step": step_def.name, "error": str(e)},
            )

        # compensate 실행
        step_instance.status = SagaStepStatus.COMPENSATING
        step_instance.compensate_started_at = now().isoformat()

        try:
            timeout = step_def.timeout_seconds or definition.timeout_seconds

            # ── R6: Compensation 루프도 Lock Heartbeat 매개변수 전달 ─
            #    리뷰 R6 보완: §3.2와 동일하게 lock 정보 전달
            result = self._execute_with_timeout(
                step_def.compensate,
                instance.context,
                timeout,
                lock_namespace=lock_namespace,
                instance_id=instance.id,
            )

            if result.success:
                step_instance.status = SagaStepStatus.COMPENSATED
                step_instance.compensate_completed_at = now().isoformat()

                # ── R1: Compensate 성공 Checkpoint ──
                #    선례: 244번 §2.10 — step.compensation_status = COMPENSATED → _save_session()
                instance.compensate_step_index = i - 1
                instance.version += 1
                self._save_instance(instance)

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

                # ── R1: Compensate 실패 Checkpoint ──
                instance.version += 1
                self._save_instance(instance)

        except Exception as e:
            step_instance.status = SagaStepStatus.COMPENSATE_FAILED
            step_instance.error_message = str(e)
            step_instance.compensate_completed_at = now().isoformat()
            compensation_failures.append(step_def.name)

            # ── R1: Compensate 예외 Checkpoint ──
            instance.version += 1
            self._save_instance(instance)

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

    # ── R1: 최종 상태 Checkpoint ──
    instance.version += 1
    self._save_instance(instance)

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

### 3.5 _execute_with_timeout() — 타임아웃 래핑 + Lock Heartbeat

```python
def _execute_with_timeout(
    self,
    fn: Callable[[SagaContext], StepResult],
    ctx: SagaContext,
    timeout_seconds: int,
    lock_namespace: str | None = None,
    instance_id: str | None = None,
) -> StepResult:
    """Step 함수를 타임아웃 내에서 실행 + Lock Heartbeat.

    v1.1.0 변경 (리뷰 R6):
    - lock_namespace, instance_id 매개변수 추가
    - Polling 루프로 60초마다 깨어나 Lock TTL 300초(5분) 연장

    246번 문서(STEP_TIMEOUT_MONITOR)와 동일한 접근:
    - concurrent.futures.ThreadPoolExecutor(max_workers=1)
    - 타임아웃 초과 시 StepResult.failed() 반환

    Lock Heartbeat 설계 근거:
    - 244번 §2.11: self._recovery_lock.extend(additional_seconds=300)
    - settings/meta_watchdog.py L49: probe_interval_seconds=30
    - distributed_recovery_lock.py L261-310: extend() 시그니처

    HEARTBEAT_INTERVAL=60 선택 이유:
    - settings/distributed_lock.py의 extend_interval_seconds=60과 동일
    - stuck_threshold_seconds=300 (5분)의 1/5 비율

    EXTEND_SECONDS=300 선택 이유:
    - 244번 §2.11의 additional_seconds=300과 동일
    - extend_interval_seconds=60의 5배 — 보수적 마진
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    HEARTBEAT_INTERVAL = 60   # seconds — extend_interval_seconds와 동일
    EXTEND_SECONDS = 300      # seconds — 244번 §2.11 선례

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, ctx)

        elapsed = 0
        while elapsed < timeout_seconds:
            wait_time = min(HEARTBEAT_INTERVAL, timeout_seconds - elapsed)
            try:
                result = future.result(timeout=wait_time)
                return result
            except TimeoutError:
                elapsed += wait_time

                # Lock Heartbeat: Polling 루프 내 락 연장
                #    선례: 244번 §2.11
                #    self._recovery_lock.extend(namespace, session_id, additional_seconds=300)
                if lock_namespace and instance_id and self._lock:
                    try:
                        self._lock.extend(
                            namespace=lock_namespace,
                            session_id=instance_id,
                            additional_seconds=EXTEND_SECONDS,
                        )
                    except Exception as e:
                        logger.warning(
                            "[Saga] Lock extend failed during step execution",
                            extra={
                                "instance_id": instance_id,
                                "elapsed": elapsed,
                                "error": str(e),
                            },
                        )
            except Exception as e:
                return StepResult.failed(
                    error=f"Step execution error: {type(e).__name__}: {str(e)[:200]}",
                    error_code="STEP_EXCEPTION",
                    retryable=False,
                )

        # 최종 타임아웃
        return StepResult.failed(
            error=f"Step execution timed out after {timeout_seconds}s",
            error_code="STEP_TIMEOUT",
            retryable=True,
        )
```

### 3.6 _save_instance() — 상태 영속화 + Fail-fast (R1)

```python
def _save_instance(self, instance: SagaInstance) -> None:
    """SagaInstance를 저장소에 영속화 (OCC 적용).

    v1.1.0 추가 (리뷰 R1 — State Persistence).

    저장 실패 시 Fail-fast 전략:
    - SessionVersionConflictError → re-raise (동시 쓰기 충돌)
    - Redis 장애/타임아웃 → re-raise (Split-brain 방지)
    - 호출자(Celery Task)가 예외를 잡지 않음 → 태스크 FAILURE 마킹
    - 재시작 시 Watchdog(§3.8)이 고아 Saga를 스캔하여 재개

    저장 실패 = 태스크 실패. 메모리 상태와 DB 상태의 불일치(Split-brain)를
    허용하지 않는다.

    설계 근거:
    - recovery_coordinator.py L1537-1580 — _save_session() 패턴:
      SessionVersionConflictError는 re-raise, 그 외 Exception은 폴백 또는 re-raise.
    - recovery_coordinator.py에서 _save_session()이 14곳에서 호출됨.
    - SagaOrchestrator도 동일 빈도로 Checkpoint를 수행.

    OCC 메커니즘:
    - 호출 전 instance.version += 1 (호출부 책임)
    - Redis Lua 스크립트(§4.1 SAGA_TRANSITION_SCRIPT)로 원자적 CAS
    - 버전 불일치 시 SessionVersionConflictError 발생
    """
    backend = self._get_backend()
    key = f"saga:instance:{instance.id}"
    expected_version = instance.version - 1  # 호출부에서 이미 +1 했으므로

    try:
        data = instance.to_dict()

        if hasattr(backend, "_client"):
            # Redis: Lua CAS 스크립트로 원자적 저장
            import json
            serialized = json.dumps(data, default=str)
            result = backend._client.eval(
                SAGA_INSTANCE_CAS_SCRIPT,
                1,
                backend._make_key(key),
                serialized,
                str(expected_version),
            )
            if result == 0:
                instance.version = expected_version  # 롤백
                raise SessionVersionConflictError(
                    instance.id, expected_version, instance.version
                )
        else:
            # InMemory/테스트: 단순 저장
            backend.set(key, data)

    except SessionVersionConflictError:
        # OCC 충돌: 동시 쓰기 감지 → re-raise
        #    선례: recovery_coordinator.py L1565 — except SessionVersionConflictError: raise
        raise

    except Exception as e:
        # Redis 장애/타임아웃: Split-brain 방지를 위해 re-raise
        #    선례: recovery_coordinator.py의 Fail-fast 패턴
        #    리뷰 R1 보완: 저장 실패 = 태스크 실패 원칙
        logger.error(
            "[Saga] _save_instance failed — Fail-fast",
            extra={
                "instance_id": instance.id,
                "version": instance.version,
                "error": str(e),
            },
        )
        raise
```

**Checkpoint 호출 지점 요약** (14곳):

| # | 위치 | 상태 전환 | 선례 |
|---|------|----------|------|
| 1 | `execute_saga()` 인스턴스 생성 후 | PENDING → (초기) | `_save_session()` L522 |
| 2 | `_execute_forward()` RUNNING 전환 | PENDING → RUNNING | `_save_session()` L661 |
| 3 | `_execute_forward()` SUSPENDED 전환 | RUNNING → SUSPENDED | `_save_session()` L736 |
| 4 | `_execute_forward()` can_execute 실패 | → COMPENSATING | `_save_session()` L779 |
| 5 | `_execute_forward()` Step 성공 | step → EXECUTED | `_save_session()` L1314 |
| 6 | `_execute_forward()` Step 실패 | step → EXECUTE_FAILED | `_save_session()` L1382 |
| 7 | `_execute_forward()` RETRY_SCHEDULED | step → RETRY_SCHEDULED | `_save_session()` L1605 |
| 8 | `_execute_forward()` COMPENSATING 전환 | RUNNING → COMPENSATING | `_save_session()` L1644 |
| 9 | `_execute_forward()` COMPLETED | RUNNING → COMPLETED | `_save_session()` L1660 |
| 10 | `_execute_compensation()` compensate 성공 | step → COMPENSATED | `_save_session()` L1888 |
| 11 | `_execute_compensation()` compensate 실패 | step → COMPENSATE_FAILED | `_save_session()` L1895 |
| 12 | `_execute_compensation()` compensate 예외 | step → COMPENSATE_FAILED | `_save_session()` L1902 |
| 13 | `_execute_compensation()` 최종 상태 | → COMPENSATED/COMPENSATION_FAILED | `_save_session()` L1907 |
| 14 | `resume_saga()` 재개 시 | SUSPENDED → RUNNING | §3.7 |

### 3.7 resume_saga() — 중단된 Saga 재개 API (R2)

```python
def resume_saga(self, instance_id: str) -> SagaInstance:
    """중단된 Saga를 현재 상태에서 이어서 실행.

    v1.1.0 추가 (리뷰 R2 — Saga Resumption).

    설계 근거:
    - 235번 §5: resume_recovery() — "실패 지점 재개" 패턴
    - 235번 §11-5: resume_recovery() — 무한 재개 방지 (카운터 검사 + 메타데이터 기록)
    - SagaInstance.current_step_index / compensate_step_index로 정확한 지점부터 재개

    재개 대상 상태:
    - SUSPENDED → 서킷브레이커 상태 재확인 후 Forward 재개
    - RUNNING (고아) → current_step_index부터 Forward 재개
    - COMPENSATING (고아) → compensate_step_index부터 Compensate 재개
    - RETRY_SCHEDULED → Celery countdown 만료 후 자동 호출됨

    멱등성:
    - IdempotencyService.check()으로 이미 완료된 Step 자동 skip
    - 분산 락 획득 실패 시 즉시 반환 (다른 워커가 처리 중)

    무한 재개 방지:
    - instance.metadata에 resume_count 기록
    - MAX_RESUME_COUNT (기본 10) 초과 시 COMPENSATION_FAILED 전환 + DLQ 저장
    - 선례: 235번 §11-5 카운터 검사 패턴

    GC Pause 방어 (리뷰 R2 보완):
    - 재개 시 반드시 분산 락 획득을 선행
    - 락 획득 성공 = 이전 워커의 락 만료 확인 = 안전하게 재개 가능
    - 락 획득 실패 = 다른 워커가 활성 = 재개 불필요
    - 선례: distributed_recovery_lock.py L217-260 — release() 소유권 검증 Lua 스크립트
    """
    MAX_RESUME_COUNT = 10

    # 1. 인스턴스 로드
    instance = self._load_instance(instance_id)
    if instance is None:
        raise ValueError(f"Saga instance '{instance_id}' not found")

    # 2. 재개 가능 상태 확인
    resumable_statuses = {
        SagaStatus.SUSPENDED,
        SagaStatus.RUNNING,
        SagaStatus.COMPENSATING,
    }
    if instance.status not in resumable_statuses:
        logger.info(
            "[Saga] Instance not resumable",
            extra={"instance_id": instance_id, "status": instance.status.value},
        )
        return instance

    # 3. 무한 재개 방지 카운터
    #    선례: 235번 §11-5 — resume_recovery() 카운터 검사
    if instance.metadata is None:
        instance.metadata = {}
    resume_count = instance.metadata.get("resume_count", 0)
    if resume_count >= MAX_RESUME_COUNT:
        instance.status = SagaStatus.COMPENSATION_FAILED
        instance.error_message = (
            f"Max resume count ({MAX_RESUME_COUNT}) exceeded"
        )
        instance.version += 1
        self._save_instance(instance)
        self._store_compensation_failure_to_dlq(instance, [])
        return instance
    instance.metadata["resume_count"] = resume_count + 1

    # 4. 정의 조회 + 버전 호환성 검증
    definition = get_saga_definition(instance.saga_name)
    if not self._validate_version_compatibility(instance):
        instance.version += 1
        self._save_instance(instance)
        return instance

    # 5. 분산 락 획득 (GC Pause 방어)
    #    리뷰 R2 핵심: 신규 락 획득 시도를 먼저 거친 후 성공했을 때만 재개
    #    선례: distributed_recovery_lock.py L172-207
    lock_namespace = f"saga:{instance.saga_name}:{instance.id}"
    acquired = self._lock.acquire(
        namespace=lock_namespace,
        session_id=instance.id,
    )
    if not acquired:
        logger.info(
            "[Saga] Lock acquisition failed on resume — another worker active",
            extra={"instance_id": instance_id},
        )
        return instance

    try:
        self._event_bus.emit(
            EventType.SAGA_RESUMED,
            data={"instance_id": instance.id, "resume_count": resume_count + 1},
            source="SagaOrchestrator",
        )

        # 6. 상태별 재개
        if instance.status in {SagaStatus.SUSPENDED, SagaStatus.RUNNING}:
            # Forward 재개: current_step_index부터
            instance = self._execute_forward(instance, definition)
            if instance.status == SagaStatus.COMPENSATING:
                instance = self._execute_compensation(instance, definition)

        elif instance.status == SagaStatus.COMPENSATING:
            # Compensate 재개: compensate_step_index부터
            instance = self._execute_compensation(instance, definition)

    finally:
        self._lock.release(
            namespace=lock_namespace,
            session_id=instance.id,
        )

    self._emit_final_event(instance)
    return instance
```

### 3.8 scan_orphan_sagas() — 고아 Saga 스캐너 (R2)

```python
@celery_app.task(
    name="selfhealing.scan_orphan_sagas",
    queue="selfhealing_recovery",
)
def scan_orphan_sagas() -> dict[str, Any]:
    """고아/중단 Saga를 주기적으로 스캔하여 재개.

    v1.1.0 추가 (리뷰 R2 — Saga Resumption + GC Pause 방어).

    Celery Beat로 2분 간격 실행.

    설계 근거:
    - meta/watchdog.py — SelfHealerWatchdog:
      daemon=True 스레드 + probe_interval_seconds=30 루프
    - recovery_tasks.py L260 — execute_recovery_step_task:
      Celery Task 기반 비동기 복구 패턴
    - settings/meta_watchdog.py L68:
      stuck_threshold_seconds=300.0 (5분) — Stuck 감지 임계치

    고아 판별 기준:
    1. status == RUNNING 이지만 updated_at이 STALE_THRESHOLD(5분)보다 오래됨
       → 워커 크래시 또는 OOM으로 락 만료된 고아
    2. status == SUSPENDED 이고 해당 서킷브레이커가 CLOSED로 복귀
       → 외부 서비스 정상화, 재개 가능
    3. status == COMPENSATING 이지만 updated_at이 STALE_THRESHOLD보다 오래됨
       → 보상 루프 중 크래시

    GC Pause 방어 원칙 (리뷰 R2 핵심):
    - 재개를 트리거할 때, 반드시 "신규 분산 락 획득 시도"를 먼저 수행
    - 락 획득 성공 시에만 resume_saga_instance_task를 dispatch
    - 선례: distributed_recovery_lock.py release() — Lua 스크립트 소유권 검증

    STALE_THRESHOLD=300 선택 이유:
    - settings/meta_watchdog.py L68: stuck_threshold_seconds=300.0과 동일
    - 일반적인 분산 락 TTL(30분)보다 훨씬 짧아 빠른 감지 가능
    - GC Pause가 5분 이상 지속되는 경우는 극히 드물어 오탐 위험 적음
    """
    STALE_THRESHOLD_SECONDS = 300  # settings/meta_watchdog.py stuck_threshold_seconds

    results = {"scanned": 0, "resumed": 0, "skipped": 0}

    # 1. 비-터미널 상태의 모든 Saga 인스턴스 스캔
    active_instances = _scan_active_saga_instances()
    results["scanned"] = len(active_instances)

    now_utc = datetime.now(timezone.utc)

    for instance in active_instances:
        is_stale = False

        if instance.status in {SagaStatus.RUNNING, SagaStatus.COMPENSATING}:
            # updated_at이 STALE_THRESHOLD보다 오래되면 고아
            if instance.started_at:
                last_update = datetime.fromisoformat(instance.started_at)
                is_stale = (now_utc - last_update).total_seconds() > STALE_THRESHOLD_SECONDS

        elif instance.status == SagaStatus.SUSPENDED:
            # 서킷브레이커 상태 확인
            # 해당 Step의 서킷브레이커가 CLOSED면 재개 가능
            is_stale = True  # SUSPENDED는 항상 재개 시도 (resume_saga에서 CB 재확인)

        if not is_stale:
            results["skipped"] += 1
            continue

        # 2. GC Pause 방어: 락 획득 시도
        #    리뷰 R2 핵심: 락 획득 성공 시에만 재개
        lock_namespace = f"saga:{instance.saga_name}:{instance.id}"
        acquired = _try_acquire_lock(lock_namespace, instance.id)

        if acquired:
            # 즉시 해제 — resume_saga_instance_task가 다시 획득
            _release_lock(lock_namespace, instance.id)

            # 3. Celery Task로 재개 위임
            #    선례: recovery_tasks.py L391
            #    execute_recovery_step_task.delay(session_id, namespace)
            resume_saga_instance_task.delay(instance.id)
            results["resumed"] += 1
        else:
            # 다른 워커가 처리 중 — skip
            results["skipped"] += 1

    return results
```

**Celery Beat 등록**:

```python
# celery/beat_schedule.py 또는 settings에 추가
CELERY_BEAT_SCHEDULE = {
    # ... 기존 Beat 태스크 ...

    "scan-orphan-sagas": {
        "task": "selfhealing.scan_orphan_sagas",
        "schedule": 120.0,  # 2분 간격
        "options": {"queue": "selfhealing_recovery"},
    },
}
```

**resume_saga_instance_task — Celery Task**:

```python
@celery_app.task(
    name="selfhealing.resume_saga_instance",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="selfhealing_recovery",
)
def resume_saga_instance_task(self, instance_id: str) -> dict[str, Any]:
    """중단된 Saga 인스턴스를 재개하는 Celery Task.

    v1.1.0 추가 (리뷰 R2 + R3).

    호출 경로:
    1. scan_orphan_sagas() Beat Task → 고아 감지 시 디스패치
    2. _execute_forward() Async Yield → RETRY_SCHEDULED + countdown으로 디스패치
    3. 외부 API → 수동 재개 트리거

    네이밍 근거:
    - 기존: execute_recovery_step_task (recovery_tasks.py L260)
    - 패턴: [동사]_[대상]_[단위]_task
    - resume_saga_instance_task: resume(동사) + saga_instance(대상) + task(접미어)
    """
    orchestrator = SagaOrchestrator()  # 의존성 주입은 ProviderRegistry로 해결
    instance = orchestrator.resume_saga(instance_id)
    return {
        "instance_id": instance_id,
        "final_status": instance.status.value,
    }
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

### 4.2 SAGA_INSTANCE_CAS_SCRIPT — 인스턴스 저장용 (R1)

```python
# v1.1.0 추가 (리뷰 R1 — _save_instance용 CAS 스크립트)
#
# recovery_coordinator.py의 SESSION_CAS_SCRIPT와 동일한 패턴:
# - expected_version과 현재 저장된 version을 비교
# - 일치하면 새 데이터로 덮어쓰기
# - 불일치하면 0 반환 → SessionVersionConflictError 발생
SAGA_INSTANCE_CAS_SCRIPT = """
local key = KEYS[1]
local new_data = ARGV[1]
local expected_version = tonumber(ARGV[2])

local current = redis.call("GET", key)
if current then
    local decoded = cjson.decode(current)
    local current_version = tonumber(decoded["version"] or 0)
    if current_version ~= expected_version then
        return 0
    end
end

redis.call("SET", key, new_data)
return 1
"""
```

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

| 실패 유형 | Orchestrator 행동 | DLQ 저장 | EventBus 이벤트 | Checkpoint |
|----------|------------------|---------|----------------|-----------|
| Step execute 실패 (retryable) | RETRY_SCHEDULED + Celery countdown 위임 (R3) | ❌ | SAGA_STEP_FAILED | ✅ |
| Step execute 실패 (non-retryable) | 즉시 COMPENSATING 전환 + Context Injection (R5) | ❌ | SAGA_STEP_FAILED | ✅ |
| Step execute 실패 (partial_execution) | compensate_step_index = i (현재 Step 포함 보상, R4) | ❌ | SAGA_STEP_FAILED | ✅ |
| Step execute 타임아웃 | retryable=True로 RETRY_SCHEDULED (R3) | ❌ | SAGA_STEP_FAILED | ✅ |
| CircuitBreaker OPEN | SUSPENDED 전환, Watchdog 대기 (R2) | ❌ | SAGA_SUSPENDED | ✅ |
| can_execute 거부 | COMPENSATING 전환 + Context Injection (R5) | ❌ | SAGA_STEP_FAILED | ✅ |
| Step compensate 성공 | 다음 역순 Step으로 진행 + Lock 연장 (R6) | ❌ | SAGA_COMPENSATING | ✅ |
| Step compensate 실패 | COMPENSATION_FAILED + DLQ | ✅ | SAGA_COMPENSATION_FAILED | ✅ |
| 전체 Saga 타임아웃 | COMPENSATING 전환 후 역순 보상 | ❌* | SAGA_TIMED_OUT | ✅ |
| 거버넌스 차단 | 즉시 반환, 실행하지 않음 | ❌ | (audit_on_block) | ❌ |
| 분산 락 획득 실패 | 즉시 반환 | ❌ | ❌ | ❌ |
| _save_instance 실패 (R1) | Fail-fast — 예외 re-raise → 태스크 FAILURE | ❌ | ❌ | (실패) |
| 고아 Saga 감지 (R2) | scan_orphan_sagas → resume_saga_instance_task | ❌ | SAGA_RESUMED | ✅ |
| 무한 재개 초과 (R2) | COMPENSATION_FAILED + DLQ | ✅ | SAGA_COMPENSATION_FAILED | ✅ |

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

### 9.3 v1.1.0 추가 테스트

```python
def test_retry_scheduled_async_yield():
    """R3: retryable 실패 시 RETRY_SCHEDULED + Celery countdown 위임 확인."""
    # MockRetryableStep: execute()가 retryable=True로 실패
    definition = SagaDefinition(
        name="test_saga",
        steps=[MockRetryableStep()],
        max_retries_per_step=2,
        retry_backoff_strategy="exponential",
    )
    result = orchestrator.execute_saga("test_saga", {"order_id": 123})

    # Forward가 중단되고 Celery 위임됨
    assert result.step_instances[0].status == SagaStepStatus.RETRY_SCHEDULED
    assert result.step_instances[0].next_retry_at is not None
    mock_celery.apply_async.assert_called_once()


def test_partial_execution_compensation_includes_failed_step():
    """R4: partial_execution=True인 EXECUTE_FAILED Step도 보상 대상."""
    # Step 1: 성공, Step 2: failed_with_side_effect, Step 3: 실패
    definition = SagaDefinition(
        name="test_saga",
        steps=[
            MockSuccessStep("payment"),
            MockPartialFailStep("inventory"),  # partial_execution=True
        ],
    )
    result = orchestrator.execute_saga("test_saga", {"order_id": 123})

    # inventory Step이 EXECUTE_FAILED이지만 compensate 호출됨
    assert result.step_instances[1].status in {
        SagaStepStatus.COMPENSATED,
        SagaStepStatus.COMPENSATE_FAILED,
    }
    assert result.step_instances[0].status == SagaStepStatus.COMPENSATED


def test_context_injection_on_compensation():
    """R5: COMPENSATING 전환 시 failed_step_name, abort_reason 주입 확인."""
    definition = SagaDefinition(
        name="test_saga",
        steps=[MockSuccessStep("payment"), MockFailingStep("inventory")],
    )
    result = orchestrator.execute_saga("test_saga", {"order_id": 123})

    assert result.context.failed_step_name == "inventory"
    assert result.context.abort_reason is not None


def test_save_instance_failure_causes_task_failure():
    """R1: _save_instance 실패 시 예외가 re-raise되어 태스크 FAILURE."""
    mock_backend = Mock(side_effect=ConnectionError("Redis down"))
    orchestrator = SagaOrchestrator(lock=mock_lock, ...)

    with pytest.raises(ConnectionError):
        orchestrator.execute_saga("test_saga", {"order_id": 123})


def test_resume_saga_acquires_lock_first():
    """R2: resume_saga()가 분산 락 획득을 선행하는지 확인 (GC Pause 방어)."""
    mock_lock = Mock()
    mock_lock.acquire.return_value = False  # 다른 워커가 보유 중

    orchestrator = SagaOrchestrator(lock=mock_lock, ...)
    result = orchestrator.resume_saga("saga-123")

    mock_lock.acquire.assert_called_once()
    # 락 획득 실패 → 재개하지 않음
    assert result.status != SagaStatus.COMPLETED


def test_resume_saga_max_count_exceeded():
    """R2: 무한 재개 방지 — MAX_RESUME_COUNT 초과 시 COMPENSATION_FAILED."""
    instance = create_test_instance(
        status=SagaStatus.SUSPENDED,
        metadata={"resume_count": 10},
    )
    result = orchestrator.resume_saga(instance.id)

    assert result.status == SagaStatus.COMPENSATION_FAILED
    mock_dlq.store_failure.assert_called_once()
```

---

## 10. 파일 구조 (최종)

```
services/saga/
├── __init__.py          # 패키지 export
├── models.py            # SagaStatus, StepResult, SagaContext,
│                        # SagaStepStatus (RETRY_SCHEDULED 포함),
│                        # SagaStepInstance, SagaDefinition, SagaInstance
├── step.py              # SagaStep(ABC)
├── registry.py          # register_saga(), get_saga_definition()
├── orchestrator.py      # SagaOrchestrator (_save_instance, resume_saga 포함)
├── tasks.py             # resume_saga_instance_task, scan_orphan_sagas (Celery)
├── events.py            # EventType 확장 (SAGA_*)
└── lua_scripts.py       # SAGA_TRANSITION_SCRIPT, SAGA_INSTANCE_CAS_SCRIPT
```

---

## 11. v1.1.0 리뷰 반영 상세

### 11.1 R1 — State Persistence (Fail-fast Checkpoint)

**리뷰 요약**: 매 상태 전환 시 `_save_instance()` Checkpoint 필요. 저장 실패 시 Fail-fast.

**반영 내용**:
- §3.6에 `_save_instance()` 메서드 신규 추가
- §3.2, §3.3에 14곳의 Checkpoint 호출 삽입
- §4.2에 `SAGA_INSTANCE_CAS_SCRIPT` Lua 스크립트 추가
- 저장 실패 시 `try-except-reraise` 패턴으로 Fail-fast

**코드 근거**:
- `recovery_coordinator.py` L1537-1580: `_save_session()` — `SessionVersionConflictError` re-raise
- `recovery_coordinator.py`에서 `_save_session()` 14곳 호출
- `RecoverySession.version: int = 0` (`recovery_state.py` L239): OCC 패턴

**리뷰 보완 (Edge Case)**: 저장 실패 = 태스크 실패 원칙을 채택.
별도 `SagaPersistenceError` 예외 클래스는 **추가하지 않음**.
이유: 기존 에러 체계가 모듈별 분산(`RecoveryLockError`, `SessionVersionConflictError`, `StepTimeoutError` 등)되어 있고,
통합 base 에러 클래스(`SelfHealingError` 등)가 존재하지 않음.
기존 패턴(re-raise)과 일관성을 유지.

### 11.2 R2 — Saga Resumption (GC Pause 방어)

**리뷰 요약**: `resume_saga()` API + Watchdog + GC Pause 시 락 선행 획득.

**반영 내용**:
- §3.7에 `resume_saga()` 메서드 신규 추가
- §3.8에 `scan_orphan_sagas()` Celery Beat 태스크 + `resume_saga_instance_task` 추가
- 재개 시 분산 락 획득을 선행 조건으로 강제
- 무한 재개 방지: `metadata.resume_count` 카운터 (MAX=10)

**코드 근거**:
- `235번 §5`: `resume_recovery()` — 실패 지점 재개 패턴
- `235번 §11-5`: 무한 재개 방지 카운터 검사
- `recovery_tasks.py` L351-354: `apply_async(countdown=delay)` 패턴
- `meta/watchdog.py` L607-625: daemon=True 스레드 + 주기적 루프
- `settings/meta_watchdog.py` L49-68: `stuck_threshold_seconds=300.0`
- `distributed_recovery_lock.py` L217-260: `release()` — Lua 스크립트 소유권 검증

**네이밍 결정**:
| 제안 | 최종 결정 | 이유 |
|------|----------|------|
| `resume_saga()` | ✅ 채택 | `resume_recovery()` 선례와 일관 |
| `resume_saga_task` | → `resume_saga_instance_task` | 기존 패턴 `[동사]_[대상]_[단위]_task`에 맞춤 (`execute_recovery_step_task` 선례) |
| `scan_orphan_sagas` | ✅ 채택 | 역할 명확, 충돌 없음 |

**STALE_THRESHOLD=300 결정 근거**:
`settings/meta_watchdog.py` L68의 `stuck_threshold_seconds=300.0`과 동일 값 채택.

### 11.3 R3 — Async Yield (RETRY_SCHEDULED)

**리뷰 요약**: 스레드 블로킹 대신 Celery countdown 위임 + 명시적 재시도 상태.

**반영 내용**:
- §3.2의 재시도 분기를 `apply_async(countdown=delay)` + `return instance` 방식으로 변경
- 248번 문서에 `SagaStepStatus.RETRY_SCHEDULED` 추가 (v1.2.0)

**코드 근거**:
- `recovery_tasks.py` L351-354: `apply_async(countdown=delay)` 선례
- `core/backoff.py` L249-274: `get_backoff_calculator()` 팩토리
- `core/backoff.py` L44: `ExponentialBackoff(jitter=True, jitter_factor=0.2)`
- `SagaStepInstance.next_retry_at` (248번 §2.5): 다음 재시도 예정 시각

**네이밍 결정**:
| 리뷰 제안 | 최종 결정 | 이유 |
|-----------|----------|------|
| `PENDING_RETRY` | → `RETRY_SCHEDULED` | 기존 enum 패턴 분석 결과 `NOT_STARTED`(상태명사), `EXECUTING`(진행형), `EXECUTED`(완료형) 패턴. `PENDING_RETRY`는 `형용사_명사` 형식으로 비일관. `RETRY_SCHEDULED`는 "Celery에 스케줄됨"이라는 실제 동작을 정확히 반영. |

**RETRY_SCHEDULED와 다른 상태의 교차 처리**:
- `get_compensation_targets()`에서 `RETRY_SCHEDULED`는 **제외** (재시도 대기 중이므로 보상 불필요)
- 재시도 횟수 초과 시 `EXECUTE_FAILED` + `partial_execution` 전파 → 보상 대상 포함
- `current_step_index`는 실패 시 증가하지 않으므로 재개 시 정확한 Step 재실행

### 11.4 R4 — Partial Execution (보상 필터 + compensate_step_index)

**리뷰 요약**: 248번 `partial_execution` 합의를 §3.3 보상 루프에 반영.

**반영 내용**:
- §3.3의 보상 대상 필터를 `EXECUTED ∥ (EXECUTE_FAILED ∧ partial_execution)` 조건으로 변경
- §3.2에서 `step_instance.partial_execution = result.partial_execution` 전파 추가
- §3.2에서 `compensate_step_index`를 `partial_execution` 여부에 따라 보정 (`i` vs `i - 1`)

**코드 근거**:
- `SagaInstance.get_compensation_targets()` (248번 §2.7): 동일 조건 유틸 메서드
- `StepResult.failed_with_side_effect()` (248번 §2.2): `partial_execution=True` 팩토리

### 11.5 R5 — Context Injection (실패 원인 주입)

**리뷰 요약**: COMPENSATING 전환 직전에 `set_compensation_context()` 호출.

**반영 내용**:
- §3.2에서 execute 실패 → COMPENSATING 전환 직전에 `instance.context.set_compensation_context()` 호출
- §3.2에서 `can_execute()` 실패 시에도 동일하게 호출 (신규 발견)

**코드 근거**:
- `SagaContext.failed_step_name` (248번 §2.3): "COMPENSATING 전환 시 Orchestrator가 설정"
- `SagaContext.set_compensation_context()` (248번 §2.3): 편의 메서드
- `recovery_coordinator.py` L1638-1640: `session.abort_reason = error` → `session.status = COMPENSATING`

**신규 발견**: `can_execute()` 실패 시 Context Injection 누락
원래 리뷰에서 언급하지 않았으나, `can_execute()` 실패도 `COMPENSATING` 전환을 유발하므로
동일하게 `abort_reason=f"can_execute rejected: {reason}"` 설정.

### 11.6 R6 — Lock Heartbeat (시그니처 확장 + Compensation 호출부)

**리뷰 요약**: `_execute_with_timeout()` 시그니처에 락 매개변수 추가 + Compensation 루프에도 반영.

**반영 내용**:
- §3.5의 시그니처에 `lock_namespace`, `instance_id` 매개변수 추가
- §3.5 내부에 60초 Polling + 300초 Lock 연장 루프 구현
- §3.2의 Forward 호출부에 락 매개변수 전달
- §3.3의 Compensation 호출부에도 동일하게 전달 (리뷰 보완)
- §3.3에서 매 Step 보상 전 `self._lock.extend()` 직접 호출 추가 (244번 §2.11 패턴)
- `lock_namespace`는 `instance` 필드에서 결정적 도출 — 별도 매개변수 불필요

**코드 근거**:
- `distributed_recovery_lock.py` L261-310: `extend(namespace, session_id, additional_seconds)`
- `244번 §2.11`: `self._recovery_lock.extend(additional_seconds=300)` — 매 Step 보상 전 Lock 연장
- `settings/meta_watchdog.py` L49: `probe_interval_seconds=30` → `HEARTBEAT_INTERVAL=60` (2배 마진)
- `246번 §9.6.3`: `_execute_with_timeout()` 내부 ThreadPoolExecutor + Polling 루프

**`lock_namespace` 접근 경로 결정**:
| 방안 | 선택 | 이유 |
|------|------|------|
| A: `instance`에서 도출 | ✅ 채택 | `f"saga:{instance.saga_name}:{instance.id}"` — 결정적. 매개변수 추가 불필요 |
| B: `_execute_compensation` 매개변수 추가 | ❌ 미채택 | 시그니처 변경의 파급 범위가 큼, 도출이 가능한데 불필요한 중복 |

---

## 12. 네이밍 검증 — 기존 코드베이스 충돌 여부 (v1.1.0 추가분)

| 신규 네이밍 | 코드베이스 존재 여부 | 판정 |
|---|---|---|
| `_save_instance()` | `_save_session()` 존재 (다른 클래스) | ✅ 충돌 없음 — `session` → `instance` 컨벤션 전환 |
| `resume_saga()` | `resume_recovery()` 존재 (다른 클래스) | ✅ 충돌 없음 — `recovery` → `saga` 전환 |
| `resume_saga_instance_task` | `execute_recovery_step_task` 존재 (다른 태스크) | ✅ 충돌 없음 |
| `scan_orphan_sagas` | 없음 | ✅ |
| `RETRY_SCHEDULED` | `SagaStepStatus`에 없음. `RecoveryStatus`에도 없음 | ✅ |
| `SAGA_INSTANCE_CAS_SCRIPT` | `SESSION_CAS_SCRIPT` 존재 (다른 스크립트) | ✅ 충돌 없음 |
| `STALE_THRESHOLD_SECONDS` | `stuck_threshold_seconds` 존재 (settings) | ✅ 충돌 없음 — 상수 vs 설정 필드 |
| `HEARTBEAT_INTERVAL` | 없음 | ✅ |
| `EXTEND_SECONDS` | 없음 | ✅ |
| `MAX_RESUME_COUNT` | 없음 | ✅ |

---

## 13. Docker Compose 통합 테스트 (v1.2.0)

> §9의 단위·통합 테스트는 **Mock 기반**이다.
> 본 절은 **Real Redis + Docker Compose** 환경에서 실제 Lua 스크립트·OCC·동시성 충돌을 검증하는 통합 테스트를 정의한다.

### 13.1 실행 방법

```bash
docker-compose -f docker-compose.test.yml run --rm test-saga-orchestrator
```

- **서비스**: `test-saga-orchestrator` (`docker-compose.test.yml`에 정의)
- **의존**: `db` (PostgreSQL 15), `redis` (Redis 7)
- **테스트 파일**: `tests/integration/selfhealing/test_saga_orchestrator_integration.py`

### 13.2 테스트 클래스 총괄 (10개 클래스, 31개 테스트)

| # | 클래스 | 테스트 수 | 검증 대상 |
|---|---|---|---|
| 1 | `TestRedisOCC` | 5 | `SAGA_INSTANCE_CAS_SCRIPT` — 신규 키, 버전 일치/불일치, 순차 증가, 복잡 데이터 |
| 2 | `TestRedisTransitionScript` | 4 | `SAGA_TRANSITION_SCRIPT` — 상태 CAS 일치/불일치, 존재하지 않는 키, 전체 라이프사이클 |
| 3 | `TestConcurrencyConflict` | 3 | 멀티스레드 동시 저장 (1 성공/1 `SessionVersionConflictError`), 순차 증가, Stale 거부 |
| 4 | `TestSagaLifecycleRedis` | 6 | Forward 성공, Forward 실패 → COMPENSATED, 3-step 실패, 부분 실행, DLQ 저장, 버전 카운트 |
| 5 | `TestCeleryTaskDispatch` | 4 | RETRY_SCHEDULED → Celery dispatch, resume 재개, MAX_RESUME_COUNT 초과, 락 실패 |
| 6 | `TestOrphanSagaScan` | 3 | Stale RUNNING 감지, SUSPENDED 감지, 종료 상태 무시 |
| 7 | `TestLockHeartbeat` | 2 | 느린 Step 중 TTL 연장, Compensation 루프 중 TTL 연장 |
| 8 | `TestEventDispatchChain` | 2 | 성공 이벤트 순서, 실패 이벤트 순서 |
| 9 | `TestGovernanceBlockIntegration` | 1 | BlastRadius 차단 → GOVERNANCE_BLOCKED |
| 10 | `TestCircuitBreakerIntegration` | 1 | CircuitBreaker OPEN → SUSPENDED 전환 |

### 13.3 핵심 검증 포인트

#### Redis OCC (Lua CAS)

```
✔ SAGA_INSTANCE_CAS_SCRIPT — 신규 키: version=0이면 SET 성공
✔ SAGA_INSTANCE_CAS_SCRIPT — 버전 일치: 기대 version과 저장 version 동일 시 UPDATE + version+1
✔ SAGA_INSTANCE_CAS_SCRIPT — 버전 불일치: 기대 version ≠ 저장 version이면 nil 반환 (충돌 감지)
✔ SAGA_TRANSITION_SCRIPT — status CAS: 현재 status == expected 시에만 전환 허용
```

#### 동시성 충돌

```
✔ 동시 _save_instance 호출 → 1 성공 / 1 SessionVersionConflictError
✔ 순차 _save_instance → version 0→1→2 단조 증가
✔ Stale version write → SessionVersionConflictError 발생 확인
```

#### Celery Task 디스패치 체인

```
✔ RETRY_SCHEDULED 상태 → resume_saga_instance_task.apply_async 호출 확인
✔ resume_saga_instance_task → orchestrator.resume_saga 호출 확인
✔ MAX_RESUME_COUNT 초과 → DLQ 저장 + COMPENSATION_FAILED
✔ 락 획득 실패 → resume 중단
```

### 13.4 테스트 인프라 구성

```python
class RedisTestBackend:
    """Real Redis 기반 StateBackend (테스트 전용)."""
    PREFIX = "selfhealing:state:"

    def __init__(self, redis_client):
        self._client = redis_client

    def _make_key(self, key: str) -> str:
        return f"{self.PREFIX}{key}"

    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...
    def scan(self, match: str) -> list[str]: ...
```

- **Mock 대상**: `DistributedRecoveryLock`, `IdempotencyService`, `DLQService`, `EventBus`, `RecoveryCircuitBreaker`, `BlastRadiusService`
- **Real 대상**: Redis (Lua 스크립트 실행, 키-값 저장, OCC 검증)

### 13.5 실행 결과

```
31 passed in 6.15s
```

| 지표 | 값 |
|---|---|
| 총 테스트 | 31 |
| 성공 | 31 |
| 실패 | 0 |
| 실행 시간 | 6.15s |
| Python | 3.12.12 |
| pytest | 9.0.2 |
| Redis | 7-alpine |
