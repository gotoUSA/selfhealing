"""
Audit Reconciler Settings - Pydantic v2.

WAL vs 중앙 저장소 정합성 검증 설정.
ADR-005 (Fail-Open + WAL 기반 누락 0 보장) 구현의 보조 컴포넌트.

Source:
- audit/reconciler.py (ReconcilerConfig)

Environment Variables:
    SELFHEALING_AUDIT_RECONCILER_CHECK_INTERVAL_SECONDS=300.0
    SELFHEALING_AUDIT_RECONCILER_CHECK_WINDOW_SECONDS=3600.0
    SELFHEALING_AUDIT_RECONCILER_RESEND_BATCH_SIZE=50
    SELFHEALING_AUDIT_RECONCILER_MAX_RESEND_ATTEMPTS=3
    SELFHEALING_AUDIT_RECONCILER_ALERT_THRESHOLD=10
    SELFHEALING_AUDIT_RECONCILER_MAX_CONFIRMED_IDS=10000
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AuditReconcilerSettings(BaseSettings):
    """
    Audit Reconciler 설정.

    WAL과 중앙 저장소 간의 정합성 검증 주기, 재전송 설정 등을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUDIT_RECONCILER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Check Interval & Window (from reconciler.py lines 41-44)
    # ==========================================================================
    check_interval_seconds: float = Field(
        default=300.0,
        ge=60.0,
        le=3600.0,
        description="검증 주기 (초). 기본 5분.",
    )
    check_window_seconds: float = Field(
        default=3600.0,
        ge=300.0,
        le=86400.0,
        description="검증 범위 (초). 최근 N초 내 엔트리만 검증. 기본 1시간.",
    )

    # ==========================================================================
    # Resend Settings (from reconciler.py lines 47-50)
    # ==========================================================================
    resend_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="누락 재전송 시 배치 크기",
    )
    max_resend_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="최대 재전송 시도 횟수",
    )

    # ==========================================================================
    # Alert Settings (from reconciler.py line 53)
    # ==========================================================================
    alert_threshold: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="N개 이상 누락 시 알림 발생",
    )

    # ==========================================================================
    # Cache Settings (from reconciler.py line 170)
    # ==========================================================================
    max_confirmed_ids: int = Field(
        default=10000,
        ge=1000,
        le=1000000,
        description="중앙 저장소에서 확인된 record_id 캐시 최대 크기",
    )

    @field_validator("check_interval_seconds")
    @classmethod
    def validate_check_interval(cls, v: float) -> float:
        """check_interval이 너무 짧으면 경고."""
        if v < 120:
            logger.warning(
                f"[AuditReconcilerSettings] Low check_interval={v}s, "
                "consider using >= 120s to reduce overhead"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[AuditReconcilerSettings] = None


def get_audit_reconciler_settings() -> AuditReconcilerSettings:
    """
    캐시된 AuditReconcilerSettings 인스턴스 반환.

    Returns:
        AuditReconcilerSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = AuditReconcilerSettings()
    return _settings


def reset_audit_reconciler_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
