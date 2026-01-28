"""
Post-mortem Settings - Pydantic v2.

Post-mortem 리포트 생성 및 자동 트리거 관련 설정입니다.

X-Test 모듈에서 분리된 독립적인 설정으로, 프로덕션 환경에서도 사용됩니다.

Environment Variables:
    SELFHEALING_POSTMORTEM_HISTORY_LIMIT=100
    SELFHEALING_POSTMORTEM_AUTO_ENABLED=false
    SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION=30

Reference:
- docs/self_healing/middleware_system/134_POSTMORTEM_NEW_MODULE_STRUCTURE.md
"""

from __future__ import annotations

import logging
import os
import warnings

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class PostmortemSettings(BaseSettings):
    """
    Post-mortem 리포트 생성 및 자동 트리거 설정.

    히스토리 조회:
    - history_limit: Post-mortem 생성 시 조회할 이벤트 수 (100)

    자동 생성:
    - auto_enabled: CB CLOSED 시 자동 Post-mortem 생성 (False)
    - auto_min_duration: 자동 생성 최소 인시던트 지속 시간 (30초)

    알림:
    - notification_enabled: Post-mortem 생성 시 알림 발송 (True)
    - notification_min_duration: 알림 발송 최소 인시던트 지속 시간 (60초)

    인시던트 목록:
    - incidents_default_limit: 인시던트 목록 조회 기본 limit (10)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_POSTMORTEM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # History Limit - Post-mortem 생성 시 이벤트 조회 개수
    # ==========================================================================
    history_limit: int = Field(
        default=100,
        ge=50,
        le=500,
        description="Post-mortem 생성 시 조회할 이벤트 수",
    )

    # ==========================================================================
    # Auto Generation - CB CLOSED 시 자동 Post-mortem 생성
    # ==========================================================================
    auto_enabled: bool = Field(
        default=False,
        description="CB CLOSED 시 자동 Post-mortem 생성 활성화",
    )

    auto_min_duration: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="자동 Post-mortem 생성 최소 인시던트 지속 시간 (초)",
    )

    # ==========================================================================
    # Notification - Post-mortem 생성 시 알림 발송
    # ==========================================================================
    notification_enabled: bool = Field(
        default=True,
        description="Post-mortem 생성 시 알림 발송 활성화",
    )

    notification_min_duration: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="Post-mortem 알림 발송 최소 인시던트 지속 시간 (초)",
    )

    # ==========================================================================
    # Incidents List - 인시던트 목록 조회
    # ==========================================================================
    incidents_default_limit: int = Field(
        default=10,
        ge=5,
        le=100,
        description="인시던트 목록 조회 기본 limit",
    )


# ==========================================================================
# Singleton 관리
# ==========================================================================
_postmortem_settings: PostmortemSettings | None = None


def get_postmortem_settings() -> PostmortemSettings:
    """Get cached PostmortemSettings instance."""
    global _postmortem_settings
    if _postmortem_settings is None:
        _postmortem_settings = PostmortemSettings()
    return _postmortem_settings


def reset_postmortem_settings() -> None:
    """Reset cached settings (for testing)."""
    global _postmortem_settings
    _postmortem_settings = None


# ==========================================================================
# Deprecated Alias (하위 호환성)
# ==========================================================================


def _get_deprecated_setting(old_name: str, new_name: str, default_value):
    """Deprecated 환경 변수에서 값을 가져오고 경고 출력."""
    env_value = os.getenv(f"SELFHEALING_{old_name}")
    if env_value is not None:
        warnings.warn(
            f"SELFHEALING_{old_name} is deprecated. " f"Use SELFHEALING_POSTMORTEM_{new_name.upper()} instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        if isinstance(default_value, bool):
            return env_value.lower() in ("true", "1", "yes")
        elif isinstance(default_value, int):
            return int(env_value)
        return env_value
    return None


@property
def xtest_auto_postmortem_enabled(self) -> bool:
    """Deprecated: Use auto_enabled instead."""
    deprecated_value = _get_deprecated_setting("XTEST_AUTO_POSTMORTEM_ENABLED", "AUTO_ENABLED", False)
    if deprecated_value is not None:
        return deprecated_value
    return get_postmortem_settings().auto_enabled


@property
def xtest_auto_postmortem_min_duration(self) -> int:
    """Deprecated: Use auto_min_duration instead."""
    deprecated_value = _get_deprecated_setting("XTEST_AUTO_POSTMORTEM_MIN_DURATION", "AUTO_MIN_DURATION", 30)
    if deprecated_value is not None:
        return deprecated_value
    return get_postmortem_settings().auto_min_duration


@property
def xtest_postmortem_history_limit(self) -> int:
    """Deprecated: Use history_limit instead."""
    deprecated_value = _get_deprecated_setting("XTEST_POSTMORTEM_HISTORY_LIMIT", "HISTORY_LIMIT", 100)
    if deprecated_value is not None:
        return deprecated_value
    return get_postmortem_settings().history_limit


# ==========================================================================
# Module Exports
# ==========================================================================

__all__ = [
    "PostmortemSettings",
    "get_postmortem_settings",
    "reset_postmortem_settings",
]
