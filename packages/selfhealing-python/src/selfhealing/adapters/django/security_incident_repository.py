"""
Django ORM Implementation of SecurityIncidentRepository.

Wraps the SecurityIncident model for security incident management.
Security incidents should NEVER be auto-healed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from django.utils import timezone

from selfhealing.core.types import SecurityIncidentData
from selfhealing.interfaces.repositories import SecurityIncidentRepository


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
    # Additional methods
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
