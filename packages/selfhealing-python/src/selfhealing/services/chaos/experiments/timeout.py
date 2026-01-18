"""
Timeout Experiment.

Injects request timeouts to simulate services that hang without responding.
"""

from __future__ import annotations

import logging

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)


logger = logging.getLogger(__name__)


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


__all__ = ["TimeoutExperiment"]
