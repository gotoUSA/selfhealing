"""
Admin configuration for Product domain.

Models: Product, Category, ProductImage, ProductReview
"""

from django.contrib import admin

from mptt.admin import DraggableMPTTAdmin

from shopping.models import Category, Product, ProductImage, ProductReview


# ============================================
# Inline Classes
# ============================================


class ProductImageInline(admin.TabularInline):
    """Inline for ProductImage within Product admin."""

    model = ProductImage
    extra = 1
    fields = ["image", "alt_text", "order", "is_primary"]
    ordering = ["order"]


class ProductReviewInline(admin.TabularInline):
    """Inline for ProductReview within Product admin (read-only)."""

    model = ProductReview
    extra = 0
    readonly_fields = ["user", "rating", "comment", "created_at"]
    can_delete = False


# ============================================
# Admin Classes
# ============================================


@admin.register(Category)
class CategoryAdmin(DraggableMPTTAdmin):
    """
    Category admin with MPTT drag-and-drop functionality.

    Features:
    - Tree structure visualization
    - Product count per category
    - Cumulative product count (including descendants)
    """

    mptt_level_indent = 20

    list_display = [
        "tree_actions",
        "indented_title",
        "related_products_count",
        "related_products_cumulative_count",
    ]

    list_display_links = ["indented_title"]

    list_filter = [
        "is_active",
    ]

    search_fields = ["name", "slug"]

    prepopulated_fields = {"slug": ("name",)}

    readonly_fields = ["created_at", "updated_at"]

    def related_products_count(self, obj):
        """Count products directly in this category."""
        return obj.products.count()

    related_products_count.short_description = "Direct Products"

    def related_products_cumulative_count(self, obj):
        """Count products in this category and all descendants."""
        return Product.objects.filter(
            category__in=obj.get_descendants(include_self=True)
        ).count()

    related_products_cumulative_count.short_description = "Total Products"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """
    Product admin page configuration.

    Features:
    - Image and review inline editing
    - Price formatting
    - Stock management
    """

    list_display = [
        "name",
        "category",
        "seller",
        "price",
        "formatted_price",
        "stock",
        "is_active",
        "created_at",
    ]

    list_filter = ["category", "is_active", "created_at"]
    search_fields = ["name", "description", "sku"]
    prepopulated_fields = {"slug": ("name",)}
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Basic Information", {"fields": ("name", "slug", "category", "sku", "seller")}),
        ("Price & Stock", {"fields": ("price", "stock")}),
        ("Details", {"fields": ("description", "is_active")}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    inlines = [ProductImageInline, ProductReviewInline]

    def formatted_price(self, obj):
        """Display price in Korean Won format."""
        return f"₩{obj.price:,.0f}"

    formatted_price.short_description = "Price"
    formatted_price.admin_order_field = "price"


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    """
    Product review admin page configuration.

    Features:
    - Rating filter
    - Comment preview
    """

    list_display = ["product", "user", "rating", "comment_preview", "created_at"]
    list_filter = ["rating", "created_at"]
    search_fields = ["product__name", "user__username", "comment"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    def comment_preview(self, obj):
        """Display truncated comment (50 chars)."""
        if len(obj.comment) > 50:
            return obj.comment[:50] + "..."
        return obj.comment

    comment_preview.short_description = "Review"
