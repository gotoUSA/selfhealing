# 248. Saga 코어 모델 설계

> **Version**: 1.0.0
> **Created**: 2026-02-19
> **Status**: Approved
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

    @classmethod
    def succeeded(cls, data: dict[str, Any] | None = None) -> StepResult:
        return cls(success=True, data=data or {})

    @classmethod
    def failed(cls, error: str, error_code: str = "", retryable: bool = False) -> StepResult:
        return cls(success=False, error=error, error_code=error_code, retryable=retryable)
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

    def get(self, key: str, default: Any = None) -> Any:
        """초기 데이터 + 모든 Step 결과에서 key 검색.

        검색 순서: step_results (최신 먼저) → initial_data
        """
        # Step 결과에서 검색 (나중 Step 우선)
        for step_data in reversed(list(self.step_results.values())):
            if key in step_data:
                return step_data[key]
        # 초기 데이터에서 검색
        return self.initial_data.get(key, default)

    def merge_step_result(self, step_name: str, data: dict[str, Any]) -> None:
        """Step 실행 결과를 컨텍스트에 merge."""
        self.step_results[step_name] = data

    def to_dict(self) -> dict[str, Any]:
        return {
            "saga_instance_id": self.saga_instance_id,
            "initial_data": self.initial_data,
            "step_results": self.step_results,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaContext:
        return cls(
            saga_instance_id=data["saga_instance_id"],
            initial_data=data.get("initial_data", {}),
            step_results=data.get("step_results", {}),
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

    steps: list[SagaStep] = field(default_factory=list)
    """실행할 Step 목록 (순서대로 execute, 역순으로 compensate)."""

    timeout_seconds: int = 600
    """전체 Saga 타임아웃 (초). 기본 10분.

    개별 Step의 timeout_seconds가 None이면 이 값이 적용됨.
    """

    max_retries_per_step: int = 2
    """Step 실패 시 최대 재시도 횟수. StepResult.retryable=True일 때만 적용."""

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
        """Forward 실행이 성공한 Step 목록 (역순 compensate 대상)."""
        return [
            s for s in self.step_instances
            if s.status == SagaStepStatus.EXECUTED
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

    Usage (어댑터 레이어):
        from selfhealing.services.saga import register_saga, SagaDefinition
        from myapp.saga_steps import PaymentStep, PointStep, InventoryStep

        register_saga(SagaDefinition(
            name="order_creation",
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
