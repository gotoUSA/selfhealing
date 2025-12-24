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
        fail_open_rate_limit_enabled: Fail-Open 시 Rate Limit 적용 여부 (기본: True)
        fail_open_rate_limit_per_minute: Fail-Open 시 분당 최대 허용 횟수 (기본: 10)
        fail_open_rate_limit_window_seconds: Rate Limit 슬라이딩 윈도우 크기 (기본: 60초)
        circuit_breaker_enabled: Circuit Breaker 활성화 여부 (기본: True)
        circuit_breaker_failure_threshold: 연속 실패 횟수 임계값 (기본: 5)
        circuit_breaker_recovery_timeout: 회로 복구 대기 시간 초 (기본: 30)
        alert_on_fail_open: Fail-Open 발동 시 알림 발송 여부 (기본: True)
        alert_cooldown_seconds: 동일 알림 재발송 쿨다운 (기본: 300초)
    """
    enabled: bool = True
    critical_threshold_percent: float = 10.0
    warning_threshold_percent: float = 20.0
    fail_open: bool = True
    cache_ttl_seconds: int = 30
    # Fail-Open Rate Limiting (최소한의 제약이 있는 방임)
    fail_open_rate_limit_enabled: bool = True
    fail_open_rate_limit_per_minute: int = 10
    fail_open_rate_limit_window_seconds: int = 60
    # Circuit Breaker (빠른 실패 처리)
    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: int = 30
    # 알림 설정
    alert_on_fail_open: bool = True
    alert_cooldown_seconds: int = 300
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "critical_threshold_percent": self.critical_threshold_percent,
            "warning_threshold_percent": self.warning_threshold_percent,
            "fail_open": self.fail_open,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "fail_open_rate_limit_enabled": self.fail_open_rate_limit_enabled,
            "fail_open_rate_limit_per_minute": self.fail_open_rate_limit_per_minute,
            "fail_open_rate_limit_window_seconds": self.fail_open_rate_limit_window_seconds,
            "circuit_breaker_enabled": self.circuit_breaker_enabled,
            "circuit_breaker_failure_threshold": self.circuit_breaker_failure_threshold,
            "circuit_breaker_recovery_timeout": self.circuit_breaker_recovery_timeout,
            "alert_on_fail_open": self.alert_on_fail_open,
            "alert_cooldown_seconds": self.alert_cooldown_seconds,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ErrorBudgetGateConfig":
        return cls(
            enabled=data.get("enabled", True),
            critical_threshold_percent=data.get("critical_threshold_percent", 10.0),
            warning_threshold_percent=data.get("warning_threshold_percent", 20.0),
            fail_open=data.get("fail_open", True),
            cache_ttl_seconds=data.get("cache_ttl_seconds", 30),
            fail_open_rate_limit_enabled=data.get("fail_open_rate_limit_enabled", True),
            fail_open_rate_limit_per_minute=data.get("fail_open_rate_limit_per_minute", 10),
            fail_open_rate_limit_window_seconds=data.get("fail_open_rate_limit_window_seconds", 60),
            circuit_breaker_enabled=data.get("circuit_breaker_enabled", True),
            circuit_breaker_failure_threshold=data.get("circuit_breaker_failure_threshold", 5),
            circuit_breaker_recovery_timeout=data.get("circuit_breaker_recovery_timeout", 30),
            alert_on_fail_open=data.get("alert_on_fail_open", True),
            alert_cooldown_seconds=data.get("alert_cooldown_seconds", 300),
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
    
    FAIL_OPEN_RATE_LIMITED = "fail_open_rate_limited"
    """자동화 차단 (Rate Limit 초과) - Fail-Open 상황에서 과도한 요청."""
    
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
    
    rate_limit_remaining: Optional[int] = None
    """Rate limit 잔여 횟수 (Fail-Open 시에만 유효)."""
    
    rate_limit_reset_at: Optional[datetime] = None
    """Rate limit 리셋 시각."""
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "allowed": self.allowed,
            "status": self.status.value,
            "error_budget_percent": self.error_budget_percent,
            "threshold_percent": self.threshold_percent,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "checked_at": self.checked_at.isoformat(),
            "fail_open_triggered": self.fail_open_triggered,
        }
        if self.rate_limit_remaining is not None:
            result["rate_limit_remaining"] = self.rate_limit_remaining
        if self.rate_limit_reset_at is not None:
            result["rate_limit_reset_at"] = self.rate_limit_reset_at.isoformat()
        return result


# =============================================================================
# In-Memory Rate Limiter (Redis 의존 없음)
# =============================================================================


class InMemoryRateLimiter:
    """
    Redis 의존 없이 동작하는 메모리 기반 Sliding Window Rate Limiter.
    
    Fail-Open 상황(Redis/DB 장애)에서도 무한 요청을 방지하기 위한
    최후의 방어선입니다. 외부 의존성 없이 순수 메모리로 동작합니다.
    
    특징:
    - Thread-safe (RLock 사용)
    - Sliding window 알고리즘
    - 자동 정리 (오래된 타임스탬프 제거)
    - 설정 동적 변경 지원
    """
    
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            max_requests: 윈도우 내 최대 요청 횟수
            window_seconds: 슬라이딩 윈도우 크기 (초)
        """
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.RLock()
    
    def update_limits(self, max_requests: int, window_seconds: int) -> None:
        """Rate limit 설정 동적 업데이트."""
        with self._lock:
            self._max_requests = max_requests
            self._window_seconds = window_seconds
            logger.info(
                f"[RateLimiter] Updated limits: {max_requests} requests / {window_seconds}s"
            )
    
    def _cleanup_old_timestamps(self, now: float) -> None:
        """윈도우 밖의 오래된 타임스탬프 정리."""
        cutoff = now - self._window_seconds
        self._timestamps = [ts for ts in self._timestamps if ts > cutoff]
    
    def try_acquire(self) -> tuple[bool, int, datetime]:
        """
        요청 허용 여부 확인 및 카운트 증가.
        
        Returns:
            tuple of:
                - allowed: 요청 허용 여부
                - remaining: 남은 요청 횟수
                - reset_at: 윈도우 리셋 시각
        """
        with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            self._cleanup_old_timestamps(now)
            
            remaining = max(0, self._max_requests - len(self._timestamps))
            reset_at = datetime.fromtimestamp(
                now + self._window_seconds, tz=timezone.utc
            )
            
            if len(self._timestamps) < self._max_requests:
                self._timestamps.append(now)
                remaining = max(0, self._max_requests - len(self._timestamps))
                return True, remaining, reset_at
            else:
                return False, 0, reset_at
    
    def get_status(self) -> Dict[str, Any]:
        """현재 Rate Limiter 상태 조회."""
        with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            self._cleanup_old_timestamps(now)
            
            return {
                "current_count": len(self._timestamps),
                "max_requests": self._max_requests,
                "window_seconds": self._window_seconds,
                "remaining": max(0, self._max_requests - len(self._timestamps)),
            }
    
    def reset(self) -> None:
        """Rate limiter 초기화 (테스트/관리용)."""
        with self._lock:
            self._timestamps.clear()
            logger.info("[RateLimiter] Reset - all timestamps cleared")


# =============================================================================
# Gate Fault Detector (Gate 내부 장애 감지)
# =============================================================================


class GateFaultState(str, Enum):
    """Gate Fault Detector 상태."""
    HEALTHY = "healthy"        # 정상 - Error Budget 조회 가능
    DEGRADED = "degraded"      # 장애 - 빠른 Fail-Open
    RECOVERING = "recovering"  # 복구 시도 중


class GateFaultDetector:
    """
    Gate 내부 장애 감지기.
    
    Error Budget Gate가 Error Budget 서비스(Redis/DB)에 반복 접근 실패 시,
    매번 타임아웃을 기다리지 않고 즉시 Fail-Open으로 응답합니다.
    
    ⚠️ 주의: 이것은 메인 CircuitBreakerService와 다릅니다!
    - GateFaultDetector: Gate 내부용, 메모리 전용 (외부 의존성 없음)
    - CircuitBreakerService: 외부 API 호출용, 분산 환경 지원
    
    상태 전이:
    - HEALTHY: 정상 동작, 실패 시 카운트 증가
    - DEGRADED: failure_threshold 초과 시, 모든 요청 즉시 Fail-Open
    - RECOVERING: recovery_timeout 후, 한 번 시도하여 성공하면 HEALTHY로 복귀
    """
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 30):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._state = GateFaultState.HEALTHY
        self._lock = threading.RLock()
    
    def update_config(self, failure_threshold: int, recovery_timeout: int) -> None:
        """설정 동적 업데이트."""
        with self._lock:
            self._failure_threshold = failure_threshold
            self._recovery_timeout = recovery_timeout
            logger.info(
                f"[CircuitBreaker] Updated: threshold={failure_threshold}, timeout={recovery_timeout}s"
            )
    
    def can_execute(self) -> bool:
        """요청 실행 가능 여부."""
        with self._lock:
            if self._state == GateFaultState.HEALTHY:
                return True
            
            if self._state == GateFaultState.DEGRADED:
                # 복구 시간이 지났는지 확인
                if self._last_failure_time:
                    elapsed = (datetime.now(timezone.utc) - self._last_failure_time).total_seconds()
                    if elapsed >= self._recovery_timeout:
                        self._state = GateFaultState.RECOVERING
                        logger.info("[GateFaultDetector] State: DEGRADED -> RECOVERING (attempting recovery)")
                        return True
                return False
            
            # RECOVERING: 한 번 시도 허용
            return True
    
    def record_success(self) -> None:
        """성공 기록."""
        with self._lock:
            if self._state == GateFaultState.RECOVERING:
                logger.info("[GateFaultDetector] State: RECOVERING -> HEALTHY (recovered)")
            self._state = GateFaultState.HEALTHY
            self._failure_count = 0
    
    def record_failure(self) -> None:
        """실패 기록."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now(timezone.utc)
            
            if self._state == GateFaultState.RECOVERING:
                # 복구 실패 - 다시 DEGRADED
                self._state = GateFaultState.DEGRADED
                logger.warning("[GateFaultDetector] State: RECOVERING -> DEGRADED (recovery failed)")
            elif self._failure_count >= self._failure_threshold:
                self._state = GateFaultState.DEGRADED
                logger.warning(
                    f"[GateFaultDetector] State: HEALTHY -> DEGRADED "
                    f"(failures: {self._failure_count}/{self._failure_threshold})"
                )
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 조회."""
        with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "last_failure_time": self._last_failure_time.isoformat() if self._last_failure_time else None,
            }
    
    def reset(self) -> None:
        """Gate Fault Detector 리셋."""
        with self._lock:
            self._state = GateFaultState.HEALTHY
            self._failure_count = 0
            self._last_failure_time = None
            logger.info("[GateFaultDetector] Reset to HEALTHY state")


# =============================================================================
# 하위 호환성 별칭 (Deprecated)
# =============================================================================

# 기존 이름 유지 - 추후 제거 예정
CircuitState = GateFaultState  # Deprecated: use GateFaultState
InMemoryCircuitBreaker = GateFaultDetector  # Deprecated: use GateFaultDetector


# =============================================================================
# Alert Manager (Fail-Open 알림)
# =============================================================================


class GateAlertManager:
    """
    Gate 상태 변경 시 알림 발송 관리.
    
    Fail-Open 발동, Rate Limit 초과 등 중요 이벤트에 대해
    Slack/PagerDuty 등으로 알림을 발송합니다.
    쿨다운 적용으로 알림 폭주를 방지합니다.
    """
    
    def __init__(self, cooldown_seconds: int = 300):
        self._cooldown_seconds = cooldown_seconds
        self._last_alert_times: Dict[str, datetime] = {}
        self._lock = threading.RLock()
    
    def update_config(self, cooldown_seconds: int) -> None:
        """설정 동적 업데이트."""
        with self._lock:
            self._cooldown_seconds = cooldown_seconds
    
    def _can_send_alert(self, alert_type: str) -> bool:
        """쿨다운 확인."""
        with self._lock:
            last_time = self._last_alert_times.get(alert_type)
            if last_time is None:
                return True
            elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
            return elapsed >= self._cooldown_seconds
    
    def _record_alert_sent(self, alert_type: str) -> None:
        """알림 발송 기록."""
        with self._lock:
            self._last_alert_times[alert_type] = datetime.now(timezone.utc)
    
    def send_fail_open_alert(self, reason: str, rate_limit_remaining: Optional[int] = None) -> bool:
        """
        Fail-Open 발동 알림.
        
        Returns:
            bool: 알림 발송 여부 (쿨다운 중이면 False)
        """
        alert_type = "fail_open"
        if not self._can_send_alert(alert_type):
            logger.debug(f"[GateAlert] Skipping {alert_type} alert (cooldown)")
            return False
        
        try:
            self._send_notification(
                title="🔶 Error Budget Gate: Fail-Open 발동",
                message=(
                    f"Error Budget 조회에 실패하여 Fail-Open 모드로 전환되었습니다.\n"
                    f"• 사유: {reason}\n"
                    f"• Rate Limit 잔여: {rate_limit_remaining if rate_limit_remaining is not None else 'N/A'}\n"
                    f"• 조치: Error Budget 서비스 상태를 확인하세요."
                ),
                severity="warning",
            )
            self._record_alert_sent(alert_type)
            return True
        except Exception as e:
            logger.warning(f"[GateAlert] Failed to send fail_open alert: {e}")
            return False
    
    def send_rate_limit_exceeded_alert(self) -> bool:
        """Rate Limit 초과 알림."""
        alert_type = "rate_limit_exceeded"
        if not self._can_send_alert(alert_type):
            return False
        
        try:
            self._send_notification(
                title="🔴 Error Budget Gate: Rate Limit 초과",
                message=(
                    "Fail-Open 상태에서 Rate Limit을 초과하여 자동화가 차단되었습니다.\n"
                    "Error Budget 서비스를 즉시 복구하세요."
                ),
                severity="critical",
            )
            self._record_alert_sent(alert_type)
            return True
        except Exception as e:
            logger.warning(f"[GateAlert] Failed to send rate_limit alert: {e}")
            return False
    
    def send_circuit_open_alert(self, failure_count: int) -> bool:
        """Circuit Breaker Open 알림."""
        alert_type = "circuit_open"
        if not self._can_send_alert(alert_type):
            return False
        
        try:
            self._send_notification(
                title="🔴 Error Budget Gate: Circuit Breaker Open",
                message=(
                    f"Error Budget 서비스가 {failure_count}회 연속 실패하여 Circuit Breaker가 열렸습니다.\n"
                    "서비스 상태를 확인하세요."
                ),
                severity="critical",
            )
            self._record_alert_sent(alert_type)
            return True
        except Exception as e:
            logger.warning(f"[GateAlert] Failed to send circuit_open alert: {e}")
            return False
    
    def _send_notification(self, title: str, message: str, severity: str) -> None:
        """실제 알림 발송 (Notification 서비스 연동)."""
        try:
            from selfhealing.services.notification import get_notification_service
            
            service = get_notification_service()
            service.send(
                channel="slack",  # 또는 설정에 따라
                title=title,
                message=message,
                severity=severity,
                tags=["error_budget_gate", "fail_open"],
            )
            logger.info(f"[GateAlert] Sent alert: {title}")
        except ImportError:
            # Notification 서비스가 없으면 로그만
            logger.warning(f"[GateAlert] {title}: {message}")
        except Exception as e:
            logger.warning(f"[GateAlert] Notification failed: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """알림 상태 조회."""
        with self._lock:
            return {
                "cooldown_seconds": self._cooldown_seconds,
                "last_alerts": {
                    k: v.isoformat() for k, v in self._last_alert_times.items()
                },
            }
    
    def reset(self) -> None:
        """알림 쿨다운 리셋."""
        with self._lock:
            self._last_alert_times.clear()
            logger.info("[GateAlert] Alert cooldowns reset")


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
            from selfhealing.services.metrics import record_fail_safe_triggered
            record_fail_safe_triggered(
                component="error_budget_gate",
                reason="error_budget_retrieval_failed",
                fallback_action="fail_open_with_rate_limit",
            )
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
                from selfhealing.services.metrics import record_fail_safe_triggered
                record_fail_safe_triggered(
                    component="error_budget_gate",
                    reason="fail_open_rate_limit_exceeded",
                    fallback_action="blocked",
                )
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
    
    # 하위 호환성을 위한 별칭
    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """Deprecated: get_fault_detector_status() 사용 권장."""
        return self.get_fault_detector_status()
    
    def reset_fault_detector(self) -> None:
        """Gate Fault Detector 리셋."""
        self._fault_detector.reset()
        logger.info("[ErrorBudgetGate] Fault detector reset by admin action")
    
    # 하위 호환성을 위한 별칭
    def reset_circuit_breaker(self) -> None:
        """Deprecated: reset_fault_detector() 사용 권장."""
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
