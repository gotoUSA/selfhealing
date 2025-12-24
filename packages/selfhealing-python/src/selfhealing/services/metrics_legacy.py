"""
Self-Healing Observability Metrics.

⚠️ DEPRECATED: This file is maintained for backward compatibility.
Please import from selfhealing.services.metrics package instead.

This module has been refactored into:
- selfhealing/services/metrics/registry.py
- selfhealing/services/metrics/definitions.py
- selfhealing/services/metrics/recorders.py
- selfhealing/services/metrics/updaters.py
- selfhealing/services/metrics/alerting_rules.py

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)
"""

from __future__ import annotations

# Re-export everything from the new metrics package
from selfhealing.services.metrics import *  # noqa: F401, F403
from selfhealing.services.metrics import __all__  # noqa: F401
