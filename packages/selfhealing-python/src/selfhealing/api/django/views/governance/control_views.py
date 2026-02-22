"""
Governance Control Views.

정합성 조정, 모드 전환 등 Control 관련 View 클래스들입니다.

Reference:
- docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
"""

from __future__ import annotations

import structlog

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import (
    EmergencyEscalationPermission,
    IsSelfHealingAdmin,
)
from selfhealing.services.governance_api_service import get_governance_api_service

logger = structlog.get_logger()


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
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """정합성 조정 수행."""
        from selfhealing.api.django.serializers.metric_sync import (
            MetricSyncRequestSerializer,
        )

        serializer = MetricSyncRequestSerializer(data=request.data)

        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        validated = serializer.validated_data
        domains = validated.get("domains")
        dry_run = validated.get("dry_run", False)
        reason = validated.get("reason", "")

        # 사용자 이름 추출
        actor = "unknown"
        if request.user and request.user.is_authenticated:
            actor = request.user.username

        service = get_governance_api_service()
        result = service.reconcile(
            domains=domains,
            dry_run=dry_run,
            actor=actor,
            reason=reason,
        )

        return Response(result, status=status.HTTP_200_OK)


class GovernanceModeView(APIView):
    """
    POST /api/self-healing/governance/mode/

    운영 모드 강제 전환 API (Control - 비상 스위치).

    엔진의 지능(Operating Mode)을 수동으로 제어합니다.

    Break Glass Pattern (긴급 에스컬레이션):
        - STRICT 전환: Operator도 가능 (일방향 긴급권) + reason 필수
        - NORMAL 복구: Admin만 가능
        - 기타 모드: Admin만 가능

    자동 만료:
        - STRICT 모드는 governance config의 emergency_expiry_hours 후 자동 만료
        - Celery Beat 태스크(check_emergency_mode_expiry)가 15분 주기로 체크

    Request Body:
        - mode (str, required): "NORMAL" | "CAUTIOUS" | "STRICT" | "EMERGENCY"
        - reason (str, required for STRICT): 전환 사유 (강제 Audit)

    Response:
        - status: "mode_changed"
        - changed_at: 전환 시각
        - actor: 수행자
        - previous_mode: 이전 모드
        - current_mode: 현재 모드
        - reason: 사유
        - warning: 모드별 경고 메시지
        - expires_at: 자동 만료 시각 (STRICT 모드인 경우)

    Reference:
        - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md (Section 1.5)
        - docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
    """

    permission_classes = [EmergencyEscalationPermission]

    def post(self, request: Request) -> Response:
        """운영 모드 전환."""
        mode = request.data.get("mode")
        reason = request.data.get("reason", "")

        if not mode:
            raise ValueError("mode is required")

        # 사용자 이름 추출
        actor = "unknown"
        if request.user and request.user.is_authenticated:
            actor = request.user.username

        service = get_governance_api_service()
        result = service.set_mode(
            mode=mode,
            actor=actor,
            reason=reason,
        )

        return Response(result, status=status.HTTP_200_OK)

    def get(self, request: Request) -> Response:
        """현재 운영 모드 조회."""
        from selfhealing.metrics.reliability_manager import get_reliability_manager

        manager = get_reliability_manager()
        mode = manager.get_global_mode()

        return Response(
            {
                "current_mode": mode.value if hasattr(mode, "value") else str(mode),
                "valid_modes": ["NORMAL", "CAUTIOUS", "STRICT", "EMERGENCY"],
            },
            status=status.HTTP_200_OK,
        )


__all__ = [
    "GovernanceReconcileView",
    "GovernanceModeView",
]
