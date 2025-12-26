"""
Deployment Policy Views.

배포 정책 관련 API - 동결/Override/해제 결정 기록.

Endpoints:
- GET  /api/self-healing/deployment-policy/verdict/     - Get deployment verdict
- POST /api/self-healing/deployment-policy/acknowledge/ - Acknowledge freeze
- POST /api/self-healing/deployment-policy/override/    - Approve override
- POST /api/self-healing/deployment-policy/lift/        - Lift freeze
- GET  /api/self-healing/deployment-policy/active-override/ - Check active override

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
실제 CI/CD 차단 기능은 없으며, 상태 조회 및 결정 기록만 제공합니다.

FAIL-SAFE DESIGN:
- Error Budget 시스템 장애 시 → 기본값 PROCEED (fail-open)
- 배포를 막는 것보다 시스템 가용성이 더 중요
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.error_budget_service import (
    get_error_budget_service,
    get_failsafe_verdict_response,
    OverrideType,
)

logger = logging.getLogger(__name__)


class DeploymentVerdictView(APIView):
    """
    배포 가능 여부 판정 API.

    GET /api/self-healing/deployment-policy/verdict/

    Returns deployment verdict including:
    - status: proceed, caution, warning, freeze_recommended
    - can_deploy: boolean
    - requires_override: boolean
    - message: Human-readable recommendation
    - allowed_deployment_types: List of allowed deployment types

    Query Parameters:
    - slo_name: SLO name to evaluate (default: "availability")

    Note: This is an ADVISORY endpoint. It does not block deployments.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            slo_name = request.query_params.get("slo_name", "availability")

            service = get_error_budget_service()
            verdict = service.get_deployment_verdict(slo_name)

            # 활성 Override 확인
            active_override = service.check_active_override()

            response_data = verdict.to_dict()

            if active_override:
                response_data["active_override"] = active_override.to_dict()
                response_data["verdict"]["has_active_override"] = True
            else:
                response_data["verdict"]["has_active_override"] = False

            return Response(
                {
                    "status": "success",
                    "data": response_data,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Verdict failed: {e}", exc_info=True)
            # FAIL-SAFE: 시스템 장애 시에도 기본 PROCEED 응답 (200 OK)
            return Response(get_failsafe_verdict_response(str(e)))


class DeploymentFreezeAcknowledgeView(APIView):
    """
    배포 동결 확정 API.

    POST /api/self-healing/deployment-policy/acknowledge/

    Request Body:
    {
        "justification": "Error budget critical, pausing all deployments"
    }

    Records that the operator has acknowledged the freeze recommendation.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            justification = request.data.get("justification", "")

            if not justification:
                return Response(
                    {
                        "status": "error",
                        "error": "justification is required",
                        "message": "동결 확정 사유를 입력해주세요.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            decided_by = getattr(request.user, "username", str(request.user))

            record = service.acknowledge_freeze(
                decided_by=decided_by,
                justification=justification,
            )

            logger.info(f"[DeploymentPolicy] Freeze acknowledged by {decided_by}: {justification}")

            return Response(
                {
                    "status": "success",
                    "message": "배포 동결이 확정되었습니다.",
                    "data": record.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Acknowledge failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeploymentOverrideView(APIView):
    """
    배포 동결 무시(Override) 승인 API.

    POST /api/self-healing/deployment-policy/override/

    Request Body:
    {
        "justification": "Critical security patch for CVE-2024-XXXX",
        "override_type": "security_patch",  // hotfix, security_patch, executive_approval, rollback
        "deployment_id": "deploy-abc123",   // optional
        "deployment_name": "payment-service v1.2.3",  // optional
        "expires_hours": 4  // optional, default 4
    }

    IMPORTANT: This is a governance record. It does NOT automatically
    enable deployments. CI/CD systems should check for active overrides.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            justification = request.data.get("justification", "")
            override_type_str = request.data.get("override_type", "")
            deployment_id = request.data.get("deployment_id")
            deployment_name = request.data.get("deployment_name")
            expires_hours = int(request.data.get("expires_hours", 4))

            # Validation
            if not justification:
                return Response(
                    {
                        "status": "error",
                        "error": "justification is required",
                        "message": "Override 사유를 입력해주세요.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not override_type_str:
                return Response(
                    {
                        "status": "error",
                        "error": "override_type is required",
                        "message": "Override 유형을 선택해주세요.",
                        "valid_types": [t.value for t in OverrideType],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                override_type = OverrideType(override_type_str)
            except ValueError:
                return Response(
                    {
                        "status": "error",
                        "error": f"Invalid override_type: {override_type_str}",
                        "valid_types": [t.value for t in OverrideType],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            decided_by = getattr(request.user, "username", str(request.user))

            record = service.approve_override(
                decided_by=decided_by,
                justification=justification,
                override_type=override_type,
                deployment_id=deployment_id,
                deployment_name=deployment_name,
                expires_hours=expires_hours,
            )

            logger.warning(
                f"[DeploymentPolicy] Override approved by {decided_by}: "
                f"type={override_type.value}, deployment={deployment_name}"
            )

            return Response(
                {
                    "status": "success",
                    "message": "배포 동결 무시가 승인되었습니다. 이 결정은 감사 로그에 기록됩니다.",
                    "warning": "Error Budget이 낮은 상태에서의 배포는 추가 장애 위험이 있습니다.",
                    "data": record.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Override failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeploymentFreezeLiftView(APIView):
    """
    배포 동결 해제 API.

    POST /api/self-healing/deployment-policy/lift/

    Request Body:
    {
        "justification": "Error budget recovered to healthy level"
    }

    Records that the deployment freeze has been lifted.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            justification = request.data.get("justification", "")

            if not justification:
                return Response(
                    {
                        "status": "error",
                        "error": "justification is required",
                        "message": "동결 해제 사유를 입력해주세요.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            decided_by = getattr(request.user, "username", str(request.user))

            record = service.lift_freeze(
                decided_by=decided_by,
                justification=justification,
            )

            logger.info(f"[DeploymentPolicy] Freeze lifted by {decided_by}: {justification}")

            return Response(
                {
                    "status": "success",
                    "message": "배포 동결이 해제되었습니다. 일반 배포가 가능합니다.",
                    "data": record.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Lift failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ActiveOverrideView(APIView):
    """
    활성 Override 조회 API.

    GET /api/self-healing/deployment-policy/active-override/

    Returns the currently active override, if any.
    CI/CD systems can use this to check if a deployment is allowed
    despite the freeze recommendation.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            service = get_error_budget_service()
            active_override = service.check_active_override()

            if active_override:
                return Response(
                    {
                        "status": "success",
                        "has_active_override": True,
                        "data": active_override.to_dict(),
                        "timestamp": timezone.now().isoformat(),
                    }
                )
            else:
                return Response(
                    {
                        "status": "success",
                        "has_active_override": False,
                        "data": None,
                        "timestamp": timezone.now().isoformat(),
                    }
                )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Active override check failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


__all__ = [
    "DeploymentVerdictView",
    "DeploymentFreezeAcknowledgeView",
    "DeploymentOverrideView",
    "DeploymentFreezeLiftView",
    "ActiveOverrideView",
]
