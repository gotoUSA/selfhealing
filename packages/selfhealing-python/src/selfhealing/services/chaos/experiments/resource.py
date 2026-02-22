"""
Resource Exhaustion Experiments.

Includes:
- ResourceExhaustionExperiment: CPU, memory, connection pool exhaustion
- PoolExhaustionExperiment: Connection pool exhaustion simulation
"""

from __future__ import annotations

import structlog
from typing import Any

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)
from selfhealing.services.chaos.experiments.hypothesis import (
    POOL_EXHAUSTION_HYPOTHESIS,
)

logger = structlog.get_logger()


class ResourceExhaustionExperiment(ChaosExperiment):
    """
    Simulate resource exhaustion conditions.

    Simulates CPU spike, memory pressure, connection pool exhaustion.

    Config parameters:
        - resource_type: "cpu", "memory", "connections" (default: "connections")
        - exhaustion_percent: Percentage of resource to consume (default: 80%)

    Safety Features:
        - Cgroup-aware memory limit (15% safety margin)
        - OOM Killer 방지를 위한 자동 캡핑
    """

    experiment_type = ExperimentType.RESOURCE_EXHAUSTION.value
    requires_approval = True  # High risk

    # 안전 마진 (15% - 아키텍트 제안 10% + 5% 버퍼)
    SAFETY_MARGIN_PERCENT = 0.15

    @property
    def resource_type(self) -> str:
        return self.config.parameters.get("resource_type", "connections")

    @property
    def exhaustion_percent(self) -> float:
        return self.config.parameters.get("exhaustion_percent", 0.80)  # 80%

    def _get_safe_exhaustion_bytes(self) -> int | None:
        """
        Cgroup 제한을 고려한 안전한 메모리 사용량 계산.

        Returns:
            안전하게 사용 가능한 bytes. None이면 제한 없음.
        """
        if self.resource_type != "memory":
            return None

        try:
            from selfhealing.core.resource_monitor import CgroupResourceMonitor

            max_bytes = CgroupResourceMonitor.get_memory_max_bytes()
            if max_bytes is None:
                return None

            # 요청된 메모리 계산
            requested_bytes = int(max_bytes * self.exhaustion_percent)

            # 안전 한계 확인
            is_safe, actual_bytes = CgroupResourceMonitor.check_safe_for_exhaustion(
                requested_bytes=requested_bytes,
                safety_margin=self.SAFETY_MARGIN_PERCENT,
            )

            if not is_safe:
                logger.warning(
                    f"[ResourceExhaustion] Capping memory to {actual_bytes / 1024 / 1024:.0f}MB "
                    f"(cgroup limit: {max_bytes / 1024 / 1024:.0f}MB, "
                    f"safety margin: {self.SAFETY_MARGIN_PERCENT * 100:.0f}%)"
                )

            return actual_bytes
        except Exception as e:
            logger.debug(
                "resource_exhaustion.cgroup_check_failed",
                error=e,
            )
            return None

    def inject_chaos(self) -> bool:
        """Inject resource exhaustion with TTL and cgroup safety margin."""
        # 메모리 타입일 경우 cgroup 안전 마진 적용
        safe_bytes = self._get_safe_exhaustion_bytes()
        exhaustion_config: dict[str, Any] = {
            "exhaustion_percent": self.exhaustion_percent,
        }

        if safe_bytes is not None:
            exhaustion_config["capped_bytes"] = safe_bytes
            exhaustion_config["safety_margin_applied"] = True

        logger.info(
            f"[ResourceExhaustion] Exhausting {self.resource_type} to "
            f"{self.exhaustion_percent*100}% on {self.config.target_service} "
            f"(TTL: {self._effective_ttl}s"
            f"{f', capped to {safe_bytes / 1024 / 1024:.0f}MB' if safe_bytes else ''})"
        )

        try:
            _apply_chaos_config(
                {
                    "resource_exhaustion": {
                        "enabled": True,
                        "target_service": self.config.target_service,
                        "resource_type": self.resource_type,
                        **exhaustion_config,
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
            logger.error(
                "resource_exhaustion.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """Release exhausted resources with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    f"[ResourceExhaustion] Rollback already completed for {self.experiment_id}"
                )
                return

            logger.info(
                "resource_exhaustion.rolling_back",
                self=self.experiment_id,
            )

            try:
                _apply_chaos_config(
                    {
                        "resource_exhaustion": {
                            "enabled": False,
                            "target_service": self.config.target_service,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
            except Exception as e:
                logger.error(
                    "resource_exhaustion.rollback_failed",
                    error=e,
                )


class PoolExhaustionExperiment(ChaosExperiment):
    """
    Connection Pool 고갈 시뮬레이션 실험.

    실제 인프라를 변경하지 않고 PoolMonitor가 EXHAUSTED 상태를 보고하도록
    시뮬레이션하여 알림/복구 체인을 검증.

    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • Pool 고갈 시뮬레이션 시, 시스템이 graceful degradation     │
    │ • 120초 내에 복구 완료                                       │
    │ • Fallback(cache) 활성화 필수                                │
    │                                                              │
    │ LearningService 연동:                                        │
    │ → 실제 결과와 비교하여 "복구 성능 저하 추세" 자동 감지        │
    └─────────────────────────────────────────────────────────────┘

    Config parameters:
        - simulated_status: 시뮬레이션할 Pool 상태 (default: "exhausted")
        - target_pool: 대상 Pool 이름 (optional)

    Usage:
        experiment = PoolExhaustionExperiment(
            config=ExperimentConfig(
                target_service="payment",
                parameters={"simulated_status": "exhausted"},
            )
        )
        result = experiment.execute()
    """

    experiment_type = ExperimentType.POOL_EXHAUSTION.value
    requires_approval = True  # 높은 위험 - 수동 승인 필요

    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = POOL_EXHAUSTION_HYPOTHESIS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._monitor_instance = None

    @property
    def simulated_status(self) -> str:
        return self.config.parameters.get("simulated_status", "exhausted")

    @property
    def target_pool(self) -> str:
        return self.config.parameters.get("target_pool", "default")

    def inject_chaos(self) -> bool:
        """시뮬레이션 모드로 Pool 고갈 상태 주입."""
        from selfhealing.core.pool_monitor import (
            ConnectionPoolMonitor,
            PoolHealthStatus,
        )

        logger.info(
            f"[PoolExhaustion] Injecting simulated {self.simulated_status} status "
            f"for pool '{self.target_pool}' (TTL: {self._effective_ttl}s)"
        )

        try:
            # 상태 매핑
            status_map = {
                "exhausted": PoolHealthStatus.EXHAUSTED,
                "critical": PoolHealthStatus.CRITICAL,
                "warning": PoolHealthStatus.WARNING,
                "leak_suspected": PoolHealthStatus.LEAK_SUSPECTED,
            }

            health_status = status_map.get(
                self.simulated_status.lower(), PoolHealthStatus.EXHAUSTED
            )

            # Monitor 인스턴스 생성 및 시뮬레이션 설정
            self._monitor_instance = ConnectionPoolMonitor()
            self._monitor_instance.set_simulation_override(
                health_status=health_status,
                experiment_id=self.experiment_id,
            )

            _apply_chaos_config(
                {
                    "pool_exhaustion": {
                        "enabled": True,
                        "target_pool": self.target_pool,
                        "simulated_status": self.simulated_status,
                        "experiment_id": self.experiment_id,
                        "expires_at": (
                            self._expires_at.isoformat() if self._expires_at else ""
                        ),
                        "ttl_seconds": self._effective_ttl,
                    }
                }
            )

            logger.info(
                f"[PoolExhaustion] Simulation override set: {health_status.value}"
            )
            return True

        except Exception as e:
            logger.error(
                "pool_exhaustion.failed_inject",
                error=e,
            )
            return False

    def rollback(self) -> None:
        """시뮬레이션 오버라이드 해제."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(
                    f"[PoolExhaustion] Rollback already completed for {self.experiment_id}"
                )
                return

            logger.info(
                "pool_exhaustion.rolling_back",
                self=self.experiment_id,
            )

            try:
                if self._monitor_instance:
                    self._monitor_instance.clear_simulation_override()

                _apply_chaos_config(
                    {
                        "pool_exhaustion": {
                            "enabled": False,
                            "experiment_id": self.experiment_id,
                        }
                    }
                )
                self._rollback_completed = True
                logger.info("pool_exhaustion.simulation_override_cleared")
            except Exception as e:
                logger.error(
                    "pool_exhaustion.rollback_failed",
                    error=e,
                )


__all__ = ["ResourceExhaustionExperiment", "PoolExhaustionExperiment"]
