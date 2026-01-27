"""
Experiments Package.

This package contains modular chaos experiment implementations.
All experiments are re-exported here for backward compatibility.
"""

from __future__ import annotations

# Factory function and core types from base
from selfhealing.services.chaos.base import (
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    ExperimentType,
    _apply_chaos_config,
)

# Audit system experiments
from selfhealing.services.chaos.experiments.audit import (
    AuditStorageFailureExperiment,
    ReplayFloodExperiment,
)

# Cascade failure experiments
from selfhealing.services.chaos.experiments.cascade import (
    CascadingFailureExperiment,
    PartialFailureExperiment,
)

# Circuit breaker experiments
from selfhealing.services.chaos.experiments.circuit_breaker import (
    CircuitBreakerOpenExperiment,
)

# HTTP error experiments
from selfhealing.services.chaos.experiments.http_errors import (
    Error4xxExperiment,
    Error5xxExperiment,
)

# Hypothesis dataclass and constants
from selfhealing.services.chaos.experiments.hypothesis import (  # Core experiment hypotheses; Additional hypotheses
    AUDIT_STORAGE_FAILURE_HYPOTHESIS,
    CB_OPEN_HYPOTHESIS,
    CERTIFICATE_EXPIRY_HYPOTHESIS,
    CLOCK_SKEW_HYPOTHESIS,
    CONNECTION_PARTITION_HYPOTHESIS,
    DNS_FAILURE_HYPOTHESIS,
    ERROR_5XX_HYPOTHESIS,
    LATENCY_INJECTION_HYPOTHESIS,
    NETWORK_BLACKHOLE_HYPOTHESIS,
    POOL_EXHAUSTION_HYPOTHESIS,
    REPLAY_FLOOD_HYPOTHESIS,
    SIMULATED_DISK_IO_HYPOTHESIS,
    SIMULATED_TLS_FAILURE_HYPOTHESIS,
    FailureHypothesis,
)

# Infrastructure failure experiments
from selfhealing.services.chaos.experiments.infrastructure import (
    CertificateExpiryExperiment,
    ClockSkewExperiment,
    DNSFailureExperiment,
    SimulatedDiskIOExperiment,
    SimulatedTLSFailureExperiment,
)

# Latency experiments
from selfhealing.services.chaos.experiments.latency import (
    LatencyInjectionExperiment,
)

# Network experiments
from selfhealing.services.chaos.experiments.network import (
    ConnectionPartitionExperiment,
    ConnectionResetExperiment,
    NetworkBlackholeExperiment,
    PacketLossExperiment,
)

# Rate limiting experiments
from selfhealing.services.chaos.experiments.rate_limit import (
    RateLimitExperiment,
)

# Resource experiments
from selfhealing.services.chaos.experiments.resource import (
    PoolExhaustionExperiment,
    ResourceExhaustionExperiment,
)

# Timeout experiments
from selfhealing.services.chaos.experiments.timeout import (
    TimeoutExperiment,
)


def create_experiment(experiment_type: str, config, experiment_id: str = None):
    """
    Factory function to create experiment instances.

    Args:
        experiment_type: Type of experiment (from ExperimentType enum)
        config: ExperimentConfig instance
        experiment_id: Optional experiment ID (auto-generated if not provided)

    Returns:
        ChaosExperiment instance

    Raises:
        ValueError: If experiment_type is not supported
    """
    experiment_classes = {
        ExperimentType.LATENCY_INJECTION.value: LatencyInjectionExperiment,
        ExperimentType.ERROR_5XX.value: Error5xxExperiment,
        ExperimentType.ERROR_4XX.value: Error4xxExperiment,
        ExperimentType.PACKET_LOSS.value: PacketLossExperiment,
        ExperimentType.TIMEOUT.value: TimeoutExperiment,
        ExperimentType.RESOURCE_EXHAUSTION.value: ResourceExhaustionExperiment,
        ExperimentType.CIRCUIT_BREAKER_OPEN.value: CircuitBreakerOpenExperiment,
        ExperimentType.RATE_LIMIT.value: RateLimitExperiment,
        ExperimentType.PARTIAL_FAILURE.value: PartialFailureExperiment,
        ExperimentType.CONNECTION_RESET.value: ConnectionResetExperiment,
        ExperimentType.CASCADING_FAILURE.value: CascadingFailureExperiment,
        ExperimentType.POOL_EXHAUSTION.value: PoolExhaustionExperiment,
        ExperimentType.CONNECTION_PARTITION.value: ConnectionPartitionExperiment,
        ExperimentType.CERTIFICATE_EXPIRY.value: CertificateExpiryExperiment,
        ExperimentType.CLOCK_SKEW.value: ClockSkewExperiment,
        ExperimentType.DNS_FAILURE.value: DNSFailureExperiment,
        ExperimentType.NETWORK_BLACKHOLE.value: NetworkBlackholeExperiment,
        ExperimentType.SIMULATED_DISK_IO.value: SimulatedDiskIOExperiment,
        ExperimentType.SIMULATED_TLS_FAILURE.value: SimulatedTLSFailureExperiment,
        ExperimentType.AUDIT_STORAGE_FAILURE.value: AuditStorageFailureExperiment,
        ExperimentType.REPLAY_FLOOD.value: ReplayFloodExperiment,
    }

    if experiment_type not in experiment_classes:
        raise ValueError(f"Unsupported experiment type: {experiment_type}")

    experiment_class = experiment_classes[experiment_type]
    return experiment_class(experiment_id=experiment_id, config=config)


__all__ = [
    # Hypothesis
    "FailureHypothesis",
    "CB_OPEN_HYPOTHESIS",
    "LATENCY_INJECTION_HYPOTHESIS",
    "ERROR_5XX_HYPOTHESIS",
    "POOL_EXHAUSTION_HYPOTHESIS",
    "CONNECTION_PARTITION_HYPOTHESIS",
    "CERTIFICATE_EXPIRY_HYPOTHESIS",
    "CLOCK_SKEW_HYPOTHESIS",
    "DNS_FAILURE_HYPOTHESIS",
    "NETWORK_BLACKHOLE_HYPOTHESIS",
    "SIMULATED_DISK_IO_HYPOTHESIS",
    "SIMULATED_TLS_FAILURE_HYPOTHESIS",
    "AUDIT_STORAGE_FAILURE_HYPOTHESIS",
    "REPLAY_FLOOD_HYPOTHESIS",
    # Experiments
    "LatencyInjectionExperiment",
    "Error5xxExperiment",
    "Error4xxExperiment",
    "PacketLossExperiment",
    "ConnectionResetExperiment",
    "NetworkBlackholeExperiment",
    "ConnectionPartitionExperiment",
    "TimeoutExperiment",
    "ResourceExhaustionExperiment",
    "PoolExhaustionExperiment",
    "CircuitBreakerOpenExperiment",
    "RateLimitExperiment",
    "PartialFailureExperiment",
    "CascadingFailureExperiment",
    "CertificateExpiryExperiment",
    "ClockSkewExperiment",
    "DNSFailureExperiment",
    "SimulatedDiskIOExperiment",
    "SimulatedTLSFailureExperiment",
    "AuditStorageFailureExperiment",
    "ReplayFloodExperiment",
    # Factory
    "create_experiment",
    # Config and utils (for tests)
    "ExperimentConfig",
    "_apply_chaos_config",
]
