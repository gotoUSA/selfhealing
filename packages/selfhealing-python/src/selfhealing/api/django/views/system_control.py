"""
System Control API - Global Kill Switch.

Provides API endpoints to enable/disable the entire self-healing system
at runtime without requiring server restart.

Endpoints:
- GET  /api/self-healing/system/status/   - Get system status
- POST /api/self-healing/system/enable/   - Enable self-healing
- POST /api/self-healing/system/disable/  - Disable self-healing (Kill Switch)

Note:
- 비즈니스 로직은 SystemControlManager(services/system_control.py)로 분리됨
- View는 Request/Response 처리만 담당

Configuration:
    # Django settings.py
    SELFHEALING_STATE_BACKEND = "redis"  # or "file" (default)
    SELFHEALING_REDIS_URL = "redis://localhost:6379/0"
    
    # Or environment variables
    SELFHEALING_STATE_BACKEND=redis
    SELFHEALING_REDIS_URL=redis://localhost:6379/0
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

# Import from services layer
from selfhealing.services.system_control import (
    SystemControlManager,
    SystemState,
    get_system_control,
    is_selfhealing_enabled,
    is_dry_run,
    should_execute_action,
)

logger = logging.getLogger(__name__)


# Re-export for backward compatibility
__all__ = [
    # Classes
    "SystemControlManager",
    "SystemState",
    # Functions
    "get_system_control",
    "is_selfhealing_enabled",
    "is_dry_run",
    "should_execute_action",
    # Views
    "SystemStatusView",
    "SystemEnableView",
    "SystemDisableView",
    "DryRunEnableView",
    "DryRunDisableView",
]


# =============================================================================
# API Views
# =============================================================================


class SystemStatusView(APIView):
    """
    GET /api/self-healing/system/status/
    
    Returns the current system status including:
    - enabled: Whether self-healing is active
    - disabled_at: When it was disabled (if applicable)
    - disabled_by: Who disabled it
    - disabled_reason: Why it was disabled
    """
    
    def get(self, request: Request) -> Response:
        manager = get_system_control()
        state = manager.get_state()
        backend_info = manager.get_backend_info()
        
        return Response({
            "system": "selfhealing",
            "status": "enabled" if state.enabled else "disabled",
            **state.to_dict(),
            "backend": backend_info,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


class SystemEnableView(APIView):
    """
    POST /api/self-healing/system/enable/
    
    Re-enables the self-healing system after it was disabled.
    
    Request body (optional):
        {
            "reason": "Maintenance complete"
        }
    """
    permission_classes = [IsAdminUser]
    
    def post(self, request: Request) -> Response:
        reason = request.data.get("reason", "")
        actor = getattr(request.user, "username", "api")
        
        state = get_system_control().enable(actor=actor, reason=reason)
        
        return Response({
            "success": True,
            "message": "Self-healing system enabled",
            "state": state.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


class SystemDisableView(APIView):
    """
    POST /api/self-healing/system/disable/
    
    Disables the entire self-healing system (Kill Switch).
    
    Use this for:
    - Emergency situations where healing is causing issues
    - Maintenance windows
    - Debugging
    
    Request body:
        {
            "reason": "Emergency maintenance"  // Required
        }
    """
    permission_classes = [IsAdminUser]
    
    def post(self, request: Request) -> Response:
        reason = request.data.get("reason", "")
        
        if not reason:
            return Response(
                {
                    "success": False,
                    "error": "reason is required",
                    "message": "Please provide a reason for disabling the system",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        actor = getattr(request.user, "username", "api")
        
        state = get_system_control().disable(actor=actor, reason=reason)
        
        return Response({
            "success": True,
            "message": "Self-healing system DISABLED (Kill Switch activated)",
            "warning": "All self-healing operations are now stopped",
            "state": state.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


class DryRunEnableView(APIView):
    """
    POST /api/self-healing/system/dry-run/enable/
    
    Enables dry run mode for safe testing on production traffic.
    
    In dry run mode:
    - All self-healing detection logic runs normally
    - Circuit breaker triggers are detected
    - DLQ candidates are identified
    - BUT no actual actions are taken
    - All "would-be" actions are logged for review
    
    Use this to:
    - Test self-healing on production before going live
    - Validate thresholds and rules
    - Build confidence before full deployment
    """
    permission_classes = [IsAdminUser]
    
    def post(self, request: Request) -> Response:
        actor = getattr(request.user, "username", "api")
        
        state = get_system_control().enable_dry_run(actor=actor)
        
        return Response({
            "success": True,
            "message": "Dry run mode ENABLED",
            "info": "Self-healing will observe and log but not take actions",
            "state": state.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


class DryRunDisableView(APIView):
    """
    POST /api/self-healing/system/dry-run/disable/
    
    Disables dry run mode - self-healing goes LIVE.
    
    After disabling dry run:
    - All self-healing actions will be executed for real
    - Circuit breakers will actually trip
    - DLQ entries will be created
    - Retries will be attempted
    
    Request body:
        {
            "confirm": true  // Required confirmation
        }
    """
    permission_classes = [IsAdminUser]
    
    def post(self, request: Request) -> Response:
        confirm = request.data.get("confirm", False)
        
        if not confirm:
            return Response(
                {
                    "success": False,
                    "error": "confirmation required",
                    "message": "Set 'confirm': true to disable dry run mode and go LIVE",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        actor = getattr(request.user, "username", "api")
        
        state = get_system_control().disable_dry_run(actor=actor)
        
        return Response({
            "success": True,
            "message": "Dry run mode DISABLED - Self-healing is now LIVE",
            "warning": "All self-healing actions will now be executed for real",
            "state": state.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
