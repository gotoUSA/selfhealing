"""
Rate Limit Throttle Integration Settings - Pydantic v2.

429 응답과 AdaptiveThrottle 간의 연동 설정을 정의합니다.

Features:
    - 429 발생 시 throttle limit 자동 감소
    - 연속 429 횟수별 감소 비율 설정
    - Key-Service 매핑 (인접 간섭 방지)
    - Recovery 전략 설정
    - 에스컬레이션 설정

Environment Variables:
    SELFHEALING_RATE_LIMIT_THROTTLE_ENABLED=true
    SELFHEALING_RATE_LIMIT_THROTTLE_DEBOUNCE_WINDOW_SECONDS=5.0
    SELFHEALING_RATE_LIMIT_THROTTLE_ESCALATION_ENABLED=true
    ... etc
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class RateLimitThrottleIntegrationSettings(BaseSettings):
    """
    429-Throttle 연동 설정.

    외부 API의 429 응답 수신 시 AdaptiveThrottle의 limit을
    자동으로 조정하기 위한 설정입니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RATE_LIMIT_THROTTLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # =========================================================================
    # 기본 활성화 설정
    # =========================================================================
    enabled: bool = Field(
        default=True,
        description="429 발생 시 throttle limit 감소 활성화",
    )

    # =========================================================================
    # 연속 429 횟수별 limit 감소 비율
    # =========================================================================
    reduction_ratio_1: float = Field(
        default=0.8,
        ge=0.1,
        le=1.0,
        description="1회 429 시 유지 비율 (0.8 = 20% 감소)",
    )
    reduction_ratio_2: float = Field(
        default=0.6,
        ge=0.1,
        le=1.0,
        description="2회 연속 429 시 유지 비율 (0.6 = 40% 감소)",
    )
    reduction_ratio_3: float = Field(
        default=0.5,
        ge=0.1,
        le=1.0,
        description="3회 이상 연속 429 시 유지 비율 (0.5 = 50% 감소)",
    )

    # =========================================================================
    # SLA Warning/Critical 임계값
    # =========================================================================
    sla_warning_threshold: int = Field(
        default=3,
        ge=1,
        le=100,
        description="SLA Warning 발행 임계값 (연속 429 횟수)",
    )

    # =========================================================================
    # Recovery 전략 설정
    # =========================================================================
    recovery_strategy: Literal["immediate", "gradual"] = Field(
        default="gradual",
        description="Cooldown 해제 후 limit 복구 전략",
    )
    recovery_dampening_steps: int = Field(
        default=3,
        ge=1,
        le=10,
        description="gradual 복구 시 단계 수",
    )

    # =========================================================================
    # EventBus 디바운싱 설정
    # =========================================================================
    debounce_window_seconds: float = Field(
        default=5.0,
        ge=0.0,
        le=60.0,
        description="동일 key에 대한 이벤트 중복 방지 윈도우 (초)",
    )

    # =========================================================================
    # Key-Service 매핑 (인접 간섭 방지)
    # =========================================================================
    default_service: str = Field(
        default="default",
        description="매핑되지 않은 Key의 기본 서비스",
    )

    # Note: key_to_service_mapping은 환경변수로 설정하기 어려우므로
    # 코드에서 직접 설정하거나 별도 설정 파일 사용

    # =========================================================================
    # 에스컬레이션 설정
    # =========================================================================
    escalation_enabled: bool = Field(
        default=True,
        description="연속 429 임계치 도달 시 에스컬레이션 활성화",
    )
    escalation_threshold_consecutive_429s: int = Field(
        default=10,
        ge=1,
        le=100,
        description="에스컬레이션 발동 임계값 (연속 429 횟수)",
    )

    # =========================================================================
    # Conservative Limit (Min-Winner 정책)
    # =========================================================================
    conservative_limit_enabled: bool = Field(
        default=True,
        description="RTT와 429 limit 중 낮은 값 선택 활성화",
    )

    def get_reduction_ratio(self, consecutive_429s: int) -> float:
        """
        연속 429 횟수에 따른 감소 비율 반환.

        Args:
            consecutive_429s: 연속 429 횟수

        Returns:
            유지 비율 (예: 0.8 = 20% 감소)
        """
        if consecutive_429s >= 3:
            return self.reduction_ratio_3
        elif consecutive_429s == 2:
            return self.reduction_ratio_2
        else:
            return self.reduction_ratio_1


@lru_cache(maxsize=1)
def get_rate_limit_throttle_settings() -> RateLimitThrottleIntegrationSettings:
    """
    Rate Limit Throttle Integration 설정 싱글톤 반환.

    Returns:
        RateLimitThrottleIntegrationSettings 인스턴스
    """
    return RateLimitThrottleIntegrationSettings()


def clear_settings_cache() -> None:
    """설정 캐시 초기화 (테스트용)."""
    get_rate_limit_throttle_settings.cache_clear()
