"""
Emergency Mode Data Models.

RecoveryGateConfig and EmergencyState dataclasses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .enums import EmergencyLevel


@dataclass
class RecoveryGateConfig:
    """
    복구 게이트 설정.

    안전한 비상 모드 해제를 위한 안정화 기간 및 조건을 정의합니다.
    """

    # 안정화 대기 기간 (초)
    stabilization_period_seconds: int = 300  # 5분

    # 메트릭 기반 안정성 확인 필요 여부
    require_metrics_stable: bool = True

    # CPU 사용률 임계값 (이 이하여야 복구 가능)
    cpu_threshold_percent: float = 80.0

    # 오류율 임계값 (이 이하여야 복구 가능)
    error_rate_threshold: float = 0.05  # 5%

    # 점진적 복구 사용 여부
    gradual_recovery: bool = True

    # 레벨 단계별 복구 지연 시간 (초)
    level_step_delay_seconds: int = 60  # 1분

    # 복구 중 메트릭 재확인 주기 (초)
    health_check_interval_seconds: int = 30

    # 복구 실패 시 자동 롤백 여부
    auto_rollback_on_failure: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryGateConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EmergencyState:
    """비상 모드 상태."""

    level: EmergencyLevel = EmergencyLevel.NORMAL
    is_active: bool = False

    # 활성화 정보
    activated_at: str | None = None
    activated_by: str | None = None
    activation_reason: str | None = None

    # 자동 만료 시간
    expires_at: str | None = None

    # 비활성화 정보
    deactivated_at: str | None = None
    deactivated_by: str | None = None

    # 자동 활성화 여부 (시스템에 의한 자동 감지)
    is_auto_triggered: bool = False

    # 복구 중 상태
    is_recovering: bool = False
    recovery_started_at: str | None = None
    target_level: EmergencyLevel | None = None

    # Chaos-Aware 메타데이터
    metadata: dict[str, Any] | None = None
    """
    추가 메타데이터.

    카오스 실험으로 인한 활성화 시:
        {
            "is_chaos_experiment": True,
            "experiment_id": "exp-xxx",
            "classification": "chaos_induced_test"
        }
    """

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["level"] = self.level.value
        if self.target_level:
            result["target_level"] = self.target_level.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmergencyState:
        data = dict(data)
        if "level" in data:
            data["level"] = EmergencyLevel(data["level"])
        if "target_level" in data and data["target_level"] is not None:
            data["target_level"] = EmergencyLevel(data["target_level"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
