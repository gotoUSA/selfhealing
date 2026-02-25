"""
Runbook Approval Gate — 위험도 기반 승인 라우팅 게이트.

Runbook 실행 전 위험도(RiskLevel)에 따라 승인 경로를 자동 분기한다.

1단계: 거버넌스 체크 (Kill Switch / Emergency / Error Budget)
2단계: RiskLevel 기반 승인 라우팅 (자동 / 타이머 / 수동 / 차단)

기존 GovernanceCheckMixin을 상속하여 check_governance()를 직접 활용하고,
ApprovalMixin의 approve_recovery() 패턴을 Runbook에 맞게 확장한다.

Reference:
    docs/self_healing/middleware_system/276_RUNBOOK_APPROVAL_GATE.md
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from selfhealing.services.governance.checks import GovernanceCheckMixin, GovernanceCheckResult
from selfhealing.services.runbook.exceptions import (
    ApprovalAlreadyDecidedError,
    RunbookApprovalDuplicateError,
    RunbookApprovalError,
)
from selfhealing.services.runbook.execution_models import (
    ApprovalDecision,
    ApprovalDecisionType,
    RunbookApprovalRequest,
)
from selfhealing.services.runbook.runbook_registry import RiskLevel

if TYPE_CHECKING:
    from selfhealing.core.state_backend import StateBackend
    from selfhealing.services.runbook.execution_models import RunbookExecutionContext
    from selfhealing.services.runbook.runbook_registry import Runbook
    from selfhealing.services.unified_notification.service import UnifiedNotificationManager

logger = structlog.get_logger()

# =============================================================================
# 상수
# =============================================================================

APPROVAL_REQUEST_KEY = "selfhealing:runbook:approval:{execution_id}"
"""승인 요청 Redis 키 템플릿."""

APPROVAL_INDEX_KEY = "selfhealing:runbook:approval:index:{runbook_id}:{namespace}"
"""중복 방지용 보조 인덱스 키 템플릿."""

APPROVAL_REQUEST_TTL = 86400
"""승인 요청 TTL (24시간)."""

# 모듈 기본값 — Settings 로드 실패 시 폴백
DEFAULT_APPROVAL_TIMER_SECONDS = 300
DEFAULT_APPROVAL_MAX_WAIT_SECONDS = 3600
DEFAULT_APPROVAL_REMINDER_INTERVALS_MINUTES = [15, 30]

# Redis Lua: 승인 요청 상태 기반 CAS
# atomic_transition.py ATOMIC_TRANSITION_SCRIPT 패턴 — "현재 상태가 expected와 같을 때만 갱신"
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


# =============================================================================
# RunbookApprovalGate
# =============================================================================


class RunbookApprovalGate(GovernanceCheckMixin):
    """Runbook 실행 전 승인 게이트.

    GovernanceCheckMixin을 상속하여 check_governance()로 시스템 수준 안전장치를 체크하고,
    RiskLevel에 따라 자동 승인, 타이머 대기, 수동 승인, 차단을 결정한다.

    RiskLevel별 승인 정책:
        LOW      — 자동 승인 (알림 없음)
        MEDIUM   — 알림 발송 + 타이머 자동 승인 (기본 300초)
        HIGH     — 수동 승인 필수 (무기한 대기, 타임아웃 시 차단)
        CRITICAL — 자동 실행 차단 (force_execute_runbook()으로만 실행 가능)
    """

    _governance_service_name = "runbook"
    _governance_domain = "selfhealing"

    def __init__(
        self,
        notification_manager: UnifiedNotificationManager | None = None,
        state_backend: StateBackend | None = None,
    ) -> None:
        self._notification = notification_manager
        self._backend = state_backend

    # =========================================================================
    # Settings 기반 값 조회 — 모듈 상수를 폴백으로 사용
    # =========================================================================

    def _get_settings(self):
        """RunbookSettings 싱글톤 로드."""
        from selfhealing.settings.runbook import get_runbook_settings

        return get_runbook_settings()

    def _get_timer_seconds(self) -> int:
        """MEDIUM 위험도 타이머 자동 승인 대기 시간 (초)."""
        try:
            return self._get_settings().approval_timer_seconds
        except Exception:
            return DEFAULT_APPROVAL_TIMER_SECONDS

    def _get_max_wait_seconds(self) -> int:
        """HIGH 위험도 최대 대기 시간 (초). 0이면 무기한."""
        try:
            return self._get_settings().approval_max_wait_seconds
        except Exception:
            return DEFAULT_APPROVAL_MAX_WAIT_SECONDS

    def _get_reminder_intervals(self) -> list[int]:
        """승인 대기 중 리마인더 발송 간격 (분 단위 목록)."""
        try:
            return self._get_settings().approval_reminder_intervals_minutes
        except Exception:
            return DEFAULT_APPROVAL_REMINDER_INTERVALS_MINUTES

    def _get_backend(self) -> StateBackend:
        """StateBackend 지연 로드."""
        if self._backend is None:
            from selfhealing.core.state_backend import get_state_backend

            self._backend = get_state_backend()
        return self._backend

    # =========================================================================
    # 거버넌스 체크
    # =========================================================================

    def evaluate_governance(
        self,
        runbook: Runbook,
        namespace: str,
    ) -> GovernanceCheckResult:
        """check_all_governance() 호출. GovernanceCheckMixin.check_governance()를 직접 사용."""
        return self.check_governance(
            check_kill_switch=True,
            check_emergency=True,
            check_error_budget=True,
            operation_name=f"runbook:{runbook.id}",
            audit_on_block=True,
        )

    # =========================================================================
    # 승인 평가
    # =========================================================================

    def evaluate_approval(
        self,
        runbook: Runbook,
        ctx: RunbookExecutionContext,
    ) -> ApprovalDecision:
        """RiskLevel에 따른 승인 결정.

        1단계: 거버넌스 체크 (시스템 수준 안전장치)
        2단계: RiskLevel 기반 라우팅

        Args:
            runbook: 실행 대상 Runbook 정의
            ctx: 실행 컨텍스트

        Returns:
            ApprovalDecision — 승인 결정 결과

        Raises:
            RunbookApprovalDuplicateError: 동일 런북+네임스페이스에 이미 대기 중인 승인 존재
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
                    f"CRITICAL risk runbook '{runbook.id}' requires manual " f"execution — automated execution is blocked"
                ),
            )

        # 알 수 없는 RiskLevel — 안전하게 차단
        return ApprovalDecision(
            decision_type=ApprovalDecisionType.BLOCKED,
            risk_level=risk,
            block_message=f"Unknown risk level: {risk}",
        )

    # =========================================================================
    # 수동 승인 API
    # =========================================================================

    def approve_runbook(
        self,
        execution_id: str,
        approved_by: str,
    ) -> ApprovalDecision:
        """Runbook 실행 수동 승인.

        CAS(Compare-And-Set)로 WAITING → MANUALLY_APPROVED 원자적 전환.
        타이머 만료(TIMER_APPROVED)와 동시 도달 시 CAS가 선착순 보장.
        CAS 실패 시 ApprovalAlreadyDecidedError 발생 (→ HTTP 409 Conflict).

        승인 성공 시 _trigger_resume()으로 Runbook 실행을 재개한다.

        Args:
            execution_id: 대상 실행 ID
            approved_by: 승인자 ID

        Returns:
            ApprovalDecision — 수동 승인 결과

        Raises:
            RunbookApprovalError: 승인 요청을 찾지 못한 경우
            ApprovalAlreadyDecidedError: 이미 다른 결정으로 확정된 경우
        """
        request = self._load_approval_request(execution_id)
        if request is None:
            raise RunbookApprovalError(f"Approval request not found: {execution_id}")

        if request.status != ApprovalDecisionType.WAITING:
            raise ApprovalAlreadyDecidedError(execution_id, request.status)

        # CAS 상태 전환
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

        # 승인 후 실행 재개 트리거
        self._trigger_resume(execution_id)

        return ApprovalDecision(
            decision_type=ApprovalDecisionType.MANUALLY_APPROVED,
            risk_level=request.risk_level,
            approved_by=approved_by,
            decided_at=request.decided_at,
        )

    # =========================================================================
    # 수동 거부 API
    # =========================================================================

    def reject_runbook(
        self,
        execution_id: str,
        rejected_by: str,
        reason: str = "",
    ) -> ApprovalDecision:
        """Runbook 실행 거부.

        CAS로 WAITING → REJECTED 원자적 전환.
        CAS 실패 시 ApprovalAlreadyDecidedError 발생 (→ HTTP 409 Conflict).

        Args:
            execution_id: 대상 실행 ID
            rejected_by: 거부자 ID
            reason: 거부 사유

        Returns:
            ApprovalDecision — 거부 결과

        Raises:
            RunbookApprovalError: 승인 요청을 찾지 못한 경우
            ApprovalAlreadyDecidedError: 이미 다른 결정으로 확정된 경우
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

    # =========================================================================
    # CRITICAL 런북 강제 실행
    # =========================================================================

    def force_execute_runbook(
        self,
        runbook: Runbook,
        ctx: RunbookExecutionContext,
        force_executed_by: str,
        justification: str,
    ) -> ApprovalDecision:
        """CRITICAL 런북 강제 실행.

        Break Glass(시스템 전역 거버넌스 우회)와 분리된 런북 단위 강제 실행.
        거버넌스 체크(Kill Switch/Emergency/Error Budget)는 여전히 수행하되,
        RiskLevel.CRITICAL 차단만 우회한다.

        Args:
            runbook: 실행 대상 Runbook
            ctx: 실행 컨텍스트
            force_executed_by: 강제 실행자 ID
            justification: 강제 실행 사유 (비어있으면 거부)

        Returns:
            ApprovalDecision — 강제 실행 승인 결과

        Raises:
            RunbookApprovalError: justification이 비어있는 경우
        """
        if not justification or not justification.strip():
            raise RunbookApprovalError("Force execution requires a non-empty justification")

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

    # =========================================================================
    # MEDIUM 타이머 자동 승인
    # =========================================================================

    def check_timer_approval(
        self,
        execution_id: str,
    ) -> ApprovalDecision | None:
        """MEDIUM 위험도 타이머 만료 체크.

        Celery Beat에서 주기적으로 호출. CAS로 WAITING → TIMER_APPROVED 원자적 전환.
        CAS 실패 시(운영자가 먼저 거부) 멱등하게 무시.

        Returns:
            ApprovalDecision if timer expired, None if still waiting or not applicable.
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
            # 타이머 만료 → CAS 상태 전환
            request.status = ApprovalDecisionType.TIMER_APPROVED
            request.decided_by = "system:timer"
            request.decided_at = now

            success = self._cas_save_approval_request(
                request,
                expected_status="waiting",
            )
            if not success:
                # CAS 실패: 운영자가 먼저 거부/승인 → 멱등하게 무시
                logger.info(
                    "runbook_approval.timer_cas_conflict",
                    execution_id=execution_id,
                )
                return None

            # 타이머 승인 성공 → 실행 재개 트리거
            self._trigger_resume(execution_id)

            return ApprovalDecision(
                decision_type=ApprovalDecisionType.TIMER_APPROVED,
                risk_level=request.risk_level,
                approved_by="system:timer",
                decided_at=now,
            )

        return None  # 아직 대기 중

    def check_all_timer_approvals(self) -> list[ApprovalDecision]:
        """모든 대기 중인 MEDIUM 타이머를 체크. Celery Beat에서 호출."""
        results: list[ApprovalDecision] = []
        for request in self._list_waiting_requests():
            if request.risk_level == RiskLevel.MEDIUM:
                decision = self.check_timer_approval(request.execution_id)
                if decision is not None:
                    results.append(decision)
        return results

    # =========================================================================
    # 리마인더
    # =========================================================================

    def check_and_send_reminders(self) -> list[RunbookApprovalRequest]:
        """대기 중인 승인 요청에 리마인더 발송.

        PendingRecoveryApprovalManager.check_and_send_reminders() 패턴.
        """
        reminded: list[RunbookApprovalRequest] = []
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
        """리마인더 발송 여부 판단.

        PendingRecoveryApprovalManager._should_send_reminder() 패턴.
        created_at으로부터의 경과 시간과 intervals를 비교하여
        다음 리마인더 시점에 도달했는지 확인한다.
        """
        if not request.created_at:
            return False

        created = datetime.fromisoformat(request.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_minutes = (now - created).total_seconds() / 60.0
        reminder_count = request.reminder_count or 0

        intervals = self._get_reminder_intervals()
        for idx, interval in enumerate(intervals):
            if reminder_count <= idx and elapsed_minutes >= interval:
                return True

        return False

    # =========================================================================
    # 타임아웃 처리
    # =========================================================================

    def check_approval_timeouts(self) -> list[RunbookApprovalRequest]:
        """타임아웃 체크. Celery Beat에서 주기적으로 호출.

        HIGH 위험도 런북이 max_wait_seconds를 초과하면:
        1. BLOCKED 상태 전환 (자동 취소)
        2. CRITICAL 알림 발송
        3. DLQ 저장
        """
        timed_out: list[RunbookApprovalRequest] = []
        now = datetime.now(timezone.utc)
        max_wait = self._get_max_wait_seconds()
        if max_wait <= 0:
            return []  # 0이면 무기한 대기

        for request in self._list_waiting_requests():
            if not request.created_at:
                continue

            created = datetime.fromisoformat(request.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (now - created).total_seconds()

            if elapsed > max_wait:
                # 1. 상태 전환
                request.status = ApprovalDecisionType.BLOCKED
                request.decided_by = "system:timeout"
                request.decided_at = now.isoformat()
                self._cas_save_approval_request(
                    request,
                    expected_status="waiting",
                )

                # 2. CRITICAL 알림
                self._send_timeout_notification(request, elapsed)

                # 3. DLQ 저장
                self._store_timeout_to_dlq(request, elapsed)

                timed_out.append(request)

        return timed_out

    # =========================================================================
    # 알림 발송
    # =========================================================================

    def _send_approval_notification(
        self,
        runbook: Runbook,
        ctx: RunbookExecutionContext,
        is_timer: bool,
    ) -> None:
        """승인 요청 알림 발송.

        NotificationCategory.APPROVAL + 적절한 NotificationPriority 사용.
        """
        if self._notification is None:
            logger.warning("runbook_approval.no_notification_manager")
            return

        from selfhealing.services.unified_notification.models import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )

        # RiskLevel → NotificationPriority 매핑
        priority = (
            NotificationPriority.HIGH
            if is_timer  # MEDIUM risk → HIGH priority
            else NotificationPriority.CRITICAL  # HIGH risk → CRITICAL priority
        )

        timer_info = ""
        if is_timer:
            timer_seconds = self._get_timer_seconds()
            timer_info = f"\n⏱ 자동 승인까지 {timer_seconds}초 남음. " f"거부하려면 수동 개입 필요."

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
            logger.warning("runbook_approval.notification_failed", error=str(e))

    def _send_reminder_notification(self, request: RunbookApprovalRequest) -> None:
        """리마인더 알림 발송."""
        if self._notification is None:
            return

        from selfhealing.services.unified_notification.models import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )

        created = datetime.fromisoformat(request.created_at) if request.created_at else None
        elapsed_str = ""
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60.0
            elapsed_str = f"\n⏰ 대기 시간: {elapsed_minutes:.0f}분 경과"

        payload = NotificationPayload(
            title=f"[Runbook 승인 리마인더] {request.runbook_summary.get('name', request.runbook_id)}",
            message=(
                f"Runbook: {request.runbook_id}\n"
                f"Risk Level: {request.risk_level.value}\n"
                f"Namespace: {request.namespace}\n"
                f"Execution ID: {request.execution_id}\n"
                f"리마인더 #{request.reminder_count}"
                f"{elapsed_str}"
            ),
            priority=NotificationPriority.CRITICAL,
            category=NotificationCategory.APPROVAL,
            source="runbook_approval_gate",
            metadata={
                "execution_id": request.execution_id,
                "runbook_id": request.runbook_id,
                "reminder_count": request.reminder_count,
            },
            dedup_key=f"runbook_reminder:{request.execution_id}:{request.reminder_count}",
        )

        try:
            self._notification.notify(payload)
        except Exception as e:
            logger.warning("runbook_approval.reminder_notification_failed", error=str(e))

    def _send_timeout_notification(
        self,
        request: RunbookApprovalRequest,
        elapsed_seconds: float,
    ) -> None:
        """타임아웃 알림 발송. CRITICAL 우선순위."""
        if self._notification is None:
            return

        from selfhealing.services.unified_notification.models import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )

        payload = NotificationPayload(
            title=f"[Runbook 승인 타임아웃] {request.runbook_summary.get('name', request.runbook_id)}",
            message=(
                f"Runbook: {request.runbook_id}\n"
                f"Namespace: {request.namespace}\n"
                f"Execution ID: {request.execution_id}\n"
                f"⚠️ 승인 대기 타임아웃 ({elapsed_seconds:.0f}초 경과). "
                f"자동 치유가 차단되었습니다."
            ),
            priority=NotificationPriority.CRITICAL,
            category=NotificationCategory.APPROVAL,
            source="runbook_approval_gate",
            metadata={
                "execution_id": request.execution_id,
                "runbook_id": request.runbook_id,
                "elapsed_seconds": elapsed_seconds,
                "timeout_type": "approval_timeout",
            },
            dedup_key=f"runbook_timeout:{request.execution_id}",
        )

        try:
            self._notification.notify(payload)
        except Exception as e:
            logger.warning("runbook_approval.timeout_notification_failed", error=str(e))

    # =========================================================================
    # 영속화 — 승인 요청 CRUD + CAS
    # =========================================================================

    def _create_approval_request(
        self,
        runbook: Runbook,
        ctx: RunbookExecutionContext,
        with_timer: bool,
    ) -> RunbookApprovalRequest:
        """승인 요청 생성 + 영속화.

        중복 방지: 동일 runbook_id + namespace에 WAITING 상태 요청이 이미 존재하면 차단.
        PendingRecoveryApprovalManager.create_request() 패턴.

        Raises:
            RunbookApprovalDuplicateError: 동일 런북+네임스페이스에 이미 대기 중인 승인 존재
        """
        # 중복 승인 요청 방지
        existing = self._find_waiting_request_by_runbook(runbook.id, ctx.namespace)
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
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=timer_seconds)).isoformat()

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
        """동일 runbook_id + namespace의 WAITING 상태 요청 검색.

        보조 인덱스 키를 사용하여 효율적으로 조회.
        """
        backend = self._get_backend()
        index_key = APPROVAL_INDEX_KEY.format(runbook_id=runbook_id, namespace=namespace)
        data = backend.get(index_key)
        if data and isinstance(data, dict):
            execution_id = data.get("execution_id")
            if execution_id:
                request = self._load_approval_request(execution_id)
                if request and request.status == ApprovalDecisionType.WAITING:
                    return request
        return None

    def _save_approval_request(self, request: RunbookApprovalRequest) -> None:
        """승인 요청 저장."""
        backend = self._get_backend()
        key = APPROVAL_REQUEST_KEY.format(execution_id=request.execution_id)
        backend.set(key, request.to_dict(), ttl_seconds=APPROVAL_REQUEST_TTL)

        # 보조 인덱스: runbook_id + namespace → execution_id
        index_key = APPROVAL_INDEX_KEY.format(
            runbook_id=request.runbook_id,
            namespace=request.namespace,
        )
        backend.set(
            index_key,
            {"execution_id": request.execution_id},
            ttl_seconds=APPROVAL_REQUEST_TTL,
        )

    def _cas_save_approval_request(
        self,
        request: RunbookApprovalRequest,
        expected_status: str,
    ) -> bool:
        """CAS (Compare-And-Set) 기반 승인 요청 상태 전환.

        승인 요청은 단방향 상태 전환(WAITING → 최종 상태)이므로
        version 카운터 대신 status 필드 기반 CAS로 충분하다.

        Args:
            request: 새 상태가 설정된 요청 객체
            expected_status: CAS 조건 — 현재 상태가 이 값일 때만 저장

        Returns:
            True if CAS succeeded, False if status already changed
        """
        backend = self._get_backend()
        key = APPROVAL_REQUEST_KEY.format(execution_id=request.execution_id)

        if hasattr(backend, "_client"):
            # Redis: Lua CAS
            try:
                make_key = getattr(backend, "_make_key", lambda k: k)
                result = backend._client.eval(
                    APPROVAL_CAS_SCRIPT,
                    1,
                    make_key(key),
                    json.dumps(request.to_dict(), default=str),
                    expected_status,
                )
                if result == 0:
                    return False  # 상태 불일치 — 정상적 CAS 실패
                return True
            except Exception:
                # Lua 호출 자체 실패 → Fail-Open 폴백
                logger.warning("runbook_approval.cas_lua_fallback", execution_id=request.execution_id)
                backend.set(key, request.to_dict(), ttl_seconds=APPROVAL_REQUEST_TTL)
                return True
        else:
            # InMemory/File: 단순 비교 + 저장 (테스트 환경)
            existing = backend.get(key)
            if existing and isinstance(existing, dict):
                if existing.get("status") != expected_status:
                    return False
            backend.set(key, request.to_dict(), ttl_seconds=APPROVAL_REQUEST_TTL)
            return True

    def _load_approval_request(
        self,
        execution_id: str,
    ) -> RunbookApprovalRequest | None:
        """승인 요청 로드."""
        backend = self._get_backend()
        key = APPROVAL_REQUEST_KEY.format(execution_id=execution_id)
        data = backend.get(key)
        if data and isinstance(data, dict):
            return RunbookApprovalRequest.from_dict(data)
        return None

    def _list_waiting_requests(self) -> list[RunbookApprovalRequest]:
        """WAITING 상태의 모든 승인 요청 조회.

        StateBackend.get_all()을 사용하여 패턴 매칭으로 조회한다.
        """
        backend = self._get_backend()
        requests: list[RunbookApprovalRequest] = []

        try:
            all_data = backend.get_all("selfhealing:runbook:approval:*")
            if all_data:
                for _key, value in all_data.items():
                    if isinstance(value, dict) and value.get("status") == "waiting":
                        try:
                            req = RunbookApprovalRequest.from_dict(value)
                            requests.append(req)
                        except (KeyError, ValueError):
                            continue
        except Exception:
            logger.warning("runbook_approval.list_waiting_failed")

        return requests

    # =========================================================================
    # 실행 재개 트리거
    # =========================================================================

    def _trigger_resume(self, execution_id: str) -> None:
        """승인 확정 후 Runbook 실행 재개 트리거.

        recovery_tasks.py의 apply_async() 이벤트 드리븐 패턴을 따른다.
        resume_runbook_task가 Governance Double-Check + Lock 획득 후 실행을 재개한다.
        """
        try:
            from selfhealing.services.runbook.tasks import resume_runbook_task

            resume_runbook_task.apply_async(
                args=[execution_id],
                queue="selfhealing_runbook",
            )
        except Exception as e:
            # 태스크 큐 연결 실패 시 로그만 기록 (테스트 환경 등)
            logger.warning(
                "runbook_approval.trigger_resume_failed",
                execution_id=execution_id,
                error=str(e),
            )

    # =========================================================================
    # DLQ 저장
    # =========================================================================

    def _store_timeout_to_dlq(
        self,
        request: RunbookApprovalRequest,
        elapsed_seconds: float,
    ) -> None:
        """타임아웃된 승인 요청을 DLQ에 저장.

        3단계 Fallback (LMDB → JSONL → stderr) 무손실 보장.
        """
        try:
            from selfhealing.services.dlq import store_to_dlq

            store_to_dlq(
                domain="runbook",
                failure_type="approval_timeout",
                entity_type="runbook_approval",
                entity_id=request.execution_id,
                error_message=(
                    f"Approval timed out after {elapsed_seconds:.0f}s "
                    f"for runbook '{request.runbook_id}' "
                    f"in namespace '{request.namespace}'"
                ),
                snapshot_data={
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
        except Exception as e:
            logger.warning(
                "runbook_approval.dlq_store_failed",
                execution_id=request.execution_id,
                error=str(e),
            )


# =============================================================================
# 모듈 수준 싱글톤
# =============================================================================

_approval_gate: RunbookApprovalGate | None = None


def get_runbook_approval_gate() -> RunbookApprovalGate:
    """RunbookApprovalGate 싱글톤 인스턴스."""
    global _approval_gate
    if _approval_gate is None:
        _approval_gate = RunbookApprovalGate()
    return _approval_gate


def reset_runbook_approval_gate() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _approval_gate
    _approval_gate = None
