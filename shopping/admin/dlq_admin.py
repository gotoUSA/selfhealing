"""
Admin configuration for Failed Operation (DLQ) domain.

Models: FailedOperation
"""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from shopping.models.failed_operation import FailedOperation


# ============================================
# Admin Classes
# ============================================


@admin.register(FailedOperation)
class FailedOperationAdmin(admin.ModelAdmin):
    """
    Failed Operation (DLQ) admin page configuration.

    Provides review and replay capabilities for dead letter queue entries.
    """

    list_display = [
        "id",
        "domain",
        "failure_type",
        "status_display",
        "order_link",
        "user_link",
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
        "order__id",
        "user__username",
        "user__email",
    ]

    readonly_fields = [
        "domain",
        "failure_type",
        "order",
        "payment",
        "user",
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
        "replay_selected",
        "mark_as_resolved",
        "mark_as_rejected",
        "mark_as_requires_review",
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
                "fields": ("order", "payment", "user"),
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
                    "resolved_by",
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

    def order_link(self, obj):
        """Display order link."""
        if obj.order:
            url = reverse("admin:shopping_order_change", args=[obj.order.id])
            return format_html('<a href="{}">{}</a>', url, f"Order #{obj.order.id}")
        return "-"

    order_link.short_description = "Order"

    def user_link(self, obj):
        """Display user link."""
        if obj.user:
            url = reverse("admin:shopping_user_change", args=[obj.user.id])
            return format_html('<a href="{}">{}</a>', url, obj.user.username)
        return "-"

    user_link.short_description = "User"

    @admin.action(description="Replay selected DLQ entries")
    def replay_selected(self, request, queryset):
        """Replay selected DLQ entries."""
        from selfhealing.services import get_replay_service

        service = get_replay_service()
        success_count = 0
        fail_count = 0

        for entry in queryset.filter(status__in=["pending", "requires_review"]):
            if entry.retry_count >= entry.max_retries:
                fail_count += 1
                continue

            result = service.replay_single(entry.id)
            if result.success:
                success_count += 1
            else:
                fail_count += 1

        self.message_user(
            request,
            f"Replay complete: {success_count} succeeded, {fail_count} failed.",
        )

    @admin.action(description="Mark as RESOLVED")
    def mark_as_resolved(self, request, queryset):
        """Mark selected entries as resolved."""
        count = 0
        for entry in queryset:
            entry.mark_as_resolved(
                resolved_by=request.user,
                note=f"Manually resolved by {request.user.username}",
                resolution_type="manual_fix",
            )
            count += 1

        self.message_user(request, f"Marked {count} entries as resolved.")

    @admin.action(description="Mark as REJECTED (unrecoverable)")
    def mark_as_rejected(self, request, queryset):
        """Mark selected entries as rejected."""
        count = 0
        for entry in queryset:
            entry.mark_as_rejected(
                resolved_by=request.user,
                note=f"Rejected by {request.user.username}",
            )
            count += 1

        self.message_user(request, f"Marked {count} entries as rejected.")

    @admin.action(description="Mark as REQUIRES_REVIEW")
    def mark_as_requires_review(self, request, queryset):
        """Mark selected entries as requiring review."""
        count = 0
        for entry in queryset:
            entry.mark_as_requires_review(
                note=f"Escalated by {request.user.username}",
            )
            count += 1

        self.message_user(request, f"Marked {count} entries as requiring review.")
