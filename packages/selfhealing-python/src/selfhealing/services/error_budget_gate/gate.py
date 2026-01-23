"""
Error Budget Gate - Core Gate Class.

에러 예산 기반 자동화 제어 게이트.
위기 상황일수록 인간의 개입을 강제하는 설계.
"""

from __future__ import annotations

import functools
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from selfhealing.services.error_budget_gate.config import (
    ErrorBudgetGateConfig,
    GateStatus,
    GateCheckResult,
)
from selfhealing.services.error_budget_gate.rate_limiter import InMemoryRateLimiter
from selfhealing.services.error_budget_gate.fault_detector import GateFaultDetector
from selfhealing.services.error_budget_gate.alert_manager import GateAlertManager
from selfhealing.services.error_budget_gate.exceptions import AutomationBlockedError

logger = logging.getLogger(__name__)


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
        
        # Fail-Open Rate Limiter 초기화 (Redis 의존 없음)
        self._fail_open_rate_limiter = InMemoryRateLimiter(
            max_requests=self._config.fail_open_rate_limit_per_minute,
            window_seconds=self._config.fail_open_rate_limit_window_seconds,
        )
        
        # Gate Fault Detector 초기화 (Error Budget 서비스 장애 시 빠른 Fail-Open)
        # ⚠️ 이것은 메인 CircuitBreakerService와 다릅니다 (Gate 전용)
        self._fault_detector = GateFaultDetector(
            failure_threshold=self._config.circuit_breaker_failure_threshold,
            recovery_timeout=self._config.circuit_breaker_recovery_timeout,
        )
        
        # Alert Manager 초기화 (Fail-Open 시 알림 발송)
        self._alert_manager = GateAlertManager(
            cooldown_seconds=self._config.alert_cooldown_seconds,
        )
        
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
                # 컴포넌트 설정 동기화
                self._fail_open_rate_limiter.update_limits(
                    max_requests=self._config.fail_open_rate_limit_per_minute,
                    window_seconds=self._config.fail_open_rate_limit_window_seconds,
                )
                self._fault_detector.update_config(
                    failure_threshold=self._config.circuit_breaker_failure_threshold,
                    recovery_timeout=self._config.circuit_breaker_recovery_timeout,
                )
                self._alert_manager.update_config(
                    cooldown_seconds=self._config.alert_cooldown_seconds,
                )
        except Exception as e:
            logger.warning(f"[ErrorBudgetGate] Failed to load config: {e}")
    
    def _is_cache_valid(self) -> bool:
        """캐시 유효성 체크."""
        if self._cache is None or self._cache_time is None:
            return False
        
        elapsed = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
        return elapsed < self._config.cache_ttl_seconds
    
    def _get_error_budget_percent(self) -> Optional[float]:
        """
        현재 에러 예산 잔여율 조회.
        
        Gate Fault Detector가 적용되어 반복 실패 시 빠른 Fail-Open 처리.
        """
        # Fault Detector 체크
        if self._config.circuit_breaker_enabled and not self._fault_detector.can_execute():
            logger.debug("[ErrorBudgetGate] Fault detector DEGRADED - fast fail-open")
            return None
        
        try:
            from selfhealing.services.error_budget_service import get_error_budget_service
            
            service = get_error_budget_service()
            status = service.get_current_status()
            
            if status is None:
                self._fault_detector.record_failure()
                return None
            
            # Dict 형태인 경우 (API 응답)
            if isinstance(status, dict):
                budget = status.get("budget", {})
                result = budget.get("remaining_percent", None)
                if result is not None:
                    self._fault_detector.record_success()
                    return result
                self._fault_detector.record_failure()
                return None
            
            # ErrorBudgetStatus 객체인 경우
            if hasattr(status, "budget_remaining_percent"):
                self._fault_detector.record_success()
                return status.budget_remaining_percent
            
            self._fault_detector.record_failure()
            return None
            
        except Exception as e:
            logger.error(f"[ErrorBudgetGate] Failed to get error budget: {e}")
            self._fault_detector.record_failure()
            
            # Fault Detector Degraded 시 알림
            fd_status = self._fault_detector.get_status()
            if fd_status["state"] == "degraded" and self._config.alert_on_fail_open:
                self._alert_manager.send_circuit_open_alert(fd_status["failure_count"])
            
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
            
            # 컴포넌트 설정 동기화
            self._fail_open_rate_limiter.update_limits(
                max_requests=self._config.fail_open_rate_limit_per_minute,
                window_seconds=self._config.fail_open_rate_limit_window_seconds,
            )
            self._fault_detector.update_config(
                failure_threshold=self._config.circuit_breaker_failure_threshold,
                recovery_timeout=self._config.circuit_breaker_recovery_timeout,
            )
            self._alert_manager.update_config(
                cooldown_seconds=self._config.alert_cooldown_seconds,
            )
            
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
            # 이벤트 발행: 에러 예산 임계치 도달
            self._emit_error_budget_critical_event(budget_percent)
            
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
            # 이벤트 발행: 에러 예산 경고
            self._emit_error_budget_warning_event(budget_percent)
            
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
        """
        Fail-open 처리 (Rate Limit 적용).
        
        Redis/DB 장애 상황에서도 무한 폭주를 방지하기 위해
        Rate Limit을 적용한 "최소한의 제약이 있는 방임" 정책을 사용합니다.
        """
        if not self._config.fail_open:
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
        
        # Fail-open with Rate Limiting
        logger.warning(
            "[ErrorBudgetGate] FAIL-OPEN triggered - "
            "Could not retrieve error budget"
        )
        
        # 메트릭 기록
        try:
            from selfhealing.services.metrics.recorders import record_failsafe_triggered
            record_failsafe_triggered(component="error_budget_gate")
        except Exception:
            pass
        
        # Rate Limit 적용 여부 확인
        if not self._config.fail_open_rate_limit_enabled:
            # Rate Limit 비활성화 - 무조건 허용 (기존 동작)
            logger.info("[ErrorBudgetGate] Rate limiting disabled - allowing without limit")
            return GateCheckResult(
                allowed=True,
                status=GateStatus.FAIL_OPEN,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason="Error budget retrieval failed - fail-open policy applied (no rate limit)",
                recommendation=(
                    "에러 예산 조회에 실패했습니다. "
                    "Fail-open 정책에 따라 자동화를 허용합니다. "
                    "에러 예산 시스템을 확인하세요."
                ),
                fail_open_triggered=True,
            )
        
        # Rate Limit 체크 (Redis 의존 없는 메모리 기반)
        allowed, remaining, reset_at = self._fail_open_rate_limiter.try_acquire()
        
        if allowed:
            logger.info(
                f"[ErrorBudgetGate] FAIL-OPEN allowed (rate limit: {remaining} remaining)"
            )
            
            # Fail-Open 알림 발송
            if self._config.alert_on_fail_open:
                self._alert_manager.send_fail_open_alert(
                    reason="Error budget service unavailable",
                    rate_limit_remaining=remaining,
                )
            
            return GateCheckResult(
                allowed=True,
                status=GateStatus.FAIL_OPEN,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason=(
                    f"Error budget retrieval failed - fail-open with rate limit "
                    f"({remaining} requests remaining)"
                ),
                recommendation=(
                    "에러 예산 조회에 실패했습니다. "
                    f"Rate Limit 내에서 자동화를 허용합니다 (잔여: {remaining}회). "
                    "에러 예산 시스템을 확인하세요."
                ),
                fail_open_triggered=True,
                rate_limit_remaining=remaining,
                rate_limit_reset_at=reset_at,
            )
        else:
            # Rate Limit 초과 - 차단
            logger.warning(
                f"[ErrorBudgetGate] FAIL-OPEN rate limit exceeded - "
                f"blocking automation (reset at {reset_at.isoformat()})"
            )
            
            # Rate Limit 초과 알림 발송
            if self._config.alert_on_fail_open:
                self._alert_manager.send_rate_limit_exceeded_alert()
            
            # Rate Limit 초과 메트릭
            try:
                from selfhealing.services.metrics.recorders import record_failsafe_triggered
                record_failsafe_triggered(component="error_budget_gate_rate_limited")
            except Exception:
                pass
            
            return GateCheckResult(
                allowed=False,
                status=GateStatus.FAIL_OPEN_RATE_LIMITED,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason=(
                    f"Error budget retrieval failed and rate limit exceeded "
                    f"({self._config.fail_open_rate_limit_per_minute}/min)"
                ),
                recommendation=(
                    "에러 예산 조회 실패 상황에서 Rate Limit을 초과했습니다. "
                    f"자동화가 일시 차단됩니다 (리셋: {reset_at.strftime('%H:%M:%S')}). "
                    "에러 예산 시스템을 즉시 확인하세요."
                ),
                fail_open_triggered=True,
                rate_limit_remaining=0,
                rate_limit_reset_at=reset_at,
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
        """
        차단 이벤트 감사 로깅.
        
        audit_helpers 통합:
        - 기존: AuditAdapter.log 직접 호출
        - 변경: log_error_budget_blocked_audit 헬퍼 사용 (WAL + 해시 체인 연결)
        """
        try:
            from selfhealing.services.audit_helpers import log_error_budget_blocked_audit
            
            log_error_budget_blocked_audit(
                action=action,
                gate_status=result.status.value,
                error_budget_percent=result.error_budget_percent,
                threshold_percent=result.threshold_percent,
                reason=result.reason,
            )
        except Exception as e:
            logger.warning(f"[ErrorBudgetGate] Failed to audit block: {e}")
    
    def clear_cache(self) -> None:
        """캐시 초기화."""
        with self._lock:
            self._cache = None
            self._cache_time = None
    
    # -------------------------------------------------------------------------
    # Event Bus
    # -------------------------------------------------------------------------
    
    def _emit_error_budget_critical_event(self, budget_percent: float) -> None:
        """
        에러 예산 임계치 도달 이벤트 발행.
        
        Chaos 실험 자동 차단, 자동 Replay 일시 중지 등
        다른 컴포넌트가 이 이벤트를 구독하여 반응합니다.
        """
        try:
            from selfhealing.services.event_bus import (
                get_event_bus,
                EventType,
                EventPriority,
            )
            
            bus = get_event_bus()
            bus.emit(
                event_type=EventType.ERROR_BUDGET_CRITICAL,
                data={
                    "budget_percent": budget_percent,
                    "threshold": self._config.critical_threshold_percent,
                    "status": "critical",
                },
                source="error_budget_gate",
                priority=EventPriority.CRITICAL,
            )
        except Exception as e:
            # 이벤트 발행 실패해도 Gate 동작에는 영향 없음
            logger.warning(f"[ErrorBudgetGate] Failed to emit critical event: {e}")
    
    def _emit_error_budget_warning_event(self, budget_percent: float) -> None:
        """에러 예산 경고 이벤트 발행."""
        try:
            from selfhealing.services.event_bus import (
                get_event_bus,
                EventType,
                EventPriority,
            )
            
            bus = get_event_bus()
            bus.emit(
                event_type=EventType.ERROR_BUDGET_WARNING,
                data={
                    "budget_percent": budget_percent,
                    "threshold": self._config.warning_threshold_percent,
                    "status": "warning",
                },
                source="error_budget_gate",
                priority=EventPriority.HIGH,
            )
        except Exception as e:
            logger.warning(f"[ErrorBudgetGate] Failed to emit warning event: {e}")
    
    # -------------------------------------------------------------------------
    # Rate Limiter & Fault Detector Status
    # -------------------------------------------------------------------------
    
    def get_rate_limiter_status(self) -> Dict[str, Any]:
        """
        Fail-Open Rate Limiter 현재 상태 조회.
        
        Returns:
            Rate limiter 상태 정보 (현재 카운트, 최대값, 남은 횟수 등)
        """
        status = self._fail_open_rate_limiter.get_status()
        status["enabled"] = self._config.fail_open_rate_limit_enabled
        return status
    
    def reset_rate_limiter(self) -> None:
        """
        Fail-Open Rate Limiter 초기화.
        
        관리/테스트 목적으로 Rate Limiter를 리셋합니다.
        주의: 프로덕션에서는 신중하게 사용하세요.
        """
        self._fail_open_rate_limiter.reset()
        logger.info("[ErrorBudgetGate] Rate limiter reset by admin action")
    
    def get_fault_detector_status(self) -> Dict[str, Any]:
        """Gate Fault Detector 현재 상태 조회."""
        status = self._fault_detector.get_status()
        status["enabled"] = self._config.circuit_breaker_enabled
        return status
    
    # 하위 호환성을 위한 별칭 (Deprecated)
    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """
        Gate Fault Detector 현재 상태 조회.

        .. deprecated:: 2.0.0
            Use :meth:`get_fault_detector_status` instead.
            Will be removed in version 3.0.0.
        """
        import warnings
        warnings.warn(
            "get_circuit_breaker_status() is deprecated. "
            "Use get_fault_detector_status() instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_fault_detector_status()
    
    def reset_fault_detector(self) -> None:
        """Gate Fault Detector 리셋."""
        self._fault_detector.reset()
        logger.info("[ErrorBudgetGate] Fault detector reset by admin action")
    
    # 하위 호환성을 위한 별칭 (Deprecated)
    def reset_circuit_breaker(self) -> None:
        """
        Gate Fault Detector 리셋.

        .. deprecated:: 2.0.0
            Use :meth:`reset_fault_detector` instead.
            Will be removed in version 3.0.0.
        """
        import warnings
        warnings.warn(
            "reset_circuit_breaker() is deprecated. "
            "Use reset_fault_detector() instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.reset_fault_detector()
    
    def get_alert_status(self) -> Dict[str, Any]:
        """Alert Manager 현재 상태 조회."""
        status = self._alert_manager.get_status()
        status["enabled"] = self._config.alert_on_fail_open
        return status
    
    def reset_alert_cooldowns(self) -> None:
        """알림 쿨다운 리셋."""
        self._alert_manager.reset()
        logger.info("[ErrorBudgetGate] Alert cooldowns reset by admin action")
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Gate 전체 헬스 상태 조회.
        
        헬스체크 엔드포인트 (/health/gate)용 종합 상태 정보.
        
        Returns:
            종합 헬스 상태 딕셔너리
        """
        # 현재 Gate 상태 체크
        try:
            current_result = self.check(force_refresh=True)
            gate_status = current_result.status.value
            gate_healthy = current_result.status not in (
                GateStatus.FAIL_OPEN,
                GateStatus.FAIL_OPEN_RATE_LIMITED,
            )
        except Exception as e:
            gate_status = "error"
            gate_healthy = False
            logger.error(f"[ErrorBudgetGate] Health check failed: {e}")
        
        # 컴포넌트 상태
        fault_detector_status = self.get_fault_detector_status()
        rate_limiter_status = self.get_rate_limiter_status()
        alert_status = self.get_alert_status()
        
        # 종합 건강 상태 판단
        is_healthy = (
            gate_healthy
            and fault_detector_status.get("state") != "degraded"
        )
        
        return {
            "healthy": is_healthy,
            "status": "healthy" if is_healthy else "degraded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gate": {
                "enabled": self._config.enabled,
                "status": gate_status,
                "fail_open_triggered": not gate_healthy,
            },
            "fault_detector": fault_detector_status,
            "circuit_breaker": fault_detector_status,  # 하위 호환성 별칭
            "rate_limiter": rate_limiter_status,
            "alerts": alert_status,
            "config": {
                "critical_threshold_percent": self._config.critical_threshold_percent,
                "warning_threshold_percent": self._config.warning_threshold_percent,
                "fail_open": self._config.fail_open,
            },
        }


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
    자동화 게이트 데코레이터 (동기/비동기 모두 지원).
    
    에러 예산이 부족하면 함수 실행을 차단합니다.
    asyncio.iscoroutinefunction()으로 자동 분기하여
    sync/async 함수 모두 동일하게 사용 가능합니다.
    
    Args:
        action: 게이트 체크에 사용될 액션 이름.
                생략 시 함수 이름 사용.
    
    Usage:
        >>> @automation_gate(action="dlq_auto_replay")
        ... def auto_replay_dlq():
        ...     # 에러 예산이 충분할 때만 실행됨
        ...     pass
        
        >>> @automation_gate(action="async_cleanup")
        ... async def async_cleanup():
        ...     # 비동기 함수도 동일하게 사용
        ...     await do_cleanup()
    """
    import asyncio
    
    def decorator(func: Callable):
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            # require_automation_allowed는 sync 함수지만
            # I/O 없는 빠른 체크이므로 직접 호출해도 무방
            require_automation_allowed(action=action or func.__name__)
            return await func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


__all__ = [
    "ErrorBudgetGate",
    "get_error_budget_gate",
    "check_automation_allowed",
    "require_automation_allowed",
    "is_automation_allowed",
    "automation_gate",
]
