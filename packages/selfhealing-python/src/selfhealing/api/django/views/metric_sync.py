"""
Metric Sync Views - Poll 제거 + Manual API.

비침습적 Drift 관리를 위한 수동 동기화 API.
주기적 DB 폴링을 제거하고, 운영자의 명시적 요청만 허용합니다.

Endpoints:
- POST /api/self-healing/metrics/sync/ - 수동 메트릭 동기화
- GET /api/self-healing/metrics/drift-report/ - Drift 상태 조회
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin
from selfhealing.api.django.serializers.metric_sync import (
    MetricSyncRequestSerializer,
    MetricSyncResponseSerializer,
    DriftReportResponseSerializer,
)
from selfhealing.metrics.reconciler import (
    MetricReconciler,
    DriftSeverity,
    get_reconciler,
)
from selfhealing.adapters.metrics.factory import get_metric_adapter

if TYPE_CHECKING:
    from selfhealing.adapters.metrics.base import MetricSourceAdapter

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================


class DriftThresholds:
    """Drift 심각도 임계값 (퍼센트)."""
    
    WARNING = 5.0      # 5% 이상: 경고
    CRITICAL = 20.0    # 20% 이상: 심각
    INCIDENT = 50.0    # 50% 이상: 인시던트


# =============================================================================
# Service Layer
# =============================================================================


class MetricSyncService:
    """
    메트릭 동기화 서비스.
    
    Reconciler를 래핑하여 API에서 필요한 형태로 결과를 변환합니다.
    """
    
    def __init__(
        self,
        reconciler: Optional[MetricReconciler] = None,
        adapter: Optional["MetricSourceAdapter"] = None,
    ):
        self.reconciler = reconciler or get_reconciler(adapter)
        self.adapter = adapter or get_metric_adapter()
    
    def sync_metrics(
        self,
        domains: Optional[List[str]] = None,
        dry_run: bool = False,
        actor: str = "unknown",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        메트릭 동기화 수행.
        
        Args:
            domains: 동기화할 도메인 목록 (None이면 전체)
            dry_run: True면 리포트만 생성
            actor: 동기화 수행자
            reason: 동기화 사유
            
        Returns:
            동기화 결과 딕셔너리
        """
        now = datetime.now(timezone.utc)
        
        # 현재 상태 캡처 (동기화 전)
        before_state = self._capture_current_state(domains)
        
        if dry_run:
            # Dry run: 실제 동기화 없이 리포트만
            after_state = self._get_actual_state(domains)
            results = self._build_results(before_state, after_state, domains)
            summary = self._calculate_summary(results)
            
            return {
                "status": "dry_run",
                "synced_at": now.isoformat(),
                "actor": actor,
                "dry_run": True,
                "results": results,
                "summary": summary,
            }
        
        # 실제 동기화 수행
        if domains:
            for domain in domains:
                self.reconciler.sync_domain_gauges(domain)
        else:
            self.reconciler.sync_all_gauges()
        
        # 동기화 후 상태
        after_state = self._get_actual_state(domains)
        results = self._build_results(before_state, after_state, domains)
        summary = self._calculate_summary(results)
        
        # Audit 로깅
        self._log_sync_action(actor, domains, dry_run, reason, summary)
        
        return {
            "status": "completed",
            "synced_at": now.isoformat(),
            "actor": actor,
            "dry_run": False,
            "results": results,
            "summary": summary,
        }
    
    def get_drift_report(self) -> Dict[str, Any]:
        """
        현재 Drift 상태 조회 (읽기 전용).
        
        DB 조회는 수행하지만 Gauge 값은 변경하지 않습니다.
        
        Returns:
            Drift 리포트 딕셔너리
        """
        now = datetime.now(timezone.utc)
        
        # 인메모리 상태
        in_memory_state = self._capture_current_state(None)
        
        # 실제 DB 상태
        actual_state = self._get_actual_state(None)
        
        # Drift 계산
        metrics = self._calculate_drift_metrics(in_memory_state, actual_state)
        max_drift_percent = self._get_max_drift_percent(metrics)
        overall_health = self._classify_health(max_drift_percent)
        recommendation = self._get_recommendation(overall_health)
        
        return {
            "generated_at": now.isoformat(),
            "metrics": metrics,
            "overall_health": overall_health,
            "max_drift_percent": max_drift_percent,
            "recommendation": recommendation,
        }
    
    def _capture_current_state(
        self, domains: Optional[List[str]]
    ) -> Dict[str, Dict[str, Any]]:
        """현재 인메모리(Gauge) 상태 캡처."""
        result: Dict[str, Dict[str, Any]] = {
            "dlq_pending": {},
            "circuit_breaker": {},
            "retry_rate": {},
        }
        
        target_domains = domains or self._get_all_domains()
        
        # 현재 Gauge 값 읽기 시도
        try:
            from selfhealing.metrics.prometheus import get_metrics
            metrics = get_metrics()
            
            if metrics and hasattr(metrics, "dlq_pending_gauge"):
                for domain in target_domains:
                    try:
                        gauge = metrics.dlq_pending_gauge.labels(domain=domain)
                        # Prometheus Gauge의 현재 값 접근
                        result["dlq_pending"][domain] = gauge._value.get()
                    except Exception:
                        result["dlq_pending"][domain] = 0
        except ImportError:
            # prometheus 모듈 없으면 0으로 가정
            for domain in target_domains:
                result["dlq_pending"][domain] = 0
        
        return result
    
    def _get_actual_state(
        self, domains: Optional[List[str]]
    ) -> Dict[str, Dict[str, Any]]:
        """DB에서 실제 상태 조회."""
        result: Dict[str, Dict[str, Any]] = {
            "dlq_pending": {},
            "circuit_breaker": {},
            "retry_rate": {},
        }
        
        target_domains = domains or self._get_all_domains()
        
        for domain in target_domains:
            try:
                result["dlq_pending"][domain] = self.adapter.get_dlq_pending_count(domain)
            except Exception as e:
                logger.warning(f"Failed to get DLQ pending for {domain}: {e}")
                result["dlq_pending"][domain] = 0
            
            try:
                result["retry_rate"][domain] = self.adapter.get_retry_success_rate(domain)
            except Exception as e:
                logger.warning(f"Failed to get retry rate for {domain}: {e}")
                result["retry_rate"][domain] = 0.0
        
        return result
    
    def _get_all_domains(self) -> List[str]:
        """등록된 모든 도메인 목록 반환."""
        try:
            from selfhealing.metrics.prometheus import get_domains
            return get_domains()
        except ImportError:
            return ["payment", "point", "inventory"]
    
    def _build_results(
        self,
        before: Dict[str, Dict[str, Any]],
        after: Dict[str, Dict[str, Any]],
        domains: Optional[List[str]],
    ) -> Dict[str, Dict[str, Any]]:
        """동기화 결과 빌드."""
        results: Dict[str, Dict[str, Any]] = {}
        target_domains = domains or self._get_all_domains()
        
        for domain in target_domains:
            before_dlq = before.get("dlq_pending", {}).get(domain, 0)
            after_dlq = after.get("dlq_pending", {}).get(domain, 0)
            
            results[domain] = {
                "dlq_pending": {
                    "before": before_dlq,
                    "after": after_dlq,
                    "drift": after_dlq - before_dlq,
                }
            }
        
        return results
    
    def _calculate_summary(
        self, results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """동기화 요약 계산."""
        total_drifts = 0
        max_drift_percent = 0.0
        
        for domain, domain_result in results.items():
            dlq = domain_result.get("dlq_pending", {})
            drift = abs(dlq.get("drift", 0))
            before = dlq.get("before", 0)
            
            if drift > 0:
                total_drifts += 1
                if before > 0:
                    drift_pct = (drift / before) * 100
                    max_drift_percent = max(max_drift_percent, drift_pct)
                elif dlq.get("after", 0) > 0:
                    max_drift_percent = max(max_drift_percent, 100.0)
        
        return {
            "total_drifts_detected": total_drifts,
            "total_drifts_corrected": total_drifts,  # 동기화 후에는 모두 보정됨
            "max_drift_percent": round(max_drift_percent, 2),
        }
    
    def _calculate_drift_metrics(
        self,
        in_memory: Dict[str, Dict[str, Any]],
        actual: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Drift 메트릭 계산."""
        metrics: Dict[str, Dict[str, Any]] = {
            "dlq_pending_count": {},
        }
        
        for domain in in_memory.get("dlq_pending", {}):
            in_mem = in_memory["dlq_pending"].get(domain, 0)
            act = actual.get("dlq_pending", {}).get(domain, 0)
            drift = act - in_mem
            
            # Drift 퍼센트 계산
            if in_mem > 0:
                drift_percent = abs(drift / in_mem) * 100
            elif act > 0:
                drift_percent = 100.0
            else:
                drift_percent = 0.0
            
            metrics["dlq_pending_count"][domain] = {
                "in_memory": in_mem,
                "actual": act,
                "drift": drift,
                "drift_percent": round(drift_percent, 2),
                "is_critical": drift_percent >= DriftThresholds.CRITICAL,
            }
        
        return metrics
    
    def _get_max_drift_percent(
        self, metrics: Dict[str, Dict[str, Any]]
    ) -> float:
        """최대 Drift 퍼센트 추출."""
        max_pct = 0.0
        
        for metric_type, domains in metrics.items():
            for domain, info in domains.items():
                pct = info.get("drift_percent", 0.0)
                max_pct = max(max_pct, pct)
        
        return round(max_pct, 2)
    
    def _classify_health(self, max_drift_percent: float) -> str:
        """Drift 기반 상태 분류."""
        if max_drift_percent >= DriftThresholds.INCIDENT:
            return "incident"
        elif max_drift_percent >= DriftThresholds.CRITICAL:
            return "critical"
        elif max_drift_percent >= DriftThresholds.WARNING:
            return "warning"
        return "healthy"
    
    def _get_recommendation(self, health: str) -> str:
        """상태 기반 권장 조치."""
        recommendations = {
            "healthy": "",
            "warning": "Drift가 감지되었습니다. 모니터링을 권장합니다.",
            "critical": "심각한 Drift입니다. POST /api/self-healing/metrics/sync/ 실행을 권장합니다.",
            "incident": "인시던트 수준의 Drift입니다. 즉시 동기화하고 이벤트 유실 여부를 확인하세요.",
        }
        return recommendations.get(health, "")
    
    def _log_sync_action(
        self,
        actor: str,
        domains: Optional[List[str]],
        dry_run: bool,
        reason: Optional[str],
        summary: Dict[str, Any],
    ) -> None:
        """Audit 로깅."""
        try:
            from selfhealing.audit.logger import AuditLogger, ConfigChangeEvent, AuditAction
            
            audit_logger = AuditLogger.get_instance()
            event = ConfigChangeEvent(
                config_type="metric_sync",
                config_key="manual_sync",
                action=AuditAction.APPLY,
                old_value=None,
                new_value={
                    "domains": domains or "all",
                    "dry_run": dry_run,
                    "drifts_corrected": summary.get("total_drifts_corrected", 0),
                },
                reason=reason or "Manual metric synchronization",
                user=actor,
                source="api",
                metadata={
                    "category": "metric_reconciliation",
                    "summary": summary,
                },
            )
            audit_logger.log(event)
            logger.info(f"[MetricSync] Audit logged: actor={actor}, drifts={summary}")
        except Exception as e:
            # Audit 실패해도 동기화는 계속
            logger.warning(f"[MetricSync] Failed to log audit: {e}")


# =============================================================================
# Singleton Service Instance
# =============================================================================


_metric_sync_service: Optional[MetricSyncService] = None


def get_metric_sync_service() -> MetricSyncService:
    """MetricSyncService 싱글톤 인스턴스 반환."""
    global _metric_sync_service
    
    if _metric_sync_service is None:
        _metric_sync_service = MetricSyncService()
    
    return _metric_sync_service


def reset_metric_sync_service() -> None:
    """MetricSyncService 인스턴스 리셋 (테스트용)."""
    global _metric_sync_service
    _metric_sync_service = None


# =============================================================================
# API Views
# =============================================================================


class MetricSyncView(APIView):
    """
    POST /api/self-healing/metrics/sync/
    
    수동 메트릭 동기화 API.
    
    운영자가 명시적으로 요청할 때만 DB를 조회하여
    인메모리 Gauge 값을 실제 값과 동기화합니다.
    
    Permissions:
        - IsAdmin: selfhealing_admin 그룹 또는 superuser만 접근 가능
    
    Request Body:
        - domains (list, optional): 동기화할 도메인 목록
        - dry_run (bool, optional): True면 리포트만 생성
        - reason (str, optional): 동기화 사유 (Audit용)
    
    Response:
        - status: completed | dry_run | failed
        - synced_at: 동기화 시각
        - actor: 수행자
        - results: 도메인별 동기화 결과
        - summary: 요약 정보
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """메트릭 동기화 수행."""
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
            service = get_metric_sync_service()
            result = service.sync_metrics(
                domains=domains,
                dry_run=dry_run,
                actor=actor,
                reason=reason,
            )
            
            response_serializer = MetricSyncResponseSerializer(data=result)
            if response_serializer.is_valid():
                return Response(response_serializer.data, status=status.HTTP_200_OK)
            else:
                # 응답 직렬화 실패 시 원본 반환
                return Response(result, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"[MetricSync] Sync failed: {e}")
            return Response(
                {
                    "status": "failed",
                    "error": str(e),
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                    "actor": actor,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DriftReportView(APIView):
    """
    GET /api/self-healing/metrics/drift-report/
    
    현재 Drift 상태 조회 API (읽기 전용).
    
    DB를 조회하여 인메모리 Gauge 값과 비교하지만,
    Gauge 값을 변경하지는 않습니다.
    
    Permissions:
        - IsAdmin: selfhealing_admin 그룹 또는 superuser만 접근 가능
    
    Response:
        - generated_at: 리포트 생성 시각
        - metrics: 메트릭별 Drift 정보
        - overall_health: 전반적 상태 (healthy/warning/critical/incident)
        - max_drift_percent: 최대 Drift 퍼센트
        - recommendation: 권장 조치
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request: Request) -> Response:
        """Drift 리포트 조회."""
        try:
            service = get_metric_sync_service()
            result = service.get_drift_report()
            
            response_serializer = DriftReportResponseSerializer(data=result)
            if response_serializer.is_valid():
                return Response(response_serializer.data, status=status.HTTP_200_OK)
            else:
                return Response(result, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"[DriftReport] Report generation failed: {e}")
            return Response(
                {
                    "error": str(e),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


__all__ = [
    "MetricSyncView",
    "DriftReportView",
    "MetricSyncService",
    "get_metric_sync_service",
    "reset_metric_sync_service",
]
