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
