"""
Cascade Failure Experiments.

Includes:
- CascadingFailureExperiment: Simulate cascading failures across multiple services
- PartialFailureExperiment: Inject partial failures to test graceful degradation
"""

from __future__ import annotations

import logging
import time
from typing import Any

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)

logger = logging.getLogger(__name__)


class PartialFailureExperiment(ChaosExperiment):
    """
    Inject partial failures to test graceful degradation.

    Tests load shedding, emergency mode escalation.

    Config parameters:
        - failure_rate: Percentage of requests to fail (default: 30%)
        - affected_endpoints: List of endpoints to affect (optional)
        - trigger_shedding: Whether to trigger load shedding (default: True)
    """

    experiment_type = ExperimentType.PARTIAL_FAILURE.value
    requires_approval = True  # High risk

    @property
    def failure_rate(self) -> float:
        return self.config.parameters.get("failure_rate", 0.30)

    @property
    def affected_endpoints(self) -> list[str]:
        return self.config.parameters.get("affected_endpoints", [])

    @property
    def trigger_shedding(self) -> bool:
        return self.config.parameters.get("trigger_shedding", True)

    def inject_chaos(self) -> bool:
        """Inject partial failures."""
        logger.info(
            f"[PartialFailure] Injecting {self.failure_rate*100}% failures "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )

        try:
            _apply_chaos_config(
                {
                    "partial_failure": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "failure_rate": self.failure_rate,
                        "affected_endpoints": self.affected_endpoints,
                        "trigger_shedding": self.trigger_shedding,
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
            logger.error(f"[PartialFailure] Failed to inject: {e}")
            return False

    def rollback(self) -> None:
        """Remove partial failure injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    f"[PartialFailure] Rollback already completed for {self.experiment_id}"
                )
                return

            logger.info(f"[PartialFailure] Rolling back {self.experiment_id}")

            try:
                _apply_chaos_config(
                    {
                        "partial_failure": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PartialFailure] Rollback failed: {e}")

    # =========================================================================
    # Load Shedding 연동
    # =========================================================================

    def _trigger_load_shedding(self) -> dict[str, Any]:
        """
        Load Shedding 강제 트리거 시뮬레이션.

        Returns:
            Dict with before/after status and whether shedding was triggered
        """
        try:
            from selfhealing.services.circuit_breaker.load_shedding import (
                get_load_shedding_manager,
            )

            manager = get_load_shedding_manager()
            before_status = manager.get_status()

            # 강제 활성화 (레벨 0 = 가장 낮은 단계)
            if self.trigger_shedding and not before_status.active:
                manager.force_activate(
                    level_index=0,
                    reason=f"chaos_experiment:{self.experiment_id}",
                )

            after_status = manager.get_status()

            return {
                "before": (
                    before_status.to_dict()
                    if hasattr(before_status, "to_dict")
                    else {
                        "active": before_status.active,
                        "current_level_index": before_status.current_level_index,
                    }
                ),
                "after": (
                    after_status.to_dict()
                    if hasattr(after_status, "to_dict")
                    else {
                        "active": after_status.active,
                        "current_level_index": after_status.current_level_index,
                    }
                ),
                "shedding_triggered": after_status.active and not before_status.active,
            }
        except ImportError:
            logger.debug("[PartialFailure] LoadSheddingManager not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[PartialFailure] Load shedding trigger failed: {e}")
            return {"available": False, "error": str(e)}

    def _verify_shedding_behavior(self) -> dict[str, Any]:
        """
        Load Shedding 동작 검증.

        Returns:
            Dict with shedding status information
        """
        try:
            from selfhealing.services.circuit_breaker.load_shedding import (
                get_load_shedding_manager,
            )

            manager = get_load_shedding_manager()
            status = manager.get_status()

            return {
                "shedding_active": status.active,
                "current_level_index": status.current_level_index,
                "current_level_description": status.current_level_description,
                "shed_services": status.shed_services,
                "shed_criticality": status.shed_criticality,
                "traffic_limit": status.traffic_limit,
                "timestamp": status.timestamp,
            }
        except ImportError:
            logger.debug("[PartialFailure] LoadSheddingManager not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[PartialFailure] Shedding verification failed: {e}")
            return {"available": False, "error": str(e)}

    def _deactivate_load_shedding(self) -> None:
        """
        Load Shedding 비활성화 (rollback 시 호출).
        """
        try:
            from selfhealing.services.circuit_breaker.load_shedding import (
                get_load_shedding_manager,
            )

            manager = get_load_shedding_manager()
            if manager.is_shedding_active():
                manager.force_deactivate(
                    reason=f"chaos_experiment_rollback:{self.experiment_id}",
                )
                logger.info(
                    f"[PartialFailure] Load shedding deactivated for {self.experiment_id}"
                )
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[PartialFailure] Load shedding deactivation failed: {e}")


class CascadingFailureExperiment(ChaosExperiment):
    """
    Simulate cascading failures across multiple services.

    Tests panic threshold detection and Emergency Level 3 escalation.

    ⚠️ WARNING: This is a CRITICAL RISK experiment.
    - Requires manual approval before execution
    - Can trigger system-wide Emergency Level 3
    - Should only be run in isolated test environments

    Config parameters:
        - affected_services: List of services to fail
        - cascade_delay_seconds: Delay between service failures (default: 5)
        - target_open_percent: Target CB OPEN percentage (default: 75%)
    """

    experiment_type = ExperimentType.CASCADING_FAILURE.value
    requires_approval = True  # Critical risk - requires manual approval

    @property
    def affected_services(self) -> list[str]:
        return self.config.parameters.get("affected_services", [])

    @property
    def cascade_delay_seconds(self) -> int:
        return self.config.parameters.get("cascade_delay_seconds", 5)

    @property
    def target_open_percent(self) -> float:
        return self.config.parameters.get("target_open_percent", 75.0)

    def _is_killed(self) -> bool:
        """Check if kill switch was activated."""
        return self._kill_requested

    def inject_chaos(self) -> bool:
        """Inject cascading failures across services."""
        logger.warning(
            f"[CascadingFailure] ⚠️ CRITICAL: Injecting cascading failures to "
            f"{len(self.affected_services)} services (TTL: {self._effective_ttl}s)"
        )

        if not self.affected_services:
            logger.error("[CascadingFailure] No affected_services specified")
            return False

        try:
            from selfhealing.services.circuit_breaker import (
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()
            opened_services = []

            for service in self.affected_services:
                # Kill Switch 확인
                if self._is_killed():
                    logger.warning(
                        "[CascadingFailure] Kill switch activated, stopping cascade"
                    )
                    break

                result = cb_service.force_open(
                    service_name=service,
                    reason=f"Cascading Failure Experiment: {self.experiment_id}",
                    controlled_by="chaos_engine",
                )

                if result.success:
                    opened_services.append(service)
                    logger.info(f"[CascadingFailure] Opened CB for {service}")
                else:
                    logger.warning(
                        f"[CascadingFailure] Failed to open CB for {service}: {result.message}"
                    )

                # 연쇄 효과 시뮬레이션을 위한 지연
                if service != self.affected_services[-1]:
                    time.sleep(self.cascade_delay_seconds)

            _apply_chaos_config(
                {
                    "cascading_failure": {
                        "enabled": True,
                        "affected_services": opened_services,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )

            logger.warning(
                f"[CascadingFailure] Cascade injection complete: "
                f"{len(opened_services)}/{len(self.affected_services)} services affected"
            )
            return True
        except Exception as e:
            logger.error(f"[CascadingFailure] Failed to inject: {e}")
            return False

    def rollback(self) -> None:
        """Close all opened circuit breakers."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    f"[CascadingFailure] Rollback already completed for {self.experiment_id}"
                )
                return

            logger.info(f"[CascadingFailure] Rolling back {self.experiment_id}")

            try:
                from selfhealing.services.circuit_breaker import (
                    get_circuit_breaker_service,
                )

                cb_service = get_circuit_breaker_service()

                for service in self.affected_services:
                    cb_service.force_close(
                        service_name=service,
                        reason=f"Cascading Failure Rollback: {self.experiment_id}",
                        controlled_by="chaos_engine",
                        trigger_replay=False,
                    )

                _apply_chaos_config(
                    {
                        "cascading_failure": {
                            "enabled": False,
                            "affected_services": [],
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
                logger.info(
                    f"[CascadingFailure] Rollback complete for {len(self.affected_services)} services"
                )
            except Exception as e:
                logger.error(f"[CascadingFailure] Rollback failed: {e}")


__all__ = ["PartialFailureExperiment", "CascadingFailureExperiment"]
