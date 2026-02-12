"""
Abstract Django Models for Self-Healing System.

This module provides domain-free abstract models that can be inherited
by any Django project. The abstract models define common fields, indexes,
and state transition methods without any domain-specific dependencies.

Usage:
    # In your Django app's models.py
    from selfhealing.adapters.django.models import AbstractFailedOperation

    class FailedOperation(AbstractFailedOperation):
        # Add domain-specific choices
        class Domain(models.TextChoices):
            PAYMENT = "payment"
            ORDER = "order"
            # ... your domains

        # Override domain field with choices
        domain = models.CharField(
            max_length=50,
            choices=Domain.choices,
            db_index=True
        )

        # Add project-specific FKs
        user = models.ForeignKey("auth.User", ...)

        class Meta(AbstractFailedOperation.Meta):
            abstract = False
            db_table = "failed_operations"
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

try:
    from django.db import models
    from django.utils import timezone

    DJANGO_AVAILABLE = True
except ImportError:
    DJANGO_AVAILABLE = False
    models = None  # type: ignore
    timezone = None  # type: ignore

if TYPE_CHECKING:
    pass


class AbstractFailedOperation(models.Model if DJANGO_AVAILABLE else object):
    """
    Abstract Dead Letter Queue model for unrecoverable failures.

    This abstract model provides:
    - Common fields for DLQ entries (status, retry, error info, etc.)
    - State transition methods (mark_as_resolved, queue_for_replay, etc.)
    - Composite indexes for efficient queries
    - No domain-specific dependencies (no hardcoded choices, no FKs)

    Subclasses should:
    - Define Domain choices specific to their business
    - Add ForeignKey fields as needed (user, etc.)
    - Set abstract = False in Meta
    - Optionally override db_table

    Attributes:
        domain: Business domain classification (no choices - subclass defines)
        failure_type: Specific failure type (e.g., 'timeout', 'validation_error')
        status: Current status in DLQ lifecycle
        entity_type: Type of related entity (e.g., 'order', 'payment')
        entity_id: ID of related entity
        error_code: Error code from external system
        error_message: Human-readable error message
        retry_count: Number of replay attempts
        max_retries: Maximum allowed retries
        snapshot_data: State snapshot for recovery
        request_data: Original request payload
        response_data: External system response
        metadata: Additional debug context
    """

    if not DJANGO_AVAILABLE:
        raise ImportError("Django is required to use AbstractFailedOperation. " "Install it with: pip install django")

    # ========================================
    # Status Choices (domain-free)
    # ========================================
    class Status(models.TextChoices):
        """State machine for DLQ item lifecycle."""

        PENDING = "pending", "Pending Review"
        REVIEWING = "reviewing", "Under Review"
        REPLAYED = "replayed", "Replay Queued"
        REQUIRES_REVIEW = "requires_review", "Requires Human Review"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected (Unrecoverable)"
        ARCHIVED = "archived", "Archived"
        EXPIRED = "expired", "Retention Expired"

    class ResolutionType(models.TextChoices):
        """How the failure was resolved."""

        AUTO_REPLAY = "auto_replay", "Automatic Replay"
        MANUAL_FIX = "manual_fix", "Manual Fix"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        INTERNAL_ERROR = "internal_error", "Internal Error"
        ARCHIVED = "archived", "Archived"

    class RecommendedAction(models.TextChoices):
        """Suggested action for operators."""

        REPLAY = "replay", "Replay Operation"
        MANUAL_CHECK = "manual_check", "Manual Verification"
        ESCALATE = "escalate", "Escalate to Senior"
        ARCHIVE = "archive", "Archive (No Action)"

    # ========================================
    # Domain & Classification
    # ========================================
    domain = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="Domain",
        help_text="Business domain where the failure occurred",
    )

    failure_type = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="Failure Type",
        help_text="Specific failure classification (e.g., TIMEOUT, VALIDATION_ERROR)",
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )

    # ========================================
    # Entity Reference (Generic - no FK dependencies)
    # ========================================
    entity_type = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Entity Type",
        help_text="Type of related entity (e.g., 'order', 'payment', 'subscription')",
    )

    entity_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Entity ID",
        help_text="ID of related entity",
    )

    # Additional entity references as JSON (for multiple related entities)
    entity_refs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Entity References",
        help_text="Additional entity references as {type: id} mapping",
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

    # Note: resolved_by FK should be added by subclass
    # resolved_by = models.ForeignKey("YourUserModel", ...)

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
        abstract = True
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["domain", "status"]),
            models.Index(fields=["failure_type", "status"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
        ]

    def __str__(self) -> str:
        return f"[{self.domain}] {self.failure_type} - {self.status}"

    # ========================================
    # State Transition Methods
    # ========================================
    def mark_as_resolved(
        self,
        resolved_by: Any = None,
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
        if hasattr(self, "resolved_by"):
            self.resolved_by = resolved_by
        self.resolution_type = resolution_type
        self.resolution_note = note

        update_fields = [
            "status",
            "resolved_at",
            "resolution_type",
            "resolution_note",
            "updated_at",
        ]
        if hasattr(self, "resolved_by"):
            update_fields.append("resolved_by")
        self.save(update_fields=update_fields)

    def mark_as_rejected(
        self,
        resolved_by: Any = None,
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
        if hasattr(self, "resolved_by"):
            self.resolved_by = resolved_by
        self.resolution_type = self.ResolutionType.REJECTED
        self.resolution_note = note

        update_fields = [
            "status",
            "resolved_at",
            "resolution_type",
            "resolution_note",
            "updated_at",
        ]
        if hasattr(self, "resolved_by"):
            update_fields.append("resolved_by")
        self.save(update_fields=update_fields)

    def queue_for_replay(self) -> None:
        """
        Queue this DLQ entry for replay.

        Raises:
            ValueError: If maximum replay attempts exceeded
        """
        if self.retry_count >= self.max_retries:
            raise ValueError(f"Maximum replay attempts ({self.max_retries}) exceeded")

        self.status = self.Status.REPLAYED
        self.retry_count += 1
        self.last_retry_at = timezone.now()
        self.save(update_fields=["status", "retry_count", "last_retry_at", "updated_at"])

    def mark_as_reviewing(self, reviewer: Any = None) -> None:
        """
        Mark this DLQ entry as under review.

        Args:
            reviewer: User who is reviewing (stored in metadata)
        """
        self.status = self.Status.REVIEWING
        if reviewer:
            reviewer_id = getattr(reviewer, "id", reviewer)
            self.metadata["reviewer_id"] = reviewer_id
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
        if self.retry_count >= 3:
            self.status = self.Status.REQUIRES_REVIEW
            self.recommended_action = self.RecommendedAction.ESCALATE
        else:
            self.status = self.Status.PENDING

        if note:
            self.error_message = f"{self.error_message}\n[Replay failed] {note}".strip()
        self.save(
            update_fields=[
                "status",
                "error_message",
                "recommended_action",
                "updated_at",
            ]
        )

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
            if self.resolution_note:
                self.resolution_note = f"{self.resolution_note} | [Escalated] {note}"
            else:
                self.resolution_note = f"[Escalated] {note}"
        self.save(
            update_fields=[
                "status",
                "error_message",
                "recommended_action",
                "resolution_note",
                "updated_at",
            ]
        )

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
        self.save(
            update_fields=[
                "status",
                "resolution_type",
                "resolved_at",
                "resolution_note",
                "updated_at",
            ]
        )

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
        """
        try:
            from selfhealing.services import get_sla_thresholds

            sla_config = get_sla_thresholds()
            threshold = sla_config.get_threshold(self.domain)
            return self.status == self.Status.PENDING and (timezone.now() - self.created_at) > threshold
        except ImportError:
            # Fallback to 1 hour if service not available
            from datetime import timedelta

            return self.status == self.Status.PENDING and (timezone.now() - self.created_at) > timedelta(hours=1)

    # ========================================
    # Factory Methods
    # ========================================
    @classmethod
    def create_from_failure(
        cls,
        domain: str,
        failure_type: str,
        entity_type: str = "",
        entity_id: str = "",
        error_code: str = "",
        error_message: str = "",
        snapshot_data: dict[str, Any] | None = None,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        next_action_hint: str = "",
        recommended_action: str = "",
        retention_days: int = 30,
        **extra_fields: Any,
    ) -> AbstractFailedOperation:
        """
        Factory method to create a DLQ entry from a failure.

        Args:
            domain: Business domain (project-specific)
            failure_type: Specific failure type (e.g., TIMEOUT, VALIDATION_ERROR)
            entity_type: Type of related entity (e.g., 'order', 'payment')
            entity_id: ID of related entity
            error_code: Error code from external system
            error_message: Human-readable error message
            snapshot_data: State snapshot for recovery
            request_data: Original request payload
            response_data: External system response
            metadata: Additional debug context
            next_action_hint: Guidance for operators
            recommended_action: Suggested action (replay, manual_check, etc.)
            retention_days: Days to retain before auto-archive
            **extra_fields: Additional fields (e.g., user=user_instance)

        Returns:
            Created FailedOperation instance
        """
        expires_at = timezone.now() + timedelta(days=retention_days)

        # Phase 0: metadata에 region 자동 주입 (221 설계)
        metadata = metadata or {}
        try:
            from selfhealing.core.cluster_identity import get_cluster_identity

            identity = get_cluster_identity()
            if identity.region:
                metadata.setdefault("region", identity.region)
        except Exception:
            pass  # Fail-Open: region 주입 실패 시 무시

        return cls.objects.create(
            domain=domain,
            failure_type=failure_type,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else "",
            error_code=error_code,
            error_message=error_message,
            snapshot_data=snapshot_data or {},
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata,
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
            expires_at=expires_at,
            **extra_fields,
        )


# =============================================================================
# AbstractAuditLog - Q3 보완 구현 (WAL 복구 시 중복 제거 2차 방어)
# =============================================================================


class AbstractAuditLog(models.Model if DJANGO_AVAILABLE else object):
    """
    Abstract Audit Log model for continuous audit recording.

    136_EXCEPTION_HANDLER_6_ENHANCEMENTS.md Q3 보완 구현:
    - audit_event_id: WAL 복구 시 중복 제거를 위한 Unique 필드
    - ON CONFLICT (audit_event_id) DO NOTHING 지원

    특징:
    - 해시 체인 기반 무결성 검증 지원
    - WAL 복구 시 중복 삽입 방지 (2차 방어)
    - 규정 준수를 위한 감사 추적

    Subclasses should:
    - Set abstract = False in Meta
    - Optionally override db_table
    - Add project-specific indexes

    Usage:
        # In your Django app's models.py
        from selfhealing.adapters.django.models import AbstractAuditLog

        class AuditLog(AbstractAuditLog):
            class Meta(AbstractAuditLog.Meta):
                abstract = False
                db_table = "audit_log"
    """

    if not DJANGO_AVAILABLE:
        raise ImportError("Django is required to use AbstractAuditLog. " "Install it with: pip install django")

    # ========================================
    # Unique Event Identifier (WAL 중복 방지용)
    # ========================================
    audit_event_id = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        verbose_name="Audit Event ID",
        help_text=("Unique identifier for audit event. " "Used for WAL recovery deduplication (ON CONFLICT DO NOTHING)."),
    )

    # ========================================
    # Action & Timestamp
    # ========================================
    action = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="Action",
        help_text="Audit action type (e.g., AUTO_TUNING_ADJUSTMENT, CB_FORCE_OPEN)",
    )

    timestamp = models.DateTimeField(
        db_index=True,
        verbose_name="Timestamp",
        help_text="When the action occurred",
    )

    # ========================================
    # Actor Information
    # ========================================
    actor_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name="Actor ID",
        help_text="Who performed the action",
    )

    actor_type = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Actor Type",
        help_text="Type of actor (user, system, etc.)",
    )

    actor_roles = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Actor Roles",
        help_text="RBAC roles of the actor at action time",
    )

    # ========================================
    # Target Information
    # ========================================
    target_type = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Target Type",
        help_text="Type of target entity",
    )

    target_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name="Target ID",
        help_text="ID of target entity",
    )

    # ========================================
    # Service & Domain
    # ========================================
    service_name = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Service Name",
        help_text="Service that generated the audit event",
    )

    domain = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name="Domain",
        help_text="Business domain",
    )

    # ========================================
    # Details & Reason
    # ========================================
    reason = models.TextField(
        blank=True,
        verbose_name="Reason",
        help_text="Reason for the action",
    )

    details = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Details",
        help_text="Full action details as JSON",
    )

    # ========================================
    # Result
    # ========================================
    success = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Success",
        help_text="Whether the action succeeded",
    )

    error_message = models.TextField(
        blank=True,
        verbose_name="Error Message",
        help_text="Error message if action failed",
    )

    # ========================================
    # Integrity (Hash Chain)
    # ========================================
    integrity_hash = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        verbose_name="Integrity Hash",
        help_text="Hash for integrity verification (hash chain)",
    )

    previous_hash = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Previous Hash",
        help_text="Hash of previous entry (for chain verification)",
    )

    sequence_number = models.BigIntegerField(
        default=0,
        db_index=True,
        verbose_name="Sequence Number",
        help_text="Monotonic sequence for ordering",
    )

    # ========================================
    # Metadata
    # ========================================
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Created At",
    )

    class Meta:
        abstract = True
        ordering = ["-sequence_number", "-timestamp"]
        indexes = [
            models.Index(fields=["action", "timestamp"]),
            models.Index(fields=["actor_id", "timestamp"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["service_name", "action"]),
            models.Index(fields=["success", "timestamp"]),
        ]

    def __str__(self) -> str:
        return f"AuditLog({self.action}, {self.audit_event_id})"

    @classmethod
    def insert_ignore_conflict(
        cls,
        audit_event_id: str,
        **fields: Any,
    ) -> tuple[Any, bool]:
        """
        Insert with ON CONFLICT DO NOTHING semantics.

        WAL 복구 시 중복 삽입 방지 (2차 방어).

        Args:
            audit_event_id: Unique event identifier
            **fields: Other model fields

        Returns:
            Tuple of (instance, created)
            created=False if record already exists

        Example:
            log, created = AuditLog.insert_ignore_conflict(
                audit_event_id="wal:123:pg_insert",
                action="AUTO_TUNING_ADJUSTMENT",
                timestamp=datetime.now(),
                ...
            )
            if not created:
                logger.info(f"Duplicate audit event: {audit_event_id}")
        """
        from django.db import IntegrityError

        try:
            instance = cls.objects.create(
                audit_event_id=audit_event_id,
                **fields,
            )
            return instance, True
        except IntegrityError:
            # Unique constraint violation - record already exists
            instance = cls.objects.filter(audit_event_id=audit_event_id).first()
            return instance, False

    @classmethod
    def bulk_insert_ignore_conflict(
        cls,
        entries: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """
        Bulk insert with ON CONFLICT DO NOTHING.

        Uses PostgreSQL-specific INSERT ... ON CONFLICT DO NOTHING
        for optimal performance.

        Args:
            entries: List of field dictionaries (must include audit_event_id)

        Returns:
            Tuple of (inserted_count, skipped_count)
        """
        from django.db import connection

        if not entries:
            return 0, 0

        # PostgreSQL-specific bulk insert
        if connection.vendor == "postgresql":
            return cls._pg_bulk_insert_ignore(entries)
        else:
            # Fallback for other databases
            return cls._fallback_bulk_insert(entries)

    @classmethod
    def _pg_bulk_insert_ignore(
        cls,
        entries: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """PostgreSQL-specific bulk insert with ON CONFLICT DO NOTHING."""
        import json
        from datetime import datetime, timezone

        from django.db import connection

        if not entries:
            return 0, 0

        # Default values for missing fields (raw SQL doesn't use Django defaults)
        default_values = {
            "actor_id": "",
            "actor_type": "",
            "actor_roles": [],
            "target_type": "",
            "target_id": "",
            "service_name": "",
            "domain": "",
            "reason": "",
            "details": {},
            "success": True,
            "error_message": "",
            "integrity_hash": "",
            "previous_hash": "",
            "sequence_number": 0,
            "created_at": datetime.now(timezone.utc),
        }

        # Normalize entries with defaults
        normalized = []
        for entry in entries:
            norm_entry = {**default_values, **entry}
            # Ensure created_at is always fresh for each entry if not provided
            if "created_at" not in entry:
                norm_entry["created_at"] = datetime.now(timezone.utc)
            normalized.append(norm_entry)

        # Build INSERT ... ON CONFLICT DO NOTHING query
        table_name = cls._meta.db_table
        fields = list(normalized[0].keys())
        placeholders = ", ".join(["%s"] * len(fields))
        columns = ", ".join(f'"{f}"' for f in fields)

        sql = f"""
            INSERT INTO "{table_name}" ({columns})
            VALUES ({placeholders})
            ON CONFLICT (audit_event_id) DO NOTHING
        """

        inserted = 0
        with connection.cursor() as cursor:
            for entry in normalized:
                # dict/list 타입 필드를 JSON 문자열로 변환 (psycopg2 호환)
                values = []
                for f in fields:
                    val = entry[f]
                    if isinstance(val, (dict, list)):
                        values.append(json.dumps(val))
                    else:
                        values.append(val)
                cursor.execute(sql, values)
                if cursor.rowcount > 0:
                    inserted += 1

        skipped = len(entries) - inserted
        return inserted, skipped

    @classmethod
    def _fallback_bulk_insert(
        cls,
        entries: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Fallback bulk insert for non-PostgreSQL databases."""
        inserted = 0
        skipped = 0

        for entry in entries:
            audit_event_id = entry.pop("audit_event_id")
            _, created = cls.insert_ignore_conflict(
                audit_event_id=audit_event_id,
                **entry,
            )
            if created:
                inserted += 1
            else:
                skipped += 1

        return inserted, skipped


# =============================================================================
# Postmortem Record Abstract Model
# =============================================================================


class AbstractPostmortemRecord(models.Model if DJANGO_AVAILABLE else object):
    """
    장애 사후 분석(Post-mortem) 영속 저장소 추상 모델.

    서버 재시작 시 데이터 손실 방지를 위해 PostgreSQL에 영구 저장합니다.
    In-Memory 저장소의 한계(최대 100개, 다중 워커 불일치)를 해결합니다.

    Attributes:
        incident_id: 고유 인시던트 식별자
        started_at: 인시던트 시작 시각
        resolved_at: 인시던트 종료 시각
        duration_seconds: 장애 지속 시간(초)
        affected_services: 영향받은 서비스 목록
        timeline: 시간순 이벤트 기록
        auto_actions: 자동으로 수행된 복구 조치
        recommendations: 권장 사항 목록
        system_snapshot: 장애 시점 시스템 상태 스냅샷
        created_at: 레코드 생성 시각
        source: 생성 출처 (auto/manual)
    """

    if not DJANGO_AVAILABLE:
        raise ImportError("Django is required to use AbstractPostmortemRecord. " "Install it with: pip install django")

    class Source(models.TextChoices):
        """Post-mortem 생성 출처."""

        AUTO = "auto", "Automatic (System Generated)"
        MANUAL = "manual", "Manual (User Created)"

    # ========================================
    # Primary Identifier
    # ========================================
    id = models.UUIDField(
        primary_key=True,
        editable=False,
        verbose_name="ID",
    )

    incident_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name="Incident ID",
        help_text="Unique identifier for the incident",
    )

    # ========================================
    # Timing Information
    # ========================================
    started_at = models.DateTimeField(
        db_index=True,
        verbose_name="Incident Start Time",
        help_text="When the incident started",
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Incident Resolution Time",
        help_text="When the incident was resolved",
    )

    duration_seconds = models.FloatField(
        default=0.0,
        db_index=True,
        verbose_name="Duration (seconds)",
        help_text="Total duration of the incident in seconds",
    )

    # ========================================
    # Incident Details (JSON Fields)
    # ========================================
    affected_services = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Affected Services",
        help_text="List of services impacted by the incident",
    )

    timeline = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Timeline",
        help_text="Chronological list of events during the incident",
    )

    auto_actions = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Automatic Actions",
        help_text="List of automatic recovery actions performed",
    )

    recommendations = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Recommendations",
        help_text="Suggested actions for future prevention",
    )

    system_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="System Snapshot",
        help_text="System state snapshot at the time of incident",
    )

    # ========================================
    # Metadata
    # ========================================
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Record Created At",
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.AUTO,
        db_index=True,
        verbose_name="Source",
        help_text="How this post-mortem was created (auto/manual)",
    )

    class Meta:
        abstract = True
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at", "-duration_seconds"]),
            models.Index(fields=["source", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Postmortem {self.incident_id} ({self.started_at})"

    @classmethod
    def create_from_incident_dict(
        cls,
        incident_data: dict[str, Any],
    ) -> "AbstractPostmortemRecord":
        """
        In-Memory 인시던트 딕셔너리로부터 Postmortem 레코드 생성.

        Args:
            incident_data: 기존 add_healing_incident()에 전달되는 딕셔너리

        Returns:
            생성된 PostmortemRecord 인스턴스 (미저장)
        """
        import uuid
        from django.utils import timezone as dj_timezone
        from datetime import datetime

        # incident_id 추출 또는 생성
        incident_id = incident_data.get("incident_id") or str(uuid.uuid4())

        # started_at 파싱
        started_at_raw = incident_data.get("started_at")
        if isinstance(started_at_raw, str):
            try:
                started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
            except ValueError:
                started_at = dj_timezone.now()
        elif isinstance(started_at_raw, datetime):
            started_at = started_at_raw
        else:
            started_at = dj_timezone.now()

        # resolved_at 파싱
        resolved_at_raw = incident_data.get("resolved_at")
        resolved_at = None
        if resolved_at_raw:
            if isinstance(resolved_at_raw, str):
                try:
                    resolved_at = datetime.fromisoformat(resolved_at_raw.replace("Z", "+00:00"))
                except ValueError:
                    resolved_at = None
            elif isinstance(resolved_at_raw, datetime):
                resolved_at = resolved_at_raw

        # duration 계산
        duration_seconds = incident_data.get("duration_seconds", 0.0)
        if not duration_seconds and resolved_at and started_at:
            duration_seconds = (resolved_at - started_at).total_seconds()

        # source 결정 (is_auto 필드 또는 source 필드)
        source = cls.Source.AUTO
        if incident_data.get("source") == "manual":
            source = cls.Source.MANUAL
        elif incident_data.get("is_auto") is False:
            source = cls.Source.MANUAL

        return cls(
            id=uuid.uuid4(),
            incident_id=incident_id,
            started_at=started_at,
            resolved_at=resolved_at,
            duration_seconds=duration_seconds,
            affected_services=incident_data.get("affected_services", []),
            timeline=incident_data.get("timeline", []),
            auto_actions=incident_data.get("auto_actions", []),
            recommendations=incident_data.get("recommendations", []),
            system_snapshot=incident_data.get("system_snapshot", {}),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        """레코드를 딕셔너리로 변환 (API 응답용)."""
        return {
            "id": str(self.id),
            "incident_id": self.incident_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "duration_seconds": self.duration_seconds,
            "affected_services": self.affected_services,
            "timeline": self.timeline,
            "auto_actions": self.auto_actions,
            "recommendations": self.recommendations,
            "system_snapshot": self.system_snapshot,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "source": self.source,
        }
