"""
Redis Event Bus - Backward Compatibility Shim.

.. deprecated:: 2.1.0
    Import from ``selfhealing.services.event_bus.redis_bus`` instead.
    This shim will be removed in v3.0.0.
"""
import sys
import warnings

warnings.warn(
    "Importing from 'selfhealing.services.event_bus_redis' is deprecated. "
    "Use 'selfhealing.services.event_bus.redis_bus' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

from selfhealing.services.event_bus import redis_bus as _module  # noqa: E402

_module.__deprecated__ = True
sys.modules[__name__] = _module
