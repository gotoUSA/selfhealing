# 276. Runbook Approval Gate 설계

> **Status**: Design
> **References**:
> - [274_RUNBOOK_REGISTRY.md](274_RUNBOOK_REGISTRY.md) — RiskLevel enum 정의
> - [275_RUNBOOK_EXECUTOR.md](275_RUNBOOK_EXECUTOR.md) — RunbookExecutionContext, RunbookExecutionStatus
> - `services/governance/checks.py` — check_all_governance(), GovernanceCheckMixin, GovernanceCheckResult, BlockReason
> - `services/coordination/recovery_coordinator/_approval.py` — ApprovalMixin, approve_recovery(), READY_TO_RESTORE
> - `services/unified_notification/models.py` — NotificationPayload, NotificationPriority, NotificationCategory.APPROVAL
> - `services/coordination/recovery_coordinator/_session_persistence.py` — _save_session() OCC 패턴

---

## 1. 목적

274에서 정의한 `Runbook.risk_level` (LOW / MEDIUM / HIGH / CRITICAL)에 따라
**실행 전 승인 경로를 자동 분기**하는 게이트를 설계한다.

기존 코드베이스의 두 가지 승인 메커니즘을 통합:

| 기존 메커니즘 | 출처 | 역할 |
|---|---|---|
| **GovernanceCheckMixin** | `services/governance/checks.py` | Kill Switch / Emergency / Error Budget 차단 |
| **ApprovalMixin** | `recovery_coordinator/_approval.py` | READY_TO_RESTORE → 수동 승인 → COMPLETED |

Runbook Approval Gate는 이 두 메커니즘을 **순차적으로** 적용한다:
1. 거버넌스 체크 (시스템 수준 안전장치) — 실패 시 즉시 차단
2. 리스크 기반 승인 라우팅 — RiskLevel에 따른 자동/타이머/수동/차단 결정

---

## 2. 기존 코드 근거

### 2.1 check_all_governance() (`services/governance/checks.py`)

```python
def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    operation_name: str = "unknown_operation",
    service_name: str | None = None,
    audit_on_block: bool = True,
    tier_id: str | None = None,
    region: str | None = None,
) -> GovernanceCheckResult:
    """
    체크 순서:
    0. Break Glass (활성화 시 모든 체크 우회)
    1. Kill Switch
    2. Emergency Level
    3. Error Budget
    첫 번째 실패에서 조기 반환.
    """
```

- `GovernanceCheckResult(allowed: bool, block_reason: BlockReason | None, block_message: str)`
- `BlockReason`: KILL_SWITCH, EMERGENCY_MODE, ERROR_BUDGET, RATE_LIMITED, MANUALLY_BLOCKED
- Break Glass 모드: `settings.break_glass_enabled`이면 모든 거버넌스 우회

### 2.2 GovernanceCheckMixin (`services/governance/checks.py`)

```python
class GovernanceCheckMixin:
    _governance_service_name: str | None = None
    _governance_domain: str | None = None

    def check_governance(self, ...) -> GovernanceCheckResult:
        return check_all_governance(...)

    def is_automation_allowed(self, ...) -> bool:
        result = self.check_governance(...)
        return result.allowed
```

- Runbook Approval Gate는 `GovernanceCheckMixin`을 상속하여
  `check_governance()` 메서드를 직접 활용한다.

### 2.3 ApprovalMixin (`recovery_coordinator/_approval.py`)

```python
class ApprovalMixin:
    def _handle_all_steps_completed(self, session: RecoverySession) -> None:
        if requires_approval:
            session.status = RecoveryStatus.READY_TO_RESTORE
            self._save_session(session)
            self._create_approval_request(session)
        else:
            self._complete_session(session)

    def approve_recovery(self, namespace, approved_by) -> RecoverySession | None:
        # READY_TO_RESTORE 상태인 세션만 승인 가능
        if session.status != RecoveryStatus.READY_TO_RESTORE:
            return None
        session.status = RecoveryStatus.COMPLETED
        session.metadata["approved_by"] = approved_by
        session.metadata["approved_at"] = ...
```

- `requires_approval` 결정: `session.metadata.get("requires_approval", False)`
- `_create_approval_request()`: `PendingRecoveryApprovalManager.create_request()`
- 승인 API: `approve_recovery(namespace, approved_by)` → COMPLETED 전환

### 2.4 NotificationPayload (`services/unified_notification/models.py`)

```python
class NotificationCategory(str, Enum):
    APPROVAL = "approval"       # <<< 승인 요청용 카테고리 존재

class NotificationPriority(str, Enum):
    CRITICAL = "critical"       # all channels
    HIGH = "high"               # Slack + Email
    MEDIUM = "medium"           # Slack only

@dataclass
class NotificationPayload:
    title: str
    message: str
    priority: NotificationPriority
    category: NotificationCategory
    source: str
    metadata: dict[str, Any]
    dedup_key: str | None = None
```

- `NotificationCategory.APPROVAL`이 이미 정의되어 있으므로,
  승인 요청 알림을 별도 카테고리 없이 기존 인프라로 발송 가능

---

## 3. RiskLevel-기반 승인 라우팅

274에서 정의한 `RiskLevel`에 따른 승인 정책:

### 3.1 라우팅 매트릭스

| RiskLevel | 거버넌스 체크 | 승인 방식 | 타이머 | 알림 |
|---|---|---|---|---|
| **LOW** | check_all_governance() | 자동 승인 | 없음 | 없음 |
| **MEDIUM** | check_all_governance() | 알림 후 타이머 자동 승인 | SELFHEALING_RUNBOOK_APPROVAL_TIMER_SECONDS (기본 300초) | NotificationPriority.HIGH |
| **HIGH** | check_all_governance() | 수동 승인 필수 | 없음 (무기한 대기) | NotificationPriority.CRITICAL |
| **CRITICAL** | check_all_governance() | 실행 차단 | 해당 없음 | NotificationPriority.CRITICAL |

### 3.2 흐름

```
evaluate_approval(runbook, ctx)
│
├─ 1. 거버넌스 체크: check_all_governance()
│    ├─ Break Glass → 모든 거버넌스 우회 (기존 로직 그대로)
│    ├─ Kill Switch → BLOCKED
│    ├─ Emergency Mode LEVEL_2+ → BLOCKED
│    └─ Error Budget < threshold → BLOCKED
│
├─ 2. RiskLevel 분기:
│    ├─ LOW → ApprovalDecision.AUTO_APPROVED
│    │
│    ├─ MEDIUM → 알림 발송 + 타이머 설정
│    │    ├─ 타이머 내 수동 거부 → ApprovalDecision.REJECTED
│    │    └─ 타이머 만료 → ApprovalDecision.TIMER_APPROVED
│    │
│    ├─ HIGH → 알림 발송 + WAITING_APPROVAL 상태
│    │    ├─ approve_runbook() 호출 → ApprovalDecision.MANUALLY_APPROVED
│    │    └─ reject_runbook() 호출 → ApprovalDecision.REJECTED
│    │
│    └─ CRITICAL → ApprovalDecision.BLOCKED
│
└─ 3. 결과 반환: ApprovalDecision
```

---

## 4. 데이터 모델

### 4.1 ApprovalDecision

```python
class ApprovalDecisionType(str, Enum):
    """승인 결정 유형."""
    AUTO_APPROVED = "auto_approved"         # LOW: 자동 승인
    TIMER_APPROVED = "timer_approved"       # MEDIUM: 타이머 자동 승인
    MANUALLY_APPROVED = "manually_approved" # HIGH: 수동 승인
    REJECTED = "rejected"                   # 수동 거부
    BLOCKED = "blocked"                     # 거버넌스 차단 또는 CRITICAL
    WAITING = "waiting"                     # 승인 대기 중

@dataclass
class ApprovalDecision:
    """승인 결정 결과."""
    decision_type: ApprovalDecisionType
    risk_level: RiskLevel
    approved_by: str | None = None          # 수동 승인자 ID
    block_reason: BlockReason | None = None # 거버넌스 차단 사유
    block_message: str = ""
    decided_at: str | None = None
    governance_result: GovernanceCheckResult | None = None

    @property
    def is_approved(self) -> bool:
        return self.decision_type in (
            ApprovalDecisionType.AUTO_APPROVED,
            ApprovalDecisionType.TIMER_APPROVED,
            ApprovalDecisionType.MANUALLY_APPROVED,
        )
```

### 4.2 ApprovalRequest (영속화)

```python
@dataclass
class RunbookApprovalRequest:
    """
    승인 요청 레코드.
    PendingRecoveryApprovalManager.create_request() 패턴 참조.
    """
    request_id: str
    execution_id: str     # RunbookExecutionContext.execution_id
    runbook_id: str
    namespace: str
    risk_level: RiskLevel
    status: ApprovalDecisionType = ApprovalDecisionType.WAITING
    created_at: str | None = None
    expires_at: str | None = None   # MEDIUM 타이머 만료 시각
    decided_at: str | None = None
    decided_by: str | None = None
    runbook_summary: dict[str, Any] = field(default_factory=dict)
    # runbook_summary: runbook 이름, step 수, 대상 네임스페이스 등
```

---

## 5. RunbookApprovalGate 클래스

```python
class RunbookApprovalGate(GovernanceCheckMixin):
    """
    Runbook 실행 전 승인 게이트.

    GovernanceCheckMixin 상속으로 check_governance() 직접 사용.
    ApprovalMixin의 approve_recovery() 패턴을 Runbook에 맞게 확장.
    """

    _governance_service_name = "runbook"
    _governance_domain = "selfhealing"

    def __init__(
        self,
        notification_manager: UnifiedNotificationManager | None = None,
        state_backend: StateBackend | None = None,
    ):
        self._notification = notification_manager
        self._backend = state_backend
```

### 5.1 거버넌스 체크 (`evaluate_governance`)

```python
def evaluate_governance(
    self,
    runbook: Runbook,
    namespace: str,
) -> GovernanceCheckResult:
    """
    check_all_governance() 호출.
    GovernanceCheckMixin.check_governance()를 직접 사용.
    """
    return self.check_governance(
        check_kill_switch=True,
        check_emergency=True,
        check_error_budget=True,
        operation_name=f"runbook:{runbook.id}",
        audit_on_block=True,
    )
```

### 5.2 승인 평가 (`evaluate_approval`)

```python
def evaluate_approval(
    self,
    runbook: Runbook,
    ctx: RunbookExecutionContext,
) -> ApprovalDecision:
    """
    RiskLevel에 따른 승인 결정.

    1단계: 거버넌스 체크 (시스템 안전)
    2단계: RiskLevel 기반 라우팅
    """
    # 1. 거버넌스 체크
    gov_result = self.evaluate_governance(runbook, ctx.namespace)
    if not gov_result.allowed:
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=runbook.risk_level,
            block_reason=gov_result.block_reason,
            block_message=gov_result.block_message,
            governance_result=gov_result,
        )

    # 2. RiskLevel 라우팅
    risk = runbook.risk_level

    if risk == RiskLevel.LOW:
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.AUTO_APPROVED,
            risk_level=risk,
            approved_by="system:auto",
            decided_at=datetime.now(timezone.utc).isoformat(),
        )

    elif risk == RiskLevel.MEDIUM:
        # 알림 발송 + 타이머 설정
        self._send_approval_notification(runbook, ctx, is_timer=True)
        self._create_approval_request(runbook, ctx, with_timer=True)
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.WAITING,
            risk_level=risk,
        )

    elif risk == RiskLevel.HIGH:
        # 알림 발송 + 무기한 대기
        self._send_approval_notification(runbook, ctx, is_timer=False)
        self._create_approval_request(runbook, ctx, with_timer=False)
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.WAITING,
            risk_level=risk,
        )

    elif risk == RiskLevel.CRITICAL:
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=risk,
            block_message=(
                f"CRITICAL risk runbook '{runbook.id}' requires manual "
                f"execution — automated execution is blocked"
            ),
        )

    # 알 수 없는 RiskLevel — 안전하게 차단
    return ApprovalDecision(
        decision_type=ApprovalDecisionType.BLOCKED,
        risk_level=risk,
        block_message=f"Unknown risk level: {risk}",
    )
```

### 5.3 수동 승인 API (`approve_runbook`)

ApprovalMixin.approve_recovery() 패턴을 따른다:

```python
def approve_runbook(
    self,
    execution_id: str,
    approved_by: str,
) -> ApprovalDecision:
    """
    Runbook 실행 수동 승인.

    ApprovalMixin.approve_recovery() 패턴:
    - WAITING 상태인 요청만 승인 가능
    - 승인자 정보 + 시각 기록
    - 승인 요청 상태 업데이트
    """
    request = self._load_approval_request(execution_id)
    if request is None:
        raise RunbookApprovalError(f"Approval request not found: {execution_id}")

    if request.status != ApprovalDecisionType.WAITING:
        raise RunbookApprovalError(
            f"Cannot approve: request is in {request.status} state"
        )

    # 승인 처리 (ApprovalMixin 패턴)
    request.status = ApprovalDecisionType.MANUALLY_APPROVED
    request.decided_by = approved_by
    request.decided_at = datetime.now(timezone.utc).isoformat()
    self._save_approval_request(request)

    return ApprovalDecision(
        decision_type=ApprovalDecisionType.MANUALLY_APPROVED,
        risk_level=request.risk_level,
        approved_by=approved_by,
        decided_at=request.decided_at,
    )
```

### 5.4 수동 거부 API (`reject_runbook`)

```python
def reject_runbook(
    self,
    execution_id: str,
    rejected_by: str,
    reason: str = "",
) -> ApprovalDecision:
    """Runbook 실행 거부."""
    request = self._load_approval_request(execution_id)
    if request is None:
        raise RunbookApprovalError(f"Approval request not found: {execution_id}")

    if request.status != ApprovalDecisionType.WAITING:
        raise RunbookApprovalError(
            f"Cannot reject: request is in {request.status} state"
        )

    request.status = ApprovalDecisionType.REJECTED
    request.decided_by = rejected_by
    request.decided_at = datetime.now(timezone.utc).isoformat()
    self._save_approval_request(request)

    return ApprovalDecision(
        decision_type=ApprovalDecisionType.REJECTED,
        risk_level=request.risk_level,
        block_message=reason,
    )
```

---

## 6. 알림 발송

### 6.1 승인 요청 알림 (`_send_approval_notification`)

```python
def _send_approval_notification(
    self,
    runbook: Runbook,
    ctx: RunbookExecutionContext,
    is_timer: bool,
) -> None:
    """
    승인 요청 알림 발송.

    NotificationCategory.APPROVAL + 적절한 Priority 사용.
    """
    if self._notification is None:
        logger.warning("runbook_approval.no_notification_manager")
        return

    # RiskLevel → NotificationPriority 매핑
    priority = (
        NotificationPriority.HIGH
        if is_timer  # MEDIUM risk → HIGH priority
        else NotificationPriority.CRITICAL  # HIGH risk → CRITICAL priority
    )

    timer_info = ""
    if is_timer:
        timer_seconds = self._get_timer_seconds()
        timer_info = f"\n⏱ 자동 승인까지 {timer_seconds}초 남음. 거부하려면 수동 개입 필요."

    payload = NotificationPayload(
        title=f"[Runbook 승인 요청] {runbook.name}",
        message=(
            f"Runbook: {runbook.id}\n"
            f"Risk Level: {runbook.risk_level.value}\n"
            f"Namespace: {ctx.namespace}\n"
            f"Execution ID: {ctx.execution_id}\n"
            f"Steps: {len(runbook.steps)}개\n"
            f"Description: {runbook.description}"
            f"{timer_info}"
        ),
        priority=priority,
        category=NotificationCategory.APPROVAL,
        source="runbook_approval_gate",
        metadata={
            "execution_id": ctx.execution_id,
            "runbook_id": runbook.id,
            "risk_level": runbook.risk_level.value,
            "namespace": ctx.namespace,
            "requires_timer": is_timer,
        },
        dedup_key=f"runbook_approval:{ctx.execution_id}",
    )

    try:
        self._notification.notify(payload)
    except Exception as e:
        logger.warning("runbook_approval.notification_failed", error=e)
```

---

## 7. MEDIUM 타이머 자동 승인

### 7.1 타이머 메커니즘

MEDIUM 리스크의 Runbook은 알림 발송 후 타이머가 시작되고,
타이머 만료까지 수동 거부가 없으면 자동 승인된다.

```python
def check_timer_approval(
    self,
    execution_id: str,
) -> ApprovalDecision | None:
    """
    MEDIUM 리스크 타이머 만료 체크.

    Celery Beat 또는 polling에서 주기적으로 호출.

    Returns:
        ApprovalDecision if timer expired, None if still waiting
    """
    request = self._load_approval_request(execution_id)
    if request is None or request.status != ApprovalDecisionType.WAITING:
        return None

    if request.risk_level != RiskLevel.MEDIUM:
        return None  # HIGH는 타이머 없음

    if request.expires_at is None:
        return None

    now = datetime.now(timezone.utc).isoformat()
    if now >= request.expires_at:
        # 타이머 만료 → 자동 승인
        request.status = ApprovalDecisionType.TIMER_APPROVED
        request.decided_by = "system:timer"
        request.decided_at = now
        self._save_approval_request(request)

        return ApprovalDecision(
            decision_type=ApprovalDecisionType.TIMER_APPROVED,
            risk_level=request.risk_level,
            approved_by="system:timer",
            decided_at=now,
        )

    return None  # 아직 대기 중
```

### 7.2 승인 대기 폴링 (`wait_for_approval`)

RunbookExecutor(275)에서 승인 대기가 필요한 경우 호출:

```python
def wait_for_approval(
    self,
    execution_id: str,
    poll_interval_seconds: int = 10,
    max_wait_seconds: int | None = None,
) -> ApprovalDecision:
    """
    승인 결과를 폴링하며 대기.

    MEDIUM: 타이머 만료까지 대기 (check_timer_approval 병행)
    HIGH: max_wait_seconds까지 또는 무기한 대기

    Returns:
        최종 ApprovalDecision
    """
    start = time.time()

    while True:
        # 1. 타이머 만료 체크 (MEDIUM)
        timer_result = self.check_timer_approval(execution_id)
        if timer_result is not None:
            return timer_result

        # 2. 수동 승인/거부 체크
        request = self._load_approval_request(execution_id)
        if request and request.status != ApprovalDecisionType.WAITING:
            return ApprovalDecision(
                decision_type=request.status,
                risk_level=request.risk_level,
                approved_by=request.decided_by,
                decided_at=request.decided_at,
            )

        # 3. 타임아웃 체크
        if max_wait_seconds and (time.time() - start) > max_wait_seconds:
            return ApprovalDecision(
                decision_type=ApprovalDecisionType.BLOCKED,
                risk_level=request.risk_level if request else RiskLevel.HIGH,
                block_message="Approval wait timeout exceeded",
            )

        time.sleep(poll_interval_seconds)
```

---

## 8. 영속화

### 8.1 승인 요청 저장

```python
APPROVAL_REQUEST_KEY = "selfhealing:runbook:approval:{execution_id}"

def _create_approval_request(
    self,
    runbook: Runbook,
    ctx: RunbookExecutionContext,
    with_timer: bool,
) -> RunbookApprovalRequest:
    """
    승인 요청 생성 + 영속화.
    PendingRecoveryApprovalManager.create_request() 패턴.
    """
    timer_seconds = self._get_timer_seconds() if with_timer else None
    expires_at = None
    if timer_seconds:
        from datetime import timedelta
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=timer_seconds)
        ).isoformat()

    request = RunbookApprovalRequest(
        request_id=f"approval-{uuid4()}",
        execution_id=ctx.execution_id,
        runbook_id=runbook.id,
        namespace=ctx.namespace,
        risk_level=runbook.risk_level,
        status=ApprovalDecisionType.WAITING,
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at,
        runbook_summary={
            "name": runbook.name,
            "description": runbook.description,
            "step_count": len(runbook.steps),
            "risk_level": runbook.risk_level.value,
        },
    )
    self._save_approval_request(request)
    return request

def _save_approval_request(self, request: RunbookApprovalRequest) -> None:
    backend = self._get_backend()
    key = self.APPROVAL_REQUEST_KEY.format(execution_id=request.execution_id)
    backend.set(key, request.to_dict(), ttl=86400)  # 24시간 TTL

def _load_approval_request(self, execution_id: str) -> RunbookApprovalRequest | None:
    backend = self._get_backend()
    key = self.APPROVAL_REQUEST_KEY.format(execution_id=execution_id)
    data = backend.get(key)
    if data:
        return RunbookApprovalRequest.from_dict(data)
    return None
```

---

## 9. RunbookExecutor 통합 (275 연동)

275의 `execute_runbook()` 흐름에 승인 게이트를 삽입:

```python
# RunbookExecutor.execute_runbook() 내부

def execute_runbook(self, runbook, trigger_event, namespace):
    execution_id = f"runbook-{uuid4()}"
    ctx = RunbookExecutionContext(
        execution_id=execution_id,
        runbook_id=runbook.id,
        namespace=namespace,
        trigger_event=trigger_event,
    )

    # === 승인 게이트 (276) ===
    approval_gate = RunbookApprovalGate(
        notification_manager=self._notification,
        state_backend=self._backend,
    )
    decision = approval_gate.evaluate_approval(runbook, ctx)

    if decision.decision_type == ApprovalDecisionType.BLOCKED:
        ctx.status = RunbookExecutionStatus.CANCELLED
        ctx.abort_reason = decision.block_message
        self._save_context(ctx)
        return ctx

    if decision.decision_type == ApprovalDecisionType.WAITING:
        # WAITING_APPROVAL 상태로 전환
        ctx.status = RunbookExecutionStatus.WAITING_APPROVAL
        self._save_context(ctx)

        # 승인 대기 (MEDIUM: 타이머, HIGH: 무기한)
        max_wait = None
        if runbook.risk_level == RiskLevel.MEDIUM:
            max_wait = approval_gate._get_timer_seconds() + 60  # 타이머 + 여유
        decision = approval_gate.wait_for_approval(
            execution_id, max_wait_seconds=max_wait,
        )

        if not decision.is_approved:
            ctx.status = RunbookExecutionStatus.CANCELLED
            ctx.abort_reason = f"Approval {decision.decision_type.value}: {decision.block_message}"
            self._save_context(ctx)
            return ctx

    # === 승인 완료 → Lock 획득 → 실행 ===
    acquired = self._recovery_lock.acquire(namespace, execution_id)
    if not acquired:
        raise RunbookLockConflictError(...)

    try:
        return self._run(runbook, trigger_event, namespace, execution_id, ctx)
    finally:
        self._recovery_lock.release(namespace, execution_id)
```

---

## 10. 설정

```python
# Pydantic BaseSettings, SELFHEALING_ 접두사

SELFHEALING_RUNBOOK_APPROVAL_TIMER_SECONDS: int = 300
# MEDIUM 리스크 자동 승인 타이머 (기본 5분)

SELFHEALING_RUNBOOK_APPROVAL_POLL_INTERVAL_SECONDS: int = 10
# 승인 대기 폴링 간격

SELFHEALING_RUNBOOK_APPROVAL_MAX_WAIT_SECONDS: int = 3600
# HIGH 리스크 최대 대기 시간 (기본 1시간, 0이면 무기한)
```

---

## 11. 모듈 구조

```
packages/selfhealing-python/src/selfhealing/
└── services/
    └── runbook/
        ├── approval_gate.py     ← RunbookApprovalGate (이 문서)
        ├── models.py            ← ApprovalDecision, ApprovalDecisionType,
        │                           RunbookApprovalRequest 추가
        └── ...
```

---

## 12. 기존 컴포넌트와의 관계 요약

| 기존 컴포넌트 | Approval Gate에서의 사용 |
|---|---|
| `GovernanceCheckMixin` | 상속. check_governance()로 Kill Switch/Emergency/Error Budget 체크 |
| `check_all_governance()` | 거버넌스 체크 함수 직접 호출 |
| `GovernanceCheckResult` | 거버넌스 체크 결과 타입 그대로 사용 |
| `BlockReason` | 차단 사유 enum 그대로 사용 |
| `ApprovalMixin` | approve_recovery() 승인 패턴 차용 (WAITING → APPROVED) |
| `PendingRecoveryApprovalManager` | create_request() 승인 요청 생성 패턴 참조 |
| `UnifiedNotificationManager` | 승인 요청 알림 발송 |
| `NotificationCategory.APPROVAL` | 기존 카테고리 사용 |
| `StateBackend` | 승인 요청 영속화 (Redis/InMemory) |
| `Break Glass` | governance settings.break_glass_enabled → 모든 거버넌스 우회 |
