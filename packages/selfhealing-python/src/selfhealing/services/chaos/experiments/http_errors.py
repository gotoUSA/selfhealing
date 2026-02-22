"""
HTTP Error Experiments (4xx, 5xx).

Injects HTTP error responses into service calls.
Simulates server errors, client errors, authentication failures, and rate limiting.
"""

from __future__ import annotations

import structlog

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)
from selfhealing.services.chaos.experiments.hypothesis import (
    ERROR_5XX_HYPOTHESIS,
)

logger = structlog.get_logger()


class Error5xxExperiment(ChaosExperiment):
    """
    Inject HTTP 5xx errors into service responses.

    Simulates server errors, service unavailability, or backend failures.

    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • 503 에러 주입 시, 5초 내에 CB OPEN                         │
    │ • 30초 내에 복구 완료                                        │
    │ • Fallback 활성화 필수                                       │
    │                                                              │
    │ LearningService 연동:                                        │
    │ → 실제 결과와 비교하여 "복구 성능 저하 추세" 자동 감지        │
    └─────────────────────────────────────────────────────────────┘

    Config parameters:
        - error_code: HTTP error code to inject (default: 503)
        - error_message: Error message (default: "Service Unavailable")
    """

    experiment_type = ExperimentType.ERROR_5XX.value
    requires_approval = False  # Medium risk

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = ERROR_5XX_HYPOTHESIS

    @property
    def error_code(self) -> int:
        return self.config.parameters.get("error_code", 503)

    @property
    def error_message(self) -> str:
        return self.config.parameters.get(
            "error_message", "Service Unavailable (Chaos Experiment)"
        )

    def inject_chaos(self) -> bool:
        """Inject 5xx errors into target service with TTL."""
        logger.info(
            "error5xx_injection.injecting_errors_rate_ttl",
            self=self.error_code,
            self_1=self.config.target_service,
            self_2=self.config.injection_rate*100,
            self_3=self._effective_ttl,
        )

        try:
            _apply_chaos_config(
                {
                    "error_injection": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "error_code": self.error_code,
                        "error_message": self.error_message,
                        "rate": self.config.injection_rate,
                        "traffic_type": self.config.traffic_type,
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
                "error5xx_injection.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove error injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    "error5xx_injection.rollback_already_completed",
                    self=self.experiment_id,
                )
                return

            logger.info(
                "error5xx_injection.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "error_injection": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "error5xx_injection.rollback_failed",
                    error=e,
                )


class Error4xxExperiment(ChaosExperiment):
    """
    Inject HTTP 4xx errors into service responses.

    Simulates client errors, authentication failures, rate limiting.
    Tests frontend error handling and throttle reaction.

    Config parameters:
        - error_code: HTTP error code to inject (default: 400)
        - error_codes: List of codes for random selection (optional)
        - error_message: Error message (default: auto-generated based on code)
    """

    experiment_type = ExperimentType.ERROR_4XX.value
    requires_approval = False  # Low risk

    # Mapping of error codes to messages
    ERROR_MESSAGES = {
        400: "Bad Request (Chaos Experiment)",
        401: "Unauthorized (Chaos Experiment)",
        403: "Forbidden (Chaos Experiment)",
        404: "Not Found (Chaos Experiment)",
        429: "Too Many Requests (Chaos Experiment)",
    }

    @property
    def error_code(self) -> int:
        """Get error code, optionally random from list."""
        codes = self.config.parameters.get("error_codes")
        if codes:
            import random

            return random.choice(codes)
        return self.config.parameters.get("error_code", 400)

    @property
    def error_message(self) -> str:
        """Get error message for the error code."""
        return self.config.parameters.get(
            "error_message",
            self.ERROR_MESSAGES.get(self.error_code, "Client Error (Chaos Experiment)"),
        )

    def inject_chaos(self) -> bool:
        """Inject 4xx errors into target service with TTL."""
        logger.info(
            "error4xx_injection.injecting_errors_rate_ttl",
            self=self.error_code,
            self_1=self.config.target_service,
            self_2=self.config.injection_rate*100,
            self_3=self._effective_ttl,
        )

        try:
            _apply_chaos_config(
                {
                    "error_4xx_injection": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "error_code": self.error_code,
                        "error_message": self.error_message,
                        "rate": self.config.injection_rate,
                        "traffic_type": self.config.traffic_type,
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
                "error4xx_injection.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Remove 4xx error injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    "error4xx_injection.rollback_already_completed",
                    self=self.experiment_id,
                )
                return

            logger.info(
                "error4xx_injection.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "error_4xx_injection": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "error4xx_injection.rollback_failed",
                    error=e,
                )


__all__ = ["Error5xxExperiment", "Error4xxExperiment"]
