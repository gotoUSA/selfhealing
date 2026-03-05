"""
Circuit Mesh Settings - Pydantic v2.

Adaptive Circuit Breaker Mesh Coordinator 설정.
하류 CB 상태 기반 상류 임계치 동적 조정, 감쇠 전파, 순차 복구, TTL Heartbeat.

Source:
- services/circuit_mesh/mesh_coordinator.py
- services/circuit_mesh/store.py

Environment Variables:
    SELFHEALING_CIRCUIT_MESH_ENABLED=false
    SELFHEALING_CIRCUIT_MESH_THRESHOLD_MULTIPLIER=2.0
    SELFHEALING_CIRCUIT_MESH_RECOVERY_TIMEOUT_MULTIPLIER=3.0
    SELFHEALING_CIRCUIT_MESH_OVERRIDE_TTL_SECONDS=600
    SELFHEALING_CIRCUIT_MESH_PROPAGATION_MAX_DEPTH=1
    SELFHEALING_CIRCUIT_MESH_FAST_RECOVERY_TIMEOUT_SECONDS=5
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class CircuitMeshSettings(BaseSettings):
    """
    Adaptive Circuit Breaker Mesh 설정.

    하류 CB OPEN 시 상류 임계치 동적 조정, 감쇠 전파, 순차 복구를 제어합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CIRCUIT_MESH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=False,
        description="Circuit Mesh Coordinator 활성화 여부",
    )

    # --- 임계치 조정 ---

    threshold_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="하류 OPEN 시 상류 failure_threshold 배율 (2.0 = 임계치 2배 상향)",
    )

    recovery_timeout_multiplier: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="하류 OPEN 시 상류 recovery_timeout 배율 (3.0 = 타임아웃 3배 연장)",
    )

    override_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="오버라이드 자동 만료 (seconds) — safety net",
    )

    # --- 감쇠 전파 ---

    propagation_max_depth: int = Field(
        default=1,
        ge=1,
        le=5,
        description="전파 최대 깊이 (1 = 직접 부모만, 2 = 조부모까지)",
    )

    propagation_damping_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="depth마다 배율 감쇠 계수 (0.5 = depth 2에서 배율 50% 적용)",
    )

    # --- 순차 복구 ---

    coordinated_recovery_enabled: bool = Field(
        default=True,
        description="순차 복구 활성화 여부",
    )

    recovery_step_delay_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="순차 복구 시 단계 간 대기 시간 (seconds)",
    )

    fast_recovery_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="하류 복구 시 상류 fast-recovery timeout (seconds)",
    )

    # --- TTL Heartbeat ---

    max_renewals: int = Field(
        default=3,
        ge=0,
        le=10,
        description="최대 자동 갱신 횟수 (초과 시 EmergencyCoordinator 에스컬레이션)",
    )

    renewal_check_threshold_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="TTL 만료 N초 전부터 갱신 체크",
    )

    # --- 운영 ---

    snapshot_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="메쉬 상태 스냅샷 + 갱신 체크 주기 (seconds, Celery beat)",
    )

    max_concurrent_overrides: int = Field(
        default=20,
        ge=1,
        le=100,
        description="최대 동시 오버라이드 수 (안전장치)",
    )

    @field_validator("threshold_multiplier")
    @classmethod
    def validate_threshold_multiplier(cls, v: float) -> float:
        """threshold_multiplier가 1.0이면 조정 효과 없음 경고."""
        if v == 1.0:
            logger.warning(
                "circuit_mesh_settings.threshold_multiplier_is_one",
                setting_value=v,
                hint="multiplier=1.0 means no threshold adjustment",
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: CircuitMeshSettings | None = None


def get_circuit_mesh_settings() -> CircuitMeshSettings:
    """
    캐시된 CircuitMeshSettings 인스턴스 반환.

    Returns:
        CircuitMeshSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = CircuitMeshSettings()
    return _settings


def reset_circuit_mesh_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
