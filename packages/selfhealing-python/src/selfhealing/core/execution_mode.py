"""
Execution Mode Configuration for Shadow/Evaluation Mode Support.

Provides centralized control over whether actions are executed or only logged.

Usage:
    from selfhealing.core.execution_mode import ExecutionMode, get_execution_mode

    mode = get_execution_mode()
    if mode.is_active:
        # 실제 액션 실행
    else:
        # 로깅만

Environment Variable:
    SELFHEALING_EXECUTION_MODE: "active" | "shadow" | "evaluation"

Reference: docs/capability-audit/capablitity_정의/03-PROTECTION-MECHANISMS.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Optional


class ExecutionModeType(str, Enum):
    """Execution mode types."""

    # 실제 액션 실행 (프로덕션 기본값)
    ACTIVE = "active"

    # 결정만 로깅, 액션 실행 안함 (관찰 모드)
    SHADOW = "shadow"

    # 결정 + 검증 로깅, 액션 실행 안함 (평가 모드)
    EVALUATION = "evaluation"


@dataclass(frozen=True)
class ExecutionMode:
    """
    Execution mode configuration.

    Attributes:
        mode: Current execution mode
        log_decisions: Whether to log all decisions
        execute_actions: Whether to actually execute actions
        validate_only: Whether to validate without execution
    """

    mode: ExecutionModeType
    log_decisions: bool = True
    execute_actions: bool = True
    validate_only: bool = False

    @property
    def is_active(self) -> bool:
        """Check if in active (production) mode."""
        return self.mode == ExecutionModeType.ACTIVE

    @property
    def is_shadow(self) -> bool:
        """Check if in shadow (observe-only) mode."""
        return self.mode == ExecutionModeType.SHADOW

    @property
    def is_evaluation(self) -> bool:
        """Check if in evaluation mode."""
        return self.mode == ExecutionModeType.EVALUATION

    @property
    def should_execute(self) -> bool:
        """Check if actions should be executed."""
        return self.execute_actions and self.is_active

    @property
    def is_dry_run(self) -> bool:
        """Check if this is a dry-run (no side effects)."""
        return not self.execute_actions

    @classmethod
    def active(cls) -> "ExecutionMode":
        """Create active mode configuration."""
        return cls(
            mode=ExecutionModeType.ACTIVE,
            log_decisions=True,
            execute_actions=True,
            validate_only=False,
        )

    @classmethod
    def shadow(cls) -> "ExecutionMode":
        """Create shadow mode configuration."""
        return cls(
            mode=ExecutionModeType.SHADOW,
            log_decisions=True,
            execute_actions=False,
            validate_only=False,
        )

    @classmethod
    def evaluation(cls) -> "ExecutionMode":
        """Create evaluation mode configuration."""
        return cls(
            mode=ExecutionModeType.EVALUATION,
            log_decisions=True,
            execute_actions=False,
            validate_only=True,
        )


# =============================================================================
# Global Mode Access
# =============================================================================

_override_mode: Optional[ExecutionMode] = None


def set_execution_mode(mode: ExecutionMode) -> None:
    """
    Override the execution mode programmatically.

    Useful for testing or temporary mode changes.

    Args:
        mode: ExecutionMode to set
    """
    global _override_mode
    _override_mode = mode


def clear_execution_mode_override() -> None:
    """Clear any programmatic mode override."""
    global _override_mode
    _override_mode = None


@lru_cache(maxsize=1)
def _get_mode_from_env() -> ExecutionMode:
    """Load execution mode from environment variable."""
    mode_str = os.environ.get("SELFHEALING_EXECUTION_MODE", "active").lower()

    if mode_str == "shadow":
        return ExecutionMode.shadow()
    elif mode_str == "evaluation":
        return ExecutionMode.evaluation()
    else:
        return ExecutionMode.active()


def get_execution_mode() -> ExecutionMode:
    """
    Get the current execution mode.

    Priority:
    1. Programmatic override (set_execution_mode)
    2. Environment variable (SELFHEALING_EXECUTION_MODE)
    3. Default: active

    Returns:
        Current ExecutionMode configuration
    """
    if _override_mode is not None:
        return _override_mode
    return _get_mode_from_env()


__all__ = [
    "ExecutionModeType",
    "ExecutionMode",
    "get_execution_mode",
    "set_execution_mode",
    "clear_execution_mode_override",
]
