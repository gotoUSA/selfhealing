# 244. Recovery Step Compensate Slot 추가

> **Version**: 2.0.0
> **Created**: 2026-02-19
> **Updated**: 2026-02-19
> **Status**: Approved
> **Parent**: [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
> **Related**: [248_SAGA_CORE_MODELS.md](248_SAGA_CORE_MODELS.md)
> **Priority**: P1 — 저비용, 기존 동작 무변경

## 0. 요약

`RecoveryCoordinator.register_step_handler()`에 **선택적 compensate 함수 슬롯**을 추가한다.
기존 Forward-only 핸들러 등록을 유지하면서, **향후 역순 보상이 필요할 때 즉시 활성화**할 수 있는 구조적 준비.

추가로 다음 7가지 보강 사항을 Phase 1에 포함한다:

1. `RecoveryStep`에 Forward 실행 결과 저장 필드(`result_data`) 추가
2. `RecoveryStep`에 보상 상태 추적 필드(`compensation_status`) 추가
3. `RecoveryStatus.COMPENSATING` 상태 추가
4. 보상 루프 내 Lock Lease Extension (하트비트)
5. 보상 호출 시 실패 원인(`abort_reason`) 접근 보장
6. `CompensationResult` 반환 구조체 정의
7. At-least-once 보상 + 멱등성 원칙 명문화

---

## 1. 문제점 (AS-IS)

### 1.1 현재 코드: Forward 함수만 등록 가능

**파일**: `services/coordination/recovery_coordinator.py` L348-361

```python
def register_step_handler(
    self,
    step_type: RecoveryStepType,
    handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
) -> None:
    """
    커스텀 단계 핸들러 등록.

    Args:
        step_type: 복구 단계 유형
        handler: 핸들러 함수 (session, step) -> {"success": bool, ...}
    """
    self._step_handlers[step_type] = handler
```

### 1.2 기본 핸들러 등록도 Forward만

**파일**: `services/coordination/recovery_coordinator.py` L340-347

```python
def _register_default_handlers(self) -> None:
    """기본 단계 핸들러 등록."""
    self._step_handlers = {
        RecoveryStepType.BUDGET_RESET: self._handle_budget_reset,
        RecoveryStepType.HEALTH_CHECK: self._handle_health_check,
        RecoveryStepType.CANARY_RESUME: self._handle_canary_resume,
        RecoveryStepType.GOVERNANCE_NORMAL: self._handle_governance_normal,
    }
```

### 1.3 실패 시 보상 경로 없음

**파일**: `services/coordination/recovery_coordinator.py` L1329-1355

```python
def _fail_session(self, session, error):
    session.status = RecoveryStatus.FAILED
    session.abort_reason = error
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)
    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
```

`_fail_session()`은 **상태를 FAILED로 바꾸고 락을 해제**할 뿐, 이미 성공한 Step을 되돌리는 로직이 없다.

### 1.4 Forward 실행 결과가 Step에 저장되지 않음

**파일**: `services/coordination/recovery_coordinator.py` L500-511

```python
result = handler(session, step)

if result.get("success"):
    step.status = RecoveryStatus.COMPLETED
    step.completed_at = datetime.now(timezone.utc).isoformat()
    session.current_step_index += 1
```

핸들러 반환값(`result`)은 로컬 변수로만 사용되고 `step`에 영속화되지 않는다.
`_record_step_executed()`의 audit metadata에만 기록될 뿐, 보상 시 접근 불가.

### 1.5 보상 진행 상태를 구분할 수 없음

**파일**: `services/coordination/enums.py` L91-135

현재 `RecoveryStatus` enum:
`NORMAL`, `EMERGENCY`, `NOT_STARTED`, `IN_PROGRESS`, `RECOVERING`,
`HEALTH_CHECK`, `READY_TO_RESTORE`, `COMPLETED`, `FAILED`, `ABORTED`

보상 진행 중(`COMPENSATING`)에 해당하는 상태값이 없다.

### 1.6 Lock 하트비트 미호출

**파일**: `services/coordination/distributed_recovery_lock.py` L262-310

`extend()` 메서드가 구현되어 있으나, `recovery_coordinator.py` 내에서
`extend`를 호출하는 코드가 **0건**. 기본 TTL은 30분(`settings/distributed_lock.py` L57).

---

## 2. 해결책 (TO-BE)

### 2.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **기존 동작 무변경** | `compensate=None`이 기본값 → 기존 코드 수정 불필요 |
| **선택적 활성화** | compensate가 등록된 Step만 역순 보상 시도 |
| **Fail-Open** | 보상 실패가 세션 실패 처리를 중단시키지 않음 |
| **도메인 프리** | compensate 함수도 Forward와 동일한 시그니처 → 인프라 레벨 |
| **At-least-once** | 보상은 최소 1회 실행을 보장하며, 핸들러가 멱등성을 책임짐 |
| **248 Saga 정합성** | 네이밍과 상태값을 248번 Saga Core Models 설계와 통일 |

### 2.2 `CompensationStatus` 신규 Enum (Review #2)

**파일**: `services/coordination/enums.py`

```python
class CompensationStatus(str, Enum):
    """
    개별 Step의 보상 상태.

    248번 Saga Core Models의 SagaStepStatus 네이밍과 정렬.
    (SagaStepStatus: COMPENSATING, COMPENSATED, COMPENSATE_FAILED)
    """

    NOT_REQUIRED = "not_required"
    """보상 핸들러 미등록 — 보상 대상 아님 (기본값)."""

    PENDING = "pending"
    """보상 대기 — 세션 실패 시 보상 대상."""

    COMPENSATED = "compensated"
    """보상 완료."""

    COMPENSATE_FAILED = "compensate_failed"
    """보상 실패."""
```

**네이밍 선택 근거**:

| 항목 | 리뷰 제안 | 채택값 | 이유 |
|------|-----------|--------|------|
| Enum 이름 | `compensation_status` (필드명) | `CompensationStatus` (별도 Enum 클래스) | 기존 시스템의 `RecoveryStatus`, `PendingStatus` 등 모두 별도 Enum 클래스 |
| 완료값 | `COMPLETED` | `COMPENSATED` | 248번 `SagaStepStatus.COMPENSATED`와 통일. `COMPLETED`는 `RecoveryStatus.COMPLETED`(Forward 완료)와 혼동 |
| 실패값 | `FAILED` | `COMPENSATE_FAILED` | 248번 `SagaStepStatus.COMPENSATE_FAILED`와 통일. `FAILED`는 `RecoveryStatus.FAILED`와 혼동 |
| 미필요값 | `NOT_NEEDED` | `NOT_REQUIRED` | 코드베이스에 `NOT_NEEDED` 사용례 0건. `NOT_REQUIRED`가 더 명시적 |

### 2.3 `CompensationResult` 신규 데이터클래스 (Review #6)

**파일**: `services/coordination/recovery_state.py`

```python
@dataclass
class CompensationResult:
    """
    보상 실행 결과.

    _attempt_compensation()의 반환값으로, 보상 성공/실패/건너뜀 Step 목록을 구조화.
    Phase 3 DLQ 연동 시 failed_steps를 DLQ로 전송.
    """

    compensated_steps: list[RecoveryStep] = field(default_factory=list)
    """보상 성공한 Step 목록."""

    failed_steps: list[tuple[RecoveryStep, str]] = field(default_factory=list)
    """보상 실패한 Step 목록. (step, error_message) 튜플."""

    skipped_steps: list[RecoveryStep] = field(default_factory=list)
    """compensate 핸들러 미등록으로 건너뛴 Step 목록."""

    @property
    def all_compensated(self) -> bool:
        """모든 보상 대상이 성공했는지 여부."""
        return len(self.failed_steps) == 0
```

**네이밍 선택 근거**:

| 항목 | 리뷰 제안 | 채택값 | 이유 |
|------|-----------|--------|------|
| 클래스명 | `CompensationResult` | `CompensationResult` | 코드베이스에 동명 클래스 0건. 충돌 없음 |
| 성공 필드 | `success_steps` | `compensated_steps` | `CompensationStatus.COMPENSATED`와 용어 통일 |
| 실패 타입 | `list[tuple[RecoveryStep, Exception]]` | `list[tuple[RecoveryStep, str]]` | `Exception`은 JSON 직렬화 불가. DLQ 전송 시 `str` 필요 |
| 성공 프로퍼티 | `all_success` | `all_compensated` | 248번 `SagaStatus.COMPENSATED`와 용어 통일 |
| skipped | 없음 | `skipped_steps` 추가 | DLQ에서 "핸들러 미등록 건너뜀"과 "보상 실패"를 구분 |

### 2.4 `RecoveryStep` 모델 변경 (Review #1, #2)

**파일**: `services/coordination/recovery_state.py`

```python
@dataclass
class RecoveryStep:
    """
    복구 단계.

    개별 복구 작업을 나타내며, 상태 추적 및 파라미터 관리.
    """

    step_type: RecoveryStepType
    order: int
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    wait_after_seconds: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None

    # ── Review #1: Forward 실행 결과 저장 ──
    result_data: dict[str, Any] = field(default_factory=dict)
    """
    Forward 핸들러 실행 결과 데이터.

    핸들러가 반환하는 dict를 저장하여 compensate 시 참조 가능하게 함.
    Saga 패턴의 기본 원칙: "무엇을 했는지 알아야 되돌릴 수 있다."

    예시:
    - BUDGET_RESET: {"success": True, "multiplier": 1.0}
    - CANARY_RESUME: {"success": True, "resumed_count": 3, "staggered": True}

    네이밍 근거:
    - 248번 Saga Core Models의 StepResult.data 필드와 의미적 통일.
    - execution_context는 248번 SagaContext(Step 간 공유 데이터)와 혼동 우려.
    """

    # ── Review #2: 보상 상태 추적 ──
    compensation_status: CompensationStatus = CompensationStatus.NOT_REQUIRED
    """
    보상 상태.

    서버 재시작 시 어디까지 보상했는지 추적.
    _attempt_compensation 루프에서 보상 성공 시 즉시 COMPENSATED로 업데이트 + Redis 저장.

    네이밍 근거:
    - CompensationStatus Enum으로 별도 정의 (RecoveryStatus와 분리).
    - 248번 SagaStepStatus의 COMPENSATED/COMPENSATE_FAILED와 값 통일.
    """

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "step_type": self.step_type.value,
            "order": self.order,
            "status": self.status.value,
            "wait_after_seconds": self.wait_after_seconds,
            "params": self.params,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "result_data": self.result_data,
            "compensation_status": self.compensation_status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryStep:
        """딕셔너리에서 생성."""
        return cls(
            step_type=RecoveryStepType(data["step_type"]),
            order=data["order"],
            status=RecoveryStatus(data.get("status", "not_started")),
            wait_after_seconds=data.get("wait_after_seconds", 0),
            params=data.get("params", {}),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error_message=data.get("error_message"),
            result_data=data.get("result_data", {}),
            compensation_status=CompensationStatus(
                data.get("compensation_status", "not_required")
            ),
        )
```

**하위 호환성**: `result_data=field(default_factory=dict)`, `compensation_status=NOT_REQUIRED`
기본값이므로 기존 코드 무변경. `from_dict()`에서 `.get()` + 기본값으로 기존 JSON 호환.

### 2.5 `RecoveryStatus.COMPENSATING` 추가 (Review #3)

**파일**: `services/coordination/enums.py`

```python
class RecoveryStatus(str, Enum):
    # ... 기존 값 유지 ...

    COMPENSATING = "compensating"
    """
    보상 진행 중.

    _fail_session() 진입 시 COMPENSATING으로 전환 → 보상 루프 실행 → 최종 FAILED.
    모니터링 대시보드에서 "복구 진행 중"과 "보상 롤백 중"을 구분할 수 있게 함.

    네이밍 근거:
    - 248번 SagaStatus.COMPENSATING = "compensating"과 동일한 값.
    - 향후 Saga Orchestrator 통합 시 자연스러운 매핑.
    """

    # ... COMPLETED, FAILED, ABORTED ...
```

**연쇄 변경 필요**:

`start_recovery()` 중복 체크에 `COMPENSATING` 추가:

```python
# recovery_coordinator.py L390-397
active = self.get_active_session(namespace)
if active and active.status in (
    RecoveryStatus.IN_PROGRESS,
    RecoveryStatus.HEALTH_CHECK,
    RecoveryStatus.READY_TO_RESTORE,
    RecoveryStatus.COMPENSATING,  # 보상 중에도 새 복구 차단
):
    raise ValueError(f"Recovery already in progress: {active.id}")
```

### 2.6 `register_step_handler()` 변경 (Review #7 포함)

```python
def register_step_handler(
    self,
    step_type: RecoveryStepType,
    handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
    compensate: Callable[[RecoverySession, RecoveryStep], dict[str, Any]] | None = None,
) -> None:
    """
    커스텀 단계 핸들러 등록.

    Args:
        step_type: 복구 단계 유형
        handler: Forward 핸들러 함수 (session, step) -> {"success": bool, ...}
        compensate: 보상 핸들러 함수 (선택). Step 실패 시 이전 성공 Step 역순 보상에 사용.
                    None이면 해당 Step은 보상 대상에서 제외.

    Note:
        이 시스템은 At-least-once 보상을 지향합니다.
        compensate 핸들러는 반드시 **멱등성(Idempotency)**을 보장해야 합니다.
        동일한 Step에 대해 compensate가 여러 번 호출되어도 동일한 결과를 보장해야 합니다.
        (서버 재시작, 네트워크 재시도 등으로 인해 중복 호출될 수 있음)

        상태 저장은 Redis/File 기반 StateBackend이며, Django Transaction과 독립적입니다.
        보상 핸들러 내부에서 외부 API/DB 호출과 StateBackend 저장 간에 Atomic 보장이 없으므로,
        핸들러 구현자가 멱등 방어 코드를 작성해야 합니다.

        Phase 4에서 IdempotentCompensateHandler가 인프라 레벨에서 이를 보장할 예정이나,
        그 전까지는 핸들러 구현자의 책임입니다.

        Reference:
            docs/self_healing/middleware_system/248_SAGA_CORE_MODELS.md §2.4 SagaStep
    """
    self._step_handlers[step_type] = handler
    if compensate is not None:
        self._compensate_handlers[step_type] = compensate
```

### 2.7 `__init__()` 변경

```python
def __init__(self, ...):
    ...
    self._step_handlers: dict[RecoveryStepType, Callable] = {}
    self._compensate_handlers: dict[RecoveryStepType, Callable] = {}  # NEW
    self._register_default_handlers()
```

### 2.8 `_register_default_handlers()` 변경

```python
def _register_default_handlers(self) -> None:
    """기본 단계 핸들러 등록."""
    self._step_handlers = {
        RecoveryStepType.BUDGET_RESET: self._handle_budget_reset,
        RecoveryStepType.HEALTH_CHECK: self._handle_health_check,
        RecoveryStepType.CANARY_RESUME: self._handle_canary_resume,
        RecoveryStepType.GOVERNANCE_NORMAL: self._handle_governance_normal,
    }
    # Compensate 핸들러: 현재는 빈 dict (Phase 2에서 필요 시 등록)
    self._compensate_handlers = {}
```

### 2.9 `execute_next_step()`에서 `result_data` 저장 (Review #1)

**파일**: `services/coordination/recovery_coordinator.py` L508-512

```python
if result.get("success"):
    step.status = RecoveryStatus.COMPLETED
    step.completed_at = datetime.now(timezone.utc).isoformat()
    step.result_data = result  # Forward 실행 결과 저장
    step.compensation_status = CompensationStatus.PENDING  # 보상 대상으로 표시
    session.current_step_index += 1
```

**변경 이유**:
- Forward 핸들러 반환값(`result`)을 `step.result_data`에 저장하여
  compensate 호출 시 `step.result_data`로 "무엇을 했는지" 참조 가능.
- `compensation_status`를 `PENDING`으로 설정하여 "보상 대상"임을 표시.
  compensate 핸들러가 등록되지 않은 경우에도 PENDING으로 두고,
  실제 보상 시점에 핸들러 존재 여부로 SKIPPED 처리.

### 2.10 `_fail_session()` 변경 (Review #3, #5)

```python
def _fail_session(self, session, error):
    """세션 실패 처리 + 등록된 compensate 핸들러 역순 실행."""
    # Review #5: abort_reason을 보상 루프 전에 설정
    # compensate 핸들러가 session.abort_reason으로 실패 원인 참조 가능.
    # 별도 인자 전달 대신 session 필드 선설정 방식을 채택.
    # 이유: Forward 핸들러와 동일한 시그니처(session, step) 유지.
    #       248번 SagaStep.compensate(ctx: SagaContext)도 별도 error 인자 없이
    #       ctx에서 정보를 가져오는 패턴.
    session.abort_reason = error

    # Review #3: COMPENSATING 상태로 전환 (보상 진행 중 표시)
    session.status = RecoveryStatus.COMPENSATING
    self._save_session(session)

    # 1. 이미 완료된 Step 역순 보상 시도
    comp_result = self._attempt_compensation(session)

    # Review #6: 보상 실패 Step이 있으면 DLQ 대상으로 로깅
    if not comp_result.all_compensated:
        logger.warning(
            f"[Recovery] Compensation incomplete: session={session.id}, "
            f"failed={len(comp_result.failed_steps)}, "
            f"skipped={len(comp_result.skipped_steps)}"
        )
        # Phase 3 (245번 문서): comp_result.failed_steps → DLQ 전송

    # 2. 최종 실패 상태 (기존 로직 유지)
    session.status = RecoveryStatus.FAILED
    session.completed_at = datetime.now(timezone.utc).isoformat()
    self._save_session(session)

    # 락 해제
    self._recovery_lock.release(session.namespace, session.id)
    logger.error(f"[Recovery] Failed: id={session.id}, error={error}")
```

**Review #5 선택 근거**:

| 방식 | 장점 | 단점 | 채택 |
|------|------|------|------|
| `_attempt_compensation(session, error)` 인자 전달 | Side-effect 없이 명시적 | compensate 핸들러 시그니처를 `(session, step, error)`로 변경해야 함 → Forward와 불일치 | ❌ |
| `session.abort_reason = error` 선설정 | Forward와 동일한 시그니처 유지, 248 SagaStep 패턴과 일치 | session 필드 변경이라는 side-effect | ✅ |

채택 이유:
- 현재 Forward 핸들러 시그니처: `(session, step) -> dict[str, Any]`
- 248번 SagaStep.compensate 시그니처: `(ctx: SagaContext) -> StepResult` — error 별도 인자 없음
- compensate도 동일 시그니처를 유지해야 `register_step_handler()`의 타입 힌트 통일 가능
- `session.abort_reason`은 `_fail_session()` 내에서 어차피 설정되므로 순서만 앞당기는 것

### 2.11 `_attempt_compensation()` 신규 메서드 (Review #2, #4, #6, #7)

```python
def _attempt_compensation(
    self,
    session: RecoverySession,
) -> CompensationResult:
    """
    완료된 Step을 역순으로 보상 시도.

    설계 원칙:
    - Fail-Open: 보상 실패가 세션 실패 처리를 중단시키지 않음.
    - At-least-once: 보상은 최소 1회 실행을 보장.
      compensate 핸들러는 반드시 멱등성(Idempotency)을 가져야 한다.
      서버 재시작 시 compensation_status가 PENDING인 Step들은
      재보상 대상이 될 수 있으므로, 핸들러가 중복 실행에 안전해야 한다.
    - compensate 핸들러가 등록되지 않은 Step은 건너뜀.

    Returns:
        CompensationResult — 보상 성공/실패/건너뜀 Step 목록.
        Phase 3 DLQ 연동 시 failed_steps를 DLQ로 전송.

    Reference:
        docs/self_healing/middleware_system/248_SAGA_CORE_MODELS.md §2.3 SagaContext
        docs/self_healing/middleware_system/248_SAGA_CORE_MODELS.md §2.4 SagaStep
    """
    result = CompensationResult()

    completed_steps = [
        step for step in session.steps
        if step.status == RecoveryStatus.COMPLETED
    ]

    # 역순 정렬 (order 기준 내림차순)
    completed_steps.sort(key=lambda s: s.order, reverse=True)

    for step in completed_steps:
        # Review #4: 매 Step 보상 전 Lock TTL 연장 (하트비트)
        # distributed_recovery_lock.py의 extend() 사용.
        # 기본 TTL 30분(settings/distributed_lock.py L57)이 보상 도중 만료되면
        # 다른 Worker가 동일 세션을 잡아 중복 실행할 위험이 있음.
        self._recovery_lock.extend(
            session.namespace,
            session.id,
            additional_seconds=300,  # 5분 연장
        )

        compensate_handler = self._compensate_handlers.get(step.step_type)
        if compensate_handler is None:
            logger.debug(
                f"[Recovery] No compensate handler for {step.step_type.value}, skipping"
            )
            result.skipped_steps.append(step)
            continue

        # Review #2: 이미 보상 완료된 Step은 건너뜀 (재시작 안전성)
        if step.compensation_status == CompensationStatus.COMPENSATED:
            logger.debug(
                f"[Recovery] Already compensated: {step.step_type.value}, skipping"
            )
            result.compensated_steps.append(step)
            continue

        try:
            handler_result = compensate_handler(session, step)
            if handler_result.get("success"):
                # Review #2: 보상 성공 즉시 상태 저장 (서버 재시작 대비)
                step.compensation_status = CompensationStatus.COMPENSATED
                self._save_session(session)

                result.compensated_steps.append(step)
                logger.info(
                    f"[Recovery] Compensated: {step.step_type.value}, "
                    f"session={session.id}"
                )
            else:
                error_msg = handler_result.get("error", "Unknown error")
                step.compensation_status = CompensationStatus.COMPENSATE_FAILED
                self._save_session(session)

                result.failed_steps.append((step, error_msg))
                logger.warning(
                    f"[Recovery] Compensation failed: {step.step_type.value}, "
                    f"error={error_msg}"
                )
        except Exception as e:
            step.compensation_status = CompensationStatus.COMPENSATE_FAILED
            self._save_session(session)

            result.failed_steps.append((step, str(e)))
            logger.warning(
                f"[Recovery] Compensation exception: {step.step_type.value}, "
                f"error={e}"
            )
            # Fail-Open: 보상 실패가 세션 실패 처리를 중단시키지 않음

    return result
```

**Review #4 (Lock Heartbeat) 설계 근거**:

| 항목 | 코드 근거 |
|------|-----------|
| `extend()` 존재 여부 | `distributed_recovery_lock.py` L262-310 — Lua 스크립트(EXTEND_SCRIPT)까지 구현 완료 |
| 기본 TTL | `settings/distributed_lock.py` L57 — `timeout_minutes=30` |
| `auto_extend_enabled` | `settings/distributed_lock.py` L87 — `True` 설정이지만 호출 코드 0건 |
| 시그니처 | `extend(namespace, session_id, additional_seconds=None)` — 기존 API 그대로 사용 |

`additional_seconds=300` (5분)을 선택한 이유:
- `settings/distributed_lock.py` L80의 `extend_interval_seconds=60`(확인 간격)의 5배
- 보상 핸들러 1개가 5분 이상 걸리는 경우 다음 루프에서 다시 연장됨
- 기본 TTL(30분) 전체를 매번 갱신하는 것보다 보수적

---

## 3. 영향 범위

### 3.1 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `recovery_coordinator.py` | `__init__`, `_register_default_handlers`, `register_step_handler`, `execute_next_step`, `_fail_session`, `_attempt_compensation` (신규) |
| `recovery_state.py` | `RecoveryStep`에 `result_data`, `compensation_status` 필드 추가. `CompensationResult` 신규. `to_dict()`/`from_dict()` 업데이트 |
| `enums.py` | `CompensationStatus` 신규 Enum. `RecoveryStatus.COMPENSATING` 추가 |
| `idempotent_step_handlers.py` | 변경 없음 |

### 3.2 하위 호환성

| 항목 | 호환성 |
|------|--------|
| `register_step_handler(step_type, handler)` | ✅ `compensate=None` 기본값으로 기존 코드 무변경 |
| `_register_default_handlers()` | ✅ `_compensate_handlers = {}` → 기존 동작 동일 |
| `_fail_session()` | ✅ `_attempt_compensation()`이 빈 dict이면 no-op (CompensationResult 빈 리스트 반환) |
| `IdempotentStepHandlerRegistry` | ✅ Forward 핸들러와 독립적 |
| `RecoveryStep.result_data` | ✅ `field(default_factory=dict)` → 기존 Step에 자동 빈 dict |
| `RecoveryStep.compensation_status` | ✅ `NOT_REQUIRED` 기본값 → 기존 Step 무변경 |
| `RecoveryStep.from_dict()` | ✅ `.get("result_data", {})`, `.get("compensation_status", "not_required")` → 기존 JSON 호환 |
| `RecoveryStatus.COMPENSATING` | ✅ 기존 상태 전이에 영향 없음. `start_recovery()` 중복 체크에만 추가 |

### 3.3 기존 연동 시스템 영향

| 시스템 | 영향 |
|--------|------|
| `RecoveryCircuitBreaker` | 없음 — 에러율 기반 트립은 별도 경로 |
| `RecoveryAuditRecorder` | Phase 2에서 보상 감사 이벤트 추가 가능 |
| `DistributedRecoveryLock` | 기존 `extend()` API 사용 — 신규 코드 아님 |
| `EventBus` | Phase 2에서 `COMPENSATION_EXECUTED` 이벤트 추가 가능 |
| `RecoveryDashboard` | `COMPENSATING` 상태를 표시하도록 업데이트 필요 |

---

## 4. 테스트 전략

### 4.1 기존 테스트 무영향 확인

```python
# 기존 코드: compensate 없이 등록 → 동일하게 동작해야 함
coordinator.register_step_handler(RecoveryStepType.BUDGET_RESET, my_handler)

# 기존 JSON에서 역직렬화 → 새 필드는 기본값으로 채워짐
old_json = {"step_type": "budget_reset", "order": 1, "status": "completed"}
step = RecoveryStep.from_dict(old_json)
assert step.result_data == {}
assert step.compensation_status == CompensationStatus.NOT_REQUIRED
```

### 4.2 신규 테스트 케이스

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | `test_register_with_compensate` | compensate 함수가 `_compensate_handlers`에 저장됨 |
| 2 | `test_register_without_compensate` | `_compensate_handlers`에 등록되지 않음 (기존 동작) |
| 3 | `test_attempt_compensation_reverse_order` | 완료 Step이 역순으로 보상됨 |
| 4 | `test_attempt_compensation_skip_no_handler` | compensate 미등록 Step은 건너뜀 + `result.skipped_steps`에 포함 |
| 5 | `test_attempt_compensation_fail_open` | 보상 실패해도 세션 실패 처리 계속 진행 |
| 6 | `test_attempt_compensation_empty` | 완료 Step 없으면 빈 CompensationResult 반환 |
| 7 | `test_fail_session_calls_compensation` | `_fail_session()`이 `_attempt_compensation()` 호출 |
| 8 | `test_fail_session_compensating_state` | `_fail_session()` 진입 시 `COMPENSATING` → 보상 후 `FAILED` 전이 |
| 9 | `test_result_data_saved_on_step_success` | Forward 성공 시 `step.result_data`에 핸들러 반환값 저장됨 |
| 10 | `test_compensation_status_persisted` | 보상 성공 시 `step.compensation_status == COMPENSATED` + `_save_session` 호출됨 |
| 11 | `test_compensation_status_failed_persisted` | 보상 실패 시 `step.compensation_status == COMPENSATE_FAILED` 저장됨 |
| 12 | `test_already_compensated_skip` | `compensation_status == COMPENSATED`인 Step은 재보상하지 않음 |
| 13 | `test_lock_extended_during_compensation` | 보상 루프에서 `_recovery_lock.extend()` 호출됨 |
| 14 | `test_abort_reason_accessible_in_compensate` | compensate 핸들러 내에서 `session.abort_reason` 접근 가능 |
| 15 | `test_compensation_result_structure` | `CompensationResult`의 compensated/failed/skipped 분류 정확성 |
| 16 | `test_start_recovery_blocks_compensating` | `status == COMPENSATING`인 세션이 있으면 새 복구 차단 |
| 17 | `test_step_to_dict_includes_new_fields` | `to_dict()`에 `result_data`, `compensation_status` 포함 |
| 18 | `test_step_from_dict_backward_compatible` | 기존 JSON(새 필드 없음)에서 역직렬화 시 기본값 적용 |

---

## 5. 향후 확장 경로

| Phase | 내용 | 의존성 |
|-------|------|--------|
| Phase 1 (본 문서) | compensate 슬롯 + `_attempt_compensation()` + `result_data` + `CompensationStatus` + `COMPENSATING` + Lock heartbeat + `CompensationResult` + 멱등성 docstring | 없음 |
| Phase 2 | 기본 compensate 핸들러 등록 (BUDGET_RESET → 가중치 복원 등) | 인프라 안정성 검증 |
| Phase 3 | 보상 감사 이벤트 (`RecoveryAuditEventType.COMPENSATION_EXECUTED`) + DLQ 연동 (`CompensationResult.failed_steps` 활용) | 문서 245 DLQ 연동 |
| Phase 4 | IdempotentCompensateHandler (보상 멱등성 인프라) | `idempotent_step_handlers.py` 확장 |
| Phase 5 | 248 Saga Orchestrator 통합 — `CompensationStatus` → `SagaStepStatus` 매핑 | 248, 249번 문서 코드 구현 |

---

## 6. 248 Saga Core Models 정합성 매핑

본 문서의 네이밍은 248번 Saga Core Models(설계 상태, 코드 미구현)과 향후 통합을 위해 정렬했다.

| 244 (Recovery Coordinator) | 248 (Saga Core Models) | 정렬 상태 |
|---------------------------|------------------------|-----------|
| `CompensationStatus.COMPENSATED` | `SagaStepStatus.COMPENSATED` | ✅ 값 동일 |
| `CompensationStatus.COMPENSATE_FAILED` | `SagaStepStatus.COMPENSATE_FAILED` | ✅ 값 동일 |
| `RecoveryStatus.COMPENSATING` | `SagaStatus.COMPENSATING` | ✅ 값 동일 (`"compensating"`) |
| `CompensationResult` | (해당 없음 — 248은 Orchestrator 내부 처리) | ⬜ 244 고유 |
| `RecoveryStep.result_data` | `StepResult.data` → `SagaContext.step_results` | ✅ 의미적 동일 |
| `register_step_handler(compensate=)` | `SagaStep.compensate(ctx)` | ✅ 시그니처 호환 (session ≈ ctx) |

---

## 7. Review 반영 이력

| # | 리뷰 항목 | 반영 섹션 | 핵심 변경 |
|---|-----------|-----------|-----------|
| R1 | `RecoveryStep`에 실행 결과 저장 | §2.4, §2.9 | `result_data` 필드 추가. 네이밍은 248 `StepResult.data`와 통일 |
| R2 | 보상 상태 추적 | §2.2, §2.4, §2.11 | `CompensationStatus` Enum + `compensation_status` 필드. 값은 248 `SagaStepStatus`와 통일 |
| R3 | `COMPENSATING` 상태 | §2.5, §2.10 | `RecoveryStatus.COMPENSATING` 추가. `start_recovery()` 중복 체크에도 반영 |
| R4 | Lock Heartbeat | §2.11 | `_attempt_compensation` 루프 내 `_recovery_lock.extend()` 호출. 기존 구현 활용 |
| R5 | 실패 원인 접근 보장 | §2.10 | `session.abort_reason = error` 선설정. 별도 인자 대신 session 필드 방식 채택 (시그니처 통일) |
| R6 | `CompensationResult` 반환 | §2.3, §2.10, §2.11 | 구조화된 반환값. `Exception` → `str`, `all_success` → `all_compensated`, `skipped_steps` 추가 |
| R7 | 멱등성 원칙 문서화 | §2.6, §2.11 | `register_step_handler()` docstring + `_attempt_compensation()` docstring에 At-least-once 원칙 명시 |
