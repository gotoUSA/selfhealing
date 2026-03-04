# 296. Builtin Runbooks — 시스템 기본 제공 런북 정의

> **Status**: Design
> **Target**: `packages/selfhealing-python/src/selfhealing/services/runbook/builtins.py`
> **References**:
> - [274_RUNBOOK_REGISTRY.md](274_RUNBOOK_REGISTRY.md) — RunbookRegistry, ActionPrimitiveRegistry, Runbook/RunbookStep 데이터 모델
> - [275_RUNBOOK_EXECUTOR.md](275_RUNBOOK_EXECUTOR.md) — Step 순차 실행 + 보상
> - [278_RUNBOOK_SERVICE.md](278_RUNBOOK_SERVICE.md) — RunbookService 파이프라인 + 초기화 시퀀스 (§8.1)
> - [297_EXECUTOR_ENHANCEMENTS.md](297_EXECUTOR_ENHANCEMENTS.md) — continue_on_failure, poll_interval, labels Fail-Fast (본 문서 의존)
> - `services/coordination/recovery_coordinator/__init__.py` — DEFAULT_RECOVERY_STEPS (기존 하드코딩 복구)
> - `services/runbook/primitives.py` — 빌트인 ActionPrimitive 7종

---

## 1. 목적

RecoveryCoordinator의 `DEFAULT_RECOVERY_STEPS`에 하드코딩된 복구 절차를
선언적 Runbook 정의로 전환한다.

현재 RecoveryCoordinator는 Emergency 레벨별 복구를 하드코딩으로 처리한다:

```python
# recovery_coordinator/__init__.py:171-244
DEFAULT_RECOVERY_STEPS = {
    "LEVEL_3": [BUDGET_RESET → HEALTH_CHECK → CANARY_RESUME → GOVERNANCE_NORMAL],
    "LEVEL_2": [BUDGET_RESET → HEALTH_CHECK → CANARY_RESUME],
    "LEVEL_1": [BUDGET_RESET → HEALTH_CHECK],
}
```

이 방식의 한계:
- 복구 절차 수정 시 코드 배포 필요
- 승인 게이트(ApprovalGate) 미적용
- 감사 기록(Recorder) 미적용
- 조건부 분기(StepCondition) 불가

Builtin Runbook은 동일한 복구 절차를 Runbook 데이터 모델로 재선언하여
RunbookService 파이프라인(PatternMatcher → ApprovalGate → Executor → Recorder)을
자동 적용한다.

---

## 2. 기존 코드 근거

### 2.1 RecoveryCoordinator.DEFAULT_RECOVERY_STEPS

```python
# recovery_coordinator/__init__.py:171-244
"LEVEL_3": [
    RecoveryStep(step_type=BUDGET_RESET,     order=1, wait_after=0,   params={"target_multiplier": 1.0}),
    RecoveryStep(step_type=HEALTH_CHECK,     order=2, wait_after=0,   params={"duration_minutes": 5, "success_threshold": 0.95, "error_rate_threshold": 0.1}),
    RecoveryStep(step_type=CANARY_RESUME,    order=3, wait_after=60,  params={"resume_paused_only": True}),
    RecoveryStep(step_type=GOVERNANCE_NORMAL, order=4, wait_after=300, params={"reason": "[AUTO-RECOVERY] Stability confirmed"}),
]
"LEVEL_2": [
    RecoveryStep(step_type=BUDGET_RESET,  order=1, wait_after=0,  params={"target_multiplier": 1.0}),
    RecoveryStep(step_type=HEALTH_CHECK,  order=2, wait_after=0,  params={"duration_minutes": 3, "success_threshold": 0.95, "error_rate_threshold": 0.15}),
    RecoveryStep(step_type=CANARY_RESUME, order=3, wait_after=30, params={"resume_paused_only": True}),
]
"LEVEL_1": [
    RecoveryStep(step_type=BUDGET_RESET, order=1, wait_after=0, params={"target_multiplier": 1.0}),
    RecoveryStep(step_type=HEALTH_CHECK, order=2, wait_after=0, params={"duration_minutes": 2, "success_threshold": 0.90, "error_rate_threshold": 0.2}),
]
```

### 2.2 빌트인 ActionPrimitive 7종 (`primitives.py`)

| Action | 카테고리 | 설명 |
|--------|---------|------|
| `config.set` | config | RuntimeConfigManager를 통해 설정값 변경 |
| `assert.metric` | assert | 메트릭 현재값을 threshold와 비교하는 검증 게이트 |
| `notify.send` | notify | UnifiedNotificationManager를 통해 알림 발송 |
| `recovery.start` | recovery | RecoveryCoordinator.start_recovery() 호출 |
| `emergency.activate` | emergency | GracefulDegradationManager.activate_auto() 호출 |
| `emergency.deactivate` | emergency | GracefulDegradationManager.deactivate() 호출 |
| `wait.stabilize` | wait | 지정 시간 대기 후 선택적 메트릭 검증 |

### 2.3 RunbookService 구독 이벤트 (`service.py:501-511`)

```python
target_events = [
    EventType.EMERGENCY_LEVEL_CHANGED,
    EventType.ERROR_BUDGET_CRITICAL,
    EventType.CIRCUIT_BREAKER_OPENED,
]
optional_events = ["HEALTH_CHECK_FAILED", "SLA_VIOLATION_DETECTED"]
```

### 2.4 initialize_runbook_system 호출 경로 (`service.py:628-641`)

```python
def initialize_runbook_system() -> RunbookService:
    service = RunbookService()
    from selfhealing.services.runbook.builtins import register_builtin_runbooks
    register_builtin_runbooks()  # ← 이 함수가 빌트인 런북을 등록
    ...
```

### 2.5 PatternCondition 데이터 모델 (`models.py:213-264`)

```python
@dataclass
class PatternCondition:
    metric_conditions: list[MetricCondition]  # AND 결합
    event_conditions: list[EventCondition]    # OR 트리거
    min_duration_seconds: int = 0
```

### 2.6 Runbook/RunbookStep 데이터 모델 (`runbook_registry.py:143-330`)

```python
@dataclass
class RunbookStep:
    name: str                              # Step 식별자
    action: str                            # Action Primitive 이름
    order: int = 0                         # 실행 순서
    params: dict[str, Any]                 # 파라미터
    on_failure_action: str | None = None   # 보상 Action
    on_failure_params: dict[str, Any]      # 보상 파라미터
    validation_action: str | None = None   # 완료 후 검증 Action
    condition: StepCondition | None = None # 조건부 실행
    timeout_seconds: int = 120             # Step 타임아웃
    wait_after_seconds: int = 0            # 완료 후 안정화 대기
    idempotent: bool = True                # 멱등성 힌트
    continue_on_failure: bool = False      # True면 실패해도 abort 없이 다음 Step 진행 (297 §2)

@dataclass
class Runbook:
    id: str
    name: str
    description: str
    trigger_condition: PatternCondition
    steps: list[RunbookStep]
    risk_level: RiskLevel = RiskLevel.LOW
    enabled: bool = True
    priority: int = 100
    cooldown_seconds: int = 300
    version: int = 1
    tags: list[str]
    global_timeout_seconds: int | None = None
```

---

## 3. RecoveryCoordinator와의 관계

### 3.1 전환 전략

RecoveryCoordinator의 하드코딩 복구를 Runbook으로 **대체**한다.

대체 사유:
1. 동일 이벤트에 두 시스템이 반응하면 **실행 충돌** 발생 (config 덮어쓰기, 중복 recovery.start)
2. RecoveryCoordinator는 ApprovalGate/Recorder 파이프라인을 거치지 않아 **거버넌스 사각지대**
3. 복구 로직이 두 곳에 분산되면 장애 분석 시 **인과 관계 추적이 복잡**
4. 개발 단계이므로 병존 없이 직접 전환 가능

### 3.2 RecoveryStep → RunbookStep 매핑

| RecoveryStepType | RunbookStep action | 비고 |
|------------------|--------------------|------|
| `BUDGET_RESET` | `config.set` | `key="crisis_multiplier.target_multiplier"` |
| `HEALTH_CHECK` | `assert.metric` + `wait.stabilize` | 2-Step 조합으로 변환 |
| `CANARY_RESUME` | `recovery.start` | `trigger_level`로 Canary 단계 지정 |
| `GOVERNANCE_NORMAL` | `config.set` | `key="governance.mode", value="NORMAL"` |

`HEALTH_CHECK`는 단일 RecoveryStep이지만 Runbook에서는 2개 Step으로 분해:
1. `wait.stabilize` — 지정 시간 대기 + 메트릭 확인
2. `assert.metric` — 에러율이 임계값 이하인지 검증 게이트

---

## 4. 빌트인 런북 정의

### 4.1 emergency_recovery_level3 — LEVEL_3 복구

RecoveryCoordinator의 `DEFAULT_RECOVERY_STEPS["LEVEL_3"]` 4-Step 시퀀스를 대체한다.

```python
Runbook(
    id="emergency_recovery_level3",
    name="Emergency LEVEL_3 Recovery",
    description="Emergency LEVEL_3에서 정상으로 복구하는 4단계 절차. "
                "Budget Reset → Health Check → Canary Resume → Governance Normal.",
    trigger_condition=PatternCondition(
        event_conditions=[
            EventCondition(
                event_type="emergency_level_changed",
                data_filter={"previous_level": 3, "direction": "deescalation"},
            ),
        ],
    ),
    steps=[
        # Step 1: Budget Multiplier 리셋 (BUDGET_RESET 대응)
        RunbookStep(
            name="budget_reset",
            action="config.set",
            order=1,
            params={
                "key": "crisis_multiplier.target_multiplier",
                "value": 1.0,
            },
            on_failure_action="notify.send",
            on_failure_params={
                "title": "[LEVEL_3 Recovery] Budget Reset Failed",
                "message": "Crisis Multiplier 리셋 실패. 수동 확인 필요.",
                "priority": "critical",
            },
            timeout_seconds=30,
            wait_after_seconds=0,
        ),
        # Step 2: 안정화 대기 (HEALTH_CHECK 전반부 대응)
        RunbookStep(
            name="stabilization_wait",
            action="wait.stabilize",
            order=2,
            params={
                "seconds": 300,  # 5분 대기
                "assert_metric": "error_rate",
                "threshold": 0.9,  # 대기 중 에러율 90% 이상이면 실패
            },
            timeout_seconds=360,
            wait_after_seconds=0,
        ),
        # Step 3: 에러율 검증 게이트 (HEALTH_CHECK 후반부 대응)
        RunbookStep(
            name="health_check_gate",
            action="assert.metric",
            order=3,
            params={
                "metric_name": "error_rate",
                "operator": "lte",
                "threshold": 0.1,  # 에러율 10% 이하
            },
            timeout_seconds=30,
            wait_after_seconds=0,
        ),
        # Step 4: Canary 롤아웃 재개 (CANARY_RESUME 대응)
        RunbookStep(
            name="canary_resume",
            action="recovery.start",
            order=4,
            params={
                "namespace": "${trigger.namespace}",
                "trigger_level": "CANARY_RESUME",
            },
            timeout_seconds=120,
            wait_after_seconds=60,  # 기존 wait_after_seconds=60
        ),
        # Step 5: Governance NORMAL 전환 (GOVERNANCE_NORMAL 대응)
        RunbookStep(
            name="governance_normal",
            action="config.set",
            order=5,
            params={
                "key": "governance.mode",
                "value": "NORMAL",
            },
            on_failure_action="notify.send",
            on_failure_params={
                "title": "[LEVEL_3 Recovery] Governance Restore Failed",
                "message": "Governance NORMAL 전환 실패. 수동 확인 필요.",
                "priority": "high",
            },
            timeout_seconds=30,
            wait_after_seconds=300,  # 기존 wait_after_seconds=300 (5분 안정화)
        ),
        # Step 6: 최종 알림
        RunbookStep(
            name="completion_notify",
            action="notify.send",
            order=6,
            params={
                "title": "[LEVEL_3 Recovery] Completed",
                "message": "Emergency LEVEL_3 복구 완료. 모든 서비스 정상 복원.",
                "priority": "medium",
            },
            timeout_seconds=30,
        ),
    ],
    risk_level=RiskLevel.HIGH,
    priority=10,
    cooldown_seconds=600,
    tags=["emergency", "recovery", "level3"],
    global_timeout_seconds=1200,  # 20분
)
```

설계 결정:
- **RiskLevel.HIGH** — LEVEL_3는 최고 심각도이므로 수동 승인 필수 (`ApprovalGate:206-246`)
- **priority=10** — 다른 런북보다 높은 우선순위 (낮을수록 높음)
- **cooldown_seconds=600** — 10분 쿨다운 (LEVEL_3 복구는 빈번하지 않아야 함)
- **global_timeout_seconds=1200** — wait.stabilize(300초) + 나머지 Step 합산 고려

### 4.2 emergency_recovery_level2 — LEVEL_2 복구

RecoveryCoordinator의 `DEFAULT_RECOVERY_STEPS["LEVEL_2"]` 3-Step 시퀀스를 대체한다.

```python
Runbook(
    id="emergency_recovery_level2",
    name="Emergency LEVEL_2 Recovery",
    description="Emergency LEVEL_2에서 정상으로 복구하는 3단계 절차. "
                "Budget Reset → Health Check → Canary Resume.",
    trigger_condition=PatternCondition(
        event_conditions=[
            EventCondition(
                event_type="emergency_level_changed",
                data_filter={"previous_level": 2, "direction": "deescalation"},
            ),
        ],
    ),
    steps=[
        # Step 1: Budget Multiplier 리셋
        RunbookStep(
            name="budget_reset",
            action="config.set",
            order=1,
            params={
                "key": "crisis_multiplier.target_multiplier",
                "value": 1.0,
            },
            on_failure_action="notify.send",
            on_failure_params={
                "title": "[LEVEL_2 Recovery] Budget Reset Failed",
                "message": "Crisis Multiplier 리셋 실패.",
                "priority": "high",
            },
            timeout_seconds=30,
        ),
        # Step 2: 안정화 대기 + 에러율 확인
        RunbookStep(
            name="stabilization_wait",
            action="wait.stabilize",
            order=2,
            params={
                "seconds": 180,  # 3분 대기
                "assert_metric": "error_rate",
                "threshold": 0.85,
            },
            timeout_seconds=240,
        ),
        # Step 3: 에러율 검증 게이트
        RunbookStep(
            name="health_check_gate",
            action="assert.metric",
            order=3,
            params={
                "metric_name": "error_rate",
                "operator": "lte",
                "threshold": 0.15,  # 에러율 15% 이하
            },
            timeout_seconds=30,
        ),
        # Step 4: Canary 롤아웃 재개
        RunbookStep(
            name="canary_resume",
            action="recovery.start",
            order=4,
            params={
                "namespace": "${trigger.namespace}",
                "trigger_level": "CANARY_RESUME",
            },
            timeout_seconds=120,
            wait_after_seconds=30,  # 기존 wait_after_seconds=30
        ),
        # Step 5: 완료 알림
        RunbookStep(
            name="completion_notify",
            action="notify.send",
            order=5,
            params={
                "title": "[LEVEL_2 Recovery] Completed",
                "message": "Emergency LEVEL_2 복구 완료.",
                "priority": "medium",
            },
            timeout_seconds=30,
        ),
    ],
    risk_level=RiskLevel.MEDIUM,
    priority=20,
    cooldown_seconds=300,
    tags=["emergency", "recovery", "level2"],
    global_timeout_seconds=900,  # 15분
)
```

설계 결정:
- **RiskLevel.MEDIUM** — LEVEL_2는 알림 후 타이머 대기. 타이머 만료 시 자동 실행 (`ApprovalGate:218-221`)
- **Health Check 파라미터** — 기존 `duration_minutes=3`, `error_rate_threshold=0.15` 그대로 반영

### 4.3 emergency_recovery_level1 — LEVEL_1 복구

RecoveryCoordinator의 `DEFAULT_RECOVERY_STEPS["LEVEL_1"]` 2-Step 시퀀스를 대체한다.

```python
Runbook(
    id="emergency_recovery_level1",
    name="Emergency LEVEL_1 Recovery",
    description="Emergency LEVEL_1에서 정상으로 복구하는 2단계 절차. "
                "Budget Reset → Health Check.",
    trigger_condition=PatternCondition(
        event_conditions=[
            EventCondition(
                event_type="emergency_level_changed",
                data_filter={"previous_level": 1, "direction": "deescalation"},
            ),
        ],
    ),
    steps=[
        # Step 1: Budget Multiplier 리셋
        RunbookStep(
            name="budget_reset",
            action="config.set",
            order=1,
            params={
                "key": "crisis_multiplier.target_multiplier",
                "value": 1.0,
            },
            timeout_seconds=30,
        ),
        # Step 2: 안정화 대기 + 에러율 확인
        RunbookStep(
            name="stabilization_wait",
            action="wait.stabilize",
            order=2,
            params={
                "seconds": 120,  # 2분 대기
                "assert_metric": "error_rate",
                "threshold": 0.8,
            },
            timeout_seconds=180,
        ),
        # Step 3: 에러율 검증 게이트
        RunbookStep(
            name="health_check_gate",
            action="assert.metric",
            order=3,
            params={
                "metric_name": "error_rate",
                "operator": "lte",
                "threshold": 0.2,  # 에러율 20% 이하
            },
            timeout_seconds=30,
        ),
        # Step 4: 완료 알림
        RunbookStep(
            name="completion_notify",
            action="notify.send",
            order=4,
            params={
                "title": "[LEVEL_1 Recovery] Completed",
                "message": "Emergency LEVEL_1 복구 완료.",
                "priority": "low",
            },
            timeout_seconds=30,
        ),
    ],
    risk_level=RiskLevel.LOW,
    priority=30,
    cooldown_seconds=180,
    tags=["emergency", "recovery", "level1"],
    global_timeout_seconds=600,  # 10분
)
```

설계 결정:
- **RiskLevel.LOW** — LEVEL_1은 경미한 장애이므로 승인 없이 즉시 자동 실행
- **Canary Resume/Governance Normal 미포함** — 기존 DEFAULT_RECOVERY_STEPS["LEVEL_1"]과 동일 범위

### 4.4 circuit_breaker_opened_response — CB Open 대응

RunbookService가 구독하는 `CIRCUIT_BREAKER_OPENED` 이벤트에 대응한다.
RecoveryCoordinator에는 없는 **신규 런북**이다.

```python
Runbook(
    id="circuit_breaker_opened_response",
    name="Circuit Breaker Opened Response",
    description="Circuit Breaker가 Open되면 알림 발송 후 에러율을 확인하고, "
                "임계값 초과 시 Emergency 모드를 활성화한다.",
    trigger_condition=PatternCondition(
        event_conditions=[
            EventCondition(event_type="circuit_breaker_opened"),
        ],
    ),
    steps=[
        # Step 1: 즉시 알림
        RunbookStep(
            name="alert_notify",
            action="notify.send",
            order=1,
            params={
                "title": "[CB Open] ${trigger.source}",
                "message": "Circuit Breaker가 Open되었습니다. 에러율 확인 중.",
                "priority": "high",
            },
            timeout_seconds=30,
        ),
        # Step 2: 안정화 대기 (자연 복구 기회 제공)
        RunbookStep(
            name="stabilization_wait",
            action="wait.stabilize",
            order=2,
            params={
                "seconds": 60,
                "assert_metric": "error_rate",
                "threshold": 0.7,
                "labels": {"service": "${trigger.source}"},
            },
            timeout_seconds=90,
        ),
        # Step 3: 에러율 확인 — 자연 복구 여부 판단 (continue_on_failure: 297 §2 참조)
        RunbookStep(
            name="error_rate_check",
            action="assert.metric",
            order=3,
            params={
                "metric_name": "error_rate",
                "operator": "lte",
                "threshold": 0.3,  # 30% 이하면 자연 복구로 판단
                "labels": {"service": "${trigger.source}"},
            },
            continue_on_failure=True,
            timeout_seconds=30,
        ),
        # Step 4: 에러율 초과 시 Emergency 활성화 (Step 3 실패 시에만 실행)
        RunbookStep(
            name="emergency_escalation",
            action="emergency.activate",
            order=4,
            params={
                "level": 1,
                "reason": "CB Open 후 에러율 미회복 — 자동 에스컬레이션",
            },
            condition=StepCondition(type="prev_failed"),
            timeout_seconds=30,
        ),
        # Step 5: 에스컬레이션 알림 (Step 4가 실행된 경우)
        RunbookStep(
            name="escalation_notify",
            action="notify.send",
            order=5,
            params={
                "title": "[CB Open] Emergency LEVEL_1 Activated",
                "message": "에러율 미회복으로 Emergency LEVEL_1이 활성화되었습니다.",
                "priority": "critical",
            },
            condition=StepCondition(type="prev_succeeded"),
            timeout_seconds=30,
        ),
    ],
    risk_level=RiskLevel.MEDIUM,
    priority=50,
    cooldown_seconds=300,
    tags=["circuit_breaker", "escalation"],
    global_timeout_seconds=300,  # 5분
)
```

설계 결정:
- **StepCondition 활용** — Step 3(assert.metric) 실패 시에만 Step 4(emergency.activate) 실행. 조건부 분기로 불필요한 에스컬레이션 방지
- **continue_on_failure=True** — Step 3은 검증 게이트 역할이므로 실패 시 Executor abort가 아닌 상태만 기록하고 다음 Step으로 진행해야 한다. 이 필드가 없으면 Step 3 실패 → 즉시 abort → Step 4(prev_failed 조건) 영원히 미도달. 상세 설계는 [297 §2](297_EXECUTOR_ENHANCEMENTS.md) 참조
- **Per-Service 메트릭 스코프** — `labels: {"service": "${trigger.source}"}`로 CB가 열린 특정 서비스의 에러율만 측정. 전역 에러율 집계 오탐 방지 ([273 §3](273_RUNBOOK_PATTERN_MATCHER.md) LabelFilter 설계 근거)
- **자연 복구 기회** — 60초 대기 후 에러율 확인. CB가 Half-Open → Closed로 자연 복구될 시간 확보
- **에스컬레이션 경로** — CB Open → (미회복) → Emergency LEVEL_1 → (미회복) → LEVEL_2/3은 기존 Emergency 시스템이 처리

### 4.5 error_budget_critical_response — Error Budget 소진 대응

RunbookService가 구독하는 `ERROR_BUDGET_CRITICAL` 이벤트에 대응한다.

```python
Runbook(
    id="error_budget_critical_response",
    name="Error Budget Critical Response",
    description="Error Budget이 소진되면 알림 발송 후 트래픽 제한을 적용하고, "
                "안정화 확인 후 에스컬레이션 여부를 판단한다.",
    trigger_condition=PatternCondition(
        event_conditions=[
            EventCondition(event_type="error_budget_critical"),
        ],
    ),
    steps=[
        # Step 1: 긴급 알림
        RunbookStep(
            name="alert_notify",
            action="notify.send",
            order=1,
            params={
                "title": "[Error Budget] Critical — Budget Exhausted",
                "message": "Error Budget이 소진되었습니다. 자동 대응 시작.",
                "priority": "critical",
            },
            timeout_seconds=30,
        ),
        # Step 2: Crisis Multiplier 상향 (트래픽 억제)
        RunbookStep(
            name="increase_crisis_multiplier",
            action="config.set",
            order=2,
            params={
                "key": "crisis_multiplier.target_multiplier",
                "value_delta": 0.5,  # 현재값 + 0.5
            },
            on_failure_action="notify.send",
            on_failure_params={
                "title": "[Error Budget] Crisis Multiplier Adjustment Failed",
                "message": "Crisis Multiplier 조정 실패. 수동 확인 필요.",
                "priority": "critical",
            },
            timeout_seconds=30,
        ),
        # Step 3: 안정화 대기
        RunbookStep(
            name="stabilization_wait",
            action="wait.stabilize",
            order=3,
            params={
                "seconds": 120,
                "assert_metric": "error_rate",
                "threshold": 0.8,
            },
            timeout_seconds=180,
        ),
        # Step 4: 에러율 확인 (continue_on_failure: 297 §2 참조)
        RunbookStep(
            name="error_rate_check",
            action="assert.metric",
            order=4,
            params={
                "metric_name": "error_rate",
                "operator": "lte",
                "threshold": 0.15,
                "labels": {"service": "${trigger.source}"},
            },
            continue_on_failure=True,
            timeout_seconds=30,
        ),
        # Step 5: 안정화 실패 시 Emergency 활성화
        RunbookStep(
            name="emergency_escalation",
            action="emergency.activate",
            order=5,
            params={
                "level": 1,
                "reason": "Error Budget 소진 후 에러율 미회복 — 자동 에스컬레이션",
            },
            condition=StepCondition(type="prev_failed"),
            timeout_seconds=30,
        ),
    ],
    risk_level=RiskLevel.MEDIUM,
    priority=40,
    cooldown_seconds=600,
    tags=["error_budget", "escalation"],
    global_timeout_seconds=600,  # 10분
)
```

설계 결정:
- **value_delta 사용** — `config.set`의 `value_delta` 파라미터로 현재 multiplier에 0.5를 가산. 절대값 덮어쓰기보다 안전
- **continue_on_failure=True** — Step 4(assert.metric)은 검증 게이트이므로 실패해도 abort하지 않고 Step 5(prev_failed 조건)로 진행. [297 §2](297_EXECUTOR_ENHANCEMENTS.md) 참조
- **Per-Service 메트릭 스코프** — `labels: {"service": "${trigger.source}"}`로 Error Budget이 소진된 서비스의 에러율만 측정
- **에스컬레이션 경로** — Error Budget → Crisis Multiplier → (미회복) → Emergency LEVEL_1

---

## 5. RiskLevel 배정 기준

| 런북 | RiskLevel | 근거 |
|------|-----------|------|
| `emergency_recovery_level3` | HIGH | LEVEL_3 복구는 전체 시스템 영향. 수동 승인 필수 |
| `emergency_recovery_level2` | MEDIUM | 중간 심각도. 알림 후 타이머 대기 |
| `emergency_recovery_level1` | LOW | 경미한 장애. 즉시 자동 실행 |
| `circuit_breaker_opened_response` | MEDIUM | CB Open은 빈번할 수 있으나 Emergency 에스컬레이션 가능성. 타이머 대기 |
| `error_budget_critical_response` | MEDIUM | Crisis Multiplier 변경 포함. 타이머 대기 |

RiskLevel → ApprovalGate 매핑 (`approval_gate.py:206-246`):
- **LOW** → `AUTO_APPROVED` (즉시 실행)
- **MEDIUM** → `WAITING` (타이머 대기 후 자동 실행)
- **HIGH** → `WAITING` (수동 승인 필수)
- **CRITICAL** → `BLOCKED` (force_execute만 가능)

---

## 6. 보상(Compensation) 전략

### 6.1 원칙

1. **상태 변경 Step에만 보상 정의** — `config.set`, `emergency.activate` 등
2. **알림/검증 Step은 보상 불필요** — `notify.send`, `assert.metric`은 부작용 없음
3. **보상 실패 시 알림** — `on_failure_action="notify.send"`로 운영자에게 통보
4. **Forward Recovery 원칙 (강제 규약)** — 시스템 상태를 전진시키는(State-progressing) 설정 변경의 보상은 **역보상(이전 값으로 원복)을 하지 않고 알림(notify.send)만 발송**한다. 이유: Emergency de-escalation 과정에서 이미 해제된 억제 상태(예: Multiplier 0.5)로 기계적으로 롤백하면 정상 트래픽까지 차단되어 오히려 장애를 유발한다. 복구 실패 시에는 현재 상태를 유지한 채 운영자가 판단하는 Forward Recovery가 안전하다. `on_failure_action`에 `config.set` 역원복을 추가하는 것은 이 규약 위반이다.

### 6.2 보상 매핑

| Step action | 보상 action | 설명 |
|-------------|-----------|------|
| `config.set` (budget_reset) | `notify.send` | Multiplier는 멱등하므로 원복 대신 알림 |
| `config.set` (governance_normal) | `notify.send` | Governance 원복은 위험하므로 알림만 |
| `emergency.activate` | `emergency.deactivate` | Emergency 활성화 원복 |
| `recovery.start` | 없음 | RecoveryCoordinator 내부 보상에 위임 |

---

## 7. register_builtin_runbooks 구현

```python
# builtins.py

def register_builtin_runbooks() -> None:
    """빌트인 Runbook을 레지스트리에 등록.

    initialize_runbook_system() (service.py:637)에서 호출된다.
    """
    from selfhealing.services.runbook.runbook_registry import (
        Runbook,
        RunbookRegistry,
        RunbookStep,
        RiskLevel,
        StepCondition,
    )
    from selfhealing.services.runbook.models import (
        EventCondition,
        PatternCondition,
    )

    # ProviderRegistry에서 RunbookRegistry 획득 시도
    # 없으면 새 인스턴스 생성 (테스트 환경)
    try:
        from selfhealing.factory import ProviderRegistry
        registry = ProviderRegistry.get("runbook_registry")
    except Exception:
        registry = None

    if registry is None:
        registry = RunbookRegistry()

    builtin_runbooks = [
        _build_emergency_recovery_level3(),
        _build_emergency_recovery_level2(),
        _build_emergency_recovery_level1(),
        _build_circuit_breaker_opened_response(),
        _build_error_budget_critical_response(),
    ]

    registered = 0
    for runbook in builtin_runbooks:
        try:
            registry.register(runbook)
            registered += 1
        except ValueError as exc:
            logger.warning(
                "runbook_builtins.register_failed",
                runbook_id=runbook.id,
                error=str(exc),
            )

    logger.info(
        "runbook_builtins.registered",
        count=registered,
        total=len(builtin_runbooks),
    )
```

각 `_build_*()` 함수는 §4의 Runbook 정의를 반환하는 팩토리 함수이다.

---

## 8. 쿨다운/우선순위 설계

### 8.1 우선순위 (priority — 낮을수록 높음)

```
10: emergency_recovery_level3    — 최고 우선순위 (LEVEL_3는 시스템 전체 영향)
20: emergency_recovery_level2
30: emergency_recovery_level1
40: error_budget_critical_response
50: circuit_breaker_opened_response — 가장 낮은 우선순위 (개별 서비스 수준)
```

PatternMatcher의 `select_runbook()`에서 동일 이벤트에 여러 런북이 매칭될 때
`priority` → `risk_level` → `confidence` 순으로 Tie-breaking한다.

### 8.2 쿨다운 (cooldown_seconds)

| 런북 | 쿨다운 | 근거 |
|------|--------|------|
| `emergency_recovery_level3` | 600초 | LEVEL_3 복구 후 최소 10분 안정화 필요 |
| `emergency_recovery_level2` | 300초 | 5분 |
| `emergency_recovery_level1` | 180초 | 3분 — 경미한 장애는 빈번할 수 있으므로 짧게 |
| `circuit_breaker_opened_response` | 300초 | CB는 여러 서비스에서 동시 Open될 수 있으므로 적절한 간격 |
| `error_budget_critical_response` | 600초 | Budget 소진은 근본 원인 해결 전 재트리거 방지 |

---

## 9. 모듈 구조

```
packages/selfhealing-python/src/selfhealing/services/runbook/
├── builtins.py         ← 이 문서의 구현 대상
│   ├── register_builtin_runbooks()           # 진입점
│   ├── _build_emergency_recovery_level3()    # §4.1
│   ├── _build_emergency_recovery_level2()    # §4.2
│   ├── _build_emergency_recovery_level1()    # §4.3
│   ├── _build_circuit_breaker_opened_response()  # §4.4
│   └── _build_error_budget_critical_response()   # §4.5
├── primitives.py       ← 빌트인 ActionPrimitive 7종 (274)
├── runbook_registry.py ← Runbook/RunbookStep/ActionPrimitiveRegistry (274)
├── models.py           ← PatternCondition/EventCondition (273)
└── service.py          ← initialize_runbook_system() → register_builtin_runbooks() (278)
```

---

## 10. 테스트 전략

테스트 위치: `packages/selfhealing-python/tests/unit/test_runbook_builtins.py`

### 10.1 등록 검증

```python
def test_register_builtin_runbooks_registers_all():
    """5개 빌트인 런북이 모두 등록되는지 확인."""
    registry = RunbookRegistry()
    register_builtin_runbooks()
    assert len(registry.get_all()) == 5

def test_all_builtin_runbooks_pass_validation():
    """모든 빌트인 런북이 Runbook.validate()를 통과하는지 확인."""
    for runbook in [...]:
        valid, error = runbook.validate()
        assert valid, f"{runbook.id}: {error}"
```

### 10.2 Step 구성 검증

```python
def test_level3_recovery_has_correct_step_count():
    """LEVEL_3 복구 런북이 6개 Step을 가지는지 확인."""
    runbook = _build_emergency_recovery_level3()
    assert len(runbook.steps) == 6

def test_level3_recovery_step_actions_match_primitives():
    """모든 Step의 action이 빌트인 프리미티브에 존재하는지 확인."""
    from selfhealing.services.runbook.primitives import BUILTIN_PRIMITIVES
    runbook = _build_emergency_recovery_level3()
    for step in runbook.steps:
        assert step.action in BUILTIN_PRIMITIVES
```

### 10.3 조건부 분기 검증

```python
def test_cb_response_emergency_step_has_prev_failed_condition():
    """CB Open 런북의 emergency_escalation Step이 prev_failed 조건을 가지는지 확인."""
    runbook = _build_circuit_breaker_opened_response()
    escalation_step = next(s for s in runbook.steps if s.name == "emergency_escalation")
    assert escalation_step.condition is not None
    assert escalation_step.condition.type == "prev_failed"
```
