"""
HTTP Client Settings - Pydantic v2.

Self-Healing HTTP 클라이언트 설정.
외부 API 호출 타임아웃 등의 기본값을 정의합니다.

Source:
- services/http_client.py

Environment Variables:
    SELFHEALING_HTTP_CLIENT_DEFAULT_TIMEOUT=30.0
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class HttpClientSettings(BaseSettings):
    """
    HTTP 클라이언트 설정.

    외부 API 호출 시 사용되는 기본 타임아웃 값을 정의합니다.
    환경에 따라 타임아웃을 조정할 수 있습니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_HTTP_CLIENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # HTTP Request Timeout (from services/http_client.py line 42)
    # ==========================================================================
    default_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="외부 API 호출 기본 타임아웃 (초)",
    )

    webhook_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Timeout for outbound webhook HTTP calls (notifier, etc.)",
    )


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: HttpClientSettings | None = None


def get_http_client_settings() -> HttpClientSettings:
    """
    캐시된 HttpClientSettings 인스턴스 반환.

    Returns:
        HttpClientSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = HttpClientSettings()
    return _settings


def reset_http_client_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
