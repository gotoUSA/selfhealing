"""
Self-Learning DNA API Views

Self-Learning DNA 서비스의 REST API 엔드포인트
"""

import structlog
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsOperator, IsViewer

logger = structlog.get_logger()


def get_learning_service():
    """Learning 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.learning.service import LearningService

        return LearningService()
    except ImportError:
        return None


class LearningSessionView(APIView):
    """
    학습 세션 관리 API

    POST /api/self-healing/learning/session/<action>/ - 세션 시작/종료 (Operator)
    """

    permission_classes = [IsOperator]

    def post(self, request, action: str = None):
        """세션 시작/종료"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data if request.data else {}

        if action == "start":
            stage_name = data.get("stage_name", "default")
            session = service.start_session(stage_name)
            return Response(session.to_dict(), status=status.HTTP_201_CREATED)

        elif action == "end":
            session_id = data.get("session_id")
            if not session_id:
                raise ValueError("session_id required")
            session = service.end_session(session_id)
            if session:
                return Response(session.to_dict())
            from django.http import Http404

            raise Http404("Session not found")

        else:
            raise ValueError("Invalid action")


class LearningPatternView(APIView):
    """
    학습 패턴 API

    GET  /api/self-healing/learning/pattern/ - 패턴 조회 (Viewer)
    POST /api/self-healing/learning/pattern/ - 패턴 학습 (Operator)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsOperator()]

    def get(self, request):
        """패턴 조회"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        pattern_type = request.query_params.get("type")
        min_confidence = float(request.query_params.get("min_confidence", "0.0"))

        from selfhealing.services.learning.models import PatternType

        pt = None
        if pattern_type:
            try:
                pt = PatternType(pattern_type)
            except ValueError:
                pass

        patterns = service.get_patterns(pattern_type=pt, min_confidence=min_confidence)
        return Response({"patterns": [p.to_dict() for p in patterns]})

    def post(self, request):
        """패턴 학습"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data
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
        return Response(pattern.to_dict(), status=status.HTTP_201_CREATED)


class LearningSuggestionView(APIView):
    """
    최적화 제안 API

    GET  /api/self-healing/learning/suggestion/                 - 제안 조회 (Viewer)
    POST /api/self-healing/learning/suggestion/<suggestion_id>/ - 제안 적용 (Operator)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsOperator()]

    def get(self, request):
        """제안 조회"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        stage_name = request.query_params.get("stage_name")
        unapplied = request.query_params.get("unapplied", "false").lower() == "true"

        suggestions = service.get_suggestions(
            stage_name=stage_name,
            unapplied_only=unapplied,
        )
        return Response({"suggestions": [s.to_dict() for s in suggestions]})

    def post(self, request, suggestion_id: str):
        """제안 적용"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.apply_suggestion(suggestion_id):
            return Response({"message": "Suggestion applied"})
        return Response(
            {"error": "Suggestion not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


class LearningMetricView(APIView):
    """
    성능 메트릭 API

    POST /api/self-healing/learning/metric/ - 메트릭 기록 (Operator)
    """

    permission_classes = [IsOperator]

    def post(self, request):
        """메트릭 기록"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data
        metric = service.record_metric(
            metric_name=data.get("metric_name"),
            value=float(data.get("value")),
            stage_name=data.get("stage_name", ""),
            unit=data.get("unit", ""),
            tags=data.get("tags", {}),
        )
        return Response(metric.to_dict(), status=status.HTTP_201_CREATED)


class LearningInsightsView(APIView):
    """
    Cross-Stage 인사이트 API

    GET /api/self-healing/learning/insights/ - 인사이트 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        """인사이트 조회"""
        service = get_learning_service()
        if not service:
            return Response(
                {"error": "Learning service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        insights = service.get_cross_stage_insights()
        return Response(insights)
