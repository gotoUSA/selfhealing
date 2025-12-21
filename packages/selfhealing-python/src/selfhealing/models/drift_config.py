"""
Drift Threshold Configuration Model.

Provides dynamic configuration for metric drift thresholds.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any


@dataclass
class DriftThresholdConfig:
    """
    Drift 임계값 설정.

    운영자가 동적으로 조정할 수 있으며,
    변경 시 Audit 로그가 기록됩니다.

    Thresholds (임계값):
        - warning: 5% - 경고, 로그만 기록
        - critical: 20% - 심각, 알림 발송
        - incident: 50% - 인시던트, 이벤트 유실 의심

    Example:
        >>> config = DriftThresholdConfig()
        >>> print(f"Warning at: {config.warning_threshold * 100}%")
        Warning at: 5.0%
        >>>
        >>> # 사용자 정의 임계값
        >>> config = DriftThresholdConfig(
        ...     warning_threshold=0.10,
        ...     critical_threshold=0.30,
        ... )
    """

    # 임계값 (0.0 ~ 1.0)
    warning_threshold: float = 0.05     # 5%
    critical_threshold: float = 0.20    # 20%
    incident_threshold: float = 0.50    # 50%

    # 알림 설정
    alert_enabled: bool = True
    incident_auto_create: bool = True

    # 메타데이터
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None

    def __post_init__(self) -> None:
        """생성 후 유효성 검사 수행."""
        self._validate()

    def _validate(self) -> None:
        """임계값 유효성 검사."""
        if not (
            0 < self.warning_threshold < self.critical_threshold < self.incident_threshold <= 1.0
        ):
            raise ValueError(
                "Thresholds must be: 0 < warning < critical < incident <= 1.0. "
                f"Got: warning={self.warning_threshold}, critical={self.critical_threshold}, "
                f"incident={self.incident_threshold}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DriftThresholdConfig":
        """딕셔너리에서 생성."""
        valid_fields = {
            "warning_threshold",
            "critical_threshold",
            "incident_threshold",
            "alert_enabled",
            "incident_auto_create",
            "updated_at",
            "updated_by",
        }
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_env(cls) -> "DriftThresholdConfig":
        """환경 변수에서 생성."""
        import os

        return cls(
            warning_threshold=float(
                os.environ.get("SELFHEALING_DRIFT_WARNING_THRESHOLD", "0.05")
            ),
            critical_threshold=float(
                os.environ.get("SELFHEALING_DRIFT_CRITICAL_THRESHOLD", "0.20")
            ),
            incident_threshold=float(
                os.environ.get("SELFHEALING_DRIFT_INCIDENT_THRESHOLD", "0.50")
            ),
            alert_enabled=os.environ.get(
                "SELFHEALING_DRIFT_ALERT_ENABLED", "true"
            ).lower() == "true",
            incident_auto_create=os.environ.get(
                "SELFHEALING_DRIFT_INCIDENT_ENABLED", "true"
            ).lower() == "true",
        )

    def update(
        self,
        actor_id: Optional[str] = None,
        **kwargs: Any,
    ) -> "DriftThresholdConfig":
        """
        새로운 값으로 업데이트된 설정을 반환합니다.

        Args:
            actor_id: 업데이트를 수행한 사용자 ID
            **kwargs: 업데이트할 필드들

        Returns:
            업데이트된 새 DriftThresholdConfig 인스턴스
        """
        current = self.to_dict()
        current.update(kwargs)
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        current["updated_by"] = actor_id
        return self.from_dict(current)

    def get_threshold_percent_display(self) -> Dict[str, str]:
        """임계값을 퍼센트 문자열로 반환."""
        return {
            "warning": f"{self.warning_threshold * 100:.1f}%",
            "critical": f"{self.critical_threshold * 100:.1f}%",
            "incident": f"{self.incident_threshold * 100:.1f}%",
        }


__all__ = ["DriftThresholdConfig"]
