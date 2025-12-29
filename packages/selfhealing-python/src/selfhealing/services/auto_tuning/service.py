"""
Auto Tuning Service - 자율 조정 서비스

RuntimeFeedbackLoop, DecisionEngine, SafetyBounds를 조합하여
완전한 자율 조정 서비스를 제공합니다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from .adjustment_recorder import AdjustmentRecorder
from .models import TuningState

logger = logging.getLogger(__name__)


class AutoTuningService:
    """
    자율 조정 서비스
    
    RuntimeFeedbackLoop + DecisionEngine + SafetyBounds + AutoRollbackGuard를
    통합하여 완전한 자율 조정 기능을 제공합니다.
    
    사용 예:
        service = AutoTuningService(
            metrics_adapter=prometheus_adapter,
            config_provider=settings_provider,
            audit_adapter=file_audit,
        )
        service.start()
    """
    
    def __init__(
        self,
        metrics_adapter,
        config_provider,
        config_applier,
        audit_adapter,
        alert_manager=None,
        enabled: bool = True,
        auto_rollback_enabled: bool = True,
    ):
        self._lock = RLock()
        self._enabled = enabled
        
        # 컴포넌트 초기화
        from selfhealing.core.safety_bounds import SafetyBounds
        from selfhealing.core.decision_engine import DecisionEngine
        from selfhealing.core.runtime_feedback import RuntimeFeedbackLoop
        from selfhealing.core.auto_rollback_guard import AutoRollbackGuard
        
        self.safety_bounds = SafetyBounds()
        self.decision_engine = DecisionEngine(config_provider)
        self.adjustment_recorder = AdjustmentRecorder()
        
        # Alert Manager 생성 (없으면 기본)
        if alert_manager is None:
            alert_manager = self._create_default_alert_manager()
        
        self.feedback_loop = RuntimeFeedbackLoop(
            metrics_adapter=metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=audit_adapter,
            alert_manager=alert_manager,
            config_applier=config_applier,
            enabled=enabled,
            auto_rollback_enabled=auto_rollback_enabled,
        )
        
        # AutoRollbackGuard (독립 안전장치)
        self.rollback_guard = AutoRollbackGuard(
            metrics_provider=self._create_metrics_provider(metrics_adapter),
            config_applier=config_applier,
            alert_callback=self._handle_guard_alert,
            enabled=enabled,
        )
        
        logger.info("[AutoTuningService] Initialized")
    
    def start(self) -> bool:
        """서비스 시작"""
        with self._lock:
            # 세션 시작
            self.adjustment_recorder.start_session("AutoTuningService started")
            
            # 피드백 루프 시작
            self.feedback_loop.start()
            
            # 롤백 가드 시작
            self.rollback_guard.start()
            
            logger.info("[AutoTuningService] Started")
            return True
    
    def stop(self) -> bool:
        """서비스 중지"""
        with self._lock:
            self.feedback_loop.stop()
            self.rollback_guard.stop()
            self.adjustment_recorder.end_session(TuningState.COMPLETED)
            
            logger.info("[AutoTuningService] Stopped")
            return True
    
    def pause(self, reason: str = "manual") -> bool:
        """서비스 일시 정지"""
        with self._lock:
            self.feedback_loop.pause(reason)
            return True
    
    def resume(self) -> bool:
        """서비스 재개"""
        with self._lock:
            return self.feedback_loop.resume()
    
    def trigger_emergency_recovery(self, reason: str = "manual") -> bool:
        """긴급 복구 트리거"""
        logger.warning(f"[AutoTuningService] Emergency recovery triggered: {reason}")
        return self.rollback_guard.trigger_manual_emergency(reason)
    
    def get_status(self) -> Dict[str, Any]:
        """전체 상태 조회"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "feedback_loop": self.feedback_loop.get_status(),
                "rollback_guard": self.rollback_guard.get_status(),
                "statistics": self.adjustment_recorder.get_statistics(),
                "safety_bounds": self.safety_bounds.get_all_bounds(),
            }
    
    def get_adjustment_history(
        self,
        parameter: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """조정 이력 조회"""
        records = self.adjustment_recorder.get_records(parameter, limit)
        return [r.to_dict() for r in records]
    
    def update_safety_bounds(
        self,
        parameter: str,
        config: Dict[str, float]
    ) -> bool:
        """안전 한계 업데이트"""
        return self.safety_bounds.update_bounds(parameter, config)
    
    def _create_default_alert_manager(self):
        """기본 Alert Manager 생성"""
        class DefaultAlertManager:
            def send_auto_tuning_alert(self, **kwargs):
                logger.info(f"[AutoTuning Alert] {kwargs}")
            
            def _send_notification(self, title, message, severity):
                logger.info(f"[Notification] {title}: {message}")
        
        return DefaultAlertManager()
    
    def _create_metrics_provider(self, metrics_adapter):
        """MetricsProvider 래퍼 생성"""
        class MetricsProviderWrapper:
            def __init__(self, adapter):
                self.adapter = adapter
            
            def get_error_rate(self) -> float:
                metrics = self.adapter.fetch_current_metrics()
                return metrics.get("error_rate", 0.0)
            
            def get_latency_p99(self) -> float:
                metrics = self.adapter.fetch_current_metrics()
                return metrics.get("p99_latency_ms", 0.0)
            
            def get_throughput(self) -> float:
                metrics = self.adapter.fetch_current_metrics()
                return metrics.get("throughput_rps", 0.0)
        
        return MetricsProviderWrapper(metrics_adapter)
    
    def _handle_guard_alert(self, alert_type: str, message: str):
        """Guard 알림 처리"""
        logger.warning(f"[AutoTuningService] Guard alert: {alert_type} - {message}")


__all__ = ["AutoTuningService"]
