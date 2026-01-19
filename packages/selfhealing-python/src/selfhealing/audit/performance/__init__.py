"""
Hash Chain Performance Optimization Components (Phase 3).

Provides high-performance features for distributed hash chain operations:

- LuaAtomicHashChain: 5 RTT → 1 RTT via Lua script atomization
- PipelineBatchQuery: Multi-key batch retrieval via Redis pipeline
- BatchFlushWriter: n×fsync → 1×fsync via batched file writes
- AsyncAuditWriter: Non-blocking async write operations
- SamplingVerifier: O(n) → O(k) probabilistic chain verification
- PendingSequenceWatchdog: Self-cleanup daemon for stale entries

Code patterns reused from existing codebase:
- Lua scripts: adapters/cache/redis_adapter.py#L137-143
- Pipeline: api/django/rate_limit/redis_adapter.py#L149-153
- Batch config: audit/config.py#L69-73
- Lazy init: adapters/django/middleware.py#L647
- Sampling: throttle/config.py#L26
- Watchdog: audit/audit_watchdog.py#L150-270
"""

from selfhealing.audit.performance.lua_atomic import LuaAtomicHashChain
from selfhealing.audit.performance.batch_query import PipelineBatchQuery
from selfhealing.audit.performance.batch_writer import BatchFlushConfig, BatchFlushWriter
from selfhealing.audit.performance.async_writer import AsyncAuditWriter
from selfhealing.audit.performance.sampling import SamplingConfig, SamplingVerifier
from selfhealing.audit.performance.watchdog import PendingSequenceWatchdog
from selfhealing.audit.performance.manager import HashChainPerformanceManager

__all__ = [
    # Lua Atomic
    "LuaAtomicHashChain",
    # Batch Query
    "PipelineBatchQuery",
    # Batch Writer
    "BatchFlushConfig",
    "BatchFlushWriter",
    # Async Writer
    "AsyncAuditWriter",
    # Sampling
    "SamplingConfig",
    "SamplingVerifier",
    # Watchdog
    "PendingSequenceWatchdog",
    # Manager
    "HashChainPerformanceManager",
]
