"""
Admin configuration for Return domain.

Models: Return, ReturnItem
"""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from shopping.models import Return, ReturnItem


# ============================================
# Inline Classes
# ============================================


class ReturnItemInline(admin.TabularInline):
    """Inline for ReturnItem within Return admin (read-only)."""

    model = ReturnItem
    extra = 0
    readonly_fields = ["product_name", "product_price", "quantity", "get_subtotal"]
    can_delete = False

    def get_subtotal(self, obj):
        """Display return amount in Korean Won format."""
        if obj.id:
            return f"₩{obj.get_subtotal():,.0f}"
        return "-"

    get_subtotal.short_description = "Return Amount"


# ============================================
# Admin Classes
# ============================================


@admin.register(Return)
class ReturnAdmin(admin.ModelAdmin):
    """
    Return/Exchange admin page configuration.

    Features:
    - Type and status colored display
    - Approve/reject/receive actions
    - Refund and exchange tracking
    """

    list_display = [
        "return_number",
        "colored_type",
        "colored_status",
        "order_link",
        "user_display",
        "refund_amount_display",
        "created_at",
    ]

    list_filter = [
        "type",
        "status",
        "reason",
        "created_at",
    ]

    search_fields = [
        "return_number",
        "order__order_number",
        "user__username",
        "user__email",
    ]

    date_hierarchy = "created_at"

    ordering = ["-created_at"]

    readonly_fields = [
        "return_number",
        "created_at",
        "updated_at",
        "approved_at",
        "completed_at",
    ]

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "return_number",
                    "order",
                    "user",
                    "type",
                    "status",
                )
            },
        ),
        (
            "Reason",
            {
                "fields": (
                    "reason",
                    "reason_detail",
                )
            },
        ),
        (
            "Return Shipping Information",
            {
                "fields": (
                    "return_shipping_company",
                    "return_tracking_number",
                    "return_shipping_fee",
                )
            },
        ),
        (
            "Refund Information",
            {
                "fields": (
                    "refund_amount",
                    "refund_method",
                    "refund_account_bank",
                    "refund_account_number",
                    "refund_account_holder",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Exchange Information",
            {
                "fields": (
                    "exchange_product",
                    "exchange_shipping_company",
                    "exchange_tracking_number",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Processing Information",
            {
                "fields": (
                    "admin_memo",
                    "rejected_reason",
                    "approved_at",
                    "completed_at",
                )
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

    inlines = [ReturnItemInline]

    actions = ["approve_returns", "reject_returns", "mark_as_received"]

    def colored_type(self, obj):
        """Display return type with color indicator."""
        colors = {
            "refund": "#dc3545",
            "exchange": "#0d6efd",
        }
        color = colors.get(obj.type, "#6c757d")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_type_display(),
        )

    colored_type.short_description = "Type"

    def colored_status(self, obj):
        """Display status as colored badge."""
        colors = {
            "requested": "#0d6efd",
            "approved": "#198754",
            "rejected": "#dc3545",
            "shipping": "#fd7e14",
            "received": "#6610f2",
            "completed": "#198754",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; '
            'padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display(),
        )

    colored_status.short_description = "Status"

    def order_link(self, obj):
        """Display order number with link to order admin."""
        url = reverse("admin:shopping_order_change", args=[obj.order.id])
        return format_html('<a href="{}">{}</a>', url, obj.order.order_number)

    order_link.short_description = "Order Number"

    def user_display(self, obj):
        """Display user information."""
        return f"{obj.user.username} ({obj.user.email})"

    user_display.short_description = "Requester"

    def refund_amount_display(self, obj):
        """Display refund amount for refund type returns."""
        if obj.type == "refund":
            return f"₩{obj.refund_amount:,.0f}"
        return "-"

    refund_amount_display.short_description = "Refund Amount"

    @admin.action(description="Approve selected returns")
    def approve_returns(self, request, queryset):
        """Approve selected return requests."""
        count = 0
        for return_obj in queryset.filter(status="requested"):
            try:
                return_obj.approve()
                count += 1
            except ValueError as e:
                self.message_user(request, f"{return_obj.return_number}: {str(e)}", level="error")

        self.message_user(request, f"{count} return(s) approved.")

    @admin.action(description="Reject selected returns")
    def reject_returns(self, request, queryset):
        """Reject selected return requests."""
        count = 0
        for return_obj in queryset.filter(status="requested"):
            try:
                return_obj.reject("Batch rejected by admin")
                count += 1
            except ValueError as e:
                self.message_user(request, f"{return_obj.return_number}: {str(e)}", level="error")

        self.message_user(request, f"{count} return(s) rejected.")

    @admin.action(description="Mark selected as RECEIVED")
    def mark_as_received(self, request, queryset):
        """Mark selected returns as received."""
        count = 0
        for return_obj in queryset.filter(status="shipping"):
            try:
                return_obj.confirm_receive()
                count += 1
            except ValueError as e:
                self.message_user(request, f"{return_obj.return_number}: {str(e)}", level="error")

        self.message_user(request, f"{count} return(s) marked as received.")


@admin.register(ReturnItem)
class ReturnItemAdmin(admin.ModelAdmin):
    """
    Return item admin page configuration.

    Features:
    - Individual return item management
    - Read-only audit trail
    """

    list_display = [
        "id",
        "return_number",
        "product_name",
        "quantity",
        "product_price",
        "subtotal_display",
        "created_at",
    ]

    list_filter = [
        "created_at",
        "return_request__type",
        "return_request__status",
    ]

    search_fields = [
        "product_name",
        "return_request__return_number",
    ]

    readonly_fields = [
        "return_request",
        "order_item",
        "product_name",
        "product_price",
        "quantity",
        "created_at",
    ]

    def return_number(self, obj):
        """Display return request number."""
        return obj.return_request.return_number

    return_number.short_description = "Return Number"

    def subtotal_display(self, obj):
        """Display return amount in Korean Won format."""
        return f"₩{obj.get_subtotal():,.0f}"

    subtotal_display.short_description = "Return Amount"

    def has_add_permission(self, request):
        """Prevent manual item creation."""
        return False
