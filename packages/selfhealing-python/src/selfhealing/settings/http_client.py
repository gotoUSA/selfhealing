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
        env_file=None,
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


def get_http_client_settings() -> "HttpClientSettings":
    from selfhealing.settings.root import get_config

    return get_config().adapters.http_client

def reset_http_client_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().adapters.__dict__["http_client"]
    except KeyError:
        pass
