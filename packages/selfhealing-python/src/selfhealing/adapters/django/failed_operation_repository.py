"""
Django ORM Implementation of FailedOperationRepository.

Wraps the FailedOperation model for DLQ operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from selfhealing.core.types import (
    FailedOperationData,
    OperationStatus,
)
from selfhealing.interfaces.repositories import FailedOperationRepository


class DjangoFailedOperationRepository(FailedOperationRepository):
    """
    Django ORM implementation of FailedOperationRepository.

    Wraps the FailedOperation model for DLQ operations.
    """

    def _get_model(self):
        """
        Lazy import to avoid circular dependencies.

        Uses selfhealing's own Django model for standalone deployments.
        """
        from selfhealing.adapters.django.models import FailedOperation

        return FailedOperation

    def _to_data(self, obj) -> FailedOperationData:
        """Convert Django model instance to data class."""
        return FailedOperationData(
            id=obj.id,
            domain=obj.domain,
            failure_type=obj.failure_type,
            status=obj.status,
            created_at=obj.created_at,
            context=obj.snapshot_data or {},
            error_message=obj.error_message or "",
            retry_count=obj.retry_count,
            max_retries=obj.max_retries,
            last_retry_at=obj.last_retry_at,
            next_retry_at=obj.next_retry_at,
            resolved_at=obj.resolved_at,
            updated_at=obj.updated_at,
        )

    def create(
        self,
        domain: str,
        failure_type: str,
        context: Dict[str, Any],
        error_message: str,
        max_retries: int = 3,
    ) -> FailedOperationData:
        """Create a new failed operation record."""
        FailedOperation = self._get_model()

        obj = FailedOperation.objects.create(
            domain=domain,
            failure_type=failure_type,
            snapshot_data=context,
            error_message=error_message,
            max_retries=max_retries,
            status=OperationStatus.PENDING.value,
        )
        return self._to_data(obj)

    def get_by_id(self, operation_id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by its ID."""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=operation_id)
            return self._to_data(obj)
        except FailedOperation.DoesNotExist:
            return None

    def get_pending(
        self,
        domain: Optional[str] = None,
        limit: int = 10,
    ) -> List[FailedOperationData]:
        """Get pending operations ready for retry."""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(status=OperationStatus.PENDING.value)
        if domain:
            queryset = queryset.filter(domain=domain)

        queryset = queryset.order_by("created_at")[:limit]
        return [self._to_data(obj) for obj in queryset]

    def get_by_status(
        self,
        status: OperationStatus,
        domain: Optional[str] = None,
    ) -> List[FailedOperationData]:
        """Get operations by status."""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(status=status.value)
        if domain:
            queryset = queryset.filter(domain=domain)

        return [self._to_data(obj) for obj in queryset.order_by("-created_at")]

    def update_status(
        self,
        operation_id: int,
        status: OperationStatus,
        error_message: Optional[str] = None,
    ) -> Optional[FailedOperationData]:
        """Update the status of an operation."""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=operation_id)
            obj.status = status.value
            if error_message is not None:
                obj.error_message = error_message
            obj.save(update_fields=["status", "error_message", "updated_at"])
            return self._to_data(obj)
        except FailedOperation.DoesNotExist:
            return None

    def increment_retry(
        self,
        operation_id: int,
        error_message: str,
        next_retry_at: Optional[datetime] = None,
    ) -> Optional[FailedOperationData]:
        """Increment retry count and update error message."""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=operation_id)
            obj.retry_count = F("retry_count") + 1
            obj.last_retry_at = timezone.now()
            obj.error_message = error_message
            if next_retry_at:
                obj.next_retry_at = next_retry_at
            obj.save()
            obj.refresh_from_db()
            return self._to_data(obj)
        except FailedOperation.DoesNotExist:
            return None

    def mark_completed(
        self,
        operation_id: int,
    ) -> Optional[FailedOperationData]:
        """Mark an operation as successfully completed."""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=operation_id)
            obj.status = OperationStatus.COMPLETED.value
            obj.resolved_at = timezone.now()
            obj.resolution_type = "auto_replay"
            obj.save(update_fields=["status", "resolved_at", "resolution_type", "updated_at"])
            return self._to_data(obj)
        except FailedOperation.DoesNotExist:
            return None

    def mark_failed(
        self,
        operation_id: int,
        error_message: str,
    ) -> Optional[FailedOperationData]:
        """Mark an operation as permanently failed."""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=operation_id)
            obj.status = OperationStatus.FAILED.value
            obj.error_message = error_message
            obj.resolved_at = timezone.now()
            obj.resolution_type = "rejected"
            obj.save(update_fields=["status", "error_message", "resolved_at", "resolution_type", "updated_at"])
            return self._to_data(obj)
        except FailedOperation.DoesNotExist:
            return None

    def count_by_status(
        self,
        status: Optional[OperationStatus] = None,
        domain: Optional[str] = None,
    ) -> int:
        """Count operations by status and optionally domain."""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.all()
        if status:
            queryset = queryset.filter(status=status.value)
        if domain:
            queryset = queryset.filter(domain=domain)

        return queryset.count()

    def delete_expired(
        self,
        older_than: datetime,
    ) -> int:
        """Delete expired operations. Returns count of deleted."""
        FailedOperation = self._get_model()

        result = FailedOperation.objects.filter(expires_at__lt=older_than).delete()
        return result[0] if result else 0

    # =========================================================================
    # Cleanup Operations
    # =========================================================================

    def archive_old_resolved(
        self,
        older_than_days: int = 30,
    ) -> int:
        """Archive resolved entries older than N days."""
        FailedOperation = self._get_model()
        cutoff = timezone.now() - timedelta(days=older_than_days)

        # Update resolved entries to archived
        count = FailedOperation.objects.filter(
            status=OperationStatus.COMPLETED.value,
            resolved_at__lt=cutoff,
        ).update(
            status=OperationStatus.ARCHIVED.value if hasattr(OperationStatus, "ARCHIVED") else "archived",
            updated_at=timezone.now(),
        )
        return count

    def purge_archived(
        self,
        ids: Optional[List[int]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """Permanently delete archived entries."""
        FailedOperation = self._get_model()
        archived_status = OperationStatus.ARCHIVED.value if hasattr(OperationStatus, "ARCHIVED") else "archived"

        if ids is not None and older_than_days is not None:
            raise ValueError("Specify either ids or older_than_days, not both")

        if ids is not None:
            # Verify all are archived before deleting
            non_archived = (
                FailedOperation.objects.filter(id__in=ids).exclude(status=archived_status).values_list("id", "status")
            )

            if non_archived:
                first_bad = list(non_archived)[0]
                raise ValueError(
                    f"Entry {first_bad[0]} is not archived (status: {first_bad[1]}). " "Only archived entries can be purged."
                )

            result = FailedOperation.objects.filter(
                id__in=ids,
                status=archived_status,
            ).delete()
            return result[0] if result else 0

        elif older_than_days is not None:
            cutoff = timezone.now() - timedelta(days=older_than_days)
            result = FailedOperation.objects.filter(
                status=archived_status,
                updated_at__lt=cutoff,
            ).delete()
            return result[0] if result else 0

        else:
            # Purge all archived
            result = FailedOperation.objects.filter(
                status=archived_status,
            ).delete()
            return result[0] if result else 0

    def get_cleanup_stats(self) -> Dict[str, Any]:
        """Get statistics for cleanup operations."""
        FailedOperation = self._get_model()
        now_time = timezone.now()
        day_30_ago = now_time - timedelta(days=30)
        day_90_ago = now_time - timedelta(days=90)

        archived_status = OperationStatus.ARCHIVED.value if hasattr(OperationStatus, "ARCHIVED") else "archived"
        completed_status = OperationStatus.COMPLETED.value

        # Count by status
        from django.db.models import Count

        status_counts = dict(
            FailedOperation.objects.values("status").annotate(count=Count("id")).values_list("status", "count")
        )

        # Count resolved older than 30 days
        resolved_older_than_30_days = FailedOperation.objects.filter(
            status=completed_status,
            resolved_at__lt=day_30_ago,
        ).count()

        # Count archived older than 90 days
        archived_older_than_90_days = FailedOperation.objects.filter(
            status=archived_status,
            updated_at__lt=day_90_ago,
        ).count()

        return {
            "total": FailedOperation.objects.count(),
            "by_status": status_counts,
            "resolved_older_than_30_days": resolved_older_than_30_days,
            "archived_older_than_90_days": archived_older_than_90_days,
        }

    # =========================================================================
    # SLA & Replay Operations
    # =========================================================================

    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: Dict[str, timedelta],
    ) -> List[FailedOperationData]:
        """Find operations that have breached their SLA."""
        FailedOperation = self._get_model()

        results = []
        for domain, threshold in sla_thresholds.items():
            cutoff_time = current_time - threshold
            queryset = FailedOperation.objects.filter(
                domain=domain,
                status=OperationStatus.PENDING.value,
                created_at__lt=cutoff_time,
            )
            results.extend([self._to_data(obj) for obj in queryset])

        return results

    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Find operations that can be replayed (pending and retry_count < max_retries)."""
        FailedOperation = self._get_model()

        filters = {
            "status": OperationStatus.PENDING.value,
            "retry_count__lt": max_retries,
        }
        if domain:
            filters["domain"] = domain
        if failure_type:
            filters["failure_type"] = failure_type

        queryset = FailedOperation.objects.filter(**filters).order_by("created_at")[:limit]
        return [self._to_data(obj) for obj in queryset]

    def find_expired(
        self,
        current_time: datetime,
    ) -> List[FailedOperationData]:
        """Find operations past their retention period."""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(
            expires_at__lt=current_time,
        )
        return [self._to_data(obj) for obj in queryset]

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about failed operations."""
        FailedOperation = self._get_model()
        from django.db.models import Count, Avg

        total = FailedOperation.objects.count()
        by_status = dict(
            FailedOperation.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )
        by_domain = dict(
            FailedOperation.objects.values("domain")
            .annotate(count=Count("id"))
            .values_list("domain", "count")
        )
        avg_retries = FailedOperation.objects.aggregate(avg_retries=Avg("retry_count"))["avg_retries"] or 0

        return {
            "total": total,
            "by_status": by_status,
            "by_domain": by_domain,
            "avg_retries": float(avg_retries),
        }

    def try_acquire_for_replay(
        self,
        operation_id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """
        Atomically acquire a DLQ entry for replay.

        Uses row-level locking (SELECT FOR UPDATE) to prevent race conditions.
        """
        FailedOperation = self._get_model()

        with transaction.atomic():
            try:
                obj = FailedOperation.objects.select_for_update(nowait=True).get(id=operation_id)

                # Check if eligible for replay
                if obj.status != OperationStatus.PENDING.value:
                    return None
                if obj.retry_count >= max_retries:
                    # Max retries exceeded - mark as rejected
                    obj.status = OperationStatus.REJECTED.value
                    obj.resolution_note = f"Max retries ({max_retries}) exceeded"
                    obj.save(update_fields=["status", "resolution_note", "updated_at"])
                    return None

                # Acquire the entry
                obj.status = "replaying"
                obj.retry_count += 1
                obj.last_retry_at = timezone.now()
                obj.save(update_fields=["status", "retry_count", "last_retry_at", "updated_at"])

                return self._to_data(obj)
            except FailedOperation.DoesNotExist:
                return None
            except Exception:
                # Could be locked by another process
                return None

    def complete_replay(
        self,
        operation_id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Complete a replay operation by updating the final status."""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=operation_id)

            if success:
                obj.status = OperationStatus.RESOLVED.value
                obj.resolution_type = resolution_type or "auto_replay"
                obj.resolution_note = note
                obj.resolved_by_id = resolved_by_id
                obj.resolved_at = timezone.now()
            else:
                # Revert to pending for retry or mark as requires_review
                if obj.retry_count >= obj.max_retries:
                    obj.status = OperationStatus.REQUIRES_REVIEW.value
                else:
                    obj.status = OperationStatus.PENDING.value

                if error_details:
                    obj.metadata = {**(obj.metadata or {}), "last_error": error_details}
                if note:
                    obj.resolution_note = note

            obj.save()
            return True
        except FailedOperation.DoesNotExist:
            return False

    def release_stale_replaying(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """
        Release DLQ entries stuck in REPLAYING state.

        Entries can get stuck if the replay process crashes.
        This reverts them to PENDING for retry.
        """
        FailedOperation = self._get_model()

        cutoff_time = timezone.now() - timedelta(minutes=older_than_minutes)

        # Find stale entries (status='replaying' and last_retry_at is old)
        updated = FailedOperation.objects.filter(
            status="replaying",
            last_retry_at__lt=cutoff_time,
        ).update(
            status=OperationStatus.PENDING.value,
            updated_at=timezone.now(),
        )

        return updated

    def bulk_update_status(
        self,
        ids: List[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations."""
        FailedOperation = self._get_model()

        return FailedOperation.objects.filter(id__in=ids).update(
            status=status,
            updated_at=timezone.now(),
        )

    def find_by_status(
        self,
        status: str,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Find operations by status with optional filters."""
        FailedOperation = self._get_model()

        filters = {"status": status}
        if domain:
            filters["domain"] = domain
        if failure_type:
            filters["failure_type"] = failure_type

        queryset = FailedOperation.objects.filter(**filters).order_by("-created_at")[:limit]
        return [self._to_data(obj) for obj in queryset]

    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> List[FailedOperationData]:
        """Get pending operations for a specific domain."""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(
            domain=domain,
            status=OperationStatus.PENDING.value,
        ).order_by("created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain."""
        FailedOperation = self._get_model()

        return FailedOperation.objects.filter(
            domain=domain,
            status=OperationStatus.PENDING.value,
        ).count()
