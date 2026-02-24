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
    action_result = self._execute_with_timeout(
        lambda: self._action_executor.execute(action),
        timeout_seconds=timeout,
        step_name=step.name,
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

### 5.3 타임아웃 처리

RecoveryCoordinator의 `_execute_with_timeout` → `StepTimeoutError` 패턴을 재사용:

```python
def _execute_with_timeout(
    self,
    fn: Callable,
    timeout_seconds: int | None,
    step_name: str,
) -> ActionResult:
    """
    RecoveryCoordinator._execute_with_timeout 패턴.
    StepTimeoutError 발생 시 step 실패로 처리.
    """
    if timeout_seconds is None or timeout_seconds <= 0:
        return fn()

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            raise StepTimeoutError(
                f"Runbook step '{step_name}' timed out after {timeout_seconds}s"
            )
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

    # 성공한 Step만 역순 정렬 (order 내림차순)
    # _attempt_compensation()과 동일: completed steps만 보상 대상
    executed_steps = [
        (name, result) for name, result in ctx.step_results.items()
        if result.success and result.executed
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

274에서 정의한 `ActionPrimitiveRegistry`에 보상 전용 메서드를 추가:

```python
class ActionPrimitiveRegistry:
    _compensate_registry: ClassVar[dict[str, Callable]] = {}

    @classmethod
    def register_compensate(cls, step_name: str, fn: Callable) -> None:
        cls._compensate_registry[step_name] = fn

    @classmethod
    def get_compensate(cls, step_name: str) -> Callable | None:
        return cls._compensate_registry.get(step_name)
```

예시 등록:

```python
# enable_circuit_breaker의 보상 = disable_circuit_breaker
ActionPrimitiveRegistry.register_compensate(
    "enable_circuit_breaker",
    lambda service_name, **kw: circuit_breaker_repo.atomic_force_close(service_name),
)
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

기존 `generate_idempotency_key()` 함수를 그대로 사용:

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
- `params` → `step.params`

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

Step 간 데이터 전달을 위해 `RunbookExecutionContext.variables`에 이전 Step 결과를 주입하고,
다음 Step의 `params`에서 참조할 수 있도록 한다.

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
- 보상 대상에서 자동 제외 (§6.1의 `if result.success and result.executed` 조건)

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
FAILED/EXECUTING 상태의 실행을 재개할 수 있도록 한다:

```python
def resume_execution(self, execution_id: str) -> RunbookExecutionContext:
    """
    중단된 실행 재개.

    RecoveryCoordinator.resume_recovery() 패턴:
    - FAILED 상태 → current_step_index부터 재실행
    - EXECUTING 상태 → 멱등성 키로 중복 방지 후 이어서 실행
    """
    ctx = self._load_context(execution_id)
    if ctx is None:
        raise RunbookExecutionError(f"Execution not found: {execution_id}")

    if ctx.status not in (RunbookExecutionStatus.FAILED, RunbookExecutionStatus.EXECUTING):
        raise RunbookExecutionError(
            f"Cannot resume execution in status: {ctx.status}"
        )

    runbook = RunbookRegistry.get(ctx.runbook_id)
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
        └── exceptions.py        ← RunbookExecutionError, RunbookLockConflictError, ...
```

---

## 16. 설정 (`SelfHealingSettings`)

```python
# settings 패턴: Pydantic BaseSettings + SELFHEALING_ 접두사

SELFHEALING_RUNBOOK_DEFAULT_STEP_TIMEOUT_SECONDS: int = 300   # Step별 기본 타임아웃
SELFHEALING_RUNBOOK_GLOBAL_TIMEOUT_SECONDS: int = 1800        # 전체 실행 타임아웃
SELFHEALING_RUNBOOK_LOCK_EXTEND_SECONDS: int = 300            # Lock 연장 기본값
SELFHEALING_RUNBOOK_IDEMPOTENCY_TTL_HOURS: int = 24           # 멱등성 키 TTL
SELFHEALING_RUNBOOK_CONTEXT_TTL_SECONDS: int = 86400          # 컨텍스트 영속화 TTL
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
