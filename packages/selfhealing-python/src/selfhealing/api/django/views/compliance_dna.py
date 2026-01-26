"""
Compliance DNA API Views

Compliance DNA 서비스의 REST API 엔드포인트
"""

import logging
from typing import List

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import BasePermission

from selfhealing.api.django.permissions import IsViewer, IsOperator, IsSelfHealingAdmin

logger = logging.getLogger(__name__)


def get_compliance_service():
    """Compliance 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.compliance.service import ComplianceService

        return ComplianceService()
    except ImportError:
        return None


class ComplianceStandardsView(APIView):
    """
    규정 표준 관리 API

    GET  /api/self-healing/compliance/standards/              - 표준 목록 조회 (Viewer)
    GET  /api/self-healing/compliance/standards/<stage_name>/ - Stage별 규정 상태 조회 (Viewer)
    POST /api/self-healing/compliance/standards/<stage_name>/ - Stage에 규정 표준 설정 (Admin)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request, stage_name: str = None):
        """Stage별 규정 상태 조회"""
        service = get_compliance_service()
        if not service:
            return Response(
                {"error": "Compliance service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if stage_name:
            compliance_status = service.get_compliance_status(stage_name)
            return Response(compliance_status)
        else:
            # 모든 표준 목록
            from selfhealing.services.compliance.models import ComplianceStandard

            standards = [s.value for s in ComplianceStandard]
            return Response({"available_standards": standards})

    def post(self, request, stage_name: str):
        """Stage에 규정 표준 설정"""
        service = get_compliance_service()
        if not service:
            return Response(
                {"error": "Compliance service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data
        from selfhealing.services.compliance.models import ComplianceStandard

        standards = [
            ComplianceStandard(s) for s in data.get("standards", ["DORA_2025"])
        ]
        service.set_stage_standards(stage_name, standards)
        return Response(
            {"stage_name": stage_name, "standards": [s.value for s in standards]},
            status=status.HTTP_201_CREATED,
        )


class ComplianceCheckView(APIView):
    """
    규정 검사 실행 API

    POST /api/self-healing/compliance/check/<stage_name>/ - 규정 검사 실행 (Operator)
    """

    permission_classes = [IsOperator]

    def post(self, request, stage_name: str):
        """규정 검사 실행"""
        service = get_compliance_service()
        if not service:
            return Response(
                {"error": "Compliance service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data if request.data else {}
        from selfhealing.services.compliance.models import ComplianceStandard

        standards = None
        if "standards" in data:
            standards = [ComplianceStandard(s) for s in data["standards"]]

        report = service.run_all_checks(stage_name, standards)
        return Response(report.to_dict())


class ComplianceViolationView(APIView):
    """
    규정 위반 관리 API

    GET  /api/self-healing/compliance/violation/               - 위반 목록 조회 (Viewer)
    POST /api/self-healing/compliance/violation/<violation_id>/ - 위반 해결 처리 (Operator)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsOperator()]

    def get(self, request):
        """위반 목록 조회"""
        service = get_compliance_service()
        if not service:
            return Response(
                {"error": "Compliance service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        stage_name = request.query_params.get("stage_name")
        standard = request.query_params.get("standard")
        unresolved = request.query_params.get("unresolved", "false").lower() == "true"

        from selfhealing.services.compliance.models import ComplianceStandard

        std = None
        if standard:
            try:
                std = ComplianceStandard(standard)
            except ValueError:
                pass

        violations = service.get_violations(
            stage_name=stage_name,
            standard=std,
            unresolved_only=unresolved,
        )
        return Response({"violations": [v.to_dict() for v in violations]})

    def post(self, request, violation_id: str):
        """위반 해결 처리"""
        service = get_compliance_service()
        if not service:
            return Response(
                {"error": "Compliance service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.resolve_violation(violation_id):
            return Response({"message": "Violation resolved"})
        return Response(
            {"error": "Violation not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


class ComplianceReportView(APIView):
    """
    규정 준수 리포트 API

    GET /api/self-healing/compliance/report/ - 리포트 목록 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        """리포트 목록 조회"""
        service = get_compliance_service()
        if not service:
            return Response(
                {"error": "Compliance service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        stage_name = request.query_params.get("stage_name")
        limit = int(request.query_params.get("limit", "100"))

        reports = service.get_reports(stage_name=stage_name, limit=limit)
        return Response({"reports": [r.to_dict() for r in reports]})
