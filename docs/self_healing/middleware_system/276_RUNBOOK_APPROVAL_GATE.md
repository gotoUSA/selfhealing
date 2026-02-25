# 276. Runbook Approval Gate 설계

> **Status**: Implemented
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

ApprovalMixin.approve_recovery() 패턴을 따르되,
**Redis Lua CAS로 원자적 상태 전환**을 보장하고
**CAS 실패 시 409 Conflict 응답**을 반환한다.

코드 근거:
- `_session_persistence.py` L27-69 — `_save_session()` OCC (Version + Lua CAS)
- `atomic_transition.py` L28-50 — `ATOMIC_TRANSITION_SCRIPT` 상태 기반 Lua CAS
- `_approval.py` L100 — `with self._lock:` + 상태 검증 패턴

```python
class ApprovalAlreadyDecidedError(Exception):
    """승인 요청이 이미 다른 결정으로 확정된 경우.

    API 레이어에서 HTTP 409 Conflict로 매핑.
    프론트엔드/슬랙 앱이 운영자에게 적절한 안내를 표시할 수 있도록
    현재 확정된 상태를 포함한다.

    코드 근거: SessionVersionConflictError — __init__.py L92-103
    """
    def __init__(self, execution_id: str, current_status: ApprovalDecisionType):
        self.execution_id = execution_id
        self.current_status = current_status
        super().__init__(
            f"Approval already decided: {execution_id} "
            f"is in '{current_status.value}' state"
        )

def approve_runbook(
    self,
    execution_id: str,
    approved_by: str,
) -> ApprovalDecision:
    """
    Runbook 실행 수동 승인.

    동시성 방어:
    - Redis Lua CAS로 WAITING → MANUALLY_APPROVED 원자적 전환
    - 타이머 만료(TIMER_APPROVED)와 동시 도달 시 CAS가 선착순 보장
    - CAS 실패 시 ApprovalAlreadyDecidedError (→ HTTP 409 Conflict)

    승인 성공 시 resume_runbook_task를 트리거하여 실행 재개.
    """
    request = self._load_approval_request(execution_id)
    if request is None:
        raise RunbookApprovalError(f"Approval request not found: {execution_id}")

    if request.status != ApprovalDecisionType.WAITING:
        # 이미 결정됨 — 409 Conflict 전용 예외
        raise ApprovalAlreadyDecidedError(execution_id, request.status)

    # CAS 상태 전환 (§13 참조)
    request.status = ApprovalDecisionType.MANUALLY_APPROVED
    request.decided_by = approved_by
    request.decided_at = datetime.now(timezone.utc).isoformat()

    success = self._cas_save_approval_request(request, expected_status="waiting")
    if not success:
        # CAS 실패: 타이머 또는 다른 운영자가 먼저 결정
        current = self._load_approval_request(execution_id)
        raise ApprovalAlreadyDecidedError(
            execution_id,
            current.status if current else ApprovalDecisionType.BLOCKED,
        )

    # 승인 후 실행 재개 트리거 (§14 이벤트 드리븐 재개)
    self._trigger_resume(execution_id)

    return ApprovalDecision(
        decision_type=ApprovalDecisionType.MANUALLY_APPROVED,
        risk_level=request.risk_level,
        approved_by=approved_by,
        decided_at=request.decided_at,
    )
```

### 5.4 수동 거부 API (`reject_runbook`)

승인과 동일하게 **CAS + 409 Conflict** 패턴을 적용한다.

```python
def reject_runbook(
    self,
    execution_id: str,
    rejected_by: str,
    reason: str = "",
) -> ApprovalDecision:
    """Runbook 실행 거부.

    CAS로 WAITING → REJECTED 원자적 전환.
    CAS 실패 시 ApprovalAlreadyDecidedError (→ HTTP 409 Conflict).
    """
    request = self._load_approval_request(execution_id)
    if request is None:
        raise RunbookApprovalError(f"Approval request not found: {execution_id}")

    if request.status != ApprovalDecisionType.WAITING:
        raise ApprovalAlreadyDecidedError(execution_id, request.status)

    request.status = ApprovalDecisionType.REJECTED
    request.decided_by = rejected_by
    request.decided_at = datetime.now(timezone.utc).isoformat()

    success = self._cas_save_approval_request(request, expected_status="waiting")
    if not success:
        current = self._load_approval_request(execution_id)
        raise ApprovalAlreadyDecidedError(
            execution_id,
            current.status if current else ApprovalDecisionType.BLOCKED,
        )

    return ApprovalDecision(
        decision_type=ApprovalDecisionType.REJECTED,
        risk_level=request.risk_level,
        block_message=reason,
    )
```

### 5.5 CRITICAL 런북 강제 실행 API (`force_execute_runbook`)

Break Glass(시스템 전역 거버넌스 우회)와 분리된 **런북 단위 강제 실행 진입점**.

설계 근거:
- Break Glass는 `settings.break_glass_enabled` 환경변수로 **시스템 전역** Kill Switch/
  Emergency/Error Budget을 모두 무력화 (`checks.py` L486-502). CRITICAL 런북 1건을
  위해 전역 안전장치를 해제하는 것은 과도한 권한 상승.
- Force Execute는 **거버넌스 체크는 유지**하되 `RiskLevel.CRITICAL` 차단만 우회.
- `approve_recovery()` (`_approval.py` L100)가 `approved_by`를 기록하는 패턴과 동일하게
  `force_executed_by` + `justification`을 감사 기록에 남긴다.

| 구분 | Break Glass | Force Execute |
|------|------------|---------------|
| 범위 | 시스템 전역 | 특정 Runbook 1건 |
| 거버넌스 체크 | 전부 우회 | Kill Switch/Emergency **여전히 적용** |
| 활성화 | 환경변수 설정 | API 호출 시 파라미터 |
| 감사 | `break_glass_audit_required` | **항상 필수** |
| 권한 | 환경변수 접근 권한 | `role: emergency_responder` 필수 |

```python
def force_execute_runbook(
    self,
    runbook: Runbook,
    ctx: RunbookExecutionContext,
    force_executed_by: str,
    justification: str,
) -> ApprovalDecision:
    """
    CRITICAL 런북 강제 실행.

    Break Glass와 달리:
    - 거버넌스 체크(Kill Switch/Emergency/Error Budget)는 여전히 수행
    - RiskLevel.CRITICAL 차단만 우회
    - justification 필수 (빈 문자열 시 거부)
    - 감사 로그 필수 기록
    - 호출자의 IAM 권한 검증 필수 (API 레이어에서 @require_role 데코레이터)

    API 레이어 결합 예시:
        @require_role("emergency_responder")
        def post(self, request, execution_id):
            gate.force_execute_runbook(...)
    """
    if not justification or not justification.strip():
        raise RunbookApprovalError(
            "Force execution requires a non-empty justification"
        )

    # 1. 거버넌스 체크 — Kill Switch/Emergency/Error Budget은 여전히 적용
    gov_result = self.evaluate_governance(runbook, ctx.namespace)
    if not gov_result.allowed:
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=runbook.risk_level,
            block_reason=gov_result.block_reason,
            block_message=(
                f"Force execution blocked by governance: "
                f"{gov_result.block_message}. "
                f"Use Break Glass to override system-level governance."
            ),
            governance_result=gov_result,
        )

    # 2. CRITICAL 차단 우회 + 감사 기록
    logger.warning(
        "runbook_approval.force_execute",
        runbook_id=runbook.id,
        risk_level=runbook.risk_level.value,
        force_executed_by=force_executed_by,
        justification=justification,
        namespace=ctx.namespace,
    )

    return ApprovalDecision(
        decision_type=ApprovalDecisionType.MANUALLY_APPROVED,
        risk_level=runbook.risk_level,
        approved_by=f"force:{force_executed_by}",
        decided_at=datetime.now(timezone.utc).isoformat(),
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

**CAS 기반 원자적 상태 전환**을 적용하여 타이머 만료와
운영자의 수동 거부가 동시에 도달하더라도 데이터 정합성을 보장한다.

코드 근거:
- `atomic_transition.py` L28-50 — 상태 기반 Lua CAS (expected status → new status)
- `_session_persistence.py` L27-69 — CAS 실패 시 Fail-Open 폴백 패턴

```python
def check_timer_approval(
    self,
    execution_id: str,
) -> ApprovalDecision | None:
    """
    MEDIUM 리스크 타이머 만료 체크.

    Celery Beat에서 주기적으로 호출.
    CAS로 WAITING → TIMER_APPROVED 원자적 전환.
    CAS 실패 시(운영자가 먼저 거부) 멱등하게 무시.

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
        # 타이머 만료 → CAS 상태 전환 (§13 참조)
        request.status = ApprovalDecisionType.TIMER_APPROVED
        request.decided_by = "system:timer"
        request.decided_at = now

        success = self._cas_save_approval_request(
            request, expected_status="waiting",
        )
        if not success:
            # CAS 실패: 운영자가 먼저 거부/승인 → 멱등하게 무시
            logger.info(
                "runbook_approval.timer_cas_conflict",
                execution_id=execution_id,
            )
            return None

        # 타이머 승인 성공 → 실행 재개 트리거 (§14)
        self._trigger_resume(execution_id)

        return ApprovalDecision(
            decision_type=ApprovalDecisionType.TIMER_APPROVED,
            risk_level=request.risk_level,
            approved_by="system:timer",
            decided_at=now,
        )

    return None  # 아직 대기 중
```

### 7.2 이벤트 드리븐 재개 (폴링 블로킹 제거)

> **설계 변경**: 기존 `wait_for_approval()` (while True + time.sleep) → 삭제.
> 이벤트 드리븐 비동기 재개 방식으로 전환.

**변경 사유** — `recovery_tasks.py` L370-410의 기존 패턴과 불일치:

기존 `recovery_tasks.py`는 Step 간 대기 시 `time.sleep()`이 아닌
`apply_async(countdown=delay)`로 Worker를 즉시 해방하고 다음 Step을
별도 Celery Task로 예약하는 이벤트 드리븐 체인을 사용한다.

```python
# recovery_tasks.py L404-410 — 기존 이벤트 드리븐 패턴
if wait_seconds > 0:
    execute_recovery_step_task.apply_async(
        args=[session_id, namespace],
        countdown=wait_seconds,  # Worker 해방 + 지연 예약
    )
else:
    execute_recovery_step_task.delay(session_id, namespace)  # 즉시 예약
```

반면 기존 `wait_for_approval()`은 `while True: time.sleep(10)`으로 Worker를
최대 3600초 블로킹하여 **Worker Exhaustion** 위험을 초래한다.
HIGH 리스크 런북 수십 개가 동시에 승인 대기에 들어가면
Celery Worker Pool이 고갈되어 다른 전체 태스크가 중단될 수 있다.

**대체 설계** — 승인/타이머 이벤트가 실행 재개를 트리거:

```
evaluate_approval()
├─ WAITING 결정 → 영속화 + 알림 → Worker 즉시 반환 (블로킹 없음)
│
├─ [이벤트 A] approve_runbook() API 호출
│    └─ CAS 성공 → resume_runbook_task.apply_async()
│
├─ [이벤트 B] check_timer_approval() (Celery Beat)
│    └─ CAS 성공 → resume_runbook_task.apply_async()
│
└─ [이벤트 C] reject_runbook() API 호출
     └─ CAS 성공 → 실행 취소 (재개 없음)
```

```python
def _trigger_resume(self, execution_id: str) -> None:
    """
    승인 확정 후 Runbook 실행 재개 트리거.

    코드 근거:
    - recovery_tasks.py L404 — apply_async(countdown=) 이벤트 드리븐 패턴
    - 275 §13.1 resume_execution() — Lock 획득 + Stale Context 방어 + 버전 검증

    resume_runbook_task가:
    1. Governance Double-Check 수행 (§15)
    2. Lock 획득
    3. resume_execution() 호출
    """
    from selfhealing.services.runbook.tasks import resume_runbook_task

    resume_runbook_task.apply_async(
        args=[execution_id],
        queue="selfhealing_runbook",  # 전용 큐 분리
    )
```

> **`wait_for_approval()`은 테스트 전용 동기 헬퍼로 유지 가능하나,
> 프로덕션 실행 경로에서는 사용하지 않는다.**

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

    코드 근거:
    - PendingRecoveryApprovalManager.create_request() — pending_recovery_approval.py L270-275
      동일 session_id에 PENDING 상태 요청이 있으면 ValueError를 발생시키는 중복 방지 패턴.

    중복 방지:
    - 동일 runbook_id + namespace에 WAITING 상태 요청이 이미 존재하면 생성 차단.
    - 장애 지속 중 동일 런북이 반복 트리거될 때 승인 요청/알림 폭주를 방지.
    - 알림 레이어(APPROVAL cooldown=0)를 변경하지 않고 요청 생성 단계에서 차단함으로써
      기존 Recovery 승인 알림에 영향을 주지 않음.
    """
    # === 중복 승인 요청 방지 ===
    # PendingRecoveryApprovalManager.create_request() 패턴:
    # "이미 해당 세션에 대한 대기 중 요청이 있으면 raise ValueError"
    existing = self._find_waiting_request_by_runbook(
        runbook.id, ctx.namespace,
    )
    if existing:
        logger.info(
            "runbook_approval.duplicate_suppressed",
            existing_execution_id=existing.execution_id,
            new_execution_id=ctx.execution_id,
            runbook_id=runbook.id,
            namespace=ctx.namespace,
        )
        raise RunbookApprovalDuplicateError(
            f"Approval already pending for runbook '{runbook.id}' "
            f"in namespace '{ctx.namespace}' "
            f"(existing execution: {existing.execution_id})"
        )

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

def _find_waiting_request_by_runbook(
    self,
    runbook_id: str,
    namespace: str,
) -> RunbookApprovalRequest | None:
    """
    동일 runbook_id + namespace의 WAITING 상태 요청 검색.

    Redis SCAN 또는 보조 인덱스 키를 사용:
    selfhealing:runbook:approval:index:{runbook_id}:{namespace}
    """
    backend = self._get_backend()
    index_key = f"selfhealing:runbook:approval:index:{runbook_id}:{namespace}"
    execution_id = backend.get(index_key)
    if execution_id:
        request = self._load_approval_request(execution_id)
        if request and request.status == ApprovalDecisionType.WAITING:
            return request
    return None

def _save_approval_request(self, request: RunbookApprovalRequest) -> None:
    backend = self._get_backend()
    key = self.APPROVAL_REQUEST_KEY.format(execution_id=request.execution_id)
    backend.set(key, request.to_dict(), ttl=86400)  # 24시간 TTL

    # 보조 인덱스: runbook_id + namespace → execution_id
    index_key = (
        f"selfhealing:runbook:approval:index:"
        f"{request.runbook_id}:{request.namespace}"
    )
    backend.set(index_key, request.execution_id, ttl=86400)

def _cas_save_approval_request(
    self,
    request: RunbookApprovalRequest,
    expected_status: str,
) -> bool:
    """
    CAS (Compare-And-Set) 기반 승인 요청 상태 전환.

    코드 근거:
    - SESSION_CAS_SCRIPT (__init__.py L106-118) — version 기반 CAS
    - ATOMIC_TRANSITION_SCRIPT (atomic_transition.py L28-50) — 상태 기반 CAS

    승인 요청은 단방향 상태 전환(WAITING → 최종 상태)이므로
    version 카운터 대신 status 필드 기반 CAS로 충분하다.
    Lua 스크립트가 Redis 내부에서 원자적으로 실행되므로
    타이머 만료(Celery Beat)와 수동 거부(API)가 동시에 도달해도
    하나만 성공한다.

    Args:
        request: 새 상태가 설정된 요청 객체
        expected_status: CAS 조건 — 현재 상태가 이 값일 때만 저장

    Returns:
        True if CAS succeeded, False if status already changed
    """
    backend = self._get_backend()
    key = self.APPROVAL_REQUEST_KEY.format(
        execution_id=request.execution_id,
    )

    if hasattr(backend, "_client"):
        # Redis: Lua CAS
        result = backend._client.eval(
            APPROVAL_CAS_SCRIPT,
            1,
            backend._make_key(key),
            json.dumps(request.to_dict(), default=str),
            expected_status,
        )
        if result == 0:
            return False  # CAS 실패
        return True
    else:
        # InMemory: 단순 저장 (테스트 환경)
        backend.set(key, request.to_dict(), ttl=86400)
        return True

def _load_approval_request(
    self, execution_id: str,
) -> RunbookApprovalRequest | None:
    backend = self._get_backend()
    key = self.APPROVAL_REQUEST_KEY.format(execution_id=execution_id)
    data = backend.get(key)
    if data:
        return RunbookApprovalRequest.from_dict(data)
    return None
```

---

## 9. RunbookExecutor 통합 (275 연동)

275의 `execute_runbook()` 흐름에 승인 게이트를 삽입한다.

**설계 원칙**: Worker 블로킹 금지. 승인 대기가 필요한 경우
Worker를 즉시 반환하고, 승인 이벤트가 `resume_runbook_task`를 트리거한다.

코드 근거:
- `recovery_tasks.py` L404-410 — `apply_async(countdown=)` 이벤트 드리븐 체인
- 275 §13.1 `resume_execution()` — Lock 획득 + Stale Context + 버전 검증

### 9.1 초기 실행 진입 (`execute_runbook`)

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

    try:
        decision = approval_gate.evaluate_approval(runbook, ctx)
    except RunbookApprovalDuplicateError as e:
        # 동일 런북+네임스페이스에 이미 대기 중인 승인 요청 존재 (§8.1)
        ctx.status = RunbookExecutionStatus.CANCELLED
        ctx.abort_reason = str(e)
        self._save_context(ctx)
        return ctx

    if decision.decision_type == ApprovalDecisionType.BLOCKED:
        ctx.status = RunbookExecutionStatus.CANCELLED
        ctx.abort_reason = decision.block_message
        self._save_context(ctx)
        return ctx

    if decision.decision_type == ApprovalDecisionType.WAITING:
        # WAITING_APPROVAL 상태 영속화 → Worker 즉시 반환 (블로킹 없음)
        # approve_runbook() 또는 check_timer_approval()이
        # resume_runbook_task를 트리거하여 실행 재개 (§7.2, §14)
        ctx.status = RunbookExecutionStatus.WAITING_APPROVAL
        self._save_context(ctx)
        return ctx  # ← time.sleep 폴링 없이 즉시 반환

    # === AUTO_APPROVED (LOW) → 즉시 실행 ===
    return self._execute_after_approval(runbook, ctx)
```

### 9.2 승인 후 실행 재개 (`resume_runbook_task`)

승인 이벤트(수동 승인, 타이머 만료)가 트리거하는 Celery Task.

```python
# services/runbook/tasks.py

@shared_task(
    name="selfhealing.runbook.resume",
    queue="selfhealing_runbook",        # 전용 큐 분리
    max_retries=3,
    default_retry_delay=10,
)
def resume_runbook_task(execution_id: str) -> dict[str, Any]:
    """
    승인 완료 후 Runbook 실행 재개.

    코드 근거:
    - recovery_tasks.py L362-410 — Step 단위 이벤트 드리븐 체인, Worker 비차단
    - 275 §13.1 resume_execution() — 4가지 방어(Stale/Version/Resume Count/Lock)
    """
    executor = get_runbook_executor()
    ctx = executor._load_context(execution_id)

    if ctx is None or ctx.status != RunbookExecutionStatus.WAITING_APPROVAL:
        return {"status": "skipped", "reason": "invalid_state"}

    runbook = RunbookRegistry.get(ctx.runbook_id)

    result = executor._execute_after_approval(runbook, ctx)
    return {"status": result.status.value, "execution_id": execution_id}
```

### 9.3 승인 후 공통 실행 경로 (`_execute_after_approval`)

Governance Double-Check + Lock 획득 + Step 실행.

```python
def _execute_after_approval(
    self,
    runbook: Runbook,
    ctx: RunbookExecutionContext,
) -> RunbookExecutionContext:
    """
    승인 완료 후 실제 실행.

    AUTO_APPROVED(즉시)와 MANUALLY_APPROVED/TIMER_APPROVED(재개) 모두
    이 경로를 통과한다.
    """
    approval_gate = RunbookApprovalGate(
        notification_manager=self._notification,
        state_backend=self._backend,
    )

    # === Governance Double-Check (§15) ===
    # 승인 대기 중(수 분~수십 분) Kill Switch/Emergency/Error Budget 변경 방어
    gov_recheck = approval_gate.evaluate_governance(runbook, ctx.namespace)
    if not gov_recheck.allowed:
        ctx.status = RunbookExecutionStatus.CANCELLED
        ctx.abort_reason = (
            f"Governance re-check failed after approval: "
            f"{gov_recheck.block_reason.value} — {gov_recheck.block_message}"
        )
        self._save_context(ctx)

        # 승인자에게 취소 알림 (§17 타임아웃 알림 패턴 재사용)
        self._send_cancellation_notification(ctx, gov_recheck)
        return ctx

    # === Lock 획득 → 실행 ===
    acquired = self._recovery_lock.acquire(ctx.namespace, ctx.execution_id)
    if not acquired:
        raise RunbookLockConflictError(
            f"Namespace '{ctx.namespace}' is locked by another recovery/runbook"
        )

    try:
        return self._run(
            runbook, ctx.trigger_event, ctx.namespace,
            ctx.execution_id, ctx,
        )
    finally:
        self._recovery_lock.release(ctx.namespace, ctx.execution_id)
```

---

## 10. 설정

```python
# Pydantic BaseSettings, SELFHEALING_ 접두사

# --- 타이머 ---
SELFHEALING_RUNBOOK_APPROVAL_TIMER_SECONDS: int = 300
# MEDIUM 리스크 자동 승인 타이머 (기본 5분)

# --- 타임아웃 ---
SELFHEALING_RUNBOOK_APPROVAL_MAX_WAIT_SECONDS: int = 3600
# HIGH 리스크 최대 대기 시간 (기본 1시간, 0이면 무기한)
# 초과 시 BLOCKED + CRITICAL 알림 + DLQ 저장 (§17)

# --- 리마인더 (§16) ---
SELFHEALING_RUNBOOK_APPROVAL_REMINDER_INTERVALS_MINUTES: list[int] = [15, 30]
# 승인 대기 중 리마인더 발송 간격 (분)
# PendingRecoveryApprovalManager._reminder_intervals 패턴 참조

# --- Celery Beat 폴링 ---
SELFHEALING_RUNBOOK_APPROVAL_CHECK_INTERVAL_SECONDS: int = 30
# Celery Beat에서 check_timer_approval + check_and_send_reminders 호출 간격
# wait_for_approval 폴링을 대체 (§7.2 이벤트 드리븐 전환)

# --- Force Execute (§5.5) ---
SELFHEALING_RUNBOOK_FORCE_EXECUTE_AUDIT_REQUIRED: bool = True
# CRITICAL 런북 강제 실행 시 감사 로그 필수 여부
# governance.break_glass_audit_required 패턴 참조
```

---

## 11. 모듈 구조

```
packages/selfhealing-python/src/selfhealing/
└── services/
    └── runbook/
        ├── approval_gate.py      ← RunbookApprovalGate (이 문서)
        ├── execution_models.py   ← ApprovalDecision, ApprovalDecisionType,
        │                            RunbookApprovalRequest 추가
        ├── exceptions.py         ← ApprovalAlreadyDecidedError,
        │                            RunbookApprovalDuplicateError 추가
        └── ...
```

---

## 12. 기존 컴포넌트와의 관계 요약

| 기존 컴포넌트 | Approval Gate에서의 사용 |
|---|---|
| `GovernanceCheckMixin` | 상속. check_governance()로 Kill Switch/Emergency/Error Budget 체크 |
| `check_all_governance()` | 거버넌스 체크 함수 직접 호출. 승인 후 Double-Check(§15)에서 재호출 |
| `GovernanceCheckResult` | 거버넌스 체크 결과 타입 그대로 사용 |
| `BlockReason` | 차단 사유 enum 그대로 사용 |
| `ApprovalMixin` | approve_recovery() 승인 패턴 차용 (WAITING → APPROVED) |
| `PendingRecoveryApprovalManager` | create_request() 중복 방지 패턴 + 리마인더 체계 재사용 |
| `UnifiedNotificationManager` | 승인 요청/타임아웃/리마인더 알림 발송 |
| `NotificationCategory.APPROVAL` | 기존 카테고리 사용 (cooldown=0 유지) |
| `StateBackend` | 승인 요청 영속화 (Redis/InMemory) |
| `Break Glass` | governance settings.break_glass_enabled → 모든 거버넌스 우회 |
| `SESSION_CAS_SCRIPT` | 승인 상태 전환용 Lua CAS 패턴 원본 (§13) |
| `ATOMIC_TRANSITION_SCRIPT` | 상태 기반 CAS Lua 패턴 참조 (§13) |
| `recovery_tasks.py` | apply_async(countdown=) 이벤트 드리븐 체인 패턴 (§14) |
| `DLQService` | 승인 타임아웃 실패 건 DLQ 저장 + Fresh Start Replay (§17) |
| `resume_execution()` (275) | 승인 후 실행 재개 시 4가지 방어 적용 (§14) |

---

## 13. 동시성 제어 — Redis Lua CAS

### 13.1 문제

MEDIUM 리스크에서 타이머 만료(Celery Beat)와 운영자의 수동 거부(API 호출)가
동일 밀리초에 도달하면, 단순 `backend.set()`은 두 결정 모두 성공하여
상태 불일치가 발생한다.

### 13.2 해법: status 기반 Lua CAS

기존 코드베이스에 검증된 두 가지 CAS 패턴 중 **상태 기반 CAS**를 선택한다.

| 패턴 | 원본 | 특징 |
|------|------|------|
| version 기반 CAS | `SESSION_CAS_SCRIPT` (`__init__.py` L106-118) | version 카운터 증가 + JSON decode 비교 |
| 상태 기반 CAS | `ATOMIC_TRANSITION_SCRIPT` (`atomic_transition.py` L28-50) | expected level ≠ actual level 시 실패 |

**선택: 상태 기반 CAS** — 승인 요청은 단방향 상태 전환(WAITING → 최종 상태)이므로
version 카운터의 복잡성이 불필요하다. `atomic_transition.py`의
"현재 상태가 예상과 다르면 실패" 패턴이 정확히 맞는 시맨틱이다.

```python
# Redis Lua: 승인 요청 상태 기반 CAS
# 코드 근거: atomic_transition.py L28-50 ATOMIC_TRANSITION_SCRIPT
APPROVAL_CAS_SCRIPT = """
local current = redis.call("GET", KEYS[1])
if current == false then
    return 0
end
local data = cjson.decode(current)
if data["status"] == ARGV[2] then
    redis.call("SET", KEYS[1], ARGV[1])
    return 1
else
    return 0
end
"""
-- KEYS[1]: 승인 요청 키
-- ARGV[1]: 새 데이터 (JSON)
-- ARGV[2]: expected status (예: "waiting")
-- return 1: CAS 성공, 0: 상태 불일치 (이미 다른 결정)
```

### 13.3 CAS Fail-Open 정책

`_session_persistence.py` L61-67의 **Fail-Open** 패턴을 따른다:
- Redis Lua 호출 자체가 실패(네트워크 오류 등)하면 기존 `backend.set()` 폴백
- CAS **로직** 실패(status 불일치)는 정상적인 동시성 방어이므로 폴백하지 않음

```python
# _cas_save_approval_request() 내부 — §8.1 참조
if hasattr(backend, "_client"):
    try:
        result = backend._client.eval(
            APPROVAL_CAS_SCRIPT, 1, key, data, expected_status,
        )
        if result == 0:
            return False  # 상태 불일치 — 정상적 CAS 실패
        return True
    except Exception:
        # Lua 호출 자체 실패 → Fail-Open 폴백
        backend.set(key, request.to_dict(), ttl=86400)
        return True
```

### 13.4 CAS 실패 시 UX — HTTP 409 Conflict

운영자가 슬랙 알림의 '승인' 버튼을 눌렀으나 간발의 차이로 타이머가
이미 `TIMER_APPROVED`로 확정한 경우, API는 `ApprovalAlreadyDecidedError`를
발생시킨다 (§5.3).

DRF ViewSet에서의 매핑:

```python
# API 레이어 예시
try:
    decision = gate.approve_runbook(execution_id, approved_by)
    return Response(decision.to_dict(), status=200)
except ApprovalAlreadyDecidedError as e:
    return Response(
        {
            "error": "approval_already_decided",
            "message": str(e),
            "current_status": e.current_status.value,
        },
        status=409,
    )
```

---

## 14. 이벤트 드리븐 실행 재개

### 14.1 변경 사유

기존 §7.2의 `wait_for_approval()`은 `while True: time.sleep(10)`으로
Celery Worker를 최대 3600초 블로킹한다. 이는 프로젝트의 확립된 패턴과 불일치한다:

| 컴포넌트 | 대기 방식 | Worker 상태 |
|----------|-----------|------------|
| `recovery_tasks.py` L404-410 | `apply_async(countdown=)` | **즉시 해방** |
| `BackpressureTaskMixin` L41-75 | `retry(countdown=)` | **즉시 해방** |
| 275 `time.sleep(step.wait_after_seconds)` | `time.sleep()` | 블로킹 (수 초~수십 초, 허용 범위) |
| **기존 §7.2 wait_for_approval()** | `time.sleep(10)` × 360회 | **최대 3600초 블로킹** |

HIGH 리스크 런북 수십 개가 동시에 승인 대기에 들어가면
`selfhealing_runbook` 큐의 Worker Pool이 고갈된다.

### 14.2 재개 흐름 (§9와 연동)

```
evaluate_approval() → WAITING 결정
│
├─ ctx.status = WAITING_APPROVAL 영속화
├─ Worker 즉시 반환 (return ctx)
│
├─ [이벤트 A] approve_runbook()
│    └─ CAS(WAITING → MANUALLY_APPROVED) 성공
│         └─ _trigger_resume() → resume_runbook_task.apply_async()
│              └─ _execute_after_approval() → Governance Double-Check → Lock → Run
│
├─ [이벤트 B] check_timer_approval() (Celery Beat)
│    └─ CAS(WAITING → TIMER_APPROVED) 성공
│         └─ _trigger_resume() → resume_runbook_task.apply_async()
│              └─ _execute_after_approval() → Governance Double-Check → Lock → Run
│
├─ [이벤트 C] reject_runbook()
│    └─ CAS(WAITING → REJECTED) 성공
│         └─ 재개 없음 (실행 취소)
│
└─ [이벤트 D] check_approval_timeout() (Celery Beat, §17)
     └─ 타임아웃 → BLOCKED + 알림 + DLQ
```

### 14.3 Celery Beat 등록

```python
# Celery Beat Schedule
{
    "runbook-approval-check": {
        "task": "selfhealing.runbook.check_pending_approvals",
        "schedule": SELFHEALING_RUNBOOK_APPROVAL_CHECK_INTERVAL_SECONDS,
        "options": {"queue": "selfhealing_runbook"},
    },
}

@shared_task(name="selfhealing.runbook.check_pending_approvals")
def check_pending_approvals_task() -> dict[str, Any]:
    """
    주기적 타이머/리마인더/타임아웃 체크.

    코드 근거: recovery_tasks.py L591 — manager.check_and_send_reminders()
    """
    gate = get_runbook_approval_gate()

    # 1. MEDIUM 타이머 만료 체크 → 자동 승인 + resume
    timer_results = gate.check_all_timer_approvals()

    # 2. 리마인더 발송 (§16)
    reminded = gate.check_and_send_reminders()

    # 3. 타임아웃 체크 (§17)
    timed_out = gate.check_approval_timeouts()

    return {
        "timer_approved": len(timer_results),
        "reminders_sent": len(reminded),
        "timed_out": len(timed_out),
    }
```

### 14.4 전용 큐 분리

기존 `recovery_tasks.py` L735-770에서 큐를 분리하는 패턴을 따른다:

```python
# 기존 큐 분리 패턴
queue="selfhealing_recovery"       # 복구 전용
queue="selfhealing_notifications"  # 알림 전용
queue="selfhealing_maintenance"    # 정리 전용

# 추가
queue="selfhealing_runbook"        # 런북 전용 (신규)
```

런북 실행이 복구 태스크를 starvation시키거나,
반대로 복구 폭주 시 런북 실행이 지연되는 것을 방지한다.

---

## 15. Governance Double-Check (Staleness 방어)

### 15.1 문제

`evaluate_approval()`에서 거버넌스 체크를 통과한 후
수동 승인을 기다리는 동안 40분이 경과했다고 가정한다.
그 사이 누군가 Kill Switch를 켰거나 Error Budget이 완전히 소진되었을 수 있다.

기존 `approve_recovery()`(`_approval.py` L82-139)도 동일한 갭이 있다:
```python
def approve_recovery(self, namespace, approved_by):
    # ← 여기에 check_governance()가 없음
    session.status = RecoveryStatus.COMPLETED  # 바로 완료
```

프로젝트 전체에 "승인 → 실행 사이 거버넌스 재검증" 패턴은 없으나,
Runbook 특성상 승인 대기 시간이 수십 분에 달하므로 276에서 신규 도입한다.

### 15.2 적용 위치

§9.3 `_execute_after_approval()` 내부에서 Lock 획득 **직전**에 수행:

```
approve_runbook() / check_timer_approval()
  └─ resume_runbook_task
       └─ _execute_after_approval()
            ├─ Governance Double-Check ← 여기
            ├─ Lock 획득
            └─ _run()
```

### 15.3 캐시 무효화 주의

`check_all_governance()`는 내부적으로 TTL 캐시(기본 30초)를 사용한다.
승인 후 즉시 재검증하면 캐시된 (허용) 결과를 받을 수 있다.

해결: `evaluate_governance()` 호출 시 캐시를 강제 갱신한다.

```python
# _execute_after_approval() 내부
from selfhealing.services.governance.checks import invalidate_governance_cache

invalidate_governance_cache()  # 캐시 무효화
gov_recheck = approval_gate.evaluate_governance(runbook, ctx.namespace)
```

---

## 16. 승인 대기 리마인더

### 16.1 기존 인프라 재사용

`PendingRecoveryApprovalManager`에 리마인더 시스템이 완전히 구현되어 있다:

- `_reminder_intervals = [15, 30, 60]` — 경과 시간별 단계적 리마인더
  (`pending_recovery_approval.py` L232)
- `_should_send_reminder()` — elapsed_minutes vs intervals 비교
  (`pending_recovery_approval.py` L610-628)
- `check_and_send_reminders()` — Celery Beat에서 주기 호출
  (`pending_recovery_approval.py` L483-515)
- 알림 메시지 포맷 — 긴급도 이모지, 경과/만료 시간, 승인 버튼
  (`recovery_notifications.py` L381-420)

이 패턴을 그대로 차용한다:

```python
def check_and_send_reminders(self) -> list[RunbookApprovalRequest]:
    """
    대기 중인 승인 요청에 리마인더 발송.

    코드 근거: PendingRecoveryApprovalManager.check_and_send_reminders()
    — pending_recovery_approval.py L483-515
    """
    reminded = []
    now = datetime.now(timezone.utc)

    for request in self._list_waiting_requests():
        if self._should_send_reminder(request, now):
            request.reminder_count = (request.reminder_count or 0) + 1
            request.last_reminder_at = now.isoformat()
            self._save_approval_request(request)

            self._send_reminder_notification(request)
            reminded.append(request)

    return reminded

def _should_send_reminder(
    self,
    request: RunbookApprovalRequest,
    now: datetime,
) -> bool:
    """
    리마인더 발송 여부 판단.

    코드 근거: PendingRecoveryApprovalManager._should_send_reminder()
    — pending_recovery_approval.py L610-628
    """
    if not request.created_at:
        return False

    created = datetime.fromisoformat(request.created_at)
    elapsed_minutes = (now - created).total_seconds() / 60.0
    reminder_count = request.reminder_count or 0

    intervals = settings.RUNBOOK_APPROVAL_REMINDER_INTERVALS_MINUTES
    for idx, interval in enumerate(intervals):
        if reminder_count <= idx and elapsed_minutes >= interval:
            return True

    return False
```

### 16.2 RunbookApprovalRequest 확장 필드

```python
@dataclass
class RunbookApprovalRequest:
    # ... 기존 필드 ...
    reminder_count: int = 0                # 리마인더 발송 횟수
    last_reminder_at: str | None = None    # 마지막 리마인더 시각
```

---

## 17. HIGH 리스크 타임아웃 처리

### 17.1 정책

HIGH 리스크 런북이 `SELFHEALING_RUNBOOK_APPROVAL_MAX_WAIT_SECONDS`를 초과하면:
1. **BLOCKED 상태 전환** (자동 취소)
2. **CRITICAL 알림 발송** — "자동 치유 실패" 사실을 운영자에게 명시적 에스컬레이션
3. **DLQ 저장** — 사후 분석 및 재시도 가능

코드 근거:
- `PendingRecoveryApprovalManager.check_and_send_reminders()` — 만료 시
  `request.status = EXPIRED` + `_send_notification(request, "expired")`
  (`pending_recovery_approval.py` L498-502)
- `_store_failure_to_dlq()` — 3단계 Fallback (LMDB → JSONL → stderr) 무손실 보장
  (`_session_persistence.py` L189-260)

```python
def check_approval_timeouts(self) -> list[RunbookApprovalRequest]:
    """
    타임아웃 체크. Celery Beat에서 주기적으로 호출.
    """
    timed_out = []
    now = datetime.now(timezone.utc)
    max_wait = settings.RUNBOOK_APPROVAL_MAX_WAIT_SECONDS
    if max_wait <= 0:
        return []  # 0이면 무기한 대기

    for request in self._list_waiting_requests():
        if not request.created_at:
            continue

        created = datetime.fromisoformat(request.created_at)
        elapsed = (now - created).total_seconds()

        if elapsed > max_wait:
            # 1. 상태 전환
            request.status = ApprovalDecisionType.BLOCKED
            request.decided_by = "system:timeout"
            request.decided_at = now.isoformat()
            self._cas_save_approval_request(
                request, expected_status="waiting",
            )

            # 2. CRITICAL 알림
            self._send_timeout_notification(request, elapsed)

            # 3. DLQ 저장
            self._store_timeout_to_dlq(request, elapsed)

            timed_out.append(request)

    return timed_out
```

### 17.2 DLQ Replay 시 Fresh Start 강제

승인 대기 중 타임아웃이 발생했다는 것은 실행 환경(메트릭, 거버넌스 상태)이
이미 변했을 가능성이 매우 높다. 따라서 DLQ에서 Replay할 때
이전 승인 단계부터 재개하는 것이 아니라 **패턴 매칭 및 거버넌스 체크부터
완전히 새로 시작(Fresh Start)**해야 한다.

코드 근거:
- 275 §13.1 `resume_execution()` — Stale Context 거부 방어가 있으나
  이것은 resume의 진입 방어이지 DLQ Replay 진입점 제한이 아님

```python
def _store_timeout_to_dlq(
    self,
    request: RunbookApprovalRequest,
    elapsed_seconds: float,
) -> None:
    """
    타임아웃된 승인 요청을 DLQ에 저장.

    코드 근거: _store_failure_to_dlq() — _session_persistence.py L189-260
    - 3단계 Fallback (LMDB → JSONL → stderr) 무손실 보장
    - recommended_action 동적 분기
    """
    from selfhealing.services.dlq import store_to_dlq

    store_to_dlq(
        entry_type="runbook_approval_timeout",
        source="runbook_approval_gate",
        data={
            "execution_id": request.execution_id,
            "runbook_id": request.runbook_id,
            "namespace": request.namespace,
            "risk_level": request.risk_level.value,
            "elapsed_seconds": elapsed_seconds,
            "runbook_summary": request.runbook_summary,
        },
        recommended_action="fresh_start_required",
        metadata={
            "replay_restriction": "must_start_from_pattern_matching",
            "reason": (
                "Approval timed out — execution context is stale. "
                "Replay must go through full pipeline from the beginning."
            ),
        },
    )
```

---

## 18. CRITICAL 런북 강제 실행 (Force Execute)

§5.5에서 정의한 `force_execute_runbook()` API의 운영 가이드.

### 18.1 Break Glass vs Force Execute

| 시나리오 | 사용할 메커니즘 |
|----------|----------------|
| 전체 시스템 긴급 복구 — 모든 안전장치 해제 필요 | Break Glass (`SELFHEALING_GOVERNANCE_BREAK_GLASS_ENABLED=true`) |
| CRITICAL 런북 1건 실행 — 거버넌스는 유지 | Force Execute (`force_execute_runbook()`) |
| CRITICAL 런북 실행 + Kill Switch가 켜진 상태 | Break Glass **후** Force Execute |

### 18.2 권한 인가 (Authorization)

`force_execute_runbook()` API 엔드포인트에는 일반 승인 권한(`role: operator`)과
별도로 **강화된 권한 검증**이 필수적으로 결합되어야 한다.

```python
# API 레이어 — DRF ViewSet 예시
class RunbookForceExecuteView(APIView):
    permission_classes = [IsAuthenticated, HasRole("emergency_responder")]

    def post(self, request, execution_id):
        gate = get_runbook_approval_gate()
        # force_executed_by는 인증된 사용자의 ID
        # justification은 비어있으면 400 Bad Request
        decision = gate.force_execute_runbook(
            runbook=runbook,
            ctx=ctx,
            force_executed_by=request.user.id,
            justification=request.data["justification"],
        )
        return Response(decision.to_dict())
```

---

## 19. 알림 중복 발송 제어

### 19.1 설계 선택: 요청 생성 단계 차단 vs 알림 레이어 Throttling

**선택: 요청 생성 단계 차단 (§8.1)**

| 대안 | 장점 | 단점 |
|------|------|------|
| **알림 레이어 Throttling** (dedup_key 변경 + cooldown) | 구현 간단 | APPROVAL cooldown=0 변경 시 기존 Recovery 승인 영향. 정당한 요청도 suppression 위험 |
| **요청 생성 차단** (runbook_id+namespace 중복 검사) | 기존 인프라 불변. 의미적으로 정확 | 보조 인덱스 키 관리 필요 |

APPROVAL 카테고리의 cooldown이 0인 이유는 "승인 요청은 한 건도 누락되면
안 된다"는 설계 의도이다 (`routing.py` L68). 이를 변경하면 기존 Recovery
승인 알림에도 영향을 미치므로, **알림 레이어는 건드리지 않는다**.

대신 `PendingRecoveryApprovalManager.create_request()`의 중복 방지 패턴
(`pending_recovery_approval.py` L270-275)을 차용하여 **승인 요청 생성 단계에서
동일 runbook_id + namespace의 WAITING 요청이 있으면 차단**한다 (§8.1).

결과:
- 장애 스톰 중 동일 런북이 100번 트리거되어도 승인 요청+알림은 1건만 생성
- APPROVAL cooldown=0 유지 → 기존 Recovery 승인 알림 영향 없음
- 정당한 별도 execution_id의 요청은 기존 WAITING 요청이 결정된 후 정상 생성

### 19.2 보조 방어선: 리마인더 배칭 (고도화 항목)

장애 스톰 시 수십 개의 서로 다른 런북이 동시에 승인 대기에 들어가면
15분 시점에 수십 개의 리마인더가 동시 발송될 수 있다 (Thundering Herd).

고도화 시 `check_and_send_reminders()`에서 동일 네임스페이스의
대기 건들을 하나의 Digest 메시지로 묶거나, 발송 간에 Jitter를 추가한다:

```python
# 고도화 예시 — Jitter
import random
for request in waiting_requests:
    if self._should_send_reminder(request, now):
        # 0~30초 난수 지연으로 동시 발송 분산
        delay = random.uniform(0, 30)
        send_reminder_task.apply_async(
            args=[request.execution_id],
            countdown=delay,
        )
```
