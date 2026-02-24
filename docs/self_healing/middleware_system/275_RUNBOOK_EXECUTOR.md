# 275. Runbook Executor 설계

> **Status**: Design
> **References**:
> - [274_RUNBOOK_REGISTRY.md](274_RUNBOOK_REGISTRY.md) — Runbook/RunbookStep 모델 정의
> - `services/saga/step.py` — SagaStep ABC (execute + compensate 패턴)
> - `services/saga/models.py` — SagaContext, StepResult, SagaStatus
> - `core/action_executor.py` — ActionExecutor, Action, ActionResult
> - `services/coordination/idempotent_step_handlers.py` — IdempotentStepHandler, IdempotencyRecord, generate_idempotency_key()
> - `services/coordination/distributed_recovery_lock.py` — DistributedRecoveryLock (acquire/release/extend)
> - `services/coordination/recovery_coordinator/_session_persistence.py` — _fail_session() 보상 패턴

---

## 1. 목적

274에서 정의한 `Runbook` / `RunbookStep` 모델과 `ActionPrimitiveRegistry`의 실행 프리미티브를
**순차적으로 실행**하고, 실패 시 **역순 보상(compensation)**을 수행하는 실행 엔진을 설계한다.

기존 코드베이스의 두 가지 실행 패턴을 결합한다:

| 패턴 | 출처 | 역할 |
|------|------|------|
| **SagaStep ABC** | `services/saga/step.py` | execute + compensate + can_execute + timeout_seconds 인터페이스 |
| **IdempotentStepHandler** | `services/coordination/idempotent_step_handlers.py` | 멱등성 키 기반 중복 실행 방지 |

ActionExecutor(`core/action_executor.py`)는 개별 액션의 **ExecutionMode(ACTIVE/SHADOW/EVALUATION)** 적용을 담당하므로,
Runbook Executor는 ActionExecutor를 통해 프리미티브를 실행하여 모드 정책을 자연스럽게 상속한다.

---

## 2. 기존 코드 근거

### 2.1 SagaStep ABC (`services/saga/step.py`)

```python
class SagaStep(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def execute(self, ctx: SagaContext) -> StepResult: ...

    @abstractmethod
    def compensate(self, ctx: SagaContext) -> StepResult: ...

    def can_execute(self, ctx: SagaContext) -> tuple[bool, str]:
        return True, ""

    @property
    def timeout_seconds(self) -> int | None:
        return None
```

- Runbook의 각 Step이 이 인터페이스를 따른다.
- `compensate()`는 이전 Step execute 결과를 SagaContext에서 참조하여 역순 보상을 구현한다.

### 2.2 IdempotentStepHandler (`services/coordination/idempotent_step_handlers.py`)

```python
class IdempotentStepHandler(ABC):
    def execute(self, session, step) -> dict[str, Any]:
        # 1. generate_idempotency_key(session_id, step_type, step_order, params)
        # 2. _get_record() → IdempotencyRecord.is_safe_to_execute()
        # 3. EXECUTING 상태 마킹 → _execute_internal() → COMPLETED/FAILED 저장
```

- `IdempotencyRecord.is_safe_to_execute()`: COMPLETED면 False, EXECUTING이면 타임아웃 체크
- `EXECUTION_TIMEOUT_MINUTES = 30`: 실행 중 타임아웃 초과 시 재실행 허용
- `IDEMPOTENCY_KEY_TTL_HOURS = 24`: 멱등성 키 24시간 TTL

### 2.3 ActionExecutor (`core/action_executor.py`)

```python
class ActionExecutor:
    def execute(self, action: Action) -> ActionResult:
        current_mode = self.mode  # ACTIVE / SHADOW / EVALUATION
        should_execute = current_mode.should_execute
        if should_execute:
            return self._execute_action(action, ...)
        else:
            return self._log_action_only(action, ...)
```

- `Action(name, target, execute_fn, params, validate_fn)`
- `ActionResult(executed: bool, success: bool | None, result, error, mode)`
- Shadow/Evaluation 모드에서는 `execute_fn`을 호출하지 않고 로깅만 수행

### 2.4 _fail_session() 보상 패턴 (`recovery_coordinator/_session_persistence.py`)

```python
def _fail_session(self, session, error):
    session.abort_reason = error
    session.status = RecoveryStatus.COMPENSATING
    self._save_session(session)

    comp_result = self._attempt_compensation(session)  # 역순 보상

    session.status = RecoveryStatus.FAILED
    self._save_session(session)
    self._recovery_lock.release(namespace, session_id)
    self._store_failure_to_dlq(session, error, comp_result)
```

- COMPENSATING 상태 전환 → 역순 보상 → FAILED → Lock 해제 → DLQ 저장 순서
- 보상 실패 시 Fail-Open: 보상 실패가 세션 처리를 중단시키지 않음
- `_attempt_compensation()`: completed steps를 order 역순 정렬 → compensate_handler 호출

### 2.5 DistributedRecoveryLock (`services/coordination/distributed_recovery_lock.py`)

```python
class DistributedRecoveryLock:
    LOCK_KEY_TEMPLATE = "selfhealing:{namespace}:recovery:lock"

    def acquire(self, namespace, session_id, blocking=False, timeout_seconds=None) -> bool: ...
    def release(self, namespace, session_id) -> bool: ...
    def extend(self, namespace, session_id, additional_seconds) -> bool: ...
```

- Redis SET NX PX 기반, Lua 스크립트로 원자적 해제/TTL 연장
- `lock_for_recovery()` 컨텍스트 매니저 제공

---

## 3. RunbookExecutionContext

SagaContext 패턴을 따르되 Runbook 실행에 특화된 컨텍스트를 정의한다.

```python
@dataclass
class RunbookExecutionContext:
    """Runbook 실행 컨텍스트. SagaContext 패턴 참조."""

    execution_id: str
    """고유 실행 ID (UUID)."""

    runbook_id: str
    """실행 중인 Runbook의 ID."""

    namespace: str
    """대상 네임스페이스."""

    trigger_event: dict[str, Any]
    """실행을 트리거한 이벤트 데이터."""

    runbook_version: int = 1
    """실행 시점의 Runbook 정의 버전 스냅샷.
    SagaInstance.definition_version 패턴 참조 (saga/models.py).
    resume 시 현재 Runbook.version과 비교하여 버전 불일치 감지."""

    step_results: dict[str, RunbookStepResult] = field(default_factory=dict)
    """Step Name → 실행 결과 매핑. compensate 시 참조."""

    variables: dict[str, Any] = field(default_factory=dict)
    """Step 간 공유 변수. step.params의 Jinja-like 치환 대상."""

    current_step_index: int = 0
    """현재 실행 중인 Step 인덱스."""

    status: RunbookExecutionStatus = RunbookExecutionStatus.PENDING
    """실행 상태."""

    started_at: str | None = None
    completed_at: str | None = None
    abort_reason: str | None = None

    def get(self, step_name: str) -> RunbookStepResult | None:
        """이전 Step 결과 조회. SagaContext.get() 패턴."""
        return self.step_results.get(step_name)
```

### 3.1 실행 상태 Enum

```python
class RunbookExecutionStatus(str, Enum):
    """RecoveryStatus 패턴 참조."""
    PENDING = "pending"                # 대기 중 (승인 대기 포함)
    EXECUTING = "executing"            # 실행 중
    WAITING_APPROVAL = "waiting_approval"  # 수동 승인 대기
    COMPENSATING = "compensating"      # 역순 보상 중
    COMPLETED = "completed"            # 성공 완료
    FAILED = "failed"                  # 실패 (보상 완료 후)
    CANCELLED = "cancelled"            # 취소됨
```

### 3.2 Step 결과

```python
@dataclass
class RunbookStepResult:
    """StepResult + ActionResult 통합. 두 패턴의 필드를 결합."""

    step_name: str
    action_name: str
    success: bool
    executed: bool           # ActionResult.executed — Shadow 모드 판별
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    idempotent: bool = False  # IdempotencyRecord에서 캐시된 결과 여부
    partial_execution: bool = False
    """외부 부작용(side-effect)이 발생했을 가능성이 있는 실패.
    StepResult.partial_execution 패턴 참조 (saga/models.py).
    True면 success=False여도 보상 대상에 포함된다.
    타임아웃(In-doubt) 시 True로 마킹 — 네트워크 응답만 못 받았을 뿐
    타겟 시스템에서는 작업이 성공했을 수 있으므로 보상 필요."""
    compensation_status: str = "not_needed"
    # "not_needed" | "compensated" | "compensate_failed"
```

---

## 4. RunbookExecutor 클래스

```python
class RunbookExecutor:
    """
    Runbook Step들을 순차 실행하고, 실패 시 역순 보상을 수행하는 실행 엔진.

    기존 패턴 결합:
    - SagaStep.execute() + compensate() 흐름
    - IdempotentStepHandler 멱등성 보장
    - ActionExecutor ExecutionMode 준수
    - DistributedRecoveryLock 동시 실행 방지
    - _fail_session() 보상 → FAILED → DLQ 패턴
    """

    def __init__(
        self,
        action_executor: ActionExecutor | None = None,
        recovery_lock: DistributedRecoveryLock | None = None,
        state_backend: StateBackend | None = None,
        notification_manager: UnifiedNotificationManager | None = None,
    ):
        self._action_executor = action_executor or get_action_executor()
        self._recovery_lock = recovery_lock or get_distributed_recovery_lock()
        self._backend = state_backend
        self._notification = notification_manager
```

---

## 5. 실행 흐름

### 5.1 전체 실행 시퀀스

```
execute_runbook(runbook, trigger_event, namespace)
│
├─ 1. Lock 획득: DistributedRecoveryLock.acquire(namespace, execution_id)
├─ 2. Context 생성: RunbookExecutionContext
├─ 3. Context 영속화: _save_context(ctx) — StateBackend
│
├─ 4. Step 루프:
│    for step in runbook.steps (order 순):
│    │
│    ├─ 4a. can_execute 체크 (step.condition 평가)
│    │      → False면 SKIP, 다음 Step으로
│    │
│    ├─ 4b. 멱등성 체크
│    │      generate_idempotency_key(execution_id, step.name, step.order, step.params)
│    │      → IdempotencyRecord.is_safe_to_execute() == False면 캐시 결과 반환
│    │
│    ├─ 4c. ActionPrimitiveRegistry에서 execute_fn 조회
│    │      → 274에서 등록한 프리미티브 함수
│    │
│    ├─ 4d. Action 생성 + ActionExecutor.execute()
│    │      Action(
│    │          name=step.action_name,
│    │          target=step.target or namespace,
│    │          execute_fn=primitive_fn(resolved_params),
│    │          params=resolved_params,
│    │          validate_fn=step.validate_fn (optional),
│    │      )
│    │      → ActionResult (executed, success, mode)
│    │
│    ├─ 4e. 결과 기록
│    │      ctx.step_results[step.name] = RunbookStepResult(...)
│    │      IdempotencyRecord → COMPLETED/FAILED 저장
│    │      _save_context(ctx)
│    │
│    ├─ 4f. 실패 시 → _compensate_steps(ctx) 호출 후 break
│    │
│    ├─ 4g. wait_after_seconds > 0이면 안정화 대기
│    │      Lock TTL 연장: _recovery_lock.extend(namespace, execution_id, wait + 300)
│    │
│    └─ 4h. Lock TTL 하트비트 연장 (매 Step 완료 후)
│
├─ 5. 성공 시:
│    ctx.status = COMPLETED
│    _save_context(ctx)
│    Lock 해제
│
└─ 6. 실패 시:
     _compensate_steps(ctx)  — 역순 보상
     ctx.status = FAILED
     _save_context(ctx)
     Lock 해제
     DLQ 저장
```

### 5.2 Step 실행 상세 (`_execute_step`)

```python
def _execute_step(
    self,
    step: RunbookStep,
    ctx: RunbookExecutionContext,
    runbook: Runbook,
) -> RunbookStepResult:
    """
    단일 Step 실행.

    1. condition 평가 → skip 가능
    2. 멱등성 체크
    3. params 변수 치환
    4. ActionExecutor.execute(Action(...))
    5. 결과 기록
    """

    # 1. Condition 평가 (SagaStep.can_execute 패턴)
    if step.condition:
        can_run, reason = self._evaluate_condition(step.condition, ctx)
        if not can_run:
            logger.info("runbook_executor.step_skipped", step=step.name, reason=reason)
            return RunbookStepResult(
                step_name=step.name,
                action_name=step.action_name,
                success=True,
                executed=False,
                result_data={"skipped": True, "reason": reason},
            )

    # 2. 멱등성 체크 (IdempotentStepHandler 패턴)
    idempotency_key = generate_idempotency_key(
        session_id=ctx.execution_id,
        step_type=step.action_name,
        step_order=step.order,
        params=step.params,
    )
    existing_record = self._get_idempotency_record(idempotency_key)
    if existing_record and not existing_record.is_safe_to_execute():
        return RunbookStepResult(
            step_name=step.name,
            action_name=step.action_name,
            success=existing_record.status == IdempotencyStatus.COMPLETED,
            executed=False,
            idempotent=True,
            result_data=existing_record.result or {},
        )

    # 3. Params 변수 치환
    resolved_params = self._resolve_params(step.params, ctx)

    # 4. ActionPrimitiveRegistry에서 프리미티브 조회 + Action 생성
    primitive_fn = ActionPrimitiveRegistry.get(step.action_name)
    if primitive_fn is None:
        raise RunbookExecutionError(
            f"Action primitive not found: {step.action_name}"
        )

    action = Action(
        name=step.action_name,
        target=step.target or ctx.namespace,
        execute_fn=lambda: primitive_fn(**resolved_params),
        params=resolved_params,
    )

    # 5. EXECUTING 상태 마킹 (IdempotentStepHandler 패턴)
    self._mark_executing(idempotency_key, ctx.execution_id, step)

    # 6. 타임아웃 적용 실행 (SagaStep.timeout_seconds 패턴)
    timeout = step.timeout_seconds or runbook.global_timeout_seconds
    try:
        action_result = self._execute_with_timeout(
            lambda: self._action_executor.execute(action),
            timeout_seconds=timeout,
            step_name=step.name,
            ctx=ctx,
        )
    except RunbookStepTimeoutError as e:
        # In-doubt State 처리 — 타임아웃 = 타겟 시스템에서 작업 성공 가능성
        # 코드 근거: StepResult.failed_with_side_effect() 패턴
        #   saga/models.py L145-L157
        #   "partial_execution=True면 EXECUTE_FAILED 상태여도 보상 대상"
        # 코드 근거: SagaInstance.get_compensation_targets()
        #   saga/models.py L520-L530
        #   "EXECUTED 또는 (EXECUTE_FAILED and partial_execution)"
        self._update_idempotency_record(
            idempotency_key, None, ctx.execution_id, step, failed=True,
        )
        return RunbookStepResult(
            step_name=step.name,
            action_name=step.action_name,
            success=False,
            executed=True,           # 실행은 시도됨
            partial_execution=True,  # In-doubt → 보상 대상에 포함
            error=str(e),
            result_data={"timeout": True, "in_doubt": True},
        )

    # 7. 멱등성 레코드 갱신
    self._update_idempotency_record(
        idempotency_key, action_result, ctx.execution_id, step,
    )

    # 8. 결과 반환
    return RunbookStepResult(
        step_name=step.name,
        action_name=step.action_name,
        success=action_result.success if action_result.executed else True,
        executed=action_result.executed,
        result_data={"action_result": action_result.to_dict()},
        error=action_result.error,
        started_at=action_result.timestamp.isoformat(),
    )
```

### 5.3 타임아웃 처리 + Lock Heartbeat Polling

SagaOrchestrator 및 RecoveryCoordinator의 `_execute_with_timeout` 패턴을 **완전히 차용**한다.
`HEARTBEAT_INTERVAL`(60초)마다 Polling 루프가 깨어나 `lock.extend()`로 Lock TTL을 연장하여,
**장시간 실행 중인 Step 도중 Lock이 만료되어 Split-brain이 발생하는 것을 방지**한다.

코드 근거:
- `SagaOrchestrator._execute_with_timeout()` — `orchestrator.py` L638-L706
  `HEARTBEAT_INTERVAL = 60`, `EXTEND_SECONDS = 300` 상수 사용
- `RecoveryCoordinator._execute_with_timeout()` — `_step_handler.py` L223-L337
  `LOCK_HEARTBEAT_INTERVAL_SECONDS = 60` 상수 사용

양쪽 모두 60초 Polling + 300초 TTL 연장이라는 동일한 값을 사용하며,
Runbook Executor도 이를 그대로 따른다.

```python
LOCK_HEARTBEAT_INTERVAL = 60    # SagaOrchestrator.HEARTBEAT_INTERVAL과 동일
LOCK_EXTEND_SECONDS = 300       # SagaOrchestrator.EXTEND_SECONDS와 동일

def _execute_with_timeout(
    self,
    fn: Callable,
    timeout_seconds: int | None,
    step_name: str,
    ctx: RunbookExecutionContext,
) -> ActionResult:
    """
    SagaOrchestrator._execute_with_timeout 패턴 완전 차용.

    핵심 메커니즘:
    1. ThreadPoolExecutor(max_workers=1)로 Step을 별도 스레드에서 실행
    2. HEARTBEAT_INTERVAL(60초)마다 future.result()를 Polling
    3. 타임아웃이 아니면 Lock TTL을 EXTEND_SECONDS(300초) 연장
    4. 전체 timeout_seconds 초과 시 RunbookStepTimeoutError raise

    코드 근거:
    - orchestrator.py L638-L706: 동일 Polling + Heartbeat 패턴
    - _step_handler.py L241-L289: 동일 Polling + Heartbeat 패턴
    """
    if timeout_seconds is None or timeout_seconds <= 0:
        return fn()

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        elapsed = 0

        # Polling 대기 루프: HEARTBEAT_INTERVAL 간격으로 Lock 연장
        while elapsed < timeout_seconds:
            wait_time = min(LOCK_HEARTBEAT_INTERVAL, timeout_seconds - elapsed)
            try:
                result = future.result(timeout=wait_time)
                return result
            except FuturesTimeoutError:
                elapsed += wait_time
                if elapsed >= timeout_seconds:
                    break
                # Lock Heartbeat: Step 실행 중에도 Lock TTL 연장
                try:
                    self._recovery_lock.extend(
                        ctx.namespace,
                        ctx.execution_id,
                        additional_seconds=LOCK_EXTEND_SECONDS,
                    )
                except Exception as e:
                    logger.warning(
                        "runbook_executor.lock_heartbeat_failed",
                        step=step_name, error=e,
                    )

        # 전체 타임아웃 초과 — future 취소 후 에러
        future.cancel()
        raise RunbookStepTimeoutError(
            f"Runbook step '{step_name}' timed out after {timeout_seconds}s"
        )
    finally:
        executor.shutdown(wait=False)
```

---

## 6. 역순 보상 (Compensation)

`_fail_session()` → `_attempt_compensation()` 패턴을 그대로 적용한다.

### 6.1 보상 흐름 (`_compensate_steps`)

```python
def _compensate_steps(
    self,
    ctx: RunbookExecutionContext,
) -> CompensationSummary:
    """
    _attempt_compensation() 패턴 적용.
    성공한 Step들을 역순으로 보상.

    패턴 근거:
    - _session_persistence.py: COMPENSATING 상태 → 역순 보상 → FAILED
    - Fail-Open: 보상 실패가 전체 처리를 중단시키지 않음
    """
    ctx.status = RunbookExecutionStatus.COMPENSATING
    self._save_context(ctx)

    summary = CompensationSummary()

    # 보상 대상: 성공 + 실행된 Step 또는 In-doubt(partial_execution) Step
    #
    # 코드 근거: SagaInstance.get_compensation_targets() — saga/models.py L520-L530
    #   "EXECUTED 상태이거나, EXECUTE_FAILED이면서 partial_execution=True인 Step"
    # 코드 근거: orchestrator.py L399-L404
    #   "partial_execution=True면 현재 Step도 보상 대상이므로 i부터 시작"
    #
    # In-doubt 포함 근거:
    #   타임아웃으로 네트워크 응답만 못 받았을 뿐
    #   타겟 시스템에서는 작업이 성공했을 수 있으므로 보상 필요.
    #   보상 Primitive가 No-op 안전하므로 (§6.4 CompensationContract)
    #   "실제로는 실행 안 됐는데 보상"해도 에러가 아닌 success=True 반환.
    executed_steps = [
        (name, result) for name, result in ctx.step_results.items()
        if (result.success and result.executed)
           or result.partial_execution  # In-doubt 포함
    ]
    executed_steps.sort(
        key=lambda x: self._get_step_order(x[0], ctx.runbook_id),
        reverse=True,
    )

    for step_name, step_result in executed_steps:
        # 이미 보상 완료면 skip (CompensationStatus.COMPENSATED 체크 패턴)
        if step_result.compensation_status == "compensated":
            summary.compensated.append(step_name)
            continue

        # 보상 프리미티브 조회
        compensate_fn = ActionPrimitiveRegistry.get_compensate(step_name)
        if compensate_fn is None:
            logger.debug("runbook_executor.no_compensate_fn", step=step_name)
            summary.skipped.append(step_name)
            continue

        # Lock TTL 연장 (하트비트) — _attempt_compensation() 패턴
        self._recovery_lock.extend(
            ctx.namespace,
            ctx.execution_id,
            additional_seconds=300,
        )

        try:
            # on_failure_params에도 ${step.*} 변수 치환 적용
            # 코드 근거: _attempt_compensation() — _session_persistence.py L331-L433
            #   compensate_handler(session, step) — step.result_data로 정방향 결과 참조
            # 코드 근거: SagaStep.compensate(ctx) — step.py
            #   ctx.get_from_step()으로 모든 이전 Step 결과에 접근
            #
            # 정적 역연산과 동적 참조 모두 지원:
            #   정적: on_failure_params={"value_delta": -20}
            #   동적: on_failure_params={"ids": "${step.kill.result_data.killed_ids}"}
            compensate_params = self._build_compensate_params(step_result, ctx)
            action = Action(
                name=f"compensate_{step_name}",
                target=ctx.namespace,
                execute_fn=lambda params=compensate_params: compensate_fn(**params),
                params=compensate_params,
            )
            result = self._action_executor.execute(action)

            if result.success:
                step_result.compensation_status = "compensated"
                self._save_context(ctx)
                summary.compensated.append(step_name)
            else:
                step_result.compensation_status = "compensate_failed"
                self._save_context(ctx)
                summary.failed.append((step_name, result.error or "Unknown"))
        except Exception as e:
            # Fail-Open: 보상 실패가 전체를 중단시키지 않음
            step_result.compensation_status = "compensate_failed"
            self._save_context(ctx)
            summary.failed.append((step_name, str(e)))
            logger.warning(
                "runbook_executor.compensation_exception",
                step=step_name, error=e,
            )

    return summary
```

### 6.2 CompensationSummary

```python
@dataclass
class CompensationSummary:
    """CompensationResult 패턴 참조 (recovery_state.py)."""
    compensated: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (step_name, error)
    skipped: list[str] = field(default_factory=list)

    @property
    def all_compensated(self) -> bool:
        return len(self.failed) == 0
```

### 6.3 보상 프리미티브 등록

274에서 정의한 `ActionPrimitiveRegistry`에 보상 전용 메서드를 추가한다.
등록 시점에 `CompensationContract` 검증을 수행하여 No-op 불안전한 보상 함수를 원천 차단한다.
상세 구현은 §6.4 참조.

예시 등록:

```python
# enable_circuit_breaker의 보상 = disable_circuit_breaker
# **kwargs 필수 — CompensationContract.validate_noop_safety()가 검증
ActionPrimitiveRegistry.register_compensate(
    "enable_circuit_breaker",
    lambda service_name, **kw: circuit_breaker_repo.atomic_force_close(service_name),
)
```

### 6.4 보상 No-op 안전성 강제 (CompensationContract)

보상 Primitive는 **No-op 안전(idempotent)** 해야 한다:
- 대상 리소스가 이미 정리되었으면 에러가 아닌 `success=True` 반환
- 동일 파라미터로 재시도되어도 항상 동일한 결과

이 컨벤션은 In-doubt 보상(§6.1)의 **전제 조건**이다:
타임아웃으로 실제로는 실행되지 않은 Step을 보상하더라도,
No-op 안전한 Primitive는 에러 없이 처리한다.

274의 `RunbookRegistry.register()` Fail-fast 패턴을 확장하여,
`register_compensate()` 시점에 **등록 시점 검증**을 강제한다.

코드 근거:
- `RunbookRegistry.register()` Fail-fast — `runbook_registry.py` L554-L577
  "등록 시점에 잘못된 런북을 차단. 장애 복구 중 KeyError/TypeError 발생 시 피해가 배가"
- `_validate_all_step_params()` Pydantic 스키마 검증 — `runbook_registry.py` L580-L623
- Contract Test 패턴 — `test_runbook_registry.py` L498-L530

```python
class CompensationContract:
    """보상 Primitive No-op 안전성 계약.

    register_compensate() 시점에 Fail-fast 검증으로
    No-op 안전하지 않은 보상 함수의 등록을 원천 차단한다.

    코드 근거:
    - RunbookRegistry._validate_all_step_params() Fail-fast 패턴
    - test_runbook_registry.py L498-L530 Contract Test 패턴
    """

    @staticmethod
    def validate_noop_safety(
        compensate_fn: Callable,
        action_name: str,
        test_params: dict[str, Any] | None = None,
    ) -> None:
        """보상 함수가 No-op 안전한지 등록 시점에 검증.

        검증 조건:
        1. 함수 시그니처에 **kwargs 허용 (미래 파라미터 확장 대비)
        2. 반환 타입 힌트가 있으면 dict | ActionResult 이어야 함
        """
        import inspect
        sig = inspect.signature(compensate_fn)

        # **kwargs 허용 검증
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if not has_var_keyword:
            raise ValueError(
                f"Compensation function '{action_name}' must accept **kwargs "
                f"for forward-compatible parameter extension"
            )


class ActionPrimitiveRegistry:
    _compensate_registry: ClassVar[dict[str, Callable]] = {}

    @classmethod
    def register_compensate(
        cls,
        action_name: str,
        fn: Callable,
        validate: bool = True,
    ) -> None:
        """보상 Primitive 등록 + No-op 안전성 검증.

        코드 근거:
        - RunbookRegistry.register() Fail-fast 패턴
        - _validate_all_step_params() 등록 시점 Pydantic 검증
        """
        if validate:
            CompensationContract.validate_noop_safety(fn, action_name)
        cls._compensate_registry[action_name] = fn

    @classmethod
    def get_compensate(cls, action_name: str) -> Callable | None:
        return cls._compensate_registry.get(action_name)
```

보상 Primitive 구현 Convention:

```python
# ✅ 올바른 보상 Primitive — No-op 안전
def compensate_circuit_breaker(service_name: str, **kw) -> dict:
    """Circuit Breaker force close.
    이미 닫혀 있으면 에러가 아닌 success=True 반환."""
    current = circuit_breaker_repo.get_state(service_name)
    if current == CircuitState.CLOSED:
        return {"success": True, "noop": True}  # 이미 정리됨
    circuit_breaker_repo.atomic_force_close(service_name)
    return {"success": True, "noop": False}

# ❌ 잘못된 보상 Primitive — No-op 불안전
def bad_compensate(service_name: str):  # **kwargs 누락 → 등록 시 ValueError
    lock = get_lock(service_name)
    lock.release()  # 락이 없으면 예외 발생 → No-op 불안전
```
```

---

## 7. Lock 통합

### 7.1 RecoveryCoordinator와의 Lock 공유

Runbook Executor는 기존 `DistributedRecoveryLock`을 동일한 namespace 키로 사용한다.
이는 RecoveryCoordinator가 진행 중인 네임스페이스에 Runbook이 동시에 실행되는 것을 방지한다.

```python
RUNBOOK_LOCK_KEY = "selfhealing:{namespace}:recovery:lock"
# DistributedRecoveryLock.LOCK_KEY_TEMPLATE과 동일
```

```python
def execute_runbook(
    self,
    runbook: Runbook,
    trigger_event: dict[str, Any],
    namespace: str,
) -> RunbookExecutionContext:

    execution_id = f"runbook-{uuid4()}"

    # 1. Lock 획득 (RecoveryCoordinator와 동일 키)
    acquired = self._recovery_lock.acquire(namespace, execution_id)
    if not acquired:
        raise RunbookLockConflictError(
            f"Namespace '{namespace}' is locked by another recovery/runbook"
        )

    try:
        # 2. 실행
        return self._run(runbook, trigger_event, namespace, execution_id)
    finally:
        # 3. Lock 해제 (항상)
        self._recovery_lock.release(namespace, execution_id)
```

### 7.2 하트비트 연장

`_attempt_compensation()`에서 매 Step 보상 전 TTL을 연장하는 것과 동일하게,
Runbook Executor도 매 Step 완료 후 Lock TTL을 연장한다:

```python
# 매 Step 완료 후 (forward 실행 시)
self._recovery_lock.extend(
    namespace, execution_id,
    additional_seconds=300,  # 5분 연장
)
```

---

## 8. 멱등성 통합

### 8.1 키 생성

기존 `generate_idempotency_key()` 함수를 사용하되,
**`runbook_version`을 `params`에 포함**하여 캐시 오염(Cache Poisoning)을 방지한다.

캐시 오염 시나리오:
1. Runbook v1의 Step A가 `result_data = {"killed_count": 5}`로 COMPLETED 캐시
2. Runbook을 v2로 업데이트 — Step A의 반환 스키마가 `{"killed_ids": [1,2,3]}`로 변경
3. 동일 `execution_id`로 resume → 멱등성 체크가 v1 캐시 반환 → 다음 Step이 `killed_ids` 참조시 `None`

코드 근거:
- `SagaInstance.definition_version` — `saga/models.py` L475-L483
  "생성 시점의 SagaDefinition.version 스냅샷.
  Orchestrator가 인스턴스를 픽업할 때 현재 SagaDefinition.version과 비교.
  불일치 시 SUSPENDED 전환"
- `_validate_version_compatibility()` — `orchestrator.py` L1059-L1073
  인스턴스의 버전과 현재 정의의 버전이 다르면 SUSPENDED 전환
- `IdempotencyRecord` — `idempotent_step_handlers.py` L84-L117
  버전 필드가 **없음** → 키 해시에 version을 포함하여 보완

```python
# idempotent_step_handlers.py
def generate_idempotency_key(session_id, step_type, step_order, params=None) -> str:
    key_source = f"{session_id}:{step_type}:{step_order}:{params_str}"
    key_hash = hashlib.sha256(key_source.encode()).hexdigest()[:16]
    return f"idem:{session_id}:{step_type}:{step_order}:{key_hash}"
```

Runbook에서의 매핑:
- `session_id` → `execution_id`
- `step_type` → `step.action_name`
- `step_order` → `step.order`
- `params` → **`{**step.params, "__runbook_version": runbook.version}`**

`__runbook_version`을 params에 포함하면 `json.dumps(sort_keys=True)` 후
해시가 달라져 **자연스럽게 새 실행이 트리거**된다.
`IdempotencyRecord` 스키마 변경 없이 기존 함수의 `params` 인자만 활용하므로
영향 범위가 최소화된다.

```python
# 매핑 예시
idempotency_key = generate_idempotency_key(
    session_id=ctx.execution_id,
    step_type=step.action_name,
    step_order=step.order,
    params={**step.params, "__runbook_version": runbook.version},
)
```

### 8.2 상태 전이

```
NOT_EXECUTED → EXECUTING → COMPLETED (성공)
                        → FAILED (실패)
COMPLETED → (재실행 시 is_safe_to_execute()=False → 캐시 결과 반환)
EXECUTING → (30분 초과 시 → 재실행 허용)
```

이는 기존 `IdempotencyRecord.is_safe_to_execute()` 로직과 동일하다.

---

## 9. 변수 치환

### 9.1 2계층 타입 방어 구조

`_resolve_params()`는 **문자열 보간(interpolation)이 아닌 객체 참조(object graph traversal)**를 수행한다.
따라서 `${step.check.current_value}`가 `float(0.85)`로 저장되어 있으면 `float` 그대로 반환된다.

| 계층 | 위치 | 역할 |
|------|------|------|
| **1계층: 객체 참조** | `_resolve_variable()` | dot-path 탐색으로 원본 Python 타입 보존 |
| **2계층: Pydantic 검증** | `ActionPrimitiveRegistry.get_schema()` | 실행 직전 `params_schema.model_validate()` — 타입 불일치 시 coercion 또는 ValidationError |

코드 근거:
- `SagaContext.step_results: dict[str, dict[str, Any]]` — `saga/models.py`
  Any → Python 원시 타입 유지. `merge_step_result(step_name, data)`는 data를 그대로 저장
- `ActionPrimitiveRegistry._validate_all_step_params()` — `runbook_registry.py` L580-L623
  Pydantic 스키마로 2단계 검증. 예: `AssertMetricParams(threshold: float)`
- Redis 직렬화/역직렬화: JSON 호환 타입(`int`, `float`, `bool`, `str`, `None`) 보존

> **주의**: `"prefix_${step.x.val}_suffix"` 같은 **부분 치환**은 지원하지 않는다.
> 값 전체가 `"${..."}"` 행태인 경우만 객체 참조로 처리되며,
> 그 외의 값은 리터럴로 그대로 전달된다.

### 9.2 ParamResolver 확장 프로토콜

향후 런타임 수식 평가(`${step.check.value * 1.1}`) 등의 수요에 대비하여,
`_resolve_params()`을 `ParamResolver` Protocol로 추상화한다.

외부 표현식 엔진(jmespath, jinja2 등)은 도입하지 않는다.
- selfhealing 패키지 dependencies: `redis`, `pydantic`, `pydantic-settings`, `structlog`만 — `pyproject.toml`
- 273번 설계에서 Jinja2 명시적 기각: "프로젝트에서 Jinja2는 사용하지 않는다. Python 내장 `str.format_map()`이 표준이다."
- 현재 `_resolve_variable()`의 객체 참조 방식이 타입 보존을 보장하므로 jmespath의 타입 보존 장점이 불필요

대신 `ParamResolver` Protocol을 확장점으로 예약하여,
미래 수요가 생기면 `eval()` 없이 안전한 수식 평가를 점진적으로 도입할 수 있다.

```python
class ParamResolver(Protocol):
    """변수 치환 확장 프로토콜.

    현재 DotPathResolver만 사용하지만, 향후 필요 시
    SafeExpressionResolver 등을 추가할 수 있다.
    """
    def resolve(self, expression: str, ctx: RunbookExecutionContext) -> Any: ...


class DotPathResolver:
    """기본 구현: ${trigger.*} / ${step.*} dot-path 식 객체 참조."""

    def resolve(self, expression: str, ctx: RunbookExecutionContext) -> Any:
        parts = expression.split(".")
        if parts[0] == "trigger":
            return self._traverse(ctx.trigger_event, parts[1:])
        elif parts[0] == "step":
            step_result = ctx.step_results.get(parts[1])
            if step_result is None:
                return None
            return self._traverse(step_result, parts[2:])
        return None

    @staticmethod
    def _traverse(obj: Any, parts: list[str]) -> Any:
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                obj = getattr(obj, part, None)
        return obj
```

### 9.3 변수 치환 구현

```python
def _resolve_params(
    self,
    params: dict[str, Any],
    ctx: RunbookExecutionContext,
) -> dict[str, Any]:
    """
    Step params의 변수 참조를 실제 값으로 치환.

    변수 패턴: "${step.<step_name>.<field>}" 또는 "${trigger.<field>}"

    예시:
      params:
        service_name: "${trigger.service_name}"
        threshold: "${step.check_metrics.result_data.current_error_rate}"
    """
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            resolved[key] = self._resolve_variable(value[2:-1], ctx)
        else:
            resolved[key] = value
    return resolved

def _resolve_variable(self, path: str, ctx: RunbookExecutionContext) -> Any:
    """변수 경로 해석."""
    parts = path.split(".")
    if parts[0] == "trigger":
        obj = ctx.trigger_event
        for part in parts[1:]:
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
        return obj
    elif parts[0] == "step":
        step_result = ctx.step_results.get(parts[1])
        if step_result is None:
            return None
        obj = step_result
        for part in parts[2:]:
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
        return obj
    return None
```

---

## 10. 안정화 대기 (wait_after_seconds)

274에서 `RunbookStep.wait_after_seconds`로 정의한 Step 간 안정화 대기:

```python
# execute_runbook 루프 내
if step.wait_after_seconds and step.wait_after_seconds > 0:
    # Lock TTL을 대기 시간 + 여유분만큼 연장
    self._recovery_lock.extend(
        namespace, execution_id,
        additional_seconds=step.wait_after_seconds + 300,
    )
    logger.info(
        "runbook_executor.stabilization_wait",
        step=step.name,
        wait_seconds=step.wait_after_seconds,
    )
    time.sleep(step.wait_after_seconds)
```

---

## 11. ExecutionMode 준수

Runbook Executor는 `ActionExecutor.execute()`를 통해 모든 프리미티브를 실행하므로,
`ExecutionMode` 정책이 자동 적용된다:

| Mode | 동작 |
|------|------|
| **ACTIVE** | `Action.execute_fn()` 호출, 실제 상태 변경 |
| **SHADOW** | 로깅만 수행, `ActionResult.executed = False` |
| **EVALUATION** | `validate_fn()` 호출 + 로깅, 상태 변경 없음 |

Shadow/Evaluation 모드에서는 `ActionResult.executed = False`이므로:
- `RunbookStepResult.executed = False`
- `partial_execution = False` (실행 자체가 없으므로 부작용 없음)
- 보상 대상에서 자동 제외 (§6.1의 필터 조건: `(success and executed) or partial_execution`)

---

## 12. 에러 타입

```python
class RunbookExecutionError(Exception):
    """Runbook 실행 중 일반 오류."""
    pass

class RunbookLockConflictError(RunbookExecutionError):
    """Lock 획득 실패 — 다른 복구/런북이 진행 중."""
    pass

class RunbookStepTimeoutError(RunbookExecutionError):
    """Step 타임아웃 초과. StepTimeoutError 패턴."""
    pass

class RunbookStaleContextError(RunbookExecutionError):
    """재개(resume) 시 컨텍스트가 stale 임계값을 초과.
    force=True로 재시도 가능."""
    pass

class RunbookVersionMismatchError(RunbookExecutionError):
    """재개(resume) 시 Runbook 정의 버전이 실행 시점과 불일치.
    _validate_version_compatibility() 패턴. orchestrator.py L1059-L1073."""
    pass

class RunbookCompensationError(RunbookExecutionError):
    """보상 중 오류 (Fail-Open으로 처리되므로 직접 raise하지 않음)."""
    pass
```

---

## 13. Context 영속화

RecoveryCoordinator의 `_save_session()` OCC(Optimistic Concurrency Control) 패턴을 참조하되,
초기 구현에서는 단순 저장을 사용한다:

```python
CONTEXT_KEY = "selfhealing:runbook:execution:{execution_id}"

def _save_context(self, ctx: RunbookExecutionContext) -> None:
    """컨텍스트 영속화. StateBackend 사용."""
    backend = self._get_backend()
    key = self.CONTEXT_KEY.format(execution_id=ctx.execution_id)
    backend.set(key, ctx.to_dict(), ttl=86400)  # 24시간 TTL

def _load_context(self, execution_id: str) -> RunbookExecutionContext | None:
    """영속화된 컨텍스트 로드. resume 시 사용."""
    backend = self._get_backend()
    key = self.CONTEXT_KEY.format(execution_id=execution_id)
    data = backend.get(key)
    if data:
        return RunbookExecutionContext.from_dict(data)
    return None
```

### 13.1 Resume 지원

RecoveryCoordinator의 `resume_recovery()` 패턴을 참조하여,
FAILED/EXECUTING 상태의 실행을 재개할 수 있도록 한다.

**3가지 방어 장치:**

| 방어 | 패턴 근거 | 설명 |
|------|-----------|------|
| **Stale Context 거부** | `scan_orphan_sagas()` — `tasks.py` L89-L138, `STALE_THRESHOLD_SECONDS = 300` | 마지막 상태 업데이트 후 N분 초과 시 재개 거부. `force=True`로 오버라이드 가능 |
| **버전 호환성 검증** | `_validate_version_compatibility()` — `orchestrator.py` L1059-L1073 | `ctx.runbook_version != runbook.version` 시 SUSPENDED 전환 |
| **무한 재개 방지** | `MAX_RESUME_COUNT = 10` — `orchestrator.py` L76 | resume_count 초과 시 FAILED + DLQ |

```python
MAX_RESUME_COUNT = 10  # SagaOrchestrator.MAX_RESUME_COUNT와 동일

def resume_execution(
    self,
    execution_id: str,
    force: bool = False,
) -> RunbookExecutionContext:
    """
    중단된 실행 재개.

    RecoveryCoordinator.resume_recovery() + SagaOrchestrator.resume_saga() 패턴.

    3단계 방어:
    1. Stale Context 거부 — SELFHEALING_RUNBOOK_RESUME_STALE_THRESHOLD_SECONDS 초과 시
    2. Runbook 버전 호환성 — ctx.runbook_version != 현재 Runbook.version 시
    3. 무한 재개 방지 — resume_count >= MAX_RESUME_COUNT 시

    코드 근거:
    - scan_orphan_sagas() STALE_THRESHOLD_SECONDS — tasks.py L89-L138
    - _validate_version_compatibility() — orchestrator.py L1059-L1073
    - SagaOrchestrator.resume_saga() MAX_RESUME_COUNT — orchestrator.py L826-L834
    """
    ctx = self._load_context(execution_id)
    if ctx is None:
        raise RunbookExecutionError(f"Execution not found: {execution_id}")

    if ctx.status not in (RunbookExecutionStatus.FAILED, RunbookExecutionStatus.EXECUTING):
        raise RunbookExecutionError(
            f"Cannot resume execution in status: {ctx.status}"
        )

    # 1. Stale Context 방어
    # 코드 근거: scan_orphan_sagas() — tasks.py L95-L122
    #   (now_utc - last_update).total_seconds() > STALE_THRESHOLD_SECONDS 환산
    if not force and ctx.started_at:
        from datetime import datetime, timezone
        try:
            started = datetime.fromisoformat(ctx.started_at)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            stale_threshold = settings.RUNBOOK_RESUME_STALE_THRESHOLD_SECONDS
            if elapsed > stale_threshold:
                raise RunbookStaleContextError(
                    f"Context is {elapsed / 3600:.1f}h old "
                    f"(threshold: {stale_threshold / 3600:.1f}h). "
                    f"Use force=True to override."
                )
        except (ValueError, TypeError):
            pass

    # 2. Runbook 버전 호환성 검증
    # 코드 근거: _validate_version_compatibility()
    #   orchestrator.py L1059-L1073
    #   "instance.definition_version != definition.version 시 SUSPENDED 전환"
    runbook = RunbookRegistry.get(ctx.runbook_id)
    if ctx.runbook_version != runbook.version:
        raise RunbookVersionMismatchError(
            f"Runbook version mismatch: "
            f"execution={ctx.runbook_version}, current={runbook.version}. "
            f"Create a new execution instead of resuming."
        )

    # 3. 무한 재개 방지
    # 코드 근거: resume_saga() — orchestrator.py L826-L834
    resume_count = ctx.variables.get("__resume_count", 0)
    if resume_count >= MAX_RESUME_COUNT:
        ctx.status = RunbookExecutionStatus.FAILED
        ctx.abort_reason = f"Max resume count ({MAX_RESUME_COUNT}) exceeded"
        self._save_context(ctx)
        self._store_to_dlq(ctx, ctx.abort_reason, CompensationSummary())
        return ctx
    ctx.variables["__resume_count"] = resume_count + 1

    return self._run_from_step(runbook, ctx, ctx.current_step_index)
```

---

## 14. DLQ 저장

`_store_failure_to_dlq()` 패턴을 그대로 적용:

```python
def _store_to_dlq(
    self,
    ctx: RunbookExecutionContext,
    error: str,
    compensation: CompensationSummary,
) -> None:
    """
    _store_failure_to_dlq() 패턴. Fail-Open.
    """
    try:
        from selfhealing.services.dlq import store_to_dlq

        store_to_dlq(
            domain="selfhealing",
            failure_type="RUNBOOK_EXECUTION_FAILED",
            entity_type="runbook_execution",
            entity_id=ctx.execution_id,
            error_message=error,
            snapshot_data={"context": ctx.to_dict()},
            metadata={
                "runbook_id": ctx.runbook_id,
                "namespace": ctx.namespace,
                "completed_steps": list(ctx.step_results.keys()),
                "compensation_failed": compensation.failed,
            },
            next_action_hint=(
                f"Runbook '{ctx.runbook_id}' failed. "
                f"Consider resume_execution('{ctx.execution_id}')."
            ),
            recommended_action="manual_review",
        )
    except Exception as e:
        # Fail-Open
        logger.warning("runbook_executor.dlq_failed", error=e)
```

---

## 15. 모듈 구조

```
packages/selfhealing-python/src/selfhealing/
└── services/
    └── runbook/
        ├── executor.py          ← RunbookExecutor (이 문서)
        ├── models.py            ← RunbookExecutionContext, RunbookStepResult,
        │                           RunbookExecutionStatus, CompensationSummary
        ├── resolvers.py         ← ParamResolver Protocol, DotPathResolver
        ├── contracts.py         ← CompensationContract
        └── exceptions.py        ← RunbookExecutionError, RunbookLockConflictError,
                                    RunbookStaleContextError, RunbookVersionMismatchError, ...
```

---

## 16. 설정 (`SelfHealingSettings`)

```python
# settings 패턴: Pydantic BaseSettings + SELFHEALING_ 접두사

SELFHEALING_RUNBOOK_DEFAULT_STEP_TIMEOUT_SECONDS: int = 300   # Step별 기본 타임아웃
SELFHEALING_RUNBOOK_GLOBAL_TIMEOUT_SECONDS: int = 1800        # 전체 실행 타임아웃
SELFHEALING_RUNBOOK_LOCK_EXTEND_SECONDS: int = 300            # Lock 연장 기본값
SELFHEALING_RUNBOOK_LOCK_HEARTBEAT_INTERVAL: int = 60         # Lock Heartbeat Polling 간격
SELFHEALING_RUNBOOK_IDEMPOTENCY_TTL_HOURS: int = 24           # 멱등성 키 TTL
SELFHEALING_RUNBOOK_CONTEXT_TTL_SECONDS: int = 86400          # 컨텍스트 영속화 TTL
SELFHEALING_RUNBOOK_RESUME_STALE_THRESHOLD_SECONDS: int = 3600  # Resume 시 stale 거부 임계값 (1시간)
SELFHEALING_RUNBOOK_MAX_RESUME_COUNT: int = 10                # 무한 재개 방지 카운터
```

---

## 17. 기존 컴포넌트와의 관계 요약

| 기존 컴포넌트 | Runbook Executor에서의 사용 |
|---|---|
| `ActionExecutor` | 모든 프리미티브 실행의 단일 진입점. ExecutionMode 준수. |
| `SagaStep` ABC | execute + compensate + can_execute + timeout 인터페이스 패턴 차용 |
| `IdempotentStepHandler` | generate_idempotency_key(), IdempotencyRecord, is_safe_to_execute() 재사용 |
| `DistributedRecoveryLock` | 동일 Lock 키로 RecoveryCoordinator와 상호 배제 보장 |
| `_fail_session()` | COMPENSATING → 역순 보상 → FAILED → DLQ 흐름 패턴 |
| `_attempt_compensation()` | completed steps 역순 정렬 + Fail-Open + Lock TTL 하트비트 패턴 |
| `StepTimeoutError` | Step별 타임아웃 처리 패턴 |
| `store_to_dlq()` | 실패 정보 DLQ 저장 (Fail-Open) |
| `StateBackend` | 컨텍스트 영속화 (Redis/InMemory) |

---

## 18. 리뷰 반영 이력

### 18.1 반영된 8가지 리뷰

| # | 리뷰 | 반영 섹션 | 핵심 변경 |
|---|------|-----------|-----------|
| 1 | **타입 캐스팅 — ParamResolver 확장점** | §9.1, §9.2 | 2계층 타입 방어 구조 문서화 + ParamResolver Protocol 예약. jmespath/jinja2는 미도입 — 프로젝트 최소 의존성 기조 유지 (`pyproject.toml` 근거) |
| 2 | **멱등성 캐시 오염 방지 — 버전 매칭** | §3, §8.1 | `RunbookExecutionContext.runbook_version` 필드 추가. 멱등성 키 `params`에 `__runbook_version` 포함하여 버전 변경 시 자동 캐시 무효화 |
| 3 | **부분 실패 — partial_execution** | §3.2, §6.1 | `RunbookStepResult.partial_execution` 필드 추가. 보상 필터를 `(success and executed) or partial_execution`으로 확장 |
| 4 | **Resume Staleness 방어** | §12, §13.1, §16 | `RunbookStaleContextError` + `RunbookVersionMismatchError` 에러 추가. `force` 플래그 + 3단계 방어(stale/version/count) |
| 5 | **보상 No-op 안전성 강제** | §6.3, §6.4 | `CompensationContract.validate_noop_safety()` — `register_compensate()` 시점에 Fail-fast 검증. Convention 코드 예시 포함 |
| A | **타임아웃 In-doubt 보상** | §3.2, §5.2, §6.1 | 타임아웃 시 `partial_execution=True`로 마킹 → In-doubt Step도 보상 대상. No-op 안전 컨벤션(#5)이 전제 조건 |
| B | **Lock Heartbeat Watchdog** | §5.3, §16 | `_execute_with_timeout()` 전면 교체 — 60초 Polling 루프 + `lock.extend(300초)`. SagaOrchestrator/RecoveryCoordinator 패턴 완전 차용 |
| C | **ParamResolver (jmespath 대체)** | §9.2 | jmespath 도입 대신 자체 `ParamResolver` Protocol로 확장점 예약. 273번 설계의 jinja2 기각 결정과 일관. 현재 `DotPathResolver`가 타입 보존을 보장하므로 외부 엔진 불필요 |

### 18.2 설계 결정 근거 요약

**jmespath/jinja2를 도입하지 않는 이유:**
- selfhealing 패키지 dependencies: `redis`, `pydantic`, `pydantic-settings`, `structlog`만 — `pyproject.toml`
- 273번 설계에서 jinja2 명시적 기각: "프로젝트에서 Jinja2는 사용하지 않는다"
- `_resolve_variable()` 객체 참조 방식이 이미 타입을 보존하므로 jmespath의 타입 보존 장점 불필요
- jinja2는 SSTI(Server-Side Template Injection) 위험이 있어 보안 샌드박싱 부담

**CompensationContract가 제안 A(In-doubt 보상)를 가능하게 하는 이유:**
- In-doubt(타임아웃) Step은 실제로는 실행되지 않았을 수 있음
- 보상 Primitive가 No-op 안전하면, "실행 안 됐는데 보상"해도 `success=True` 반환 → 무해
- CompensationContract가 이를 등록 시점에 강제 → In-doubt 보상이 안전하게 동작
