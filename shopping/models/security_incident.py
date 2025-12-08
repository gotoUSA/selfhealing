"""
Security Incident Model

Separate storage for security violations that should NEVER self-heal.
Security incidents require immediate human intervention and investigation.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md Section 5
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.user import User


class SecurityIncident(models.Model):
    """
    Security Incident tracking for violations that never auto-recover.

    Security violations are immediately blocked and routed to security team.
    These are stored separately from the main DLQ to prevent accidental replay.
    """

    class IncidentType(models.TextChoices):
        """Types of security incidents"""
        WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid", "Webhook Signature Invalid"
        PAYMENT_AMOUNT_TAMPERED = "payment_amount_tampered", "Payment Amount Tampered"
        TOKEN_FORGED = "token_forged", "Token Forged"
        UNAUTHORIZED_ACCESS = "unauthorized_access", "Unauthorized Access"
        RATE_LIMIT_ABUSE = "rate_limit_abuse", "Rate Limit Abuse"
        SUSPICIOUS_ACTIVITY = "suspicious_activity", "Suspicious Activity"
        REPLAY_ATTACK = "replay_attack", "Replay Attack Detected"
        INJECTION_ATTEMPT = "injection_attempt", "Injection Attempt"

    class Severity(models.TextChoices):
        """Severity levels for incidents"""
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"

    class Status(models.TextChoices):
        """Investigation status"""
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"
        FALSE_POSITIVE = "false_positive", "False Positive"

    # ========================================
    # Incident Classification
    # ========================================
    incident_type = models.CharField(
        max_length=100,
        choices=IncidentType.choices,
        db_index=True,
        verbose_name="Incident Type",
    )

    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        db_index=True,
        verbose_name="Severity",
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name="Status",
    )

    # ========================================
    # Source Information
    # ========================================
    source_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Source IP",
        help_text="IP address of the request origin",
    )

    user_agent = models.TextField(
        blank=True,
        verbose_name="User Agent",
    )

    user = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_incidents",
        verbose_name="Associated User",
        help_text="User associated with the incident (if authenticated)",
    )

    # ========================================
    # Incident Details
    # ========================================
    description = models.TextField(
        verbose_name="Description",
        help_text="Detailed description of the security incident",
    )

    raw_request = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Raw Request",
        help_text="Sanitized request data for forensic analysis",
    )

    # ========================================
    # Response & Resolution
    # ========================================
    action_taken = models.TextField(
        blank=True,
        verbose_name="Action Taken",
        help_text="Immediate protective action taken (e.g., 'Session invalidated')",
    )

    investigated_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_investigations",
        verbose_name="Investigated By",
    )

    investigation_notes = models.TextField(
        blank=True,
        verbose_name="Investigation Notes",
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolved At",
    )

    # ========================================
    # Related References
    # ========================================
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_incidents",
        verbose_name="Related Order",
    )

    payment = models.ForeignKey(
        "Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_incidents",
        verbose_name="Related Payment",
    )

    # ========================================
    # Lifecycle
    # ========================================
    detected_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Detected At",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
    )

    class Meta:
        db_table = "security_incidents"
        verbose_name = "Security Incident"
        verbose_name_plural = "Security Incidents"
        ordering = ["-detected_at"]
        indexes = [
            models.Index(fields=["incident_type", "status"]),
            models.Index(fields=["severity", "status"]),
            models.Index(fields=["source_ip", "-detected_at"]),
            models.Index(fields=["status", "-detected_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.incident_type} - {self.status}"

    # ========================================
    # Severity Mapping
    # ========================================
    SEVERITY_BY_TYPE = {
        IncidentType.WEBHOOK_SIGNATURE_INVALID: Severity.CRITICAL,
        IncidentType.PAYMENT_AMOUNT_TAMPERED: Severity.CRITICAL,
        IncidentType.TOKEN_FORGED: Severity.CRITICAL,
        IncidentType.REPLAY_ATTACK: Severity.CRITICAL,
        IncidentType.UNAUTHORIZED_ACCESS: Severity.HIGH,
        IncidentType.INJECTION_ATTEMPT: Severity.HIGH,
        IncidentType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
        IncidentType.SUSPICIOUS_ACTIVITY: Severity.MEDIUM,
    }

    # ========================================
    # State Transition Methods
    # ========================================
    def start_investigation(self, investigator: "User") -> None:
        """
        Mark incident as being investigated.

        Args:
            investigator: User who is investigating
        """
        self.status = self.Status.INVESTIGATING
        self.investigated_by = investigator
        self.save(update_fields=["status", "investigated_by", "updated_at"])

    def resolve(
        self,
        investigator: "User",
        notes: str = "",
        is_false_positive: bool = False,
    ) -> None:
        """
        Resolve the security incident.

        Args:
            investigator: User who resolved the incident
            notes: Investigation findings
            is_false_positive: Whether this was a false alarm
        """
        self.status = (
            self.Status.FALSE_POSITIVE if is_false_positive else self.Status.RESOLVED
        )
        self.investigated_by = investigator
        self.investigation_notes = notes
        self.resolved_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "investigated_by",
                "investigation_notes",
                "resolved_at",
                "updated_at",
            ]
        )

    def add_action_taken(self, action: str) -> None:
        """
        Record an action taken in response to the incident.

        Args:
            action: Description of the action taken
        """
        timestamp = timezone.now().isoformat()
        if self.action_taken:
            self.action_taken = f"{self.action_taken}\n[{timestamp}] {action}"
        else:
            self.action_taken = f"[{timestamp}] {action}"
        self.save(update_fields=["action_taken", "updated_at"])

    # ========================================
    # Query Helpers
    # ========================================
    @property
    def is_open(self) -> bool:
        """Check if incident is still open."""
        return self.status in (self.Status.OPEN, self.Status.INVESTIGATING)

    @property
    def age_seconds(self) -> float:
        """Get age of this incident in seconds."""
        return (timezone.now() - self.detected_at).total_seconds()

    # ========================================
    # Factory Method
    # ========================================
    @classmethod
    def create_incident(
        cls,
        incident_type: str,
        description: str,
        source_ip: str | None = None,
        user_agent: str = "",
        user=None,
        order=None,
        payment=None,
        raw_request: dict | None = None,
        immediate_action: str = "",
    ) -> "SecurityIncident":
        """
        Factory method to create a security incident.

        Automatically determines severity based on incident type.

        Args:
            incident_type: Type of security incident
            description: Detailed description
            source_ip: Request source IP address
            user_agent: Request user agent string
            user: Associated user (if any)
            order: Related order (if any)
            payment: Related payment (if any)
            raw_request: Sanitized request data
            immediate_action: Action taken immediately

        Returns:
            Created SecurityIncident instance
        """
        # Determine severity based on incident type
        severity = cls.SEVERITY_BY_TYPE.get(incident_type, cls.Severity.MEDIUM)

        incident = cls.objects.create(
            incident_type=incident_type,
            severity=severity,
            description=description,
            source_ip=source_ip,
            user_agent=user_agent,
            user=user,
            order=order,
            payment=payment,
            raw_request=raw_request or {},
            action_taken=f"[{timezone.now().isoformat()}] {immediate_action}" if immediate_action else "",
        )

        return incident

    @classmethod
    def get_open_by_ip(cls, ip_address: str, hours: int = 24) -> models.QuerySet:
        """
        Get open incidents from a specific IP in the last N hours.

        Useful for detecting patterns of abuse.

        Args:
            ip_address: IP to check
            hours: Lookback window

        Returns:
            QuerySet of matching incidents
        """
        from datetime import timedelta

        cutoff = timezone.now() - timedelta(hours=hours)
        return cls.objects.filter(
            source_ip=ip_address,
            status__in=[cls.Status.OPEN, cls.Status.INVESTIGATING],
            detected_at__gte=cutoff,
        )

    @classmethod
    def get_open_by_user(cls, user, hours: int = 24) -> models.QuerySet:
        """
        Get open incidents associated with a specific user.

        Args:
            user: User to check
            hours: Lookback window

        Returns:
            QuerySet of matching incidents
        """
        from datetime import timedelta

        cutoff = timezone.now() - timedelta(hours=hours)
        return cls.objects.filter(
            user=user,
            status__in=[cls.Status.OPEN, cls.Status.INVESTIGATING],
            detected_at__gte=cutoff,
        )
