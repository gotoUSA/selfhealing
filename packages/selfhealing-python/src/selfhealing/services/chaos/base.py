"""
Chaos Experiment Base Classes and Data Structures

Contains core abstractions for chaos experiments:
- Protocols (AuditRecorderProtocol, KillSwitchProtocol)
- Enums (ExperimentStatus, ExperimentType, TrafficType)
- Data classes (ExperimentConfig, ExperimentResult, SteadyStateHypothesis)
- Base class (ChaosExperiment)

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
from typing import Any, Callable, Dict, List, Optional, Protocol

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
        logger.info(f"[DryRun] Starting dry run for {self.experiment_id}")
        
        self._calculate_expires_at()
        
        if not self.pre_flight_check():
            return self._create_skipped_result("Pre-flight check failed (dry run)")
        
        steady_state_before = self.capture_steady_state()
        self._audit("steady_state_captured", {"phase": "before", "metrics": steady_state_before, "dry_run": True})
        
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
        
        import time
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
        """Execute the chaos experiment."""
        self.started_at = now()
        self.status = ExperimentStatus.RUNNING
        
        if self._should_dry_run():
            return self._run_dry()
        
        try:
            self._calculate_expires_at()
            
            self._audit("experiment_started", {
                "config": self._config_to_dict(),
                "ttl_seconds": self._effective_ttl,
                "expires_at": self._expires_at.isoformat() if self._expires_at else "",
            })
            
            if not self.pre_flight_check():
                return self._create_skipped_result("Pre-flight check failed")
            
            steady_state_before = self.capture_steady_state()
            self._audit("steady_state_captured", {"phase": "before", "metrics": steady_state_before})
            
            self._audit("chaos_injection_started", {
                "ttl_seconds": self._effective_ttl,
                "expires_at": self._expires_at.isoformat() if self._expires_at else "",
            })
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
            self._audit("steady_state_captured", {"phase": "after", "metrics": steady_state_after})
            
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
            logger.warning(f"[ChaosExperiment] {self.experiment_id} - Kill requested before start")
            return False
        return True
    
    def capture_steady_state(self) -> Dict[str, float]:
        """Capture current system metrics for steady state comparison."""
        return {
            "p50_latency_ms": 50.0,
            "p99_latency_ms": 200.0,
            "error_rate_percent": 0.01,
            "throughput_rps": 500.0,
        }
    
    def validate_recovery(self) -> float:
        """Wait for system to recover and measure recovery time."""
        import time
        
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
        """Monitor experiment impact with periodic kill switch check."""
        import time
        
        metrics = {
            "total_requests": 0,
            "errors_injected": 0,
            "sla_breaches": 0,
        }
        
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
            
            if self.is_expired():
                logger.warning(f"[ChaosExperiment] {self.experiment_id} - TTL expired, auto-stopping")
                self._kill_requested = True
                self._stop_condition_violation = "TTL expired"
                self._audit("auto_abort_ttl_expired", {
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                })
                break
            
            current_metrics = self._collect_impact_metrics()
            for key in metrics:
                metrics[key] += current_metrics.get(key, 0)
            
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
            elif self.config.auto_rollback_on_sla_breach:
                if self._check_sla_breach():
                    self._kill_requested = True
                    self._stop_condition_violation = "SLA breach threshold exceeded"
                    self._audit("auto_rollback_triggered", {"reason": "SLA breach threshold exceeded"})
                    break
            
            time.sleep(poll_interval)
            elapsed += poll_interval
        
        if stop_checker:
            stop_checker.reset_breach_count(self.experiment_id)
        
        return metrics
    
    def _collect_impact_metrics(self) -> Dict[str, int]:
        """Collect current impact metrics. Override for real implementation."""
        return {"total_requests": 10, "errors_injected": 1, "sla_breaches": 0}
    
    def _check_sla_breach(self) -> Optional[str]:
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
            logger.warning(f"[ChaosExperiment] SLA check failed: {e}")
            return None
    
    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Record audit event.
        
        Phase 2: audit_helpers 통합 (20_AUDIT_UNIFICATION_PLAN.md)
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
# Runtime Config Helper
# =============================================================================


def _apply_chaos_config(config: Dict[str, Any]) -> None:
    """Apply chaos configuration via RuntimeConfigManager."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        manager.update_chaos_config(**config)
    except Exception as e:
        logger.warning(f"[ChaosExperiment] Could not apply config via RuntimeConfig: {e}")


def _get_current_chaos_config() -> Dict[str, Any]:
    """Get current chaos configuration."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        return manager.get_chaos_config()
    except Exception:
        return {}
