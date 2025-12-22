"""
Error Budget Gate - 에러 예산 기반 자동화 제어 게이트

"위기 상황일수록 인간의 개입을 강제하는 설계"

에러 예산이 임계값 미만일 때 모든 자동화 기능을 중단하고
수동 모드로 강제 전환합니다.

핵심 원칙:
- 시스템이 "대신 하는 것"이 아니라 "멈추는 것"
- 판단을 대체하지 않고, 위험할 때 보호
- Fail-Open: 게이트 자체 장애 시 자동화 허용 (단, 경고 발생)

Usage:
    from selfhealing.services.error_budget_gate import (
        check_automation_allowed,
        require_automation_allowed,
        AutomationBlockedError,
    )
    
    # 방법 1: 조건 체크
    result = check_automation_allowed()
    if not result.allowed:
        logger.warning(f"Automation blocked: {result.reason}")
        return  # 자동화 중단
    
    # 방법 2: 예외 발생 (권장)
    try:
        require_automation_allowed(action="dlq_auto_replay")
    except AutomationBlockedError as e:
        # 수동 처리로 전환
        notify_operator(e.message)
        return

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
- 설계 철학: "보고는 자동, 결정은 수동"
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Callable

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class ErrorBudgetGateConfig:
    """
    에러 예산 게이트 설정.
    
    Attributes:
        enabled: 게이트 활성화 여부 (False면 항상 자동화 허용)
        critical_threshold_percent: 이 값 미만이면 자동화 차단 (기본: 10%)
        warning_threshold_percent: 이 값 미만이면 경고 표시 (기본: 20%)
        fail_open: 에러 예산 조회 실패 시 자동화 허용 여부 (기본: True)
        cache_ttl_seconds: 에러 예산 캐시 TTL (기본: 30초)
    """
    enabled: bool = True
    critical_threshold_percent: float = 10.0
    warning_threshold_percent: float = 20.0
    fail_open: bool = True
    cache_ttl_seconds: int = 30
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "critical_threshold_percent": self.critical_threshold_percent,
            "warning_threshold_percent": self.warning_threshold_percent,
            "fail_open": self.fail_open,
            "cache_ttl_seconds": self.cache_ttl_seconds,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ErrorBudgetGateConfig":
        return cls(
            enabled=data.get("enabled", True),
            critical_threshold_percent=data.get("critical_threshold_percent", 10.0),
            warning_threshold_percent=data.get("warning_threshold_percent", 20.0),
            fail_open=data.get("fail_open", True),
            cache_ttl_seconds=data.get("cache_ttl_seconds", 30),
        )


# =============================================================================
# Gate Status
# =============================================================================


class GateStatus(str, Enum):
    """게이트 상태."""
    
    OPEN = "open"
    """자동화 허용 - 에러 예산 충분."""
    
    WARNING = "warning"
    """자동화 허용 (경고) - 에러 예산 낮음."""
    
    BLOCKED = "blocked"
    """자동화 차단 - 에러 예산 위험 수준, 수동 모드 강제."""
    
    FAIL_OPEN = "fail_open"
    """자동화 허용 (장애 복구 모드) - 에러 예산 조회 실패."""
    
    DISABLED = "disabled"
    """게이트 비활성화 - 항상 자동화 허용."""


@dataclass
class GateCheckResult:
    """게이트 체크 결과."""
    
    allowed: bool
    """자동화 허용 여부."""
    
    status: GateStatus
    """게이트 상태."""
    
    error_budget_percent: Optional[float] = None
    """현재 에러 예산 잔여율 (%)."""
    
    threshold_percent: float = 10.0
    """차단 임계값 (%)."""
    
    reason: str = ""
    """상태 설명."""
    
    recommendation: str = ""
    """권장 조치."""
    
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """체크 시각."""
    
    fail_open_triggered: bool = False
    """Fail-open이 발동되었는지 여부."""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status.value,
            "error_budget_percent": self.error_budget_percent,
            "threshold_percent": self.threshold_percent,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "checked_at": self.checked_at.isoformat(),
            "fail_open_triggered": self.fail_open_triggered,
        }


# =============================================================================
# Exception
# =============================================================================


class AutomationBlockedError(Exception):
    """
    자동화가 에러 예산 게이트에 의해 차단됨.
    
    이 예외가 발생하면 수동 처리로 전환해야 합니다.
    """
    
    def __init__(
        self,
        message: str,
        error_budget_percent: Optional[float] = None,
        threshold_percent: float = 10.0,
        action: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.error_budget_percent = error_budget_percent
        self.threshold_percent = threshold_percent
        self.action = action
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "AutomationBlockedError",
            "message": self.message,
            "error_budget_percent": self.error_budget_percent,
            "threshold_percent": self.threshold_percent,
            "action": self.action,
            "manual_mode_enforced": True,
        }


# =============================================================================
# Error Budget Gate
# =============================================================================


class ErrorBudgetGate:
    """
    에러 예산 기반 자동화 제어 게이트.
    
    중앙에서 한 번만 체크하면 모든 자동화 기능에 적용됩니다.
    
    특징:
    - Thread-safe
    - 결과 캐싱 (불필요한 API 호출 방지)
    - Fail-open 설계 (게이트 장애 시 자동화 허용)
    - 감사 로깅
    
    Usage:
        gate = get_error_budget_gate()
        
        # 체크만
        result = gate.check()
        if result.allowed:
            do_automation()
        
        # 예외 발생
        gate.require(action="chaos_experiment")  # 차단 시 예외 발생
    """
    
    def __init__(self, config: Optional[ErrorBudgetGateConfig] = None):
        """Initialize ErrorBudgetGate."""
        self._config = config or ErrorBudgetGateConfig()
        self._lock = threading.RLock()
        self._cache: Optional[GateCheckResult] = None
        self._cache_time: Optional[datetime] = None
        self._load_config()
    
    def _load_config(self) -> None:
        """RuntimeConfig에서 설정 로드."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            chaos_config = manager.get_chaos_config()
            gate_config = chaos_config.get("error_budget_gate_config", {})
            
            if gate_config:
                self._config = ErrorBudgetGateConfig.from_dict(gate_config)
        except Exception as e:
            logger.warning(f"[ErrorBudgetGate] Failed to load config: {e}")
    
    def _is_cache_valid(self) -> bool:
        """캐시 유효성 체크."""
        if self._cache is None or self._cache_time is None:
            return False
        
        elapsed = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
        return elapsed < self._config.cache_ttl_seconds
    
    def _get_error_budget_percent(self) -> Optional[float]:
        """현재 에러 예산 잔여율 조회."""
        try:
            from selfhealing.services.error_budget_service import get_error_budget_service
            
            service = get_error_budget_service()
            status = service.get_current_status()
            
            if status is None:
                return None
            
            # Dict 형태인 경우 (API 응답)
            if isinstance(status, dict):
                budget = status.get("budget", {})
                return budget.get("remaining_percent", None)
            
            # ErrorBudgetStatus 객체인 경우
            if hasattr(status, "budget_remaining_percent"):
                return status.budget_remaining_percent
            
            return None
            
        except Exception as e:
            logger.error(f"[ErrorBudgetGate] Failed to get error budget: {e}")
            return None
    
    def get_config(self) -> ErrorBudgetGateConfig:
        """현재 설정 반환."""
        return self._config
    
    def update_config(self, **kwargs) -> ErrorBudgetGateConfig:
        """설정 업데이트."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(f"[ErrorBudgetGate] Updated config.{key} = {value}")
            
            # 캐시 무효화
            self._cache = None
            self._cache_time = None
            
            # RuntimeConfig에 저장
            self._persist_config()
            
            return self._config
    
    def _persist_config(self) -> None:
        """설정을 RuntimeConfig에 저장."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(error_budget_gate_config=self._config.to_dict())
        except Exception as e:
            logger.warning(f"[ErrorBudgetGate] Failed to persist config: {e}")
    
    def check(self, force_refresh: bool = False) -> GateCheckResult:
        """
        자동화 허용 여부 체크.
        
        Args:
            force_refresh: 캐시 무시하고 새로 조회
        
        Returns:
            GateCheckResult: 체크 결과
        """
        with self._lock:
            # 게이트 비활성화 시
            if not self._config.enabled:
                return GateCheckResult(
                    allowed=True,
                    status=GateStatus.DISABLED,
                    reason="Error budget gate is disabled",
                    recommendation="Gate disabled - all automation allowed",
                )
            
            # 캐시 사용
            if not force_refresh and self._is_cache_valid() and self._cache:
                return self._cache
            
            # 에러 예산 조회
            budget_percent = self._get_error_budget_percent()
            
            # 조회 실패 시 Fail-open
            if budget_percent is None:
                result = self._handle_fail_open()
                self._cache = result
                self._cache_time = datetime.now(timezone.utc)
                return result
            
            # 정상 판정
            result = self._evaluate(budget_percent)
            self._cache = result
            self._cache_time = datetime.now(timezone.utc)
            
            # 로깅
            if result.status == GateStatus.BLOCKED:
                logger.warning(
                    f"[ErrorBudgetGate] AUTOMATION BLOCKED - "
                    f"Error budget {budget_percent:.1f}% < {self._config.critical_threshold_percent}%"
                )
            elif result.status == GateStatus.WARNING:
                logger.info(
                    f"[ErrorBudgetGate] Warning - Error budget {budget_percent:.1f}% "
                    f"< {self._config.warning_threshold_percent}%"
                )
            
            return result
    
    def _evaluate(self, budget_percent: float) -> GateCheckResult:
        """에러 예산 기반 판정."""
        # 위험 수준 - 차단
        if budget_percent < self._config.critical_threshold_percent:
            return GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=budget_percent,
                threshold_percent=self._config.critical_threshold_percent,
                reason=f"Error budget critically low: {budget_percent:.1f}% < {self._config.critical_threshold_percent}%",
                recommendation=(
                    "모든 자동화 기능이 중단되었습니다. "
                    "수동 검토 후 조치하세요. "
                    "에러 예산이 회복되면 자동으로 재개됩니다."
                ),
            )
        
        # 경고 수준 - 허용하되 경고
        if budget_percent < self._config.warning_threshold_percent:
            return GateCheckResult(
                allowed=True,
                status=GateStatus.WARNING,
                error_budget_percent=budget_percent,
                threshold_percent=self._config.critical_threshold_percent,
                reason=f"Error budget low: {budget_percent:.1f}% < {self._config.warning_threshold_percent}%",
                recommendation=(
                    "에러 예산이 낮습니다. "
                    "자동화는 계속 허용되지만, 수동 확인을 권장합니다."
                ),
            )
        
        # 정상 - 허용
        return GateCheckResult(
            allowed=True,
            status=GateStatus.OPEN,
            error_budget_percent=budget_percent,
            threshold_percent=self._config.critical_threshold_percent,
            reason=f"Error budget healthy: {budget_percent:.1f}%",
            recommendation="자동화 정상 동작 중",
        )
    
    def _handle_fail_open(self) -> GateCheckResult:
        """Fail-open 처리."""
        if self._config.fail_open:
            logger.warning(
                "[ErrorBudgetGate] FAIL-OPEN triggered - "
                "Could not retrieve error budget, allowing automation"
            )
            
            # 메트릭 기록
            try:
                from selfhealing.services.metrics import record_fail_safe_triggered
                record_fail_safe_triggered(
                    component="error_budget_gate",
                    reason="error_budget_retrieval_failed",
                    fallback_action="fail_open",
                )
            except Exception:
                pass
            
            return GateCheckResult(
                allowed=True,
                status=GateStatus.FAIL_OPEN,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason="Error budget retrieval failed - fail-open policy applied",
                recommendation=(
                    "에러 예산 조회에 실패했습니다. "
                    "Fail-open 정책에 따라 자동화를 허용합니다. "
                    "에러 예산 시스템을 확인하세요."
                ),
                fail_open_triggered=True,
            )
        else:
            # Fail-close (권장하지 않음)
            logger.error(
                "[ErrorBudgetGate] FAIL-CLOSE triggered - "
                "Could not retrieve error budget, blocking automation"
            )
            return GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason="Error budget retrieval failed - fail-close policy applied",
                recommendation=(
                    "에러 예산 조회에 실패했습니다. "
                    "Fail-close 정책에 따라 자동화를 차단합니다."
                ),
                fail_open_triggered=True,
            )
    
    def require(self, action: str = "") -> GateCheckResult:
        """
        자동화 허용 필수 체크 - 차단 시 예외 발생.
        
        Args:
            action: 수행하려는 작업 이름 (로깅용)
        
        Returns:
            GateCheckResult: 허용된 경우의 체크 결과
        
        Raises:
            AutomationBlockedError: 차단된 경우
        """
        result = self.check()
        
        if not result.allowed:
            logger.warning(
                f"[ErrorBudgetGate] Action blocked: {action or 'unknown'} - "
                f"Error budget: {result.error_budget_percent}%"
            )
            
            # 감사 로깅
            self._audit_block(action, result)
            
            raise AutomationBlockedError(
                message=result.reason,
                error_budget_percent=result.error_budget_percent,
                threshold_percent=result.threshold_percent,
                action=action,
            )
        
        return result
    
    def _audit_block(self, action: str, result: GateCheckResult) -> None:
        """차단 이벤트 감사 로깅."""
        try:
            from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction
            from selfhealing.adapters.audit import get_audit_adapter
            
            adapter = get_audit_adapter()
            adapter.log(AuditEntry(
                action=AuditAction.MANUAL_OVERRIDE,
                target_type="automation",
                target_id=action,
                reason="Error budget gate blocked automation",
                details={
                    "gate_status": result.status.value,
                    "error_budget_percent": result.error_budget_percent,
                    "threshold_percent": result.threshold_percent,
                    "manual_mode_enforced": True,
                },
                success=False,
                error_message=result.reason,
            ))
        except Exception as e:
            logger.warning(f"[ErrorBudgetGate] Failed to audit block: {e}")
    
    def clear_cache(self) -> None:
        """캐시 초기화."""
        with self._lock:
            self._cache = None
            self._cache_time = None


# =============================================================================
# Singleton & Convenience Functions
# =============================================================================


_gate_instance: Optional[ErrorBudgetGate] = None
_gate_lock = threading.Lock()


def get_error_budget_gate() -> ErrorBudgetGate:
    """ErrorBudgetGate 싱글톤 인스턴스 반환."""
    global _gate_instance
    
    if _gate_instance is None:
        with _gate_lock:
            if _gate_instance is None:
                _gate_instance = ErrorBudgetGate()
    
    return _gate_instance


def check_automation_allowed(force_refresh: bool = False) -> GateCheckResult:
    """
    자동화 허용 여부 체크 (편의 함수).
    
    Usage:
        result = check_automation_allowed()
        if result.allowed:
            do_automation()
        else:
            # 수동 처리
            notify_operator(result.reason)
    """
    gate = get_error_budget_gate()
    return gate.check(force_refresh=force_refresh)


def require_automation_allowed(action: str = "") -> GateCheckResult:
    """
    자동화 허용 필수 체크 (편의 함수).
    
    차단 시 AutomationBlockedError 예외를 발생시킵니다.
    
    Usage:
        try:
            require_automation_allowed(action="chaos_experiment")
            do_automation()
        except AutomationBlockedError as e:
            notify_operator(e.message)
    """
    gate = get_error_budget_gate()
    return gate.require(action=action)


def is_automation_allowed() -> bool:
    """
    자동화 허용 여부 (단순 bool 반환).
    
    상세 정보가 필요 없을 때 사용합니다.
    """
    result = check_automation_allowed()
    return result.allowed


# =============================================================================
# Decorator
# =============================================================================


def automation_gate(action: str = ""):
    """
    자동화 게이트 데코레이터.
    
    에러 예산이 부족하면 함수 실행을 차단합니다.
    
    Usage:
        @automation_gate(action="dlq_auto_replay")
        def auto_replay_dlq():
            # 에러 예산이 충분할 때만 실행됨
            ...
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    
    return decorator
