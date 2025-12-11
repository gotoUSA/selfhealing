"""
Logging Configuration
로깅 관련 모든 설정을 관리합니다.
"""

from pathlib import Path

# BASE_DIR은 base.py에서 import
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def get_logging_config(debug: bool = False) -> dict:
    """
    환경에 맞는 로깅 설정을 반환합니다.

    Args:
        debug: DEBUG 모드 여부
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
                "style": "{",
            },
            "simple": {
                "format": "{levelname} {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "level": "DEBUG" if debug else "INFO",
                "class": "logging.StreamHandler",
                "formatter": "simple",
            },
            "file": {
                "level": "INFO",
                "class": "logging.FileHandler",
                "filename": BASE_DIR / "logs" / "payment.log",
                "formatter": "verbose",
            },
        },
        "loggers": {
            # Django request 로거 (400/500 에러 자동 로깅)
            "django.request": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
            "shopping.views.payment_views": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
            "shopping.views.order_views": {
                "handlers": ["console"],
                "level": "DEBUG" if debug else "INFO",
                "propagate": False,
            },
            "shopping.views.cart_views": {
                "handlers": ["console"],
                "level": "DEBUG" if debug else "INFO",
                "propagate": False,
            },
            "shopping.webhooks": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
            "celery": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
            "shopping.services": {
                "handlers": ["console"],
                "level": "DEBUG" if debug else "INFO",
                "propagate": False,
            },
            "shopping.tasks": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
            # Self-healing 패키지 로깅 (Pool Circuit Breaker 등)
            "selfhealing": {
                "handlers": ["console"],
                "level": "DEBUG" if debug else "INFO",
                "propagate": False,
            },
            "selfhealing.api.django.pool_circuit_breaker": {
                "handlers": ["console"],
                "level": "DEBUG",  # 항상 DEBUG로 상세 로그 출력
                "propagate": False,
            },
        },
    }


# logs 디렉토리 생성
LOGS_DIR = BASE_DIR / "logs"
if not LOGS_DIR.exists():
    LOGS_DIR.mkdir(exist_ok=True)
