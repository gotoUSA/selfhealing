"""
Chaos Experiment Utilities.

Runtime configuration helpers for chaos experiments.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _apply_chaos_config(config: dict[str, Any]) -> None:
    """Apply chaos configuration via RuntimeConfigManager."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        manager.update_chaos_config(**config)
    except Exception as e:
        logger.warning(
            f"[ChaosExperiment] Could not apply config via RuntimeConfig: {e}"
        )


def _get_current_chaos_config() -> dict[str, Any]:
    """Get current chaos configuration."""
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        return manager.get_chaos_config()
    except Exception:
        return {}
