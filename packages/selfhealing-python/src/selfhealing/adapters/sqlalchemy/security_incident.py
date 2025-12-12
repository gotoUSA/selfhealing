"""
SQLAlchemy SecurityIncident Repository Implementation.

Manages security incidents using SQLAlchemy ORM.
Security incidents are NEVER auto-healed and require human intervention.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from selfhealing.interfaces.repositories import (
    SecurityIncidentRepository,
    SecurityIncidentData,
    SecurityIncidentStatus,
)
from selfhealing.adapters.sqlalchemy.models import SecurityIncidentModel
from selfhealing.adapters.sqlalchemy.base import BaseRepository, _now


class SQLAlchemySecurityIncidentRepository(BaseRepository, SecurityIncidentRepository):
    """
    SQLAlchemy implementation of SecurityIncidentRepository.

    Manages security incidents using SQLAlchemy ORM.
    Security incidents are NEVER auto-healed and require human intervention.
    """

    def _model_to_data(self, model: SecurityIncidentModel) -> SecurityIncidentData:
        """Convert SQLAlchemy model to data class."""
        return SecurityIncidentData(
            id=model.id,
            incident_type=model.incident_type,
            severity=model.severity,
            status=model.status,
            source_ip=model.source_ip,
            user_agent=model.user_agent or "",
            user_id=model.user_id,
            order_id=model.order_id,
            payment_id=model.payment_id,
            description=model.description or "",
            raw_payload=model.raw_payload or {},
            assigned_to_id=model.assigned_to_id,
            investigation_notes=model.investigation_notes or "",
            resolved_at=model.resolved_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
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
        """Create a new security incident."""
        session = self._get_session()
        try:
            model = SecurityIncidentModel(
                incident_type=incident_type,
                severity=severity,
                status=SecurityIncidentStatus.OPEN.value,
                description=description,
                source_ip=source_ip,
                user_agent=user_agent,
                user_id=user_id,
                order_id=order_id,
                payment_id=payment_id,
                raw_payload=raw_payload or {},
                created_at=_now(),
                updated_at=_now(),
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._model_to_data(model)
        finally:
            session.close()

    def get_by_id(self, id: int) -> Optional[SecurityIncidentData]:
        """Get a security incident by ID."""
        session = self._get_session()
        try:
            model = session.query(SecurityIncidentModel).filter_by(id=id).first()
            if model is None:
                return None
            return self._model_to_data(model)
        finally:
            session.close()

    def get_open_incidents(
        self,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get all open (unresolved) incidents."""
        session = self._get_session()
        try:
            models = (
                session.query(SecurityIncidentModel)
                .filter_by(status=SecurityIncidentStatus.OPEN.value)
                .order_by(SecurityIncidentModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def get_by_type(
        self,
        incident_type: str,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get incidents by type."""
        session = self._get_session()
        try:
            models = (
                session.query(SecurityIncidentModel)
                .filter_by(incident_type=incident_type)
                .order_by(SecurityIncidentModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def get_by_severity(
        self,
        severity: str,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get incidents by severity."""
        session = self._get_session()
        try:
            models = (
                session.query(SecurityIncidentModel)
                .filter_by(severity=severity)
                .order_by(SecurityIncidentModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def update_status(
        self,
        id: int,
        status: str,
        investigation_notes: str = "",
        assigned_to_id: Optional[int] = None,
    ) -> bool:
        """Update incident status."""
        session = self._get_session()
        try:
            model = session.query(SecurityIncidentModel).filter_by(id=id).first()
            if model is None:
                return False

            model.status = status
            model.updated_at = _now()

            if investigation_notes:
                model.investigation_notes = investigation_notes
            if assigned_to_id is not None:
                model.assigned_to_id = assigned_to_id
            if status == SecurityIncidentStatus.RESOLVED.value:
                model.resolved_at = _now()

            session.commit()
            return True
        finally:
            session.close()

    def mark_as_resolved(
        self,
        id: int,
        investigation_notes: str = "",
    ) -> bool:
        """Mark incident as resolved."""
        return self.update_status(
            id=id,
            status=SecurityIncidentStatus.RESOLVED.value,
            investigation_notes=investigation_notes,
        )

    def get_recent_by_ip(
        self,
        source_ip: str,
        hours: int = 24,
        limit: int = 100,
    ) -> list[SecurityIncidentData]:
        """Get recent incidents from a specific IP."""
        session = self._get_session()
        try:
            cutoff = _now() - timedelta(hours=hours)
            models = (
                session.query(SecurityIncidentModel)
                .filter_by(source_ip=source_ip)
                .filter(SecurityIncidentModel.created_at > cutoff)
                .order_by(SecurityIncidentModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def count_by_type_since(
        self,
        incident_type: str,
        since: datetime,
    ) -> int:
        """Count incidents of a type since a given time."""
        session = self._get_session()
        try:
            count = (
                session.query(SecurityIncidentModel)
                .filter_by(incident_type=incident_type)
                .filter(SecurityIncidentModel.created_at > since)
                .count()
            )
            return count
        finally:
            session.close()
