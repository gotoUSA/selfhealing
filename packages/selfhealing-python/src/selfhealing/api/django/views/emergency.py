"""
Emergency Mode API Views.

비상 모드 수동 활성화/해제 및 상태 조회 API.

Endpoints:
- GET  /api/self-healing/emergency/status/   - 현재 비상 모드 상태
- POST /api/self-healing/emergency/trigger/  - 수동 비상 모드 활성화
- POST /api/self-healing/emergency/release/  - 비상 모드 해제
- POST /api/self-healing/emergency/gradual-recovery/ - 점진적 복구 시작
- POST /api/self-healing/emergency/stop-recovery/    - 점진적 복구 중지
- GET  /api/self-healing/emergency/history/  - 비상 모드 변경 이력
- GET  /api/self-healing/emergency/config/   - 복구 게이트 설정 조회
- PUT  /api/self-healing/emergency/config/   - 복구 게이트 설정 변경
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer, IsSelfHealingAdmin, IsOperator
from selfhealing.services.emergency_mode import get_emergency_manager
from selfhealing.services.emergency_mode.enums import (
    EmergencyLevel,
    EMERGENCY_LEVEL_RULES,
)
from selfhealing.services.emergency_mode.models import RecoveryGateConfig

logger = logging.getLogger(__name__)


class EmergencyStatusView(APIView):
    """
    GET /api/self-healing/emergency/status/

    현재 비상 모드 상태 조회.

    Response:
        {
            "is_active": true,
            "level": "LEVEL_2",
            "level_value": 2,
            "activated_at": "2024-01-01T12:00:00Z",
            "activated_by": "admin",
            "activation_reason": "High error rate",
            "expires_at": "2024-01-01T12:30:00Z",
            "is_auto_triggered": false,
            "is_recovering": false,
            "tier_multipliers": {
                "critical": 1.0,
                "standard": 0.1,
                "non_essential": 0.0
            }
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        manager = get_emergency_manager()
        state = manager.get_state()

        # 현재 레벨의 티어 배율
        tier_multipliers = EMERGENCY_LEVEL_RULES.get(
            state.level,
            EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL],
        )

        return Response(
            {
                "is_active": state.is_active,
                "level": state.level.name,
                "level_value": state.level.value,
                "activated_at": state.activated_at,
                "activated_by": state.activated_by,
                "activation_reason": state.activation_reason,
                "expires_at": state.expires_at,
                "is_auto_triggered": state.is_auto_triggered,
                "is_recovering": state.is_recovering,
                "recovery_started_at": state.recovery_started_at,
                "target_level": state.target_level.name if state.target_level else None,
                "deactivated_at": state.deactivated_at,
                "deactivated_by": state.deactivated_by,
                "tier_multipliers": tier_multipliers,
                "available_levels": [
                    {
                        "name": level.name,
                        "value": level.value,
                        "multipliers": EMERGENCY_LEVEL_RULES[level],
                    }
                    for level in EmergencyLevel
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class EmergencyTriggerView(APIView):
    """
    POST /api/self-healing/emergency/trigger/

    수동 비상 모드 활성화.

    Request:
        {
            "level": "LEVEL_2",           // LEVEL_1, LEVEL_2, LEVEL_3
            "reason": "High error rate",  // 필수
            "duration_minutes": 30        // 선택 (미지정 시 수동 해제 필요)
        }

    Response:
        {
            "success": true,
            "status": "activated",
            "level": "LEVEL_2",
            "activated_by": "admin",
            "expires_at": "2024-01-01T12:30:00Z"
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        level_name = request.data.get("level", "LEVEL_1")
        reason = request.data.get("reason", "")
        duration_minutes = request.data.get("duration_minutes")

        # Validation
        if not reason:
            return Response(
                {
                    "success": False,
                    "error": "reason is required",
                    "message": "비상 모드 활성화 사유를 입력해주세요.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            level = EmergencyLevel[level_name]
        except KeyError:
            return Response(
                {
                    "success": False,
                    "error": "invalid_level",
                    "message": f"유효하지 않은 레벨: {level_name}. " f"사용 가능: LEVEL_1, LEVEL_2, LEVEL_3",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if level == EmergencyLevel.NORMAL:
            return Response(
                {
                    "success": False,
                    "error": "invalid_level",
                    "message": "NORMAL은 비상 모드가 아닙니다. " "비상 모드를 해제하려면 /release/ 엔드포인트를 사용하세요.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor = getattr(request.user, "username", "api")

        manager = get_emergency_manager()
        state = manager.activate_manual(
            level=level,
            reason=reason,
            activated_by=actor,
            duration_minutes=int(duration_minutes) if duration_minutes else None,
        )

        logger.warning(f"[EmergencyAPI] Emergency mode activated: level={level.name}, " f"by={actor}, reason={reason}")

        return Response(
            {
                "success": True,
                "status": "activated",
                "level": state.level.name,
                "activated_by": actor,
                "expires_at": state.expires_at,
                "tier_multipliers": EMERGENCY_LEVEL_RULES[state.level],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class EmergencyReleaseView(APIView):
    """
    POST /api/self-healing/emergency/release/

    비상 모드 해제.

    Request:
        {
            "reason": "System recovered",  // 선택
            "force": false                  // 선택 (복구 조건 무시)
        }

    Response:
        {
            "success": true,
            "status": "deactivated",
            "previous_level": "LEVEL_2",
            "deactivated_by": "admin"
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        reason = request.data.get("reason", "")
        force = request.data.get("force", False)

        manager = get_emergency_manager()

        # 현재 상태 확인
        current_state = manager.get_state()
        if not current_state.is_active:
            return Response(
                {
                    "success": False,
                    "error": "not_active",
                    "message": "비상 모드가 활성화되어 있지 않습니다.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor = getattr(request.user, "username", "api")
        previous_level = current_state.level.name

        try:
            state = manager.deactivate(
                deactivated_by=actor,
                reason=reason,
                force=force,
            )
        except ValueError as e:
            return Response(
                {
                    "success": False,
                    "error": "recovery_blocked",
                    "message": str(e),
                    "hint": "force=true를 사용하여 강제 해제할 수 있습니다.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        logger.info(f"[EmergencyAPI] Emergency mode deactivated: " f"previous_level={previous_level}, by={actor}")

        return Response(
            {
                "success": True,
                "status": "deactivated",
                "previous_level": previous_level,
                "deactivated_by": actor,
                "forced": force,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class GradualRecoveryStartView(APIView):
    """
    POST /api/self-healing/emergency/gradual-recovery/

    점진적 복구 시작.

    현재 비상 모드 레벨에서 목표 레벨까지 단계적으로 완화.
    각 단계마다 시스템 메트릭을 확인하고 안정적이면 다음 단계로 진행.

    Request:
        {
            "target_level": "NORMAL"  // 선택 (기본: NORMAL)
        }

    Response:
        {
            "success": true,
            "status": "recovery_started",
            "current_level": "LEVEL_2",
            "target_level": "NORMAL"
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        target_level_name = request.data.get("target_level", "NORMAL")

        try:
            target_level = EmergencyLevel[target_level_name]
        except KeyError:
            return Response(
                {
                    "success": False,
                    "error": "invalid_level",
                    "message": f"유효하지 않은 레벨: {target_level_name}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor = getattr(request.user, "username", "api")
        manager = get_emergency_manager()

        try:
            state = manager.start_gradual_recovery(
                initiated_by=actor,
                target_level=target_level,
            )
        except ValueError as e:
            return Response(
                {
                    "success": False,
                    "error": "invalid_request",
                    "message": str(e),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "status": "recovery_started",
                "current_level": state.level.name,
                "target_level": target_level.name,
                "initiated_by": actor,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class GradualRecoveryStopView(APIView):
    """
    POST /api/self-healing/emergency/stop-recovery/

    점진적 복구 중지.

    Request:
        {
            "reason": "Manual intervention required"  // 선택
        }

    Response:
        {
            "success": true,
            "status": "recovery_stopped"
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        reason = request.data.get("reason", "")
        actor = getattr(request.user, "username", "api")

        manager = get_emergency_manager()
        state = manager.stop_gradual_recovery(
            stopped_by=actor,
            reason=reason,
        )

        return Response(
            {
                "success": True,
                "status": "recovery_stopped",
                "current_level": state.level.name,
                "stopped_by": actor,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class EmergencyHistoryView(APIView):
    """
    GET /api/self-healing/emergency/history/

    비상 모드 변경 이력 조회.

    Query Parameters:
        - limit: 조회할 최대 개수 (기본: 50)

    Response:
        {
            "history": [
                {
                    "timestamp": "2024-01-01T12:00:00Z",
                    "action": "ACTIVATED",
                    "old_level": "NORMAL",
                    "new_level": "LEVEL_2",
                    "old_active": false,
                    "new_active": true
                },
                ...
            ]
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        limit = int(request.query_params.get("limit", 50))

        manager = get_emergency_manager()
        history = manager.get_history(limit=limit)

        return Response(
            {
                "history": history,
                "count": len(history),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class EmergencyConfigView(APIView):
    """
    GET/PUT /api/self-healing/emergency/config/

    복구 게이트 설정 조회/변경.

    GET Response:
        {
            "stabilization_period_seconds": 300,
            "require_metrics_stable": true,
            "cpu_threshold_percent": 80.0,
            "error_rate_threshold": 0.05,
            "gradual_recovery": true,
            "level_step_delay_seconds": 60,
            "health_check_interval_seconds": 30,
            "auto_rollback_on_failure": true
        }

    PUT Request:
        {
            "stabilization_period_seconds": 300,
            ...
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        manager = get_emergency_manager()
        config = manager.get_recovery_gate_config()

        return Response(
            {
                "config": config.to_dict(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def put(self, request: Request) -> Response:
        actor = getattr(request.user, "username", "api")

        try:
            config = RecoveryGateConfig.from_dict(request.data)
        except Exception as e:
            return Response(
                {
                    "success": False,
                    "error": "invalid_config",
                    "message": str(e),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        manager = get_emergency_manager()
        manager.set_recovery_gate_config(config, changed_by=actor)

        return Response(
            {
                "success": True,
                "config": config.to_dict(),
                "changed_by": actor,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class EmergencyLevelsView(APIView):
    """
    GET /api/self-healing/emergency/levels/

    비상 모드 레벨 정의 조회.

    Response:
        {
            "levels": [
                {
                    "name": "NORMAL",
                    "value": 0,
                    "description": "정상 운영",
                    "multipliers": {
                        "critical": 1.0,
                        "standard": 1.0,
                        "non_essential": 1.0
                    }
                },
                ...
            ]
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        descriptions = {
            EmergencyLevel.NORMAL: "정상 운영 (모든 트래픽 허용)",
            EmergencyLevel.LEVEL_1: "경미한 장애 - Non-Essential API 차단",
            EmergencyLevel.LEVEL_2: "중간 장애 - Standard API 10%만 허용",
            EmergencyLevel.LEVEL_3: "심각한 장애 - Critical API만 50% 허용",
        }

        levels = [
            {
                "name": level.name,
                "value": level.value,
                "description": descriptions.get(level, ""),
                "multipliers": EMERGENCY_LEVEL_RULES[level],
            }
            for level in EmergencyLevel
        ]

        return Response(
            {
                "levels": levels,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
