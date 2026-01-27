"""
Safety Guard Package.

Pre-flight safety checks for chaos experiments.
Implements error budget-based gating and system health verification.

Core Principle: "Never sacrifice production stability for testing."

Features:
- Error budget threshold checks (default: 20% minimum)
- System health verification
- Active incident detection
- Kill switch status check
- Deployment freeze detection

Usage:
    from selfhealing.services.chaos.safety_guard import (
        SafetyGuard,
        SafetyStatus,
        BlockReason,
        get_safety_guard,
    )
    
    # Get singleton instance
    guard = get_safety_guard()
    
    # Perform safety check
    result = guard.check(experiment_id="chaos-abc123")
    
    if not result.allowed:
        print(f"Blocked: {result.block_message}")
"""

# Enums
from .enums import BlockReason, SafetyStatus

# Models
from .models import SafetyCheckResult, SafetyConfig

# Main guard class
from .guard import SafetyGuard

# Singleton helpers
from .helpers import get_safety_guard, reset_safety_guard

# Resource Guard (X-Test 리소스 체크)
from .resource_guard import (
    ResourceGuard,
    ResourceStatus,
    ResourceCheckResult,
    get_resource_guard,
    reset_resource_guard,
)


__all__ = [
    # Enums
    "SafetyStatus",
    "BlockReason",
    # Models
    "SafetyConfig",
    "SafetyCheckResult",
    # Main class
    "SafetyGuard",
    # Helpers
    "get_safety_guard",
    "reset_safety_guard",
    # Resource Guard
    "ResourceGuard",
    "ResourceStatus",
    "ResourceCheckResult",
    "get_resource_guard",
    "reset_resource_guard",
]
