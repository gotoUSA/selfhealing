"""
Django Local Development Settings
로컬 개발 환경 전용 설정
"""

import os
import socket

from myproject.settings.base import *  # noqa: F401, F403
from myproject.settings.components.logging import get_logging_config

# ==========================================================================
# Debug Settings
# ==========================================================================

DEBUG = True

# ==========================================================================
# Debug Toolbar
# ==========================================================================

INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405

MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")  # noqa: F405

# ==========================================================================
# Pool Timeout Middleware (Connection Pool 고갈 시 503 반환)
# SQLAlchemy Pool Timeout 발생 시 즉시 503 반환
# ==========================================================================

USE_POOL_CIRCUIT_BREAKER = os.getenv("USE_POOL_CIRCUIT_BREAKER", "FALSE") == "TRUE"

if USE_POOL_CIRCUIT_BREAKER and SELFHEALING_AVAILABLE:  # noqa: F405
    # v6.2.0: PoolCircuitBreakerMiddleware 블로킹 이슈 해결됨!
    # - 캐시 기반 Pool 상태 조회 (백그라운드 스레드에서 100ms 간격 갱신)
    # - 매 요청에서 Non-Blocking 으로 Pool 상태 확인
    # SelfHealingMiddleware 바로 다음 위치에 배치 (순서 중요)
    MIDDLEWARE.insert(2, "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware")  # noqa: F405
    # PoolTimeoutMiddleware는 폴백으로 유지 (Pool CB가 놓친 예외 처리)
    MIDDLEWARE.insert(3, "myproject.middleware.pool_timeout_middleware.PoolTimeoutMiddleware")  # noqa: F405

INTERNAL_IPS = [
    "127.0.0.1",
    "localhost",
]

# Docker 환경에서도 작동하도록
hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
INTERNAL_IPS += [ip[: ip.rfind(".")] + ".1" for ip in ips]

# ==========================================================================
# Database (PostgreSQL with Connection Pool)
# ==========================================================================

# Connection Pool 설정 (환경변수로 제어 가능)
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
USE_CONNECTION_POOL = os.getenv("USE_CONNECTION_POOL", "FALSE") == "TRUE"

if USE_CONNECTION_POOL:
    # SQLAlchemy 기반 Connection Pool 사용
    DATABASES = {
        "default": {
            "ENGINE": "dj_db_conn_pool.backends.postgresql",
            "NAME": os.getenv("DATABASE_NAME", "myproject_dev"),
            "USER": os.getenv("DATABASE_USER", "postgres"),
            "PASSWORD": os.getenv("DATABASE_PASSWORD", "postgres"),
            "HOST": os.getenv("DATABASE_HOST", "localhost"),
            "PORT": os.getenv("DATABASE_PORT", "5432"),
            "POOL_OPTIONS": {
                "POOL_SIZE": DB_POOL_SIZE,
                "MAX_OVERFLOW": DB_MAX_OVERFLOW,
                "RECYCLE": DB_POOL_RECYCLE,
                "TIMEOUT": DB_POOL_TIMEOUT,  # 연결 대기 timeout (소문자 사용!)
                "PRE_PING": True,  # 연결 상태 확인
                "ECHO": True,  # SQL 로깅 (디버그용)
            },
            "OPTIONS": {
                "connect_timeout": 10,
            },
        }
    }
else:
    # 기본 Django 연결 (Pool 미사용)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DATABASE_NAME", "myproject_dev"),
            "USER": os.getenv("DATABASE_USER", "postgres"),
            "PASSWORD": os.getenv("DATABASE_PASSWORD", "postgres"),
            "HOST": os.getenv("DATABASE_HOST", "localhost"),
            "PORT": os.getenv("DATABASE_PORT", "5432"),
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000",
            },
        }
    }

# ==========================================================================
# Cache (Redis)
# ==========================================================================

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

# ==========================================================================
# Session Backend (Redis)
# ==========================================================================
# DB 세션 → Redis 세션으로 전환
# ⚠️ docker-compose.yml의 redis는 persist 미설정 → 재시작 시 세션 유실
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# ==========================================================================
# Rate Limiting - 개발 환경에서는 실제 제한 적용
# ==========================================================================

DISABLE_RATE_LIMITING = os.getenv("DISABLE_RATE_LIMITING", "FALSE") == "TRUE"

if DISABLE_RATE_LIMITING:
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
else:
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
# Logging (Debug mode)
# ==========================================================================

LOGGING = get_logging_config(debug=True)

# ==========================================================================
# Encryption Key Warning (개발 환경에서만)
# ==========================================================================

if not ENCRYPTION_KEY:  # noqa: F405
    import warnings

    warnings.warn(
        "⚠️ ENCRYPTION_KEY가 설정되지 않았습니다. "
        "계좌번호 암호화 기능이 작동하지 않습니다. "
        ".env 파일에 ENCRYPTION_KEY를 추가해주세요."
    )

# ==========================================================================
# Self-Healing Configuration (Local Development)
# Development-friendly settings with shorter timeouts for faster iteration.
# These values are used by shopping/services/self_healing/config.py
# ==========================================================================

SELF_HEALING = {
    # SLA (Service Level Agreement) - Shorter for dev to see failures quickly
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
    },
    # Retry Policy - Fewer retries and shorter delays for dev
    "RETRY": {
        "MAX_RETRIES": 3,  # Fewer retries for faster dev feedback
        "BACKOFF_BASE": 2,
        "BACKOFF_MAX": 60,  # Shorter max delay in dev: 1 minute
        "JITTER_PERCENT": 0.25,
    },
    # Circuit Breaker - Shorter timeout for faster dev cycles
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "SUCCESS_THRESHOLD": 3,
        "RECOVERY_TIMEOUT": 30,  # Shorter timeout in dev: 30 seconds
    },
    # Dead Letter Queue - Same as production
    "DLQ": {
        "AUTO_REPLAY_ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 30,  # Shorter delay in dev
    },
    # Idempotency - Shorter TTLs for dev testing
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,
        "EXTENDED_CACHE_TTL": 300,  # For operations requiring longer TTL
        "SHORT_CACHE_TTL": 60,  # For short-lived operations
    },
}
