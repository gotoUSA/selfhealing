"""
Stop Conditions Service

실시간 메트릭 기반 자동 중단 조건 관리.
SLA 위반 시 즉시 실험을 중단하고 롤백을 트리거합니다.

Reference: Netflix ChAP, AWS FIS Stop Conditions
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, List, Callable

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class StopConditionsConfig:
    """
    Stop Conditions 설정.
    
    자동 중단 조건을 정의합니다. 이 임계값을 초과하면
    실험이 자동으로 중단되고 롤백됩니다.
    """
    
    # 에러율 임계값 (%)
    max_error_rate_percent: float = 5.0
    """에러율이 이 값을 초과하면 실험 중단"""
    
    # 지연시간 임계값 (ms)
    max_latency_p99_ms: int = 2000
    """P99 지연시간이 이 값을 초과하면 실험 중단"""
    
    max_latency_p95_ms: int = 1000
    """P95 지연시간이 이 값을 초과하면 실험 중단"""
    
    # 에러 버짓 임계값 (%)
    min_error_budget_percent: float = 10.0
    """에러 버짓이 이 값 미만이면 실험 중단"""
    
    # 체크 주기 (초)
    check_interval_seconds: int = 10
    """메트릭 체크 주기"""
    
    # 연속 위반 횟수 (노이즈 방지)
    consecutive_breaches_required: int = 2
    """연속 N회 위반 시에만 중단 (일시적 스파이크 무시)"""
    
    # 활성화 여부
    enabled: bool = True
    """Stop Conditions 활성화 여부"""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StopConditionsConfig":
        """딕셔너리에서 생성."""
        return cls(
            max_error_rate_percent=data.get("max_error_rate_percent", 5.0),
            max_latency_p99_ms=data.get("max_latency_p99_ms", 2000),
            max_latency_p95_ms=data.get("max_latency_p95_ms", 1000),
            min_error_budget_percent=data.get("min_error_budget_percent", 10.0),
            check_interval_seconds=data.get("check_interval_seconds", 10),
            consecutive_breaches_required=data.get("consecutive_breaches_required", 2),
            enabled=data.get("enabled", True),
        )


@dataclass
class StopConditionViolation:
    """Stop Condition 위반 정보."""
    
    condition_type: str
    """위반된 조건 타입 (error_rate, latency_p99, latency_p95, error_budget)"""
    
    current_value: float
    """현재 값"""
    
    threshold_value: float
    """임계값"""
    
    message: str
    """사람이 읽을 수 있는 메시지"""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StopConditionCheckResult:
    """Stop Condition 체크 결과."""
    
    should_stop: bool = False
    """실험 중단 여부"""
    
    violations: List[StopConditionViolation] = field(default_factory=list)
    """위반된 조건들"""
    
    consecutive_breach_count: int = 0
    """연속 위반 횟수"""
    
    checked_at: str = ""
    """체크 시점"""
    
    metrics_snapshot: Dict[str, float] = field(default_factory=dict)
    """체크 시점의 메트릭 스냅샷"""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_stop": self.should_stop,
            "violations": [v.to_dict() for v in self.violations],
            "consecutive_breach_count": self.consecutive_breach_count,
            "checked_at": self.checked_at,
            "metrics_snapshot": self.metrics_snapshot,
        }


# =============================================================================
# TTL Configuration
# =============================================================================


@dataclass
class TTLConfig:
    """
    TTL (Time-To-Live) 설정.
    
    카오스 설정이 자동으로 만료되도록 합니다.
    엔진이 죽어도 타겟 시스템이 자동 복구됩니다.
    """
    
    default_ttl_seconds: int = 600
    """기본 TTL (10분)"""
    
    min_ttl_seconds: int = 60
    """최소 TTL (1분)"""
    
    max_ttl_seconds: int = 3600
    """최대 TTL (1시간)"""
    
    auto_expiration_enabled: bool = True
    """자동 만료 활성화 여부"""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TTLConfig":
        return cls(
            default_ttl_seconds=data.get("default_ttl_seconds", 600),
            min_ttl_seconds=data.get("min_ttl_seconds", 60),
            max_ttl_seconds=data.get("max_ttl_seconds", 3600),
            auto_expiration_enabled=data.get("auto_expiration_enabled", True),
        )
    
    def validate_ttl(self, ttl_seconds: int) -> int:
        """TTL 값을 검증하고 범위 내로 조정."""
        return max(self.min_ttl_seconds, min(ttl_seconds, self.max_ttl_seconds))


# =============================================================================
# Dry Run Configuration
# =============================================================================


@dataclass
class DryRunConfig:
    """
    Dry Run 설정.
    
    Dry Run 모드에서는 실제 장애 주입 없이
    전체 워크플로우만 검증합니다.
    """
    
    enabled: bool = True
    """Dry Run 모드 활성화 (기본값: True - 안전)"""
    
    reason: str = "Initial deployment - simulation mode"
    """Dry Run 모드 활성화 이유"""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DryRunConfig":
        return cls(
            enabled=data.get("enabled", True),
            reason=data.get("reason", "Initial deployment - simulation mode"),
        )


# =============================================================================
# Stop Conditions Checker
# =============================================================================


class StopConditionsChecker:
    """
    Stop Conditions 체커.
    
    실시간으로 메트릭을 체크하고 임계값 초과 시
    실험 중단을 트리거합니다.
    """
    
    def __init__(
        self,
        config: Optional[StopConditionsConfig] = None,
        metrics_collector: Optional[Any] = None,
    ):
        """
        초기화.
        
        Args:
            config: Stop Conditions 설정
            metrics_collector: 메트릭 수집기 (선택적)
        """
        self._config = config or StopConditionsConfig()
        self._metrics_collector = metrics_collector
        self._consecutive_breaches: Dict[str, int] = {}
        self._lock = threading.Lock()
    
    @property
    def config(self) -> StopConditionsConfig:
        """현재 설정 반환."""
        return self._config
    
    def update_config(self, **kwargs) -> StopConditionsConfig:
        """설정 업데이트."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(f"[StopConditions] Updated {key} = {value}")
            return self._config
    
    def check(
        self,
        experiment_id: str,
        target_service: str,
    ) -> StopConditionCheckResult:
        """
        Stop Conditions 체크.
        
        Args:
            experiment_id: 실험 ID
            target_service: 대상 서비스
            
        Returns:
            체크 결과
        """
        from selfhealing.core.timezone import now
        
        if not self._config.enabled:
            return StopConditionCheckResult(
                should_stop=False,
                checked_at=now().isoformat(),
            )
        
        violations: List[StopConditionViolation] = []
        metrics_snapshot: Dict[str, float] = {}
        
        try:
            # 메트릭 수집
            metrics = self._collect_metrics(target_service)
            metrics_snapshot = metrics
            
            # 1. 에러율 체크
            error_rate = metrics.get("error_rate_percent", 0)
            if error_rate > self._config.max_error_rate_percent:
                violations.append(StopConditionViolation(
                    condition_type="error_rate",
                    current_value=error_rate,
                    threshold_value=self._config.max_error_rate_percent,
                    message=f"Error rate {error_rate:.1f}% > {self._config.max_error_rate_percent}%",
                ))
            
            # 2. P99 지연시간 체크
            latency_p99 = metrics.get("latency_p99_ms", 0)
            if latency_p99 > self._config.max_latency_p99_ms:
                violations.append(StopConditionViolation(
                    condition_type="latency_p99",
                    current_value=latency_p99,
                    threshold_value=self._config.max_latency_p99_ms,
                    message=f"Latency P99 {latency_p99}ms > {self._config.max_latency_p99_ms}ms",
                ))
            
            # 3. P95 지연시간 체크
            latency_p95 = metrics.get("latency_p95_ms", 0)
            if latency_p95 > self._config.max_latency_p95_ms:
                violations.append(StopConditionViolation(
                    condition_type="latency_p95",
                    current_value=latency_p95,
                    threshold_value=self._config.max_latency_p95_ms,
                    message=f"Latency P95 {latency_p95}ms > {self._config.max_latency_p95_ms}ms",
                ))
            
            # 4. 에러 버짓 체크
            error_budget = metrics.get("error_budget_remaining_percent", 100)
            if error_budget < self._config.min_error_budget_percent:
                violations.append(StopConditionViolation(
                    condition_type="error_budget",
                    current_value=error_budget,
                    threshold_value=self._config.min_error_budget_percent,
                    message=f"Error budget {error_budget:.1f}% < {self._config.min_error_budget_percent}%",
                ))
            
        except Exception as e:
            logger.warning(f"[StopConditions] Metrics collection failed: {e}")
            # 메트릭 수집 실패 시 fail-open (계속 진행)
            return StopConditionCheckResult(
                should_stop=False,
                checked_at=now().isoformat(),
            )
        
        # 연속 위반 카운트
        with self._lock:
            if violations:
                self._consecutive_breaches[experiment_id] = \
                    self._consecutive_breaches.get(experiment_id, 0) + 1
            else:
                self._consecutive_breaches[experiment_id] = 0
            
            consecutive_count = self._consecutive_breaches[experiment_id]
        
        # 연속 N회 이상이면 중단
        should_stop = (
            len(violations) > 0 and
            consecutive_count >= self._config.consecutive_breaches_required
        )
        
        if should_stop:
            logger.warning(
                f"[StopConditions] Experiment {experiment_id} should be stopped. "
                f"Violations: {[v.message for v in violations]}"
            )
        
        return StopConditionCheckResult(
            should_stop=should_stop,
            violations=violations,
            consecutive_breach_count=consecutive_count,
            checked_at=now().isoformat(),
            metrics_snapshot=metrics_snapshot,
        )
    
    def reset_breach_count(self, experiment_id: str) -> None:
        """연속 위반 카운트 리셋."""
        with self._lock:
            self._consecutive_breaches.pop(experiment_id, None)
    
    def _collect_metrics(self, target_service: str) -> Dict[str, float]:
        """
        메트릭 수집.
        
        외부 메트릭 수집기가 있으면 사용하고,
        없으면 내부 서비스에서 수집.
        """
        if self._metrics_collector:
            return self._metrics_collector.get_service_metrics(target_service)
        
        # 내부 서비스에서 수집 시도
        try:
            from selfhealing.services.error_budget_service import get_error_budget_service
            
            budget_service = get_error_budget_service()
            budget_status = budget_service.get_status()
            
            return {
                "error_rate_percent": budget_status.get("current_error_rate", 0),
                "latency_p99_ms": budget_status.get("latency_p99_ms", 0),
                "latency_p95_ms": budget_status.get("latency_p95_ms", 0),
                "error_budget_remaining_percent": budget_status.get("remaining_percent", 100),
            }
        except Exception:
            # 기본값 반환
            return {
                "error_rate_percent": 0,
                "latency_p99_ms": 100,
                "latency_p95_ms": 50,
                "error_budget_remaining_percent": 100,
            }


# =============================================================================
# Singleton & Factory
# =============================================================================


_stop_conditions_checker: Optional[StopConditionsChecker] = None
_checker_lock = threading.Lock()


def get_stop_conditions_checker() -> StopConditionsChecker:
    """싱글톤 StopConditionsChecker 인스턴스 반환."""
    global _stop_conditions_checker
    
    if _stop_conditions_checker is None:
        with _checker_lock:
            if _stop_conditions_checker is None:
                _stop_conditions_checker = StopConditionsChecker()
    
    return _stop_conditions_checker


def reset_stop_conditions_checker() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _stop_conditions_checker
    with _checker_lock:
        _stop_conditions_checker = None


# =============================================================================
# Helper Functions
# =============================================================================


def get_ttl_config() -> TTLConfig:
    """TTL 설정 반환."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        chaos_config = manager.get_chaos_config()
        ttl_data = chaos_config.get("ttl_config", {})
        return TTLConfig.from_dict(ttl_data)
    except Exception:
        return TTLConfig()


def get_dry_run_config() -> DryRunConfig:
    """Dry Run 설정 반환."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        chaos_config = manager.get_chaos_config()
        dry_run_data = chaos_config.get("dry_run_config", {})
        return DryRunConfig.from_dict(dry_run_data)
    except Exception:
        return DryRunConfig()


def get_stop_conditions_config() -> StopConditionsConfig:
    """Stop Conditions 설정 반환."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        chaos_config = manager.get_chaos_config()
        stop_conditions_data = chaos_config.get("stop_conditions_config", {})
        return StopConditionsConfig.from_dict(stop_conditions_data)
    except Exception:
        return StopConditionsConfig()
