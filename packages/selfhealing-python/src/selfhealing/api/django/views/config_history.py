"""
Config History & Rollback API Views.

설정 변경 이력 조회 및 롤백 API 엔드포인트.

Endpoints:
- GET  /api/self-healing/config/{config_type}/history/    - 설정 변경 이력 조회
- GET  /api/self-healing/config/{config_type}/history/{version}/  - 특정 버전 조회
- POST /api/self-healing/config/{config_type}/rollback/   - 특정 버전으로 롤백
- GET  /api/self-healing/config/{config_type}/compare/    - 버전 비교
"""

import logging

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.services.config_history import get_config_history_service
from selfhealing.services.runtime_config import get_runtime_config_manager

logger = logging.getLogger(__name__)


class ConfigHistoryView(APIView):
    """
    설정 변경 이력 조회 API.

    GET /api/self-healing/config/{config_type}/history/

    Query Parameters:
    - limit: 조회할 버전 수 (기본값: 10, 최대: 50)

    Response:
    {
        "status": "success",
        "config_type": "circuit_breaker",
        "count": 5,
        "versions": [
            {
                "version": 5,
                "timestamp": 1703318400.123,
                "changed_by": "admin",
                "reason": "Increase threshold",
                "hash": "abc123..."
            },
            ...
        ]
    }
    """

    permission_classes = [IsViewer]  # Viewer도 조회 가능

    def get(self, request: Request, config_type: str) -> Response:
        """설정 변경 이력 조회."""
        service = get_config_history_service()

        # config_type 유효성 검사
        if not service.is_valid_config_type(config_type):
            return Response(
                {
                    "status": "error",
                    "error": f"Invalid config_type: {config_type}",
                    "valid_types": service.SUPPORTED_CONFIG_TYPES,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # limit 파라미터
        try:
            limit = int(request.query_params.get("limit", 10))
            limit = min(max(limit, 1), 50)  # 1-50 범위로 제한
        except ValueError:
            limit = 10

        history = service.get_history(config_type, limit=limit)
        current = service.get_current_version(config_type)

        return Response(
            {
                "status": "success",
                "config_type": config_type,
                "current_version": current.version if current else None,
                "count": len(history),
                "versions": [
                    {
                        "version": v.version,
                        "timestamp": v.timestamp,
                        "changed_by": v.changed_by,
                        "reason": v.reason,
                        "hash": v.hash,
                    }
                    for v in history
                ],
            },
            status=status.HTTP_200_OK,
        )


class ConfigVersionDetailView(APIView):
    """
    특정 설정 버전 상세 조회 API.

    GET /api/self-healing/config/{config_type}/history/{version}/

    Response:
    {
        "status": "success",
        "version": {
            "version": 5,
            "timestamp": 1703318400.123,
            "config_type": "circuit_breaker",
            "values": {...},
            "changed_by": "admin",
            "reason": "Increase threshold",
            "hash": "abc123..."
        }
    }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, config_type: str, version: int) -> Response:
        """특정 버전 상세 조회."""
        service = get_config_history_service()

        # config_type 유효성 검사
        if not service.is_valid_config_type(config_type):
            return Response(
                {
                    "status": "error",
                    "error": f"Invalid config_type: {config_type}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        version_data = service.get_version(config_type, version)

        if not version_data:
            return Response(
                {
                    "status": "error",
                    "error": f"Version {version} not found for {config_type}",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "success",
                "version": version_data.to_dict(),
            },
            status=status.HTTP_200_OK,
        )


class ConfigRollbackView(APIView):
    """
    설정 롤백 API.

    POST /api/self-healing/config/{config_type}/rollback/

    Request Body:
    {
        "version": 3,
        "reason": "Reverting due to issue"  // optional
    }

    Response:
    {
        "status": "success",
        "message": "Rolled back circuit_breaker to version 3",
        "rolled_back_to": 3,
        "new_version": 6,
        "applied_by": "admin",
        "applied_values": {...}
    }
    """

    permission_classes = [IsSelfHealingAdmin]  # Admin만 롤백 가능

    def post(self, request: Request, config_type: str) -> Response:
        """특정 버전으로 롤백."""
        service = get_config_history_service()

        # config_type 유효성 검사
        if not service.is_valid_config_type(config_type):
            return Response(
                {
                    "status": "error",
                    "error": f"Invalid config_type: {config_type}",
                    "valid_types": service.SUPPORTED_CONFIG_TYPES,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 버전 파라미터 확인
        target_version = request.data.get("version")

        if target_version is None:
            return Response(
                {
                    "status": "error",
                    "error": "version is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_version = int(target_version)
        except (ValueError, TypeError):
            return Response(
                {
                    "status": "error",
                    "error": "version must be a valid integer",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 롤백할 버전이 존재하는지 확인
        target = service.get_version(config_type, target_version)
        if not target:
            return Response(
                {
                    "status": "error",
                    "error": f"Version {target_version} not found for {config_type}",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # 사용자 정보
        username = getattr(request.user, "username", "unknown")
        reason = request.data.get("reason", "")

        # 롤백 수행 (이력 저장)
        rolled_back = service.rollback(
            config_type=config_type,
            target_version=target_version,
            rolled_back_by=username,
        )

        if not rolled_back:
            raise RuntimeError("Failed to rollback - see server logs for details")

        # 실제 설정 적용 - Exception은 exception handler가 처리
        self._apply_config_values(config_type, target.values)

        logger.info(
            f"[ConfigRollback] {config_type} rolled back to v{target_version} "
            f"(new v{rolled_back.version}) by {username}"
        )

        return Response(
            {
                "status": "success",
                "message": f"Rolled back {config_type} to version {target_version}",
                "rolled_back_to": target_version,
                "new_version": rolled_back.version,
                "applied_by": username,
                "applied_values": target.values,
            },
            status=status.HTTP_200_OK,
        )

    def _apply_config_values(self, config_type: str, values: dict) -> None:
        """
        롤백된 설정을 실제로 적용.

        Args:
            config_type: 설정 유형
            values: 적용할 설정 값
        """
        manager = get_runtime_config_manager()

        # config_type별 업데이트 메서드 매핑
        update_methods = {
            "circuit_breaker": manager.update_circuit_breaker_config,
            "dlq": manager.update_dlq_config,
            "retry": manager.update_retry_config,
            "sla": manager.update_sla_config,
            "slo": manager.update_slo_config,
            "rate_limit": manager.update_rate_limit_config,
            "security": manager.update_security_config,
            "idempotency": manager.update_idempotency_config,
            "notification": manager.update_notification_config,
            "forensic": manager.update_forensic_config,
            "metrics": manager.update_metrics_config,
            "error_budget": manager.update_error_budget_config,
        }

        update_method = update_methods.get(config_type)

        if update_method:
            update_method(**values)
            logger.info(f"[ConfigRollback] Applied {config_type} values: {values}")
        else:
            logger.warning(f"[ConfigRollback] No update method for {config_type}")


class ConfigCompareView(APIView):
    """
    설정 버전 비교 API.

    GET /api/self-healing/config/{config_type}/compare/

    Query Parameters:
    - version_a: 비교할 첫 번째 버전
    - version_b: 비교할 두 번째 버전

    Response:
    {
        "status": "success",
        "comparison": {
            "version_a": 3,
            "version_b": 5,
            "config_type": "circuit_breaker",
            "changes": {
                "failure_threshold": {"from": 5, "to": 10},
                "recovery_timeout": {"from": 30, "to": 60}
            }
        }
    }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, config_type: str) -> Response:
        """두 버전 간 차이 비교."""
        service = get_config_history_service()

        # config_type 유효성 검사
        if not service.is_valid_config_type(config_type):
            return Response(
                {
                    "status": "error",
                    "error": f"Invalid config_type: {config_type}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 버전 파라미터
        version_a = request.query_params.get("version_a")
        version_b = request.query_params.get("version_b")

        if not version_a or not version_b:
            return Response(
                {
                    "status": "error",
                    "error": "Both version_a and version_b are required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            version_a = int(version_a)
            version_b = int(version_b)
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "error": "version_a and version_b must be valid integers",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        comparison = service.compare_versions(config_type, version_a, version_b)

        if not comparison:
            return Response(
                {
                    "status": "error",
                    "error": "One or both versions not found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "success",
                "comparison": comparison,
            },
            status=status.HTTP_200_OK,
        )
