"""
Base utilities for In-Memory Repository Implementations.

Provides common utilities and helper functions shared across
all in-memory repository implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)
