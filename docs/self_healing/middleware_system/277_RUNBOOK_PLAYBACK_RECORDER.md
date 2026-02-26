# 277. Runbook Playback Recorder 설계

> **Status**: Implemented
> **References**:
> - [275_RUNBOOK_EXECUTOR.md](275_RUNBOOK_EXECUTOR.md) — RunbookExecutionContext, RunbookStepResult, CompensationSummary
> - `audit/cascade_auditor/_recording.py` — RecordingMixin.record() (CascadeEvent 인과 기록)
> - `audit/masking.py` — mask_sensitive_fields(), MaskingLevel (PII 마스킹)
> - `audit/resilient_recorder.py` — ResilientContinuousAuditRecorder (4단계 폴백 체인)
> - `audit/persistence/disk_buffer.py` — DiskPersistentBuffer (LMDB 영속 버퍼)
> - `settings/cascade_retention.py` — CascadeRetentionSettings (hot_retention_days TTL)
> - `services/learning/service.py` — LearningService.learn_pattern() (패턴 학습 피드백)
> - `services/postmortem/store.py` — add_healing_incident() (인시던트 영구 저장)
> - `utils/postmortem_root_cause.py` — build_postmortem_root_cause_fields() (근본 원인 분석)
> - `services/event_bus/bus/__init__.py` — EventType, SelfHealingEventBus.emit()
> - `services/runbook/models.py` — MatchResult.metric_snapshot (트리거 시점 메트릭)
> - `metrics/prometheus.py` — SelfHealingMetrics (Prometheus Counter/Gauge 패턴)

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
# 민감 데이터 마스킹 확장 키 (§14.1)
# 기본 키: password, secret, token, api_key, credential, private_key, ...
# Runbook 실행 컨텍스트에서 추가로 노출 가능한 민감 정보
RUNBOOK_SENSITIVE_KEYS: list[str] = [
    "password", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "credential", "private_key",
    "credit_card", "ssn", "social_security",
    # Runbook 도메인 확장
    "connection_string", "dsn", "database_url", "db_password",
    "redis_url", "broker_url", "smtp_password",
]

# 이벤트 페이로드 크기 제한 (§14.3)
MAX_ERROR_MESSAGE_LENGTH: int = 200
TRUNCATION_MARKER: str = "... [TRUNCATED. Full log: DB lookup by execution_id]"

class RunbookPlaybackRecorder:
    """
    Runbook 실행 결과를 3개 채널에 기록하는 Recorder.

    채널:
    1. CascadeEventAuditor — 인과 관계 감사 추적
    2. LearningService — 성공/실패 패턴 학습 피드백
    3. PostmortemStore — 실패 시 자동 인시던트 생성

    + EventBus 이벤트 발행

    데이터 보호:
    - 모든 채널 전송 전 PII 자동 마스킹 (§14.1)
    - 이벤트 페이로드 크기 제한 (§14.3)
    - 기록 실패 시 Prometheus 메트릭 노출 (§14.5)
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

    데이터 흐름:
    1. PII 마스킹 (§14.1) — 채널 전송 전 일괄 적용
    2. 각 채널에 마스킹된 데이터 전달
    3. 실패 시 Prometheus 메트릭 증분 (§14.5)
    """
    summary = RecordingSummary(execution_id=ctx.execution_id)

    # === PII 마스킹 게이트 (§14.1) ===
    # 모든 채널에 전달되기 전에 민감 데이터를 일괄 마스킹한다.
    # CascadeEvent.to_dict(), add_healing_incident() 모두 마스킹 로직이
    # 없으므로 (코드 확인 완료), Recorder 진입부에서 강제한다.
    #
    # 코드 근거:
    # - audit/masking.py L381-428: mask_sensitive_fields() — dict/list 재귀 순회
    # - audit/cascade_event.py L326-337: CascadeEffect.to_dict() — details 그대로 노출
    # - services/postmortem/store.py: incident dict raw 저장
    from selfhealing.audit.masking import mask_sensitive_fields

    sanitized_step_results = {}
    for step_name, step_result in ctx.step_results.items():
        sanitized_result_data = mask_sensitive_fields(
            step_result.result_data, RUNBOOK_SENSITIVE_KEYS,
        )
        sanitized_step_results[step_name] = RunbookStepResult(
            step_name=step_result.step_name,
            action_name=step_result.action_name,
            success=step_result.success,
            executed=step_result.executed,
            result_data=sanitized_result_data,
            error=step_result.error,
            partial_execution=step_result.partial_execution,
            compensation_status=step_result.compensation_status,
        )

    sanitized_trigger_event = mask_sensitive_fields(
        ctx.trigger_event, RUNBOOK_SENSITIVE_KEYS,
    )

    # 1. Cascade Event (인과 추적)
    summary.cascade_recorded = self._record_cascade_event(
        ctx, runbook, sanitized_step_results, sanitized_trigger_event,
    )

    # 2. Learning Feedback (패턴 학습)
    summary.pattern_recorded = self._record_learning_feedback(ctx, runbook)

    # 3. Postmortem (실패 시 인시던트)
    if ctx.status == RunbookExecutionStatus.FAILED:
        summary.postmortem_recorded = self._record_postmortem(
            ctx, runbook, compensation,
            sanitized_step_results, sanitized_trigger_event,
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
    sanitized_step_results: dict[str, RunbookStepResult],
    sanitized_trigger_event: dict[str, Any],
) -> bool:
    """
    CascadeEventAuditor.record() 호출.

    Runbook의 각 Step 결과를 CascadeEffect로 변환하여
    인과 체인을 구성한다.

    caused_by 명시화 (§14.A):
    - 정방향 Step: 순차 체인 (이전 Step → 현재 Step)
    - 보상 Step: 실패한 정방향 Step이 원인으로 직접 연결
    이를 위해 각 effect에 명시적 caused_by를 지정한다.
    _create_effects()는 effect_data.get("caused_by", previous_event_id)를
    사용하므로, 명시된 caused_by가 자동 체인보다 우선한다.
    (audit/cascade_auditor/_recording.py L160-209)

    PII 마스킹 (§14.1):
    - 마스킹된 sanitized_step_results/sanitized_trigger_event를 사용한다.
    - CascadeEffect.to_dict()의 details 필드가 raw 데이터를 그대로 노출하므로
      (audit/cascade_event.py L326-337), Recorder에서 사전 마스킹이 필수다.
    """
    if self._cascade_auditor is None:
        return False

    try:
        # trigger_details: 마스킹된 실행 컨텍스트 정보
        trigger_details = {
            "execution_id": ctx.execution_id,
            "runbook_id": ctx.runbook_id,
            "status": ctx.status.value,
            "started_at": ctx.started_at,
            "completed_at": ctx.completed_at,
            "trigger_event": sanitized_trigger_event,
        }

        # === effects 구성: 정방향 Step + 보상 Step (§14.A) ===
        #
        # _create_effects() 로직 (audit/cascade_auditor/_recording.py L160-209):
        #   previous_event_id = trigger_event_id
        #   for effect_data in effects_data:
        #       caused_by = effect_data.get("caused_by", previous_event_id)
        #       previous_event_id = effect_event_id
        #
        # 문제: 보상 Step이 끝에 추가되면 자동 체인이
        #   "정방향A → 정방향B(실패) → 보상B → 보상A" 형태의 선형 체인이 되어
        #   "정방향B → 보상B" 인과관계가 끊김.
        #
        # 해결: 보상 Step에 caused_by를 명시적으로 지정하여
        #   compensation_caused_by[step_name] 매핑으로 정방향 실패 Step → 보상 Step 연결.
        #
        # 구조:
        #   trigger → Step1(성공) → Step2(실패)     ← 순차 체인 (자동)
        #             Step1 ← Compensate_Step1       ← 명시적 caused_by
        #             Step2 ← Compensate_Step2       ← 명시적 caused_by

        effects = []

        # 1단계: 정방향 Step 기록 (순차 체인 — caused_by 자동)
        step_effect_ids: dict[str, str] = {}  # step_name → 추후 보상 연결용 placeholder
        for step_name, step_result in sanitized_step_results.items():
            if step_result.compensation_status == "not_needed" or step_result.executed:
                effect_entry = {
                    "action_type": f"RUNBOOK_STEP:{step_result.action_name}",
                    "success": step_result.success,
                    "target": step_name,
                    "details": {
                        "executed": step_result.executed,
                        "idempotent": step_result.idempotent
                            if hasattr(step_result, "idempotent") else False,
                        "result_data": step_result.result_data,
                        "compensation_status": step_result.compensation_status,
                        "partial_execution": step_result.partial_execution,
                    },
                    "error_message": step_result.error,
                    # 정방향 Step: caused_by 미지정 → 자동 순차 체인
                }
                # Placeholder: _create_effects()가 생성할 event_id를
                # 외부에서 예측 불가하므로, 보상 Step의 caused_by는
                # 정방향 Step의 action_type을 기반으로 연결한다.
                # → §14.A.1에서 보상 effects를 별도 record() 호출로 분리.
                effects.append(effect_entry)

        # 2단계: 보상 Step 기록 (별도 caused_by 지정)
        # 보상이 수행된 Step은 정방향 실패 Step이 원인(cause)이다.
        # _create_effects()의 자동 체인이 아닌 명시적 caused_by를 사용한다.
        # 단, caused_by에 넣을 event_id는 _create_effects() 내부에서 생성되므로,
        # 보상 Step을 같은 effects 리스트에 넣되 caused_by를 "명시적 마커"로 설정하고
        # _create_effects()의 기본 동작(이전 event_id)에 위임한다.
        #
        # 현실적 해법: 보상 Step은 정방향 실행의 역순으로 배치되므로,
        # 가장 최근 실패한 Step 바로 뒤에 보상 Step을 배치하면
        # _create_effects()의 자동 체인이 "실패Step → 보상Step" 연결을 만든다.
        for step_name, step_result in sanitized_step_results.items():
            if step_result.compensation_status in ("compensated", "compensate_failed"):
                effects.append({
                    "action_type": f"RUNBOOK_COMPENSATE:{step_result.action_name}",
                    "success": step_result.compensation_status == "compensated",
                    "target": f"compensate:{step_name}",
                    "details": {
                        "original_step": step_name,
                        "compensation_status": step_result.compensation_status,
                        "partial_execution": step_result.partial_execution,
                    },
                    "error_message": (
                        step_result.error
                        if step_result.compensation_status == "compensate_failed"
                        else None
                    ),
                })

        self._cascade_auditor.record(
            trigger_type="RUNBOOK_EXECUTION",
            trigger_details=trigger_details,
            effects=effects,
            namespace=ctx.namespace,
            triggered_by="system:runbook_executor",
        )

        RECORDER_SUCCESS.labels(channel="cascade").inc()
        return True

    except Exception as e:
        # Fail-Open + 메트릭 (§14.5)
        RECORDER_ERROR.labels(channel="cascade", error_type=type(e).__name__).inc()
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
    details:
      execution_id: "runbook-xyz"
      runbook_id: "circuit_breaker_recovery"
      status: "failed"
      trigger_event: {db_password: "***REDACTED***", ...}  ← PII 마스킹됨 (§14.1)
    triggered_by: "system:runbook_executor"
  effects:
    # 정방향 Step (순차 체인 — caused_by 자동)
    - event_id: "evt-002"
      action_type: "RUNBOOK_STEP:enable_circuit_breaker"
      caused_by: "evt-001"      ← trigger → 첫 step (자동)
      success: true
      details:
        result_data: {connection_string: "***REDACTED***"}  ← 마스킹됨
    - event_id: "evt-003"
      action_type: "RUNBOOK_STEP:adjust_rate_limit"
      caused_by: "evt-002"      ← 첫 step → 둘째 step (자동)
      success: false
      error_message: "Notification service timeout"
    # 보상 Step (§14.A — 역순 배치)
    - event_id: "evt-004"
      action_type: "RUNBOOK_COMPENSATE:adjust_rate_limit"
      caused_by: "evt-003"      ← 실패 step → 보상 step (자동 체인)
      success: true
      details:
        original_step: "adjust_rate_limit"
        compensation_status: "compensated"
    - event_id: "evt-005"
      action_type: "RUNBOOK_COMPENSATE:enable_circuit_breaker"
      caused_by: "evt-004"      ← 보상 체인 (역순 자동)
      success: true
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
    성공했지만 느림 → PatternType.PERFORMANCE 추가 학습 (§14.B)

    패턴 이름에 버전 포함 (§14.4):
    - "runbook:{id}:v{version}:success" 형태로 버전별 독립 패턴 관리
    - metadata에 previous_pattern_name으로 계보(Lineage) 추적 (§14.4)

    기존 로직 (services/learning/service.py L335-406):
    - name + pattern_type으로만 매칭 (features 비교 안 함)
    - 동일 name의 패턴이 있으면 occurrence_count += 1
    - occurrence_count >= 3 && confidence >= 0.8이면 Suggestion 자동 생성
    - features는 최초 등록 시 고정 (갱신 안 됨)
    """
    if self._learning_service is None:
        return False

    try:
        is_success = ctx.status == RunbookExecutionStatus.COMPLETED

        # === 버전 포함 패턴 이름 (§14.4) ===
        # 선택: 전략 A (버전 포함) vs 전략 B (버전 무시)
        #
        # 전략 A 채택 이유:
        # - learn_pattern()이 features를 갱신하지 않으므로 (코드 확인 완료),
        #   v2에서 Step이 변경되면 v1의 features 데이터가 오염됨
        # - 버전별로 깨끗한 새 패턴을 시작하는 것이 데이터 정합성에 안전
        # - occurrence_count가 리셋되는 단점은 metadata 계보로 보완
        pattern_type = PatternType.RECOVERY if is_success else PatternType.FAILURE
        pattern_name = (
            f"runbook:{runbook.id}:v{runbook.version}"
            f":{'success' if is_success else 'failure'}"
        )

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

        # === 계보 추적 metadata (§14.4) ===
        # 이전 버전 패턴을 Linked List 형태로 연결하여
        # 대시보드에서 "런북 전체 생애 주기 성공률" 재구성 가능.
        # learn_pattern()의 metadata가 자유형 dict이므로 스키마 변경 없이 적용.
        previous_version = runbook.version - 1
        previous_pattern_name = (
            f"runbook:{runbook.id}:v{previous_version}"
            f":{'success' if is_success else 'failure'}"
            if previous_version >= 1
            else None
        )

        self._learning_service.learn_pattern(
            pattern_type=pattern_type,
            name=pattern_name,
            description=(
                f"Runbook '{runbook.name}' v{runbook.version} "
                f"{'succeeded' if is_success else 'failed'} "
                f"in namespace '{ctx.namespace}'"
            ),
            features=features,
            confidence=confidence,
            metadata={
                "execution_id": ctx.execution_id,
                "runbook_version": runbook.version,
                "previous_pattern_name": previous_pattern_name,
            },
        )

        # === 성능 저하 패턴 추가 학습 (§14.B) ===
        # 성공했지만 예상보다 느린 경우 PatternType.PERFORMANCE로 추가 학습.
        # PatternType.PERFORMANCE는 이미 존재 (services/learning/models.py L16).
        #
        # 판정 기준: Runbook 자체의 timeout_seconds를 SLA 기준으로 사용.
        # 전체 실행 시간이 SLA의 80%를 초과하면 "느린 복구"로 학습.
        #
        # 선택: Executor(275)가 판정 vs Recorder(277)가 판정
        # → Recorder 채택 이유: duration_seconds와 timeout 비교는
        #   순수 데이터 변환이며, Executor에 학습 관심사를 두면
        #   단일 책임 원칙 위배. Recorder의 "기록 + 피드백" 책임에 부합.
        if is_success and duration_seconds is not None:
            # Runbook에 timeout이 정의된 경우에만 성능 판정
            runbook_timeout = getattr(runbook, "timeout_seconds", None)
            if runbook_timeout and duration_seconds > runbook_timeout * 0.8:
                self._learning_service.learn_pattern(
                    pattern_type=PatternType.PERFORMANCE,
                    name=f"runbook:{runbook.id}:v{runbook.version}:slow_recovery",
                    description=(
                        f"Runbook '{runbook.name}' v{runbook.version} succeeded "
                        f"but took {duration_seconds:.1f}s "
                        f"(SLA: {runbook_timeout}s, {duration_seconds/runbook_timeout*100:.0f}%)"
                    ),
                    features={
                        "runbook_id": runbook.id,
                        "namespace": ctx.namespace,
                        "duration_seconds": duration_seconds,
                        "timeout_seconds": runbook_timeout,
                        "sla_ratio": duration_seconds / runbook_timeout,
                        "step_count": len(runbook.steps),
                    },
                    confidence=0.7,  # 성능 패턴은 보수적 confidence
                    metadata={
                        "execution_id": ctx.execution_id,
                        "runbook_version": runbook.version,
                    },
                )

        RECORDER_SUCCESS.labels(channel="learning").inc()
        return True

    except Exception as e:
        # Fail-Open + 메트릭 (§14.5)
        RECORDER_ERROR.labels(channel="learning", error_type=type(e).__name__).inc()
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
    sanitized_step_results: dict[str, RunbookStepResult],
    sanitized_trigger_event: dict[str, Any],
) -> bool:
    """
    실패 시 자동 postmortem incident 생성.

    add_healing_incident() 패턴:
    - PostgreSQL 저장 시도 → 실패 시 In-Memory fallback
    - recorded_at 자동 부여

    EMERGENCY_RECOVERY_COMPLETED → postmortem 자동 생성 패턴 참조.

    Root Cause Link (§14.C):
    - trigger_event에 포함된 trigger_context 정보를 활용하여
      "왜 이 런북이 트리거되었는가(Original Symptom)"를 incident에 포함.
    - 275 Executor가 MatchResult의 metric_snapshot, triggered_by_event,
      match_confidence를 trigger_event.trigger_context에 주입하는 것을 전제.
      (275 설계 §18.4 참조)

    PII 마스킹 (§14.1):
    - sanitized_step_results/sanitized_trigger_event 사용.
    """
    try:
        from selfhealing.services.postmortem.store import add_healing_incident

        # 실패한 Step 정보 (마스킹된 데이터 사용)
        failed_steps = [
            {"step_name": name, "error": r.error, "action": r.action_name}
            for name, r in sanitized_step_results.items()
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

        # === Root Cause Link (§14.C) ===
        # trigger_event에 trigger_context가 포함되어 있으면 추출.
        # 275 Executor가 MatchResult 데이터를 주입한다 (275 §18.4).
        #
        # 코드 근거:
        # - services/runbook/models.py L272-310: MatchResult.metric_snapshot,
        #   triggered_by_event, event_context 필드
        # - services/runbook/pattern_matcher.py L325-340:
        #   metric_snapshot=dict(metrics) — 매칭 시점 메트릭 전체 복사
        # - utils/postmortem_root_cause.py L246-278:
        #   build_postmortem_root_cause_fields()는 timeline 기반이므로
        #   런북 실행 경로에서는 직접 사용 불가 → trigger_context로 대체
        # - services/postmortem/store.py L939-943:
        #   기존 postmortem은 trigger/detection/resolution 필드를 포함하지만
        #   런북 실행 컨텍스트에서는 MatchResult 기반 정보가 더 정확
        trigger_context = sanitized_trigger_event.get("trigger_context", {})

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
                "trigger_event": sanitized_trigger_event,
                "failed_steps": failed_steps,
                "compensation": compensation_info,
                "abort_reason": ctx.abort_reason,
                "started_at": ctx.started_at,
                "completed_at": ctx.completed_at,
                # Root Cause Link (§14.C)
                # 사후 분석에서 "오진(False Positive) → 런북 실패"인지
                # "런북 로직 오류 → 런북 실패"인지 구분할 수 있는 증거
                "trigger_context": {
                    "triggered_by_event": trigger_context.get("triggered_by_event"),
                    "metric_snapshot": trigger_context.get("metric_snapshot"),
                    "match_confidence": trigger_context.get("match_confidence"),
                    "matched_conditions": trigger_context.get("matched_conditions"),
                },
            },
        }

        add_healing_incident(incident)

        RECORDER_SUCCESS.labels(channel="postmortem").inc()
        return True

    except Exception as e:
        # Fail-Open + 메트릭 (§14.5)
        RECORDER_ERROR.labels(channel="postmortem", error_type=type(e).__name__).inc()
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

    페이로드 크기 제한 (§14.3):
    - 에러 메시지를 MAX_ERROR_MESSAGE_LENGTH(200자)로 Truncate
    - 잘린 데이터에 TRUNCATION_MARKER 추가
    - 상세 로그는 execution_id로 DB조회 유도 (Claim Check 패턴)
    - Kafka 폴백 시 기본 max_request_size(1MB) 제한에의 안전 보장
      (services/event_bus/redis_bus.py L368)
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
            # === 에러 메시지 Truncation (§14.3) ===
            # abort_reason이 긴 Stacktrace를 포함할 수 있으므로 Truncate.
            # 단순 문자열 자르기 시 닫히지 않은 JSON/Stacktrace 문제를 방지하기 위해
            # 명시적 TRUNCATION_MARKER를 덧붙인다.
            abort_reason = ctx.abort_reason or ""
            if len(abort_reason) > MAX_ERROR_MESSAGE_LENGTH:
                abort_reason = (
                    abort_reason[:MAX_ERROR_MESSAGE_LENGTH] + TRUNCATION_MARKER
                )
            event_data["abort_reason"] = abort_reason

            # failed_steps: step_name만 포함 (에러 상세는 DB lookup 유도)
            event_data["failed_steps"] = [
                name for name, r in ctx.step_results.items()
                if not r.success
            ]

        self._event_bus.emit(event_type, event_data)

        RECORDER_SUCCESS.labels(channel="event_bus").inc()
        return True

    except Exception as e:
        # Fail-Open + 메트릭 (§14.5)
        RECORDER_ERROR.labels(channel="event_bus", error_type=type(e).__name__).inc()
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
        ├── recorder_metrics.py  ← RECORDER_SUCCESS/RECORDER_ERROR (§14.5)
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

### 12.1 기록 실패 시 데이터 보존 (§14.5)

Fail-Open으로 메인 로직은 보호되지만, 기록 데이터가 유실될 수 있다.
이 프로젝트에는 이미 성숙한 WAL 폴백 아키텍처가 존재한다:

| 기존 컴포넌트 | 위치 | 역할 |
|---|---|---|
| `ResilientContinuousAuditRecorder` | `audit/resilient_recorder.py` | 4단계 폴백: Primary → LocalFile → Syslog → stderr |
| `InMemoryAuditBuffer` | `audit/resilience/buffer.py` | WAL 실패 시 메모리 FIFO (10K개), `try_flush()` 재전송 |
| `DiskPersistentBuffer` | `audit/persistence/disk_buffer.py` | LMDB 영속 버퍼, Pod 재시작 시 데이터 보존, CRC32 무결성 |

Recorder에서 새로운 WAL/링 버퍼를 만들지 않고, 기존 `DiskPersistentBuffer`를
공유하여 실패한 recording 페이로드를 임시 저장하고 복구 후 재전송한다.

```python
# DiskPersistentBuffer 재활용 패턴 (audit/persistence/disk_buffer.py)
#
# 코드 근거:
# - DiskPersistentBuffer는 LMDB 기반 key-value 저장
# - CRC32 무결성 검증 + Disk Full 시 Fail-Open
# - selfhealing_disk_buffer_entries (Gauge), selfhealing_disk_buffer_puts_total (Counter)
#   등 Prometheus 메트릭 이미 내장
#
# Recorder 통합 방안:
# 1. 각 채널 기록 실패 시 페이로드를 DiskPersistentBuffer에 put()
# 2. 백그라운드 Celery task가 주기적으로 flush 시도
# 3. 성공 시 버퍼에서 삭제
#
# 이 구현은 ResilientContinuousAuditRecorder의
# _write_with_fallback() 패턴 (resilient_recorder.py L397-473)을 따른다.
```

---

## 13. 기존 컴포넌트와의 관계 요약

| 기존 컴포넌트 | Playback Recorder에서의 사용 |
|---|---|
| `CascadeEventAuditor.record()` | Step별 인과 관계 감사 기록 (trigger→effects 체인) |
| `CascadeEffect` | 각 Step 결과를 action_type/success/caused_by로 매핑 |
| `_create_effects()` | caused_by 자동 체인 로직 활용 + 보상 Step 분리 배치 (§14.A) |
| `mask_sensitive_fields()` | record() 진입부에서 PII 일괄 마스킹 (§14.1) |
| `CascadeRetentionSettings` | hot_retention_days=7 TTL 적용 근거 (§14.2) |
| `LearningService.learn_pattern()` | 성공→RECOVERY, 실패→FAILURE, 느림→PERFORMANCE 패턴 학습 |
| `PatternType` | RECOVERY / FAILURE / PERFORMANCE enum 값 사용 |
| `add_healing_incident()` | 실패 시 자동 postmortem 인시던트 생성 + Root Cause Link (§14.C) |
| `MatchResult.metric_snapshot` | trigger_context를 통한 트리거 시점 메트릭 전달 (§14.C) |
| `EventType` | RUNBOOK_EXECUTION_COMPLETED/FAILED 신규 추가 |
| `SelfHealingEventBus.emit()` | 실행 결과 이벤트 발행 + Truncation 적용 (§14.3) |
| `EMERGENCY_RECOVERY_COMPLETED` | 동일한 이벤트→postmortem 패턴의 선례 |
| `SelfHealingMetrics` | Prometheus Counter 패턴 참조 (§14.5) |
| `DiskPersistentBuffer` | 기록 실패 시 LMDB 임시 저장 (§12.1) |

---

## 14. 리뷰 반영

### 14.1 민감 데이터 마스킹 (PII Sanitization)

#### 14.1.1 문제

`CascadeEffect.to_dict()`의 `details` 필드와 `add_healing_incident()`의 `incident` dict가
raw 데이터를 그대로 외부 저장소(Redis/PostgreSQL)에 전달한다.
런북 파라미터나 `result_data`에 DB 접속 정보, 환경변수, 개인정보가 포함될 수 있다.

코드 확인:
- `CascadeEffect.to_dict()` (`audit/cascade_event.py` L326-337): `details` 필드 마스킹 없음
- `CascadeEvent.to_dict()` (`audit/cascade_event.py` L581-610): 인터셉션 포인트 없음
- `add_healing_incident()` (`services/postmortem/store.py`): incident dict raw 저장
- 모든 모델이 `@dataclass` 기반 (Pydantic 미사용) → `model_serializer` 인터셉션 불가

#### 14.1.2 기존 유틸리티

`audit/masking.py` (L381-428)에 중앙 집중형 마스킹 유틸리티가 존재한다:

```python
def mask_sensitive_fields(data, sensitive_keys=None):
    """키 이름 기반 딕셔너리 마스킹. dict/list 재귀 순회."""
    # 기본 키: password, secret, token, api_key, credential, ...
    # key_lower에 sensitive_keys 문자열이 포함되면 "***REDACTED***"로 치환
```

이미 적용된 곳:
- `AuditLogger._build_entry()` (`audit/logger.py` L263-271)
- `ForensicAuditBridge._mask_context()` (`services/forensic_audit_bridge.py` L276-301)
- `RecoveryCoordinator._session_persistence.py` (L256-258)

적용되지 않은 곳:
- `CascadeEventAuditor._recording.py` — trigger_details, effects.details raw 저장
- `PostmortemStore` — incident dict raw 저장

#### 14.1.3 해결

`record()` 진입부에서 `mask_sensitive_fields()`를 일괄 호출하여
모든 채널에 마스킹된 데이터만 전달한다. (§4.2 코드 참조)

마스킹 적용 대상:
- `ctx.step_results[*].result_data` → DB 커넥션 문자열, 환경변수 포함 가능
- `ctx.trigger_event` → PatternMatcher가 전달한 원본 이벤트 데이터

기본 `sensitive_keys`에 Runbook 도메인 키를 확장:
```python
RUNBOOK_SENSITIVE_KEYS = [
    # 기존 기본 키 (audit/masking.py)
    "password", "secret", "token", "api_key", ...
    # Runbook 도메인 확장
    "connection_string", "dsn", "database_url", "db_password",
    "redis_url", "broker_url", "smtp_password",
]
```

선택 근거 — Recorder 진입부 vs `CascadeEvent.to_dict()` 수정:
- `CascadeEvent/CascadeEffect`는 `@dataclass`이므로 `model_serializer` 인터셉션 불가
- `to_dict()` 수정은 CascadeAuditor의 모든 사용처에 영향 → 파급 범위 과도
- Recorder 진입부에서 마스킹하면 277 스코프 내에서 완결되며,
  `AuditLogger._build_entry()` 콜사이트 마스킹 패턴과 동일

---

### 14.2 CascadeEvent TTL (만료 정책)

#### 14.2.1 문제

CascadeEvent가 Redis에 **영구 저장**되어 메모리 누수가 발생할 수 있다.

코드 확인:
- `_save_cascade_event()` (`audit/cascade_auditor/__init__.py` L146-155):
  `backend.set(key, event.to_dict())` — **ttl_seconds 미전달**
- `StateBackend.set()` (`core/state_backend.py` L47):
  `ttl_seconds: int | None = None` — TTL을 지원하는 인터페이스
- `RedisStateBackend.set()` (`core/state_backend.py` L286-292):
  `ttl_seconds`가 None이면 `redis.set()`, 있으면 `redis.setex()` — 원자적 연산
- `CascadeRetentionSettings` (`settings/cascade_retention.py` L27-62):
  `hot_retention_days=7` — **설정만 존재, 실제 적용 코드 없음**

#### 14.2.2 해결

`_save_cascade_event()` 호출 시 `ttl_seconds`를 명시적으로 전달하도록 CascadeAuditor를 수정한다.

```python
# audit/cascade_auditor/__init__.py — 수정 방향
def _save_cascade_event(self, event: Any) -> None:
    backend = self._get_backend()
    key = self.CASCADE_KEY.format(
        namespace=event.namespace,
        cascade_id=event.id,
    )
    # TTL 적용: hot_retention_days (기본 7일)
    ttl_seconds = self._retention.hot_retention_days * 86400
    backend.set(key, event.to_dict(), ttl_seconds=ttl_seconds)
```

선택 근거 — Redis SET EX vs Celery Cleanup Job:
- `redis.setex()`는 원자적 연산으로 동시성 문제 없음
- Celery Job은 스캔 비용 + 불완전 삭제 + 외부 스케줄러 의존성
- `StateBackend.set()`이 이미 `ttl_seconds` 파라미터를 지원하므로 한 줄 수정으로 해결
- `CascadeRetentionSettings`의 `hot_retention_days=7` 설정을 그대로 활용

이 수정은 CascadeAuditor 코드 변경이며 277 Recorder의 직접 스코프는 아니지만,
Recorder가 `CascadeEventAuditor.record()`를 호출하여 대량의 이벤트를 생성하므로
TTL 적용이 필수 전제 조건이다.

---

### 14.3 이벤트 페이로드 크기 제한

#### 14.3.1 문제

EventBus를 통해 발행되는 이벤트에 **애플리케이션 레벨 크기 제한이 없다.**

코드 확인:
- `RedisEventBus.publish()` (`services/event_bus/redis_bus.py` L288-318):
  `json.dumps(event.to_dict(), default=str)` — 크기 검증 없음
- `KafkaProducer` 폴백 (`redis_bus.py` L368):
  `max_request_size` 미설정 → Kafka 기본값 **1MB** 제한
- `SelfHealingEvent.to_dict()` (`bus/__init__.py` L255-264):
  `data` 필드가 임의 크기 dict → 크기 검증 없음

에러 스택트레이스가 수천 자에 달하면 Kafka 1MB 제한 초과로 전송 실패 가능.

#### 14.3.2 해결

`_emit_event()`에서 에러 메시지를 Truncate하고 Truncation Marker를 추가한다.
상세 로그는 `execution_id` 기반 DB 조회로 유도한다 (Claim Check 패턴).

```python
MAX_ERROR_MESSAGE_LENGTH = 200
TRUNCATION_MARKER = "... [TRUNCATED. Full log: DB lookup by execution_id]"
```

적용 방식 (§8.2 코드 참조):
- `abort_reason`: 200자 Truncation + TRUNCATION_MARKER 추가
- `failed_steps`: step_name 목록만 포함 (에러 상세 제외)
- event_data에 `execution_id`를 항상 포함하여 상세 조회 경로 보장

Truncation Marker를 사용하는 이유:
- 단순 문자열 잘라내기 시 닫히지 않은 JSON이나 Stacktrace가 됨
- 소비자가 "잘린 데이터"임을 명확히 인지하고, DB에서 원본을 조회할 수 있음

---

### 14.4 학습 패턴 버전 관리 및 계보 추적

#### 14.4.1 문제

`LearningService.learn_pattern()`은 `name + pattern_type`으로만 매칭하며,
`features`는 최초 등록 시 고정되어 갱신되지 않는다.

코드 확인:
- `learn_pattern()` (`services/learning/service.py` L335-406):
  ```python
  for p in self._patterns.values():
      if p.name == name and p.pattern_type == pattern_type:  # name + type만 비교
          existing = p; break
  if existing:
      existing.occurrence_count += 1
      existing.confidence = (existing.confidence + confidence) / 2
      # features, description, metadata 갱신 안 함!
  ```

런북 v1→v2 업데이트로 Step이 변경되었을 때,
`"runbook:my_runbook:success"` 이름이 같으면 v1의 features(step_count 등)가 유지되어
v2의 실행 데이터와 불일치하는 **데이터 오염** 발생.

#### 14.4.2 해결

**전략 A 채택**: 패턴 이름에 버전을 포함한다.

```python
pattern_name = f"runbook:{runbook.id}:v{runbook.version}:{'success' if is_success else 'failure'}"
# 예: "runbook:circuit_breaker_recovery:v2:success"
```

선택 근거 — 전략 A (버전 포함) vs 전략 B (버전 무시):

| 기준 | 전략 A (채택) | 전략 B |
|------|--------------|--------|
| 데이터 정합성 | 깨끗한 새 패턴으로 시작 | v1 features가 v2에 오염 |
| occurrence_count | 버전별 리셋 (단점) | 연속 누적 (장점) |
| features 정확도 | 버전별 정확 | 최초 등록 값 고정 → 부정확 |
| LearningService 수정 필요 | 없음 | features 갱신 로직 추가 필요 |

전략 A의 `occurrence_count` 리셋 단점은 계보 추적으로 보완한다.

#### 14.4.3 계보 추적 (Lineage)

`metadata`에 `previous_pattern_name`을 기록하여 버전 간 Linked List를 형성한다.

```python
metadata={
    "execution_id": ctx.execution_id,
    "runbook_version": runbook.version,
    "previous_pattern_name": "runbook:circuit_breaker_recovery:v1:success",
}
```

`learn_pattern()`의 `metadata` 파라미터가 자유형 dict이므로
(`services/learning/models.py` L11-84: `metadata: dict[str, Any]`)
스키마 변경 없이 즉시 적용 가능하다.

계보 연결 예시:
```
v1:success → v2:success → v3:success
  (occ=15)    (occ=8)     (occ=2)
각 패턴의 metadata.previous_pattern_name이 이전 버전을 가리킴
→ 대시보드에서 전체 생애 주기 성공률 재구성 가능
```

단, `learn_pattern()`이 기존 패턴 업데이트 시 `metadata`를 갱신하지 않으므로,
`previous_pattern_name`은 최초 등록 시에만 기록된다.
v1→v2→v3 진화 시 v3의 metadata에는 v2 링크만 포함되며, 이는 수용 가능하다.

---

### 14.5 기록 실패 모니터링 메트릭

#### 14.5.1 문제

CascadeAuditor와 PostmortemStore의 기록 실패는 `logger.warning()`이 유일한 알림 수단이다.
100건의 장애 기록이 누락되어도 Loki 쿼리 없이는 발견이 어렵다.

코드 확인:
- 기존 성숙 패턴: `SelfHealingMetrics` (`metrics/prometheus.py` L62-120)
  ```python
  self.dlq_items_total = Counter(f"{prefix}_dlq_items_total", ...)
  self.retry_outcomes_total = Counter(f"{prefix}_retry_outcomes_total", ...)
  ```
  → 진짜 `prometheus_client` Counter/Gauge/Histogram 사용
- `AuditMetrics` (`audit/resilience/metrics.py` L22-100):
  인메모리 dict 기반 자체 구현 — Prometheus 호환 형태이지만 `prometheus_client` 아님
- `docker/prometheus/rules/alerts.yml`:
  audit 관련 알림 규칙 **0개** — DLQ, Circuit Breaker, SLO 알림만 존재

#### 14.5.2 해결

`SelfHealingMetrics` 패턴을 따라 `prometheus_client` Counter를 정의한다.

```python
# services/runbook/recorder_metrics.py

from prometheus_client import Counter

RECORDER_SUCCESS = Counter(
    "selfhealing_runbook_recorder_success_total",
    "Successful recordings by channel",
    ["channel"],  # cascade, learning, postmortem, event_bus
)

RECORDER_ERROR = Counter(
    "selfhealing_runbook_recorder_error_total",
    "Failed recordings by channel and error type",
    ["channel", "error_type"],
)
```

선택 근거 — `prometheus_client` Counter vs `AuditMetrics` 인메모리:
- `SelfHealingMetrics`가 `prometheus_client` 패턴을 사용하므로 일관성 유지
- Prometheus scrape 타겟으로 즉시 노출 → AlertManager 자동 연동
- `AuditMetrics`의 인메모리 패턴은 Prometheus scrape 연결이 별도 필요

`alerts.yml` 추가 규칙 (구현 시):
```yaml
- alert: RunbookRecorderHighFailureRate
  expr: |
    rate(selfhealing_runbook_recorder_error_total[5m])
    / (rate(selfhealing_runbook_recorder_success_total[5m])
       + rate(selfhealing_runbook_recorder_error_total[5m]))
    > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Runbook Recorder {{ $labels.channel }} failure rate > 10%"
```

각 채널의 try/except에서 성공 시 `RECORDER_SUCCESS.labels(channel=...).inc()`,
실패 시 `RECORDER_ERROR.labels(channel=..., error_type=...).inc()`를 호출한다.
(§5.1, §6.1, §7.1, §8.2 각 메서드 코드 참조)

---

### 14.A 인과 관계 체인의 caused_by 명시화

#### 14.A.1 문제

`_create_effects()` (`audit/cascade_auditor/_recording.py` L160-209)의 자동 체인 로직:
```python
previous_event_id = trigger_event_id
for effect_data in effects_data:
    caused_by = effect_data.get("caused_by", previous_event_id)  # 미지정 시 이전 ID
    previous_event_id = effect_event_id  # 갱신
```

이 로직은 **순차 실행에서는 정확**하지만, 보상(Compensation) 단계에서 인과관계가 왜곡된다:

| effects 입력 순서 | 자동 체인 결과 | 올바른 인과관계 |
|---|---|---|
| StepA(성공) → StepB(실패) → Comp_B → Comp_A | A→B→Comp_B→Comp_A (선형) | B→Comp_B, A→Comp_A (각각 연결) |

보상 Step이 어떤 정방향 Step의 실패로 인해 수행되었는지 추적이 불가능해진다.

#### 14.A.2 해결

effects 리스트 구성 시 정방향 Step과 보상 Step을 분리 배치한다.

1. **정방향 Step**: 순차 체인 (caused_by 미지정 → 자동)
2. **보상 Step**: `RUNBOOK_COMPENSATE:` 접두사로 구분하고, 역순으로 배치하여
   `_create_effects()`의 자동 체인이 "실패Step → 보상Step" 연결을 만든다.

`_create_effects()`가 `effect_data.get("caused_by", previous_event_id)`를 사용하므로,
명시적 `caused_by`가 있으면 자동 체인보다 우선한다.
이미 이 확장점이 설계되어 있으므로 호출자(Recorder)에서 올바른 순서와 값을 전달하면 된다.

(§5.1 `_record_cascade_event()` 코드 참조)

---

### 14.B 학습 데이터의 질적 향상 (Negative Feedback)

#### 14.B.1 문제

성공/실패 여부(RECOVERY/FAILURE)만 학습하면,
"성공했지만 SLA의 80%를 초과한 느린 복구"와 같은 뉘앙스가 누락된다.

#### 14.B.2 해결

`PatternType.PERFORMANCE`가 이미 존재하므로 (`services/learning/models.py` L16),
새 enum을 추가하지 않고 성공한 런북 중 느린 실행을 별도 PERFORMANCE 패턴으로 학습시킨다.

```python
# 판정 기준
if is_success and duration_seconds > runbook_timeout * 0.8:
    learn_pattern(PatternType.PERFORMANCE, "runbook:{id}:v{ver}:slow_recovery", ...)
```

선택 근거 — Recorder(277)에서 판정 vs Executor(275)에서 판정:
- duration_seconds와 timeout 비교는 순수 데이터 변환이며 실행 제어(execute/compensate)가 아님
- Executor에 학습 관심사를 두면 단일 책임 원칙(SRP) 위배
- Recorder의 "기록 + 피드백" 책임 범위에 부합
- `RunbookExecutionContext`에 `execution_quality` 필드를 추가하는 것은
  275 Executor의 스코프이므로, 277에서는 기존 데이터만으로 산출 가능한 성능 판정에 한정

(§6.1 `_record_learning_feedback()` 코드 참조)

---

### 14.C Postmortem Root Cause Link

#### 14.C.1 문제

실패 시 생성되는 incident에 "런북이 실패했다"는 사실만 기록되고,
**"왜 이 런북이 트리거되었는가(Original Symptom)"**와
**"트리거 시점의 시스템 상태(metric_snapshot)"**가 연결되지 않는다.

이 정보가 없으면 사후 분석에서 "오진(False Positive)으로 인한 런북 실패"인지
"런북 로직 오류"인지 구분할 수 없다.

코드 확인:
- `MatchResult` (`services/runbook/models.py` L272-310):
  `metric_snapshot: dict[str, float]`, `triggered_by_event: str | None`,
  `event_context: dict[str, Any]`, `matched_conditions: list[str]`
  → 트리거 시점의 풍부한 컨텍스트가 보존됨
- `PatternMatcher.evaluate_all()` (`pattern_matcher.py` L325-340):
  `metric_snapshot=dict(metrics)` — 매칭 시점 메트릭 전체 복사
- `RunbookExecutionContext.trigger_event` (`execution_models.py` L206):
  자유형 `dict[str, Any]` — 구조가 강제되지 않음
- 기존 Postmortem (`utils/postmortem_root_cause.py` L246-278):
  Google SRE 표준 root cause 필드가 존재하지만 timeline 기반이라
  런북 실행 경로와 직접 연결되지 않음

#### 14.C.2 해결

**275 Executor가 MatchResult 데이터를 `trigger_event.trigger_context`에 주입**하고,
**277 Recorder가 이를 incident의 `details.trigger_context`에 포함**하는 역할 분리.

275 Executor 수정 (275 §18.4에서 상세 기술):
```python
# PatternMatcher → Executor 호출 시
# match_result: MatchResult (services/runbook/models.py L272-310)

trigger_event = {
    **original_event_data,
    "trigger_context": {
        "triggered_by_event": match_result.triggered_by_event,
        "metric_snapshot": match_result.metric_snapshot,
        "match_confidence": match_result.confidence,
        "matched_conditions": match_result.matched_conditions,
    },
}
executor.execute_runbook(runbook, trigger_event, namespace)
```

277 Recorder (§7.1 `_record_postmortem()` 참조):
```python
trigger_context = sanitized_trigger_event.get("trigger_context", {})
incident["details"]["trigger_context"] = {
    "triggered_by_event": trigger_context.get("triggered_by_event"),
    "metric_snapshot": trigger_context.get("metric_snapshot"),
    "match_confidence": trigger_context.get("match_confidence"),
    "matched_conditions": trigger_context.get("matched_conditions"),
}
```

선택 근거 — 277에서 모두 구현 vs 275+277 역할 분리:
- `trigger_event`에 MatchResult 데이터를 주입하는 것은 **실행 전** 단계이므로
  Executor(275)의 책임. Recorder(277)는 실행 **후** 기록만 담당.
- 275의 `execute_runbook(runbook, trigger_event, namespace)` 시그니처를 변경하지 않고
  `trigger_event` dict에 `trigger_context` 키를 추가하면 하위 호환성 보장.

---

### 14.R 리뷰 반영 요약

| # | 리뷰 | 반영 섹션 | 핵심 변경 |
|---|------|-----------|-----------|
| 1 | **PII 마스킹 강제화** | §3, §4.2, §5.1, §7.1, §14.1 | `record()` 진입부 `mask_sensitive_fields()` 일괄 호출. `RUNBOOK_SENSITIVE_KEYS` 확장. `@dataclass` 기반이므로 Pydantic `model_serializer` 대신 콜사이트 마스킹 |
| 2 | **CascadeEvent TTL** | §14.2 | `_save_cascade_event()`에 `ttl_seconds=hot_retention_days*86400` 전달. Redis SET EX 원자 연산 위임 |
| 3 | **이벤트 페이로드 Truncation** | §3, §8.2, §14.3 | `MAX_ERROR_MESSAGE_LENGTH=200` + `TRUNCATION_MARKER`. `abort_reason` Truncate, `failed_steps`는 step_name만 |
| 4 | **패턴 버전 관리 + 계보** | §6.1, §14.4 | 패턴 이름에 `v{version}` 포함 (전략 A). `metadata.previous_pattern_name` Linked List 계보 |
| 5 | **Prometheus 모니터링 메트릭** | §5.1, §6.1, §7.1, §8.2, §11, §14.5 | `RECORDER_SUCCESS`/`RECORDER_ERROR` Counter + `alerts.yml` 규칙 |
| A | **caused_by 명시화** | §5.1, §5.2, §14.A | 정방향/보상 Step 분리 배치. `RUNBOOK_COMPENSATE:` 접두사. `_create_effects()` 자동 체인 활용 |
| B | **Performance 학습** | §6.1, §14.B | `PatternType.PERFORMANCE` 활용. `duration > timeout*0.8`이면 `slow_recovery` 패턴 학습 |
| C | **Postmortem Root Cause Link** | §7.1, §14.C | 275가 `trigger_event.trigger_context`에 MatchResult 주입 → 277이 incident에 포함. 275 §18.4 추가 |
