"""
Django ORM Repository Implementations.

Concrete implementations of repository interfaces using Django ORM.
These adapters translate between abstract interface methods and
Django model operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from selfhealing.core.types import (
    FailedOperationData,
    CircuitBreakerStateData,
    SecurityIncidentData,
    OperationStatus,
    CircuitState,
)
from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
)


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
        now = timezone.now()
        day_30_ago = now - timedelta(days=30)
        day_90_ago = now - timedelta(days=90)

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
    # SLA & Replay Operations (migrated from shopping adapters)
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


class DjangoCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    Django ORM implementation of CircuitBreakerStateRepository.

    Wraps the CircuitBreakerState model for circuit breaker state management.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies."""
        from selfhealing.adapters.django.models import CircuitBreakerState

        return CircuitBreakerState

    def _to_data(self, obj) -> CircuitBreakerStateData:
        """Convert Django model instance to data class."""
        return CircuitBreakerStateData(
            service_name=obj.service_name,
            state=obj.state,
            failure_count=obj.failure_count,
            success_count=obj.success_count,
            last_failure_at=obj.last_failure_at,
            last_success_at=getattr(obj, "last_success_at", None),
            opened_at=obj.opened_at,
            half_opened_at=getattr(obj, "half_opened_at", None),
            failure_threshold=getattr(obj, "failure_threshold", 5),
            recovery_timeout=getattr(obj, "recovery_timeout", 60),
            half_open_max_calls=getattr(obj, "half_open_max_calls", 3),
            manually_controlled=getattr(obj, "manually_controlled", False),
            controlled_by_id=getattr(obj, "controlled_by_id", None),
            control_reason=getattr(obj, "control_reason", "") or "",
            manual_override_expires_at=getattr(obj, "manual_override_expires_at", None),
            half_open_request_count=getattr(obj, "half_open_request_count", 0),
            id=obj.id if hasattr(obj, "id") else None,
            created_at=getattr(obj, "created_at", None),
            updated_at=getattr(obj, "updated_at", None),
        )

    def get_state(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Get the current state of a circuit breaker."""
        CircuitBreakerState = self._get_model()

        try:
            obj = CircuitBreakerState.objects.get(service_name=service_name)
            return self._to_data(obj)
        except CircuitBreakerState.DoesNotExist:
            return None

    def get_or_create(
        self,
        service_name: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> CircuitBreakerStateData:
        """Get existing state or create a new one with defaults."""
        CircuitBreakerState = self._get_model()

        obj, created = CircuitBreakerState.objects.get_or_create(
            service_name=service_name,
            defaults=defaults
            or {
                "state": CircuitState.CLOSED.value,
                "failure_count": 0,
                "success_count": 0,
            },
        )
        return self._to_data(obj)

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> Optional[CircuitBreakerStateData]:
        """Update the state of a circuit breaker."""
        CircuitBreakerState = self._get_model()

        # Handle both string and enum values
        state_value = state.value if hasattr(state, "value") else state
        update_fields = {"state": state_value}
        if failure_count is not None:
            update_fields["failure_count"] = failure_count
        if success_count is not None:
            update_fields["success_count"] = success_count
        if opened_at is not None:
            update_fields["opened_at"] = opened_at

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(**update_fields)

        if updated:
            return self.get_state(service_name)
        return None

    def record_failure(
        self,
        service_name: str,
    ) -> CircuitBreakerStateData:
        """Record a failure and update failure count."""
        CircuitBreakerState = self._get_model()

        with transaction.atomic():
            obj, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                service_name=service_name,
                defaults={
                    "state": CircuitState.CLOSED.value,
                    "failure_count": 0,
                },
            )
            obj.record_failure()

        return self._to_data(obj)

    def record_success(
        self,
        service_name: str,
    ) -> CircuitBreakerStateData:
        """Record a success and update success count."""
        CircuitBreakerState = self._get_model()

        with transaction.atomic():
            obj, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                service_name=service_name,
                defaults={
                    "state": CircuitState.CLOSED.value,
                    "failure_count": 0,
                },
            )
            obj.record_success()

        return self._to_data(obj)

    def reset(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Reset a circuit breaker to closed state."""
        CircuitBreakerState = self._get_model()

        try:
            obj = CircuitBreakerState.objects.get(service_name=service_name)
            obj.reset()
            return self._to_data(obj)
        except CircuitBreakerState.DoesNotExist:
            return None

    def open_circuit(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Open a circuit breaker."""
        CircuitBreakerState = self._get_model()

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(
            state=CircuitState.OPEN.value,
            opened_at=timezone.now(),
        )

        if updated:
            return self.get_state(service_name)
        return None

    def half_open_circuit(
        self,
        service_name: str,
    ) -> Optional[CircuitBreakerStateData]:
        """Transition a circuit breaker to half-open state."""
        CircuitBreakerState = self._get_model()

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(
            state=CircuitState.HALF_OPEN.value,
            half_opened_at=timezone.now(),
            success_count=0,
            half_open_request_count=0,
        )

        if updated:
            return self.get_state(service_name)
        return None

    def list_all(self) -> List[CircuitBreakerStateData]:
        """List all circuit breaker states."""
        CircuitBreakerState = self._get_model()

        return [self._to_data(obj) for obj in CircuitBreakerState.objects.all().order_by("service_name")]

    def list_open(self) -> List[CircuitBreakerStateData]:
        """List all open circuit breakers."""
        CircuitBreakerState = self._get_model()

        return [
            self._to_data(obj)
            for obj in CircuitBreakerState.objects.filter(state=CircuitState.OPEN.value).order_by("service_name")
        ]

    # =========================================================================
    # Methods required by the interface
    # =========================================================================

    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name"""
        return self.get_state(service_name)

    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: Optional[int] = None,
        reason: str = "",
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Set manual control on a circuit breaker"""
        CircuitBreakerState = self._get_model()

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(
            state=state,
            manually_controlled=True,
            controlled_by_id=controlled_by_id,
            control_reason=reason,
            manual_override_expires_at=expires_at,
        )
        return updated > 0

    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """Clear manual control from a circuit breaker

        Args:
            service_name: Name of the service
            preserve_reason: If True, keep the existing control_reason value
        """
        CircuitBreakerState = self._get_model()

        update_fields = {
            "manually_controlled": False,
            "controlled_by_id": None,
            "manual_override_expires_at": None,
        }
        if not preserve_reason:
            update_fields["control_reason"] = ""

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(**update_fields)
        return updated > 0

    def get_all(self) -> List[CircuitBreakerStateData]:
        """Get all circuit breaker states"""
        return self.list_all()

    def get_all_states(self) -> List[CircuitBreakerStateData]:
        """Get all circuit breaker states"""
        return self.list_all()

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """
        Atomically force open a circuit breaker.

        Uses row-level locking to prevent concurrent modifications.
        Creates the circuit breaker if it doesn't exist.

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        CircuitBreakerState = self._get_model()

        with transaction.atomic():
            obj, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                service_name=service_name,
                defaults={
                    "state": CircuitState.CLOSED.value,
                    "failure_count": 0,
                    "success_count": 0,
                },
            )
            previous_state = obj.state
            obj.state = CircuitState.OPEN.value
            obj.manually_controlled = True
            obj.controlled_by_id = controlled_by_id
            obj.control_reason = reason
            obj.opened_at = timezone.now()
            obj.manual_override_expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
            obj.save()

        return (True, previous_state, CircuitState.OPEN.value)

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """
        Atomically force close a circuit breaker.

        Uses row-level locking to prevent concurrent modifications.

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        CircuitBreakerState = self._get_model()

        with transaction.atomic():
            obj, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                service_name=service_name,
                defaults={
                    "state": CircuitState.CLOSED.value,
                    "failure_count": 0,
                    "success_count": 0,
                },
            )
            previous_state = obj.state
            obj.state = CircuitState.CLOSED.value
            obj.manually_controlled = True
            obj.controlled_by_id = controlled_by_id
            obj.control_reason = reason
            obj.failure_count = 0
            obj.success_count = 0
            obj.opened_at = None
            obj.half_open_request_count = 0
            obj.save()

        return (True, previous_state, CircuitState.CLOSED.value)

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """
        Atomically reset a circuit breaker to initial state.

        Uses row-level locking to prevent concurrent modifications.
        Resets all counters and clears manual control.

        Returns:
            Tuple of (success, previous_state, new_state)
        """
        CircuitBreakerState = self._get_model()

        try:
            with transaction.atomic():
                obj = CircuitBreakerState.objects.select_for_update().get(service_name=service_name)
                previous_state = obj.state
                obj.state = CircuitState.CLOSED.value
                obj.failure_count = 0
                obj.success_count = 0
                obj.opened_at = None
                obj.manually_controlled = False
                obj.controlled_by_id = None
                obj.control_reason = ""
                obj.manual_override_expires_at = None
                obj.half_open_request_count = 0
                obj.save()

            return (True, previous_state, CircuitState.CLOSED.value)
        except CircuitBreakerState.DoesNotExist:
            return (False, "", "")


class DjangoSecurityIncidentRepository(SecurityIncidentRepository):
    """
    Django ORM implementation of SecurityIncidentRepository.

    Wraps the SecurityIncident model for security incident management.
    Security incidents should NEVER be auto-healed.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies."""
        from selfhealing.adapters.django.models import SecurityIncident

        return SecurityIncident

    def _to_data(self, obj) -> SecurityIncidentData:
        """Convert Django model instance to data class."""
        return SecurityIncidentData(
            id=obj.id,
            incident_type=obj.incident_type,
            severity=obj.severity,
            source_ip=obj.source_ip,
            user_id=obj.user_id,
            description=obj.description,
            context=obj.context or {},
            created_at=obj.created_at,
            resolved_at=obj.resolved_at,
            is_resolved=obj.status == "resolved",
        )

    def create(
        self,
        incident_type: str,
        severity: str,
        description: str,
        context: Optional[Dict[str, Any]] = None,
        source_ip: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> SecurityIncidentData:
        """Create a new security incident record."""
        SecurityIncident = self._get_model()

        obj = SecurityIncident.objects.create(
            incident_type=incident_type,
            severity=severity,
            description=description,
            context=context or {},
            source_ip=source_ip,
            user_id=user_id,
            status="open",
        )
        return self._to_data(obj)

    def get_by_id(
        self,
        incident_id: int,
    ) -> Optional[SecurityIncidentData]:
        """Get an incident by ID."""
        SecurityIncident = self._get_model()

        try:
            obj = SecurityIncident.objects.get(id=incident_id)
            return self._to_data(obj)
        except SecurityIncident.DoesNotExist:
            return None

    def get_recent(
        self,
        hours: int = 24,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get recent incidents."""
        SecurityIncident = self._get_model()

        since = timezone.now() - timedelta(hours=hours)
        queryset = SecurityIncident.objects.filter(created_at__gte=since)

        if severity:
            queryset = queryset.filter(severity=severity)

        queryset = queryset.order_by("-created_at")[:limit]
        return [self._to_data(obj) for obj in queryset]

    def get_unresolved(
        self,
        severity: Optional[str] = None,
    ) -> List[SecurityIncidentData]:
        """Get unresolved incidents."""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.exclude(status="resolved")

        if severity:
            queryset = queryset.filter(severity=severity)

        return [self._to_data(obj) for obj in queryset.order_by("-created_at")]

    def resolve(
        self,
        incident_id: int,
    ) -> Optional[SecurityIncidentData]:
        """Mark an incident as resolved."""
        SecurityIncident = self._get_model()

        try:
            obj = SecurityIncident.objects.get(id=incident_id)
            obj.resolve()
            return self._to_data(obj)
        except SecurityIncident.DoesNotExist:
            return None

    def count_by_type(
        self,
        hours: int = 24,
    ) -> Dict[str, int]:
        """Count incidents by type in the given time window."""
        SecurityIncident = self._get_model()
        from django.db.models import Count

        since = timezone.now() - timedelta(hours=hours)
        results = SecurityIncident.objects.filter(created_at__gte=since).values("incident_type").annotate(count=Count("id"))

        return {r["incident_type"]: r["count"] for r in results}

    def count_by_source_ip(
        self,
        source_ip: str,
        hours: int = 1,
    ) -> int:
        """Count incidents from a specific IP in the given time window."""
        SecurityIncident = self._get_model()

        since = timezone.now() - timedelta(hours=hours)
        return SecurityIncident.objects.filter(
            source_ip=source_ip,
            created_at__gte=since,
        ).count()

    # =========================================================================
    # Additional methods (migrated from shopping adapters)
    # =========================================================================

    def get_open_incidents(
        self,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get all open (unresolved) incidents."""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.filter(
            status__in=["open", "investigating"]
        ).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def get_by_type(
        self,
        incident_type: str,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get incidents by type."""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.filter(
            incident_type=incident_type
        ).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def get_by_severity(
        self,
        severity: str,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get incidents by severity."""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.filter(
            severity=severity
        ).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def update_status(
        self,
        incident_id: int,
        status: str,
        investigation_notes: str = "",
        assigned_to_id: Optional[int] = None,
    ) -> bool:
        """Update incident status."""
        SecurityIncident = self._get_model()

        update_fields = {"status": status}
        model_fields = [f.name for f in SecurityIncident._meta.get_fields()]

        if "investigation_notes" in model_fields and investigation_notes:
            update_fields["investigation_notes"] = investigation_notes
        if "assigned_to_id" in model_fields and assigned_to_id:
            update_fields["assigned_to_id"] = assigned_to_id

        updated = SecurityIncident.objects.filter(id=incident_id).update(**update_fields)
        return updated > 0

    def mark_as_resolved(
        self,
        incident_id: int,
        investigation_notes: str = "",
    ) -> bool:
        """Mark incident as resolved."""
        SecurityIncident = self._get_model()

        update_fields = {"status": "resolved"}
        model_fields = [f.name for f in SecurityIncident._meta.get_fields()]

        if "resolved_at" in model_fields:
            update_fields["resolved_at"] = timezone.now()
        if "investigation_notes" in model_fields and investigation_notes:
            update_fields["investigation_notes"] = investigation_notes

        updated = SecurityIncident.objects.filter(id=incident_id).update(**update_fields)
        return updated > 0

    def get_recent_by_ip(
        self,
        source_ip: str,
        hours: int = 24,
        limit: int = 100,
    ) -> List[SecurityIncidentData]:
        """Get recent incidents from a specific IP."""
        SecurityIncident = self._get_model()

        since = timezone.now() - timedelta(hours=hours)

        queryset = SecurityIncident.objects.filter(
            source_ip=source_ip,
            created_at__gte=since,
        ).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def count_by_type_since(
        self,
        incident_type: str,
        since: datetime,
    ) -> int:
        """Count incidents of a type since a given time."""
        SecurityIncident = self._get_model()

        return SecurityIncident.objects.filter(
            incident_type=incident_type,
            created_at__gte=since,
        ).count()
