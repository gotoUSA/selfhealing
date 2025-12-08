"""
Admin configuration for User domain.

Models: User, SellerProfile
"""

from django.contrib import admin

from shopping.models import User
from shopping.models.seller import SellerProfile


# ============================================
# Inline Classes
# ============================================


class SellerProfileInline(admin.StackedInline):
    """Inline for SellerProfile within User admin."""

    model = SellerProfile
    can_delete = False
    verbose_name = "Seller Profile"
    verbose_name_plural = "Seller Profile"
    fk_name = "user"

    def has_change_permission(self, request, obj=None):
        """Only show if user is a seller."""
        if obj and not obj.is_seller:
            return False
        return super().has_change_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        """Only allow adding if user is a seller."""
        if obj and not obj.is_seller:
            return False
        return super().has_add_permission(request, obj)


# ============================================
# Admin Classes
# ============================================


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """
    User admin page configuration.

    Features:
    - Wishlist count display
    - Points and membership level
    - Seller profile inline editing
    """

    list_display = [
        "username",
        "email",
        "phone_number",
        "get_wishlist_count",
        "points",
        "membership_level",
        "date_joined",
        "is_active",
    ]

    list_filter = [
        "is_active",
        "is_staff",
        "is_superuser",
        "membership_level",
        "date_joined",
    ]

    search_fields = ["username", "email", "phone_number", "first_name", "last_name"]
    date_hierarchy = "date_joined"
    ordering = ["-date_joined"]

    filter_horizontal = ("wishlist_products", "groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Personal Information",
            {"fields": ("first_name", "last_name", "email", "phone_number")},
        ),
        (
            "Shipping Information",
            {
                "fields": ("address", "address_detail", "postal_code"),
                "classes": ("collapse",),
            },
        ),
        (
            "Shopping Information",
            {
                "fields": (
                    "points",
                    "membership_level",
                    "is_seller",
                    "is_email_verified",
                    "wishlist_products",
                ),
                "classes": ("wide",),
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Important Dates",
            {
                "fields": ("last_login", "date_joined"),
                "classes": ("collapse",),
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "phone_number",
                ),
            },
        ),
    )

    readonly_fields = ("date_joined", "last_login")

    inlines = [SellerProfileInline]

    def get_wishlist_count(self, obj):
        """Display wishlist count with heart icon."""
        count = obj.wishlist_products.count()
        if count > 0:
            return f"❤️ {count}"
        return "-"

    get_wishlist_count.short_description = "Wishlist"
    get_wishlist_count.admin_order_field = "wished_by_users__count"

    def get_queryset(self, request):
        """Optimize queryset with prefetch."""
        return super().get_queryset(request).prefetch_related("wishlist_products", "seller_profile")


@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    """
    Seller profile admin page configuration.

    Features:
    - Store and business information display
    - Bank account details management
    """

    list_display = [
        "store_name",
        "user",
        "business_number",
        "representative_name",
        "bank_name",
        "created_at",
    ]

    list_filter = [
        "bank_name",
        "created_at",
    ]

    search_fields = [
        "store_name",
        "user__username",
        "user__email",
        "business_number",
        "representative_name",
    ]

    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Store Information", {"fields": ("user", "store_name", "store_description")}),
        (
            "Business Information",
            {"fields": ("business_number", "representative_name", "business_address")},
        ),
        ("Settlement Information", {"fields": ("bank_name", "bank_account", "bank_holder")}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related("user")
