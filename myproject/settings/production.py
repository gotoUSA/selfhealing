"""
Django Production Settings
프로덕션 환경 전용 설정

⚠️ 주의: 이 설정을 사용하기 전에 반드시 다음 환경변수를 설정하세요:
- DJANGO_SECRET_KEY
- DATABASE_* (PostgreSQL 연결 정보)
- REDIS_URL
- ENCRYPTION_KEY
"""

import os

from myproject.settings.base import *  # noqa: F401, F403
from myproject.settings.components.logging import get_logging_config

# ==========================================================================
# Security Settings
# ==========================================================================

DEBUG = False

# HTTPS 관련 보안 설정
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "TRUE") == "TRUE"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS (HTTP Strict Transport Security)
SECURE_HSTS_SECONDS = 31536000  # 1년
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# 기타 보안 헤더
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

# CSRF 추가 설정
CSRF_TRUSTED_ORIGINS = os.environ.get("CSRF_TRUSTED_ORIGINS", "https://yourdomain.com").split(",")

# ==========================================================================
# Database (PostgreSQL - Production)
# ==========================================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DATABASE_NAME"),
        "USER": os.getenv("DATABASE_USER"),
        "PASSWORD": os.getenv("DATABASE_PASSWORD"),
        "HOST": os.getenv("DATABASE_HOST"),
        "PORT": os.getenv("DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 120,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000",
        },
    }
}

# ==========================================================================
# Cache (Redis - Production)
# ==========================================================================

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            "RETRY_ON_TIMEOUT": True,
        },
    }
}

# ==========================================================================
# Rate Limiting - 프로덕션 제한
# ==========================================================================

REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = [  # noqa: F405
    "shopping.throttles.GlobalAnonRateThrottle",
    "shopping.throttles.GlobalUserRateThrottle",
]

REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "login": "3/min",
    "register": "3/hour",
    "token_refresh": "10/min",
    "password_reset": "3/hour",
    "email_verification": "1/min",
    "email_verification_resend": "3/hour",
    "payment_request": "10/min",
    "payment_confirm": "5/min",
    "payment_cancel": "5/min",
    "order_create": "10/min",
    "order_cancel": "5/min",
    "anon_global": "100/hour",
    "user_global": "1000/hour",
    "webhook": "100/min",
}

# ==========================================================================
# Logging (Production mode - less verbose)
# ==========================================================================

LOGGING = get_logging_config(debug=False)

# ==========================================================================
# REST Auth - Production 보안 설정
# ==========================================================================

REST_AUTH["JWT_AUTH_SECURE"] = True  # noqa: F405

# ==========================================================================
# Static files (Production)
# ==========================================================================

# WhiteNoise for static files (선택사항)
# MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ==========================================================================
# Encryption Key 필수 체크
# ==========================================================================

if not ENCRYPTION_KEY:  # noqa: F405
    raise ValueError("❌ ENCRYPTION_KEY가 설정되지 않았습니다! " "프로덕션 환경에서는 암호화 키가 필수입니다.")

# ==========================================================================
# Self-Healing Configuration (Production)
# Defines recovery SLA, retry policies, circuit breaker and DLQ settings.
# These values are used by shopping/services/self_healing/config.py
# ==========================================================================

SELF_HEALING = {
    # SLA (Service Level Agreement) - Maximum hours before escalation
    # Each operation type has different urgency levels
    "SLA": {
        "PAYMENT_HOURS": 1,  # Critical: Payment failures need fast resolution
        "POINT_HOURS": 4,  # Medium: Points can wait a bit longer
        "INVENTORY_HOURS": 2,  # High: Inventory sync is important for orders
        "WEBHOOK_HOURS": 8,  # Low: Webhooks can be retried later
        "NOTIFICATION_HOURS": 24,  # Lowest: Notifications are not critical
    },
    # Retry Policy - Exponential backoff with jitter
    # Production uses more retries with longer max delay
    "RETRY": {
        "MAX_RETRIES": 5,  # More retries in production than dev
        "BACKOFF_BASE": 2,  # Exponential base: delay = base^attempt
        "BACKOFF_MAX": 300,  # Maximum delay cap: 5 minutes
        "JITTER_PERCENT": 0.25,  # Random jitter to prevent thundering herd
    },
    # Circuit Breaker - Prevents cascading failures
    # Opens circuit after threshold failures, allows test requests after timeout
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,  # Number of failures before opening circuit
        "SUCCESS_THRESHOLD": 3,  # Successes needed to close from half-open
        "RECOVERY_TIMEOUT": 60,  # Seconds before trying half-open (longer in prod)
    },
    # Dead Letter Queue - Failed operation storage and replay
    # Auto-replay attempts to recover failed operations automatically
    "DLQ": {
        "AUTO_REPLAY_ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 60,  # Delay between replay attempts
    },
    # Idempotency - Prevents duplicate operations
    # Longer TTLs in production to handle delayed retries
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,  # Default: 1 minute
        "EXTENDED_CACHE_TTL": 600,  # For critical operations requiring longer TTL
        "SHORT_CACHE_TTL": 120,  # For short-lived operations
    },
}
