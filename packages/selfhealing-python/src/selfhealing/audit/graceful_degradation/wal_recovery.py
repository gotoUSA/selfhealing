"""
WAL Recovery for Hash Chain.

Contains:
- HashChainRecoveryWALEntry: WAL entry dataclass
- HashChainWALRecovery: WAL-based recovery for hash chain operations
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class HashChainRecoveryWALEntry:
    """WAL entry for hash chain operation."""

    sequence: int
    operation: str  # "add_integrity", "commit", "abort"
    entry_data: dict[str, Any]
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
        redis_client: Any | None = None,
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

        # WAL file management (writer created lazily per date)
        self._writer = None
        self._writer_date: str | None = None
        self._wal_sequence = 0

        # Recovery state
        self._recovery_done = False
        self._recovered_count = 0
        self._failed_count = 0

        # Ensure WAL directory exists
        self._wal_dir.mkdir(parents=True, exist_ok=True)

    def _get_or_create_writer(self):
        """Get or create a JSONLWriter for today's date."""
        from selfhealing.audit.wal._jsonl import JSONLWriter

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        if self._writer is None or self._writer_date != date_str:
            if self._writer is not None:
                self._writer.close()
            wal_file = self._wal_dir / f"hash_chain_wal_{date_str}.jsonl"
            self._writer = JSONLWriter(file_path=wal_file, fsync=True)
            self._writer_date = date_str
        return self._writer

    def write_wal_entry(
        self,
        operation: str,
        entry: dict[str, Any],
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

            self._get_or_create_writer().append(wal_entry)
            return wal_seq

    def mark_wal_committed(self, wal_sequence: int) -> None:
        """Mark WAL entry as committed (successfully written to Redis)."""
        with self._lock:
            self._get_or_create_writer().append(
                {
                    "_marker": "COMMIT",
                    "wal_sequence": wal_sequence,
                    "operation": "COMMIT",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    def recover_on_startup(self) -> dict[str, Any]:
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
            "idempotency_skipped": 0,
        }

        try:
            wal_files = sorted(self._wal_dir.glob("hash_chain_wal_*.jsonl"))
            result["wal_files_scanned"] = len(wal_files)

            for wal_file in wal_files:
                file_result = self._recover_from_wal_file(wal_file)
                result["entries_found"] += file_result["found"]
                result["entries_recovered"] += file_result["recovered"]
                result["entries_failed"] += file_result["failed"]
                result["entries_already_committed"] += file_result["already_committed"]
                result["idempotency_skipped"] += file_result.get(
                    "idempotency_skipped", 0
                )

            self._recovery_done = True
            self._recovered_count = result["entries_recovered"]
            self._failed_count = result["entries_failed"]

            logger.info(
                "hash_chain_wal.recovery_completed",
                recovery_result=result,
            )

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.exception(
                "watchdog.recovery_failed",
                error=e,
            )

        return result

    def _recover_from_wal_file(self, wal_file: Path) -> dict[str, int]:
        """Recover entries from a single WAL file."""
        from selfhealing.audit.wal._jsonl import JSONLReader

        result = {
            "found": 0,
            "recovered": 0,
            "failed": 0,
            "already_committed": 0,
            "idempotency_skipped": 0,
        }

        entries: dict[int, dict[str, Any]] = {}
        committed_sequences: set[int] = set()

        try:
            for entry in JSONLReader.iter_entries(wal_file):
                wal_seq = entry.get("wal_sequence")
                operation = entry.get("operation")

                if operation == "COMMIT" or entry.get("_marker") == "COMMIT":
                    committed_sequences.add(wal_seq)
                elif operation in ("add_integrity", "write"):
                    entries[wal_seq] = entry
                    result["found"] += 1

            for wal_seq, entry in entries.items():
                if wal_seq in committed_sequences:
                    result["already_committed"] += 1
                    continue

                if self._is_duplicate_via_idempotency(wal_seq, "redis_replay"):
                    result["idempotency_skipped"] += 1
                    logger.debug(
                        "hash_chain_wal.skipped_duplicate_entry_via",
                        wal_seq=wal_seq,
                    )
                    continue

                if self._replay_entry(entry):
                    result["recovered"] += 1
                    self._mark_as_processed_idempotency(wal_seq, "redis_replay")
                else:
                    result["failed"] += 1

        except Exception as e:
            logger.exception(
                "hash_chain_wal.error_reading",
                wal_file=wal_file,
                error=e,
            )

        return result

    def _replay_entry(self, wal_entry: dict[str, Any]) -> bool:
        """Replay a single WAL entry to Redis."""
        if not self._redis:
            logger.warning("hash_chain_wal.no_redis_client_replay")
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
            pipe.hset(
                state_key,
                mapping={
                    "previous_hash": current_hash,
                    "sequence": str(entry_seq),
                    "updated_at": timestamp,
                    "recovered_from": "wal",
                },
            )
            pipe.execute()

            logger.debug(
                "hash_chain_wal.replayed_entry",
                entry_seq=entry_seq,
            )
            return True

        except Exception as e:
            logger.exception(
                "hash_chain_wal.replay_failed",
                error=e,
            )
            return False

    def _is_duplicate_via_idempotency(self, wal_seq: int, operation: str) -> bool:
        """
        IdempotencyKey를 사용하여 중복 WAL 엔트리인지 확인 (1차 방어).

        Redis 기반 빠른 중복 감지로 불필요한 DB 쓰기를 방지합니다.

        Args:
            wal_seq: WAL 시퀀스 번호
            operation: 복구 작업 유형 (redis_replay, pg_insert 등)

        Returns:
            True if duplicate (should skip), False if new
        """
        try:
            from selfhealing.services.idempotency import (
                IdempotencyKey,
                IdempotencyService,
            )

            # WAL 복구용 멱등성 키 생성
            key = IdempotencyKey.for_wal_recovery(
                wal_entry_id=str(wal_seq),
                operation=operation,
            )

            service = IdempotencyService()
            result = service.check(key)

            return result.is_duplicate

        except ImportError:
            # IdempotencyService 미사용 환경
            logger.debug("hash_chain_wal.idempotencyservice_available")
            return False
        except Exception as e:
            # 멱등성 검사 실패 시 안전하게 진행 (중복 허용)
            logger.warning(
                "hash_chain_wal.idempotency_check_failed",
                error=e,
            )
            return False

    def _mark_as_processed_idempotency(self, wal_seq: int, operation: str) -> None:
        """
        복구 완료된 WAL 엔트리를 멱등성 캐시에 등록.

        다음 복구 시도에서 중복으로 처리되도록 합니다.

        Args:
            wal_seq: WAL 시퀀스 번호
            operation: 복구 작업 유형
        """
        try:
            from selfhealing.services.idempotency import (
                IdempotencyKey,
                IdempotencyService,
            )

            key = IdempotencyKey.for_wal_recovery(
                wal_entry_id=str(wal_seq),
                operation=operation,
            )

            service = IdempotencyService()
            # TTL 1시간 (복구 세션 내 중복 방지용)
            service.mark_as_processed(key, ttl=3600)

        except ImportError:
            pass
        except Exception as e:
            logger.warning(
                "hash_chain_wal.failed_mark_processed",
                error=e,
            )

    def cleanup_old_wal_files(self, max_age_days: int = 7) -> int:
        """Remove WAL files older than specified days."""
        from selfhealing.audit.wal._cleanup import cleanup_by_age

        return cleanup_by_age(self._wal_dir, "hash_chain_wal_*.jsonl", max_age_days)

    def close(self) -> None:
        """Close WAL file handle."""
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def get_stats(self) -> dict[str, Any]:
        """Get recovery statistics."""
        current_file = None
        if self._writer is not None:
            current_file = str(self._writer.path)
        return {
            "recovery_done": self._recovery_done,
            "recovered_count": self._recovered_count,
            "failed_count": self._failed_count,
            "wal_sequence": self._wal_sequence,
            "current_wal_file": current_file,
        }


__all__ = ["HashChainRecoveryWALEntry", "HashChainWALRecovery"]
