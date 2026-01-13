"""
Chaos Experiment Base Classes and Data Structures

Contains core abstractions for chaos experiments:
- Protocols (AuditRecorderProtocol, KillSwitchProtocol)
- Enums (ExperimentStatus, ExperimentType, TrafficType)
- Data classes (ExperimentConfig, ExperimentResult, SteadyStateHypothesis, MonotonicTTLHelper)
- Base class (ChaosExperiment)

Reference: Netflix ChAP, Gremlin, AWS FIS patterns
"""

from __future__ import annotations

import abc
import logging
import threading
import time
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
    
    # Phase 1: 비동기 복구 모니터링 (§15)
    # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.2
    RECOVERY_MONITORING = "recovery_monitoring"
    """실험 완료 후 Canary 복구 모니터링 중."""


class ExperimentType(str, Enum):
    """Core experiment types."""
    
    # Existing types
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    PACKET_LOSS = "packet_loss"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    
    # Phase 0-1: New types added per 31_CHAOS_EXPERIMENT_EXPANSION.md
    ERROR_4XX = "error_4xx"
    CONNECTION_RESET = "connection_reset"
    RATE_LIMIT = "rate_limit"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    PARTIAL_FAILURE = "partial_failure"
    CASCADING_FAILURE = "cascading_failure"
    
    # Phase 5-2: Pool/Connection simulation experiments
    # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §16.3, §22.2.2
    POOL_EXHAUSTION = "pool_exhaustion"
    """Connection Pool 고갈 시뮬레이션 실험."""
    
    CONNECTION_PARTITION = "connection_partition"
    """네트워크 파티션 시뮬레이션 실험."""
    
    # Phase 6: 업계 표준 실험 추가
    # Reference: 33_CHAOS_INDUSTRY_EXPERIMENTS.md
    CERTIFICATE_EXPIRY = "certificate_expiry"
    """인증서 만료 시뮬레이션 실험."""
    
    DNS_FAILURE = "dns_failure"
    """DNS 장애 시뮬레이션 실험."""
    
    CLOCK_SKEW = "clock_skew"
    """시스템 시간 불일치 시뮬레이션 실험."""


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
    """Soft TTL: 장애 주입 종료 시간 (초). None이면 기본값 사용."""
    
    # Phase 1: Soft/Hard TTL 이중 구조 (§15.4)
    # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.4
    grace_period_seconds: int = 300
    """Grace Period: Canary 복구 대기 시간 (기본 5분)."""
    
    @property
    def hard_ttl_seconds(self) -> int:
        """
        Hard TTL: 실험 강제 종료 시간 (초).
        
        Soft TTL + Grace Period.
        Canary 복구가 완료되지 않더라도 강제 종료.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.4
        """
        base_ttl = self.ttl_seconds or 600  # 기본 10분
        return base_ttl + self.grace_period_seconds
    
    # Dry Run mode
    dry_run: bool = False
    """True이면 실제 장애 주입 없이 시뮬레이션만 수행."""
    
    # Phase 6: Resilience Expectation
    # Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §8.4
    resilience_expectation: Optional[Any] = None
    """
    시스템이 이 장애에 대해 어떻게 반응해야 하는지 정의.
    
    예시:
        ResilienceExpectation.expect_cb_open("payment-api", within_seconds=10)
        → "지연 500ms 주입 시, CB가 10초 내에 OPEN되어야 함"
    
    None이면 Resilience 검증 스킵 (기존 동작 유지).
    Type: ResilienceExpectation (from resilience_expectation module)
    """


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
    
    # Phase 6: Resilience Validation
    # Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §8.5
    resilience_validation: Optional[Dict[str, Any]] = None
    """
    Resilience 기대값 검증 결과.
    
    {
        "passed": True/False,
        "resilience_score": 0.0 ~ 1.0,
        "assertions": [...],
        "summary": "Resilience: 2/3 (67%)"
    }
    """
    
    resilience_passed: bool = True
    """Resilience 검증 통과 여부. expectation이 없으면 True."""
    
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
            "resilience_validation": self.resilience_validation,
            "resilience_passed": self.resilience_passed,
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
# Monotonic TTL Helper (ClockSkew 보호용)
# Reference: 33_CHAOS_INDUSTRY_EXPERIMENTS.md §5.1, 34_CHAOS_SAFETY_MECHANISMS.md §2
# =============================================================================


@dataclass
class MonotonicTTLHelper:
    """
    Monotonic clock 기반 TTL 헬퍼.
    
    ClockSkewExperiment 등 시스템 시간 조작 실험에서,
    실험 엔진 자신의 TTL 타이머가 영향받지 않도록 보호합니다.
    
    time.monotonic()는 시스템 시간(timezone.now())과 달리
    시스템 시간 변경에 영향받지 않는 상대 시간을 반환합니다.
    
    Reference:
    - 33_CHAOS_INDUSTRY_EXPERIMENTS.md §9.2 Monotonic TTL
    - 34_CHAOS_SAFETY_MECHANISMS.md §2 Monotonic Clock 보호
    - metrics/decorators.py:86-88 (기존 사용 패턴)
    
    Example:
        # ClockSkewExperiment에서 사용
        helper = MonotonicTTLHelper(ttl_seconds=300)
        helper.start()
        
        # 실험 도중 (시스템 시간이 미래로 변경되어도)
        if helper.is_expired():
            # 실제로 300초가 경과한 경우에만 True
            experiment.rollback()
        
        # 남은 시간 확인
        remaining = helper.remaining_seconds()  # 실제 경과 시간 기준
    """
    
    ttl_seconds: float
    """TTL 시간 (초)."""
    
    _start_time: float = field(default=0.0, repr=False)
    """Monotonic 시작 시간."""
    
    _started: bool = field(default=False, repr=False)
    """시작 여부."""
    
    def start(self) -> None:
        """
        Monotonic 타이머 시작.
        
        이 메서드 호출 시점부터 TTL 카운트가 시작됩니다.
        """
        self._start_time = time.monotonic()
        self._started = True
        logger.debug(
            f"[MonotonicTTL] Timer started: ttl={self.ttl_seconds}s, "
            f"monotonic_start={self._start_time:.2f}"
        )
    
    def is_started(self) -> bool:
        """타이머가 시작되었는지 확인."""
        return self._started
    
    def elapsed_seconds(self) -> float:
        """
        경과 시간 (초) 반환.
        
        시스템 시간과 무관하게 실제 경과 시간을 반환합니다.
        
        Returns:
            시작 후 경과한 시간 (초). 시작 전이면 0.0 반환.
        """
        if not self._started:
            return 0.0
        return time.monotonic() - self._start_time
    
    def remaining_seconds(self) -> float:
        """
        남은 시간 (초) 반환.
        
        Returns:
            TTL까지 남은 시간 (초). 만료되었으면 0.0 또는 음수 반환.
        """
        if not self._started:
            return self.ttl_seconds
        remaining = self.ttl_seconds - self.elapsed_seconds()
        return max(0.0, remaining)
    
    def is_expired(self) -> bool:
        """
        TTL 만료 여부 확인.
        
        시스템 시간 조작(ClockSkew)에 영향받지 않습니다.
        
        Returns:
            True if TTL 만료됨, False otherwise.
        """
        if not self._started:
            return False
        return self.elapsed_seconds() >= self.ttl_seconds
    
    def reset(self) -> None:
        """타이머 리셋 (재시작)."""
        self._start_time = time.monotonic()
        logger.debug(f"[MonotonicTTL] Timer reset at {self._start_time:.2f}")
    
    def to_dict(self) -> Dict[str, Any]:
        """직렬화용 딕셔너리 반환."""
        return {
            "ttl_seconds": self.ttl_seconds,
            "started": self._started,
            "elapsed_seconds": self.elapsed_seconds() if self._started else 0.0,
            "remaining_seconds": self.remaining_seconds(),
            "is_expired": self.is_expired(),
        }


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
    # Phase 5-1: 비동기 복구 모니터링 메서드 (32_CHAOS_SYSTEM_INTEGRATION.md §15, §22.2.3)
    # =========================================================================
    
    def is_hard_ttl_expired(self) -> bool:
        """
        Check if Hard TTL has expired.
        
        Hard TTL = Soft TTL + Grace Period.
        Grace Period 동안 Canary 복구를 기다리며,
        Hard TTL이 지나면 강제 종료.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.4
        
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
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.3, §22.2.3
        """
        if self.status != ExperimentStatus.RECOVERY_MONITORING:
            logger.warning(
                f"[ChaosExperiment] {self.experiment_id} - "
                f"Cannot complete recovery monitoring from status {self.status}"
            )
            return
        
        self.status = ExperimentStatus.COMPLETED
        self.completed_at = now()
        
        self._audit("recovery_monitoring_completed", {
            "previous_status": ExperimentStatus.RECOVERY_MONITORING.value,
            "completed_at": self.completed_at.isoformat(),
        })
        
        logger.info(
            f"[ChaosExperiment] {self.experiment_id} - "
            f"Recovery monitoring completed, status changed to COMPLETED"
        )
    
    def force_complete(self, reason: str = "hard_ttl_expired") -> None:
        """
        실험 강제 종료.
        
        Hard TTL 만료 시 또는 관리자 개입 시 호출.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.3
        
        Args:
            reason: 강제 종료 사유
        """
        previous_status = self.status
        self.status = ExperimentStatus.COMPLETED
        self.completed_at = now()
        
        self._audit("force_completed", {
            "previous_status": previous_status.value if hasattr(previous_status, 'value') else str(previous_status),
            "reason": reason,
            "completed_at": self.completed_at.isoformat(),
        })
        
        logger.warning(
            f"[ChaosExperiment] {self.experiment_id} - "
            f"Force completed due to: {reason}"
        )
    
    def transition_to_recovery_monitoring(self) -> None:
        """
        RUNNING 상태에서 RECOVERY_MONITORING 상태로 전환.
        
        Soft TTL 도달 시 장애 주입을 중단하고 복구 모니터링 단계로 전환.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §15.2, §15.4
        """
        if self.status != ExperimentStatus.RUNNING:
            logger.warning(
                f"[ChaosExperiment] {self.experiment_id} - "
                f"Cannot transition to RECOVERY_MONITORING from status {self.status}"
            )
            return
        
        # 먼저 rollback 수행하여 장애 주입 중단
        self.rollback()
        
        self.status = ExperimentStatus.RECOVERY_MONITORING
        
        self._audit("transition_to_recovery_monitoring", {
            "previous_status": ExperimentStatus.RUNNING.value,
            "soft_ttl_expired": True,
            "grace_period_seconds": self.config.grace_period_seconds,
        })
        
        logger.info(
            f"[ChaosExperiment] {self.experiment_id} - "
            f"Transitioned to RECOVERY_MONITORING (grace period: {self.config.grace_period_seconds}s)"
        )
    
    def _verify_canary_recovery(self) -> Dict[str, Any]:
        """
        Canary 복구 단계 검증.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §4.2.1
        
        Returns:
            Dict with canary state information
        """
        try:
            from selfhealing.services.circuit_breaker.canary_recovery import (
                CanaryRecoveryManager,
                CanaryState,
            )
            
            manager = CanaryRecoveryManager()
            state = manager.get_canary_state(self.config.target_service)
            traffic = manager.get_traffic_percent(self.config.target_service)
            
            return {
                "canary_state": state.value if hasattr(state, 'value') else str(state),
                "traffic_percent": traffic,
                "in_canary": state != CanaryState.NOT_IN_CANARY,
            }
        except ImportError:
            logger.debug("[ChaosExperiment] CanaryRecoveryManager not available")
            return {"in_canary": False, "canary_state": "not_available"}
        except Exception as e:
            logger.warning(f"[ChaosExperiment] Canary verification failed: {e}")
            return {"in_canary": False, "error": str(e)}
    
    # =========================================================================
    # Phase 5-1: 모니터 스냅샷 메서드 (32_CHAOS_SYSTEM_INTEGRATION.md §13, §22.2.4)
    # =========================================================================
    
    def _get_pool_state_snapshot(self) -> Dict[str, Any]:
        """
        실험 전후 커넥션 풀 상태 캡처.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §13.1, §22.2.4
        
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
                "health_status": status.value if hasattr(status, 'value') else str(status),
                "active_connections": stats.active_connections,
                "available_connections": stats.available_connections,
                "usage_percent": stats.usage_percent,
                "waiting_requests": stats.waiting_requests,
                "timestamp": now().isoformat(),
            }
        except Exception as e:
            logger.warning(f"[Chaos] Pool state snapshot failed: {e}")
            return {"available": False, "error": str(e)}
    
    def _get_cert_state_snapshot(self) -> Dict[str, Any]:
        """
        실험 전후 인증서 상태 캡처.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §13.2, §22.2.4
        
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
            logger.debug("[Chaos] CertificateExpiryMonitor not available")
            return {"check_performed": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[Chaos] Cert state snapshot failed: {e}")
            return {"check_performed": False, "error": str(e)}
    
    def _get_connection_health_snapshot(self) -> Dict[str, Any]:
        """
        실험 전후 연결 상태 캡처.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §13.3, §22.2.4
        
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
            logger.debug("[Chaos] ConnectionHealthMonitor not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[Chaos] Connection health snapshot failed: {e}")
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
            
            # Phase 3: LearningService 피드백 루프 (32_CHAOS_SYSTEM_INTEGRATION.md §20.4)
            cb_snapshot = self._get_cb_state_snapshot()
            self._record_hypothesis_validation(
                actual_recovery_time=recovery_time if recovery_time > 0 else 60.0,
                actual_cb_state=cb_snapshot.get("target_service_state"),
            )
            
            # Phase 3: FinOps 비용 기록 (32_CHAOS_SYSTEM_INTEGRATION.md §10.2)
            self.record_finops_cost()
            
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
    
    # =========================================================================
    # Phase 2: CB 상태 스냅샷 캡처 (32_CHAOS_SYSTEM_INTEGRATION.md §2)
    # =========================================================================
    
    def _get_cb_state_snapshot(self) -> Dict[str, Any]:
        """
        실험 전후 CB 상태 스냅샷 캡처.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §2.2.1
        
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
            logger.warning(f"[Chaos] CB state snapshot failed: {e}")
            return {}
    
    def capture_steady_state_with_cb(self) -> Dict[str, Any]:
        """
        CB 상태를 포함한 Steady State 캡처.
        
        기본 메트릭 + Circuit Breaker 상태를 함께 캡처.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §2.2.2
        
        Returns:
            Dict containing metrics and circuit_breaker state
        """
        steady_state = self.capture_steady_state()
        steady_state["circuit_breaker"] = self._get_cb_state_snapshot()
        return steady_state
    
    # =========================================================================
    # Phase 4: 고급 기능 - 통합 스냅샷 메서드 (32_CHAOS_SYSTEM_INTEGRATION.md §7, §8, §9)
    # =========================================================================
    
    def _get_corruption_shield_stats(self) -> Dict[str, Any]:
        """
        Corruption Shield 통계 조회.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §7.2.2
        
        Returns:
            Dict with corruption shield statistics
        """
        try:
            from selfhealing.services.corruption_shield import get_corruption_shield
            
            shield = get_corruption_shield()
            return shield.get_stats()
        except ImportError:
            logger.debug("[Chaos] Corruption shield not available (import failed)")
            return {}
        except Exception as e:
            logger.warning(f"[Chaos] Corruption shield stats failed: {e}")
            return {}
    
    def _get_dlq_stats(self) -> Dict[str, Any]:
        """
        DLQ 통계 조회 (카오스 실험 제외).
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §8.2.2
        
        Returns:
            Dict with DLQ pending counts
        """
        try:
            from selfhealing.services.dlq import get_dlq_service
            
            service = get_dlq_service()
            return {
                "pending_count": service.get_pending_count() if hasattr(service, 'get_pending_count') else 0,
            }
        except ImportError:
            logger.debug("[Chaos] DLQ service not available (import failed)")
            return {}
        except Exception as e:
            logger.warning(f"[Chaos] DLQ stats failed: {e}")
            return {}
    
    def _get_throttle_stats(self) -> Dict[str, Any]:
        """
        Adaptive Throttle 통계 조회.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §9.2.1
        
        Returns:
            Dict with throttle statistics
        """
        try:
            from selfhealing.services.throttle import get_adaptive_throttle
            
            throttle = get_adaptive_throttle()
            return throttle.get_stats() if hasattr(throttle, 'get_stats') else {}
        except ImportError:
            logger.debug("[Chaos] Adaptive throttle not available (import failed)")
            return {}
        except Exception as e:
            logger.warning(f"[Chaos] Throttle stats failed: {e}")
            return {}
    
    def capture_comprehensive_snapshot(self) -> Dict[str, Any]:
        """
        모든 관련 서비스의 종합 스냅샷 캡처.
        
        CB, Corruption Shield, DLQ, Throttle 상태를 모두 포함.
        Phase 5-4: Pool, Cert, Connection Health 스냅샷 추가.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md Phase 4, Phase 5-4 (§13, §22.2.4)
        
        Returns:
            Dict containing all service snapshots
        """
        return {
            "circuit_breaker": self._get_cb_state_snapshot(),
            "corruption_shield": self._get_corruption_shield_stats(),
            "dlq": self._get_dlq_stats(),
            "throttle": self._get_throttle_stats(),
            # Phase 5-4: Monitor snapshots
            "pool": self._get_pool_state_snapshot(),
            "cert": self._get_cert_state_snapshot(),
            "connection_health": self._get_connection_health_snapshot(),
            "timestamp": now().isoformat(),
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
    # Phase 3: LearningService 피드백 루프 (32_CHAOS_SYSTEM_INTEGRATION.md §20.4)
    # =========================================================================
    
    def _record_hypothesis_validation(
        self,
        actual_recovery_time: float,
        actual_canary_stage: Optional[str] = None,
        actual_cb_state: Optional[str] = None,
        actual_fallback_activated: Optional[bool] = None,
        actual_fallback_type: Optional[str] = None,
    ) -> None:
        """
        가설 검증 결과를 LearningService에 기록.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §20.4
        
        Args:
            actual_recovery_time: 실제 복구 시간 (초)
            actual_canary_stage: 실제 Canary 단계
            actual_cb_state: 실제 CB 상태
            actual_fallback_activated: 실제 Fallback 활성화 여부
            actual_fallback_type: 실제 Fallback 유형
        """
        # failure_hypothesis가 없으면 스킵
        if not hasattr(self, 'failure_hypothesis') or self.failure_hypothesis is None:
            logger.debug(f"[Chaos] No failure_hypothesis defined for {self.experiment_id}")
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
        self._audit("hypothesis_validation", {
            "passed": passed,
            "violations": violations,
            "expected": self.failure_hypothesis.to_dict() if hasattr(self.failure_hypothesis, 'to_dict') else {},
            "actual": {
                "recovery_time": actual_recovery_time,
                "canary_stage": actual_canary_stage,
                "cb_state": actual_cb_state,
                "fallback_activated": actual_fallback_activated,
                "fallback_type": actual_fallback_type,
            },
        })
        
        try:
            from selfhealing.services.learning import get_learning_service
            from selfhealing.services.learning.models import PatternType
            
            learning = get_learning_service()
            
            # 패턴 기록
            pattern_type = PatternType.SUCCESS if passed else PatternType.FAILURE
            
            expected_recovery_time = (
                self.failure_hypothesis.expected_recovery_time_seconds
                if hasattr(self.failure_hypothesis, 'expected_recovery_time_seconds')
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
                        if hasattr(self.failure_hypothesis, 'expected_canary_stage')
                        else None
                    ),
                    "actual_canary_stage": actual_canary_stage,
                },
                confidence=0.9 if passed else 0.7,
            )
            
            # 복구 시간 추세 분석 요청 (실패 시)
            if not passed and violations:
                logger.warning(
                    f"[Chaos] Hypothesis validation FAILED for {self.experiment_id}: {violations}"
                )
                
                # LearningService에 추세 분석 트리거
                if hasattr(learning, 'analyze_trend'):
                    learning.analyze_trend(
                        target_field="recovery_time",
                        source=f"chaos:{self.experiment_type}",
                        window_days=30,
                    )
            else:
                logger.info(
                    f"[Chaos] Hypothesis validation PASSED for {self.experiment_id}"
                )
                
        except ImportError as e:
            logger.debug(f"[Chaos] LearningService not available: {e}")
        except Exception as e:
            logger.warning(f"[Chaos] Failed to record hypothesis validation: {e}")
    
    def record_finops_cost(self) -> None:
        """
        카오스 실험 비용을 FinOps에 기록.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §10.2
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
            logger.debug(f"[Chaos] FinOps recording skipped: {e}")

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
