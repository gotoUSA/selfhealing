"""
Chaos Experiment Library

Modular, reusable chaos experiment implementations.
Each experiment is designed for production-safe execution with:
- Kill Switch integration
- Audit trail recording
- Blast radius control
- Automatic rollback on failure

This module has been refactored for better maintainability:
- base.py: Base classes, data structures, enums
- experiment_impl.py: Concrete experiment implementations

All exports remain available from this module for backward compatibility.
"""

# Re-export everything from refactored modules for backward compatibility
from selfhealing.services.chaos.base import (
    # Protocols
    AuditRecorderProtocol,
    KillSwitchProtocol,
    # Enums
    ExperimentStatus,
    ExperimentType,
    TrafficType,
    # Data Classes
    ExperimentConfig,
    ExperimentResult,
    SteadyStateHypothesis,
    # Base Class
    ChaosExperiment,
)

from selfhealing.services.chaos.experiment_impl import (
    # Concrete Experiments
    LatencyInjectionExperiment,
    Error5xxExperiment,
    PacketLossExperiment,
    TimeoutExperiment,
    ResourceExhaustionExperiment,
    # Factory
    create_experiment,
)


__all__ = [
    # Protocols
    "AuditRecorderProtocol",
    "KillSwitchProtocol",
    # Enums
    "ExperimentStatus",
    "ExperimentType",
    "TrafficType",
    # Data Classes
    "ExperimentConfig",
    "ExperimentResult",
    "SteadyStateHypothesis",
    # Base Class
    "ChaosExperiment",
    # Concrete Experiments
    "LatencyInjectionExperiment",
    "Error5xxExperiment",
    "PacketLossExperiment",
    "TimeoutExperiment",
    "ResourceExhaustionExperiment",
    # Factory
    "create_experiment",
]
