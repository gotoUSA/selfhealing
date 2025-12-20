"""
Chaos Engineering API Views

Full API control for the Chaos Engineering system.
All settings and operations are controllable via these endpoints.

Endpoints:
- /chaos/config/safety-guard/ - Safety guard configuration
- /chaos/config/blast-radius/ - Blast radius policy
- /chaos/config/scheduler/ - Scheduler configuration
- /chaos/config/reports/ - Report configuration
- /chaos/schedules/ - Scheduled experiments CRUD
- /chaos/schedules/{id}/approve/ - Approve/deny experiments
- /chaos/schedules/{id}/execute/ - Execute immediately
- /chaos/kill-switch/ - Kill switch controls
- /chaos/safety-check/ - Run safety checks
- /chaos/blast-radius/check/ - Check blast radius
- /chaos/reports/ - Get resilience reports
- /chaos/reports/generate/ - Generate report now
"""

import logging
from typing import Any, Dict

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers.chaos import (
    SafetyGuardConfigSerializer,
    BlastRadiusPolicySerializer,
    SchedulerConfigSerializer,
    ReportConfigSerializer,
    ScheduledExperimentSerializer,
    ScheduledExperimentResponseSerializer,
    ApprovalActionSerializer,
    DailyResilienceReportSerializer,
    KillSwitchSerializer,
    SafetyCheckResultSerializer,
    BlastRadiusCheckRequestSerializer,
    BlastRadiusCheckResultSerializer,
    # Phase 2: Safety Mechanism Serializers
    TTLConfigSerializer,
    StopConditionsConfigSerializer,
    DryRunConfigSerializer,
    KillAllRequestSerializer,
    KillAllResponseSerializer,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Views
# =============================================================================


class SafetyGuardConfigView(APIView):
    """
    API for SafetyGuard configuration.
    
    GET: Retrieve current configuration
    PATCH: Update configuration
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current SafetyGuard configuration."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        guard = get_safety_guard()
        config = guard.get_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update SafetyGuard configuration."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        serializer = SafetyGuardConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        guard = get_safety_guard()
        updated_config = guard.update_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] SafetyGuard config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config.to_dict(),
        })


class BlastRadiusPolicyView(APIView):
    """
    API for BlastRadius policy configuration.
    
    GET: Retrieve current policy
    PATCH: Update policy
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current BlastRadius policy."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        
        manager = get_blast_radius_manager()
        policy = manager.get_policy()
        
        return Response({
            "status": "success",
            "data": policy.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update BlastRadius policy."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        
        serializer = BlastRadiusPolicySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        manager = get_blast_radius_manager()
        updated_policy = manager.update_policy(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] BlastRadius policy updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_policy.to_dict(),
        })


class SchedulerConfigView(APIView):
    """
    API for ChaosScheduler configuration.
    
    GET: Retrieve current configuration
    PATCH: Update configuration
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current scheduler configuration."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        config = scheduler.get_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update scheduler configuration."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        serializer = SchedulerConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        scheduler = get_chaos_scheduler()
        updated_config = scheduler.update_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] Scheduler config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config.to_dict(),
        })


class ReportConfigView(APIView):
    """
    API for ResilienceReport configuration.
    
    GET: Retrieve current configuration
    PATCH: Update configuration
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current report configuration."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        config = generator.get_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update report configuration."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        serializer = ReportConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        generator = get_report_generator()
        updated_config = generator.update_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] Report config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config.to_dict(),
        })


# =============================================================================
# Schedule Management Views
# =============================================================================


class ScheduleListView(APIView):
    """
    API for listing and creating scheduled experiments.
    
    GET: List schedules
    POST: Create new schedule
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """List scheduled experiments."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        
        # Query params
        enabled_only = request.query_params.get("enabled_only", "false").lower() == "true"
        pending_only = request.query_params.get("pending_approval_only", "false").lower() == "true"
        target_service = request.query_params.get("target_service")
        
        schedules = scheduler.list_schedules(
            enabled_only=enabled_only,
            pending_approval_only=pending_only,
            target_service=target_service,
        )
        
        return Response({
            "status": "success",
            "data": [s.to_dict() for s in schedules],
            "count": len(schedules),
        })
    
    def post(self, request: Request) -> Response:
        """Create a new scheduled experiment."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        serializer = ScheduledExperimentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        scheduler = get_chaos_scheduler()
        
        # Extract experiment_config if present
        data = serializer.validated_data
        experiment_config = data.pop("experiment_config", {})
        
        schedule = scheduler.create_schedule(
            created_by=str(request.user),
            experiment_config=experiment_config,
            **data,
        )
        
        logger.info(f"[ChaosAPI] Schedule created: {schedule.id} by {request.user}")
        
        return Response(
            {
                "status": "success",
                "data": schedule.to_dict(),
            },
            status=status.HTTP_201_CREATED,
        )


class ScheduleDetailView(APIView):
    """
    API for individual schedule operations.
    
    GET: Retrieve schedule
    PATCH: Update schedule
    DELETE: Delete schedule
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request, schedule_id: str) -> Response:
        """Get schedule details."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        schedule = scheduler.get_schedule(schedule_id)
        
        if not schedule:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        return Response({
            "status": "success",
            "data": schedule.to_dict(),
        })
    
    def patch(self, request: Request, schedule_id: str) -> Response:
        """Update schedule."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        schedule = scheduler.update_schedule(schedule_id, **request.data)
        
        if not schedule:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        logger.info(f"[ChaosAPI] Schedule updated: {schedule_id} by {request.user}")
        
        return Response({
            "status": "success",
            "data": schedule.to_dict(),
        })
    
    def delete(self, request: Request, schedule_id: str) -> Response:
        """Delete schedule."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        deleted = scheduler.delete_schedule(schedule_id)
        
        if not deleted:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        logger.info(f"[ChaosAPI] Schedule deleted: {schedule_id} by {request.user}")
        
        return Response(
            {"status": "success", "message": "Schedule deleted"},
            status=status.HTTP_204_NO_CONTENT,
        )


class ScheduleApprovalView(APIView):
    """
    API for approving/denying scheduled experiments.
    
    POST: Approve or deny a schedule
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request, schedule_id: str) -> Response:
        """Approve or deny a schedule."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        serializer = ApprovalActionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        scheduler = get_chaos_scheduler()
        action = serializer.validated_data["action"]
        reason = serializer.validated_data.get("reason", "")
        
        if action == "approve":
            schedule = scheduler.approve_schedule(schedule_id, approved_by=str(request.user))
        else:
            schedule = scheduler.deny_schedule(schedule_id, denied_by=str(request.user), reason=reason)
        
        if not schedule:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        logger.info(f"[ChaosAPI] Schedule {action}: {schedule_id} by {request.user}")
        
        return Response({
            "status": "success",
            "data": schedule.to_dict(),
        })


class ScheduleExecuteView(APIView):
    """
    API for executing a schedule immediately.
    
    POST: Execute schedule now
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request, schedule_id: str) -> Response:
        """Execute schedule immediately."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        force = request.data.get("force", False)
        
        result = scheduler.execute_now(schedule_id, force=force)
        
        logger.info(
            f"[ChaosAPI] Schedule executed: {schedule_id} by {request.user}, "
            f"result={result.status}"
        )
        
        return Response({
            "status": "success",
            "data": result.to_dict(),
        })


# =============================================================================
# Kill Switch Views
# =============================================================================


class KillSwitchView(APIView):
    """
    API for kill switch controls.
    
    GET: Get current status
    POST: Activate kill switch
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get kill switch status."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        guard = get_safety_guard()
        scheduler = get_chaos_scheduler()
        
        global_blocked, block_reason = guard.is_globally_blocked()
        running_experiments = scheduler.get_running_experiments()
        
        return Response({
            "status": "success",
            "data": {
                "global_block_active": global_blocked,
                "global_block_reason": block_reason,
                "running_experiments": running_experiments,
                "running_count": len(running_experiments),
            },
        })
    
    def post(self, request: Request) -> Response:
        """Activate kill switch."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        serializer = KillSwitchSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        action = serializer.validated_data["action"]
        reason = serializer.validated_data.get("reason", "")
        experiment_id = serializer.validated_data.get("experiment_id", "")
        
        guard = get_safety_guard()
        scheduler = get_chaos_scheduler()
        
        if action == "kill_one":
            killed = scheduler.kill_experiment(experiment_id, reason)
            message = f"Kill signal sent to {experiment_id}" if killed else "Failed to send kill signal"
        elif action == "kill_all":
            count = scheduler.kill_all(reason)
            message = f"Kill signal sent to {count} experiments"
        elif action == "block_global":
            guard.block_globally(reason)
            message = "Global block activated"
        elif action == "unblock_global":
            guard.unblock_globally()
            message = "Global block removed"
        else:
            message = "Unknown action"
        
        logger.warning(f"[ChaosAPI] Kill switch: {action} by {request.user} - {reason}")
        
        return Response({
            "status": "success",
            "message": message,
            "action": action,
        })


# =============================================================================
# Safety Check Views
# =============================================================================


class SafetyCheckView(APIView):
    """
    API for running safety checks.
    
    POST: Run safety check
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request) -> Response:
        """Run safety check."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        guard = get_safety_guard()
        
        experiment_id = request.data.get("experiment_id", "")
        target_service = request.data.get("target_service", "")
        force = request.data.get("force", False)
        
        result = guard.check(
            experiment_id=experiment_id,
            target_service=target_service,
            force=force,
        )
        
        serializer = SafetyCheckResultSerializer(result.to_dict())
        
        return Response({
            "status": "success",
            "data": serializer.data,
        })


class BlastRadiusCheckView(APIView):
    """
    API for checking blast radius policies.
    
    POST: Check blast radius
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request) -> Response:
        """Check blast radius."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        
        serializer = BlastRadiusCheckRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        manager = get_blast_radius_manager()
        result = manager.check(**serializer.validated_data)
        
        response_serializer = BlastRadiusCheckResultSerializer(result.to_dict())
        
        return Response({
            "status": "success",
            "data": response_serializer.data,
        })


# =============================================================================
# Report Views
# =============================================================================


class ReportListView(APIView):
    """
    API for resilience reports.
    
    GET: List reports
    """
    
    permission_classes = [IsAuthenticated]
    
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
    
    GET: Get report by ID or date
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request, report_id: str) -> Response:
        """Get report details."""
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
    
    POST: Generate report now
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request) -> Response:
        """Generate report now."""
        from selfhealing.services.chaos.reports import get_report_generator
        from datetime import datetime
        
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
    
    GET: Get grade history for trending
    """
    
    permission_classes = [IsAuthenticated]
    
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


# =============================================================================
# Pending Approvals View
# =============================================================================


class PendingApprovalsView(APIView):
    """
    API for pending approvals.
    
    GET: List pending approvals
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """List pending approvals."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        manager = get_blast_radius_manager()
        scheduler = get_chaos_scheduler()
        
        # Get pending from both sources
        blast_radius_pending = manager.get_pending_approvals()
        schedule_pending = scheduler.list_schedules(pending_approval_only=True)
        
        return Response({
            "status": "success",
            "data": {
                "blast_radius_approvals": [a.to_dict() for a in blast_radius_pending],
                "schedule_approvals": [s.to_dict() for s in schedule_pending],
            },
            "total_pending": len(blast_radius_pending) + len(schedule_pending),
        })


# =============================================================================
# Phase 2: Safety Mechanism Configuration Views
# =============================================================================


class StopConditionsConfigView(APIView):
    """
    API for Stop Conditions configuration.
    
    자동 중단 조건 설정을 관리합니다.
    에러율, 지연시간, 에러 버짓 임계값을 초과하면 실험이 자동 중단됩니다.
    
    GET: Retrieve current configuration
    PATCH: Update configuration
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current Stop Conditions configuration."""
        from selfhealing.services.chaos.stop_conditions import get_stop_conditions_config
        
        config = get_stop_conditions_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update Stop Conditions configuration."""
        from selfhealing.services.runtime_config import get_runtime_config_manager
        
        serializer = StopConditionsConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        manager = get_runtime_config_manager()
        updated_config = manager.update_chaos_stop_conditions_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] Stop Conditions config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config,
        })


class TTLConfigView(APIView):
    """
    API for TTL (Self-Expiration) configuration.
    
    카오스 실험의 자동 만료 설정을 관리합니다.
    엔진이 죽어도 타겟 시스템이 자동 복구됩니다.
    
    GET: Retrieve current configuration
    PATCH: Update configuration
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current TTL configuration."""
        from selfhealing.services.chaos.stop_conditions import get_ttl_config
        
        config = get_ttl_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update TTL configuration."""
        from selfhealing.services.runtime_config import get_runtime_config_manager
        
        serializer = TTLConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        manager = get_runtime_config_manager()
        updated_config = manager.update_chaos_ttl_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] TTL config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config,
        })


class DryRunConfigView(APIView):
    """
    API for Dry Run configuration.
    
    Dry Run 모드에서는 실제 장애 주입 없이
    전체 워크플로우만 검증합니다.
    
    GET: Retrieve current configuration
    PATCH: Update configuration
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request: Request) -> Response:
        """Get current Dry Run configuration."""
        from selfhealing.services.chaos.stop_conditions import get_dry_run_config
        
        config = get_dry_run_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update Dry Run configuration."""
        from selfhealing.services.runtime_config import get_runtime_config_manager
        
        serializer = DryRunConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        manager = get_runtime_config_manager()
        updated_config = manager.update_chaos_dry_run_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] Dry Run config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config,
        })


class KillAllView(APIView):
    """
    API for killing all running experiments.
    
    모든 실행 중인 카오스 실험을 즉시 중단하고 롤백합니다.
    긴급 상황에서 사용합니다.
    
    POST: Kill all running experiments
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request: Request) -> Response:
        """Kill all running experiments and initiate rollbacks."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        from selfhealing.services.runtime_config import get_runtime_config_manager
        
        serializer = KillAllRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        reason = serializer.validated_data["reason"]
        operator = serializer.validated_data.get("operator", str(request.user))
        
        scheduler = get_chaos_scheduler()
        manager = get_runtime_config_manager()
        
        # 1. Kill all running experiments
        experiments_killed = scheduler.kill_all(reason=reason)
        
        # 2. Count rollbacks (same as killed for now)
        rollbacks_initiated = experiments_killed
        
        # 3. Clear TTL configs
        ttl_configs_cleared = 0
        try:
            chaos_config = manager.get_chaos_config()
            if chaos_config.get("active_ttl_configs"):
                ttl_configs_cleared = len(chaos_config.get("active_ttl_configs", []))
                manager.clear_active_ttl_configs()
        except Exception as e:
            logger.warning(f"[ChaosAPI] Failed to clear TTL configs: {e}")
        
        logger.warning(
            f"[ChaosAPI] KILL ALL executed by {operator}: "
            f"reason='{reason}', experiments_killed={experiments_killed}, "
            f"rollbacks_initiated={rollbacks_initiated}, "
            f"ttl_configs_cleared={ttl_configs_cleared}"
        )
        
        return Response({
            "status": "success",
            "data": {
                "experiments_killed": experiments_killed,
                "rollbacks_initiated": rollbacks_initiated,
                "ttl_configs_cleared": ttl_configs_cleared,
            },
        })

