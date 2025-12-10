"""
API layer for the self-healing system.

This module contains REST API implementations for various frameworks.

Available APIs:
- django: Django REST Framework views, serializers, and URLs

Usage:
    # In your Django project's urls.py:
    from selfhealing.api.django import urls as selfhealing_urls

    urlpatterns = [
        path('api/self-healing/', include(selfhealing_urls)),
    ]
"""

# API implementations are optional

__all__ = []

# Try to import Django API
try:
    from selfhealing.api import django
    __all__.append("django")
except ImportError:
    pass
