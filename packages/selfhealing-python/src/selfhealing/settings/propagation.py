"""
Propagation Settings - Cross-Cluster Configuration Propagation.

전파 일관성 설정을 정의합니다.

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PropagationSettings(BaseSettings):
    """
    전파 일관성 설정.

    데이터 성격별 일관성 등급:
    - Tier 1: Audit, Governance, Emergency (1초 내 전파)
    - Tier 2: Metrics, Stats, Cache (30초 내 전파)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_PROPAGATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tier 1 SLA (즉시 전파)
    tier1_max_latency_ms: int = Field(
        default=1000,
        description="Tier 1 (Audit/Governance/Emergency) 최대 전파 지연 (ms)",
        ge=100,
        le=5000,
    )

    # Tier 2 SLA (최종 일관성)
    tier2_max_latency_ms: int = Field(
        default=30000,
        description="Tier 2 (Metrics/Stats) 최대 전파 지연 (ms)",
        ge=1000,
        le=300000,
    )

    # 전파 활성화
    enabled: bool = Field(
        default=True,
        description="글로벌 설정 전파 활성화 여부",
    )

    # 자동 리스너 시작
    auto_start_listener: bool = Field(
        default=False,
        description="애플리케이션 시작 시 자동으로 전파 리스너 시작",
    )

    # 재시도 설정
    retry_count: int = Field(
        default=3,
        description="전파 실패 시 재시도 횟수",
        ge=0,
        le=10,
    )
    retry_delay_ms: int = Field(
        default=500,
        description="재시도 간격 (ms)",
        ge=100,
        le=10000,
    )

    # Health Score 가중치
    health_score_weight: float = Field(
        default=0.3,
        description="종합 HealthScore에서 Propagation 점수 가중치 (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )

    # 감점 설정
    tier1_penalty_points: int = Field(
        default=5,
        description="Tier 1 SLA 위반 시 감점",
        ge=1,
        le=50,
    )
    tier2_penalty_points: int = Field(
        default=1,
        description="Tier 2 SLA 위반 시 감점",
        ge=1,
        le=10,
    )


# =============================================================================
# Singleton
# =============================================================================

_settings = None


def get_propagation_settings() -> PropagationSettings:
    """PropagationSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        _settings = PropagationSettings()
    return _settings


def reset_propagation_settings() -> None:
    """테스트용 싱글톤 리셋."""
    global _settings
    _settings = None
