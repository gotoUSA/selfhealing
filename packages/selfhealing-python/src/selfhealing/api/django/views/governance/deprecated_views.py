"""
Deprecated Governance Views.

하위 호환성을 위해 유지되는 Deprecated API View 클래스들입니다.
Warning 헤더와 함께 새 엔드포인트로 요청을 전달합니다.

Reference:
- docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin
from selfhealing.api.django.views.governance.service import get_governance_service

logger = logging.getLogger(__name__)


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


__all__ = [
    "DeprecatedMetricSyncView",
    "DeprecatedDriftReportView",
]
