"""
Compliance DNA API Views

Compliance DNA 서비스의 REST API 엔드포인트
"""

import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_compliance_service():
    """Compliance 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.compliance.service import ComplianceService

        return ComplianceService()
    except ImportError:
        return None


@method_decorator(csrf_exempt, name="dispatch")
class ComplianceStandardsView(View):
    """규정 표준 관리 API"""

    def get(self, request, stage_name: str = None):
        """Stage별 규정 상태 조회"""
        service = get_compliance_service()
        if not service:
            return JsonResponse({"error": "Compliance service not available"}, status=503)

        if stage_name:
            status = service.get_compliance_status(stage_name)
            return JsonResponse(status)
        else:
            # 모든 표준 목록
            from selfhealing.services.compliance.models import ComplianceStandard

            standards = [s.value for s in ComplianceStandard]
            return JsonResponse({"available_standards": standards})

    def post(self, request, stage_name: str):
        """Stage에 규정 표준 설정"""
        import json

        service = get_compliance_service()
        if not service:
            return JsonResponse({"error": "Compliance service not available"}, status=503)

        try:
            data = json.loads(request.body)
            from selfhealing.services.compliance.models import ComplianceStandard

            standards = [ComplianceStandard(s) for s in data.get("standards", ["DORA_2025"])]
            service.set_stage_standards(stage_name, standards)
            return JsonResponse({"stage_name": stage_name, "standards": [s.value for s in standards]}, status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name="dispatch")
class ComplianceCheckView(View):
    """규정 검사 실행 API"""

    def post(self, request, stage_name: str):
        """규정 검사 실행"""
        import json

        service = get_compliance_service()
        if not service:
            return JsonResponse({"error": "Compliance service not available"}, status=503)

        try:
            data = json.loads(request.body) if request.body else {}
            from selfhealing.services.compliance.models import ComplianceStandard

            standards = None
            if "standards" in data:
                standards = [ComplianceStandard(s) for s in data["standards"]]

            report = service.run_all_checks(stage_name, standards)
            return JsonResponse(report.to_dict())
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name="dispatch")
class ComplianceViolationView(View):
    """규정 위반 관리 API"""

    def get(self, request):
        """위반 목록 조회"""
        service = get_compliance_service()
        if not service:
            return JsonResponse({"error": "Compliance service not available"}, status=503)

        stage_name = request.GET.get("stage_name")
        standard = request.GET.get("standard")
        unresolved = request.GET.get("unresolved", "false").lower() == "true"

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
        return JsonResponse({"violations": [v.to_dict() for v in violations]})

    def post(self, request, violation_id: str):
        """위반 해결 처리"""
        service = get_compliance_service()
        if not service:
            return JsonResponse({"error": "Compliance service not available"}, status=503)

        if service.resolve_violation(violation_id):
            return JsonResponse({"message": "Violation resolved"})
        return JsonResponse({"error": "Violation not found"}, status=404)


@method_decorator(csrf_exempt, name="dispatch")
class ComplianceReportView(View):
    """규정 준수 리포트 API"""

    def get(self, request):
        """리포트 목록 조회"""
        service = get_compliance_service()
        if not service:
            return JsonResponse({"error": "Compliance service not available"}, status=503)

        stage_name = request.GET.get("stage_name")
        limit = int(request.GET.get("limit", "100"))

        reports = service.get_reports(stage_name=stage_name, limit=limit)
        return JsonResponse({"reports": [r.to_dict() for r in reports]})
