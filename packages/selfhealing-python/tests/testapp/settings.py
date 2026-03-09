"""Minimal Django settings for selfhealing integration tests.

Usage:
    pytest tests/ --ds=tests.testapp.settings
    DJANGO_SETTINGS_MODULE=tests.testapp.settings pytest tests/
"""

SECRET_KEY = "test-secret-key-for-selfhealing"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "selfhealing.adapters.django",
    "tests.testapp",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "testapp.TestUser"

ROOT_URLCONF = "tests.testapp.urls"

MIDDLEWARE = [
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# selfhealing minimal settings
SELFHEALING_CORE_DOMAINS = ["payment", "order", "test"]
SELFHEALING_AUTO_MIDDLEWARE = False
