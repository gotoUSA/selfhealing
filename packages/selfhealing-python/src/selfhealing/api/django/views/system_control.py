"""
System Control API - Global Kill Switch.

Provides API endpoints to enable/disable the entire self-healing system
at runtime without requiring server restart.

Endpoints:
- GET  /api/self-healing/system/status/   - Get system status
- POST /api/self-healing/system/enable/   - Enable self-healing
- POST /api/self-healing/system/disable/  - Disable self-healing (Kill Switch)

State persistence backends:
- File (default): Single server deployments
- Redis: Multi-server deployments with shared state

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
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.core.state_backend import get_state_backend, StateBackend

logger = logging.getLogger(__name__)


@dataclass
class SystemState:
    """Self-healing system state."""
    enabled: bool = True
    dry_run: bool = False  # Dry run mode: observe only, no actual actions
    disabled_at: Optional[str] = None
    disabled_by: Optional[str] = None
    disabled_reason: Optional[str] = None
    enabled_at: Optional[str] = None
    enabled_by: Optional[str] = None
    dry_run_enabled_at: Optional[str] = None
    dry_run_enabled_by: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SystemState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# State key for backend storage
STATE_KEY = "system_control"


class SystemControlManager:
    """
    Manages global self-healing system state with pluggable backend.
    
    Features:
    - Thread-safe state management
    - Pluggable backends (File, Redis, Memory)
    - Automatic state recovery on restart
    - Shared state across servers (with Redis backend)
    
    Usage:
        manager = SystemControlManager()
        
        # Check if system is enabled
        if manager.is_enabled():
            do_healing()
        
        # Disable system (Kill Switch)
        manager.disable(reason="Emergency maintenance", actor="admin")
        
        # Re-enable system
        manager.enable(actor="admin")
    
    Configuration:
        # Django settings.py
        SELFHEALING_STATE_BACKEND = "redis"  # or "file"
        SELFHEALING_REDIS_URL = "redis://localhost:6379/0"
    """
    
    _instance: Optional["SystemControlManager"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._state_lock = threading.Lock()
        self._backend: StateBackend = get_state_backend()
        self._load_state()
        self._initialized = True
    
    def _load_state(self) -> None:
        """Load state from backend."""
        try:
            data = self._backend.get(STATE_KEY)
            if data:
                self._cached_state = SystemState.from_dict(data)
                logger.info(
                    f"[SystemControl] Loaded state: enabled={self._cached_state.enabled}, "
                    f"backend={type(self._backend).__name__}"
                )
            else:
                self._cached_state = SystemState()
                logger.info(f"[SystemControl] No existing state, using defaults")
        except Exception as e:
            logger.warning(f"[SystemControl] Could not load state: {e}")
            self._cached_state = SystemState()
    
    def _save_state(self) -> None:
        """Save state to backend."""
        try:
            self._backend.set(STATE_KEY, self._cached_state.to_dict())
            logger.debug(f"[SystemControl] State saved")
        except Exception as e:
            logger.error(f"[SystemControl] Failed to save state: {e}")
    
    def _refresh_state(self) -> SystemState:
        """Refresh state from backend (for multi-server sync)."""
        data = self._backend.get(STATE_KEY)
        if data:
            self._cached_state = SystemState.from_dict(data)
        return self._cached_state
    
    def is_enabled(self) -> bool:
        """
        Check if self-healing system is enabled.
        
        Note: For Redis backend, this reads from cache for performance.
        Use get_state() for fresh read from backend.
        """
        with self._state_lock:
            return self._cached_state.enabled
    
    def get_state(self, refresh: bool = True) -> SystemState:
        """
        Get current system state.
        
        Args:
            refresh: If True, refresh from backend (for multi-server sync)
        """
        with self._state_lock:
            if refresh:
                self._refresh_state()
            return SystemState.from_dict(self._cached_state.to_dict())
    
    def enable(self, actor: str = "system", reason: str = "") -> SystemState:
        """Enable self-healing system."""
        with self._state_lock:
            # Refresh first for multi-server consistency
            self._refresh_state()
            was_enabled = self._cached_state.enabled
            
            self._cached_state.enabled = True
            self._cached_state.enabled_at = datetime.now(timezone.utc).isoformat()
            self._cached_state.enabled_by = actor
            self._save_state()
            
            if not was_enabled:
                logger.info(
                    f"[SystemControl] System ENABLED by {actor}. Reason: {reason or 'N/A'}"
                )
            
            return SystemState.from_dict(self._cached_state.to_dict())
    
    def disable(self, actor: str = "system", reason: str = "") -> SystemState:
        """
        Disable self-healing system (Kill Switch).
        
        This immediately stops all self-healing operations.
        State is persisted and shared across servers (with Redis backend).
        """
        with self._state_lock:
            # Refresh first for multi-server consistency
            self._refresh_state()
            was_enabled = self._cached_state.enabled
            
            self._cached_state.enabled = False
            self._cached_state.disabled_at = datetime.now(timezone.utc).isoformat()
            self._cached_state.disabled_by = actor
            self._cached_state.disabled_reason = reason
            self._save_state()
            
            if was_enabled:
                logger.warning(
                    f"[SystemControl] System DISABLED (Kill Switch) by {actor}. "
                    f"Reason: {reason or 'N/A'}"
                )
            
            return SystemState.from_dict(self._cached_state.to_dict())
    
    def enable_dry_run(self, actor: str = "system") -> SystemState:
        """
        Enable dry run mode.
        
        In dry run mode:
        - All self-healing logic executes normally
        - But actual actions (circuit breaking, retries, DLQ writes) are skipped
        - Actions that "would have been taken" are logged instead
        
        Use this to safely test self-healing on production traffic.
        """
        with self._state_lock:
            self._refresh_state()
            was_dry_run = self._cached_state.dry_run
            
            self._cached_state.dry_run = True
            self._cached_state.dry_run_enabled_at = datetime.now(timezone.utc).isoformat()
            self._cached_state.dry_run_enabled_by = actor
            self._save_state()
            
            if not was_dry_run:
                logger.info(
                    f"[SystemControl] DRY RUN mode ENABLED by {actor}. "
                    "Actions will be logged but not executed."
                )
            
            return SystemState.from_dict(self._cached_state.to_dict())
    
    def disable_dry_run(self, actor: str = "system") -> SystemState:
        """
        Disable dry run mode (go live).
        
        After disabling dry run, all self-healing actions will be executed for real.
        """
        with self._state_lock:
            self._refresh_state()
            was_dry_run = self._cached_state.dry_run
            
            self._cached_state.dry_run = False
            self._save_state()
            
            if was_dry_run:
                logger.warning(
                    f"[SystemControl] DRY RUN mode DISABLED by {actor}. "
                    "Self-healing is now LIVE."
                )
            
            return SystemState.from_dict(self._cached_state.to_dict())
    
    def is_dry_run(self) -> bool:
        """Check if dry run mode is enabled."""
        with self._state_lock:
            return self._cached_state.dry_run
    
    def reset(self) -> None:
        """Reset to default state (enabled)."""
        with self._state_lock:
            self._cached_state = SystemState()
            self._save_state()
            logger.info("[SystemControl] System state reset to defaults")
    
    def get_backend_info(self) -> Dict[str, str]:
        """Get information about the current backend."""
        return {
            "backend_type": type(self._backend).__name__,
            "backend_class": f"{type(self._backend).__module__}.{type(self._backend).__name__}",
        }


# Global instance
_system_control: Optional[SystemControlManager] = None


def get_system_control() -> SystemControlManager:
    """Get the global system control manager."""
    global _system_control
    if _system_control is None:
        _system_control = SystemControlManager()
    return _system_control


def is_selfhealing_enabled() -> bool:
    """
    Quick check if self-healing is enabled.
    
    Use this at the start of any self-healing operation:
    
        from selfhealing.api.django.views.system_control import is_selfhealing_enabled
        
        def my_healing_function():
            if not is_selfhealing_enabled():
                return  # Kill switch is active
            
            # ... healing logic
    """
    return get_system_control().is_enabled()


def is_dry_run() -> bool:
    """
    Quick check if dry run mode is enabled.
    
    Use this before taking any action:
    
        from selfhealing.api.django.views.system_control import is_dry_run
        
        def trigger_circuit_breaker(service_name):
            if is_dry_run():
                logger.info(f"[DRY RUN] Would open circuit breaker for {service_name}")
                return
            
            # Actually open the circuit breaker
            circuit_breaker.open(service_name)
    """
    return get_system_control().is_dry_run()


def should_execute_action() -> bool:
    """
    Check if self-healing actions should be executed.
    
    Returns True only if:
    - System is enabled (kill switch off)
    - AND dry run mode is disabled
    
    Usage:
        from selfhealing.api.django.views.system_control import should_execute_action
        
        def my_healing_action():
            if not should_execute_action():
                if is_dry_run():
                    logger.info("[DRY RUN] Would have taken action X")
                return
            
            # Execute the actual action
    """
    manager = get_system_control()
    return manager.is_enabled() and not manager.is_dry_run()


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
