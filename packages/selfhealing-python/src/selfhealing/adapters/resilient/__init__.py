"""
Resilient Storage Adapters.

Provides Redis-First + Graceful Degradation + WAL architecture
for zero data loss guarantees.
"""

from selfhealing.adapters.resilient.backend import (
    ResilientStorageBackend,
    ResilientStorageConfig,
    StorageMode,
)

__all__ = [
    "ResilientStorageBackend",
    "ResilientStorageConfig",
    "StorageMode",
]
