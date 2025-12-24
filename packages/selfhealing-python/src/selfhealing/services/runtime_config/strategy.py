"""
Apply Strategy Handling.

Manages IMMEDIATE, DELAYED, and GRACEFUL apply strategies for configuration changes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from selfhealing.core.apply_strategy import (
    ApplyStrategy,
    get_default_apply_config,
    get_effective_apply_options,
)

logger = logging.getLogger(__name__)


class StrategyMixin:
    """Mixin providing apply strategy functionality."""

    def get_default_strategy(self, config_type: str) -> Dict[str, Any]:
        """Get the default apply strategy for a config type."""
        default = get_default_apply_config(config_type)
        return {
            "strategy": default.strategy.value,
            "delay_seconds": default.delay_seconds,
            "grace_timeout_seconds": default.grace_timeout_seconds,
            "warning_message": default.warning_message,
        }

    def update_with_strategy(
        self,
        config_type: str,
        changes: Dict[str, Any],
        changed_by: str = "system",
        reason: str = "",
        strategy: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        grace_timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update configuration with specified apply strategy.

        Args:
            config_type: Type of config (e.g., "circuit_breaker")
            changes: Dict of field -> new value
            changed_by: User or system that made the change
            reason: Reason for the change
            strategy: Apply strategy ("immediate", "delayed", "graceful")
            delay_seconds: Seconds to wait for "delayed" strategy
            grace_timeout_seconds: Max wait for "graceful" strategy

        Returns:
            Dict with:
            - status: "applied" | "scheduled" | "waiting"
            - config: Current/new config values
            - pending_id: ID if scheduled (for cancellation)
            - scheduled_at: When it will be applied (if delayed)
            - warning_message: Any warnings
        """
        # Get effective apply options
        apply_options = get_effective_apply_options(
            config_type,
            strategy=strategy,
            delay_seconds=delay_seconds,
            grace_timeout_seconds=grace_timeout_seconds,
        )

        # Get default config for warning message
        default_config = get_default_apply_config(config_type)

        # Filter changes to only valid fields
        current = self._get_config(config_type)
        valid_changes = {k: v for k, v in changes.items() if k in current and v is not None}

        if not valid_changes:
            return {
                "status": "error",
                "error": "No valid configuration changes provided",
            }

        # Handle based on strategy
        if apply_options.strategy == ApplyStrategy.IMMEDIATE:
            # Apply immediately
            new_config = self._update_config(
                config_type,
                changed_by=changed_by,
                reason=reason,
                **valid_changes
            )
            return {
                "status": "applied",
                "config": new_config,
                "applied_strategy": "immediate",
                "warning_message": default_config.warning_message,
            }

        elif apply_options.strategy == ApplyStrategy.DELAYED:
            # Schedule for later
            from selfhealing.services.pending_config import get_pending_config_service

            pending_service = get_pending_config_service()
            pending_change = pending_service.create_pending_change(
                config_type=config_type,
                changes=valid_changes,
                apply_options=apply_options,
                previous_values={k: current[k] for k in valid_changes.keys()},
            )

            return {
                "status": "scheduled",
                "pending_id": pending_change.id,
                "scheduled_at": pending_change.scheduled_at,
                "delay_seconds": apply_options.delay_seconds,
                "config_preview": {**current, **valid_changes},
                "applied_strategy": "delayed",
                "warning_message": default_config.warning_message,
                "cancel_available": True,
            }

        elif apply_options.strategy == ApplyStrategy.GRACEFUL:
            # Schedule graceful apply (will be handled by worker)
            from selfhealing.services.pending_config import get_pending_config_service

            pending_service = get_pending_config_service()
            pending_change = pending_service.create_pending_change(
                config_type=config_type,
                changes=valid_changes,
                apply_options=apply_options,
                previous_values={k: current[k] for k in valid_changes.keys()},
            )

            return {
                "status": "waiting",
                "pending_id": pending_change.id,
                "grace_timeout_seconds": apply_options.grace_timeout_seconds,
                "config_preview": {**current, **valid_changes},
                "applied_strategy": "graceful",
                "warning_message": default_config.warning_message or "Waiting for in-progress operations to complete",
                "cancel_available": True,
            }

        return {"status": "error", "error": "Unknown strategy"}

    def apply_pending_change(self, pending_id: str) -> Dict[str, Any]:
        """
        Apply a pending configuration change.

        Called by background worker when scheduled time arrives.
        """
        from selfhealing.services.pending_config import get_pending_config_service

        pending_service = get_pending_config_service()
        pending_change = pending_service.get_pending_change(pending_id)

        if not pending_change:
            return {"status": "error", "error": f"Pending change {pending_id} not found"}

        if pending_change.status != "pending":
            return {"status": "error", "error": f"Change {pending_id} is not pending (status: {pending_change.status})"}

        try:
            # Apply the changes
            new_config = self._update_config(
                pending_change.config_type,
                changed_by="pending_config_worker",
                reason=f"Pending change {pending_id} applied",
                **pending_change.changes
            )

            # Mark as applied
            pending_service.mark_applied(pending_id)

            logger.info(f"[RuntimeConfig] Applied pending change {pending_id}")
            return {
                "status": "applied",
                "pending_id": pending_id,
                "config": new_config,
            }
        except Exception as e:
            pending_service.mark_failed(pending_id, str(e))
            logger.error(f"[RuntimeConfig] Failed to apply pending change {pending_id}: {e}")
            return {
                "status": "error",
                "pending_id": pending_id,
                "error": str(e),
            }

    def cancel_pending_change(self, pending_id: str, cancelled_by: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a pending configuration change."""
        from selfhealing.services.pending_config import get_pending_config_service

        pending_service = get_pending_config_service()
        cancelled = pending_service.cancel_pending_change(pending_id, cancelled_by)

        if cancelled:
            return {
                "status": "cancelled",
                "pending_id": pending_id,
                "config_type": cancelled.config_type,
            }
        else:
            return {
                "status": "error",
                "error": f"Pending change {pending_id} not found or already processed",
            }

    def get_pending_changes(self, config_type: Optional[str] = None) -> list:
        """Get all pending configuration changes."""
        from selfhealing.services.pending_config import get_pending_config_service

        pending_service = get_pending_config_service()

        if config_type:
            return [c.to_dict() for c in pending_service.get_pending_changes_for_config(config_type)]
        else:
            return [c.to_dict() for c in pending_service.get_all_pending_changes()]
