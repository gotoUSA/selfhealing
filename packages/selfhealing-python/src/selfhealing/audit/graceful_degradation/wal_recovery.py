"""
WAL Recovery for Hash Chain.

Contains:
- HashChainWALEntry: WAL entry dataclass
- HashChainWALRecovery: WAL-based recovery for hash chain operations
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


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


__all__ = ["HashChainWALEntry", "HashChainWALRecovery"]
