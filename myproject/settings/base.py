"""
Django Base Settings - 모든 환경에서 공통으로 사용되는 설정
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ==========================================================================
# Core Settings
# ==========================================================================

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")

if not SECRET_KEY:
    raise ValueError(
        "❌ DJANGO_SECRET_KEY 환경변수가 설정되지 않았습니다!\n"
        ".env 파일을 생성하고 SECRET_KEY를 설정하세요.\n"
        "생성 방법: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'"
    )

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,web,*").split(",")

# CSRF 설정
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://web:8000",
]

# ==========================================================================
# Application definition
# ==========================================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "shopping",
    # NOTE: selfhealing.adapters.django removed in v2.0.0
    # Redis-based adapters are now the default (no Django app needed)
    # See docs/self_healing/middleware_system/06_REDIS_MIGRATION.md
    # Third party apps
    "mptt",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "drf_spectacular_sidecar",  # Swagger UI 정적 파일 (CORB 문제 해결)
    "django_celery_beat",
    # Social login (allauth)
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.kakao",
    "allauth.socialaccount.providers.naver",
    # REST API social auth
    "rest_framework.authtoken",
    "dj_rest_auth",
    "dj_rest_auth.registration",
]

AUTH_USER_MODEL = "shopping.User"

MIDDLEWARE = [
    # ==========================================================================
    # [1] Trace ID Middleware (최상단 - 분산 추적의 시작점)
    # ==========================================================================
    # 모든 요청에 trace_id 부여, X-Request-ID 헤더 전파
    # Reference: load_tests/scenarios/integration/stage08_observability.py
    "selfhealing.audit.trace.trace_id_middleware",
    
    # ==========================================================================
    # [2] Health Bridge (DB-independent, Worker Saturation 방지)
    # ==========================================================================
    # DB 죽어도 /health/l3 즉시 응답 - Kubernetes Probe 필수
    # Reference: Stage 50 Observability
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    
    # ==========================================================================
    # [3] Tiering Middleware (Emergency Mode Load Shedding)
    # ==========================================================================
    # 비상 모드 시 Tier별 트래픽 제어 (critical/standard/non_essential)
    # 비활성화: SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
    "selfhealing.api.django.tiering.TieringMiddleware",
    
    # ==========================================================================
    # [4] Self-Healing Middleware (Circuit Breaker + DLQ)
    # ==========================================================================
    # DB 오류/502 감지 → CircuitBreaker 기록 + DLQ 자동 적재
    # Reference: Stage 16 Healing Proof
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    
    # ==========================================================================
    # [5] Actor Context Middleware (사용자 추적)
    # ==========================================================================
    # 모든 요청에서 "누가" 수행하는지 자동 추적 (Audit 연동)
    # 비활성화: SELFHEALING_ACTOR_MIDDLEWARE_ENABLED = False
    "myproject.middleware.actor_middleware.ActorContextMiddleware",
    
    # ==========================================================================
    # [6] Django Core Middlewares
    # ==========================================================================
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    
    # ==========================================================================
    # [7] Self-Healing Rate Limit (Hybrid: Redis + Local Memory Fallback)
    # ==========================================================================
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    
    # ==========================================================================
    # [8] Pool Circuit Breaker Middleware (DB Connection Pool 보호)
    # ==========================================================================
    # Pool 고갈 시 즉시 503 반환 (Fail Fast)
    # 비활성화: SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED = False
    # Reference: load_tests/scenarios/chaos/stage26_connection_pool.py
    "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware",
    
    # ==========================================================================
    # [9] Pool Timeout Middleware (SQLAlchemy Timeout 처리)
    # ==========================================================================
    # Pool Timeout 발생 시 503 반환
    # 비활성화: SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED = False
    "myproject.middleware.pool_timeout_middleware.PoolTimeoutMiddleware",
    
    # ==========================================================================
    # [10] Chaos Middleware (HELLMODE 테스트용)
    # ==========================================================================
    # X-DB-Lock-Timeout, X-DB-Statement-Timeout 헤더 처리
    # 비활성화: CHAOS_MIDDLEWARE_ENABLED = False
    "myproject.middleware.chaos_middleware.ChaosMiddleware",
    
    # Connection Pool 제한 (HELLMODE 시 5개로 제한)
    "myproject.middleware.chaos_middleware.ConnectionPoolLimiterMiddleware",
    
    # ==========================================================================
    # [11] Audit Middleware (가장 마지막 - 모든 이벤트 수집)
    # ==========================================================================
    # 모든 미들웨어 이벤트를 단일 해시 체인으로 기록
    # 비활성화: SELFHEALING_AUDIT_MIDDLEWARE_ENABLED = False
    # CRITICAL: 반드시 마지막 위치!
    "selfhealing.api.django.audit_middleware.AuditMiddleware",
]

# ==========================================================================
# Middleware Toggle Settings (활성화/비활성화 설정)
# ==========================================================================
# 각 미들웨어의 활성화 여부를 환경변수 또는 여기서 설정

# Tiering Middleware (Emergency Mode Load Shedding)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = os.environ.get(
    "SELFHEALING_TIERING_MIDDLEWARE_ENABLED", "True"
).lower() in ("true", "1", "yes")

# Actor Context Middleware (사용자 추적)
SELFHEALING_ACTOR_MIDDLEWARE_ENABLED = os.environ.get(
    "SELFHEALING_ACTOR_MIDDLEWARE_ENABLED", "True"
).lower() in ("true", "1", "yes")

# Pool Circuit Breaker Middleware
SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED = os.environ.get(
    "SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED", "True"
).lower() in ("true", "1", "yes")

# Pool Timeout Middleware
SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED = os.environ.get(
    "SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED", "True"
).lower() in ("true", "1", "yes")

# Chaos Middleware (테스트 환경에서만 True 권장)
CHAOS_MIDDLEWARE_ENABLED = os.environ.get(
    "CHAOS_MIDDLEWARE_ENABLED", "False"
).lower() in ("true", "1", "yes")

# Audit Middleware
SELFHEALING_AUDIT_MIDDLEWARE_ENABLED = os.environ.get(
    "SELFHEALING_AUDIT_MIDDLEWARE_ENABLED", "True"
).lower() in ("true", "1", "yes")

ROOT_URLCONF = "myproject.urls"

TEMPLATES = [
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
]

WSGI_APPLICATION = "myproject.wsgi.application"

# ==========================================================================
# Password validation
# ==========================================================================

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Password Reset 설정
PASSWORD_RESET_TIMEOUT = int(os.environ.get("PASSWORD_RESET_TIMEOUT", 86400))

# ==========================================================================
# Internationalization
# ==========================================================================

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# ==========================================================================
# Static & Media files
# ==========================================================================

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==========================================================================
# Encryption Settings
# ==========================================================================

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# ==========================================================================
# Return Request Settings
# ==========================================================================

RETURN_REQUEST_DEADLINE_DAYS = int(os.environ.get("RETURN_REQUEST_DEADLINE_DAYS", 7))

# ==========================================================================
# REST Framework
# ==========================================================================

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Pool Timeout 시 503 반환
    "EXCEPTION_HANDLER": "myproject.exception_handlers.custom_exception_handler",
}

# drf-spectacular 설정
SPECTACULAR_SETTINGS = {
    "TITLE": "Django 쇼핑몰 API",
    "DESCRIPTION": "쇼핑몰 프로젝트 API 문서",
    "VERSION": "1.0.0",
    # Swagger UI 정적 파일 → sidecar 패키지 사용 (CORB 문제 해결)
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": True,
    },
    "SERVE_INCLUDE_SCHEMA": False,
    # 태그 이름 정리 (소문자 → 대문자, 한글 통일)
    "TAGS": [
        {"name": "Auth", "description": "인증 관련 API (회원가입, 로그인, 토큰)"},
        {"name": "Social Auth", "description": "소셜 로그인 API"},
        {"name": "Users", "description": "사용자 관리 API"},
        {"name": "Products", "description": "상품 관련 API"},
        {"name": "Categories", "description": "카테고리 API"},
        {"name": "Orders", "description": "주문 관련 API"},
        {"name": "Cart", "description": "장바구니 API"},
        {"name": "Wishlist", "description": "찜하기 API"},
        {"name": "Payments", "description": "결제 관련 API"},
        {"name": "Points", "description": "포인트 API"},
        {"name": "Returns - 고객", "description": "교환/환불 API (고객용)"},
        {"name": "Returns - 판매자", "description": "교환/환불 API (판매자용)"},
        {"name": "Notifications", "description": "알림 API"},
        {"name": "Product Q&A", "description": "상품 문의 API"},
        {"name": "Webhooks", "description": "웹훅 API"},
    ],
    # 태그 변환 함수
    "PREPROCESSING_HOOKS": ["shopping.utils.spectacular_hooks.preprocess_exclude_endpoints"],
    "POSTPROCESSING_HOOKS": ["shopping.utils.spectacular_hooks.postprocess_tags"],
}


# ==========================================================================
# JWT Settings
# ==========================================================================

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "TOKEN_USER_CLASS": "rest_framework_simplejwt.models.TokenUser",
    "JTI_CLAIM": "jti",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "USER_AUTHENTICATION_RULE": "rest_framework_simplejwt.authentication.default_user_authentication_rule",
}

# ==========================================================================
# Celery Settings
# ==========================================================================

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = False

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ==========================================================================
# Email Settings
# ==========================================================================

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)

if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "TRUE") == "TRUE"
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@shopping.com")

# Frontend URL (이메일 링크, 결제 리다이렉트 등)
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# ==========================================================================
# Self-Healing Core Domains (Domain Bootstrap)
# ==========================================================================
# 핵심 도메인을 미리 등록하여 테스트 시작 시 unknown 상태 방지
# 시스템 시작 시 AppConfig.ready()에서 자동 초기화됨
# Reference: Stage 5 Rollback Healing Test Review

SELFHEALING_CORE_DOMAINS = [
    "payment",  # 결제 서비스 - 최우선 감시 대상
    "inventory",  # 재고 서비스 - 롤백 검증 핵심
    "order",  # 주문 서비스
    "product",  # 상품 서비스
    "database",  # 데이터베이스
    "cart",  # 장바구니 서비스
    "auth",  # 인증 서비스
]

# ==========================================================================
# Self-Healing Middleware Path Patterns (Domain-Free Configuration)
# ==========================================================================
# SelfHealingMiddleware가 감시할 경로 패턴을 여기서 설정합니다.
# 이 설정을 변경하면 인프라 코드 수정 없이 도메인 추가/제거 가능합니다.

# DLQ 적재 대상 경로 (POST, PUT, PATCH 요청만 해당)
SELF_HEALING_DLQ_ELIGIBLE_PATHS = [
    r"^/api/orders/",
    r"^/api/payments/",
    r"^/api/cart/",
    r"^/api/checkout/",
    r"^/api/points/",
    r"^/api/webhooks/",
]

# 인프라 장애로 인식할 경로 (503 응답 시 CB 실패로 기록)
SELF_HEALING_INFRA_FAILURE_PATHS = [
    r"^/api/orders/",
    r"^/api/payments/",
    r"^/api/webhooks/",
]

# 도메인 추론 매핑 (경로 패턴 -> 도메인 이름)
SELF_HEALING_DOMAIN_MAPPING = {
    "/payments/": "payment",
    "/checkout/": "payment",
    "/orders/": "order",
    "/points/": "point",
    "/cart/": "cart",
    "/webhooks/": "webhook",
}

# ==========================================================================
# Component imports (Social Auth, Payment, etc.)
# ==========================================================================

from myproject.settings.components.social_auth import *  # noqa: F401, F403, E402
from myproject.settings.components.payment import *  # noqa: F401, F403, E402
