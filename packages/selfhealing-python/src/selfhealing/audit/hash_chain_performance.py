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

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

# =============================================================================
# LAZY IMPORTS - Backward compatibility with deprecation warning
# =============================================================================

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "LuaAtomicHashChain": ("selfhealing.audit.performance.lua_atomic", "LuaAtomicHashChain"),
    "PipelineBatchQuery": ("selfhealing.audit.performance.batch_query", "PipelineBatchQuery"),
    "BatchFlushConfig": ("selfhealing.audit.performance.batch_writer", "BatchFlushConfig"),
    "BatchFlushWriter": ("selfhealing.audit.performance.batch_writer", "BatchFlushWriter"),
    "AsyncAuditWriter": ("selfhealing.audit.performance.async_writer", "AsyncAuditWriter"),
    "SamplingConfig": ("selfhealing.audit.performance.sampling", "SamplingConfig"),
    "SamplingVerifier": ("selfhealing.audit.performance.sampling", "SamplingVerifier"),
    "PendingSequenceWatchdog": ("selfhealing.audit.performance.watchdog", "PendingSequenceWatchdog"),
    "HashChainPerformanceManager": ("selfhealing.audit.performance.manager", "HashChainPerformanceManager"),
}

_loaded_symbols: dict[str, object] = {}
_warned: set[str] = set()


def __getattr__(name: str) -> object:
    """Lazy import with deprecation warning."""
    if name in _loaded_symbols:
        return _loaded_symbols[name]
    
    if name in _LAZY_IMPORTS:
        # Emit deprecation warning once per symbol
        if name not in _warned:
            warnings.warn(
                f"Importing {name} from 'selfhealing.audit.hash_chain_performance' is deprecated. "
                f"Use 'from selfhealing.audit.performance import {name}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            _warned.add(name)
        
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib
        module = importlib.import_module(module_path)
        symbol = getattr(module, attr_name)
        _loaded_symbols[name] = symbol
        return symbol
    
    raise AttributeError(f"module 'selfhealing.audit.hash_chain_performance' has no attribute '{name}'")


def __dir__() -> list[str]:
    """List available symbols for IDE autocompletion."""
    return list(__all__)


# TYPE_CHECKING block for IDE support
if TYPE_CHECKING:
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
