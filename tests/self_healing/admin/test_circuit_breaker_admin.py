"""
Circuit Breaker Admin 상속 테스트.

shopping 앱의 CircuitBreakerStateAdmin이 BaseCircuitBreakerStateAdmin을
올바르게 상속하는지 확인합니다.
"""

import pytest
from unittest.mock import MagicMock


class TestCircuitBreakerStateAdminInheritance:
    """Admin 상속 관계 테스트."""

    def test_inherits_from_base_circuit_breaker_admin(self):
        """CircuitBreakerStateAdmin이 BaseCircuitBreakerStateAdmin을 상속하는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        assert issubclass(CircuitBreakerStateAdmin, BaseCircuitBreakerStateAdmin)

    def test_list_display_inherited(self):
        """list_display가 상속되어 있는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        assert CircuitBreakerStateAdmin.list_display == BaseCircuitBreakerStateAdmin.list_display

    def test_list_filter_inherited(self):
        """list_filter가 상속되어 있는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        assert CircuitBreakerStateAdmin.list_filter == BaseCircuitBreakerStateAdmin.list_filter

    def test_search_fields_inherited(self):
        """search_fields가 상속되어 있는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        assert CircuitBreakerStateAdmin.search_fields == BaseCircuitBreakerStateAdmin.search_fields

    def test_actions_inherited(self):
        """actions가 상속되어 있는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        assert CircuitBreakerStateAdmin.actions == BaseCircuitBreakerStateAdmin.actions

    def test_fieldsets_inherited(self):
        """fieldsets가 상속되어 있는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        assert CircuitBreakerStateAdmin.fieldsets == BaseCircuitBreakerStateAdmin.fieldsets


class TestCircuitBreakerStateAdminMethods:
    """Admin 메서드가 상속되어 동작하는지 테스트."""

    def test_state_display_inherited(self):
        """state_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin

        admin = CircuitBreakerStateAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.state = "closed"
        obj.get_state_display.return_value = "Closed"

        result = admin.state_display(obj)
        assert "green" in result
        assert "Closed" in result

    def test_manually_controlled_display_inherited(self):
        """manually_controlled_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.circuit_breaker_admin import CircuitBreakerStateAdmin

        admin = CircuitBreakerStateAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.manually_controlled = True

        result = admin.manually_controlled_display(obj)
        assert "Manual" in result


class TestCircuitBreakerStateAdminCustomization:
    """호스트 앱에서 오버라이드 가능한지 테스트."""

    def test_can_override_list_display(self):
        """list_display를 오버라이드할 수 있는지 확인."""
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        class CustomAdmin(BaseCircuitBreakerStateAdmin):
            list_display = ["service_name", "state_display"]

        assert CustomAdmin.list_display == ["service_name", "state_display"]
        assert CustomAdmin.list_display != BaseCircuitBreakerStateAdmin.list_display

    def test_can_add_custom_actions(self):
        """커스텀 actions를 추가할 수 있는지 확인."""
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin

        class CustomAdmin(BaseCircuitBreakerStateAdmin):
            actions = BaseCircuitBreakerStateAdmin.actions + ["custom_action"]

        assert "custom_action" in CustomAdmin.actions
        assert len(CustomAdmin.actions) == len(BaseCircuitBreakerStateAdmin.actions) + 1
