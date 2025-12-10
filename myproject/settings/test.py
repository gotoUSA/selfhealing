"""
Django Test Settings
테스트 환경 전용 설정 (pytest, Django test)
"""

import os

from myproject.settings.base import *  # noqa: F401, F403
from myproject.settings.components.logging import get_logging_config

# ==========================================================================
# Test Mode Flag
# ==========================================================================

TESTING = True
DEBUG = True

# ==========================================================================
# Database (PostgreSQL - Test with optimized connection settings)
# ==========================================================================


# Docker 컨테이너 내부 감지: /.dockerenv 파일이 존재하거나 DATABASE_HOST가 'db'인 경우
def _is_running_in_docker():
    """Docker 컨테이너 내부에서 실행 중인지 확인"""
    return os.path.exists("/.dockerenv") or os.getenv("DATABASE_HOST") == "db"


# Docker 내부에서는 'db' 호스트 사용, 외부에서는 'localhost' 사용
_db_host = "db" if _is_running_in_docker() else "localhost"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("TEST_DATABASE_NAME", "shopping_db"),
        "USER": os.getenv("TEST_DATABASE_USER", "shopping_user"),
        "PASSWORD": os.getenv("TEST_DATABASE_PASSWORD", "shopping_pass"),
        "HOST": os.getenv("TEST_DATABASE_HOST", _db_host),
        "PORT": os.getenv("TEST_DATABASE_PORT", "5432"),
        # 테스트에서는 연결 즉시 닫기 (동시성 테스트에서 "too many clients" 방지)
        "CONN_MAX_AGE": 0,
        # Health checks 비활성화하여 연결 절약
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000",
            "client_encoding": "UTF8",  # Windows 인코딩 문제 해결
        },
        # 테스트 DB 이름 설정
        "TEST": {
            "NAME": "test_shopping_db",
        },
    }
}

# ==========================================================================
# Cache (Dummy - 테스트에서는 캐시 비활성화)
# ==========================================================================

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

# ==========================================================================
# Celery (동기 실행 - 테스트에서는 즉시 실행)
# ==========================================================================

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# ==========================================================================
# Rate Limiting - 테스트에서는 비활성화
# ==========================================================================

REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "login": "1000/min",
    "register": "1000/hour",
    "token_refresh": "1000/min",
    "password_reset": "1000/hour",
    "email_verification": "1000/min",
    "email_verification_resend": "1000/hour",
    "payment_request": "1000/min",
    "payment_confirm": "1000/min",
    "payment_cancel": "1000/min",
    "order_create": "1000/min",
    "order_cancel": "1000/min",
    "anon_global": "10000/hour",
    "user_global": "10000/hour",
    "webhook": "1000/min",
}

# ==========================================================================
# Logging (Quiet mode for tests)
# ==========================================================================

LOGGING = get_logging_config(debug=False)

# ==========================================================================
# Password Hashing (빠른 해싱 - 테스트 속도 향상)
# ==========================================================================

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ==========================================================================
# Email (콘솔 출력)
# ==========================================================================

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# ==========================================================================
# Self-Healing Configuration (Test Environment)
# Test-optimized settings with minimal delays for fast test execution.
# WARNING: CELERY_TASK_ALWAYS_EAGER=True means tasks run synchronously,
# which differs from production async behavior. See test_celery_async_mode.py
# for tests that simulate production async behavior.
# ==========================================================================

SELF_HEALING = {
    # SLA - Same thresholds, tests use freezegun for time manipulation
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
    },
    # Retry Policy - Minimal delays for fast tests
    "RETRY": {
        "MAX_RETRIES": 3,
        "BACKOFF_BASE": 2,
        "BACKOFF_MAX": 10,        # Very short for test speed
        "JITTER_PERCENT": 0.0,    # No jitter for deterministic tests
    },
    # Circuit Breaker - Enabled but with short timeout
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "SUCCESS_THRESHOLD": 3,
        "RECOVERY_TIMEOUT": 5,    # Very short for fast tests
    },
    # Dead Letter Queue - Minimal delays
    "DLQ": {
        "AUTO_REPLAY_ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 1,  # Near-instant for tests
    },
    # Idempotency - Short TTLs for test isolation
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 10,
        "PAYMENT_CACHE_TTL": 30,
        "WEBHOOK_CACHE_TTL": 20,
    },
}
