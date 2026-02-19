# 248. Saga 코어 모델 설계

> **Version**: 1.1.0
> **Created**: 2026-02-19
> **Updated**: 2026-02-19 (v1.1.0 — 6가지 리뷰 반영)
> **Status**: Implemented
> **Parent**: [247_SAGA_ORCHESTRATOR_OVERVIEW.md](247_SAGA_ORCHESTRATOR_OVERVIEW.md)
> **Priority**: P1 — Saga 엔진의 기반 자료구조

## 0. 요약

Saga Orchestrator의 **코어 모델**을 설계한다.
- `SagaStep(ABC)` — Forward + Compensate 인터페이스 (도메인-프리)
- `SagaDefinition` — Saga 정의 (이름 + Step 목록)
- `SagaInstance` — Saga 실행 인스턴스 (상태 추적)
- `SagaStatus` — 상태 머신 enum
- `SagaContext` — Step 간 데이터 전달 컨텍스트
- `StepResult` — Step 실행 결과

기존 패턴(`ReplayHandler(ABC)`, `RecoveryStep`, `RecoverySession`)의 설계 원칙을 따른다.

---

## 1. 기존 패턴 분석 (설계 근거)

### 1.1 ReplayHandler 패턴 — 도메인-프리 ABC

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

**핵심 패턴**:
- ABC가 도메인 이름(`domain`)을 프로퍼티로 요구
- ABC가 실행 함수(`replay`)와 실행 가능 여부 함수(`can_replay`)를 요구
- 코어 패키지에는 **DefaultReplayHandler** (무조건 실패 반환)만 존재
- 도메인별 구현은 어댑터 레이어에서 `register_replay_handler()`로 등록

### 1.2 RecoveryStep/Session 패턴 — 상태 추적 dataclass

**파일**: `services/coordination/recovery_state.py` L74-115

```python
@dataclass
class RecoveryStep:
    step_type: RecoveryStepType
    order: int
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    wait_after_seconds: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
```

**핵심 패턴**:
- `order`로 실행 순서 명시
- `status`로 진행 상태 추적
- `started_at`, `completed_at`으로 시간 기록
- `error_message`로 실패 원인 기록
- `to_dict()` / `from_dict()`로 직렬화/역직렬화

### 1.3 RecoverySession 패턴 — 세션 추적

**파일**: `services/coordination/recovery_state.py` L148-210

```python
@dataclass
class RecoverySession:
    id: str
    namespace: str
    trigger_level: str
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    steps: list[RecoveryStep] = field(default_factory=list)
    current_step_index: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    initiated_by: str = "system"
    abort_reason: str | None = None
    metadata: dict[str, Any] | None = None
```

---

## 2. 모델 설계

### 2.1 SagaStatus — 상태 머신 enum

```python
class SagaStatus(str, Enum):
    """Saga 인스턴스의 상태."""

    PENDING = "pending"
    """생성됨, 실행 대기 중."""

    RUNNING = "running"
    """Forward step들 순차 실행 중."""

    COMPLETED = "completed"
    """모든 Forward step 성공. 최종 완료."""

    COMPENSATING = "compensating"
    """Forward step 실패로 역순 compensate 실행 중."""

    COMPENSATED = "compensated"
    """모든 compensate 성공. 원상 복구 완료."""

    COMPENSATION_FAILED = "compensation_failed"
    """compensate 중 실패. DLQ에 저장됨. 수동 개입 필요."""

    SUSPENDED = "suspended"
    """외부 서비스 장애(CircuitBreaker OPEN)로 일시 중지."""

    TIMED_OUT = "timed_out"
    """전체 Saga 타임아웃 초과."""
```

**허용 전환 (Allowed Transitions)**:

```
PENDING       → RUNNING
RUNNING       → COMPLETED, COMPENSATING, SUSPENDED, TIMED_OUT
COMPENSATING  → COMPENSATED, COMPENSATION_FAILED
SUSPENDED     → RUNNING, COMPENSATING  (서비스 회복 시 재개)
```

잘못된 전환을 방지하기 위해 `ALLOWED_TRANSITIONS` 딕셔너리로 강제:

```python
ALLOWED_TRANSITIONS: dict[SagaStatus, set[SagaStatus]] = {
    SagaStatus.PENDING: {SagaStatus.RUNNING},
    SagaStatus.RUNNING: {
        SagaStatus.COMPLETED,
        SagaStatus.COMPENSATING,
        SagaStatus.SUSPENDED,
        SagaStatus.TIMED_OUT,
    },
    SagaStatus.COMPENSATING: {
        SagaStatus.COMPENSATED,
        SagaStatus.COMPENSATION_FAILED,
    },
    SagaStatus.SUSPENDED: {
        SagaStatus.RUNNING,
        SagaStatus.COMPENSATING,
    },
    SagaStatus.COMPLETED: set(),       # 터미널 상태
    SagaStatus.COMPENSATED: set(),     # 터미널 상태
    SagaStatus.COMPENSATION_FAILED: set(),  # 터미널 상태
    SagaStatus.TIMED_OUT: set(),       # 터미널 상태
}
```

설계 근거: `RecoveryStatus`도 동일하게 enum + 터미널 상태 패턴 사용 (`services/coordination/enums.py`).

### 2.2 StepResult — Step 실행 결과

```python
@dataclass
class StepResult:
    """SagaStep의 execute() 또는 compensate() 실행 결과."""

    success: bool
    """성공 여부."""

    data: dict[str, Any] = field(default_factory=dict)
    """Step이 생성한 데이터 (execute 시 context에 merge됨).

    예시:
    - PaymentStep.execute() → {"payment_id": "pay_001", "pg_tx_id": "tx_abc"}
    - PointStep.execute() → {"point_tx_id": "pt_001"}
    """

    error: str | None = None
    """실패 시 에러 메시지."""

    error_code: str | None = None
    """실패 시 에러 코드 (DLQ 분류용).

    예시: "PG_TIMEOUT", "INSUFFICIENT_BALANCE", "INVENTORY_SHORTAGE"
    """

    retryable: bool = False
    """재시도 가능 여부. True면 Orchestrator가 재시도를 시도할 수 있음."""

    partial_execution: bool = False
    """외부 부작용(side-effect)이 발생한 후 실패한 경우 True.

    v1.1.0 추가 (리뷰 R5 — Partial Failure Cleanup).

    문제 상황: PG 결제 성공 후 내부 DB 저장 직전에 OOM/타임아웃으로 EXECUTE_FAILED가 되면,
    Orchestrator는 이전 Step들의 compensate만 실행하고 해당 Step의 compensate는 호출하지 않는다.
    결과적으로 결제된 돈은 환불되지 않는 고아(orphan) 데이터가 된다.

    해결: partial_execution=True면 EXECUTE_FAILED 상태여도 보상 대상에 포함.

    설계 근거:
    - RecoveryStep.result_data (recovery_state.py L118-132):
      "무엇을 했는지 알아야 되돌릴 수 있다" 원칙.
    - _attempt_compensation() (recovery_coordinator.py L1855):
      COMPLETED 상태만 보상 대상 → partial_execution으로 확장.
    """

    @classmethod
    def succeeded(cls, data: dict[str, Any] | None = None) -> StepResult:
        return cls(success=True, data=data or {})

    @classmethod
    def failed(cls, error: str, error_code: str = "", retryable: bool = False) -> StepResult:
        return cls(success=False, error=error, error_code=error_code, retryable=retryable)

    @classmethod
    def failed_with_side_effect(
        cls,
        error: str,
        data: dict[str, Any] | None = None,
        error_code: str = "",
        retryable: bool = False,
    ) -> StepResult:
        """외부 부작용이 발생한 후 실패한 경우의 팩토리 메서드.

        v1.1.0 추가 (리뷰 R5).

        Orchestrator가 이 Step도 compensate 대상에 포함한다.
        data에 부분 실행 결과를 함께 전달하면 compensate가 정확한 정리를 수행할 수 있다.

        설계 근거: ReplayResult.blocked() (replay_service/models.py L43-50)
        팩토리 메서드 패턴과 동일.

        Usage:
            def execute(self, ctx: SagaContext) -> StepResult:
                payment_id = pg_client.charge(ctx.get("order_id"), ctx.get("amount"))
                # PG 결제 성공, 하지만 내부 DB 저장 시 에러
                try:
                    db.save_payment(payment_id)
                except Exception as e:
                    return StepResult.failed_with_side_effect(
                        error=str(e),
                        data={"payment_id": payment_id},  # compensate에서 환불 가능
                        error_code="DB_SAVE_AFTER_PG_SUCCESS",
                    )
                return StepResult.succeeded({"payment_id": payment_id})
        """
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            retryable=retryable,
            data=data or {},
            partial_execution=True,
        )
```

설계 근거: `ReplayResult` (`services/replay_service/models.py` L22-50)의 팩토리 메서드 패턴 동일 적용.

### 2.3 SagaContext — Step 간 데이터 공유

```python
@dataclass
class SagaContext:
    """Saga 실행 중 Step 간 데이터 전달 컨텍스트.

    각 Step의 execute()가 반환한 StepResult.data가 자동으로 merge된다.
    compensate() 시에도 이전 execute()에서 생성한 데이터에 접근 가능.

    Usage:
        # execute에서 데이터 생성
        def execute(self, ctx: SagaContext) -> StepResult:
            payment_id = pg_client.charge(ctx.get("order_id"), ctx.get("amount"))
            return StepResult.succeeded({"payment_id": payment_id})

        # compensate에서 이전 데이터 사용
        def compensate(self, ctx: SagaContext) -> StepResult:
            pg_client.refund(ctx.get("payment_id"))
            return StepResult.succeeded()
    """

    saga_instance_id: str
    """소속 Saga 인스턴스 ID."""

    initial_data: dict[str, Any] = field(default_factory=dict)
    """Saga 시작 시 전달된 초기 데이터.

    예시: {"order_id": 123, "user_id": 456, "amount": 50000}
    """

    step_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    """각 Step이 반환한 데이터. key = step_name.

    예시: {
        "payment": {"payment_id": "pay_001", "pg_tx_id": "tx_abc"},
        "point": {"point_tx_id": "pt_001"},
    }
    """

    # -----------------------------------------------------------------
    # v1.1.0 추가 필드 (리뷰 R3 — Compensation Context)
    # -----------------------------------------------------------------

    failed_step_name: str | None = None
    """Forward 실패를 유발한 Step 이름. COMPENSATING 전환 시 Orchestrator가 설정.

    설계 근거: RecoverySession.abort_reason (recovery_state.py L257)이
    _fail_session() 진입 시 설정되는 것과 동일한 시점에 채워진다.
    recovery_coordinator.py L1638-1640:
        session.abort_reason = error  # 보상 루프 전에 설정
    """

    abort_reason: str | None = None
    """실패 원인 메시지. RecoverySession.abort_reason과 동일 네이밍.

    설계 근거: recovery_coordinator.py L1628-1629:
        '보상 핸들러가 session.abort_reason으로 실패 원인을 참조할 수 있도록
         abort_reason을 보상 루프 전에 설정한다.'

    compensate() 내부에서 PG사 환불 사유(Refund Reason) 지정 등에 활용.
    """

    abort_error_code: str | None = None
    """실패 에러 코드 (StepResult.error_code에서 복사).

    DLQ 분류 및 감사 로그에 활용.
    예: "PG_TIMEOUT", "INSUFFICIENT_BALANCE"
    """

    # -----------------------------------------------------------------
    # 조회 메서드
    # -----------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """초기 데이터 + 모든 Step 결과에서 key 검색 (편의 메서드).

        검색 순서: step_results (최신 먼저) → initial_data

        Warning:
            동일한 key가 여러 Step에서 사용되면 의도치 않은 값이 반환될 수 있다.
            키 충돌 가능성이 있는 경우 get_step_data() 또는 get_from_step()을 사용할 것.
        """
        # Step 결과에서 검색 (나중 Step 우선)
        for step_data in reversed(list(self.step_results.values())):
            if key in step_data:
                return step_data[key]
        # 초기 데이터에서 검색
        return self.initial_data.get(key, default)

    def get_step_data(self, step_name: str) -> dict[str, Any]:
        """특정 Step의 결과 데이터만 반환 (네임스페이스 격리 조회).

        v1.1.0 추가 (리뷰 R2 — SagaContext Data Namespace).

        설계 근거: RecoveryStep.params (recovery_state.py L100-110)가
        Step별로 격리된 dict를 사용하는 것과 동일한 원칙.
        step_results가 이미 step_name을 키로 분리 저장하므로 단순 래퍼.

        Usage:
            payment_data = ctx.get_step_data("payment")
            payment_id = payment_data.get("tx_id")  # 명시적, 충돌 없음
        """
        return self.step_results.get(step_name, {})

    def get_from_step(self, step_name: str, key: str, default: Any = None) -> Any:
        """특정 Step의 결과에서 특정 key만 조회.

        v1.1.0 추가 (리뷰 R2).

        get()과 달리 명시적으로 Step을 지정하므로 키 충돌 위험이 없다.

        Usage:
            payment_tx = ctx.get_from_step("payment", "tx_id")
            point_tx = ctx.get_from_step("point", "tx_id")  # 다른 Step의 tx_id
        """
        return self.step_results.get(step_name, {}).get(key, default)

    # -----------------------------------------------------------------
    # 상태 변이 메서드
    # -----------------------------------------------------------------

    def merge_step_result(self, step_name: str, data: dict[str, Any]) -> None:
        """Step 실행 결과를 컨텍스트에 merge."""
        self.step_results[step_name] = data

    def set_compensation_context(
        self,
        failed_step_name: str,
        abort_reason: str,
        abort_error_code: str | None = None,
    ) -> None:
        """COMPENSATING 전환 시 보상 맥락 설정.

        v1.1.0 추가 (리뷰 R3).

        Orchestrator가 COMPENSATING 전환 시점에 호출한다.
        compensate() 함수 내에서 ctx.abort_reason으로 실패 원인을 참조 가능.

        설계 근거: recovery_coordinator.py L1638-1640의
        session.abort_reason = error 패턴과 동일.
        """
        self.failed_step_name = failed_step_name
        self.abort_reason = abort_reason
        self.abort_error_code = abort_error_code

    # -----------------------------------------------------------------
    # 직렬화
    # -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "saga_instance_id": self.saga_instance_id,
            "initial_data": self.initial_data,
            "step_results": self.step_results,
            "failed_step_name": self.failed_step_name,
            "abort_reason": self.abort_reason,
            "abort_error_code": self.abort_error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaContext:
        return cls(
            saga_instance_id=data["saga_instance_id"],
            initial_data=data.get("initial_data", {}),
            step_results=data.get("step_results", {}),
            failed_step_name=data.get("failed_step_name"),
            abort_reason=data.get("abort_reason"),
            abort_error_code=data.get("abort_error_code"),
        )
```

### 2.4 SagaStep(ABC) — Forward + Compensate 인터페이스

```python
class SagaStep(ABC):
    """Saga의 개별 단계를 정의하는 Abstract Base Class.

    도메인-프리 인터페이스. 비즈니스 로직은 어댑터 레이어에서 구현.

    ReplayHandler 패턴과 동일한 구조:
    - 코어 패키지: ABC 정의 + 레지스트리
    - 어댑터 레이어: 도메인별 구현 + 등록

    Usage (어댑터 레이어):
        class PaymentStep(SagaStep):
            @property
            def name(self) -> str:
                return "payment"

            def execute(self, ctx: SagaContext) -> StepResult:
                payment_id = pg_client.charge(
                    order_id=ctx.get("order_id"),
                    amount=ctx.get("amount"),
                )
                return StepResult.succeeded({"payment_id": payment_id})

            def compensate(self, ctx: SagaContext) -> StepResult:
                pg_client.refund(ctx.get("payment_id"))
                return StepResult.succeeded()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Step 이름 (고유 식별자).

        SagaContext.step_results의 키로 사용됨.
        예: "payment", "point", "inventory"
        """
        pass

    @abstractmethod
    def execute(self, ctx: SagaContext) -> StepResult:
        """Forward 실행.

        Args:
            ctx: Saga 컨텍스트 (이전 Step 결과 + 초기 데이터 포함)

        Returns:
            StepResult — 성공 시 data에 다음 Step/compensate가 필요한 정보 포함
        """
        pass

    @abstractmethod
    def compensate(self, ctx: SagaContext) -> StepResult:
        """역순 보상.

        이 Step의 execute()가 성공한 후 **이후 Step**이 실패했을 때 호출됨.
        ctx.get()으로 자신의 execute()가 생성한 데이터에 접근 가능.

        Args:
            ctx: Saga 컨텍스트

        Returns:
            StepResult — 실패 시 Orchestrator가 DLQ에 저장
        """
        pass

    def can_execute(self, ctx: SagaContext) -> tuple[bool, str]:
        """실행 가능 여부 확인 (선택적 오버라이드).

        기본값: 항상 실행 가능.
        오버라이드하여 전제 조건 검증 가능.

        Returns:
            (can_execute, reason) 튜플
        """
        return True, ""

    @property
    def timeout_seconds(self) -> int | None:
        """Step별 타임아웃 (초). None이면 Saga 전체 타임아웃 적용.

        246번 문서의 Step Timeout Monitor와 연동.
        기본값: None (Saga 전체 타임아웃에 위임)
        """
        return None
```

설계 근거:
- `name` 프로퍼티: `ReplayHandler.domain`과 동일 패턴 (`handlers.py` L43-46)
- `execute`/`compensate`: `ReplayHandler.replay()`의 Forward/Compensate 분리 버전
- `can_execute`: `ReplayHandler.can_replay()`과 동일 패턴 (`handlers.py` L59-65)
- `timeout_seconds`: 246번 문서의 Step Timeout Monitor 연동

### 2.5 SagaStepInstance — Step 실행 인스턴스 상태

```python
class SagaStepStatus(str, Enum):
    """개별 Step의 실행 상태."""
    NOT_STARTED = "not_started"
    EXECUTING = "executing"
    EXECUTED = "executed"            # Forward 성공
    EXECUTE_FAILED = "execute_failed"  # Forward 실패
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"      # Compensate 성공
    COMPENSATE_FAILED = "compensate_failed"  # Compensate 실패


@dataclass
class SagaStepInstance:
    """Saga Step의 실행 인스턴스 (상태 추적용).

    RecoveryStep과 동일한 패턴:
    - order: 실행 순서
    - status: 현재 상태
    - started_at/completed_at: 시간 추적
    - error_message: 실패 원인
    """

    step_name: str
    """Step 이름 (SagaStep.name과 매핑)."""

    order: int
    """실행 순서 (0부터 시작)."""

    status: SagaStepStatus = SagaStepStatus.NOT_STARTED
    """현재 상태."""

    execute_started_at: str | None = None
    """Forward 실행 시작 시각 (ISO 8601)."""

    execute_completed_at: str | None = None
    """Forward 실행 완료 시각."""

    compensate_started_at: str | None = None
    """Compensate 시작 시각."""

    compensate_completed_at: str | None = None
    """Compensate 완료 시각."""

    error_message: str | None = None
    """실패 시 에러 메시지."""

    error_code: str | None = None
    """실패 시 에러 코드."""

    retry_count: int = 0
    """재시도 횟수."""

    next_retry_at: str | None = None
    """다음 재시도 예정 시각 (ISO 8601).

    v1.1.0 추가 (리뷰 R4 — Retry Strategy).

    Orchestrator가 BackoffStrategy.calculate()로 지연 시간을 계산한 후 설정.
    워커가 이 시각 이전에는 재시도를 시도하지 않는다.

    설계 근거:
    - FailedOperationData에 next_retry_at이 설계되었으나
      DTO 통합 과정에서 삭제됨 (195_DTO_UNIFICATION_PLAN.md L211).
    - Saga는 Step 단위 재시도이므로 SagaStepInstance에 배치가 적합.
    """

    partial_execution: bool = False
    """execute 중 외부 부작용(side-effect)이 발생했는지 여부.

    v1.1.0 추가 (리뷰 R5 — Partial Failure Cleanup).

    StepResult.partial_execution=True일 때 Orchestrator가 이 필드를 설정.
    EXECUTE_FAILED 상태여도 이 값이 True면 보상 대상에 포함된다.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "order": self.order,
            "status": self.status.value,
            "execute_started_at": self.execute_started_at,
            "execute_completed_at": self.execute_completed_at,
            "compensate_started_at": self.compensate_started_at,
            "compensate_completed_at": self.compensate_completed_at,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "partial_execution": self.partial_execution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaStepInstance:
        return cls(
            step_name=data["step_name"],
            order=data["order"],
            status=SagaStepStatus(data.get("status", "not_started")),
            execute_started_at=data.get("execute_started_at"),
            execute_completed_at=data.get("execute_completed_at"),
            compensate_started_at=data.get("compensate_started_at"),
            compensate_completed_at=data.get("compensate_completed_at"),
            error_message=data.get("error_message"),
            error_code=data.get("error_code"),
            retry_count=data.get("retry_count", 0),
            next_retry_at=data.get("next_retry_at"),
            partial_execution=data.get("partial_execution", False),
        )
```

### 2.6 SagaDefinition — Saga 정의

```python
@dataclass
class SagaDefinition:
    """Saga 정의. 이름과 Step 목록으로 구성.

    어댑터 레이어에서 생성하여 SagaRegistry에 등록.

    Usage:
        order_saga = SagaDefinition(
            name="order_creation",
            steps=[PaymentStep(), PointStep(), InventoryStep()],
            timeout_seconds=300,
            description="주문 생성 트랜잭션",
        )
        saga_registry.register(order_saga)
    """

    name: str
    """Saga 이름 (고유 식별자)."""

    version: int = 1
    """정의 스키마 버전.

    v1.1.0 추가 (리뷰 R1 — Saga Versioning).

    배포 도중 Step 구성이 변경되면 version을 증가시킨다.
    SagaInstance 생성 시 이 값이 definition_version으로 스냅샷된다.
    워커가 인스턴스를 픽업할 때 definition.version != instance.definition_version이면
    SUSPENDED 상태로 전환하여 수동 개입을 유도한다 (Fail-Safe).

    설계 근거: RecoverySession.version (recovery_state.py L239)이 OCC용으로
    이미 존재하지만 용도가 다르므로, 정의 스키마 버전은 별도 필드로 분리.
    """

    steps: list[SagaStep] = field(default_factory=list)
    """실행할 Step 목록 (순서대로 execute, 역순으로 compensate)."""

    timeout_seconds: int = 600
    """전체 Saga 타임아웃 (초). 기본 10분.

    개별 Step의 timeout_seconds가 None이면 이 값이 적용됨.
    """

    max_retries_per_step: int = 2
    """Step 실패 시 최대 재시도 횟수. StepResult.retryable=True일 때만 적용."""

    retry_backoff_strategy: str = "exponential"
    """재시도 백오프 전략.

    v1.1.0 추가 (리뷰 R4 — Retry Strategy + Jitter).

    기존 core/backoff.py의 BackoffStrategy 구현 중 선택:
    - "exponential": ExponentialBackoff (기본 20% Jitter 포함)
    - "linear": LinearBackoff
    - "constant": ConstantBackoff
    - "decorrelated": DecorrelatedJitterBackoff (AWS-style)

    기본값 "exponential" 선택 이유:
    - ExponentialBackoff.calculate() (core/backoff.py L85-92)가
      이미 jitter=True, jitter_factor=0.2로 Thundering Herd를 방어.
    - BackoffSettings (settings/backoff.py L25-170)의 환경변수로
      파라미터 튜닝 가능 (SELFHEALING_BACKOFF_EXPONENTIAL_*).

    자체 파라미터(retry_backoff_base_seconds 등)를 넣지 않는 이유:
    - 247번 문서의 "기존 인프라 최대 재사용" 원칙.
    - BackoffStrategy(ABC) + BackoffSettings + JitterSettings + calculate_jitter()가
      이미 완전한 프레임워크를 구성하고 있음.

    Usage (Orchestrator 내부):
        from selfhealing.core.backoff import get_backoff_calculator
        calculator = get_backoff_calculator(strategy=definition.retry_backoff_strategy)
        delay = calculator.calculate(attempt=step.retry_count + 1)
    """

    description: str = ""
    """Saga 설명 (감사 로그용)."""

    def validate(self) -> tuple[bool, str]:
        """Saga 정의 유효성 검증.

        Returns:
            (valid, error_message) 튜플
        """
        if not self.name:
            return False, "Saga name is required"
        if not self.steps:
            return False, "At least one step is required"

        # Step 이름 중복 검사
        names = [s.name for s in self.steps]
        duplicates = [n for n in names if names.count(n) > 1]
        if duplicates:
            return False, f"Duplicate step names: {set(duplicates)}"

        return True, ""
```

### 2.7 SagaInstance — Saga 실행 인스턴스

```python
@dataclass
class SagaInstance:
    """Saga 실행 인스턴스. 상태 추적 + 직렬화.

    RecoverySession과 동일한 패턴:
    - id: 고유 ID
    - status: 전체 상태
    - steps: Step 인스턴스 목록
    - current_step_index: 현재 진행 위치
    - to_dict()/from_dict(): 직렬화
    """

    id: str
    """고유 인스턴스 ID. 형식: "saga-{uuid}"."""

    saga_name: str
    """SagaDefinition.name 참조."""

    definition_version: int = 1
    """생성 시점의 SagaDefinition.version 스냅샷.

    v1.1.0 추가 (리뷰 R1 — Saga Versioning).

    Orchestrator가 인스턴스를 픽업할 때 현재 SagaDefinition.version과 비교.
    불일치 시 SUSPENDED 전환하여 버전 불일치 데이터 오염을 방지.

    설계 근거: RecoverySession.version (recovery_state.py L239)은 OCC용.
    이 필드는 정의 스키마 호환성 검증용으로, 용도가 명확히 다르다.
    """

    version: int = 0
    """OCC(Optimistic Concurrency Control)용 버전 카운터. 저장 시마다 +1 증가.

    v1.1.0 추가 (리뷰 R1).

    설계 근거: RecoverySession.version: int = 0 (recovery_state.py L239)과
    동일한 패턴 및 동일한 네이밍. 저장 시마다 증가하여 동시 쓰기를 감지한다.
    """

    status: SagaStatus = SagaStatus.PENDING
    """현재 상태."""

    step_instances: list[SagaStepInstance] = field(default_factory=list)
    """Step 실행 인스턴스 목록."""

    current_step_index: int = 0
    """현재 Forward 진행 중인 Step 인덱스 (0부터)."""

    compensate_step_index: int = -1
    """현재 Compensate 진행 중인 Step 인덱스.

    -1이면 compensate 미시작.
    COMPENSATING 상태에서 마지막 성공 Step부터 역순으로 감소.
    """

    context: SagaContext | None = None
    """실행 컨텍스트 (Step 간 데이터 공유)."""

    started_at: str | None = None
    """Saga 시작 시각 (ISO 8601)."""

    completed_at: str | None = None
    """Saga 완료 시각."""

    initiated_by: str = "system"
    """시작 주체. "system" 또는 사용자 ID."""

    error_message: str | None = None
    """최종 에러 메시지 (실패 시)."""

    correlation_id: str | None = None
    """EventBus correlation_id (추적용)."""

    metadata: dict[str, Any] | None = None
    """추가 메타데이터."""

    def get_current_step(self) -> SagaStepInstance | None:
        """현재 Forward 진행 중인 Step 반환."""
        if 0 <= self.current_step_index < len(self.step_instances):
            return self.step_instances[self.current_step_index]
        return None

    def get_compensate_step(self) -> SagaStepInstance | None:
        """현재 Compensate 대상 Step 반환."""
        if 0 <= self.compensate_step_index < len(self.step_instances):
            return self.step_instances[self.compensate_step_index]
        return None

    def get_executed_steps(self) -> list[SagaStepInstance]:
        """Forward 실행이 성공한 Step 목록."""
        return [
            s for s in self.step_instances
            if s.status == SagaStepStatus.EXECUTED
        ]

    def get_compensation_targets(self) -> list[SagaStepInstance]:
        """역순 compensate 대상 Step 목록.

        v1.1.0 추가 (리뷰 R5 — Partial Failure Cleanup).

        기존 get_executed_steps()는 EXECUTED 상태만 반환하여,
        partial_execution=True인 EXECUTE_FAILED Step이 누락되었다.
        이 메서드는 보상이 필요한 모든 Step을 반환한다.

        설계 근거: _attempt_compensation() (recovery_coordinator.py L1855)이
        step.status == RecoveryStatus.COMPLETED만 필터하여 동일한 문제가 있었음.
        """
        return [
            s for s in self.step_instances
            if s.status == SagaStepStatus.EXECUTED
            or (s.status == SagaStepStatus.EXECUTE_FAILED and s.partial_execution)
        ]

    def get_progress(self) -> dict[str, Any]:
        """진행 상황 요약. RecoverySession.get_progress()와 동일 패턴."""
        total = len(self.step_instances)
        executed = sum(1 for s in self.step_instances if s.status == SagaStepStatus.EXECUTED)
        compensated = sum(1 for s in self.step_instances if s.status == SagaStepStatus.COMPENSATED)

        return {
            "saga_name": self.saga_name,
            "status": self.status.value,
            "total_steps": total,
            "executed_steps": executed,
            "compensated_steps": compensated,
            "progress_percent": (executed / total * 100) if total > 0 else 0,
            "current_step_index": self.current_step_index,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "saga_name": self.saga_name,
            "definition_version": self.definition_version,
            "version": self.version,
            "status": self.status.value,
            "step_instances": [s.to_dict() for s in self.step_instances],
            "current_step_index": self.current_step_index,
            "compensate_step_index": self.compensate_step_index,
            "context": self.context.to_dict() if self.context else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "initiated_by": self.initiated_by,
            "error_message": self.error_message,
            "correlation_id": self.correlation_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaInstance:
        step_instances = [
            SagaStepInstance.from_dict(s) for s in data.get("step_instances", [])
        ]
        context = (
            SagaContext.from_dict(data["context"])
            if data.get("context")
            else None
        )
        return cls(
            id=data["id"],
            saga_name=data["saga_name"],
            definition_version=data.get("definition_version", 1),
            version=data.get("version", 0),
            status=SagaStatus(data.get("status", "pending")),
            step_instances=step_instances,
            current_step_index=data.get("current_step_index", 0),
            compensate_step_index=data.get("compensate_step_index", -1),
            context=context,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            initiated_by=data.get("initiated_by", "system"),
            error_message=data.get("error_message"),
            correlation_id=data.get("correlation_id"),
            metadata=data.get("metadata"),
        )
```

---

## 3. Saga 레지스트리

```python
_saga_definitions: dict[str, SagaDefinition] = {}


def register_saga(definition: SagaDefinition) -> None:
    """Saga 정의 등록.

    ReplayHandler 레지스트리와 동일 패턴:
    - handlers.py L131: register_replay_handler(handler)
    - handlers.py L136: _replay_handlers[handler.domain] = handler

    동일 이름으로 재등록 시 기존 정의를 덮어쓴다 (버전 업그레이드 지원).
    단, 진행 중인 인스턴스는 구 버전의 definition_version을 보유하므로
    Orchestrator가 버전 불일치를 감지하여 SUSPENDED 처리한다.

    Usage (어댑터 레이어):
        from selfhealing.services.saga import register_saga, SagaDefinition
        from myapp.saga_steps import PaymentStep, PointStep, InventoryStep

        register_saga(SagaDefinition(
            name="order_creation",
            version=1,
            steps=[PaymentStep(), PointStep(), InventoryStep()],
        ))
    """
    valid, error = definition.validate()
    if not valid:
        raise ValueError(f"Invalid saga definition '{definition.name}': {error}")
    _saga_definitions[definition.name] = definition


def get_saga_definition(name: str) -> SagaDefinition | None:
    """등록된 Saga 정의 조회."""
    return _saga_definitions.get(name)


def list_saga_definitions() -> list[str]:
    """등록된 모든 Saga 이름 목록."""
    return list(_saga_definitions.keys())
```

---

## 4. 기존 코드와의 비교표

| 기준 | 기존 (RecoveryCoordinator) | 신규 (Saga) |
|------|---------------------------|-------------|
| **Step ABC** | 없음 (Callable만) | `SagaStep(ABC)` — execute + compensate |
| **Step 등록** | `register_step_handler(type, handler)` | `register_saga(SagaDefinition)` |
| **Step 상태** | `RecoveryStep.status: RecoveryStatus` | `SagaStepInstance.status: SagaStepStatus` |
| **세션** | `RecoverySession` | `SagaInstance` |
| **전체 상태** | `RecoveryStatus` (NOT_STARTED→IN_PROGRESS→COMPLETED/FAILED) | `SagaStatus` (PENDING→RUNNING→COMPLETED/COMPENSATING→COMPENSATED) |
| **데이터 전달** | `RecoveryStep.params: dict` | `SagaContext` (Step 간 데이터 merge) |
| **직렬화** | `to_dict()/from_dict()` | `to_dict()/from_dict()` (동일 패턴) |
| **타임아웃** | `RecoveryStep.wait_after_seconds` (대기용) | `SagaStep.timeout_seconds` + `SagaDefinition.timeout_seconds` |
| **버전 관리** | `RecoverySession.version` (OCC만) | `SagaDefinition.version` (스키마) + `SagaInstance.version` (OCC) + `SagaInstance.definition_version` (호환성) |
| **데이터 격리** | `RecoveryStep.params` (Step별 독립 dict) | `SagaContext.get_step_data()` / `get_from_step()` (네임스페이스 격리) |
| **보상 맥락** | `RecoverySession.abort_reason` | `SagaContext.abort_reason` + `failed_step_name` + `abort_error_code` |
| **부분 실패** | 미지원 (COMPLETED만 보상 대상) | `StepResult.partial_execution` + `get_compensation_targets()` |
| **백오프** | 미지원 (자체 없음) | `SagaDefinition.retry_backoff_strategy` → `core/backoff.py` 재사용 |
| **레지스트리 생명주기** | `SelfHealingConfig.ready()`에서 handler 등록 | `AppConfig.ready()` 동일 패턴 + 버전 재등록(덮어쓰기) + 미등록 시 SUSPENDED Fail-Safe |

---

## 5. 파일 구조

```
services/saga/
├── __init__.py          # 패키지 export (replay_service/__init__.py 동일 패턴)
├── models.py            # SagaStatus, StepResult, SagaContext, SagaStepStatus,
│                        # SagaStepInstance, SagaDefinition, SagaInstance
├── step.py              # SagaStep(ABC)
├── registry.py          # register_saga(), get_saga_definition()
├── orchestrator.py      # SagaOrchestrator (249번 문서)
└── events.py            # Saga EventType 확장
```

패키지 구조는 `services/replay_service/` 패턴을 따름:
- `models.py`: 데이터 클래스
- `handlers.py` → `step.py`: ABC 정의
- `service.py` → `orchestrator.py`: 핵심 로직

---

## 6. 리뷰 반영 상세 (v1.1.0)

### 6.1 R1 — Saga Versioning (스키마 버전과 OCC 이중 관리)

**변경 내용**:
- `SagaDefinition.version: int = 1` 추가
- `SagaInstance.definition_version: int = 1` 추가 (정의 스키마 호환성 검증)
- `SagaInstance.version: int = 0` 추가 (OCC 동시성 제어)

**코드 근거**:
- `RecoverySession.version: int = 0` (`recovery_state.py` L239) — OCC 패턴 선례
- `RecoverySession.to_dict()`에 `"version": self.version` 포함 (`recovery_state.py` L322)

**Orchestrator 버전 검증 로직** (249번 문서에서 구현):

```python
def _validate_version_compatibility(self, instance: SagaInstance) -> bool:
    """인스턴스의 정의 버전과 현재 등록된 정의 버전의 호환성 검증.

    버전 불일치 시 SUSPENDED 전환: 데이터 오염 방지를 위한 Fail-Safe.
    """
    definition = get_saga_definition(instance.saga_name)
    if definition is None:
        instance.status = SagaStatus.SUSPENDED
        instance.error_message = (
            f"Saga definition '{instance.saga_name}' not found in registry"
        )
        return False
    if definition.version != instance.definition_version:
        instance.status = SagaStatus.SUSPENDED
        instance.error_message = (
            f"Definition version mismatch: "
            f"instance={instance.definition_version}, current={definition.version}"
        )
        return False
    return True
```

### 6.2 R2 — SagaContext Data Namespace (키 충돌 방지)

**변경 내용**:
- `SagaContext.get_step_data(step_name)` 메서드 추가
- `SagaContext.get_from_step(step_name, key)` 메서드 추가
- `SagaContext.get()` 경고 문서 추가

**코드 근거**:
- `RecoveryStep.params: dict` (`recovery_state.py` L100-110) — Step별 독립 dict로 격리
- `step_results: dict[str, dict[str, Any]]`가 이미 step_name 키로 분리 저장

**선택 근거**: `ctx.get("payment.tx_id")` 같은 점 표기법 대신 `get_from_step("payment", "tx_id")`를 선택한 이유:
1. 기존 `get()` 시그니처와의 하위 호환성 유지
2. 점 표기법은 key에 `.`이 포함된 경우 파싱 모호성 발생
3. 명시적 메서드가 IDE 자동완성에 유리

### 6.3 R3 — Compensation Context (보상 시 실패 원인 전달)

**변경 내용**:
- `SagaContext.failed_step_name: str | None` 추가
- `SagaContext.abort_reason: str | None` 추가
- `SagaContext.abort_error_code: str | None` 추가
- `SagaContext.set_compensation_context()` 메서드 추가
- `SagaContext.to_dict()`/`from_dict()` 갱신

**코드 근거**:
- `RecoverySession.abort_reason: str | None` (`recovery_state.py` L257)
- `_fail_session()` (`recovery_coordinator.py` L1638-1640):
  `session.abort_reason = error` — 보상 루프 전에 설정

**`abort_reason` 네이밍 선택 근거**:
`RecoverySession.abort_reason`과 동일한 이름을 사용하여 시스템 전반의 용어 일관성 유지.
`compensation_reason`, `failure_reason` 등 대안보다 기존 코드베이스와의 매핑이 명확.

### 6.4 R4 — Retry Strategy (기존 BackoffStrategy 재사용 + Jitter)

**변경 내용**:
- `SagaDefinition.retry_backoff_strategy: str = "exponential"` 추가
- `SagaStepInstance.next_retry_at: str | None` 추가

**코드 근거** (자체 파라미터 대신 기존 인프라 재사용을 선택한 이유):

| 기존 인프라 | 파일 | Saga 활용 |
|---|---|---|
| `BackoffStrategy(ABC)` | `core/backoff.py` L19 | 전략 ABC |
| `ExponentialBackoff(jitter=True, jitter_factor=0.2)` | `core/backoff.py` L43-92 | **기본 20% 양방향 Jitter 포함** → Thundering Herd 방어 |
| `DecorrelatedJitterBackoff` | `core/backoff.py` L201 | AWS-style 대안 |
| `get_backoff_calculator(strategy)` | `core/backoff.py` L262 | 팩토리 함수 |
| `BackoffSettings` | `settings/backoff.py` L25-170 | 환경변수 통합 튜닝 |
| `calculate_jitter()` | `utils/jitter.py` L84 | 범용 Jitter 유틸 |
| `JitterSettings` | `settings/jitter.py` L28 | Jitter 설정 |

**Thundering Herd 방어 테스트도 이미 존재**:
- `test_jitter_prevents_thundering_herd()` (`tests/self_healing/resilience/test_rate_limit.py` L537)
- `test_jitter_prevents_thundering_herd()` (`tests/self_healing/integration/test_architectural_resilience_e2e.py` L156)

**Orchestrator에서의 사용** (249번 문서에서 구현):

```python
from selfhealing.core.backoff import get_backoff_calculator

def _calculate_next_retry_at(
    self,
    definition: SagaDefinition,
    step: SagaStepInstance,
) -> str:
    """다음 재시도 시각 계산.

    기존 BackoffStrategy 프레임워크를 재사용하여
    전략별 Jitter가 자동으로 포함된다.
    """
    calculator = get_backoff_calculator(strategy=definition.retry_backoff_strategy)
    delay = calculator.calculate(attempt=step.retry_count + 1)
    next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    return next_at.isoformat()
```

### 6.5 R5 — Partial Failure Cleanup (부분 실행 보상 처리)

**변경 내용**:
- `StepResult.partial_execution: bool = False` 추가
- `StepResult.failed_with_side_effect()` 팩토리 메서드 추가
- `SagaStepInstance.partial_execution: bool = False` 추가
- `SagaInstance.get_compensation_targets()` 메서드 추가

**코드 근거**:
- `_attempt_compensation()` (`recovery_coordinator.py` L1855):
  `completed_steps = [step for step in session.steps if step.status == RecoveryStatus.COMPLETED]`
  — COMPLETED만 보상 대상, 부분 실행된 Step 누락
- `RecoveryStep.result_data` (`recovery_state.py` L118-132):
  "무엇을 했는지 알아야 되돌릴 수 있다" — data 필드로 부분 결과 전달
- `ReplayResult.blocked()` (`replay_service/models.py` L43-50):
  팩토리 메서드 패턴 선례

**보상 대상 판단 흐름**:

```
Step.execute() 호출
├── StepResult(success=True)  → status=EXECUTED        → 보상 대상 ✅
├── StepResult(success=False, partial_execution=False)  → status=EXECUTE_FAILED → 보상 제외 ❌
└── StepResult(success=False, partial_execution=True)   → status=EXECUTE_FAILED → 보상 대상 ✅
    (failed_with_side_effect 팩토리로 생성)
```

### 6.6 R6 — Saga Registry Lifecycle (초기화 가이드)

레지스트리 초기화는 기존 `AppConfig.ready()` 패턴을 따른다.

**코드 근거**:
- `SelfHealingConfig.ready()` (`adapters/django/apps.py` L115-175):
  셀프힐링 시스템 초기화의 중앙 진입점
- `register_shopping_handlers()` (`shopping/handlers/replay_handlers.py` L270-286):
  어댑터 레이어에서 `AppConfig.ready()`에 등록하는 실제 사례
- `DefaultReplayHandler` (`replay_service/handlers.py` L76-91):
  미등록 도메인에 대한 Fail-Safe 기본값 패턴

**어댑터 레이어 등록 가이드** (도메인-프리 예시):

```python
# myapp/saga_definitions.py  (호스트 앱에서 구현)
def register_app_sagas() -> None:
    """호스트 앱의 Saga 정의 등록.

    AppConfig.ready()에서 호출하여 워커 부트스트랩 시
    레지스트리가 자동으로 채워지도록 한다.

    패턴 근거: register_replay_handler() 등록 방식
    (replay_service/handlers.py L96-123의 가이드와 동일)
    """
    from selfhealing.services.saga import register_saga, SagaDefinition
    from myapp.saga_steps import PaymentStep, PointStep, InventoryStep

    register_saga(SagaDefinition(
        name="order_creation",
        version=1,
        steps=[PaymentStep(), PointStep(), InventoryStep()],
        timeout_seconds=300,
        description="주문 생성 트랜잭션",
    ))


# myapp/apps.py
from django.apps import AppConfig

class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        from myapp.saga_definitions import register_app_sagas
        register_app_sagas()
```

**미등록 Saga 방어 로직** (Orchestrator):

```python
# get_saga_definition()이 None을 반환하면 SUSPENDED 처리
definition = get_saga_definition(instance.saga_name)
if definition is None:
    instance.status = SagaStatus.SUSPENDED
    instance.error_message = (
        f"Saga definition '{instance.saga_name}' not found in registry"
    )
    # DLQ에 저장하여 운영자 알림
```

이는 `DefaultReplayHandler`가 미등록 도메인에 대해
`"No replay handler registered for domain"` 에러를 반환하는
(`handlers.py` L76-91) Fail-Safe 패턴과 동일한 전략이다.

---

## 7. 네이밍 검증 — 기존 코드베이스 충돌 여부

| 신규 네이밍 | 코드베이스 존재 여부 | 판정 |
|---|---|---|
| `SagaDefinition.version` | `RecoverySession.version` (다른 클래스, OCC용) | ✅ 충돌 없음 |
| `SagaInstance.definition_version` | 없음 | ✅ |
| `SagaInstance.version` | `RecoverySession.version`과 동일 패턴 (의도적) | ✅ |
| `SagaContext.get_step_data()` | 없음 | ✅ |
| `SagaContext.get_from_step()` | 없음 | ✅ |
| `SagaContext.failed_step_name` | 없음 | ✅ |
| `SagaContext.abort_reason` | `RecoverySession.abort_reason`과 동일 네이밍 (의도적 일관성) | ✅ |
| `SagaContext.abort_error_code` | 없음 | ✅ |
| `SagaContext.set_compensation_context()` | 없음 | ✅ |
| `SagaDefinition.retry_backoff_strategy` | 없음 | ✅ |
| `SagaStepInstance.next_retry_at` | `FailedOperationData.next_retry_at` 존재했으나 삭제됨 (다른 클래스) | ✅ |
| `SagaStepInstance.partial_execution` | 없음 | ✅ |
| `StepResult.partial_execution` | 없음 | ✅ |
| `StepResult.failed_with_side_effect()` | 없음 | ✅ |
| `SagaInstance.get_compensation_targets()` | 없음 | ✅ |
