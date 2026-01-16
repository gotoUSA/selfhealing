"""
Governance Service Layer.

거버넌스 비즈니스 로직을 담당하는 서비스 클래스입니다.
메트릭 상태 조회, 정합성 조정, 모드 전환 기능을 제공합니다.

Break Glass Pattern:
- STRICT 전환: EmergencyModeTracker에 기록 (자동 만료 추적)
- NORMAL 복귀: EmergencyModeTracker에서 해제

Reference:
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md (Section 1.5)
- docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.metrics.reliability_manager import MetricReliabilityManager

logger = logging.getLogger(__name__)


class GovernanceService:
    """
    거버넌스 서비스.
    
    메트릭 상태 조회, 정합성 조정, 모드 전환 기능을 제공합니다.
    """
    
    def __init__(self):
        self._startup_time: Optional[float] = None
        self._next_scheduled_sync: Optional[float] = None
    
    def set_startup_info(
        self,
        startup_time: float,
        next_scheduled_sync: Optional[float] = None,
    ) -> None:
        """서버 시작 정보 설정 (Startup Hydration에서 호출)."""
        self._startup_time = startup_time
        self._next_scheduled_sync = next_scheduled_sync
    
    def get_status(self) -> Dict[str, Any]:
        """
        통합 메트릭 상태 조회.
        
        Returns:
            {
                "generated_at": "...",
                "operating_mode": "NORMAL",
                "overall_health": "healthy",
                "sync_status": {...},
                "snapshot_health": {...},
                "drift_summary": {...},
                "domains": {...},
                "next_sync_expected_at": "..."  # 피드백 반영
            }
        """
        now = datetime.now(timezone.utc)
        
        # ReliabilityManager에서 상태 수집
        reliability_states = self._get_reliability_states()
        
        # 각 도메인 상태 집계
        domains_status = self._build_domains_status(reliability_states)
        
        # 전반적 상태 판단
        operating_mode = self._get_global_operating_mode(reliability_states)
        overall_health = self._classify_overall_health(reliability_states)
        
        # 동기화 상태
        sync_status = self._get_sync_status(reliability_states)
        
        # 스냅샷 건강도
        snapshot_health = self._get_snapshot_health()
        
        # Drift 요약
        drift_summary = self._get_drift_summary()
        
        # 다음 예상 동기화 시간 (피드백 반영)
        next_sync = self._get_next_sync_expected_at()
        
        return {
            "generated_at": now.isoformat(),
            "operating_mode": operating_mode.value if hasattr(operating_mode, 'value') else str(operating_mode),
            "overall_health": overall_health,
            "sync_status": sync_status,
            "snapshot_health": snapshot_health,
            "drift_summary": drift_summary,
            "domains": domains_status,
            "next_sync_expected_at": next_sync,
        }
    
    def reconcile(
        self,
        domains: Optional[List[str]] = None,
        dry_run: bool = False,
        actor: str = "unknown",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        정합성 조정 수행 (sync → reconcile 용어 변경).
        
        기존 MetricSyncService.sync_metrics() 래핑.
        """
        from selfhealing.api.django.views.metric_sync import get_metric_sync_service
        
        service = get_metric_sync_service()
        result = service.sync_metrics(
            domains=domains,
            dry_run=dry_run,
            actor=actor,
            reason=reason or "Manual reconciliation via Governance API",
        )
        
        # 상태 변경을 status에서 reconciliation_result로 표현
        return {
            "reconciliation_result": result.get("status", "unknown"),
            "reconciled_at": result.get("synced_at"),
            "actor": result.get("actor"),
            "dry_run": result.get("dry_run", False),
            "results": result.get("results", {}),
            "summary": result.get("summary", {}),
        }
    
    def set_mode(
        self,
        mode: str,
        actor: str = "unknown",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        운영 모드 강제 전환.
        
        Break Glass Pattern 연동:
        - STRICT 전환: EmergencyModeTracker에 기록 (자동 만료 추적 시작)
        - NORMAL 복귀: EmergencyModeTracker에서 해제
        
        Args:
            mode: "NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"
            actor: 수행자
            reason: 사유 (STRICT 전환 시 필수)
            
        Returns:
            전환 결과 (expires_at 포함)
        """
        from selfhealing.metrics.reliability_manager import (
            get_reliability_manager,
            OperatingMode,
        )
        
        valid_modes = ["NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"]
        mode_upper = mode.upper()
        
        if mode_upper not in valid_modes:
            raise ValueError(f"Invalid mode: {mode}. Valid: {valid_modes}")
        
        target_mode = OperatingMode[mode_upper]
        manager = get_reliability_manager()
        
        # 이전 모드 기록
        old_mode = manager.get_global_mode()
        
        # 모드 강제 설정
        manager.force_global_mode(target_mode, reason=reason or f"Forced by {actor}")
        
        # EmergencyModeTracker 연동 (자동 만료 기능)
        tracker_result = self._sync_emergency_tracker(mode_upper, actor, reason, old_mode)
        
        # Audit 로깅
        self._log_mode_change(actor, old_mode, target_mode, reason)
        
        result = {
            "status": "mode_changed",
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "previous_mode": old_mode.value if hasattr(old_mode, 'value') else str(old_mode),
            "current_mode": target_mode.value,
            "reason": reason,
            "warning": self._get_mode_warning(target_mode),
        }
        
        # STRICT 모드인 경우 만료 정보 추가
        if tracker_result and tracker_result.get("expires_at"):
            result["expires_at"] = tracker_result["expires_at"]
            result["expiry_hours"] = tracker_result.get("expiry_hours", 8)
        
        return result
    
    def _sync_emergency_tracker(
        self,
        mode: str,
        actor: str,
        reason: Optional[str],
        old_mode,
    ) -> Optional[Dict[str, Any]]:
        """
        EmergencyModeTracker와 동기화 (자동 만료 기능).
        
        - STRICT 전환: 긴급 모드 활성화 기록
        - NORMAL 복귀: 긴급 모드 해제 기록
        
        Args:
            mode: 대상 모드
            actor: 수행자
            reason: 사유
            old_mode: 이전 모드
            
        Returns:
            Tracker 결과 또는 None
        """
        try:
            from selfhealing.services.governance import get_emergency_tracker
            tracker = get_emergency_tracker()
            
            old_mode_str = old_mode.value if hasattr(old_mode, 'value') else str(old_mode)
            
            if mode == "STRICT":
                # 긴급 모드 활성화 (자동 만료 추적 시작)
                result = tracker.record_emergency_activation(
                    activated_by=actor,
                    reason=reason or "Mode forced to STRICT",
                    mode="STRICT",
                )
                logger.info(
                    f"[Governance] Emergency tracker activated: "
                    f"actor={actor}, expires_at={result.get('expiry_hours', 8)}h"
                )
                
                # 만료 시각 계산
                expiry_hours = result.get("expiry_hours", 8)
                expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
                result["expires_at"] = expires_at.isoformat()
                
                return result
                
            elif mode == "NORMAL" and old_mode_str.upper() in ("STRICT", "EMERGENCY"):
                # 긴급 모드에서 NORMAL로 복귀 시 해제
                result = tracker.record_normal_restoration(
                    restored_by=actor,
                    reason=reason or "Mode restored to NORMAL",
                )
                logger.info(
                    f"[Governance] Emergency tracker deactivated: actor={actor}"
                )
                return result
                
        except ImportError:
            logger.debug("[Governance] EmergencyModeTracker not available")
        except Exception as e:
            logger.warning(f"[Governance] Failed to sync emergency tracker: {e}")
        
        return None
    
    def _get_reliability_states(self) -> Dict[str, Any]:
        """ReliabilityManager에서 모든 도메인 상태 조회."""
        try:
            from selfhealing.metrics.reliability_manager import get_reliability_manager
            manager = get_reliability_manager()
            return manager.get_all_states()
        except ImportError:
            logger.debug("[Governance] ReliabilityManager not available")
            return {}
        except Exception as e:
            logger.warning(f"[Governance] Failed to get reliability states: {e}")
            return {}
    
    def _build_domains_status(
        self, reliability_states: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """도메인별 상태 빌드."""
        domains = {}
        
        for domain, state in reliability_states.items():
            if hasattr(state, '__dict__'):
                # MetricReliabilityState 객체인 경우
                domains[domain] = {
                    "reliability_level": getattr(state, 'reliability_level', 'unknown'),
                    "operating_mode": getattr(state, 'operating_mode', 'unknown'),
                    "last_sync_time": getattr(state, 'last_sync_time', None),
                    "last_sync_source": getattr(state, 'last_sync_source', 'none'),
                    "consecutive_syncs": getattr(state, 'consecutive_successful_syncs', 0),
                    "is_data_fresh": getattr(state, 'is_data_fresh', False),
                    "stabilization_progress": getattr(state, 'stabilization_progress', 0.0),
                    "dlq_pending": {
                        "value": getattr(state, 'current_value', 0),
                        "is_synced": getattr(state, 'is_data_fresh', False),
                    },
                }
                # Enum 값 문자열 변환
                for key in ['reliability_level', 'operating_mode']:
                    if hasattr(domains[domain][key], 'value'):
                        domains[domain][key] = domains[domain][key].value
            else:
                # dict인 경우
                domains[domain] = state
        
        return domains
    
    def _get_global_operating_mode(self, reliability_states: Dict[str, Any]) -> str:
        """전역 운영 모드 결정."""
        try:
            from selfhealing.metrics.reliability_manager import get_reliability_manager
            manager = get_reliability_manager()
            mode = manager.get_global_mode()
            return mode.value if hasattr(mode, 'value') else str(mode)
        except Exception:
            # 가장 보수적인 모드로 판단
            modes = []
            for state in reliability_states.values():
                if hasattr(state, 'operating_mode'):
                    modes.append(state.operating_mode)
            
            if not modes:
                return "NORMAL"
            
            # 가장 엄격한 모드 반환
            mode_priority = {"EMERGENCY": 0, "STRICT": 1, "CAUTIOUS": 2, "NORMAL": 3}
            sorted_modes = sorted(
                modes,
                key=lambda m: mode_priority.get(
                    m.value if hasattr(m, 'value') else str(m), 3
                )
            )
            return sorted_modes[0].value if hasattr(sorted_modes[0], 'value') else str(sorted_modes[0])
    
    def _classify_overall_health(self, reliability_states: Dict[str, Any]) -> str:
        """전반적 건강 상태 분류."""
        if not reliability_states:
            return "unknown"
        
        unhealthy_count = 0
        stale_count = 0
        
        for state in reliability_states.values():
            if hasattr(state, 'reliability_level'):
                level = state.reliability_level
                level_str = level.value if hasattr(level, 'value') else str(level)
                if level_str == "unknown":
                    unhealthy_count += 1
                elif level_str == "low":
                    stale_count += 1
        
        total = len(reliability_states)
        
        if unhealthy_count > total / 2:
            return "critical"
        elif unhealthy_count > 0 or stale_count > total / 2:
            return "warning"
        elif stale_count > 0:
            return "degraded"
        return "healthy"
    
    def _get_sync_status(self, reliability_states: Dict[str, Any]) -> Dict[str, Any]:
        """동기화 상태 요약."""
        last_sync_time = None
        last_sync_actor = "unknown"
        total_consecutive = 0
        is_stale = True
        
        for state in reliability_states.values():
            if hasattr(state, 'last_sync_time') and state.last_sync_time:
                if last_sync_time is None or state.last_sync_time > last_sync_time:
                    last_sync_time = state.last_sync_time
                    last_sync_actor = getattr(state, 'last_sync_source', 'unknown')
            
            if hasattr(state, 'is_data_fresh') and state.is_data_fresh:
                is_stale = False
            
            if hasattr(state, 'consecutive_successful_syncs'):
                total_consecutive = max(total_consecutive, state.consecutive_successful_syncs)
        
        return {
            "last_sync_at": datetime.fromtimestamp(
                last_sync_time, tz=timezone.utc
            ).isoformat() if last_sync_time else None,
            "last_sync_actor": last_sync_actor,
            "is_stale": is_stale,
            "consecutive_syncs": total_consecutive,
        }
    
    def _get_snapshot_health(self) -> Dict[str, Any]:
        """스냅샷 건강도 조회."""
        try:
            from selfhealing.metrics.snapshot_storage import get_snapshot_storage
            storage = get_snapshot_storage()
            
            # 스냅샷 나이 확인
            age = storage.get_oldest_snapshot_age()
            is_valid = age is not None and age < 3600  # 1시간 이내
            
            return {
                "age_seconds": age,
                "is_valid": is_valid,
                "path": storage.base_path,
            }
        except ImportError:
            return {"age_seconds": None, "is_valid": False, "path": None}
        except Exception as e:
            logger.warning(f"[Governance] Snapshot health check failed: {e}")
            return {"age_seconds": None, "is_valid": False, "path": None, "error": str(e)}
    
    def _get_drift_summary(self) -> Dict[str, Any]:
        """Drift 요약 조회."""
        try:
            from selfhealing.api.django.views.metric_sync import get_metric_sync_service
            
            service = get_metric_sync_service()
            report = service.get_drift_report()
            
            metrics = report.get("metrics", {})
            total_drifts = 0
            critical_drifts = 0
            domains_with_drift = []
            
            for metric_type, domains in metrics.items():
                for domain, info in domains.items():
                    drift = abs(info.get("drift", 0))
                    if drift > 0:
                        total_drifts += 1
                        domains_with_drift.append(domain)
                        if info.get("is_critical", False):
                            critical_drifts += 1
            
            return {
                "total_drifts": total_drifts,
                "critical_drifts": critical_drifts,
                "domains_with_drift": list(set(domains_with_drift)),
            }
        except Exception as e:
            logger.warning(f"[Governance] Drift summary failed: {e}")
            return {"total_drifts": 0, "critical_drifts": 0, "domains_with_drift": []}
    
    def _get_next_sync_expected_at(self) -> Optional[str]:
        """
        다음 예상 동기화 시간 (피드백 반영).
        
        Startup Hydration의 Jitter가 적용된 경우 그 시간을 반환.
        """
        if self._next_scheduled_sync:
            return datetime.fromtimestamp(
                self._next_scheduled_sync, tz=timezone.utc
            ).isoformat()
        return None
    
    def _log_mode_change(
        self,
        actor: str,
        old_mode: Any,
        new_mode: Any,
        reason: Optional[str],
    ) -> None:
        """모드 변경 Audit 로깅."""
        try:
            from selfhealing.audit.logger import (
                AuditLogger, ConfigChangeEvent, AuditAction
            )
            
            audit_logger = AuditLogger.get_instance()
            event = ConfigChangeEvent(
                config_type="governance",
                config_key="operating_mode",
                action=AuditAction.OVERRIDE,
                old_value=old_mode.value if hasattr(old_mode, 'value') else str(old_mode),
                new_value=new_mode.value if hasattr(new_mode, 'value') else str(new_mode),
                reason=reason or "Manual mode change",
                user=actor,
                source="api",
                metadata={"category": "governance_control"},
            )
            audit_logger.log(event)
        except Exception as e:
            logger.warning(f"[Governance] Audit logging failed: {e}")
    
    def _get_mode_warning(self, mode: Any) -> Optional[str]:
        """모드별 경고 메시지."""
        mode_str = mode.value if hasattr(mode, 'value') else str(mode)
        mode_upper = mode_str.upper()
        
        warnings = {
            "STRICT": "STRICT 모드에서는 모든 보호 기능이 활성화됩니다. 성능 저하 가능.",
            "EMERGENCY": "EMERGENCY 모드입니다. 최소 기능만 작동합니다. 즉시 조치 필요.",
            "CAUTIOUS": "CAUTIOUS 모드입니다. 점진적으로 정상 복귀 중입니다.",
        }
        return warnings.get(mode_upper)


# =============================================================================
# Singleton Service Instance
# =============================================================================


_governance_service: Optional[GovernanceService] = None


def get_governance_service() -> GovernanceService:
    """GovernanceService 싱글톤 인스턴스 반환."""
    global _governance_service
    
    if _governance_service is None:
        _governance_service = GovernanceService()
    
    return _governance_service


def reset_governance_service() -> None:
    """GovernanceService 인스턴스 리셋 (테스트용)."""
    global _governance_service
    _governance_service = None


__all__ = [
    "GovernanceService",
    "get_governance_service",
    "reset_governance_service",
]
