"""
SQLAlchemy CircuitBreakerState Repository Implementation.

Manages circuit breaker state persistence using SQLAlchemy ORM.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from selfhealing.interfaces.repositories import (
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
)
from selfhealing.adapters.sqlalchemy.models import CircuitBreakerStateModel
from selfhealing.adapters.sqlalchemy.base import BaseRepository, _now


class SQLAlchemyCircuitBreakerStateRepository(BaseRepository, CircuitBreakerStateRepository):
    """
    SQLAlchemy implementation of CircuitBreakerStateRepository.

    Manages circuit breaker state persistence using SQLAlchemy ORM.
    """

    def _model_to_data(self, model: CircuitBreakerStateModel) -> CircuitBreakerStateData:
        """Convert SQLAlchemy model to data class."""
        return CircuitBreakerStateData(
            id=model.id,
            service_name=model.service_name,
            state=model.state,
            failure_count=model.failure_count,
            success_count=model.success_count,
            last_failure_at=model.last_failure_at,
            opened_at=model.opened_at,
            manually_controlled=model.manually_controlled,
            controlled_by_id=model.controlled_by_id,
            control_reason=model.control_reason or "",
            manual_override_expires_at=model.manual_override_expires_at,
            half_open_request_count=model.half_open_request_count,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """Get existing state or create new one for a service."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).first()

            if model is None:
                model = CircuitBreakerStateModel(
                    service_name=service_name,
                    state=CircuitBreakerStateEnum.CLOSED.value,
                    failure_count=0,
                    success_count=0,
                    created_at=_now(),
                    updated_at=_now(),
                )
                session.add(model)
                session.commit()
                session.refresh(model)

            return self._model_to_data(model)
        finally:
            session.close()

    def get_by_service_name(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        """Get circuit breaker state by service name."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).first()
            if model is None:
                return None
            return self._model_to_data(model)
        finally:
            session.close()

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        """Update circuit breaker state."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).first()
            if model is None:
                return False

            model.state = state
            model.updated_at = _now()

            if failure_count is not None:
                model.failure_count = failure_count
            if success_count is not None:
                model.success_count = success_count
            if opened_at is not None:
                model.opened_at = opened_at

            session.commit()
            return True
        finally:
            session.close()

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Record a failure and return updated state."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).with_for_update().first()

            if model is None:
                model = CircuitBreakerStateModel(
                    service_name=service_name,
                    state=CircuitBreakerStateEnum.CLOSED.value,
                    failure_count=0,
                    created_at=_now(),
                    updated_at=_now(),
                )
                session.add(model)

            model.failure_count += 1
            model.last_failure_at = _now()
            model.updated_at = _now()
            session.commit()
            session.refresh(model)

            return self._model_to_data(model)
        finally:
            session.close()

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Record a success and return updated state."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).with_for_update().first()

            if model is None:
                model = CircuitBreakerStateModel(
                    service_name=service_name,
                    state=CircuitBreakerStateEnum.CLOSED.value,
                    success_count=0,
                    created_at=_now(),
                    updated_at=_now(),
                )
                session.add(model)

            model.success_count += 1
            model.last_success_at = _now()
            model.updated_at = _now()
            session.commit()
            session.refresh(model)

            return self._model_to_data(model)
        finally:
            session.close()

    def set_manual_control(
        self,
        service_name: str,
        state: str,
        controlled_by_id: Optional[int] = None,
        reason: str = "",
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Set manual control on a circuit breaker."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).first()
            if model is None:
                return False

            model.state = state
            model.manually_controlled = True
            model.controlled_by_id = controlled_by_id
            model.control_reason = reason
            model.manual_override_expires_at = expires_at
            model.updated_at = _now()
            session.commit()
            return True
        finally:
            session.close()

    def clear_manual_control(self, service_name: str, preserve_reason: bool = False) -> bool:
        """Clear manual control from a circuit breaker."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).first()
            if model is None:
                return False

            model.manually_controlled = False
            model.controlled_by_id = None
            if not preserve_reason:
                model.control_reason = ""
            model.manual_override_expires_at = None
            model.updated_at = _now()
            session.commit()
            return True
        finally:
            session.close()

    def get_all(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states."""
        session = self._get_session()
        try:
            models = session.query(CircuitBreakerStateModel).all()
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states (alias for get_all)."""
        return self.get_all()

    def reset(self, service_name: str) -> bool:
        """Reset circuit breaker to initial closed state."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).first()
            if model is None:
                return False

            model.state = CircuitBreakerStateEnum.CLOSED.value
            model.failure_count = 0
            model.success_count = 0
            model.last_failure_at = None
            model.last_success_at = None
            model.opened_at = None
            model.half_opened_at = None
            model.manually_controlled = False
            model.controlled_by_id = None
            model.control_reason = ""
            model.manual_override_expires_at = None
            model.half_open_request_count = 0
            model.updated_at = _now()
            session.commit()
            return True
        finally:
            session.close()

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """Atomically force open a circuit breaker."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).with_for_update().first()

            if model is None:
                # Create new one
                model = CircuitBreakerStateModel(
                    service_name=service_name,
                    state=CircuitBreakerStateEnum.CLOSED.value,
                    created_at=_now(),
                    updated_at=_now(),
                )
                session.add(model)
                session.flush()

            previous_state = model.state
            model.state = CircuitBreakerStateEnum.OPEN.value
            model.manually_controlled = True
            model.controlled_by_id = controlled_by_id
            model.control_reason = reason
            model.opened_at = _now()
            model.manual_override_expires_at = _now() + timedelta(minutes=ttl_minutes)
            model.updated_at = _now()
            session.commit()

            return (True, previous_state, CircuitBreakerStateEnum.OPEN.value)
        except Exception:
            session.rollback()
            return (False, "", "")
        finally:
            session.close()

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically force close a circuit breaker."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).with_for_update().first()

            if model is None:
                return (False, "", "")

            previous_state = model.state
            model.state = CircuitBreakerStateEnum.CLOSED.value
            model.manually_controlled = True
            model.controlled_by_id = controlled_by_id
            model.control_reason = reason
            model.failure_count = 0
            model.success_count = 0
            model.updated_at = _now()
            session.commit()

            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)
        except Exception:
            session.rollback()
            return (False, "", "")
        finally:
            session.close()

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomically reset a circuit breaker to initial state."""
        session = self._get_session()
        try:
            model = session.query(CircuitBreakerStateModel).filter_by(service_name=service_name).with_for_update().first()

            if model is None:
                return (False, "", "")

            previous_state = model.state
            model.state = CircuitBreakerStateEnum.CLOSED.value
            model.failure_count = 0
            model.success_count = 0
            model.last_failure_at = None
            model.last_success_at = None
            model.opened_at = None
            model.half_opened_at = None
            model.manually_controlled = False
            model.controlled_by_id = None
            model.control_reason = reason  # Keep reason for audit
            model.manual_override_expires_at = None
            model.half_open_request_count = 0
            model.updated_at = _now()
            session.commit()

            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)
        except Exception:
            session.rollback()
            return (False, "", "")
        finally:
            session.close()
