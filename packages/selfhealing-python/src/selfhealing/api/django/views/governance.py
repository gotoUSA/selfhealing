"""
Governance API Views - 통합 거버넌스 허브.

새로운 API 구조:
- GET /api/self-healing/metrics/status/ - 통합 상태 조회 (Observability)
- POST /api/self-healing/governance/reconcile/ - 수동 정합성 조정 (Control)
- POST /api/self-healing/governance/mode/ - 운영 모드 강제 전환 (Control)
- GET /api/self-healing/governance/status/ - RBAC 상태 조회 (Phase 2)
- GET/PUT /api/self-healing/config/governance/ - 거버넌스 설정 (Phase 2)

기존 API (Deprecated):
- POST /api/self-healing/metrics/sync/ → /governance/reconcile/ (308 Redirect)
- GET /api/self-healing/metrics/drift-report/ → /metrics/status/ (308 Redirect)

Reference: 
- docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md

Design Philosophy:
- 관찰(Observability)과 제어(Control) 분리
- 엔드포인트 파편화 방지
- Deprecated API는 Warning 헤더 + 리다이렉트로 하위 호환성 유지
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer

if TYPE_CHECKING:
    from selfhealing.metrics.reliability_manager import MetricReliabilityManager

logger = logging.getLogger(__name__)


# =============================================================================
# Service Layer
# =============================================================================


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
        
        Args:
            mode: "NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"
            actor: 수행자
            reason: 사유
            
        Returns:
            전환 결과
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
        
        # Audit 로깅
        self._log_mode_change(actor, old_mode, target_mode, reason)
        
        return {
            "status": "mode_changed",
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "previous_mode": old_mode.value if hasattr(old_mode, 'value') else str(old_mode),
            "current_mode": target_mode.value,
            "reason": reason,
            "warning": self._get_mode_warning(target_mode),
        }
    
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


# =============================================================================
# API Views
# =============================================================================


class MetricStatusView(APIView):
    """
    GET /api/self-healing/metrics/status/
    
    통합 메트릭 상태 조회 API (Observability).
    
    모든 메트릭 신뢰성 지표를 한눈에 파악할 수 있는 SSOT(Single Source of Truth).
    
    Permissions:
        - IsAdmin: selfhealing_admin 그룹 또는 superuser만 접근 가능
    
    Response:
        - generated_at: 리포트 생성 시각
        - operating_mode: 현재 운영 모드 (NORMAL/CAUTIOUS/STRICT/EMERGENCY)
        - overall_health: 전반적 건강 상태 (healthy/degraded/warning/critical)
        - sync_status: 동기화 상태 요약
        - snapshot_health: L1 스냅샷 건강도
        - drift_summary: Drift 요약
        - domains: 도메인별 상세 상태
        - next_sync_expected_at: 다음 예상 동기화 시간
    
    Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request: Request) -> Response:
        """통합 상태 조회."""
        try:
            service = get_governance_service()
            result = service.get_status()
            
            return Response(result, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"[Governance] Status query failed: {e}")
            return Response(
                {
                    "error": str(e),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GovernanceReconcileView(APIView):
    """
    POST /api/self-healing/governance/reconcile/
    
    수동 정합성 조정 API (Control).
    
    기존 /metrics/sync/ 를 대체하며, 더 전문적인 "정합성 조정" 네이밍 사용.
    
    Permissions:
        - IsAdmin: selfhealing_admin 그룹 또는 superuser만 접근 가능
    
    Request Body:
        - domains (list, optional): 조정할 도메인 목록
        - dry_run (bool, optional): True면 리포트만 생성
        - reason (str, optional): 조정 사유 (Audit용)
    
    Response:
        - reconciliation_result: 조정 결과
        - reconciled_at: 조정 시각
        - actor: 수행자
        - results: 도메인별 조정 결과
        - summary: 요약 정보
    
    Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """정합성 조정 수행."""
        from selfhealing.api.django.serializers.metric_sync import (
            MetricSyncRequestSerializer,
        )
        
        serializer = MetricSyncRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid request", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        validated = serializer.validated_data
        domains = validated.get("domains")
        dry_run = validated.get("dry_run", False)
        reason = validated.get("reason", "")
        
        # 사용자 이름 추출
        actor = "unknown"
        if request.user and request.user.is_authenticated:
            actor = request.user.username
        
        try:
            service = get_governance_service()
            result = service.reconcile(
                domains=domains,
                dry_run=dry_run,
                actor=actor,
                reason=reason,
            )
            
            return Response(result, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"[Governance] Reconcile failed: {e}")
            return Response(
                {
                    "reconciliation_result": "failed",
                    "error": str(e),
                    "reconciled_at": datetime.now(timezone.utc).isoformat(),
                    "actor": actor,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GovernanceModeView(APIView):
    """
    POST /api/self-healing/governance/mode/
    
    운영 모드 강제 전환 API (Control - 비상 스위치).
    
    엔진의 지능(Operating Mode)을 수동으로 제어합니다.
    
    Permissions:
        - IsAdmin: selfhealing_admin 그룹 또는 superuser만 접근 가능
        - 권장: IsSuperUser (비상 기능이므로 더 높은 권한 권장)
    
    Request Body:
        - mode (str, required): "NORMAL" | "CAUTIOUS" | "STRICT" | "EMERGENCY"
        - reason (str, optional): 전환 사유 (Audit용)
    
    Response:
        - status: "mode_changed"
        - changed_at: 전환 시각
        - actor: 수행자
        - previous_mode: 이전 모드
        - current_mode: 현재 모드
        - reason: 사유
        - warning: 모드별 경고 메시지
    
    Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """운영 모드 전환."""
        mode = request.data.get("mode")
        reason = request.data.get("reason", "")
        
        if not mode:
            return Response(
                {
                    "error": "mode is required",
                    "valid_modes": ["NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # 사용자 이름 추출
        actor = "unknown"
        if request.user and request.user.is_authenticated:
            actor = request.user.username
        
        try:
            service = get_governance_service()
            result = service.set_mode(
                mode=mode,
                actor=actor,
                reason=reason,
            )
            
            return Response(result, status=status.HTTP_200_OK)
            
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"[Governance] Mode change failed: {e}")
            return Response(
                {
                    "status": "failed",
                    "error": str(e),
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "actor": actor,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    
    def get(self, request: Request) -> Response:
        """현재 운영 모드 조회."""
        try:
            from selfhealing.metrics.reliability_manager import get_reliability_manager
            
            manager = get_reliability_manager()
            mode = manager.get_global_mode()
            
            return Response({
                "current_mode": mode.value if hasattr(mode, 'value') else str(mode),
                "valid_modes": ["NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"],
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"[Governance] Mode query failed: {e}")
            return Response(
                {"error": str(e), "current_mode": "unknown"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Deprecated API Views (Warning + Redirect)
# =============================================================================


class DeprecatedMetricSyncView(APIView):
    """
    POST /api/self-healing/metrics/sync/ (DEPRECATED)
    
    이 엔드포인트는 deprecated 되었습니다.
    대신 POST /api/self-healing/governance/reconcile/ 을 사용하세요.
    
    Warning 헤더와 함께 새 엔드포인트로 요청을 전달합니다.
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """Deprecated: 새 엔드포인트로 전달."""
        logger.warning(
            "[Deprecated] POST /metrics/sync/ called. "
            "Use POST /governance/reconcile/ instead."
        )
        
        # 새 서비스로 처리
        from selfhealing.api.django.serializers.metric_sync import (
            MetricSyncRequestSerializer,
        )
        
        serializer = MetricSyncRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            response = Response(
                {"error": "Invalid request", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
            self._add_deprecation_headers(response)
            return response
        
        validated = serializer.validated_data
        
        actor = "unknown"
        if request.user and request.user.is_authenticated:
            actor = request.user.username
        
        try:
            service = get_governance_service()
            result = service.reconcile(
                domains=validated.get("domains"),
                dry_run=validated.get("dry_run", False),
                actor=actor,
                reason=validated.get("reason", ""),
            )
            
            response = Response(result, status=status.HTTP_200_OK)
            self._add_deprecation_headers(response)
            return response
            
        except Exception as e:
            logger.exception(f"[Deprecated Sync] Failed: {e}")
            response = Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            self._add_deprecation_headers(response)
            return response
    
    def _add_deprecation_headers(self, response: Response) -> None:
        """Deprecation 경고 헤더 추가."""
        response["Warning"] = (
            '299 - "Deprecated API: Use POST /api/self-healing/governance/reconcile/ instead"'
        )
        response["Deprecation"] = "true"
        response["Link"] = (
            '</api/self-healing/governance/reconcile/>; rel="successor-version"'
        )


class DeprecatedDriftReportView(APIView):
    """
    GET /api/self-healing/metrics/drift-report/ (DEPRECATED)
    
    이 엔드포인트는 deprecated 되었습니다.
    대신 GET /api/self-healing/metrics/status/ 를 사용하세요.
    
    Warning 헤더와 함께 새 엔드포인트로 요청을 전달합니다.
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request: Request) -> Response:
        """Deprecated: 새 엔드포인트로 전달."""
        logger.warning(
            "[Deprecated] GET /metrics/drift-report/ called. "
            "Use GET /metrics/status/ instead."
        )
        
        try:
            # 기존 drift-report 형식 유지하며 status에서 추출
            from selfhealing.api.django.views.metric_sync import get_metric_sync_service
            
            service = get_metric_sync_service()
            result = service.get_drift_report()
            
            response = Response(result, status=status.HTTP_200_OK)
            self._add_deprecation_headers(response)
            return response
            
        except Exception as e:
            logger.exception(f"[Deprecated DriftReport] Failed: {e}")
            response = Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            self._add_deprecation_headers(response)
            return response
    
    def _add_deprecation_headers(self, response: Response) -> None:
        """Deprecation 경고 헤더 추가."""
        response["Warning"] = (
            '299 - "Deprecated API: Use GET /api/self-healing/metrics/status/ instead"'
        )
        response["Deprecation"] = "true"
        response["Link"] = (
            '</api/self-healing/metrics/status/>; rel="successor-version"'
        )


# =============================================================================
# Governance RBAC Status API (Phase 2)
# Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
# =============================================================================


class GovernanceRBACStatusView(APIView):
    """
    GET /api/self-healing/governance/status/
    
    거버넌스 RBAC 상태 조회 API.
    현재 운영 모드, 긴급 모드 상태, 임계값, 남은 시간 등을 제공합니다.
    
    Response:
        {
            "status": "success",
            "governance": {
                "current_mode": "STRICT",
                "mode_changed_at": "2025-12-24T10:00:00Z",
                "mode_changed_by": "operator_kim",
                "mode_expires_at": "2025-12-24T18:00:00Z",
                "time_remaining_hours": 3.5,
                "thresholds": {
                    "operator_approve": 0.15,
                    "admin_approve": 0.30
                },
                "emergency_active": true,
                "emergency_warning_sent": false,
                "emergency_final_warning_sent": false,
                "pending_admin_acknowledgement": true
            }
        }
    
    Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    """
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """거버넌스 RBAC 상태 조회."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            from selfhealing.services.governance import get_emergency_tracker
            
            manager = get_runtime_config_manager()
            tracker = get_emergency_tracker()
            
            # 거버넌스 설정 조회
            governance_config = manager.get_governance_config()
            
            # 긴급 모드 상태 조회
            emergency_state = tracker.get_current_state()
            
            # 만료 상태 확인
            expiry_status = tracker.check_expiry_status()
            
            # 응답 구성
            response_data = {
                "status": "success",
                "governance": {
                    # 현재 모드
                    "current_mode": emergency_state.mode,
                    "default_mode": governance_config.get("default_mode", "NORMAL"),
                    
                    # 모드 변경 정보
                    "mode_changed_at": emergency_state.activated_at,
                    "mode_changed_by": emergency_state.activated_by,
                    
                    # 만료 정보
                    "mode_expires_at": expiry_status.get("expires_at"),
                    "time_remaining_hours": expiry_status.get("time_remaining_hours"),
                    
                    # 임계값 (Risk-Based Access Control)
                    "thresholds": {
                        "operator_approve": governance_config.get("threshold_operator", 0.15),
                        "admin_approve": governance_config.get("threshold_admin", 0.30),
                    },
                    
                    # 긴급 모드 상태
                    "emergency_active": emergency_state.is_active,
                    "emergency_reason": emergency_state.reason,
                    "emergency_warning_sent": emergency_state.warning_sent_at is not None,
                    "emergency_final_warning_sent": emergency_state.final_warning_sent_at is not None,
                    "pending_admin_acknowledgement": (
                        emergency_state.is_active and 
                        emergency_state.acknowledged_by is None
                    ),
                    
                    # 경고 상태
                    "should_warn": expiry_status.get("should_warn", False),
                    "should_final_warn": expiry_status.get("should_final_warn", False),
                    "should_auto_restore": expiry_status.get("should_auto_restore", False),
                    
                    # 설정 상세
                    "config": {
                        "emergency_expiry_hours": governance_config.get("emergency_expiry_hours", 8),
                        "emergency_warning_hours": governance_config.get("emergency_warning_hours", 4),
                        "emergency_final_warning_hours": governance_config.get("emergency_final_warning_hours", 6),
                        "notify_on_emergency": governance_config.get("notify_on_emergency", True),
                        "notify_channels": governance_config.get("notify_channels", ["slack", "email"]),
                        "four_eyes_enabled": governance_config.get("four_eyes_enabled", False),
                    },
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"[Governance] Status query failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GovernanceConfigView(APIView):
    """
    GET/PUT /api/self-healing/config/governance/
    
    거버넌스 설정 조회/변경 API.
    RuntimeConfigManager를 통해 중앙 관리됩니다.
    
    GET Response:
        {
            "status": "success",
            "config": {
                "threshold_operator": 0.15,
                "threshold_admin": 0.30,
                "emergency_expiry_hours": 8,
                ...
            }
        }
    
    PUT Request:
        {
            "threshold_operator": 0.20,
            "threshold_admin": 0.40,
            ...
        }
    """
    
    def get_permissions(self):
        """GET은 Viewer, PUT은 Admin 권한 필요."""
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]
    
    def get(self, request: Request) -> Response:
        """거버넌스 설정 조회."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            
            manager = get_runtime_config_manager()
            config = manager.get_governance_config()
            
            return Response(
                {
                    "status": "success",
                    "config": config,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )
            
        except Exception as e:
            logger.exception(f"[Governance] Config query failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    
    def put(self, request: Request) -> Response:
        """거버넌스 설정 변경."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            
            manager = get_runtime_config_manager()
            actor = str(request.user) if request.user.is_authenticated else "anonymous"
            
            # 허용된 필드만 추출
            allowed_fields = {
                "threshold_operator",
                "threshold_admin",
                "emergency_expiry_hours",
                "emergency_warning_hours",
                "emergency_final_warning_hours",
                "default_mode",
                "notify_on_emergency",
                "notify_channels",
                "emergency_slack_channel",
                "emergency_email_recipients",
                "four_eyes_enabled",
                "four_eyes_expiry_hours",
            }
            
            update_fields = {
                k: v for k, v in request.data.items()
                if k in allowed_fields
            }
            
            if not update_fields:
                return Response(
                    {"status": "error", "error": "No valid fields provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            # 업데이트 수행
            try:
                new_config = manager.update_governance_config(**update_fields)
            except ValueError as e:
                return Response(
                    {"status": "error", "error": str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            logger.info(
                f"[Governance] Config updated by {actor}: {list(update_fields.keys())}"
            )
            
            return Response(
                {
                    "status": "updated",
                    "config": new_config,
                    "updated_by": actor,
                    "updated_fields": list(update_fields.keys()),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )
            
        except Exception as e:
            logger.exception(f"[Governance] Config update failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Phase 3: 4-Eyes Approval API
# =============================================================================


class ApprovalRequestListView(APIView):
    """
    4-Eyes Approval Request List API (Phase 3).

    GET  /api/self-healing/governance/approval-requests/
    POST /api/self-healing/governance/approval-requests/

    4-Eyes Principle:
    - Admin A creates a request (PENDING)
    - Admin B receives notification
    - Admin B approves/rejects within 24 hours
    - Expired requests are auto-cleaned

    Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md Phase 3
    - PCI-DSS Dual Control Requirements
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        """
        Get all approval requests.

        Query Parameters:
            status: Filter by status (PENDING, APPROVED, REJECTED, EXPIRED)
            for_me: Only show requests I can approve (excludes my own)
        """
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()

            # Expire old requests first
            manager.expire_old_requests()

            status_filter = request.query_params.get("status")
            for_me = request.query_params.get("for_me", "").lower() == "true"

            actor = getattr(request.user, "username", str(request.user))

            if for_me:
                requests_list = manager.get_pending_requests_for_user(actor)
            else:
                requests_list = manager.get_approval_requests(status=status_filter)

            return Response(
                {
                    "status": "success",
                    "requests": requests_list,
                    "count": len(requests_list),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] Approval request list failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request: Request) -> Response:
        """
        Create a new approval request.

        Request Body:
            request_type: Type (config_change, mode_change, emergency_action)
            description: Human-readable description
            payload: Request data to be approved
            expiry_hours: Hours until expiry (default 24)
        """
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))

            request_type = request.data.get("request_type", "")
            description = request.data.get("description", "")
            payload = request.data.get("payload", {})
            expiry_hours = request.data.get("expiry_hours", 24)

            if not request_type:
                return Response(
                    {"status": "error", "error": "request_type is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not description:
                return Response(
                    {"status": "error", "error": "description is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            approval_request = manager.create_approval_request(
                request_type=request_type,
                description=description,
                requested_by=actor,
                payload=payload,
                expiry_hours=expiry_hours,
            )

            logger.info(
                f"[Governance] Approval request created: {approval_request['id']} by {actor}"
            )

            return Response(
                {
                    "status": "created",
                    "request": approval_request,
                    "message": "Approval request created. Awaiting approval from another admin.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.exception(f"[Governance] Approval request creation failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApprovalRequestApproveView(APIView):
    """
    4-Eyes Approval Request Approve API (Phase 3).

    POST /api/self-healing/governance/approval-requests/{id}/approve/

    Approves a pending request. The approver must be different from the requester.
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, request_id: str) -> Response:
        """Approve a pending request."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))

            result = manager.approve_request(request_id, actor)

            if result is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Request not found, already processed, expired, or self-approval attempted",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(f"[Governance] Request {request_id} approved by {actor}")

            return Response(
                {
                    "status": "approved",
                    "request": result,
                    "approved_by": actor,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] Approval failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApprovalRequestRejectView(APIView):
    """
    4-Eyes Approval Request Reject API (Phase 3).

    POST /api/self-healing/governance/approval-requests/{id}/reject/
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, request_id: str) -> Response:
        """Reject a pending request."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))
            reason = request.data.get("reason", "")

            result = manager.reject_request(request_id, actor, reason)

            if result is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Request not found or already processed",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(f"[Governance] Request {request_id} rejected by {actor}")

            return Response(
                {
                    "status": "rejected",
                    "request": result,
                    "rejected_by": actor,
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] Rejection failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Phase 3: L2 Storage Config API (Refactored)
# =============================================================================


class L2StorageConfigManagedView(APIView):
    """
    L2 Storage Config API using RuntimeConfigManager (Phase 3).

    GET  /api/self-healing/config/l2-storage/
    PUT  /api/self-healing/config/l2-storage/

    Replaces the old L2StorageConfigView with RuntimeConfigManager integration.
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        """Get L2 storage configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            config = manager.get_l2_storage_config()

            return Response(
                {
                    "status": "success",
                    "config": config,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] L2 storage config get failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Update L2 storage configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))

            update_fields = {
                k: v for k, v in request.data.items()
                if k in [
                    "redis_timeout_ms",
                    "database_timeout_ms",
                    "fallback_timeout_ms",
                    "shadow_log_enabled",
                    "shadow_log_max_entries",
                    "reconciliation_jitter_min_seconds",
                    "reconciliation_jitter_max_seconds",
                    "health_check_interval_seconds",
                    "health_check_timeout_ms",
                ]
            }

            if not update_fields:
                return Response(
                    {"status": "error", "error": "No valid fields provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            new_config = manager.update_l2_storage_config(**update_fields)

            logger.info(
                f"[Governance] L2 storage config updated by {actor}: {list(update_fields.keys())}"
            )

            return Response(
                {
                    "status": "updated",
                    "config": new_config,
                    "updated_by": actor,
                    "updated_fields": list(update_fields.keys()),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] L2 storage config update failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


__all__ = [
    # Service
    "GovernanceService",
    "get_governance_service",
    "reset_governance_service",
    # New API Views
    "MetricStatusView",
    "GovernanceReconcileView",
    "GovernanceModeView",
    # Phase 2 API Views
    "GovernanceRBACStatusView",
    "GovernanceConfigView",
    # Phase 3 API Views
    "ApprovalRequestListView",
    "ApprovalRequestApproveView",
    "ApprovalRequestRejectView",
    "L2StorageConfigManagedView",
    # Deprecated Views
    "DeprecatedMetricSyncView",
    "DeprecatedDriftReportView",
]
