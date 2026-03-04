"""
Runbook Registry — 선언적 런북 등록/조회/활성화 관리.

"패턴 X가 매칭되면 절차 Y를 실행하라"는 매핑을 한 곳에서 관리한다.

Reference:
    docs/self_healing/middleware_system/274_RUNBOOK_REGISTRY.md
"""

from __future__ import annotations

import copy
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from selfhealing.core.action_executor import Action
    from selfhealing.core.state_backend import StateBackend
    from selfhealing.services.event_bus.bus import SelfHealingEvent, SelfHealingEventBus
    from selfhealing.services.runbook.models import PatternCondition
    from selfhealing.services.saga.models import StepResult

logger = structlog.get_logger()

# Python str.format_map() 변수 감지 — SafeFormatDict 패턴과 대응.
# "{event_source}"는 매칭, '{"key": "value"}'(JSON)는 매칭하지 않는다.
_TEMPLATE_VAR_RE = re.compile(r"\{[a-zA-Z_]\w*\}")


# =============================================================================
# 위험도 분류
# =============================================================================

_RISK_PRIORITY: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class RiskLevel(str, Enum):
    """런북 위험도.

    276번 문서(ApprovalGate)에서 이 값에 따라 승인 방식이 결정된다.
    PatternMatcher의 Tie-breaker(select_runbook)에서 숫자 비교가 필요하므로
    __lt__ 등 비교 연산자를 정수 우선순위 기반으로 구현한다.
    """

    LOW = "low"
    """즉시 자동 실행 (우선순위 0)."""

    MEDIUM = "medium"
    """알림 후 타이머 대기 (우선순위 1)."""

    HIGH = "high"
    """수동 승인 필수 (우선순위 2)."""

    CRITICAL = "critical"
    """자동 실행 차단 — 강제 실행(force_execute)만 가능 (우선순위 3)."""

    @property
    def order(self) -> int:
        """위험도 정수 우선순위 — 낮을수록 먼저 선택된다."""
        return _RISK_PRIORITY[self.value]

    def __lt__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self.order < other.order
        if isinstance(other, int):
            return self.order < other
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self.order <= other.order
        if isinstance(other, int):
            return self.order <= other
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self.order > other.order
        if isinstance(other, int):
            return self.order > other
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self.order >= other.order
        if isinstance(other, int):
            return self.order >= other
        return NotImplemented

    def __int__(self) -> int:
        return self.order


# =============================================================================
# Step 실행 조건 — 선언적 분기
# =============================================================================


@dataclass
class StepCondition:
    """Step 실행 조건 — 선언적 분기.

    275번 Executor가 SagaStep.can_execute() 구현으로 변환한다.
    평가 엔진은 SagaContext.step_results 내 스냅샷 데이터만 읽어 판단한다.
    외부 I/O(메트릭 조회, DB 쿼리)는 금지된다 (순수 함수 원칙).

    type 종류:
        "always"       — 항상 실행
        "prev_failed"  — 이전 Step 실패 시만 실행
        "prev_succeeded" — 이전 Step 성공 시만 실행
        "data_match"   — source_step의 결과 데이터 기반 분기
    """

    type: str
    """조건 유형 ("always", "prev_failed", "prev_succeeded", "data_match")."""

    # data_match 전용 필드
    source_step: str | None = None
    """참조할 이전 Step 이름 (data_match 전용)."""

    field: str | None = None
    """Step 결과의 필드명 (data_match 전용)."""

    operator: str | None = None
    """비교 연산자 ("eq", "gt", "lt", "gte", "lte", "contains", "not_none") (data_match 전용)."""

    value: Any = None
    """비교 대상값 (data_match 전용)."""


# =============================================================================
# 런북 단일 실행 단계
# =============================================================================


@dataclass
class RunbookStep:
    """런북의 단일 실행 단계.

    SagaStep ABC와 대응:
    - action     → SagaStep.name (실행 unit 식별자)
    - params     → SagaContext.initial_data
    - on_failure → SagaStep.compensate()
    - condition  → SagaStep.can_execute()
    - timeout_seconds → SagaStep.timeout_seconds

    params 값에 "{event_source}" 형식 템플릿 변수를 사용하면
    275번 Executor 실행 시점에 SafeFormatDict(context)으로 치환된다.
    """

    name: str
    """Step 식별자 (Runbook 내 유일, 예: "kill_idle_connections")."""

    action: str
    """실행할 Action Primitive 이름 (예: "db.kill_idle")."""

    order: int = 0
    """실행 순서 인덱스. RunbookExecutor가 오름차순 정렬 후 실행한다.
    보상(compensation) 시 역순 정렬의 기준이 된다."""

    params: dict[str, Any] = field(default_factory=dict)
    """Action에 전달할 파라미터. 템플릿 변수({...}) 지원."""

    # 보상 (SagaStep.compensate 대응)
    on_failure_action: str | None = None
    """Step 실패 시 실행할 보상 Action 이름."""

    on_failure_params: dict[str, Any] = field(default_factory=dict)
    """보상 Action 파라미터."""

    # 완료 후 검증
    validation_action: str | None = None
    """Step 완료 후 실행할 검증 Action 이름."""

    validation_params: dict[str, Any] = field(default_factory=dict)
    """검증 Action 파라미터."""

    # 조건부 실행 (SagaStep.can_execute 대응)
    condition: StepCondition | None = None
    """None이면 항상 실행."""

    # 실행 제어
    timeout_seconds: int = 120
    """Step 단위 타임아웃 (초)."""

    wait_after_seconds: int = 0
    """Step 완료 후 안정화 대기 시간 (초)."""

    # 멱등성 힌트
    idempotent: bool = True
    """상태 기반 Action이면 True (IdempotentStepHandler 연동)."""


# =============================================================================
# Action Primitive 실행 컨텍스트
# =============================================================================


@dataclass
class RunbookStepContext:
    """Action Primitive에 전달되는 실행 컨텍스트.

    SagaContext의 경량 버전 — Step 간 데이터 전달 + 실행 메타데이터.

    SagaContext와의 차이:
    - SagaContext : Orchestrator 내부용 (전체 Saga 상태 추적, 직렬화)
    - RunbookStepContext : Action Primitive용 (이전 Step 결과 + 실행 메타데이터)
    """

    runbook_id: str
    """소속 런북 ID."""

    step_name: str
    """현재 Step 이름."""

    params: dict[str, Any]
    """템플릿 치환 완료된 파라미터."""

    prev_results: dict[str, dict]
    """이전 Step 결과 {step_name: StepResult.data}."""

    execution_id: str
    """실행 인스턴스 ID (멱등성 키)."""

    initiated_by: str = "system"
    """실행 시작 주체."""


# =============================================================================
# Action Primitive 타입 별칭
# =============================================================================

# Action Primitive 표준 시그니처
# SagaStep.execute(ctx: SagaContext) -> StepResult 패턴과 동일.
ActionHandler = Callable[["RunbookStepContext"], "StepResult"]


# =============================================================================
# 런북 정의
# =============================================================================


@dataclass
class Runbook:
    """런북 정의 — 트리거 조건 + 실행 단계 선언.

    ProviderRegistry 패턴을 따라 id로 등록/조회한다.
    시스템 제어 API에서 enabled 토글 가능.

    RecoveryCoordinator의 DEFAULT_RECOVERY_STEPS와의 차이:
        RecoveryCoordinator: {"LEVEL_3": [...]} — 하드코딩
        Runbook           : {runbook_id: Runbook(...)} — 선언적

    trigger_condition 필드명은 PatternMatcher의 RunbookLike Protocol
    (pattern_matcher.py)과 일치시킨다.
    """

    id: str
    """고유 식별자 (예: "db_pool_recovery")."""

    name: str
    """사람이 읽을 수 있는 이름."""

    description: str
    """런북 설명."""

    trigger_condition: PatternCondition
    """트리거 조건 (273번 문서의 PatternCondition)."""

    steps: list[RunbookStep]
    """실행 단계 목록."""

    risk_level: RiskLevel = RiskLevel.LOW
    """위험도 (276번 문서의 ApprovalGate에서 사용).
    PatternMatcher의 Tie-breaker select_runbook()에서 비교 키로도 사용된다."""

    enabled: bool = True
    """활성화 상태 — RunbookRegistry.set_enabled()로 토글."""

    priority: int = 100
    """우선순위 — 낮을수록 높은 우선순위 (같은 조건에 여러 런북 매칭 시)."""

    cooldown_seconds: int = 300
    """쿨다운 — 동일 런북 재트리거 방지 (기본 5분)."""

    version: int = 1
    """런북 버전."""

    created_by: str = "system"
    """생성 주체."""

    tags: list[str] = field(default_factory=list)
    """분류 태그 목록."""

    global_timeout_seconds: int | None = None
    """전체 실행 타임아웃 (초). None이면 설정값(SELFHEALING_RUNBOOK_GLOBAL_TIMEOUT_SECONDS) 사용."""

    def validate(self) -> tuple[bool, str]:
        """런북 정의 유효성 검증.

        SagaDefinition.validate()와 동일한 패턴:
        1. steps가 비어있지 않은지
        2. 각 step의 name/action이 비어있지 않은지
        3. step name이 중복되지 않는지

        Returns:
            (True, "") — 유효
            (False, "에러 메시지") — 무효
        """
        if not self.steps:
            return False, "steps가 비어있다. 최소 1개 이상의 step이 필요하다."

        seen_names: set[str] = set()
        for step in self.steps:
            if not step.name:
                return False, f"step에 name이 없다: {step}"
            if not step.action:
                return False, f"step '{step.name}'에 action이 없다."
            if step.name in seen_names:
                return False, f"step name 중복: '{step.name}'"
            seen_names.add(step.name)

        return True, ""


# =============================================================================
# Action Primitive 레지스트리
# =============================================================================


class ActionPrimitiveRegistry:
    """action 문자열 → 실행 함수 + 파라미터 스키마 매핑.

    ActionExecutor의 Action 객체를 생성하는 팩토리.

    내장 primitive 카테고리:
        config.*    — 설정 변경 (RuntimeConfigManager 연동)
        assert.*    — 검증 게이트 (메트릭 조건 확인)
        notify.*    — 알림 발송 (UnifiedNotificationManager 연동)
        recovery.*  — 기존 복구 컴포넌트 호출
        emergency.* — Emergency Mode 제어
        wait.*      — 대기 (안정화 확인)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}
        self._schemas: dict[str, type[BaseModel]] = {}
        self._compensate_handlers: dict[str, Callable[..., Any]] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        """내장 primitive + Pydantic 스키마 동시 등록."""
        try:
            from selfhealing.services.runbook.primitives import (
                register_builtin_primitives,
            )

            register_builtin_primitives(self)
        except Exception:
            logger.debug(
                "action_primitive_registry.builtins_placeholder",
                categories=list(BUILTIN_CATEGORIES.keys()),
            )

    def register(
        self,
        action_name: str,
        handler: ActionHandler,
        params_schema: type[BaseModel] | None = None,
    ) -> None:
        """커스텀 primitive 등록 — Pydantic 스키마 동시 등록.

        Args:
            action_name   : action 식별자 (예: "db.kill_idle")
            handler       : ActionHandler 시그니처(§3.6)를 따르는 실행 함수
            params_schema : 파라미터 검증용 Pydantic BaseModel 클래스 (선택)

        호스트 앱의 AppConfig.ready()에서 등록하는 패턴:
            primitive_registry.register(
                "db.kill_idle",
                kill_idle_handler,
                params_schema=KillIdleParams,
            )
        """
        self._handlers[action_name] = handler
        if params_schema is not None:
            self._schemas[action_name] = params_schema

    def get(self, action_name: str) -> ActionHandler | None:
        """action 이름으로 실행 함수 조회."""
        return self._handlers.get(action_name)

    def get_schema(self, action_name: str) -> type[BaseModel] | None:
        """action 이름으로 파라미터 스키마 조회.

        RunbookRegistry.register() 시점에서 Fail-fast 검증에 사용된다.
        """
        return self._schemas.get(action_name)

    def create_action(self, action_name: str, context: RunbookStepContext) -> Action:
        """action 이름 + context로 ActionExecutor용 Action 객체 생성.

        클로저 바인딩으로 기존 Action.execute_fn: Callable[[], Any] 호환.

        Raises:
            KeyError: action_name이 등록되지 않은 경우
        """
        from selfhealing.core.action_executor import Action

        handler = self._handlers[action_name]
        return Action(
            name=action_name,
            target=f"runbook:{context.runbook_id}",
            execute_fn=lambda: handler(context),
            params=context.params,
        )

    # =========================================================================
    # 보상 Primitive 등록/조회 — 275번 Executor 연동
    # =========================================================================

    def register_compensate(
        self,
        action_name: str,
        fn: Callable[..., Any],
        validate: bool = True,
    ) -> None:
        """보상 Primitive 등록 + No-op 안전성 검증.

        RunbookRegistry.register() Fail-fast 패턴과 동일하게
        등록 시점에 CompensationContract.validate_noop_safety()를 호출한다.

        Args:
            action_name: 보상 대상 Action 이름 (예: "enable_circuit_breaker")
            fn: 보상 함수. **kwargs를 포함해야 한다.
            validate: False이면 No-op 안전성 검증을 건너뜀 (테스트 전용)

        Raises:
            ValueError: fn에 **kwargs가 없는 경우
        """
        if validate:
            from selfhealing.services.runbook.contracts import CompensationContract

            CompensationContract.validate_noop_safety(fn, action_name)

        self._compensate_handlers[action_name] = fn
        logger.debug(
            "action_primitive_registry.compensate_registered",
            action_name=action_name,
        )

    def get_compensate(self, action_name: str) -> Callable[..., Any] | None:
        """보상 Primitive 조회.

        등록되지 않은 action_name이면 None을 반환한다.
        Executor는 None이면 보상을 skip하고 로그만 남긴다.
        """
        return self._compensate_handlers.get(action_name)


# 내장 primitive 카테고리 — 운영자 참고용
BUILTIN_CATEGORIES: dict[str, str] = {
    "config": "설정 변경 (RuntimeConfigManager 연동)",
    "assert": "검증 게이트 (메트릭 조건 확인)",
    "notify": "알림 발송 (UnifiedNotificationManager 연동)",
    "recovery": "기존 복구 컴포넌트 호출",
    "emergency": "Emergency Mode 제어",
    "wait": "대기 (안정화 확인)",
}


# =============================================================================
# Runbook Registry
# =============================================================================


class RunbookRegistry:
    """런북 등록/조회/관리 — 분산 동기화 + 스키마 검증 + 불변성 보장.

    기존 패턴 결합:
    - ProviderRegistry (factory.py)         — 등록/조회 인터페이스
    - EmergencyMode 이벤트 구독 패턴        — 분산 캐시 무효화
    - SystemControlManager 영속화 패턴      — StateBackend 저장/복원
    - SagaOrchestrator 의존성 주입 패턴     — 생성자 None 기본값

    차이점:
    - ProviderRegistry: classmethod 기반 정적 레지스트리
    - RunbookRegistry : 인스턴스 기반 — 런타임 등록/비활성화/분산 동기화 지원

    불변성 보장:
    - get(), get_enabled(), get_all(), get_by_tag() 는 deepcopy를 반환한다.
    - 외부에서 반환된 Runbook을 수정해도 레지스트리 내부 상태에 영향이 없다.
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

        Args:
            event_bus         : SelfHealingEventBus (None이면 지연 로드)
            state_backend     : StateBackend (None이면 지연 로드)
            primitive_registry: ActionPrimitiveRegistry (None이면 스키마 검증 건너뜀)
        """
        self._runbooks: dict[str, Runbook] = {}
        self._lock = threading.Lock()
        self._event_bus = event_bus
        self._state_backend = state_backend
        self._primitive_registry = primitive_registry

        # StateBackend의 enabled 맵 캐시 — 부팅 직후 1회 로드하여
        # register() 시점에 개별 런북의 enabled 상태를 복원한다.
        # 매 register()마다 Redis 호출을 피하기 위한 로컬 캐시.
        self._cached_enabled_map: dict[str, bool] = {}

        # 분산 동기화: 다른 노드의 레지스트리 변경 이벤트 구독
        self._register_event_handlers()
        # StateBackend에서 enabled 상태 로드 (앱 재시작 시)
        self._load_enabled_cache()

    # =========================================================================
    # 분산 동기화 — EmergencyMode 이벤트 구독 패턴
    # =========================================================================

    def _register_event_handlers(self) -> None:
        """EventBus 핸들러 등록.

        다른 노드에서 발생한 레지스트리 변경 이벤트를 구독하여
        로컬 상태를 동기화한다.
        """
        bus = self._get_event_bus()
        if bus:
            try:
                from selfhealing.services.event_bus.bus import EventType

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

        자신이 발행한 이벤트는 무시하여 무한 루프를 방지한다.
        """
        if event.source == "runbook_registry":
            return  # 자신이 발행한 이벤트 무시

        action = event.data.get("action")
        runbook_id = event.data.get("runbook_id")

        if action == "set_enabled":
            with self._lock:
                rb = self._runbooks.get(runbook_id)
                if rb:
                    rb.enabled = event.data.get("enabled", True)
                    logger.debug(
                        "runbook_registry.remote_enable_synced",
                        runbook_id=runbook_id,
                        enabled=rb.enabled,
                    )
        elif action == "unregister":
            with self._lock:
                removed = self._runbooks.pop(runbook_id, None)
                if removed:
                    logger.debug(
                        "runbook_registry.remote_unregister_synced",
                        runbook_id=runbook_id,
                    )

    def _broadcast_change(self, action: str, runbook_id: str, **extra: Any) -> None:
        """레지스트리 변경을 EventBus로 브로드캐스트.

        RedisEventBus가 활성 상태면 Redis Pub/Sub으로 전 노드에 전파된다.
        """
        bus = self._get_event_bus()
        if bus:
            try:
                from selfhealing.services.event_bus.bus import EventType

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
    # StateBackend 영속화 — SystemControlManager 패턴
    # =========================================================================

    def _load_enabled_cache(self) -> None:
        """StateBackend에서 enabled 맵을 로드하여 로컬 캐시에 저장.

        부팅 직후 __init__()에서 1회 호출된다.
        이 시점에는 _runbooks가 비어 있으므로 직접 적용하지 않고 캐시만 저장한다.
        이후 register()에서 개별 런북 등록 시 캐시를 참조하여 enabled 상태를 복원한다.
        """
        backend = self._get_state_backend()
        if backend:
            try:
                data = backend.get(self._ENABLED_KEY)
                if data and isinstance(data, dict):
                    self._cached_enabled_map = {k: bool(v) for k, v in data.items()}
            except Exception as exc:
                logger.warning(
                    "runbook_registry.load_enabled_cache_failed",
                    error=str(exc),
                )

    def _persist_enabled_state(self) -> None:
        """enabled 상태를 StateBackend에 영속화."""
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
        장애 복구 중 KeyError/TypeError 발생 시 피해가 배가되므로 원천 방지.

        Raises:
            ValueError: 구조 검증 또는 파라미터 스키마 검증 실패 시
        """
        # 1단계: 구조적 유효성 검증
        valid, error = runbook.validate()
        if not valid:
            raise ValueError(f"Invalid runbook '{runbook.id}': {error}")

        # 2단계: 파라미터 스키마 검증
        self._validate_all_step_params(runbook)

        with self._lock:
            self._runbooks[runbook.id] = runbook

            # StateBackend 캐시에 이전 enabled 상태가 있으면 복원한다.
            # Kill Switch(set_enabled=False)가 앱 재시작 후에도 유지되도록 보장.
            if runbook.id in self._cached_enabled_map:
                runbook.enabled = self._cached_enabled_map[runbook.id]

        logger.info(
            "runbook_registry.registered",
            runbook_id=runbook.id,
            steps=len(runbook.steps),
            risk_level=runbook.risk_level.value,
            enabled=runbook.enabled,
        )

    def _validate_all_step_params(self, runbook: Runbook) -> None:
        """모든 Step의 params를 Action Primitive의 Pydantic 스키마로 사전 검증.

        템플릿 변수({...}) 포함 값은 정적 검증에서 제외한다.
        실행 시점에 치환 후 재검증된다.

        Raises:
            ValueError: 스키마 검증 실패 시 (어느 Step, 어느 action 인지 명시)
        """
        primitives = self._get_primitive_registry()
        if not primitives:
            return

        for step in runbook.steps:
            schema = primitives.get_schema(step.action)
            if schema is None:
                continue

            # 템플릿 변수({var_name}) 포함 값은 정적 검증에서 제외.
            # 정규식으로 Python format 변수만 감지 — JSON 문자열 오탐 방지.
            template_keys = {
                k
                for k, v in step.params.items()
                if isinstance(v, str) and _TEMPLATE_VAR_RE.search(v)
            }
            static_params = {
                k: v for k, v in step.params.items() if k not in template_keys
            }

            # 모든 파라미터가 템플릿 변수이면 정적 검증 불가 — 실행 시점에 재검증
            if not static_params:
                continue

            try:
                schema(**static_params)
            except ValidationError as exc:
                if template_keys:
                    # 템플릿 변수로 필터된 필수 필드의 missing 에러는 무시한다.
                    # 해당 필드는 실행 시점에 치환 후 재검증된다.
                    non_template_errors = [
                        e
                        for e in exc.errors()
                        if not (
                            e["type"] == "missing"
                            and len(e["loc"]) == 1
                            and e["loc"][0] in template_keys
                        )
                    ]
                    if not non_template_errors:
                        continue
                raise ValueError(
                    f"Runbook '{runbook.id}' step '{step.name}' "
                    f"action '{step.action}' params 검증 실패: {exc}"
                ) from exc

    def unregister(self, runbook_id: str) -> bool:
        """런북 등록 해제 + 영속화 + 전 노드 브로드캐스트.

        Returns:
            True — 제거 성공, False — runbook_id가 존재하지 않음
        """
        with self._lock:
            removed = self._runbooks.pop(runbook_id, None) is not None

        if removed:
            self._persist_enabled_state()
            self._broadcast_change("unregister", runbook_id)
            logger.info("runbook_registry.unregistered", runbook_id=runbook_id)

        return removed

    def get(self, runbook_id: str) -> Runbook | None:
        """런북 조회 — deepcopy 반환으로 불변성 보장.

        반환된 Runbook을 외부에서 수정해도 레지스트리 내부 상태에 영향이 없다.
        275번 Executor는 이 메서드로 받은 스냅샷을 실행 끝까지 사용한다.
        """
        with self._lock:
            rb = self._runbooks.get(runbook_id)
            return copy.deepcopy(rb) if rb else None

    def get_enabled(self) -> list[Runbook]:
        """활성화된 런북 목록 — deepcopy 반환.

        PatternMatcher.evaluate_all()에서 호출된다.
        RunbookRegistryLike.get_active_runbooks() 인터페이스를 만족한다.
        """
        with self._lock:
            return [copy.deepcopy(rb) for rb in self._runbooks.values() if rb.enabled]

    def get_active_runbooks(self) -> list[Runbook]:
        """PatternMatcher의 RunbookRegistryLike.get_active_runbooks() 인터페이스 구현.

        get_enabled()의 별칭.
        """
        return self.get_enabled()

    def set_enabled(self, runbook_id: str, enabled: bool) -> bool:
        """런북 활성화/비활성화 — Redis 영속화 + 전 노드 브로드캐스트.

        Kill Switch 역할: 장애 확산 방지의 생명줄.

        동기화 체인:
        1. 로컬 메모리 업데이트
        2. StateBackend(Redis)에 영속화
        3. EventBus로 전 노드 브로드캐스트

        Returns:
            True — 업데이트 성공, False — runbook_id가 존재하지 않음
        """
        with self._lock:
            rb = self._runbooks.get(runbook_id)
            if not rb:
                return False
            rb.enabled = enabled

        self._persist_enabled_state()
        self._broadcast_change("set_enabled", runbook_id, enabled=enabled)
        logger.info(
            "runbook_registry.enabled_changed",
            runbook_id=runbook_id,
            enabled=enabled,
        )
        return True

    def get_all(self) -> list[Runbook]:
        """전체 런북 목록 (비활성화 포함) — deepcopy 반환."""
        with self._lock:
            return [copy.deepcopy(rb) for rb in self._runbooks.values()]

    def get_by_tag(self, tag: str) -> list[Runbook]:
        """태그로 런북 필터 — deepcopy 반환."""
        with self._lock:
            return [
                copy.deepcopy(rb) for rb in self._runbooks.values() if tag in rb.tags
            ]

    # =========================================================================
    # 의존성 지연 로드 — SagaOrchestrator 패턴
    # =========================================================================

    def _get_event_bus(self) -> SelfHealingEventBus | None:
        """SagaOrchestrator._get_backend() 패턴 재사용 — 지연 로드."""
        if self._event_bus is None:
            try:
                from selfhealing.services.event_bus import get_event_bus

                self._event_bus = get_event_bus()
            except Exception:
                pass
        return self._event_bus

    def _get_state_backend(self) -> StateBackend | None:
        """StateBackend 지연 로드."""
        if self._state_backend is None:
            try:
                from selfhealing.core.state_backend import get_state_backend

                self._state_backend = get_state_backend()
            except Exception:
                pass
        return self._state_backend

    def _get_primitive_registry(self) -> ActionPrimitiveRegistry | None:
        """ActionPrimitiveRegistry 조회 (지연 로드 없음 — 주입 필수)."""
        return self._primitive_registry
