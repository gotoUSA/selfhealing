# 246. Recovery Step 타임아웃 감시 (Step-Level Timeout Monitor)

> **Version**: 1.0.0
> **Created**: 2026-02-19
> **Status**: Approved
> **Parent**: [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
> **Related**: [244_COMPENSATE_SLOT_ADDITION.md](244_COMPENSATE_SLOT_ADDITION.md), [245_RECOVERY_DLQ_INTEGRATION.md](245_RECOVERY_DLQ_INTEGRATION.md)
> **Priority**: P2 — RecoveryCircuitBreaker가 부분 커버하지만 Step 단위 감시 부재

## 0. 요약

`RecoveryCoordinator.execute_next_step()`에서 **개별 Step 핸들러에 타임아웃을 적용**하고,
`RecoveryStep` 모델에 `timeout_seconds` 필드를 추가하여 **Step별 최대 실행 시간을 제어**한다.

---

## 1. 문제점 (AS-IS)

### 1.1 핸들러 호출에 타임아웃 없음

**파일**: `services/coordination/recovery_coordinator.py` L493-506

```python
# execute_next_step() 내부
step.started_at = now
step.status = RecoveryStatus.IN_PROGRESS
try:
    if idempotent_registry and idempotent_registry.has_handler(step.step_type):
        result = idempotent_registry.execute(session, step)
    else:
        handler = self._step_handlers.get(step.step_type)
        if not handler:
            raise ValueError(f"No handler for step type: {step.step_type}")
        result = handler(session, step)
        # ↑ handler가 hang하면 영원히 대기
```

`handler(session, step)` 호출에 **시간 제한이 전혀 없다.** 핸들러가 외부 서비스를 호출하거나 네트워크 타임아웃에 빠지면 **무한 대기** 상태가 된다.

### 1.2 RecoveryStep 모델에 timeout 필드 없음

**파일**: `services/coordination/recovery_state.py` L66-115

```python
@dataclass
class RecoveryStep:
    step_type: RecoveryStepType
    order: int
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    wait_after_seconds: int = 0          # "완료 후" 대기 시간 (타임아웃 아님)
    params: dict[str, Any] = field(...)
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    # timeout_seconds → 없음
```

`wait_after_seconds`는 "Step 성공 후 다음 Step 시작 전 안정화 대기"이지, "이 Step이 X초 안에 끝나야 한다"가 아니다.

### 1.3 기존 타임아웃 메커니즘들과의 갭

| 기존 메커니즘 | 위치 | 감시 대상 | Step 단위? |
|---|---|---|---|
| `RollbackService` 전체 루프 타임아웃 | `rollback/service.py` L260-262 | 롤백 핸들러 루프 전체 | ❌ 전체 루프 |
| `IdempotencyRecord.is_safe_to_execute()` | `idempotent_step_handlers.py` L118-137 | 좀비 실행 판단 (수동적) | ⚠️ 수동적 판단만 |
| `RecoveryCircuitBreaker` | `recovery_circuit_breaker.py` L176-213 | 시스템 전체 에러율 | ❌ 에러율 기반 |
| `DistributedRecoveryLock` TTL | `distributed_recovery_lock.py` | 락 자동 만료 (30분) | ❌ 세션 전체 |
| `EXECUTION_TIMEOUT_MINUTES = 30` | `idempotent_step_handlers.py` L48 | 멱등성 레코드 만료 | ❌ 능동 중단 아님 |

**갭**: 개별 Step 핸들러가 hang했을 때 **능동적으로 실행을 중단**시키는 메커니즘이 없다.

---

## 2. 해결책 (TO-BE)

### 2.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **능동적 중단** | `concurrent.futures.ThreadPoolExecutor` 또는 `signal.alarm`으로 실행 시간 제한 |
| **Step별 설정** | 각 Step에 개별 타임아웃 설정 가능 |
| **합리적 기본값** | 타임아웃 미설정 시 Settings 기반 기본값 적용 |
| **Fail-Safe** | 타임아웃 발생 시 Step FAILED + 세션 실패 처리로 연계 |
| **Audit 연동** | 타임아웃 발생 사실을 감사 로그에 기록 |

### 2.2 `RecoveryStep` 모델 변경

**파일**: `services/coordination/recovery_state.py`

```python
@dataclass
class RecoveryStep:
    step_type: RecoveryStepType
    order: int
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    wait_after_seconds: int = 0
    timeout_seconds: int = 0  # NEW: 0이면 Settings 기본값 사용
    params: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
```

### 2.3 Settings 변경

**파일**: `settings/recovery_coordinator.py`

```python
class RecoveryCoordinatorSettings(BaseSettings):
    ...
    # Step-level timeout defaults
    default_step_timeout_seconds: int = 300
    """개별 Step의 기본 타임아웃 (초). Step에 timeout_seconds가 0이면 이 값 사용."""

    budget_reset_timeout_seconds: int = 60
    """BUDGET_RESET Step 타임아웃."""

    health_check_timeout_seconds: int = 600
    """HEALTH_CHECK Step 타임아웃 (안정화 검증이므로 길게 설정)."""

    canary_resume_timeout_seconds: int = 300
    """CANARY_RESUME Step 타임아웃."""

    governance_normal_timeout_seconds: int = 120
    """GOVERNANCE_NORMAL Step 타임아웃."""
```

### 2.4 Step별 타임아웃 결정 로직

```python
def _get_step_timeout(self, step: RecoveryStep) -> int:
    """
    Step의 실효 타임아웃 결정.

    우선순위:
    1. step.timeout_seconds > 0 → Step 개별 설정 사용
    2. Settings의 Step 유형별 기본값
    3. Settings의 default_step_timeout_seconds

    Args:
        step: RecoveryStep

    Returns:
        타임아웃 (초)
    """
    # 1. Step 개별 설정
    if step.timeout_seconds > 0:
        return step.timeout_seconds

    # 2. Step 유형별 Settings
    settings = get_recovery_coordinator_settings()
    type_timeout_map = {
        RecoveryStepType.BUDGET_RESET: settings.budget_reset_timeout_seconds,
        RecoveryStepType.HEALTH_CHECK: settings.health_check_timeout_seconds,
        RecoveryStepType.CANARY_RESUME: settings.canary_resume_timeout_seconds,
        RecoveryStepType.GOVERNANCE_NORMAL: settings.governance_normal_timeout_seconds,
    }

    type_timeout = type_timeout_map.get(step.step_type)
    if type_timeout and type_timeout > 0:
        return type_timeout

    # 3. 전역 기본값
    return settings.default_step_timeout_seconds
```

### 2.5 `execute_next_step()` 변경

```python
import concurrent.futures

def execute_next_step(self, namespace: str) -> RecoveryStep | None:
    with self._lock:
        session = self.get_active_session(namespace)
        if not session:
            return None

        if session.status not in (
            RecoveryStatus.IN_PROGRESS,
            RecoveryStatus.HEALTH_CHECK,
        ):
            return None

        step = session.get_current_step()
        if not step:
            self._handle_all_steps_completed(session)
            return None

        now = datetime.now(timezone.utc).isoformat()
        step.started_at = now
        step.status = RecoveryStatus.IN_PROGRESS

        # Step 타임아웃 결정
        timeout = self._get_step_timeout(step)

        try:
            # 핸들러 선택
            idempotent_registry = self._get_idempotent_registry()
            if idempotent_registry and idempotent_registry.has_handler(step.step_type):
                handler_fn = lambda: idempotent_registry.execute(session, step)
            else:
                handler = self._step_handlers.get(step.step_type)
                if not handler:
                    raise ValueError(f"No handler for step type: {step.step_type}")
                handler_fn = lambda: handler(session, step)

            # 타임아웃 적용 실행
            result = self._execute_with_timeout(handler_fn, timeout, step)

            if result.get("success"):
                step.status = RecoveryStatus.COMPLETED
                step.completed_at = datetime.now(timezone.utc).isoformat()
                session.current_step_index += 1

                self._record_step_executed(session, step, success=True, result=result)
                logger.info(f"[Recovery] Step completed: {step.step_type.value}")
            else:
                step.status = RecoveryStatus.FAILED
                step.error_message = result.get("error", "Unknown error")
                self._record_step_executed(
                    session, step, success=False,
                    error_message=step.error_message, result=result,
                )
                self._fail_session(session, step.error_message)

        except Exception as e:
            step.status = RecoveryStatus.FAILED
            step.error_message = str(e)
            self._record_step_executed(
                session, step, success=False,
                error_message=str(e), result=None,
            )
            self._fail_session(session, str(e))

        self._save_session(session)
        return step
```

### 2.6 `_execute_with_timeout()` 신규 메서드

```python
class StepTimeoutError(Exception):
    """Step 실행 시간 초과."""
    def __init__(self, step_type: str, timeout_seconds: int):
        self.step_type = step_type
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Step '{step_type}' timed out after {timeout_seconds}s"
        )


def _execute_with_timeout(
    self,
    handler_fn: Callable[[], dict[str, Any]],
    timeout_seconds: int,
    step: RecoveryStep,
) -> dict[str, Any]:
    """
    핸들러를 타임아웃과 함께 실행.

    concurrent.futures.ThreadPoolExecutor를 사용하여
    핸들러 실행에 시간 제한을 적용한다.

    Args:
        handler_fn: 실행할 핸들러 함수 (인자 없는 callable)
        timeout_seconds: 타임아웃 (초)
        step: RecoveryStep (에러 보고용)

    Returns:
        핸들러 결과 dict

    Raises:
        StepTimeoutError: 타임아웃 초과 시
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(handler_fn)
        try:
            result = future.result(timeout=timeout_seconds)
            return result
        except concurrent.futures.TimeoutError:
            logger.error(
                f"[Recovery] Step TIMEOUT: {step.step_type.value}, "
                f"timeout={timeout_seconds}s, session step order={step.order}"
            )
            # 타임아웃 메트릭 발행 (Fail-Open)
            try:
                from selfhealing.services.event_bus import EventType, get_event_bus
                get_event_bus().emit(
                    event_type=EventType.EMERGENCY_RECOVERY_STARTED,
                    data={
                        "event": "step_timeout",
                        "step_type": step.step_type.value,
                        "timeout_seconds": timeout_seconds,
                    },
                    source="recovery_coordinator",
                )
            except Exception:
                pass

            raise StepTimeoutError(
                step_type=step.step_type.value,
                timeout_seconds=timeout_seconds,
            )
```

---

## 3. 기존 메커니즘과의 관계

### 3.1 보완 관계 (대체 아님)

```
Step 시작
  │
  ├── [본 문서] _execute_with_timeout()
  │     └── Step 핸들러가 timeout_seconds 초과 → StepTimeoutError
  │           └── _fail_session() → 보상 시도(244) + DLQ 저장(245)
  │
  ├── [기존] RecoveryCircuitBreaker.check_and_trip()
  │     └── 시스템 전체 에러율이 임계값 초과 → 세션 abort
  │
  ├── [기존] IdempotencyRecord.is_safe_to_execute()
  │     └── 30분 경과한 좀비 실행 → 재실행 허용 (수동적)
  │
  └── [기존] DistributedRecoveryLock TTL
        └── 30분 후 락 자동 만료 → 새 복구 시작 가능
```

| 메커니즘 | 감시 대상 | 시점 | 능동/수동 |
|---|---|---|---|
| **Step Timeout (본 문서)** | 개별 핸들러 실행 시간 | 실행 중 | 능동적 중단 |
| RecoveryCircuitBreaker | 시스템 전체 에러율 | 주기적 체크 | 능동적 abort |
| IdempotencyRecord 30분 | 좀비 실행 감지 | 다음 실행 시도 시 | 수동적 판단 |
| DistributedRecoveryLock TTL | 세션 전체 수명 | TTL 만료 시 | 수동적 해제 |

### 3.2 `EXECUTION_TIMEOUT_MINUTES`와의 관계

**파일**: `idempotent_step_handlers.py` L48

```python
EXECUTION_TIMEOUT_MINUTES = 30  # 좀비 판단용
```

이 값은 **"이전에 시작된 실행이 죽었는지 판단"**하는 수동적 기준이다.
본 문서의 Step Timeout은 **"현재 실행 중인 핸들러를 지정 시간 후 강제 종료"**하는 능동적 메커니즘이다.

두 값의 관계:
- `Step timeout_seconds` (능동) < `EXECUTION_TIMEOUT_MINUTES * 60` (수동)
- 예: Step timeout = 300초 → 핸들러 강제 종료 → 세션 FAILED
- 만약 Step timeout이 작동하지 않은 극단적 경우 → 30분 후 멱등성 레코드가 좀비 판단 → 재실행 허용

---

## 4. RecoveryStep `to_dict()` / `from_dict()` 변경

### 4.1 `to_dict()` 변경

```python
def to_dict(self) -> dict[str, Any]:
    return {
        "step_type": self.step_type.value,
        "order": self.order,
        "status": self.status.value,
        "wait_after_seconds": self.wait_after_seconds,
        "timeout_seconds": self.timeout_seconds,  # NEW
        "params": self.params,
        "started_at": self.started_at,
        "completed_at": self.completed_at,
        "error_message": self.error_message,
    }
```

### 4.2 `from_dict()` 변경

```python
@classmethod
def from_dict(cls, data: dict[str, Any]) -> RecoveryStep:
    return cls(
        step_type=RecoveryStepType(data["step_type"]),
        order=data["order"],
        status=RecoveryStatus(data.get("status", "not_started")),
        wait_after_seconds=data.get("wait_after_seconds", 0),
        timeout_seconds=data.get("timeout_seconds", 0),  # NEW (기본 0 → Settings 사용)
        params=data.get("params", {}),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        error_message=data.get("error_message"),
    )
```

---

## 5. 기본 복구 Step에 타임아웃 적용

### 5.1 `_get_recovery_steps()` 변경

**파일**: `recovery_coordinator.py` L942-1040

Step 생성 시 `timeout_seconds=0` (Settings 기본값 사용):

```python
# Step 1: BUDGET_RESET
steps.append(RecoveryStep(
    step_type=RecoveryStepType.BUDGET_RESET,
    order=order,
    wait_after_seconds=0,
    timeout_seconds=0,  # → settings.budget_reset_timeout_seconds (60s)
    params={"target_multiplier": 1.0},
))

# Step 2: HEALTH_CHECK
steps.append(RecoveryStep(
    step_type=RecoveryStepType.HEALTH_CHECK,
    order=order,
    wait_after_seconds=params.get("health_check_wait", 0),
    timeout_seconds=0,  # → settings.health_check_timeout_seconds (600s)
    params={...},
))
```

`timeout_seconds=0`으로 두면 `_get_step_timeout()`이 Settings에서 Step 유형별 기본값을 가져온다.
커스텀 Step은 개별 값 지정 가능:

```python
# 커스텀 Step with explicit timeout
RecoveryStep(
    step_type=RecoveryStepType.CANARY_RESUME,
    order=3,
    timeout_seconds=180,  # 3분
    params={"resume_paused_only": True},
)
```

---

## 6. 영향 범위

### 6.1 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `recovery_state.py` | `RecoveryStep.timeout_seconds` 필드 추가, `to_dict`/`from_dict` 변경 |
| `recovery_coordinator.py` | `_execute_with_timeout()` 신규, `_get_step_timeout()` 신규, `execute_next_step()` 변경 |
| `settings/recovery_coordinator.py` | Step 유형별 타임아웃 설정 추가 |

### 6.2 하위 호환성

| 항목 | 호환성 |
|------|--------|
| `RecoveryStep(timeout_seconds=0)` | ✅ 기본값 0 → Settings에서 결정 → 기존 Step 생성 코드 무변경 |
| `from_dict()` 기존 데이터 | ✅ `data.get("timeout_seconds", 0)` → 없으면 0 → Settings 사용 |
| `execute_next_step()` | ✅ 기존 handler 호출 시그니처 변경 없음 (래핑만 추가) |
| `IdempotentStepHandlerRegistry` | ✅ `execute()` 호출이 timeout 래퍼 안에서 실행될 뿐 |

### 6.3 주의사항: ThreadPoolExecutor와 데드락

`execute_next_step()`은 `with self._lock` 안에서 실행된다.
`_execute_with_timeout()`의 ThreadPoolExecutor는 **별도 스레드**에서 핸들러를 실행하므로,
핸들러 내부에서 `self._lock`을 재획득하려 하면 **데드락**이 발생한다.

현재 핸들러들(`_handle_budget_reset`, `_handle_health_check` 등)은 `self._lock`을 사용하지 않으므로 안전하다.
커스텀 핸들러 등록 시 이 제약을 문서화해야 한다:

```python
def register_step_handler(self, step_type, handler, compensate=None):
    """
    ...
    Warning:
        handler 내부에서 RecoveryCoordinator의 public API를 직접 호출하면
        데드락이 발생할 수 있습니다. 핸들러는 독립적인 서비스 호출만
        수행해야 합니다.
    """
```

---

## 7. 테스트 전략

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | `test_step_timeout_triggers_failure` | 핸들러가 타임아웃 초과 시 `StepTimeoutError` → 세션 FAILED |
| 2 | `test_step_completes_within_timeout` | 적정 시간 내 완료 → COMPLETED 정상 처리 |
| 3 | `test_get_step_timeout_explicit` | `step.timeout_seconds > 0` → 해당 값 반환 |
| 4 | `test_get_step_timeout_from_settings` | `step.timeout_seconds == 0` → Settings 유형별 값 반환 |
| 5 | `test_get_step_timeout_default_fallback` | Settings에 유형별 값 없으면 → `default_step_timeout_seconds` |
| 6 | `test_recovery_step_to_dict_includes_timeout` | `to_dict()`에 `timeout_seconds` 포함 |
| 7 | `test_recovery_step_from_dict_missing_timeout` | `from_dict()`에 `timeout_seconds` 없으면 0 |
| 8 | `test_timeout_triggers_dlq_storage` | 타임아웃 → `_fail_session()` → DLQ 저장 (245번 연동) |
| 9 | `test_timeout_triggers_compensation` | 타임아웃 → `_fail_session()` → `_attempt_compensation()` (244번 연동) |

---

## 8. 3개 문서 통합 실행 흐름

244, 245, 246번 문서가 모두 적용된 후의 `execute_next_step()` → 실패 시 흐름:

```
execute_next_step()
  │
  ├── _get_step_timeout(step)                    ← 246: 타임아웃 결정
  │
  ├── _execute_with_timeout(handler_fn, timeout)  ← 246: 타임아웃 적용 실행
  │     │
  │     ├── [성공] result = handler_fn()
  │     │     └── step.status = COMPLETED
  │     │
  │     └── [타임아웃] concurrent.futures.TimeoutError
  │           └── raise StepTimeoutError
  │
  └── [실패/타임아웃] _fail_session(session, error)
        │
        ├── _attempt_compensation(session)         ← 244: 역순 보상 시도
        │     │
        │     ├── [보상 성공] logger.info
        │     │
        │     └── [보상 실패] _store_failure_to_dlq    ← 245: 보상 실패 DLQ
        │           (failure_type="RECOVERY_COMPENSATION_FAILED")
        │
        ├── session.status = FAILED
        │
        ├── _store_failure_to_dlq(session, error)  ← 245: 세션 실패 DLQ
        │     (failure_type="RECOVERY_SESSION_FAILED")
        │
        ├── _save_session(session)
        │
        └── _recovery_lock.release()
```
