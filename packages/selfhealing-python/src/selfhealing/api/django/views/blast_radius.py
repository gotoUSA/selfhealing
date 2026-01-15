"""
Blast Radius DNA API Views

Blast Radius DNA 서비스의 REST API 엔드포인트
"""

import logging
from typing import List

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import BasePermission

from selfhealing.api.django.permissions import IsViewer, IsOperator, IsSelfHealingAdmin

logger = logging.getLogger(__name__)


def get_blast_radius_service():
    """Blast Radius 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.blast_radius.service import BlastRadiusService

        return BlastRadiusService()
    except ImportError:
        return None


class BlastRadiusPolicyView(APIView):
    """
    영향 범위 정책 관리 API

    GET  /api/self-healing/blast-radius/policy/<stage_name>/ - 정책 조회 (Viewer)
    POST /api/self-healing/blast-radius/policy/<stage_name>/ - 정책 설정 (Admin)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request, stage_name: str = None):
        """정책 조회"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if stage_name:
            policy = service.get_policy(stage_name)
            if policy:
                return Response(policy.to_dict())
            return Response(
                {"error": "Policy not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        else:
            return Response(
                {"message": "Specify stage_name"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def post(self, request, stage_name: str):
        """정책 설정"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            data = request.data if request.data else {}
            from selfhealing.services.blast_radius.models import BlastRadiusLevel

            level = BlastRadiusLevel(data.get("level", "isolated"))

            policy = service.set_policy(
                stage_name=stage_name,
                level=level,
                affected_services=data.get("affected_services", []),
                max_affected_percentage=data.get("max_affected_percentage", 10.0),
                auto_isolate=data.get("auto_isolate", True),
            )
            return Response(policy.to_dict(), status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class BlastRadiusDependencyView(APIView):
    """
    서비스 의존성 관리 API

    GET  /api/self-healing/blast-radius/dependency/<service_name>/ - 의존성 조회 (Viewer)
    POST /api/self-healing/blast-radius/dependency/                - 의존성 추가 (Admin)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request, service_name: str):
        """의존성 조회"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        deps = service.get_dependencies(service_name)
        return Response(
            {
                "service": service_name,
                "upstream": [d.to_dict() for d in deps["upstream"]],
                "downstream": [d.to_dict() for d in deps["downstream"]],
            }
        )

    def post(self, request):
        """의존성 추가"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            data = request.data
            dep = service.add_dependency(
                source_service=data.get("source_service"),
                target_service=data.get("target_service"),
                dependency_type=data.get("dependency_type", "sync"),
                criticality=data.get("criticality", "medium"),
            )
            return Response(dep.to_dict(), status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class BlastRadiusAssessmentView(APIView):
    """
    영향 평가 API

    GET  /api/self-healing/blast-radius/assessment/ - 평가 이력 조회 (Viewer)
    POST /api/self-healing/blast-radius/assessment/ - 영향 평가 수행 (Operator)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsOperator()]

    def get(self, request):
        """평가 이력 조회"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        stage_name = request.query_params.get("stage_name")
        limit = int(request.query_params.get("limit", "100"))

        assessments = service.get_assessments(stage_name=stage_name, limit=limit)
        return Response({"assessments": [a.to_dict() for a in assessments]})

    def post(self, request):
        """영향 평가 수행"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            data = request.data
            assessment = service.assess_impact(
                stage_name=data.get("stage_name"),
                trigger_event=data.get("trigger_event"),
                failing_services=data.get("failing_services", []),
                total_users=data.get("total_users", 1000),
            )
            return Response(assessment.to_dict(), status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class BlastRadiusIsolationView(APIView):
    """
    서비스 격리 관리 API

    GET    /api/self-healing/blast-radius/isolation/               - 격리된 서비스 조회 (Viewer)
    POST   /api/self-healing/blast-radius/isolation/<service_name>/ - 서비스 격리 (Admin)
    DELETE /api/self-healing/blast-radius/isolation/<service_name>/ - 격리 해제 (Admin)
    """

    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request):
        """격리된 서비스 조회"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        isolated = service.get_isolated_services()
        return Response({"isolated_services": isolated})

    def post(self, request, service_name: str):
        """서비스 격리"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.isolate_service(service_name):
            return Response({"message": f"Service {service_name} isolated"})
        return Response({"message": f"Service {service_name} already isolated"})

    def delete(self, request, service_name: str):
        """격리 해제"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.release_isolation(service_name):
            return Response({"message": f"Service {service_name} isolation released"})
        return Response(
            {"error": f"Service {service_name} not isolated"},
            status=status.HTTP_404_NOT_FOUND,
        )


class BlastRadiusGraphView(APIView):
    """
    의존성 그래프 API

    GET /api/self-healing/blast-radius/graph/ - 그래프 데이터 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        """그래프 데이터 조회"""
        service = get_blast_radius_service()
        if not service:
            return Response(
                {"error": "Blast Radius service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        graph = service.build_dependency_graph()
        return Response(graph)
