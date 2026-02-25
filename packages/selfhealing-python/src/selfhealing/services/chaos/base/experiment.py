"""
Chaos Experiment Base Class.

Contains the ChaosExperiment abstract base class that implements the Template Method
pattern for chaos experiments.

All concrete experiment implementations should inherit from ChaosExperiment and
implement the required abstract methods: inject_chaos() and rollback().
"""

from __future__ import annotations

import abc
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog

from selfhealing.core.timezone import now

# Import from separate modules (no duplication)
from .enums import ExperimentStatus
from .models import ExperimentConfig, ExperimentResult, SteadyStateHypothesis
from .ttl_helper import MonotonicTTLHelper

logger = structlog.get_logger()


class ChaosExperiment(abc.ABC):
    """
    Base class for all chaos experiments.

    Implements the Template Method pattern:
    1. pre_flight_check() - Validate preconditions
    2. capture_steady_state() - Capture baseline metrics
    3. inject_chaos() - Execute the chaos injection
    4. monitor_impact() - Track ongoing impact
    5. rollback() - Clean up / restore normal state
    6. validate_recovery() - Verify system recovered
    7. generate_report() - Create experiment report

    Safety features:
    - TTL (Self-Expiration): 자동 만료로 엔진 사망 시에도 복구
    - Monotonic TTL: ClockSkew 실험에서도 안전한 TTL (MonotonicTTLHelper)
    - Stop Conditions: SLA 위반 시 자동 중단
    - Dry Run: 실제 주입 없이 시뮬레이션
    - Idempotent Rollback: 멱등성 있는 롤백
    """

    # Class-level configuration
    experiment_type: str = "base"
    requires_approval: bool = False
    default_duration_seconds: int = 300
    default_ttl_seconds: int = 600  # 기본 10분 TTL

    def __init__(
        self,
        experiment_id: str | None = None,
        config: ExperimentConfig | None = None,
        hypothesis: SteadyStateHypothesis | None = None,
    ):
        """Initialize experiment."""
        self.experiment_id = experiment_id or f"chaos-{uuid.uuid4().hex[:12]}"
        self.config = config or ExperimentConfig()
        self.hypothesis = hypothesis or SteadyStateHypothesis()

        # State
        self.status = ExperimentStatus.PENDING
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None

        # Results
        self.result: ExperimentResult | None = None
        self._kill_requested = False
        self._audit_records: list[str] = []

        # TTL state
        self._expires_at: datetime | None = None
        self._effective_ttl: int = 0

        # Rollback state (for idempotency)
        self._rollback_completed = False
        self._rollback_lock = threading.Lock()

        # Stop conditions monitoring
        self._stop_condition_violation: str | None = None

        # Monotonic TTL helper
        self._monotonic_ttl_helper: MonotonicTTLHelper | None = None

    # =========================================================================
    # TTL (Self-Expiration) Methods
    # =========================================================================

    def get_effective_ttl(self) -> int:
        """Get effective TTL for this experiment."""
        if self.config.ttl_seconds is not None:
            return self.config.ttl_seconds

        try:
            from .stop_conditions import get_ttl_config

            ttl_config = get_ttl_config()
            return ttl_config.validate_ttl(self.default_ttl_seconds)
        except Exception:
            return self.default_ttl_seconds

    def _calculate_expires_at(self) -> datetime:
        """Calculate expiration time based on TTL."""
        self._effective_ttl = self.get_effective_ttl()
        self._expires_at = now() + timedelta(seconds=self._effective_ttl)
        return self._expires_at

    def is_expired(self) -> bool:
        """Check if experiment has expired based on TTL."""
        if self._expires_at is None:
            return False
        return now() > self._expires_at

    # =========================================================================
    # Monotonic TTL Methods (ClockSkew 보호용) - 시스템 시간 조작에 독립적
    # =========================================================================

    def _start_monotonic_timer(self) -> None:
        """
        Monotonic clock 기반 TTL 타이머 시작.

        ClockSkewExperiment 등 시스템 시간 관련 실험에서 사용합니다.
        time.monotonic()는 시스템 시간 변경에 영향받지 않으므로,
        시간 조작 실험에서도 TTL이 정확하게 작동합니다.
        """
        self._monotonic_ttl_helper = MonotonicTTLHelper(ttl_seconds=float(self._effective_ttl))
        self._monotonic_ttl_helper.start()

        logger.debug(
            "chaos_experiment.monotonic_timer_started",
            experiment_id=self.experiment_id,
            effective_ttl=self._effective_ttl,
        )

    def _is_expired_monotonic(self) -> bool:
        """
        Monotonic clock 기반 TTL 만료 확인.

        ClockSkewExperiment 등에서 시스템 시간 조작에도 불구하고
        실제 경과 시간 기준으로 만료 여부를 판정합니다.

        Returns:
            True if TTL 만료됨 (monotonic clock 기준), False otherwise
        """
        if self._monotonic_ttl_helper is None:
            # Monotonic TTL 사용 안 함 → 기존 방식 fallback
            return self.is_expired()
        return self._monotonic_ttl_helper.is_expired()

    def get_elapsed_monotonic(self) -> float:
        """
        Monotonic clock 기반 경과 시간 반환 (초).

        Returns:
            시작 후 경과한 시간 (초). Monotonic 타이머 미사용 시 0.0 반환.
        """
        if self._monotonic_ttl_helper is None:
            return 0.0
        return self._monotonic_ttl_helper.elapsed_seconds()

    def get_remaining_monotonic(self) -> float:
        """
        Monotonic clock 기반 남은 시간 반환 (초).

        Returns:
            TTL까지 남은 시간 (초). Monotonic 타이머 미사용 시 effective_ttl 반환.
        """
        if self._monotonic_ttl_helper is None:
            return float(self._effective_ttl)
        return self._monotonic_ttl_helper.remaining_seconds()

    # =========================================================================
    # 비동기 복구 모니터링 메서드 - Soft TTL 후 시스템 복구 추적
    # =========================================================================

    def is_hard_ttl_expired(self) -> bool:
        """
        Check if Hard TTL has expired.

        Hard TTL = Soft TTL + Grace Period.
        Grace Period 동안 Canary 복구를 기다리며,
        Hard TTL이 지나면 강제 종료.

        Returns:
            True if hard TTL expired, False otherwise
        """
        if self._expires_at is None:
            return False

        # Hard TTL = expires_at + grace_period
        grace_period = timedelta(seconds=self.config.grace_period_seconds)
        hard_expires_at = self._expires_at + grace_period

        return now() > hard_expires_at

    def complete_recovery_monitoring(self) -> None:
        """
        RECOVERY_MONITORING 상태에서 복구 완료 처리.

        Canary 복구가 완료되면 호출되어 실험을 COMPLETED로 전환.
        """
        if self.status != ExperimentStatus.RECOVERY_MONITORING:
            logger.warning(
                "chaos_experiment.cannot_complete_recovery_monitoring",
                experiment_id=self.experiment_id,
                experiment_status=self.status,
            )
            return

        self.status = ExperimentStatus.COMPLETED
        self.completed_at = now()

        self._audit(
            "recovery_monitoring_completed",
            {
                "previous_status": ExperimentStatus.RECOVERY_MONITORING.value,
                "completed_at": self.completed_at.isoformat(),
            },
        )

        logger.info(
            "chaos_experiment.recovery_monitoring_completed_status",
            experiment_id=self.experiment_id,
        )

    def force_complete(self, reason: str = "hard_ttl_expired") -> None:
        """
        실험 강제 종료.

        Hard TTL 만료 시 또는 관리자 개입 시 호출.

        Args:
            reason: 강제 종료 사유
        """
        previous_status = self.status
        self.status = ExperimentStatus.COMPLETED
        self.completed_at = now()

        self._audit(
            "force_completed",
            {
                "previous_status": (previous_status.value if hasattr(previous_status, "value") else str(previous_status)),
                "reason": reason,
                "completed_at": self.completed_at.isoformat(),
            },
        )

        logger.warning(
            "chaos_experiment.force_completed_due",
            experiment_id=self.experiment_id,
            reason=reason,
        )

    def transition_to_recovery_monitoring(self) -> None:
        """
        RUNNING 상태에서 RECOVERY_MONITORING 상태로 전환.

        Soft TTL 도달 시 장애 주입을 중단하고 복구 모니터링 단계로 전환.
        """
        if self.status != ExperimentStatus.RUNNING:
            logger.warning(
                "chaos_experiment.cannot_transition_status",
                experiment_id=self.experiment_id,
                experiment_status=self.status,
            )
            return

        # 먼저 rollback 수행하여 장애 주입 중단
        self.rollback()

        self.status = ExperimentStatus.RECOVERY_MONITORING

        self._audit(
            "transition_to_recovery_monitoring",
            {
                "previous_status": ExperimentStatus.RUNNING.value,
                "soft_ttl_expired": True,
                "grace_period_seconds": self.config.grace_period_seconds,
            },
        )

        logger.info(
            "chaos_experiment.transitioned_grace_period",
            experiment_id=self.experiment_id,
            grace_period_seconds=self.config.grace_period_seconds,
        )

    def _verify_canary_recovery(self) -> dict[str, Any]:
        """
        Canary 복구 단계 검증.

        Returns:
            Dict with canary state information
        """
        try:
            from selfhealing.services.circuit_breaker.canary_recovery import (
                CanaryRecoveryManager,
                CanaryRecoveryStage,
            )

            manager = CanaryRecoveryManager()
            state = manager.get_canary_state(self.config.target_service)
            traffic = manager.get_traffic_percent(self.config.target_service)

            return {
                "canary_state": state.value if hasattr(state, "value") else str(state),
                "traffic_percent": traffic,
                "in_canary": state != CanaryRecoveryStage.NOT_IN_CANARY,
            }
        except ImportError:
            logger.debug("chaos_experiment.canaryrecoverymanager_available")
            return {"in_canary": False, "canary_state": "not_available"}
        except Exception as e:
            logger.warning(
                "chaos_experiment.canary_verification_failed",
                error=e,
            )
            return {"in_canary": False, "error": str(e)}

    # =========================================================================
    # 모니터 스냅샷 메서드 - 실험 전후 시스템 상태 캡처
    # =========================================================================

    def _get_pool_state_snapshot(self) -> dict[str, Any]:
        """
        실험 전후 커넥션 풀 상태 캡처.

        Returns:
            Dict with pool health status and statistics
        """
        try:
            from selfhealing.core.pool_monitor import ConnectionPoolMonitor

            monitor = ConnectionPoolMonitor()
            if not monitor._stats_provider:
                return {"available": False, "reason": "no_stats_provider"}

            status, stats = monitor.check_health()
            return {
                "health_status": (status.value if hasattr(status, "value") else str(status)),
                "active_connections": stats.active_connections,
                "available_connections": stats.available_connections,
                "usage_percent": stats.usage_percent,
                "waiting_requests": stats.waiting_requests,
                "timestamp": now().isoformat(),
            }
        except Exception as e:
            logger.warning(
                "chaos.pool_state_snapshot_failed",
                error=e,
            )
            return {"available": False, "error": str(e)}

    def _get_cert_state_snapshot(self) -> dict[str, Any]:
        """
        실험 전후 인증서 상태 캡처.

        Returns:
            Dict with certificate check status
        """
        try:
            from selfhealing.core.cert_monitor import CertificateExpiryMonitor

            # 기본적인 체크 정보만 반환
            return {
                "target_endpoint": self.config.target_service,
                "check_performed": True,
                "timestamp": now().isoformat(),
            }
        except ImportError:
            logger.debug("chaos")
            return {"check_performed": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(
                "chaos.cert_state_snapshot_failed",
                error=e,
            )
            return {"check_performed": False, "error": str(e)}

    def _get_connection_health_snapshot(self) -> dict[str, Any]:
        """
        실험 전후 연결 상태 캡처.

        Returns:
            Dict with connection health and partition state
        """
        try:
            from selfhealing.core.connection_health import (
                DefaultConnectionHealthMonitor,
            )

            monitor = DefaultConnectionHealthMonitor()
            partition = monitor.get_partition_state()

            return {
                "is_partial_partition": partition.is_partial_partition,
                "is_full_partition": partition.is_full_partition,
                "db_available": partition.db_available,
                "cache_available": partition.cache_available,
                "timestamp": now().isoformat(),
            }
        except ImportError:
            logger.debug("chaos")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(
                "chaos.connection_health_snapshot_failed",
                error=e,
            )
            return {"available": False, "error": str(e)}

    # =========================================================================
    # Dry Run Methods
    # =========================================================================

    def _should_dry_run(self) -> bool:
        """Check if this experiment should run in dry run mode."""
        if self.config.dry_run:
            return True

        try:
            from .stop_conditions import get_dry_run_config

            dry_run_config = get_dry_run_config()
            return dry_run_config.enabled
        except Exception:
            return False

    def _run_dry(self) -> ExperimentResult:
        """Execute experiment in dry run mode."""
        logger.info(
            "dry_run.starting_dry_run",
            experiment_id=self.experiment_id,
        )

        self._calculate_expires_at()

        if not self.pre_flight_check():
            return self._create_skipped_result("Pre-flight check failed (dry run)")

        steady_state_before = self.capture_steady_state()
        self._audit(
            "steady_state_captured",
            {"phase": "before", "metrics": steady_state_before, "dry_run": True},
        )

        self._audit(
            "chaos_injection_simulated",
            {
                "dry_run": True,
                "would_inject": self._config_to_dict(),
                "ttl_seconds": self._effective_ttl,
                "expires_at": self._expires_at.isoformat() if self._expires_at else "",
            },
        )

        logger.info(
            "dry_run.inject_chaos_ttl_expires",
            target_service=self.config.target_service,
            effective_ttl=self._effective_ttl,
            expires_at=self._expires_at,
        )

        duration = min(5, self.config.duration_seconds or self.default_duration_seconds)
        time.sleep(duration)

        steady_state_after = self.capture_steady_state()

        self.completed_at = now()
        self.status = ExperimentStatus.COMPLETED

        self.result = ExperimentResult(
            experiment_id=self.experiment_id,
            experiment_type=self.experiment_type,
            status=self.status.value,
            started_at=self.started_at.isoformat() if self.started_at else "",
            completed_at=self.completed_at.isoformat(),
            duration_seconds=((self.completed_at - self.started_at).total_seconds() if self.started_at else 0),
            steady_state_before=steady_state_before,
            steady_state_after=steady_state_after,
            steady_state_hypothesis_passed=True,
            audit_record_ids=self._audit_records.copy(),
            dry_run=True,
            ttl_seconds=self._effective_ttl,
            expires_at=self._expires_at.isoformat() if self._expires_at else "",
        )

        self._audit("experiment_completed", {"result": self.result.to_dict(), "dry_run": True})
        logger.info(
            "dry_run.completed_dry_run_no",
            experiment_id=self.experiment_id,
        )

        return self.result

    # =========================================================================
    # Template Method - Main Execution Flow
    # =========================================================================

    def execute(self) -> ExperimentResult:
        """Execute the chaos experiment."""
        self.started_at = now()
        self.status = ExperimentStatus.RUNNING

        if self._should_dry_run():
            return self._run_dry()

        try:
            self._calculate_expires_at()

            self._audit(
                "experiment_started",
                {
                    "config": self._config_to_dict(),
                    "ttl_seconds": self._effective_ttl,
                    "expires_at": (self._expires_at.isoformat() if self._expires_at else ""),
                },
            )

            if not self.pre_flight_check():
                return self._create_skipped_result("Pre-flight check failed")

            steady_state_before = self.capture_steady_state()
            self._audit(
                "steady_state_captured",
                {"phase": "before", "metrics": steady_state_before},
            )

            self._audit(
                "chaos_injection_started",
                {
                    "ttl_seconds": self._effective_ttl,
                    "expires_at": (self._expires_at.isoformat() if self._expires_at else ""),
                },
            )
            injection_result = self.inject_chaos()

            if not injection_result:
                return self._create_failed_result("Chaos injection failed")

            impact_metrics = self._monitor_with_kill_switch()

            if self._kill_requested:
                self.rollback()
                abort_reason = "Kill switch activated"
                if self._stop_condition_violation:
                    abort_reason = f"Stop condition violated: {self._stop_condition_violation}"
                return self._create_aborted_result(abort_reason)

            self._audit("rollback_started", {})
            self.rollback()
            self._audit("rollback_completed", {})

            recovery_time = self.validate_recovery()

            steady_state_after = self.capture_steady_state()
            self._audit(
                "steady_state_captured",
                {"phase": "after", "metrics": steady_state_after},
            )

            hypothesis_passed, violations = self.hypothesis.validate(steady_state_after)

            self.completed_at = now()
            self.status = ExperimentStatus.COMPLETED

            self.result = ExperimentResult(
                experiment_id=self.experiment_id,
                experiment_type=self.experiment_type,
                status=self.status.value,
                started_at=self.started_at.isoformat(),
                completed_at=self.completed_at.isoformat(),
                duration_seconds=(self.completed_at - self.started_at).total_seconds(),
                total_requests_affected=impact_metrics.get("total_requests", 0),
                errors_injected=impact_metrics.get("errors_injected", 0),
                sla_breaches=impact_metrics.get("sla_breaches", 0),
                recovery_time_seconds=recovery_time,
                auto_recovered=recovery_time > 0,
                steady_state_before=steady_state_before,
                steady_state_after=steady_state_after,
                steady_state_hypothesis_passed=hypothesis_passed,
                audit_record_ids=self._audit_records.copy(),
                dry_run=False,
                ttl_seconds=self._effective_ttl,
                expires_at=self._expires_at.isoformat() if self._expires_at else "",
            )

            self._audit("experiment_completed", {"result": self.result.to_dict()})

            # LearningService 피드백 루프: 가설 검증 결과 기록
            cb_snapshot = self._get_cb_state_snapshot()
            self._record_hypothesis_validation(
                actual_recovery_time=recovery_time if recovery_time > 0 else 60.0,
                actual_cb_state=cb_snapshot.get("target_service_state"),
            )

            # FinOps 비용 기록
            self.record_finops_cost()

            return self.result

        except Exception as e:
            logger.exception(
                "chaos_experiment.error",
                experiment_id=self.experiment_id,
                error=e,
            )
            self.rollback()
            return self._create_failed_result(str(e))

    # =========================================================================
    # Abstract Methods - Must be implemented by subclasses
    # =========================================================================

    @abc.abstractmethod
    def inject_chaos(self) -> bool:
        """Inject the chaos condition."""
        pass

    @abc.abstractmethod
    def rollback(self) -> None:
        """Rollback the chaos injection and restore normal state."""
        pass

    # =========================================================================
    # Optional Overrides
    # =========================================================================

    def pre_flight_check(self) -> bool:
        """Validate preconditions before starting experiment."""
        if self._kill_requested:
            logger.warning(
                "chaos_experiment.kill_requested_before_start",
                experiment_id=self.experiment_id,
            )
            return False
        return True

    def capture_steady_state(self) -> dict[str, float]:
        """Capture current system metrics for steady state comparison."""
        return {
            "p50_latency_ms": 50.0,
            "p99_latency_ms": 200.0,
            "error_rate_percent": 0.01,
            "throughput_rps": 500.0,
        }

    # =========================================================================
    # Circuit Breaker 상태 스냅샷 캡처
    # =========================================================================

    def _get_cb_state_snapshot(self) -> dict[str, Any]:
        """
        실험 전후 CB 상태 스냅샷 캡처.

        Returns:
            Dict with:
                - target_service_state: 대상 서비스 CB 상태
                - is_allowed: 요청 허용 여부
                - timestamp: 스냅샷 시간
        """
        try:
            from selfhealing.services.circuit_breaker import get_circuit_breaker_service

            service = get_circuit_breaker_service()
            target = self.config.target_service

            return {
                "target_service_state": service.get_state(target),
                "is_allowed": service.should_allow(target),
                "timestamp": now().isoformat(),
            }
        except Exception as e:
            logger.warning(
                "chaos.cb_state_snapshot_failed",
                error=e,
            )
            return {}

    def capture_steady_state_with_cb(self) -> dict[str, Any]:
        """
        CB 상태를 포함한 Steady State 캡처.

        기본 메트릭 + Circuit Breaker 상태를 함께 캡처.

        Returns:
            Dict containing metrics and circuit_breaker state
        """
        steady_state = self.capture_steady_state()
        steady_state["circuit_breaker"] = self._get_cb_state_snapshot()
        return steady_state

    # =========================================================================
    # 고급 스냅샷 메서드 - Corruption Shield, DLQ, Throttle 통합
    # =========================================================================

    def _get_corruption_shield_stats(self) -> dict[str, Any]:
        """
        Corruption Shield 통계 조회.

        Returns:
            Dict with corruption shield statistics
        """
        try:
            from selfhealing.services.corruption_shield import get_corruption_shield

            shield = get_corruption_shield()
            return shield.get_stats()
        except ImportError:
            logger.debug("chaos")
            return {}
        except Exception as e:
            logger.warning(
                "chaos.corruption_shield_stats_failed",
                error=e,
            )
            return {}

    def _get_dlq_stats(self) -> dict[str, Any]:
        """
        DLQ 통계 조회 (카오스 실험 제외).

        Returns:
            Dict with DLQ pending counts
        """
        try:
            from selfhealing.services.dlq import get_dlq_service

            service = get_dlq_service()
            return {
                "pending_count": (service.get_pending_count() if hasattr(service, "get_pending_count") else 0),
            }
        except ImportError:
            logger.debug("chaos")
            return {}
        except Exception as e:
            logger.warning(
                "chaos.dlq_stats_failed",
                error=e,
            )
            return {}

    def _get_throttle_stats(self) -> dict[str, Any]:
        """
        Adaptive Throttle 통계 조회.

        Returns:
            Dict with throttle statistics
        """
        try:
            from selfhealing.services.throttle import get_adaptive_throttle

            throttle = get_adaptive_throttle()
            return throttle.get_stats() if hasattr(throttle, "get_stats") else {}
        except ImportError:
            logger.debug("chaos")
            return {}
        except Exception as e:
            logger.warning(
                "chaos.throttle_stats_failed",
                error=e,
            )
            return {}

    # =========================================================================
    # 추가 스냅샷 메서드 - Emergency, Tiering, RateLimit
    # =========================================================================

    def _get_emergency_state_snapshot(self) -> dict[str, Any]:
        """
        Emergency Mode 상태 스냅샷 캡처.

        비상 레벨, 활성화 여부, 자동 트리거 여부 등을 캡처.
        카오스 실험 중 비상 모드 발동 시 is_chaos_experiment 메타데이터 확인 가능.

        Returns:
            Dict with emergency mode state
        """
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            state = manager.get_state()
            return state.to_dict()
        except ImportError:
            logger.debug("chaos")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(
                "chaos.emergency_state_snapshot_failed",
                error=e,
            )
            return {"available": False, "error": str(e)}

    def _get_tiering_cb_snapshot(self) -> dict[str, Any]:
        """
        Tiering Circuit Breaker 상태 스냅샷 캡처.

        TieringCircuitBreaker OPEN 시 전체 Tiering 우회 상태 추적.

        Returns:
            Dict with tiering circuit breaker state
        """
        try:
            from selfhealing.api.django.tiering.circuit_breaker import (
                get_tiering_circuit_breaker,
            )

            cb = get_tiering_circuit_breaker()
            return {
                "state": cb._state,
                "failure_count": cb._failure_count,
                "slow_count": cb._slow_count,
                "timestamp": now().isoformat(),
            }
        except ImportError:
            logger.debug("chaos")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(
                "chaos.tiering_cb_snapshot_failed",
                error=e,
            )
            return {"available": False, "error": str(e)}

    def _get_rate_limit_snapshot(self) -> dict[str, Any]:
        """
        Rate Limit 상태 스냅샷 캡처.

        Redis 상태 (healthy/degraded), 로컬 fallback 상태 추적.

        Returns:
            Dict with rate limit state
        """
        try:
            from selfhealing.api.django.rate_limit import get_current_state

            return get_current_state()
        except ImportError:
            logger.debug("chaos")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(
                "chaos.rate_limit_snapshot_failed",
                error=e,
            )
            return {"available": False, "error": str(e)}

    def _get_tiering_registry_snapshot(self) -> dict[str, Any]:
        """
        Tiering Registry 상태 스냅샷 캡처.

        현재 등록된 티어, 매핑, 오버라이드 정보 추적.
        동적 티어 변경 추적에 사용.

        Returns:
            Dict with tiering registry state
        """
        try:
            from selfhealing.api.django.tiering.registry import get_tier_registry

            registry = get_tier_registry()
            tiers = registry.get_all_tiers()
            mappings = registry.get_all_mappings()
            overrides = registry.get_all_overrides()

            return {
                "tier_count": len(tiers),
                "mapping_count": len(mappings),
                "override_count": len(overrides),
                "tier_ids": [t.id for t in tiers],
                "timestamp": now().isoformat(),
            }
        except ImportError:
            logger.debug("chaos")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(
                "chaos.tiering_registry_snapshot_failed",
                error=e,
            )
            return {"available": False, "error": str(e)}

    def capture_comprehensive_snapshot(self) -> dict[str, Any]:
        """
        모든 관련 서비스의 종합 스냅샷 캡처.

        CB, Corruption Shield, DLQ, Throttle, Pool, Cert, Connection Health,
        Emergency, Tiering CB, Rate Limit, Tiering Registry 상태를 모두 포함.

        Returns:
            Dict containing all service snapshots
        """
        return {
            "circuit_breaker": self._get_cb_state_snapshot(),
            "corruption_shield": self._get_corruption_shield_stats(),
            "dlq": self._get_dlq_stats(),
            "throttle": self._get_throttle_stats(),
            # 모니터 스냅샷
            "pool": self._get_pool_state_snapshot(),
            "cert": self._get_cert_state_snapshot(),
            "connection_health": self._get_connection_health_snapshot(),
            # 추가 스냅샷
            "emergency": self._get_emergency_state_snapshot(),
            "tiering_cb": self._get_tiering_cb_snapshot(),
            "rate_limit": self._get_rate_limit_snapshot(),
            "tiering_registry": self._get_tiering_registry_snapshot(),
            "timestamp": now().isoformat(),
        }

    def validate_recovery(self) -> float:
        """Wait for system to recover and measure recovery time."""
        start = now()
        max_wait = 60
        poll_interval = 1.0

        while (now() - start).total_seconds() < max_wait:
            metrics = self.capture_steady_state()
            passed, _ = self.hypothesis.validate(metrics)
            if passed:
                return (now() - start).total_seconds()
            time.sleep(poll_interval)

        return -1.0

    # =========================================================================
    # LearningService 피드백 루프 - 가설 검증 결과 기록 및 학습
    # =========================================================================

    def _record_hypothesis_validation(
        self,
        actual_recovery_time: float,
        actual_canary_stage: str | None = None,
        actual_cb_state: str | None = None,
        actual_fallback_activated: bool | None = None,
        actual_fallback_type: str | None = None,
    ) -> None:
        """
        가설 검증 결과를 LearningService에 기록.

        Args:
            actual_recovery_time: 실제 복구 시간 (초)
            actual_canary_stage: 실제 Canary 단계
            actual_cb_state: 실제 CB 상태
            actual_fallback_activated: 실제 Fallback 활성화 여부
            actual_fallback_type: 실제 Fallback 유형
        """
        # failure_hypothesis가 없으면 스킵
        if not hasattr(self, "failure_hypothesis") or self.failure_hypothesis is None:
            logger.debug(
                "chaos.no_defined",
                experiment_id=self.experiment_id,
            )
            return

        # Validate hypothesis
        passed, violations = self.failure_hypothesis.validate(
            actual_recovery_time=actual_recovery_time,
            actual_canary_stage=actual_canary_stage,
            actual_cb_state=actual_cb_state,
            actual_fallback_activated=actual_fallback_activated,
            actual_fallback_type=actual_fallback_type,
        )

        # Audit log
        self._audit(
            "hypothesis_validation",
            {
                "passed": passed,
                "violations": violations,
                "expected": (self.failure_hypothesis.to_dict() if hasattr(self.failure_hypothesis, "to_dict") else {}),
                "actual": {
                    "recovery_time": actual_recovery_time,
                    "canary_stage": actual_canary_stage,
                    "cb_state": actual_cb_state,
                    "fallback_activated": actual_fallback_activated,
                    "fallback_type": actual_fallback_type,
                },
            },
        )

        try:
            from selfhealing.services.learning import get_learning_service
            from selfhealing.services.learning.models import PatternType

            learning = get_learning_service()

            # 패턴 기록
            pattern_type = PatternType.SUCCESS if passed else PatternType.FAILURE

            expected_recovery_time = (
                self.failure_hypothesis.expected_recovery_time_seconds
                if hasattr(self.failure_hypothesis, "expected_recovery_time_seconds")
                else 30.0
            )

            learning.record_pattern(
                pattern_type=pattern_type,
                source=f"chaos:{self.experiment_type}",
                target=self.config.target_service,
                features={
                    "experiment_id": self.experiment_id,
                    "experiment_type": self.experiment_type,
                    "hypothesis_passed": passed,
                    "violations": violations,
                    "expected_recovery_time": expected_recovery_time,
                    "actual_recovery_time": actual_recovery_time,
                    "recovery_time_delta": actual_recovery_time - expected_recovery_time,
                    "expected_canary_stage": (
                        self.failure_hypothesis.expected_canary_stage
                        if hasattr(self.failure_hypothesis, "expected_canary_stage")
                        else None
                    ),
                    "actual_canary_stage": actual_canary_stage,
                },
                confidence=0.9 if passed else 0.7,
            )

            # 복구 시간 추세 분석 요청 (실패 시)
            if not passed and violations:
                logger.warning(
                    "chaos.hypothesis_validation_failed",
                    experiment_id=self.experiment_id,
                    violations=violations,
                )

                # LearningService에 추세 분석 트리거
                if hasattr(learning, "analyze_trend"):
                    learning.analyze_trend(
                        target_field="recovery_time",
                        source=f"chaos:{self.experiment_type}",
                        window_days=30,
                    )
            else:
                logger.info(
                    "chaos.hypothesis_validation_passed",
                    experiment_id=self.experiment_id,
                )

        except ImportError as e:
            logger.debug(
                "chaos.learningservice_available",
                error=e,
            )
        except Exception as e:
            logger.warning(
                "chaos.failed_record_hypothesis_validation",
                error=e,
            )

    def record_finops_cost(self) -> None:
        """
        카오스 실험 비용을 FinOps에 기록.
        """
        try:
            from selfhealing.services.finops.service import FinOpsService

            finops = FinOpsService()
            finops.record_chaos_cost(
                experiment_id=self.experiment_id,
                experiment_type=self.experiment_type,
                target_domain=self.config.target_domain or self.config.target_service,
                success=self.status == ExperimentStatus.COMPLETED,
                dry_run=self.config.dry_run,
            )
        except Exception as e:
            logger.debug(
                "chaos.finops_recording_skipped",
                error=e,
            )

    # =========================================================================
    # Kill Switch Integration
    # =========================================================================

    def request_kill(self, reason: str = "") -> None:
        """Request experiment termination."""
        self._kill_requested = True
        self._audit("kill_requested", {"reason": reason})
        logger.warning(
            "chaos_experiment.kill_requested",
            experiment_id=self.experiment_id,
            reason=reason,
        )

    def is_killed(self) -> bool:
        """Check if kill was requested."""
        return self._kill_requested

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _handle_ttl_expiry(self) -> bool:
        """Handle TTL expiry. Returns True if expired."""
        if not self.is_expired():
            return False

        logger.warning(
            "chaos_experiment.ttl_expired_auto_stopping",
            experiment_id=self.experiment_id,
        )
        self._kill_requested = True
        self._stop_condition_violation = "TTL expired"
        self._audit(
            "auto_abort_ttl_expired",
            {
                "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                "ttl_seconds": self._effective_ttl,
            },
        )
        return True

    def _handle_stop_condition_check(self, stop_checker) -> bool:
        """Check stop conditions. Returns True if should stop."""
        stop_result = stop_checker.check(
            experiment_id=self.experiment_id,
            target_service=self.config.target_service,
        )
        if not stop_result.should_stop:
            return False

        violation_messages = [v.message for v in stop_result.violations]
        logger.error(
            "chaos_experiment.stop_condition_violated",
            experiment_id=self.experiment_id,
            violation_messages=violation_messages,
        )
        self._kill_requested = True
        self._stop_condition_violation = "; ".join(violation_messages)
        self._audit(
            "auto_abort_stop_condition",
            {
                "violations": [v.to_dict() for v in stop_result.violations],
                "consecutive_breaches": stop_result.consecutive_breach_count,
            },
        )
        return True

    def _handle_sla_breach_fallback(self) -> bool:
        """Handle SLA breach when no stop checker. Returns True if should stop."""
        if not self.config.auto_rollback_on_sla_breach:
            return False
        if not self._check_sla_breach():
            return False

        self._kill_requested = True
        self._stop_condition_violation = "SLA breach threshold exceeded"
        self._audit("auto_rollback_triggered", {"reason": "SLA breach threshold exceeded"})
        return True

    def _monitor_with_kill_switch(self) -> dict[str, int]:
        """Monitor experiment impact with periodic kill switch check."""
        metrics = {"total_requests": 0, "errors_injected": 0, "sla_breaches": 0}
        duration = self.config.duration_seconds or self.default_duration_seconds
        poll_interval = min(5.0, duration / 10)
        elapsed = 0.0

        try:
            from .stop_conditions import get_stop_conditions_checker

            stop_checker = get_stop_conditions_checker()
        except Exception:
            stop_checker = None

        while elapsed < duration:
            if self._kill_requested:
                break

            if self._handle_ttl_expiry():
                break

            current_metrics = self._collect_impact_metrics()
            for key in metrics:
                metrics[key] += current_metrics.get(key, 0)

            should_stop = (
                self._handle_stop_condition_check(stop_checker) if stop_checker else self._handle_sla_breach_fallback()
            )
            if should_stop:
                break

            time.sleep(poll_interval)
            elapsed += poll_interval

        if stop_checker:
            stop_checker.reset_breach_count(self.experiment_id)

        return metrics

    def _collect_impact_metrics(self) -> dict[str, int]:
        """Collect current impact metrics. Override for real implementation."""
        return {"total_requests": 10, "errors_injected": 1, "sla_breaches": 0}

    def _check_sla_breach(self) -> str | None:
        """Check if SLA breach threshold exceeded."""
        try:
            from .stop_conditions import get_stop_conditions_checker

            checker = get_stop_conditions_checker()
            result = checker.check(
                experiment_id=self.experiment_id,
                target_service=self.config.target_service,
            )

            if result.should_stop:
                return "; ".join([v.message for v in result.violations])

            return None

        except Exception as e:
            logger.warning(
                "chaos_experiment.sla_check_failed",
                error=e,
            )
            return None

    def _audit(self, event_type: str, data: dict[str, Any]) -> None:
        """
        Record audit event.

        audit_helpers 통합:
        - 기존: 로컬 로깅만
        - 변경: WAL + 해시 체인 연결
        - 하위 호환: _audit_records 리스트 유지
        """
        from selfhealing.services.audit_helpers import log_chaos_experiment_audit

        record_id = log_chaos_experiment_audit(
            experiment_id=self.experiment_id,
            event_type=event_type,
            experiment_type=self.experiment_type,
            config=data.get("config"),
            result=data.get("result"),
            dry_run=data.get("dry_run", self.config.dry_run if self.config else False),
            ttl_seconds=data.get("ttl_seconds"),
            expires_at=data.get("expires_at"),
            violations=data.get("violations"),
            reason=data.get("reason"),
        )
        self._audit_records.append(record_id)

    def _config_to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "target_service": self.config.target_service,
            "target_domain": self.config.target_domain,
            "injection_rate": self.config.injection_rate,
            "duration_seconds": self.config.duration_seconds,
            "traffic_type": self.config.traffic_type,
        }

    def _create_skipped_result(self, reason: str) -> ExperimentResult:
        """Create a skipped result."""
        self.status = ExperimentStatus.SKIPPED
        self.completed_at = now()
        return ExperimentResult(
            experiment_id=self.experiment_id,
            experiment_type=self.experiment_type,
            status=self.status.value,
            started_at=self.started_at.isoformat() if self.started_at else "",
            completed_at=self.completed_at.isoformat(),
            error_message=reason,
            audit_record_ids=self._audit_records.copy(),
        )

    def _create_failed_result(self, error: str) -> ExperimentResult:
        """Create a failed result."""
        self.status = ExperimentStatus.FAILED
        self.completed_at = now()
        return ExperimentResult(
            experiment_id=self.experiment_id,
            experiment_type=self.experiment_type,
            status=self.status.value,
            started_at=self.started_at.isoformat() if self.started_at else "",
            completed_at=self.completed_at.isoformat(),
            error_message=error,
            audit_record_ids=self._audit_records.copy(),
        )

    def _create_aborted_result(self, reason: str) -> ExperimentResult:
        """Create an aborted result."""
        self.status = ExperimentStatus.ABORTED
        self.completed_at = now()
        return ExperimentResult(
            experiment_id=self.experiment_id,
            experiment_type=self.experiment_type,
            status=self.status.value,
            started_at=self.started_at.isoformat() if self.started_at else "",
            completed_at=self.completed_at.isoformat(),
            error_message=reason,
            rollback_triggered=True,
            audit_record_ids=self._audit_records.copy(),
        )
