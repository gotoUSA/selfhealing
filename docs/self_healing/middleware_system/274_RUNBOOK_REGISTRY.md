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
    # 템플릿 변수 지원 — §11 컨텍스트 변수 보간 참조
    # 예: {"target_service": "{event_source}", "threshold": 300}
    # 275번 Executor가 실행 시점에 format_map(SafeFormatDict(context))으로 치환

    # 보상 (SagaStep.compensate 대응)
    on_failure_action: str | None = None     # 실패 시 보상 action
    on_failure_params: dict[str, Any] = field(default_factory=dict)

    # 검증 게이트
    validation_action: str | None = None     # step 완료 후 검증 action
    validation_params: dict[str, Any] = field(default_factory=dict)

    # 조건부 실행 — §3.4 StepCondition 참조
    # SagaStep.can_execute(ctx) 대응. 평가는 순수 함수(Pure Function) 원칙 (§3.4 참조).
    condition: StepCondition | None = None   # None이면 항상 실행

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
    # PatternMatcher의 RunbookLike Protocol이 trigger_condition 프로퍼티를 요구하므로
    # 필드명을 trigger_condition으로 지정한다 (pattern_matcher.py RunbookLike 참조)
    trigger_condition: PatternCondition

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

        추가 검증 (§8 파라미터 스키마 검증):
        - 각 Step의 params를 ActionPrimitiveRegistry의 Pydantic 스키마로 사전 검증
        - 템플릿 변수({...}) 포함 값은 정적 검증에서 제외, 실행 시점에 재검증
        """
        ...
```

### 3.4 StepCondition — 데이터 기반 분기

```python
@dataclass
class StepCondition:
    """Step 실행 조건 — 선언적 분기.

    SagaStep.can_execute(ctx)가 SagaContext 전체에 접근하여 임의 조건을
    평가하는 것과 대응. RunbookStep에서는 선언적 조건으로 정의하고,
    275번 Executor가 SagaStep.can_execute() 구현으로 변환한다.

    **순수 함수(Pure Function) 원칙:**
    평가 엔진은 SagaContext.step_results 내 스냅샷 데이터만 읽어 판단한다.
    외부 I/O(메트릭 조회, DB 쿼리) 금지.

    코드 근거 — 순수 함수 원칙이 이미 적용된 사례:
    - SagaContext.get_from_step(step_name, key, default) → saga/models.py
      → step_results dict 조회만 수행, 외부 I/O 없음
    - PatternMatcher._resolve_labeled_metrics()
      → resolved = dict(snapshot) — 원본 불변성 유지 후 read-only 평가
      → services/runbook/pattern_matcher.py

    구조 근거:
    - PatternCondition의 MetricCondition(metric_name, operator, threshold) 재사용
      → 273번 문서
    """
    type: str                        # "always", "prev_failed", "prev_succeeded", "data_match"

    # data_match 전용 필드 — SagaContext.get_from_step() 기반
    source_step: str | None = None   # 참조할 이전 Step 이름
    field: str | None = None         # Step 결과의 필드명
    operator: str | None = None      # "eq", "gt", "lt", "gte", "lte", "contains", "not_none"
    value: Any = None                # 비교 대상값
```

**사용 예시:**

```python
# 이전 Step 실패 시만 실행 (기존 "prev_step_failed"에 대응)
condition=StepCondition(type="prev_failed")

# 이전 Step의 결과 데이터 기반 분기
condition=StepCondition(
    type="data_match",
    source_step="verify_pool",          # 참조할 Step
    field="current_usage",              # StepResult.data의 키
    operator="gt",                      # 비교 연산자
    value=0.8,                          # 임계값
)
# → SagaContext.get_from_step("verify_pool", "current_usage") > 0.8 이면 실행
```

### 3.5 RunbookStepContext — Action Handler 실행 컨텍스트

```python
@dataclass
class RunbookStepContext:
    """Action primitive에 전달되는 실행 컨텍스트.

    SagaContext의 경량 버전 — Step 간 데이터 전달 + 실행 메타데이터.

    SagaContext와의 차이:
    - SagaContext: Orchestrator 내부용 (전체 Saga 상태 추적, 직렬화)
    - RunbookStepContext: Action Primitive용 (이전 Step 결과 + 실행 메타데이터)

    코드 근거:
    - SagaContext.step_results: dict[str, dict] → saga/models.py
      → prev_results가 동일한 {step_name: result_data} 구조
    - Action(name, target, execute_fn, params) → core/action_executor.py
      → params와 execution_id가 Action 생성 시 바인딩
    """
    runbook_id: str                       # 소속 런북 ID
    step_name: str                        # 현재 Step 이름
    params: dict[str, Any]                # 템플릿 치환 완료된 파라미터 (§11 참조)
    prev_results: dict[str, dict]         # 이전 Step 결과 {step_name: StepResult.data}
    execution_id: str                     # 실행 인스턴스 ID (멱등성 키)
    initiated_by: str = "system"          # 시작 주체
```

### 3.6 ActionHandler 타입 — 표준 시그니처

```python
# Action Primitive 표준 시그니처
#
# 코드 근거:
# - SagaStep.execute(ctx: SagaContext) -> StepResult → saga/step.py
#   → 동일한 Context-in, Result-out 패턴
# - Action.execute_fn: Callable[[], Any] → core/action_executor.py
#   → create_action()에서 클로저로 context를 바인딩하여 호환
# - StepResult(success, data, error, retryable, partial_execution)
#   → saga/models.py — Orchestrator, DLQ 연동에 동일 타입 사용
ActionHandler = Callable[[RunbookStepContext], StepResult]
```

## 4. RunbookRegistry 클래스

### 모듈-레벨 상수

```python
import re

# Python str.format_map() 변수 감지 — SafeFormatDict 패턴과 대응.
# "{event_source}"는 매칭, '{"key": "value"}'(JSON)는 매칭하지 않는다.
_TEMPLATE_VAR_RE = re.compile(r"\{[a-zA-Z_]\w*\}")
```

### RunbookRegistry

```python
class RunbookRegistry:
    """런북 등록/조회/관리 — 분산 동기화 + 스키마 검증 + 불변성 보장.

    기존 패턴 결합:
    - ProviderRegistry 패턴 (factory.py) — 등록/조회 인터페이스
    - EmergencyMode 이벤트 구독 패턴 (emergency_mode/manager.py) — 분산 캐시 무효화
    - SystemControlManager 영속화 패턴 (system_control.py) — StateBackend 저장/복원
    - SagaOrchestrator 의존성 주입 패턴 (saga/orchestrator.py) — 생성자 None 기본값

    차이점:
    - ProviderRegistry: classmethod 기반 정적 레지스트리
    - RunbookRegistry: 인스턴스 기반 — 런타임 등록/비활성화/분산 동기화 지원
    """

    # StateBackend 키 패턴 (기존 시스템 키 패턴 준수)
    _ENABLED_KEY = "selfhealing:runbook:registry:enabled"

    def __init__(
        self,
        event_bus: SelfHealingEventBus | None = None,
        state_backend: StateBackend | None = None,
        primitive_registry: ActionPrimitiveRegistry | None = None,
    ) -> None:
        """의존성 주입.

        SagaOrchestrator.__init__()과 동일한 패턴:
        None이면 모듈-레벨 팩토리 함수로 지연 로드.
        테스트 시 Mock 직접 주입 가능 (Seam).

        코드 근거: services/saga/orchestrator.py
            def __init__(self, lock=None, idempotency=None, dlq=None,
                         event_bus=None, circuit_breaker=None, ...):
                self._lock = lock       # None이면 나중에 지연 로드
                self._event_bus = event_bus
        """
        self._runbooks: dict[str, Runbook] = {}   # id → Runbook
        self._lock: threading.Lock = threading.Lock()
        self._event_bus = event_bus
        self._state_backend = state_backend
        self._primitive_registry = primitive_registry

        # StateBackend의 enabled 맵 캐시 — 부팅 직후 1회 로드하여
        # register() 시점에 개별 런북의 enabled 상태를 복원한다.
        # 매 register()마다 Redis 호출을 피하기 위한 로컬 캐시.
        self._cached_enabled_map: dict[str, bool] = {}

        # 분산 동기화: 다른 노드의 레지스트리 변경 이벤트 구독 (§9 참조)
        self._register_event_handlers()
        # StateBackend에서 enabled 상태 로드 (앱 재시작 시)
        self._load_enabled_cache()

    # =========================================================================
    # 분산 동기화 — EmergencyMode 이벤트 구독 패턴 (§9 참조)
    # =========================================================================

    def _register_event_handlers(self) -> None:
        """EventBus 핸들러 등록 — EmergencyMode 패턴 재사용.

        코드 근거: services/emergency_mode/manager.py
            bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED,
                          self._on_external_level_changed)
        """
        bus = self._get_event_bus()
        if bus:
            try:
                bus.subscribe(
                    EventType.RUNBOOK_REGISTRY_UPDATED,
                    self._on_registry_updated,
                )
            except Exception as exc:
                logger.warning(
                    "runbook_registry.event_subscribe_failed",
                    error=str(exc),
                )

    def _on_registry_updated(self, event: SelfHealingEvent) -> None:
        """다른 노드에서 발생한 레지스트리 변경 이벤트 수신 시 로컬 상태 동기화.

        코드 근거: services/emergency_mode/manager.py
            def _on_external_level_changed(self, event):
                if event.source != "emergency_manager":
                    self._invalidate_cache()
        """
        if event.source == "runbook_registry":
            return  # 자신이 발행한 이벤트 무시

        action = event.data.get("action")
        runbook_id = event.data.get("runbook_id")

        if action == "set_enabled":
            with self._lock:
                rb = self._runbooks.get(runbook_id)
                if rb:
                    rb.enabled = event.data["enabled"]
        elif action == "unregister":
            with self._lock:
                self._runbooks.pop(runbook_id, None)

    def _broadcast_change(self, action: str, runbook_id: str, **extra: Any) -> None:
        """레지스트리 변경을 EventBus로 브로드캐스트.

        RedisEventBus가 활성 상태면 Redis Pub/Sub으로 전 노드에 전파.

        코드 근거: services/event_bus/redis_bus.py
            self._redis_client.publish(channel, json.dumps(event.to_dict()))
        """
        bus = self._get_event_bus()
        if bus:
            try:
                bus.emit(
                    event_type=EventType.RUNBOOK_REGISTRY_UPDATED,
                    data={"action": action, "runbook_id": runbook_id, **extra},
                    source="runbook_registry",
                )
            except Exception as exc:
                logger.warning(
                    "runbook_registry.broadcast_failed",
                    action=action,
                    runbook_id=runbook_id,
                    error=str(exc),
                )

    # =========================================================================
    # StateBackend 영속화 — SystemControlManager 패턴 (§9 참조)
    # =========================================================================

    def _load_enabled_cache(self) -> None:
        """StateBackend에서 enabled 맵을 로드하여 로컬 캐시에 저장.

        부팅 직후 __init__()에서 1회 호출된다.
        이 시점에는 _runbooks가 비어 있으므로 직접 적용하지 않고 캐시만 저장한다.
        이후 register()에서 개별 런북 등록 시 캐시를 참조하여 enabled 상태를 복원한다.

        코드 근거: services/system_control.py
            def _load_state(self):
                data = self._backend.get(STATE_KEY)
                if data:
                    self._cached_state = SystemState.from_dict(data)
        """
        backend = self._get_state_backend()
        if backend:
            try:
                data = backend.get(self._ENABLED_KEY)
                if data and isinstance(data, dict):
                    self._cached_enabled_map = {
                        k: bool(v) for k, v in data.items()
                    }
            except Exception as exc:
                logger.warning(
                    "runbook_registry.load_enabled_cache_failed",
                    error=str(exc),
                )

    def _persist_enabled_state(self) -> None:
        """enabled 상태를 StateBackend에 영속화.

        코드 근거: services/system_control.py
            def _save_state(self):
                self._backend.set(STATE_KEY, self._cached_state.to_dict())
        """
        backend = self._get_state_backend()
        if backend:
            try:
                enabled_map = {
                    rb_id: rb.enabled for rb_id, rb in self._runbooks.items()
                }
                backend.set(self._ENABLED_KEY, enabled_map)
            except Exception as exc:
                # 쓰기 실패는 데이터 유실 위험 — exception 레벨로 기록
                logger.exception(
                    "runbook_registry.persist_state_failed",
                    error=str(exc),
                )

    # =========================================================================
    # Public API
    # =========================================================================

    def register(self, runbook: Runbook) -> None:
        """런북 등록 — 유효성 검증 + 파라미터 스키마 검증 (Fail-fast).

        검증 단계:
        1. Runbook.validate() — 구조적 유효성 (빈 steps, 중복 name 등)
        2. _validate_all_step_params() — 각 Step의 params를 Pydantic 스키마로 검증

        Fail-fast 원칙: 등록 시점에 잘못된 런북을 차단.
        장애 복구 중 KeyError/TypeError가 발생하면 피해가 2배이므로 원천 방지.

        코드 근거: services/saga/registry.py
            def register_saga(definition):
                valid, error = definition.validate()
                if not valid:
                    raise ValueError(...)
                _saga_definitions[definition.name] = definition
        """
        # 1. 구조적 유효성 검증
        valid, error = runbook.validate()
        if not valid:
            raise ValueError(f"Invalid runbook '{runbook.id}': {error}")

        # 2. 파라미터 스키마 검증 (§8 참조)
        self._validate_all_step_params(runbook)

        with self._lock:
            self._runbooks[runbook.id] = runbook

            # StateBackend 캐시에 이전 enabled 상태가 있으면 복원한다.
            # Kill Switch(set_enabled=False)가 앱 재시작 후에도 유지되도록 보장.
            if runbook.id in self._cached_enabled_map:
                runbook.enabled = self._cached_enabled_map[runbook.id]

    def _validate_all_step_params(self, runbook: Runbook) -> None:
        """모든 Step의 params를 Action Primitive의 Pydantic 스키마로 사전 검증.

        §8 파라미터 스키마 검증 참조.

        코드 근거: settings/api_rate_limit.py
            @field_validator("emergency_limit")
            def validate_emergency_limit(cls, v, info) -> int: ...

        Raises:
            ValueError: 스키마 검증 실패 시 (어떤 Step의 어떤 파라미터가 문제인지 명시)
        """
        primitives = self._get_primitive_registry()
        if not primitives:
            return

        for step in runbook.steps:
            schema = primitives.get_schema(step.action)
            if schema is None:
                continue

            # 템플릿 변수({var_name}) 포함 값은 정적 검증에서 제외.
            # _TEMPLATE_VAR_RE 정규식으로 Python format 변수만 감지 — JSON 문자열 오탐 방지.
            template_keys = {
                k for k, v in step.params.items()
                if isinstance(v, str) and _TEMPLATE_VAR_RE.search(v)
            }
            static_params = {
                k: v for k, v in step.params.items()
                if k not in template_keys
            }

            # 모든 파라미터가 템플릿 변수이면 정적 검증 불가 — 실행 시점에 재검증
            if not static_params:
                continue

            try:
                schema(**static_params)
            except ValidationError as e:
                if template_keys:
                    # 템플릿 변수로 필터된 필수 필드의 missing 에러는 무시.
                    # 해당 필드는 실행 시점에 치환 후 재검증된다.
                    non_template_errors = [
                        err for err in e.errors()
                        if not (
                            err["type"] == "missing"
                            and len(err["loc"]) == 1
                            and err["loc"][0] in template_keys
                        )
                    ]
                    if not non_template_errors:
                        continue
                raise ValueError(
                    f"Runbook '{runbook.id}' step '{step.name}' "
                    f"action '{step.action}' params 검증 실패: {e}"
                ) from e

    def unregister(self, runbook_id: str) -> bool:
        """런북 등록 해제 + 전 노드 브로드캐스트."""
        with self._lock:
            removed = self._runbooks.pop(runbook_id, None) is not None
        if removed:
            self._persist_enabled_state()
            self._broadcast_change("unregister", runbook_id)
        return removed

    def get(self, runbook_id: str) -> Runbook | None:
        """런북 조회 — deepcopy 반환으로 불변성 보장 (§10 참조).

        반환된 Runbook 객체를 외부에서 수정해도 레지스트리 내부 상태에 영향 없음.
        275번 Executor는 이 메서드로 받은 스냅샷을 실행 끝까지 사용.

        코드 근거: adapters/traffic_routing/k8s_ingress_adapter.py
            rollback_info = {"previous_rules": copy.deepcopy(current_ingress.spec.rules)}
        """
        with self._lock:
            rb = self._runbooks.get(runbook_id)
            return copy.deepcopy(rb) if rb else None

    def get_enabled(self) -> list[Runbook]:
        """활성화된 런북 목록 — deepcopy 반환.

        PatternMatcher.evaluate_all()에서 호출.
        """
        with self._lock:
            return [
                copy.deepcopy(rb)
                for rb in self._runbooks.values()
                if rb.enabled
            ]

    def set_enabled(self, runbook_id: str, enabled: bool) -> bool:
        """런북 활성화/비활성화 — Redis 영속화 + 전 노드 브로드캐스트 (§9 참조).

        Kill Switch 역할: 장애 확산 방지의 생명줄.

        동기화 체인:
        1. 로컬 메모리 업데이트
        2. StateBackend(Redis)에 영속화
        3. EventBus로 전 노드 브로드캐스트

        코드 근거: services/system_control.py
            def disable(self, actor, reason):
                self._refresh_state()                 # 최신 상태 확인
                self._cached_state.enabled = False
                self._save_state()                    # StateBackend 영속화
        """
        with self._lock:
            rb = self._runbooks.get(runbook_id)
            if not rb:
                return False
            rb.enabled = enabled

        self._persist_enabled_state()
        self._broadcast_change("set_enabled", runbook_id, enabled=enabled)
        return True

    def get_all(self) -> list[Runbook]:
        """전체 런북 목록 (비활성화 포함) — deepcopy 반환."""
        with self._lock:
            return [copy.deepcopy(rb) for rb in self._runbooks.values()]

    def get_by_tag(self, tag: str) -> list[Runbook]:
        """태그로 런북 필터 — deepcopy 반환."""
        with self._lock:
            return [
                copy.deepcopy(rb)
                for rb in self._runbooks.values()
                if tag in rb.tags
            ]

    # =========================================================================
    # 의존성 지연 로드 — SagaOrchestrator 패턴
    # =========================================================================

    def _get_event_bus(self) -> SelfHealingEventBus | None:
        """SagaOrchestrator._get_backend() 패턴 재사용."""
        if self._event_bus is None:
            try:
                from selfhealing.services.event_bus import get_event_bus
                self._event_bus = get_event_bus()
            except Exception:
                pass
        return self._event_bus

    def _get_state_backend(self) -> StateBackend | None:
        if self._state_backend is None:
            try:
                from selfhealing.core.state_backend import get_state_backend
                self._state_backend = get_state_backend()
            except Exception:
                pass
        return self._state_backend

    def _get_primitive_registry(self) -> ActionPrimitiveRegistry | None:
        return self._primitive_registry
```

## 5. Action Primitive 레지스트리

런북의 `RunbookStep.action` 필드가 참조하는 **내장 실행 단위**. 각 primitive는 `ActionExecutor`를 통해 실행된다.

**변경점 (리뷰 반영):**
1. `register()`에 `params_schema` 추가 — Pydantic 모델로 Fail-fast 검증 (§8 참조)
2. `ActionHandler` 표준 시그니처 적용 (§3.6 참조)
3. 생성자 주입으로 테스트 Seam 확보

```python
# 내장 primitive 카테고리 — 운영자 참고용 (모듈 레벨 정의로 직접 import 가능)
BUILTIN_CATEGORIES: dict[str, str] = {
        "config":    "설정 변경 (RuntimeConfigManager 연동)",
        "assert":    "검증 게이트 (메트릭 조건 확인)",
        "notify":    "알림 발송 (UnifiedNotificationManager 연동)",
        "recovery":  "기존 복구 컴포넌트 호출",
    "emergency": "Emergency Mode 제어",
    "wait":      "대기 (안정화 확인)",
}


class ActionPrimitiveRegistry:
    """action 문자열 → 실행 함수 + 파라미터 스키마 매핑.

    ActionExecutor의 Action 객체를 생성하는 팩토리.

    코드 근거:
    - Action(name, target, execute_fn, params, validate_fn)
      → core/action_executor.py
    - RecoveryCoordinator._step_handlers dict
      → coordination/recovery_coordinator/__init__.py
    - Pydantic @field_validator
      → settings/api_rate_limit.py
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}    # action → handler
        self._schemas: dict[str, type[BaseModel]] = {}    # action → Pydantic schema
        self._register_builtins()

    def _register_builtins(self) -> None:
        """내장 primitive + Pydantic 스키마 동시 등록.

        각 primitive는 state-based로 설계 (멱등성 보장):
        - config.set(key, value)    — 설정을 value로 설정 (=상태 기반)
        - assert.metric(...)        — 현재 값 검증
        - notify.send(...)          — 알림 발송
        - recovery.start(...)       — RecoveryCoordinator.start_recovery() 호출
        - emergency.activate(...)   — GracefulDegradationManager.activate_auto() 호출
        - emergency.deactivate(...) — GracefulDegradationManager.deactivate() 호출
        - wait.stabilize(...)       — 대기 + 검증

        스키마 등록 예시:
            self.register("config.set", _handle_config_set, ConfigSetParams)
            self.register("assert.metric", _handle_assert_metric, AssertMetricParams)
        """
        ...

    def register(
        self,
        action_name: str,
        handler: ActionHandler,
        params_schema: type[BaseModel] | None = None,
    ) -> None:
        """커스텀 primitive 등록 — Pydantic 스키마 동시 등록.

        Args:
            action_name: action 식별자 (예: "db.kill_idle")
            handler: ActionHandler 시그니처(§3.6)를 따르는 실행 함수
            params_schema: 파라미터 검증용 Pydantic BaseModel 클래스 (선택)

        호스트 앱의 AppConfig.ready()에서 등록하는 패턴:
            primitive_registry.register(
                "db.kill_idle",
                kill_idle_handler,
                params_schema=KillIdleParams,
            )

        의존성 주입 (Seam):
            handler 내부에서 외부 의존성(DB Connection Pool 등)에 접근할 때,
            클로저로 바인딩하거나 모듈-레벨 팩토리 함수를 사용한다.
            기존 Action.execute_fn = lambda: repository.atomic_force_open(...)
            패턴과 동일 (core/action_executor.py).

        코드 근거: settings/api_rate_limit.py
            @field_validator("emergency_limit")
            def validate_emergency_limit(cls, v, info) -> int: ...
        """
        self._handlers[action_name] = handler
        if params_schema is not None:
            self._schemas[action_name] = params_schema

    def get(self, action_name: str) -> ActionHandler | None:
        """action 이름으로 실행 함수 조회."""
        return self._handlers.get(action_name)

    def get_schema(self, action_name: str) -> type[BaseModel] | None:
        """action 이름으로 파라미터 스키마 조회.

        RunbookRegistry.register() 시점에서 호출하여 Fail-fast 검증에 사용 (§8 참조).
        """
        return self._schemas.get(action_name)

    def create_action(self, action_name: str, context: RunbookStepContext) -> Action:
        """action 이름 + context로 ActionExecutor용 Action 객체 생성.

        클로저 바인딩으로 기존 Action.execute_fn: Callable[[], Any] 호환.

        코드 근거: core/action_executor.py
            Action(
                name="force_open_circuit",
                target="external_api",
                execute_fn=lambda: repository.atomic_force_open(...),
            )
        """
        handler = self._handlers[action_name]
        return Action(
            name=action_name,
            target=f"runbook:{context.runbook_id}",
            execute_fn=lambda: handler(context),  # 클로저 바인딩
            params=context.params,
        )
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
            condition=StepCondition(type="prev_failed"),  # 검증 실패 시만 실행
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

## 8. 파라미터 스키마 검증 (Fail-fast)

런북은 **장애 복구 중 실행되는 코드**이므로, 런북 자체에서 `KeyError`/`TypeError`가 발생하면 피해가 2배가 된다. `ActionPrimitiveRegistry`에 Pydantic 스키마를 함께 등록하여 **`register()` 시점에 잘못된 런북을 차단**한다.

### 8.1 코드 근거

프로젝트의 50+ Settings 클래스가 이미 Pydantic `@field_validator` / `@model_validator`로 인스턴스 생성 시점 검증을 수행한다:

```python
# settings/api_rate_limit.py (기존 코드)
@field_validator("emergency_limit")
@classmethod
def validate_emergency_limit(cls, v: int, info) -> int:
    if v > 50:
        logger.warning("api_rate_limit.high_consider_using_safety", v=v)
    return v
```

```python
# settings/domain_sensitivity.py (기존 코드)
@model_validator(mode="after")
def _validate_domain_weights(self) -> DomainSensitivitySettings:
    """모든 도메인 가중치 범위 검증."""
    ...
```

### 8.2 내장 Primitive 스키마 정의

```python
# 각 Builtin Action Primitive에 대응하는 Pydantic 스키마

class ConfigSetParams(BaseModel):
    """config.set action 파라미터."""
    key: str
    value: Any | None = None
    value_delta: int | float | None = None

    @model_validator(mode="after")
    def _validate_value_or_delta(self) -> ConfigSetParams:
        if self.value is None and self.value_delta is None:
            raise ValueError("value 또는 value_delta 중 하나는 필수")
        return self

class AssertMetricParams(BaseModel):
    """assert.metric action 파라미터."""
    metric_name: str
    operator: Literal["gt", "lt", "eq", "gte", "lte"]
    threshold: float

class NotifySendParams(BaseModel):
    """notify.send action 파라미터."""
    title: str
    message: str = ""
    priority: Literal["low", "medium", "high", "critical"] = "medium"

class RecoveryStartParams(BaseModel):
    """recovery.start action 파라미터."""
    namespace: str = "global"
    trigger_level: str

class EmergencyActivateParams(BaseModel):
    """emergency.activate action 파라미터."""
    level: int = Field(ge=1, le=5)
    reason: str

class WaitStabilizeParams(BaseModel):
    """wait.stabilize action 파라미터."""
    seconds: int = Field(ge=1, le=3600)
    assert_metric: str | None = None
    threshold: float | None = None
```

### 8.3 2단계 검증 흐름

```
RunbookRegistry.register(runbook)
  ├─ 1. runbook.validate()           → 구조적 유효성 (빈 steps, 중복 name)
  └─ 2. _validate_all_step_params()  → 각 Step의 params를 Pydantic 스키마로 검증
       └─ for step in runbook.steps:
            schema = primitive_registry.get_schema(step.action)
            if schema:
                template_keys = {k: 템플릿 변수 포함 키}
                static_params = {k: v for non-template keys}
                if static_params:
                    schema(**static_params)
                    └─ ValidationError 중 template_keys의 missing 에러 → 무시
                    └─ 나머지 에러 → register 실패
```

| 검증 시점 | 대상 | 동작 |
|---|---|---|
| **등록 시점** (`register()`) | 정적 params만 (템플릿 변수 `{event_source}` 제외). 필수 필드가 템플릿이면 missing 에러 무시 | Pydantic `ValidationError` → `ValueError` 전파 (비-템플릿 에러만) |
| **실행 시점** (275번 Executor) | 템플릿 치환 후 최종 params 전체 | Pydantic 재검증 → 실패 시 `StepResult.failed()` |

## 9. 분산 상태 동기화

다중 Pod/Worker 환경에서 한 노드의 레지스트리 변경이 다른 노드에 **즉시 전파**되어야 한다. 특히 `set_enabled(runbook_id, False)` Kill Switch는 장애 확산을 막는 생명줄이므로 지연 불가.

### 9.1 코드 근거

기존 시스템의 3가지 분산 동기화 패턴을 결합한다:

**① EmergencyMode — EventBus 구독 → 캐시 무효화:**

```python
# services/emergency_mode/manager.py (기존 코드)
bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, self._on_external_level_changed)

def _on_external_level_changed(self, event):
    if event.source != "emergency_manager":
        self._invalidate_cache()
```

**② SystemControlManager — StateBackend 영속화:**

```python
# services/system_control.py (기존 코드)
def _save_state(self):
    self._backend.set(STATE_KEY, self._cached_state.to_dict())

def _refresh_state(self):
    data = self._backend.get(STATE_KEY)
    if data:
        self._cached_state = SystemState.from_dict(data)
```

**③ RedisEventBus — 3중 전파 안전망:**

```python
# services/event_bus/redis_bus.py (기존 코드)
# 폴백 체인: Redis Pub/Sub → Kafka 폴백 → WAL 기록
self._redis_client.publish(channel, json.dumps(event.to_dict(), default=str))
```

### 9.2 동기화 체인

```
set_enabled(runbook_id, False)
  ├─ 1. 로컬 메모리: self._runbooks[runbook_id].enabled = False
  ├─ 2. StateBackend(Redis): backend.set(ENABLED_KEY, enabled_map)
  └─ 3. EventBus.emit(RUNBOOK_REGISTRY_UPDATED, {action, runbook_id, enabled})
         └─ RedisEventBus: Redis PUBLISH → 전 노드 수신
              └─ 각 노드 _on_registry_updated():
                   self._runbooks[runbook_id].enabled = False
```

### 9.3 이벤트 타입

기존 `EventType` enum에 이미 10개의 `RUNBOOK_*` 이벤트가 정의되어 있다:

```python
# services/event_bus/bus/__init__.py (기존 코드)
RUNBOOK_TRIGGERED = "runbook_triggered"
RUNBOOK_STEP_COMPLETED = "runbook_step_completed"
RUNBOOK_STEP_FAILED = "runbook_step_failed"
RUNBOOK_COMPLETED = "runbook_completed"
RUNBOOK_FAILED = "runbook_failed"
RUNBOOK_SKIPPED_COOLDOWN = "runbook_skipped_cooldown"
# ... (10개)
```

**신규 추가:**

```python
RUNBOOK_REGISTRY_UPDATED = "runbook_registry_updated"
"""런북 레지스트리 변경 (등록/해제/활성화 토글). 분산 동기화용."""
```

### 9.4 Push + Pull 하이브리드

| 경로 | 메커니즘 | 지연 |
|---|---|---|
| **Push (즉시)** | EventBus → Redis Pub/Sub → 각 노드 핸들러 | ~수 ms |
| **Pull (안전망)** | `_load_enabled_cache()` — 앱 시작 시 StateBackend에서 캐시 로드 → register() 시 복원 | 부팅 시 1회 |

Push가 실패해도 Pull이 앱 재시작 시 상태를 복구하므로, 최종 일관성(Eventual Consistency)을 보장한다.

## 10. 런북 불변성 및 스냅샷 보호

런북이 `wait_after_seconds: 300` 같은 장기 대기를 포함하는 도중, 다른 스레드/노드에서 런북 정의를 변경하면 다음 Step이 꼬일 수 있다.

### 10.1 코드 근거

프로젝트에 확립된 불변성 패턴 3가지를 결합한다:

**① `@dataclass(frozen=True)` — Copy-on-Write:**

```python
# services/system_metrics_cache.py (기존 코드)
@dataclass(frozen=True)
class CachedMetrics:
    """frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지.
    백그라운드 스레드는 새 인스턴스를 생성하여 참조를 교체한다 (Copy-on-Write).
    Python GIL 하에서 참조 교체는 atomic이므로 Lock 불필요.
    """
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
```

**② `copy.deepcopy` — 스냅샷 보존:**

```python
# adapters/traffic_routing/k8s_ingress_adapter.py (기존 코드)
rollback_info = {
    "previous_rules": copy.deepcopy(current_ingress.spec.rules),
}
patched_rules = copy.deepcopy(current_ingress.spec.rules)
```

**③ Saga 버전 호환성 검증:**

```python
# services/saga/orchestrator.py (기존 코드)
if instance.definition_version != definition.version:
    instance.status = SagaStatus.SUSPENDED
    instance.error_message = f"Definition version mismatch: ..."
    return False
```

### 10.2 적용 설계

**선택: `deepcopy` 반환 전략** — `k8s_ingress_adapter.py` 패턴.

선택 근거:
- `frozen=True`를 쓰면 `enabled` 토글이 불가능 (`FrozenInstanceError` 발생)
- `enabled`를 외부로 분리하면 데이터 모델이 분산되어 복잡도 증가
- `deepcopy`는 기존 k8s 어댑터 패턴과 일치하며, 런북 수가 적으므로(수십 개 이하) 성능 부담 없음

| 메서드 | 반환 | 불변성 |
|---|---|---|
| `get(runbook_id)` | `copy.deepcopy(runbook)` | ✅ 외부 수정 차단 |
| `get_enabled()` | `[copy.deepcopy(rb) for ...]` | ✅ 외부 수정 차단 |
| `get_all()` | `[copy.deepcopy(rb) for ...]` | ✅ 외부 수정 차단 |

### 10.3 275번 Executor 실행 보호

```
RunbookExecutor.execute(runbook_id)
  ├─ runbook = registry.get(runbook_id)   ← deepcopy 스냅샷 획득
  ├─ saga_def = _to_saga_definition(runbook)
  └─ orchestrator.execute_saga(saga_def)  ← 스냅샷 기반 실행
       └─ 실행 도중 레지스트리 변경되어도 영향 없음
```

## 11. 컨텍스트 변수 보간

런북은 선언적이므로 파라미터에 하드코딩된 값뿐만 아니라 **동적인 이벤트 컨텍스트를 주입**할 수 있어야 한다.

### 11.1 코드 근거

기존 `SafeFormatDict` + `format_map()` 패턴을 재사용한다:

```python
# utils/template.py (기존 코드)
class SafeFormatDict(dict):
    """format_map()에서 누락 키를 빈 문자열로 대체하는 dict."""
    def __missing__(self, key: str) -> str:
        logger.warning("template.missing_variable", key=key)
        return ""
```

```python
# 273번 문서 §8에서 이미 채택한 패턴
# services/correlation_engine/incident_timeline.py (기존 코드)
desc = template.format_map(_SafeFormatDict(format_vars))
```

**Jinja2 스타일 `{{ }}` 구문을 사용하지 않는 이유:**

- 프로젝트 전체가 Python `str.format_map()` (`{key}` 구문)을 사용
- `{{ metric.current_value * 1.5 }}` 같은 표현식 평가는 `eval()` 필요 → **코드 인젝션 위험**
- 273번 문서 §8 + `incident_timeline.py`가 동일 구문을 이미 채택

### 11.2 치환 문법

```python
# RunbookStep.params에서 템플릿 변수 사용
RunbookStep(
    name="notify_service_owner",
    action="notify.send",
    params={
        "title": "서비스 {event_source} 장애 감지",
        "message": "{event_type} 발생 — 에러율 {error_rate}",
        "priority": "high",
    },
)
```

### 11.3 치환 시점 및 주체

275번 Executor가 실행 시점에 치환한다 (레지스트리는 정의만 보관):

```python
# 275번 Executor의 치환 로직 (개념 코드)
from selfhealing.utils.template import SafeFormatDict

def _resolve_params(
    self,
    step: RunbookStep,
    event_context: dict[str, Any],
) -> dict[str, Any]:
    """템플릿 변수를 실제 값으로 치환.

    str 타입 값만 치환하고, int/float/dict 등은 그대로 전달.

    코드 근거: services/correlation_engine/incident_timeline.py
        desc = template.format_map(_SafeFormatDict(format_vars))
    """
    safe_ctx = SafeFormatDict(event_context)
    return {
        k: v.format_map(safe_ctx) if isinstance(v, str) else v
        for k, v in step.params.items()
    }
```

### 11.4 검증과의 관계

§8 파라미터 스키마 검증에서 템플릿 변수 포함 값은 정적 검증에서 제외한다.
필수 필드가 템플릿 변수로 제외된 경우, 해당 missing 에러는 무시하고 실행 시점에 재검증한다:

```python
# 등록 시점: 템플릿 변수 제외하여 검증 (정규식으로 Python format 변수만 감지)
template_keys = {k for k, v in params.items()
                 if isinstance(v, str) and _TEMPLATE_VAR_RE.search(v)}
static_params = {k: v for k, v in params.items() if k not in template_keys}
if static_params:
    try:
        schema(**static_params)
    except ValidationError:
        # template_keys의 missing 에러만이면 무시 → 실행 시점에 재검증

# 실행 시점: 치환 후 재검증
resolved_params = _resolve_params(step, event_context)
schema(**resolved_params)  # 치환된 최종값으로 Pydantic 재검증
```

## 12. SagaDefinition 변환 — 결정론성과 감사

274번 `Runbook` 객체를 275번 Executor가 사용하는 `SagaDefinition` 모델로 변환하는 책임은 **275번 Executor가 소유**한다. 레지스트리는 정의만 보관한다.

### 12.1 코드 근거

**변환 주체 = Executor:** 기존 패턴에서 "정의 → 실행" 변환은 항상 실행 측에서 수행한다:

```python
# services/saga/orchestrator.py (기존 코드)
def execute_saga(self, saga_name, initial_data, ...):
    definition = get_saga_definition(saga_name)     # ← 레지스트리에서 조회만
    instance = self._create_instance(definition, initial_data, ...)  # ← Executor가 변환
```

### 12.2 변환 팩토리 (개념 코드)

```python
class RunbookExecutor:
    def _to_saga_definition(self, runbook: Runbook) -> SagaDefinition:
        """Runbook → SagaDefinition 결정론적 변환.

        RunbookStep → SagaStep 매핑:
        - RunbookStep.action     → ActionPrimitiveRegistry로 SagaStep.execute() 바인딩
        - RunbookStep.on_failure → SagaStep.compensate() 바인딩
        - RunbookStep.condition  → SagaStep.can_execute() 구현
        - RunbookStep.timeout    → SagaStep.timeout_seconds

        결정론성 보장:
        동일한 Runbook + 동일한 컨텍스트 → 항상 동일한 SagaDefinition.
        함수 내부에서 uuid4(), datetime.now() 등 비결정론적 요소 호출 금지.
        """
        saga_steps = [self._wrap_as_saga_step(step) for step in runbook.steps]
        return SagaDefinition(
            name=f"runbook:{runbook.id}",
            version=runbook.version,
            steps=saga_steps,
            timeout_seconds=sum(s.timeout_seconds for s in runbook.steps),
        )
```

### 12.3 감사 로그

변환 직후 SagaDefinition 스냅샷을 structlog로 기록한다. Playback Recorder나 Audit Log를 통해 복구 실패 원인을 분석할 때, "당시 어떤 파라미터 조합으로 Saga가 구성되었는지" 정확히 재현할 수 있다.

```python
# 275번 Executor (개념 코드)
logger.info(
    "runbook_executor.saga_definition_compiled",
    runbook_id=runbook.id,
    runbook_version=runbook.version,
    saga_name=definition.name,
    step_count=len(definition.steps),
    step_names=[s.name for s in definition.steps],
    timeout_total=definition.timeout_seconds,
)
```

### 12.4 책임 분리

| 계층 | 책임 |
|---|---|
| **RunbookRegistry (274)** | Runbook 등록/조회/활성화 관리. 변환 로직 없음 |
| **RunbookExecutor (275)** | Runbook → SagaDefinition 변환(Compile) 소유 |
| **SagaOrchestrator** | SagaDefinition → SagaInstance 실행 |

## 13. 참조

- `ProviderRegistry`: `factory.py` — 등록/조회 패턴
- `RecoveryCoordinator.register_step_handler()`: `coordination/recovery_coordinator/__init__.py`
- `RecoveryCoordinator.DEFAULT_RECOVERY_STEPS`: 동일 파일
- `SagaStep` ABC: `services/saga/step.py`
- `SagaDefinition.validate()`: `services/saga/models.py`
- `SagaContext.get_from_step()`: `services/saga/models.py` — StepCondition 평가 기반
- `StepResult`: `services/saga/models.py` — ActionHandler 반환 타입
- `Action` dataclass: `core/action_executor.py`
- `ActionExecutor.execute()`: 동일 파일
- `PatternCondition`: 273번 문서
- `SafeFormatDict`: `utils/template.py` — 컨텍스트 변수 보간 유틸리티
- `EmergencyMode._on_external_level_changed()`: `services/emergency_mode/manager.py` — 분산 캐시 무효화 패턴
- `SystemControlManager._save_state()`: `services/system_control.py` — StateBackend 영속화 패턴
- `RedisEventBus.publish()`: `services/event_bus/redis_bus.py` — 3중 전파 안전망
- `SagaOrchestrator._validate_version_compatibility()`: `services/saga/orchestrator.py` — 버전 보호 패턴
- `k8s_ingress_adapter.py`: `adapters/traffic_routing/` — `copy.deepcopy` 스냅샷 패턴
- `@field_validator`: `settings/api_rate_limit.py` — Pydantic 검증 패턴
- `EventType.RUNBOOK_*`: `services/event_bus/bus/__init__.py` — 이벤트 타입 슬롯
