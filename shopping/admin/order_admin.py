"""
Admin configuration for Order domain.

Models: Order, OrderItem, Cart, CartItem
"""

from django.contrib import admin

from shopping.models import Cart, CartItem, Order, OrderItem


# ============================================
# Inline Classes
# ============================================


class OrderItemInline(admin.TabularInline):
    """Inline for OrderItem within Order admin (read-only)."""

    model = OrderItem
    extra = 0
    readonly_fields = ["product", "quantity", "price"]
    can_delete = False


class CartItemInline(admin.TabularInline):
    """Inline for CartItem within Cart admin."""

    model = CartItem
    extra = 0
    fields = ["product", "quantity", "get_subtotal", "added_at"]
    readonly_fields = ["get_subtotal", "added_at"]

    def get_subtotal(self, obj):
        """Display subtotal in Korean Won format."""
        return f"₩{obj.subtotal:,.0f}"

    get_subtotal.short_description = "Subtotal"


# ============================================
# Admin Classes
# ============================================


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Order admin page configuration.

    Features:
    - Status-based filtering and actions
    - Points tracking (used, earned)
    - Shipping information management
    """

    list_display = [
        "id",
        "user",
        "status",
        "formatted_total_amount",
        "created_at",
        "used_points",
        "final_amount",
        "earned_points",
    ]

    list_filter = ["status", "created_at"]
    search_fields = ["order_number", "user__username", "user__email"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    readonly_fields = ["order_number", "total_amount", "created_at", "updated_at"]

    fieldsets = (
        (
            "Order Information",
            {"fields": ("user", "status", "order_number", "total_amount")},
        ),
        (
            "Shipping Information",
            {
                "fields": (
                    "shipping_name",
                    "shipping_phone",
                    ("shipping_postal_code", "shipping_address"),
                    "shipping_address_detail",
                    "order_memo",
                )
            },
        ),
        ("Payment Information", {"fields": ("payment_method",), "classes": ("collapse",)}),
        (
            "Points",
            {
                "fields": (
                    "used_points",
                    "final_amount",
                    "earned_points",
                ),
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

    inlines = [OrderItemInline]

    actions = ["mark_as_paid", "mark_as_shipped", "mark_as_delivered"]

    def formatted_total_amount(self, obj):
        """Display total amount in Korean Won format."""
        return f"₩{obj.total_amount:,.0f}"

    formatted_total_amount.short_description = "Total Amount"
    formatted_total_amount.admin_order_field = "total_amount"

    @admin.action(description="Mark selected orders as PAID")
    def mark_as_paid(self, request, queryset):
        """Change selected orders status to paid."""
        queryset.update(status="paid")
        self.message_user(request, f"{queryset.count()} order(s) marked as paid.")

    @admin.action(description="Mark selected orders as SHIPPED")
    def mark_as_shipped(self, request, queryset):
        """Change selected orders status to shipped."""
        queryset.update(status="shipped")
        self.message_user(request, f"{queryset.count()} order(s) marked as shipped.")

    @admin.action(description="Mark selected orders as DELIVERED")
    def mark_as_delivered(self, request, queryset):
        """Change selected orders status to delivered."""
        queryset.update(status="delivered")
        self.message_user(request, f"{queryset.count()} order(s) marked as delivered.")


class CartAdmin(admin.ModelAdmin):
    """
    Cart admin page configuration.

    Features:
    - Item count display
    - CartItem inline management
    """

    list_display = [
        "id",
        "user",
        "session_key",
        "item_count",
        "created_at",
    ]

    list_filter = ["created_at"]
    search_fields = ["user__username", "session_key"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    inlines = [CartItemInline]

    def item_count(self, obj):
        """Display number of items in cart."""
        return obj.items.count()

    item_count.short_description = "Item Count"


# Register Cart with custom admin
admin.site.register(Cart, CartAdmin)
