"""
Admin configuration for Payment domain.

Models: Payment, PaymentLog
"""

import json

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from shopping.models.payment import Payment, PaymentLog


# ============================================
# Admin Classes
# ============================================


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    Payment admin page configuration.

    Features:
    - Colored status display
    - Order and receipt links
    - Card information management
    - Cancel and refund tracking
    """

    list_display = [
        "id",
        "order_link",
        "order_id",
        "colored_status",
        "amount_display",
        "method",
        "user_display",
        "approved_at",
        "created_at",
    ]

    list_filter = [
        "status",
        "method",
        "is_canceled",
        "created_at",
        "approved_at",
    ]

    search_fields = [
        "order_id",
        "payment_key",
        "order__user__username",
        "order__user__email",
        "order__shipping_name",
    ]

    readonly_fields = [
        "payment_key",
        "order_id",
        "approved_at",
        "canceled_at",
        "receipt_url_link",
        "raw_response_formatted",
        "created_at",
        "updated_at",
    ]

    fieldsets = (
        ("Order Information", {"fields": ("order", "order_id")}),
        (
            "Payment Information",
            {
                "fields": (
                    "payment_key",
                    "amount",
                    "status",
                    "method",
                    "approved_at",
                    "receipt_url_link",
                )
            },
        ),
        (
            "Card Information",
            {
                "fields": (
                    "card_company",
                    "card_number",
                    "installment_plan_months",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Cancellation Information",
            {
                "fields": (
                    "is_canceled",
                    "canceled_amount",
                    "cancel_reason",
                    "canceled_at",
                ),
                "classes": ("collapse",),
            },
        ),
        ("Failure Information", {"fields": ("fail_reason",), "classes": ("collapse",)}),
        (
            "Raw Response Data",
            {"fields": ("raw_response_formatted",), "classes": ("collapse",)},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def order_link(self, obj):
        """Display order number with link to order admin."""
        if obj.order:
            url = reverse("admin:shopping_order_change", args=[obj.order.pk])
            return format_html('<a href="{}">{}</a>', url, obj.order.order_number)
        return "-"

    order_link.short_description = "Order Number"

    def colored_status(self, obj):
        """Display status with color indicator."""
        colors = {
            "ready": "#FFA500",
            "in_progress": "#4169E1",
            "done": "#008000",
            "canceled": "#DC143C",
            "aborted": "#8B0000",
            "expired": "#808080",
        }
        color = colors.get(obj.status, "#000000")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    colored_status.short_description = "Status"

    def amount_display(self, obj):
        """Display amount in Korean Won format."""
        formatted_amount = "{:,}".format(int(obj.amount))
        return format_html("<strong>{}원</strong>", formatted_amount)

    amount_display.short_description = "Amount"

    def user_display(self, obj):
        """Display user information."""
        if obj.order and obj.order.user:
            user = obj.order.user
            return f"{user.username} ({user.email})"
        return "-"

    user_display.short_description = "User"

    def receipt_url_link(self, obj):
        """Display receipt link."""
        if obj.receipt_url:
            return format_html('<a href="{}" target="_blank">View Receipt</a>', obj.receipt_url)
        return "-"

    receipt_url_link.short_description = "Receipt"

    def raw_response_formatted(self, obj):
        """Display formatted JSON response."""
        if obj.raw_response:
            formatted = json.dumps(obj.raw_response, indent=2, ensure_ascii=False)
            return format_html("<pre>{}</pre>", formatted)
        return "-"

    raw_response_formatted.short_description = "Toss Payments Response"

    def has_delete_permission(self, request, obj=None):
        """Prevent payment deletion."""
        return False

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related("order", "order__user")


@admin.register(PaymentLog)
class PaymentLogAdmin(admin.ModelAdmin):
    """
    Payment log admin page configuration.

    Features:
    - Colored log type display
    - Read-only audit trail
    """

    list_display = [
        "id",
        "payment_link",
        "colored_log_type",
        "message_preview",
        "created_at",
    ]

    list_filter = [
        "log_type",
        "created_at",
    ]

    search_fields = [
        "payment__order_id",
        "payment__payment_key",
        "message",
    ]

    readonly_fields = [
        "payment",
        "log_type",
        "message",
        "data_formatted",
        "created_at",
    ]

    fieldsets = (
        ("Basic Information", {"fields": ("payment", "log_type", "message")}),
        ("Additional Data", {"fields": ("data_formatted",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at",)}),
    )

    def payment_link(self, obj):
        """Display order ID with link to payment admin."""
        url = reverse("admin:shopping_payment_change", args=[obj.payment.pk])
        return format_html('<a href="{}">{}</a>', url, obj.payment.order_id)

    payment_link.short_description = "Order ID"

    def colored_log_type(self, obj):
        """Display log type with color indicator."""
        colors = {
            "request": "#4169E1",
            "approve": "#008000",
            "cancel": "#FFA500",
            "webhook": "#9370DB",
            "error": "#DC143C",
        }
        color = colors.get(obj.log_type, "#000000")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_log_type_display(),
        )

    colored_log_type.short_description = "Log Type"

    def message_preview(self, obj):
        """Display truncated message (50 chars)."""
        if len(obj.message) > 50:
            return obj.message[:50] + "..."
        return obj.message

    message_preview.short_description = "Message"

    def data_formatted(self, obj):
        """Display formatted JSON data."""
        if obj.data:
            formatted = json.dumps(obj.data, indent=2, ensure_ascii=False)
            return format_html("<pre>{}</pre>", formatted)
        return "-"

    data_formatted.short_description = "Data"

    def has_add_permission(self, request):
        """Prevent manual log creation."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent log deletion."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent log modification."""
        return False
