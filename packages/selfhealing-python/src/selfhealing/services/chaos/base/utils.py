"""
Chaos Experiment Utilities.

Runtime configuration helpers for chaos experiments.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def _apply_chaos_config(config: dict[str, Any]) -> None:
    """Apply chaos configuration via RuntimeConfigManager."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        manager.update_chaos_config(**config)
    except Exception as e:
        logger.warning(
            "chaos_experiment.apply_config_via_runtimeconfig",
            error=e,
        )


def _get_current_chaos_config() -> dict[str, Any]:
    """Get current chaos configuration."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        return manager.get_chaos_config()
    except Exception:
        return {}
