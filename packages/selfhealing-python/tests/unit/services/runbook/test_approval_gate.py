"""
RunbookApprovalGate 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.approval_gate

계약 검증 클래스 (Test*Contract):
    - TestApprovalDecisionTypeContract     — Enum 값 설계 계약
    - TestApprovalDecisionContract         — 기본값 설계 계약
    - TestRunbookApprovalRequestContract   — 기본값 설계 계약
    - TestApprovalGateRoutingContract      — 라우팅 매트릭스 설계 계약
    - TestApprovalGateConstantsContract    — 모듈 상수 설계 계약

동작 검증 클래스 (Test*Behavior):
    - TestApprovalDecisionBehavior          — is_approved, to_dict
    - TestRunbookApprovalRequestSerializationBehavior — 직렬화 왕복
    - TestApprovalGateGovernanceBehavior    — 거버넌스 차단 동작
    - TestApprovalGateApproveRejectBehavior — 수동 승인/거부 CAS 동작
    - TestApprovalGateForceExecuteBehavior  — CRITICAL 강제 실행 동작
    - TestApprovalGateTimerBehavior         — MEDIUM 타이머 자동 승인
    - TestApprovalGateReminderBehavior      — 리마인더 판단 로직
    - TestApprovalGateTimeoutBehavior       — HIGH 타임아웃 처리
    - TestApprovalGateDuplicateBehavior     — 중복 승인 요청 차단
    - TestApprovalGateNotificationBehavior  — 알림 발송 부수효과
    - TestApprovalGateSingletonBehavior     — 싱글톤 캐싱/리셋
    - TestApprovalAlreadyDecidedErrorBehavior — 예외 속성 검증
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.governance.checks import BlockReason, GovernanceCheckResult
from selfhealing.services.runbook.approval_gate import (
    APPROVAL_CAS_SCRIPT,
    APPROVAL_REQUEST_KEY,
    APPROVAL_REQUEST_TTL,
    DEFAULT_APPROVAL_MAX_WAIT_SECONDS,
    DEFAULT_APPROVAL_REMINDER_INTERVALS_MINUTES,
    DEFAULT_APPROVAL_TIMER_SECONDS,
    RunbookApprovalGate,
    get_runbook_approval_gate,
    reset_runbook_approval_gate,
)
from selfhealing.services.runbook.exceptions import (
    ApprovalAlreadyDecidedError,
    RunbookApprovalDuplicateError,
    RunbookApprovalError,
)
from selfhealing.services.runbook.execution_models import (
    ApprovalDecision,
    ApprovalDecisionType,
    RunbookApprovalRequest,
    RunbookExecutionContext,
)
from selfhealing.services.runbook.models import PatternCondition
from selfhealing.services.runbook.runbook_registry import (
    Runbook,
    RunbookStep,
    RiskLevel,
)


# =============================================================================
# 테스트 헬퍼
# =============================================================================


def _make_runbook(
    runbook_id: str = "rb-test",
    risk_level: RiskLevel = RiskLevel.LOW,
    name: str = "Test Runbook",
    steps: list[RunbookStep] | None = None,
) -> Runbook:
    """테스트용 Runbook 생성."""
    return Runbook(
        id=runbook_id,
        name=name,
        description="테스트용 런북",
        trigger_condition=PatternCondition(),
        steps=steps
        or [
            RunbookStep(name="s1", action="action.test", order=0, params={}),
        ],
        risk_level=risk_level,
    )


def _make_ctx(
    execution_id: str = "exec-001",
    namespace: str = "default",
    runbook_id: str = "rb-test",
) -> RunbookExecutionContext:
    """테스트용 실행 컨텍스트 생성."""
    return RunbookExecutionContext(
        execution_id=execution_id,
        runbook_id=runbook_id,
        namespace=namespace,
        trigger_event={"event_type": "test"},
    )


def _make_gate(
    governance_allowed: bool = True,
    notification_manager: MagicMock | None = None,
    backend: MagicMock | None = None,
) -> RunbookApprovalGate:
    """Mock 의존성이 주입된 RunbookApprovalGate 생성."""
    gate = RunbookApprovalGate(
        notification_manager=notification_manager,
        state_backend=backend or _make_memory_backend(),
    )

    # GovernanceCheckMixin.check_governance()를 모킹
    if governance_allowed:
        gov_result = GovernanceCheckResult.allowed_result()
    else:
        gov_result = GovernanceCheckResult(
            allowed=False,
            block_reason=BlockReason.KILL_SWITCH,
            block_message="Kill Switch is active",
        )
    gate.check_governance = MagicMock(return_value=gov_result)  # type: ignore[method-assign]

    return gate


def _make_memory_backend() -> MagicMock:
    """InMemory StateBackend Mock 생성. dict 기반 저장소."""
    store: dict[str, dict] = {}
    backend = MagicMock()

    def mock_get(key, default=None):
        return store.get(key, default)

    def mock_set(key, value, ttl_seconds=None):
        store[key] = value

    def mock_get_all(pattern="*"):
        if pattern == "*":
            return dict(store)
        prefix = pattern.replace("*", "")
        return {k: v for k, v in store.items() if prefix in k}

    backend.get = MagicMock(side_effect=mock_get)
    backend.set = MagicMock(side_effect=mock_set)
    backend.get_all = MagicMock(side_effect=mock_get_all)
    backend._store = store  # 테스트 내부에서 직접 접근용

    # Redis CAS를 사용하지 않도록 _client 속성 없음
    if hasattr(backend, "_client"):
        del backend._client

    return backend


# =============================================================================
# 계약 검증 — ApprovalDecisionType Enum 값
# =============================================================================


class TestApprovalDecisionTypeContract:
    """승인 결정 유형 Enum 설계 계약값 검증."""

    def test_decision_type_values_match_design_document(self):
        """§4.1 설계 계약: 6가지 결정 유형이 명시된 문자열과 일치해야 한다."""
        assert ApprovalDecisionType.AUTO_APPROVED.value == "auto_approved"
        assert ApprovalDecisionType.TIMER_APPROVED.value == "timer_approved"
        assert ApprovalDecisionType.MANUALLY_APPROVED.value == "manually_approved"
        assert ApprovalDecisionType.REJECTED.value == "rejected"
        assert ApprovalDecisionType.BLOCKED.value == "blocked"
        assert ApprovalDecisionType.WAITING.value == "waiting"

    def test_decision_type_count_matches_design_document(self):
        """§4.1 설계 계약: 정확히 6개 결정 유형만 존재해야 한다."""
        assert len(ApprovalDecisionType) == 6

    def test_decision_type_is_str_subclass(self):
        """str 서브클래스이므로 JSON 직렬화 시 str로 처리된다."""
        assert isinstance(ApprovalDecisionType.AUTO_APPROVED, str)


# =============================================================================
# 계약 검증 — ApprovalDecision 기본값
# =============================================================================


class TestApprovalDecisionContract:
    """ApprovalDecision 기본값 설계 계약 검증."""

    def test_approved_by_default_is_none(self):
        """§4.1 설계 계약: approved_by 기본값은 None."""
        d = ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=RiskLevel.LOW,
        )
        assert d.approved_by is None

    def test_block_reason_default_is_none(self):
        """§4.1 설계 계약: block_reason 기본값은 None."""
        d = ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=RiskLevel.LOW,
        )
        assert d.block_reason is None

    def test_block_message_default_is_empty_string(self):
        """§4.1 설계 계약: block_message 기본값은 빈 문자열."""
        d = ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=RiskLevel.LOW,
        )
        assert d.block_message == ""


# =============================================================================
# 계약 검증 — RunbookApprovalRequest 기본값
# =============================================================================


class TestRunbookApprovalRequestContract:
    """RunbookApprovalRequest 기본값 설계 계약 검증."""

    def test_status_default_is_waiting(self):
        """§4.2 설계 계약: status 기본값은 WAITING."""
        r = RunbookApprovalRequest(
            request_id="req-1",
            execution_id="exec-1",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.MEDIUM,
        )
        assert r.status == ApprovalDecisionType.WAITING

    def test_reminder_count_default_is_zero(self):
        """§16.2 설계 계약: reminder_count 기본값은 0."""
        r = RunbookApprovalRequest(
            request_id="req-1",
            execution_id="exec-1",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.MEDIUM,
        )
        assert r.reminder_count == 0

    def test_runbook_summary_default_is_empty_dict(self):
        """§4.2 설계 계약: runbook_summary 기본값은 빈 dict."""
        r = RunbookApprovalRequest(
            request_id="req-1",
            execution_id="exec-1",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.MEDIUM,
        )
        assert r.runbook_summary == {}


# =============================================================================
# 계약 검증 — 라우팅 매트릭스
# =============================================================================


class TestApprovalGateRoutingContract:
    """RiskLevel별 승인 라우팅 매트릭스 설계 계약 검증."""

    def test_low_risk_auto_approved(self):
        """§3.1 설계 계약: LOW → AUTO_APPROVED."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decision_type == ApprovalDecisionType.AUTO_APPROVED
        assert decision.risk_level == RiskLevel.LOW

    def test_medium_risk_waiting(self):
        """§3.1 설계 계약: MEDIUM → WAITING (타이머 대기)."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decision_type == ApprovalDecisionType.WAITING
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_high_risk_waiting(self):
        """§3.1 설계 계약: HIGH → WAITING (수동 승인 대기)."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decision_type == ApprovalDecisionType.WAITING
        assert decision.risk_level == RiskLevel.HIGH

    def test_critical_risk_blocked(self):
        """§3.1 설계 계약: CRITICAL → BLOCKED."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decision_type == ApprovalDecisionType.BLOCKED
        assert decision.risk_level == RiskLevel.CRITICAL

    def test_critical_blocked_message_contains_runbook_id(self):
        """§3.1 설계 계약: CRITICAL 차단 메시지에 런북 ID가 포함되어야 한다."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(runbook_id="rb-critical-test", risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert "rb-critical-test" in decision.block_message


# =============================================================================
# 계약 검증 — 모듈 상수
# =============================================================================


class TestApprovalGateConstantsContract:
    """모듈 상수 설계 계약 검증."""

    def test_default_timer_seconds(self):
        """§10 설계 계약: 기본 타이머 300초."""
        assert DEFAULT_APPROVAL_TIMER_SECONDS == 300

    def test_default_max_wait_seconds(self):
        """§10 설계 계약: 기본 최대 대기 3600초."""
        assert DEFAULT_APPROVAL_MAX_WAIT_SECONDS == 3600

    def test_default_reminder_intervals_minutes(self):
        """§10 설계 계약: 기본 리마인더 간격 [15, 30]."""
        assert DEFAULT_APPROVAL_REMINDER_INTERVALS_MINUTES == [15, 30]

    def test_approval_request_ttl(self):
        """§8.1 설계 계약: TTL은 24시간(86400초)."""
        assert APPROVAL_REQUEST_TTL == 86400

    def test_approval_request_key_template(self):
        """§8 설계 계약: 키 템플릿에 execution_id 플레이스홀더 포함."""
        assert "{execution_id}" in APPROVAL_REQUEST_KEY


# =============================================================================
# 동작 검증 — ApprovalDecision
# =============================================================================


class TestApprovalDecisionBehavior:
    """ApprovalDecision 동작 검증."""

    @pytest.mark.parametrize(
        "decision_type,expected",
        [
            (ApprovalDecisionType.AUTO_APPROVED, True),
            (ApprovalDecisionType.TIMER_APPROVED, True),
            (ApprovalDecisionType.MANUALLY_APPROVED, True),
            (ApprovalDecisionType.REJECTED, False),
            (ApprovalDecisionType.BLOCKED, False),
            (ApprovalDecisionType.WAITING, False),
        ],
    )
    def test_is_approved_returns_correct_value(self, decision_type, expected):
        """is_approved는 승인 상태 3가지에서만 True를 반환해야 한다."""
        d = ApprovalDecision(decision_type=decision_type, risk_level=RiskLevel.LOW)
        assert d.is_approved is expected

    def test_to_dict_contains_required_keys(self):
        """to_dict()는 필수 키를 포함해야 한다."""
        d = ApprovalDecision(
            decision_type=ApprovalDecisionType.AUTO_APPROVED,
            risk_level=RiskLevel.LOW,
            approved_by="system:auto",
        )
        result = d.to_dict()

        assert "decision_type" in result
        assert "risk_level" in result
        assert "approved_by" in result
        assert "is_approved" in result
        assert result["decision_type"] == "auto_approved"
        assert result["is_approved"] is True


# =============================================================================
# 동작 검증 — RunbookApprovalRequest 직렬화
# =============================================================================


class TestRunbookApprovalRequestSerializationBehavior:
    """RunbookApprovalRequest 직렬화 왕복 검증."""

    def test_round_trip_preserves_all_fields(self):
        """to_dict → from_dict 왕복 시 모든 필드가 보존된다."""
        # Given
        original = RunbookApprovalRequest(
            request_id="req-123",
            execution_id="exec-456",
            runbook_id="rb-test",
            namespace="production",
            risk_level=RiskLevel.MEDIUM,
            status=ApprovalDecisionType.WAITING,
            created_at="2026-02-26T10:00:00+00:00",
            expires_at="2026-02-26T10:05:00+00:00",
            decided_at=None,
            decided_by=None,
            runbook_summary={"name": "Test", "step_count": 3},
            reminder_count=2,
            last_reminder_at="2026-02-26T10:15:00+00:00",
        )

        # When
        serialized = original.to_dict()
        restored = RunbookApprovalRequest.from_dict(serialized)

        # Then
        assert restored.request_id == original.request_id
        assert restored.execution_id == original.execution_id
        assert restored.runbook_id == original.runbook_id
        assert restored.namespace == original.namespace
        assert restored.risk_level == original.risk_level
        assert restored.status == original.status
        assert restored.created_at == original.created_at
        assert restored.expires_at == original.expires_at
        assert restored.runbook_summary == original.runbook_summary
        assert restored.reminder_count == original.reminder_count
        assert restored.last_reminder_at == original.last_reminder_at

    def test_serialized_keys_match_design(self):
        """직렬화된 딕셔너리의 키가 설계와 일치한다."""
        request = RunbookApprovalRequest(
            request_id="req-1",
            execution_id="exec-1",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.LOW,
        )
        data = request.to_dict()

        expected_keys = {
            "request_id",
            "execution_id",
            "runbook_id",
            "namespace",
            "risk_level",
            "status",
            "created_at",
            "expires_at",
            "decided_at",
            "decided_by",
            "runbook_summary",
            "reminder_count",
            "last_reminder_at",
        }
        assert set(data.keys()) == expected_keys


# =============================================================================
# 동작 검증 — 거버넌스 차단
# =============================================================================


class TestApprovalGateGovernanceBehavior:
    """거버넌스 차단 동작 검증."""

    def test_governance_blocked_returns_blocked_decision(self):
        """거버넌스 체크 실패 시 BLOCKED 결정을 반환해야 한다."""
        gate = _make_gate(governance_allowed=False)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decision_type == ApprovalDecisionType.BLOCKED
        assert decision.block_reason == BlockReason.KILL_SWITCH

    def test_governance_blocked_includes_governance_result(self):
        """거버넌스 차단 시 governance_result가 설정되어야 한다."""
        gate = _make_gate(governance_allowed=False)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.governance_result is not None
        assert decision.governance_result.allowed is False

    def test_governance_blocks_even_low_risk(self):
        """LOW 위험도라도 거버넌스 차단 시 BLOCKED 결정."""
        gate = _make_gate(governance_allowed=False)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decision_type == ApprovalDecisionType.BLOCKED

    def test_evaluate_governance_calls_check_governance(self):
        """evaluate_governance()가 check_governance()를 올바른 인자로 호출."""
        gate = _make_gate()
        runbook = _make_runbook(runbook_id="rb-gov-test")

        gate.evaluate_governance(runbook, "default")

        gate.check_governance.assert_called_once_with(
            check_kill_switch=True,
            check_emergency=True,
            check_error_budget=True,
            operation_name="runbook:rb-gov-test",
            audit_on_block=True,
        )


# =============================================================================
# 동작 검증 — 수동 승인/거부 CAS
# =============================================================================


class TestApprovalGateApproveRejectBehavior:
    """수동 승인/거부 CAS 동작 검증."""

    def test_approve_runbook_transitions_to_manually_approved(self):
        """수동 승인 시 WAITING → MANUALLY_APPROVED 전환."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-approve")

        # 승인 요청 수동 생성 (evaluate_approval 대신)
        gate._create_approval_request(runbook, ctx, with_timer=False)

        # When
        with patch.object(gate, "_trigger_resume"):
            decision = gate.approve_runbook("exec-approve", approved_by="admin@test.com")

        # Then
        assert decision.decision_type == ApprovalDecisionType.MANUALLY_APPROVED
        assert decision.approved_by == "admin@test.com"

    def test_reject_runbook_transitions_to_rejected(self):
        """수동 거부 시 WAITING → REJECTED 전환."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-reject")

        gate._create_approval_request(runbook, ctx, with_timer=False)

        # When
        decision = gate.reject_runbook("exec-reject", rejected_by="admin@test.com", reason="too risky")

        # Then
        assert decision.decision_type == ApprovalDecisionType.REJECTED
        assert decision.block_message == "too risky"

    def test_approve_nonexistent_request_raises_error(self):
        """존재하지 않는 execution_id로 승인 시 RunbookApprovalError 발생."""
        gate = _make_gate()

        with pytest.raises(RunbookApprovalError, match="not found"):
            gate.approve_runbook("nonexistent-id", "admin")

    def test_reject_nonexistent_request_raises_error(self):
        """존재하지 않는 execution_id로 거부 시 RunbookApprovalError 발생."""
        gate = _make_gate()

        with pytest.raises(RunbookApprovalError, match="not found"):
            gate.reject_runbook("nonexistent-id", "admin")

    def test_approve_already_approved_raises_already_decided(self):
        """이미 승인된 요청에 대한 재승인 시 ApprovalAlreadyDecidedError 발생."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-double-approve")
        gate._create_approval_request(runbook, ctx, with_timer=False)

        # 첫 번째 승인
        with patch.object(gate, "_trigger_resume"):
            gate.approve_runbook("exec-double-approve", "admin1")

        # When / Then — 두 번째 승인 시도
        with pytest.raises(ApprovalAlreadyDecidedError) as exc_info:
            gate.approve_runbook("exec-double-approve", "admin2")

        assert exc_info.value.execution_id == "exec-double-approve"
        assert exc_info.value.current_status == ApprovalDecisionType.MANUALLY_APPROVED

    def test_reject_already_rejected_raises_already_decided(self):
        """이미 거부된 요청에 대한 재거부 시 ApprovalAlreadyDecidedError 발생."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-double-reject")
        gate._create_approval_request(runbook, ctx, with_timer=False)

        gate.reject_runbook("exec-double-reject", "admin1")

        # When / Then
        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.reject_runbook("exec-double-reject", "admin2")

    def test_approve_triggers_resume(self):
        """승인 성공 시 _trigger_resume()이 호출되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-resume-trigger")
        gate._create_approval_request(runbook, ctx, with_timer=False)

        # When
        with patch.object(gate, "_trigger_resume") as mock_resume:
            gate.approve_runbook("exec-resume-trigger", "admin")

        # Then
        mock_resume.assert_called_once_with("exec-resume-trigger")


# =============================================================================
# 동작 검증 — CRITICAL 강제 실행
# =============================================================================


class TestApprovalGateForceExecuteBehavior:
    """CRITICAL 런북 강제 실행 동작 검증."""

    def test_force_execute_with_valid_justification_returns_approved(self):
        """유효한 사유와 함께 강제 실행 시 MANUALLY_APPROVED 반환."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        decision = gate.force_execute_runbook(
            runbook,
            ctx,
            force_executed_by="oncall-admin",
            justification="P0 incident: database corruption requires immediate action",
        )

        assert decision.decision_type == ApprovalDecisionType.MANUALLY_APPROVED
        assert decision.approved_by == "force:oncall-admin"

    def test_force_execute_empty_justification_raises_error(self):
        """빈 justification으로 강제 실행 시 RunbookApprovalError 발생."""
        gate = _make_gate()
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        with pytest.raises(RunbookApprovalError, match="non-empty justification"):
            gate.force_execute_runbook(runbook, ctx, "admin", "")

    def test_force_execute_whitespace_justification_raises_error(self):
        """공백만 있는 justification으로 강제 실행 시 RunbookApprovalError 발생."""
        gate = _make_gate()
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        with pytest.raises(RunbookApprovalError, match="non-empty justification"):
            gate.force_execute_runbook(runbook, ctx, "admin", "   ")

    def test_force_execute_blocked_by_governance(self):
        """거버넌스 차단 시 강제 실행도 BLOCKED 반환."""
        gate = _make_gate(governance_allowed=False)
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        decision = gate.force_execute_runbook(
            runbook,
            ctx,
            "admin",
            "urgent fix",
        )

        assert decision.decision_type == ApprovalDecisionType.BLOCKED
        assert "Break Glass" in decision.block_message


# =============================================================================
# 동작 검증 — MEDIUM 타이머 자동 승인
# =============================================================================


class TestApprovalGateTimerBehavior:
    """MEDIUM 위험도 타이머 자동 승인 동작 검증."""

    def test_timer_approval_when_expired(self):
        """타이머 만료 시 TIMER_APPROVED로 전환되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)

        # 과거 시각으로 만료된 요청 생성
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        expired_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

        request = RunbookApprovalRequest(
            request_id="req-timer",
            execution_id="exec-timer",
            runbook_id="rb-medium",
            namespace="default",
            risk_level=RiskLevel.MEDIUM,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
            expires_at=expired_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-timer")
        backend._store[key] = request.to_dict()

        # When
        with patch.object(gate, "_trigger_resume"):
            decision = gate.check_timer_approval("exec-timer")

        # Then
        assert decision is not None
        assert decision.decision_type == ApprovalDecisionType.TIMER_APPROVED
        assert decision.approved_by == "system:timer"

    def test_timer_not_expired_returns_none(self):
        """타이머 미만료 시 None을 반환해야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)

        future_time = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()

        request = RunbookApprovalRequest(
            request_id="req-timer-future",
            execution_id="exec-timer-future",
            runbook_id="rb-medium",
            namespace="default",
            risk_level=RiskLevel.MEDIUM,
            status=ApprovalDecisionType.WAITING,
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=future_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-timer-future")
        backend._store[key] = request.to_dict()

        # When
        decision = gate.check_timer_approval("exec-timer-future")

        # Then
        assert decision is None

    def test_timer_check_ignores_high_risk(self):
        """HIGH 위험도 요청은 타이머 체크에서 무시되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()

        request = RunbookApprovalRequest(
            request_id="req-high",
            execution_id="exec-high",
            runbook_id="rb-high",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
            expires_at=past_time,  # 만료됐지만 HIGH이므로 무시
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-high")
        backend._store[key] = request.to_dict()

        # When
        decision = gate.check_timer_approval("exec-high")

        # Then
        assert decision is None

    def test_timer_triggers_resume_on_success(self):
        """타이머 승인 성공 시 _trigger_resume()이 호출되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        expired_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

        request = RunbookApprovalRequest(
            request_id="req-resume",
            execution_id="exec-resume",
            runbook_id="rb-medium",
            namespace="default",
            risk_level=RiskLevel.MEDIUM,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
            expires_at=expired_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-resume")
        backend._store[key] = request.to_dict()

        # When
        with patch.object(gate, "_trigger_resume") as mock_resume:
            gate.check_timer_approval("exec-resume")

        # Then
        mock_resume.assert_called_once_with("exec-resume")


# =============================================================================
# 동작 검증 — 리마인더 판단 로직
# =============================================================================


class TestApprovalGateReminderBehavior:
    """리마인더 발송 판단 동작 검증."""

    def test_should_send_reminder_at_first_interval(self):
        """첫 번째 간격(15분) 이후 리마인더 발송 대상이 되어야 한다."""
        gate = _make_gate()

        created_at = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-r1",
            execution_id="exec-r1",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            created_at=created_at,
            reminder_count=0,
        )

        result = gate._should_send_reminder(request, datetime.now(timezone.utc))
        assert result is True

    def test_should_not_send_reminder_before_first_interval(self):
        """첫 번째 간격(15분) 이전에는 리마인더를 발송하지 않아야 한다."""
        gate = _make_gate()

        created_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-r2",
            execution_id="exec-r2",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            created_at=created_at,
            reminder_count=0,
        )

        result = gate._should_send_reminder(request, datetime.now(timezone.utc))
        assert result is False

    def test_should_send_second_reminder_at_second_interval(self):
        """두 번째 간격(30분) 이후 두 번째 리마인더 발송 대상이 되어야 한다."""
        gate = _make_gate()

        created_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-r3",
            execution_id="exec-r3",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            created_at=created_at,
            reminder_count=1,  # 첫 번째 리마인더는 이미 발송됨
        )

        result = gate._should_send_reminder(request, datetime.now(timezone.utc))
        assert result is True

    def test_should_not_send_reminder_without_created_at(self):
        """created_at이 없으면 리마인더를 발송하지 않아야 한다."""
        gate = _make_gate()

        request = RunbookApprovalRequest(
            request_id="req-r4",
            execution_id="exec-r4",
            runbook_id="rb1",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            created_at=None,
        )

        result = gate._should_send_reminder(request, datetime.now(timezone.utc))
        assert result is False


# =============================================================================
# 동작 검증 — HIGH 타임아웃 처리
# =============================================================================


class TestApprovalGateTimeoutBehavior:
    """HIGH 위험도 타임아웃 처리 동작 검증."""

    def test_timeout_transitions_to_blocked(self):
        """타임아웃 시 BLOCKED으로 전환되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)

        # max_wait_seconds를 짧게 설정
        gate._get_max_wait_seconds = lambda: 600  # type: ignore[method-assign]

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-timeout",
            execution_id="exec-timeout",
            runbook_id="rb-high",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-timeout")
        backend._store[key] = request.to_dict()

        # When
        with patch.object(gate, "_send_timeout_notification"), patch.object(gate, "_store_timeout_to_dlq"):
            timed_out = gate.check_approval_timeouts()

        # Then
        assert len(timed_out) == 1
        assert timed_out[0].execution_id == "exec-timeout"

    def test_no_timeout_when_max_wait_is_zero(self):
        """max_wait_seconds가 0이면 타임아웃 체크를 건너뛰어야 한다."""
        gate = _make_gate()
        gate._get_max_wait_seconds = lambda: 0  # type: ignore[method-assign]

        timed_out = gate.check_approval_timeouts()

        assert timed_out == []

    def test_timeout_stores_to_dlq(self):
        """타임아웃 시 DLQ에 저장되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        gate._get_max_wait_seconds = lambda: 600  # type: ignore[method-assign]

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-dlq",
            execution_id="exec-dlq",
            runbook_id="rb-high",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-dlq")
        backend._store[key] = request.to_dict()

        # When
        with patch.object(gate, "_send_timeout_notification"), patch.object(gate, "_store_timeout_to_dlq") as mock_dlq:
            gate.check_approval_timeouts()

        # Then
        mock_dlq.assert_called_once()
        call_args = mock_dlq.call_args
        assert call_args[0][0].execution_id == "exec-dlq"


# =============================================================================
# 동작 검증 — 중복 승인 요청 차단
# =============================================================================


class TestApprovalGateDuplicateBehavior:
    """중복 승인 요청 차단 동작 검증."""

    def test_duplicate_approval_request_raises_error(self):
        """동일 runbook_id + namespace에 WAITING 요청이 있으면 생성 차단."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx1 = _make_ctx(execution_id="exec-dup-1")
        ctx2 = _make_ctx(execution_id="exec-dup-2")

        # 첫 번째 요청 생성
        gate._create_approval_request(runbook, ctx1, with_timer=True)

        # When / Then — 두 번째 요청 시도
        with pytest.raises(RunbookApprovalDuplicateError, match="already pending"):
            gate._create_approval_request(runbook, ctx2, with_timer=True)

    def test_duplicate_check_allows_after_decision(self):
        """기존 요청이 결정된 후에는 새 요청을 생성할 수 있다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx1 = _make_ctx(execution_id="exec-first")

        gate._create_approval_request(runbook, ctx1, with_timer=False)

        # 첫 번째 요청 거부
        gate.reject_runbook("exec-first", "admin", "rejected")

        # When — 새 요청 생성 가능
        ctx2 = _make_ctx(execution_id="exec-second")
        request = gate._create_approval_request(runbook, ctx2, with_timer=False)

        # Then
        assert request.execution_id == "exec-second"

    def test_evaluate_approval_medium_duplicate_returns_cancelled(self):
        """MEDIUM 런북에서 중복 승인 시 evaluate_approval가 예외를 전파."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx1 = _make_ctx(execution_id="exec-med-1")

        gate.evaluate_approval(runbook, ctx1)

        # When / Then — 동일 런북에 두 번째 evaluate_approval 시도 시 예외
        ctx2 = _make_ctx(execution_id="exec-med-2")
        with pytest.raises(RunbookApprovalDuplicateError):
            gate.evaluate_approval(runbook, ctx2)


# =============================================================================
# 동작 검증 — 알림 발송 부수효과
# =============================================================================


class TestApprovalGateNotificationBehavior:
    """알림 발송 부수효과 검증."""

    def test_medium_risk_sends_notification(self):
        """MEDIUM 위험도 런북은 알림을 발송해야 한다."""
        # Given
        mock_notification = MagicMock()
        gate = _make_gate(
            governance_allowed=True,
            notification_manager=mock_notification,
        )
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx = _make_ctx()

        # When
        gate.evaluate_approval(runbook, ctx)

        # Then
        mock_notification.notify.assert_called_once()

    def test_high_risk_sends_notification(self):
        """HIGH 위험도 런북은 알림을 발송해야 한다."""
        # Given
        mock_notification = MagicMock()
        gate = _make_gate(
            governance_allowed=True,
            notification_manager=mock_notification,
        )
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx()

        # When
        gate.evaluate_approval(runbook, ctx)

        # Then
        mock_notification.notify.assert_called_once()

    def test_low_risk_does_not_send_notification(self):
        """LOW 위험도 런북은 알림을 발송하지 않아야 한다."""
        # Given
        mock_notification = MagicMock()
        gate = _make_gate(
            governance_allowed=True,
            notification_manager=mock_notification,
        )
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        # When
        gate.evaluate_approval(runbook, ctx)

        # Then
        mock_notification.notify.assert_not_called()

    def test_notification_failure_does_not_raise(self):
        """알림 발송 실패 시 예외를 전파하지 않아야 한다."""
        # Given
        mock_notification = MagicMock()
        mock_notification.notify.side_effect = RuntimeError("connection failed")
        gate = _make_gate(
            governance_allowed=True,
            notification_manager=mock_notification,
        )
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx = _make_ctx()

        # When / Then — 예외 없이 정상 반환
        decision = gate.evaluate_approval(runbook, ctx)
        assert decision.decision_type == ApprovalDecisionType.WAITING

    def test_no_notification_manager_logs_warning(self):
        """notification_manager가 없으면 경고 로그만 남기고 진행해야 한다."""
        # Given
        gate = _make_gate(governance_allowed=True, notification_manager=None)
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx = _make_ctx()

        # When / Then — 예외 없이 정상 반환
        decision = gate.evaluate_approval(runbook, ctx)
        assert decision.decision_type == ApprovalDecisionType.WAITING


# =============================================================================
# 동작 검증 — 싱글톤 캐싱/리셋
# =============================================================================


class TestApprovalGateSingletonBehavior:
    """싱글톤 패턴 캐싱/리셋 동작 검증."""

    def test_get_returns_same_instance(self):
        """get_runbook_approval_gate()는 동일 인스턴스를 반환해야 한다."""
        # Given
        reset_runbook_approval_gate()

        # When
        first = get_runbook_approval_gate()
        second = get_runbook_approval_gate()

        # Then
        assert first is second

        # Cleanup
        reset_runbook_approval_gate()

    def test_reset_clears_cached_instance(self):
        """reset 후 새 인스턴스가 생성되어야 한다."""
        # Given
        reset_runbook_approval_gate()
        first = get_runbook_approval_gate()

        # When
        reset_runbook_approval_gate()
        second = get_runbook_approval_gate()

        # Then
        assert first is not second

        # Cleanup
        reset_runbook_approval_gate()


# =============================================================================
# 동작 검증 — ApprovalAlreadyDecidedError 예외
# =============================================================================


class TestApprovalAlreadyDecidedErrorBehavior:
    """ApprovalAlreadyDecidedError 예외 속성 검증."""

    def test_error_contains_execution_id(self):
        """예외에 execution_id가 포함되어야 한다."""
        error = ApprovalAlreadyDecidedError("exec-123", ApprovalDecisionType.TIMER_APPROVED)

        assert error.execution_id == "exec-123"

    def test_error_contains_current_status(self):
        """예외에 현재 상태가 포함되어야 한다."""
        error = ApprovalAlreadyDecidedError("exec-123", ApprovalDecisionType.TIMER_APPROVED)

        assert error.current_status == ApprovalDecisionType.TIMER_APPROVED

    def test_error_message_includes_status_value(self):
        """예외 메시지에 상태 값이 포함되어야 한다."""
        error = ApprovalAlreadyDecidedError("exec-123", ApprovalDecisionType.REJECTED)

        assert "rejected" in str(error)
        assert "exec-123" in str(error)


# =============================================================================
# 동작 검증 — LOW 자동 승인 세부
# =============================================================================


class TestApprovalGateLowRiskBehavior:
    """LOW 위험도 자동 승인 세부 동작 검증."""

    def test_low_risk_approved_by_system_auto(self):
        """LOW 위험도 자동 승인 시 approved_by가 'system:auto'."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.approved_by == "system:auto"

    def test_low_risk_decided_at_is_set(self):
        """LOW 위험도 자동 승인 시 decided_at이 설정되어야 한다."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.decided_at is not None

    def test_low_risk_is_approved(self):
        """LOW 위험도 자동 승인은 is_approved가 True."""
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.LOW)
        ctx = _make_ctx()

        decision = gate.evaluate_approval(runbook, ctx)

        assert decision.is_approved is True


# =============================================================================
# 동작 검증 — 승인 요청 영속화
# =============================================================================


class TestApprovalGatePersistenceBehavior:
    """승인 요청 영속화 동작 검증."""

    def test_medium_creates_approval_request_with_timer(self):
        """MEDIUM 위험도 시 타이머가 있는 승인 요청이 생성되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.MEDIUM)
        ctx = _make_ctx(execution_id="exec-persist-med")

        # When
        gate.evaluate_approval(runbook, ctx)

        # Then
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-persist-med")
        data = backend._store.get(key)
        assert data is not None
        assert data["expires_at"] is not None  # 타이머 설정됨

    def test_high_creates_approval_request_without_timer(self):
        """HIGH 위험도 시 타이머 없는 승인 요청이 생성되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-persist-high")

        # When
        gate.evaluate_approval(runbook, ctx)

        # Then
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-persist-high")
        data = backend._store.get(key)
        assert data is not None
        assert data["expires_at"] is None  # 타이머 없음

    def test_approval_request_summary_contains_runbook_info(self):
        """승인 요청의 runbook_summary에 런북 정보가 포함되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(
            runbook_id="rb-summary",
            risk_level=RiskLevel.HIGH,
            name="Summary Test",
        )
        ctx = _make_ctx(execution_id="exec-summary")

        # When
        gate.evaluate_approval(runbook, ctx)

        # Then
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-summary")
        data = backend._store.get(key)
        summary = data["runbook_summary"]
        assert summary["name"] == "Summary Test"
        assert summary["risk_level"] == "high"
        assert "step_count" in summary

    def test_cas_in_memory_checks_expected_status(self):
        """InMemory 환경에서 CAS가 expected_status를 검증해야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        runbook = _make_runbook(risk_level=RiskLevel.HIGH)
        ctx = _make_ctx(execution_id="exec-cas")
        gate._create_approval_request(runbook, ctx, with_timer=False)

        # 수동으로 상태 변경
        request = gate._load_approval_request("exec-cas")
        request.status = ApprovalDecisionType.REJECTED
        request.decided_by = "admin"

        # When — 이미 변경된 상태에서 CAS 시도
        success = gate._cas_save_approval_request(request, expected_status="blocked")

        # Then — expected_status("blocked")와 실제("waiting")가 불일치하므로 실패
        assert success is False


# =============================================================================
# 계약 검증 — Lua CAS 스크립트 KEEPTTL
# =============================================================================


class TestApprovalCasScriptContract:
    """CAS Lua 스크립트가 TTL을 보존하는지 계약 검증."""

    def test_cas_script_uses_keepttl(self):
        """APPROVAL_CAS_SCRIPT는 SET 시 KEEPTTL을 사용하여 기존 TTL을 보존해야 한다."""
        assert "KEEPTTL" in APPROVAL_CAS_SCRIPT

    def test_cas_script_does_not_use_plain_set(self):
        """SET 명령이 KEEPTTL 없이 단독으로 사용되면 안 된다.

        redis.call("SET", ...) 다음에 반드시 "KEEPTTL"이 인자로 포함되어야 한다.
        """
        import re

        # SET 호출이 KEEPTTL을 포함하는지 확인
        set_calls = re.findall(r'redis\.call\("SET"[^)]+\)', APPROVAL_CAS_SCRIPT)
        for call in set_calls:
            assert "KEEPTTL" in call, f"SET call without KEEPTTL: {call}"

    def test_cas_script_returns_zero_on_missing_key(self):
        """키가 없으면 0을 반환해야 한다 (스크립트 첫 분기)."""
        assert "return 0" in APPROVAL_CAS_SCRIPT

    def test_cas_script_returns_one_on_status_match(self):
        """상태가 일치하면 1을 반환해야 한다."""
        assert "return 1" in APPROVAL_CAS_SCRIPT


# =============================================================================
# 동작 검증 — 타임아웃 CAS 실패 시 건너뜀
# =============================================================================


class TestApprovalGateTimeoutCasConflictBehavior:
    """타임아웃 CAS 실패 시 알림/DLQ를 건너뛰는 동작 검증."""

    def test_timeout_skips_notification_on_cas_failure(self):
        """CAS 실패 시 타임아웃 알림을 발송하지 않아야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        gate._get_max_wait_seconds = lambda: 600  # type: ignore[method-assign]

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-cas-timeout",
            execution_id="exec-cas-timeout",
            runbook_id="rb-high",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-cas-timeout")
        backend._store[key] = request.to_dict()

        # CAS가 항상 실패하도록 설정 (다른 운영자/타이머가 먼저 결정)
        gate._cas_save_approval_request = MagicMock(return_value=False)  # type: ignore[method-assign]

        # When
        with (
            patch.object(gate, "_send_timeout_notification") as mock_notify,
            patch.object(gate, "_store_timeout_to_dlq") as mock_dlq,
        ):
            timed_out = gate.check_approval_timeouts()

        # Then — CAS 실패이므로 알림/DLQ가 호출되지 않아야 함
        assert len(timed_out) == 0
        mock_notify.assert_not_called()
        mock_dlq.assert_not_called()

    def test_timeout_proceeds_on_cas_success(self):
        """CAS 성공 시 타임아웃 알림 + DLQ 저장이 정상 수행되어야 한다."""
        # Given
        backend = _make_memory_backend()
        gate = _make_gate(backend=backend)
        gate._get_max_wait_seconds = lambda: 600  # type: ignore[method-assign]

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        request = RunbookApprovalRequest(
            request_id="req-cas-ok",
            execution_id="exec-cas-ok",
            runbook_id="rb-high",
            namespace="default",
            risk_level=RiskLevel.HIGH,
            status=ApprovalDecisionType.WAITING,
            created_at=past_time,
        )
        key = APPROVAL_REQUEST_KEY.format(execution_id="exec-cas-ok")
        backend._store[key] = request.to_dict()

        # When
        with (
            patch.object(gate, "_send_timeout_notification") as mock_notify,
            patch.object(gate, "_store_timeout_to_dlq") as mock_dlq,
        ):
            timed_out = gate.check_approval_timeouts()

        # Then — CAS 성공이므로 알림 + DLQ 모두 호출
        assert len(timed_out) == 1
        mock_notify.assert_called_once()
        mock_dlq.assert_called_once()


# =============================================================================
# 동작 검증 — force_execute_audit_required 설정 연동
# =============================================================================


class TestApprovalGateForceExecuteAuditBehavior:
    """force_execute_audit_required 설정에 따른 감사 로그 동작 검증."""

    def test_force_execute_logs_warning_when_audit_required(self):
        """audit_required=True일 때 warning 레벨로 로그를 남겨야 한다."""
        # Given
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        mock_settings = MagicMock()
        mock_settings.force_execute_audit_required = True
        gate._get_settings = MagicMock(return_value=mock_settings)  # type: ignore[method-assign]

        # When / Then — warning 로그 확인
        with patch("selfhealing.services.runbook.approval_gate.logger") as mock_logger:
            decision = gate.force_execute_runbook(
                runbook,
                ctx,
                "admin",
                "P0 incident",
            )

        assert decision.decision_type == ApprovalDecisionType.MANUALLY_APPROVED
        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        assert call_kwargs[1]["audit_required"] is True

    def test_force_execute_logs_info_when_audit_not_required(self):
        """audit_required=False일 때 info 레벨로 로그를 남겨야 한다."""
        # Given
        gate = _make_gate(governance_allowed=True)
        runbook = _make_runbook(risk_level=RiskLevel.CRITICAL)
        ctx = _make_ctx()

        mock_settings = MagicMock()
        mock_settings.force_execute_audit_required = False
        gate._get_settings = MagicMock(return_value=mock_settings)  # type: ignore[method-assign]

        # When
        with patch("selfhealing.services.runbook.approval_gate.logger") as mock_logger:
            decision = gate.force_execute_runbook(
                runbook,
                ctx,
                "admin",
                "P0 incident",
            )

        # Then
        assert decision.decision_type == ApprovalDecisionType.MANUALLY_APPROVED
        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs[1]["audit_required"] is False
        mock_logger.warning.assert_not_called()


# =============================================================================
# 계약 검증 — 276 Approval Gate 설정 계약
# =============================================================================


class TestApprovalGateSettingsContract:
    """276 Approval Gate 설정 필드 설계 계약값 검증."""

    def test_approval_timer_seconds_default_is_300(self):
        """§10 설계 계약: MEDIUM 타이머 기본값 300초."""
        from selfhealing.settings.runbook import RunbookSettings, reset_runbook_settings

        reset_runbook_settings()
        with patch.dict("os.environ", {}, clear=True):
            settings = RunbookSettings()
            assert settings.approval_timer_seconds == 300

    def test_approval_max_wait_seconds_default_is_3600(self):
        """§10 설계 계약: HIGH 최대 대기 기본값 3600초."""
        from selfhealing.settings.runbook import RunbookSettings, reset_runbook_settings

        reset_runbook_settings()
        with patch.dict("os.environ", {}, clear=True):
            settings = RunbookSettings()
            assert settings.approval_max_wait_seconds == 3600

    def test_approval_reminder_intervals_default(self):
        """§10 설계 계약: 리마인더 간격 기본값 [15, 30]."""
        from selfhealing.settings.runbook import RunbookSettings, reset_runbook_settings

        reset_runbook_settings()
        with patch.dict("os.environ", {}, clear=True):
            settings = RunbookSettings()
            assert settings.approval_reminder_intervals_minutes == [15, 30]

    def test_approval_check_interval_seconds_default_is_30(self):
        """§10 설계 계약: Celery Beat 폴링 간격 기본값 30초."""
        from selfhealing.settings.runbook import RunbookSettings, reset_runbook_settings

        reset_runbook_settings()
        with patch.dict("os.environ", {}, clear=True):
            settings = RunbookSettings()
            assert settings.approval_check_interval_seconds == 30

    def test_force_execute_audit_required_default_is_true(self):
        """§10 설계 계약: 강제 실행 감사 필수 기본값 True."""
        from selfhealing.settings.runbook import RunbookSettings, reset_runbook_settings

        reset_runbook_settings()
        with patch.dict("os.environ", {}, clear=True):
            settings = RunbookSettings()
            assert settings.force_execute_audit_required is True

    def test_duplicate_approval_timeout_field_removed(self):
        """중복 설정 필드 approval_timeout_seconds가 제거되어야 한다."""
        from selfhealing.settings.runbook import RunbookSettings

        assert "approval_timeout_seconds" not in RunbookSettings.model_fields
