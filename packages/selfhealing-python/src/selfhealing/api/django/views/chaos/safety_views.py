"""
Chaos Engineering Safety Views.

API views for kill switch, safety checks, and blast radius verification.
"""

import logging

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.api.django.serializers.chaos import (
    BlastRadiusCheckRequestSerializer,
    BlastRadiusCheckResultSerializer,
    DryRunConfigSerializer,
    KillAllRequestSerializer,
    KillSwitchSerializer,
    SafetyCheckResultSerializer,
    StopConditionsConfigSerializer,
    TTLConfigSerializer,
)

logger = logging.getLogger(__name__)


class KillSwitchView(APIView):
    """
    API for kill switch controls.

    GET: Get current status (Viewer)
    POST: Activate kill switch (Admin only - emergency control)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get kill switch status."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        guard = get_safety_guard()
        scheduler = get_chaos_scheduler()

        global_blocked, block_reason = guard.is_globally_blocked()
        running_experiments = scheduler.get_running_experiments()

        return Response(
            {
                "status": "success",
                "data": {
                    "global_block_active": global_blocked,
                    "global_block_reason": block_reason,
                    "running_experiments": running_experiments,
                    "running_count": len(running_experiments),
                },
            }
        )

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
            message = (
                f"Kill signal sent to {experiment_id}"
                if killed
                else "Failed to send kill signal"
            )
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

        return Response(
            {
                "status": "success",
                "message": message,
                "action": action,
            }
        )


class SafetyCheckView(APIView):
    """
    API for running safety checks.

    POST: Run safety check (Viewer - read-only analysis)
    """

    permission_classes = [IsViewer]

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

        return Response(
            {
                "status": "success",
                "data": serializer.data,
            }
        )


class BlastRadiusCheckView(APIView):
    """
    API for checking blast radius policies.

    POST: Check blast radius (Viewer - read-only analysis)
    """

    permission_classes = [IsViewer]

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

        return Response(
            {
                "status": "success",
                "data": response_serializer.data,
            }
        )


class StopConditionsConfigView(APIView):
    """
    API for Stop Conditions configuration.

    자동 중단 조건 설정을 관리합니다.
    에러율, 지연시간, 에러 버짓 임계값을 초과하면 실험이 자동 중단됩니다.

    GET: Retrieve current configuration (Viewer)
    PATCH: Update configuration (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get current Stop Conditions configuration."""
        from selfhealing.services.chaos.stop_conditions import (
            get_stop_conditions_config,
        )

        config = get_stop_conditions_config()

        return Response(
            {
                "status": "success",
                "data": config.to_dict(),
            }
        )

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
        updated_config = manager.update_chaos_stop_conditions_config(
            **serializer.validated_data
        )

        logger.info(f"[ChaosAPI] Stop Conditions config updated by {request.user}")

        return Response(
            {
                "status": "success",
                "data": updated_config,
            }
        )


class TTLConfigView(APIView):
    """
    API for TTL (Self-Expiration) configuration.

    카오스 실험의 자동 만료 설정을 관리합니다.
    엔진이 죽어도 타겟 시스템이 자동 복구됩니다.

    GET: Retrieve current configuration (Viewer)
    PATCH: Update configuration (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get current TTL configuration."""
        from selfhealing.services.chaos.stop_conditions import get_ttl_config

        config = get_ttl_config()

        return Response(
            {
                "status": "success",
                "data": config.to_dict(),
            }
        )

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

        return Response(
            {
                "status": "success",
                "data": updated_config,
            }
        )


class DryRunConfigView(APIView):
    """
    API for Dry Run configuration.

    Dry Run 모드에서는 실제 장애 주입 없이
    전체 워크플로우만 검증합니다.

    GET: Retrieve current configuration (Viewer)
    PATCH: Update configuration (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get current Dry Run configuration."""
        from selfhealing.services.chaos.stop_conditions import get_dry_run_config

        config = get_dry_run_config()

        return Response(
            {
                "status": "success",
                "data": config.to_dict(),
            }
        )

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
        updated_config = manager.update_chaos_dry_run_config(
            **serializer.validated_data
        )

        logger.info(f"[ChaosAPI] Dry Run config updated by {request.user}")

        return Response(
            {
                "status": "success",
                "data": updated_config,
            }
        )


class KillAllView(APIView):
    """
    API for killing all running experiments.

    모든 실행 중인 카오스 실험을 즉시 중단하고 롤백합니다.
    긴급 상황에서 사용합니다.

    POST: Kill all running experiments (Admin only - emergency control)
    """

    permission_classes = [IsSelfHealingAdmin]

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

        return Response(
            {
                "status": "success",
                "data": {
                    "experiments_killed": experiments_killed,
                    "rollbacks_initiated": rollbacks_initiated,
                    "ttl_configs_cleared": ttl_configs_cleared,
                },
            }
        )
