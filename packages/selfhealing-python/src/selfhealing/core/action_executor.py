"""
Action Executor - Central Execution Point for Self-Healing Actions.

Provides a single execution point that respects ExecutionMode settings.
All state-changing operations should go through this executor.

Usage:
    from selfhealing.core.action_executor import ActionExecutor, Action

    executor = ActionExecutor()

    result = executor.execute(
        Action(
            name="force_open_circuit",
            target="external_api",
            params={"reason": "Service maintenance"},
            execute_fn=lambda: repository.atomic_force_open(...),
        )
    )

    if result.executed:
        # 실제 실행됨
    else:
        # Shadow/Evaluation 모드로 로깅만 됨
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog

from selfhealing.core.decision_logger import (
    DecisionLogger,
    ReasonCode,
    log_intervention_evaluated,
)
from selfhealing.core.execution_mode import ExecutionMode, get_execution_mode
from selfhealing.core.timezone import now

logger = structlog.get_logger()


# =============================================================================
# Action Types
# =============================================================================


@dataclass
class Action:
    """
    Represents an action to be executed.

    Attributes:
        name: Action identifier (e.g., "force_open_circuit")
        target: Target service/resource name
        params: Action parameters
        execute_fn: Function to call for actual execution
        validate_fn: Optional validation function
        description: Human-readable description
    """

    name: str
    target: str
    execute_fn: Callable[[], Any]
    params: dict[str, Any] = field(default_factory=dict)
    validate_fn: Callable[[], bool] | None = None
    description: str = ""
    action_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self):
        if not self.description:
            self.description = f"{self.name} on {self.target}"


@dataclass
class ActionResult:
    """
    Result of an action execution attempt.

    Attributes:
        action_id: Unique action identifier
        action_name: Name of the action
        target: Target service/resource
        executed: Whether the action was actually executed
        success: Whether execution succeeded (None if not executed)
        result: Return value from execute_fn (None if not executed)
        error: Error message if failed
        mode: Execution mode used
        timestamp: When the action was processed
        decision_logged: Whether the decision was logged
        validation_result: Result of validation (if applicable)
    """

    action_id: str
    action_name: str
    target: str
    executed: bool
    mode: str
    timestamp: datetime
    success: bool | None = None
    result: Any = None
    error: str | None = None
    decision_logged: bool = False
    validation_result: bool | None = None

    @property
    def was_dry_run(self) -> bool:
        """Check if this was a dry-run (shadow/evaluation mode)."""
        return not self.executed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "action_id": self.action_id,
            "action_name": self.action_name,
            "target": self.target,
            "executed": self.executed,
            "success": self.success,
            "error": self.error,
            "mode": self.mode,
            "timestamp": self.timestamp.isoformat(),
            "decision_logged": self.decision_logged,
            "validation_result": self.validation_result,
            "was_dry_run": self.was_dry_run,
        }


# =============================================================================
# Action Executor
# =============================================================================


class ActionExecutor:
    """
    Central execution point for all self-healing actions.

    Respects ExecutionMode settings:
    - ACTIVE: Execute action and log decision
    - SHADOW: Log decision only, skip execution
    - EVALUATION: Validate and log, skip execution

    This ensures consistent behavior across all action types.
    """

    def __init__(self, mode: ExecutionMode | None = None):
        """
        Initialize the executor.

        Args:
            mode: Optional execution mode override. If None, uses global mode.
        """
        self._mode_override = mode

    @property
    def mode(self) -> ExecutionMode:
        """Get the current execution mode."""
        return self._mode_override or get_execution_mode()

    def execute(self, action: Action) -> ActionResult:
        """
        Execute an action respecting the current execution mode.

        Args:
            action: Action to execute

        Returns:
            ActionResult with execution details
        """
        current_mode = self.mode
        timestamp = now()

        # Log entering decision zone
        decision_logger = DecisionLogger(service_name=action.target)

        # Determine if we should execute
        should_execute = current_mode.should_execute

        # Log the decision
        decision_logged = False
        if current_mode.log_decisions:
            try:
                log_intervention_evaluated(
                    service_name=action.target,
                    allowed=should_execute,
                    reason=(
                        ReasonCode.INTERVENTION_ALLOWED
                        if should_execute
                        else ReasonCode.POLICY_CONSTRAINT_ACTIVE
                    ),
                )
                decision_logged = True
            except Exception as e:
                logger.warning(
                    "failed_log_decision",
                    error=e,
                )

        # Validation (for evaluation mode)
        validation_result = None
        if current_mode.validate_only and action.validate_fn:
            try:
                validation_result = action.validate_fn()
            except Exception as e:
                logger.warning(
                    "validation_failed",
                    action=action.name,
                    error=e,
                )
                validation_result = False

        # Execute if in active mode
        if should_execute:
            return self._execute_action(
                action=action,
                mode=current_mode,
                timestamp=timestamp,
                decision_logged=decision_logged,
                validation_result=validation_result,
            )
        else:
            # Shadow/Evaluation mode - log only
            return self._log_action_only(
                action=action,
                mode=current_mode,
                timestamp=timestamp,
                decision_logged=decision_logged,
                validation_result=validation_result,
            )

    def _execute_action(
        self,
        action: Action,
        mode: ExecutionMode,
        timestamp: datetime,
        decision_logged: bool,
        validation_result: bool | None,
    ) -> ActionResult:
        """Execute the action and return result."""
        try:
            result = action.execute_fn()
            logger.info(
                "action_executor.executed",
                action=action.name,
                action_1=action.target,
                mode=mode.mode.value,
            )
            return ActionResult(
                action_id=action.action_id,
                action_name=action.name,
                target=action.target,
                executed=True,
                success=True,
                result=result,
                mode=mode.mode.value,
                timestamp=timestamp,
                decision_logged=decision_logged,
                validation_result=validation_result,
            )
        except Exception as e:
            logger.exception(
                "action_executor.failed",
                action=action.name,
                action_1=action.target,
                error=e,
            )
            return ActionResult(
                action_id=action.action_id,
                action_name=action.name,
                target=action.target,
                executed=True,
                success=False,
                error=str(e),
                mode=mode.mode.value,
                timestamp=timestamp,
                decision_logged=decision_logged,
                validation_result=validation_result,
            )

    def _log_action_only(
        self,
        action: Action,
        mode: ExecutionMode,
        timestamp: datetime,
        decision_logged: bool,
        validation_result: bool | None,
    ) -> ActionResult:
        """Log the action without executing (shadow/evaluation mode)."""
        logger.info(
            "action_executor.execute",
            action=action.name,
            action_1=action.target,
            mode=mode.mode.value,
            action_3=action.params,
        )
        return ActionResult(
            action_id=action.action_id,
            action_name=action.name,
            target=action.target,
            executed=False,
            success=None,  # Not executed, so no success/failure
            mode=mode.mode.value,
            timestamp=timestamp,
            decision_logged=decision_logged,
            validation_result=validation_result,
        )


# =============================================================================
# Convenience Functions
# =============================================================================

# Global executor instance
_default_executor: ActionExecutor | None = None


def get_action_executor() -> ActionExecutor:
    """Get the default action executor instance."""
    global _default_executor
    if _default_executor is None:
        _default_executor = ActionExecutor()
    return _default_executor


def execute_action(action: Action) -> ActionResult:
    """
    Execute an action using the default executor.

    Convenience function for simple usage.

    Args:
        action: Action to execute

    Returns:
        ActionResult
    """
    return get_action_executor().execute(action)


__all__ = [
    "Action",
    "ActionResult",
    "ActionExecutor",
    "get_action_executor",
    "execute_action",
]
