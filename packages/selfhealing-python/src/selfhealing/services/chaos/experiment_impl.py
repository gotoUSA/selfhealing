"""
Concrete Chaos Experiment Implementations

Contains specific experiment types:
- LatencyInjectionExperiment
- Error5xxExperiment
- PacketLossExperiment
- TimeoutExperiment
- ResourceExhaustionExperiment

Reference: Netflix ChAP, Gremlin, AWS FIS patterns
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentConfig,
    ExperimentType,
    SteadyStateHypothesis,
    _apply_chaos_config,
    _get_current_chaos_config,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Latency Injection Experiment
# =============================================================================


class LatencyInjectionExperiment(ChaosExperiment):
    """
    Inject artificial latency into service calls.
    
    Simulates network delays, slow database queries, or congested services.
    
    Config parameters:
        - latency_ms: Amount of latency to inject (default: 500ms)
        - latency_jitter_ms: Random jitter range (default: 100ms)
    """
    
    experiment_type = ExperimentType.LATENCY_INJECTION.value
    requires_approval = False  # Low risk
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_state: Dict[str, Any] = {}
    
    @property
    def latency_ms(self) -> int:
        return self.config.parameters.get("latency_ms", 500)
    
    @property
    def latency_jitter_ms(self) -> int:
        return self.config.parameters.get("latency_jitter_ms", 100)
    
    def inject_chaos(self) -> bool:
        """Inject latency into target service with TTL."""
        logger.info(
            f"[LatencyInjection] Injecting {self.latency_ms}±{self.latency_jitter_ms}ms "
            f"latency to {self.config.target_service} at {self.config.injection_rate*100}% rate "
            f"(TTL: {self._effective_ttl}s, expires: {self._expires_at})"
        )
        
        try:
            self._original_state = _get_current_chaos_config()
            
            _apply_chaos_config({
                "latency_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "latency_ms": self.latency_ms,
                    "jitter_ms": self.latency_jitter_ms,
                    "rate": self.config.injection_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            
            return True
            
        except Exception as e:
            logger.error(f"[LatencyInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove latency injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[LatencyInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[LatencyInjection] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "latency_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[LatencyInjection] Rollback failed: {e}")


# =============================================================================
# Error 5xx Experiment
# =============================================================================


class Error5xxExperiment(ChaosExperiment):
    """
    Inject HTTP 5xx errors into service responses.
    
    Simulates server errors, service unavailability, or backend failures.
    
    Config parameters:
        - error_code: HTTP error code to inject (default: 503)
        - error_message: Error message (default: "Service Unavailable")
    """
    
    experiment_type = ExperimentType.ERROR_5XX.value
    requires_approval = False  # Medium risk
    
    @property
    def error_code(self) -> int:
        return self.config.parameters.get("error_code", 503)
    
    @property
    def error_message(self) -> str:
        return self.config.parameters.get("error_message", "Service Unavailable (Chaos Experiment)")
    
    def inject_chaos(self) -> bool:
        """Inject 5xx errors into target service with TTL."""
        logger.info(
            f"[Error5xxInjection] Injecting {self.error_code} errors "
            f"to {self.config.target_service} at {self.config.injection_rate*100}% rate "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "error_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "error_code": self.error_code,
                    "error_message": self.error_message,
                    "rate": self.config.injection_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[Error5xxInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove error injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[Error5xxInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[Error5xxInjection] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "error_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Error5xxInjection] Rollback failed: {e}")


# =============================================================================
# Packet Loss Experiment
# =============================================================================


class PacketLossExperiment(ChaosExperiment):
    """
    Simulate network packet loss.
    
    Simulates unreliable network conditions, dropped connections.
    
    Config parameters:
        - loss_rate: Percentage of packets to drop (default: 5%)
    """
    
    experiment_type = ExperimentType.PACKET_LOSS.value
    requires_approval = True  # Higher risk
    
    @property
    def loss_rate(self) -> float:
        return self.config.parameters.get("loss_rate", 0.05)  # 5%
    
    def inject_chaos(self) -> bool:
        """Inject packet loss with TTL."""
        logger.info(
            f"[PacketLoss] Injecting {self.loss_rate*100}% packet loss "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "packet_loss": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "loss_rate": self.loss_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[PacketLoss] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove packet loss injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[PacketLoss] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[PacketLoss] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "packet_loss": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PacketLoss] Rollback failed: {e}")


# =============================================================================
# Timeout Experiment
# =============================================================================


class TimeoutExperiment(ChaosExperiment):
    """
    Inject request timeouts.
    
    Simulates services that hang without responding.
    
    Config parameters:
        - timeout_delay_seconds: How long to delay before timeout (default: 30)
    """
    
    experiment_type = ExperimentType.TIMEOUT.value
    requires_approval = False
    
    @property
    def timeout_delay_seconds(self) -> int:
        return self.config.parameters.get("timeout_delay_seconds", 30)
    
    def inject_chaos(self) -> bool:
        """Inject timeout delays with TTL."""
        logger.info(
            f"[Timeout] Injecting {self.timeout_delay_seconds}s timeout delays "
            f"to {self.config.target_service} at {self.config.injection_rate*100}% rate "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "timeout_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "delay_seconds": self.timeout_delay_seconds,
                    "rate": self.config.injection_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[Timeout] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove timeout injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[Timeout] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[Timeout] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "timeout_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Timeout] Rollback failed: {e}")


# =============================================================================
# Resource Exhaustion Experiment
# =============================================================================


class ResourceExhaustionExperiment(ChaosExperiment):
    """
    Simulate resource exhaustion conditions.
    
    Simulates CPU spike, memory pressure, connection pool exhaustion.
    
    Config parameters:
        - resource_type: "cpu", "memory", "connections" (default: "connections")
        - exhaustion_percent: Percentage of resource to consume (default: 80%)
    """
    
    experiment_type = ExperimentType.RESOURCE_EXHAUSTION.value
    requires_approval = True  # High risk
    
    @property
    def resource_type(self) -> str:
        return self.config.parameters.get("resource_type", "connections")
    
    @property
    def exhaustion_percent(self) -> float:
        return self.config.parameters.get("exhaustion_percent", 0.80)  # 80%
    
    def inject_chaos(self) -> bool:
        """Inject resource exhaustion with TTL."""
        logger.info(
            f"[ResourceExhaustion] Exhausting {self.resource_type} to "
            f"{self.exhaustion_percent*100}% on {self.config.target_service} "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "resource_exhaustion": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "resource_type": self.resource_type,
                    "exhaustion_percent": self.exhaustion_percent,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ResourceExhaustion] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Release exhausted resources with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[ResourceExhaustion] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[ResourceExhaustion] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "resource_exhaustion": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ResourceExhaustion] Rollback failed: {e}")


# =============================================================================
# Experiment Factory
# =============================================================================


def create_experiment(
    experiment_type: str,
    config: Optional[ExperimentConfig] = None,
    hypothesis: Optional[SteadyStateHypothesis] = None,
) -> ChaosExperiment:
    """
    Factory function to create experiment instances.
    
    Args:
        experiment_type: Type of experiment to create
        config: Experiment configuration
        hypothesis: Steady state hypothesis
        
    Returns:
        ChaosExperiment instance
    """
    experiment_classes = {
        ExperimentType.LATENCY_INJECTION.value: LatencyInjectionExperiment,
        ExperimentType.ERROR_5XX.value: Error5xxExperiment,
        ExperimentType.PACKET_LOSS.value: PacketLossExperiment,
        ExperimentType.TIMEOUT.value: TimeoutExperiment,
        ExperimentType.RESOURCE_EXHAUSTION.value: ResourceExhaustionExperiment,
    }
    
    experiment_class = experiment_classes.get(experiment_type)
    if not experiment_class:
        raise ValueError(f"Unknown experiment type: {experiment_type}")
    
    return experiment_class(config=config, hypothesis=hypothesis)


# =============================================================================
# Backward Compatibility Re-exports
# =============================================================================

# Re-export from base for backward compatibility
from selfhealing.services.chaos.base import (
    AuditRecorderProtocol,
    KillSwitchProtocol,
    ExperimentStatus,
    ExperimentType,
    TrafficType,
    ExperimentConfig,
    ExperimentResult,
    SteadyStateHypothesis,
    ChaosExperiment,
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
