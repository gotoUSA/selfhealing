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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from selfhealing.interfaces.messaging_common import MessageSeverity

logger = structlog.get_logger()


# Backward-compatible alias: AlertSeverity는 MessageSeverity로 통합됨.
# 기존 AlertSeverity.CRITICAL / .WARNING / .INFO 모두 MessageSeverity에 포함.
AlertSeverity = MessageSeverity


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

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Source information
    source: str = "selfhealing"  # Component that generated alert
    service_name: str | None = None
    domain: str | None = None

    # SLO context (if SLO violation)
    slo_name: str | None = None
    slo_target: float | None = None
    slo_current: float | None = None

    # Additional context
    details: dict[str, Any] = field(default_factory=dict)
    runbook_url: str | None = None

    # Deduplication
    alert_key: str | None = None  # For grouping/deduping alerts

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
        service_name: str | None = None,
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

    def alert_failsafe_recovered(
        self,
        component: str,
        downtime_seconds: float,
        recovery_reason: str = "System recovered automatically",
    ) -> None:
        """
        복구 완료 알림: Fail-Safe 모드에서 정상 복구 시 발송.

        PagerDuty/OpsGenie의 "resolved" 이벤트와 유사하게,
        장애가 해소되었음을 적극적으로 알립니다.

        Args:
            component: 복구된 컴포넌트 (예: "error_budget", "circuit_breaker")
            downtime_seconds: 장애 지속 시간 (초)
            recovery_reason: 복구 원인 설명

        Note:
            이 알림은 "침묵하는 복구"를 방지합니다.
            장애가 해소되었을 때 명시적으로 알려, 운영팀이
            장애 상태를 계속 추적할 필요가 없도록 합니다.
        """
        # 다운타임 포맷팅
        if downtime_seconds < 60:
            downtime_str = f"{downtime_seconds:.0f}초"
        elif downtime_seconds < 3600:
            downtime_str = f"{downtime_seconds / 60:.1f}분"
        else:
            downtime_str = f"{downtime_seconds / 3600:.1f}시간"

        self.send(
            Alert(
                title=f"✅ RECOVERED: {component}",
                description=(
                    f"Self-Healing '{component}' 시스템이 정상으로 복구되었습니다.\n\n"
                    f"복구 원인: {recovery_reason}\n"
                    f"장애 지속 시간: {downtime_str}\n\n"
                    f"🟢 시스템이 정상 운영 상태로 돌아왔습니다."
                ),
                severity=AlertSeverity.INFO,  # 복구는 INFO 레벨
                category=AlertCategory.FAILSAFE,
                source="selfhealing",
                details={
                    "component": component,
                    "downtime_seconds": downtime_seconds,
                    "recovery_reason": recovery_reason,
                    "recovered": True,
                },
                alert_key=f"failsafe:recovered:{component}",
            )
        )
        # 기존 장애 알림도 resolve
        self.resolve(f"failsafe:{component}")

    def alert_override_escalation(
        self,
        override_type: str,
        requester: str,
        reason: str,
        service_name: str | None = None,
        escalation_channel: str = "#governance",
        escalation_mention: str = "@cto @security",
    ) -> None:
        """
        Override 에스컬레이션 알림: Error Budget 부족 상태에서 배포 override 시 발송.

        Netflix CAB(Change Advisory Board) 스타일로, Error Budget이 소진된 상태에서
        배포를 강행할 경우 상위 책임자/거버넌스 채널에 에스컬레이션합니다.

        Args:
            override_type: Override 유형 (hotfix, security_patch, business_critical 등)
            requester: Override 요청자
            reason: Override 사유
            service_name: 대상 서비스 이름
            escalation_channel: 에스컬레이션 채널 (예: #governance)
            escalation_mention: 멘션할 담당자 (예: @cto @security)

        Note:
            이 알림은 "위험한 행동"에 대한 가시성을 제공합니다.
            Error Budget 정책을 우회하는 모든 행위는 추적되어야 합니다.
        """
        self.send(
            Alert(
                title=f"⚠️ OVERRIDE ESCALATION: {override_type}",
                description=(
                    f"Error Budget 부족 상태에서 배포 Override가 승인되었습니다.\n\n"
                    f"유형: {override_type}\n"
                    f"요청자: {requester}\n"
                    f"사유: {reason}\n"
                    f"대상: {service_name or 'N/A'}\n\n"
                    f"채널: {escalation_channel}\n"
                    f"멘션: {escalation_mention}\n\n"
                    f"⚠️ 이 override는 감사 로그에 기록됩니다."
                ),
                severity=AlertSeverity.WARNING,
                category=AlertCategory.SLO_VIOLATION,  # SLO 위반 카테고리로 분류
                source="selfhealing",
                service_name=service_name,
                details={
                    "override_type": override_type,
                    "requester": requester,
                    "reason": reason,
                    "escalation_channel": escalation_channel,
                    "escalation_mention": escalation_mention,
                    "is_escalation": True,
                },
                alert_key=f"override:escalation:{override_type}:{service_name or 'global'}",
            )
        )
