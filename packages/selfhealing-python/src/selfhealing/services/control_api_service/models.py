"""
Control API Service - Models

ReasonClassification, ControlRequest, ControlResponse 데이터 모델 정의.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

# =============================================================================
# Reason Classification
# =============================================================================


class ReasonClassification(str, Enum):
    """AI/system assigned reason classifications."""

    EXTERNAL_DEPENDENCY_FAILURE = "external-dependency-failure"
    INTERNAL_SERVICE_ERROR = "internal-service-error"
    MAINTENANCE_WINDOW = "maintenance-window"
    SLA_BREACH_MITIGATION = "sla-breach-mitigation"
    CHAOS_EXPERIMENT = "chaos-experiment"
    MANUAL_INTERVENTION = "manual-intervention"
    RECOVERY_PROCEDURE = "recovery-procedure"
    SECURITY_INCIDENT = "security-incident"
    UNKNOWN = "unknown"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ControlRequest:
    """Internal representation of a control API request."""

    service_name: str
    action: str
    reason: str
    environment: str
    ttl_minutes: int | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)
    actor: str = "system"
    actor_role: str = "automation"


@dataclass
class ControlResponse:
    """Internal representation of a control API response."""

    status: str
    action_applied: str
    system_state: str = ""
    effective_until: str | None = None
    reason_classification: str = ""
    evidence: dict = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    error_code: str = ""
    error_message: str = ""
    risk_level: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "status": self.status,
            "action_applied": self.action_applied,
            "correlation_id": self.correlation_id,
        }

        if self.system_state:
            result["system_state"] = self.system_state
        if self.effective_until:
            result["effective_until"] = self.effective_until
        if self.reason_classification:
            result["reason_classification"] = self.reason_classification
        if self.evidence:
            result["evidence"] = self.evidence
        if self.error_code:
            result["error_code"] = self.error_code
        if self.error_message:
            result["error_message"] = self.error_message
        if self.risk_level:
            result["risk_level"] = self.risk_level

        return result
