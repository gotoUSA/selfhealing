"""
Chaos Scheduler Helpers.

Singleton factory functions for ChaosSchedulerService.
"""

from __future__ import annotations

import threading
from typing import Optional

from .service import ChaosSchedulerService

# Singleton instance
_chaos_scheduler: Optional[ChaosSchedulerService] = None
_scheduler_lock = threading.Lock()


def get_chaos_scheduler() -> ChaosSchedulerService:
    """
    Get the singleton ChaosSchedulerService instance.
    
    Returns:
        ChaosSchedulerService singleton
    """
    global _chaos_scheduler
    
    if _chaos_scheduler is None:
        with _scheduler_lock:
            if _chaos_scheduler is None:
                _chaos_scheduler = ChaosSchedulerService()
                _chaos_scheduler._load_config()
    
    return _chaos_scheduler


def reset_chaos_scheduler() -> None:
    """
    Reset the singleton (for testing).
    
    Clears the singleton instance so a fresh one is created on next access.
    """
    global _chaos_scheduler
    with _scheduler_lock:
        _chaos_scheduler = None
