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

Structure:
- protocols.py: AuditRecorderProtocol, KillSwitchProtocol
- enums.py: ExperimentStatus, ExperimentType, TrafficType
- models.py: ExperimentConfig, ExperimentResult, SteadyStateHypothesis
- ttl_helper.py: MonotonicTTLHelper
- experiment.py: ChaosExperiment (abstract base class)
- utils.py: _apply_chaos_config, _get_current_chaos_config
"""

from .enums import ExperimentStatus, ExperimentType, TrafficType
from .experiment import ChaosExperiment
from .models import ExperimentConfig, ExperimentResult, SteadyStateHypothesis

# Import from separate modules
from .protocols import AuditRecorderProtocol, KillSwitchProtocol
from .ttl_helper import MonotonicTTLHelper
from .utils import _apply_chaos_config, _get_current_chaos_config

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
