"""
Django ORM Failed Operation Repository

DjangoFailedOperationRepository - Django ORM implementation for DLQ operations.

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from selfhealing.interfaces.repositories import (
    FailedOperationData,
    FailedOperationRepository,
    FailedOperationStatus,
)


class DjangoFailedOperationRepository(FailedOperationRepository):
    """
    Django ORM implementation of FailedOperationRepository.

    Wraps the FailedOperation model for DLQ operations.
    """

    def _get_model(self):
        """
        Lazy import to avoid circular dependencies.

        Note: This adapter (django_repos) specifically requires shopping app models.
        For standalone deployments, use selfhealing.adapters.django.repositories instead.
        """
        try:
            from shopping.models.failed_operation import FailedOperation
            return FailedOperation
        except ImportError:
            raise ImportError(
                "FailedOperation model not found. "
                "This adapter (django_repos) requires the shopping app models. "
                "For standalone deployments, use selfhealing.adapters.django.repositories instead, "
                "which uses selfhealing's own Django models."
            )

    def _to_data(self, obj) -> FailedOperationData:
        """Convert Django model instance to data class"""
        return FailedOperationData(
            id=obj.id,
            domain=obj.domain,
            failure_type=obj.failure_type,
            status=obj.status,
            order_id=obj.order_id,
            payment_id=obj.payment_id,
            user_id=obj.user_id,
            snapshot_data=obj.snapshot_data or {},
            error_code=obj.error_code or "",
            error_message=obj.error_message or "",
            retry_count=obj.retry_count,
            max_retries=obj.max_retries,
            last_retry_at=obj.last_retry_at,
            request_data=obj.request_data or {},
            response_data=obj.response_data or {},
            metadata=obj.metadata or {},
            resolved_at=obj.resolved_at,
            resolved_by_id=obj.resolved_by_id,
            resolution_type=obj.resolution_type or "",
            resolution_note=obj.resolution_note or "",
            next_action_hint=obj.next_action_hint or "",
            recommended_action=obj.recommended_action or "",
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            expires_at=obj.expires_at if hasattr(obj, "expires_at") else None,
        )

    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        user_id: Optional[int] = None,
        snapshot_data: Optional[dict[str, Any]] = None,
        request_data: Optional[dict[str, Any]] = None,
        response_data: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
    ) -> FailedOperationData:
        """Create a new failed operation record"""
        FailedOperation = self._get_model()

        obj = FailedOperation.objects.create(
            domain=domain,
            failure_type=failure_type,
            error_message=error_message,
            error_code=error_code,
            order_id=order_id,
            payment_id=payment_id,
            user_id=user_id,
            snapshot_data=snapshot_data or {},
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata or {},
            retry_count=retry_count,
            max_retries=max_retries,
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
            status=FailedOperationStatus.PENDING.value,
        )
        return self._to_data(obj)

    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        """Get a failed operation by ID"""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=id)
            return self._to_data(obj)
        except FailedOperation.DoesNotExist:
            return None

    def get_pending_by_domain(
        self,
        domain: str,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get pending operations for a specific domain"""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(
            domain=domain,
            status=FailedOperationStatus.PENDING.value,
        ).order_by(
            "created_at"
        )[:limit]

        return [self._to_data(obj) for obj in queryset]

    def get_pending_count_by_domain(self, domain: str) -> int:
        """Get count of pending operations for a domain"""
        FailedOperation = self._get_model()

        return FailedOperation.objects.filter(
            domain=domain,
            status=FailedOperationStatus.PENDING.value,
        ).count()

    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Update the status of a failed operation"""
        FailedOperation = self._get_model()

        update_fields = {"status": status}

        if resolution_type:
            update_fields["resolution_type"] = resolution_type
        if resolution_note:
            update_fields["resolution_note"] = resolution_note
        if resolved_by_id:
            update_fields["resolved_by_id"] = resolved_by_id
        if status in [FailedOperationStatus.RESOLVED.value, FailedOperationStatus.REJECTED.value]:
            update_fields["resolved_at"] = timezone.now()

        updated = FailedOperation.objects.filter(id=id).update(**update_fields)
        return updated > 0

    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count and update last_retry_at"""
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=id)
            obj.retry_count += 1
            obj.last_retry_at = timezone.now()
            obj.save(update_fields=["retry_count", "last_retry_at", "updated_at"])
            return True
        except FailedOperation.DoesNotExist:
            return False

    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str,
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        """Mark a failed operation as resolved"""
        return self.update_status(
            id=id,
            status=FailedOperationStatus.RESOLVED.value,
            resolution_type=resolution_type,
            resolution_note=resolution_note,
            resolved_by_id=resolved_by_id,
        )

    def get_expired_operations(
        self,
        before_date: datetime,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Get operations that have expired"""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(
            expires_at__lt=before_date,
            status=FailedOperationStatus.PENDING.value,
        ).order_by("expires_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
    ) -> int:
        """Bulk update status for multiple operations"""
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
    ) -> list[FailedOperationData]:
        """Find operations by status with optional filters"""
        FailedOperation = self._get_model()

        filters = {"status": status}
        if domain:
            filters["domain"] = domain
        if failure_type:
            filters["failure_type"] = failure_type

        queryset = FailedOperation.objects.filter(**filters).order_by("-created_at")[:limit]
        return [self._to_data(obj) for obj in queryset]

    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        """Find operations that can be replayed (pending and retry_count < max_retries)"""
        FailedOperation = self._get_model()

        filters = {
            "status": FailedOperationStatus.PENDING.value,
            "retry_count__lt": max_retries,
        }
        if domain:
            filters["domain"] = domain
        if failure_type:
            filters["failure_type"] = failure_type

        queryset = FailedOperation.objects.filter(**filters).order_by("created_at")[:limit]
        return [self._to_data(obj) for obj in queryset]

    def find_sla_breached(
        self,
        current_time: datetime,
        sla_thresholds: dict[str, timedelta],
    ) -> list[FailedOperationData]:
        """Find operations that have breached their SLA"""
        FailedOperation = self._get_model()

        results = []
        for domain, threshold in sla_thresholds.items():
            cutoff_time = current_time - threshold
            queryset = FailedOperation.objects.filter(
                domain=domain,
                status=FailedOperationStatus.PENDING.value,
                created_at__lt=cutoff_time,
            )
            results.extend([self._to_data(obj) for obj in queryset])

        return results

    def find_expired(
        self,
        current_time: datetime,
    ) -> list[FailedOperationData]:
        """Find operations past their retention period"""
        FailedOperation = self._get_model()

        queryset = FailedOperation.objects.filter(
            expires_at__lt=current_time,
        )
        return [self._to_data(obj) for obj in queryset]

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about failed operations"""
        FailedOperation = self._get_model()
        from django.db.models import Count, Avg

        total = FailedOperation.objects.count()
        by_status = dict(FailedOperation.objects.values("status").annotate(count=Count("id")).values_list("status", "count"))
        by_domain = dict(FailedOperation.objects.values("domain").annotate(count=Count("id")).values_list("domain", "count"))
        avg_retries = FailedOperation.objects.aggregate(avg_retries=Avg("retry_count"))["avg_retries"] or 0

        return {
            "total": total,
            "by_status": by_status,
            "by_domain": by_domain,
            "avg_retries": float(avg_retries),
        }

    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """
        Atomically acquire a DLQ entry for replay.

        Uses row-level locking (SELECT FOR UPDATE) to prevent race conditions.
        """
        FailedOperation = self._get_model()

        with transaction.atomic():
            try:
                obj = FailedOperation.objects.select_for_update(nowait=True).get(id=id)

                # Check if eligible for replay
                if obj.status != FailedOperationStatus.PENDING.value:
                    return None
                if obj.retry_count >= max_retries:
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
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        resolved_by_id: Optional[int] = None,
        error_details: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Complete a replay operation by updating the final status.
        """
        FailedOperation = self._get_model()

        try:
            obj = FailedOperation.objects.get(id=id)

            if success:
                obj.status = FailedOperationStatus.RESOLVED.value
                obj.resolution_type = resolution_type or "auto_replay"
                obj.resolution_note = note
                obj.resolved_by_id = resolved_by_id
                obj.resolved_at = timezone.now()
            else:
                # Revert to pending for retry or mark as requires_review
                if obj.retry_count >= obj.max_retries:
                    obj.status = FailedOperationStatus.REQUIRES_REVIEW.value
                else:
                    obj.status = FailedOperationStatus.PENDING.value

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
            status=FailedOperationStatus.PENDING.value,
            updated_at=timezone.now(),
        )

        return updated
