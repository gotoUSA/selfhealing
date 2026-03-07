"""
Backpressure Settings - Pydantic v2.

Auto-Scaling & Backpressure 설정.
트래픽 제어용 설정 (기존 ScaleSettings와 역할 분리).

Moved from: scaling/config.py (위치 통일)

환경변수 접두사: SELFHEALING_BACKPRESSURE_
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackpressureLevel(str, Enum):
    """
    Backpressure 레벨.

    큐 크기에 따라 시스템 부하 상태를 나타냅니다.
    """

    NONE = "none"  # 정상 상태
    LOW = "low"  # 약간 과부하
    MEDIUM = "medium"  # 중간 과부하
    HIGH = "high"  # 높은 과부하
    CRITICAL = "critical"  # 위험 (긴급 조치 필요)


class BackpressureStrategy(str, Enum):
    """
    Backpressure 전략.

    과부하 시 어떤 방식으로 대응할지 결정합니다.
    """

    DROP_OLDEST = "drop_oldest"  # 오래된 항목 삭제
    DROP_NEWEST = "drop_newest"  # 최신 항목 삭제
    REJECT = "reject"  # 거부 (HTTP 503)
    THROTTLE = "throttle"  # Rate Limit 적용
    QUEUE = "queue"  # 대기열에 추가


# =============================================================================
# AIMD (Additive Increase, Multiplicative Decrease) 패턴
# 레벨별 Rate 감소 배율
# =============================================================================

LEVEL_RATE_MULTIPLIERS: dict[BackpressureLevel, float] = {
    BackpressureLevel.NONE: 1.0,  # 정상: 100% 처리율
    BackpressureLevel.LOW: 1.0,  # 약간: 유지
    BackpressureLevel.MEDIUM: 0.9,  # 중간: 90%로 감소
    BackpressureLevel.HIGH: 0.8,  # 높음: 80%로 감소
    BackpressureLevel.CRITICAL: 0.5,  # 위험: 50%로 급감 (AIMD의 MD 부분)
}


class BackpressureSettings(BaseSettings):
    """
    Auto-Scaling & Backpressure 설정.

    환경변수:
        SELFHEALING_BACKPRESSURE_ENABLED=true
        SELFHEALING_BACKPRESSURE_DEFAULT_STRATEGY=throttle
        SELFHEALING_BACKPRESSURE_MAX_RATE_PER_SECOND=1000
        ...
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BACKPRESSURE_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # Backpressure 활성화
    backpressure_enabled: bool = Field(
        default=True,
        description="Backpressure 활성화 여부",
    )

    # 기본 전략
    default_strategy: BackpressureStrategy = Field(
        default=BackpressureStrategy.THROTTLE,
        description="기본 Backpressure 전략",
    )

    # 큐 임계치 (큐 크기에 따른 레벨 결정)
    queue_low_threshold: int = Field(
        default=100,
        ge=1,
        description="LOW 레벨 큐 크기 임계치",
    )
    queue_medium_threshold: int = Field(
        default=500,
        ge=1,
        description="MEDIUM 레벨 큐 크기 임계치",
    )
    queue_high_threshold: int = Field(
        default=1000,
        ge=1,
        description="HIGH 레벨 큐 크기 임계치",
    )
    queue_critical_threshold: int = Field(
        default=5000,
        ge=1,
        description="CRITICAL 레벨 큐 크기 임계치",
    )

    # Rate Limit (처리/초)
    max_rate_per_second: float = Field(
        default=1000.0,
        ge=1.0,
        description="최대 처리율 (항목/초)",
    )
    min_rate_per_second: float = Field(
        default=10.0,
        ge=1.0,
        description="최소 처리율 (항목/초)",
    )

    # Rate 조절 파라미터
    rate_increase_factor: float = Field(
        default=1.1,
        ge=1.0,
        le=2.0,
        description="Rate 증가 계수 (정상화 시)",
    )
    rate_adjust_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description="Rate 조절 주기 (초)",
    )

    # 큐 크기 캐싱 (Redis 네트워크 지연 방지)
    queue_size_cache_ttl_seconds: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        description="큐 크기 캐시 TTL (초)",
    )

    # Prometheus 메트릭 설정
    metrics_enabled: bool = Field(
        default=True,
        description="Prometheus 메트릭 활성화",
    )
    metrics_prefix: str = Field(
        default="selfhealing_",
        description="메트릭 이름 prefix",
    )

    # HPA 설정
    hpa_enabled: bool = Field(
        default=True,
        description="HPA 커스텀 메트릭 활성화",
    )
    hpa_target_queue_depth: int = Field(
        default=100,
        ge=1,
        description="HPA 목표 큐 깊이",
    )

    # Graceful Degradation
    graceful_degradation_enabled: bool = Field(
        default=True,
        description="Graceful Degradation 활성화",
    )

    # CPU 사용률 기반 Rate 감쇠 임계치
    resource_cpu_high_threshold: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        description="CPU 사용률이 이 값 이상이면 Rate를 50%로 감쇠",
    )
    resource_cpu_critical_threshold: float = Field(
        default=90.0,
        ge=0.0,
        le=100.0,
        description="CPU 사용률이 이 값 이상이면 Rate를 10%로 감쇠",
    )

    # 503 응답 커스터마이징
    reject_message: str = Field(
        default="Service temporarily unavailable due to high load",
        description="503 거부 시 응답 메시지",
    )
    reject_retry_after_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Retry-After 헤더 값 (초)",
    )

    # =========================================================================
    # Priority Watermark — 토큰 잔량 비율 임계치
    # 현재 토큰 비율이 이 값 미만이면 해당 tier 요청을 거부한다.
    # 환경변수 예: SELFHEALING_BACKPRESSURE_WATERMARK_STANDARD=0.4
    # =========================================================================
    watermark_critical: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="critical tier Watermark 임계치. 토큰 비율이 이 값 미만이면 거부.",
    )
    watermark_standard: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="standard tier Watermark 임계치. 토큰 비율이 이 값 미만이면 거부.",
    )
    watermark_non_essential: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="non_essential tier Watermark 임계치. 토큰 비율이 이 값 미만이면 거부.",
    )

    def get_level_for_queue_size(self, queue_size: int) -> BackpressureLevel:
        """
        큐 크기에 따른 Backpressure 레벨 반환.

        Args:
            queue_size: 현재 큐 크기

        Returns:
            BackpressureLevel: 해당하는 레벨
        """
        if queue_size >= self.queue_critical_threshold:
            return BackpressureLevel.CRITICAL
        elif queue_size >= self.queue_high_threshold:
            return BackpressureLevel.HIGH
        elif queue_size >= self.queue_medium_threshold:
            return BackpressureLevel.MEDIUM
        elif queue_size >= self.queue_low_threshold:
            return BackpressureLevel.LOW
        else:
            return BackpressureLevel.NONE

    def get_rate_multiplier(self, level: BackpressureLevel) -> float:
        """
        레벨별 Rate 감소 배율 반환 (AIMD 패턴).

        Args:
            level: Backpressure 레벨

        Returns:
            float: Rate 배율 (0.0 ~ 1.0)
        """
        return LEVEL_RATE_MULTIPLIERS.get(level, 1.0)

    def get_priority_watermarks(self) -> dict[str, float]:
        """Tier별 Watermark 임계치 딕셔너리 반환."""
        return {
            "critical": self.watermark_critical,
            "standard": self.watermark_standard,
            "non_essential": self.watermark_non_essential,
        }

    def get_retry_after_for_level(self, level: BackpressureLevel) -> int:
        """BackpressureLevel별 Retry-After 값 반환.

        부하가 높을수록 클라이언트 재시도 간격을 늘려
        Retry Storm을 방지한다.
        base * 배율로 계산하며 단일 설정 노브로 전체 스케일 조절 가능.

        Args:
            level: 현재 Backpressure 레벨

        Returns:
            Retry-After 값 (초)
        """
        base = self.reject_retry_after_seconds
        multiplier = {
            BackpressureLevel.NONE: 1,
            BackpressureLevel.LOW: 1,
            BackpressureLevel.MEDIUM: 2,
            BackpressureLevel.HIGH: 4,
            BackpressureLevel.CRITICAL: 8,
        }
        return base * multiplier.get(level, 1)


def get_backpressure_settings() -> "BackpressureSettings":
    from selfhealing.settings.root import get_config

    return get_config().scaling.backpressure

def reset_backpressure_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().scaling.__dict__["backpressure"]
    except KeyError:
        pass
