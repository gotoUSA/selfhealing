# 278. Runbook Service 통합 설계

> **Status**: Design
> **References**:
> - [272_RUNBOOK_ARCHITECTURE_OVERVIEW.md](272_RUNBOOK_ARCHITECTURE_OVERVIEW.md) — 전체 아키텍처
> - [273_RUNBOOK_PATTERN_MATCHER.md](273_RUNBOOK_PATTERN_MATCHER.md) — 이벤트/메트릭 → 패턴 감지
> - [274_RUNBOOK_REGISTRY.md](274_RUNBOOK_REGISTRY.md) — Runbook 정의 조회 + 프리미티브 등록
> - [275_RUNBOOK_EXECUTOR.md](275_RUNBOOK_EXECUTOR.md) — Step 순차 실행 + 보상
> - [276_RUNBOOK_APPROVAL_GATE.md](276_RUNBOOK_APPROVAL_GATE.md) — 리스크 기반 승인/차단
> - [277_RUNBOOK_PLAYBACK_RECORDER.md](277_RUNBOOK_PLAYBACK_RECORDER.md) — 감사 기록 + 학습 피드백
> - `services/event_bus/bus/__init__.py` — SelfHealingEventBus.subscribe/emit, EventType
> - `adapters/celery/tasks/*.py` — @shared_task Celery 태스크 패턴
> - `factory.py` — ProviderRegistry (싱글톤 DI 패턴)

---

## 1. 목적

273~277에서 설계한 5개 모듈을 **하나의 파이프라인으로 조립**하는
진입점(Entry Point) 서비스를 설계한다.

```
이벤트 → PatternMatcher → RunbookRegistry → ApprovalGate → Executor → Recorder
   273          274             276            275         277
```

이 문서는 위 파이프라인의 **오케스트레이션 로직**,
**트리거 방식(EventBus / Celery)**, **싱글톤 초기화** 를 정의한다.

---

## 2. 기존 코드 근거

### 2.1 SelfHealingEventBus.subscribe() (`services/event_bus/bus/__init__.py`)

```python
class SelfHealingEventBus:
    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[SelfHealingEvent], None],
        priority: EventPriority = EventPriority.NORMAL,
    ) -> EventSubscription:
        """
        이벤트 핸들러 구독.
        중복 구독 방지 (handler_name 기준).
        priority로 정렬 (높은 것 먼저).
        """

    def emit(
        self,
        event_type: EventType,
        data: dict[str, Any],
        ...
    ) -> None:
        """이벤트 발행 → 등록된 핸들러 순차 호출."""
```

- 기존 패턴: `_register_default_subscriptions()`에서 EventType → 핸들러를 일괄 등록
- Runbook Service도 동일 패턴으로 관심 EventType들을 구독

### 2.2 @shared_task Celery 패턴 (`adapters/celery/tasks/`)

```python
from celery import shared_task

@shared_task(
    bind=True,
    name="selfhealing.tasks.monitoring.run_health_check",
    max_retries=3,
    default_retry_delay=60,
)
def run_health_check(self, namespace: str = "global"):
    ...
```

- 기존 Celery 태스크: `monitoring`, `circuit_breaker`, `postmortem`, `dlq_replay`, `persistence`, `sla_notification`
- Celery Beat로 주기적 실행 또는 `.delay()`로 비동기 실행
- Runbook 실행도 동일하게 Celery 태스크로 래핑하여 비동기 처리

### 2.3 ProviderRegistry (`factory.py`)

```python
class ProviderRegistry:
    _registry: ClassVar[dict[str, Any]] = {}

    @classmethod
    def register(cls, key: str, provider: Any) -> None: ...

    @classmethod
    def get(cls, key: str) -> Any: ...
```

- 싱글톤 서비스 등록/조회 패턴
- RunbookService도 ProviderRegistry에 등록하여 다른 컴포넌트에서 접근 가능

---

## 3. RunbookService 클래스

```python
class RunbookService:
    """
    Runbook 시스템의 단일 진입점.

    273~277 모듈을 조합하여 전체 파이프라인을 오케스트레이션한다.
    LearningService와 동일한 싱글톤 패턴.

    파이프라인:
    1. PatternMatcher — 이벤트/메트릭에서 패턴 감지
    2. RunbookRegistry — 패턴 ID로 Runbook 조회
    3. RunbookApprovalGate — 거버넌스 체크 + 리스크 기반 승인
    4. RunbookExecutor — Step 순차 실행 + 보상
    5. RunbookPlaybackRecorder — 감사 기록 + 학습 피드백
    """

    _instance: RunbookService | None = None
    _lock = threading.Lock()

    def __new__(cls) -> RunbookService:
        """싱글톤 (LearningService 패턴)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # 의존성 (lazy init — 각 모듈의 싱글톤/팩토리 사용)
        self._pattern_matcher: PatternMatcher | None = None
        self._registry: RunbookRegistry | None = None
        self._approval_gate: RunbookApprovalGate | None = None
        self._executor: RunbookExecutor | None = None
        self._recorder: RunbookPlaybackRecorder | None = None
        self._event_bus: SelfHealingEventBus | None = None

        self._enabled = True
        self._initialized = True

        logger.info("runbook_service.initialized")
```

### 3.1 Lazy 의존성 획득

```python
def _get_pattern_matcher(self) -> PatternMatcher:
    if self._pattern_matcher is None:
        self._pattern_matcher = PatternMatcher()
    return self._pattern_matcher

def _get_registry(self) -> RunbookRegistry:
    if self._registry is None:
        self._registry = RunbookRegistry()
    return self._registry

def _get_approval_gate(self) -> RunbookApprovalGate:
    if self._approval_gate is None:
        self._approval_gate = RunbookApprovalGate()
    return self._approval_gate

def _get_executor(self) -> RunbookExecutor:
    if self._executor is None:
        self._executor = RunbookExecutor()
    return self._executor

def _get_recorder(self) -> RunbookPlaybackRecorder:
    if self._recorder is None:
        self._recorder = RunbookPlaybackRecorder()
    return self._recorder

def _get_event_bus(self) -> SelfHealingEventBus:
    if self._event_bus is None:
        from selfhealing.services.event_bus import get_event_bus
        self._event_bus = get_event_bus()
    return self._event_bus
```

---

## 4. 전체 파이프라인

### 4.1 자동 실행 파이프라인 (`handle_event`)

EventBus 이벤트 수신 시 호출되는 핵심 메서드:

```python
def handle_event(self, event: SelfHealingEvent) -> RunbookExecutionContext | None:
    """
    이벤트 수신 → 패턴 매칭 → Runbook 실행 전체 파이프라인.

    EventBus 핸들러로 등록되어 자동 호출됨.

    Returns:
        RunbookExecutionContext (실행 완료 시)
        None (패턴 미매칭 또는 Runbook 없음)
    """
    if not self._enabled:
        return None

    # 1. 패턴 매칭 (273)
    matcher = self._get_pattern_matcher()
    matched_patterns = matcher.match(event)

    if not matched_patterns:
        return None  # 매칭 패턴 없음

    # 2. 매칭된 각 패턴에 대해 Runbook 조회 (274)
    registry = self._get_registry()
    for pattern in matched_patterns:
        runbook = registry.find_by_pattern(pattern.pattern_id)
        if runbook is None:
            continue

        # 3. 파이프라인 실행
        return self._execute_pipeline(
            runbook=runbook,
            trigger_event=event.to_dict(),
            namespace=event.data.get("namespace", "global"),
        )

    return None  # Runbook 없음
```

### 4.2 수동 실행 API (`execute_runbook`)

운영자가 직접 Runbook ID를 지정하여 실행:

```python
def execute_runbook(
    self,
    runbook_id: str,
    namespace: str = "global",
    trigger_event: dict[str, Any] | None = None,
) -> RunbookExecutionContext:
    """
    Runbook을 수동으로 실행.

    API 또는 CLI에서 호출.

    Raises:
        RunbookNotFoundError: Runbook ID가 레지스트리에 없음
    """
    registry = self._get_registry()
    runbook = registry.get(runbook_id)
    if runbook is None:
        raise RunbookNotFoundError(f"Runbook not found: {runbook_id}")

    return self._execute_pipeline(
        runbook=runbook,
        trigger_event=trigger_event or {"manual": True, "runbook_id": runbook_id},
        namespace=namespace,
    )
```

### 4.3 파이프라인 실행 (`_execute_pipeline`)

```python
def _execute_pipeline(
    self,
    runbook: Runbook,
    trigger_event: dict[str, Any],
    namespace: str,
) -> RunbookExecutionContext:
    """
    승인 → 실행 → 기록 파이프라인.

    순서:
    1. ApprovalGate (276) — 거버넌스 + 리스크 기반 승인
    2. Executor (275) — Step 실행 + Lock + 멱등성 + 보상
    3. Recorder (277) — 감사 기록 + 학습 + Postmortem
    """
    executor = self._get_executor()
    recorder = self._get_recorder()

    # 1~2. 승인 + 실행 (275에서 276을 내부적으로 호출)
    ctx = executor.execute_runbook(runbook, trigger_event, namespace)

    # 3. 기록 (277)
    compensation = getattr(ctx, '_compensation_summary', None)
    recorder.record(ctx, runbook, compensation)

    logger.info(
        "runbook_service.pipeline_completed",
        runbook_id=runbook.id,
        status=ctx.status.value,
        namespace=namespace,
    )

    return ctx
```

---

## 5. EventBus 구독

### 5.1 구독 등록

```python
def register_subscriptions(self) -> None:
    """
    EventBus에 Runbook 관련 핸들러를 등록.

    _register_default_subscriptions() 패턴을 따름.
    """
    event_bus = self._get_event_bus()

    # PatternMatcher가 관심 있는 이벤트 타입들을 구독
    # 273에서 등록된 패턴 조건의 event_type 목록을 기반으로 구독
    target_events = [
        EventType.EMERGENCY_LEVEL_CHANGED,
        EventType.ERROR_BUDGET_CRITICAL,
        EventType.CIRCUIT_BREAKER_STATE_CHANGED,
        EventType.HEALTH_CHECK_FAILED,
        EventType.SLA_VIOLATION_DETECTED,
    ]

    for event_type in target_events:
        event_bus.subscribe(
            event_type=event_type,
            handler=self._on_event_received,
            priority=EventPriority.LOW,  # 기존 핸들러보다 낮은 우선순위
        )

    logger.info(
        "runbook_service.subscriptions_registered",
        count=len(target_events),
    )
```

### 5.2 이벤트 핸들러

```python
def _on_event_received(self, event: SelfHealingEvent) -> None:
    """
    EventBus 이벤트 수신 핸들러.

    동기 실행 시 EventBus를 차단하므로,
    Celery 태스크로 비동기 위임한다.
    """
    try:
        # Celery 태스크로 비동기 실행
        from selfhealing.adapters.celery.tasks.runbook import (
            execute_runbook_for_event,
        )
        execute_runbook_for_event.delay(event.to_dict())
    except ImportError:
        # Celery 미사용 시 동기 실행 (개발/테스트 환경)
        self.handle_event(event)
    except Exception as e:
        logger.warning("runbook_service.event_dispatch_failed", error=e)
```

---

## 6. Celery 태스크

### 6.1 이벤트 기반 실행 태스크

```python
# adapters/celery/tasks/runbook.py

from celery import shared_task

@shared_task(
    bind=True,
    name="selfhealing.tasks.runbook.execute_runbook_for_event",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def execute_runbook_for_event(self, event_data: dict) -> dict:
    """
    이벤트 기반 Runbook 실행.

    EventBus → _on_event_received() → 이 태스크 (비동기).
    """
    try:
        from selfhealing.services.event_bus.bus import SelfHealingEvent
        from selfhealing.services.runbook.service import RunbookService

        event = SelfHealingEvent.from_dict(event_data)
        service = RunbookService()
        ctx = service.handle_event(event)

        if ctx is None:
            return {"status": "no_match", "event_type": event_data.get("event_type")}

        return {
            "status": ctx.status.value,
            "execution_id": ctx.execution_id,
            "runbook_id": ctx.runbook_id,
        }

    except Exception as e:
        logger.exception("runbook_task.event_execution_failed", error=e)
        raise self.retry(exc=e)
```

### 6.2 수동 실행 태스크

```python
@shared_task(
    bind=True,
    name="selfhealing.tasks.runbook.execute_runbook_manual",
    max_retries=0,
    acks_late=True,
)
def execute_runbook_manual(
    self,
    runbook_id: str,
    namespace: str = "global",
    trigger_event: dict | None = None,
) -> dict:
    """
    수동 Runbook 실행.

    API/CLI → 이 태스크 (비동기).
    """
    try:
        from selfhealing.services.runbook.service import RunbookService

        service = RunbookService()
        ctx = service.execute_runbook(runbook_id, namespace, trigger_event)

        return {
            "status": ctx.status.value,
            "execution_id": ctx.execution_id,
        }

    except Exception as e:
        logger.exception("runbook_task.manual_execution_failed", error=e)
        return {"status": "error", "error": str(e)}
```

### 6.3 MEDIUM 승인 타이머 체크 태스크

```python
@shared_task(
    name="selfhealing.tasks.runbook.check_approval_timers",
)
def check_approval_timers() -> dict:
    """
    MEDIUM 리스크 승인 타이머 만료 체크.

    Celery Beat에서 주기적으로 호출 (예: 매 30초).
    """
    from selfhealing.services.runbook.approval_gate import RunbookApprovalGate

    gate = RunbookApprovalGate()
    # 대기 중인 승인 요청 목록 조회 → 타이머 만료 체크
    expired = gate.check_all_pending_timers()

    return {"expired_count": len(expired)}
```

---

## 7. ProviderRegistry 등록

```python
# factory.py 또는 app 초기화 코드

from selfhealing.factory import ProviderRegistry
from selfhealing.services.runbook.service import RunbookService

# 싱글톤 등록
ProviderRegistry.register("runbook_service", RunbookService())
```

조회:
```python
service = ProviderRegistry.get("runbook_service")
service.execute_runbook("circuit_breaker_recovery", namespace="global")
```

---

## 8. 초기화 시퀀스

### 8.1 앱 시작 시

```python
def initialize_runbook_system() -> RunbookService:
    """
    Runbook 시스템 초기화.

    Django AppConfig.ready() 또는 Celery worker_init에서 호출.
    """
    # 1. 서비스 싱글톤 생성
    service = RunbookService()

    # 2. 빌트인 Runbook 등록 (274)
    from selfhealing.services.runbook.builtins import register_builtin_runbooks
    register_builtin_runbooks()

    # 3. 빌트인 프리미티브 등록 (274)
    from selfhealing.services.runbook.primitives import register_builtin_primitives
    register_builtin_primitives()

    # 4. EventBus 구독 등록
    service.register_subscriptions()

    # 5. ProviderRegistry 등록
    from selfhealing.factory import ProviderRegistry
    ProviderRegistry.register("runbook_service", service)

    logger.info("runbook_system.initialized")
    return service
```

### 8.2 Celery Beat 스케줄

```python
# settings 또는 celery_config

CELERY_BEAT_SCHEDULE = {
    # ... 기존 태스크들 ...

    "runbook-check-approval-timers": {
        "task": "selfhealing.tasks.runbook.check_approval_timers",
        "schedule": 30.0,  # 매 30초
    },
}
```

---

## 9. API 엔드포인트 (Django)

```python
# adapters/django/views/runbook.py (개요만)

# POST /api/selfhealing/runbook/execute/
#   body: { "runbook_id": "...", "namespace": "global" }
#   → execute_runbook_manual.delay(runbook_id, namespace)
#   → 202 Accepted + {"execution_id": "..."}

# POST /api/selfhealing/runbook/approve/
#   body: { "execution_id": "...", "approved_by": "admin" }
#   → RunbookApprovalGate.approve_runbook(execution_id, approved_by)
#   → 200 OK + ApprovalDecision

# POST /api/selfhealing/runbook/reject/
#   body: { "execution_id": "...", "rejected_by": "admin", "reason": "..." }
#   → RunbookApprovalGate.reject_runbook(execution_id, rejected_by, reason)
#   → 200 OK + ApprovalDecision

# GET /api/selfhealing/runbook/status/{execution_id}/
#   → RunbookExecutor._load_context(execution_id)
#   → 200 OK + RunbookExecutionContext

# GET /api/selfhealing/runbook/list/
#   → RunbookRegistry.list_all()
#   → 200 OK + [Runbook]
```

---

## 10. 설정 요약

```python
# Pydantic BaseSettings, SELFHEALING_ 접두사

SELFHEALING_RUNBOOK_ENABLED: bool = True
# Runbook 시스템 활성화 여부

SELFHEALING_RUNBOOK_ASYNC_EXECUTION: bool = True
# True: Celery 태스크로 비동기 실행
# False: EventBus 핸들러에서 동기 실행 (개발/테스트용)

SELFHEALING_RUNBOOK_EVENT_PRIORITY: str = "LOW"
# EventBus 구독 우선순위 (기존 핸들러보다 낮게)

# 나머지 설정은 각 모듈 문서 참조:
# 273: SELFHEALING_RUNBOOK_PATTERN_DEDUP_SECONDS
# 274: (레지스트리는 코드 기반, 별도 설정 없음)
# 275: SELFHEALING_RUNBOOK_DEFAULT_STEP_TIMEOUT_SECONDS, GLOBAL_TIMEOUT_SECONDS 등
# 276: SELFHEALING_RUNBOOK_APPROVAL_TIMER_SECONDS 등
# 277: (Recorder는 기존 컴포넌트 설정 그대로 사용)
```

---

## 11. 최종 모듈 구조

```
packages/selfhealing-python/src/selfhealing/
├── services/
│   └── runbook/
│       ├── __init__.py              ← 패키지 공개 API
│       ├── service.py               ← RunbookService (278, 이 문서)
│       ├── pattern_matcher.py       ← PatternMatcher (273)
│       ├── registry.py              ← RunbookRegistry, ActionPrimitiveRegistry (274)
│       ├── executor.py              ← RunbookExecutor (275)
│       ├── approval_gate.py         ← RunbookApprovalGate (276)
│       ├── recorder.py              ← RunbookPlaybackRecorder (277)
│       ├── models.py                ← 공용 데이터 모델
│       ├── exceptions.py            ← 에러 타입
│       ├── builtins.py              ← 빌트인 Runbook 정의
│       └── primitives.py            ← 빌트인 프리미티브 등록
│
├── adapters/
│   └── celery/
│       └── tasks/
│           └── runbook.py           ← Celery 태스크 (278)
│
└── adapters/
    └── django/
        └── views/
            └── runbook.py           ← REST API (278)
```

---

## 12. 전체 데이터 흐름 다이어그램

```
┌─────────────┐    EventBus     ┌──────────────────┐
│  기존 시스템  │ ──subscribe──→ │  RunbookService   │
│  (EventBus)  │                │  (278, 진입점)    │
└─────────────┘                └──────┬───────────┘
                                      │ 1. handle_event()
                                      ▼
                               ┌──────────────────┐
                               │  PatternMatcher   │
                               │  (273, 패턴 감지)  │
                               └──────┬───────────┘
                                      │ matched_patterns
                                      ▼
                               ┌──────────────────┐
                               │  RunbookRegistry  │
                               │  (274, 조회)      │
                               └──────┬───────────┘
                                      │ runbook
                                      ▼
                               ┌──────────────────┐
                               │  ApprovalGate     │
                               │  (276, 승인)      │
                               └──────┬───────────┘
                                      │ approved
                                      ▼
                               ┌──────────────────┐
                               │  RunbookExecutor  │──→ ActionExecutor
                               │  (275, 실행)      │──→ DistributedRecoveryLock
                               │                   │──→ IdempotencyRecord
                               └──────┬───────────┘
                                      │ ctx (완료/실패)
                                      ▼
                               ┌──────────────────┐
                               │  PlaybackRecorder │──→ CascadeEventAuditor
                               │  (277, 기록)      │──→ LearningService
                               │                   │──→ PostmortemStore
                               └──────────────────┘──→ EventBus (emit)
```

---

## 13. 기존 컴포넌트와의 관계 총정리

| 기존 컴포넌트 | Runbook 시스템에서의 역할 | 관련 문서 |
|---|---|---|
| **SelfHealingEventBus** | 트리거 수신 (subscribe) + 결과 발행 (emit) | 273, 277, 278 |
| **EventType** | 트리거 이벤트 타입 + 신규 RUNBOOK_ 이벤트 | 273, 277 |
| **ActionExecutor** | Step 프리미티브 실행 (ExecutionMode 준수) | 275 |
| **ExecutionMode** | ACTIVE/SHADOW/EVALUATION 자동 적용 | 275 |
| **SagaStep ABC** | execute + compensate 인터페이스 패턴 | 275 |
| **IdempotentStepHandler** | 멱등성 키 + 중복 실행 방지 | 275 |
| **DistributedRecoveryLock** | 동일 namespace Lock 공유 (RecoveryCoordinator와 상호 배제) | 275 |
| **GovernanceCheckMixin** | Kill Switch / Emergency / Error Budget 체크 | 276 |
| **ApprovalMixin** | WAITING → APPROVED 수동 승인 패턴 | 276 |
| **UnifiedNotificationManager** | 승인 요청 알림 발송 | 276 |
| **CascadeEventAuditor** | Step별 인과 관계 감사 기록 | 277 |
| **LearningService** | 성공/실패 패턴 학습 피드백 | 277 |
| **PostmortemStore** | 실패 시 자동 인시던트 생성 | 277 |
| **store_to_dlq** | 실패 정보 DLQ 저장 (Fail-Open) | 275 |
| **ProviderRegistry** | 싱글톤 서비스 등록/조회 | 278 |
| **@shared_task** | Celery 비동기 실행 | 278 |
| **StateBackend** | 컨텍스트/승인 요청 영속화 | 275, 276 |

Runbook 시스템은 위 컴포넌트들의 **파사드(Facade)**로 동작하며,
기존 하드코딩된 경로(RecoveryCoordinator, ProtectionOrchestrator, AutoRollbackGuard)는
그대로 유지된다.

---

## 14. 파이프라인 Suspend & Resume 분리

### 14.1 문제

§4.3의 `_execute_pipeline`은 동기적으로 `executor.execute_runbook()`을 호출한다.
그러나 `ApprovalGate.evaluate_approval()`이 MEDIUM/HIGH 리스크에서
`ApprovalDecisionType.WAITING`을 반환하면(`approval_gate.py:218-230`),
Celery 태스크가 승인 완료까지 블로킹되거나 승인 없이 Executor로 넘어가는 결함이 발생한다.

### 14.2 코드 근거

`evaluate_approval()`의 반환값 분기(`approval_gate.py:206-246`):
```python
if risk == RiskLevel.LOW:
    return ApprovalDecision(decision_type=ApprovalDecisionType.AUTO_APPROVED, ...)

elif risk == RiskLevel.MEDIUM:
    self._create_approval_request(runbook, ctx, with_timer=True)
    return ApprovalDecision(decision_type=ApprovalDecisionType.WAITING, ...)

elif risk == RiskLevel.HIGH:
    self._create_approval_request(runbook, ctx, with_timer=False)
    return ApprovalDecision(decision_type=ApprovalDecisionType.WAITING, ...)
```

승인 후 재개 흐름은 이미 구현되어 있다(`approval_gate.py:297-298`):
```python
# approve_runbook() 내부
self._trigger_resume(execution_id)  # → resume_runbook_task.apply_async()
```

`RunbookExecutionStatus`에 `WAITING_APPROVAL` 상태도 정의되어 있다(`execution_models.py:88`):
```python
WAITING_APPROVAL = "waiting_approval"
```

### 14.3 설계

`_execute_pipeline`을 **2경로로 분할**한다:

```python
def _execute_pipeline(
    self,
    runbook: Runbook,
    trigger_event: dict[str, Any],
    namespace: str,
) -> RunbookExecutionContext:
    """
    승인 → 실행 → 기록 파이프라인.

    경로 A (LOW 리스크): 승인 즉시 → 실행 → 기록 (동기)
    경로 B (MEDIUM/HIGH): 승인 대기 → Suspend → return
        → 승인 후 _trigger_resume() → resume_pipeline() → 실행 → 기록 (비동기)
    """
    approval_gate = self._get_approval_gate()
    executor = self._get_executor()

    # 실행 컨텍스트 사전 생성 (approve 시 execution_id 필요)
    from uuid import uuid4
    execution_id = f"runbook-{uuid4()}"
    ctx = RunbookExecutionContext(
        execution_id=execution_id,
        runbook_id=runbook.id,
        namespace=namespace,
        trigger_event=trigger_event,
        runbook_version=runbook.version,
    )

    # 1. 승인 평가 (276)
    decision = approval_gate.evaluate_approval(runbook, ctx)

    if decision.decision_type == ApprovalDecisionType.BLOCKED:
        ctx.status = RunbookExecutionStatus.FAILED
        ctx.abort_reason = decision.block_message
        executor._save_context(ctx)
        return ctx

    if decision.decision_type == ApprovalDecisionType.WAITING:
        # 경로 B: Suspend — 컨텍스트 저장 후 즉시 반환
        ctx.status = RunbookExecutionStatus.WAITING_APPROVAL
        executor._save_context(ctx)
        return ctx

    # 경로 A: AUTO_APPROVED — 즉시 실행
    return self._run_and_record(runbook, trigger_event, namespace)


def resume_pipeline(self, execution_id: str) -> RunbookExecutionContext:
    """
    승인 완료 후 파이프라인 재개.

    호출 경로: approve_runbook() → _trigger_resume() → resume_runbook_task
             → RunbookService.resume_pipeline()

    RunbookExecutor.resume_execution()의 3단계 방어가 적용된다:
    1. Stale Context 거부 (resume_stale_threshold_seconds, executor.py:248-263)
    2. Runbook 버전 호환성 검증 (executor.py:266-277)
    3. 무한 재개 방지 (max_resume_count, executor.py:280-288)
    """
    executor = self._get_executor()
    recorder = self._get_recorder()

    ctx = executor.resume_execution(execution_id)

    # 기록 (277)
    compensation = getattr(ctx, '_compensation_summary', None)
    recorder.record(ctx, ctx.runbook_id, compensation)

    return ctx


def _run_and_record(
    self,
    runbook: Runbook,
    trigger_event: dict[str, Any],
    namespace: str,
) -> RunbookExecutionContext:
    """AUTO_APPROVED 실행 + 기록. 내부 헬퍼."""
    executor = self._get_executor()
    recorder = self._get_recorder()

    ctx = executor.execute_runbook(runbook, trigger_event, namespace)

    compensation = getattr(ctx, '_compensation_summary', None)
    recorder.record(ctx, runbook, compensation)

    return ctx
```

### 14.4 Celery 태스크 수정

§6.1의 `resume_runbook_task`가 `resume_pipeline()`을 호출하도록 연결:

```python
# services/runbook/tasks.py (기존 _trigger_resume()에서 참조)

@shared_task(
    bind=True,
    name="selfhealing.runbook.resume_pipeline",
    max_retries=3,
    default_retry_delay=60,
    queue="selfhealing_runbook",
)
def resume_runbook_task(self, execution_id: str) -> dict:
    """승인 후 파이프라인 재개."""
    from selfhealing.services.runbook.service import RunbookService

    service = RunbookService()
    ctx = service.resume_pipeline(execution_id)
    return {"status": ctx.status.value, "execution_id": execution_id}
```

---

## 15. Orphaned Execution Watchdog

### 15.1 문제

Celery 워커가 런북 실행 중 OOM/SIGKILL로 강제 종료되면,
해당 런북은 `EXECUTING` 상태로 영구히 남는다(고아 런북).
또한 §14에서 추가된 `WAITING_APPROVAL` 상태에서 `_trigger_resume()`의
`apply_async()`가 실패하면, 승인은 완료되었으나 재개되지 않는 고아도 발생한다.

### 15.2 코드 근거 — Saga 선례

`scan_orphan_sagas`(`saga/tasks.py:85-149`):
```python
@shared_task(name="selfhealing.scan_orphan_sagas", queue="selfhealing_recovery")
def scan_orphan_sagas() -> dict[str, Any]:
    # RUNNING/COMPENSATING + started_at > STALE_THRESHOLD(5분) → 고아 판별
    # GC Pause 방어: 락 획득 시도 → 성공 시에만 resume_saga_instance_task.delay()
```

`RunbookExecutor.resume_execution()`(`executor.py:219-303`)에 3단계 방어 내장:
- Stale Context 거부(`executor.py:248-263`)
- Runbook 버전 호환성(`executor.py:266-277`)
- 무한 재개 방지 max_resume_count(`executor.py:280-288`)

### 15.3 설계

```python
# services/runbook/tasks.py

ORPHAN_SCAN_STALE_THRESHOLD_SECONDS = 600
"""고아 런북 판별 임계값 (초). 10분.
런북이 Saga(5분)보다 오래 걸리므로 2배로 설정."""

APPROVED_STALE_THRESHOLD_SECONDS = 120
"""승인 완료 후 재개되지 않은 런북 판별 임계값 (초). 2분."""

@shared_task(
    name="selfhealing.runbook.scan_orphan_executions",
    queue="selfhealing_recovery",
)
def scan_orphan_runbook_executions() -> dict[str, Any]:
    """고아 런북을 주기적으로 스캔하여 재개 또는 실패 처리.

    Celery Beat로 2분 간격 실행.

    스캔 대상:
    1. status=EXECUTING + started_at > ORPHAN_SCAN_STALE_THRESHOLD(10분) → 고아
    2. status=WAITING_APPROVAL + approval이 APPROVED인데 재개되지 않음 → 유실 메시지 복구

    GC Pause 방어 (scan_orphan_sagas 패턴):
    - 재개 전 분산 락 획득 시도
    - 락 획득 성공 시에만 resume_runbook_task.delay() 디스패치

    Returns:
        {"scanned": N, "resumed": N, "skipped": N}
    """
    from selfhealing.core.state_backend import get_state_backend
    from selfhealing.services.coordination.distributed_recovery_lock import (
        get_distributed_recovery_lock,
    )

    backend = get_state_backend()
    lock = get_distributed_recovery_lock()
    results = {"scanned": 0, "resumed": 0, "skipped": 0}
    now_utc = datetime.now(timezone.utc)

    # 활성 런북 실행 컨텍스트 스캔
    # 인덱스 키 사용: selfhealing:runbook:active_executions (Set)
    active_keys = backend.get_all("selfhealing:runbook:execution:*", max_keys=200)
    results["scanned"] = len(active_keys)

    for key, data in active_keys.items():
        ctx = RunbookExecutionContext.from_dict(data)
        is_orphan = False

        if ctx.status == RunbookExecutionStatus.EXECUTING:
            if ctx.started_at:
                started = datetime.fromisoformat(ctx.started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                is_orphan = (now_utc - started).total_seconds() > ORPHAN_SCAN_STALE_THRESHOLD_SECONDS

        elif ctx.status == RunbookExecutionStatus.WAITING_APPROVAL:
            # APPROVED 상태인데 resume이 안 된 건 (메시지 유실)
            is_orphan = _is_approved_but_not_resumed(ctx, now_utc)

        if not is_orphan:
            results["skipped"] += 1
            continue

        # GC Pause 방어: 락 획득 시도
        acquired = lock.acquire(ctx.namespace, ctx.execution_id)
        if acquired:
            lock.release(ctx.namespace, ctx.execution_id)
            resume_runbook_task.delay(ctx.execution_id)
            results["resumed"] += 1
        else:
            results["skipped"] += 1

    return results
```

### 15.4 Celery Beat 스케줄 추가

§8.2에 추가:
```python
CELERY_BEAT_SCHEDULE = {
    # ... 기존 ...
    "runbook-scan-orphan-executions": {
        "task": "selfhealing.runbook.scan_orphan_executions",
        "schedule": 120.0,  # 2분 간격
        "options": {
            "expires": 110,
            "queue": "selfhealing_recovery",
        },
    },
}
```

### 15.5 설정

§10에 추가:
```python
SELFHEALING_RUNBOOK_ORPHAN_SCAN_INTERVAL_SECONDS: int = 120
# 고아 스캔 Beat 간격 (초). 기본 2분

SELFHEALING_RUNBOOK_ORPHAN_STALE_THRESHOLD_SECONDS: int = 600
# EXECUTING 상태 고아 판별 임계값 (초). 기본 10분
```

---

## 16. 수동 실행 시 락 충돌 처리 및 취소 API

### 16.1 선택: A안 (기존 락에 막혀 실패 처리)

**B안(락 탈취/Preemption) 비채택 사유**:

1. `DistributedRecoveryLock`이 Redis SET NX PX 기반 바이너리 락으로 Preemption을 지원하지 않는다
   (`distributed_recovery_lock.py:194-200`)
2. 실행 중인 런북의 Step을 외부에서 강제 중단하면, `_compensate_steps()`의 Fail-Open 시맨틱이
   비동기 취소 상황의 보상 정합성을 보장하지 못한다
3. 기존 프로젝트의 모든 분산 락(Saga, Recovery)이 Fail-Fast 시맨틱을 따른다
   (`distributed_recovery_lock.py:187`: "blocking=True는 권장하지 않음")

### 16.2 수동 실행 API 응답에 락 소유자 정보 포함

`execute_runbook_manual` 태스크(§6.2)에서 `RunbookLockConflictError` 발생 시
`get_lock_owner()`(`distributed_recovery_lock.py:329-349`)를 호출하여
현재 락 소유자 정보를 응답에 포함한다:

```python
@shared_task(...)
def execute_runbook_manual(self, runbook_id, namespace, trigger_event):
    try:
        service = RunbookService()
        ctx = service.execute_runbook(runbook_id, namespace, trigger_event)
        return {"status": ctx.status.value, "execution_id": ctx.execution_id}
    except RunbookLockConflictError:
        from selfhealing.services.coordination.distributed_recovery_lock import (
            get_distributed_recovery_lock,
        )
        lock = get_distributed_recovery_lock()
        owner = lock.get_lock_owner(namespace)
        return {
            "status": "lock_conflict",
            "current_owner": owner,
            "namespace": namespace,
            "message": (
                f"Namespace '{namespace}'에 다른 실행이 진행 중이다. "
                f"cancel_runbook_execution()으로 기존 실행을 취소하거나 "
                f"완료될 때까지 대기하라."
            ),
        }
```

### 16.3 우아한 취소 API (cancel_runbook_execution)

`CANCELLED` 상태와 `WAITING_APPROVAL → CANCELLED` 전이가
이미 정의되어 있다(`execution_models.py:78, 100`).
`abort_reason` 필드도 존재한다(`execution_models.py:274`).

**EXECUTING → CANCELLED 전이 추가**:
허용 전이에 `EXECUTING → CANCELLED`를 추가한다. 다만 실행 중인 Step을
즉시 중단하지 않고, **현재 Step 완료 후** 보상을 태운 뒤 CANCELLED로 전환한다.

```python
# RunbookService에 추가

def cancel_runbook_execution(
    self,
    execution_id: str,
    cancelled_by: str,
    reason: str = "",
) -> RunbookExecutionContext:
    """실행 중인 런북을 우아하게 취소.

    현재 Step까지만 완료 후 역순 보상(compensate)을 실행하고 CANCELLED로 전환.

    허용 상태:
    - WAITING_APPROVAL → 즉시 CANCELLED (보상 불필요)
    - EXECUTING → 취소 시그널 → 현재 Step 완료 → 보상 → CANCELLED
    - PENDING → 즉시 CANCELLED

    코드 근거:
    - CANCELLED 상태: execution_models.py:100
    - WAITING_APPROVAL → CANCELLED 전이: execution_models.py:78
    - abort_reason 필드: execution_models.py:274
    """
    executor = self._get_executor()
    ctx = executor._load_context(execution_id)
    if ctx is None:
        raise RunbookExecutionError(f"실행을 찾을 수 없다: {execution_id}")

    cancellable = {
        RunbookExecutionStatus.PENDING,
        RunbookExecutionStatus.WAITING_APPROVAL,
        RunbookExecutionStatus.EXECUTING,
    }
    if ctx.status not in cancellable:
        raise RunbookExecutionError(
            f"상태 '{ctx.status.value}'인 실행은 취소할 수 없다."
        )

    # WAITING_APPROVAL / PENDING → 즉시 취소 (보상 불필요)
    if ctx.status in {RunbookExecutionStatus.PENDING, RunbookExecutionStatus.WAITING_APPROVAL}:
        ctx.status = RunbookExecutionStatus.CANCELLED
        ctx.abort_reason = f"Cancelled by {cancelled_by}: {reason}"
        executor._save_context(ctx)
        return ctx

    # EXECUTING → 취소 플래그 설정
    # 다음 Step 진입 시 _run_from_step()에서 플래그를 확인하고
    # 현재 Step 완료 후 보상을 태움
    ctx.variables["__cancel_requested"] = True
    ctx.variables["__cancelled_by"] = cancelled_by
    ctx.variables["__cancel_reason"] = reason
    executor._save_context(ctx)

    logger.info(
        "runbook_service.cancel_requested",
        execution_id=execution_id,
        cancelled_by=cancelled_by,
    )

    return ctx
```

### 16.4 REST API 엔드포인트

§9에 추가:
```python
# POST /api/selfhealing/runbook/cancel/
#   body: { "execution_id": "...", "cancelled_by": "admin", "reason": "..." }
#   → RunbookService.cancel_runbook_execution(execution_id, cancelled_by, reason)
#   → 200 OK + RunbookExecutionContext
```

---

## 17. 무한 루프(Recursive Trigger) 방지

### 17.1 문제

런북 실행(예: Circuit Breaker 강제 오픈)이 새 이벤트를 EventBus에 발행하면,
PatternMatcher가 이 이벤트를 감지하여 런북이 런북을 트리거하는 무한 루프가 발생한다.

### 17.2 코드 근거

`RunbookRegistry._on_registry_updated()`(`runbook_registry.py:558-564`)에
자기 자신이 발행한 이벤트를 무시하는 패턴이 이미 존재한다:
```python
def _on_registry_updated(self, event: SelfHealingEvent) -> None:
    if event.source == "runbook_registry":
        return  # 자신이 발행한 이벤트 무시
```

그러나 `handle_event()`(§4.1)에는 이 소스 필터링이 적용되어 있지 않다.

`trigger_context`(`models.py:330-338`)에 `triggered_by_event`가 포함되지만,
이는 재귀 방지가 아닌 Postmortem Root Cause Link용이다.

### 17.3 설계

**소스 필터링 + 재귀 깊이 제한 이중 방어**를 적용한다:

```python
# RunbookService.handle_event() 수정 — §4.1 교체

MAX_CASCADE_DEPTH = 3
"""런북 → 런북 체이닝 최대 깊이. 의도적 에스컬레이션(1차 복구 실패 → 2차)을 허용하되 무한 루프 방지."""

def handle_event(self, event: SelfHealingEvent) -> RunbookExecutionContext | None:
    if not self._enabled:
        return None

    # 방어 1: 런북 실행으로 인해 파생된 이벤트 필터링
    # runbook_registry.py:563 패턴 — event.source 기반 자기 참조 차단
    if event.source == "runbook_executor":
        logger.debug(
            "runbook_service.skip_executor_event",
            event_type=event.event_type,
            source=event.source,
        )
        return None

    # 방어 2: 재귀 깊이 제한 — trigger_context 체인 추적
    cascade_depth = event.data.get("trigger_context", {}).get("cascade_depth", 0)
    if cascade_depth >= MAX_CASCADE_DEPTH:
        logger.warning(
            "runbook_service.max_cascade_depth_exceeded",
            event_type=event.event_type,
            cascade_depth=cascade_depth,
        )
        return None

    # 1. 패턴 매칭 (273)
    matcher = self._get_pattern_matcher()
    matched_patterns = matcher.match(event)

    if not matched_patterns:
        return None

    # 2. 매칭된 각 패턴에 대해 Runbook 조회 (274)
    registry = self._get_registry()
    for pattern in matched_patterns:
        runbook = registry.find_by_pattern(pattern.pattern_id)
        if runbook is None:
            continue

        # trigger_event에 cascade_depth 전파
        trigger_event = event.to_dict()
        trigger_event.setdefault("trigger_context", {})["cascade_depth"] = cascade_depth + 1

        return self._execute_pipeline(
            runbook=runbook,
            trigger_event=trigger_event,
            namespace=event.data.get("namespace", "global"),
        )

    return None
```

### 17.4 Executor 이벤트 발행 시 source 태깅

`RunbookExecutor`가 이벤트를 발행할 때 `source="runbook_executor"`를 명시해야 한다.
`RecoveryCoordinator`가 `source="recovery_coordinator"`를 사용하는 패턴과 동일하다.

---

## 18. 글로벌 동시 실행 제한 (Global Concurrency Semaphore)

### 18.1 문제

`max_concurrent_runbooks=3` 설정이 `RunbookSettings`에 정의되어 있다(`settings/runbook.py:67-72`):
```python
max_concurrent_runbooks: int = Field(
    default=3, ge=1, le=20,
    description="동시 실행 가능한 런북 수 제한",
)
```

그러나 이 설정을 강제하는 구현이 없다.
`DistributedRecoveryLock`은 네임스페이스 단위 상호 배제만 제공하며(`distributed_recovery_lock.py:162`),
서로 다른 네임스페이스에서 동시에 10개의 런북이 실행되는 것을 막지 못한다.

### 18.2 설계

`DistributedRecoveryLock`의 Redis Lua 스크립트 패턴을 참고하여,
**Redis INCR/DECR 기반 글로벌 세마포어**를 구현한다.

```python
# RunbookService._execute_pipeline() 진입부에 추가

SEMAPHORE_KEY = "selfhealing:runbook:global_semaphore"
SEMAPHORE_TTL_SECONDS = 3600  # 1시간 (안전 만료)

def _acquire_global_semaphore(self) -> bool:
    """글로벌 동시 실행 제한 확인.

    Redis INCR 기반 카운터. DistributedRecoveryLock과 동일한 원자적 접근.

    Returns:
        True if 실행 가능, False if max_concurrent_runbooks 초과
    """
    from selfhealing.core.state_backend import get_state_backend

    settings = get_runbook_settings()
    max_concurrent = settings.max_concurrent_runbooks

    backend = get_state_backend()
    redis = backend._client  # RedisStateBackend

    # INCR + TTL (첫 호출 시 키 생성 + TTL 설정)
    current = redis.incr(SEMAPHORE_KEY)
    if current == 1:
        redis.expire(SEMAPHORE_KEY, SEMAPHORE_TTL_SECONDS)

    if current > max_concurrent:
        redis.decr(SEMAPHORE_KEY)
        return False

    return True

def _release_global_semaphore(self) -> None:
    """글로벌 세마포어 해제."""
    from selfhealing.core.state_backend import get_state_backend

    backend = get_state_backend()
    redis = backend._client
    redis.decr(SEMAPHORE_KEY)
```

### 18.3 _execute_pipeline 통합

§14.3의 `_execute_pipeline` 진입부에 세마포어 확인을 추가한다:

```python
def _execute_pipeline(self, runbook, trigger_event, namespace):
    # 0. 글로벌 동시 실행 제한 확인
    if not self._acquire_global_semaphore():
        logger.warning(
            "runbook_service.global_concurrency_limit_reached",
            runbook_id=runbook.id,
            namespace=namespace,
        )
        # DLQ에 저장하여 이후 재시도 가능하게 함
        from selfhealing.services.dlq import store_to_dlq
        store_to_dlq(
            domain="runbook",
            failure_type="concurrency_limit",
            entity_type="runbook_execution",
            entity_id=runbook.id,
            error_message=f"Global concurrency limit reached (max={get_runbook_settings().max_concurrent_runbooks})",
            snapshot_data={"runbook_id": runbook.id, "namespace": namespace},
            recommended_action="retry_after_current_executions_complete",
        )
        ctx = RunbookExecutionContext(...)
        ctx.status = RunbookExecutionStatus.FAILED
        ctx.abort_reason = "Global concurrency limit reached"
        return ctx

    try:
        # ... 기존 승인 → 실행 → 기록 로직 (§14.3) ...
    finally:
        # WAITING_APPROVAL로 Suspend된 경우에는 해제하지 않음
        # resume_pipeline()에서 완료/실패 시 해제
        if ctx.status != RunbookExecutionStatus.WAITING_APPROVAL:
            self._release_global_semaphore()
```

`resume_pipeline()`에도 finally에서 세마포어 해제를 추가한다.

### 18.4 설정 연동

기존 `SELFHEALING_RUNBOOK_MAX_CONCURRENT_RUNBOOKS`(`settings/runbook.py:67-72`)를
그대로 사용한다. 추가 설정 불필요.

---

## 19. 승인 완료 메시지 유실 방어

### 19.1 문제

`approve_runbook()`에서 CAS로 APPROVED 전환 후 `_trigger_resume()`의
`apply_async()`가 실패하면, 로그 경고만 남긴다(`approval_gate.py:957-963`):
```python
except Exception as e:
    logger.warning("runbook_approval.trigger_resume_failed", ...)
```

이 경우 런북은 APPROVED 상태이나 실행 재개 Celery 메시지가 유실된다.

### 19.2 설계

§15의 Orphaned Execution Watchdog이 이 문제도 함께 해결한다.
별도의 Transactional Outbox를 도입하지 않는다.

이유: 프로젝트의 기존 유실 방어 패턴이 "DLQ + 주기적 스캔"이지
Transactional Outbox가 아니다. 일관성을 유지한다.

Watchdog의 스캔 대상 2번(`§15.3`)이 이 시나리오를 커버한다:
```python
elif ctx.status == RunbookExecutionStatus.WAITING_APPROVAL:
    # APPROVED 상태인데 resume이 안 된 건 (메시지 유실)
    is_orphan = _is_approved_but_not_resumed(ctx, now_utc)
```

`_is_approved_but_not_resumed()` 구현:
```python
def _is_approved_but_not_resumed(
    ctx: RunbookExecutionContext,
    now_utc: datetime,
) -> bool:
    """승인 완료되었으나 재개되지 않은 런북 감지.

    ApprovalRequest의 status가 MANUALLY_APPROVED 또는 TIMER_APPROVED인데
    RunbookExecutionContext가 여전히 WAITING_APPROVAL이면 메시지 유실로 판단.
    """
    from selfhealing.services.runbook.approval_gate import get_runbook_approval_gate

    gate = get_runbook_approval_gate()
    request = gate._load_approval_request(ctx.execution_id)
    if request is None:
        return False

    approved_statuses = {
        ApprovalDecisionType.MANUALLY_APPROVED,
        ApprovalDecisionType.TIMER_APPROVED,
    }
    if request.status not in approved_statuses:
        return False

    # 승인 후 APPROVED_STALE_THRESHOLD(2분) 이상 경과
    if request.decided_at:
        decided = datetime.fromisoformat(request.decided_at)
        if decided.tzinfo is None:
            decided = decided.replace(tzinfo=timezone.utc)
        return (now_utc - decided).total_seconds() > APPROVED_STALE_THRESHOLD_SECONDS

    return False
```

---

## 20. EventBus 중복 구독 방어

### 20.1 문제

`SelfHealingEventBus`가 In-Memory 기반일 때는 프로세스 내에서만 이벤트가 전달되므로 문제없다.
그러나 `RedisEventBus`(`services/event_bus/redis_bus.py`)를 사용하면 Redis Pub/Sub 기반이므로,
Gunicorn 워커 N개 + Celery 워커 M개가 모두 구독하여 같은 이벤트를 N+M번 중복 처리한다.

### 20.2 선택: 네임스페이스 락 기반 중복 방어

**Consumer Group 도입 비채택 사유**: 현재 EventBus가 Redis Pub/Sub 기반이며,
Redis Streams 전환은 EventBus 전체 리팩토링이 필요하다. 278 범위를 초과한다.

**네임스페이스 락 선택 사유**: `_execute_pipeline` 진입 시
`DistributedRecoveryLock.acquire()`(`distributed_recovery_lock.py:166-219`)가
namespace 단위 상호 배제를 보장한다. 동일 이벤트로 N개 워커가 동시에
`_execute_pipeline`에 진입해도, **하나만 락을 획득**하고 나머지는
`RunbookLockConflictError`로 자연 탈락한다.

### 20.3 설계

추가 구현 없이 기존 아키텍처가 자연스럽게 방어한다:

```
Worker A: handle_event() → _execute_pipeline() → lock.acquire(ns) → 성공 → 실행
Worker B: handle_event() → _execute_pipeline() → lock.acquire(ns) → 실패 → RunbookLockConflictError
Worker C: handle_event() → _execute_pipeline() → lock.acquire(ns) → 실패 → RunbookLockConflictError
```

단, `_execute_pipeline`에서 `RunbookLockConflictError`를 **조용히 처리**해야 한다
(중복 실행은 정상 시나리오이므로 ERROR가 아닌 DEBUG 로그):

```python
def _execute_pipeline(self, runbook, trigger_event, namespace):
    # ... 세마포어 체크 ...
    try:
        # ... 승인 + 실행 ...
    except RunbookLockConflictError:
        # RedisEventBus 환경에서 다수 워커 중복 수신 시 정상 동작
        logger.debug(
            "runbook_service.duplicate_event_filtered_by_lock",
            runbook_id=runbook.id,
            namespace=namespace,
        )
        return RunbookExecutionContext(
            execution_id="",
            runbook_id=runbook.id,
            namespace=namespace,
            trigger_event=trigger_event,
            status=RunbookExecutionStatus.CANCELLED,
            abort_reason="Duplicate event — another worker acquired the lock",
        )
```

### 20.4 register_subscriptions 환경 분리 (선택적 최적화)

불필요한 PatternMatcher 연산을 줄이기 위해, 웹 워커에서는 구독하지 않는 옵션:

```python
def register_subscriptions(self) -> None:
    # Celery Worker 환경에서만 구독 (Gunicorn 웹 워커는 구독하지 않음)
    import os
    if os.environ.get("SELFHEALING_RUNBOOK_SUBSCRIBE_EVENTS", "true").lower() != "true":
        logger.info("runbook_service.event_subscription_disabled_by_env")
        return

    # ... 기존 구독 로직 ...
```

§10에 설정 추가:
```python
SELFHEALING_RUNBOOK_SUBSCRIBE_EVENTS: bool = True
# EventBus 구독 여부. Celery Worker에서만 True, 웹 서버에서는 False 권장
```
