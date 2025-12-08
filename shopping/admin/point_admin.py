"""
Admin configuration for Point domain.

Models: PointHistory
"""

from django.contrib import admin

from shopping.models.point import PointHistory


# ============================================
# Admin Classes
# ============================================


@admin.register(PointHistory)
class PointHistoryAdmin(admin.ModelAdmin):
    """
    Point history admin page configuration.

    Features:
    - Point transaction tracking
    - Balance display
    - Expiration date filtering
    - Read-only audit trail
    """

    list_display = [
        "id",
        "user",
        "formatted_points",
        "type",
        "balance",
        "order",
        "description",
        "created_at",
    ]

    list_filter = [
        "type",
        "created_at",
        ("expires_at", admin.DateFieldListFilter),
    ]

    search_fields = [
        "user__username",
        "user__email",
        "order__order_number",
        "description",
    ]

    readonly_fields = [
        "user",
        "points",
        "balance",
        "type",
        "order",
        "description",
        "expires_at",
        "metadata",
        "created_at",
    ]

    ordering = ["-created_at"]

    def formatted_points(self, obj):
        """Display points with sign indicator."""
        if obj.points > 0:
            return f"+{obj.points}P"
        else:
            return f"{obj.points}P"

    formatted_points.short_description = "Points"

    def has_add_permission(self, request):
        """Prevent manual point creation (system only)."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent point history deletion."""
        return False

    class Media:
        css = {"all": ("admin/css/point_history.css",)}
