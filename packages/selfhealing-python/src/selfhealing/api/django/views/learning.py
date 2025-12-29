"""
Self-Learning DNA API Views

Self-Learning DNA 서비스의 REST API 엔드포인트
"""

import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_learning_service():
    """Learning 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.learning import LearningService
        return LearningService()
    except ImportError:
        return None


@method_decorator(csrf_exempt, name='dispatch')
class LearningSessionView(View):
    """학습 세션 관리 API"""
    
    def post(self, request, action: str = None):
        """세션 시작/종료"""
        import json
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        try:
            data = json.loads(request.body) if request.body else {}
            
            if action == "start":
                stage_name = data.get("stage_name", "default")
                session = service.start_session(stage_name)
                return JsonResponse(session.to_dict(), status=201)
            
            elif action == "end":
                session_id = data.get("session_id")
                if not session_id:
                    return JsonResponse({"error": "session_id required"}, status=400)
                session = service.end_session(session_id)
                if session:
                    return JsonResponse(session.to_dict())
                return JsonResponse({"error": "Session not found"}, status=404)
            
            else:
                return JsonResponse({"error": "Invalid action"}, status=400)
                
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class LearningPatternView(View):
    """학습 패턴 API"""
    
    def get(self, request):
        """패턴 조회"""
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        pattern_type = request.GET.get("type")
        min_confidence = float(request.GET.get("min_confidence", "0.0"))
        
        from selfhealing.services.learning.models import PatternType
        pt = None
        if pattern_type:
            try:
                pt = PatternType(pattern_type)
            except ValueError:
                pass
        
        patterns = service.get_patterns(pattern_type=pt, min_confidence=min_confidence)
        return JsonResponse({
            "patterns": [p.to_dict() for p in patterns]
        })
    
    def post(self, request):
        """패턴 학습"""
        import json
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            from selfhealing.services.learning.models import PatternType
            
            pattern = service.learn_pattern(
                pattern_type=PatternType(data.get("pattern_type", "failure")),
                name=data.get("name"),
                description=data.get("description", ""),
                features=data.get("features", {}),
                confidence=data.get("confidence", 0.8),
                session_id=data.get("session_id"),
                metadata=data.get("metadata", {}),
            )
            return JsonResponse(pattern.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class LearningSuggestionView(View):
    """최적화 제안 API"""
    
    def get(self, request):
        """제안 조회"""
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        stage_name = request.GET.get("stage_name")
        unapplied = request.GET.get("unapplied", "false").lower() == "true"
        
        suggestions = service.get_suggestions(
            stage_name=stage_name,
            unapplied_only=unapplied,
        )
        return JsonResponse({
            "suggestions": [s.to_dict() for s in suggestions]
        })
    
    def post(self, request, suggestion_id: str):
        """제안 적용"""
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        if service.apply_suggestion(suggestion_id):
            return JsonResponse({"message": "Suggestion applied"})
        return JsonResponse({"error": "Suggestion not found"}, status=404)


@method_decorator(csrf_exempt, name='dispatch')
class LearningMetricView(View):
    """성능 메트릭 API"""
    
    def post(self, request):
        """메트릭 기록"""
        import json
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            metric = service.record_metric(
                metric_name=data.get("metric_name"),
                value=float(data.get("value")),
                stage_name=data.get("stage_name", ""),
                unit=data.get("unit", ""),
                tags=data.get("tags", {}),
            )
            return JsonResponse(metric.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class LearningInsightsView(View):
    """Cross-Stage 인사이트 API"""
    
    def get(self, request):
        """인사이트 조회"""
        service = get_learning_service()
        if not service:
            return JsonResponse({"error": "Learning service not available"}, status=503)
        
        insights = service.get_cross_stage_insights()
        return JsonResponse(insights)
