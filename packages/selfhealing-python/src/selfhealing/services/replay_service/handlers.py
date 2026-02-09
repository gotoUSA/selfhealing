"""
Replay Handlers - Domain-specific replay handler registry.

ReplayHandler ABC, DefaultReplayHandler, 핸들러 레지스트리를 제공합니다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .models import ReplayResult

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationData


# =============================================================================
# Replay Handlers (Domain-specific)
# =============================================================================


class ReplayHandler(ABC):
    """
    Abstract base class for domain-specific replay handlers.

    Each domain (payment, point, inventory, etc.) should implement
    its own replay logic by subclassing this.

    Note: Handlers should work with FailedOperationData (a simple dataclass)
    not Django models. The handler receives operation data and should
    use injected services for actual operations.
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """Return the domain this handler handles."""
        pass

    @abstractmethod
    def replay(self, failed_op: FailedOperationData) -> ReplayResult:
        """
        Execute replay for a single failed operation.

        Args:
            failed_op: The FailedOperationData to replay

        Returns:
            ReplayResult indicating success or failure
        """
        pass

    @abstractmethod
    def can_replay(self, failed_op: FailedOperationData) -> tuple[bool, str]:
        """
        Check if the operation can be replayed.

        Args:
            failed_op: The FailedOperationData to check

        Returns:
            Tuple of (can_replay: bool, reason: str)
        """
        pass


class DefaultReplayHandler(ReplayHandler):
    """
    Default replay handler that returns an error.

    This handler is used when no specific handler is registered for a domain.
    Users should register their own handlers for each domain they need.
    """

    def __init__(self, domain_name: str):
        self._domain = domain_name

    @property
    def domain(self) -> str:
        return self._domain

    def can_replay(self, failed_op: FailedOperationData) -> tuple[bool, str]:
        return False, f"No replay handler registered for domain '{self._domain}'"

    def replay(self, failed_op: FailedOperationData) -> ReplayResult:
        return ReplayResult.failed(
            failed_op.id,
            f"No replay handler registered for domain '{self._domain}'. "
            "Please register a handler using register_replay_handler().",
        )


# =============================================================================
# Domain-Specific Handlers (MOVED TO ADAPTER LAYER)
# =============================================================================
# NOTE: Domain-specific handlers have been moved to the adapter layer.
#
# For Django projects, create your own handlers:
#   from myapp.services.self_healing.replay_handlers import (
#       OrderReplayHandler,
#       NotificationReplayHandler,
#   )
#
# Or register your own handlers:
#   from selfhealing.services.replay_service import register_replay_handler, ReplayHandler
#
#   class MyDomainHandler(ReplayHandler):
#       @property
#       def domain(self) -> str:
#           return "my_domain"
#
#       def can_replay(self, failed_op) -> tuple[bool, str]:
#           # Your validation logic
#           return True, ""
#
#       def replay(self, failed_op) -> ReplayResult:
#           # Your replay logic
#           return ReplayResult.succeeded(failed_op.id, "Done")
#
#   register_replay_handler(MyDomainHandler())
# =============================================================================


# =============================================================================
# Replay Handler Registry
# =============================================================================


_replay_handlers: dict[str, ReplayHandler] = {}


def register_replay_handler(handler: ReplayHandler) -> None:
    """Register a replay handler for a domain."""
    _replay_handlers[handler.domain] = handler


def get_replay_handler(domain: str) -> ReplayHandler:
    """
    Get the replay handler for a domain.

    Args:
        domain: The domain name

    Returns:
        ReplayHandler instance for the domain
    """
    if domain in _replay_handlers:
        return _replay_handlers[domain]

    # Return default handler if no specific handler exists
    return DefaultReplayHandler(domain)


# NOTE: No default handlers registered in core package.
# Domain-specific handlers should be registered by the adapter layer.
# Example (in your adapter):
#   from selfhealing.services.replay_service import register_replay_handler
#   from myapp.services.replay_handlers import OrderReplayHandler
#   register_replay_handler(OrderReplayHandler())
