# 274. Runbook Registry 설계

## 1. 목적

"패턴 X가 매칭되면 절차 Y를 실행하라"는 선언적 매핑을 한 곳에서 관리한다. 런북의 등록, 조회, 활성화/비활성화를 담당한다.

## 2. 기존 레지스트리 패턴 참조 — 코드 근거

`ProviderRegistry` (`factory.py`)의 패턴을 따른다:

```python
# factory.py (기존 코드 패턴)
class ProviderRegistry:
    _cache_providers: ClassVar[dict[str, type]] = {}

    @classmethod
    def register_cache(cls, name: str, provider_class: type) -> None: ...

    @classmethod
    def get_cache(cls, name: str | None = None) -> CacheProviderInterface: ...
```

`RecoveryCoordinator`의 `register_step_handler()` 패턴도 참조:

```python
# coordination/recovery_coordinator/__init__.py (기존 코드)
def register_step_handler(
    self,
    step_type: RecoveryStepType,
    handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
    compensate: Callable[[RecoverySession, RecoveryStep], dict[str, Any]] | None = None,
) -> None:
```

**차이점:** `RecoveryCoordinator`는 `RecoveryStepType` enum에 고정되지만, 런북 레지스트리는 **임의의 문자열 ID로 런북을 등록**한다.

## 3. 데이터 모델

### 3.1 RiskLevel — 위험도 분류

```python
class RiskLevel(str, Enum):
    """런북 위험도.

    276번 문서(ApprovalGate)에서 이 값에 따라 승인 방식이 결정된다.
    """
    LOW = "low"        # 즉시 자동 실행
    MEDIUM = "medium"  # 알림 후 타이머 대기
    HIGH = "high"      # 수동 승인 필수
```

### 3.2 RunbookStep — 단일 실행 단계

```python
@dataclass
class RunbookStep:
    """런북의 단일 실행 단계.

    SagaStep ABC와 대응:
    - action → SagaStep.name
    - params → SagaContext.initial_data
    - on_failure → SagaStep.compensate()
    - can_execute_condition → SagaStep.can_execute()
    - timeout_seconds → SagaStep.timeout_seconds

    설계 근거: saga/step.py의 SagaStep(ABC):
        execute(ctx: SagaContext) -> StepResult
        compensate(ctx: SagaContext) -> StepResult
        can_execute(ctx: SagaContext) -> tuple[bool, str]
        timeout_seconds -> int | None
    """
    name: str                    # step 식별자 (예: "kill_idle_connections")
    action: str                  # 실행할 action primitive (예: "db.kill_idle")
    params: dict[str, Any] = field(default_factory=dict)

    # 보상 (SagaStep.compensate 대응)
    on_failure_action: str | None = None     # 실패 시 보상 action
    on_failure_params: dict[str, Any] = field(default_factory=dict)

    # 검증 게이트
    validation_action: str | None = None     # step 완료 후 검증 action
    validation_params: dict[str, Any] = field(default_factory=dict)

    # 조건부 실행 (SagaStep.can_execute 대응)
    condition: str | None = None   # "prev_step_failed", "prev_step_succeeded", None(항상)

    # 실행 제어
    timeout_seconds: int = 120     # step 타임아웃
    wait_after_seconds: int = 0    # step 완료 후 대기 (안정화)

    # 멱등성 힌트 (IdempotentStepHandler 연동)
    idempotent: bool = True        # state-based action이면 True
```

### 3.3 Runbook — 런북 정의

```python
@dataclass
class Runbook:
    """런북 정의.

    ProviderRegistry 패턴을 따라 id로 등록/조회한다.
    시스템 제어 API에서 enabled 토글 가능.

    RecoveryCoordinator의 DEFAULT_RECOVERY_STEPS와 대응:
        RecoveryCoordinator: {"LEVEL_3": [RecoveryStep(...)]}  ← 하드코딩
        RunbookRegistry: {runbook_id: Runbook(...)}            ← 선언적
    """
    id: str                            # 고유 식별자 (예: "db_pool_recovery")
    name: str                          # 사람이 읽을 수 있는 이름
    description: str                   # 설명

    # 트리거 조건 (273번 문서의 PatternCondition)
    trigger: PatternCondition

    # 실행 단계
    steps: list[RunbookStep]

    # 위험도 (276번 문서의 ApprovalGate에서 사용)
    risk_level: RiskLevel = RiskLevel.LOW

    # 활성화 상태
    enabled: bool = True

    # 우선순위 (같은 조건에 여러 런북이 매칭될 때)
    priority: int = 100               # 낮을수록 높은 우선순위

    # 쿨다운 (동일 런북 재트리거 방지)
    cooldown_seconds: int = 300       # 기본 5분

    # 메타데이터
    version: int = 1
    created_by: str = "system"
    tags: list[str] = field(default_factory=list)

    def validate(self) -> tuple[bool, str]:
        """런북 정의 유효성 검증.

        SagaDefinition.validate()와 동일한 패턴:
        - steps가 비어있지 않은지
        - action이 유효한지
        - step name이 중복되지 않는지
        """
        ...
```

## 4. RunbookRegistry 클래스

```python
class RunbookRegistry:
    """런북 등록/조회/관리.

    ProviderRegistry 패턴을 따른다.
    싱글톤으로 운영.

    차이점:
    - ProviderRegistry: classmethod 기반 정적 레지스트리
    - RunbookRegistry: 인스턴스 기반 — 런타임 등록/비활성화 지원
    """

    def __init__(self) -> None:
        self._runbooks: dict[str, Runbook] = {}   # id → Runbook
        self._lock: threading.Lock = threading.Lock()

    def register(self, runbook: Runbook) -> None:
        """런북 등록.

        Args:
            runbook: 등록할 Runbook 인스턴스

        Raises:
            ValueError: 유효성 검증 실패 시

        RecoveryCoordinator.register_step_handler()와 유사하나,
        key가 enum이 아닌 문자열 id이므로 코드 변경 없이 추가 가능.
        """
        ...

    def unregister(self, runbook_id: str) -> bool:
        """런북 등록 해제."""
        ...

    def get(self, runbook_id: str) -> Runbook | None:
        """런북 조회."""
        ...

    def get_enabled(self) -> list[Runbook]:
        """활성화된 런북 목록 조회.

        PatternMatcher.evaluate_all()에서 호출.
        """
        ...

    def set_enabled(self, runbook_id: str, enabled: bool) -> bool:
        """런북 활성화/비활성화.

        런타임에 특정 런북을 끌 수 있다.
        GovernanceCheckMixin의 Kill Switch와 유사한 역할.
        """
        ...

    def get_all(self) -> list[Runbook]:
        """전체 런북 목록 (비활성화 포함)."""
        ...

    def get_by_tag(self, tag: str) -> list[Runbook]:
        """태그로 런북 필터."""
        ...
```

## 5. Action Primitive 레지스트리

런북의 `RunbookStep.action` 필드가 참조하는 **내장 실행 단위**. 각 primitive는 `ActionExecutor`를 통해 실행된다.

```python
class ActionPrimitiveRegistry:
    """action 문자열 → 실행 함수 매핑.

    ActionExecutor의 Action 객체를 생성하는 팩토리.

    코드 근거:
    - Action(name, target, execute_fn, params, validate_fn)
      → core/action_executor.py
    - RecoveryCoordinator._step_handlers dict
      → coordination/recovery_coordinator/__init__.py
    """

    # 내장 primitive 카테고리
    BUILTIN_CATEGORIES = {
        "config":    "설정 변경 (RuntimeConfigManager 연동)",
        "assert":    "검증 게이트 (메트릭 조건 확인)",
        "notify":    "알림 발송 (UnifiedNotificationManager 연동)",
        "recovery":  "기존 복구 컴포넌트 호출",
        "emergency": "Emergency Mode 제어",
        "wait":      "대기 (안정화 확인)",
    }

    def __init__(self) -> None:
        self._primitives: dict[str, Callable] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        """내장 primitive 등록.

        각 primitive는 state-based로 설계 (멱등성 보장):
        - config.set(key, value) — 설정을 value로 설정 (=상태 기반)
        - assert.metric(metric_name, operator, threshold) — 현재 값 검증
        - notify.send(title, message, priority) — 알림 발송
        - recovery.start(trigger_level) — RecoveryCoordinator.start_recovery() 호출
        - emergency.activate(level, reason) — GracefulDegradationManager.activate_auto() 호출
        - emergency.deactivate(reason) — GracefulDegradationManager.deactivate() 호출
        - wait.stabilize(seconds, assert_metric, threshold) — 대기 + 검증
        """
        ...

    def register(self, action_name: str, handler: Callable) -> None:
        """커스텀 primitive 등록.

        사용자가 Python 함수를 action으로 등록할 수 있다.
        """
        ...

    def get(self, action_name: str) -> Callable | None:
        """action 이름으로 실행 함수 조회."""
        ...

    def create_action(self, action_name: str, params: dict) -> Action:
        """action 이름 + params로 ActionExecutor용 Action 객체 생성.

        Returns:
            core/action_executor.py의 Action(
                name=action_name,
                target=params.get("target", "runbook"),
                execute_fn=primitive_fn,
                params=params,
            )
        """
        ...
```

## 6. 런북 정의 예시

```python
# 실제 등록 코드 예시
registry = RunbookRegistry()

registry.register(Runbook(
    id="db_pool_recovery",
    name="DB 커넥션 풀 복구",
    description="에러율 상승 + DB 풀 사용률 높을 때 idle 커넥션 정리 후 안정화 확인",
    trigger=PatternCondition(
        metric_conditions=[
            MetricCondition("error_rate", ConditionOperator.GT, 0.05),
            MetricCondition("db_pool_usage", ConditionOperator.GT, 0.9),
        ],
        event_conditions=[
            EventCondition(event_type="circuit_breaker_opened"),
        ],
    ),
    risk_level=RiskLevel.MEDIUM,
    steps=[
        RunbookStep(
            name="kill_idle_connections",
            action="db.kill_idle",
            params={"threshold_seconds": 300},
            on_failure_action="notify.send",
            on_failure_params={"title": "DB idle kill 실패", "priority": "high"},
            idempotent=True,
        ),
        RunbookStep(
            name="verify_pool",
            action="assert.metric",
            params={"metric_name": "db_pool_usage", "operator": "lt", "threshold": 0.8},
            timeout_seconds=60,
        ),
        RunbookStep(
            name="increase_pool_if_needed",
            action="config.set",
            params={"key": "db_max_connections", "value_delta": 20},
            condition="prev_step_failed",  # 검증 실패 시만 실행
            on_failure_action="config.set",
            on_failure_params={"key": "db_max_connections", "value_delta": -20},
            idempotent=True,
        ),
        RunbookStep(
            name="stabilize",
            action="wait.stabilize",
            params={"seconds": 300, "assert_metric": "error_rate", "threshold": 0.03},
            timeout_seconds=360,
        ),
    ],
    cooldown_seconds=600,
    tags=["database", "connection_pool"],
))
```

## 7. RecoveryCoordinator의 DEFAULT_RECOVERY_STEPS와의 관계

`RecoveryCoordinator`의 하드코딩된 step 목록은 **그대로 유지**한다:

```python
# 기존 코드 (변경하지 않음)
DEFAULT_RECOVERY_STEPS = {
    "LEVEL_3": [
        RecoveryStep(step_type=RecoveryStepType.BUDGET_RESET, order=1, ...),
        RecoveryStep(step_type=RecoveryStepType.HEALTH_CHECK, order=2, ...),
        RecoveryStep(step_type=RecoveryStepType.CANARY_RESUME, order=3, ...),
        RecoveryStep(step_type=RecoveryStepType.GOVERNANCE_NORMAL, order=4, ...),
    ],
}
```

런북은 이 복구 절차를 **호출하는 상위 레이어**이다:

```python
RunbookStep(
    name="emergency_recovery",
    action="recovery.start",
    params={"namespace": "global", "trigger_level": "LEVEL_3"},
)
```

## 8. 참조

- `ProviderRegistry`: `factory.py` — 등록/조회 패턴
- `RecoveryCoordinator.register_step_handler()`: `coordination/recovery_coordinator/__init__.py`
- `RecoveryCoordinator.DEFAULT_RECOVERY_STEPS`: 동일 파일
- `SagaStep` ABC: `services/saga/step.py`
- `SagaDefinition.validate()`: `services/saga/models.py`
- `Action` dataclass: `core/action_executor.py`
- `ActionExecutor.execute()`: 동일 파일
- `PatternCondition`: 273번 문서
