"""
Rate Limit Experiment.

Injects rate limit (429) responses to trigger CB auto-open.
Tests rate limit cascade detection and self-DDoS protection.
"""

from __future__ import annotations

import logging

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)


logger = logging.getLogger(__name__)


class RateLimitExperiment(ChaosExperiment):
    """
    Inject rate limit (429) responses to trigger CB auto-open.
    
    Tests rate limit cascade detection and self-DDoS protection.
    
    Config parameters:
        - rate_limit_count: Number of 429s to inject (default: 10)
        - window_seconds: Time window for injection (default: 60)
        - retry_after_seconds: Retry-After header value (default: 30)
    """
    
    experiment_type = ExperimentType.RATE_LIMIT.value
    requires_approval = False  # Medium risk
    
    @property
    def rate_limit_count(self) -> int:
        return self.config.parameters.get("rate_limit_count", 10)
    
    @property
    def retry_after_seconds(self) -> int:
        return self.config.parameters.get("retry_after_seconds", 30)
    
    def inject_chaos(self) -> bool:
        """Inject rate limit responses to trigger CB cascade."""
        logger.info(
            f"[RateLimitInjection] Injecting {self.rate_limit_count} rate limits "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )
        
        try:
            # Rate Limit Tracker에 직접 기록 (CB 자동 OPEN 유발)
            from selfhealing.services.circuit_breaker import (
                get_rate_limit_tracker,
            )
            
            tracker = get_rate_limit_tracker()
            for _ in range(self.rate_limit_count):
                tracker.record_rate_limit(
                    service_name=self.config.target_service,
                    retry_after=self.retry_after_seconds,
                )
            
            # 설정 저장 (TTL용)
            _apply_chaos_config({
                "rate_limit_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "count": self.rate_limit_count,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[RateLimitInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Clear rate limit injection state."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[RateLimitInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[RateLimitInjection] Rolling back {self.experiment_id}")
            
            try:
                # Rate Limit Tracker 리셋은 어려우므로 설정만 해제
                _apply_chaos_config({
                    "rate_limit_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[RateLimitInjection] Rollback failed: {e}")


__all__ = ["RateLimitExperiment"]
