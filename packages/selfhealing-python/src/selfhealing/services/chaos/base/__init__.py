"""
Chaos Experiment Base Package.

Contains core abstractions for chaos experiments:
- Protocols (AuditRecorderProtocol, KillSwitchProtocol)
- Enums (ExperimentStatus, ExperimentType, TrafficType)
- Data classes (ExperimentConfig, ExperimentResult, SteadyStateHypothesis)
- TTL Helper (MonotonicTTLHelper)
- Base class (ChaosExperiment)
- Utils (_apply_chaos_config, _get_current_chaos_config)

Usage:
    from selfhealing.services.chaos.base import (
        ChaosExperiment,
        ExperimentConfig,
        ExperimentResult,
        ExperimentStatus,
        ExperimentType,
    )

Note: experiment.py contains the full original base.py content including
ChaosExperiment class. Other files (protocols.py, enums.py, models.py,
ttl_helper.py, utils.py) contain extracted components for modular access.
"""

# Import from experiment.py (original base.py content)
from .experiment import (
    # Protocols
    AuditRecorderProtocol,
    KillSwitchProtocol,
    # Enums
    ExperimentStatus,
    ExperimentType,
    TrafficType,
    # Models
    ExperimentConfig,
    ExperimentResult,
    SteadyStateHypothesis,
    # TTL Helper
    MonotonicTTLHelper,
    # Base Class
    ChaosExperiment,
    # Utils
    _apply_chaos_config,
    _get_current_chaos_config,
)


__all__ = [
    # Protocols
    "AuditRecorderProtocol",
    "KillSwitchProtocol",
    # Enums
    "ExperimentStatus",
    "ExperimentType",
    "TrafficType",
    # Models
    "ExperimentConfig",
    "ExperimentResult",
    "SteadyStateHypothesis",
    # TTL Helper
    "MonotonicTTLHelper",
    # Base Class
    "ChaosExperiment",
    # Utils
    "_apply_chaos_config",
    "_get_current_chaos_config",
]
