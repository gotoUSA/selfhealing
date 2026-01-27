"""
Service Factory for Self-Healing Components.

.. deprecated:: 2.0.0
    This module is kept for backward compatibility only.
    Import directly from selfhealing.services.factory package instead.
    Will be removed in version 3.0.0.

This module re-exports all components from the factory package:
    - from selfhealing.services.factory import ServiceFactory
    - from selfhealing.services.factory import ProviderRegistry
    - etc.

For new code, use:
    from selfhealing.services.factory import (
        ServiceFactory,
        FrameworkType,
        ProviderRegistry,
        create_dlq_service,
        ...
    )
"""

import warnings

warnings.warn(
    "Importing from 'selfhealing.services.factory' (single module) is deprecated. "
    "Import from 'selfhealing.services.factory' package directly. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the factory package for backward compatibility
