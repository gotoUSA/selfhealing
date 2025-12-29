"""
Rollback DNA API Views

Rollback DNA 서비스의 REST API 엔드포인트
"""

import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_rollback_service():
    """Rollback 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.rollback import RollbackService
        return RollbackService()
    except ImportError:
        return None


@method_decorator(csrf_exempt, name='dispatch')
class RollbackPolicyView(View):
    """롤백 정책 관리 API"""
    
    def get(self, request, stage_name: str = None):
        """정책 조회"""
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        if stage_name:
            policy = service.get_policy(stage_name)
            if policy:
                return JsonResponse(policy.to_dict())
            return JsonResponse({"error": "Policy not found"}, status=404)
        else:
            # 모든 정책 조회 (구현 필요 시 확장)
            return JsonResponse({"message": "Specify stage_name"}, status=400)
    
    def post(self, request, stage_name: str):
        """정책 설정"""
        import json
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        try:
            data = json.loads(request.body) if request.body else {}
            from selfhealing.services.rollback.models import RollbackStrategy
            
            strategy = RollbackStrategy(data.get("strategy", "automatic"))
            
            policy = service.set_policy(
                stage_name=stage_name,
                strategy=strategy,
                timeout_seconds=data.get("timeout_seconds", 120),
                max_retries=data.get("max_retries", 3),
                require_approval=data.get("require_approval", False),
            )
            return JsonResponse(policy.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class RollbackRequestView(View):
    """롤백 요청 API"""
    
    def get(self, request, request_id: str = None):
        """롤백 결과 조회"""
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        if request_id:
            result = service.get_result(request_id)
            if result:
                return JsonResponse(result.to_dict())
            return JsonResponse({"error": "Request not found"}, status=404)
        else:
            # 대기 중인 요청 조회
            stage_name = request.GET.get("stage_name")
            pending = service.get_pending_requests(stage_name)
            return JsonResponse({
                "pending_requests": [r.to_dict() for r in pending]
            })
    
    def post(self, request):
        """롤백 요청 생성"""
        import json
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            rollback_request = service.request_rollback(
                stage_name=data.get("stage_name"),
                reason=data.get("reason"),
                triggered_by=data.get("triggered_by", "user"),
                source_version=data.get("source_version", ""),
                target_version=data.get("target_version", ""),
                metadata=data.get("metadata", {}),
            )
            return JsonResponse(rollback_request.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class RollbackExecuteView(View):
    """롤백 실행 API"""
    
    def post(self, request, request_id: str):
        """롤백 실행"""
        import json
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        try:
            data = json.loads(request.body) if request.body else {}
            components = data.get("components")
            
            result = service.execute_rollback(request_id, components)
            return JsonResponse(result.to_dict())
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class RollbackCancelView(View):
    """롤백 취소 API"""
    
    def post(self, request, request_id: str):
        """롤백 취소"""
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        if service.cancel_rollback(request_id):
            return JsonResponse({"message": "Rollback cancelled"})
        return JsonResponse({"error": "Cannot cancel rollback"}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class RollbackHistoryView(View):
    """롤백 이력 API"""
    
    def get(self, request):
        """이력 조회"""
        service = get_rollback_service()
        if not service:
            return JsonResponse({"error": "Rollback service not available"}, status=503)
        
        stage_name = request.GET.get("stage_name")
        limit = int(request.GET.get("limit", "100"))
        
        history = service.get_history(stage_name=stage_name, limit=limit)
        return JsonResponse({"history": history})
