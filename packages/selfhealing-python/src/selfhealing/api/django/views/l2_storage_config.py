"""
L2 Storage Configuration API Views.

Endpoints:
- GET  /api/self-healing/l2-storage/config/      - Get L2 storage config
- PUT  /api/self-healing/l2-storage/config/      - Update L2 storage config
- POST /api/self-healing/l2-storage/config/reset - Reset config to defaults
"""

import structlog
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.api.django.serializers.config import L2StorageConfigSerializer
from selfhealing.config import get_l2_storage_runtime_config

logger = structlog.get_logger()


class L2StorageConfigView(APIView):
    """
    L2 Storage Configuration API.

    GET  /api/self-healing/l2-storage/config/ - Get current config (Viewer)
    PUT  /api/self-healing/l2-storage/config/ - Update config (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get current L2 storage configuration."""
        config = get_l2_storage_runtime_config()

        return Response(
            {
                "status": "success",
                "config": config.to_dict(),
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리

    def put(self, request: Request) -> Response:
        """Update L2 storage configuration."""
        serializer = L2StorageConfigSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValueError(f"Validation failed: {serializer.errors}")

        config = get_l2_storage_runtime_config()
        changes = serializer.get_config_changes()

        if not changes:
            raise ValueError("No changes provided")

        # ValueError는 exception handler가 400으로 처리
        updated_config = config.update(
            **changes,
            updated_by=str(request.user),
        )

        logger.info(
            "l2_storage_api.config_updated",
            request=request.user,
            changes=changes,
        )

        return Response(
            {
                "status": "success",
                "message": "L2 storage configuration updated",
                "config": updated_config,
                "changes": changes,
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리


class L2StorageConfigResetView(APIView):
    """
    L2 Storage Configuration Reset API.

    POST /api/self-healing/l2-storage/config/reset - Reset to defaults (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Reset L2 storage configuration to defaults."""
        config = get_l2_storage_runtime_config()
        config.reset()

        logger.info(
            "l2_storage_api.config_reset_defaults",
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "message": "L2 storage configuration reset to defaults",
                "config": config.to_dict(),
                "timestamp": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )
        # Exception은 exception handler가 처리
