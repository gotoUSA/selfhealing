"""
Django ORM Security Incident Repository

DjangoSecurityIncidentRepository - Django ORM implementation for security incident management.
Security incidents are NEVER auto-healed.

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from django.utils import timezone

from selfhealing.interfaces.repositories import (
    SecurityIncidentData,
    SecurityIncidentRepository,
    SecurityIncidentStatus,
)


class DjangoSecurityIncidentRepository(SecurityIncidentRepository):
    """
    Django ORM implementation of SecurityIncidentRepository.

    Wraps the SecurityIncident model for security incident management.
    Security incidents are NEVER auto-healed.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies."""
        try:
            from shopping.models.security_incident import SecurityIncident
            return SecurityIncident
        except ImportError:
            raise ImportError(
                "SecurityIncident model not found. "
                "Django adapter requires shopping app. "
                "Install shopping app or use selfhealing.adapters.django.repositories instead."
            )

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
