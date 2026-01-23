"""
Recovery REST API Views.

복구 프로세스 관리를 위한 REST API 엔드포인트입니다.

Endpoints:
- GET  /api/self-healing/recovery/status/ - 현재 복구 상태
- POST /api/self-healing/recovery/start/ - 복구 시작
- POST /api/self-healing/recovery/abort/ - 복구 중단
- GET  /api/self-healing/recovery/pending-approvals/ - 대기 중인 승인 목록
- POST /api/self-healing/recovery/approve/ - 복구 승인
- POST /api/self-healing/recovery/reject/ - 복구 거부
- GET  /api/self-healing/recovery/history/ - 복구 이력

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#10.2.4
"""

import logging
from typing import Optional
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.recovery_coordinator import (
    get_recovery_coordinator,
)
from selfhealing.services.coordination.recovery_circuit_breaker import (
    get_recovery_circuit_breaker,
)
from selfhealing.services.coordination.pending_recovery_approval import (
    get_pending_recovery_approval_manager,
)
from selfhealing.services.coordination.regional_recovery_policy import (
    get_regional_recovery_policy_engine,
)
from selfhealing.services.coordination.recovery_dashboard import (
    get_recovery_dashboard_service,
    get_status_display,
    get_status_color,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Recovery Status View
# =============================================================================

class RecoveryStatusView(APIView):
    """
    현재 복구 상태 조회.

    GET /api/self-healing/recovery/status/
    GET /api/self-healing/recovery/status/?namespace=seoul
    
    Returns:
        - status: 현재 상태 (NORMAL, EMERGENCY, RECOVERING, etc.)
        - active_session: 진행 중인 복구 세션 (있으면)
        - circuit_breaker: 회로 차단기 상태
        - pending_approvals: 대기 중인 승인 수
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get current recovery status."""
        namespace = request.query_params.get("namespace", "global")
        
        try:
            coordinator = get_recovery_coordinator()
            circuit_breaker = get_recovery_circuit_breaker()
            approval_manager = get_pending_recovery_approval_manager()
            policy_engine = get_regional_recovery_policy_engine()
            
            # 현재 상태
            current_status = coordinator.get_current_status(namespace)
            
            # 활성 세션
            active_session = coordinator.get_active_session(namespace)
            session_data = None
            if active_session:
                session_data = {
                    "session_id": active_session.session_id,
                    "status": active_session.status.value,
                    "current_step": active_session.current_step_index,
                    "total_steps": len(active_session.steps),
                    "started_at": active_session.started_at.isoformat() if active_session.started_at else None,
                    "namespace": active_session.namespace,
                }
            
            # 회로 차단기 상태
            cb_status = circuit_breaker.get_status(namespace)
            
            # 대기 중인 승인
            pending = approval_manager.list_pending_requests(namespace=namespace)
            
            # 리전 설정
            config = policy_engine.get_config(namespace)
            
            return Response({
                "status": current_status.value,
                "namespace": namespace,
                "active_session": session_data,
                "circuit_breaker": {
                    "state": cb_status.get("state", "unknown"),
                    "trip_count": cb_status.get("trip_count", 0),
                    "is_permanently_open": cb_status.get("is_permanently_open", False),
                },
                "pending_approvals_count": len(pending),
                "regional_config": {
                    "require_manual_approval": config.require_manual_approval,
                    "stability_check_minutes": config.stability_check_minutes,
                    "approval_timeout_minutes": config.approval_timeout_minutes,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            
        except Exception as e:
            logger.exception(f"[RecoveryStatusView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Recovery Start View
# =============================================================================

class RecoveryStartView(APIView):
    """
    복구 시작.

    POST /api/self-healing/recovery/start/
    
    Request Body:
        - namespace: 네임스페이스 (기본: global)
        - force: 강제 시작 여부 (기본: false)
        - skip_approval: 승인 건너뛰기 (기본: false, 권한 필요)
    
    Returns:
        - session_id: 시작된 세션 ID
        - status: 세션 상태
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Start recovery process."""
        namespace = request.data.get("namespace", "global")
        force = request.data.get("force", False)
        skip_approval = request.data.get("skip_approval", False)
        
        try:
            coordinator = get_recovery_coordinator()
            policy_engine = get_regional_recovery_policy_engine()
            approval_manager = get_pending_recovery_approval_manager()
            
            # 현재 상태 확인
            current_status = coordinator.get_current_status(namespace)
            
            if current_status == RecoveryStatus.RECOVERING:
                active = coordinator.get_active_session(namespace)
                return Response({
                    "error": "Recovery already in progress",
                    "session_id": active.session_id if active else None,
                    "status": current_status.value,
                }, status=status.HTTP_409_CONFLICT)
            
            # 리전 설정 확인
            config = policy_engine.get_config(namespace)
            
            # 수동 승인 필요 여부
            if config.require_manual_approval and not skip_approval:
                # 기존 대기 중인 요청 확인
                existing = approval_manager.get_request_by_session_or_pending(
                    namespace=namespace
                )
                
                if existing is None:
                    # 새 승인 요청 생성
                    request_obj = approval_manager.create_request(
                        session_id=f"manual-{namespace}-{datetime.now(timezone.utc).isoformat()}",
                        namespace=namespace,
                        trigger_level="MANUAL",
                        timeout_minutes=config.approval_timeout_minutes,
                        metadata={
                            "requested_by": str(request.user),
                            "force": force,
                        },
                    )
                    
                    return Response({
                        "message": "Approval required before starting recovery",
                        "approval_request_id": request_obj.request_id,
                        "status": RecoveryStatus.READY_TO_RESTORE.value,
                        "approval_timeout_minutes": config.approval_timeout_minutes,
                    }, status=status.HTTP_202_ACCEPTED)
                
                if existing.status.value == "pending":
                    return Response({
                        "message": "Waiting for approval",
                        "approval_request_id": existing.request_id,
                        "status": RecoveryStatus.READY_TO_RESTORE.value,
                    }, status=status.HTTP_202_ACCEPTED)
            
            # 복구 시작
            session = coordinator.start_recovery(
                namespace=namespace,
                trigger_source="manual" if not force else "force",
            )
            
            logger.info(
                f"[RecoveryStartView] Recovery started: "
                f"session_id={session.session_id}, namespace={namespace}, "
                f"user={request.user}"
            )
            
            return Response({
                "session_id": session.session_id,
                "status": session.status.value,
                "namespace": namespace,
                "steps": [s.name for s in session.steps],
                "message": "Recovery started successfully",
            }, status=status.HTTP_201_CREATED)
            
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"[RecoveryStartView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Recovery Abort View
# =============================================================================

class RecoveryAbortView(APIView):
    """
    복구 중단.

    POST /api/self-healing/recovery/abort/
    
    Request Body:
        - session_id: 중단할 세션 ID
        - reason: 중단 사유
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Abort recovery process."""
        session_id = request.data.get("session_id")
        reason = request.data.get("reason", "Manual abort by user")
        namespace = request.data.get("namespace", "global")
        
        try:
            coordinator = get_recovery_coordinator()
            
            # session_id 없으면 활성 세션 사용
            if not session_id:
                active = coordinator.get_active_session(namespace)
                if active:
                    session_id = active.session_id
            
            if not session_id:
                return Response(
                    {"error": "No active recovery session to abort"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            
            result = coordinator.abort_recovery(
                session_id=session_id,
                reason=f"{reason} (by {request.user})",
            )
            
            if result:
                logger.info(
                    f"[RecoveryAbortView] Recovery aborted: "
                    f"session_id={session_id}, user={request.user}"
                )
                
                return Response({
                    "session_id": session_id,
                    "status": RecoveryStatus.ABORTED.value,
                    "reason": reason,
                    "message": "Recovery aborted successfully",
                })
            else:
                return Response(
                    {"error": f"Session not found or already completed: {session_id}"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            
        except Exception as e:
            logger.exception(f"[RecoveryAbortView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Pending Approvals View
# =============================================================================

class RecoveryPendingApprovalsView(APIView):
    """
    대기 중인 승인 목록.

    GET /api/self-healing/recovery/pending-approvals/
    GET /api/self-healing/recovery/pending-approvals/?namespace=seoul
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get pending approval requests."""
        namespace = request.query_params.get("namespace")  # None이면 전체
        
        try:
            manager = get_pending_recovery_approval_manager()
            
            pending = manager.list_pending_requests(namespace=namespace)
            
            return Response({
                "pending_approvals": [r.to_dict() for r in pending],
                "total_count": len(pending),
                "namespace_filter": namespace,
            })
            
        except Exception as e:
            logger.exception(f"[RecoveryPendingApprovalsView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Recovery Approve View
# =============================================================================

class RecoveryApproveView(APIView):
    """
    복구 승인.

    POST /api/self-healing/recovery/approve/
    
    Request Body:
        - request_id: 승인할 요청 ID
        - reason: 승인 사유 (선택)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Approve a recovery request."""
        request_id = request.data.get("request_id")
        reason = request.data.get("reason", "")
        auto_start = request.data.get("auto_start", True)
        
        if not request_id:
            return Response(
                {"error": "request_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            manager = get_pending_recovery_approval_manager()
            coordinator = get_recovery_coordinator()
            
            result = manager.approve(
                request_id=request_id,
                approved_by=str(request.user),
                reason=reason,
            )
            
            if result is None:
                return Response(
                    {"error": f"Request not found: {request_id}"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            
            logger.info(
                f"[RecoveryApproveView] Approved: "
                f"request_id={request_id}, user={request.user}"
            )
            
            response_data = {
                "request_id": request_id,
                "status": result.status.value,
                "approved_by": result.approved_by,
                "approved_at": result.approved_at.isoformat() if result.approved_at else None,
            }
            
            # 승인 후 자동 시작
            if auto_start:
                try:
                    session = coordinator.start_recovery(
                        namespace=result.namespace,
                        trigger_source="approved",
                    )
                    response_data["session_id"] = session.session_id
                    response_data["recovery_started"] = True
                except ValueError as e:
                    response_data["recovery_started"] = False
                    response_data["recovery_error"] = str(e)
            
            return Response(response_data)
            
        except Exception as e:
            logger.exception(f"[RecoveryApproveView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Recovery Reject View
# =============================================================================

class RecoveryRejectView(APIView):
    """
    복구 거부.

    POST /api/self-healing/recovery/reject/
    
    Request Body:
        - request_id: 거부할 요청 ID
        - reason: 거부 사유
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Reject a recovery request."""
        request_id = request.data.get("request_id")
        reason = request.data.get("reason", "")
        
        if not request_id:
            return Response(
                {"error": "request_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        if not reason:
            return Response(
                {"error": "reason is required for rejection"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            manager = get_pending_recovery_approval_manager()
            
            result = manager.reject(
                request_id=request_id,
                rejected_by=str(request.user),
                reason=reason,
            )
            
            if result is None:
                return Response(
                    {"error": f"Request not found: {request_id}"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            
            logger.info(
                f"[RecoveryRejectView] Rejected: "
                f"request_id={request_id}, reason={reason}, user={request.user}"
            )
            
            return Response({
                "request_id": request_id,
                "status": result.status.value,
                "rejected_by": result.approved_by,
                "rejection_reason": reason,
            })
            
        except Exception as e:
            logger.exception(f"[RecoveryRejectView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Recovery History View
# =============================================================================

class RecoveryHistoryView(APIView):
    """
    복구 이력 조회.

    GET /api/self-healing/recovery/history/
    GET /api/self-healing/recovery/history/?namespace=seoul&limit=10
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get recovery history."""
        namespace = request.query_params.get("namespace")
        limit = int(request.query_params.get("limit", 20))
        
        try:
            coordinator = get_recovery_coordinator()
            
            # 세션 히스토리 조회
            history = coordinator.get_session_history(
                namespace=namespace,
                limit=limit,
            )
            
            return Response({
                "history": [
                    {
                        "session_id": s.session_id,
                        "status": s.status.value,
                        "namespace": s.namespace,
                        "started_at": s.started_at.isoformat() if s.started_at else None,
                        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                        "steps_completed": sum(1 for step in s.steps if step.completed),
                        "total_steps": len(s.steps),
                    }
                    for s in history
                ],
                "total_count": len(history),
                "namespace_filter": namespace,
            })
            
        except Exception as e:
            logger.exception(f"[RecoveryHistoryView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Recovery Dashboard Widget View
# =============================================================================

class RecoveryDashboardWidgetView(APIView):
    """
    복구 대시보드 위젯 데이터.

    GET /api/self-healing/recovery/widget/
    
    대시보드에 표시할 요약 정보를 반환합니다.
    
    비즈니스 로직은 RecoveryDashboardService로 분리되어 있습니다.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get dashboard widget data."""
        namespace = request.query_params.get("namespace", "global")
        
        try:
            service = get_recovery_dashboard_service()
            widget_data = service.get_widget_data(namespace=namespace)
            return Response(widget_data.to_dict())
            
        except Exception as e:
            logger.exception(f"[RecoveryDashboardWidgetView] Error: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Helper Functions (deprecated - use recovery_dashboard.py instead)
# =============================================================================

def _get_status_display(recovery_status: RecoveryStatus) -> str:
    """상태 표시 문자열."""
    displays = {
        RecoveryStatus.NORMAL: "정상",
        RecoveryStatus.EMERGENCY: "비상",
        RecoveryStatus.RECOVERING: "복구 중",
        RecoveryStatus.READY_TO_RESTORE: "복구 대기",
        RecoveryStatus.COMPLETED: "완료",
        RecoveryStatus.ABORTED: "중단됨",
        RecoveryStatus.FAILED: "실패",
    }
    return displays.get(status, status.value)


def _get_status_color(status: RecoveryStatus) -> str:
    """상태 색상."""
    colors = {
        RecoveryStatus.NORMAL: "green",
        RecoveryStatus.EMERGENCY: "red",
        RecoveryStatus.RECOVERING: "yellow",
        RecoveryStatus.READY_TO_RESTORE: "orange",
        RecoveryStatus.COMPLETED: "green",
        RecoveryStatus.ABORTED: "gray",
        RecoveryStatus.FAILED: "red",
    }
    return colors.get(status, "gray")


def _get_session_progress(session) -> dict:
    """세션 진행률."""
    if not session:
        return {"percent": 0, "current_step": None, "total_steps": 0}
    
    completed = sum(1 for s in session.steps if s.completed)
    total = len(session.steps)
    current = session.steps[session.current_step_index] if session.current_step_index < total else None
    
    return {
        "percent": int((completed / total) * 100) if total > 0 else 0,
        "current_step": current.name if current else None,
        "completed_steps": completed,
        "total_steps": total,
    }


def _get_available_actions(status: RecoveryStatus, session, pending) -> list:
    """사용 가능한 액션 목록."""
    actions = []
    
    if status == RecoveryStatus.EMERGENCY:
        actions.append({
            "action": "start_recovery",
            "label": "복구 시작",
            "enabled": True,
        })
    
    if status == RecoveryStatus.RECOVERING and session:
        actions.append({
            "action": "abort_recovery",
            "label": "복구 중단",
            "enabled": True,
        })
    
    if pending:
        actions.append({
            "action": "approve_recovery",
            "label": f"승인 대기 ({len(pending)}건)",
            "enabled": True,
            "urgent": any(p.get_waiting_time_minutes() > 30 for p in pending),
        })
    
    return actions
