"""
Failed Operation Model (Dead Letter Queue)

Central DLQ storage for unrecoverable failures across all domains.
Supports human review, replay, and resolution tracking.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.user import User


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

    # ========================================
    # Domain & Classification
    # ========================================
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
    # Original References
    # ========================================
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failed_operations",
        verbose_name="Order",
    )

    payment = models.ForeignKey(
        "Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failed_operations",
        verbose_name="Payment",
    )

    user = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failed_operations",
        verbose_name="User",
    )

    # ========================================
    # Snapshot Data (for recovery without original records)
    # ========================================
    snapshot_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Snapshot Data",
        help_text="Complete state snapshot for recovery without accessing original records",
    )

    # ========================================
    # Error Information
    # ========================================
    error_code = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Error Code",
    )

    error_message = models.TextField(
        blank=True,
        verbose_name="Error Message",
    )

    # ========================================
    # Retry Tracking
    # ========================================
    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Retry Count",
        help_text="Number of replay attempts from DLQ",
    )

    max_retries = models.PositiveIntegerField(
        default=2,
        verbose_name="Max Retries",
        help_text="Maximum allowed replay attempts (default: 2)",
    )

    last_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Retry At",
    )

    # ========================================
    # Forensic Context
    # ========================================
    request_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Request Data",
        help_text="Original request payload",
    )

    response_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Response Data",
        help_text="External system response",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Metadata",
        help_text="Additional debug info: timing, retry history, state snapshots",
    )

    # ========================================
    # Resolution
    # ========================================
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolved At",
    )

    resolved_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_operations",
        verbose_name="Resolved By",
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

    # ========================================
    # Recovery Hints
    # ========================================
    next_action_hint = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Next Action Hint",
        help_text="Guidance for operators (e.g., 'Verify payment in PG admin')",
    )

    recommended_action = models.CharField(
        max_length=30,
        choices=RecommendedAction.choices,
        blank=True,
        verbose_name="Recommended Action",
    )

    # ========================================
    # Lifecycle
    # ========================================
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Created At",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Updated At",
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Expires At",
        help_text="Auto-archive after retention period",
    )

    class Meta:
        db_table = "failed_operations"
        verbose_name = "Failed Operation (DLQ)"
        verbose_name_plural = "Failed Operations (DLQ)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["domain", "status"]),
            models.Index(fields=["failure_type", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.domain}] {self.failure_type} - {self.status}"

    # ========================================
    # State Transition Methods
    # ========================================
    def mark_as_resolved(
        self,
        resolved_by: "User | None" = None,
        note: str = "",
        resolution_type: str = "",
    ) -> None:
        """
        Mark this DLQ entry as resolved.

        Args:
            resolved_by: User who resolved the issue (None for system)
            note: Resolution notes
            resolution_type: How it was resolved (use ResolutionType enum values)
        """
        if not resolution_type:
            resolution_type = self.ResolutionType.MANUAL_FIX
        self.status = self.Status.RESOLVED
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by
        self.resolution_type = resolution_type
        self.resolution_note = note
        self.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolution_type",
                "resolution_note",
                "updated_at",
            ]
        )

    def mark_as_rejected(
        self,
        resolved_by: "User | None" = None,
        note: str = "",
    ) -> None:
        """
        Mark this DLQ entry as rejected (unrecoverable).

        Args:
            resolved_by: User who rejected the entry
            note: Rejection reason
        """
        self.status = self.Status.REJECTED
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by
        self.resolution_type = self.ResolutionType.REJECTED
        self.resolution_note = note
        self.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolution_type",
                "resolution_note",
                "updated_at",
            ]
        )

    def queue_for_replay(self) -> None:
        """
        Queue this DLQ entry for replay.

        Raises:
            ValueError: If maximum replay attempts (2) exceeded
        """
        if self.retry_count >= self.max_retries:
            raise ValueError(f"Maximum replay attempts ({self.max_retries}) exceeded")

        self.status = self.Status.REPLAYED
        self.retry_count += 1
        self.last_retry_at = timezone.now()
        self.save(update_fields=["status", "retry_count", "last_retry_at", "updated_at"])

    def mark_as_reviewing(self, reviewer: "User | None" = None) -> None:
        """
        Mark this DLQ entry as under review.

        Args:
            reviewer: User who is reviewing (stored in metadata)
        """
        self.status = self.Status.REVIEWING
        if reviewer:
            self.metadata["reviewer_id"] = reviewer.id
            self.metadata["review_started_at"] = timezone.now().isoformat()
        self.save(update_fields=["status", "metadata", "updated_at"])

    def revert_to_pending(self, note: str = "") -> None:
        """
        Revert from REPLAYED back to PENDING after replay failure.
        If retry_count reaches threshold, escalate to REQUIRES_REVIEW.

        Escalation Rule:
        - 1-2 failures: stays PENDING
        - 3+ failures: escalates to REQUIRES_REVIEW

        Args:
            note: Additional error information
        """
        # Escalate to REQUIRES_REVIEW if too many failures
        if self.retry_count >= 3:
            self.status = self.Status.REQUIRES_REVIEW
            self.recommended_action = self.RecommendedAction.ESCALATE
        else:
            self.status = self.Status.PENDING

        if note:
            self.error_message = f"{self.error_message}\n[Replay failed] {note}".strip()
        self.save(update_fields=["status", "error_message", "recommended_action", "updated_at"])

    def mark_as_requires_review(self, note: str = "") -> None:
        """
        Mark this DLQ entry as requiring human investigation.

        Used when:
        - Multiple replay failures indicate non-transient issue
        - Handler encounters unexpected exception
        - Data inconsistency detected

        Args:
            note: Reason for escalation
        """
        self.status = self.Status.REQUIRES_REVIEW
        self.recommended_action = self.RecommendedAction.ESCALATE
        if note:
            self.error_message = f"{self.error_message}\n[Escalated] {note}".strip()
            # Also update resolution_note for operational visibility
            if self.resolution_note:
                self.resolution_note = f"{self.resolution_note} | [Escalated] {note}"
            else:
                self.resolution_note = f"[Escalated] {note}"
        self.save(update_fields=["status", "error_message", "recommended_action", "resolution_note", "updated_at"])

    def mark_as_archived(self, note: str = "") -> None:
        """
        Soft-delete by marking as archived (not hard delete).

        Used for long-term retention of resolved/rejected entries.
        Archived entries are excluded from normal queries but retained for audit.

        Args:
            note: Archive reason
        """
        self.status = self.Status.ARCHIVED
        self.resolution_type = self.ResolutionType.ARCHIVED
        self.resolved_at = timezone.now()
        if note:
            self.resolution_note = note
        self.save(update_fields=["status", "resolution_type", "resolved_at", "resolution_note", "updated_at"])

    def mark_as_expired(self) -> None:
        """Mark this DLQ entry as expired (retention period passed)."""
        self.status = self.Status.EXPIRED
        self.resolution_type = self.ResolutionType.EXPIRED
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolution_type", "resolved_at", "updated_at"])

    # ========================================
    # Query Helpers
    # ========================================
    @property
    def is_replayable(self) -> bool:
        """Check if this entry can be replayed."""
        return self.status == self.Status.PENDING and self.retry_count < self.max_retries

    @property
    def age_seconds(self) -> float:
        """Get age of this DLQ entry in seconds."""
        return (timezone.now() - self.created_at).total_seconds()

    @property
    def is_sla_breached(self) -> bool:
        """
        Check if this entry has breached its SLA.

        SLA thresholds are loaded from configuration.
        See services/self_healing/config.py for default values.
        """
        from selfhealing.services import get_sla_thresholds

        sla_config = get_sla_thresholds()
        threshold = sla_config.get_threshold(self.domain)
        return self.status == self.Status.PENDING and (timezone.now() - self.created_at) > threshold

    # ========================================
    # Factory Methods
    # ========================================
    @classmethod
    def create_from_failure(
        cls,
        domain: str,
        failure_type: str,
        order=None,
        payment=None,
        user=None,
        error_code: str = "",
        error_message: str = "",
        snapshot_data: dict[str, Any] | None = None,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        next_action_hint: str = "",
        recommended_action: str = "",
        retention_days: int = 30,
    ) -> "FailedOperation":
        """
        Factory method to create a DLQ entry from a failure.

        Args:
            domain: Business domain (payment, point, inventory, webhook, notification)
            failure_type: Specific failure type (e.g., PG_TIMEOUT, AMOUNT_MISMATCH)
            order: Related Order instance
            payment: Related Payment instance
            user: Related User instance
            error_code: Error code from external system
            error_message: Human-readable error message
            snapshot_data: State snapshot for recovery
            request_data: Original request payload
            response_data: External system response
            metadata: Additional debug context
            next_action_hint: Guidance for operators
            recommended_action: Suggested action (replay, manual_check, etc.)
            retention_days: Days to retain before auto-archive

        Returns:
            Created FailedOperation instance
        """
        expires_at = timezone.now() + timedelta(days=retention_days)

        return cls.objects.create(
            domain=domain,
            failure_type=failure_type,
            order=order,
            payment=payment,
            user=user,
            error_code=error_code,
            error_message=error_message,
            snapshot_data=snapshot_data or {},
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata or {},
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
            expires_at=expires_at,
        )
