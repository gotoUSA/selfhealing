"""
API View Settings - Pydantic v2.

API 페이징 및 필터링 기본 설정입니다.

Replaces:
- api/django/views 내 default_limit, default_offset, max_limit

Environment Variables:
    SELFHEALING_API_VIEW_DEFAULT_LIMIT=100
    SELFHEALING_API_VIEW_DEFAULT_OFFSET=0
    SELFHEALING_API_VIEW_MAX_LIMIT=1000

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [22])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §3.6
"""

import logging
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ApiViewSettings(BaseSettings):
    """
    API View 페이징 및 필터링 설정.

    페이징:
    - default_limit: 기본 페이지 크기 (100)
    - default_offset: 기본 시작 위치 (0)
    - max_limit: 최대 페이지 크기 (1000)

    정렬:
    - default_order: 기본 정렬 순서 ("-created_at")

    기타:
    - max_events: XTest 최대 이벤트 수 (500)
    - max_incidents: XTest 최대 인시던트 수 (100)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_API_VIEW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Pagination - from api/django/views
    # ==========================================================================
    default_limit: int = Field(
        default=100,
        ge=10,
        le=500,
        description="기본 페이지 크기",
    )

    default_offset: int = Field(
        default=0,
        ge=0,
        description="기본 시작 위치",
    )

    max_limit: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="최대 페이지 크기",
    )

    # ==========================================================================
    # Ordering - from api/django/views
    # ==========================================================================
    default_order: str = Field(
        default="-created_at",
        description="기본 정렬 순서 (- 접두사는 역순)",
    )

    # ==========================================================================
    # XTest Views - from xtest/base.py
    # ==========================================================================
    max_events: int = Field(
        default=500,
        ge=100,
        le=5000,
        description="XTest 최대 이벤트 수",
    )

    max_incidents: int = Field(
        default=100,
        ge=50,
        le=1000,
        description="XTest 최대 인시던트 수",
    )

    max_injection: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="XTest 최대 주입 수",
    )

    # ==========================================================================
    # Throttle Adapter - from throttle_adapter.py
    # ==========================================================================
    throttle_max_limit: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="스로틀 어댑터 최대 리밋",
    )

    # ==========================================================================
    # Auto-Tuning Views - from views/auto_tuning.py (Phase 3 리팩토링)
    # ==========================================================================
    auto_tuning_export_limit: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Auto-Tuning CSV 내보내기 최대 레코드 수",
    )

    auto_tuning_default_page_size: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Auto-Tuning 히스토리 기본 페이지 크기",
    )

    # ==========================================================================
    # XTest Observability Views - from views/xtest/observability.py (Phase 3 리팩토링)
    # ==========================================================================
    xtest_timeline_default_limit: int = Field(
        default=50,
        ge=10,
        le=500,
        description="XTest 타임라인 조회 기본 limit",
    )

    xtest_postmortem_history_limit: int = Field(
        default=100,
        ge=50,
        le=500,
        description="Postmortem 생성 시 히스토리 조회 limit",
    )

    xtest_incidents_default_limit: int = Field(
        default=10,
        ge=5,
        le=100,
        description="XTest 인시던트 목록 조회 기본 limit",
    )

    @model_validator(mode="after")
    def validate_limits(self) -> "ApiViewSettings":
        """default_limit이 max_limit보다 작은지 검증."""
        if self.default_limit > self.max_limit:
            raise ValueError(
                f"default_limit ({self.default_limit}) must be less than or equal to "
                f"max_limit ({self.max_limit})"
            )
        return self


# ==========================================================================
# Singleton 관리
# ==========================================================================
_api_view_settings: Optional[ApiViewSettings] = None


def get_api_view_settings() -> ApiViewSettings:
    """Get cached ApiViewSettings instance."""
    global _api_view_settings
    if _api_view_settings is None:
        _api_view_settings = ApiViewSettings()
    return _api_view_settings


def reset_api_view_settings() -> None:
    """Reset cached settings (for testing)."""
    global _api_view_settings
    _api_view_settings = None
