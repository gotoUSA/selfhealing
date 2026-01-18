"""
Safety Guard Helpers.

Singleton management and helper functions.
"""

from __future__ import annotations

import threading
from typing import Optional

from .guard import SafetyGuard


# =============================================================================
# Singleton
# =============================================================================


_safety_guard: Optional[SafetyGuard] = None
_guard_lock = threading.Lock()


def get_safety_guard() -> SafetyGuard:
    """Get the singleton SafetyGuard instance."""
    global _safety_guard
    
    if _safety_guard is None:
        with _guard_lock:
            if _safety_guard is None:
                _safety_guard = SafetyGuard()
                _safety_guard._load_config()
    
    return _safety_guard


def reset_safety_guard() -> None:
    """Reset the singleton (for testing)."""
    global _safety_guard
    with _guard_lock:
        _safety_guard = None
