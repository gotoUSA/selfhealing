"""
Alert Adapter Interface

Provides an abstraction for alerting, allowing users to choose
how and where alerts are sent without being tied to any specific
alerting system.

Design Philosophy:
- No forced dependencies (no PagerDuty API, no Slack webhook, etc.)
- User chooses: stdout, file, email, Slack, PagerDuty, or custom
- Default is non-invasive (file or stdout)

Usage:
    # Use default stdout adapter
    from selfhealing.adapters.alert import StdoutAlertAdapter
    adapter = StdoutAlertAdapter()

    # Or implement your own
    class MySlackAdapter(AlertAdapter):
        def send(self, alert: Alert) -> None:
            slack_webhook.post(alert.to_dict())
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


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    CRITICAL = "critical"  # Immediate attention required
    WARNING = "warning"  # Needs attention soon
    INFO = "info"  # Informational


class AlertCategory(str, Enum):
    """Alert categories for routing."""

    AVAILABILITY = "availability"  # Service down
    LATENCY = "latency"  # Response time issues
    ERROR_RATE = "error_rate"  # High error rate
    CIRCUIT_BREAKER = "circuit_breaker"  # CB state changes
    DLQ = "dlq"  # DLQ issues
    RESOURCE = "resource"  # CPU, Memory, Disk
    SECURITY = "security"  # Security incidents
    SLO_VIOLATION = "slo_violation"  # SLO breached
    FAILSAFE = "failsafe"  # Fail-safe mode activated (self-healing degraded)


@dataclass
class Alert:
    """
    Alert containing all relevant context.

    Captures:
    - What happened (title, description)
    - How severe (severity)
    - What category (category)
    - Where (source, service_name)
    - Additional context (details)
    """

    title: str
    description: str
    severity: AlertSeverity = AlertSeverity.WARNING
    category: AlertCategory = AlertCategory.AVAILABILITY

    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Source information
    source: str = "selfhealing"  # Component that generated alert
    service_name: Optional[str] = None
    domain: Optional[str] = None

    # SLO context (if SLO violation)
    slo_name: Optional[str] = None
    slo_target: Optional[float] = None
    slo_current: Optional[float] = None

    # Additional context
    details: dict[str, Any] = field(default_factory=dict)
    runbook_url: Optional[str] = None

    # Deduplication
    alert_key: Optional[str] = None  # For grouping/deduping alerts

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "service_name": self.service_name,
            "domain": self.domain,
            "slo_name": self.slo_name,
            "slo_target": self.slo_target,
            "slo_current": self.slo_current,
            "details": self.details,
            "runbook_url": self.runbook_url,
            "alert_key": self.alert_key,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @property
    def key(self) -> str:
        """Generate alert key for deduplication."""
        if self.alert_key:
            return self.alert_key
        return f"{self.category.value}:{self.service_name or 'unknown'}:{self.title}"


class AlertAdapter(ABC):
    """
    Abstract interface for alerting.

    Implementations can send alerts to:
    - stdout (StdoutAlertAdapter)
    - Files (FileAlertAdapter)
    - Slack/Teams (user implements)
    - PagerDuty/OpsGenie (user implements)
    - Email (user implements)
    - Nowhere (NullAlertAdapter)
    """

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """
        Send an alert.

        Args:
            alert: The alert to send
        """
        pass

    @abstractmethod
    def resolve(self, alert_key: str) -> None:
        """
        Resolve/close an alert.

        Args:
            alert_key: The key of the alert to resolve
        """
        pass

    def alert_cb_opened(
        self,
        service_name: str,
        failure_count: int,
        threshold: int,
        is_manual: bool = False,
    ) -> None:
        """Convenience method for Circuit Breaker open alert."""
        self.send(
            Alert(
                title=f"Circuit Breaker Opened: {service_name}",
                description=(
                    f"Circuit breaker for {service_name} has been {'manually' if is_manual else 'automatically'} opened. "
                    f"Failure count: {failure_count}/{threshold}"
                ),
                severity=AlertSeverity.WARNING,
                category=AlertCategory.CIRCUIT_BREAKER,
                service_name=service_name,
                details={
                    "failure_count": failure_count,
                    "threshold": threshold,
                    "is_manual": is_manual,
                },
                alert_key=f"cb:open:{service_name}",
            )
        )

    def alert_cb_closed(
        self,
        service_name: str,
        is_manual: bool = False,
    ) -> None:
        """Convenience method for Circuit Breaker close (resolves alert)."""
        self.resolve(f"cb:open:{service_name}")

    def alert_dlq_threshold(
        self,
        domain: str,
        pending_count: int,
        threshold: int,
    ) -> None:
        """Convenience method for DLQ threshold alert."""
        self.send(
            Alert(
                title=f"DLQ Threshold Exceeded: {domain}",
                description=(
                    f"DLQ for domain '{domain}' has {pending_count} pending items, "
                    f"exceeding threshold of {threshold}. Human review required."
                ),
                severity=AlertSeverity.WARNING,
                category=AlertCategory.DLQ,
                domain=domain,
                details={
                    "pending_count": pending_count,
                    "threshold": threshold,
                },
                alert_key=f"dlq:threshold:{domain}",
            )
        )

    def alert_slo_violation(
        self,
        slo_name: str,
        target: float,
        current: float,
        service_name: Optional[str] = None,
    ) -> None:
        """Convenience method for SLO violation alert."""
        self.send(
            Alert(
                title=f"SLO Violation: {slo_name}",
                description=(
                    f"SLO '{slo_name}' is violated. Target: {target:.2%}, Current: {current:.2%}"
                ),
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.SLO_VIOLATION,
                service_name=service_name,
                slo_name=slo_name,
                slo_target=target,
                slo_current=current,
                alert_key=f"slo:violation:{slo_name}",
            )
        )

    def alert_high_error_rate(
        self,
        service_name: str,
        error_rate: float,
        threshold: float,
    ) -> None:
        """Convenience method for high error rate alert."""
        self.send(
            Alert(
                title=f"High Error Rate: {service_name}",
                description=(
                    f"Error rate for {service_name} is {error_rate:.1%}, "
                    f"exceeding threshold of {threshold:.1%}"
                ),
                severity=AlertSeverity.WARNING,
                category=AlertCategory.ERROR_RATE,
                service_name=service_name,
                details={
                    "error_rate": error_rate,
                    "threshold": threshold,
                },
                alert_key=f"error_rate:{service_name}",
            )
        )

    def alert_failsafe_activated(
        self,
        component: str,
        error_message: str,
        fallback_action: str = "PROCEED",
    ) -> None:
        """
        CRITICAL: Fail-Safe 모드 발동 알림.
        
        Self-Healing 시스템의 일부가 장애를 일으켜 Fail-Safe 모드로
        전환되었을 때 발송됩니다. 이 알림은 즉각적인 주의가 필요합니다.
        
        Args:
            component: 장애가 발생한 컴포넌트 (예: "error_budget", "circuit_breaker")
            error_message: 장애 원인 메시지
            fallback_action: 취해진 fallback 동작 (예: "PROCEED", "ALLOW")
        
        Note:
            이 알림은 "침묵하는 장애"를 방지하기 위해 설계되었습니다.
            Fail-Safe가 작동하면 시스템은 계속 동작하지만, 운영팀은
            즉시 알림을 받아 근본 원인을 해결해야 합니다.
        """
        self.send(
            Alert(
                title=f"🚨 FAIL-SAFE 발동: {component}",
                description=(
                    f"Self-Healing '{component}' 시스템이 장애로 인해 Fail-Safe 모드로 전환되었습니다.\n\n"
                    f"오류: {error_message}\n"
                    f"Fallback 동작: {fallback_action}\n\n"
                    f"⚠️ 배포는 허용되었지만, 시스템 복구가 필요합니다."
                ),
                severity=AlertSeverity.CRITICAL,  # 항상 CRITICAL
                category=AlertCategory.FAILSAFE,
                source="selfhealing",
                details={
                    "component": component,
                    "error_message": error_message,
                    "fallback_action": fallback_action,
                    "failsafe_applied": True,
                    "requires_immediate_attention": True,
                },
                runbook_url="https://docs.internal/runbooks/selfhealing-failsafe",
                alert_key=f"failsafe:{component}",
            )
        )

    def resolve_failsafe(self, component: str) -> None:
        """Fail-Safe 복구 시 알림 해제."""
        self.resolve(f"failsafe:{component}")

