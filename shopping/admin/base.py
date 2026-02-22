"""
Common admin mixins and utilities.

STRICT SCOPE: Display formatting and readonly operations ONLY.
NO business logic, NO database writes, NO external calls.
"""

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.utils.html import format_html

import json


class DisplayFormattingMixin:
    """Common display formatting methods for admin classes."""

    @staticmethod
    def format_currency(amount):
        """Format amount as Korean Won."""
        if amount is None:
            return "-"
        return f"₩{amount:,.0f}"

    @staticmethod
    def format_status_badge(status, color_map=None):
        """Render status as colored badge."""
        default_colors = {
            "SUCCESS": "green",
            "FAILED": "red",
            "PENDING": "orange",
        }
        colors = color_map or default_colors
        color = colors.get(status, "gray")
        return format_html(
            '<span style="background:{}; padding:2px 8px; ' 'border-radius:4px; color:white;">{}</span>',
            color,
            status,
        )


class ReadOnlyAdminMixin:
    """Mixin for read-only admin views."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MaskingMixin:
    """Mixin for masking sensitive data in admin displays."""

    @staticmethod
    def mask_email(email):
        """Mask email address: t***@example.com"""
        if not email or "@" not in email:
            return email
        local, domain = email.split("@", 1)
        return f"{local[0]}***@{domain}"

    @staticmethod
    def mask_phone(phone):
        """Mask phone number: 010-****-5678"""
        if not phone or len(phone) < 8:
            return phone
        return f"{phone[:3]}-****-{phone[-4:]}"

    @staticmethod
    def mask_card_number(card_number):
        """Mask card number: ****-****-****-1234"""
        if not card_number or len(card_number) < 4:
            return card_number
        return f"****-****-****-{card_number[-4:]}"


class AdminActionLogMixin:
    """Mixin to log admin actions with before/after values."""

    def log_action_with_details(
        self,
        request,
        obj,
        action_name: str,
        old_values: dict,
        new_values: dict,
    ):
        """
        Log admin action with detailed before/after state.

        Args:
            request: HTTP request
            obj: Model instance
            action_name: Name of the action performed
            old_values: State before action
            new_values: State after action
        """
        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=ContentType.objects.get_for_model(obj).pk,
            object_id=obj.pk,
            object_repr=str(obj),
            action_flag=CHANGE,
            change_message=json.dumps(
                {
                    "action": action_name,
                    "old_values": old_values,
                    "new_values": new_values,
                    "ip_address": self._get_client_ip(request),
                    "user_agent": request.META.get("HTTP_USER_AGENT", ""),
                }
            ),
        )

    def _get_client_ip(self, request):
        """Extract client IP from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0]
        return request.META.get("REMOTE_ADDR")


class ColoredStatusMixin:
    """Mixin for rendering colored status badges."""

    STATUS_COLORS = {
        # Common statuses
        "ready": "#FFA500",
        "pending": "#FFA500",
        "in_progress": "#4169E1",
        "processing": "#4169E1",
        "done": "#008000",
        "success": "#008000",
        "completed": "#008000",
        "canceled": "#DC143C",
        "failed": "#DC143C",
        "aborted": "#8B0000",
        "expired": "#808080",
        # Payment statuses
        "approved": "#198754",
        "rejected": "#dc3545",
        # Circuit breaker states
        "closed": "green",
        "open": "red",
        "half_open": "orange",
    }

    def render_colored_status(self, status, display_text=None, color_map=None):
        """Render status with appropriate color."""
        colors = color_map or self.STATUS_COLORS
        color = colors.get(status, "#000000")
        text = display_text or status
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            text,
        )

    def render_status_badge(self, status, display_text=None, color_map=None):
        """Render status as a colored badge."""
        colors = color_map or self.STATUS_COLORS
        color = colors.get(status, "#6c757d")
        text = display_text or status
        return format_html(
            '<span style="background-color: {}; color: white; '
            'padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            text,
        )


class PreviewMixin:
    """Mixin for text preview methods."""

    @staticmethod
    def truncate_text(text, max_length=50):
        """Truncate text to specified length with ellipsis."""
        if not text:
            return "-"
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text


class JsonDisplayMixin:
    """Mixin for displaying JSON data in admin."""

    @staticmethod
    def format_json_display(data):
        """Format JSON data for admin display."""
        if not data:
            return "-"
        formatted = json.dumps(data, indent=2, ensure_ascii=False)
        return format_html("<pre>{}</pre>", formatted)
