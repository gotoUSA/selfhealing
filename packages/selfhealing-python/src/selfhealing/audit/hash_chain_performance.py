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

import hashlib
import json
import logging
import os
import queue
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Lua Script Atomic Hash Chain (5 RTT → 1 RTT)
# =============================================================================

class LuaAtomicHashChain:
    """
    Lua Script based atomic hash chain operations.
    
    Problem:
        Python-side operations require 5 round trips:
        1. INCR sequence
        2. GET previous_hash
        3. HSET pending entry
        4. HSET chain state
        5. DEL pending entry
    
    Solution:
        Execute all operations in a single Lua script server-side.
        Redis guarantees atomic execution within a Lua script.
    
    Effect:
        5 RTT → 1 RTT (80% reduction in network latency)
    
    Usage:
        lua_chain = LuaAtomicHashChain(redis_client)
        result = lua_chain.add_integrity_atomic(entry_data, expected_hash)
    """
    
    # Lua script for atomic sequence allocation + state update
    LUA_ATOMIC_ADD_INTEGRITY = """
    -- KEYS[1] = seq_key (audit:hash_chain:seq)
    -- KEYS[2] = state_key (audit:hash_chain:state)
    -- KEYS[3] = pending_key (audit:hash_chain:pending:{seq})
    -- ARGV[1] = expected_hash
    -- ARGV[2] = previous_hash
    -- ARGV[3] = timestamp
    -- ARGV[4] = pending_ttl_seconds
    
    -- 1. Atomically increment sequence
    local new_seq = redis.call('INCR', KEYS[1])
    
    -- 2. Get current previous_hash for validation
    local stored_prev = redis.call('HGET', KEYS[2], 'previous_hash')
    if stored_prev and stored_prev ~= ARGV[2] then
        -- Rollback sequence on mismatch
        redis.call('DECR', KEYS[1])
        return {err='PREV_HASH_MISMATCH', expected=ARGV[2], found=stored_prev}
    end
    
    -- 3. Set PENDING state with TTL
    local pending_key = KEYS[3] .. ':' .. tostring(new_seq)
    redis.call('HSET', pending_key, 
               'expected_hash', ARGV[1],
               'previous_hash', ARGV[2],
               'reserved_at', ARGV[3])
    redis.call('EXPIRE', pending_key, tonumber(ARGV[4]))
    
    -- 4. Return allocated sequence
    return {seq=new_seq, prev_hash=(stored_prev or 'GENESIS')}
    """
    
    # Lua script for atomic commit (clear pending + update state)
    LUA_ATOMIC_COMMIT = """
    -- KEYS[1] = pending_key
    -- KEYS[2] = state_key
    -- ARGV[1] = sequence
    -- ARGV[2] = new_hash
    -- ARGV[3] = timestamp
    
    -- 1. Verify pending entry exists
    local exists = redis.call('EXISTS', KEYS[1])
    if exists == 0 then
        return {err='PENDING_NOT_FOUND'}
    end
    
    -- 2. Verify expected hash matches (tamper detection)
    local expected = redis.call('HGET', KEYS[1], 'expected_hash')
    if expected and expected ~= ARGV[2] then
        return {err='HASH_MISMATCH', expected=expected, actual=ARGV[2]}
    end
    
    -- 3. Update chain state atomically
    redis.call('HSET', KEYS[2],
               'previous_hash', ARGV[2],
               'sequence', ARGV[1],
               'updated_at', ARGV[3])
    
    -- 4. Delete pending entry
    redis.call('DEL', KEYS[1])
    
    return {ok=true}
    """
    
    # Lua script for batch state query
    LUA_BATCH_GET_STATE = """
    -- KEYS = list of state keys
    -- Returns array of {seq, prev_hash} for each key
    
    local results = {}
    for i, key in ipairs(KEYS) do
        local seq = redis.call('HGET', key, 'sequence') or '0'
        local prev_hash = redis.call('HGET', key, 'previous_hash') or 'GENESIS'
        table.insert(results, {seq=tonumber(seq), prev_hash=prev_hash, key=key})
    end
    return cjson.encode(results)
    """
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        pending_ttl_seconds: int = 30,
    ):
        """
        Initialize Lua atomic hash chain.
        
        Args:
            redis_client: Redis client instance
            key_prefix: Prefix for all Redis keys
            pending_ttl_seconds: TTL for pending entries
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._pending_ttl = pending_ttl_seconds
        self._scripts_loaded = False
        self._add_integrity_sha: Optional[str] = None
        self._commit_sha: Optional[str] = None
        self._batch_get_sha: Optional[str] = None
    
    def _ensure_scripts_loaded(self) -> None:
        """Load Lua scripts into Redis (cached via SHA)."""
        if self._scripts_loaded:
            return
        
        try:
            self._add_integrity_sha = self._redis.script_load(
                self.LUA_ATOMIC_ADD_INTEGRITY
            )
            self._commit_sha = self._redis.script_load(self.LUA_ATOMIC_COMMIT)
            self._batch_get_sha = self._redis.script_load(self.LUA_BATCH_GET_STATE)
            self._scripts_loaded = True
            logger.debug("[LuaAtomicHashChain] Scripts loaded successfully")
        except Exception as e:
            logger.warning(f"[LuaAtomicHashChain] Script load failed: {e}")
            # Fallback to eval on each call
    
    def _get_keys(self) -> Dict[str, str]:
        """Get standard Redis key names."""
        return {
            "seq": f"{self._key_prefix}audit:hash_chain:seq",
            "state": f"{self._key_prefix}audit:hash_chain:state",
            "pending_prefix": f"{self._key_prefix}audit:hash_chain:pending",
        }
    
    def reserve_sequence_atomic(
        self,
        expected_hash: str,
        previous_hash: str,
    ) -> Tuple[bool, int, str]:
        """
        Atomically reserve a sequence number with expected hash.
        
        Single RTT operation combining:
        - Sequence increment
        - Previous hash validation
        - Pending state creation
        
        Args:
            expected_hash: Expected final hash after write
            previous_hash: Previous hash for chain validation
        
        Returns:
            Tuple of (success, sequence, error_message)
        """
        self._ensure_scripts_loaded()
        keys = self._get_keys()
        timestamp = datetime.now(timezone.utc).isoformat()
        
        try:
            if self._add_integrity_sha:
                result = self._redis.evalsha(
                    self._add_integrity_sha,
                    3,
                    keys["seq"],
                    keys["state"],
                    keys["pending_prefix"],
                    expected_hash,
                    previous_hash,
                    timestamp,
                    str(self._pending_ttl),
                )
            else:
                result = self._redis.eval(
                    self.LUA_ATOMIC_ADD_INTEGRITY,
                    3,
                    keys["seq"],
                    keys["state"],
                    keys["pending_prefix"],
                    expected_hash,
                    previous_hash,
                    timestamp,
                    str(self._pending_ttl),
                )
            
            if isinstance(result, dict) and "err" in result:
                return False, 0, result["err"]
            
            if isinstance(result, list) and len(result) >= 2:
                # Redis returns list: [seq, prev_hash]
                seq = int(result[0]) if result[0] else 0
                return True, seq, ""
            
            # Parse other formats
            if hasattr(result, "get"):
                seq = result.get("seq", 0)
                return True, int(seq), ""
            
            return True, int(result) if result else 0, ""
            
        except Exception as e:
            logger.error(f"[LuaAtomicHashChain] Reserve failed: {e}")
            return False, 0, str(e)
    
    def commit_sequence_atomic(
        self,
        sequence: int,
        actual_hash: str,
    ) -> Tuple[bool, str]:
        """
        Atomically commit a reserved sequence.
        
        Single RTT operation combining:
        - Pending entry verification
        - Hash validation
        - State update
        - Pending cleanup
        
        Args:
            sequence: Sequence number to commit
            actual_hash: Actual computed hash
        
        Returns:
            Tuple of (success, error_message)
        """
        self._ensure_scripts_loaded()
        keys = self._get_keys()
        pending_key = f"{keys['pending_prefix']}:{sequence}"
        timestamp = datetime.now(timezone.utc).isoformat()
        
        try:
            if self._commit_sha:
                result = self._redis.evalsha(
                    self._commit_sha,
                    2,
                    pending_key,
                    keys["state"],
                    str(sequence),
                    actual_hash,
                    timestamp,
                )
            else:
                result = self._redis.eval(
                    self.LUA_ATOMIC_COMMIT,
                    2,
                    pending_key,
                    keys["state"],
                    str(sequence),
                    actual_hash,
                    timestamp,
                )
            
            if isinstance(result, dict) and "err" in result:
                return False, result["err"]
            
            return True, ""
            
        except Exception as e:
            logger.error(f"[LuaAtomicHashChain] Commit failed: {e}")
            return False, str(e)


# =============================================================================
# Pipeline Batch Query (Multi-key retrieval)
# =============================================================================

class PipelineBatchQuery:
    """
    Redis pipeline-based batch query for hash chain state.
    
    Problem:
        Querying multiple chain states requires N round trips.
    
    Solution:
        Use Redis pipeline to batch all queries into single RTT.
    
    Pattern source:
        api/django/rate_limit/redis_adapter.py#L149-153
    
    Usage:
        batch = PipelineBatchQuery(redis_client)
        states = batch.get_multiple_chain_states(["chain1", "chain2"])
    """
    
    def __init__(self, redis_client: Any, key_prefix: str = "selfhealing:"):
        """
        Initialize pipeline batch query.
        
        Args:
            redis_client: Redis client instance
            key_prefix: Prefix for Redis keys
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
    
    def get_multiple_chain_states(
        self,
        chain_keys: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get multiple chain states in single round trip.
        
        Args:
            chain_keys: List of chain identifiers
        
        Returns:
            Dict mapping chain_key to {sequence, previous_hash, updated_at}
        """
        if not chain_keys:
            return {}
        
        try:
            with self._redis.pipeline(transaction=False) as pipe:
                for key in chain_keys:
                    state_key = f"{self._key_prefix}audit:hash_chain:state:{key}"
                    pipe.hgetall(state_key)
                
                results = pipe.execute()
            
            states = {}
            for i, key in enumerate(chain_keys):
                if i < len(results) and results[i]:
                    raw = results[i]
                    states[key] = {
                        "sequence": int(raw.get(b"sequence", raw.get("sequence", 0))),
                        "previous_hash": (
                            raw.get(b"previous_hash", raw.get("previous_hash", b"GENESIS"))
                        ),
                        "updated_at": raw.get(b"updated_at", raw.get("updated_at", "")),
                    }
                    # Decode bytes if needed
                    if isinstance(states[key]["previous_hash"], bytes):
                        states[key]["previous_hash"] = states[key]["previous_hash"].decode()
                else:
                    states[key] = {
                        "sequence": 0,
                        "previous_hash": "GENESIS",
                        "updated_at": "",
                    }
            
            return states
            
        except Exception as e:
            logger.error(f"[PipelineBatchQuery] Batch query failed: {e}")
            return {key: {"sequence": 0, "previous_hash": "GENESIS"} for key in chain_keys}
    
    def batch_check_pending(
        self,
        sequences: List[int],
    ) -> Dict[int, bool]:
        """
        Check multiple pending states in single round trip.
        
        Args:
            sequences: List of sequence numbers to check
        
        Returns:
            Dict mapping sequence to exists (True/False)
        """
        if not sequences:
            return {}
        
        try:
            with self._redis.pipeline(transaction=False) as pipe:
                for seq in sequences:
                    pending_key = f"{self._key_prefix}audit:hash_chain:pending:{seq}"
                    pipe.exists(pending_key)
                
                results = pipe.execute()
            
            return {
                seq: bool(results[i]) if i < len(results) else False
                for i, seq in enumerate(sequences)
            }
            
        except Exception as e:
            logger.error(f"[PipelineBatchQuery] Pending check failed: {e}")
            return {seq: False for seq in sequences}


# =============================================================================
# Batch Flush Writer (n×fsync → 1×fsync)
# =============================================================================

@dataclass
class BatchFlushConfig:
    """Configuration for batch flush writer."""
    batch_size: int = 100
    flush_interval_seconds: float = 10.0
    sync_on_flush: bool = True


class BatchFlushWriter:
    """
    Batched file writer with reduced fsync overhead.
    
    Problem:
        Each fsync() call takes 1-10ms depending on disk.
        Writing 1000 entries/sec = 1000-10000ms of blocking per second.
    
    Solution:
        Buffer entries and flush in batches with single fsync.
        100 entries per batch = 99% reduction in fsync calls.
    
    Pattern source:
        audit/config.py#L69-73 (batch_size, batch_flush_interval_seconds)
    
    Usage:
        writer = BatchFlushWriter(path, BatchFlushConfig(batch_size=100))
        writer.write(entry)  # Buffered
        # Auto-flush when batch_size reached or interval elapsed
    """
    
    def __init__(
        self,
        file_path: Path,
        config: Optional[BatchFlushConfig] = None,
    ):
        """
        Initialize batch flush writer.
        
        Args:
            file_path: Path to output file
            config: Batch configuration
        """
        self._file_path = Path(file_path)
        self._config = config or BatchFlushConfig()
        self._buffer: List[str] = []
        self._lock = threading.RLock()
        self._last_flush = time.monotonic()
        self._file_handle = None
        self._entries_written = 0
        self._flushes_performed = 0
    
    def write(self, entry: Dict[str, Any]) -> bool:
        """
        Write entry to buffer, flush if needed.
        
        Args:
            entry: Dictionary to write as JSON line
        
        Returns:
            True if successfully buffered/written
        """
        with self._lock:
            try:
                line = json.dumps(entry, default=str, ensure_ascii=False)
                self._buffer.append(line)
                
                # Check flush conditions
                should_flush = (
                    len(self._buffer) >= self._config.batch_size or
                    time.monotonic() - self._last_flush >= self._config.flush_interval_seconds
                )
                
                if should_flush:
                    return self._flush()
                
                return True
                
            except Exception as e:
                logger.error(f"[BatchFlushWriter] Write failed: {e}")
                return False
    
    def _flush(self) -> bool:
        """
        Flush buffer to file with single fsync.
        
        Returns:
            True if flush successful
        """
        if not self._buffer:
            return True
        
        try:
            self._ensure_file_open()
            
            # Capture count before clearing
            flushed_count = len(self._buffer)
            
            # Write all buffered entries
            content = "\n".join(self._buffer) + "\n"
            self._file_handle.write(content)
            
            # Always flush to ensure data is written to OS buffer
            self._file_handle.flush()
            
            # fsync for durability (optional, expensive)
            if self._config.sync_on_flush:
                os.fsync(self._file_handle.fileno())
            
            self._entries_written += flushed_count
            self._flushes_performed += 1
            self._buffer.clear()
            self._last_flush = time.monotonic()
            
            logger.debug(
                f"[BatchFlushWriter] Flushed {flushed_count} entries "
                f"(total: {self._entries_written})"
            )
            return True
            
        except Exception as e:
            logger.error(f"[BatchFlushWriter] Flush failed: {e}")
            return False
    
    def _ensure_file_open(self) -> None:
        """Ensure file is open for writing."""
        if self._file_handle is None:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = open(self._file_path, "a", encoding="utf-8")
    
    def force_flush(self) -> bool:
        """Force immediate flush of buffer."""
        with self._lock:
            return self._flush()
    
    def close(self) -> None:
        """Flush and close file."""
        with self._lock:
            self._flush()
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get writer statistics."""
        return {
            "entries_written": self._entries_written,
            "flushes_performed": self._flushes_performed,
            "buffer_size": len(self._buffer),
            "avg_entries_per_flush": (
                self._entries_written / self._flushes_performed
                if self._flushes_performed > 0 else 0
            ),
        }


# =============================================================================
# Async Audit Writer (Non-blocking writes)
# =============================================================================

class AsyncAuditWriter:
    """
    Async audit writer with background thread.
    
    Problem:
        Synchronous file/Redis writes block request handling.
        Write latency directly impacts API response time.
    
    Solution:
        Queue entries for background thread processing.
        Request handler returns immediately after queueing.
    
    Pattern source:
        audit/audit_watchdog.py#L150-270 (daemon thread pattern)
    
    Usage:
        writer = AsyncAuditWriter(sync_writer)
        writer.start()
        writer.write_async(entry)  # Non-blocking
    """
    
    def __init__(
        self,
        sync_writer: Callable[[Dict[str, Any]], bool],
        max_queue_size: int = 10000,
        batch_size: int = 50,
        flush_interval_seconds: float = 0.1,
    ):
        """
        Initialize async audit writer.
        
        Args:
            sync_writer: Synchronous write function to wrap
            max_queue_size: Maximum queue size before blocking
            batch_size: Number of entries to write per batch
            flush_interval_seconds: Max time between writes
        """
        self._sync_writer = sync_writer
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._entries_queued = 0
        self._entries_written = 0
        self._entries_dropped = 0
    
    def start(self) -> None:
        """Start background writer thread."""
        if self._is_running:
            return
        
        self._is_running = True
        self._stop_event.clear()
        
        self._thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="AsyncAuditWriter",
        )
        self._thread.start()
        logger.info("[AsyncAuditWriter] Started")
    
    def stop(self, timeout: float = 5.0) -> None:
        """Stop background writer thread."""
        if not self._is_running:
            return
        
        self._stop_event.set()
        
        if self._thread:
            self._thread.join(timeout=timeout)
        
        self._is_running = False
        logger.info(
            f"[AsyncAuditWriter] Stopped. "
            f"Queued: {self._entries_queued}, Written: {self._entries_written}"
        )
    
    def write_async(
        self,
        entry: Dict[str, Any],
        block: bool = False,
    ) -> bool:
        """
        Queue entry for async write.
        
        Args:
            entry: Entry to write
            block: Block if queue is full (default: drop)
        
        Returns:
            True if queued successfully
        """
        try:
            self._queue.put(entry, block=block, timeout=0.01)
            self._entries_queued += 1
            return True
        except queue.Full:
            self._entries_dropped += 1
            logger.warning(
                f"[AsyncAuditWriter] Queue full, entry dropped "
                f"(total dropped: {self._entries_dropped})"
            )
            return False
    
    def _writer_loop(self) -> None:
        """Background writer loop."""
        batch: List[Dict[str, Any]] = []
        last_flush = time.monotonic()
        
        while not self._stop_event.is_set():
            try:
                # Collect batch
                try:
                    entry = self._queue.get(timeout=self._flush_interval)
                    batch.append(entry)
                except queue.Empty:
                    pass
                
                # Check flush conditions
                should_flush = (
                    len(batch) >= self._batch_size or
                    (batch and time.monotonic() - last_flush >= self._flush_interval)
                )
                
                if should_flush and batch:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()
                
            except Exception as e:
                logger.error(f"[AsyncAuditWriter] Writer loop error: {e}")
        
        # Final flush on stop
        if batch:
            self._flush_batch(batch)
    
    def _flush_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Flush a batch of entries."""
        for entry in batch:
            try:
                if self._sync_writer(entry):
                    self._entries_written += 1
            except Exception as e:
                logger.error(f"[AsyncAuditWriter] Write failed: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get writer statistics."""
        return {
            "queued": self._entries_queued,
            "written": self._entries_written,
            "dropped": self._entries_dropped,
            "queue_size": self._queue.qsize(),
            "is_running": self._is_running,
        }


# =============================================================================
# Sampling Verifier (O(n) → O(k))
# =============================================================================

@dataclass
class SamplingConfig:
    """Configuration for sampling verification."""
    sample_rate: float = 0.1  # 10% sampling
    min_samples: int = 10
    max_samples: int = 1000
    full_verify_on_failure: bool = True


class SamplingVerifier:
    """
    Probabilistic chain verification using sampling.
    
    Problem:
        Full chain verification is O(n) for n entries.
        Millions of entries = seconds/minutes of CPU time.
    
    Solution:
        Random sampling reduces to O(k) where k = n × sample_rate.
        If sample passes, chain is likely valid.
        If sample fails, fall back to full verification.
    
    Pattern source:
        throttle/config.py#L26 (sample_rate concept)
        runtime_config/core_configs.py#L215 (sampling intervals)
    
    Usage:
        verifier = SamplingVerifier(SamplingConfig(sample_rate=0.1))
        is_valid, issues = verifier.verify_sampled(entries)
    """
    
    GENESIS_HASH = "GENESIS"
    
    def __init__(self, config: Optional[SamplingConfig] = None):
        """
        Initialize sampling verifier.
        
        Args:
            config: Sampling configuration
        """
        self._config = config or SamplingConfig()
    
    def verify_sampled(
        self,
        entries: List[Dict[str, Any]],
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Verify chain using probabilistic sampling.
        
        Args:
            entries: List of log entries to verify
        
        Returns:
            Tuple of (is_valid, issues_found)
        """
        if not entries:
            return True, []
        
        # Calculate sample size
        n = len(entries)
        sample_size = max(
            self._config.min_samples,
            min(
                self._config.max_samples,
                int(n * self._config.sample_rate),
            ),
        )
        
        # Can't sample more than we have
        sample_size = min(sample_size, n)
        
        # Select random sample indices
        if sample_size >= n:
            sample_indices = list(range(n))
        else:
            sample_indices = sorted(random.sample(range(n), sample_size))
        
        logger.debug(
            f"[SamplingVerifier] Sampling {sample_size}/{n} entries "
            f"({sample_size/n*100:.1f}%)"
        )
        
        # Verify sampled entries
        issues = self._verify_indices(entries, sample_indices)
        
        if issues and self._config.full_verify_on_failure:
            # Sample failed - do full verification
            logger.info(
                "[SamplingVerifier] Sample verification failed, "
                "performing full verification"
            )
            return self._verify_full(entries)
        
        return len(issues) == 0, issues
    
    def _verify_indices(
        self,
        entries: List[Dict[str, Any]],
        indices: List[int],
    ) -> List[Dict[str, Any]]:
        """Verify specific indices in the chain."""
        issues = []
        
        for i in indices:
            entry = entries[i]
            integrity = entry.get("integrity", {})
            
            # Verify hash computation
            stored_hash = integrity.get("current_hash", "")
            entry_copy = self._remove_current_hash(entry)
            computed_hash = self._compute_hash(entry_copy)
            
            if stored_hash != computed_hash:
                issues.append({
                    "type": "hash_mismatch",
                    "index": i,
                    "sequence": integrity.get("sequence"),
                    "stored_hash": stored_hash[:16] + "...",
                    "computed_hash": computed_hash[:16] + "...",
                })
            
            # Verify chain linkage (if not first entry)
            if i > 0:
                prev_entry = entries[i - 1]
                expected_prev = prev_entry.get("integrity", {}).get("current_hash", "")
                actual_prev = integrity.get("previous_hash", "")
                
                if expected_prev and actual_prev != expected_prev:
                    issues.append({
                        "type": "chain_broken",
                        "index": i,
                        "sequence": integrity.get("sequence"),
                        "expected_prev": expected_prev[:16] + "...",
                        "actual_prev": actual_prev[:16] + "...",
                    })
        
        return issues
    
    def _verify_full(
        self,
        entries: List[Dict[str, Any]],
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Full chain verification."""
        issues = []
        previous_hash = self.GENESIS_HASH
        
        for i, entry in enumerate(entries):
            integrity = entry.get("integrity", {})
            
            # Check previous hash linkage
            prev_hash = integrity.get("previous_hash", "")
            if prev_hash != previous_hash:
                issues.append({
                    "type": "chain_broken",
                    "index": i,
                    "sequence": integrity.get("sequence"),
                    "expected_prev": previous_hash[:16] + "...",
                    "actual_prev": prev_hash[:16] + "...",
                })
            
            # Verify hash
            stored_hash = integrity.get("current_hash", "")
            entry_copy = self._remove_current_hash(entry)
            computed_hash = self._compute_hash(entry_copy)
            
            if stored_hash != computed_hash:
                issues.append({
                    "type": "hash_mismatch",
                    "index": i,
                    "sequence": integrity.get("sequence"),
                })
            
            previous_hash = stored_hash
        
        return len(issues) == 0, issues
    
    def _remove_current_hash(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Remove current_hash for verification."""
        entry_copy = json.loads(json.dumps(entry))
        if "integrity" in entry_copy and "current_hash" in entry_copy["integrity"]:
            del entry_copy["integrity"]["current_hash"]
        return entry_copy
    
    def _compute_hash(self, data: Dict[str, Any]) -> str:
        """Compute SHA-256 hash."""
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()


# =============================================================================
# Pending Sequence Watchdog (Self-Cleanup)
# =============================================================================

class PendingSequenceWatchdog:
    """
    Background watchdog for cleaning stale pending sequences.
    
    Problem:
        PENDING entries may be orphaned if process crashes after reserve
        but before commit/abort. Global TTL (60s) is too long for responsiveness.
    
    Solution:
        Local watchdog thread monitors own reservations.
        On write failure, immediately cleans up (no TTL wait).
    
    Pattern source:
        audit/audit_watchdog.py#L150-270 (daemon thread pattern)
        api/django/rate_limit.py#L176-178 (cleanup interval pattern)
    
    Usage:
        watchdog = PendingSequenceWatchdog(redis_client)
        watchdog.start()
        
        seq = watchdog.register_pending(5)
        try:
            do_write()
            watchdog.mark_committed(seq)
        except:
            watchdog.mark_failed(seq)  # Immediate cleanup
    """
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        check_interval_seconds: float = 5.0,
        stale_threshold_seconds: float = 30.0,
    ):
        """
        Initialize pending sequence watchdog.
        
        Args:
            redis_client: Redis client
            key_prefix: Key prefix for Redis keys
            check_interval_seconds: How often to check for stale entries
            stale_threshold_seconds: Age after which entry is considered stale
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._check_interval = check_interval_seconds
        self._stale_threshold = stale_threshold_seconds
        
        # Track local pending sequences
        self._local_pending: Dict[int, float] = {}  # seq -> monotonic_time
        self._lock = threading.RLock()
        
        # Background thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        
        # Stats
        self._cleaned_count = 0
    
    def start(self) -> None:
        """Start watchdog thread."""
        with self._lock:
            if self._is_running:
                return
            
            self._is_running = True
            self._stop_event.clear()
            
            self._thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="PendingSequenceWatchdog",
            )
            self._thread.start()
            logger.info("[PendingWatchdog] Started")
    
    def stop(self, timeout: float = 5.0) -> None:
        """Stop watchdog thread."""
        with self._lock:
            if not self._is_running:
                return
            
            self._stop_event.set()
            
            if self._thread:
                self._thread.join(timeout=timeout)
            
            self._is_running = False
            logger.info(f"[PendingWatchdog] Stopped. Cleaned: {self._cleaned_count}")
    
    def register_pending(self, sequence: int) -> None:
        """Register a pending sequence for tracking."""
        with self._lock:
            self._local_pending[sequence] = time.monotonic()
    
    def mark_committed(self, sequence: int) -> None:
        """Mark sequence as committed (remove from tracking)."""
        with self._lock:
            self._local_pending.pop(sequence, None)
    
    def mark_failed(self, sequence: int) -> None:
        """
        Mark sequence as failed (immediate cleanup).
        
        Unlike waiting for TTL, this cleans up immediately.
        """
        with self._lock:
            self._local_pending.pop(sequence, None)
        
        # Immediate Redis cleanup
        try:
            pending_key = f"{self._key_prefix}audit:hash_chain:pending:{sequence}"
            self._redis.delete(pending_key)
            self._cleaned_count += 1
            logger.debug(f"[PendingWatchdog] Immediately cleaned seq {sequence}")
        except Exception as e:
            logger.warning(f"[PendingWatchdog] Cleanup failed for seq {sequence}: {e}")
    
    def _cleanup_loop(self) -> None:
        """Background cleanup loop."""
        while not self._stop_event.is_set():
            try:
                self._cleanup_stale_local()
            except Exception as e:
                logger.error(f"[PendingWatchdog] Cleanup error: {e}")
            
            self._stop_event.wait(timeout=self._check_interval)
    
    def _cleanup_stale_local(self) -> None:
        """Clean up locally tracked stale entries."""
        now = time.monotonic()
        stale_sequences = []
        
        with self._lock:
            for seq, start_time in list(self._local_pending.items()):
                if now - start_time > self._stale_threshold:
                    stale_sequences.append(seq)
                    del self._local_pending[seq]
        
        for seq in stale_sequences:
            try:
                pending_key = f"{self._key_prefix}audit:hash_chain:pending:{seq}"
                deleted = self._redis.delete(pending_key)
                if deleted:
                    self._cleaned_count += 1
                    logger.info(f"[PendingWatchdog] Cleaned stale seq {seq}")
            except Exception as e:
                logger.warning(f"[PendingWatchdog] Stale cleanup failed for {seq}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get watchdog statistics."""
        with self._lock:
            return {
                "local_pending_count": len(self._local_pending),
                "cleaned_count": self._cleaned_count,
                "is_running": self._is_running,
            }


# =============================================================================
# Performance Manager (Unified Access)
# =============================================================================

class HashChainPerformanceManager:
    """
    Unified manager for all performance optimization components.
    
    Provides lazy initialization and centralized access to:
    - lua_chain: LuaAtomicHashChain (5 RTT → 1 RTT)
    - batch_query: PipelineBatchQuery (batch state retrieval)
    - batch_writer: BatchFlushWriter (n×fsync → 1×fsync)
    - async_writer: AsyncAuditWriter (non-blocking writes)
    - sampler: SamplingVerifier (O(n) → O(k))
    - watchdog: PendingSequenceWatchdog (self-cleanup)
    
    Pattern source:
        adapters/django/middleware.py#L647 (lazy initialization)
    """
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        log_dir: Optional[Path] = None,
        key_prefix: str = "selfhealing:",
    ):
        """
        Initialize performance manager.
        
        Args:
            redis_client: Redis client (required for distributed features)
            log_dir: Directory for log files
            key_prefix: Redis key prefix
        """
        self._redis = redis_client
        self._log_dir = Path(log_dir) if log_dir else Path("logs/audit")
        self._key_prefix = key_prefix
        self._lock = threading.RLock()
        
        # Lazy-initialized components
        self._lua_chain: Optional[LuaAtomicHashChain] = None
        self._batch_query: Optional[PipelineBatchQuery] = None
        self._batch_writer: Optional[BatchFlushWriter] = None
        self._async_writer: Optional[AsyncAuditWriter] = None
        self._sampler: Optional[SamplingVerifier] = None
        self._watchdog: Optional[PendingSequenceWatchdog] = None
    
    @property
    def lua_chain(self) -> LuaAtomicHashChain:
        """Lazy-init Lua atomic hash chain."""
        if self._lua_chain is None:
            with self._lock:
                if self._lua_chain is None:
                    if not self._redis:
                        raise ValueError("Redis client required for LuaAtomicHashChain")
                    self._lua_chain = LuaAtomicHashChain(
                        redis_client=self._redis,
                        key_prefix=self._key_prefix,
                    )
        return self._lua_chain
    
    @property
    def batch_query(self) -> PipelineBatchQuery:
        """Lazy-init pipeline batch query."""
        if self._batch_query is None:
            with self._lock:
                if self._batch_query is None:
                    if not self._redis:
                        raise ValueError("Redis client required for PipelineBatchQuery")
                    self._batch_query = PipelineBatchQuery(
                        redis_client=self._redis,
                        key_prefix=self._key_prefix,
                    )
        return self._batch_query
    
    @property
    def sampler(self) -> SamplingVerifier:
        """Lazy-init sampling verifier."""
        if self._sampler is None:
            with self._lock:
                if self._sampler is None:
                    self._sampler = SamplingVerifier()
        return self._sampler
    
    def get_batch_writer(
        self,
        file_path: Path,
        config: Optional[BatchFlushConfig] = None,
    ) -> BatchFlushWriter:
        """Create batch writer for specific file."""
        return BatchFlushWriter(file_path, config)
    
    def get_async_writer(
        self,
        sync_writer: Callable[[Dict[str, Any]], bool],
    ) -> AsyncAuditWriter:
        """Create async writer wrapping sync function."""
        return AsyncAuditWriter(sync_writer)
    
    def get_watchdog(self) -> PendingSequenceWatchdog:
        """Lazy-init pending sequence watchdog."""
        if self._watchdog is None:
            with self._lock:
                if self._watchdog is None:
                    if not self._redis:
                        raise ValueError("Redis client required for PendingSequenceWatchdog")
                    self._watchdog = PendingSequenceWatchdog(
                        redis_client=self._redis,
                        key_prefix=self._key_prefix,
                    )
        return self._watchdog
    
    def start_watchdog(self) -> None:
        """Start watchdog if not already running."""
        watchdog = self.get_watchdog()
        watchdog.start()
    
    def stop_all(self) -> None:
        """Stop all background components."""
        if self._watchdog and self._watchdog._is_running:
            self._watchdog.stop()
        
        if self._async_writer and self._async_writer._is_running:
            self._async_writer.stop()
        
        if self._batch_writer:
            self._batch_writer.close()
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics from all components."""
        stats = {}
        
        if self._batch_writer:
            stats["batch_writer"] = self._batch_writer.get_stats()
        
        if self._async_writer:
            stats["async_writer"] = self._async_writer.get_stats()
        
        if self._watchdog:
            stats["watchdog"] = self._watchdog.get_stats()
        
        return stats
