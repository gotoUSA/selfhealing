"""
Circuit Breaker Open Experiment.

Forces Circuit Breaker to OPEN state.
Tests fast-fail behavior, fallback strategies, and canary recovery.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from selfhealing.core.timezone import now
from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)
from selfhealing.services.chaos.experiments.hypothesis import (
    CB_OPEN_HYPOTHESIS,
)

logger = structlog.get_logger()


class CircuitBreakerOpenExperiment(ChaosExperiment):
    """
    Force Circuit Breaker to OPEN state.

    Tests fast-fail behavior, fallback strategies, and canary recovery.

    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • CB Open 후 30초 내에 Canary Stage 1 시작                   │
    │ • 60초 내에 복구 완료 (HALF_OPEN 전환)                        │
    │ • Fallback(cache) 활성화 필수                                │
    │                                                              │
    │ LearningService 연동:                                        │
    │ → 실제 결과와 비교하여 "복구 성능 저하 추세" 자동 감지        │
    └─────────────────────────────────────────────────────────────┘

    Config parameters:
        - trigger_canary: Whether to wait for canary recovery (default: True)
        - fallback_type: Expected fallback type (cache, dlq, default)
    """

    experiment_type = ExperimentType.CIRCUIT_BREAKER_OPEN.value
    requires_approval = True  # High risk - blocks real traffic

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = CB_OPEN_HYPOTHESIS

    @property
    def trigger_canary(self) -> bool:
        return self.config.parameters.get("trigger_canary", True)

    @property
    def fallback_type(self) -> str:
        return self.config.parameters.get("fallback_type", "default")

    def inject_chaos(self) -> bool:
        """Force CB to OPEN state."""
        logger.info(
            "cb_open_injection.forcing_cb_open_ttl",
            _self=self.config.target_service,
            self_1=self._effective_ttl,
        )

        try:
            from selfhealing.services.circuit_breaker import (
                get_circuit_breaker_service,
            )

            # CB 강제 OPEN
            cb_service = get_circuit_breaker_service()
            result = cb_service.force_open(
                service_name=self.config.target_service,
                reason=f"Chaos Experiment: {self.experiment_id}",
                controlled_by="chaos_engine",
            )

            if not result.success:
                logger.error(
                    "cb_open_injection.failed_open_cb",
                    result=result.message,
                )
                return False

            # 설정 저장 (TTL 및 rollback용)
            _apply_chaos_config(
                {
                    "circuit_breaker_open": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "trigger_canary": self.trigger_canary,
                        "experiment_id": self.experiment_id,
                        "expires_at": (self._expires_at.isoformat() if self._expires_at else ""),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )
            return True
        except Exception as e:
            logger.exception(
                "cb_open_injection.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Force CB back to CLOSED state."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    "cb_open_injection.rollback_already_completed",
                    _self=self.experiment_id,
                )
                return

            logger.info(
                "cb_open_injection.rolling_back",
                _self=self.experiment_id,
            )

            try:
                from selfhealing.services.circuit_breaker import (
                    get_circuit_breaker_service,
                )

                cb_service = get_circuit_breaker_service()
                cb_service.force_close(
                    service_name=self.config.target_service,
                    reason=f"Chaos Experiment Rollback: {self.experiment_id}",
                    controlled_by="chaos_engine",
                    trigger_replay=False,  # 실험이므로 리플레이 안 함
                )

                _apply_chaos_config(
                    {
                        "circuit_breaker_open": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.exception(
                    "cb_open_injection.rollback_failed",
                    error=e,
                )

    # =========================================================================
    # Canary Recovery 검증
    # =========================================================================

    def _verify_canary_recovery(self) -> dict[str, Any]:
        """
        Canary 복구 단계 검증.

        Returns:
            Dict with:
                - canary_state: 현재 Canary 상태 (e.g., "canary_1")
                - traffic_percent: 현재 트래픽 비율 (%)
                - in_canary: Canary 복구 진행 중 여부
                - success_rate: 성공률 (%)
                - stage_started_at: 현재 단계 시작 시간
        """
        try:
            from selfhealing.services.circuit_breaker.canary_recovery import (
                CanaryRecoveryStage,
                get_canary_recovery_manager,
            )

            manager = get_canary_recovery_manager()
            target = self.config.target_service

            state = manager.get_recovery_state(target)

            if state is None:
                return {
                    "canary_state": CanaryRecoveryStage.NOT_IN_CANARY.value,
                    "traffic_percent": 100.0,
                    "in_canary": False,
                    "success_rate": None,
                    "stage_started_at": None,
                }

            return {
                "canary_state": state.current_stage.value,
                "traffic_percent": state.traffic_percent,
                "in_canary": state.is_in_canary(),
                "success_rate": state.success_rate,
                "stage_started_at": (state.stage_started_at.isoformat() if state.stage_started_at else None),
            }
        except Exception as e:
            logger.warning(
                "cb_open_experiment.canary_verification_failed",
                error=e,
            )
            return {
                "canary_state": "unknown",
                "traffic_percent": None,
                "in_canary": None,
                "success_rate": None,
                "error": str(e),
            }

    def _check_canary_started(self, timeout_seconds: float = 30.0) -> bool:
        """
        Canary 복구 시작 확인 (비동기 폴링용).

        Args:
            timeout_seconds: 대기 시간 (초)

        Returns:
            True if canary started within timeout
        """
        start_time = now()

        while (now() - start_time).total_seconds() < timeout_seconds:
            status = self._verify_canary_recovery()

            if status.get("in_canary", False):
                logger.info(
                    "cb_open_experiment.canary_recovery_started",
                    status=status.get('canary_state'),
                    status_1=status.get('traffic_percent'),
                )
                return True

            time.sleep(1.0)  # 1초마다 체크

        logger.warning(
            "cb_open_experiment.canary_recovery_start_within",
            timeout_seconds=timeout_seconds,
        )
        return False

    def get_canary_verification_result(self) -> dict[str, Any]:
        """
        실험 결과에 포함할 Canary 검증 결과.

        Returns:
            Dict containing canary recovery verification data
        """
        canary_status = self._verify_canary_recovery()

        # FailureHypothesis 검증
        if hasattr(self, "failure_hypothesis") and self.failure_hypothesis:
            actual_canary_stage = canary_status.get("canary_state")
            hypothesis_canary_match = actual_canary_stage == self.failure_hypothesis.expected_canary_stage
        else:
            hypothesis_canary_match = None

        return {
            "canary_status": canary_status,
            "hypothesis_canary_match": hypothesis_canary_match,
            "expected_canary_stage": (
                self.failure_hypothesis.expected_canary_stage
                if hasattr(self, "failure_hypothesis") and self.failure_hypothesis
                else None
            ),
        }


__all__ = ["CircuitBreakerOpenExperiment"]
