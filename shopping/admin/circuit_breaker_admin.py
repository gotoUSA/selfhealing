"""
Admin configuration for Circuit Breaker domain.

Models: CircuitBreakerState
"""

from django.contrib import admin
from django.utils.html import format_html

from shopping.models.failed_external_request import CircuitBreakerState


# ============================================
# Admin Classes
# ============================================


@admin.register(CircuitBreakerState)
class CircuitBreakerStateAdmin(admin.ModelAdmin):
    """
    Circuit Breaker State admin page configuration.

    Provides operational controls for managing external service circuit breakers.
    Supports force open/close operations with audit logging.
    """

    list_display = [
        "service_name",
        "state_display",
        "failure_count",
        "success_count",
        "manually_controlled_display",
        "controlled_by_id",
        "opened_at",
        "updated_at",
    ]

    list_filter = [
        "state",
        "manually_controlled",
        "created_at",
    ]

    search_fields = [
        "service_name",
        "control_reason",
    ]

    readonly_fields = [
        "failure_count",
        "success_count",
        "last_failure_at",
        "opened_at",
        "created_at",
        "updated_at",
    ]

    actions = [
        "force_open_selected",
        "force_close_selected",
        "force_close_with_replay",
        "reset_selected",
    ]

    fieldsets = (
        (
            "Service Information",
            {
                "fields": ("service_name", "state"),
            },
        ),
        (
            "Counters",
            {
                "fields": ("failure_count", "success_count", "last_failure_at"),
                "classes": ("collapse",),
            },
        ),
        (
            "Manual Control",
            {
                "fields": ("manually_controlled", "controlled_by_id", "control_reason"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("opened_at", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def state_display(self, obj):
        """Display state with color indicator."""
        colors = {
            "closed": "green",
            "open": "red",
            "half_open": "orange",
        }
        color = colors.get(obj.state, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_state_display(),
        )

    state_display.short_description = "State"
    state_display.admin_order_field = "state"

    def manually_controlled_display(self, obj):
        """Display manual control status."""
        if obj.manually_controlled:
            return format_html('<span style="color: blue;">✓ Manual</span>')
        return format_html('<span style="color: gray;">Auto</span>')

    manually_controlled_display.short_description = "Control"

    @admin.action(description="Force OPEN selected circuits (block requests)")
    def force_open_selected(self, request, queryset):
        """Force open selected circuit breakers."""
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.force_open(
                service_name=circuit.service_name,
                reason=f"Admin action by {request.user.username}",
                controlled_by=request.user,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully opened {count} circuit breaker(s).",
        )

    @admin.action(description="Force CLOSE selected circuits (allow requests)")
    def force_close_selected(self, request, queryset):
        """Force close selected circuit breakers without replay."""
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.force_close(
                service_name=circuit.service_name,
                reason=f"Admin action by {request.user.username}",
                controlled_by=request.user,
                trigger_replay=False,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully closed {count} circuit breaker(s).",
        )

    @admin.action(description="Force CLOSE with DLQ replay")
    def force_close_with_replay(self, request, queryset):
        """Force close selected circuit breakers and trigger DLQ replay."""
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.force_close(
                service_name=circuit.service_name,
                reason=f"Admin action with replay by {request.user.username}",
                controlled_by=request.user,
                trigger_replay=True,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully closed {count} circuit breaker(s) with DLQ replay triggered.",
        )

    @admin.action(description="Reset selected circuits to initial state")
    def reset_selected(self, request, queryset):
        """Reset selected circuit breakers."""
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.reset(
                service_name=circuit.service_name,
                reason=f"Admin reset by {request.user.username}",
                controlled_by=request.user,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully reset {count} circuit breaker(s).",
        )
