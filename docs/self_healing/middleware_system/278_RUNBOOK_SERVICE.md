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
