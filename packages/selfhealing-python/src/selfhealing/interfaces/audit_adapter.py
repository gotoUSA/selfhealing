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
from datetime import datetime
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

    # System
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"


@dataclass
class AuditEntry:
    """
    Audit log entry containing all relevant context.

    Captures:
    - What happened (action)
    - Who did it (actor_id, actor_type)
    - What was affected (target_type, target_id)
    - Why (reason)
    - Additional context (details)
    """

    action: AuditAction | str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Actor information
    actor_id: Optional[str] = None
    actor_type: str = "system"  # system, user, scheduler, etc.

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

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "action": self.action.value if isinstance(self.action, AuditAction) else self.action,
            "timestamp": self.timestamp.isoformat(),
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
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
