"""
Governance Config Views.

설정 조회/변경 관련 View 클래스들입니다.

Reference:
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

logger = logging.getLogger(__name__)


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

    def put(self, request: Request) -> Response:
        """거버넌스 설정 변경."""
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

        update_fields = {k: v for k, v in request.data.items() if k in allowed_fields}

        if not update_fields:
            raise ValueError("No valid fields provided")

        # 업데이트 수행 - ValueError는 exception handler로 전파
        new_config = manager.update_governance_config(**update_fields)

        logger.info(f"[Governance] Config updated by {actor}: {list(update_fields.keys())}")

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


class L2StorageConfigManagedView(APIView):
    """
    L2 Storage Config API using RuntimeConfigManager.

    GET  /api/self-healing/config/l2-storage/
    PUT  /api/self-healing/config/l2-storage/

    Replaces the old L2StorageConfigView with RuntimeConfigManager integration.
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        """Get L2 storage configuration."""
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

    def put(self, request: Request) -> Response:
        """Update L2 storage configuration."""
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        actor = getattr(request.user, "username", str(request.user))

        update_fields = {
            k: v
            for k, v in request.data.items()
            if k
            in [
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
            raise ValueError("No valid fields provided")

        new_config = manager.update_l2_storage_config(**update_fields)

        logger.info(f"[Governance] L2 storage config updated by {actor}: {list(update_fields.keys())}")

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


__all__ = [
    "GovernanceConfigView",
    "L2StorageConfigManagedView",
]
