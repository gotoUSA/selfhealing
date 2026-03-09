# Minimal selfhealing Django settings example
#
# Copy the selfhealing-specific sections below into your project's settings.py.
# Adjust domain names, paths, and task mappings to match your application.

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # selfhealing — AppConfig.ready() handles internal initialization
    # (hash chain sync, orphan services, session signals, etc.)
    # MIDDLEWARE/REST_FRAMEWORK auto-configuration is NOT performed here.
    "selfhealing.adapters.django",
    # Your app
    "myapp",
]

# Business domain list (required)
SELFHEALING_CORE_DOMAINS = ["payment", "order", "inventory"]

# DLQ-eligible request paths (optional)
SELF_HEALING_DLQ_ELIGIBLE_PATHS = [
    r"^/api/orders/",
    r"^/api/payments/",
]

# Domain inference mapping (optional)
SELF_HEALING_DOMAIN_MAPPING = {
    "/payments/": "payment",
    "/orders/": "order",
}

# Celery signal domain mapping (optional)
SELFHEALING_TASK_DOMAIN_MAPPING = {
    "myapp.tasks.process_payment": "payment",
    "myapp.tasks.process_order": "order",
}

# Disable specific features (optional, all default to True)
# SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
# SELFHEALING_AUDIT_MIDDLEWARE_ENABLED = False

# ─── Must be called at the very end of settings.py ───
# Explicit wrapper pattern (per doc 320):
# Configures MIDDLEWARE, REST_FRAMEWORK, and optional OTEL initialization.
from selfhealing.adapters.django import configure_selfhealing

configure_selfhealing(namespace=globals())
# For Gunicorn environments, defer OTEL to post_worker_init:
# configure_selfhealing(namespace=globals(), disable_auto_otel=True)
