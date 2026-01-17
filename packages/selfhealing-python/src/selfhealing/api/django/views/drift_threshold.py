"""
Drift Threshold Configuration API Views.

REST API endpoints for Drift threshold management.
Now integrated with RuntimeConfigManager for centralized config management.

Endpoints:
- GET  /api/self-healing/config/drift-thresholds/       - Get drift threshold config
- PUT  /api/self-healing/config/drift-thresholds/       - Update drift threshold config
- POST /api/self-healing/config/drift-thresholds/reset/ - Reset to defaults
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer, IsSelfHealingAdmin
from selfhealing.services.runtime_config import get_runtime_config_manager

logger = logging.getLogger(__name__)


def _get_threshold_percent_display(config: Dict[str, Any]) -> Dict[str, str]:
    """임계값을 퍼센트 문자열로 반환."""
    return {
        "warning": f"{config.get('warning_threshold', 0.05) * 100:.1f}%",
        "critical": f"{config.get('critical_threshold', 0.20) * 100:.1f}%",
        "incident": f"{config.get('incident_threshold', 0.50) * 100:.1f}%",
    }


class DriftThresholdConfigView(APIView):
    """
    Drift Threshold Configuration API.

    GET  /api/self-healing/config/drift-thresholds/
    PUT  /api/self-healing/config/drift-thresholds/

    Drift 임계값을 동적으로 조정할 수 있습니다.
    변경 시 Audit 로그가 기록됩니다.
    RuntimeConfigManager를 통해 중앙 관리됩니다.

    Thresholds:
        - warning_threshold: 5% (기본) - 경고 로그
        - critical_threshold: 20% (기본) - 알림 발송
        - incident_threshold: 50% (기본) - 인시던트 생성
    """

    def get_permissions(self):
        """GET은 Viewer, PUT은 Admin 권한 필요."""
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """현재 Drift 임계값 설정 조회."""
        try:
            manager = get_runtime_config_manager()
            config = manager.get_drift_threshold_config()

            return Response(
                {
                    "status": "success",
                    "config": config,
                    "thresholds_percent": _get_threshold_percent_display(config),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[DriftThresholdAPI] Error getting config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Drift 임계값 설정 업데이트."""
        try:
            manager = get_runtime_config_manager()
            actor_id = str(request.user) if request.user.is_authenticated else "anonymous"

            # 요청 데이터 검증
            data = request.data
            update_fields = {}

            # 임계값 필드 검증
            for field in ["warning_threshold", "critical_threshold", "incident_threshold"]:
                if field in data:
                    value = data[field]
                    if not isinstance(value, (int, float)):
                        return Response(
                            {
                                "status": "error",
                                "error": f"{field} must be a number",
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    if not 0 < value <= 1.0:
                        return Response(
                            {
                                "status": "error",
                                "error": f"{field} must be between 0 and 1.0",
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    update_fields[field] = float(value)

            # Boolean 필드 검증
            for field in ["alert_enabled", "incident_auto_create"]:
                if field in data:
                    update_fields[field] = bool(data[field])

            if not update_fields:
                return Response(
                    {
                        "status": "error",
                        "error": "No valid fields provided for update",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # RuntimeConfigManager를 통해 업데이트 (검증 및 History 자동 처리)
            try:
                new_config = manager.update_drift_threshold_config(
                    changed_by=actor_id,
                    reason=f"API update: {list(update_fields.keys())}",
                    **update_fields
                )
            except ValueError as e:
                return Response(
                    {"status": "error", "error": str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Audit 로깅
            logger.info(
                f"[DriftThresholdAPI] Config updated by {actor_id}: "
                f"fields={list(update_fields.keys())}"
            )

            return Response(
                {
                    "status": "updated",
                    "config": new_config,
                    "thresholds_percent": _get_threshold_percent_display(new_config),
                    "updated_by": actor_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"[DriftThresholdAPI] Error updating config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DriftThresholdResetView(APIView):
    """
    Drift Threshold Reset API.

    POST /api/self-healing/config/drift-thresholds/reset/

    Drift 임계값을 기본값으로 리셋합니다.
    RuntimeConfigManager를 통해 중앙 관리됩니다.
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Drift 임계값 기본값으로 리셋."""
        try:
            actor_id = str(request.user) if request.user.is_authenticated else "anonymous"
            manager = get_runtime_config_manager()

            # RuntimeConfigManager를 통해 리셋
            default_config = manager.reset_drift_threshold_config(changed_by=actor_id)

            # Audit 로깅
            logger.info(f"[DriftThresholdAPI] Config reset by {actor_id}")

            return Response(
                {
                    "status": "reset",
                    "config": default_config,
                    "thresholds_percent": _get_threshold_percent_display(default_config),
                    "reset_by": actor_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"[DriftThresholdAPI] Error resetting config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


__all__ = [
    "DriftThresholdConfigView",
    "DriftThresholdResetView",
]
