"""
Hash Chain Performance Optimization Components (Phase 3).

DEPRECATED: This module is deprecated. Import from selfhealing.audit.performance instead.

This file is maintained for backward compatibility only.
All classes have been moved to selfhealing.audit.performance package.

Usage:
    # New (recommended)
    from selfhealing.audit.performance import HashChainPerformanceManager
    
    # Old (still works for backward compatibility)
    from selfhealing.audit.hash_chain_performance import HashChainPerformanceManager
"""

# Re-export all symbols from the new package for backward compatibility
from selfhealing.audit.performance import (
    LuaAtomicHashChain,
    PipelineBatchQuery,
    BatchFlushConfig,
    BatchFlushWriter,
    AsyncAuditWriter,
    SamplingConfig,
    SamplingVerifier,
    PendingSequenceWatchdog,
    HashChainPerformanceManager,
)

__all__ = [
    "LuaAtomicHashChain",
    "PipelineBatchQuery",
    "BatchFlushConfig",
    "BatchFlushWriter",
    "AsyncAuditWriter",
    "SamplingConfig",
    "SamplingVerifier",
    "PendingSequenceWatchdog",
    "HashChainPerformanceManager",
]
