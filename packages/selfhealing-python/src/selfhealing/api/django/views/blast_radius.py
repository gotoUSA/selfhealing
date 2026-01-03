"""
Blast Radius DNA API Views

Blast Radius DNA 서비스의 REST API 엔드포인트
"""

import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_blast_radius_service():
    """Blast Radius 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.blast_radius.service import BlastRadiusService
        return BlastRadiusService()
    except ImportError:
        return None


@method_decorator(csrf_exempt, name='dispatch')
class BlastRadiusPolicyView(View):
    """영향 범위 정책 관리 API"""
    
    def get(self, request, stage_name: str = None):
        """정책 조회"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        if stage_name:
            policy = service.get_policy(stage_name)
            if policy:
                return JsonResponse(policy.to_dict())
            return JsonResponse({"error": "Policy not found"}, status=404)
        else:
            return JsonResponse({"message": "Specify stage_name"}, status=400)
    
    def post(self, request, stage_name: str):
        """정책 설정"""
        import json
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        try:
            data = json.loads(request.body) if request.body else {}
            from selfhealing.services.blast_radius.models import BlastRadiusLevel
            
            level = BlastRadiusLevel(data.get("level", "isolated"))
            
            policy = service.set_policy(
                stage_name=stage_name,
                level=level,
                affected_services=data.get("affected_services", []),
                max_affected_percentage=data.get("max_affected_percentage", 10.0),
                auto_isolate=data.get("auto_isolate", True),
            )
            return JsonResponse(policy.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class BlastRadiusDependencyView(View):
    """서비스 의존성 관리 API"""
    
    def get(self, request, service_name: str):
        """의존성 조회"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        deps = service.get_dependencies(service_name)
        return JsonResponse({
            "service": service_name,
            "upstream": [d.to_dict() for d in deps["upstream"]],
            "downstream": [d.to_dict() for d in deps["downstream"]],
        })
    
    def post(self, request):
        """의존성 추가"""
        import json
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            dep = service.add_dependency(
                source_service=data.get("source_service"),
                target_service=data.get("target_service"),
                dependency_type=data.get("dependency_type", "sync"),
                criticality=data.get("criticality", "medium"),
            )
            return JsonResponse(dep.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class BlastRadiusAssessmentView(View):
    """영향 평가 API"""
    
    def get(self, request):
        """평가 이력 조회"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        stage_name = request.GET.get("stage_name")
        limit = int(request.GET.get("limit", "100"))
        
        assessments = service.get_assessments(stage_name=stage_name, limit=limit)
        return JsonResponse({
            "assessments": [a.to_dict() for a in assessments]
        })
    
    def post(self, request):
        """영향 평가 수행"""
        import json
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            assessment = service.assess_impact(
                stage_name=data.get("stage_name"),
                trigger_event=data.get("trigger_event"),
                failing_services=data.get("failing_services", []),
                total_users=data.get("total_users", 1000),
            )
            return JsonResponse(assessment.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class BlastRadiusIsolationView(View):
    """서비스 격리 관리 API"""
    
    def get(self, request):
        """격리된 서비스 조회"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        isolated = service.get_isolated_services()
        return JsonResponse({"isolated_services": isolated})
    
    def post(self, request, service_name: str):
        """서비스 격리"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        if service.isolate_service(service_name):
            return JsonResponse({"message": f"Service {service_name} isolated"})
        return JsonResponse({"message": f"Service {service_name} already isolated"})
    
    def delete(self, request, service_name: str):
        """격리 해제"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        if service.release_isolation(service_name):
            return JsonResponse({"message": f"Service {service_name} isolation released"})
        return JsonResponse({"error": f"Service {service_name} not isolated"}, status=404)


@method_decorator(csrf_exempt, name='dispatch')
class BlastRadiusGraphView(View):
    """의존성 그래프 API"""
    
    def get(self, request):
        """그래프 데이터 조회"""
        service = get_blast_radius_service()
        if not service:
            return JsonResponse({"error": "Blast Radius service not available"}, status=503)
        
        graph = service.build_dependency_graph()
        return JsonResponse(graph)
