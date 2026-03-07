"""
X-Test Resource Guard Settings - Pydantic v2.

X-Test 요청 시 시스템 CPU/메모리 과부하 상태를 체크하여
운영 시스템에 추가 부담을 방지하기 위한 설정.

Environment Variables:
    SELFHEALING_RESOURCE_GUARD_CPU_THRESHOLD=80           # CPU 임계값 (%)
    SELFHEALING_RESOURCE_GUARD_MEMORY_THRESHOLD=85        # 메모리 임계값 (%)
    SELFHEALING_RESOURCE_GUARD_RESOURCE_CHECK_ENABLED=true  # 리소스 체크 활성화 여부
    SELFHEALING_RESOURCE_GUARD_RETRY_AFTER_SECONDS=30     # 429 응답 시 권장 대기 시간
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ResourceGuardSettings(BaseSettings):
    """
    X-Test 리소스 가드 설정.

    시스템 CPU/메모리가 과부하 상태일 때 X-Test 요청을 차단하여
    운영 시스템 안정성을 보호합니다.

    RecoveryGate의 cpu_threshold_percent (80%)와 일관성 유지.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RESOURCE_GUARD_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # CPU 임계값
    # ==========================================================================
    cpu_threshold: float = Field(
        default=80.0,
        ge=50.0,
        le=99.0,
        description="CPU 사용률 임계값 (%). 이 값을 초과하면 X-Test 차단. RecoveryGate와 동일하게 80%.",
    )

    # ==========================================================================
    # 메모리 임계값
    # ==========================================================================
    memory_threshold: float = Field(
        default=85.0,
        ge=50.0,
        le=99.0,
        description="메모리 사용률 임계값 (%). 이 값을 초과하면 X-Test 차단.",
    )

    # ==========================================================================
    # 리소스 체크 활성화 여부
    # ==========================================================================
    resource_check_enabled: bool = Field(
        default=True,
        description="리소스 체크 활성화 여부. false 시 체크 생략.",
    )

    # ==========================================================================
    # 429 응답 시 권장 대기 시간
    # ==========================================================================
    retry_after_seconds: int = Field(
        default=30,
        ge=10,
        le=300,
        description="429 응답 시 Retry-After 헤더 값 (초).",
    )


def get_resource_guard_settings() -> "ResourceGuardSettings":
    from selfhealing.settings.root import get_config

    return get_config().meta.resource_guard


def reset_resource_guard_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().meta.__dict__["resource_guard"]
    except KeyError:
        pass
