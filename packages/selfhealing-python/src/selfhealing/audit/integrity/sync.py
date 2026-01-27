"""
Startup Hash Chain Synchronization.

Contains:
- StartupHashChainSync: Synchronizes hash chain state between Redis and local files at startup
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StartupHashChainSync:
    """
    Synchronizes hash chain state between Redis and local files at startup.

    Why Needed:
        After a restart, Redis and local file state may diverge:
        - Redis restarted and lost data -> File is ahead
        - Process crashed mid-write -> Redis is ahead (pending writes)
        - Both empty -> Fresh start

    Sync Logic:
        1. Read last sequence/hash from local log files
        2. Read current sequence/hash from Redis
        3. Compare and sync:
           - Redis < File: Update Redis to match file (data recovery)
           - Redis > File: Normal (some writes pending, no action needed)
           - Equal: In sync, no action needed
        4. Clean up stale PENDING sequences from previous crashes

    Idempotent:
        sync() is safe to call multiple times. After first sync,
        subsequent calls return immediately without re-syncing.
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

    def sync(self) -> dict[str, Any]:
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

    def _get_last_file_state(self) -> tuple[int, str]:
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

    def _get_redis_state(self) -> tuple[int, str]:
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
            pipe.hset(
                state_key,
                mapping={
                    "previous_hash": file_hash,
                    "sequence": str(file_seq),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "synced_from": "file_recovery",
                },
            )
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


__all__ = ["StartupHashChainSync"]
