"""
Governance Status Views.

메트릭 상태 조회, RBAC 상태 조회 등 Observability 관련 View 클래스들입니다.

Reference:
- docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.services.governance_api_service import get_governance_api_service

logger = logging.getLogger(__name__)


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
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        """통합 상태 조회."""
        service = get_governance_api_service()
        result = service.get_status()

        return Response(result, status=status.HTTP_200_OK)


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
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """거버넌스 RBAC 상태 조회."""
        from selfhealing.services.governance import get_emergency_tracker
        from selfhealing.services.runtime_config import get_runtime_config_manager

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
                "pending_admin_acknowledgement": (emergency_state.is_active and emergency_state.acknowledged_by is None),
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


__all__ = [
    "MetricStatusView",
    "GovernanceRBACStatusView",
]
