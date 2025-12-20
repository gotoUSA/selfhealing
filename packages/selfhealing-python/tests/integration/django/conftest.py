"""
Django Integration Test Configuration.

This conftest.py configures Django for testing the selfhealing package
independently from the main Django project.
"""

import os
import sys
import django
from django.conf import settings
import pytest


def pytest_configure(config):
    """Configure Django settings for testing."""
    # Disable pytest-django if it's auto-loading
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "")

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            USE_TZ=True,
            TIME_ZONE="UTC",
            # Database
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            # Installed apps - include admin for admin.py registration
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.admin",
                "django.contrib.sessions",
                "django.contrib.messages",
                "rest_framework",
                "selfhealing.adapters.django",
            ],
            # Middleware for admin
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ],
            # Templates for admin
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [],
                    "APP_DIRS": True,
                    "OPTIONS": {
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ],
                    },
                },
            ],
            # REST Framework settings
            REST_FRAMEWORK={
                "DEFAULT_AUTHENTICATION_CLASSES": [],
                "DEFAULT_PERMISSION_CLASSES": [
                    "rest_framework.permissions.AllowAny",
                ],
            },
            # Root URL conf (not really needed for unit tests)
            ROOT_URLCONF="selfhealing.api.django.urls",
            # Secret key for testing
            SECRET_KEY="test-secret-key-for-selfhealing-tests",
            # Default auto field
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        )

        django.setup()

        # Create tables immediately after setup
        from django.core.management import call_command

        call_command("migrate", "--run-syncdb", verbosity=0)


@pytest.fixture(autouse=True)
def reset_database(request):
    """Reset database between tests by using transactions.
    
    Only runs for tests that use the database (have django_db marker).
    Serializer tests don't need this.
    """
    # Check if this test needs database access
    marker = request.node.get_closest_marker("django_db")
    if marker is None:
        # No django_db marker - skip database cleanup
        yield
        return

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    # Start transaction
    yield

    # Rollback all changes - clear all data from tables
    try:
        from selfhealing.adapters.django.models import (
            FailedOperation,
            CircuitBreakerState,
            SecurityIncident,
        )

        FailedOperation.objects.all().delete()
        CircuitBreakerState.objects.all().delete()
        SecurityIncident.objects.all().delete()
    except Exception:
        # If database is not available, skip cleanup
        pass
