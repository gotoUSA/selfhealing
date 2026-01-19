"""
Chaos Engineering Report Views.

API views for resilience reports and grade history.
"""

import logging
from datetime import datetime

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer, IsSelfHealingAdmin
from selfhealing.api.django.serializers.chaos import (
    DryRunAnalysisRequestSerializer,
)

logger = logging.getLogger(__name__)


class ReportListView(APIView):
    """
    API for resilience reports.
    
    GET: List reports (Viewer)
    """
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """List resilience reports."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        
        days = int(request.query_params.get("days", "30"))
        grade_filter = request.query_params.get("grade")
        
        reports = generator.get_reports(days=days, grade_filter=grade_filter)
        
        return Response({
            "status": "success",
            "data": [r.to_dict() for r in reports],
            "count": len(reports),
        })


class ReportDetailView(APIView):
    """
    API for individual report.
    
    GET: Get report by ID or date (Viewer)
    """
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request, report_id: str) -> Response:
        """Get report details."""
        from rest_framework import status
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        
        # Try as date first (YYYY-MM-DD)
        if len(report_id) == 10 and "-" in report_id:
            report = generator.get_report_by_date(report_id)
        else:
            report = generator.get_report(report_id)
        
        if not report:
            return Response(
                {"status": "error", "message": "Report not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        return Response({
            "status": "success",
            "data": report.to_dict(),
        })


class ReportGenerateView(APIView):
    """
    API for generating reports on demand.
    
    POST: Generate report now (Admin)
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """Generate report now."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        
        # Optional date parameter
        date_str = request.data.get("date")
        if date_str:
            report_date = datetime.fromisoformat(date_str)
        else:
            report_date = None
        
        report = generator.generate_daily_report(report_date=report_date)
        
        logger.info(f"[ChaosAPI] Report generated: {report.report_id} by {request.user}")
        
        return Response({
            "status": "success",
            "data": report.to_dict(),
        })


class GradeHistoryView(APIView):
    """
    API for grade history.
    
    GET: Get grade history for trending (Viewer)
    """
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """Get grade history."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        days = int(request.query_params.get("days", "30"))
        
        history = generator.get_grade_history(days=days)
        
        return Response({
            "status": "success",
            "data": history,
        })


class DryRunAnalysisView(APIView):
    """
    API for Dry Run analysis with impact prediction.

    Dry Run 모드에서 실험의 예상 결과와 영향 범위를 분석합니다.
    실제 장애 주입 없이 안전하게 실험 계획을 검증할 수 있습니다.

    POST: Analyze experiment with predictions (Viewer - read-only analysis)
    """

    permission_classes = [IsViewer]

    def post(self, request: Request) -> Response:
        """
        Analyze experiment and return predictions.

        Request body:
        {
            "target_service": "payment-api",
            "experiment_type": "latency_injection",
            "config": {"latency_ms": 500},
            "include_blast_radius": true
        }

        Response:
        {
            "status": "success",
            "data": {
                "target_service": "payment-api",
                "experiment_type": "latency_injection",
                "predicted_outcome": {...},
                "blast_radius_analysis": {...},
                "service_impacts": [...],
                "experiment_allowed": true,
                "requires_approval": false,
                "approval_level": "",
                "overall_risk_level": "medium"
            }
        }
        """
        from rest_framework import status
        
        serializer = DryRunAnalysisRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_service = serializer.validated_data["target_service"]
        experiment_type = serializer.validated_data["experiment_type"]
        config = serializer.validated_data.get("config", {})
        include_blast_radius = serializer.validated_data.get("include_blast_radius", True)

        try:
            from selfhealing.services.chaos.impact_predictor import get_impact_predictor
            from selfhealing.services.chaos.blast_radius_analyzer import get_blast_radius_analyzer

            # 1. Impact Prediction
            predictor = get_impact_predictor()
            predicted_outcome = predictor.predict_outcome(
                experiment_type=experiment_type,
                target_service=target_service,
                config=config,
            )

            # 2. Service Impact Analysis
            service_impacts = predictor.predict_service_impact(
                target_service=target_service,
                experiment_type=experiment_type,
                config=config,
            )

            # 3. Blast Radius Analysis (optional)
            blast_radius_analysis = None
            if include_blast_radius:
                analyzer = get_blast_radius_analyzer()
                blast_radius_analysis = analyzer.analyze(
                    target_service=target_service,
                    experiment_type=experiment_type,
                )

            # 4. Determine overall risk level and approval requirements
            requires_approval = predicted_outcome.requires_approval
            approval_level = predicted_outcome.approval_reason
            experiment_allowed = True

            if blast_radius_analysis:
                if blast_radius_analysis.requires_approval:
                    requires_approval = True
                    approval_level = blast_radius_analysis.approval_level
                if not blast_radius_analysis.experiment_allowed:
                    experiment_allowed = False

            # 5. Calculate overall risk level
            risk_score = blast_radius_analysis.risk_score if blast_radius_analysis else 0.5
            overall_risk_level = self._calculate_risk_level(
                risk_score=risk_score,
                confidence_score=predicted_outcome.confidence_score,
            )

            logger.info(
                f"[ChaosAPI] Dry run analysis completed by {request.user}: "
                f"target={target_service}, type={experiment_type}, "
                f"risk={overall_risk_level}, allowed={experiment_allowed}"
            )

            response_data = {
                "target_service": target_service,
                "experiment_type": experiment_type,
                "predicted_outcome": predicted_outcome.to_dict(),
                "blast_radius_analysis": (
                    blast_radius_analysis.to_dict() if blast_radius_analysis else None
                ),
                "service_impacts": [s.to_dict() for s in service_impacts],
                "experiment_allowed": experiment_allowed,
                "requires_approval": requires_approval,
                "approval_level": approval_level,
                "overall_risk_level": overall_risk_level,
            }

            return Response({
                "status": "success",
                "data": response_data,
            })

        except Exception as e:
            logger.error(f"[ChaosAPI] Dry run analysis failed: {e}")
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _calculate_risk_level(
        self,
        risk_score: float,
        confidence_score: float,
    ) -> str:
        """Calculate overall risk level."""
        # Lower confidence = higher uncertainty = higher effective risk
        effective_risk = risk_score + (1 - confidence_score) * 0.2

        if effective_risk >= 0.75:
            return "critical"
        elif effective_risk >= 0.5:
            return "high"
        elif effective_risk >= 0.25:
            return "medium"
        else:
            return "low"
