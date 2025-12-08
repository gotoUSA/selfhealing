"""
Admin configuration for Q&A domain.

Models: ProductQuestion, ProductAnswer
"""

from django.contrib import admin

from shopping.models.product_qa import ProductAnswer, ProductQuestion


# ============================================
# Inline Classes
# ============================================


class ProductAnswerInline(admin.StackedInline):
    """Inline for ProductAnswer within ProductQuestion admin."""

    model = ProductAnswer
    extra = 0
    readonly_fields = ["created_at", "updated_at"]
    can_delete = True


# ============================================
# Admin Classes
# ============================================


@admin.register(ProductQuestion)
class ProductQuestionAdmin(admin.ModelAdmin):
    """
    Product question admin page configuration.

    Features:
    - Secret question handling
    - Answer status display
    - Inline answer management
    """

    list_display = [
        "id",
        "product",
        "user",
        "title_preview",
        "is_secret",
        "is_answered",
        "created_at",
    ]

    list_filter = [
        "is_secret",
        "is_answered",
        "created_at",
    ]

    search_fields = [
        "title",
        "content",
        "user__username",
        "product__name",
    ]

    date_hierarchy = "created_at"

    ordering = ["-created_at"]

    readonly_fields = ["created_at", "updated_at"]

    inlines = [ProductAnswerInline]

    fieldsets = (
        ("Basic Information", {"fields": ("product", "user")}),
        ("Question Content", {"fields": ("title", "content", "is_secret")}),
        ("Status", {"fields": ("is_answered",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def title_preview(self, obj):
        """Display truncated title (30 chars)."""
        if len(obj.title) > 30:
            return obj.title[:30] + "..."
        return obj.title

    title_preview.short_description = "Title"


@admin.register(ProductAnswer)
class ProductAnswerAdmin(admin.ModelAdmin):
    """
    Product answer admin page configuration.

    Features:
    - Answer content preview
    - Seller information display
    """

    list_display = [
        "id",
        "question",
        "seller",
        "content_preview",
        "created_at",
    ]

    search_fields = [
        "content",
        "question__title",
        "seller__username",
    ]

    date_hierarchy = "created_at"

    ordering = ["-created_at"]

    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Basic Information", {"fields": ("question", "seller")}),
        ("Answer Content", {"fields": ("content",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def content_preview(self, obj):
        """Display truncated content (50 chars)."""
        if len(obj.content) > 50:
            return obj.content[:50] + "..."
        return obj.content

    content_preview.short_description = "Answer Content"
