"""
Hook Registry System for Self-Healing Infrastructure.

Enterprise-grade hook system for runtime behavior modification.
Supports priority-based execution and audit logging integration.

Key Features:
- Priority-based hook execution (higher priority runs first)
- Audit trail for all bypass decisions
- Environment-conditional hook registration
- Thread-safe operation

Usage:
    # Register a bypass hook (in testing/resilience module)
    from selfhealing.core.hooks import BypassRegistry
    
    def platinum_bypass_hook(request) -> bool:
        return request.headers.get("X-Test-Mode") == "platinum"
    
    BypassRegistry.register(
        platinum_bypass_hook,
        priority=1000,
        name="platinum_mode",
        description="Bypass for PLATINUM stress testing"
    )
    
    # Check bypass in middleware
    should_bypass, reason = BypassRegistry.should_bypass(request)
    if should_bypass:
        audit_logger.log_bypass(reason, request)

Reference:
- docs/self_healing/52_HOOK_REGISTRY_ARCHITECTURE.md
- Big 4 Audit Compliance: All bypass decisions are logged
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


# =============================================================================
# Hook Data Structures
# =============================================================================


@dataclass
class HookInfo:
    """Metadata for a registered hook."""
    
    func: Callable[["HttpRequest"], bool]
    priority: int
    name: str
    description: str
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    invocation_count: int = 0
    bypass_count: int = 0
    
    def __call__(self, request: "HttpRequest") -> bool:
        """Execute the hook and track statistics."""
        self.invocation_count += 1
        result = self.func(request)
        if result:
            self.bypass_count += 1
        return result


@dataclass
class BypassResult:
    """Result of a bypass check with audit information."""
    
    bypassed: bool
    reason: str
    hook_name: str
    priority: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_path: str = ""
    request_method: str = ""
    
    def to_audit_dict(self) -> dict[str, Any]:
        """Convert to audit log format."""
        return {
            "event_type": "bypass_decision",
            "bypassed": self.bypassed,
            "reason": self.reason,
            "hook_name": self.hook_name,
            "priority": self.priority,
            "timestamp": self.timestamp,
            "request_path": self.request_path,
            "request_method": self.request_method,
        }


# =============================================================================
# Bypass Registry (Singleton)
# =============================================================================


class BypassRegistry:
    """
    Enterprise-grade Hook Registry for bypass decisions.
    
    All bypass decisions are:
    1. Priority-ordered (higher priority runs first)
    2. Audit-logged (for compliance)
    3. Thread-safe
    
    Architecture:
    - Production code calls should_bypass() only
    - Test/Resilience modules register hooks conditionally
    - All decisions are traceable
    
    Example:
        # In rate_limit.py (production code)
        result = BypassRegistry.should_bypass(request)
        if result.bypassed:
            self._log_bypass_audit(result)
            return True
    """
    
    _hooks: list[HookInfo] = []
    _lock = threading.Lock()
    _audit_logger: Optional[Any] = None
    _initialized: bool = False
    
    @classmethod
    def register(
        cls,
        hook_func: Callable[["HttpRequest"], bool],
        priority: int = 100,
        name: Optional[str] = None,
        description: str = "",
    ) -> None:
        """
        Register a bypass hook.
        
        Args:
            hook_func: Function that returns True if bypass should occur
            priority: Higher priority hooks run first (default: 100)
            name: Hook identifier for audit logs (default: function name)
            description: Human-readable description
        
        Example:
            def my_hook(request):
                return request.headers.get("X-Test") == "true"
            
            BypassRegistry.register(my_hook, priority=500, name="test_mode")
        """
        hook_name = name or hook_func.__name__
        
        with cls._lock:
            # Check for duplicate registration
            existing = [h for h in cls._hooks if h.name == hook_name]
            if existing:
                logger.warning(f"[BypassRegistry] Hook '{hook_name}' already registered, skipping")
                return
            
            hook_info = HookInfo(
                func=hook_func,
                priority=priority,
                name=hook_name,
                description=description,
            )
            
            cls._hooks.append(hook_info)
            # Sort by priority descending (higher priority first)
            cls._hooks.sort(key=lambda h: -h.priority)
            
            logger.info(
                f"[BypassRegistry] Registered hook: {hook_name} "
                f"(priority={priority}, total_hooks={len(cls._hooks)})"
            )
    
    @classmethod
    def unregister(cls, name: str) -> bool:
        """
        Unregister a hook by name.
        
        Returns:
            True if hook was found and removed
        """
        with cls._lock:
            original_count = len(cls._hooks)
            cls._hooks = [h for h in cls._hooks if h.name != name]
            removed = len(cls._hooks) < original_count
            
            if removed:
                logger.info(f"[BypassRegistry] Unregistered hook: {name}")
            
            return removed
    
    @classmethod
    def should_bypass(cls, request: "HttpRequest") -> BypassResult:
        """
        Check if request should bypass (e.g., rate limiting).
        
        Executes hooks in priority order until one returns True.
        All decisions are returned with audit information.
        
        Args:
            request: Django HttpRequest object
        
        Returns:
            BypassResult with bypass decision and audit info
        """
        request_path = getattr(request, "path", "unknown")
        request_method = getattr(request, "method", "unknown")
        
        with cls._lock:
            hooks_snapshot = list(cls._hooks)
        
        for hook in hooks_snapshot:
            try:
                if hook(request):
                    result = BypassResult(
                        bypassed=True,
                        reason=hook.description or f"Hook '{hook.name}' triggered",
                        hook_name=hook.name,
                        priority=hook.priority,
                        request_path=request_path,
                        request_method=request_method,
                    )
                    
                    # Audit log
                    cls._log_bypass(result)
                    
                    return result
                    
            except Exception as e:
                logger.error(f"[BypassRegistry] Hook '{hook.name}' raised exception: {e}")
                # Continue to next hook on error (fail-open for hooks)
        
        # No bypass
        return BypassResult(
            bypassed=False,
            reason="No bypass hook triggered",
            hook_name="",
            priority=0,
            request_path=request_path,
            request_method=request_method,
        )
    
    @classmethod
    def _log_bypass(cls, result: BypassResult) -> None:
        """Log bypass decision to audit system."""
        # Standard logging
        logger.info(
            f"[BypassRegistry] BYPASS GRANTED: "
            f"hook={result.hook_name}, reason={result.reason}, "
            f"path={result.request_path}"
        )
        
        # Audit system integration (lazy init)
        try:
            if cls._audit_logger is None:
                from selfhealing.audit import get_audit_logger
                cls._audit_logger = get_audit_logger()
            
            if cls._audit_logger:
                cls._audit_logger.log(result.to_audit_dict())
        except Exception as e:
            # Audit failure should not block bypass
            logger.warning(f"[BypassRegistry] Audit log failed: {e}")
    
    @classmethod
    def get_registered_hooks(cls) -> list[dict[str, Any]]:
        """Get list of all registered hooks (for debugging/monitoring)."""
        with cls._lock:
            return [
                {
                    "name": h.name,
                    "priority": h.priority,
                    "description": h.description,
                    "registered_at": h.registered_at,
                    "invocation_count": h.invocation_count,
                    "bypass_count": h.bypass_count,
                }
                for h in cls._hooks
            ]
    
    @classmethod
    def clear_all(cls) -> int:
        """
        Clear all registered hooks. For testing only.
        
        Returns:
            Number of hooks cleared
        """
        with cls._lock:
            count = len(cls._hooks)
            cls._hooks = []
            logger.warning(f"[BypassRegistry] Cleared all {count} hooks")
            return count
    
    @classmethod
    def get_statistics(cls) -> dict[str, Any]:
        """Get registry statistics for monitoring."""
        with cls._lock:
            total_invocations = sum(h.invocation_count for h in cls._hooks)
            total_bypasses = sum(h.bypass_count for h in cls._hooks)
            
            return {
                "total_hooks": len(cls._hooks),
                "total_invocations": total_invocations,
                "total_bypasses": total_bypasses,
                "bypass_rate": total_bypasses / total_invocations if total_invocations > 0 else 0,
                "hooks": cls.get_registered_hooks(),
            }


# =============================================================================
# Convenience Functions
# =============================================================================


def register_bypass_hook(
    priority: int = 100,
    name: Optional[str] = None,
    description: str = "",
) -> Callable:
    """
    Decorator for registering bypass hooks.
    
    Example:
        @register_bypass_hook(priority=1000, name="platinum")
        def platinum_hook(request):
            return request.headers.get("X-Test-Mode") == "platinum"
    """
    def decorator(func: Callable[["HttpRequest"], bool]) -> Callable:
        BypassRegistry.register(func, priority=priority, name=name, description=description)
        return func
    return decorator


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "BypassRegistry",
    "BypassResult",
    "HookInfo",
    "register_bypass_hook",
]
