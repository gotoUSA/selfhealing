"""
Admin configuration for Notification domain.

Models: Notification, EmailVerificationToken, EmailLog
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from shopping.models.email_verification import EmailLog, EmailVerificationToken
from shopping.models.notification import Notification


# ============================================
# Admin Classes
# ============================================


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    """
    Email verification token admin page configuration.

    Features:
    - Token status display (used/expired/valid)
    - Verification code display
    - User email search
    """

    list_display = [
        "user_email",
        "verification_code_display",
        "status_display",
        "created_at",
        "is_expired_display",
        "used_at",
    ]

    list_filter = [
        "is_used",
        "created_at",
        ("user", admin.RelatedOnlyFieldListFilter),
    ]

    search_fields = ["user__email", "user__username", "verification_code", "token"]

    readonly_fields = ["token", "verification_code", "created_at", "used_at"]

    ordering = ["created_at"]

    def user_email(self, obj):
        """Display user email address."""
        return obj.user.email

    user_email.short_description = "User Email"
    user_email.admin_order_field = "user__email"

    def verification_code_display(self, obj):
        """Display verification code with styling."""
        return format_html(
            '<code style="font-size: 14px; font-weight: bold; '
            'background: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{}</code>',
            obj.verification_code,
        )

    verification_code_display.short_description = "Verification Code"

    def status_display(self, obj):
        """Display token status with colored indicator."""
        if obj.is_used:
            return format_html('<span style="color: green;">✓ Used</span>')
        elif obj.is_expired():
            return format_html('<span style="color: red;">✗ Expired</span>')
        else:
            return format_html('<span style="color: blue;">● Valid</span>')

    status_display.short_description = "Status"

    def is_expired_display(self, obj):
        """Display expiration status as boolean."""
        return obj.is_expired()

    is_expired_display.short_description = "Expired"
    is_expired_display.boolean = True

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related("user")


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    """
    Email log admin page configuration.

    Features:
    - Email status badges
    - Statistics summary
    - Status-based actions
    """

    list_display = [
        "id",
        "email_type",
        "recipient_email",
        "status_badge",
        "sent_at",
        "verified_at",
        "created_at",
    ]

    list_filter = ["email_type", "status", "created_at", "sent_at", "verified_at"]

    search_fields = ["recipient_email", "subject", "user__email", "user__username"]

    readonly_fields = [
        "created_at",
        "sent_at",
        "opened_at",
        "clicked_at",
        "verified_at",
    ]

    ordering = ["-created_at"]

    date_hierarchy = "created_at"

    actions = ["mark_as_sent", "mark_as_failed"]

    def status_badge(self, obj):
        """Display status as colored badge with icon."""
        colors = {
            "pending": "#ffc107",
            "sent": "#28a745",
            "failed": "#dc3545",
            "opened": "#17a2b8",
            "clicked": "#6610f2",
            "verified": "#28a745",
        }

        icons = {
            "pending": "⏳",
            "sent": "✉️",
            "failed": "❌",
            "opened": "👁️",
            "clicked": "👆",
            "verified": "✅",
        }

        return format_html(
            '<span style="background-color: {}; color: white; '
            'padding: 3px 8px; border-radius: 3px; font-size: 11px;">'
            "{} {}</span>",
            colors.get(obj.status, "#6c757d"),
            icons.get(obj.status, ""),
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return super().get_queryset(request).select_related("user", "token")

    @admin.action(description="Mark selected emails as SENT")
    def mark_as_sent(self, request, queryset):
        """Mark pending emails as sent."""
        updated = queryset.filter(status="pending").update(status="sent", sent_at=timezone.now())
        self.message_user(request, f"{updated} email(s) marked as sent.")

    @admin.action(description="Mark selected emails as FAILED")
    def mark_as_failed(self, request, queryset):
        """Mark pending emails as failed."""
        updated = queryset.filter(status="pending").update(status="failed")
        self.message_user(request, f"{updated} email(s) marked as failed.")

    def changelist_view(self, request, extra_context=None):
        """Add email statistics summary to changelist view."""
        extra_context = extra_context or {}

        # Today's statistics
        today = timezone.now().date()
        today_logs = EmailLog.objects.filter(created_at__date=today, email_type="verification")

        # Total statistics
        total_logs = EmailLog.objects.filter(email_type="verification")

        extra_context["summary"] = {
            "today_sent": today_logs.filter(status="sent").count(),
            "today_verified": today_logs.filter(status="verified").count(),
            "today_failed": today_logs.filter(status="failed").count(),
            "total_sent": total_logs.filter(status="sent").count(),
            "total_verified": total_logs.filter(status="verified").count(),
            "verification_rate": self._calculate_verification_rate(total_logs),
        }

        return super().changelist_view(request, extra_context=extra_context)

    def _calculate_verification_rate(self, queryset):
        """Calculate email verification rate."""
        sent_count = queryset.filter(status="sent").count()
        verified_count = queryset.filter(status="verified").count()

        if sent_count > 0:
            rate = (verified_count / sent_count) * 100
            return f"{rate:.1f}%"
        return "0%"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """
    Notification admin page configuration.

    Features:
    - Notification type filtering
    - Read status tracking
    - Read-only creation (system only)
    """

    list_display = [
        "id",
        "user",
        "notification_type",
        "title",
        "is_read",
        "created_at",
    ]

    list_filter = [
        "notification_type",
        "is_read",
        "created_at",
    ]

    search_fields = [
        "user__username",
        "user__email",
        "title",
        "message",
    ]

    date_hierarchy = "created_at"

    ordering = ["-created_at"]

    readonly_fields = ["created_at", "read_at"]

    fieldsets = (
        ("Basic Information", {"fields": ("user", "notification_type", "title", "message")}),
        ("Link", {"fields": ("link",)}),
        ("Status", {"fields": ("is_read", "read_at")}),
        ("Timestamps", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        """Prevent manual notification creation (system only)."""
        return False
