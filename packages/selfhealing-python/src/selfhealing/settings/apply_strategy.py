"""
ApplyStrategy Settings - Pydantic v2.

설정 적용 전략별 delay 및 grace_timeout 설정.
config 타입별 기본 지연 시간을 환경변수로 설정 가능.

Environment Variables:
    SELFHEALING_APPLY_SLA_DELAY=0
    SELFHEALING_APPLY_METRICS_DELAY=0
    SELFHEALING_APPLY_NOTIFICATION_DELAY=0
    SELFHEALING_APPLY_FORENSIC_DELAY=0
    SELFHEALING_APPLY_RATE_LIMIT_DELAY=0
    SELFHEALING_APPLY_RETRY_DELAY=10
    SELFHEALING_APPLY_DLQ_DELAY=10
    SELFHEALING_APPLY_CIRCUIT_BREAKER_DELAY=30
    SELFHEALING_APPLY_IDEMPOTENCY_DELAY=30
    SELFHEALING_APPLY_SECURITY_DELAY=60
    SELFHEALING_APPLY_ERROR_BUDGET_DELAY=30
    SELFHEALING_APPLY_DEFAULT_GRACE_TIMEOUT=60
"""

import logging
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ApplyStrategySettings(BaseSettings):
    """
    ApplyStrategy 설정.

    설정 변경 적용 시 타입별 지연 시간 및 grace timeout 설정.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_APPLY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 즉시 적용 (Safe Immediate) - delay_seconds
    # ==========================================================================
    sla_delay: int = Field(
        default=0,
        ge=0,
        le=300,
        description="SLA 설정 적용 지연 시간 (초)",
    )
    metrics_delay: int = Field(
        default=0,
        ge=0,
        le=300,
        description="메트릭 설정 적용 지연 시간 (초)",
    )
    notification_delay: int = Field(
        default=0,
        ge=0,
        le=300,
        description="알림 설정 적용 지연 시간 (초)",
    )
    forensic_delay: int = Field(
        default=0,
        ge=0,
        le=300,
        description="포렌식 설정 적용 지연 시간 (초)",
    )

    # ==========================================================================
    # 트래픽 제어 - 즉시지만 주의 필요
    # ==========================================================================
    rate_limit_delay: int = Field(
        default=0,
        ge=0,
        le=300,
        description="Rate Limit 설정 적용 지연 시간 (초)",
    )

    # ==========================================================================
    # 처리 관련 - 지연 적용
    # ==========================================================================
    retry_delay: int = Field(
        default=10,
        ge=0,
        le=300,
        description="재시도 설정 적용 지연 시간 (초)",
    )
    dlq_delay: int = Field(
        default=10,
        ge=0,
        le=300,
        description="DLQ 설정 적용 지연 시간 (초)",
    )

    # ==========================================================================
    # 핵심 보호 - 긴 지연
    # ==========================================================================
    circuit_breaker_delay: int = Field(
        default=30,
        ge=0,
        le=600,
        description="서킷브레이커 설정 적용 지연 시간 (초)",
    )
    idempotency_delay: int = Field(
        default=30,
        ge=0,
        le=600,
        description="멱등성 설정 적용 지연 시간 (초)",
    )
    security_delay: int = Field(
        default=60,
        ge=0,
        le=600,
        description="보안 설정 적용 지연 시간 (초)",
    )
    error_budget_delay: int = Field(
        default=30,
        ge=0,
        le=600,
        description="에러 버짓 설정 적용 지연 시간 (초)",
    )

    # ==========================================================================
    # 공통 설정
    # ==========================================================================
    default_grace_timeout: int = Field(
        default=60,
        ge=10,
        le=600,
        description="GRACEFUL 전략 사용 시 기본 최대 대기 시간 (초)",
    )

    def get_delay_for_config_type(self, config_type: str) -> int:
        """
        config 타입에 맞는 delay 값 반환.

        Args:
            config_type: 설정 타입명 (예: "circuit_breaker", "retry")

        Returns:
            지연 시간 (초). 알 수 없는 타입은 0 반환.
        """
        delay_map = {
            "sla": self.sla_delay,
            "metrics": self.metrics_delay,
            "notification": self.notification_delay,
            "forensic": self.forensic_delay,
            "rate_limit": self.rate_limit_delay,
            "retry": self.retry_delay,
            "dlq": self.dlq_delay,
            "circuit_breaker": self.circuit_breaker_delay,
            "idempotency": self.idempotency_delay,
            "security": self.security_delay,
            "error_budget": self.error_budget_delay,
        }
        return delay_map.get(config_type, 0)


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[ApplyStrategySettings] = None


def get_apply_strategy_settings() -> ApplyStrategySettings:
    """
    캐시된 ApplyStrategySettings 인스턴스 반환.

    Returns:
        ApplyStrategySettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = ApplyStrategySettings()
        logger.debug(
            "[ApplyStrategySettings] Loaded: "
            f"cb_delay={_settings.circuit_breaker_delay}s, "
            f"security_delay={_settings.security_delay}s, "
            f"grace_timeout={_settings.default_grace_timeout}s"
        )
    return _settings


def reset_apply_strategy_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).
    """
    global _settings
    _settings = None
    logger.debug("[ApplyStrategySettings] Reset")
