"""
Audit Logger - Main entry point for audit logging.

Provides a unified interface for logging configuration changes
with privacy protection, tamper detection, and multi-backend support.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from selfhealing.audit.backends import (
    CompositeBackend,
    get_default_backend,
)
from selfhealing.audit.backends.base import AuditBackend
from selfhealing.audit.masking import (
    extract_ip_from_request,
    mask_ip,
    mask_sensitive_fields,
)
from selfhealing.audit.trace import get_trace_id, get_trace_id_full

logger = structlog.get_logger()


class ConfigAuditAction(str, Enum):
    """Types of audit actions."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    READ = "read"  # For sensitive config reads
    APPLY = "apply"  # For config application
    ROLLBACK = "rollback"
    VERIFY = "verify"  # Integrity verification
    EXPORT = "export"  # Bulk export


@dataclass
class AuditConfigChangeEvent:
    """
    Represents a configuration change event.

    This is the primary data structure for audit logging.
    """

    config_type: str
    config_key: str
    action: ConfigAuditAction | str
    old_value: Any = None
    new_value: Any = None
    reason: str | None = None
    user: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    source: str = "api"  # api, cli, system, scheduler
    apply_strategy: str | None = None
    apply_delay_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = asdict(self)
        if isinstance(result["action"], ConfigAuditAction):
            result["action"] = result["action"].value
        return result


class AuditLogger:
    """
    Main audit logger for configuration changes.

    Features:
    - Privacy-compliant IP masking
    - Old/new value tracking
    - Trace ID correlation
    - Structured JSON output
    - Hash chain integrity
    - Multi-backend support
    """

    # Singleton instance
    _instance: AuditLogger | None = None

    def __init__(
        self,
        backend: AuditBackend | None = None,
        mask_ip_addresses: bool = True,
        sensitive_fields: list[str] | None = None,
        enable_console_log: bool = True,
    ):
        """
        Initialize audit logger.

        Args:
            backend: Audit backend (defaults to LocalFileBackend)
            mask_ip_addresses: Whether to mask IP addresses for privacy
            sensitive_fields: List of field names to redact
            enable_console_log: Also log to standard logging
        """
        self._backend = backend or get_default_backend()
        self._mask_ip = mask_ip_addresses
        self._sensitive_fields = sensitive_fields or [
            "password",
            "secret",
            "token",
            "api_key",
            "private_key",
            "credit_card",
        ]
        self._enable_console = enable_console_log

    @classmethod
    def get_instance(cls) -> AuditLogger:
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def configure(
        cls,
        backend: AuditBackend | None = None,
        **kwargs,
    ) -> AuditLogger:
        """Configure the singleton instance."""
        cls._instance = cls(backend=backend, **kwargs)
        return cls._instance

    def log_change(
        self,
        event: AuditConfigChangeEvent | dict[str, Any],
        request=None,
    ) -> bool:
        """
        Log a configuration change event.

        Args:
            event: AuditConfigChangeEvent or dict with event data
            request: Optional Django/Flask request object for IP extraction

        Returns:
            True if successfully logged
        """
        try:
            # Convert to dict if needed
            if isinstance(event, AuditConfigChangeEvent):
                event_dict = event.to_dict()
            else:
                event_dict = dict(event)

            # Extract IP from request if available
            if request and not event_dict.get("ip_address"):
                event_dict["ip_address"] = extract_ip_from_request(request)
                if hasattr(request, "META"):
                    event_dict["user_agent"] = request.META.get("HTTP_USER_AGENT", "")

            # Build audit log entry
            entry = self._build_entry(event_dict)

            # Write to backend
            success = self._backend.write(entry)

            # Also log to console if enabled
            if self._enable_console:
                self._log_to_console(entry)

            return success

        except Exception as e:
            logger.exception(
                "audit_logger.failed_log_change",
                error=e,
            )
            return False

    def log_config_update(
        self,
        config_type: str,
        config_key: str,
        old_value: Any,
        new_value: Any,
        user: str | None = None,
        ip_address: str | None = None,
        reason: str | None = None,
        request=None,
        **kwargs,
    ) -> bool:
        """
        Convenience method for logging config updates.

        Args:
            config_type: Type of config (e.g., 'RETRY_CONFIG')
            config_key: Specific key being changed
            old_value: Value before change
            new_value: Value after change
            user: Username making the change
            ip_address: IP address of requester
            reason: Reason for change
            request: Django/Flask request object
            **kwargs: Additional metadata
        """
        event = AuditConfigChangeEvent(
            config_type=config_type,
            config_key=config_key,
            action=ConfigAuditAction.UPDATE,
            old_value=old_value,
            new_value=new_value,
            user=user,
            ip_address=ip_address,
            reason=reason,
            metadata=kwargs,
        )
        return self.log_change(event, request=request)

    def log_batch_update(
        self,
        config_type: str,
        changes: list[dict[str, Any]],
        user: str | None = None,
        request=None,
    ) -> bool:
        """
        Log multiple configuration changes as a batch.

        Args:
            config_type: Type of config
            changes: List of changes, each with key, old_value, new_value
            user: Username
            request: Request object
        """
        success = True
        batch_id = get_trace_id() or self._generate_batch_id()

        for change in changes:
            event = AuditConfigChangeEvent(
                config_type=config_type,
                config_key=change.get("key", ""),
                action=ConfigAuditAction.UPDATE,
                old_value=change.get("old_value"),
                new_value=change.get("new_value"),
                user=user,
                metadata={"batch_id": batch_id, "batch_size": len(changes)},
            )
            if not self.log_change(event, request=request):
                success = False

        return success

    def _build_entry(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Build the full audit log entry."""
        now = datetime.now(timezone.utc)

        # Mask sensitive data
        if self._mask_ip and event_dict.get("ip_address"):
            event_dict["ip_address"] = mask_ip(event_dict["ip_address"])

        # Redact sensitive fields in values
        if event_dict.get("old_value"):
            event_dict["old_value"] = mask_sensitive_fields(
                event_dict["old_value"],
                self._sensitive_fields,
            )
        if event_dict.get("new_value"):
            event_dict["new_value"] = mask_sensitive_fields(
                event_dict["new_value"],
                self._sensitive_fields,
            )

        # Get trace IDs (short for display, full for storage/correlation)
        trace_id = get_trace_id()
        trace_id_full = get_trace_id_full()  # 32자 hex (OTEL 활성화 시)

        entry = {
            "timestamp": now.isoformat(),
            "trace_id": trace_id,
            "trace_id_full": trace_id_full,  # 전체 W3C trace_id (글로벌 통합용)
            "event_type": "config_change",
            "actor": {
                "user": event_dict.get("user") or "system",
                "ip_address": event_dict.get("ip_address"),
                "user_agent": event_dict.get("user_agent"),
                "source": event_dict.get("source", "api"),
            },
            "change": {
                "config_type": event_dict.get("config_type"),
                "config_key": event_dict.get("config_key"),
                "action": event_dict.get("action"),
                "old_value": event_dict.get("old_value"),
                "new_value": event_dict.get("new_value"),
                "reason": event_dict.get("reason"),
            },
            "apply_strategy": (
                {
                    "strategy": event_dict.get("apply_strategy"),
                    "delay_seconds": event_dict.get("apply_delay_seconds"),
                }
                if event_dict.get("apply_strategy")
                else None
            ),
            "metadata": event_dict.get("metadata", {}),
        }

        # Remove None values for cleaner output
        entry = {k: v for k, v in entry.items() if v is not None}

        return entry

    def _log_to_console(self, entry: dict[str, Any]) -> None:
        """Log to standard Python logging."""
        change = entry.get("change", {})
        actor = entry.get("actor", {})

        logger.info(
            "audit.event",
            change=change.get("action", "unknown").upper(),
            config_type=change.get("config_type", ""),
            config_key=change.get("config_key", ""),
            actor=actor.get("user", "system"),
            ip_address=actor.get("ip_address", "unknown"),
        )

    def _generate_batch_id(self) -> str:
        """Generate a batch ID for grouped changes."""
        import uuid

        return f"batch-{uuid.uuid4().hex[:12]}"

    def verify_integrity(self) -> tuple:
        """Verify integrity of audit logs."""
        if hasattr(self._backend, "verify_integrity"):
            return self._backend.verify_integrity()

        # For composite backend, check primary
        if isinstance(self._backend, CompositeBackend):
            for backend in self._backend._backends:
                if hasattr(backend, "verify_integrity"):
                    return backend.verify_integrity()

        return True, []

    def query(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        config_type: str | None = None,
        user: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit logs."""
        return self._backend.query(
            start_time=start_time,
            end_time=end_time,
            config_type=config_type,
            user=user,
            limit=limit,
        )

    def get_backend_health(self) -> dict[str, Any]:
        """Get health status of all backends."""
        if isinstance(self._backend, CompositeBackend):
            return {
                "composite": True,
                "backends": [{"name": b.name, "health": b.health_check().__dict__} for b in self._backend._backends],
            }

        health = self._backend.health_check()
        return {"name": self._backend.name, "health": health.__dict__}

    def close(self) -> None:
        """Close the audit logger and flush buffers."""
        if hasattr(self._backend, "close"):
            self._backend.close()


# Convenience functions
def log_config_change(
    config_type: str,
    config_key: str,
    old_value: Any,
    new_value: Any,
    user: str | None = None,
    request=None,
    **kwargs,
) -> bool:
    """
    Log a configuration change using the global logger.

    This is the simplest way to log a config change.
    """
    return AuditLogger.get_instance().log_config_update(
        config_type=config_type,
        config_key=config_key,
        old_value=old_value,
        new_value=new_value,
        user=user,
        request=request,
        **kwargs,
    )


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance."""
    return AuditLogger.get_instance()


# 하위 호환 alias (deprecated)
ConfigChangeEvent = AuditConfigChangeEvent
