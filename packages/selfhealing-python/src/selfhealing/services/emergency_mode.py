"""
Emergency Mode Service - Graceful Degradation Manager.

비상 모드의 단계별 제어와 안전한 복구를 담당합니다.

Features:
- EmergencyLevel: 단계별 비상 모드 (NORMAL, LEVEL_1, LEVEL_2, LEVEL_3)
- GracefulDegradationManager: 비상 모드 진입/해제 관리
- RecoveryGate: 복구 안정화 및 점진적 복구

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md (Section 4)

Usage:
    from selfhealing.services.emergency_mode import (
        get_emergency_manager,
        EmergencyLevel,
        is_emergency_active,
    )
    
    # Check emergency status
    if is_emergency_active():
        level = get_emergency_manager().get_current_level()
        ...
    
    # Activate emergency mode
    get_emergency_manager().activate_manual(
        level=EmergencyLevel.LEVEL_2,
        reason="High error rate detected",
        activated_by="admin",
        duration_minutes=30,
    )
    
    # Deactivate emergency mode
    get_emergency_manager().deactivate(deactivated_by="admin")
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, Any, Optional, List, Callable

logger = logging.getLogger(__name__)


# =============================================================================
# Emergency Level Definition
# =============================================================================


class EmergencyLevel(Enum):
    """
    비상 모드 레벨 정의.
    
    각 레벨은 티어별 트래픽 허용 배율을 결정합니다.
    """
    NORMAL = 0       # 정상 운영 (모든 트래픽 허용)
    LEVEL_1 = 1      # 경미한 장애 - Tier 3 (Non-Essential)만 차단
    LEVEL_2 = 2      # 중간 장애 - Tier 2, 3 차단, Tier 1은 100%
    LEVEL_3 = 3      # 심각한 장애 - Tier 1만 50% 허용


# 각 레벨별 티어 트래픽 배율 규칙
EMERGENCY_LEVEL_RULES: Dict[EmergencyLevel, Dict[str, float]] = {
    EmergencyLevel.NORMAL: {
        "critical": 1.0,
        "standard": 1.0,
        "non_essential": 1.0,
    },
    EmergencyLevel.LEVEL_1: {
        "critical": 1.0,
        "standard": 1.0,
        "non_essential": 0.0,  # Non-Essential 차단
    },
    EmergencyLevel.LEVEL_2: {
        "critical": 1.0,
        "standard": 0.1,       # Standard 10%만 허용
        "non_essential": 0.0,  # Non-Essential 차단
    },
    EmergencyLevel.LEVEL_3: {
        "critical": 0.5,       # Critical도 50%만 허용
        "standard": 0.0,       # Standard 차단
        "non_essential": 0.0,  # Non-Essential 차단
    },
}


# =============================================================================
# Recovery Gate Configuration
# =============================================================================


@dataclass
class RecoveryGateConfig:
    """
    복구 게이트 설정.
    
    안전한 비상 모드 해제를 위한 안정화 기간 및 조건을 정의합니다.
    """
    # 안정화 대기 기간 (초)
    stabilization_period_seconds: int = 300  # 5분
    
    # 메트릭 기반 안정성 확인 필요 여부
    require_metrics_stable: bool = True
    
    # CPU 사용률 임계값 (이 이하여야 복구 가능)
    cpu_threshold_percent: float = 80.0
    
    # 오류율 임계값 (이 이하여야 복구 가능)
    error_rate_threshold: float = 0.05  # 5%
    
    # 점진적 복구 사용 여부
    gradual_recovery: bool = True
    
    # 레벨 단계별 복구 지연 시간 (초)
    level_step_delay_seconds: int = 60  # 1분
    
    # 복구 중 메트릭 재확인 주기 (초)
    health_check_interval_seconds: int = 30
    
    # 복구 실패 시 자동 롤백 여부
    auto_rollback_on_failure: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryGateConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Emergency State
# =============================================================================


@dataclass
class EmergencyState:
    """비상 모드 상태."""
    level: EmergencyLevel = EmergencyLevel.NORMAL
    is_active: bool = False
    
    # 활성화 정보
    activated_at: Optional[str] = None
    activated_by: Optional[str] = None
    activation_reason: Optional[str] = None
    
    # 자동 만료 시간
    expires_at: Optional[str] = None
    
    # 비활성화 정보
    deactivated_at: Optional[str] = None
    deactivated_by: Optional[str] = None
    
    # 자동 활성화 여부 (시스템에 의한 자동 감지)
    is_auto_triggered: bool = False
    
    # 복구 중 상태
    is_recovering: bool = False
    recovery_started_at: Optional[str] = None
    target_level: Optional[EmergencyLevel] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["level"] = self.level.value
        if self.target_level:
            result["target_level"] = self.target_level.value
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmergencyState":
        data = dict(data)
        if "level" in data:
            data["level"] = EmergencyLevel(data["level"])
        if "target_level" in data and data["target_level"] is not None:
            data["target_level"] = EmergencyLevel(data["target_level"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Recovery Gate
# =============================================================================


class RecoveryGate:
    """
    복구 게이트 - 안전한 비상 모드 해제를 관리.
    
    Features:
    - 메트릭 기반 안정성 확인
    - 점진적 복구 (레벨별 단계적 완화)
    - 복구 실패 시 자동 롤백
    """
    
    def __init__(
        self,
        config: Optional[RecoveryGateConfig] = None,
        metrics_checker: Optional[Callable[[], Dict[str, float]]] = None,
    ):
        """
        Args:
            config: 복구 게이트 설정
            metrics_checker: 현재 시스템 메트릭을 반환하는 콜백 함수
                           반환값: {"cpu_percent": 75.0, "error_rate": 0.02}
        """
        self.config = config or RecoveryGateConfig()
        self._metrics_checker = metrics_checker or self._default_metrics_checker
        self._lock = threading.Lock()
    
    def _default_metrics_checker(self) -> Dict[str, float]:
        """기본 메트릭 체커 (실제 구현에서는 Prometheus 등에서 가져옴)."""
        # Placeholder - 실제 구현 시 Prometheus/시스템 메트릭 사용
        return {
            "cpu_percent": 50.0,
            "error_rate": 0.01,
        }
    
    def check_recovery_allowed(self) -> tuple[bool, str]:
        """
        복구 가능 여부 확인.
        
        Returns:
            (is_allowed, reason): 복구 가능 여부와 사유
        """
        if not self.config.require_metrics_stable:
            return (True, "Metrics check disabled")
        
        try:
            metrics = self._metrics_checker()
            
            cpu = metrics.get("cpu_percent", 0.0)
            error_rate = metrics.get("error_rate", 0.0)
            
            if cpu > self.config.cpu_threshold_percent:
                return (
                    False,
                    f"CPU usage too high: {cpu:.1f}% > {self.config.cpu_threshold_percent}%",
                )
            
            if error_rate > self.config.error_rate_threshold:
                return (
                    False,
                    f"Error rate too high: {error_rate:.3f} > {self.config.error_rate_threshold}",
                )
            
            return (True, "All metrics within thresholds")
            
        except Exception as e:
            logger.warning(f"[RecoveryGate] Metrics check failed: {e}")
            # 메트릭 확인 실패 시 보수적으로 허용하지 않음
            return (False, f"Metrics check failed: {e}")
    
    def get_next_recovery_level(
        self,
        current_level: EmergencyLevel,
    ) -> Optional[EmergencyLevel]:
        """
        점진적 복구에서 다음 레벨 반환.
        
        LEVEL_3 → LEVEL_2 → LEVEL_1 → NORMAL
        
        Returns:
            다음 레벨 또는 None (이미 NORMAL인 경우)
        """
        if current_level == EmergencyLevel.NORMAL:
            return None
        elif current_level == EmergencyLevel.LEVEL_3:
            return EmergencyLevel.LEVEL_2
        elif current_level == EmergencyLevel.LEVEL_2:
            return EmergencyLevel.LEVEL_1
        elif current_level == EmergencyLevel.LEVEL_1:
            return EmergencyLevel.NORMAL
        return None


# =============================================================================
# Graceful Degradation Manager
# =============================================================================


class GracefulDegradationManager:
    """
    비상 모드 관리자 - 단계별 비상 모드 진입/해제.
    
    Thread-safe 싱글톤으로 구현.
    
    Usage:
        manager = GracefulDegradationManager()
        
        # 수동 비상 모드 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="High error rate",
            activated_by="admin",
            duration_minutes=30,
        )
        
        # 현재 상태 확인
        state = manager.get_state()
        
        # 비상 모드 해제
        manager.deactivate(deactivated_by="admin")
    """
    
    _instance: Optional["GracefulDegradationManager"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "GracefulDegradationManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance
    
    def _init(self):
        """초기화."""
        self._state_lock = threading.RLock()
        self._state = EmergencyState()
        self._recovery_gate = RecoveryGate()
        self._recovery_thread: Optional[threading.Thread] = None
        self._stop_recovery = threading.Event()
        self._history: List[Dict[str, Any]] = []
        
        # 백엔드에서 상태 로드 시도
        self._load_state()
    
    def _load_state(self):
        """백엔드에서 상태 로드."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            data = backend.get("emergency_mode")
            if data:
                self._state = EmergencyState.from_dict(data)
                logger.info(
                    f"[EmergencyMode] Loaded state: level={self._state.level.name}, "
                    f"is_active={self._state.is_active}"
                )
        except Exception as e:
            logger.warning(f"[EmergencyMode] Could not load state: {e}")
    
    def _save_state(self):
        """상태 저장."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            backend.set("emergency_mode", self._state.to_dict())
        except Exception as e:
            logger.error(f"[EmergencyMode] Failed to save state: {e}")
    
    # -------------------------------------------------------------------------
    # State Access
    # -------------------------------------------------------------------------
    
    def get_state(self) -> EmergencyState:
        """현재 상태 조회."""
        with self._state_lock:
            # 만료 확인
            self._check_expiration()
            return EmergencyState.from_dict(self._state.to_dict())
    
    def get_current_level(self) -> EmergencyLevel:
        """현재 비상 모드 레벨 조회."""
        with self._state_lock:
            self._check_expiration()
            return self._state.level
    
    def is_active(self) -> bool:
        """비상 모드 활성화 여부."""
        with self._state_lock:
            self._check_expiration()
            return self._state.is_active
    
    def _check_expiration(self):
        """만료 확인 및 자동 해제."""
        if not self._state.is_active or not self._state.expires_at:
            return
        
        try:
            expires_at = datetime.fromisoformat(self._state.expires_at)
            if datetime.now(timezone.utc) > expires_at:
                logger.info("[EmergencyMode] Auto-expired, deactivating")
                self._do_deactivate("system", "Auto-expired")
        except Exception as e:
            logger.error(f"[EmergencyMode] Expiration check failed: {e}")
    
    # -------------------------------------------------------------------------
    # Tier Multiplier
    # -------------------------------------------------------------------------
    
    def get_tier_multiplier(self, tier_id: str) -> float:
        """
        현재 비상 모드 레벨에 따른 티어 배율 반환.
        
        Args:
            tier_id: 티어 ID (critical, standard, non_essential)
            
        Returns:
            배율 (0.0 ~ 1.0)
        """
        with self._state_lock:
            self._check_expiration()
            level = self._state.level
            rules = EMERGENCY_LEVEL_RULES.get(level, EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL])
            return rules.get(tier_id, 1.0)
    
    # -------------------------------------------------------------------------
    # Manual Activation/Deactivation
    # -------------------------------------------------------------------------
    
    def activate_manual(
        self,
        level: EmergencyLevel,
        reason: str,
        activated_by: str,
        duration_minutes: Optional[int] = None,
    ) -> EmergencyState:
        """
        수동 비상 모드 활성화.
        
        Args:
            level: 비상 모드 레벨
            reason: 활성화 사유 (필수)
            activated_by: 활성화한 사용자
            duration_minutes: 자동 만료 시간 (분), None이면 수동 해제 필요
            
        Returns:
            새 상태
        """
        if not reason:
            raise ValueError("reason is required")
        
        if level == EmergencyLevel.NORMAL:
            # NORMAL로 설정하는 것은 비활성화와 동일
            return self.deactivate(activated_by)
        
        with self._state_lock:
            now = datetime.now(timezone.utc)
            
            old_state = EmergencyState.from_dict(self._state.to_dict())
            
            self._state.level = level
            self._state.is_active = True
            self._state.activated_at = now.isoformat()
            self._state.activated_by = activated_by
            self._state.activation_reason = reason
            self._state.is_auto_triggered = False
            self._state.deactivated_at = None
            self._state.deactivated_by = None
            
            if duration_minutes:
                self._state.expires_at = (now + timedelta(minutes=duration_minutes)).isoformat()
            else:
                self._state.expires_at = None
            
            self._save_state()
            self._log_history("ACTIVATED", old_state, self._state)
            self._log_audit("activate", activated_by, reason)
            
            logger.warning(
                f"[EmergencyMode] ACTIVATED by {activated_by}: "
                f"level={level.name}, reason={reason}, "
                f"expires_at={self._state.expires_at or 'manual'}"
            )
            
            return self.get_state()
    
    def activate_auto(
        self,
        level: EmergencyLevel,
        reason: str,
        duration_minutes: int = 30,
    ) -> EmergencyState:
        """
        자동 비상 모드 활성화 (시스템에 의해 트리거).
        
        Args:
            level: 비상 모드 레벨
            reason: 자동 감지 사유
            duration_minutes: 자동 만료 시간 (기본 30분)
            
        Returns:
            새 상태
        """
        with self._state_lock:
            now = datetime.now(timezone.utc)
            
            # 이미 더 높은 레벨이면 무시
            if self._state.is_active and self._state.level.value >= level.value:
                logger.info(
                    f"[EmergencyMode] Auto-trigger ignored: "
                    f"current level {self._state.level.name} >= requested {level.name}"
                )
                return self.get_state()
            
            old_state = EmergencyState.from_dict(self._state.to_dict())
            
            self._state.level = level
            self._state.is_active = True
            self._state.activated_at = now.isoformat()
            self._state.activated_by = "system"
            self._state.activation_reason = reason
            self._state.is_auto_triggered = True
            self._state.expires_at = (now + timedelta(minutes=duration_minutes)).isoformat()
            
            self._save_state()
            self._log_history("AUTO_ACTIVATED", old_state, self._state)
            self._log_audit("auto_activate", "system", reason)
            
            logger.warning(
                f"[EmergencyMode] AUTO-ACTIVATED: level={level.name}, "
                f"reason={reason}, expires_in={duration_minutes}min"
            )
            
            return self.get_state()
    
    def deactivate(
        self,
        deactivated_by: str,
        reason: str = "",
        force: bool = False,
    ) -> EmergencyState:
        """
        비상 모드 해제.
        
        Args:
            deactivated_by: 해제한 사용자
            reason: 해제 사유 (선택)
            force: 복구 조건 무시하고 강제 해제
            
        Returns:
            새 상태
        """
        with self._state_lock:
            if not self._state.is_active:
                return self.get_state()
            
            # 복구 조건 확인 (force가 아닌 경우)
            if not force and self._recovery_gate.config.require_metrics_stable:
                allowed, check_reason = self._recovery_gate.check_recovery_allowed()
                if not allowed:
                    logger.warning(
                        f"[EmergencyMode] Deactivation blocked: {check_reason}. "
                        f"Use force=True to override."
                    )
                    raise ValueError(f"Recovery not allowed: {check_reason}")
            
            return self._do_deactivate(deactivated_by, reason or "Manual deactivation")
    
    def _do_deactivate(self, deactivated_by: str, reason: str) -> EmergencyState:
        """실제 비상 모드 해제 수행."""
        now = datetime.now(timezone.utc)
        old_state = EmergencyState.from_dict(self._state.to_dict())
        
        self._state.level = EmergencyLevel.NORMAL
        self._state.is_active = False
        self._state.deactivated_at = now.isoformat()
        self._state.deactivated_by = deactivated_by
        self._state.is_recovering = False
        self._state.recovery_started_at = None
        self._state.target_level = None
        
        self._save_state()
        self._log_history("DEACTIVATED", old_state, self._state)
        self._log_audit("deactivate", deactivated_by, reason)
        
        logger.info(f"[EmergencyMode] DEACTIVATED by {deactivated_by}: {reason}")
        
        return self.get_state()
    
    # -------------------------------------------------------------------------
    # Gradual Recovery
    # -------------------------------------------------------------------------
    
    def start_gradual_recovery(
        self,
        initiated_by: str,
        target_level: EmergencyLevel = EmergencyLevel.NORMAL,
    ) -> EmergencyState:
        """
        점진적 복구 시작.
        
        현재 레벨에서 목표 레벨까지 단계적으로 완화.
        각 단계마다 메트릭 확인 후 진행.
        
        Args:
            initiated_by: 복구 시작한 사용자
            target_level: 목표 레벨 (기본: NORMAL)
            
        Returns:
            현재 상태
        """
        with self._state_lock:
            if not self._state.is_active:
                raise ValueError("Emergency mode is not active")
            
            if self._state.is_recovering:
                raise ValueError("Gradual recovery already in progress")
            
            if self._state.level.value <= target_level.value:
                raise ValueError(
                    f"Target level {target_level.name} must be lower than "
                    f"current level {self._state.level.name}"
                )
            
            self._state.is_recovering = True
            self._state.recovery_started_at = datetime.now(timezone.utc).isoformat()
            self._state.target_level = target_level
            self._save_state()
            
            logger.info(
                f"[EmergencyMode] Gradual recovery started by {initiated_by}: "
                f"{self._state.level.name} → {target_level.name}"
            )
        
        # 복구 스레드 시작
        self._stop_recovery.clear()
        self._recovery_thread = threading.Thread(
            target=self._gradual_recovery_worker,
            daemon=True,
        )
        self._recovery_thread.start()
        
        return self.get_state()
    
    def stop_gradual_recovery(
        self,
        stopped_by: str,
        reason: str = "",
    ) -> EmergencyState:
        """점진적 복구 중지."""
        self._stop_recovery.set()
        
        with self._state_lock:
            if self._state.is_recovering:
                self._state.is_recovering = False
                self._save_state()
                logger.info(
                    f"[EmergencyMode] Gradual recovery stopped by {stopped_by}: "
                    f"{reason or 'Manual stop'}"
                )
        
        return self.get_state()
    
    def _gradual_recovery_worker(self):
        """점진적 복구 워커 스레드."""
        config = self._recovery_gate.config
        
        while not self._stop_recovery.is_set():
            with self._state_lock:
                if not self._state.is_recovering:
                    break
                
                current_level = self._state.level
                target_level = self._state.target_level or EmergencyLevel.NORMAL
                
                if current_level.value <= target_level.value:
                    # 목표 도달
                    self._state.is_recovering = False
                    self._save_state()
                    logger.info(
                        f"[EmergencyMode] Gradual recovery complete: "
                        f"reached {current_level.name}"
                    )
                    break
            
            # 안정화 대기
            logger.info(
                f"[EmergencyMode] Waiting {config.stabilization_period_seconds}s "
                f"for stabilization before next step..."
            )
            if self._stop_recovery.wait(config.stabilization_period_seconds):
                break  # 중지 요청됨
            
            # 메트릭 확인
            allowed, reason = self._recovery_gate.check_recovery_allowed()
            
            if not allowed:
                if config.auto_rollback_on_failure:
                    logger.warning(
                        f"[EmergencyMode] Recovery check failed: {reason}. "
                        "Stopping gradual recovery."
                    )
                    with self._state_lock:
                        self._state.is_recovering = False
                        self._save_state()
                    break
                else:
                    logger.warning(
                        f"[EmergencyMode] Recovery check failed: {reason}. "
                        f"Retrying in {config.health_check_interval_seconds}s..."
                    )
                    if self._stop_recovery.wait(config.health_check_interval_seconds):
                        break
                    continue
            
            # 다음 레벨로 진행
            next_level = self._recovery_gate.get_next_recovery_level(current_level)
            
            if next_level is None:
                with self._state_lock:
                    self._state.is_recovering = False
                    self._save_state()
                break
            
            with self._state_lock:
                old_level = self._state.level
                self._state.level = next_level
                self._save_state()
                
                logger.info(
                    f"[EmergencyMode] Gradual recovery step: "
                    f"{old_level.name} → {next_level.name}"
                )
                
                if next_level == EmergencyLevel.NORMAL:
                    self._state.is_active = False
                    self._state.is_recovering = False
                    self._state.deactivated_at = datetime.now(timezone.utc).isoformat()
                    self._state.deactivated_by = "gradual_recovery"
                    self._save_state()
                    
                    logger.info("[EmergencyMode] Gradual recovery complete: NORMAL")
                    break
            
            # 다음 단계 전 대기
            if self._stop_recovery.wait(config.level_step_delay_seconds):
                break
    
    # -------------------------------------------------------------------------
    # History & Audit
    # -------------------------------------------------------------------------
    
    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """변경 이력 조회."""
        with self._state_lock:
            return list(self._history[-limit:])
    
    def _log_history(
        self,
        action: str,
        old_state: EmergencyState,
        new_state: EmergencyState,
    ):
        """이력 기록."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "old_level": old_state.level.name,
            "new_level": new_state.level.name,
            "old_active": old_state.is_active,
            "new_active": new_state.is_active,
        }
        self._history.append(entry)
        
        # 최근 100개만 유지
        if len(self._history) > 100:
            self._history = self._history[-100:]
    
    def _log_audit(self, action: str, user: str, reason: str):
        """Shadow Audit 기록."""
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type="emergency_mode",
                config_key="state",
                old_value=None,
                new_value={
                    "action": action,
                    "level": self._state.level.name,
                    "is_active": self._state.is_active,
                    "reason": reason,
                    "severity": "warning" if action == "deactivate" else "critical",
                    "tag": f"EMERGENCY_{action.upper()}",
                },
                user=user,
            )
        except Exception as e:
            logger.error(f"[EmergencyMode] Audit log failed: {e}")
    
    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------
    
    def set_recovery_gate_config(
        self,
        config: RecoveryGateConfig,
        changed_by: str = "system",
    ):
        """복구 게이트 설정 변경."""
        with self._state_lock:
            self._recovery_gate.config = config
            logger.info(f"[EmergencyMode] Recovery gate config updated by {changed_by}")
    
    def get_recovery_gate_config(self) -> RecoveryGateConfig:
        """현재 복구 게이트 설정 조회."""
        with self._state_lock:
            return RecoveryGateConfig(**self._recovery_gate.config.to_dict())
    
    # -------------------------------------------------------------------------
    # Reset (Testing)
    # -------------------------------------------------------------------------
    
    def reset(self):
        """상태 초기화 (테스트용)."""
        with self._state_lock:
            self._stop_recovery.set()
            self._state = EmergencyState()
            self._history.clear()
            self._save_state()
            logger.info("[EmergencyMode] State reset to defaults")


# =============================================================================
# Singleton & Factory Functions
# =============================================================================


# Global instance
_emergency_manager: Optional[GracefulDegradationManager] = None


def get_emergency_manager() -> GracefulDegradationManager:
    """비상 모드 관리자 싱글톤 획득."""
    global _emergency_manager
    if _emergency_manager is None:
        _emergency_manager = GracefulDegradationManager()
    return _emergency_manager


def is_emergency_active() -> bool:
    """
    비상 모드 활성화 여부 확인 (간편 함수).
    
    Usage:
        if is_emergency_active():
            # 비상 모드 처리
            ...
    """
    return get_emergency_manager().is_active()


def get_emergency_level() -> EmergencyLevel:
    """
    현재 비상 모드 레벨 조회 (간편 함수).
    
    Usage:
        level = get_emergency_level()
        if level >= EmergencyLevel.LEVEL_2:
            # 심각한 비상 상황 처리
            ...
    """
    return get_emergency_manager().get_current_level()


def get_tier_multiplier(tier_id: str) -> float:
    """
    현재 비상 모드에 따른 티어 배율 조회 (간편 함수).
    
    Usage:
        multiplier = get_tier_multiplier("standard")
        if random.random() > multiplier:
            # Load shedding
            return Response(status=503)
    """
    return get_emergency_manager().get_tier_multiplier(tier_id)
