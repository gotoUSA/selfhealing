"""
Django App Configuration for Self-Healing.

This allows the selfhealing.adapters.django module to be used
as a Django app in INSTALLED_APPS.
"""

from django.apps import AppConfig


class SelfHealingConfig(AppConfig):
    """Django app configuration for self-healing."""

    name = "selfhealing.adapters.django"
    label = "selfhealing"
    verbose_name = "Self-Healing System"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """Called when the app is ready."""
        # Import admin to register admin classes
        try:
            from selfhealing.adapters.django import admin  # noqa: F401
        except ImportError:
            pass
