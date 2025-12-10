"""
Django ORM Repository Adapters

Concrete implementations of repository interfaces using Django ORM.
These adapters translate between the abstract interface methods and
Django model operations.

Design:
- Each adapter wraps a Django model
- Methods return data classes, not Django model instances
- All database operations are encapsulated here

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from selfhealing.interfaces.repositories import (
    # Data Classes
    FailedOperationData,
    CircuitBreakerStateData,
    SecurityIncidentData,
    # Repository Interfaces
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
    # Enums
    FailedOperationStatus,
    CircuitBreakerStateEnum,
    SecurityIncidentStatus,
)


class DjangoFailedOperationRepository(FailedOperationRepository):
    """
    Django ORM implementation of FailedOperationRepository.

    Wraps the FailedOperation model for DLQ operations.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies"""
        from shopping.models.failed_operation import FailedOperation

        return FailedOperation

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


class DjangoCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    Django ORM implementation of CircuitBreakerStateRepository.

    Wraps the CircuitBreakerState model for circuit breaker state management.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies"""
        from shopping.models.failed_payment import CircuitBreakerState

        return CircuitBreakerState

    def _to_data(self, obj) -> CircuitBreakerStateData:
        """Convert Django model instance to data class"""
        return CircuitBreakerStateData(
            id=obj.id,
            service_name=obj.service_name,
            state=obj.state,
            failure_count=obj.failure_count,
            success_count=obj.success_count,
            last_failure_at=obj.last_failure_at,
            opened_at=obj.opened_at,
            manually_controlled=obj.manually_controlled,
            controlled_by_id=obj.controlled_by_id,
            control_reason=obj.control_reason or "",
            manual_override_expires_at=obj.manual_override_expires_at,
            half_open_request_count=obj.half_open_request_count,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """Get existing state or create new one for a service"""
        CircuitBreakerState = self._get_model()

        obj, created = CircuitBreakerState.objects.get_or_create(
            service_name=service_name,
            defaults={
                "state": CircuitBreakerStateEnum.CLOSED.value,
                "failure_count": 0,
                "success_count": 0,
            },
        )
        return self._to_data(obj)

    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name"""
        CircuitBreakerState = self._get_model()

        try:
            obj = CircuitBreakerState.objects.get(service_name=service_name)
            return self._to_data(obj)
        except CircuitBreakerState.DoesNotExist:
            return None

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """Update circuit breaker state"""
        CircuitBreakerState = self._get_model()

        update_fields = {"state": state}

        if failure_count is not None:
            update_fields["failure_count"] = failure_count
        if success_count is not None:
            update_fields["success_count"] = success_count
        if opened_at is not None:
            update_fields["opened_at"] = opened_at

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(**update_fields)
        return updated > 0

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state"""
        CircuitBreakerState = self._get_model()

        with transaction.atomic():
            obj, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                service_name=service_name,
                defaults={
                    "state": CircuitBreakerStateEnum.CLOSED.value,
                    "failure_count": 0,
                },
            )
            obj.record_failure()

        return self._to_data(obj)

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Record a success and return updated state"""
        CircuitBreakerState = self._get_model()

        with transaction.atomic():
            obj, created = CircuitBreakerState.objects.select_for_update().get_or_create(
                service_name=service_name,
                defaults={
                    "state": CircuitBreakerStateEnum.CLOSED.value,
                    "failure_count": 0,
                },
            )
            obj.record_success()

        return self._to_data(obj)

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

    def clear_manual_control(self, service_name: str) -> bool:
        """Clear manual control from a circuit breaker"""
        CircuitBreakerState = self._get_model()

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(
            manually_controlled=False,
            controlled_by_id=None,
            control_reason="",
            manual_override_expires_at=None,
        )
        return updated > 0

    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states"""
        CircuitBreakerState = self._get_model()

        queryset = CircuitBreakerState.objects.all().order_by("service_name")
        return [self._to_data(obj) for obj in queryset]

    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state"""
        CircuitBreakerState = self._get_model()

        updated = CircuitBreakerState.objects.filter(service_name=service_name).update(
            state=CircuitBreakerStateEnum.CLOSED.value,
            failure_count=0,
            success_count=0,
            opened_at=None,
            manually_controlled=False,
            controlled_by_id=None,
            control_reason="",
            manual_override_expires_at=None,
            half_open_request_count=0,
        )
        return updated > 0


class DjangoSecurityIncidentRepository(SecurityIncidentRepository):
    """
    Django ORM implementation of SecurityIncidentRepository.

    Wraps the SecurityIncident model for security incident management.
    Security incidents are NEVER auto-healed.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies"""
        from shopping.models.security_incident import SecurityIncident

        return SecurityIncident

    def _to_data(self, obj) -> SecurityIncidentData:
        """Convert Django model instance to data class"""
        return SecurityIncidentData(
            id=obj.id,
            incident_type=obj.incident_type,
            severity=obj.severity,
            status=obj.status,
            source_ip=obj.source_ip,
            user_agent=obj.user_agent or "",
            user_id=obj.user_id,
            order_id=obj.order_id if hasattr(obj, "order_id") else None,
            payment_id=obj.payment_id if hasattr(obj, "payment_id") else None,
            description=obj.description if hasattr(obj, "description") else "",
            raw_payload=obj.raw_payload if hasattr(obj, "raw_payload") else {},
            assigned_to_id=obj.assigned_to_id if hasattr(obj, "assigned_to_id") else None,
            investigation_notes=obj.investigation_notes if hasattr(obj, "investigation_notes") else "",
            resolved_at=obj.resolved_at if hasattr(obj, "resolved_at") else None,
            created_at=obj.created_at,
            updated_at=obj.updated_at if hasattr(obj, "updated_at") else None,
        )

    def create(
        self,
        incident_type: str,
        severity: str,
        description: str = "",
        source_ip: Optional[str] = None,
        user_agent: str = "",
        user_id: Optional[int] = None,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        raw_payload: Optional[dict[str, Any]] = None,
    ) -> SecurityIncidentData:
        """Create a new security incident"""
        SecurityIncident = self._get_model()

        create_kwargs = {
            "incident_type": incident_type,
            "severity": severity,
            "status": SecurityIncidentStatus.OPEN.value,
        }

        # Add optional fields if model supports them
        if source_ip:
            create_kwargs["source_ip"] = source_ip
        if user_agent:
            create_kwargs["user_agent"] = user_agent
        if user_id:
            create_kwargs["user_id"] = user_id

        # Check if model has these optional fields
        model_fields = [f.name for f in SecurityIncident._meta.get_fields()]

        if "description" in model_fields and description:
            create_kwargs["description"] = description
        if "order_id" in model_fields and order_id:
            create_kwargs["order_id"] = order_id
        if "payment_id" in model_fields and payment_id:
            create_kwargs["payment_id"] = payment_id
        if "raw_payload" in model_fields:
            create_kwargs["raw_payload"] = raw_payload or {}

        obj = SecurityIncident.objects.create(**create_kwargs)
        return self._to_data(obj)

    def get_by_id(self, id: int) -> Optional[SecurityIncidentData]:
        """Get a security incident by ID"""
        SecurityIncident = self._get_model()

        try:
            obj = SecurityIncident.objects.get(id=id)
            return self._to_data(obj)
        except SecurityIncident.DoesNotExist:
            return None

    def get_open_incidents(
        self,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get all open (unresolved) incidents"""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.filter(
            status__in=[
                SecurityIncidentStatus.OPEN.value,
                SecurityIncidentStatus.INVESTIGATING.value,
            ]
        ).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def get_by_type(
        self,
        incident_type: str,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get incidents by type"""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.filter(incident_type=incident_type).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def get_by_severity(
        self,
        severity: str,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get incidents by severity"""
        SecurityIncident = self._get_model()

        queryset = SecurityIncident.objects.filter(severity=severity).order_by("-created_at")[:limit]

        return [self._to_data(obj) for obj in queryset]

    def update_status(
        self,
        id: int,
        status: str,
        investigation_notes: str = "",
        assigned_to_id: Optional[int] = None,
    ) -> bool:
        """Update incident status"""
        SecurityIncident = self._get_model()

        update_fields = {"status": status}

        model_fields = [f.name for f in SecurityIncident._meta.get_fields()]

        if "investigation_notes" in model_fields and investigation_notes:
            update_fields["investigation_notes"] = investigation_notes
        if "assigned_to_id" in model_fields and assigned_to_id:
            update_fields["assigned_to_id"] = assigned_to_id

        updated = SecurityIncident.objects.filter(id=id).update(**update_fields)
        return updated > 0

    def mark_as_resolved(
        self,
        id: int,
        investigation_notes: str = "",
    ) -> bool:
        """Mark incident as resolved"""
        SecurityIncident = self._get_model()

        update_fields = {
            "status": SecurityIncidentStatus.RESOLVED.value,
        }

        model_fields = [f.name for f in SecurityIncident._meta.get_fields()]

        if "resolved_at" in model_fields:
            update_fields["resolved_at"] = timezone.now()
        if "investigation_notes" in model_fields and investigation_notes:
            update_fields["investigation_notes"] = investigation_notes

        updated = SecurityIncident.objects.filter(id=id).update(**update_fields)
        return updated > 0

    def get_recent_by_ip(
        self,
        source_ip: str,
        hours: int = 24,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get recent incidents from a specific IP"""
        SecurityIncident = self._get_model()

        since = timezone.now() - timedelta(hours=hours)

        queryset = SecurityIncident.objects.filter(
            source_ip=source_ip,
            created_at__gte=since,
        ).order_by(
            "-created_at"
        )[:limit]

        return [self._to_data(obj) for obj in queryset]

    def count_by_type_since(
        self,
        incident_type: str,
        since: datetime,
    ) -> int:
        """Count incidents of a type since a given time"""
        SecurityIncident = self._get_model()

        return SecurityIncident.objects.filter(
            incident_type=incident_type,
            created_at__gte=since,
        ).count()
