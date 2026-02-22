"""
Rate Limit Experiment.

Injects rate limit (429) responses to trigger CB auto-open.
Tests rate limit cascade detection and self-DDoS protection.
"""

from __future__ import annotations

import structlog

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)

logger = structlog.get_logger()


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
            "rate_limit_injection.injecting_rate_limits_ttl",
            _self=self.rate_limit_count,
            self_1=self.config.target_service,
            self_2=self._effective_ttl,
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
            _apply_chaos_config(
                {
                    "rate_limit_injection": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "count": self.rate_limit_count,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "rate_limit_injection.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Clear rate limit injection state."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    "rate_limit_injection.rollback_already_completed",
                    _self=self.experiment_id,
                )
                return

            logger.info(
                "rate_limit_injection.rolling_back",
                _self=self.experiment_id,
            )

            try:
                # Rate Limit Tracker 리셋은 어려우므로 설정만 해제
                _apply_chaos_config(
                    {
                        "rate_limit_injection": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "rate_limit_injection.rollback_failed",
                    error=e,
                )


__all__ = ["RateLimitExperiment"]
