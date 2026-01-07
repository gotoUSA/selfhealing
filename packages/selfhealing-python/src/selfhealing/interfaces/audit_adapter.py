"""
Audit Log Adapter Interface

Provides an abstraction for audit logging, allowing users to choose
where and how audit logs are stored without being tied to any specific
storage backend.

Design Philosophy:
- No forced dependencies on user's system (no DB tables, no external services)
- User chooses: file, stdout, their own DB, Grafana/Loki, or custom solution
- Default is non-invasive (file or stdout)

Usage:
    # Use default file adapter
    from selfhealing.adapters.audit import FileAuditLogAdapter
    adapter = FileAuditLogAdapter("logs/audit.log")

    # Or implement your own
    class MyGrafanaAdapter(AuditLogAdapter):
        def log(self, entry: AuditEntry) -> None:
            loki_client.push(entry.to_dict())
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    """Standard audit action types."""

    # Circuit Breaker
    CB_FORCE_OPEN = "cb_force_open"
    CB_FORCE_CLOSE = "cb_force_close"
    CB_AUTO_OPEN = "cb_auto_open"
    CB_AUTO_CLOSE = "cb_auto_close"
    CB_HALF_OPEN = "cb_half_open"

    # DLQ
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY_START = "dlq_replay_start"
    DLQ_REPLAY_SUCCESS = "dlq_replay_success"
    DLQ_REPLAY_FAILED = "dlq_replay_failed"
    DLQ_ESCALATE = "dlq_escalate"
    DLQ_RESOLVE = "dlq_resolve"
    DLQ_REJECT = "dlq_reject"

    # Retry
    RETRY_ATTEMPT = "retry_attempt"
    RETRY_SUCCESS = "retry_success"
    RETRY_EXHAUSTED = "retry_exhausted"

    # Security
    SECURITY_INCIDENT = "security_incident"
    SECURITY_ALERT = "security_alert"

    # Governance (자동화 차단 추적)
    GOVERNANCE_BLOCKED = "governance_blocked"
    GOVERNANCE_KILL_SWITCH = "governance_kill_switch"
    GOVERNANCE_EMERGENCY = "governance_emergency"
    GOVERNANCE_ERROR_BUDGET = "governance_error_budget"

    # System
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"

    # Auto Tuning (자율 조정)
    AUTO_TUNING_ADJUSTMENT = "auto_tuning_adjustment"
    AUTO_TUNING_ENABLED = "auto_tuning_enabled"
    AUTO_TUNING_DISABLED = "auto_tuning_disabled"
    AUTO_TUNING_BOUNDS_CHANGED = "auto_tuning_bounds_changed"
    AUTO_TUNING_REJECTED = "auto_tuning_rejected"  # 안전 한계 초과
    AUTO_TUNING_ROLLBACK = "auto_tuning_rollback"

    # DNA Drift (구성 드리프트)
    DNA_DRIFT_DETECTED = "dna_drift_detected"
    DNA_DRIFT_RESOLVED = "dna_drift_resolved"

    # Compliance (규정 준수)
    COMPLIANCE_CHECK = "compliance_check"
    COMPLIANCE_VIOLATION = "compliance_violation"


class ContextType(str, Enum):
    """
    Audit 이벤트 발생 컨텍스트 유형.
    
    미들웨어 vs Celery Task vs 시스템 자동화를 구분하여
    분석 시 일관된 필터링이 가능합니다.
    
    업계 사례:
    - AWS CloudTrail: eventSource + eventType
    - Datadog APM: trace.origin
    - OpenTelemetry: SpanKind
    """
    REQUEST = "request"      # HTTP 요청 처리 중 (미들웨어)
    TASK = "task"            # 백그라운드 태스크 (Celery, RQ)
    SYSTEM = "system"        # 시스템 자동화 (스케줄러, 자동 복구)
    WEBHOOK = "webhook"      # 외부 웹훅 처리
    CLI = "cli"              # CLI 명령 실행
    UNKNOWN = "unknown"      # 알 수 없음 (폴백)


def _get_default_actor() -> tuple[Optional[str], str, list[str]]:
    """
    Get default actor from ActorContext if available.

    Returns (actor_id, actor_type, roles) tuple.
    Falls back to (None, "system", []) if ActorContext not available.
    
    Phase 25: roles 도 함께 반환하여 RBAC-Audit 연동 지원.
    """
    try:
        from selfhealing.context.actor_context import ActorContext

        if ActorContext.is_set():
            actor = ActorContext.get_current()
            return actor.actor_id, actor.actor_type, actor.roles
    except ImportError:
        pass
    return None, "system", []


@dataclass
class AuditEntry:
    """
    Audit log entry containing all relevant context.

    Captures:
    - What happened (action)
    - Who did it (actor_id, actor_type) - 자동으로 ActorContext에서 가져옴
    - What was affected (target_type, target_id)
    - Why (reason)
    - Additional context (details)

    Note:
        actor_id와 actor_type은 명시적으로 설정하지 않으면
        ActorContext에서 자동으로 가져옵니다.
        이를 통해 "누가 언제 설정했는지" 자동 추적됩니다.
    """

    action: AuditAction | str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Actor information - 자동으로 ActorContext에서 가져옴
    actor_id: Optional[str] = field(default=None)
    actor_type: str = field(default="system")
    actor_roles: list[str] = field(default_factory=list)  # Phase 25: RBAC 역할
    
    # Context type - 이벤트 발생 환경 구분 (미들웨어/태스크/시스템)
    context_type: ContextType = field(default=ContextType.UNKNOWN)

    # Target information
    target_type: Optional[str] = None  # circuit_breaker, dlq_entry, etc.
    target_id: Optional[str] = None

    # Context
    service_name: Optional[str] = None
    domain: Optional[str] = None
    reason: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    # Result
    success: bool = True
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        """
        Post-init: ActorContext에서 actor 정보 자동 채우기.

        actor_id가 명시적으로 설정되지 않았으면 ActorContext에서 가져옵니다.
        이를 통해 "누가 이 설정을 변경했는지" 자동 추적됩니다.
        
        Phase 25: actor_roles도 자동으로 채움.
        """
        # actor_id가 None이고 actor_type이 기본값 "system"이면 자동 채우기
        if self.actor_id is None and self.actor_type == "system":
            auto_actor_id, auto_actor_type, auto_roles = _get_default_actor()
            if auto_actor_id is not None:
                # Use object.__setattr__ for frozen-like behavior compatibility
                object.__setattr__(self, "actor_id", auto_actor_id)
                object.__setattr__(self, "actor_type", auto_actor_type)
                # Phase 25: roles도 자동 채우기
                if auto_roles and not self.actor_roles:
                    object.__setattr__(self, "actor_roles", auto_roles)
        
        # Phase 25: actor_id가 설정되었지만 actor_roles가 비어있으면 ActorContext에서 가져오기
        if not self.actor_roles:
            try:
                from selfhealing.context.actor_context import ActorContext
                if ActorContext.is_set():
                    actor = ActorContext.get_current()
                    if actor.roles:
                        object.__setattr__(self, "actor_roles", actor.roles)
            except ImportError:
                pass

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "action": self.action.value if isinstance(self.action, AuditAction) else self.action,
            "timestamp": self.timestamp.isoformat(),
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "actor_roles": self.actor_roles,
            "context_type": self.context_type.value if isinstance(self.context_type, ContextType) else self.context_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "service_name": self.service_name,
            "domain": self.domain,
            "reason": self.reason,
            "details": self.details,
            "success": self.success,
            "error_message": self.error_message,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


class AuditLogAdapter(ABC):
    """
    Abstract interface for audit logging.

    Implementations can store audit logs in:
    - Files (FileAuditLogAdapter)
    - stdout (StdoutAuditLogAdapter)
    - Database (user implements)
    - External services like Loki, Datadog (user implements)
    - Nowhere (NullAuditLogAdapter)
    """

    @abstractmethod
    def log(self, entry: AuditEntry) -> None:
        """
        Log an audit entry.

        Args:
            entry: The audit entry to log
        """
        pass

    @abstractmethod
    def query(
        self,
        action: Optional[AuditAction | str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """
        Query audit logs (optional - may not be supported by all adapters).

        Args:
            action: Filter by action type
            target_type: Filter by target type
            target_id: Filter by target ID
            start_time: Filter from this time
            end_time: Filter until this time
            limit: Maximum entries to return

        Returns:
            List of matching audit entries
        """
        pass

    def log_cb_open(
        self,
        service_name: str,
        reason: str,
        actor_id: Optional[str] = None,
        is_manual: bool = True,
    ) -> None:
        """Convenience method for Circuit Breaker open."""
        self.log(
            AuditEntry(
                action=AuditAction.CB_FORCE_OPEN if is_manual else AuditAction.CB_AUTO_OPEN,
                service_name=service_name,
                target_type="circuit_breaker",
                target_id=service_name,
                actor_id=actor_id,
                actor_type="user" if is_manual else "system",
                reason=reason,
            )
        )

    def log_cb_close(
        self,
        service_name: str,
        reason: str,
        actor_id: Optional[str] = None,
        is_manual: bool = True,
        trigger_replay: bool = False,
    ) -> None:
        """Convenience method for Circuit Breaker close."""
        self.log(
            AuditEntry(
                action=AuditAction.CB_FORCE_CLOSE if is_manual else AuditAction.CB_AUTO_CLOSE,
                service_name=service_name,
                target_type="circuit_breaker",
                target_id=service_name,
                actor_id=actor_id,
                actor_type="user" if is_manual else "system",
                reason=reason,
                details={"trigger_replay": trigger_replay},
            )
        )

    def log_dlq_store(
        self,
        dlq_id: int,
        domain: str,
        failure_type: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Convenience method for DLQ storage."""
        self.log(
            AuditEntry(
                action=AuditAction.DLQ_STORE,
                domain=domain,
                target_type="dlq_entry",
                target_id=str(dlq_id),
                details={
                    "failure_type": failure_type,
                    "error_message": error_message[:200] if error_message else None,
                },
            )
        )

    def log_dlq_replay(
        self,
        dlq_id: int,
        domain: str,
        success: bool,
        actor_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Convenience method for DLQ replay."""
        self.log(
            AuditEntry(
                action=AuditAction.DLQ_REPLAY_SUCCESS if success else AuditAction.DLQ_REPLAY_FAILED,
                domain=domain,
                target_type="dlq_entry",
                target_id=str(dlq_id),
                actor_id=actor_id,
                actor_type="user" if actor_id else "system",
                success=success,
                error_message=error_message,
            )
        )

    def log_retry(
        self,
        domain: str,
        func_name: str,
        attempt: int,
        max_attempts: int,
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """Convenience method for retry attempts."""
        if success:
            action = AuditAction.RETRY_SUCCESS
        elif attempt >= max_attempts:
            action = AuditAction.RETRY_EXHAUSTED
        else:
            action = AuditAction.RETRY_ATTEMPT

        self.log(
            AuditEntry(
                action=action,
                domain=domain,
                target_type="operation",
                target_id=func_name,
                details={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
                success=success,
                error_message=error_message,
            )
        )

    def log_governance_blocked(
        self,
        block_reason: str,
        operation_name: str,
        details: Optional[dict[str, Any]] = None,
        service_name: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> None:
        """
        거버넌스에 의해 자동화가 차단된 경우 기록.

        이 메서드는 '왜 이때 작업이 안 됐지?'라는 질문에
        명확한 답변을 제공합니다.

        Args:
            block_reason: 차단 사유 (kill_switch, emergency_mode, error_budget)
            operation_name: 차단된 작업 이름
            details: 추가 상세 정보 (emergency_level, budget_percent 등)
            service_name: 관련 서비스 이름
            domain: 도메인 (payment, point 등)
        """
        # block_reason에 따라 적절한 action 선택
        action_map = {
            "kill_switch": AuditAction.GOVERNANCE_KILL_SWITCH,
            "emergency_mode": AuditAction.GOVERNANCE_EMERGENCY,
            "error_budget": AuditAction.GOVERNANCE_ERROR_BUDGET,
        }
        action = action_map.get(block_reason, AuditAction.GOVERNANCE_BLOCKED)

        self.log(
            AuditEntry(
                action=action,
                service_name=service_name,
                domain=domain,
                target_type="automation",
                target_id=operation_name,
                reason=f"Governance blocked: {block_reason}",
                details=details or {},
                success=False,
                error_message=f"Operation '{operation_name}' blocked by {block_reason}",
            )
        )
