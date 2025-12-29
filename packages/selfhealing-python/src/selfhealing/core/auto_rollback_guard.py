"""
Auto Rollback Guard - 자율 조정 실패 대비 안전장치

RuntimeFeedbackLoop이 작동하지 않거나 잘못된 조정을 수행했을 때
자동으로 복구하는 독립적인 안전장치

핵심 기능:
1. 주기적 헬스체크로 시스템 상태 모니터링
2. 심각한 저하 감지 시 자동 롤백
3. RuntimeFeedbackLoop 자체의 장애 감지
4. 긴급 복구 모드 (모든 설정을 안전한 기본값으로)

이 모듈은 문서 36에 명시되지 않은 추가 안전장치입니다.

Architecture:
┌─────────────────────────────────────────────────────────────┐
│                    AutoRollbackGuard                         │
│                                                              │
│  ┌──────────────────┐    ┌──────────────────┐               │
│  │ Health Monitor   │───▶│ Rollback Executor│               │
│  │ (독립 스레드)    │    │                  │               │
│  └──────────────────┘    └──────────────────┘               │
│           │                       │                          │
│           ▼                       ▼                          │
│  ┌──────────────────┐    ┌──────────────────┐               │
│  │ Degradation      │    │ Safe Defaults    │               │
│  │ Detector         │    │ Registry         │               │
│  └──────────────────┘    └──────────────────┘               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Protocol
from enum import Enum

logger = logging.getLogger(__name__)


class GuardState(str, Enum):
    """가드 상태"""
    INACTIVE = "inactive"
    MONITORING = "monitoring"
    ALERT = "alert"
    EMERGENCY = "emergency"
    RECOVERING = "recovering"


class DegradationLevel(str, Enum):
    """저하 수준"""
    NONE = "none"
    MINOR = "minor"       # 경미 - 알림만
    MAJOR = "major"       # 심각 - 롤백 고려
    CRITICAL = "critical" # 긴급 - 즉시 롤백


@dataclass
class HealthCheckResult:
    """헬스체크 결과"""
    healthy: bool
    degradation_level: DegradationLevel
    error_rate: float
    latency_p99_ms: float
    throughput_rps: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SafeDefault:
    """안전한 기본값 정의"""
    parameter: str
    safe_value: float
    description: str


class MetricsProvider(Protocol):
    """메트릭 제공자 프로토콜"""
    
    def get_error_rate(self) -> float:
        """현재 에러율 (0.0 ~ 1.0)"""
        ...
    
    def get_latency_p99(self) -> float:
        """P99 레이턴시 (ms)"""
        ...
    
    def get_throughput(self) -> float:
        """처리량 (rps)"""
        ...


class AutoRollbackGuard:
    """
    자율 조정 실패 대비 독립 안전장치
    
    RuntimeFeedbackLoop과 독립적으로 작동하여
    시스템 상태를 모니터링하고 문제 발생 시 자동 복구
    
    Safety Features:
    1. 독립 스레드로 RuntimeFeedbackLoop 장애와 무관하게 작동
    2. 연속 헬스체크 실패 시 자동 롤백
    3. 긴급 모드: 모든 설정을 안전한 기본값으로 복원
    4. 수동 트리거 지원
    
    복구 전략 (우선순위):
    1. Last Known Good: 바꾸기 직전 상태로 롤백 (가장 안전)
    2. DNA Declared: DNA에 선언된 Desired 상태로 복구
    3. System Defaults: 하드코딩된 안전한 기본값 (최후 수단)
    """
    
    class RecoveryStrategy(Enum):
        """복구 전략"""
        LAST_KNOWN_GOOD = "last_known_good"  # 바꾸기 전 상태로 롤백
        DNA_DECLARED = "dna_declared"        # DNA 선언값으로 복구
        SYSTEM_DEFAULTS = "system_defaults"  # 하드코딩 기본값 (최후 수단)
        PAUSE_ONLY = "pause_only"            # 조정만 중지, 현재값 유지
    
    # 시스템 기본값 (정말 최후 수단 - DNA도 스냅샷도 없을 때만 사용)
    SYSTEM_DEFAULTS: List[SafeDefault] = [
        SafeDefault("timeout_ms", 5000, "시스템 기본 타임아웃 - 보수적 값"),
        SafeDefault("retry_count", 3, "시스템 기본 재시도 - 일반적 값"),
        SafeDefault("circuit_breaker_threshold", 0.5, "시스템 기본 CB - 중간값"),
        SafeDefault("jitter_range", 0.1, "시스템 기본 지터"),
        SafeDefault("rate_limit_rps", 1000, "시스템 기본 Rate Limit - 보수적"),
    ]
    
    # 임계값
    ERROR_RATE_MAJOR = 0.1      # 10% 이상 에러 → MAJOR
    ERROR_RATE_CRITICAL = 0.3   # 30% 이상 에러 → CRITICAL
    LATENCY_MAJOR_MS = 5000     # 5초 이상 레이턴시 → MAJOR
    LATENCY_CRITICAL_MS = 10000 # 10초 이상 레이턴시 → CRITICAL
    
    # 연속 실패 임계값
    CONSECUTIVE_FAILURES_ALERT = 3
    CONSECUTIVE_FAILURES_EMERGENCY = 5
    
    def __init__(
        self,
        metrics_provider: MetricsProvider,
        config_applier,  # ConfigApplier
        alert_callback: Optional[Callable[[str, str], None]] = None,
        check_interval_seconds: int = 30,
        enabled: bool = True,
    ):
        self.metrics_provider = metrics_provider
        self.config_applier = config_applier
        self.alert_callback = alert_callback
        self.check_interval_seconds = check_interval_seconds
        self.enabled = enabled
        
        # 상태 관리
        self._state = GuardState.INACTIVE
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # 헬스체크 이력
        self._health_history: List[HealthCheckResult] = []
        self._consecutive_failures = 0
        self._last_rollback_time: Optional[datetime] = None
        
        # 설정 스냅샷 (롤백용)
        self._config_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        
        logger.info("[AutoRollbackGuard] Initialized")
    
    @property
    def state(self) -> GuardState:
        """현재 상태"""
        return self._state
    
    def start(self) -> bool:
        """가드 시작"""
        with self._lock:
            if self._running:
                return False
            
            self._running = True
            self._state = GuardState.MONITORING
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            logger.info("[AutoRollbackGuard] Started monitoring")
            return True
    
    def stop(self) -> bool:
        """가드 중지"""
        with self._lock:
            self._running = False
            self._state = GuardState.INACTIVE
        
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        
        logger.info("[AutoRollbackGuard] Stopped")
        return True
    
    def _monitor_loop(self):
        """모니터링 루프"""
        while self._running:
            if self.enabled:
                try:
                    self._perform_health_check()
                except Exception as e:
                    logger.error(f"[AutoRollbackGuard] Health check error: {e}")
                    self._consecutive_failures += 1
            
            time.sleep(self.check_interval_seconds)
    
    def _perform_health_check(self):
        """헬스체크 수행"""
        try:
            error_rate = self.metrics_provider.get_error_rate()
            latency_p99 = self.metrics_provider.get_latency_p99()
            throughput = self.metrics_provider.get_throughput()
        except Exception as e:
            logger.warning(f"[AutoRollbackGuard] Metrics fetch failed: {e}")
            self._consecutive_failures += 1
            self._check_failure_threshold()
            return
        
        # 저하 수준 판단
        degradation = self._assess_degradation(error_rate, latency_p99)
        
        result = HealthCheckResult(
            healthy=degradation == DegradationLevel.NONE,
            degradation_level=degradation,
            error_rate=error_rate,
            latency_p99_ms=latency_p99,
            throughput_rps=throughput,
        )
        
        # 이력 저장
        self._health_history.append(result)
        if len(self._health_history) > 100:
            self._health_history = self._health_history[-100:]
        
        # 상태 업데이트 및 조치
        self._handle_health_result(result)
    
    def _assess_degradation(
        self,
        error_rate: float,
        latency_p99: float
    ) -> DegradationLevel:
        """저하 수준 평가"""
        # 에러율 기반 판단
        if error_rate >= self.ERROR_RATE_CRITICAL:
            return DegradationLevel.CRITICAL
        if error_rate >= self.ERROR_RATE_MAJOR:
            return DegradationLevel.MAJOR
        
        # 레이턴시 기반 판단
        if latency_p99 >= self.LATENCY_CRITICAL_MS:
            return DegradationLevel.CRITICAL
        if latency_p99 >= self.LATENCY_MAJOR_MS:
            return DegradationLevel.MAJOR
        
        # 경미한 저하 (에러 5% 이상 또는 레이턴시 3초 이상)
        if error_rate >= 0.05 or latency_p99 >= 3000:
            return DegradationLevel.MINOR
        
        return DegradationLevel.NONE
    
    def _handle_health_result(self, result: HealthCheckResult):
        """헬스체크 결과 처리"""
        with self._lock:
            if result.healthy:
                self._consecutive_failures = 0
                if self._state in (GuardState.ALERT, GuardState.RECOVERING):
                    self._state = GuardState.MONITORING
                    logger.info("[AutoRollbackGuard] System recovered")
                return
            
            # 저하 감지됨
            self._consecutive_failures += 1
            
            if result.degradation_level == DegradationLevel.CRITICAL:
                self._handle_critical_degradation(result)
            elif result.degradation_level == DegradationLevel.MAJOR:
                self._handle_major_degradation(result)
            else:
                self._handle_minor_degradation(result)
    
    def _handle_minor_degradation(self, result: HealthCheckResult):
        """경미한 저하 처리 - 알림만"""
        if self._consecutive_failures >= self.CONSECUTIVE_FAILURES_ALERT:
            self._state = GuardState.ALERT
            self._send_alert(
                "minor_degradation",
                f"시스템 경미한 저하 감지 (에러율: {result.error_rate:.1%}, "
                f"레이턴시: {result.latency_p99_ms:.0f}ms)"
            )
    
    def _handle_major_degradation(self, result: HealthCheckResult):
        """심각한 저하 처리 - 롤백 고려"""
        self._state = GuardState.ALERT
        self._send_alert(
            "major_degradation",
            f"시스템 심각한 저하 감지 (에러율: {result.error_rate:.1%}, "
            f"레이턴시: {result.latency_p99_ms:.0f}ms). 롤백을 고려하세요."
        )
        
        # 연속 MAJOR 저하 시 자동 롤백
        if self._consecutive_failures >= self.CONSECUTIVE_FAILURES_ALERT:
            self._execute_rollback("major_degradation_consecutive")
    
    def _handle_critical_degradation(self, result: HealthCheckResult):
        """긴급 저하 처리 - 즉시 롤백"""
        self._state = GuardState.EMERGENCY
        self._send_alert(
            "critical_degradation",
            f"🚨 시스템 긴급 저하! (에러율: {result.error_rate:.1%}, "
            f"레이턴시: {result.latency_p99_ms:.0f}ms). 즉시 롤백 수행."
        )
        
        self._execute_emergency_recovery()
    
    def _check_failure_threshold(self):
        """연속 실패 임계값 체크"""
        if self._consecutive_failures >= self.CONSECUTIVE_FAILURES_EMERGENCY:
            self._state = GuardState.EMERGENCY
            self._send_alert(
                "health_check_failed",
                f"헬스체크 {self._consecutive_failures}회 연속 실패. 긴급 복구 수행."
            )
            self._execute_emergency_recovery()
    
    def _execute_rollback(self, reason: str):
        """롤백 실행"""
        # 최근 롤백 후 5분 이내면 스킵 (무한 롤백 방지)
        if self._last_rollback_time:
            elapsed = datetime.now(timezone.utc) - self._last_rollback_time
            if elapsed < timedelta(minutes=5):
                logger.warning("[AutoRollbackGuard] Rollback skipped (cooldown)")
                return
        
        self._state = GuardState.RECOVERING
        logger.warning(f"[AutoRollbackGuard] Executing rollback: {reason}")
        
        # 최근 스냅샷으로 롤백
        for param, snapshots in self._config_snapshots.items():
            if snapshots:
                last_good = snapshots[-1]
                try:
                    self.config_applier.rollback(param, last_good["value"])
                    logger.info(f"[AutoRollbackGuard] Rolled back {param} to {last_good['value']}")
                except Exception as e:
                    logger.error(f"[AutoRollbackGuard] Rollback failed for {param}: {e}")
        
        self._last_rollback_time = datetime.now(timezone.utc)
    
    def _execute_emergency_recovery(self):
        """
        긴급 복구 - 3단계 우선순위 기반 복구
        
        복구 우선순위:
        1. Last Known Good - 직전 스냅샷으로 롤백 (가장 안전)
        2. DNA Declared - DNA에 선언된 값으로 복구
        3. System Defaults - 하드코딩 기본값 (최후 수단)
        
        왜 이 순서인가?
        - 직전 상태: 최소한 그 때까지는 작동했음 → 가장 신뢰할 수 있음
        - DNA 선언값: 관리자가 원하는 Desired 상태
        - 시스템 기본값: 어떤 서비스에도 적용 가능한 보수적 값
        """
        self._state = GuardState.EMERGENCY
        logger.critical("[AutoRollbackGuard] EMERGENCY RECOVERY - Starting tiered recovery")
        
        self._send_alert(
            "emergency_recovery",
            "🆘 긴급 복구 모드 활성화! 단계별 복구를 시작합니다."
        )
        
        # 각 파라미터별로 복구 시도
        recovery_results: Dict[str, str] = {}
        
        for safe_default in self.SYSTEM_DEFAULTS:
            param = safe_default.parameter
            recovery_method = self._recover_parameter(param, safe_default.safe_value)
            recovery_results[param] = recovery_method
        
        # 복구 결과 알림
        self._send_alert(
            "recovery_complete",
            f"복구 완료: {recovery_results}"
        )
        
        self._last_rollback_time = datetime.now(timezone.utc)
        self._state = GuardState.RECOVERING
    
    def _recover_parameter(
        self,
        parameter: str,
        system_default: float
    ) -> str:
        """
        개별 파라미터 복구 (3단계 우선순위)
        
        Returns:
            어떤 방법으로 복구했는지 문자열
        """
        # 1단계: Last Known Good (직전 스냅샷)
        if parameter in self._config_snapshots and self._config_snapshots[parameter]:
            # 스냅샷에서 가장 오래된 것 사용 (마지막 변경 직전 상태)
            # 가장 최근([-1])이 아니라 첫번째([0])를 사용
            # 왜냐하면 스냅샷은 변경 전 값이므로, 첫 스냅샷이 가장 안정적
            snapshots = self._config_snapshots[parameter]
            if len(snapshots) >= 2:
                # 최근 변경 직전 상태 (두번째로 오래된 것)
                last_good_value = snapshots[-2]["value"]
            else:
                # 스냅샷이 하나뿐이면 그것 사용
                last_good_value = snapshots[0]["value"]
            
            try:
                self.config_applier.apply(parameter, last_good_value)
                logger.info(
                    f"[AutoRollbackGuard] Recovered {parameter} to "
                    f"last known good: {last_good_value}"
                )
                return f"last_known_good:{last_good_value}"
            except Exception as e:
                logger.warning(
                    f"[AutoRollbackGuard] Last known good failed for {parameter}: {e}"
                )
        
        # 2단계: DNA Declared (DNA 선언값)
        dna_value = self._get_dna_declared_value(parameter)
        if dna_value is not None:
            try:
                self.config_applier.apply(parameter, dna_value)
                logger.info(
                    f"[AutoRollbackGuard] Recovered {parameter} to "
                    f"DNA declared: {dna_value}"
                )
                return f"dna_declared:{dna_value}"
            except Exception as e:
                logger.warning(
                    f"[AutoRollbackGuard] DNA declared failed for {parameter}: {e}"
                )
        
        # 3단계: System Defaults (최후 수단)
        try:
            self.config_applier.apply(parameter, system_default)
            logger.info(
                f"[AutoRollbackGuard] Recovered {parameter} to "
                f"system default: {system_default}"
            )
            return f"system_default:{system_default}"
        except Exception as e:
            logger.error(
                f"[AutoRollbackGuard] ALL RECOVERY FAILED for {parameter}: {e}"
            )
            return "FAILED"
    
    def _get_dna_declared_value(self, parameter: str) -> Optional[float]:
        """
        DNA에서 선언된 Desired 값을 가져옴
        
        DNA는 관리자가 선언한 "원하는 상태"를 담고 있음.
        selfhealing의 다른 모듈이나 외부 설정에서 가져올 수 있음.
        """
        # TODO: 실제 DNA 시스템과 연동
        # 현재는 config_applier에 get_dna_value 메서드가 있다고 가정
        if hasattr(self.config_applier, 'get_dna_value'):
            try:
                return self.config_applier.get_dna_value(parameter)
            except Exception:
                pass
        
        # DNA 연동이 없으면 None 반환 → System Default로 fallback
        return None
    
    def save_snapshot(self, parameter: str, value: float):
        """설정 스냅샷 저장 (롤백용)"""
        with self._lock:
            if parameter not in self._config_snapshots:
                self._config_snapshots[parameter] = []
            
            self._config_snapshots[parameter].append({
                "value": value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            
            # 최근 10개만 유지
            if len(self._config_snapshots[parameter]) > 10:
                self._config_snapshots[parameter] = self._config_snapshots[parameter][-10:]
    
    def trigger_manual_emergency(self, reason: str = "manual") -> bool:
        """수동 긴급 복구 트리거"""
        logger.warning(f"[AutoRollbackGuard] Manual emergency triggered: {reason}")
        self._execute_emergency_recovery()
        return True
    
    def _send_alert(self, alert_type: str, message: str):
        """알림 발송"""
        if self.alert_callback:
            try:
                self.alert_callback(alert_type, message)
            except Exception as e:
                logger.warning(f"[AutoRollbackGuard] Alert callback failed: {e}")
        
        # 항상 로그에도 기록
        logger.warning(f"[AutoRollbackGuard] {alert_type}: {message}")
    
    def get_status(self) -> Dict[str, Any]:
        """상태 조회"""
        with self._lock:
            return {
                "state": self._state.value,
                "enabled": self.enabled,
                "consecutive_failures": self._consecutive_failures,
                "last_rollback": (
                    self._last_rollback_time.isoformat()
                    if self._last_rollback_time else None
                ),
                "health_history_count": len(self._health_history),
                "recent_health": [
                    {
                        "healthy": h.healthy,
                        "degradation": h.degradation_level.value,
                        "error_rate": h.error_rate,
                        "latency_ms": h.latency_p99_ms,
                        "timestamp": h.timestamp.isoformat(),
                    }
                    for h in self._health_history[-5:]
                ],
            }
    
    def get_safe_defaults(self) -> List[Dict[str, Any]]:
        """안전한 기본값 목록 조회 (시스템 기본값)"""
        return [
            {
                "parameter": sd.parameter,
                "safe_value": sd.safe_value,
                "description": sd.description,
            }
            for sd in self.SYSTEM_DEFAULTS
        ]
    
    def update_safe_default(
        self,
        parameter: str,
        safe_value: float,
        description: Optional[str] = None
    ) -> bool:
        """안전한 기본값 업데이트"""
        for sd in self.SYSTEM_DEFAULTS:
            if sd.parameter == parameter:
                sd.safe_value = safe_value
                if description:
                    sd.description = description
                logger.info(f"[AutoRollbackGuard] Updated safe default: {parameter}={safe_value}")
                return True
        
        # 새로운 파라미터 추가
        self.SYSTEM_DEFAULTS.append(SafeDefault(
            parameter=parameter,
            safe_value=safe_value,
            description=description or f"Safe default for {parameter}",
        ))
        return True


__all__ = [
    "AutoRollbackGuard",
    "GuardState",
    "DegradationLevel",
    "HealthCheckResult",
    "SafeDefault",
]
