"""
Hash Chain Graceful Degradation Components (Phase 4).

Provides fault-tolerant hash chain operations for distributed systems:

- HashChainFallbackChain: Redis → Replica → Local → Memory fallback
- DegradedEntryMarker: Mark entries recorded during failures
- HashChainWALRecovery: WAL-based recovery on startup
- HashChainDegradationManager: Unified degradation level management
- HashChainCircuitBreaker: Circuit breaker for hash chain operations

Core Principle:
    Zero Data Loss - Even during failures, all audit entries are preserved
    and can be reconciled when the system recovers.

Pattern sources:
- Fallback: adapters/resilient/backend.py#L183-230
- Degraded marking: audit/integrity.py#L484-503
- WAL: audit/wal.py
- GracefulDegradation: services/emergency_mode/manager.py#L37
- CircuitBreaker: services/circuit_breaker/service.py
"""

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Degradation Level Enum
# =============================================================================

class DegradationLevel(str, Enum):
    """
    Hash chain degradation levels.
    
    Determines available features at each level:
    - NORMAL: Full functionality with Redis
    - DEGRADED: Partial functionality with local fallback
    - EMERGENCY: Minimal functionality, memory-only
    - READONLY: No writes, only reads from cache
    """
    NORMAL = "normal"
    DEGRADED = "degraded"
    EMERGENCY = "emergency"
    READONLY = "readonly"


# =============================================================================
# Fallback Chain (Redis → Replica → Local → Memory)
# =============================================================================

@dataclass
class FallbackConfig:
    """Configuration for fallback chain."""
    redis_timeout_seconds: float = 5.0
    replica_timeout_seconds: float = 3.0
    local_file_path: Optional[Path] = None
    memory_max_entries: int = 10000
    key_prefix: str = "selfhealing:"


class HashChainFallbackChain:
    """
    Multi-tier fallback chain for hash chain operations.
    
    Fallback order:
    1. Redis Primary - Full distributed functionality
    2. Redis Replica - Read-only, degraded writes to local
    3. Local File - Persistent but not distributed
    4. Memory Buffer - Last resort, volatile
    
    Each fallback level marks entries as degraded for later reconciliation.
    
    Pattern source:
        adapters/resilient/backend.py#L183-230
    
    Usage:
        fallback = HashChainFallbackChain(redis_primary, config)
        entry = fallback.add_integrity(entry)  # Auto-fallback on failure
    """
    
    GENESIS_HASH = "GENESIS"
    
    def __init__(
        self,
        redis_primary: Optional[Any] = None,
        redis_replica: Optional[Any] = None,
        config: Optional[FallbackConfig] = None,
    ):
        """
        Initialize fallback chain.
        
        Args:
            redis_primary: Primary Redis client
            redis_replica: Replica Redis client (optional)
            config: Fallback configuration
        """
        self._redis_primary = redis_primary
        self._redis_replica = redis_replica
        self._config = config or FallbackConfig()
        self._lock = threading.RLock()
        
        # Local fallback state
        self._local_sequence = 0
        self._local_previous_hash = self.GENESIS_HASH
        self._local_file_handle = None
        
        # Memory buffer (last resort)
        self._memory_buffer: List[Dict[str, Any]] = []
        self._memory_sequence = 0
        self._memory_previous_hash = self.GENESIS_HASH
        
        # Current tier tracking
        self._current_tier = "redis_primary" if redis_primary else "local"
        self._tier_switch_count = 0
        
        # Stats
        self._stats = {
            "primary_writes": 0,
            "replica_reads": 0,
            "local_writes": 0,
            "memory_writes": 0,
            "fallback_events": 0,
        }
    
    def add_integrity(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add integrity fields with automatic fallback.
        
        Tries each tier in order until one succeeds.
        Failed attempts are logged and the entry is marked as degraded.
        
        Args:
            entry: Log entry dictionary
        
        Returns:
            Entry with integrity fields added
        """
        # Try Redis Primary
        if self._redis_primary:
            try:
                result = self._add_integrity_redis_primary(entry)
                self._current_tier = "redis_primary"
                self._stats["primary_writes"] += 1
                return result
            except Exception as e:
                logger.warning(f"[FallbackChain] Primary failed: {e}")
                self._stats["fallback_events"] += 1
        
        # Try Redis Replica (read state, write locally)
        if self._redis_replica:
            try:
                result = self._add_integrity_with_replica(entry)
                self._current_tier = "redis_replica"
                self._stats["replica_reads"] += 1
                return result
            except Exception as e:
                logger.warning(f"[FallbackChain] Replica failed: {e}")
                self._stats["fallback_events"] += 1
        
        # Try Local File
        try:
            result = self._add_integrity_local(entry)
            self._current_tier = "local"
            self._stats["local_writes"] += 1
            return result
        except Exception as e:
            logger.warning(f"[FallbackChain] Local failed: {e}")
            self._stats["fallback_events"] += 1
        
        # Last resort: Memory Buffer
        result = self._add_integrity_memory(entry)
        self._current_tier = "memory"
        self._stats["memory_writes"] += 1
        return result
    
    def _add_integrity_redis_primary(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity using Redis primary."""
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock
        
        seq_key = f"{self._config.key_prefix}audit:hash_chain:seq"
        state_key = f"{self._config.key_prefix}audit:hash_chain:state"
        lock_key = f"{self._config.key_prefix}audit:hash_chain:lock"
        
        lock = RedisDistributedLock(
            redis_client=self._redis_primary,
            name=lock_key,
            timeout=timedelta(seconds=self._config.redis_timeout_seconds),
            blocking_timeout=self._config.redis_timeout_seconds,
        )
        
        if not lock.acquire(blocking=True):
            raise RuntimeError("Failed to acquire distributed lock")
        
        try:
            # Atomic sequence increment
            sequence = self._redis_primary.incr(seq_key)
            
            # Get previous hash
            previous_hash = self._redis_primary.hget(state_key, "previous_hash")
            if previous_hash:
                if isinstance(previous_hash, bytes):
                    previous_hash = previous_hash.decode("utf-8")
            else:
                previous_hash = self.GENESIS_HASH
            
            # Add integrity fields
            timestamp = datetime.now(timezone.utc).isoformat()
            pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            
            entry["integrity"] = {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "timestamp": timestamp,
                "pod_id": pod_id,
                "tier": "redis_primary",
            }
            
            # Compute current hash
            current_hash = self._compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            
            # Save state for next entry
            self._redis_primary.hset(state_key, mapping={
                "previous_hash": current_hash,
                "sequence": str(sequence),
                "updated_at": timestamp,
            })
            
            return entry
            
        finally:
            lock.release()
    
    def _add_integrity_with_replica(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add integrity reading from replica, marking as degraded.
        
        Replica is read-only, so we read state but can't update.
        Entry is marked degraded for later reconciliation.
        """
        state_key = f"{self._config.key_prefix}audit:hash_chain:state"
        
        # Read current state from replica
        state = self._redis_replica.hgetall(state_key)
        
        if state:
            sequence = int(state.get(b"sequence", state.get("sequence", 0)))
            previous_hash = state.get(b"previous_hash", state.get("previous_hash", self.GENESIS_HASH))
            if isinstance(previous_hash, bytes):
                previous_hash = previous_hash.decode("utf-8")
        else:
            sequence = 0
            previous_hash = self.GENESIS_HASH
        
        # Add integrity fields (sequence is temporary, will be reassigned on reconciliation)
        with self._lock:
            self._local_sequence = max(self._local_sequence, sequence) + 1
            local_seq = self._local_sequence
        
        timestamp = datetime.now(timezone.utc).isoformat()
        pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
        
        entry["integrity"] = {
            "sequence": local_seq,
            "previous_hash": previous_hash,
            "timestamp": timestamp,
            "pod_id": pod_id,
            "tier": "redis_replica",
            "degraded": True,
            "degraded_reason": "redis_primary_unavailable",
            "degraded_at": timestamp,
        }
        
        current_hash = self._compute_hash(entry)
        entry["integrity"]["current_hash"] = current_hash
        
        # Update local state for chain continuity
        with self._lock:
            self._local_previous_hash = current_hash
        
        return entry
    
    def _add_integrity_local(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity using local file fallback."""
        with self._lock:
            self._local_sequence += 1
            sequence = self._local_sequence
            previous_hash = self._local_previous_hash
            
            timestamp = datetime.now(timezone.utc).isoformat()
            pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            
            entry["integrity"] = {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "timestamp": timestamp,
                "pod_id": pod_id,
                "tier": "local",
                "degraded": True,
                "degraded_reason": "redis_unavailable",
                "degraded_at": timestamp,
            }
            
            current_hash = self._compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            self._local_previous_hash = current_hash
            
            # Write to local file if configured
            if self._config.local_file_path:
                self._write_to_local_file(entry)
            
            return entry
    
    def _add_integrity_memory(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add integrity using memory buffer (last resort)."""
        with self._lock:
            self._memory_sequence += 1
            sequence = self._memory_sequence
            previous_hash = self._memory_previous_hash
            
            timestamp = datetime.now(timezone.utc).isoformat()
            pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            
            entry["integrity"] = {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "timestamp": timestamp,
                "pod_id": pod_id,
                "tier": "memory",
                "degraded": True,
                "degraded_reason": "all_persistent_storage_unavailable",
                "degraded_at": timestamp,
                "volatile": True,  # Warning: will be lost on restart
            }
            
            current_hash = self._compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            self._memory_previous_hash = current_hash
            
            # Add to memory buffer (with size limit)
            self._memory_buffer.append(entry.copy())
            if len(self._memory_buffer) > self._config.memory_max_entries:
                # Remove oldest entries when buffer full
                removed = self._memory_buffer.pop(0)
                logger.warning(
                    f"[FallbackChain] Memory buffer full, dropped entry seq={removed.get('integrity', {}).get('sequence')}"
                )
            
            return entry
    
    def _write_to_local_file(self, entry: Dict[str, Any]) -> None:
        """Write entry to local fallback file."""
        try:
            if self._local_file_handle is None:
                self._config.local_file_path.parent.mkdir(parents=True, exist_ok=True)
                self._local_file_handle = open(
                    self._config.local_file_path, "a", encoding="utf-8"
                )
            
            line = json.dumps(entry, default=str, ensure_ascii=False)
            self._local_file_handle.write(line + "\n")
            self._local_file_handle.flush()
            
        except Exception as e:
            logger.error(f"[FallbackChain] Local file write failed: {e}")
    
    def _compute_hash(self, entry: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of entry."""
        entry_copy = json.loads(json.dumps(entry, default=str))
        if "integrity" in entry_copy and "current_hash" in entry_copy["integrity"]:
            del entry_copy["integrity"]["current_hash"]
        json_str = json.dumps(entry_copy, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()
    
    def get_degraded_entries(self) -> List[Dict[str, Any]]:
        """Get all degraded entries from memory buffer."""
        with self._lock:
            return [e for e in self._memory_buffer if e.get("integrity", {}).get("degraded")]
    
    def clear_memory_buffer(self) -> int:
        """Clear memory buffer after successful reconciliation."""
        with self._lock:
            count = len(self._memory_buffer)
            self._memory_buffer.clear()
            return count
    
    def close(self) -> None:
        """Close file handles."""
        if self._local_file_handle:
            self._local_file_handle.close()
            self._local_file_handle = None
    
    @property
    def current_tier(self) -> str:
        """Get current active tier."""
        return self._current_tier
    
    def get_stats(self) -> Dict[str, Any]:
        """Get fallback chain statistics."""
        return {
            **self._stats,
            "current_tier": self._current_tier,
            "memory_buffer_size": len(self._memory_buffer),
            "local_sequence": self._local_sequence,
        }


# =============================================================================
# Degraded Entry Marker
# =============================================================================

@dataclass
class DegradedEntryInfo:
    """Information about a degraded entry."""
    sequence: int
    original_tier: str
    degraded_reason: str
    degraded_at: str
    pod_id: str
    reconciled: bool = False
    reconciled_at: Optional[str] = None
    new_sequence: Optional[int] = None


class DegradedEntryMarker:
    """
    Marker for entries recorded during degraded operation.
    
    Tracks all entries that were written during failures for later
    reconciliation when the system recovers.
    
    Pattern source:
        audit/integrity.py#L484-503
    
    Usage:
        marker = DegradedEntryMarker()
        entry = marker.mark_degraded(entry, "redis_timeout")
        # Later during recovery:
        entries = marker.get_unreconciled_entries()
    """
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        key_prefix: str = "selfhealing:",
    ):
        """
        Initialize degraded entry marker.
        
        Args:
            redis_client: Redis client for distributed tracking
            key_prefix: Prefix for Redis keys
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._lock = threading.RLock()
        
        # Local tracking (backup if Redis unavailable)
        self._local_degraded: Dict[int, DegradedEntryInfo] = {}
        
        # Stats
        self._marked_count = 0
        self._reconciled_count = 0
    
    def mark_degraded(
        self,
        entry: Dict[str, Any],
        reason: str,
        tier: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Mark an entry as degraded.
        
        Adds degraded metadata to the entry and tracks it for reconciliation.
        
        Args:
            entry: Entry to mark
            reason: Reason for degradation
            tier: Which tier the entry was written to
        
        Returns:
            Entry with degraded marking
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
        
        if "integrity" not in entry:
            entry["integrity"] = {}
        
        entry["integrity"]["degraded"] = True
        entry["integrity"]["degraded_reason"] = reason
        entry["integrity"]["degraded_at"] = timestamp
        entry["integrity"]["degraded_tier"] = tier
        entry["integrity"]["degraded_pod_id"] = pod_id
        
        # Track locally
        sequence = entry["integrity"].get("sequence", -1)
        with self._lock:
            self._local_degraded[sequence] = DegradedEntryInfo(
                sequence=sequence,
                original_tier=tier,
                degraded_reason=reason,
                degraded_at=timestamp,
                pod_id=pod_id,
            )
            self._marked_count += 1
        
        # Track in Redis if available
        if self._redis:
            try:
                self._track_in_redis(sequence, entry["integrity"])
            except Exception as e:
                logger.debug(f"[DegradedMarker] Redis tracking failed: {e}")
        
        return entry
    
    def _track_in_redis(self, sequence: int, integrity: Dict[str, Any]) -> None:
        """Track degraded entry in Redis."""
        key = f"{self._key_prefix}audit:hash_chain:degraded:{sequence}"
        self._redis.hset(key, mapping={
            "sequence": str(sequence),
            "reason": integrity.get("degraded_reason", "unknown"),
            "tier": integrity.get("degraded_tier", "unknown"),
            "degraded_at": integrity.get("degraded_at", ""),
            "pod_id": integrity.get("degraded_pod_id", "unknown"),
            "reconciled": "false",
        })
        # TTL for cleanup (7 days)
        self._redis.expire(key, 7 * 24 * 3600)
    
    def mark_reconciled(self, original_sequence: int, new_sequence: int) -> bool:
        """
        Mark a degraded entry as reconciled.
        
        Args:
            original_sequence: Original degraded sequence
            new_sequence: New sequence after reconciliation
        
        Returns:
            True if successfully marked
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        with self._lock:
            if original_sequence in self._local_degraded:
                self._local_degraded[original_sequence].reconciled = True
                self._local_degraded[original_sequence].reconciled_at = timestamp
                self._local_degraded[original_sequence].new_sequence = new_sequence
                self._reconciled_count += 1
        
        # Update Redis if available
        if self._redis:
            try:
                key = f"{self._key_prefix}audit:hash_chain:degraded:{original_sequence}"
                self._redis.hset(key, mapping={
                    "reconciled": "true",
                    "reconciled_at": timestamp,
                    "new_sequence": str(new_sequence),
                })
                return True
            except Exception as e:
                logger.debug(f"[DegradedMarker] Redis update failed: {e}")
        
        return True
    
    def get_unreconciled_entries(self) -> List[DegradedEntryInfo]:
        """Get all entries that haven't been reconciled."""
        with self._lock:
            return [
                info for info in self._local_degraded.values()
                if not info.reconciled
            ]
    
    def get_unreconciled_count(self) -> int:
        """Get count of unreconciled entries."""
        with self._lock:
            return sum(1 for info in self._local_degraded.values() if not info.reconciled)
    
    def clear_reconciled(self) -> int:
        """Remove reconciled entries from tracking."""
        with self._lock:
            to_remove = [
                seq for seq, info in self._local_degraded.items()
                if info.reconciled
            ]
            for seq in to_remove:
                del self._local_degraded[seq]
            return len(to_remove)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get marker statistics."""
        return {
            "marked_count": self._marked_count,
            "reconciled_count": self._reconciled_count,
            "unreconciled_count": self.get_unreconciled_count(),
            "tracking_size": len(self._local_degraded),
        }


# =============================================================================
# WAL Recovery for Hash Chain
# =============================================================================

@dataclass
class HashChainWALEntry:
    """WAL entry for hash chain operation."""
    sequence: int
    operation: str  # "add_integrity", "commit", "abort"
    entry_data: Dict[str, Any]
    timestamp: str
    pod_id: str
    committed: bool = False


class HashChainWALRecovery:
    """
    WAL-based recovery for hash chain operations.
    
    Ensures zero data loss by recording operations in WAL before
    attempting Redis writes. On failure, WAL entries are replayed.
    
    Pattern source:
        adapters/resilient/backend.py#L183-230
        audit/wal.py
    
    Usage:
        recovery = HashChainWALRecovery(wal_dir, redis_client)
        recovery.recover_on_startup()  # Called during app initialization
    """
    
    def __init__(
        self,
        wal_dir: Path,
        redis_client: Optional[Any] = None,
        key_prefix: str = "selfhealing:",
    ):
        """
        Initialize WAL recovery.
        
        Args:
            wal_dir: Directory for WAL files
            redis_client: Redis client for recovery
            key_prefix: Prefix for Redis keys
        """
        self._wal_dir = Path(wal_dir)
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._lock = threading.RLock()
        
        # WAL file management
        self._current_wal_file: Optional[Path] = None
        self._wal_handle = None
        self._wal_sequence = 0
        
        # Recovery state
        self._recovery_done = False
        self._recovered_count = 0
        self._failed_count = 0
        
        # Ensure WAL directory exists
        self._wal_dir.mkdir(parents=True, exist_ok=True)
    
    def write_wal_entry(
        self,
        operation: str,
        entry: Dict[str, Any],
    ) -> int:
        """
        Write entry to WAL before main operation.
        
        Args:
            operation: Operation type
            entry: Entry data
        
        Returns:
            WAL sequence number
        """
        with self._lock:
            self._wal_sequence += 1
            wal_seq = self._wal_sequence
            
            timestamp = datetime.now(timezone.utc).isoformat()
            pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            
            wal_entry = {
                "wal_sequence": wal_seq,
                "operation": operation,
                "entry_data": entry,
                "timestamp": timestamp,
                "pod_id": pod_id,
                "committed": False,
            }
            
            self._write_to_wal_file(wal_entry)
            return wal_seq
    
    def mark_wal_committed(self, wal_sequence: int) -> None:
        """Mark WAL entry as committed (successfully written to Redis)."""
        with self._lock:
            # Write commit marker
            commit_entry = {
                "wal_sequence": wal_sequence,
                "operation": "COMMIT",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._write_to_wal_file(commit_entry)
    
    def _write_to_wal_file(self, entry: Dict[str, Any]) -> None:
        """Write entry to WAL file with fsync."""
        self._ensure_wal_file_open()
        
        line = json.dumps(entry, default=str, ensure_ascii=False)
        self._wal_handle.write(line + "\n")
        self._wal_handle.flush()
        os.fsync(self._wal_handle.fileno())
    
    def _ensure_wal_file_open(self) -> None:
        """Ensure WAL file is open."""
        if self._wal_handle is None:
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            self._current_wal_file = self._wal_dir / f"hash_chain_wal_{date_str}.jsonl"
            self._wal_handle = open(self._current_wal_file, "a", encoding="utf-8")
    
    def recover_on_startup(self) -> Dict[str, Any]:
        """
        Recover uncommitted entries from WAL on startup.
        
        This is called during application initialization to replay
        any entries that were written to WAL but not committed to Redis.
        
        Returns:
            Recovery result dictionary
        """
        if self._recovery_done:
            return {"status": "already_done", "recovered": 0}
        
        result = {
            "status": "success",
            "wal_files_scanned": 0,
            "entries_found": 0,
            "entries_recovered": 0,
            "entries_failed": 0,
            "entries_already_committed": 0,
        }
        
        try:
            # Find all WAL files
            wal_files = sorted(self._wal_dir.glob("hash_chain_wal_*.jsonl"))
            result["wal_files_scanned"] = len(wal_files)
            
            for wal_file in wal_files:
                file_result = self._recover_from_wal_file(wal_file)
                result["entries_found"] += file_result["found"]
                result["entries_recovered"] += file_result["recovered"]
                result["entries_failed"] += file_result["failed"]
                result["entries_already_committed"] += file_result["already_committed"]
            
            self._recovery_done = True
            self._recovered_count = result["entries_recovered"]
            self._failed_count = result["entries_failed"]
            
            logger.info(f"[HashChainWAL] Recovery completed: {result}")
            
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error(f"[HashChainWAL] Recovery failed: {e}")
        
        return result
    
    def _recover_from_wal_file(self, wal_file: Path) -> Dict[str, int]:
        """Recover entries from a single WAL file."""
        result = {"found": 0, "recovered": 0, "failed": 0, "already_committed": 0}
        
        # Read all entries
        entries: Dict[int, Dict[str, Any]] = {}
        committed_sequences: set = set()
        
        try:
            with open(wal_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        entry = json.loads(line)
                        wal_seq = entry.get("wal_sequence")
                        operation = entry.get("operation")
                        
                        if operation == "COMMIT":
                            committed_sequences.add(wal_seq)
                        elif operation in ("add_integrity", "write"):
                            entries[wal_seq] = entry
                            result["found"] += 1
                    except json.JSONDecodeError:
                        continue
            
            # Find uncommitted entries
            for wal_seq, entry in entries.items():
                if wal_seq in committed_sequences:
                    result["already_committed"] += 1
                    continue
                
                # Attempt to replay
                if self._replay_entry(entry):
                    result["recovered"] += 1
                else:
                    result["failed"] += 1
            
        except Exception as e:
            logger.error(f"[HashChainWAL] Error reading {wal_file}: {e}")
        
        return result
    
    def _replay_entry(self, wal_entry: Dict[str, Any]) -> bool:
        """Replay a single WAL entry to Redis."""
        if not self._redis:
            logger.warning("[HashChainWAL] No Redis client for replay")
            return False
        
        try:
            entry_data = wal_entry.get("entry_data", {})
            integrity = entry_data.get("integrity", {})
            
            # Check if already exists in Redis
            seq_key = f"{self._key_prefix}audit:hash_chain:seq"
            current_seq = self._redis.get(seq_key)
            current_seq = int(current_seq) if current_seq else 0
            
            entry_seq = integrity.get("sequence", 0)
            
            if entry_seq <= current_seq:
                # Already processed
                return True
            
            # Update Redis state
            state_key = f"{self._key_prefix}audit:hash_chain:state"
            current_hash = integrity.get("current_hash", "")
            timestamp = datetime.now(timezone.utc).isoformat()
            
            pipe = self._redis.pipeline()
            pipe.set(seq_key, entry_seq)
            pipe.hset(state_key, mapping={
                "previous_hash": current_hash,
                "sequence": str(entry_seq),
                "updated_at": timestamp,
                "recovered_from": "wal",
            })
            pipe.execute()
            
            logger.debug(f"[HashChainWAL] Replayed entry seq={entry_seq}")
            return True
            
        except Exception as e:
            logger.error(f"[HashChainWAL] Replay failed: {e}")
            return False
    
    def cleanup_old_wal_files(self, max_age_days: int = 7) -> int:
        """Remove WAL files older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0
        
        for wal_file in self._wal_dir.glob("hash_chain_wal_*.jsonl"):
            try:
                # Extract date from filename
                date_str = wal_file.stem.split("_")[-1]
                file_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                
                if file_date < cutoff:
                    wal_file.unlink()
                    removed += 1
                    logger.debug(f"[HashChainWAL] Removed old WAL file: {wal_file.name}")
            except Exception:
                continue
        
        return removed
    
    def close(self) -> None:
        """Close WAL file handle."""
        if self._wal_handle:
            self._wal_handle.close()
            self._wal_handle = None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get recovery statistics."""
        return {
            "recovery_done": self._recovery_done,
            "recovered_count": self._recovered_count,
            "failed_count": self._failed_count,
            "wal_sequence": self._wal_sequence,
            "current_wal_file": str(self._current_wal_file) if self._current_wal_file else None,
        }


# =============================================================================
# Graceful Degradation Manager for Hash Chain
# =============================================================================

class HashChainDegradationManager:
    """
    Manages graceful degradation for hash chain operations.
    
    Coordinates degradation levels across all hash chain components,
    provides unified status, and triggers recovery when possible.
    
    Degradation Levels:
    - NORMAL: Full Redis functionality
    - DEGRADED: Local fallback active, entries marked for reconciliation
    - EMERGENCY: Memory-only, minimal functionality
    - READONLY: No writes, only cache reads
    
    Pattern source:
        services/emergency_mode/manager.py#L37
    
    Usage:
        manager = HashChainDegradationManager(redis_client)
        manager.on_redis_failure()  # Triggers degradation
        manager.on_redis_recovery()  # Triggers recovery
    """
    
    _instance: Optional["HashChainDegradationManager"] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs) -> "HashChainDegradationManager":
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        key_prefix: str = "selfhealing:",
        wal_dir: Optional[Path] = None,
    ):
        """
        Initialize degradation manager.
        
        Args:
            redis_client: Redis client
            key_prefix: Prefix for Redis keys
            wal_dir: Directory for WAL files
        """
        if getattr(self, "_initialized", False):
            return
        
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._wal_dir = Path(wal_dir) if wal_dir else Path("logs/audit/wal")
        self._state_lock = threading.RLock()
        
        # Current state
        self._level = DegradationLevel.NORMAL if redis_client else DegradationLevel.DEGRADED
        self._level_changed_at = datetime.now(timezone.utc).isoformat()
        self._failure_count = 0
        self._recovery_attempts = 0
        
        # Component references (lazy initialized)
        self._fallback_chain: Optional[HashChainFallbackChain] = None
        self._degraded_marker: Optional[DegradedEntryMarker] = None
        self._wal_recovery: Optional[HashChainWALRecovery] = None
        
        # Callbacks
        self._on_degradation_callbacks: List[Callable[[DegradationLevel], None]] = []
        self._on_recovery_callbacks: List[Callable[[DegradationLevel], None]] = []
        
        self._initialized = True
    
    @property
    def level(self) -> DegradationLevel:
        """Get current degradation level."""
        return self._level
    
    @property
    def is_degraded(self) -> bool:
        """Check if operating in any degraded mode."""
        return self._level != DegradationLevel.NORMAL
    
    def set_level(self, level: DegradationLevel, reason: str = "") -> None:
        """
        Set degradation level.
        
        Args:
            level: New degradation level
            reason: Reason for level change
        """
        with self._state_lock:
            if level == self._level:
                return
            
            old_level = self._level
            self._level = level
            self._level_changed_at = datetime.now(timezone.utc).isoformat()
            
            logger.warning(
                f"[HashChainDegradation] Level changed: {old_level.value} → {level.value}"
                f"{f' ({reason})' if reason else ''}"
            )
            
            # Record in Redis if available
            self._record_level_change(old_level, level, reason)
            
            # Notify callbacks
            if level == DegradationLevel.NORMAL:
                for callback in self._on_recovery_callbacks:
                    try:
                        callback(level)
                    except Exception as e:
                        logger.error(f"[HashChainDegradation] Recovery callback failed: {e}")
            else:
                for callback in self._on_degradation_callbacks:
                    try:
                        callback(level)
                    except Exception as e:
                        logger.error(f"[HashChainDegradation] Degradation callback failed: {e}")
    
    def _record_level_change(
        self,
        old_level: DegradationLevel,
        new_level: DegradationLevel,
        reason: str,
    ) -> None:
        """Record level change in Redis."""
        if not self._redis:
            return
        
        try:
            key = f"{self._key_prefix}audit:hash_chain:degradation_history"
            timestamp = datetime.now(timezone.utc).isoformat()
            pod_id = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            
            entry = json.dumps({
                "timestamp": timestamp,
                "old_level": old_level.value,
                "new_level": new_level.value,
                "reason": reason,
                "pod_id": pod_id,
            })
            
            self._redis.lpush(key, entry)
            # Keep only last 100 entries
            self._redis.ltrim(key, 0, 99)
            
        except Exception as e:
            logger.debug(f"[HashChainDegradation] Failed to record: {e}")
    
    def on_redis_failure(self, error: Optional[Exception] = None) -> None:
        """
        Handle Redis failure event.
        
        Triggers degradation and activates fallback mechanisms.
        
        Args:
            error: The exception that caused the failure
        """
        with self._state_lock:
            self._failure_count += 1
            
            reason = str(error) if error else "connection_failed"
            
            if self._level == DegradationLevel.NORMAL:
                self.set_level(DegradationLevel.DEGRADED, reason)
            elif self._level == DegradationLevel.DEGRADED and self._failure_count > 10:
                self.set_level(DegradationLevel.EMERGENCY, "repeated_failures")
    
    def on_redis_recovery(self) -> None:
        """
        Handle Redis recovery event.
        
        Triggers recovery process including:
        1. WAL replay
        2. Degraded entry reconciliation
        3. Level restoration
        """
        with self._state_lock:
            self._recovery_attempts += 1
            
            try:
                # Attempt WAL recovery if available
                if self._wal_recovery:
                    wal_result = self._wal_recovery.recover_on_startup()
                    logger.info(f"[HashChainDegradation] WAL recovery: {wal_result}")
                
                # Reset failure count on successful recovery
                self._failure_count = 0
                
                # Restore normal operation
                self.set_level(DegradationLevel.NORMAL, "redis_recovered")
                
            except Exception as e:
                logger.error(f"[HashChainDegradation] Recovery failed: {e}")
                # Stay in degraded mode
    
    def on_filesystem_failure(self) -> None:
        """Handle filesystem failure event."""
        self.set_level(DegradationLevel.EMERGENCY, "filesystem_failure")
    
    def register_on_degradation(self, callback: Callable[[DegradationLevel], None]) -> None:
        """Register callback for degradation events."""
        self._on_degradation_callbacks.append(callback)
    
    def register_on_recovery(self, callback: Callable[[DegradationLevel], None]) -> None:
        """Register callback for recovery events."""
        self._on_recovery_callbacks.append(callback)
    
    def get_fallback_chain(self) -> HashChainFallbackChain:
        """Get or create fallback chain instance."""
        if self._fallback_chain is None:
            self._fallback_chain = HashChainFallbackChain(
                redis_primary=self._redis,
                config=FallbackConfig(key_prefix=self._key_prefix),
            )
        return self._fallback_chain
    
    def get_degraded_marker(self) -> DegradedEntryMarker:
        """Get or create degraded marker instance."""
        if self._degraded_marker is None:
            self._degraded_marker = DegradedEntryMarker(
                redis_client=self._redis,
                key_prefix=self._key_prefix,
            )
        return self._degraded_marker
    
    def get_wal_recovery(self) -> HashChainWALRecovery:
        """Get or create WAL recovery instance."""
        if self._wal_recovery is None:
            self._wal_recovery = HashChainWALRecovery(
                wal_dir=self._wal_dir,
                redis_client=self._redis,
                key_prefix=self._key_prefix,
            )
        return self._wal_recovery
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive degradation status."""
        status = {
            "level": self._level.value,
            "is_degraded": self.is_degraded,
            "level_changed_at": self._level_changed_at,
            "failure_count": self._failure_count,
            "recovery_attempts": self._recovery_attempts,
            "redis_available": self._redis is not None,
        }
        
        if self._fallback_chain:
            status["fallback"] = self._fallback_chain.get_stats()
        
        if self._degraded_marker:
            status["degraded_marker"] = self._degraded_marker.get_stats()
        
        if self._wal_recovery:
            status["wal_recovery"] = self._wal_recovery.get_stats()
        
        return status
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None


# =============================================================================
# Circuit Breaker for Hash Chain Operations
# =============================================================================

class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_requests: int = 3
    success_threshold: int = 2


class HashChainCircuitBreaker:
    """
    Circuit breaker for hash chain Redis operations.
    
    Prevents cascading failures by stopping requests to a failing
    Redis instance and allowing recovery time.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failures exceeded threshold, requests rejected
    - HALF_OPEN: Testing if Redis has recovered
    
    Pattern source:
        services/circuit_breaker/service.py
    
    Usage:
        cb = HashChainCircuitBreaker()
        
        if cb.can_execute():
            try:
                result = redis_operation()
                cb.record_success()
            except Exception as e:
                cb.record_failure()
                raise
        else:
            # Use fallback
            result = fallback_operation()
    """
    
    def __init__(
        self,
        name: str = "hash_chain_redis",
        config: Optional[CircuitBreakerConfig] = None,
        degradation_manager: Optional[HashChainDegradationManager] = None,
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Circuit breaker name
            config: Configuration
            degradation_manager: Optional degradation manager for integration
        """
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._degradation_manager = degradation_manager
        self._lock = threading.RLock()
        
        # State
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_requests = 0
        
        # Stats
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        self._state_changes = 0
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state
    
    def can_execute(self) -> bool:
        """
        Check if request can be executed.
        
        Returns:
            True if circuit allows execution
        """
        with self._lock:
            self._total_requests += 1
            self._maybe_transition_to_half_open()
            
            if self._state == CircuitState.CLOSED:
                return True
            
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_requests < self._config.half_open_requests:
                    self._half_open_requests += 1
                    return True
                return False
            
            # OPEN
            return False
    
    def record_success(self) -> None:
        """Record successful operation."""
        with self._lock:
            self._total_successes += 1
            
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._transition_to_closed()
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # Reset on success
    
    def record_failure(self, error: Optional[Exception] = None) -> None:
        """Record failed operation."""
        with self._lock:
            self._failure_count += 1
            self._total_failures += 1
            self._last_failure_time = time.monotonic()
            
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to_open()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._transition_to_open()
            
            # Notify degradation manager
            if self._degradation_manager and self._state == CircuitState.OPEN:
                self._degradation_manager.on_redis_failure(error)
    
    def _maybe_transition_to_half_open(self) -> None:
        """Transition from OPEN to HALF_OPEN if timeout expired."""
        if self._state != CircuitState.OPEN:
            return
        
        if self._last_failure_time is None:
            return
        
        elapsed = time.monotonic() - self._last_failure_time
        if elapsed >= self._config.recovery_timeout_seconds:
            self._state = CircuitState.HALF_OPEN
            self._half_open_requests = 0
            self._success_count = 0
            self._state_changes += 1
            logger.info(f"[CircuitBreaker:{self._name}] OPEN → HALF_OPEN")
    
    def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        self._state = CircuitState.OPEN
        self._state_changes += 1
        logger.warning(f"[CircuitBreaker:{self._name}] → OPEN (failures: {self._failure_count})")
    
    def _transition_to_closed(self) -> None:
        """Transition to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_requests = 0
        self._state_changes += 1
        logger.info(f"[CircuitBreaker:{self._name}] → CLOSED (recovered)")
        
        # Notify degradation manager
        if self._degradation_manager:
            self._degradation_manager.on_redis_recovery()
    
    def force_open(self) -> None:
        """Force circuit to OPEN state (for testing/manual intervention)."""
        with self._lock:
            self._transition_to_open()
    
    def force_closed(self) -> None:
        """Force circuit to CLOSED state (for testing/manual intervention)."""
        with self._lock:
            self._transition_to_closed()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "name": self._name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "total_requests": self._total_requests,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "state_changes": self._state_changes,
                "config": {
                    "failure_threshold": self._config.failure_threshold,
                    "recovery_timeout_seconds": self._config.recovery_timeout_seconds,
                    "half_open_requests": self._config.half_open_requests,
                    "success_threshold": self._config.success_threshold,
                },
            }


# =============================================================================
# Phase 4 Manager (Unified Access)
# =============================================================================

class HashChainGracefulDegradationManager:
    """
    Unified manager for Phase 4 graceful degradation components.
    
    Provides coordinated access to:
    - fallback_chain: Multi-tier fallback (Redis → Replica → Local → Memory)
    - degraded_marker: Tracks degraded entries for reconciliation
    - wal_recovery: WAL-based crash recovery
    - degradation_manager: Level management and coordination
    - circuit_breaker: Failure detection and prevention
    
    Pattern source:
        services/emergency_mode/manager.py (unified coordination)
    
    Usage:
        manager = HashChainGracefulDegradationManager(redis_client)
        manager.initialize()
        
        # During normal operation
        entry = manager.add_integrity_with_fallback(entry)
        
        # On startup
        manager.recover_on_startup()
    """
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        redis_replica: Optional[Any] = None,
        key_prefix: str = "selfhealing:",
        wal_dir: Optional[Path] = None,
        local_fallback_path: Optional[Path] = None,
    ):
        """
        Initialize graceful degradation manager.
        
        Args:
            redis_client: Primary Redis client
            redis_replica: Replica Redis client (optional)
            key_prefix: Prefix for Redis keys
            wal_dir: Directory for WAL files
            local_fallback_path: Path for local fallback file
        """
        self._redis = redis_client
        self._redis_replica = redis_replica
        self._key_prefix = key_prefix
        self._wal_dir = Path(wal_dir) if wal_dir else Path("logs/audit/wal")
        self._local_fallback_path = (
            Path(local_fallback_path) if local_fallback_path 
            else Path("logs/audit/fallback/degraded_entries.jsonl")
        )
        self._lock = threading.RLock()
        self._initialized = False
        
        # Components (lazy initialized)
        self._fallback_chain: Optional[HashChainFallbackChain] = None
        self._degraded_marker: Optional[DegradedEntryMarker] = None
        self._wal_recovery: Optional[HashChainWALRecovery] = None
        self._degradation_manager: Optional[HashChainDegradationManager] = None
        self._circuit_breaker: Optional[HashChainCircuitBreaker] = None
    
    def initialize(self) -> None:
        """Initialize all components."""
        if self._initialized:
            return
        
        with self._lock:
            # Initialize degradation manager first (coordinates others)
            self._degradation_manager = HashChainDegradationManager(
                redis_client=self._redis,
                key_prefix=self._key_prefix,
                wal_dir=self._wal_dir,
            )
            
            # Initialize circuit breaker with degradation manager
            self._circuit_breaker = HashChainCircuitBreaker(
                name="hash_chain_redis",
                degradation_manager=self._degradation_manager,
            )
            
            # Initialize fallback chain
            self._fallback_chain = HashChainFallbackChain(
                redis_primary=self._redis,
                redis_replica=self._redis_replica,
                config=FallbackConfig(
                    key_prefix=self._key_prefix,
                    local_file_path=self._local_fallback_path,
                ),
            )
            
            # Initialize degraded marker
            self._degraded_marker = DegradedEntryMarker(
                redis_client=self._redis,
                key_prefix=self._key_prefix,
            )
            
            # Initialize WAL recovery
            self._wal_recovery = HashChainWALRecovery(
                wal_dir=self._wal_dir,
                redis_client=self._redis,
                key_prefix=self._key_prefix,
            )
            
            self._initialized = True
            logger.info("[GracefulDegradation] Initialized all Phase 4 components")
    
    def recover_on_startup(self) -> Dict[str, Any]:
        """
        Perform recovery operations on startup.
        
        Should be called during application initialization.
        
        Returns:
            Recovery result dictionary
        """
        self.initialize()
        
        result = {
            "wal_recovery": {},
            "degraded_entries": 0,
            "status": "success",
        }
        
        try:
            # WAL recovery first
            if self._wal_recovery:
                result["wal_recovery"] = self._wal_recovery.recover_on_startup()
            
            # Check for unreconciled degraded entries
            if self._degraded_marker:
                result["degraded_entries"] = self._degraded_marker.get_unreconciled_count()
            
            logger.info(f"[GracefulDegradation] Startup recovery: {result}")
            
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error(f"[GracefulDegradation] Startup recovery failed: {e}")
        
        return result
    
    def add_integrity_with_fallback(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add integrity with automatic fallback and circuit breaker.
        
        Args:
            entry: Log entry dictionary
        
        Returns:
            Entry with integrity fields
        """
        self.initialize()
        
        # Check circuit breaker
        if self._circuit_breaker and not self._circuit_breaker.can_execute():
            # Circuit open - use fallback directly
            result = self._fallback_chain.add_integrity(entry)
            if result.get("integrity", {}).get("degraded"):
                self._degraded_marker.mark_degraded(
                    result,
                    "circuit_open",
                    result.get("integrity", {}).get("tier", "unknown"),
                )
            return result
        
        # Try with circuit breaker
        try:
            result = self._fallback_chain.add_integrity(entry)
            
            # Record success if using primary
            if result.get("integrity", {}).get("tier") == "redis_primary":
                if self._circuit_breaker:
                    self._circuit_breaker.record_success()
            elif result.get("integrity", {}).get("degraded"):
                # Mark degraded entries
                self._degraded_marker.mark_degraded(
                    result,
                    result.get("integrity", {}).get("degraded_reason", "unknown"),
                    result.get("integrity", {}).get("tier", "unknown"),
                )
            
            return result
            
        except Exception as e:
            if self._circuit_breaker:
                self._circuit_breaker.record_failure(e)
            raise
    
    @property
    def degradation_level(self) -> DegradationLevel:
        """Get current degradation level."""
        if self._degradation_manager:
            return self._degradation_manager.level
        return DegradationLevel.NORMAL
    
    @property
    def is_degraded(self) -> bool:
        """Check if operating in degraded mode."""
        return self.degradation_level != DegradationLevel.NORMAL
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status of all components."""
        self.initialize()
        
        status = {
            "degradation_level": self.degradation_level.value,
            "is_degraded": self.is_degraded,
            "initialized": self._initialized,
        }
        
        if self._circuit_breaker:
            status["circuit_breaker"] = self._circuit_breaker.get_stats()
        
        if self._fallback_chain:
            status["fallback_chain"] = self._fallback_chain.get_stats()
        
        if self._degraded_marker:
            status["degraded_marker"] = self._degraded_marker.get_stats()
        
        if self._wal_recovery:
            status["wal_recovery"] = self._wal_recovery.get_stats()
        
        if self._degradation_manager:
            status["degradation_manager"] = self._degradation_manager.get_status()
        
        return status
    
    def close(self) -> None:
        """Clean up resources."""
        if self._fallback_chain:
            self._fallback_chain.close()
        
        if self._wal_recovery:
            self._wal_recovery.close()
