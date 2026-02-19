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

Step 생성 시 `timeout_seconds`를 생략하여 dataclass 기본값(0)을 사용한다:

```python
# Step 1: BUDGET_RESET
steps.append(RecoveryStep(
    step_type=RecoveryStepType.BUDGET_RESET,
    order=order,
    wait_after_seconds=0,
    params={"target_multiplier": 1.0},
))

# Step 2: HEALTH_CHECK
steps.append(RecoveryStep(
    step_type=RecoveryStepType.HEALTH_CHECK,
    order=order,
    wait_after_seconds=params.get("health_check_wait", 0),
    params={...},
))
```

`timeout_seconds` 기본값이 0이므로, 생략 시 `_get_step_timeout()`이 Settings에서 Step 유형별 기본값을 가져온다.
프로젝트 전체의 `RecoveryStep` 생성 패턴(`DEFAULT_RECOVERY_STEPS`, `_get_recovery_steps()`)과 동일하게
dataclass 기본값과 동일한 인자는 명시하지 않는다.
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

---

## 9. 리뷰 반영 — 보완 설계 (6가지)

246번 문서 리뷰에서 도출된 6가지 위험 요소에 대한 보완 설계이다.

---

### 9.1 좀비 스레드 대비 — Defense in Depth

#### 9.1.1 문제

`future.result(timeout=...)` 타임아웃이 발생해도 **백그라운드 스레드의 핸들러는 계속 실행**된다.
Python의 `concurrent.futures.ThreadPoolExecutor`는 스레드를 강제 종료하는 API를 제공하지 않으므로,
네트워크 I/O 블로킹 중인 핸들러는 **좀비 스레드**로 남는다.

#### 9.1.2 방어 전략: 2층 방어 (Defense in Depth)

| 층 | 전략 | 설명 |
|---|---|---|
| **1층** | 하위 서비스 라이브러리 Timeout 의무화 | 핸들러가 호출하는 외부 API에 자체 timeout 강제 |
| **2층** | 협력적 취소 (Cooperative Cancellation) | `threading.Event`로 핸들러 내부에서 취소 신호 감지 |

**1층만으로는 불충분한 이유**: 현재 4개 기본 핸들러가 호출하는 하위 서비스에 자체 timeout이 설정되어 있다는 보장이 없다.

**파일**: `recovery_coordinator.py` L798-956

```python
# _handle_budget_reset → provider.reset_multiplier() → timeout 설정 없음
# _handle_health_check → self._check_stability() → 현재 stub (즉시 True)
# _handle_canary_resume → service.resume_paused_rollouts_staggered() → timeout 설정 없음
# _handle_governance_normal → tracker.record_normal_restoration() → timeout 설정 없음
```

어떤 핸들러도 `requests`, `boto3`, `httpx` 등을 직접 import하지 않지만,
간접 호출 대상의 내부 동작이 네트워크 I/O를 포함할 수 있다.

#### 9.1.3 네이밍 선택: `cancel_event` vs `stop_event`

시스템 전체에서 `threading.Event` 종료 신호 네이밍 조사 결과:

| 컴포넌트 | 네이밍 | 파일 |
|---|---|---|
| `RedisLeaderElector` | `self._stop_event` | `coordination/redis_elector.py` L146 |
| `EtcdLeaderElector` | `self._stop_event` | `coordination/etcd_elector.py` L102 |
| `DLQConsumer` | `self._stop_event` | `coordination/dlq_consumer.py` L71 |
| `StateRefresher` | `self._stop_event` | `canary/state_refresher.py` L90 |
| `SyntheticLoadGenerator` | `self._stop_event` | `chaos/synthetic_load.py` L333 |
| `RegionalHealthMonitor` | `self._stop_event` | `multiregion/health_monitor.py` L148 |
| `FailoverOrchestrator` | `self._stop_event` | `multiregion/failover.py` L162 |
| `HealthProbe` | `self._stop_event` | `meta/health_probe.py` L402 |
| `Watchdog` | `self._stop_event` | `meta/watchdog.py` L107 |
| `HpaExporter` | `self._stop_event` | `scaling/hpa_exporter.py` L94 |
| `AuditWatchdog` | `self._stop_event` | `audit/audit_watchdog.py` L209 |
| `ResilientRecorder` | `self._stop_event` | `audit/resilient_recorder.py` L212 |
| `SyncWorker` | `self._stop_event` | `audit/sync_worker.py` L200 |
| `AsyncWriter` | `self._stop_event` | `audit/performance/async_writer.py` L59 |
| `AutoRollbackGuard` | `self._stop_event` | `core/auto_rollback_guard.py` L189 |
| `RuntimeFeedback` | `self._stop_event` | `core/runtime_feedback.py` L136 |
| `Scheduler` | `self._stop_event` | `coordination/scheduler.py` L120 |
| `EmergencyModeManager` | `self._stop_recovery` | `emergency_mode/manager.py` L80 |

**17개 중 16개가 `_stop_event`**, 1개만 `_stop_recovery`이다.
**결정**: `stop_event`를 사용한다. 시스템 전체 네이밍 일관성.
`cancel_event`는 사용하지 않는다 — 기존 패턴과 불일치.

#### 9.1.4 전달 방식 선택: 핸들러 시그니처 변경 vs Step 어트리뷰트 주입

현재 핸들러 시그니처:

**파일**: `recovery_coordinator.py` L355-356

```python
handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]]
```

**Option A**: 시그니처에 `stop_event` 추가 → `Callable[[RecoverySession, RecoveryStep, Event], ...]`
- ❌ 기존 핸들러 4개 + `register_step_handler()` 호환성 파괴
- ❌ `IdempotentStepHandlerRegistry.execute(session, step)` 시그니처도 변경 필요

**Option B**: `RecoveryStep`에 런타임 어트리뷰트 주입 → `step._stop_event = Event()`
- ✅ 기존 핸들러 시그니처 무변경 — 하위 호환
- ✅ 새 핸들러만 `getattr(step, '_stop_event', None)`으로 참조
- ✅ `RecoveryStep`은 `@dataclass`이므로 `__dict__`에 동적 어트리뷰트 추가 가능
- ⚠️ `to_dict()/from_dict()`에는 포함되지 않음 (직렬화 불필요 — 런타임 전용)

**결정**: **Option B**. 기존 시그니처 보존.

#### 9.1.5 설계 코드

```python
import threading

def execute_next_step(self, namespace: str) -> RecoveryStep | None:
    with self._lock:
        # ... (세션/step 조회 생략)

        # 협력적 취소 Event 주입
        stop_event = threading.Event()
        step._stop_event = stop_event  # 런타임 어트리뷰트 주입

        timeout = self._get_step_timeout(step)

        try:
            # ... (핸들러 선택 생략)
            result = self._execute_with_timeout(handler_fn, timeout, step, session)

        except StepTimeoutError:
            # 타임아웃 시 stop_event 설정 → 좀비 스레드에 종료 신호
            stop_event.set()
            # ... (실패 처리)
```

핸들러 구현 가이드:

```python
# 기존 핸들러: 변경 불필요 (stop_event를 참조하지 않음)
def _handle_budget_reset(self, session, step):
    provider.reset_multiplier(session.namespace)
    return {"success": True}

# 장기 실행 커스텀 핸들러: stop_event 활용
def custom_long_handler(session, step):
    stop_event = getattr(step, '_stop_event', None)
    for batch in large_batches:
        if stop_event and stop_event.is_set():
            return {"success": False, "error": "Cancelled by timeout"}
        process(batch)
    return {"success": True}
```

#### 9.1.6 `register_step_handler()` 가이드라인 추가

```python
def register_step_handler(self, step_type, handler, compensate=None):
    """
    ...
    Warning:
        1. handler 내부에서 RecoveryCoordinator의 public API를 직접 호출하면
           데드락이 발생할 수 있습니다. (self._lock이 RLock이지만
           ThreadPoolExecutor의 별도 스레드에서 실행되므로)
        2. 핸들러가 호출하는 모든 외부 서비스에는 반드시 라이브러리 레벨
           Timeout을 설정해야 합니다. (예: requests.get(url, timeout=30))
        3. 장기 실행 핸들러는 getattr(step, '_stop_event', None)으로
           취소 신호를 주기적으로 확인해야 합니다.
    """
```

---

### 9.2 Late Mutation 방어 — Optimistic Concurrency Control

#### 9.2.1 문제

타임아웃 발생 후 메인 스레드는 세션을 FAILED 처리하지만,
좀비 스레드가 뒤늦게 `session` 객체의 멤버를 변경하면 **race condition**이 발생한다.
현재 `_save_session()`은 단순 `backend.set()`이며 **CAS(Compare-And-Set) 보호가 없다**.

**파일**: `recovery_coordinator.py` L1306-1313

```python
def _save_session(self, session: RecoverySession) -> None:
    backend = self._get_backend()
    key = self.SESSION_KEY.format(...)
    backend.set(key, session.to_dict())  # 무조건 덮어쓰기
```

**파일**: `state_backend.py` L242-252 (RedisStateBackend)

```python
def set(self, key, value, ttl_seconds=None):
    data = json.dumps(value, default=str)
    self._client.set(self._make_key(key), data)  # CAS 없음
```

#### 9.2.2 선택: `RecoverySession.version` (OCC) vs `execution_id` (UUID 회전)

**RecoverySession.version 방식 (OCC) 채택 이유:**

1. **시스템에 OCC 선례가 있다.**

    **파일**: `canary/versioning.py` L40-70

    ```python
    class VersionConflictError(Exception):
        def __init__(self, expected_version, actual_version,
                     conflicting_operator, config_type):
    ```

    Canary Config에서 `VersionConflictError`로 Optimistic Locking을 이미 구현 중이다.
    동일 패턴을 `RecoverySession`에 확장하면 **검증된 패턴의 재사용**이 된다.

2. **`_save_session()` 레벨에서 일관되게 보호.**
    `execution_id` 방식은 핸들러 레벨 보호에 제한되지만,
    `version`은 어떤 경로로 `_save_session`이 호출되어도 CAS가 적용된다.
    현재 `_save_session` 호출 지점은 **13곳**이다 (grep 확인).

3. **기존 핸들러가 직접 DB를 쓰지 않으므로 `execution_id`의 효과가 제한적.**
    현재 4개 기본 핸들러는 dict만 반환하며, 상태 변경은 `execute_next_step()` 내부에서만 수행한다.

#### 9.2.3 `RecoverySession` 모델 변경

```python
@dataclass
class RecoverySession:
    id: str
    namespace: str
    trigger_level: str
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    version: int = 0  # NEW: OCC용 버전 카운터
    steps: list[RecoveryStep] = field(default_factory=list)
    # ... (기존 필드)
```

`to_dict()` / `from_dict()`:

```python
def to_dict(self) -> dict[str, Any]:
    return {
        "id": self.id,
        "version": self.version,  # NEW
        # ...
    }

@classmethod
def from_dict(cls, data: dict[str, Any]) -> RecoverySession:
    return cls(
        id=data["id"],
        version=data.get("version", 0),  # NEW (기존 데이터 호환)
        # ...
    )
```

#### 9.2.4 `_save_session()` CAS 변경

```python
class SessionVersionConflictError(Exception):
    """세션 저장 시 버전 충돌.

    좀비 스레드의 뒤늦은 저장을 차단하기 위한 OCC 예외.

    네이밍 근거:
    - canary/versioning.py의 VersionConflictError와 패턴 통일.
    - 'Session' 접두사로 Recovery 도메인 구분.
    """
    def __init__(self, session_id: str, expected: int, actual: int):
        self.session_id = session_id
        self.expected_version = expected
        self.actual_version = actual
        super().__init__(
            f"Session version conflict: {session_id}, "
            f"expected v{expected}, actual v{actual}"
        )


# Redis Lua 스크립트: version 기반 CAS
SESSION_CAS_SCRIPT = """
local current = redis.call("GET", KEYS[1])
if current == false then
    redis.call("SET", KEYS[1], ARGV[1])
    return 1
end
local data = cjson.decode(current)
local expected_version = tonumber(ARGV[2])
if data["version"] == nil or data["version"] == expected_version then
    redis.call("SET", KEYS[1], ARGV[1])
    return 1
else
    return 0
end
"""


def _save_session(self, session: RecoverySession) -> None:
    """세션 저장 (OCC 적용).

    저장 시 version을 +1 증가시키고, Redis에서 현재 version과
    일치할 때만 저장한다. 불일치 시 SessionVersionConflictError.

    좀비 스레드의 뒤늦은 저장 시도를 차단한다.
    """
    expected_version = session.version
    session.version += 1  # 저장 전 version 증가

    backend = self._get_backend()
    key = self.SESSION_KEY.format(
        namespace=session.namespace,
        session_id=session.id,
    )

    # RedisStateBackend인 경우 CAS 적용
    if hasattr(backend, '_client'):
        try:
            data = json.dumps(session.to_dict(), default=str)
            result = backend._client.eval(
                self.SESSION_CAS_SCRIPT,
                1,
                backend._make_key(key),
                data,
                str(expected_version),
            )
            if result == 0:
                session.version = expected_version  # 롤백
                raise SessionVersionConflictError(
                    session.id, expected_version,
                    session.version,
                )
        except SessionVersionConflictError:
            raise
        except Exception:
            # CAS 실패 시 Fail-Open: 기존 방식으로 폴백
            backend.set(key, session.to_dict())
    else:
        # InMemory/File 백엔드: 단순 저장 (self._lock으로 이미 보호)
        backend.set(key, session.to_dict())
```

#### 9.2.5 하위 호환성

| 항목 | 호환성 |
|------|--------|
| `from_dict()` 기존 데이터 | ✅ `data.get("version", 0)` → 없으면 0 |
| `_save_session()` InMemory | ✅ `self._lock(RLock)`으로 단일 스레드 보호, CAS 불필요 |
| `_save_session()` Redis CAS 실패 | ✅ Fail-Open — 기존 `set()` 폴백 |

---

### 9.3 보상 핸들러 타임아웃 적용

#### 9.3.1 문제

`_attempt_compensation()` 내부에서 `compensate_handler(session, step)`가 **타임아웃 없이 직접 호출**된다.

**파일**: `recovery_coordinator.py` L1558-1640

```python
for step in completed_steps:
    self._recovery_lock.extend(...)
    compensate_handler = self._compensate_handlers.get(step.step_type)
    # ...
    handler_result = compensate_handler(session, step)  # ← 타임아웃 없음
```

Forward 핸들러가 외부 API 타임아웃으로 실패했다면,
동일 API를 호출하는 보상 핸들러도 높은 확률로 hang된다.

#### 9.3.2 Settings 추가

246 문서 §2.3에서 `default_step_timeout_seconds` 신규 필드를 제안했으나,
Settings에 **이미 동일 목적의 필드가 존재**한다:

**파일**: `settings/recovery_coordinator.py` L199-203

```python
step_execution_timeout_seconds: int = Field(
    default=300, ge=30, le=1800,
    description="단일 복구 단계 실행 타임아웃 (초)",
)
```

따라서 246 문서 §2.3의 `default_step_timeout_seconds`는 **기존 `step_execution_timeout_seconds`를 재사용**하도록 수정한다.
보상 타임아웃은 **별도 필드를 신규 추가**한다:

```python
class RecoveryCoordinatorSettings(BaseSettings):
    # ... (기존)
    step_execution_timeout_seconds: int = Field(
        default=300, ge=30, le=1800,
        description="단일 복구 단계 실행 타임아웃 (초)",
    )  # ← 이미 존재. §2.3의 default_step_timeout_seconds 대체.

    # NEW: 보상 전용 타임아웃
    compensation_step_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="개별 보상 핸들러 실행 타임아웃 (초). Forward보다 짧게 설정 권장.",
    )
```

**`compensation_step_timeout_seconds`의 기본값이 120초인 이유:**
- Forward 기본값(300초)의 40%. 보상은 Forward보다 Fail-Fast 해야 한다.
- 외부 API가 이미 장애 상태이므로, 긴 대기는 무의미하다.

**네이밍 `compensation_step_timeout_seconds` 선택 이유:**
- `default_compensation_timeout_seconds`는 "전체 보상 루프 타임아웃"으로 오해될 수 있음.
- `compensation_step_timeout_seconds`는 "개별 보상 Step의 타임아웃"임을 명확히 함.
- 기존 `step_execution_timeout_seconds`의 `_step_timeout_seconds` 패턴과 대칭.

#### 9.3.3 `_attempt_compensation()` 변경

```python
def _attempt_compensation(self, session: RecoverySession) -> CompensationResult:
    result = CompensationResult()
    completed_steps = [s for s in session.steps if s.status == RecoveryStatus.COMPLETED]
    completed_steps.sort(key=lambda s: s.order, reverse=True)

    settings = get_recovery_coordinator_settings()
    comp_timeout = settings.compensation_step_timeout_seconds

    for step in completed_steps:
        self._recovery_lock.extend(
            session.namespace, session.id, additional_seconds=300,
        )

        compensate_handler = self._compensate_handlers.get(step.step_type)
        if compensate_handler is None:
            result.skipped_steps.append(step)
            continue

        if step.compensation_status == CompensationStatus.COMPENSATED:
            result.compensated_steps.append(step)
            continue

        try:
            # 보상에도 타임아웃 적용 (Forward와 독립적 설정)
            handler_fn = lambda s=step: compensate_handler(session, s)
            handler_result = self._execute_with_timeout(
                handler_fn, comp_timeout, step, session,
            )

            if handler_result.get("success"):
                step.compensation_status = CompensationStatus.COMPENSATED
                self._save_session(session)
                result.compensated_steps.append(step)
            else:
                error_msg = handler_result.get("error", "Unknown error")
                step.compensation_status = CompensationStatus.COMPENSATE_FAILED
                self._save_session(session)
                result.failed_steps.append((step, error_msg))
        except StepTimeoutError as e:
            # 보상 타임아웃도 COMPENSATE_FAILED 처리
            step.compensation_status = CompensationStatus.COMPENSATE_FAILED
            self._save_session(session)
            result.failed_steps.append((step, str(e)))
            logger.warning(
                f"[Recovery] Compensation timeout: {step.step_type.value}, "
                f"timeout={comp_timeout}s"
            )
        except Exception as e:
            step.compensation_status = CompensationStatus.COMPENSATE_FAILED
            self._save_session(session)
            result.failed_steps.append((step, str(e)))
            # Fail-Open: 보상 실패가 세션 실패 처리를 중단시키지 않음

    return result
```

---

### 9.4 Django DB 커넥션 누수 방지

#### 9.4.1 문제

`ThreadPoolExecutor`로 새 스레드에서 핸들러를 실행하면, **Thread-local DB 커넥션**이 생성된다.
스레드 종료 시 Django가 자동으로 `close()`하지 않으므로 **Connection Pool 고갈** 위험이 있다.

#### 9.4.2 래퍼 설계

시스템 전체에서 `close_old_connections`가 한 번도 사용되지 않았음을 확인했다 (grep 0건).
selfhealing 패키지는 Django 선택적 의존이므로, **`ImportError` 방어**를 포함해야 한다.

```python
def _execute_with_timeout(self, handler_fn, timeout_seconds, step, session):
    def _wrapped_handler():
        """Django DB 커넥션 안전 래퍼."""
        try:
            from django.db import close_old_connections
            close_old_connections()  # 스레드 시작 시 stale 커넥션 정리
        except ImportError:
            pass  # Django 미설치 환경

        try:
            return handler_fn()
        finally:
            try:
                from django.db import close_old_connections
                close_old_connections()  # 스레드 종료 시 커넥션 반환
            except ImportError:
                pass

    # ThreadPoolExecutor에 _wrapped_handler 제출
    # ... (§9.6에서 전체 코드 참조)
```

---

### 9.5 ThreadPoolExecutor 매번 생성 — 설계 의도 문서화

#### 9.5.1 기존 설계 유지 (변경 없음)

`_execute_with_timeout()` 내부에서 `with ThreadPoolExecutor(max_workers=1) as executor:`를
매 Step마다 생성하는 현재 설계를 유지한다.

#### 9.5.2 설계 의도 (주석 추가)

```python
def _execute_with_timeout(self, ...):
    # ThreadPoolExecutor를 매 Step마다 생성하는 이유 (Bulkhead 격리):
    #
    # 1. 좀비 스레드 격리: 전역 풀(max_workers=1)에서 좀비가 worker를
    #    점유하면 다음 Step 제출이 영구 block됨 (Head-of-Line Blocking).
    #    매번 생성하면 좀비가 다음 Step/세션에 영향을 주지 않음.
    #
    # 2. 낮은 오버헤드: 복구 세션당 최대 4개 Step(LEVEL_3 기준)이므로
    #    스레드 생성/소멸 비용이 무시 가능.
    #
    # 3. self._lock(RLock)으로 동시 실행이 1개로 제한되므로
    #    스레드 풀 재사용의 병렬성 이점이 없음.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        ...
```

---

### 9.6 Lock Heartbeat — Polling 방식 대기

#### 9.6.1 문제

`future.result(timeout=600)`으로 10분간 블로킹되면, 그 동안
**분산 락 연장(heartbeat)을 수행할 주체가 없다.**

현재 `_recovery_lock.extend()`가 호출되는 곳은 `_attempt_compensation()` 루프 내부뿐이다:

**파일**: `recovery_coordinator.py` L1593-1598

```python
# _attempt_compensation() 내부
self._recovery_lock.extend(
    session.namespace, session.id, additional_seconds=300,
)
```

`execute_next_step()` → `_execute_with_timeout()` 경로에서는 **Lock extend가 없다.**

#### 9.6.2 시그니처 변경

`_execute_with_timeout()`에 `session` 인자를 추가한다.
Lock 연장에 `session.namespace`와 `session.id`가 필요하기 때문이다:

**파일**: `distributed_recovery_lock.py` L261-265

```python
def extend(self, namespace: str, session_id: str,
           additional_seconds: int | None = None) -> bool:
```

#### 9.6.3 변경된 `_execute_with_timeout()` 전체 코드

§2.6의 원본을 대체하는 최종 버전이다.
§9.1(좀비 방어), §9.4(Django DB), §9.5(풀 격리), §9.6(Lock heartbeat)을 모두 통합한다.

```python
import concurrent.futures
import threading

# 기존 Settings 필드 재사용 (§9.3.2 참조):
# step_execution_timeout_seconds = 300 (이미 존재)

LOCK_HEARTBEAT_INTERVAL_SECONDS = 60
"""Lock 하트비트 간격 (초). future.result() 루프 주기."""


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
    session: RecoverySession,
) -> dict[str, Any]:
    """
    핸들러를 타임아웃 + Lock Heartbeat + Django DB 안전 래퍼와 함께 실행.

    §2.6 원본 대비 변경사항:
    - session 인자 추가 (Lock extend용)
    - Polling 방식 대기 (heartbeat_interval마다 Lock 연장)
    - Django close_old_connections() 래퍼
    - Bulkhead 격리 주석

    Args:
        handler_fn: 실행할 핸들러 함수 (인자 없는 callable)
        timeout_seconds: 타임아웃 (초)
        step: RecoveryStep (에러 보고용 + _stop_event 참조)
        session: RecoverySession (Lock extend용)

    Returns:
        핸들러 결과 dict

    Raises:
        StepTimeoutError: 타임아웃 초과 시
    """
    def _wrapped_handler():
        """Django DB 커넥션 안전 래퍼."""
        try:
            from django.db import close_old_connections
            close_old_connections()
        except ImportError:
            pass
        try:
            return handler_fn()
        finally:
            try:
                from django.db import close_old_connections
                close_old_connections()
            except ImportError:
                pass

    # ThreadPoolExecutor를 매 Step마다 생성 (Bulkhead 격리)
    # 이유: 전역 풀(max_workers=1)에서 좀비가 worker를 점유하면
    # 다음 Step 제출이 영구 block (Head-of-Line Blocking).
    # 복구 세션당 최대 4 Step이므로 생성 오버헤드 무시 가능.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_wrapped_handler)
        elapsed = 0
        heartbeat = LOCK_HEARTBEAT_INTERVAL_SECONDS

        # Polling 대기 루프: heartbeat 간격으로 Lock 연장
        while elapsed < timeout_seconds:
            remaining = timeout_seconds - elapsed
            wait_time = min(heartbeat, remaining)
            try:
                result = future.result(timeout=wait_time)
                return result
            except concurrent.futures.TimeoutError:
                elapsed += wait_time
                if elapsed >= timeout_seconds:
                    break
                # Lock TTL 연장 (하트비트)
                self._recovery_lock.extend(
                    session.namespace,
                    session.id,
                    additional_seconds=300,  # 5분 연장
                )
                logger.debug(
                    f"[Recovery] Lock heartbeat: step={step.step_type.value}, "
                    f"elapsed={elapsed}s/{timeout_seconds}s"
                )

        # 타임아웃 초과
        logger.error(
            f"[Recovery] Step TIMEOUT: {step.step_type.value}, "
            f"timeout={timeout_seconds}s, session={session.id}"
        )

        # 좀비 스레드에 종료 신호 (§9.1 협력적 취소)
        stop_event = getattr(step, '_stop_event', None)
        if stop_event:
            stop_event.set()

        # 타임아웃 메트릭 발행 (Fail-Open)
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus
            get_event_bus().emit(
                event_type=EventType.EMERGENCY_RECOVERY_STARTED,
                data={
                    "event": "step_timeout",
                    "step_type": step.step_type.value,
                    "timeout_seconds": timeout_seconds,
                    "session_id": session.id,
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

#### 9.6.4 Lock Heartbeat 동작 설명

```
HEALTH_CHECK Step, timeout=600s, heartbeat=60s인 경우:

T=0s     future.result(timeout=60)  → TimeoutError
           └── lock.extend(+300s)
T=60s    future.result(timeout=60)  → TimeoutError
           └── lock.extend(+300s)
T=120s   future.result(timeout=60)  → 핸들러 완료!
           └── return result

또는:

T=0s     future.result(timeout=60)  → TimeoutError
           └── lock.extend(+300s)
...
T=600s   elapsed >= timeout → StepTimeoutError
           └── stop_event.set()
```

기존 Lock TTL(기본 30분)을 타임아웃보다 짧게 설정하더라도,
heartbeat로 **주기적으로 연장**하므로 Lock이 만료되지 않는다.

---

### 9.7 §2.3 Settings 수정 사항

#### 9.7.1 `default_step_timeout_seconds` → `step_execution_timeout_seconds` 변경

246 문서 §2.3에서 신규 제안한 `default_step_timeout_seconds: int = 300`은
**이미 존재하는 `step_execution_timeout_seconds: int = 300`과 동일 목적**이다.

**파일**: `settings/recovery_coordinator.py` L199-203

```python
step_execution_timeout_seconds: int = Field(
    default=300,
    ge=30,
    le=1800,
    description="단일 복구 단계 실행 타임아웃 (초)",
)
```

따라서 §2.3의 `default_step_timeout_seconds`를 제거하고,
§2.4 `_get_step_timeout()`의 3단계 폴백에서 기존 필드를 사용한다:

```python
def _get_step_timeout(self, step: RecoveryStep) -> int:
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

    # 3. 전역 기본값 (기존 필드 재사용)
    return settings.step_execution_timeout_seconds
```

#### 9.7.2 Settings 최종 추가 목록

| 필드 | 신규/기존 | 기본값 |
|------|-----------|--------|
| `step_execution_timeout_seconds` | **기존** (L199) | 300 |
| `budget_reset_timeout_seconds` | 신규 | 60 |
| `health_check_timeout_seconds` | 신규 | 600 |
| `canary_resume_timeout_seconds` | 신규 | 300 |
| `governance_normal_timeout_seconds` | 신규 | 120 |
| `compensation_step_timeout_seconds` | 신규 | 120 |

---

### 9.8 테스트 추가 (리뷰 반영분)

§7의 기존 9개 테스트에 다음을 추가한다:

| # | 테스트 | 검증 내용 | 대응 절 |
|---|--------|-----------|---------|
| 10 | `test_stop_event_set_on_timeout` | 타임아웃 시 `step._stop_event.is_set() == True` | §9.1 |
| 11 | `test_handler_checks_stop_event` | 핸들러가 `stop_event`를 체크하여 조기 종료 | §9.1 |
| 12 | `test_session_version_increments_on_save` | `_save_session()` 호출 시 `version` +1 | §9.2 |
| 13 | `test_session_version_conflict_rejected` | 뒤늦은 저장이 `SessionVersionConflictError` 발생 | §9.2 |
| 14 | `test_session_from_dict_missing_version` | `from_dict()`에 `version` 없으면 0 | §9.2 |
| 15 | `test_compensation_timeout_applied` | 보상 핸들러 hang 시 `compensation_step_timeout_seconds` 후 실패 | §9.3 |
| 16 | `test_compensation_timeout_independent` | 보상 타임아웃이 Forward 타임아웃과 독립 | §9.3 |
| 17 | `test_lock_heartbeat_during_long_step` | 600s Step 실행 중 `extend()` 호출 확인 | §9.6 |
| 18 | `test_lock_heartbeat_interval` | 60s 간격으로 `extend()` 호출 | §9.6 |

---

### 9.9 통합 실행 흐름 (§8 확장)

§8의 기존 흐름에 §9 보완 설계를 오버레이한 최종 흐름:

```
execute_next_step()
  │
  ├── stop_event = threading.Event()              ← §9.1: 협력적 취소 준비
  ├── step._stop_event = stop_event               ← §9.1: 런타임 주입
  │
  ├── _get_step_timeout(step)                     ← 246 §2.4
  │     └── 3단계 폴백: step → type별 Settings
  │         → settings.step_execution_timeout_seconds (기존 필드)  ← §9.7
  │
  ├── _execute_with_timeout(handler_fn, timeout, step, session)  ← §9.6 시그니처
  │     │
  │     ├── _wrapped_handler()                    ← §9.4: Django DB 래퍼
  │     │     ├── close_old_connections()
  │     │     ├── handler_fn()
  │     │     └── finally: close_old_connections()
  │     │
  │     ├── ThreadPoolExecutor(max_workers=1)     ← §9.5: Bulkhead 격리
  │     │
  │     ├── while elapsed < timeout:              ← §9.6: Polling 루프
  │     │     ├── future.result(timeout=60)
  │     │     │     ├── [성공] return result
  │     │     │     └── [TimeoutError] lock.extend()  ← Lock Heartbeat
  │     │     └── [timeout 초과] stop_event.set()     ← §9.1: 종료 신호
  │     │
  │     └── raise StepTimeoutError
  │
  ├── [성공] step.status = COMPLETED
  │     └── session.version += 1                  ← §9.2: OCC
  │
  └── [실패/타임아웃] _fail_session(session, error)
        │
        ├── _attempt_compensation(session)         ← 244 + §9.3
        │     ├── for step in reversed(completed_steps):
        │     │     ├── lock.extend()
        │     │     └── _execute_with_timeout(      ← §9.3: 보상에도 타임아웃
        │     │           compensate_handler,
        │     │           compensation_step_timeout_seconds,
        │     │           step, session)
        │     └── [보상 타임아웃] StepTimeoutError → COMPENSATE_FAILED
        │
        ├── session.status = FAILED
        ├── _save_session(session)                 ← §9.2: CAS 적용
        │     └── version 불일치 시 SessionVersionConflictError
        │
        ├── _store_failure_to_dlq(session, error)  ← 245
        └── _recovery_lock.release()
```
