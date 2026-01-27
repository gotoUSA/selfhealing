"""
Chaos Experiment Data Models.

Contains data classes for experiment configuration, results, and hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from selfhealing.services.chaos.base.enums import TrafficType
from selfhealing.settings import ChaosExperimentSettings, get_layered_settings


def _get_experiment_defaults() -> ChaosExperimentSettings:
    """
    LayeredSettings에서 실험 기본값 가져오기.

    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [12] ChaosExperimentSettings 참조.
    """
    return get_layered_settings(ChaosExperimentSettings, "chaos_experiment")


@dataclass
class ExperimentConfig:
    """Configuration for a chaos experiment."""

    # Target configuration
    target_service: str = ""
    target_domain: str = ""
    target_instances: list[str] = field(default_factory=list)

    # Injection parameters
    injection_rate: float = 0.001  # 0.1% default
    duration_seconds: int = field(
        default_factory=lambda: _get_experiment_defaults().default_duration_seconds
    )

    # Traffic targeting
    traffic_type: str = TrafficType.SYNTHETIC.value

    # Rollback configuration
    auto_rollback_on_sla_breach: bool = True
    sla_breach_threshold_percent: float = field(
        default_factory=lambda: _get_experiment_defaults().sla_breach_threshold_percent
    )

    # Additional parameters (experiment-specific)
    parameters: dict[str, Any] = field(default_factory=dict)

    # TTL (Self-Expiration) configuration
    ttl_seconds: int | None = None
    """Soft TTL: 장애 주입 종료 시간 (초). None이면 기본값 사용."""

    # Soft/Hard TTL 이중 구조: Soft TTL 후 Grace Period 동안 복구 모니터링
    grace_period_seconds: int = field(
        default_factory=lambda: _get_experiment_defaults().grace_period_seconds
    )
    """Grace Period: Canary 복구 대기 시간 (ChaosExperimentSettings에서 로드)."""

    @property
    def hard_ttl_seconds(self) -> int:
        """
        Hard TTL: 실험 강제 종료 시간 (초).

        Soft TTL + Grace Period.
        Canary 복구가 완료되지 않더라도 강제 종료.
        """
        defaults = _get_experiment_defaults()
        base_ttl = self.ttl_seconds or defaults.default_ttl_seconds
        return base_ttl + self.grace_period_seconds

    # Dry Run mode
    dry_run: bool = False
    """True이면 실제 장애 주입 없이 시뮬레이션만 수행."""

    # Resilience Expectation: 시스템이 장애에 어떻게 반응해야 하는지 정의
    resilience_expectation: Any | None = None
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
    steady_state_before: dict[str, Any] = field(default_factory=dict)
    steady_state_after: dict[str, Any] = field(default_factory=dict)
    steady_state_hypothesis_passed: bool = True

    # Forensic analysis
    forensic_advisory: dict[str, Any] = field(default_factory=dict)

    # Errors
    error_message: str = ""

    # Audit
    audit_record_ids: list[str] = field(default_factory=list)

    # Dry Run / TTL info
    dry_run: bool = False
    """True이면 실제 장애 주입 없이 시뮬레이션만 수행됨."""

    ttl_seconds: int = 0
    """사용된 TTL 값 (초)."""

    expires_at: str = ""
    """카오스 설정 만료 시간 (ISO format)."""

    auto_expired: bool = False
    """TTL에 의해 자동 만료되었는지 여부."""

    # Resilience Validation: Chaos 실험 결과로 시스템 회복력 검증
    resilience_validation: dict[str, Any] | None = None
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

    def to_dict(self) -> dict[str, Any]:
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
    custom_metrics: dict[str, dict[str, float]] = field(default_factory=dict)

    def validate(self, metrics: dict[str, float]) -> tuple[bool, list[str]]:
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
