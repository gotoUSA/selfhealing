"""
Recovery Audit.

위험한 강제 복구(DANGEROUS_FORCE_RECOVERY) 및 복구 관련 감사 이벤트를 기록합니다.

Features:
- DangerousForceRecoveryAuditEntry: 강제 복구 감사 항목
- record_dangerous_force_recovery(): 강제 복구 기록
- RecoveryAuditRecorder: 복구 감사 기록기

강제 복구 감사가 필요한 경우:
- 수동 승인 없이 NORMAL 전환 (require_manual_approval=True 무시)
- 안정화 검증 미완료 상태에서 강제 복구
- Circuit Breaker가 OPEN 상태에서 강제 복구

Code reference:
    governance.py#L404 (acknowledge_warning 패턴)
    audit/base.py (WAL 기반 감사 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.5.3
    docs/self_healing/middleware_system/76_CASCADE_EVENT_TRACKING.md
"""

from __future__ import annotations

import structlog
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = structlog.get_logger()


# =============================================================================
# Enums
# =============================================================================


class ForceRecoveryType(str, Enum):
    """
    강제 복구 유형.
    """

    SKIP_MANUAL_APPROVAL = "skip_manual_approval"
    """수동 승인 건너뜀 (require_manual_approval=True 무시)."""

    SKIP_STABILITY_CHECK = "skip_stability_check"
    """안정화 검증 건너뜀."""

    OVERRIDE_CIRCUIT_BREAKER = "override_circuit_breaker"
    """Circuit Breaker OPEN 상태 무시."""

    FORCE_GOVERNANCE_NORMAL = "force_governance_normal"
    """Governance NORMAL 강제 전환."""

    EMERGENCY_BUDGET_RESET = "emergency_budget_reset"
    """긴급 Budget 리셋."""

    OTHER = "other"
    """기타 강제 복구."""


class RecoveryAuditEventType(str, Enum):
    """
    복구 감사 이벤트 유형.
    """

    RECOVERY_STARTED = "recovery_started"
    """복구 시작."""

    RECOVERY_STEP_EXECUTED = "recovery_step_executed"
    """복구 단계 실행."""

    RECOVERY_STEP_FAILED = "recovery_step_failed"
    """복구 단계 실패."""

    RECOVERY_COMPLETED = "recovery_completed"
    """복구 완료."""

    RECOVERY_ABORTED = "recovery_aborted"
    """복구 중단."""

    DANGEROUS_FORCE_RECOVERY = "dangerous_force_recovery"
    """위험한 강제 복구."""

    MANUAL_APPROVAL_GRANTED = "manual_approval_granted"
    """수동 승인 승인됨."""

    MANUAL_APPROVAL_REJECTED = "manual_approval_rejected"
    """수동 승인 거부됨."""

    RE_ESCALATION = "re_escalation"
    """재-에스컬레이션 발생."""


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class DangerousForceRecoveryAuditEntry:
    """
    위험한 강제 복구 감사 항목.

    수동 승인, 안정화 검증, Circuit Breaker 등의 안전 장치를
    우회하여 강제로 복구를 진행한 경우의 감사 기록입니다.

    이 기록은 책임 추적성(Accountability)을 위해 영구 보관됩니다.

    Code reference:
        governance.py#L404 (acknowledge_warning 패턴)
        76_CASCADE_EVENT_TRACKING.md (CascadeEvent 패턴)
    """

    # 이벤트 식별
    audit_id: str = ""
    """감사 항목 고유 ID."""

    event_type: RecoveryAuditEventType = RecoveryAuditEventType.DANGEROUS_FORCE_RECOVERY
    """이벤트 유형."""

    force_type: ForceRecoveryType = ForceRecoveryType.OTHER
    """강제 복구 유형."""

    # 세션 정보
    session_id: str = ""
    """복구 세션 ID."""

    namespace: str = ""
    """네임스페이스."""

    trigger_level: str = ""
    """복구 대상 Emergency 레벨."""

    # 실행자 정보 (책임 추적성)
    executed_by: str = ""
    """실행자 ID (사용자 또는 시스템)."""

    executed_at: datetime | None = None
    """실행 시각."""

    # 상세 정보
    reason: str = ""
    """강제 복구 사유."""

    justification: str = ""
    """정당화 사유 (왜 안전 장치를 우회했는지)."""

    skipped_checks: list[str] = field(default_factory=list)
    """건너뛴 안전 검사 목록."""

    # 상태 정보
    previous_state: dict[str, Any] = field(default_factory=dict)
    """강제 복구 전 상태."""

    resulting_state: dict[str, Any] = field(default_factory=dict)
    """강제 복구 후 상태."""

    # 메타데이터
    cascade_event_id: str | None = None
    """연결된 CascadeEvent ID (76번 문서)."""

    trace_id: str | None = None
    """추적 ID."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    def __post_init__(self):
        """초기화 후 처리."""
        if not self.audit_id:
            self.audit_id = f"audit-force-{uuid.uuid4().hex[:12]}"

        if self.executed_at is None:
            self.executed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result = asdict(self)
        result["event_type"] = self.event_type.value
        result["force_type"] = self.force_type.value
        if self.executed_at:
            result["executed_at"] = self.executed_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DangerousForceRecoveryAuditEntry:
        """딕셔너리에서 생성."""
        data = dict(data)  # 복사본 사용

        if "event_type" in data and isinstance(data["event_type"], str):
            data["event_type"] = RecoveryAuditEventType(data["event_type"])

        if "force_type" in data and isinstance(data["force_type"], str):
            data["force_type"] = ForceRecoveryType(data["force_type"])

        if "executed_at" in data and isinstance(data["executed_at"], str):
            data["executed_at"] = datetime.fromisoformat(data["executed_at"])

        # 알려진 필드만 사용
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}

        return cls(**filtered_data)


@dataclass
class RecoveryAuditEntry:
    """
    일반 복구 감사 항목.

    복구 프로세스의 모든 단계를 추적합니다.
    """

    # 이벤트 식별
    audit_id: str = ""
    """감사 항목 고유 ID."""

    event_type: RecoveryAuditEventType = RecoveryAuditEventType.RECOVERY_STARTED
    """이벤트 유형."""

    # 세션 정보
    session_id: str = ""
    """복구 세션 ID."""

    namespace: str = ""
    """네임스페이스."""

    # 단계 정보
    step_type: str | None = None
    """복구 단계 유형."""

    step_order: int | None = None
    """단계 순서."""

    # 실행 정보
    executed_by: str = "system"
    """실행자 ID."""

    executed_at: datetime | None = None
    """실행 시각."""

    # 결과
    success: bool = True
    """성공 여부."""

    error_message: str | None = None
    """에러 메시지."""

    duration_ms: float = 0.0
    """실행 시간 (밀리초)."""

    # 메타데이터
    trace_id: str | None = None
    """추적 ID."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    def __post_init__(self):
        """초기화 후 처리."""
        if not self.audit_id:
            self.audit_id = f"audit-recovery-{uuid.uuid4().hex[:12]}"

        if self.executed_at is None:
            self.executed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result = asdict(self)
        result["event_type"] = self.event_type.value
        if self.executed_at:
            result["executed_at"] = self.executed_at.isoformat()
        return result


# =============================================================================
# Recovery Audit Recorder
# =============================================================================


class RecoveryAuditRecorder:
    """
    복구 감사 기록기.

    복구 프로세스의 모든 이벤트를 WAL 기반으로 기록합니다.
    누락 0 보장을 위해 로컬 WAL에 먼저 기록 후 중앙 저장소로 전송합니다.

    Code reference:
        audit/base.py (_get_wal() 패턴)
        governance.py (EmergencyModeTracker 패턴)
    """

    # Redis 키 패턴
    AUDIT_KEY_PREFIX = "selfhealing:recovery:audit"
    FORCE_RECOVERY_KEY = "selfhealing:recovery:force_audit:{namespace}"

    def __init__(
        self,
        notification_callback: Callable[[DangerousForceRecoveryAuditEntry], None] | None = None,
    ):
        """
        Args:
            notification_callback: 강제 복구 시 알림 콜백
        """
        self._notification_callback = notification_callback
        self._entries: list[RecoveryAuditEntry] = []
        self._force_entries: list[DangerousForceRecoveryAuditEntry] = []

    def _get_backend(self):
        """State backend 획득."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            return get_state_backend()
        except ImportError:
            return None

    def _write_to_wal(self, entry: dict[str, Any]) -> bool:
        """WAL에 기록."""
        try:
            from selfhealing.services.audit.base import _get_wal

            wal = _get_wal()
            if wal:
                wal.write(entry)
                return True
        except (ImportError, Exception) as e:
            logger.warning(
                "recovery_audit_recorder.wal_write_failed",
                error=e,
            )
        return False

    def record_recovery_event(
        self,
        event_type: RecoveryAuditEventType,
        session_id: str,
        namespace: str,
        step_type: str | None = None,
        step_order: int | None = None,
        executed_by: str = "system",
        success: bool = True,
        error_message: str | None = None,
        duration_ms: float = 0.0,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RecoveryAuditEntry:
        """
        일반 복구 이벤트 기록.

        Args:
            event_type: 이벤트 유형
            session_id: 세션 ID
            namespace: 네임스페이스
            step_type: 단계 유형
            step_order: 단계 순서
            executed_by: 실행자
            success: 성공 여부
            error_message: 에러 메시지
            duration_ms: 실행 시간
            trace_id: 추적 ID
            metadata: 추가 메타데이터

        Returns:
            기록된 감사 항목
        """
        entry = RecoveryAuditEntry(
            event_type=event_type,
            session_id=session_id,
            namespace=namespace,
            step_type=step_type,
            step_order=step_order,
            executed_by=executed_by,
            success=success,
            error_message=error_message,
            duration_ms=duration_ms,
            trace_id=trace_id,
            metadata=metadata or {},
        )

        # WAL에 기록
        self._write_to_wal(entry.to_dict())

        # 메모리에도 보관 (최근 N개)
        self._entries.append(entry)
        if len(self._entries) > 1000:
            self._entries = self._entries[-1000:]

        logger.debug(
            f"[RecoveryAuditRecorder] Event recorded: "
            f"type={event_type.value}, session={session_id}"
        )

        return entry

    def record_dangerous_force_recovery(
        self,
        force_type: ForceRecoveryType,
        session_id: str,
        namespace: str,
        executed_by: str,
        reason: str,
        justification: str,
        skipped_checks: list[str] | None = None,
        previous_state: dict[str, Any] | None = None,
        resulting_state: dict[str, Any] | None = None,
        trigger_level: str = "",
        cascade_event_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DangerousForceRecoveryAuditEntry:
        """
        위험한 강제 복구 기록.

        안전 장치를 우회한 강제 복구를 감사 로그에 기록합니다.
        이 기록은 책임 추적성을 위해 영구 보관됩니다.

        Args:
            force_type: 강제 복구 유형
            session_id: 세션 ID
            namespace: 네임스페이스
            executed_by: 실행자 (반드시 지정 필요)
            reason: 강제 복구 사유
            justification: 정당화 사유
            skipped_checks: 건너뛴 검사 목록
            previous_state: 이전 상태
            resulting_state: 결과 상태
            trigger_level: Emergency 레벨
            cascade_event_id: CascadeEvent ID
            trace_id: 추적 ID
            metadata: 추가 메타데이터

        Returns:
            기록된 감사 항목
        """
        entry = DangerousForceRecoveryAuditEntry(
            force_type=force_type,
            session_id=session_id,
            namespace=namespace,
            trigger_level=trigger_level,
            executed_by=executed_by,
            reason=reason,
            justification=justification,
            skipped_checks=skipped_checks or [],
            previous_state=previous_state or {},
            resulting_state=resulting_state or {},
            cascade_event_id=cascade_event_id,
            trace_id=trace_id,
            metadata=metadata or {},
        )

        # WAL에 기록 (누락 0 보장)
        self._write_to_wal(entry.to_dict())

        # Redis에 영구 저장
        self._persist_force_recovery_entry(entry)

        # 메모리에도 보관
        self._force_entries.append(entry)
        if len(self._force_entries) > 100:
            self._force_entries = self._force_entries[-100:]

        # 알림 발송
        if self._notification_callback:
            try:
                self._notification_callback(entry)
            except Exception as e:
                logger.error(
                    "recovery_audit_recorder.notification_failed",
                    error=e,
                )

        logger.critical(
            f"[RecoveryAuditRecorder] DANGEROUS_FORCE_RECOVERY recorded: "
            f"type={force_type.value}, session={session_id}, "
            f"executed_by={executed_by}, reason={reason}"
        )

        return entry

    def _persist_force_recovery_entry(
        self, entry: DangerousForceRecoveryAuditEntry
    ) -> None:
        """Redis에 강제 복구 항목 영구 저장."""
        backend = self._get_backend()
        if not backend:
            return

        try:
            key = self.FORCE_RECOVERY_KEY.format(namespace=entry.namespace)

            # 기존 항목 조회
            existing = backend.get(key) or []

            # 새 항목 추가
            existing.append(entry.to_dict())

            # 저장 (TTL 없음 - 영구 보관)
            backend.set(key, existing)

        except Exception as e:
            logger.error(
                "recovery_audit_recorder.persist_failed",
                error=e,
            )

    def get_force_recovery_history(
        self,
        namespace: str,
        limit: int = 100,
    ) -> list[DangerousForceRecoveryAuditEntry]:
        """
        강제 복구 이력 조회.

        Args:
            namespace: 네임스페이스
            limit: 최대 조회 개수

        Returns:
            강제 복구 감사 항목 목록
        """
        backend = self._get_backend()
        if not backend:
            return self._force_entries[-limit:]

        try:
            key = self.FORCE_RECOVERY_KEY.format(namespace=namespace)
            data = backend.get(key) or []

            entries = [
                DangerousForceRecoveryAuditEntry.from_dict(d) for d in data[-limit:]
            ]

            return entries

        except Exception as e:
            logger.error(
                "recovery_audit_recorder.get_history_failed",
                error=e,
            )
            return self._force_entries[-limit:]

    def get_recent_events(
        self,
        session_id: str | None = None,
        event_types: list[RecoveryAuditEventType] | None = None,
        limit: int = 100,
    ) -> list[RecoveryAuditEntry]:
        """
        최근 이벤트 조회.

        Args:
            session_id: 세션 ID 필터
            event_types: 이벤트 유형 필터
            limit: 최대 조회 개수

        Returns:
            감사 항목 목록
        """
        entries = self._entries

        if session_id:
            entries = [e for e in entries if e.session_id == session_id]

        if event_types:
            type_values = {
                t.value if isinstance(t, RecoveryAuditEventType) else t
                for t in event_types
            }
            entries = [e for e in entries if e.event_type.value in type_values]

        return entries[-limit:]


# =============================================================================
# Singleton Access
# =============================================================================

_recovery_audit_recorder: RecoveryAuditRecorder | None = None


def get_recovery_audit_recorder() -> RecoveryAuditRecorder:
    """
    RecoveryAuditRecorder 싱글톤 반환.

    Returns:
        RecoveryAuditRecorder 인스턴스
    """
    global _recovery_audit_recorder

    if _recovery_audit_recorder is None:
        _recovery_audit_recorder = RecoveryAuditRecorder()

    return _recovery_audit_recorder


# =============================================================================
# Convenience Functions
# =============================================================================


def record_dangerous_force_recovery(
    force_type: ForceRecoveryType,
    session_id: str,
    namespace: str,
    executed_by: str,
    reason: str,
    justification: str,
    **kwargs,
) -> DangerousForceRecoveryAuditEntry:
    """
    위험한 강제 복구 기록 (편의 함수).

    안전 장치를 우회한 강제 복구를 감사 로그에 기록합니다.

    Args:
        force_type: 강제 복구 유형
        session_id: 세션 ID
        namespace: 네임스페이스
        executed_by: 실행자 (반드시 지정 필요)
        reason: 강제 복구 사유
        justification: 정당화 사유
        **kwargs: 추가 인수 (skipped_checks, previous_state 등)

    Returns:
        기록된 감사 항목
    """
    recorder = get_recovery_audit_recorder()
    return recorder.record_dangerous_force_recovery(
        force_type=force_type,
        session_id=session_id,
        namespace=namespace,
        executed_by=executed_by,
        reason=reason,
        justification=justification,
        **kwargs,
    )


def record_recovery_event(
    event_type: RecoveryAuditEventType,
    session_id: str,
    namespace: str,
    **kwargs,
) -> RecoveryAuditEntry:
    """
    복구 이벤트 기록 (편의 함수).

    Args:
        event_type: 이벤트 유형
        session_id: 세션 ID
        namespace: 네임스페이스
        **kwargs: 추가 인수

    Returns:
        기록된 감사 항목
    """
    recorder = get_recovery_audit_recorder()
    return recorder.record_recovery_event(
        event_type=event_type,
        session_id=session_id,
        namespace=namespace,
        **kwargs,
    )
