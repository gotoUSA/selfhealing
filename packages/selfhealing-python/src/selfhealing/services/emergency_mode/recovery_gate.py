"""
Recovery Gate - Safe Emergency Mode Deactivation.

Manages metrics-based recovery checks and gradual recovery steps.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional, Callable, Tuple

from .enums import EmergencyLevel
from .models import RecoveryGateConfig

logger = logging.getLogger(__name__)


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
    
    def check_recovery_allowed(self) -> Tuple[bool, str]:
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
