"""
Django ORM Circuit Breaker State Repository

DjangoCircuitBreakerStateRepository - Django ORM implementation for circuit breaker state management.

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    CircuitBreakerStateRepository,
    CircuitBreakerStateEnum,
)


class DjangoCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    Django ORM implementation of CircuitBreakerStateRepository.

    Wraps the CircuitBreakerState model for circuit breaker state management.
    """

    def _get_model(self):
        """Lazy import to avoid circular dependencies."""
        try:
            from shopping.models.failed_payment import CircuitBreakerState
            return CircuitBreakerState
        except ImportError:
            raise ImportError(
                "CircuitBreakerState model not found. "
                "Django adapter requires shopping app. "
                "Install shopping app or use selfhealing.adapters.django.repositories instead."
            )

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

    def get_all(self) -> list[CircuitBreakerStateData]:
        """Get all circuit breaker states (alias for get_all_states)"""
        return self.get_all_states()

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
                    "state": CircuitBreakerStateEnum.CLOSED.value,
                    "failure_count": 0,
                    "success_count": 0,
                },
            )
            previous_state = obj.state
            obj.state = CircuitBreakerStateEnum.OPEN.value
            obj.manually_controlled = True
            obj.controlled_by_id = controlled_by_id
            obj.control_reason = reason
            obj.opened_at = timezone.now()
            obj.manual_override_expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
            obj.save()

        return (True, previous_state, CircuitBreakerStateEnum.OPEN.value)

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
                    "state": CircuitBreakerStateEnum.CLOSED.value,
                    "failure_count": 0,
                    "success_count": 0,
                },
            )
            previous_state = obj.state
            obj.state = CircuitBreakerStateEnum.CLOSED.value
            obj.manually_controlled = True
            obj.controlled_by_id = controlled_by_id
            obj.control_reason = reason
            obj.failure_count = 0
            obj.success_count = 0
            obj.opened_at = None
            obj.half_open_request_count = 0
            obj.save()

        return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)

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
                obj.state = CircuitBreakerStateEnum.CLOSED.value
                obj.failure_count = 0
                obj.success_count = 0
                obj.opened_at = None
                obj.manually_controlled = False
                obj.controlled_by_id = None
                obj.control_reason = ""
                obj.manual_override_expires_at = None
                obj.half_open_request_count = 0
                obj.save()

            return (True, previous_state, CircuitBreakerStateEnum.CLOSED.value)
        except CircuitBreakerState.DoesNotExist:
            return (False, "", "")
