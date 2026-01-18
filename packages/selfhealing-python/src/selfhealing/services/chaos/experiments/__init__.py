"""
Experiments Package.

This package contains modular chaos experiment implementations.
All experiments are re-exported here for backward compatibility.
"""

from __future__ import annotations

# Hypothesis dataclass and constants
from selfhealing.services.chaos.experiments.hypothesis import (
    FailureHypothesis,
    LATENCY_SPIKE_HYPOTHESIS,
    SERVER_ERROR_HYPOTHESIS,
    CLIENT_ERROR_HYPOTHESIS,
    PACKET_LOSS_HYPOTHESIS,
    TIMEOUT_HYPOTHESIS,
    RESOURCE_EXHAUSTION_HYPOTHESIS,
    CIRCUIT_OPEN_HYPOTHESIS,
    RATE_LIMIT_HYPOTHESIS,
    PARTIAL_FAILURE_HYPOTHESIS,
    CONNECTION_RESET_HYPOTHESIS,
    CASCADING_FAILURE_HYPOTHESIS,
    POOL_EXHAUSTION_HYPOTHESIS,
    CONNECTION_PARTITION_HYPOTHESIS,
)

# Latency experiments
from selfhealing.services.chaos.experiments.latency import (
    LatencyInjectionExperiment,
)

# HTTP error experiments
from selfhealing.services.chaos.experiments.http_errors import (
    Error5xxExperiment,
    Error4xxExperiment,
)

# Network experiments
from selfhealing.services.chaos.experiments.network import (
    PacketLossExperiment,
    ConnectionResetExperiment,
    NetworkBlackholeExperiment,
    ConnectionPartitionExperiment,
)

# Timeout experiments
from selfhealing.services.chaos.experiments.timeout import (
    TimeoutExperiment,
)

# Resource experiments
from selfhealing.services.chaos.experiments.resource import (
    ResourceExhaustionExperiment,
    PoolExhaustionExperiment,
)

# Circuit breaker experiments
from selfhealing.services.chaos.experiments.circuit_breaker import (
    CircuitBreakerOpenExperiment,
)

# Rate limiting experiments
from selfhealing.services.chaos.experiments.rate_limit import (
    RateLimitExperiment,
)

# Cascade failure experiments
from selfhealing.services.chaos.experiments.cascade import (
    PartialFailureExperiment,
    CascadingFailureExperiment,
)

# Infrastructure failure experiments
from selfhealing.services.chaos.experiments.infrastructure import (
    CertificateExpiryExperiment,
    ClockSkewExperiment,
    DNSFailureExperiment,
    SimulatedDiskIOExperiment,
    SimulatedTLSFailureExperiment,
)

# Audit system experiments
from selfhealing.services.chaos.experiments.audit import (
    AuditStorageFailureExperiment,
    ReplayFloodExperiment,
)

# Factory function
from selfhealing.services.chaos.base import ExperimentType


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
    return experiment_class(config, experiment_id)


__all__ = [
    # Hypothesis
    "FailureHypothesis",
    "LATENCY_SPIKE_HYPOTHESIS",
    "SERVER_ERROR_HYPOTHESIS",
    "CLIENT_ERROR_HYPOTHESIS",
    "PACKET_LOSS_HYPOTHESIS",
    "TIMEOUT_HYPOTHESIS",
    "RESOURCE_EXHAUSTION_HYPOTHESIS",
    "CIRCUIT_OPEN_HYPOTHESIS",
    "RATE_LIMIT_HYPOTHESIS",
    "PARTIAL_FAILURE_HYPOTHESIS",
    "CONNECTION_RESET_HYPOTHESIS",
    "CASCADING_FAILURE_HYPOTHESIS",
    "POOL_EXHAUSTION_HYPOTHESIS",
    "CONNECTION_PARTITION_HYPOTHESIS",
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
]
