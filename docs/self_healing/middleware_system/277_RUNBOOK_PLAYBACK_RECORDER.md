# 277. Runbook Playback Recorder 설계

> **Status**: Design
> **References**:
> - [275_RUNBOOK_EXECUTOR.md](275_RUNBOOK_EXECUTOR.md) — RunbookExecutionContext, RunbookStepResult, CompensationSummary
> - `audit/cascade_auditor/_recording.py` — RecordingMixin.record() (CascadeEvent 인과 기록)
> - `services/learning/service.py` — LearningService.learn_pattern() (패턴 학습 피드백)
> - `services/postmortem/store.py` — add_healing_incident() (인시던트 영구 저장)
> - `services/event_bus/bus/__init__.py` — EventType, SelfHealingEventBus.emit()

---

## 1. 목적

275의 RunbookExecutor가 실행을 완료(성공/실패)한 후, 그 **결과를 기록**하는 모듈을 설계한다.
기존 코드베이스의 세 가지 기록 채널을 통합 활용한다:

| 채널 | 출처 | 기록 대상 |
|------|------|-----------|
| **CascadeEventAuditor** | `audit/cascade_auditor/_recording.py` | 인과 관계 추적 (누가→무엇→결과) |
| **LearningService** | `services/learning/service.py` | 패턴 학습 피드백 (성공/실패→재사용) |
| **PostmortemStore** | `services/postmortem/store.py` | 인시던트 기록 (실패 시 자동 Postmortem) |

EventBus를 통해 RUNBOOK_COMPLETED / RUNBOOK_FAILED 이벤트를 발행하여,
다른 컴포넌트(알림, 대시보드 등)가 구독할 수 있도록 한다.

---

## 2. 기존 코드 근거

### 2.1 CascadeEventAuditor.record() (`audit/cascade_auditor/_recording.py`)

```python
class RecordingMixin:
    def record(
        self,
        trigger_type: str,           # 예: "EMERGENCY_LEVEL_CHANGED"
        trigger_details: dict[str, Any],
        effects: list[dict[str, Any]],  # 연쇄 효과 목록
        namespace: str,
        triggered_by: str | None = None,
        external_trace: ExternalTraceContext | None = None,
    ) -> CascadeEvent:
        """
        1. cascade_id, event_id 생성
        2. CascadeTrigger 생성
        3. CascadeEffect 목록 생성 (각 action_type, success, target, details)
        4. previous_hash 조회 → 해시 체인 연결
        5. 저장 (Redis, 실패 시 로컬 폴백)
        """
```

- `CascadeEffect(event_id, action_type, caused_by, success, target, details, error_message)`
- 각 effect의 `caused_by`가 명시되지 않으면 이전 event_id 사용 → **인과 체인 자동 구성**
- Runbook의 각 Step 결과를 `effects` 리스트로 매핑하면 Step 간 인과 관계가 자동 기록됨

### 2.2 LearningService.learn_pattern() (`services/learning/service.py`)

```python
class LearningService:
    def learn_pattern(
        self,
        pattern_type: PatternType,  # FAILURE / RECOVERY / PERFORMANCE / ANOMALY / OPTIMIZATION
        name: str,
        description: str,
        features: dict[str, Any],
        confidence: float = 0.8,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> LearningPattern:
        """
        기존 패턴이면 occurrence_count += 1, confidence 평균 갱신.
        새 패턴이면 신규 생성.
        occurrence_count >= 3 && confidence >= 0.8이면 Suggestion 자동 생성.
        """
```

- `PatternType.RECOVERY`: 성공한 Runbook 실행 패턴 기록에 적합
- `PatternType.FAILURE`: 실패한 Runbook 실행 패턴 기록에 적합
- 기존 패턴 이름과 매칭되면 `occurrence_count` 증가 → 반복 패턴 자동 인식
- `features` dict에 Runbook 실행 컨텍스트(namespace, step 수, 실행 시간 등)를 저장

### 2.3 add_healing_incident() (`services/postmortem/store.py`)

```python
def add_healing_incident(incident: dict[str, Any]) -> None:
    """
    힐링 인시던트 기록.
    PostgreSQL에 영구 저장 시도, 실패 시 In-Memory fallback.
    """
    incident["recorded_at"] = _get_current_timestamp()
    # PostgreSQL 저장 → 실패 시 In-Memory
```

- incident dict에 `incident_type`, `service_name`, `action_taken`, `resolved`, `details` 등 포함
- Runbook 실패 시 자동 postmortem incident 생성에 활용

### 2.4 EventType / EventBus (`services/event_bus/bus/__init__.py`)

```python
class EventType(str, Enum):
    EMERGENCY_RECOVERY_COMPLETED = "emergency_recovery_completed"  # 기존
    # 신규 추가 예정:
    # RUNBOOK_EXECUTION_COMPLETED = "runbook_execution_completed"
    # RUNBOOK_EXECUTION_FAILED = "runbook_execution_failed"
```

- `EMERGENCY_RECOVERY_COMPLETED` 이벤트 발행 시 `_on_emergency_recovery_completed_postmortem` 핸들러가
  자동으로 postmortem을 생성하는 패턴이 이미 존재
- Runbook도 동일 패턴으로 `RUNBOOK_EXECUTION_COMPLETED/FAILED` 이벤트를 발행하면
  기존 구독 인프라가 자동 활용됨

---

## 3. RunbookPlaybackRecorder 클래스

```python
class RunbookPlaybackRecorder:
    """
    Runbook 실행 결과를 3개 채널에 기록하는 Recorder.

    채널:
    1. CascadeEventAuditor — 인과 관계 감사 추적
    2. LearningService — 성공/실패 패턴 학습 피드백
    3. PostmortemStore — 실패 시 자동 인시던트 생성

    + EventBus 이벤트 발행
    """

    def __init__(
        self,
        cascade_auditor: CascadeEventAuditor | None = None,
        learning_service: LearningService | None = None,
        event_bus: SelfHealingEventBus | None = None,
    ):
        self._cascade_auditor = cascade_auditor
        self._learning_service = learning_service
        self._event_bus = event_bus
```

---

## 4. 기록 흐름

### 4.1 전체 시퀀스

```
RunbookExecutor.execute_runbook() 완료
│
└─ RunbookPlaybackRecorder.record(ctx, runbook, compensation)
   │
   ├─ 1. Cascade Event 기록
   │    CascadeEventAuditor.record(
   │        trigger_type="RUNBOOK_EXECUTION",
   │        trigger_details={runbook_id, namespace, execution_id, ...},
   │        effects=[Step별 CascadeEffect],
   │        namespace=ctx.namespace,
   │    )
   │
   ├─ 2. Learning Service 피드백
   │    ├─ 성공: learn_pattern(RECOVERY, ...)
   │    └─ 실패: learn_pattern(FAILURE, ...)
   │
   ├─ 3. Postmortem 생성 (실패 시)
   │    add_healing_incident({...})
   │
   └─ 4. EventBus 이벤트 발행
        ├─ 성공: emit(RUNBOOK_EXECUTION_COMPLETED, {...})
        └─ 실패: emit(RUNBOOK_EXECUTION_FAILED, {...})
```

### 4.2 record() 메서드

```python
def record(
    self,
    ctx: RunbookExecutionContext,
    runbook: Runbook,
    compensation: CompensationSummary | None = None,
) -> RecordingSummary:
    """
    Runbook 실행 결과를 모든 채널에 기록.

    각 채널은 독립적으로 Fail-Open 처리 — 한 채널 실패가 다른 채널을 차단하지 않음.
    """
    summary = RecordingSummary(execution_id=ctx.execution_id)

    # 1. Cascade Event (인과 추적)
    summary.cascade_recorded = self._record_cascade_event(ctx, runbook)

    # 2. Learning Feedback (패턴 학습)
    summary.pattern_recorded = self._record_learning_feedback(ctx, runbook)

    # 3. Postmortem (실패 시 인시던트)
    if ctx.status == RunbookExecutionStatus.FAILED:
        summary.postmortem_recorded = self._record_postmortem(
            ctx, runbook, compensation,
        )

    # 4. EventBus (이벤트 발행)
    summary.event_emitted = self._emit_event(ctx, runbook)

    return summary
```

---

## 5. 채널 1: Cascade Event 기록

### 5.1 CascadeEventAuditor.record() 호출

```python
def _record_cascade_event(
    self,
    ctx: RunbookExecutionContext,
    runbook: Runbook,
) -> bool:
    """
    CascadeEventAuditor.record() 호출.

    Runbook의 각 Step 결과를 CascadeEffect로 변환하여
    인과 체인을 구성한다.

    effects 리스트의 caused_by가 명시되지 않으면
    이전 event_id가 자동 사용되므로 (RecordingMixin._create_effects 로직),
    Step 실행 순서가 자동으로 인과 체인이 된다.
    """
    if self._cascade_auditor is None:
        return False

    try:
        # trigger_details: 실행 컨텍스트 정보
        trigger_details = {
            "execution_id": ctx.execution_id,
            "runbook_id": ctx.runbook_id,
            "status": ctx.status.value,
            "started_at": ctx.started_at,
            "completed_at": ctx.completed_at,
            "trigger_event": ctx.trigger_event,
        }

        # effects: Step별 결과를 CascadeEffect 형식으로 변환
        effects = []
        for step_name, step_result in ctx.step_results.items():
            effects.append({
                "action_type": f"RUNBOOK_STEP:{step_result.action_name}",
                "success": step_result.success,
                "target": step_name,
                "details": {
                    "executed": step_result.executed,
                    "idempotent": step_result.idempotent,
                    "result_data": step_result.result_data,
                    "compensation_status": step_result.compensation_status,
                },
                "error_message": step_result.error,
            })

        self._cascade_auditor.record(
            trigger_type="RUNBOOK_EXECUTION",
            trigger_details=trigger_details,
            effects=effects,
            namespace=ctx.namespace,
            triggered_by="system:runbook_executor",
        )
        return True

    except Exception as e:
        # Fail-Open
        logger.warning("runbook_recorder.cascade_failed", error=e)
        return False
```

### 5.2 생성되는 CascadeEvent 구조

```
CascadeEvent:
  id: "cascade-abc123"
  trigger:
    trigger_type: "RUNBOOK_EXECUTION"
    event_id: "evt-001"
    details: {execution_id, runbook_id, status, ...}
    triggered_by: "system:runbook_executor"
  effects:
    - event_id: "evt-002"
      action_type: "RUNBOOK_STEP:enable_circuit_breaker"
      caused_by: "evt-001"      ← trigger → 첫 step
      success: true
    - event_id: "evt-003"
      action_type: "RUNBOOK_STEP:adjust_rate_limit"
      caused_by: "evt-002"      ← 첫 step → 둘째 step (자동)
      success: true
    - event_id: "evt-004"
      action_type: "RUNBOOK_STEP:send_notification"
      caused_by: "evt-003"      ← 둘째 step → 셋째 step (자동)
      success: false
      error_message: "Notification service timeout"
  namespace: "global"
  previous_hash: "aef3..."      ← 이전 CascadeEvent와 해시 체인 연결
  current_hash: "c7b1..."
```

---

## 6. 채널 2: Learning Service 피드백

### 6.1 성공/실패 패턴 학습

```python
def _record_learning_feedback(
    self,
    ctx: RunbookExecutionContext,
    runbook: Runbook,
) -> bool:
    """
    LearningService.learn_pattern() 호출.

    성공 → PatternType.RECOVERY (복구 패턴)
    실패 → PatternType.FAILURE (장애 패턴)

    기존 로직:
    - 동일 name의 패턴이 있으면 occurrence_count += 1
    - occurrence_count >= 3 && confidence >= 0.8이면 Suggestion 자동 생성
    """
    if self._learning_service is None:
        return False

    try:
        is_success = ctx.status == RunbookExecutionStatus.COMPLETED

        pattern_type = PatternType.RECOVERY if is_success else PatternType.FAILURE
        pattern_name = f"runbook:{runbook.id}:{'success' if is_success else 'failure'}"

        # 실행 시간 계산
        duration_seconds = None
        if ctx.started_at and ctx.completed_at:
            from datetime import datetime
            start = datetime.fromisoformat(ctx.started_at)
            end = datetime.fromisoformat(ctx.completed_at)
            duration_seconds = (end - start).total_seconds()

        # features: 실행 특성 (step 수, 실행 시간, 실패 지점 등)
        features = {
            "runbook_id": runbook.id,
            "namespace": ctx.namespace,
            "step_count": len(runbook.steps),
            "executed_step_count": len(ctx.step_results),
            "duration_seconds": duration_seconds,
            "risk_level": runbook.risk_level.value,
        }

        if not is_success:
            # 실패 지점 정보 추가
            failed_steps = [
                name for name, r in ctx.step_results.items()
                if not r.success
            ]
            features["failed_steps"] = failed_steps
            features["abort_reason"] = ctx.abort_reason

        confidence = 0.9 if is_success else 0.85

        self._learning_service.learn_pattern(
            pattern_type=pattern_type,
            name=pattern_name,
            description=(
                f"Runbook '{runbook.name}' "
                f"{'succeeded' if is_success else 'failed'} "
                f"in namespace '{ctx.namespace}'"
            ),
            features=features,
            confidence=confidence,
            metadata={
                "execution_id": ctx.execution_id,
                "runbook_version": runbook.version,
            },
        )
        return True

    except Exception as e:
        # Fail-Open
        logger.warning("runbook_recorder.learning_failed", error=e)
        return False
```

### 6.2 학습 피드백 활용

LearningService 내부의 기존 로직이 자동으로 동작한다:

```
learn_pattern(RECOVERY, "runbook:circuit_breaker_recovery:success", ...)
  → occurrence_count += 1
  → occurrence_count >= 3 && confidence >= 0.8
  → _generate_failure_suggestion() 또는 _generate_performance_suggestion()
  → Suggestion 생성 (action="enable_circuit_breaker", parameters={...})
```

이 Suggestion은 273 PatternMatcher가 참조할 수 있는 학습 데이터가 된다.

---

## 7. 채널 3: Postmortem 생성 (실패 시)

### 7.1 add_healing_incident() 호출

```python
def _record_postmortem(
    self,
    ctx: RunbookExecutionContext,
    runbook: Runbook,
    compensation: CompensationSummary | None,
) -> bool:
    """
    실패 시 자동 postmortem incident 생성.

    add_healing_incident() 패턴:
    - PostgreSQL 저장 시도 → 실패 시 In-Memory fallback
    - recorded_at 자동 부여

    EMERGENCY_RECOVERY_COMPLETED → postmortem 자동 생성 패턴 참조.
    """
    try:
        from selfhealing.services.postmortem.store import add_healing_incident

        # 실패한 Step 정보
        failed_steps = [
            {"step_name": name, "error": r.error, "action": r.action_name}
            for name, r in ctx.step_results.items()
            if not r.success
        ]

        # 보상 결과
        compensation_info = None
        if compensation:
            compensation_info = {
                "compensated": compensation.compensated,
                "failed": compensation.failed,
                "skipped": compensation.skipped,
                "all_compensated": compensation.all_compensated,
            }

        incident = {
            "incident_type": "runbook_execution_failed",
            "service_name": ctx.namespace,
            "action_taken": f"runbook:{runbook.id}",
            "resolved": False,
            "details": {
                "execution_id": ctx.execution_id,
                "runbook_id": runbook.id,
                "runbook_name": runbook.name,
                "risk_level": runbook.risk_level.value,
                "namespace": ctx.namespace,
                "trigger_event": ctx.trigger_event,
                "failed_steps": failed_steps,
                "compensation": compensation_info,
                "abort_reason": ctx.abort_reason,
                "started_at": ctx.started_at,
                "completed_at": ctx.completed_at,
            },
        }

        add_healing_incident(incident)
        return True

    except Exception as e:
        # Fail-Open
        logger.warning("runbook_recorder.postmortem_failed", error=e)
        return False
```

---

## 8. EventBus 이벤트 발행

### 8.1 신규 EventType 추가

```python
class EventType(str, Enum):
    # ... 기존 이벤트 ...
    EMERGENCY_RECOVERY_COMPLETED = "emergency_recovery_completed"

    # 신규 추가
    RUNBOOK_EXECUTION_COMPLETED = "runbook_execution_completed"
    RUNBOOK_EXECUTION_FAILED = "runbook_execution_failed"
```

### 8.2 이벤트 발행 (`_emit_event`)

```python
def _emit_event(
    self,
    ctx: RunbookExecutionContext,
    runbook: Runbook,
) -> bool:
    """
    EventBus에 실행 결과 이벤트 발행.

    EMERGENCY_RECOVERY_COMPLETED 발행 시
    _on_emergency_recovery_completed_postmortem 핸들러가
    자동 호출되는 기존 패턴을 따른다.
    """
    if self._event_bus is None:
        return False

    try:
        is_success = ctx.status == RunbookExecutionStatus.COMPLETED

        event_type = (
            EventType.RUNBOOK_EXECUTION_COMPLETED
            if is_success
            else EventType.RUNBOOK_EXECUTION_FAILED
        )

        event_data = {
            "execution_id": ctx.execution_id,
            "runbook_id": ctx.runbook_id,
            "runbook_name": runbook.name,
            "namespace": ctx.namespace,
            "status": ctx.status.value,
            "risk_level": runbook.risk_level.value,
            "step_count": len(runbook.steps),
            "executed_step_count": len(ctx.step_results),
            "started_at": ctx.started_at,
            "completed_at": ctx.completed_at,
        }

        if not is_success:
            event_data["abort_reason"] = ctx.abort_reason
            event_data["failed_steps"] = [
                name for name, r in ctx.step_results.items()
                if not r.success
            ]

        self._event_bus.emit(event_type, event_data)
        return True

    except Exception as e:
        # Fail-Open
        logger.warning("runbook_recorder.event_emit_failed", error=e)
        return False
```

---

## 9. RecordingSummary

```python
@dataclass
class RecordingSummary:
    """기록 결과 요약."""
    execution_id: str
    cascade_recorded: bool = False
    pattern_recorded: bool = False
    postmortem_recorded: bool = False
    event_emitted: bool = False

    @property
    def all_recorded(self) -> bool:
        return self.cascade_recorded and self.pattern_recorded and self.event_emitted

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "cascade_recorded": self.cascade_recorded,
            "pattern_recorded": self.pattern_recorded,
            "postmortem_recorded": self.postmortem_recorded,
            "event_emitted": self.event_emitted,
            "all_recorded": self.all_recorded,
        }
```

---

## 10. RunbookExecutor 통합 (275 연동)

275의 `execute_runbook()` 완료 후 recorder 호출:

```python
# RunbookExecutor.execute_runbook() 내부, 마지막 단계

def execute_runbook(self, runbook, trigger_event, namespace):
    # ... Lock 획득, 승인 게이트 (276), Step 실행 (275) ...

    try:
        ctx = self._run(runbook, trigger_event, namespace, execution_id)
    except Exception as e:
        ctx.status = RunbookExecutionStatus.FAILED
        ctx.abort_reason = str(e)
        compensation = self._compensate_steps(ctx)
    finally:
        self._recovery_lock.release(namespace, execution_id)

    # === Playback Recorder (277) ===
    recorder = RunbookPlaybackRecorder(
        cascade_auditor=self._cascade_auditor,
        learning_service=self._learning_service,
        event_bus=self._event_bus,
    )
    recording = recorder.record(ctx, runbook, compensation)

    logger.info(
        "runbook_executor.recorded",
        execution_id=ctx.execution_id,
        cascade=recording.cascade_recorded,
        learning=recording.pattern_recorded,
        postmortem=recording.postmortem_recorded,
        event=recording.event_emitted,
    )

    return ctx
```

---

## 11. 모듈 구조

```
packages/selfhealing-python/src/selfhealing/
└── services/
    └── runbook/
        ├── recorder.py          ← RunbookPlaybackRecorder (이 문서)
        ├── models.py            ← RecordingSummary 추가
        └── ...
```

---

## 12. Fail-Open 원칙

RecordingMixin, PostmortemStore, LearningService 모두 이미 Fail-Open으로 동작한다:

- `CascadeEventAuditor.record()`: Redis 실패 시 `_save_to_local_fallback()` 로컬 저장
- `add_healing_incident()`: PostgreSQL 실패 시 In-Memory fallback
- `LearningService.learn_pattern()`: 메모리 내 `_patterns` dict에 저장 (항상 성공)

RunbookPlaybackRecorder는 각 채널을 독립적 try/except로 감싸서,
**한 채널 실패가 다른 채널 기록을 차단하지 않도록** 한다.

---

## 13. 기존 컴포넌트와의 관계 요약

| 기존 컴포넌트 | Playback Recorder에서의 사용 |
|---|---|
| `CascadeEventAuditor.record()` | Step별 인과 관계 감사 기록 (trigger→effects 체인) |
| `CascadeEffect` | 각 Step 결과를 action_type/success/caused_by로 매핑 |
| `LearningService.learn_pattern()` | 성공→RECOVERY, 실패→FAILURE 패턴 학습 |
| `PatternType` | RECOVERY / FAILURE enum 값 사용 |
| `add_healing_incident()` | 실패 시 자동 postmortem 인시던트 생성 |
| `EventType` | RUNBOOK_EXECUTION_COMPLETED/FAILED 신규 추가 |
| `SelfHealingEventBus.emit()` | 실행 결과 이벤트 발행 |
| `EMERGENCY_RECOVERY_COMPLETED` | 동일한 이벤트→postmortem 패턴의 선례 |
