"""
Corruption Shield Configuration.

92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [14] CorruptionShieldSettings 참조.
91_CONFIG_INVENTORY.md §9.5 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selfhealing.settings import CorruptionShieldSettings, get_layered_settings


def _get_corruption_shield_defaults() -> CorruptionShieldSettings:
    """LayeredSettings에서 Corruption Shield 기본값 가져오기."""
    return get_layered_settings(CorruptionShieldSettings, "corruption_shield")


@dataclass
class CorruptionShieldConfig:
    """Configuration for Corruption Shield."""

    # Enable/disable layers
    l1_enabled: bool = True  # Schema validation
    l2_enabled: bool = True  # Business rules
    l3_enabled: bool = True  # Anomaly detection

    # L1: Schema validation
    required_fields: list[str] = field(default_factory=lambda: ["amount", "order_id"])
    max_string_length: int = 1000

    # L2: Business rules
    min_amount: int = 100  # Won
    max_amount: int = 100_000_000  # 1억 Won
    allowed_statuses: set[str] = field(
        default_factory=lambda: {"DONE", "CANCELED", "PENDING"}
    )

    # L3: Anomaly detection
    z_score_threshold: float = 3.0  # Standard deviations
    iqr_multiplier: float = 1.5  # IQR outlier multiplier
    min_samples_for_anomaly: int = 10  # Need at least this many samples

    # Logging
    log_violations: bool = True
    log_to_security_incident: bool = True

    @classmethod
    def from_settings(cls) -> CorruptionShieldConfig:
        """
        LayeredSettings에서 설정 로드.

        92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [14] 참조.

        Returns:
            Settings 기반 CorruptionShieldConfig
        """
        settings = _get_corruption_shield_defaults()

        return cls(
            l1_enabled=settings.l1_enabled,
            l2_enabled=settings.l2_enabled,
            l3_enabled=settings.l3_enabled,
            required_fields=settings.required_fields,
            max_string_length=settings.max_string_length,
            min_amount=settings.min_amount,
            max_amount=settings.max_amount,
            allowed_statuses=set(settings.allowed_statuses),
            z_score_threshold=settings.z_score_threshold,
            iqr_multiplier=settings.iqr_multiplier,
            min_samples_for_anomaly=settings.min_samples_for_anomaly,
            log_violations=settings.log_violations,
            log_to_security_incident=settings.log_to_security_incident,
        )

    @classmethod
    def from_dict(cls, data: dict) -> CorruptionShieldConfig:
        """Create config from dictionary."""
        return cls(
            l1_enabled=data.get("l1_enabled", True),
            l2_enabled=data.get("l2_enabled", True),
            l3_enabled=data.get("l3_enabled", True),
            required_fields=data.get("required_fields", ["amount", "order_id"]),
            max_string_length=data.get("max_string_length", 1000),
            min_amount=data.get("min_amount", 100),
            max_amount=data.get("max_amount", 100_000_000),
            allowed_statuses=set(
                data.get("allowed_statuses", ["DONE", "CANCELED", "PENDING"])
            ),
            z_score_threshold=data.get("z_score_threshold", 3.0),
            iqr_multiplier=data.get("iqr_multiplier", 1.5),
            min_samples_for_anomaly=data.get("min_samples_for_anomaly", 10),
            log_violations=data.get("log_violations", True),
            log_to_security_incident=data.get("log_to_security_incident", True),
        )
