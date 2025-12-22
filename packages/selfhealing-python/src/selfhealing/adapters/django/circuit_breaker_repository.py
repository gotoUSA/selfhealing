"""
Django ORM Implementation of CircuitBreakerStateRepository.

Wraps the CircuitBreakerState model for circuit breaker state management.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from selfhealing.core.types import (
    CircuitBreakerStateData,
    CircuitState,
)
from selfhealing.interfaces.repositories import CircuitBreakerStateRepository


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
