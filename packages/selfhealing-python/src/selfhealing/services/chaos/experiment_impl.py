"""
Concrete Chaos Experiment Implementations

FACADE MODULE: This file now re-exports from the experiments package.
All implementations have been moved to selfhealing/services/chaos/experiments/

Contains specific experiment types:
- LatencyInjectionExperiment
- Error5xxExperiment
- PacketLossExperiment
- TimeoutExperiment
- ResourceExhaustionExperiment
- And many more...

For new development, import directly from experiments package:
    from selfhealing.services.chaos.experiments import (
        LatencyInjectionExperiment,
        create_experiment,
    )
"""

from __future__ import annotations

# Re-export everything from experiments package for backward compatibility
from selfhealing.services.chaos.experiments import (
    # Hypothesis
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
    # Experiments
    LatencyInjectionExperiment,
    Error5xxExperiment,
    Error4xxExperiment,
    PacketLossExperiment,
    ConnectionResetExperiment,
    NetworkBlackholeExperiment,
    ConnectionPartitionExperiment,
    TimeoutExperiment,
    ResourceExhaustionExperiment,
    PoolExhaustionExperiment,
    CircuitBreakerOpenExperiment,
    RateLimitExperiment,
    PartialFailureExperiment,
    CascadingFailureExperiment,
    CertificateExpiryExperiment,
    ClockSkewExperiment,
    DNSFailureExperiment,
    SimulatedDiskIOExperiment,
    SimulatedTLSFailureExperiment,
    AuditStorageFailureExperiment,
    ReplayFloodExperiment,
    # Factory
    create_experiment,
)


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
