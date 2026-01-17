"""
Air-Gap Storage Adapters.

Provides an abstraction layer between Self-Healing engine and business DB.
The engine reads metrics from Air-Gap storage (Redis) instead of directly
accessing the business database.

Design Philosophy:
- Self-Healing engine NEVER touches business DB directly
- Business layer writes summaries to Air-Gap storage
- Engine reads from Air-Gap storage only

Usage:
    >>> from selfhealing.adapters.airgap import get_airgap_adapter
    >>> adapter = get_airgap_adapter()
    >>> 
    >>> # Business layer writes
    >>> adapter.write_summary("dlq:payment:pending", 5)
    >>> 
    >>> # Self-Healing engine reads
    >>> count = adapter.read_summary("dlq:payment:pending")
"""

from selfhealing.adapters.airgap.base import (
    AirGapStorageAdapter,
    BaseAirGapAdapter,
)
from selfhealing.adapters.airgap.null_adapter import NullAirGapAdapter
from selfhealing.adapters.airgap.factory import (
    get_airgap_adapter,
    configure_airgap_adapter,
    reset_airgap_adapter,
)

__all__ = [
    "AirGapStorageAdapter",
    "BaseAirGapAdapter",
    "NullAirGapAdapter",
    "get_airgap_adapter",
    "configure_airgap_adapter",
    "reset_airgap_adapter",
]
