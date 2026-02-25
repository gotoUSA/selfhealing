"""
Latency Injection Experiment.

Injects artificial latency into service calls.
Simulates network delays, slow database queries, or congested services.
"""

from __future__ import annotations

from typing import Any

import structlog

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
    _get_current_chaos_config,
)
from selfhealing.services.chaos.experiments.hypothesis import (
    LATENCY_INJECTION_HYPOTHESIS,
)

logger = structlog.get_logger()


class LatencyInjectionExperiment(ChaosExperiment):
    """
    Inject artificial latency into service calls.

    Simulates network delays, slow database queries, or congested services.

    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • 500ms 지연 주입 시, CB가 10초 내에 OPEN되어야 함           │
    │ • 45초 내에 복구 완료                                        │
    │                                                              │
    │ LearningService 연동:                                        │
    │ → 실제 결과와 비교하여 "복구 성능 저하 추세" 자동 감지        │
    └─────────────────────────────────────────────────────────────┘

    Config parameters:
        - latency_ms: Amount of latency to inject (default: 500ms)
        - latency_jitter_ms: Random jitter range (default: 100ms)
    """

    experiment_type = ExperimentType.LATENCY_INJECTION.value
    requires_approval = False  # Low risk

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = LATENCY_INJECTION_HYPOTHESIS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_state: dict[str, Any] = {}

    @property
    def latency_ms(self) -> int:
        return self.config.parameters.get("latency_ms", 500)

    @property
    def latency_jitter_ms(self) -> int:
        return self.config.parameters.get("latency_jitter_ms", 100)

    def inject_chaos(self) -> bool:
        """Inject latency into target service with TTL."""
        logger.info(
            "latency_injection.injecting_ms_latency_rate",
            _self=self.latency_ms,
            latency_jitter_ms=self.latency_jitter_ms,
            target_service=self.config.target_service,
            injection_rate_pct=self.config.injection_rate * 100,
            effective_ttl=self._effective_ttl,
            expires_at=self._expires_at,
        )

        try:
            self._original_state = _get_current_chaos_config()

            _apply_chaos_config(
                {
                    "latency_injection": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "latency_ms": self.latency_ms,
                        "jitter_ms": self.latency_jitter_ms,
                        "rate": self.config.injection_rate,
                        "traffic_type": self.config.traffic_type,
                        "experiment_id": self.experiment_id,
                        "expires_at": (self._expires_at.isoformat() if self._expires_at else ""),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )

            return True

        except Exception as e:
            logger.exception(
                "latency_injection.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove latency injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    "latency_injection.rollback_already_completed",
                    _self=self.experiment_id,
                )
                return

            logger.info(
                "latency_injection.rolling_back",
                _self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "latency_injection": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "latency_injection.rollback_failed",
                    error=e,
                )


__all__ = ["LatencyInjectionExperiment"]
