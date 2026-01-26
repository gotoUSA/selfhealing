"""
Canary Rollout API Views.

설정 변경의 점진적 배포(Canary Rollout) 관리 API 엔드포인트.

Endpoints:
    GET  /api/self-healing/canary/rollouts/              - 활성 롤아웃 목록 조회
    POST /api/self-healing/canary/rollouts/              - 새 롤아웃 생성
    GET  /api/self-healing/canary/rollouts/{id}/         - 롤아웃 상세 조회
    POST /api/self-healing/canary/rollouts/{id}/start/   - 롤아웃 시작
    POST /api/self-healing/canary/rollouts/{id}/promote/ - 다음 단계로 프로모션
    POST /api/self-healing/canary/rollouts/{id}/rollback/ - 롤백
    POST /api/self-healing/canary/rollouts/{id}/pause/   - 일시 중지
    POST /api/self-healing/canary/rollouts/{id}/resume/  - 재개
    POST /api/self-healing/canary/rollouts/{id}/cancel/  - 취소
    POST /api/self-healing/canary/panic-rollback/        - 긴급 롤백 (모든 활성 롤아웃)

Reference:
    docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""
from __future__ import annotations

import logging
from typing import List

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import (
    IsSelfHealingAdmin,
    IsSelfHealingAuthenticated,
    IsViewer,
    IsOperator,
    IsPanicRollbackAuthorized,
)
from selfhealing.services.canary import (
    get_canary_rollout_service,
    CanaryStage,
    CanaryState,
    ConfigLockError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================

def _format_rollout_summary(rollout) -> dict:
    """
    롤아웃 요약 정보를 딕셔너리로 변환.
    
    목록 조회용 간략한 정보만 포함.
    """
    return {
        "id": rollout.id,
        "config_type": rollout.config_type,
        "state": rollout.state.value,
        "current_stage": (
            rollout.current_stage.name if rollout.current_stage else None
        ),
        "current_stage_index": rollout.current_stage_index,
        "total_stages": len(rollout.stages),
        "affected_clusters": rollout.affected_clusters,
        "created_by": rollout.created_by,
        "created_at": rollout.created_at.isoformat(),
        "progress_percentage": rollout.progress_percentage,
    }


def _format_rollout_detail(rollout) -> dict:
    """
    롤아웃 상세 정보를 딕셔너리로 변환.
    
    상세 조회용 전체 정보 포함.
    """
    return {
        "id": rollout.id,
        "config_type": rollout.config_type,
        "state": rollout.state.value,
        "current_stage_index": rollout.current_stage_index,
        "previous_values": rollout.previous_values,
        "new_values": rollout.new_values,
        "stages": [
            {
                "name": s.name,
                "clusters": s.clusters,
                "percentage": s.percentage,
                "duration_minutes": s.duration_minutes,
                "auto_promote": s.auto_promote,
            }
            for s in rollout.stages
        ],
        "affected_clusters": rollout.affected_clusters,
        "created_by": rollout.created_by,
        "created_at": rollout.created_at.isoformat(),
        "completed_at": (
            rollout.completed_at.isoformat() if rollout.completed_at else None
        ),
        "reason": rollout.reason,
        "rollback_reason": rollout.rollback_reason,
        "progress_percentage": rollout.progress_percentage,
        "is_terminal": rollout.is_terminal,
    }


def _get_username(request: Request) -> str:
    """요청에서 사용자명 추출."""
    if hasattr(request, 'user') and request.user:
        if hasattr(request.user, 'username') and request.user.username:
            return request.user.username
        if hasattr(request.user, 'email') and request.user.email:
            return request.user.email
    return "anonymous"


# =============================================================================
# API Views
# =============================================================================

class CanaryRolloutListView(APIView):
    """
    Canary 롤아웃 목록 조회 및 생성 API.
    
    GET  /api/self-healing/canary/rollouts/
        활성 롤아웃 목록 조회 (Viewer 이상)
        
    POST /api/self-healing/canary/rollouts/
        새 롤아웃 생성 (Admin)
    
    Query Parameters (GET):
        - include_completed: true/false (기본값: false) - 완료된 롤아웃 포함 여부
        - config_type: 필터링할 설정 유형
    
    Request Body (POST):
        {
            "config_type": "circuit_breaker",
            "new_values": {"failure_threshold": 3},
            "stages": [
                {
                    "name": "canary",
                    "clusters": ["seoul-canary"],
                    "percentage": 10,
                    "duration_minutes": 5,
                    "auto_promote": true
                }
            ],
            "reason": "Reduce failure threshold"
        }
    """
    
    def get_permissions(self) -> List[BasePermission]:
        """요청 메서드에 따라 권한 분기."""
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]
    
    def get(self, request: Request) -> Response:
        """활성 롤아웃 목록 조회."""
        service = get_canary_rollout_service()
        
        # Query parameters
        include_completed = (
            request.query_params.get("include_completed", "").lower() == "true"
        )
        config_type_filter = request.query_params.get("config_type")
        
        # 롤아웃 조회
        rollouts = service.get_active_rollouts()
        
        # 완료된 롤아웃 포함 시
        if include_completed:
            limit = self._get_completed_rollouts_limit()
            completed = service.get_completed_rollouts(limit=limit)
            rollouts = rollouts + completed
        
        # config_type 필터링
        if config_type_filter:
            rollouts = [r for r in rollouts if r.config_type == config_type_filter]
        
        return Response({
            "status": "success",
            "count": len(rollouts),
            "rollouts": [_format_rollout_summary(r) for r in rollouts],
        })

    @staticmethod
    def _get_completed_rollouts_limit() -> int:
        """Settings에서 completed_rollouts_limit 조회."""
        try:
            from selfhealing.settings.canary import get_canary_settings
            return get_canary_settings().default_completed_rollouts_limit
        except Exception:
            return 20  # 기본값
    
    def post(self, request: Request) -> Response:
        """새 롤아웃 생성."""
        service = get_canary_rollout_service()
        
        # 필수 필드 검증
        config_type = request.data.get("config_type")
        if not config_type:
            return Response(
                {"status": "error", "error": "config_type is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        new_values = request.data.get("new_values", {})
        if not new_values:
            return Response(
                {"status": "error", "error": "new_values is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        stages_data = request.data.get("stages", [])
        if not stages_data:
            return Response(
                {"status": "error", "error": "At least one stage is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # stages 파싱 - ValueError/KeyError/TypeError는 exception handler가 처리
        stages = [
            CanaryStage(
                name=s.get("name", f"stage_{i}"),
                clusters=s.get("clusters", []),
                percentage=float(s.get("percentage", 0)),
                duration_minutes=int(s.get("duration_minutes", 5)),
                auto_promote=s.get("auto_promote", True),
            )
            for i, s in enumerate(stages_data)
        ]
        
        # 롤아웃 생성 - ConfigLockError/ValueError는 exception handler가 처리
        rollout = service.create_rollout(
            config_type=config_type,
            new_values=new_values,
            stages=stages,
            created_by=_get_username(request),
            reason=request.data.get("reason", ""),
            force_during_chaos=request.data.get("force_during_chaos", False),
        )
        
        logger.info(
            f"[CanaryAPI] Rollout created: id={rollout.id}, "
            f"config={config_type}, by={_get_username(request)}"
        )
        
        return Response(
            {
                "status": "success",
                "rollout": _format_rollout_detail(rollout),
            },
            status=status.HTTP_201_CREATED,
        )


class CanaryRolloutDetailView(APIView):
    """
    Canary 롤아웃 상세 조회 API.
    
    GET /api/self-healing/canary/rollouts/{rollout_id}/
    
    Viewer 이상 권한 필요.
    """
    permission_classes = [IsViewer]
    
    def get(self, request: Request, rollout_id: str) -> Response:
        """롤아웃 상세 조회."""
        service = get_canary_rollout_service()
        rollout = service.get_rollout(rollout_id)
        
        if not rollout:
            return Response(
                {"status": "error", "error": "Rollout not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        return Response({
            "status": "success",
            "rollout": _format_rollout_detail(rollout),
        })


# =============================================================================
# Action Handlers for CanaryRolloutActionView (Complexity Reduction)
# =============================================================================

def _action_start(service, rollout_id: str, rollout, request) -> tuple[bool, str | None]:
    """Handle start action."""
    success = service.start_rollout(rollout_id)
    error_msg = None if success else f"Cannot start rollout in state: {rollout.state.value}"
    return success, error_msg


def _action_promote(service, rollout_id: str, rollout, request) -> tuple[bool, str | None]:
    """Handle promote action."""
    force = request.data.get("force", False)
    success = service.promote(rollout_id, force=force)
    error_msg = None if success else "Promotion failed - check metrics or state"
    return success, error_msg


def _action_rollback(service, rollout_id: str, rollout, request) -> tuple[bool, str | None]:
    """Handle rollback action."""
    reason = request.data.get("reason", f"Manual rollback by {_get_username(request)}")
    success = service.rollback(rollout_id, reason=reason)
    error_msg = None if success else "Rollback failed - rollout may be in terminal state"
    return success, error_msg


def _action_pause(service, rollout_id: str, rollout, request) -> tuple[bool, str | None]:
    """Handle pause action."""
    success = service.pause(rollout_id)
    error_msg = None if success else "Cannot pause - rollout is not in CANARY state"
    return success, error_msg


def _action_resume(service, rollout_id: str, rollout, request) -> tuple[bool, str | None]:
    """Handle resume action."""
    success = service.resume(rollout_id)
    error_msg = None if success else "Cannot resume - rollout is not in PAUSED state"
    return success, error_msg


def _action_cancel(service, rollout_id: str, rollout, request) -> tuple[bool, str | None]:
    """Handle cancel action."""
    success = service.cancel(rollout_id)
    error_msg = None if success else "Cannot cancel - rollout may be in terminal state"
    return success, error_msg


# Action handler registry
_ACTION_HANDLERS = {
    "start": _action_start,
    "promote": _action_promote,
    "rollback": _action_rollback,
    "pause": _action_pause,
    "resume": _action_resume,
    "cancel": _action_cancel,
}


class CanaryRolloutActionView(APIView):
    """
    Canary 롤아웃 액션 API.
    
    POST /api/self-healing/canary/rollouts/{rollout_id}/{action}/
    
    지원하는 액션:
        - start: 롤아웃 시작 (첫 번째 단계 적용)
        - promote: 다음 단계로 프로모션
        - rollback: 롤백 (이전 설정으로 복원)
        - pause: 일시 중지
        - resume: 재개
        - cancel: 취소
    
    Request Body:
        - promote: {"force": true} - 메트릭 검증 무시하고 프로모션
        - rollback: {"reason": "..."} - 롤백 사유
    """
    permission_classes = [IsSelfHealingAdmin]
    
    VALID_ACTIONS = list(_ACTION_HANDLERS.keys())
    
    def post(self, request: Request, rollout_id: str, action: str) -> Response:
        """롤아웃 액션 실행."""
        if action not in self.VALID_ACTIONS:
            return Response(
                {
                    "status": "error",
                    "error": f"Unknown action: {action}",
                    "valid_actions": self.VALID_ACTIONS,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        service = get_canary_rollout_service()
        rollout = service.get_rollout(rollout_id)
        
        if not rollout:
            return Response(
                {"status": "error", "error": "Rollout not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # 액션 핸들러 실행 - Exception은 exception handler가 처리
        handler = _ACTION_HANDLERS[action]
        success, error_message = handler(service, rollout_id, rollout, request)
        
        if not success:
            return Response(
                {"status": "error", "error": error_message or "Action failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # 업데이트된 롤아웃 조회
        updated_rollout = service.get_rollout(rollout_id)
        
        logger.info(
            f"[CanaryAPI] Action executed: rollout={rollout_id}, "
            f"action={action}, new_state={updated_rollout.state.value if updated_rollout else 'unknown'}"
        )
        
        return Response({
            "status": "success",
            "action": action,
            "rollout": _format_rollout_summary(updated_rollout) if updated_rollout else None,
        })


class CanaryPanicRollbackView(APIView):
    """
    긴급 전체 롤백 API (Panic Rollback).
    
    POST /api/self-healing/canary/panic-rollback/
    
    모든 활성 롤아웃을 즉시 롤백합니다.
    Break Glass 패턴: 4-Eyes 승인 없이 단일 승인자가 긴급 실행 가능.
    
    권한: IsPanicRollbackAuthorized (Admin 또는 Emergency Escalation)
    
    Request Body:
        {
            "reason": "Production incident - immediate rollback required",
            "emergency_code": "EMERGENCY-2024-001"  # 선택사항
        }
    """
    permission_classes = [IsPanicRollbackAuthorized]
    
    def post(self, request: Request) -> Response:
        """모든 활성 롤아웃 긴급 롤백."""
        service = get_canary_rollout_service()
        
        reason = request.data.get(
            "reason", f"Panic rollback by {_get_username(request)}"
        )
        emergency_code = request.data.get("emergency_code", "")
        
        # 활성 롤아웃 조회
        active_rollouts = service.get_active_rollouts()
        
        if not active_rollouts:
            return Response({
                "status": "success",
                "message": "No active rollouts to rollback",
                "rolled_back": [],
            })
        
        # 모든 활성 롤아웃 롤백
        results = []
        for rollout in active_rollouts:
            try:
                success = service.rollback(
                    rollout.id, 
                    reason=f"[PANIC] {reason}"
                )
                results.append({
                    "id": rollout.id,
                    "config_type": rollout.config_type,
                    "success": success,
                    "affected_clusters": rollout.affected_clusters,
                })
            except Exception as e:
                results.append({
                    "id": rollout.id,
                    "config_type": rollout.config_type,
                    "success": False,
                    "error": str(e),
                })
        
        success_count = sum(1 for r in results if r.get("success"))
        
        logger.warning(
            f"[CanaryAPI] PANIC ROLLBACK executed: "
            f"by={_get_username(request)}, reason={reason}, "
            f"emergency_code={emergency_code}, "
            f"success={success_count}/{len(results)}"
        )
        
        return Response({
            "status": "success",
            "message": f"Panic rollback completed: {success_count}/{len(results)} successful",
            "emergency_code": emergency_code,
            "rolled_back": results,
        })


class CanaryMetricsView(APIView):
    """
    Canary 롤아웃 메트릭 조회 API.
    
    GET /api/self-healing/canary/rollouts/{rollout_id}/metrics/
    
    현재 단계의 메트릭 수집 결과를 반환합니다.
    """
    permission_classes = [IsViewer]
    
    def get(self, request: Request, rollout_id: str) -> Response:
        """롤아웃 메트릭 조회."""
        service = get_canary_rollout_service()
        rollout = service.get_rollout(rollout_id)
        
        if not rollout:
            return Response(
                {"status": "error", "error": "Rollout not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # 메트릭 수집
        metrics = service.collect_metrics(rollout_id)
        
        return Response({
            "status": "success",
            "rollout_id": rollout_id,
            "state": rollout.state.value,
            "current_stage": (
                rollout.current_stage.name if rollout.current_stage else None
            ),
            "metrics": [
                {
                    "cluster": m.cluster,
                    "stage_name": m.stage_name,
                    "error_rate_before": m.error_rate_before,
                    "error_rate_after": m.error_rate_after,
                    "latency_p50_before": m.latency_p50_before,
                    "latency_p50_after": m.latency_p50_after,
                    "latency_p99_before": m.latency_p99_before,
                    "latency_p99_after": m.latency_p99_after,
                    "requests_total": m.requests_total,
                    "errors_total": m.errors_total,
                    "is_healthy": m.is_healthy,
                    "unhealthy_reason": m.unhealthy_reason,
                }
                for m in metrics
            ] if metrics else [],
        })


class CanaryHistoryView(APIView):
    """
    Canary 롤아웃 이력 조회 API.
    
    GET /api/self-healing/canary/history/
    
    Query Parameters:
        - config_type: 설정 유형으로 필터링
        - limit: 조회 개수 (기본 20, 최대 100)
        - state: 상태로 필터링 (completed, rolled_back, failed, cancelled)
    """
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """롤아웃 이력 조회."""
        service = get_canary_rollout_service()
        
        # Query parameters
        config_type = request.query_params.get("config_type")
        state_filter = request.query_params.get("state")
        
        try:
            limit = int(request.query_params.get("limit", 20))
            limit = min(max(limit, 1), 100)
        except ValueError:
            limit = 20
        
        # 완료된 롤아웃 조회
        rollouts = service.get_completed_rollouts(limit=limit)
        
        # 필터링
        if config_type:
            rollouts = [r for r in rollouts if r.config_type == config_type]
        
        if state_filter:
            try:
                target_state = CanaryState(state_filter)
                rollouts = [r for r in rollouts if r.state == target_state]
            except ValueError:
                pass  # 잘못된 state는 무시
        
        return Response({
            "status": "success",
            "count": len(rollouts),
            "history": [_format_rollout_summary(r) for r in rollouts],
        })
