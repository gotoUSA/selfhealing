"""
Chaos Experiment Protocols.

Contains protocol definitions for audit recording and kill switch integration.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol


class AuditRecorderProtocol(Protocol):
    """Protocol for audit recording."""

    def record(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record an audit event."""
        ...


class KillSwitchProtocol(Protocol):
    """Protocol for kill switch integration."""

    def is_killed(self, experiment_id: str) -> bool:
        """Check if experiment should be killed."""
        ...

    def kill(self, experiment_id: str, reason: str) -> None:
        """Kill an experiment."""
        ...
