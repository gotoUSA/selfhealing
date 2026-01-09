"""
Concrete Chaos Experiment Implementations

Contains specific experiment types:
- LatencyInjectionExperiment
- Error5xxExperiment
- PacketLossExperiment
- TimeoutExperiment
- ResourceExhaustionExperiment

Reference: Netflix ChAP, Gremlin, AWS FIS patterns
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentConfig,
    ExperimentType,
    SteadyStateHypothesis,
    _apply_chaos_config,
    _get_current_chaos_config,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Failure Hypothesis (복구 기대 가설)
# Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §20.2
# =============================================================================


@dataclass
class FailureHypothesis:
    """
    카오스 실험의 복구 기대 가설.
    
    LearningService가 실제 결과와 비교하여
    "시스템 복구 성능 저하 추세"를 자동 감지하게 함.
    
    Reference:
    - Architect 제안: "CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함"
    - 31_CHAOS_EXPERIMENT_EXPANSION.md §8
    - 32_CHAOS_SYSTEM_INTEGRATION.md §20.2
    
    Example:
        hypothesis = FailureHypothesis(
            description="CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함",
            expected_recovery_time_seconds=60.0,
            expected_canary_stage="canary_1",
            expected_cb_state_after="half_open",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=45.0,
            actual_canary_stage="canary_1",
            actual_cb_state="half_open",
        )
    """
    
    # 복구 관련 기대
    expected_recovery_time_seconds: float = 30.0
    """기대 복구 시간 (초). 이 시간 내에 복구되어야 함."""
    
    expected_canary_stage: Optional[str] = None
    """기대 Canary 단계. 예: "canary_1" (10% 트래픽)."""
    
    expected_canary_start_within_seconds: Optional[float] = None
    """Canary 시작까지 기대 시간 (초)."""
    
    # CB 관련 기대
    expected_cb_state_after: Optional[str] = None
    """실험 후 기대 CB 상태. 예: "open", "half_open"."""
    
    expected_cb_transition_within_seconds: Optional[float] = None
    """CB 상태 전환까지 기대 시간 (초)."""
    
    # Fallback 관련 기대
    expected_fallback_activated: bool = False
    """Fallback 활성화 기대 여부."""
    
    expected_fallback_type: Optional[str] = None
    """기대 Fallback 유형. 예: "cache", "dlq", "default"."""
    
    # 메타데이터
    description: str = ""
    """사람이 읽을 수 있는 가설 설명."""
    
    tolerance_percent: float = 20.0
    """허용 오차 (%). 기대 시간의 ±20% 내면 정상."""
    
    def validate(
        self,
        actual_recovery_time: float,
        actual_canary_stage: Optional[str] = None,
        actual_cb_state: Optional[str] = None,
        actual_fallback_activated: Optional[bool] = None,
        actual_fallback_type: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        가설 검증.
        
        Args:
            actual_recovery_time: 실제 복구 시간 (초)
            actual_canary_stage: 실제 Canary 단계
            actual_cb_state: 실제 CB 상태
            actual_fallback_activated: 실제 Fallback 활성화 여부
            actual_fallback_type: 실제 Fallback 유형
        
        Returns:
            (passed, violations) 튜플
            - passed: True이면 가설 검증 통과
            - violations: 위반 사항 목록
        """
        violations: List[str] = []
        tolerance_factor = 1 + (self.tolerance_percent / 100)
        
        # 복구 시간 검증
        max_allowed_time = self.expected_recovery_time_seconds * tolerance_factor
        if actual_recovery_time > max_allowed_time:
            violations.append(
                f"Recovery time {actual_recovery_time:.1f}s > "
                f"expected {self.expected_recovery_time_seconds:.1f}s "
                f"(+{self.tolerance_percent}% tolerance = {max_allowed_time:.1f}s)"
            )
        
        # Canary 단계 검증
        if self.expected_canary_stage and actual_canary_stage != self.expected_canary_stage:
            violations.append(
                f"Canary stage '{actual_canary_stage}' != expected '{self.expected_canary_stage}'"
            )
        
        # CB 상태 검증
        if self.expected_cb_state_after and actual_cb_state != self.expected_cb_state_after:
            violations.append(
                f"CB state '{actual_cb_state}' != expected '{self.expected_cb_state_after}'"
            )
        
        # Fallback 활성화 검증
        if self.expected_fallback_activated:
            if actual_fallback_activated is False:
                violations.append(
                    f"Fallback expected to be activated but was not"
                )
            # Fallback 유형 검증 (Fallback이 활성화된 경우에만)
            if (
                self.expected_fallback_type
                and actual_fallback_activated
                and actual_fallback_type != self.expected_fallback_type
            ):
                violations.append(
                    f"Fallback type '{actual_fallback_type}' != expected '{self.expected_fallback_type}'"
                )
        
        return len(violations) == 0, violations
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "expected_recovery_time_seconds": self.expected_recovery_time_seconds,
            "expected_canary_stage": self.expected_canary_stage,
            "expected_canary_start_within_seconds": self.expected_canary_start_within_seconds,
            "expected_cb_state_after": self.expected_cb_state_after,
            "expected_cb_transition_within_seconds": self.expected_cb_transition_within_seconds,
            "expected_fallback_activated": self.expected_fallback_activated,
            "expected_fallback_type": self.expected_fallback_type,
            "description": self.description,
            "tolerance_percent": self.tolerance_percent,
        }


# =============================================================================
# 실험별 기대 가설 정의 (클래스 레벨 상수)
# Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §20.3
# =============================================================================

# CircuitBreakerOpenExperiment
CB_OPEN_HYPOTHESIS = FailureHypothesis(
    description="CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함",
    expected_recovery_time_seconds=60.0,
    expected_canary_stage="canary_1",
    expected_canary_start_within_seconds=30.0,
    expected_cb_state_after="half_open",
    expected_cb_transition_within_seconds=30.0,
    expected_fallback_activated=True,
    expected_fallback_type="cache",
)

# LatencyInjectionExperiment
LATENCY_INJECTION_HYPOTHESIS = FailureHypothesis(
    description="500ms 지연 주입 시, CB가 10초 내에 OPEN되어야 함",
    expected_recovery_time_seconds=45.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=10.0,
    expected_fallback_activated=False,
)

# Error5xxExperiment
ERROR_5XX_HYPOTHESIS = FailureHypothesis(
    description="503 에러 주입 시, 5초 내에 CB OPEN 및 Fallback 활성화",
    expected_recovery_time_seconds=30.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=5.0,
    expected_fallback_activated=True,
)


# =============================================================================
# Latency Injection Experiment
# =============================================================================


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
    # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §20.3
    failure_hypothesis = LATENCY_INJECTION_HYPOTHESIS
    
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
            self._original_state = _get_current_chaos_config()
            
            _apply_chaos_config({
                "latency_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "latency_ms": self.latency_ms,
                    "jitter_ms": self.latency_jitter_ms,
                    "rate": self.config.injection_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            
            return True
            
        except Exception as e:
            logger.error(f"[LatencyInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove latency injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[LatencyInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[LatencyInjection] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "latency_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[LatencyInjection] Rollback failed: {e}")


# =============================================================================
# Error 5xx Experiment
# =============================================================================


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
    # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §20.3
    failure_hypothesis = ERROR_5XX_HYPOTHESIS
    
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
            _apply_chaos_config({
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
                _apply_chaos_config({
                    "error_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Error5xxInjection] Rollback failed: {e}")


# =============================================================================
# Packet Loss Experiment
# =============================================================================


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
            _apply_chaos_config({
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
                _apply_chaos_config({
                    "packet_loss": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PacketLoss] Rollback failed: {e}")


# =============================================================================
# Timeout Experiment
# =============================================================================


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
            _apply_chaos_config({
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
                _apply_chaos_config({
                    "timeout_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Timeout] Rollback failed: {e}")


# =============================================================================
# Resource Exhaustion Experiment
# =============================================================================


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
        
    Reference:
        31_CHAOS_EXPERIMENT_EXPANSION.md §7.3 (Q3: Cgroup-Aware Limit)
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
    
    def _get_safe_exhaustion_bytes(self) -> Optional[int]:
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
            logger.debug(f"[ResourceExhaustion] Cgroup check failed: {e}")
            return None
    
    def inject_chaos(self) -> bool:
        """Inject resource exhaustion with TTL and cgroup safety margin."""
        # 메모리 타입일 경우 cgroup 안전 마진 적용
        safe_bytes = self._get_safe_exhaustion_bytes()
        exhaustion_config = {
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
            _apply_chaos_config({
                "resource_exhaustion": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "resource_type": self.resource_type,
                    **exhaustion_config,
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
                _apply_chaos_config({
                    "resource_exhaustion": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ResourceExhaustion] Rollback failed: {e}")


# =============================================================================
# Phase 1: CircuitBreakerOpenExperiment (P1 Priority)
# Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §2.4
# =============================================================================


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
    # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §20.3
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
            f"[CBOpenInjection] Forcing CB OPEN for {self.config.target_service} "
            f"(TTL: {self._effective_ttl}s)"
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
                logger.error(f"[CBOpenInjection] Failed to open CB: {result.message}")
                return False
            
            # 설정 저장 (TTL 및 rollback용)
            _apply_chaos_config({
                "circuit_breaker_open": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "trigger_canary": self.trigger_canary,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[CBOpenInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Force CB back to CLOSED state."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[CBOpenInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[CBOpenInjection] Rolling back {self.experiment_id}")
            
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
                
                _apply_chaos_config({
                    "circuit_breaker_open": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[CBOpenInjection] Rollback failed: {e}")
    
    # =========================================================================
    # Phase 3: Canary Recovery 검증 (32_CHAOS_SYSTEM_INTEGRATION.md §4)
    # =========================================================================
    
    def _verify_canary_recovery(self) -> Dict[str, Any]:
        """
        Canary 복구 단계 검증.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §4.2.1
        
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
                get_canary_recovery_manager,
                CanaryState,
            )
            
            manager = get_canary_recovery_manager()
            target = self.config.target_service
            
            state = manager.get_recovery_state(target)
            
            if state is None:
                return {
                    "canary_state": CanaryState.NOT_IN_CANARY.value,
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
                "stage_started_at": state.stage_started_at.isoformat() if state.stage_started_at else None,
            }
        except Exception as e:
            logger.warning(f"[CBOpenExperiment] Canary verification failed: {e}")
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
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §4.2.2
        
        Args:
            timeout_seconds: 대기 시간 (초)
            
        Returns:
            True if canary started within timeout
        """
        import time
        from selfhealing.core.timezone import now
        
        start_time = now()
        
        while (now() - start_time).total_seconds() < timeout_seconds:
            status = self._verify_canary_recovery()
            
            if status.get("in_canary", False):
                logger.info(
                    f"[CBOpenExperiment] Canary recovery started: "
                    f"stage={status.get('canary_state')}, "
                    f"traffic={status.get('traffic_percent')}%"
                )
                return True
            
            time.sleep(1.0)  # 1초마다 체크
        
        logger.warning(
            f"[CBOpenExperiment] Canary recovery did not start within {timeout_seconds}s"
        )
        return False
    
    def get_canary_verification_result(self) -> Dict[str, Any]:
        """
        실험 결과에 포함할 Canary 검증 결과.
        
        Returns:
            Dict containing canary recovery verification data
        """
        canary_status = self._verify_canary_recovery()
        
        # FailureHypothesis 검증
        if hasattr(self, 'failure_hypothesis') and self.failure_hypothesis:
            actual_canary_stage = canary_status.get("canary_state")
            hypothesis_canary_match = (
                actual_canary_stage == self.failure_hypothesis.expected_canary_stage
            )
        else:
            hypothesis_canary_match = None
        
        return {
            "canary_status": canary_status,
            "hypothesis_canary_match": hypothesis_canary_match,
            "expected_canary_stage": (
                self.failure_hypothesis.expected_canary_stage
                if hasattr(self, 'failure_hypothesis') and self.failure_hypothesis
                else None
            ),
        }


# =============================================================================
# Phase 1: RateLimitExperiment (P1 Priority)
# Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §2.3
# =============================================================================


class RateLimitExperiment(ChaosExperiment):
    """
    Inject rate limit (429) responses to trigger CB auto-open.
    
    Tests rate limit cascade detection and self-DDoS protection.
    
    Config parameters:
        - rate_limit_count: Number of 429s to inject (default: 10)
        - window_seconds: Time window for injection (default: 60)
        - retry_after_seconds: Retry-After header value (default: 30)
    """
    
    experiment_type = ExperimentType.RATE_LIMIT.value
    requires_approval = False  # Medium risk
    
    @property
    def rate_limit_count(self) -> int:
        return self.config.parameters.get("rate_limit_count", 10)
    
    @property
    def retry_after_seconds(self) -> int:
        return self.config.parameters.get("retry_after_seconds", 30)
    
    def inject_chaos(self) -> bool:
        """Inject rate limit responses to trigger CB cascade."""
        logger.info(
            f"[RateLimitInjection] Injecting {self.rate_limit_count} rate limits "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )
        
        try:
            # Rate Limit Tracker에 직접 기록 (CB 자동 OPEN 유발)
            from selfhealing.services.circuit_breaker import (
                get_rate_limit_tracker,
            )
            
            tracker = get_rate_limit_tracker()
            for _ in range(self.rate_limit_count):
                tracker.record_rate_limit(
                    service_name=self.config.target_service,
                    retry_after=self.retry_after_seconds,
                )
            
            # 설정 저장 (TTL용)
            _apply_chaos_config({
                "rate_limit_injection": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "count": self.rate_limit_count,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[RateLimitInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Clear rate limit injection state."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[RateLimitInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[RateLimitInjection] Rolling back {self.experiment_id}")
            
            try:
                # Rate Limit Tracker 리셋은 어려우므로 설정만 해제
                _apply_chaos_config({
                    "rate_limit_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[RateLimitInjection] Rollback failed: {e}")


# =============================================================================
# Phase 2: Error4xxExperiment (P2 Priority)
# Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §2.1
# =============================================================================


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
            self.ERROR_MESSAGES.get(self.error_code, "Client Error (Chaos Experiment)")
        )
    
    def inject_chaos(self) -> bool:
        """Inject 4xx errors into target service with TTL."""
        logger.info(
            f"[Error4xxInjection] Injecting {self.error_code} errors "
            f"to {self.config.target_service} at {self.config.injection_rate*100}% rate "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "error_4xx_injection": {
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
            logger.error(f"[Error4xxInjection] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove 4xx error injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[Error4xxInjection] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[Error4xxInjection] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "error_4xx_injection": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[Error4xxInjection] Rollback failed: {e}")


# =============================================================================
# Phase 2: PartialFailureExperiment (P2 Priority)
# Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §2.5
# =============================================================================


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
    def affected_endpoints(self) -> list:
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
            _apply_chaos_config({
                "partial_failure": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "failure_rate": self.failure_rate,
                    "affected_endpoints": self.affected_endpoints,
                    "trigger_shedding": self.trigger_shedding,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[PartialFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove partial failure injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[PartialFailure] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[PartialFailure] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "partial_failure": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PartialFailure] Rollback failed: {e}")


# =============================================================================
# Phase 3: ConnectionResetExperiment (P3 Priority)
# Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §2.2
# =============================================================================


class ConnectionResetExperiment(ChaosExperiment):
    """
    Simulate network connection reset (TCP RST).
    
    Simulates sudden connection drops, network instability.
    Tests retry/backoff logic and circuit breaker reaction.
    
    Config parameters:
        - reset_after_bytes: Bytes to send before reset (0=immediate)
        - reset_probability: Probability of reset per request (0-1)
    """
    
    experiment_type = ExperimentType.CONNECTION_RESET.value
    requires_approval = True  # Medium-High risk
    
    @property
    def reset_after_bytes(self) -> int:
        return self.config.parameters.get("reset_after_bytes", 0)
    
    @property
    def reset_probability(self) -> float:
        return self.config.parameters.get("reset_probability", 0.5)
    
    def inject_chaos(self) -> bool:
        """Inject connection reset behavior with TTL."""
        logger.info(
            f"[ConnectionReset] Injecting connection resets "
            f"to {self.config.target_service} at {self.reset_probability*100}% probability "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "connection_reset": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "reset_after_bytes": self.reset_after_bytes,
                    "reset_probability": self.reset_probability,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ConnectionReset] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove connection reset injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[ConnectionReset] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[ConnectionReset] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "connection_reset": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ConnectionReset] Rollback failed: {e}")


# =============================================================================
# Phase 3: CascadingFailureExperiment (P3 Priority - CRITICAL RISK)
# Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §2.6
# WARNING: This is a HIGH RISK experiment that requires manual approval
# =============================================================================


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
    def affected_services(self) -> list:
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
        import time
        
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
                    logger.warning("[CascadingFailure] Kill switch activated, stopping cascade")
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
                    logger.warning(f"[CascadingFailure] Failed to open CB for {service}: {result.message}")
                
                # 연쇄 효과 시뮬레이션을 위한 지연
                if service != self.affected_services[-1]:
                    time.sleep(self.cascade_delay_seconds)
            
            _apply_chaos_config({
                "cascading_failure": {
                    "enabled": True,
                    "affected_services": opened_services,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            
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
                logger.info(f"[CascadingFailure] Rollback already completed for {self.experiment_id}")
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
                
                _apply_chaos_config({
                    "cascading_failure": {
                        "enabled": False,
                        "affected_services": [],
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
                logger.info(f"[CascadingFailure] Rollback complete for {len(self.affected_services)} services")
            except Exception as e:
                logger.error(f"[CascadingFailure] Rollback failed: {e}")


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
        # Existing types
        ExperimentType.LATENCY_INJECTION.value: LatencyInjectionExperiment,
        ExperimentType.ERROR_5XX.value: Error5xxExperiment,
        ExperimentType.PACKET_LOSS.value: PacketLossExperiment,
        ExperimentType.TIMEOUT.value: TimeoutExperiment,
        ExperimentType.RESOURCE_EXHAUSTION.value: ResourceExhaustionExperiment,
        # Phase 1: P1 Priority experiments (31_CHAOS_EXPERIMENT_EXPANSION.md)
        ExperimentType.CIRCUIT_BREAKER_OPEN.value: CircuitBreakerOpenExperiment,
        ExperimentType.RATE_LIMIT.value: RateLimitExperiment,
        # Phase 2: P2 Priority experiments (31_CHAOS_EXPERIMENT_EXPANSION.md)
        ExperimentType.ERROR_4XX.value: Error4xxExperiment,
        ExperimentType.PARTIAL_FAILURE.value: PartialFailureExperiment,
        # Phase 3: P3 Priority experiments (31_CHAOS_EXPERIMENT_EXPANSION.md)
        ExperimentType.CONNECTION_RESET.value: ConnectionResetExperiment,
        ExperimentType.CASCADING_FAILURE.value: CascadingFailureExperiment,
    }
    
    experiment_class = experiment_classes.get(experiment_type)
    if not experiment_class:
        raise ValueError(f"Unknown experiment type: {experiment_type}")
    
    return experiment_class(config=config, hypothesis=hypothesis)


# =============================================================================
# Backward Compatibility Re-exports
# =============================================================================

# Re-export from base for backward compatibility
from selfhealing.services.chaos.base import (
    AuditRecorderProtocol,
    KillSwitchProtocol,
    ExperimentStatus,
    ExperimentType,
    TrafficType,
    ExperimentConfig,
    ExperimentResult,
    SteadyStateHypothesis,
    ChaosExperiment,
)


__all__ = [
    # Protocols
    "AuditRecorderProtocol",
    "KillSwitchProtocol",
    # Enums
    "ExperimentStatus",
    "ExperimentType",
    "TrafficType",
    # Data Classes
    "ExperimentConfig",
    "ExperimentResult",
    "SteadyStateHypothesis",
    # Base Class
    "ChaosExperiment",
    # Concrete Experiments (Existing)
    "LatencyInjectionExperiment",
    "Error5xxExperiment",
    "PacketLossExperiment",
    "TimeoutExperiment",
    "ResourceExhaustionExperiment",
    # Concrete Experiments (Phase 1 - P1 Priority)
    "CircuitBreakerOpenExperiment",
    "RateLimitExperiment",
    # Concrete Experiments (Phase 2 - P2 Priority)
    "Error4xxExperiment",
    "PartialFailureExperiment",
    # Concrete Experiments (Phase 3 - P3 Priority)
    "ConnectionResetExperiment",
    "CascadingFailureExperiment",
    # Factory
    "create_experiment",
]
