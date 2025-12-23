"""
Drift Threshold Configuration API Views.

REST API endpoints for Drift threshold management.

Endpoints:
- GET  /api/self-healing/config/drift-thresholds/       - Get drift threshold config
- PUT  /api/self-healing/config/drift-thresholds/       - Update drift threshold config
- POST /api/self-healing/config/drift-thresholds/reset/ - Reset to defaults

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
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
from selfhealing.models.drift_config import DriftThresholdConfig
from selfhealing.core.state_backend import get_state_backend
from selfhealing.services.config_history import get_config_history_service

logger = logging.getLogger(__name__)

# Storage key for drift threshold config
DRIFT_THRESHOLD_CONFIG_KEY = "drift_threshold_config"


class DriftThresholdConfigView(APIView):
    """
    Drift Threshold Configuration API.

    GET  /api/self-healing/config/drift-thresholds/
    PUT  /api/self-healing/config/drift-thresholds/

    Drift 임계값을 동적으로 조정할 수 있습니다.
    변경 시 Audit 로그가 기록됩니다.

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
            config = self._get_config()

            return Response(
                {
                    "status": "success",
                    "config": config.to_dict(),
                    "thresholds_percent": config.get_threshold_percent_display(),
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
            current = self._get_config()
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

            # 새 설정 생성 (검증 포함)
            try:
                new_config = current.update(actor_id=actor_id, **update_fields)
            except ValueError as e:
                return Response(
                    {"status": "error", "error": str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 저장
            backend = get_state_backend()
            backend.set(DRIFT_THRESHOLD_CONFIG_KEY, new_config.to_dict())

            # ConfigHistory에 버전 저장 (감사 추적용)
            try:
                history_service = get_config_history_service()
                history_service.save_version(
                    config_type="drift_threshold",
                    values=new_config.to_dict(),
                    changed_by=actor_id,
                    reason=f"Updated fields: {list(update_fields.keys())}",
                )
            except Exception as history_err:
                # History 저장 실패해도 설정 변경은 성공으로 처리 (Graceful Degradation)
                logger.warning(f"[DriftThresholdAPI] History save failed: {history_err}")

            # Audit 로깅
            logger.info(
                f"[DriftThresholdAPI] Config updated by {actor_id}: "
                f"before={current.to_dict()}, after={new_config.to_dict()}"
            )

            return Response(
                {
                    "status": "updated",
                    "config": new_config.to_dict(),
                    "thresholds_percent": new_config.get_threshold_percent_display(),
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

    def _get_config(self) -> DriftThresholdConfig:
        """저장된 설정 로드 또는 기본값 반환."""
        try:
            backend = get_state_backend()
            data = backend.get(DRIFT_THRESHOLD_CONFIG_KEY)

            if data:
                return DriftThresholdConfig.from_dict(data)
        except Exception as e:
            logger.warning(f"[DriftThresholdAPI] Failed to load config: {e}")

        return DriftThresholdConfig()  # 기본값


class DriftThresholdResetView(APIView):
    """
    Drift Threshold Reset API.

    POST /api/self-healing/config/drift-thresholds/reset/

    Drift 임계값을 기본값으로 리셋합니다.
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Drift 임계값 기본값으로 리셋."""
        try:
            actor_id = str(request.user) if request.user.is_authenticated else "anonymous"
            backend = get_state_backend()

            # 현재 설정 조회 (Audit용)
            current_data = backend.get(DRIFT_THRESHOLD_CONFIG_KEY)

            # 기본값으로 리셋
            default = DriftThresholdConfig(
                updated_at=datetime.now(timezone.utc).isoformat(),
                updated_by=actor_id,
            )
            backend.set(DRIFT_THRESHOLD_CONFIG_KEY, default.to_dict())

            # ConfigHistory에 버전 저장 (감사 추적용)
            try:
                history_service = get_config_history_service()
                history_service.save_version(
                    config_type="drift_threshold",
                    values=default.to_dict(),
                    changed_by=actor_id,
                    reason="Reset to default values",
                )
            except Exception as history_err:
                # History 저장 실패해도 리셋은 성공으로 처리 (Graceful Degradation)
                logger.warning(f"[DriftThresholdAPI] History save failed: {history_err}")

            # Audit 로깅
            logger.info(
                f"[DriftThresholdAPI] Config reset by {actor_id}: "
                f"before={current_data}, after={default.to_dict()}"
            )

            return Response(
                {
                    "status": "reset",
                    "config": default.to_dict(),
                    "thresholds_percent": default.get_threshold_percent_display(),
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
    "DRIFT_THRESHOLD_CONFIG_KEY",
]
