"""
Django Admin Configuration for Self-Healing Models.

Provides admin interfaces for:
- FailedOperationAdmin: DLQ management
- CircuitBreakerStateAdmin: Circuit breaker controls
- SecurityIncidentAdmin: Security incident monitoring
"""

from django.contrib import admin
from django.utils.html import format_html

from selfhealing.adapters.django.models import (
    FailedOperation,
    CircuitBreakerState,
    SecurityIncident,
)


@admin.register(FailedOperation)
class FailedOperationAdmin(admin.ModelAdmin):
    """
    Failed Operation (DLQ) admin configuration.

    Provides review and replay capabilities for dead letter queue entries.
    """

    list_display = [
        "id",
        "domain",
        "failure_type",
        "status_display",
        "order_id",
        "user_id",
        "retry_count",
        "created_at",
        "resolved_at",
    ]

    list_filter = [
        "domain",
        "status",
        "failure_type",
        "created_at",
        "resolved_at",
    ]

    search_fields = [
        "failure_type",
        "error_code",
        "error_message",
        "order_id",
        "user_id",
    ]

    readonly_fields = [
        "domain",
        "failure_type",
        "order_id",
        "payment_id",
        "user_id",
        "snapshot_data",
        "error_code",
        "error_message",
        "retry_count",
        "max_retries",
        "last_retry_at",
        "request_data",
        "response_data",
        "metadata",
        "next_action_hint",
        "recommended_action",
        "created_at",
        "updated_at",
        "expires_at",
    ]

    actions = [
        "mark_as_resolved",
        "mark_as_rejected",
        "mark_as_requires_review",
        "archive_selected",
        "archive_old_resolved",
        "purge_selected_archived",
    ]

    fieldsets = (
        (
            "Classification",
            {
                "fields": ("domain", "failure_type", "status"),
            },
        ),
        (
            "References",
            {
                "fields": ("order_id", "payment_id", "user_id"),
            },
        ),
        (
            "Error Details",
            {
                "fields": (
                    "error_code",
                    "error_message",
                    "next_action_hint",
                    "recommended_action",
                ),
            },
        ),
        (
            "Retry Information",
            {
                "fields": ("retry_count", "max_retries", "last_retry_at"),
                "classes": ("collapse",),
            },
        ),
        (
            "Resolution",
            {
                "fields": (
                    "resolved_at",
                    "resolved_by_id",
                    "resolution_type",
                    "resolution_note",
                ),
            },
        ),
        (
            "Forensic Data",
            {
                "fields": (
                    "snapshot_data",
                    "request_data",
                    "response_data",
                    "metadata",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at", "expires_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def status_display(self, obj):
        """Display status with color indicator."""
        colors = {
            "pending": "orange",
            "reviewing": "blue",
            "replayed": "purple",
            "requires_review": "red",
            "resolved": "green",
            "rejected": "gray",
            "archived": "lightgray",
            "expired": "lightgray",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_display.short_description = "Status"
    status_display.admin_order_field = "status"

    @admin.action(description="Mark as Resolved")
    def mark_as_resolved(self, request, queryset):
        """Mark selected entries as resolved."""
        count = queryset.update(status=FailedOperation.Status.RESOLVED)
        self.message_user(request, f"{count} entries marked as resolved.")

    @admin.action(description="Mark as Rejected")
    def mark_as_rejected(self, request, queryset):
        """Mark selected entries as rejected."""
        count = queryset.update(status=FailedOperation.Status.REJECTED)
        self.message_user(request, f"{count} entries marked as rejected.")

    @admin.action(description="Mark as Requires Review")
    def mark_as_requires_review(self, request, queryset):
        """Mark selected entries as requiring review."""
        count = queryset.update(status=FailedOperation.Status.REQUIRES_REVIEW)
        self.message_user(request, f"{count} entries marked as requires review.")

    @admin.action(description="Archive selected (resolved only)")
    def archive_selected(self, request, queryset):
        """Archive selected entries (only resolved entries can be archived)."""
        resolved_queryset = queryset.filter(status=FailedOperation.Status.RESOLVED)
        count = resolved_queryset.update(status=FailedOperation.Status.ARCHIVED)
        skipped = queryset.count() - count
        if skipped > 0:
            self.message_user(
                request,
                f"{count} entries archived. {skipped} skipped (not resolved).",
                level="warning",
            )
        else:
            self.message_user(request, f"{count} entries archived.")

    @admin.action(description="Archive all resolved older than 30 days")
    def archive_old_resolved(self, request, queryset):
        """Archive all resolved entries older than 30 days."""
        from django.utils import timezone
        from datetime import timedelta

        cutoff = timezone.now() - timedelta(days=30)
        count = FailedOperation.objects.filter(
            status=FailedOperation.Status.RESOLVED,
            resolved_at__lt=cutoff,
        ).update(status=FailedOperation.Status.ARCHIVED)
        self.message_user(request, f"{count} entries archived (resolved > 30 days ago).")

    @admin.action(description="⚠️ PERMANENTLY DELETE selected archived")
    def purge_selected_archived(self, request, queryset):
        """Permanently delete selected archived entries."""
        archived_queryset = queryset.filter(status=FailedOperation.Status.ARCHIVED)
        count = archived_queryset.count()
        non_archived = queryset.count() - count

        if count > 0:
            archived_queryset.delete()
            msg = f"⚠️ {count} archived entries permanently deleted."
            if non_archived > 0:
                msg += f" {non_archived} skipped (not archived)."
            self.message_user(request, msg)
        else:
            self.message_user(
                request,
                "No archived entries selected. Only archived entries can be purged.",
                level="error",
            )


@admin.register(CircuitBreakerState)
class CircuitBreakerStateAdmin(admin.ModelAdmin):
    """
    Circuit Breaker State admin configuration.

    Provides operational controls for managing circuit breakers.
    """

    list_display = [
        "service_name",
        "state_display",
        "failure_count",
        "success_count",
        "manually_controlled_display",
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
        "last_success_at",
        "opened_at",
        "half_opened_at",
        "created_at",
        "updated_at",
    ]

    actions = [
        "force_open_selected",
        "force_close_selected",
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
                "fields": (
                    "failure_count",
                    "success_count",
                    "last_failure_at",
                    "last_success_at",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Configuration",
            {
                "fields": (
                    "failure_threshold",
                    "recovery_timeout",
                    "half_open_max_calls",
                ),
            },
        ),
        (
            "Manual Control",
            {
                "fields": (
                    "manually_controlled",
                    "controlled_by_id",
                    "control_reason",
                    "manual_override_expires_at",
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("opened_at", "half_opened_at", "created_at", "updated_at"),
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
        from django.utils import timezone

        count = queryset.update(
            state=CircuitBreakerState.State.OPEN,
            opened_at=timezone.now(),
            manually_controlled=True,
            control_reason=f"Forced open by admin: {request.user.username}",
        )
        self.message_user(request, f"Successfully opened {count} circuit breaker(s).")

    @admin.action(description="Force CLOSE selected circuits (allow requests)")
    def force_close_selected(self, request, queryset):
        """Force close selected circuit breakers."""
        count = queryset.update(
            state=CircuitBreakerState.State.CLOSED,
            failure_count=0,
            success_count=0,
            opened_at=None,
            half_opened_at=None,
            manually_controlled=True,
            control_reason=f"Forced closed by admin: {request.user.username}",
        )
        self.message_user(request, f"Successfully closed {count} circuit breaker(s).")

    @admin.action(description="Reset selected circuits to initial state")
    def reset_selected(self, request, queryset):
        """Reset selected circuit breakers."""
        for circuit in queryset:
            circuit.reset()
        self.message_user(request, f"Successfully reset {queryset.count()} circuit breaker(s).")


@admin.register(SecurityIncident)
class SecurityIncidentAdmin(admin.ModelAdmin):
    """
    Security Incident admin configuration.

    Provides monitoring and investigation capabilities for security incidents.
    """

    list_display = [
        "id",
        "severity_display",
        "incident_type",
        "status_display",
        "source_ip",
        "user_id",
        "created_at",
        "resolved_at",
    ]

    list_filter = [
        "severity",
        "incident_type",
        "status",
        "created_at",
    ]

    search_fields = [
        "description",
        "source_ip",
        "user_id",
        "investigation_notes",
    ]

    readonly_fields = [
        "incident_type",
        "severity",
        "source_ip",
        "user_agent",
        "user_id",
        "description",
        "context",
        "raw_request",
        "order_id",
        "payment_id",
        "created_at",
    ]

    actions = [
        "mark_as_investigating",
        "mark_as_resolved",
        "mark_as_false_positive",
    ]

    fieldsets = (
        (
            "Incident Details",
            {
                "fields": ("incident_type", "severity", "status", "description"),
            },
        ),
        (
            "Source Information",
            {
                "fields": ("source_ip", "user_agent", "user_id"),
            },
        ),
        (
            "Related Objects",
            {
                "fields": ("order_id", "payment_id"),
            },
        ),
        (
            "Investigation",
            {
                "fields": (
                    "action_taken",
                    "investigated_by_id",
                    "investigation_notes",
                    "resolved_at",
                ),
            },
        ),
        (
            "Forensic Data",
            {
                "fields": ("context", "raw_request"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def severity_display(self, obj):
        """Display severity with color indicator."""
        colors = {
            "critical": "darkred",
            "high": "red",
            "medium": "orange",
            "low": "gray",
        }
        color = colors.get(obj.severity, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_severity_display(),
        )

    severity_display.short_description = "Severity"
    severity_display.admin_order_field = "severity"

    def status_display(self, obj):
        """Display status with color indicator."""
        colors = {
            "open": "red",
            "investigating": "orange",
            "resolved": "green",
            "false_positive": "gray",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_display.short_description = "Status"
    status_display.admin_order_field = "status"

    @admin.action(description="Mark as Investigating")
    def mark_as_investigating(self, request, queryset):
        """Mark selected incidents as under investigation."""
        count = queryset.update(status=SecurityIncident.Status.INVESTIGATING)
        self.message_user(request, f"{count} incidents marked as investigating.")

    @admin.action(description="Mark as Resolved")
    def mark_as_resolved(self, request, queryset):
        """Mark selected incidents as resolved."""
        from django.utils import timezone

        count = queryset.update(
            status=SecurityIncident.Status.RESOLVED,
            resolved_at=timezone.now(),
        )
        self.message_user(request, f"{count} incidents marked as resolved.")

    @admin.action(description="Mark as False Positive")
    def mark_as_false_positive(self, request, queryset):
        """Mark selected incidents as false positive."""
        from django.utils import timezone

        count = queryset.update(
            status=SecurityIncident.Status.FALSE_POSITIVE,
            resolved_at=timezone.now(),
        )
        self.message_user(request, f"{count} incidents marked as false positive.")
