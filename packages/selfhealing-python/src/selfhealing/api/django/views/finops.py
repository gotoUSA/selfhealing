"""
FinOps DNA API Views

FinOps DNA 서비스의 REST API 엔드포인트
"""

from decimal import Decimal
import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_finops_service():
    """FinOps 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.finops.service import FinOpsService
        return FinOpsService()
    except ImportError:
        return None


@method_decorator(csrf_exempt, name='dispatch')
class FinOpsBudgetView(View):
    """FinOps 예산 관리 API"""
    
    def get(self, request, stage_name: str = None):
        """예산 조회"""
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        if stage_name:
            budget = service.get_budget(stage_name)
            if budget:
                return JsonResponse(budget.to_dict())
            return JsonResponse({"error": "Budget not found"}, status=404)
        else:
            budgets = service.get_all_budgets()
            return JsonResponse({"budgets": budgets})
    
    def post(self, request, stage_name: str):
        """예산 설정"""
        import json
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            budget = service.set_budget(
                stage_name=stage_name,
                max_budget=Decimal(str(data.get("max_budget", "10.00"))),
                alert_threshold=data.get("alert_threshold", 0.8),
                hard_limit=data.get("hard_limit", True),
                reset_period=data.get("reset_period", "daily"),
            )
            return JsonResponse(budget.to_dict(), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)
    
    def delete(self, request, stage_name: str):
        """예산 리셋"""
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        if service.reset_budget(stage_name):
            return JsonResponse({"message": "Budget reset successfully"})
        return JsonResponse({"error": "Budget not found"}, status=404)


@method_decorator(csrf_exempt, name='dispatch')
class FinOpsCostView(View):
    """FinOps 비용 기록 API"""
    
    def post(self, request):
        """비용 기록"""
        import json
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        try:
            data = json.loads(request.body)
            cost = data.get("cost")
            record = service.record_cost(
                operation=data.get("operation"),
                stage_name=data.get("stage_name"),
                cost=Decimal(str(cost)) if cost else None,
                success=data.get("success", True),
                metadata=data.get("metadata", {}),
            )
            return JsonResponse(record.to_dict(), status=201)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class FinOpsReportView(View):
    """FinOps 리포트 API"""
    
    def get(self, request):
        """리포트 생성"""
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        period = request.GET.get("period", "daily")
        stage_name = request.GET.get("stage_name")
        
        report = service.generate_report(period=period, stage_name=stage_name)
        return JsonResponse(report.to_dict())


@method_decorator(csrf_exempt, name='dispatch')
class FinOpsAlertsView(View):
    """FinOps 알림 API"""
    
    def get(self, request):
        """알림 조회"""
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        stage_name = request.GET.get("stage_name")
        unacknowledged = request.GET.get("unacknowledged", "false").lower() == "true"
        
        alerts = service.get_alerts(
            stage_name=stage_name,
            unacknowledged_only=unacknowledged,
        )
        return JsonResponse({
            "alerts": [a.to_dict() for a in alerts]
        })
    
    def post(self, request, alert_index: int):
        """알림 확인 처리"""
        service = get_finops_service()
        if not service:
            return JsonResponse({"error": "FinOps service not available"}, status=503)
        
        if service.acknowledge_alert(alert_index):
            return JsonResponse({"message": "Alert acknowledged"})
        return JsonResponse({"error": "Alert not found"}, status=404)
