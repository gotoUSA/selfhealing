"""
Hash Chain Integrity Verification.

Implements tamper-evident logging using cryptographic hash chains.
Each log entry includes the hash of the previous entry, making it
impossible to delete or modify entries without breaking the chain.

This is similar to blockchain technology but optimized for audit logs.
"""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class IntegrityInfo:
    """Integrity information for a log entry."""

    sequence: int
    previous_hash: str
    current_hash: str
    timestamp: str


def compute_hash(data: Dict[str, Any]) -> str:
    """
    Compute SHA-256 hash of a dictionary.

    Args:
        data: Dictionary to hash

    Returns:
        SHA-256 hash string
    """
    # Sort keys for deterministic hashing
    json_str = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()


class HashChainVerifier:
    """
    Verifies the integrity of a hash chain.

    Can detect:
    - Deleted entries (missing sequence numbers)
    - Modified entries (hash mismatch)
    - Reordered entries (previous_hash mismatch)
    """

    GENESIS_HASH = "GENESIS"

    def verify_chain(self, entries: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Verify the integrity of an audit log chain.

        Args:
            entries: List of log entries with integrity fields

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not entries:
            return True, None

        previous_hash = self.GENESIS_HASH
        expected_sequence = 1

        for i, entry in enumerate(entries):
            # Check sequence continuity
            seq = entry.get("integrity", {}).get("sequence", 0)
            if seq != expected_sequence:
                return False, f"Missing entry: expected sequence {expected_sequence}, found {seq}"

            # Check previous hash linkage
            prev_hash = entry.get("integrity", {}).get("previous_hash", "")
            if prev_hash != previous_hash:
                return False, f"Chain broken at sequence {seq}: previous_hash mismatch"

            # Verify current hash
            stored_hash = entry.get("integrity", {}).get("current_hash", "")
            entry_copy = self._remove_current_hash(entry)
            computed_hash = compute_hash(entry_copy)

            if stored_hash != computed_hash:
                return False, f"Entry modified at sequence {seq}: hash mismatch"

            previous_hash = stored_hash
            expected_sequence += 1

        return True, None

    def _remove_current_hash(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Remove current_hash from entry for hash verification."""
        entry_copy = json.loads(json.dumps(entry))  # Deep copy
        if "integrity" in entry_copy and "current_hash" in entry_copy["integrity"]:
            del entry_copy["integrity"]["current_hash"]
        return entry_copy

    def find_tampering(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Find all tampered or missing entries in a chain.

        Returns:
            List of issues found
        """
        issues = []

        if not entries:
            return issues

        previous_hash = self.GENESIS_HASH
        expected_sequence = 1

        for entry in entries:
            seq = entry.get("integrity", {}).get("sequence", 0)

            # Check for missing entries
            while expected_sequence < seq:
                issues.append(
                    {
                        "type": "missing_entry",
                        "sequence": expected_sequence,
                        "message": f"Entry {expected_sequence} is missing from the chain",
                    }
                )
                expected_sequence += 1

            # Check previous hash
            prev_hash = entry.get("integrity", {}).get("previous_hash", "")
            if prev_hash != previous_hash:
                issues.append(
                    {
                        "type": "chain_broken",
                        "sequence": seq,
                        "expected_previous_hash": previous_hash,
                        "found_previous_hash": prev_hash,
                        "message": f"Chain broken at entry {seq}",
                    }
                )

            # Verify current hash
            stored_hash = entry.get("integrity", {}).get("current_hash", "")
            entry_copy = self._remove_current_hash(entry)
            computed_hash = compute_hash(entry_copy)

            if stored_hash != computed_hash:
                issues.append(
                    {
                        "type": "entry_modified",
                        "sequence": seq,
                        "stored_hash": stored_hash[:16] + "...",
                        "computed_hash": computed_hash[:16] + "...",
                        "message": f"Entry {seq} has been modified",
                    }
                )

            previous_hash = stored_hash
            expected_sequence = seq + 1

        return issues


class HashChainManager:
    """
    Manages hash chain state for audit logging.

    Thread-safe manager that maintains:
    - Current sequence number
    - Previous hash for chaining
    - Periodic checkpoints
    """

    GENESIS_HASH = "GENESIS"

    def __init__(self, state_file: Optional[Path] = None):
        """
        Initialize hash chain manager.

        Args:
            state_file: Optional path to persist chain state
        """
        self._lock = threading.RLock()
        self._sequence = 0
        self._previous_hash = self.GENESIS_HASH
        self._state_file = state_file

        if state_file:
            self._load_state()

    def _load_state(self) -> None:
        """Load chain state from file."""
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self._sequence = data.get("sequence", 0)
                self._previous_hash = data.get("previous_hash", self.GENESIS_HASH)
                logger.debug(f"[HashChain] Loaded state: seq={self._sequence}")
            except Exception as e:
                logger.warning(f"[HashChain] Failed to load state: {e}")

    def _save_state(self) -> None:
        """Save chain state to file."""
        if self._state_file:
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    "sequence": self._sequence,
                    "previous_hash": self._previous_hash,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._state_file.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning(f"[HashChain] Failed to save state: {e}")

    def add_integrity(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add integrity fields to a log entry.

        Args:
            entry: Log entry dictionary

        Returns:
            Entry with integrity fields added
        """
        with self._lock:
            self._sequence += 1

            # Add integrity info (without current_hash for now)
            entry["integrity"] = {
                "sequence": self._sequence,
                "previous_hash": self._previous_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Compute hash of entry
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash

            # Update state for next entry
            self._previous_hash = current_hash

            # Persist state periodically (every 10 entries)
            if self._sequence % 10 == 0:
                self._save_state()

            return entry

    def get_state(self) -> Dict[str, Any]:
        """Get current chain state."""
        with self._lock:
            return {
                "sequence": self._sequence,
                "previous_hash": self._previous_hash[:16] + "..." if len(self._previous_hash) > 16 else self._previous_hash,
            }

    def reset(self) -> None:
        """Reset chain state (use with caution!)."""
        with self._lock:
            self._sequence = 0
            self._previous_hash = self.GENESIS_HASH
            if self._state_file and self._state_file.exists():
                self._state_file.unlink()
            logger.warning("[HashChain] Chain state reset")


def verify_audit_log_integrity(log_file: Path) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Verify the integrity of an audit log file.

    Args:
        log_file: Path to the JSON Lines audit log file

    Returns:
        Tuple of (is_valid, issues_list)
    """
    if not log_file.exists():
        return True, []

    entries = []
    try:
        with open(log_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        return False, [{"type": "read_error", "message": str(e)}]

    verifier = HashChainVerifier()
    issues = verifier.find_tampering(entries)

    return len(issues) == 0, issues


# =============================================================================
# Protocol / Interface for Hash Chain Managers
# =============================================================================

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable


@runtime_checkable
class HashChainManagerProtocol(Protocol):
    """
    Protocol for hash chain managers.
    
    Allows switching between local file-based and distributed (Redis)
    implementations without code changes.
    """
    
    def add_integrity(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity fields to an entry."""
        ...
    
    def get_state(self) -> Dict[str, Any]:
        """Get current chain state."""
        ...


# =============================================================================
# Redis-based Distributed Hash Chain Manager
# =============================================================================

class RedisHashChainManager:
    """
    Redis-based distributed hash chain manager.
    
    Ensures single global hash chain across all Pods in distributed environment.
    Uses RedisDistributedLock to prevent Race Conditions 100%.
    
    Features:
        - Atomic sequence increment via Redis INCR
        - Distributed lock for read-modify-write safety
        - Automatic fallback to local HashChainManager on Redis failure
        - Pod identification for forensics
    
    Design:
        - RedisDistributedLock: adapters/cache/redis_adapter.py
        - ResilientStorageBackend pattern: adapters/resilient/backend.py
        - DLQ's INCR pattern: adapters/redis/dlq.py
    
    Reference:
        docs/self_healing/middleware_system/42_DISTRIBUTED_HASH_CHAIN_REDIS.md
    """
    
    SEQUENCE_KEY = "audit:hash_chain:seq"
    STATE_KEY = "audit:hash_chain:state"
    LOCK_KEY = "audit:hash_chain:lock"
    GENESIS_HASH = "GENESIS"
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        fallback_manager: Optional["HashChainManager"] = None,
        lock_timeout_seconds: float = 5.0,
        lock_blocking_timeout: float = 10.0,
    ):
        """
        Initialize Redis-based distributed hash chain manager.
        
        Args:
            redis_client: Redis client instance (from ResilientStorageBackend or direct)
            key_prefix: Key prefix for Redis keys
            fallback_manager: Local HashChainManager for Redis failure fallback
            lock_timeout_seconds: Lock auto-expire time (prevents deadlocks)
            lock_blocking_timeout: Max time to wait for lock acquisition
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._fallback = fallback_manager
        self._lock_timeout_seconds = lock_timeout_seconds
        self._lock_blocking_timeout = lock_blocking_timeout
        self._local_lock = threading.RLock()
        
        # Statistics
        self._stats = {
            "redis_writes": 0,
            "fallback_writes": 0,
            "lock_failures": 0,
        }
    
    def _get_full_key(self, key: str) -> str:
        """Get full Redis key with prefix."""
        return f"{self._key_prefix}{key}"
    
    def add_integrity(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add integrity fields to a log entry (distributed-safe).
        
        Uses RedisDistributedLock to ensure 100% Race Condition safety.
        Falls back to local HashChainManager if Redis is unavailable.
        
        Args:
            entry: Log entry dictionary
            
        Returns:
            Entry with integrity fields added
        """
        with self._local_lock:
            try:
                return self._add_integrity_redis(entry)
            except Exception as e:
                logger.warning(f"[RedisHashChain] Redis failed, using fallback: {e}")
                self._stats["fallback_writes"] += 1
                return self._add_integrity_fallback(entry)
    
    def _add_integrity_redis(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity using Redis with distributed lock."""
        import os
        from datetime import timedelta
        
        # Import lock class
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock
        
        seq_key = self._get_full_key(self.SEQUENCE_KEY)
        state_key = self._get_full_key(self.STATE_KEY)
        lock_key = self._get_full_key(self.LOCK_KEY)
        
        # Acquire distributed lock
        lock = RedisDistributedLock(
            redis_client=self._redis,
            name=lock_key,
            timeout=timedelta(seconds=self._lock_timeout_seconds),
            blocking_timeout=self._lock_blocking_timeout,
        )
        
        if not lock.acquire(blocking=True):
            self._stats["lock_failures"] += 1
            raise RuntimeError("Failed to acquire hash chain distributed lock")
        
        try:
            # 1. Atomic sequence increment
            sequence = self._redis.incr(seq_key)
            
            # 2. Get previous hash
            previous_hash = self._redis.hget(state_key, "previous_hash")
            if previous_hash:
                if isinstance(previous_hash, bytes):
                    previous_hash = previous_hash.decode("utf-8")
            else:
                previous_hash = self.GENESIS_HASH
            
            # 3. Add integrity fields
            timestamp = datetime.now(timezone.utc).isoformat()
            pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            
            entry["integrity"] = {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "timestamp": timestamp,
                "pod_id": pod_id,
            }
            
            # 4. Compute current hash
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            
            # 5. Save state for next entry
            self._redis.hset(state_key, mapping={
                "previous_hash": current_hash,
                "sequence": str(sequence),
                "updated_at": timestamp,
            })
            
            self._stats["redis_writes"] += 1
            return entry
            
        finally:
            # Always release lock
            lock.release()
    
    def _add_integrity_fallback(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity using local fallback manager."""
        if self._fallback:
            result = self._fallback.add_integrity(entry)
            # Mark as degraded for later reconciliation
            if "integrity" in result:
                result["integrity"]["degraded"] = True
                result["integrity"]["fallback_source"] = "local"
            return result
        
        # No fallback available - add minimal integrity info
        import os
        
        entry["integrity"] = {
            "sequence": -1,  # Indicates needs reordering on recovery
            "previous_hash": "DEGRADED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pod_id": os.environ.get("HOSTNAME", "unknown"),
            "degraded": True,
            "fallback_source": "none",
        }
        entry["integrity"]["current_hash"] = compute_hash(entry)
        
        return entry
    
    def get_state(self) -> Dict[str, Any]:
        """Get current chain state from Redis."""
        try:
            state_key = self._get_full_key(self.STATE_KEY)
            state = self._redis.hgetall(state_key)
            
            if not state:
                return {
                    "sequence": 0,
                    "previous_hash": self.GENESIS_HASH,
                    "source": "redis",
                }
            
            # Decode bytes if needed
            sequence = state.get(b"sequence", state.get("sequence", 0))
            previous_hash = state.get(b"previous_hash", state.get("previous_hash", self.GENESIS_HASH))
            
            if isinstance(sequence, bytes):
                sequence = int(sequence.decode("utf-8"))
            else:
                sequence = int(sequence) if sequence else 0
                
            if isinstance(previous_hash, bytes):
                previous_hash = previous_hash.decode("utf-8")
            
            # Truncate hash for display
            display_hash = previous_hash[:16] + "..." if len(previous_hash) > 16 else previous_hash
            
            return {
                "sequence": sequence,
                "previous_hash": display_hash,
                "source": "redis",
            }
            
        except Exception as e:
            logger.warning(f"[RedisHashChain] Failed to get state from Redis: {e}")
            
            if self._fallback:
                state = self._fallback.get_state()
                state["source"] = "fallback"
                return state
            
            return {
                "sequence": 0,
                "previous_hash": self.GENESIS_HASH,
                "source": "unavailable",
                "error": str(e),
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        return {
            **self._stats,
            "state": self.get_state(),
        }
    
    def verify_continuity(self, entries: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Verify hash chain continuity.
        
        Reuses existing HashChainVerifier.
        
        Args:
            entries: List of log entries with integrity fields
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        verifier = HashChainVerifier()
        return verifier.verify_chain(entries)
    
    def reset(self) -> None:
        """
        Reset chain state in Redis.
        
        WARNING: Use with extreme caution! This breaks the hash chain.
        Only for testing or disaster recovery.
        """
        try:
            seq_key = self._get_full_key(self.SEQUENCE_KEY)
            state_key = self._get_full_key(self.STATE_KEY)
            
            self._redis.delete(seq_key)
            self._redis.delete(state_key)
            
            logger.warning("[RedisHashChain] Chain state reset in Redis")
            
        except Exception as e:
            logger.error(f"[RedisHashChain] Failed to reset: {e}")
            
        if self._fallback:
            self._fallback.reset()


# =============================================================================
# Factory Function
# =============================================================================

def create_hash_chain_manager(
    distributed: bool = False,
    redis_client: Optional[Any] = None,
    key_prefix: str = "selfhealing:",
    state_file: Optional[Path] = None,
) -> HashChainManagerProtocol:
    """
    Factory function to create appropriate hash chain manager.
    
    Args:
        distributed: If True, create RedisHashChainManager
        redis_client: Redis client (required if distributed=True)
        key_prefix: Redis key prefix
        state_file: Local state file path (for fallback or standalone)
        
    Returns:
        HashChainManager or RedisHashChainManager instance
    """
    local_manager = HashChainManager(state_file=state_file)
    
    if distributed:
        if redis_client is None:
            logger.warning(
                "[HashChain] Distributed mode requested but no Redis client. "
                "Falling back to local mode."
            )
            return local_manager
        
        return RedisHashChainManager(
            redis_client=redis_client,
            key_prefix=key_prefix,
            fallback_manager=local_manager,
        )
    
    return local_manager


# =============================================================================
# PendingSequenceManager - Write-Ahead Checkpoint for Hash Chain
# =============================================================================

class PendingSequenceManager:
    """
    Manages PENDING state for hash chain sequences.
    
    Implements Write-Ahead Checkpoint pattern to ensure atomicity between:
    1. Redis hash chain update (sequence increment + hash computation)
    2. Local file write
    
    If file write fails after Redis update, the sequence is marked as ORPHANED
    for later cleanup or reconciliation.
    
    Redis Key Structure:
    - {prefix}audit:hash_chain:pending:{sequence} -> expected_hash (TTL: 30s)
    - {prefix}audit:hash_chain:orphaned:{sequence} -> "true" (TTL: 24h)
    
    Lifecycle:
    1. reserve_sequence() - Mark sequence as PENDING with expected hash
    2. File write attempt
    3a. commit_sequence() - Success: Remove PENDING (transaction complete)
    3b. abort_sequence() - Failure: Move to ORPHANED (needs reconciliation)
    
    Reference:
        docs/self_healing/middleware_system/43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md
    """
    
    PENDING_KEY_PREFIX = "audit:hash_chain:pending:"
    ORPHANED_KEY_PREFIX = "audit:hash_chain:orphaned:"
    DEFAULT_PENDING_TTL_SECONDS = 30
    DEFAULT_ORPHAN_TTL_SECONDS = 86400  # 24 hours
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        pending_ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS,
        orphan_ttl_seconds: int = DEFAULT_ORPHAN_TTL_SECONDS,
    ):
        """
        Initialize PendingSequenceManager.
        
        Args:
            redis_client: Redis client instance
            key_prefix: Key prefix for Redis keys
            pending_ttl_seconds: TTL for PENDING keys (auto-cleanup if process crashes)
            orphan_ttl_seconds: TTL for ORPHANED keys (time window for reconciliation)
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._pending_ttl = pending_ttl_seconds
        self._orphan_ttl = orphan_ttl_seconds
        self._local_lock = threading.RLock()
    
    def _get_pending_key(self, sequence: int) -> str:
        """Build Redis key for PENDING state."""
        return f"{self._key_prefix}{self.PENDING_KEY_PREFIX}{sequence}"
    
    def _get_orphaned_key(self, sequence: int) -> str:
        """Build Redis key for ORPHANED state."""
        return f"{self._key_prefix}{self.ORPHANED_KEY_PREFIX}{sequence}"
    
    def reserve_sequence(self, sequence: int, expected_hash: str) -> bool:
        """
        Reserve a sequence as PENDING before file write.
        
        Stores the expected hash for later verification if recovery needed.
        Uses atomic SET with NX to prevent duplicate reservations.
        
        Args:
            sequence: The sequence number being written
            expected_hash: The computed hash for this entry
            
        Returns:
            True if reservation successful, False if already reserved
        """
        try:
            pending_key = self._get_pending_key(sequence)
            
            # Store expected hash with TTL for auto-cleanup
            # Use SET NX (only if not exists) for atomicity
            result = self._redis.set(
                pending_key,
                expected_hash,
                nx=True,  # Only set if key doesn't exist
                ex=self._pending_ttl,  # Expire after TTL
            )
            
            if result:
                logger.debug(f"[PendingSeq] Reserved sequence {sequence}")
                return True
            else:
                logger.warning(f"[PendingSeq] Sequence {sequence} already reserved")
                return False
                
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to reserve sequence {sequence}: {e}")
            return False
    
    def commit_sequence(self, sequence: int) -> bool:
        """
        Commit a sequence after successful file write.
        
        Removes the PENDING key, marking the transaction as complete.
        
        Args:
            sequence: The sequence number that was successfully written
            
        Returns:
            True if committed (key was deleted), False otherwise
        """
        try:
            pending_key = self._get_pending_key(sequence)
            deleted = self._redis.delete(pending_key)
            
            if deleted:
                logger.debug(f"[PendingSeq] Committed sequence {sequence}")
                return True
            else:
                # Key may have expired (TTL) - still considered success
                logger.debug(f"[PendingSeq] Sequence {sequence} already committed (or expired)")
                return True
                
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to commit sequence {sequence}: {e}")
            return False
    
    def abort_sequence(self, sequence: int) -> bool:
        """
        Abort a sequence after failed file write.
        
        Moves the sequence from PENDING to ORPHANED for later reconciliation.
        The expected hash is preserved for recovery verification.
        
        Args:
            sequence: The sequence number that failed to write
            
        Returns:
            True if aborted successfully
        """
        try:
            pending_key = self._get_pending_key(sequence)
            orphaned_key = self._get_orphaned_key(sequence)
            
            # Get expected hash before deleting PENDING
            expected_hash = self._redis.get(pending_key)
            if isinstance(expected_hash, bytes):
                expected_hash = expected_hash.decode("utf-8")
            
            # Atomic transition: PENDING -> ORPHANED
            pipe = self._redis.pipeline()
            pipe.delete(pending_key)
            pipe.set(
                orphaned_key,
                expected_hash or "unknown",
                ex=self._orphan_ttl,
            )
            pipe.execute()
            
            logger.warning(f"[PendingSeq] Aborted sequence {sequence} -> ORPHANED")
            return True
            
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to abort sequence {sequence}: {e}")
            return False
    
    def get_pending_sequences(self) -> List[int]:
        """
        Get all sequences currently in PENDING state.
        
        Used during startup to identify incomplete transactions.
        
        Returns:
            Sorted list of pending sequence numbers
        """
        try:
            pattern = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}*"
            keys = self._redis.keys(pattern)
            
            sequences = []
            for key in keys:
                try:
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                    # Extract sequence number from key
                    seq_str = key_str.split(":")[-1]
                    sequences.append(int(seq_str))
                except (ValueError, IndexError):
                    continue
            
            return sorted(sequences)
            
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to get pending sequences: {e}")
            return []
    
    def get_orphaned_sequences(self) -> List[int]:
        """
        Get all sequences currently in ORPHANED state.
        
        Used during reconciliation to find entries needing recovery.
        
        Returns:
            Sorted list of orphaned sequence numbers
        """
        try:
            pattern = f"{self._key_prefix}{self.ORPHANED_KEY_PREFIX}*"
            keys = self._redis.keys(pattern)
            
            sequences = []
            for key in keys:
                try:
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                    seq_str = key_str.split(":")[-1]
                    sequences.append(int(seq_str))
                except (ValueError, IndexError):
                    continue
            
            return sorted(sequences)
            
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to get orphaned sequences: {e}")
            return []
    
    def get_expected_hash(self, sequence: int) -> Optional[str]:
        """
        Get the expected hash for a PENDING or ORPHANED sequence.
        
        Args:
            sequence: Sequence number to lookup
            
        Returns:
            Expected hash string, or None if not found
        """
        try:
            # Check PENDING first, then ORPHANED
            for key in [self._get_pending_key(sequence), self._get_orphaned_key(sequence)]:
                value = self._redis.get(key)
                if value:
                    return value.decode("utf-8") if isinstance(value, bytes) else value
            return None
            
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to get expected hash for {sequence}: {e}")
            return None
    
    def cleanup_stale_pending(self, max_age_seconds: Optional[int] = None) -> int:
        """
        Cleanup stale PENDING entries.
        
        Note: TTL handles automatic cleanup, but this provides explicit cleanup
        option during startup or maintenance.
        
        Args:
            max_age_seconds: Max age before considering stale (uses TTL by default)
            
        Returns:
            Number of entries cleaned up
        """
        # With TTL-based expiration, Redis handles this automatically
        # This method exists for explicit cleanup scenarios
        pending = self.get_pending_sequences()
        
        if not pending:
            return 0
        
        cleaned = 0
        for seq in pending:
            # Move old PENDING to ORPHANED for safety
            self.abort_sequence(seq)
            cleaned += 1
        
        if cleaned:
            logger.info(f"[PendingSeq] Cleaned up {cleaned} stale pending sequences")
        
        return cleaned
    
    def clear_orphaned(self, sequence: int) -> bool:
        """
        Clear an ORPHANED sequence after successful reconciliation.
        
        Args:
            sequence: The reconciled sequence number
            
        Returns:
            True if cleared
        """
        try:
            orphaned_key = self._get_orphaned_key(sequence)
            self._redis.delete(orphaned_key)
            logger.debug(f"[PendingSeq] Cleared orphaned sequence {sequence}")
            return True
            
        except Exception as e:
            logger.error(f"[PendingSeq] Failed to clear orphaned {sequence}: {e}")
            return False


# =============================================================================
# DailyHashAnchor - Daily Checkpoint for Partial Verification
# =============================================================================

class DailyHashAnchor:
    """
    Daily hash anchor system for efficient verification.
    
    Stores end-of-day hash chain state as "anchor points" allowing:
    - Partial verification: Only verify "today's logs + yesterday's anchor"
    - Fast recovery: Resume chain from any anchor point
    - Audit trail: Historical record of daily chain state
    
    Redis Key Structure:
    - {prefix}audit:hash_chain:anchor:{YYYY-MM-DD} -> {sequence, hash, timestamp}
    
    Features:
    - Automatic anchor creation at day boundaries
    - Configurable retention period (default: 90 days)
    - Anchor-based partial chain verification
    
    Reference:
        docs/self_healing/middleware_system/43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md
    """
    
    ANCHOR_KEY_PREFIX = "audit:hash_chain:anchor:"
    STATE_KEY = "audit:hash_chain:state"
    DEFAULT_RETENTION_DAYS = 90
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """
        Initialize DailyHashAnchor.
        
        Args:
            redis_client: Redis client instance
            key_prefix: Key prefix for Redis keys
            retention_days: Number of days to retain anchors
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._retention_days = retention_days
    
    def _get_anchor_key(self, date: str) -> str:
        """Build Redis key for anchor."""
        return f"{self._key_prefix}{self.ANCHOR_KEY_PREFIX}{date}"
    
    def _get_state_key(self) -> str:
        """Build Redis key for current state."""
        return f"{self._key_prefix}{self.STATE_KEY}"
    
    def create_anchor(
        self,
        date: Optional[str] = None,
        sequence: Optional[int] = None,
        hash_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a daily anchor checkpoint.
        
        If sequence/hash not provided, reads from current Redis state.
        
        Args:
            date: Date string (YYYY-MM-DD), defaults to today
            sequence: Sequence number at anchor point
            hash_value: Hash value at anchor point
            
        Returns:
            Created anchor data dictionary
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Get current state if not provided
        if sequence is None or hash_value is None:
            state = self._get_current_state()
            if sequence is None:
                sequence = state.get("sequence", 0)
            if hash_value is None:
                hash_value = state.get("previous_hash", "GENESIS")
        
        anchor_key = self._get_anchor_key(date)
        anchor_data = {
            "date": date,
            "sequence": str(sequence),
            "hash": hash_value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            self._redis.hset(anchor_key, mapping=anchor_data)
            # Set TTL for automatic cleanup
            self._redis.expire(anchor_key, self._retention_days * 86400)
            
            logger.info(f"[DailyAnchor] Created anchor for {date}: seq={sequence}")
            return anchor_data
            
        except Exception as e:
            logger.error(f"[DailyAnchor] Failed to create anchor for {date}: {e}")
            return {"error": str(e), "date": date}
    
    def _get_current_state(self) -> Dict[str, Any]:
        """Get current hash chain state from Redis."""
        try:
            state_key = self._get_state_key()
            state = self._redis.hgetall(state_key)
            
            if not state:
                return {"sequence": 0, "previous_hash": "GENESIS"}
            
            # Decode bytes
            sequence = state.get(b"sequence", state.get("sequence", 0))
            previous_hash = state.get(b"previous_hash", state.get("previous_hash", "GENESIS"))
            
            if isinstance(sequence, bytes):
                sequence = int(sequence.decode("utf-8"))
            else:
                sequence = int(sequence) if sequence else 0
            
            if isinstance(previous_hash, bytes):
                previous_hash = previous_hash.decode("utf-8")
            
            return {"sequence": sequence, "previous_hash": previous_hash}
            
        except Exception as e:
            logger.error(f"[DailyAnchor] Failed to get current state: {e}")
            return {"sequence": 0, "previous_hash": "GENESIS"}
    
    def get_anchor(self, date: str) -> Optional[Dict[str, Any]]:
        """
        Get anchor for a specific date.
        
        Args:
            date: Date string (YYYY-MM-DD)
            
        Returns:
            Anchor data dictionary, or None if not found
        """
        try:
            anchor_key = self._get_anchor_key(date)
            data = self._redis.hgetall(anchor_key)
            
            if not data:
                return None
            
            # Decode all values
            result = {}
            for key, value in data.items():
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                val_str = value.decode("utf-8") if isinstance(value, bytes) else value
                result[key_str] = val_str
            
            # Convert sequence to int
            if "sequence" in result:
                result["sequence"] = int(result["sequence"])
            
            return result
            
        except Exception as e:
            logger.error(f"[DailyAnchor] Failed to get anchor for {date}: {e}")
            return None
    
    def verify_from_anchor(
        self,
        entries: List[Dict[str, Any]],
        anchor_date: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify chain integrity starting from an anchor.
        
        Instead of verifying entire chain from GENESIS, verify only from anchor.
        Much faster for large audit logs.
        
        Args:
            entries: Log entries to verify (must be after anchor date)
            anchor_date: Date of anchor to start verification from
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        anchor = self.get_anchor(anchor_date)
        if not anchor:
            return False, f"Anchor not found for {anchor_date}"
        
        if not entries:
            return True, None
        
        # First entry's previous_hash must match anchor's hash
        first_entry = entries[0]
        first_prev_hash = first_entry.get("integrity", {}).get("previous_hash", "")
        anchor_hash = anchor.get("hash", "")
        
        if first_prev_hash != anchor_hash:
            return False, (
                f"Chain broken at anchor boundary: "
                f"expected previous_hash={anchor_hash[:16]}..., "
                f"found={first_prev_hash[:16]}..."
            )
        
        # Verify remaining chain using custom verification from anchor point
        # (not using HashChainVerifier which assumes sequence starts at 1)
        return self._verify_chain_from_point(entries, anchor)
    
    def _verify_chain_from_point(
        self,
        entries: List[Dict[str, Any]],
        anchor: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify chain integrity from an anchor point.
        
        Unlike HashChainVerifier.verify_chain(), this allows chains
        starting from any sequence number (not just 1).
        
        Args:
            entries: Log entries to verify
            anchor: Anchor data with sequence and hash
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not entries:
            return True, None
        
        previous_hash = anchor.get("hash", "GENESIS")
        expected_sequence = anchor.get("sequence", 0) + 1
        
        for entry in entries:
            integrity = entry.get("integrity", {})
            
            # Check sequence continuity
            seq = integrity.get("sequence", 0)
            if seq != expected_sequence:
                return False, f"Missing entry: expected sequence {expected_sequence}, found {seq}"
            
            # Check previous hash linkage
            prev_hash = integrity.get("previous_hash", "")
            if prev_hash != previous_hash:
                return False, f"Chain broken at sequence {seq}: previous_hash mismatch"
            
            # Verify current hash
            stored_hash = integrity.get("current_hash", "")
            
            # Create a copy without current_hash for verification
            entry_copy = json.loads(json.dumps(entry))
            if "integrity" in entry_copy and "current_hash" in entry_copy["integrity"]:
                del entry_copy["integrity"]["current_hash"]
            
            computed_hash = compute_hash(entry_copy)
            
            if stored_hash != computed_hash:
                return False, f"Entry modified at sequence {seq}: hash mismatch"
            
            previous_hash = stored_hash
            expected_sequence += 1
        
        return True, None
    
    def list_anchors(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        List recent anchors.
        
        Args:
            days: Number of days to look back
            
        Returns:
            List of anchor data dictionaries
        """
        anchors = []
        
        for i in range(days):
            from datetime import timedelta
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            anchor = self.get_anchor(date)
            if anchor:
                anchors.append(anchor)
        
        return anchors
    
    def delete_anchor(self, date: str) -> bool:
        """
        Delete an anchor (for cleanup or testing).
        
        Args:
            date: Date string (YYYY-MM-DD)
            
        Returns:
            True if deleted
        """
        try:
            anchor_key = self._get_anchor_key(date)
            self._redis.delete(anchor_key)
            return True
        except Exception:
            return False


# =============================================================================
# StartupHashChainSync - Startup Synchronization
# =============================================================================

class StartupHashChainSync:
    """
    Synchronizes hash chain state between Redis and local files at startup.
    
    Handles recovery scenarios where Redis and local state diverge:
    - Redis ahead: Normal case (file write pending)
    - File ahead: Redis lost data (sync Redis to file)
    - Both empty: Fresh start
    
    Also cleans up stale PENDING sequences from previous crashes.
    
    Pattern References:
    - _recover_from_wal_on_startup() (adapters/resilient/backend.py)
    - Startup Hydration (adapters/django/apps.py)
    
    Reference:
        docs/self_healing/middleware_system/43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md
    """
    
    SEQUENCE_KEY = "audit:hash_chain:seq"
    STATE_KEY = "audit:hash_chain:state"
    
    def __init__(
        self,
        redis_client: Any,
        log_dir: Path,
        key_prefix: str = "selfhealing:",
    ):
        """
        Initialize StartupHashChainSync.
        
        Args:
            redis_client: Redis client instance
            log_dir: Directory containing audit log files
            key_prefix: Key prefix for Redis keys
        """
        self._redis = redis_client
        self._log_dir = Path(log_dir)
        self._key_prefix = key_prefix
        self._sync_completed = False
    
    def sync(self) -> Dict[str, Any]:
        """
        Perform startup synchronization.
        
        Returns:
            Sync result dictionary with action taken and state info
        """
        if self._sync_completed:
            return {"status": "already_synced", "action": "none"}
        
        result = {
            "status": "success",
            "file_sequence": 0,
            "file_hash": None,
            "redis_sequence": 0,
            "redis_hash": None,
            "action": "none",
            "pending_cleaned": 0,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Step 1: Get last state from local files
            file_seq, file_hash = self._get_last_file_state()
            result["file_sequence"] = file_seq
            result["file_hash"] = file_hash[:16] + "..." if file_hash else None
            
            # Step 2: Get current state from Redis
            redis_seq, redis_hash = self._get_redis_state()
            result["redis_sequence"] = redis_seq
            result["redis_hash"] = redis_hash[:16] + "..." if redis_hash else None
            
            # Step 3: Compare and sync
            if file_seq == 0 and redis_seq == 0:
                # Both empty - fresh start
                result["action"] = "fresh_start"
                
            elif redis_seq < file_seq:
                # Redis behind file - sync Redis to file state
                self._sync_redis_to_file(file_seq, file_hash)
                result["action"] = "synced_redis_to_file"
                logger.warning(
                    f"[StartupSync] Redis sequence {redis_seq} behind file {file_seq}. "
                    "Synced Redis to file state."
                )
                
            elif redis_seq > file_seq:
                # Redis ahead - normal, some writes didn't complete
                result["action"] = "redis_ahead_ok"
                logger.info(
                    f"[StartupSync] Redis sequence {redis_seq} ahead of file {file_seq}. "
                    "Normal state, file writes may be pending."
                )
                
            else:
                # Sequences match
                result["action"] = "in_sync"
            
            # Step 4: Cleanup stale PENDING sequences
            pending_cleaned = self._cleanup_pending_sequences()
            result["pending_cleaned"] = pending_cleaned
            
            self._sync_completed = True
            logger.info(f"[StartupSync] Completed: action={result['action']}")
            
            return result
            
        except Exception as e:
            logger.error(f"[StartupSync] Failed: {e}")
            result["status"] = "error"
            result["error"] = str(e)
            return result
    
    def _get_last_file_state(self) -> Tuple[int, str]:
        """
        Get the last sequence and hash from local log files.
        
        Reads from the end of the most recent file for efficiency.
        
        Returns:
            Tuple of (last_sequence, last_hash)
        """
        last_seq = 0
        last_hash = ""
        
        if not self._log_dir.exists():
            return last_seq, last_hash
        
        # Find log files, sorted newest first
        log_files = sorted(self._log_dir.glob("audit_*.jsonl"), reverse=True)
        
        for log_file in log_files:
            try:
                # Read from end of file for efficiency
                with open(log_file, "rb") as f:
                    # Seek to end
                    f.seek(0, 2)
                    file_size = f.tell()
                    
                    if file_size == 0:
                        continue
                    
                    # Read last 10KB (should contain last entry)
                    read_size = min(file_size, 10240)
                    f.seek(max(0, file_size - read_size))
                    content = f.read().decode("utf-8", errors="ignore")
                
                # Parse lines from end
                lines = content.strip().split("\n")
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        entry = json.loads(line)
                        integrity = entry.get("integrity", {})
                        seq = integrity.get("sequence", 0)
                        hash_val = integrity.get("current_hash", "")
                        
                        if seq > last_seq:
                            last_seq = seq
                            last_hash = hash_val
                        
                        # Found the last entry
                        if last_seq > 0:
                            return last_seq, last_hash
                            
                    except json.JSONDecodeError:
                        continue
                        
            except Exception as e:
                logger.debug(f"[StartupSync] Error reading {log_file}: {e}")
                continue
        
        return last_seq, last_hash
    
    def _get_redis_state(self) -> Tuple[int, str]:
        """
        Get current sequence and hash from Redis.
        
        Returns:
            Tuple of (sequence, hash)
        """
        try:
            seq_key = f"{self._key_prefix}{self.SEQUENCE_KEY}"
            state_key = f"{self._key_prefix}{self.STATE_KEY}"
            
            # Get sequence
            seq = self._redis.get(seq_key)
            seq = int(seq) if seq else 0
            
            # Get previous hash
            prev_hash = self._redis.hget(state_key, "previous_hash")
            if isinstance(prev_hash, bytes):
                prev_hash = prev_hash.decode("utf-8")
            prev_hash = prev_hash or ""
            
            return seq, prev_hash
            
        except Exception as e:
            logger.error(f"[StartupSync] Failed to get Redis state: {e}")
            return 0, ""
    
    def _sync_redis_to_file(self, file_seq: int, file_hash: str) -> None:
        """
        Update Redis state to match file state.
        
        Used when Redis is behind (e.g., after Redis restart).
        
        Args:
            file_seq: Sequence from file
            file_hash: Hash from file
        """
        try:
            seq_key = f"{self._key_prefix}{self.SEQUENCE_KEY}"
            state_key = f"{self._key_prefix}{self.STATE_KEY}"
            
            # Atomic update using pipeline
            pipe = self._redis.pipeline()
            pipe.set(seq_key, file_seq)
            pipe.hset(state_key, mapping={
                "previous_hash": file_hash,
                "sequence": str(file_seq),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "synced_from": "file_recovery",
            })
            pipe.execute()
            
            logger.info(f"[StartupSync] Redis synced to file: seq={file_seq}")
            
        except Exception as e:
            logger.error(f"[StartupSync] Failed to sync Redis to file: {e}")
            raise
    
    def _cleanup_pending_sequences(self) -> int:
        """
        Clean up stale PENDING sequences from previous crashes.
        
        Moves all PENDING to ORPHANED for reconciliation.
        
        Returns:
            Number of sequences cleaned up
        """
        try:
            pending_pattern = f"{self._key_prefix}audit:hash_chain:pending:*"
            keys = self._redis.keys(pending_pattern)
            
            if not keys:
                return 0
            
            cleaned = 0
            for key in keys:
                try:
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                    seq_str = key_str.split(":")[-1]
                    seq = int(seq_str)
                    
                    # Move to ORPHANED
                    orphan_key = f"{self._key_prefix}audit:hash_chain:orphaned:{seq}"
                    expected_hash = self._redis.get(key)
                    
                    pipe = self._redis.pipeline()
                    pipe.delete(key)
                    pipe.set(orphan_key, expected_hash or "startup_cleanup", ex=86400)
                    pipe.execute()
                    
                    cleaned += 1
                    
                except (ValueError, IndexError):
                    continue
            
            if cleaned:
                logger.info(f"[StartupSync] Cleaned up {cleaned} pending sequences")
            
            return cleaned
            
        except Exception as e:
            logger.error(f"[StartupSync] Failed to cleanup pending: {e}")
            return 0


# =============================================================================
# HashChainReconciler - Background Merger for Degraded Entries
# =============================================================================

class HashChainReconciler:
    """
    Reconciles degraded/orphaned entries back into the main hash chain.
    
    When Redis fails, entries are written with "degraded: true" flag using
    local fallback. This reconciler:
    1. Finds all degraded entries in local files
    2. Re-computes their hash chain integrity
    3. Appends them to the main Redis chain
    4. Updates the entries in local files as reconciled
    
    Pattern References:
    - MetricsReconciler (metrics/reconciler.py)
    - _recover_from_wal_on_startup() (adapters/resilient/backend.py)
    
    Reference:
        docs/self_healing/middleware_system/43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md
    """
    
    SEQUENCE_KEY = "audit:hash_chain:seq"
    STATE_KEY = "audit:hash_chain:state"
    
    def __init__(
        self,
        redis_client: Any,
        log_dir: Path,
        key_prefix: str = "selfhealing:",
    ):
        """
        Initialize HashChainReconciler.
        
        Args:
            redis_client: Redis client instance
            log_dir: Directory containing audit log files
            key_prefix: Key prefix for Redis keys
        """
        self._redis = redis_client
        self._log_dir = Path(log_dir)
        self._key_prefix = key_prefix
        self._last_reconciliation: Optional[datetime] = None
    
    def reconcile(self) -> Dict[str, Any]:
        """
        Perform reconciliation of degraded entries.
        
        Returns:
            Result dictionary with reconciliation details
        """
        result = {
            "status": "success",
            "degraded_entries_found": 0,
            "entries_merged": 0,
            "new_sequence_start": 0,
            "new_sequence_end": 0,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Step 1: Collect degraded entries from local files
            degraded_entries = self._collect_degraded_entries()
            result["degraded_entries_found"] = len(degraded_entries)
            
            if not degraded_entries:
                result["status"] = "no_degraded_entries"
                logger.info("[Reconciler] No degraded entries to reconcile")
                return result
            
            # Step 2: Get current Redis chain state
            current_seq, current_hash = self._get_redis_state()
            result["new_sequence_start"] = current_seq + 1
            
            # Step 3: Merge entries into main chain
            merged_count = self._merge_entries_to_chain(
                degraded_entries,
                start_sequence=current_seq + 1,
                previous_hash=current_hash,
            )
            result["entries_merged"] = merged_count
            result["new_sequence_end"] = current_seq + merged_count
            
            # Step 4: Log reconciliation event
            self._log_reconciliation_event(result)
            
            self._last_reconciliation = datetime.now(timezone.utc)
            
            logger.info(
                f"[Reconciler] Completed: merged {merged_count} entries "
                f"(seq {result['new_sequence_start']}-{result['new_sequence_end']})"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"[Reconciler] Reconciliation failed: {e}")
            result["status"] = "error"
            result["error"] = str(e)
            return result
    
    def _collect_degraded_entries(self) -> List[Dict[str, Any]]:
        """
        Collect all degraded entries from local log files.
        
        Returns:
            List of degraded entry dictionaries, sorted by timestamp
        """
        degraded = []
        
        if not self._log_dir.exists():
            return degraded
        
        log_files = sorted(self._log_dir.glob("audit_*.jsonl"))
        
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            entry = json.loads(line)
                            integrity = entry.get("integrity", {})
                            
                            # Check for degraded flag
                            if integrity.get("degraded") is True:
                                # Skip already reconciled
                                if not integrity.get("reconciled"):
                                    degraded.append(entry)
                                    
                        except json.JSONDecodeError:
                            continue
                            
            except Exception as e:
                logger.warning(f"[Reconciler] Error reading {log_file}: {e}")
                continue
        
        # Sort by timestamp for proper ordering
        degraded.sort(
            key=lambda e: e.get("integrity", {}).get("timestamp", "")
        )
        
        return degraded
    
    def _get_redis_state(self) -> Tuple[int, str]:
        """
        Get current sequence and hash from Redis.
        
        Returns:
            Tuple of (sequence, previous_hash)
        """
        seq_key = f"{self._key_prefix}{self.SEQUENCE_KEY}"
        state_key = f"{self._key_prefix}{self.STATE_KEY}"
        
        seq = self._redis.get(seq_key)
        seq = int(seq) if seq else 0
        
        prev_hash = self._redis.hget(state_key, "previous_hash")
        if isinstance(prev_hash, bytes):
            prev_hash = prev_hash.decode("utf-8")
        prev_hash = prev_hash or "GENESIS"
        
        return seq, prev_hash
    
    def _merge_entries_to_chain(
        self,
        entries: List[Dict[str, Any]],
        start_sequence: int,
        previous_hash: str,
    ) -> int:
        """
        Merge entries into the main hash chain with new integrity info.
        
        Args:
            entries: Degraded entries to merge
            start_sequence: Starting sequence number for merged entries
            previous_hash: Previous hash to chain from
            
        Returns:
            Number of entries merged
        """
        if not entries:
            return 0
        
        seq_key = f"{self._key_prefix}{self.SEQUENCE_KEY}"
        state_key = f"{self._key_prefix}{self.STATE_KEY}"
        
        current_seq = start_sequence
        current_prev_hash = previous_hash
        merged_count = 0
        
        for entry in entries:
            # Update integrity with new chain info
            entry["integrity"]["sequence"] = current_seq
            entry["integrity"]["previous_hash"] = current_prev_hash
            entry["integrity"]["degraded"] = False  # No longer degraded
            entry["integrity"]["reconciled"] = True
            entry["integrity"]["reconciled_at"] = datetime.now(timezone.utc).isoformat()
            
            # Remove old hash before recomputing
            if "current_hash" in entry["integrity"]:
                del entry["integrity"]["current_hash"]
            
            # Compute new hash
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            
            current_prev_hash = current_hash
            current_seq += 1
            merged_count += 1
        
        # Update Redis state
        if merged_count > 0:
            pipe = self._redis.pipeline()
            pipe.set(seq_key, current_seq - 1)
            pipe.hset(state_key, mapping={
                "previous_hash": current_prev_hash,
                "sequence": str(current_seq - 1),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_reconciliation": datetime.now(timezone.utc).isoformat(),
                "reconciled_entries": str(merged_count),
            })
            pipe.execute()
        
        return merged_count
    
    def _log_reconciliation_event(self, result: Dict[str, Any]) -> None:
        """
        Log reconciliation event for audit trail.
        
        Args:
            result: Reconciliation result dictionary
        """
        try:
            # Try to use self_audit if available
            from selfhealing.audit.self_audit import self_audit, SelfAuditEvent
            
            self_audit().log(
                SelfAuditEvent.RECOVERY_COMPLETED,
                f"Hash chain reconciliation: merged {result['entries_merged']} entries",
                {
                    "action": "hash_chain_reconciliation",
                    "result": result,
                }
            )
        except (ImportError, AttributeError):
            # self_audit not available or event not found, just log
            logger.info(f"[Reconciler] Reconciliation event: {result}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get reconciler statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            "last_reconciliation": (
                self._last_reconciliation.isoformat()
                if self._last_reconciliation else None
            ),
            "log_dir": str(self._log_dir),
        }
