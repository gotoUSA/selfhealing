"""
Django ORM Models for Self-Healing System.

These models provide persistence for:
- FailedOperation: Dead Letter Queue entries
- CircuitBreakerState: Circuit breaker state management
- SecurityIncident: Security violation tracking

These models are designed to be used with Django applications
that integrate the selfhealing package.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    pass


class FailedOperation(models.Model):
    """
    Dead Letter Queue for unrecoverable failures.

    Central storage for failures that cannot be auto-recovered.
    Every failure that exhausts retry attempts lands here for human review.
    """

    class Domain(models.TextChoices):
        """Domain classification for failed operations"""

        PAYMENT = "payment", "Payment"
        POINT = "point", "Point"
        INVENTORY = "inventory", "Inventory"
        WEBHOOK = "webhook", "Webhook"
        NOTIFICATION = "notification", "Notification"

    class Status(models.TextChoices):
        """State machine for DLQ item lifecycle"""

        PENDING = "pending", "Pending Review"
        REVIEWING = "reviewing", "Under Review"
        REPLAYED = "replayed", "Replay Queued"
        REQUIRES_REVIEW = "requires_review", "Requires Human Review"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected (Unrecoverable)"
        ARCHIVED = "archived", "Archived"
        EXPIRED = "expired", "Retention Expired"

    class ResolutionType(models.TextChoices):
        """How the failure was resolved"""

        AUTO_REPLAY = "auto_replay", "Automatic Replay"
        MANUAL_FIX = "manual_fix", "Manual Fix"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        INTERNAL_ERROR = "internal_error", "Internal Error"
        ARCHIVED = "archived", "Archived"

    class RecommendedAction(models.TextChoices):
        """Suggested action for operators"""

        REPLAY = "replay", "Replay Operation"
        MANUAL_CHECK = "manual_check", "Manual Verification"
        ESCALATE = "escalate", "Escalate to Senior"
        ARCHIVE = "archive", "Archive (No Action)"

    # Domain & Classification
    domain = models.CharField(
        max_length=50,
        choices=Domain.choices,
        db_index=True,
        verbose_name="Domain",
        help_text="Business domain where the failure occurred",
    )

    failure_type = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="Failure Type",
        help_text="Specific failure classification (e.g., PG_TIMEOUT, AMOUNT_MISMATCH)",
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )

    # ========================================
    # Generic Entity Reference (Domain Neutral)
    # ========================================
    entity_type = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Entity Type",
        help_text="Type of related entity (e.g., 'order', 'subscription', 'user')",
    )

    entity_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Entity ID",
        help_text="ID of the related entity",
    )

    entity_refs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Entity References",
        help_text="Additional entity references (e.g., {'user_id': 123, 'tenant_id': 'abc'})",
    )

    user_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="User ID",
    )

    # Snapshot Data
    snapshot_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Snapshot Data",
        help_text="Complete state snapshot for recovery",
    )

    # Error Information
    error_code = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Error Code",
    )

    error_message = models.TextField(
        blank=True,
        verbose_name="Error Message",
    )

    # Retry Tracking
    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Retry Count",
    )

    max_retries = models.PositiveIntegerField(
        default=3,
        verbose_name="Max Retries",
    )

    last_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Retry At",
    )

    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Next Retry At",
    )

    # Forensic Context
    request_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Request Data",
    )

    response_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Response Data",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Metadata",
    )

    # Resolution
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolved At",
    )

    resolved_by_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Resolved By User ID",
    )

    resolution_type = models.CharField(
        max_length=30,
        choices=ResolutionType.choices,
        blank=True,
        verbose_name="Resolution Type",
    )

    resolution_note = models.TextField(
        blank=True,
        verbose_name="Resolution Note",
    )

    next_action_hint = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Next Action Hint",
    )

    recommended_action = models.CharField(
        max_length=30,
        choices=RecommendedAction.choices,
        blank=True,
        verbose_name="Recommended Action",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Created At",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Expires At",
    )

    class Meta:
        db_table = "selfhealing_failed_operation"
        verbose_name = "Failed Operation"
        verbose_name_plural = "Failed Operations"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["domain", "status"]),
            models.Index(fields=["failure_type", "status"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.domain}] {self.failure_type} - {self.status}"

    def mark_as_resolved(
        self,
        resolution_type: str,
        note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> None:
        """Mark the operation as resolved."""
        self.status = self.Status.RESOLVED
        self.resolved_at = timezone.now()
        self.resolution_type = resolution_type
        self.resolution_note = note
        if resolved_by_id:
            self.resolved_by_id = resolved_by_id
        self.save(
            update_fields=["status", "resolved_at", "resolution_type", "resolution_note", "resolved_by_id", "updated_at"]
        )

    def increment_retry(self) -> None:
        """Increment retry count and update timestamp."""
        self.retry_count += 1
        self.last_retry_at = timezone.now()
        self.save(update_fields=["retry_count", "last_retry_at", "updated_at"])


class CircuitBreakerState(models.Model):
    """
    Circuit Breaker state persistence.

    Tracks the state of circuit breakers for external services,
    supporting both automatic transitions and manual overrides.
    """

    class State(models.TextChoices):
        """Circuit breaker states"""

        CLOSED = "closed", "Closed (Normal)"
        OPEN = "open", "Open (Blocked)"
        HALF_OPEN = "half_open", "Half-Open (Testing)"

    service_name = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name="Service Name",
    )

    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.CLOSED,
        verbose_name="State",
    )

    failure_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Failure Count",
    )

    success_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Success Count",
    )

    last_failure_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Failure At",
    )

    last_success_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Success At",
    )

    opened_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Opened At",
    )

    half_opened_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Half Opened At",
    )

    # Manual Control
    manually_controlled = models.BooleanField(
        default=False,
        verbose_name="Manually Controlled",
    )

    controlled_by_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Controlled By User ID",
    )

    control_reason = models.TextField(
        blank=True,
        verbose_name="Control Reason",
    )

    manual_override_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Manual Override Expires At",
    )

    half_open_request_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Half-Open Request Count",
    )

    # Configuration (can be overridden per-service)
    failure_threshold = models.PositiveIntegerField(
        default=5,
        verbose_name="Failure Threshold",
    )

    recovery_timeout = models.PositiveIntegerField(
        default=60,
        verbose_name="Recovery Timeout (seconds)",
    )

    half_open_max_calls = models.PositiveIntegerField(
        default=3,
        verbose_name="Half-Open Max Calls",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Created At",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
    )

    class Meta:
        db_table = "selfhealing_circuit_breaker_state"
        verbose_name = "Circuit Breaker State"
        verbose_name_plural = "Circuit Breaker States"

    def __str__(self) -> str:
        return f"{self.service_name}: {self.get_state_display()}"

    def record_failure(self) -> None:
        """Record a failure and potentially open the circuit."""
        self.failure_count += 1
        self.success_count = 0
        self.last_failure_at = timezone.now()

        if self.failure_count >= self.failure_threshold and self.state == self.State.CLOSED:
            self.state = self.State.OPEN
            self.opened_at = timezone.now()

        self.save()

    def record_success(self) -> None:
        """Record a success and potentially close the circuit."""
        self.last_success_at = timezone.now()

        if self.state == self.State.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_max_calls:
                self.state = self.State.CLOSED
                self.failure_count = 0
                self.opened_at = None
                self.half_opened_at = None
        elif self.state == self.State.CLOSED:
            # Reset failure count on success when closed
            if self.failure_count > 0:
                self.failure_count = 0

        self.save()

    def reset(self) -> None:
        """Reset the circuit breaker to initial state."""
        self.state = self.State.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.opened_at = None
        self.half_opened_at = None
        self.manually_controlled = False
        self.controlled_by_id = None
        self.control_reason = ""
        self.manual_override_expires_at = None
        self.half_open_request_count = 0
        self.save()

    def is_expired_manual_override(self) -> bool:
        """Check if manual override has expired."""
        if self.manual_override_expires_at:
            return timezone.now() > self.manual_override_expires_at
        return False


class SecurityIncident(models.Model):
    """
    Security Incident tracking.

    Security incidents require human intervention and should NEVER self-heal.
    This model provides a separate storage from DLQ to prevent accidental replay.
    """

    class IncidentType(models.TextChoices):
        """Types of security incidents (domain-neutral core + legacy aliases)"""

        # Domain-neutral core types
        SIGNATURE_INVALID = "signature_invalid", "Signature Invalid"
        DATA_TAMPERED = "data_tampered", "Data Tampered"
        TOKEN_FORGED = "token_forged", "Token Forged"
        UNAUTHORIZED_ACCESS = "unauthorized_access", "Unauthorized Access"
        RATE_LIMIT_ABUSE = "rate_limit_abuse", "Rate Limit Abuse"
        SUSPICIOUS_ACTIVITY = "suspicious_activity", "Suspicious Activity"
        REPLAY_ATTACK = "replay_attack", "Replay Attack Detected"
        INJECTION_ATTEMPT = "injection_attempt", "Injection Attempt"

        # Additional incident types (domain-agnostic)
        WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid", "Webhook Signature Invalid"
        AMOUNT_TAMPERED = "amount_tampered", "Amount Tampered"

    class Severity(models.TextChoices):
        """Severity levels"""

        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        """Investigation status"""

        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"
        FALSE_POSITIVE = "false_positive", "False Positive"

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

    # Source Information
    source_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Source IP",
    )

    user_agent = models.TextField(
        blank=True,
        verbose_name="User Agent",
    )

    user_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="User ID",
    )

    # Incident Details
    description = models.TextField(
        verbose_name="Description",
    )

    context = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Context Data",
    )

    raw_request = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Raw Request",
    )

    # Response & Resolution
    action_taken = models.TextField(
        blank=True,
        verbose_name="Action Taken",
    )

    investigated_by_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Investigated By User ID",
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

    # Generic Entity References (Domain Neutral)
    entity_type = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Entity Type",
        help_text="Type of related entity (e.g., 'order', 'subscription')",
    )

    entity_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Entity ID",
    )

    entity_refs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Entity References",
        help_text="Additional entity references as key-value pairs",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Created At",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
    )

    class Meta:
        db_table = "selfhealing_security_incident"
        verbose_name = "Security Incident"
        verbose_name_plural = "Security Incidents"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["incident_type", "status"]),
            models.Index(fields=["severity", "status"]),
            models.Index(fields=["source_ip", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.incident_type} - {self.status}"

    def resolve(self, notes: str = "", investigated_by_id: Optional[int] = None) -> None:
        """Mark the incident as resolved."""
        self.status = self.Status.RESOLVED
        self.resolved_at = timezone.now()
        if notes:
            self.investigation_notes = notes
        if investigated_by_id:
            self.investigated_by_id = investigated_by_id
        self.save(update_fields=["status", "resolved_at", "investigation_notes", "investigated_by_id", "updated_at"])
