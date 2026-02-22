"""
Metric Sync Views - Poll 제거 + Manual API.

비침습적 Drift 관리를 위한 수동 동기화 API.
주기적 DB 폴링을 제거하고, 운영자의 명시적 요청만 허용합니다.

Endpoints:
- POST /api/self-healing/metrics/sync/ - 수동 메트릭 동기화
- GET /api/self-healing/metrics/drift-report/ - Drift 상태 조회
"""

from __future__ import annotations

import structlog
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin
from selfhealing.api.django.serializers.metric_sync import (
    DriftReportResponseSerializer,
    MetricSyncRequestSerializer,
    MetricSyncResponseSerializer,
)

# Import from services layer (not from views to avoid circular imports)
from selfhealing.services.metric_sync_service import (
    MetricSyncService,
    get_metric_sync_service,
    reset_metric_sync_service,
)

logger = structlog.get_logger()


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
        # Exception은 exception handler가 처리


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
        service = get_metric_sync_service()
        result = service.get_drift_report()

        response_serializer = DriftReportResponseSerializer(data=result)
        if response_serializer.is_valid():
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        else:
            return Response(result, status=status.HTTP_200_OK)
        # Exception은 exception handler가 처리


__all__ = [
    "MetricSyncView",
    "DriftReportView",
    "MetricSyncService",
    "get_metric_sync_service",
    "reset_metric_sync_service",
]
