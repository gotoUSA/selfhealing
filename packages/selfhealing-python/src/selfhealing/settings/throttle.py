"""
Throttle Settings - Pydantic v2.

Netflix Gradient 기반 적응형 스로틀 설정입니다.

Replaces:
- services/throttle/config.py:ThrottleConfig

Environment Variables:
    SELFHEALING_THROTTLE_INITIAL_LIMIT=100
    SELFHEALING_THROTTLE_MIN_LIMIT=10
    SELFHEALING_THROTTLE_MAX_LIMIT=500

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 2 [10])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §7.1
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ThrottleSettings(BaseSettings):
    """
    Netflix Gradient 기반 적응형 스로틀 설정.

    RTT 그래디언트를 기반으로 동적으로 요청 제한을 조절합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_THROTTLE_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Basic Rate Limiting (from throttle/config.py)
    # ==========================================================================
    initial_limit: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="초기 요청 제한 (윈도우당 요청 수)",
    )

    window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="윈도우 크기 (초)",
    )

    # ==========================================================================
    # Adaptive Throttling Limits
    # ==========================================================================
    min_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="최소 제한 (이 값 아래로 내려가지 않음)",
    )

    max_limit: int = Field(
        default=500,
        ge=100,
        le=100000,
        description="최대 제한 (이 값 위로 올라가지 않음)",
    )

    # ==========================================================================
    # Gradient Calculation Settings
    # ==========================================================================
    sample_interval_ms: int = Field(
        default=500,
        ge=50,
        le=5000,
        description="RTT 샘플링 간격 (ms)",
    )

    smoothing_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="지수 평활화 계수 (0-1, 높을수록 반응적)",
    )

    sample_window_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=300.0,
        description="RTT 샘플 윈도우 크기 (초). GradientCalculator에서 샘플 조회 범위",
    )

    gradient_min_samples: int = Field(
        default=3,
        ge=1,
        le=100,
        description="그래디언트 계산에 필요한 최소 샘플 수",
    )

    # ==========================================================================
    # Adjustment Rates
    # ==========================================================================
    decrease_ratio: float = Field(
        default=0.9,
        ge=0.5,
        le=0.99,
        description="RTT 증가 시 곱할 비율 (감소)",
    )

    increase_step: int = Field(
        default=1,
        ge=1,
        le=100,
        description="RTT 감소 시 더할 값 (증가)",
    )

    # ==========================================================================
    # SLA Thresholds (ms)
    # ==========================================================================
    sla_warning_ms: int = Field(
        default=200,
        ge=10,
        le=5000,
        description="스로틀링 시작 RTT 임계값 (ms)",
    )

    sla_critical_ms: int = Field(
        default=500,
        ge=50,
        le=10000,
        description="공격적 스로틀링 시작 RTT 임계값 (ms)",
    )

    # ==========================================================================
    # Emergency Mode
    # ==========================================================================
    emergency_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="비상 모드 시 윈도우당 요청 제한",
    )

    # ==========================================================================
    # Emergency Level Multipliers (Level별 limit 배율)
    # ==========================================================================
    emergency_level_0_multiplier: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="NORMAL(0) 레벨 limit 배율 (100%)",
    )

    emergency_level_1_multiplier: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="LEVEL_1(1) limit 배율 (80%)",
    )

    emergency_level_2_multiplier: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="LEVEL_2(2) limit 배율 (50%)",
    )

    emergency_level_3_multiplier: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LEVEL_3(3) limit 배율 (min_limit 사용, 0.0)",
    )

    # ==========================================================================
    # Circuit Breaker 연동 설정
    # ==========================================================================
    cb_open_limit_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="CB OPEN 시 limit 비율 (0.0 = min_limit 사용)",
    )

    cb_half_open_limit_percent: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="CB HALF_OPEN 시 limit 비율 (50%)",
    )

    # ==========================================================================
    # EventBus 연동 설정
    # ==========================================================================
    enable_event_integration: bool = Field(
        default=True,
        description="EventBus 이벤트 연동 활성화",
    )

    sync_on_startup: bool = Field(
        default=True,
        description="시작 시 Emergency/CB 상태 동기화",
    )

    # ==========================================================================
    # Recovery Dampening 설정 (점진적 복구)
    # ==========================================================================
    recovery_dampening_enabled: bool = Field(
        default=True,
        description="Recovery Dampening 활성화 (Thundering Herd 방지)",
    )

    recovery_step_1_percent: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="복구 1단계 비율 (80%)",
    )

    recovery_step_2_percent: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="복구 2단계 비율 (90%)",
    )

    recovery_step_3_percent: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="복구 3단계 비율 (100%)",
    )

    recovery_step_interval_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="복구 단계 간격 (초)",
    )

    # ==========================================================================
    # Gradient Freeze 설정
    # ==========================================================================
    gradient_freeze_on_level_3: bool = Field(
        default=True,
        description="LEVEL_3에서 Gradient 적용 중단 (계산은 유지)",
    )

    # ==========================================================================
    # Full Stop 설정 (3중 조건)
    # ==========================================================================
    full_stop_conditions_enabled: bool = Field(
        default=True,
        description="Full Stop 3중 조건 활성화 (LEVEL_3 + DB_CB + Budget)",
    )

    # ==========================================================================
    # Safe-Open 폴백 설정
    # ==========================================================================
    safe_open_fallback_enabled: bool = Field(
        default=True,
        description="Redis 다운 시 Safe-Open 폴백 활성화",
    )

    static_safe_limit_percent: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Safe-Open 시 정적 limit 비율 (50%)",
    )

    redis_last_safe_limit_key_pattern: str = Field(
        default="throttle:last_safe_limit:{service}",
        description="Cold Start 복구용 Redis 키 패턴",
    )

    # ==========================================================================
    # Sync 콜백 설정
    # ==========================================================================
    sync_callback_enabled: bool = Field(
        default=True,
        description="CB OPEN Sync 콜백 활성화 (로컬 즉시 적용)",
    )

    # ==========================================================================
    # Load Shedding 연동 설정
    # ==========================================================================
    shedding_compensation_factor: float = Field(
        default=1.5,
        ge=1.0,
        le=3.0,
        description="Load Shedding 이중 차단 방지 보상 계수. "
        "Middleware가 이미 차단한 비율을 감안하여 Throttle limit 감소를 완화.",
    )

    # ==========================================================================
    # Redis Key Prefix
    # ==========================================================================
    key_prefix: str = Field(
        default="selfhealing:throttle",
        description="Redis 키 접두사",
    )

    # ==========================================================================
    # Prometheus Metrics Label
    # ==========================================================================
    service_name: str = Field(
        default="default",
        description="Prometheus 메트릭의 service 라벨 값. " "환경변수 SELFHEALING_THROTTLE_SERVICE_NAME으로 주입.",
    )

    # ==========================================================================
    # DLQ 연동 설정 (Throttle 거부 요청 DLQ 저장 및 Recovery 시 자동 Replay)
    # 기존 ThrottleConfig(dataclass)에서 통합됨
    # ==========================================================================
    dlq_on_rejection: bool = Field(
        default=True,
        description="Throttle 거부 시 DLQ에 저장 여부",
    )

    auto_replay_on_recovery: bool = Field(
        default=True,
        description="Recovery 시 DLQ 자동 Replay 여부",
    )

    replay_batch_size: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Replay 배치 크기",
    )

    replay_interval_ms: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Replay 배치 간격 (ms)",
    )

    replay_min_recovery_percent: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        description="Replay 시작을 위한 최소 Recovery 비율 (%)",
    )

    dlq_store_sampling_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="DLQ 저장 샘플링 비율 (1.0 = 전수 저장)",
    )

    dlq_store_non_essential: bool = Field(
        default=False,
        description="비필수 요청도 DLQ에 저장 여부",
    )

    @field_validator("max_limit")
    @classmethod
    def validate_max_limit(cls, v: int, info) -> int:
        """max_limit이 min_limit보다 커야 함."""
        # 다른 필드 접근이 어려우므로 기본값과 비교
        if v < 10:  # min_limit 기본값
            logger.warning(
                "safe_default.very_low_cause_issues",
                setting_value=v,
            )
        return v

    @field_validator("sla_critical_ms")
    @classmethod
    def validate_sla_critical(cls, v: int, info) -> int:
        """sla_critical_ms가 sla_warning_ms보다 커야 함."""
        # 기본값 200과 비교
        if v < 200:
            logger.warning(
                "safe_default.lower_than_typical_warning",
                setting_value=v,
            )
        return v

    def get_emergency_level_multipliers(self) -> dict[int, float]:
        """Emergency Level별 limit 배율 딕셔너리 반환."""
        return {
            0: self.emergency_level_0_multiplier,
            1: self.emergency_level_1_multiplier,
            2: self.emergency_level_2_multiplier,
            3: self.emergency_level_3_multiplier,
        }

    def get_recovery_steps(self) -> tuple[float, ...]:
        """Recovery Dampening 단계 비율 튜플 반환."""
        return (
            self.recovery_step_1_percent,
            self.recovery_step_2_percent,
            self.recovery_step_3_percent,
        )

    # =========================================================================
    # 하위 호환 메서드 (기존 ThrottleConfig 인터페이스)
    # =========================================================================
    @classmethod
    def from_dict(cls, data: dict) -> "ThrottleSettings":
        """Create settings from dictionary (backward compat with ThrottleConfig)."""
        return cls(**{k: v for k, v in data.items() if k in cls.model_fields})

    @classmethod
    def from_settings(cls) -> "ThrottleSettings":
        """Create from current settings (backward compat with ThrottleConfig)."""
        return get_throttle_settings()


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: ThrottleSettings | None = None


def get_throttle_settings() -> ThrottleSettings:
    """Get cached ThrottleSettings instance."""
    global _settings
    if _settings is None:
        _settings = ThrottleSettings()
    return _settings


def reset_throttle_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
