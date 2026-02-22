"""
Interlock 우회 감사 로거.

Emergency Override (Break Glass) 사용을 감사 로그에 기록합니다.
규정 준수(Compliance) 및 Post-Incident Review에 활용됩니다.

주요 기능:
- InterlockBypassAuditEntry: 감사 로그 엔트리
- InterlockBypassAuditor: 감사 로깅 및 조회

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.14
"""

from __future__ import annotations

import structlog
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.services.canary.override import EmergencyOverrideRequest

logger = structlog.get_logger()


@dataclass
class InterlockBypassAuditEntry:
    """
    Interlock 우회 감사 로그 엔트리.

    Attributes:
        audit_id: 감사 ID
        timestamp: 발생 시각 ISO 형식
        rollout_id: 롤아웃 ID
        config_type: 설정 유형
        operation: 수행한 작업
        emergency_level: Emergency 레벨 값
        emergency_level_name: Emergency 레벨 이름
        namespace: 네임스페이스
        bypassed_by: 우회 요청자
        bypass_reason: 우회 사유
        ticket_id: 관련 티켓 ID
        acknowledged_risks: 인지한 위험 목록
    """

    audit_id: str
    """감사 ID."""

    timestamp: str
    """발생 시각 ISO 형식."""

    rollout_id: str
    """롤아웃 ID."""

    config_type: str
    """설정 유형 (circuit_breaker, dlq 등)."""

    operation: str
    """수행한 작업 (promote, rollback 등)."""

    emergency_level: int
    """Emergency 레벨 값."""

    emergency_level_name: str
    """Emergency 레벨 이름."""

    namespace: str
    """네임스페이스."""

    bypassed_by: str
    """우회 요청자."""

    bypass_reason: str
    """우회 사유."""

    ticket_id: str | None = None
    """관련 티켓 ID."""

    acknowledged_risks: list[str] = field(default_factory=list)
    """인지한 위험 목록."""

    @property
    def severity(self) -> str:
        """심각도 (레벨 기반)."""
        if self.emergency_level >= 3:
            return "CRITICAL"
        elif self.emergency_level >= 2:
            return "HIGH"
        elif self.emergency_level >= 1:
            return "MEDIUM"
        return "LOW"

    @property
    def requires_incident_review(self) -> bool:
        """인시던트 리뷰 필요 여부."""
        return self.emergency_level >= 2

    @property
    def incident_review_due_hours(self) -> int:
        """인시던트 리뷰 마감 시간 (시간)."""
        if self.emergency_level >= 3:
            return 48
        elif self.emergency_level >= 2:
            return 72
        return 168  # 1 week

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        # 리뷰 마감 시각 계산
        timestamp_dt = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        review_due_at = timestamp_dt + timedelta(hours=self.incident_review_due_hours)

        return {
            "event_type": "DANGEROUS_BYPASS_INTERLOCK",
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "rollout_id": self.rollout_id,
            "config_type": self.config_type,
            "operation": self.operation,
            "emergency_level": self.emergency_level,
            "emergency_level_name": self.emergency_level_name,
            "namespace": self.namespace,
            "severity": self.severity,
            "bypassed_by": self.bypassed_by,
            "bypass_reason": self.bypass_reason,
            "ticket_id": self.ticket_id,
            "acknowledged_risks": self.acknowledged_risks,
            "governance": {
                "requires_incident_review": self.requires_incident_review,
                "incident_review_due_hours": self.incident_review_due_hours,
                "review_due_at": review_due_at.isoformat(),
            },
        }


class InterlockBypassAuditor:
    """
    Interlock 우회 감사 로거.

    Emergency Override (Break Glass) 사용을 감사 로그에 기록합니다.

    Features:
    - 우회 이벤트 기록
    - 이력 조회
    - PIR 대기 항목 조회
    """

    def __init__(self, enable_notifications: bool = True):
        """
        InterlockBypassAuditor 초기화.

        Args:
            enable_notifications: 알림 활성화 여부
        """
        self._entries: list[InterlockBypassAuditEntry] = []
        self._lock = threading.Lock()
        self._enable_notifications = enable_notifications

    def record_bypass(
        self,
        rollout_id: str,
        config_type: str,
        operation: str,
        emergency_level: int,
        emergency_level_name: str,
        namespace: str,
        override: EmergencyOverrideRequest,
    ) -> InterlockBypassAuditEntry:
        """
        우회 이벤트 기록.

        Args:
            rollout_id: 롤아웃 ID
            config_type: 설정 유형
            operation: 수행한 작업
            emergency_level: Emergency 레벨 값
            emergency_level_name: Emergency 레벨 이름
            namespace: 네임스페이스
            override: EmergencyOverrideRequest

        Returns:
            생성된 감사 로그 엔트리
        """
        entry = InterlockBypassAuditEntry(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            rollout_id=rollout_id,
            config_type=config_type,
            operation=operation,
            emergency_level=emergency_level,
            emergency_level_name=emergency_level_name,
            namespace=namespace,
            bypassed_by=override.requested_by,
            bypass_reason=override.reason,
            ticket_id=override.ticket_id,
            acknowledged_risks=override.acknowledged_risks.copy(),
        )

        with self._lock:
            self._entries.append(entry)

        # 로깅
        logger.warning(
            f"[AUDIT] Interlock Bypass: "
            f"id={entry.audit_id}, "
            f"by={entry.bypassed_by}, "
            f"reason={entry.bypass_reason[:50]}..., "
            f"level={entry.emergency_level_name}, "
            f"severity={entry.severity}"
        )

        return entry

    def get_entries(self, limit: int = 100) -> list[InterlockBypassAuditEntry]:
        """
        기록된 항목 조회.

        Args:
            limit: 최대 조회 개수

        Returns:
            감사 로그 엔트리 목록
        """
        with self._lock:
            return list(self._entries[-limit:])

    def get_entry(self, audit_id: str) -> InterlockBypassAuditEntry | None:
        """
        ID로 항목 조회.

        Args:
            audit_id: 감사 ID

        Returns:
            감사 로그 엔트리 (없으면 None)
        """
        with self._lock:
            for entry in self._entries:
                if entry.audit_id == audit_id:
                    return entry
        return None

    def get_pending_reviews(self) -> list[InterlockBypassAuditEntry]:
        """
        PIR 대기 중인 항목 조회.

        Returns:
            인시던트 리뷰가 필요한 항목 목록
        """
        with self._lock:
            return [e for e in self._entries if e.requires_incident_review]

    def clear(self) -> None:
        """모든 항목 삭제 (테스트용)."""
        with self._lock:
            self._entries.clear()


# =============================================================================
# Singleton
# =============================================================================

_bypass_auditor: InterlockBypassAuditor | None = None
_bypass_auditor_lock = threading.Lock()


def get_interlock_bypass_auditor() -> InterlockBypassAuditor:
    """
    InterlockBypassAuditor 싱글톤 반환.

    Returns:
        InterlockBypassAuditor 인스턴스
    """
    global _bypass_auditor

    if _bypass_auditor is None:
        with _bypass_auditor_lock:
            if _bypass_auditor is None:
                _bypass_auditor = InterlockBypassAuditor()

    return _bypass_auditor


def reset_interlock_bypass_auditor() -> None:
    """
    InterlockBypassAuditor 싱글톤 초기화 (테스트용).
    """
    global _bypass_auditor
    with _bypass_auditor_lock:
        if _bypass_auditor is not None:
            _bypass_auditor.clear()
        _bypass_auditor = None


__all__ = [
    "InterlockBypassAuditEntry",
    "InterlockBypassAuditor",
    "get_interlock_bypass_auditor",
    "reset_interlock_bypass_auditor",
]
