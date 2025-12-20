"""
Chaos Experiment Library

Modular, reusable chaos experiment implementations.
Each experiment is designed for production-safe execution with:
- Kill Switch integration
- Audit trail recording
- Blast radius control
- Automatic rollback on failure

Reference: Netflix ChAP, Gremlin, AWS FIS patterns
"""

from __future__ import annotations

import abc
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from selfhealing.core.timezone import now

logger = logging.getLogger(__name__)


# =============================================================================
# Protocols
# =============================================================================


class AuditRecorderProtocol(Protocol):
    """Protocol for audit recording."""
    
    def record(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record an audit event."""
        ...


class KillSwitchProtocol(Protocol):
    """Protocol for kill switch integration."""
    
    def is_killed(self, experiment_id: str) -> bool:
        """Check if experiment should be killed."""
        ...
    
    def kill(self, experiment_id: str, reason: str) -> None:
        """Kill an experiment."""
        ...


# =============================================================================
# Enums
# =============================================================================


class ExperimentStatus(str, Enum):
    """Experiment lifecycle status."""
    
    PENDING = "pending"
    """Experiment is scheduled but not yet started."""
    
    AWAITING_APPROVAL = "awaiting_approval"
    """High-risk experiment awaiting manual approval."""
    
    RUNNING = "running"
    """Experiment is currently active."""
    
    COMPLETED = "completed"
    """Experiment finished successfully."""
    
    FAILED = "failed"
    """Experiment encountered an error."""
    
    ABORTED = "aborted"
    """Experiment was manually stopped via Kill Switch."""
    
    SKIPPED = "skipped"
    """Experiment was skipped (e.g., low error budget)."""
    
    ROLLED_BACK = "rolled_back"
    """Experiment was rolled back due to issues."""


class ExperimentType(str, Enum):
    """Core experiment types."""
    
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    PACKET_LOSS = "packet_loss"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


class TrafficType(str, Enum):
    """Traffic type for experiment targeting."""
    
    SYNTHETIC = "synthetic"
    """Synthetic/test traffic only."""
    
    SHADOW = "shadow"
    """Shadow/mirrored production traffic."""
    
    CANARY = "canary"
    """Small percentage of real traffic."""
    
    PRODUCTION = "production"
    """Full production traffic."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ExperimentConfig:
    """Configuration for a chaos experiment."""
    
    # Target configuration
    target_service: str = ""
    target_domain: str = ""
    target_instances: List[str] = field(default_factory=list)
    
    # Injection parameters
    injection_rate: float = 0.001  # 0.1% default
    duration_seconds: int = 300  # 5 minutes
    
    # Traffic targeting
    traffic_type: str = TrafficType.SYNTHETIC.value
    
    # Rollback configuration
    auto_rollback_on_sla_breach: bool = True
    sla_breach_threshold_percent: float = 1.0  # 1% error rate triggers rollback
    
    # Additional parameters (experiment-specific)
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    # TTL (Self-Expiration) configuration
    ttl_seconds: Optional[int] = None
    """실험 자동 만료 시간 (초). None이면 기본값 사용."""
    
    # Dry Run mode
    dry_run: bool = False
    """True이면 실제 장애 주입 없이 시뮬레이션만 수행."""


@dataclass
class ExperimentResult:
    """Result of a chaos experiment execution."""
    
    experiment_id: str
    experiment_type: str
    status: str
    
    # Timing
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    
    # Impact metrics
    total_requests_affected: int = 0
    errors_injected: int = 0
    sla_breaches: int = 0
    
    # Recovery metrics
    recovery_time_seconds: float = 0.0
    auto_recovered: bool = False
    rollback_triggered: bool = False
    
    # Steady state validation
    steady_state_before: Dict[str, Any] = field(default_factory=dict)
    steady_state_after: Dict[str, Any] = field(default_factory=dict)
    steady_state_hypothesis_passed: bool = True
    
    # Forensic analysis
    forensic_advisory: Dict[str, Any] = field(default_factory=dict)
    
    # Errors
    error_message: str = ""
    
    # Audit
    audit_record_ids: List[str] = field(default_factory=list)
    
    # Dry Run / TTL info
    dry_run: bool = False
    """True이면 실제 장애 주입 없이 시뮬레이션만 수행됨."""
    
    ttl_seconds: int = 0
    """사용된 TTL 값 (초)."""
    
    expires_at: str = ""
    """카오스 설정 만료 시간 (ISO format)."""
    
    auto_expired: bool = False
    """TTL에 의해 자동 만료되었는지 여부."""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "experiment_type": self.experiment_type,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "total_requests_affected": self.total_requests_affected,
            "errors_injected": self.errors_injected,
            "sla_breaches": self.sla_breaches,
            "recovery_time_seconds": self.recovery_time_seconds,
            "auto_recovered": self.auto_recovered,
            "rollback_triggered": self.rollback_triggered,
            "steady_state_before": self.steady_state_before,
            "steady_state_after": self.steady_state_after,
            "steady_state_hypothesis_passed": self.steady_state_hypothesis_passed,
            "forensic_advisory": self.forensic_advisory,
            "error_message": self.error_message,
            "audit_record_ids": self.audit_record_ids,
            # Dry Run / TTL info
            "dry_run": self.dry_run,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "auto_expired": self.auto_expired,
        }


@dataclass
class SteadyStateHypothesis:
    """Defines what 'normal' looks like for steady state validation."""
    
    # Latency thresholds (ms)
    p50_latency_max_ms: float = 100.0
    p99_latency_max_ms: float = 500.0
    
    # Error rate thresholds (%)
    error_rate_max_percent: float = 0.1
    
    # Throughput thresholds (rps)
    throughput_min_rps: float = 100.0
    
    # Custom metrics
    custom_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    def validate(self, metrics: Dict[str, float]) -> tuple[bool, List[str]]:
        """
        Validate metrics against hypothesis.
        
        Returns:
            Tuple of (passed, list of violations)
        """
        violations = []
        
        if metrics.get("p50_latency_ms", 0) > self.p50_latency_max_ms:
            violations.append(
                f"p50 latency {metrics['p50_latency_ms']:.1f}ms > {self.p50_latency_max_ms}ms"
            )
        
        if metrics.get("p99_latency_ms", 0) > self.p99_latency_max_ms:
            violations.append(
                f"p99 latency {metrics['p99_latency_ms']:.1f}ms > {self.p99_latency_max_ms}ms"
            )
        
        if metrics.get("error_rate_percent", 0) > self.error_rate_max_percent:
            violations.append(
                f"error rate {metrics['error_rate_percent']:.2f}% > {self.error_rate_max_percent}%"
            )
        
        if metrics.get("throughput_rps", float("inf")) < self.throughput_min_rps:
            violations.append(
                f"throughput {metrics['throughput_rps']:.1f} rps < {self.throughput_min_rps} rps"
            )
        
        for metric_name, thresholds in self.custom_metrics.items():
            value = metrics.get(metric_name, 0)
            if "min" in thresholds and value < thresholds["min"]:
                violations.append(f"{metric_name} {value} < min {thresholds['min']}")
            if "max" in thresholds and value > thresholds["max"]:
                violations.append(f"{metric_name} {value} > max {thresholds['max']}")
        
        return len(violations) == 0, violations


# =============================================================================
# Base Chaos Experiment
# =============================================================================


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
        experiment_id: Optional[str] = None,
        config: Optional[ExperimentConfig] = None,
        hypothesis: Optional[SteadyStateHypothesis] = None,
    ):
        """Initialize experiment."""
        self.experiment_id = experiment_id or f"chaos-{uuid.uuid4().hex[:12]}"
        self.config = config or ExperimentConfig()
        self.hypothesis = hypothesis or SteadyStateHypothesis()
        
        # State
        self.status = ExperimentStatus.PENDING
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        
        # Results
        self.result: Optional[ExperimentResult] = None
        self._kill_requested = False
        self._audit_records: List[str] = []
        
        # TTL state
        self._expires_at: Optional[datetime] = None
        self._effective_ttl: int = 0
        
        # Rollback state (for idempotency)
        self._rollback_completed = False
        self._rollback_lock = threading.Lock()
        
        # Stop conditions monitoring
        self._stop_condition_violation: Optional[str] = None
    
    # =========================================================================
    # TTL (Self-Expiration) Methods
    # =========================================================================
    
    def get_effective_ttl(self) -> int:
        """
        Get effective TTL for this experiment.
        
        Priority: config.ttl_seconds > default_ttl_seconds > global TTL config
        """
        if self.config.ttl_seconds is not None:
            return self.config.ttl_seconds
        
        # Try to get from global config
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
    # Dry Run Methods
    # =========================================================================
    
    def _should_dry_run(self) -> bool:
        """Check if this experiment should run in dry run mode."""
        # Config-level override
        if self.config.dry_run:
            return True
        
        # Global dry run mode
        try:
            from .stop_conditions import get_dry_run_config
            dry_run_config = get_dry_run_config()
            return dry_run_config.enabled
        except Exception:
            return False
    
    def _run_dry(self) -> ExperimentResult:
        """
        Execute experiment in dry run mode.
        
        Performs all validations without actual chaos injection.
        """
        logger.info(f"[DryRun] Starting dry run for {self.experiment_id}")
        
        # Calculate TTL (for simulation)
        self._calculate_expires_at()
        
        # Pre-flight check
        if not self.pre_flight_check():
            return self._create_skipped_result("Pre-flight check failed (dry run)")
        
        # Simulate steady state capture
        steady_state_before = self.capture_steady_state()
        self._audit("steady_state_captured", {"phase": "before", "metrics": steady_state_before, "dry_run": True})
        
        # Simulate injection (no actual injection)
        self._audit("chaos_injection_simulated", {
            "dry_run": True,
            "would_inject": self._config_to_dict(),
            "ttl_seconds": self._effective_ttl,
            "expires_at": self._expires_at.isoformat() if self._expires_at else "",
        })
        
        logger.info(
            f"[DryRun] Would inject chaos to {self.config.target_service} "
            f"with TTL {self._effective_ttl}s (expires at {self._expires_at})"
        )
        
        # Simulate duration wait (shortened for dry run)
        duration = min(5, self.config.duration_seconds or self.default_duration_seconds)
        import time
        time.sleep(duration)
        
        # Simulate steady state after
        steady_state_after = self.capture_steady_state()
        
        self.completed_at = now()
        self.status = ExperimentStatus.COMPLETED
        
        self.result = ExperimentResult(
            experiment_id=self.experiment_id,
            experiment_type=self.experiment_type,
            status=self.status.value,
            started_at=self.started_at.isoformat() if self.started_at else "",
            completed_at=self.completed_at.isoformat(),
            duration_seconds=(self.completed_at - self.started_at).total_seconds() if self.started_at else 0,
            steady_state_before=steady_state_before,
            steady_state_after=steady_state_after,
            steady_state_hypothesis_passed=True,
            audit_record_ids=self._audit_records.copy(),
            dry_run=True,
            ttl_seconds=self._effective_ttl,
            expires_at=self._expires_at.isoformat() if self._expires_at else "",
        )
        
        self._audit("experiment_completed", {"result": self.result.to_dict(), "dry_run": True})
        logger.info(f"[DryRun] Completed dry run for {self.experiment_id} - no actual chaos injected")
        
        return self.result
    
    # =========================================================================
    # Template Method - Main Execution Flow
    # =========================================================================
    
    def execute(self) -> ExperimentResult:
        """
        Execute the chaos experiment.
        
        This is the main entry point that orchestrates the experiment lifecycle.
        Supports dry run mode and TTL-based auto-expiration.
        """
        self.started_at = now()
        self.status = ExperimentStatus.RUNNING
        
        # Check if we should run in dry run mode
        if self._should_dry_run():
            return self._run_dry()
        
        try:
            # 0. Calculate TTL and expiration time
            self._calculate_expires_at()
            
            # 1. Pre-flight check
            self._audit("experiment_started", {
                "config": self._config_to_dict(),
                "ttl_seconds": self._effective_ttl,
                "expires_at": self._expires_at.isoformat() if self._expires_at else "",
            })
            
            if not self.pre_flight_check():
                return self._create_skipped_result("Pre-flight check failed")
            
            # 2. Capture steady state BEFORE
            steady_state_before = self.capture_steady_state()
            self._audit("steady_state_captured", {"phase": "before", "metrics": steady_state_before})
            
            # 3. Inject chaos (with TTL)
            self._audit("chaos_injection_started", {
                "ttl_seconds": self._effective_ttl,
                "expires_at": self._expires_at.isoformat() if self._expires_at else "",
            })
            injection_result = self.inject_chaos()
            
            if not injection_result:
                return self._create_failed_result("Chaos injection failed")
            
            # 4. Monitor impact (with kill switch and stop conditions check)
            impact_metrics = self._monitor_with_kill_switch()
            
            if self._kill_requested:
                self.rollback()
                abort_reason = "Kill switch activated"
                if self._stop_condition_violation:
                    abort_reason = f"Stop condition violated: {self._stop_condition_violation}"
                return self._create_aborted_result(abort_reason)
            
            # 5. Rollback / cleanup
            self._audit("rollback_started", {})
            self.rollback()
            self._audit("rollback_completed", {})
            
            # 6. Wait for recovery and validate
            recovery_time = self.validate_recovery()
            
            # 7. Capture steady state AFTER
            steady_state_after = self.capture_steady_state()
            self._audit("steady_state_captured", {"phase": "after", "metrics": steady_state_after})
            
            # 8. Validate hypothesis
            hypothesis_passed, violations = self.hypothesis.validate(steady_state_after)
            
            # 9. Generate result
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
            
            return self.result
            
        except Exception as e:
            logger.exception(f"[ChaosExperiment] Error in {self.experiment_id}: {e}")
            self.rollback()
            return self._create_failed_result(str(e))
    
    # =========================================================================
    # Abstract Methods - Must be implemented by subclasses
    # =========================================================================
    
    @abc.abstractmethod
    def inject_chaos(self) -> bool:
        """
        Inject the chaos condition.
        
        Returns:
            True if injection succeeded, False otherwise
        """
        pass
    
    @abc.abstractmethod
    def rollback(self) -> None:
        """
        Rollback the chaos injection and restore normal state.
        """
        pass
    
    # =========================================================================
    # Optional Overrides
    # =========================================================================
    
    def pre_flight_check(self) -> bool:
        """
        Validate preconditions before starting experiment.
        
        Override to add custom checks.
        
        Returns:
            True if all checks pass
        """
        # Check kill switch
        if self._kill_requested:
            logger.warning(f"[ChaosExperiment] {self.experiment_id} - Kill requested before start")
            return False
        
        return True
    
    def capture_steady_state(self) -> Dict[str, float]:
        """
        Capture current system metrics for steady state comparison.
        
        Override to capture service-specific metrics.
        
        Returns:
            Dictionary of metric name -> value
        """
        # Default implementation - override for real metrics
        return {
            "p50_latency_ms": 50.0,
            "p99_latency_ms": 200.0,
            "error_rate_percent": 0.01,
            "throughput_rps": 500.0,
        }
    
    def validate_recovery(self) -> float:
        """
        Wait for system to recover and measure recovery time.
        
        Returns:
            Recovery time in seconds
        """
        import time
        
        start = now()
        max_wait = 60  # Maximum 60 seconds
        poll_interval = 1.0
        
        while (now() - start).total_seconds() < max_wait:
            metrics = self.capture_steady_state()
            passed, _ = self.hypothesis.validate(metrics)
            if passed:
                return (now() - start).total_seconds()
            time.sleep(poll_interval)
        
        return -1.0  # Recovery timeout
    
    # =========================================================================
    # Kill Switch Integration
    # =========================================================================
    
    def request_kill(self, reason: str = "") -> None:
        """Request experiment termination."""
        self._kill_requested = True
        self._audit("kill_requested", {"reason": reason})
        logger.warning(f"[ChaosExperiment] Kill requested for {self.experiment_id}: {reason}")
    
    def is_killed(self) -> bool:
        """Check if kill was requested."""
        return self._kill_requested
    
    # =========================================================================
    # Internal Helpers
    # =========================================================================
    
    def _monitor_with_kill_switch(self) -> Dict[str, int]:
        """
        Monitor experiment impact with periodic kill switch check.
        
        Also checks:
        - Stop Conditions (SLA breach)
        - TTL expiration
        """
        import time
        
        metrics = {
            "total_requests": 0,
            "errors_injected": 0,
            "sla_breaches": 0,
        }
        
        duration = self.config.duration_seconds or self.default_duration_seconds
        poll_interval = min(5.0, duration / 10)  # Check at least 10 times
        elapsed = 0.0
        
        # Get stop conditions checker
        try:
            from .stop_conditions import get_stop_conditions_checker
            stop_checker = get_stop_conditions_checker()
        except Exception:
            stop_checker = None
        
        while elapsed < duration:
            # 1. Check kill switch
            if self._kill_requested:
                break
            
            # 2. Check TTL expiration
            if self.is_expired():
                logger.warning(f"[ChaosExperiment] {self.experiment_id} - TTL expired, auto-stopping")
                self._kill_requested = True
                self._stop_condition_violation = "TTL expired"
                self._audit("auto_abort_ttl_expired", {
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                })
                break
            
            # 3. Update metrics
            current_metrics = self._collect_impact_metrics()
            for key in metrics:
                metrics[key] += current_metrics.get(key, 0)
            
            # 4. Check Stop Conditions (via Stop Conditions Checker)
            if stop_checker:
                stop_result = stop_checker.check(
                    experiment_id=self.experiment_id,
                    target_service=self.config.target_service,
                )
                if stop_result.should_stop:
                    violation_messages = [v.message for v in stop_result.violations]
                    logger.error(
                        f"[ChaosExperiment] {self.experiment_id} - Stop condition violated: {violation_messages}"
                    )
                    self._kill_requested = True
                    self._stop_condition_violation = "; ".join(violation_messages)
                    self._audit("auto_abort_stop_condition", {
                        "violations": [v.to_dict() for v in stop_result.violations],
                        "consecutive_breaches": stop_result.consecutive_breach_count,
                    })
                    break
            
            # 5. Legacy SLA breach check (fallback)
            elif self.config.auto_rollback_on_sla_breach:
                if self._check_sla_breach():
                    self._kill_requested = True
                    self._stop_condition_violation = "SLA breach threshold exceeded"
                    self._audit("auto_rollback_triggered", {"reason": "SLA breach threshold exceeded"})
                    break
            
            time.sleep(poll_interval)
            elapsed += poll_interval
        
        # Cleanup stop conditions checker state
        if stop_checker:
            stop_checker.reset_breach_count(self.experiment_id)
        
        return metrics
    
    def _collect_impact_metrics(self) -> Dict[str, int]:
        """Collect current impact metrics. Override for real implementation."""
        return {"total_requests": 10, "errors_injected": 1, "sla_breaches": 0}
    
    def _check_sla_breach(self) -> Optional[str]:
        """
        Check if SLA breach threshold exceeded.
        
        Returns:
            Breach reason string if breached, None otherwise.
        """
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
            logger.warning(f"[ChaosExperiment] SLA check failed: {e}")
            return None
    
    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record audit event."""
        record_id = f"audit-{uuid.uuid4().hex[:8]}"
        self._audit_records.append(record_id)
        
        # Log for now - integrate with DecisionRecord in production
        logger.info(
            f"[ChaosAudit] {self.experiment_id} | {event_type} | {record_id}",
            extra={"audit_data": data}
        )
    
    def _config_to_dict(self) -> Dict[str, Any]:
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


# =============================================================================
# Concrete Experiment Implementations
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
            # Store original state for rollback
            self._original_state = self._get_current_chaos_config()
            
            # Apply latency injection via chaos config with TTL
            self._apply_chaos_config({
                "latency_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "latency_ms": self.latency_ms,
                    "jitter_ms": self.latency_jitter_ms,
                    "rate": self.config.injection_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    # TTL for self-expiration
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            
            return True
            
        except Exception as e:
            logger.error(f"[LatencyInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """
        Remove latency injection with idempotency.
        
        Can be called multiple times safely - only executes once.
        """
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[LatencyInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[LatencyInjection] Rolling back {self.experiment_id}")
            
            try:
                self._apply_chaos_config({
                    "latency_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[LatencyInjection] Rollback failed: {e}")
    
    def _get_current_chaos_config(self) -> Dict[str, Any]:
        """Get current chaos configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            return manager.get_chaos_config()
        except Exception:
            return {}
    
    def _apply_chaos_config(self, config: Dict[str, Any]) -> None:
        """Apply chaos configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(**config)
        except Exception as e:
            logger.warning(f"[LatencyInjection] Could not apply config via RuntimeConfig: {e}")
            # Fallback to in-memory state
            pass


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
            self._apply_chaos_config({
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
                self._apply_chaos_config({
                    "error_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Error5xxInjection] Rollback failed: {e}")
    
    def _apply_chaos_config(self, config: Dict[str, Any]) -> None:
        """Apply chaos configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(**config)
        except Exception:
            pass


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
            self._apply_chaos_config({
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
                self._apply_chaos_config({
                    "packet_loss": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PacketLoss] Rollback failed: {e}")
    
    def _apply_chaos_config(self, config: Dict[str, Any]) -> None:
        """Apply chaos configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(**config)
        except Exception:
            pass


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
            self._apply_chaos_config({
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
                self._apply_chaos_config({
                    "timeout_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Timeout] Rollback failed: {e}")
    
    def _apply_chaos_config(self, config: Dict[str, Any]) -> None:
        """Apply chaos configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(**config)
        except Exception:
            pass


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
            self._apply_chaos_config({
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
                self._apply_chaos_config({
                    "resource_exhaustion": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ResourceExhaustion] Rollback failed: {e}")
    
    def _apply_chaos_config(self, config: Dict[str, Any]) -> None:
        """Apply chaos configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(**config)
        except Exception:
            pass


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
