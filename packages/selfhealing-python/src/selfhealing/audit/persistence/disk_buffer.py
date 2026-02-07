"""
LMDB 기반 Disk-Persistent Buffer.

Pod 재시작에도 데이터가 보존되는 영속 버퍼입니다.
InMemoryAuditBuffer를 대체하여 휘발성 문제를 해결합니다.

주요 기능:
- LMDB 기반 고성능 영속 저장
- CRC32 체크섬으로 무결성 검증
- Group Commit으로 I/O 최적화
- Disk Full 시 Fail-Open 모드
- Dead Letter DB로 Poison Pill 격리
- Graceful Shutdown 지원
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import struct
import sys
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from selfhealing.audit.persistence.config import (
    DiskBufferSettings,
    get_disk_buffer_settings,
)

logger = logging.getLogger(__name__)


@dataclass
class BufferEntry:
    """버퍼 엔트리 데이터 클래스."""

    key: bytes
    """고유 키 (timestamp + sequence)."""

    data: dict[str, Any]
    """이벤트 데이터."""

    timestamp: float
    """저장 시각 (Unix timestamp)."""

    checksum: int
    """CRC32 체크섬."""

    @property
    def sequence(self) -> int:
        """시퀀스 번호 추출."""
        # 키 형식: b"{timestamp:.6f}:{sequence:08d}"
        try:
            return int(self.key.decode().split(":")[1])
        except (ValueError, IndexError):
            return 0


class DiskBufferError(Exception):
    """Disk Buffer 에러."""

    pass


class BufferState(Enum):
    """버퍼 상태."""

    UNINITIALIZED = 0
    ACTIVE = 1
    DISK_FULL_FAILOPEN = 2
    CORRUPTED = 3
    CLOSED = 4


class DiskPersistentBuffer:
    """
    LMDB 기반 Disk-Persistent Buffer.

    특징:
    - Pod 재시작에도 데이터 보존
    - ACID 트랜잭션
    - 초고속 읽기/쓰기
    - CRC32 체크섬으로 무결성 검증
    - 자동 정리 (retention 기반)

    기존 InMemoryAuditBuffer와 호환되는 메서드:
    - add(entry) → put(entry)
    - try_flush(callback) → flush_to(callback)
    - get_all() → iter_entries()

    Usage:
        buffer = DiskPersistentBuffer()

        # 저장
        buffer.put({"event": "dlq_store", "domain": "payment"})

        # 조회
        for entry in buffer.iter_entries():
            print(entry.data)

        # 플러시
        buffer.flush_to(lambda entries: send_to_kafka(entries))
    """

    # 데이터베이스 이름
    DB_ENTRIES = b"entries"
    DB_META = b"meta"
    DB_DEAD_LETTER = b"dead_letter"

    def __init__(
        self,
        settings: DiskBufferSettings | None = None,
        db_name: str | None = None,
    ):
        """
        DiskPersistentBuffer 초기화.

        Args:
            settings: 버퍼 설정 (None이면 환경변수에서 로드)
            db_name: 데이터베이스 이름 (None이면 자동 생성)
        """
        self._settings = settings or get_disk_buffer_settings()
        self._db_name = db_name or self._generate_db_name()
        self._lock = threading.RLock()

        # 상태
        self._state = BufferState.UNINITIALIZED

        # LMDB 환경
        self._env: Any = None
        self._entries_db: Any = None
        self._meta_db: Any = None
        self._dead_letter_db: Any = None

        # 시퀀스 번호
        self._sequence = 0

        # Group Commit 버퍼
        self._group_buffer: list[dict[str, Any]] = []
        self._last_flush_time: float = time.time()

        # Poison Pill 재시도 카운터
        self._retry_counters: dict[str, int] = {}

        # 통계
        self._stats: dict[str, int] = {
            "total_puts": 0,
            "total_gets": 0,
            "total_deletes": 0,
            "checksum_errors": 0,
            "group_commit_flushes": 0,
            "dead_letter_moves": 0,
            "disk_full_events": 0,
            "quarantine_events": 0,
        }

        self._init_storage()

        # Graceful Shutdown 등록
        if self._settings.enable_shutdown_handlers:
            register_disk_buffer_shutdown(self)

    def _generate_db_name(self) -> str:
        """
        DB 이름 자동 생성 (Multi-Instance 안전).

        멀티 Pod, 멀티 프로세스 환경에서 충돌 방지를 위해
        호스트네임과 PID를 포함합니다.
        """
        parts = ["audit_buffer"]

        if self._settings.include_hostname_in_db_name:
            hostname = os.environ.get("HOSTNAME", os.environ.get("POD_NAME", "unknown"))
            parts.append(hostname)

        if self._settings.include_pid_in_db_name:
            parts.append(str(os.getpid()))

        return "_".join(parts)

    def _init_storage(self) -> None:
        """LMDB 스토리지 초기화 (Quarantine 지원)."""
        try:
            import lmdb
        except ImportError as e:
            raise DiskBufferError("lmdb not installed. Install with: pip install lmdb") from e

        db_path = self._settings.data_path / self._db_name
        db_path.mkdir(parents=True, exist_ok=True)

        try:
            self._env = lmdb.open(
                str(db_path),
                **self._settings.get_lmdb_open_kwargs(),
            )
        except Exception as e:
            # Corruption 감지 시 Quarantine
            if self._settings.quarantine_on_corruption:
                if self._quarantine_corrupt_db(db_path):
                    # 새 DB로 재시도
                    self._env = lmdb.open(
                        str(db_path),
                        **self._settings.get_lmdb_open_kwargs(),
                    )
                else:
                    raise DiskBufferError(f"Failed to recover from corruption: {e}")
            else:
                raise

        # 데이터베이스 열기
        self._entries_db = self._env.open_db(self.DB_ENTRIES, create=True)
        self._meta_db = self._env.open_db(self.DB_META, create=True)

        if self._settings.enable_dead_letter_db:
            self._dead_letter_db = self._env.open_db(self.DB_DEAD_LETTER, create=True)

        self._recover_sequence()
        self._state = BufferState.ACTIVE

        logger.info(f"[DiskBuffer] Initialized: path={db_path}, " f"sequence={self._sequence}, db_name={self._db_name}")

    def _quarantine_corrupt_db(self, db_path: Path) -> bool:
        """
        손상된 DB를 격리하고 새 DB 생성 준비.

        손상된 DB 파일을 별도 디렉토리로 이동하고
        새 DB를 생성할 수 있도록 합니다.

        Args:
            db_path: 손상된 DB 경로

        Returns:
            True: 격리 성공
            False: 격리 실패
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            corrupt_path = db_path.parent / f"{db_path.name}{self._settings.quarantine_suffix}.{timestamp}"

            shutil.move(str(db_path), str(corrupt_path))
            self._stats["quarantine_events"] += 1

            logger.critical(f"[DiskBuffer] Quarantined corrupt DB: {db_path} -> {corrupt_path}")

            # 알림 전송 시도
            self._send_corruption_alert(db_path, corrupt_path)

            return True
        except Exception as e:
            logger.error(f"[DiskBuffer] Quarantine failed: {e}")
            return False

    def _send_corruption_alert(self, original: Path, quarantined: Path) -> None:
        """손상 알림 전송."""
        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            payload = NotificationPayload(
                title="🚨 DiskBuffer DB Corruption Detected",
                message=f"DB 손상 감지 및 격리 완료: {original} -> {quarantined}",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.OPERATIONS,
                source="DiskPersistentBuffer",
                dedup_key=f"disk_buffer:corruption:{original}",
            )
            UnifiedNotificationManager().notify(payload)
        except Exception as e:
            logger.debug(f"[DiskBuffer] Alert send failed: {e}")

    def _recover_sequence(self) -> None:
        """마지막 시퀀스 복구."""
        with self._env.begin(db=self._meta_db) as txn:
            value = txn.get(b"last_sequence")
            if value:
                self._sequence = struct.unpack(">Q", value)[0]

    def _save_sequence(self, txn: Any) -> None:
        """시퀀스 저장."""
        txn.put(
            b"last_sequence",
            struct.pack(">Q", self._sequence),
            db=self._meta_db,
        )

    def _compute_checksum(self, data: bytes) -> int:
        """CRC32 체크섬 계산."""
        return zlib.crc32(data) & 0xFFFFFFFF

    def _generate_key(self) -> bytes:
        """고유 키 생성."""
        self._sequence += 1
        timestamp = time.time()
        return f"{timestamp:.6f}:{self._sequence:08d}".encode()

    # ─────────────────────────────────────────────────────
    # Public API: 쓰기
    # ─────────────────────────────────────────────────────

    def put(self, entry: dict[str, Any]) -> bytes | None:
        """
        엔트리 저장.

        Disk Full 시 Fail-Open 모드로 전환하여 서비스를 지속합니다.
        Group Commit 활성화 시 배치로 저장하여 I/O를 최적화합니다.

        Args:
            entry: 이벤트 데이터

        Returns:
            저장된 키 (Fail-Open 모드에서는 None 반환)
        """
        with self._lock:
            # Disk Full Fail-Open 모드 확인
            if self._state == BufferState.DISK_FULL_FAILOPEN:
                logger.warning("[DiskBuffer] Disk full fail-open mode, skipping write")
                return None

            if self._state == BufferState.CLOSED:
                logger.warning("[DiskBuffer] Buffer closed, skipping write")
                return None

            # 디스크 여유 공간 확인 및 Purge
            if not self._check_disk_space():
                if self._settings.fail_open_on_disk_full:
                    return None
                raise DiskBufferError("Disk full and fail_open disabled")

            # Group Commit 활성화 시 버퍼링
            if self._settings.group_commit_enabled:
                return self._buffered_put(entry)

            return self._direct_put(entry)

    def _check_disk_space(self) -> bool:
        """
        디스크 여유 공간 확인 및 Priority-based Purge.

        Returns:
            True: 정상 또는 공간 확보 성공
            False: Disk Full 상태
        """
        try:
            usage = shutil.disk_usage(self._settings.data_path)
            free_ratio = usage.free / usage.total

            if free_ratio < self._settings.disk_full_threshold:
                # Priority-based Purge 시도
                if self._settings.priority_based_purge:
                    purged = self._purge_old_entries_for_space()
                    if purged > 0:
                        logger.warning(f"[DiskBuffer] Purged {purged} entries for disk space")
                        return True

                # Purge 실패 시 Fail-Open 모드 전환
                self._state = BufferState.DISK_FULL_FAILOPEN
                self._stats["disk_full_events"] += 1
                logger.critical("[DiskBuffer] DISK FULL - Switching to fail-open mode")
                self._send_disk_full_alert()
                return False

            # 복구 확인
            if self._state == BufferState.DISK_FULL_FAILOPEN:
                if free_ratio > self._settings.disk_recovery_threshold:
                    self._state = BufferState.ACTIVE
                    logger.info("[DiskBuffer] Disk space recovered, resuming normal operation")

            return True
        except Exception as e:
            logger.debug(f"[DiskBuffer] Disk check failed: {e}")
            return True  # 체크 실패 시 계속 진행

    def _purge_old_entries_for_space(self) -> int:
        """오래된 엔트리 삭제로 공간 확보."""
        # 가장 오래된 10% 삭제
        total = self.count()
        if total < 100:
            return 0

        to_delete = total // 10
        keys_to_delete = []

        for entry in self.iter_entries(limit=to_delete):
            keys_to_delete.append(entry.key)

        return self.delete_batch(keys_to_delete)

    def _send_disk_full_alert(self) -> None:
        """Disk Full 알림 전송."""
        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            payload = NotificationPayload(
                title="🚨 DiskBuffer Disk Full - Fail-Open Mode",
                message="DiskBuffer 디스크 용량 부족으로 Fail-Open 모드 전환. 즉시 조치 필요!",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.OPERATIONS,
                source="DiskPersistentBuffer",
                dedup_key="disk_buffer:disk_full",
            )
            UnifiedNotificationManager().notify(payload)
        except Exception as e:
            logger.debug(f"[DiskBuffer] Alert send failed: {e}")

    def _buffered_put(self, entry: dict[str, Any]) -> bytes:
        """
        Group Commit 모드 저장.

        여러 엔트리를 버퍼에 모았다가 한 번에 저장하여
        fsync 호출을 최소화합니다.
        """
        key = self._generate_key()

        entry_with_meta = {
            **entry,
            "_stored_at": datetime.now(timezone.utc).isoformat(),
            "_buffer_key": key.decode(),
        }

        self._group_buffer.append(
            {
                "key": key,
                "entry": entry_with_meta,
            }
        )

        # 플러시 조건
        should_flush = (
            len(self._group_buffer) >= self._settings.group_commit_max_entries
            or self._time_since_last_flush_ms() >= self._settings.group_commit_interval_ms
        )

        if should_flush:
            self._flush_group_buffer()

        return key

    def _time_since_last_flush_ms(self) -> float:
        """마지막 플러시 이후 경과 시간 (ms)."""
        return (time.time() - self._last_flush_time) * 1000

    def _flush_group_buffer(self) -> None:
        """Group Buffer 플러시 (단일 fsync)."""
        if not self._group_buffer:
            return

        with self._env.begin(write=True, db=self._entries_db) as txn:
            for item in self._group_buffer:
                key = item["key"]
                entry = item["entry"]

                data = json.dumps(entry, default=str, ensure_ascii=False)
                data_bytes = data.encode("utf-8")
                checksum = self._compute_checksum(data_bytes)
                value = struct.pack(">I", checksum) + data_bytes

                txn.put(key, value)
                self._stats["total_puts"] += 1

            self._save_sequence(txn)

        # fsync 수행 (sync=False일 때도 주기적 sync)
        if not self._settings.sync_on_write:
            self._env.sync()

        self._stats["group_commit_flushes"] += 1
        self._group_buffer.clear()
        self._last_flush_time = time.time()

    def _direct_put(self, entry: dict[str, Any]) -> bytes:
        """직접 저장 (기존 방식)."""
        key = self._generate_key()

        entry_with_meta = {
            **entry,
            "_stored_at": datetime.now(timezone.utc).isoformat(),
            "_buffer_key": key.decode(),
        }

        data = json.dumps(entry_with_meta, default=str, ensure_ascii=False)
        data_bytes = data.encode("utf-8")
        checksum = self._compute_checksum(data_bytes)
        value = struct.pack(">I", checksum) + data_bytes

        with self._env.begin(write=True, db=self._entries_db) as txn:
            txn.put(key, value)
            self._save_sequence(txn)

        self._stats["total_puts"] += 1
        logger.debug(f"[DiskBuffer] Put: key={key.decode()}")
        return key

    def flush_group_commit(self) -> None:
        """Group Commit 버퍼 강제 플러시 (Graceful Shutdown용)."""
        with self._lock:
            if self._settings.group_commit_enabled:
                self._flush_group_buffer()

    # ─────────────────────────────────────────────────────
    # Public API: 읽기
    # ─────────────────────────────────────────────────────

    def get(self, key: bytes) -> BufferEntry | None:
        """
        엔트리 조회.

        Args:
            key: 엔트리 키

        Returns:
            BufferEntry 또는 None
        """
        with self._env.begin(db=self._entries_db) as txn:
            value = txn.get(key)
            if not value:
                return None

            self._stats["total_gets"] += 1
            return self._parse_entry(key, value)

    def _parse_entry(self, key: bytes, value: bytes) -> BufferEntry | None:
        """엔트리 파싱."""
        try:
            # 체크섬 추출
            stored_checksum = struct.unpack(">I", value[:4])[0]
            data_bytes = value[4:]

            # 체크섬 검증
            if self._settings.enable_checksum:
                computed_checksum = self._compute_checksum(data_bytes)
                if stored_checksum != computed_checksum:
                    logger.warning(f"[DiskBuffer] Checksum mismatch: key={key.decode()}")
                    self._stats["checksum_errors"] += 1
                    return None

            # JSON 파싱
            data = json.loads(data_bytes.decode("utf-8"))

            # 타임스탬프 추출
            timestamp = float(key.decode().split(":")[0])

            return BufferEntry(
                key=key,
                data=data,
                timestamp=timestamp,
                checksum=stored_checksum,
            )
        except Exception as e:
            logger.error(f"[DiskBuffer] Parse error: {e}")
            return None

    def iter_entries(
        self,
        limit: int | None = None,
        reverse: bool = False,
    ) -> Iterator[BufferEntry]:
        """
        엔트리 순회.

        Args:
            limit: 최대 반환 수
            reverse: 역순 여부

        Yields:
            BufferEntry
        """
        count = 0
        with self._env.begin(db=self._entries_db) as txn:
            cursor = txn.cursor()

            if reverse:
                cursor.last()
                iterator = cursor.iterprev()
            else:
                cursor.first()
                iterator = cursor.iternext()

            for key, value in iterator:
                if limit and count >= limit:
                    break

                entry = self._parse_entry(key, value)
                if entry:
                    count += 1
                    yield entry

    def count(self) -> int:
        """엔트리 수 반환."""
        if self._env is None:
            return 0
        with self._env.begin(db=self._entries_db) as txn:
            return txn.stat()["entries"]

    # ─────────────────────────────────────────────────────
    # Public API: 삭제
    # ─────────────────────────────────────────────────────

    def delete(self, key: bytes) -> bool:
        """
        엔트리 삭제.

        Args:
            key: 엔트리 키

        Returns:
            삭제 성공 여부
        """
        with self._env.begin(write=True, db=self._entries_db) as txn:
            result = txn.delete(key)
            if result:
                self._stats["total_deletes"] += 1
            return result

    def delete_batch(self, keys: list[bytes]) -> int:
        """
        배치 삭제.

        Args:
            keys: 삭제할 키 목록

        Returns:
            삭제된 수
        """
        deleted = 0
        with self._env.begin(write=True, db=self._entries_db) as txn:
            for key in keys:
                if txn.delete(key):
                    deleted += 1

        self._stats["total_deletes"] += deleted
        return deleted

    # ─────────────────────────────────────────────────────
    # Public API: 플러시
    # ─────────────────────────────────────────────────────

    def flush_to(
        self,
        handler: Callable[[list[BufferEntry]], bool],
        batch_size: int | None = None,
    ) -> int:
        """
        핸들러로 플러시 후 삭제.

        Args:
            handler: 배치 처리 핸들러 (성공 시 True 반환)
            batch_size: 배치 크기

        Returns:
            플러시된 엔트리 수
        """
        batch_size = batch_size or self._settings.flush_batch_size
        flushed = 0

        while True:
            # 배치 수집
            entries = list(self.iter_entries(limit=batch_size))
            if not entries:
                break

            # 핸들러 호출
            try:
                success = handler(entries)
            except Exception as e:
                logger.error(f"[DiskBuffer] Flush handler error: {e}")
                # Poison Pill 격리 시도
                self._handle_flush_failure(entries, e)
                break

            if success:
                # 성공 시 삭제 + 재시도 카운터 초기화
                keys = [e.key for e in entries]
                self._clear_retry_counters(keys)
                deleted = self.delete_batch(keys)
                flushed += deleted

                logger.debug(f"[DiskBuffer] Flushed {deleted} entries")
            else:
                # 실패 시 재시도 카운터 증가 및 Poison Pill 검사
                for entry in entries:
                    self._increment_retry_counter(entry.key)
                    if self._is_poison_pill(entry.key):
                        self._move_to_dead_letter(entry)
                break

        return flushed

    def _handle_flush_failure(self, entries: list[BufferEntry], error: Exception) -> None:
        """
        플러시 실패 처리 - Poison Pill 격리.

        반복 실패하는 엔트리는 Dead Letter DB로 이동하여
        정상 처리 흐름을 방해하지 않도록 합니다.
        """
        for entry in entries:
            self._increment_retry_counter(entry.key)
            if self._is_poison_pill(entry.key):
                self._move_to_dead_letter(entry, error=str(error))

    def _increment_retry_counter(self, key: bytes) -> None:
        """재시도 카운터 증가."""
        key_str = key.decode()
        self._retry_counters[key_str] = self._retry_counters.get(key_str, 0) + 1

    def _clear_retry_counters(self, keys: list[bytes]) -> None:
        """성공한 키들의 재시도 카운터 삭제."""
        for key in keys:
            key_str = key.decode()
            self._retry_counters.pop(key_str, None)

    def _is_poison_pill(self, key: bytes) -> bool:
        """Poison Pill 여부 확인 (max_flush_retries 초과)."""
        key_str = key.decode()
        return self._retry_counters.get(key_str, 0) >= self._settings.max_flush_retries

    def _move_to_dead_letter(self, entry: BufferEntry, error: str = "") -> None:
        """
        Poison Pill 엔트리를 Dead Letter DB로 이동.

        반복 실패하는 엔트리를 격리하여 관리자가 검토할 수 있도록 합니다.
        """
        if not self._settings.enable_dead_letter_db:
            logger.warning(f"[DiskBuffer] Poison pill detected but DLQ disabled: {entry.key}")
            self.delete(entry.key)  # 무한 루프 방지
            return

        try:
            # Dead Letter DB에 저장 (REQUIRES_REVIEW 상태)
            dlq_entry = {
                **entry.data,
                "_dead_letter_at": datetime.now(timezone.utc).isoformat(),
                "_original_key": entry.key.decode(),
                "_retry_count": self._retry_counters.get(entry.key.decode(), 0),
                "_failure_reason": error,
                "status": "requires_review",
            }

            dlq_data = json.dumps(dlq_entry, default=str, ensure_ascii=False)
            dlq_bytes = dlq_data.encode("utf-8")
            checksum = self._compute_checksum(dlq_bytes)
            dlq_value = struct.pack(">I", checksum) + dlq_bytes

            with self._env.begin(write=True, db=self._dead_letter_db) as txn:
                txn.put(entry.key, dlq_value)

            # 원본 DB에서 삭제
            self.delete(entry.key)

            # 재시도 카운터 정리
            self._retry_counters.pop(entry.key.decode(), None)

            self._stats["dead_letter_moves"] += 1
            logger.warning(f"[DiskBuffer] Moved to dead letter DB: {entry.key.decode()}")

            # 알림 전송
            self._send_poison_pill_alert(entry)

        except Exception as e:
            logger.error(f"[DiskBuffer] Dead letter move failed: {e}")

    def _send_poison_pill_alert(self, entry: BufferEntry) -> None:
        """Poison Pill 알림 전송."""
        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            payload = NotificationPayload(
                title="⚠️ DiskBuffer Poison Pill Detected",
                message=(f"반복 실패 엔트리가 Dead Letter DB로 격리됨: " f"{entry.key.decode()}"),
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.OPERATIONS,
                source="DiskPersistentBuffer",
                dedup_key=f"disk_buffer:poison_pill:{entry.key.decode()[:20]}",
            )
            UnifiedNotificationManager().notify(payload)
        except Exception as e:
            logger.debug(f"[DiskBuffer] Alert send failed: {e}")

    # ─────────────────────────────────────────────────────
    # Public API: Dead Letter 관리
    # ─────────────────────────────────────────────────────

    def get_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Dead Letter DB 조회 (관리자 검토용).

        Returns:
            Dead Letter 엔트리 목록
        """
        if not self._settings.enable_dead_letter_db or self._dead_letter_db is None:
            return []

        result = []
        with self._env.begin(db=self._dead_letter_db) as txn:
            cursor = txn.cursor()
            count = 0
            for key, value in cursor:
                if count >= limit:
                    break
                try:
                    data_bytes = value[4:]  # checksum skip
                    data = json.loads(data_bytes.decode("utf-8"))
                    result.append({"key": key.decode(), **data})
                    count += 1
                except Exception:
                    pass
        return result

    def replay_dead_letter(self, key: bytes) -> bool:
        """
        Dead Letter 엔트리 재시도.

        Args:
            key: Dead Letter 키

        Returns:
            성공 여부
        """
        if not self._settings.enable_dead_letter_db or self._dead_letter_db is None:
            return False

        with self._env.begin(db=self._dead_letter_db) as txn:
            value = txn.get(key)
            if not value:
                return False

        try:
            data_bytes = value[4:]
            data = json.loads(data_bytes.decode("utf-8"))

            # Dead Letter 메타데이터 제거 후 원본 DB로 이동
            data.pop("_dead_letter_at", None)
            data.pop("_retry_count", None)
            data.pop("_failure_reason", None)
            data.pop("status", None)

            self.put(data)

            # Dead Letter에서 삭제
            with self._env.begin(write=True, db=self._dead_letter_db) as txn:
                txn.delete(key)

            logger.info(f"[DiskBuffer] Replayed dead letter: {key.decode()}")
            return True
        except Exception as e:
            logger.error(f"[DiskBuffer] Dead letter replay failed: {e}")
            return False

    # ─────────────────────────────────────────────────────
    # Public API: 정리
    # ─────────────────────────────────────────────────────

    def cleanup_old_entries(self, max_age_seconds: float | None = None) -> int:
        """
        오래된 엔트리 정리.

        Args:
            max_age_seconds: 최대 보관 시간 (초)

        Returns:
            삭제된 엔트리 수
        """
        max_age = max_age_seconds or (self._settings.retention_hours * 3600)
        cutoff_time = time.time() - max_age

        keys_to_delete = []
        for entry in self.iter_entries():
            if entry.timestamp < cutoff_time:
                keys_to_delete.append(entry.key)

        if keys_to_delete:
            deleted = self.delete_batch(keys_to_delete)
            logger.info(f"[DiskBuffer] Cleaned up {deleted} old entries")
            return deleted

        return 0

    # ─────────────────────────────────────────────────────
    # Public API: 상태 조회
    # ─────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        if self._env is None:
            return {**self._stats, "count": 0, "db_size_bytes": 0, "sequence": 0}

        with self._env.begin(db=self._entries_db) as txn:
            stat = txn.stat()

        return {
            **self._stats,
            "count": stat["entries"],
            "db_size_bytes": stat.get("psize", 0) * stat.get("leaf_pages", 0),
            "sequence": self._sequence,
        }

    def get_health_status(self) -> dict[str, Any]:
        """
        Health Check용 상태 반환.

        Kubernetes readiness/liveness probe 통합용.

        Returns:
            {
                "healthy": bool,
                "state": str,
                "entry_count": int,
                "disk_free_ratio": float,
                "dead_letter_count": int,
                "errors": list[str],
            }
        """
        errors: list[str] = []
        healthy = True

        # 상태 확인
        state = self._state.name
        if self._state == BufferState.DISK_FULL_FAILOPEN:
            healthy = False
            errors.append("Disk full - fail-open mode active")
        elif self._state == BufferState.CORRUPTED:
            healthy = False
            errors.append("DB corrupted")

        # 엔트리 수
        try:
            entry_count = self.count()
        except Exception as e:
            healthy = False
            errors.append(f"Cannot read entry count: {e}")
            entry_count = -1

        # 디스크 여유 공간
        disk_free_ratio = -1.0
        try:
            usage = shutil.disk_usage(self._settings.data_path)
            disk_free_ratio = usage.free / usage.total

            if disk_free_ratio < 0.1:  # 10% 미만 경고
                errors.append(f"Low disk space: {disk_free_ratio:.1%}")
        except Exception as e:
            errors.append(f"Cannot check disk space: {e}")

        # Dead Letter 수
        dead_letter_count = 0
        if self._settings.enable_dead_letter_db:
            try:
                dead_letters = self.get_dead_letters(limit=1000)
                dead_letter_count = len(dead_letters)
                if dead_letter_count > 100:
                    errors.append(f"High dead letter count: {dead_letter_count}")
            except Exception:
                pass

        return {
            "healthy": healthy and len(errors) == 0,
            "state": state,
            "entry_count": entry_count,
            "disk_free_ratio": disk_free_ratio,
            "dead_letter_count": dead_letter_count,
            "errors": errors,
        }

    @property
    def state(self) -> BufferState:
        """현재 버퍼 상태."""
        return self._state

    # ─────────────────────────────────────────────────────
    # 리소스 관리
    # ─────────────────────────────────────────────────────

    def close(self) -> None:
        """버퍼 종료."""
        with self._lock:
            if self._env:
                # Group Commit 버퍼 플러시
                if self._settings.group_commit_enabled and self._group_buffer:
                    try:
                        self._flush_group_buffer()
                    except Exception as e:
                        logger.error(f"[DiskBuffer] Final flush failed: {e}")

                self._env.close()
                self._env = None
                self._state = BufferState.CLOSED
                logger.info("[DiskBuffer] Closed")

    def __enter__(self) -> "DiskPersistentBuffer":
        """Context manager 진입."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager 종료."""
        self.close()


# ─────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────

_buffer: DiskPersistentBuffer | None = None
_buffer_lock = threading.Lock()


def get_disk_buffer() -> DiskPersistentBuffer:
    """Disk Buffer 싱글톤 반환."""
    global _buffer
    if _buffer is None:
        with _buffer_lock:
            if _buffer is None:
                _buffer = DiskPersistentBuffer()
    return _buffer


def reset_disk_buffer() -> None:
    """버퍼 리셋 (테스트용)."""
    global _buffer
    with _buffer_lock:
        if _buffer is not None:
            _buffer.close()
            _buffer = None


# ─────────────────────────────────────────────────────────────
# InMemoryAuditBuffer 호환 어댑터
# ─────────────────────────────────────────────────────────────


class DiskBufferAdapter:
    """
    InMemoryAuditBuffer 호환 어댑터.

    기존 코드와의 호환성을 위해 InMemoryAuditBuffer 인터페이스 제공.
    내부적으로 DiskPersistentBuffer 사용.

    Usage:
        # 기존 코드
        buffer = InMemoryAuditBuffer.get_instance()
        buffer.add(entry)

        # 신규 코드 (동일 인터페이스)
        buffer = DiskBufferAdapter.get_instance()
        buffer.add(entry)
    """

    _instance: DiskBufferAdapter | None = None
    _lock = threading.Lock()

    def __init__(self, disk_buffer: DiskPersistentBuffer | None = None):
        """초기화."""
        self._disk_buffer = disk_buffer or get_disk_buffer()
        self._total_buffered = 0
        self._total_dropped = 0

    @classmethod
    def get_instance(cls) -> DiskBufferAdapter:
        """싱글톤 반환."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """인스턴스 리셋 (테스트용)."""
        with cls._lock:
            cls._instance = None

    def add(self, entry: dict[str, Any]) -> bool:
        """
        엔트리 추가 (InMemoryAuditBuffer 호환).

        Args:
            entry: 이벤트 데이터

        Returns:
            True (항상 성공, Fail-Open 시 드롭)
        """
        result = self._disk_buffer.put(entry)
        if result:
            self._total_buffered += 1
        else:
            self._total_dropped += 1
        return True

    def try_flush(
        self,
        wal_write_func: Callable[[dict[str, Any]], int | None],
    ) -> int:
        """
        플러시 시도 (InMemoryAuditBuffer 호환).

        Args:
            wal_write_func: WAL 쓰기 함수

        Returns:
            플러시된 엔트리 수
        """

        def handler(entries: list[BufferEntry]) -> bool:
            for entry in entries:
                result = wal_write_func(entry.data)
                if result is None:
                    return False
            return True

        return self._disk_buffer.flush_to(handler)

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        stats = self._disk_buffer.get_stats()
        return {
            **stats,
            "total_buffered": self._total_buffered,
            "total_dropped": self._total_dropped,
        }

    def __len__(self) -> int:
        """현재 엔트리 수."""
        return self._disk_buffer.count()


# ─────────────────────────────────────────────────────────────
# Graceful Shutdown
# ─────────────────────────────────────────────────────────────

_disk_buffer_instance: DiskPersistentBuffer | None = None


def register_disk_buffer_shutdown(buffer: DiskPersistentBuffer) -> None:
    """
    DiskPersistentBuffer Shutdown 핸들러 등록.

    SIGTERM, SIGINT 시그널 및 프로세스 종료 시
    버퍼를 안전하게 종료합니다.

    Args:
        buffer: DiskPersistentBuffer 인스턴스
    """
    global _disk_buffer_instance
    _disk_buffer_instance = buffer

    # atexit 핸들러 등록
    atexit.register(_shutdown_disk_buffer)

    # 시그널 핸들러 등록 (기존 핸들러 체인)
    _register_signal_handlers()

    logger.debug("[DiskBuffer] Shutdown handlers registered")


def _register_signal_handlers() -> None:
    """SIGTERM, SIGINT 핸들러 등록."""
    if sys.platform == "win32":
        # Windows는 SIGTERM 미지원, atexit만 사용
        return

    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def _sigterm_handler(signum: int, frame: Any) -> None:
        """SIGTERM 핸들러."""
        logger.info("[DiskBuffer] SIGTERM received, initiating shutdown")
        _shutdown_disk_buffer()
        # 원래 핸들러 호출
        if callable(original_sigterm):
            original_sigterm(signum, frame)

    def _sigint_handler(signum: int, frame: Any) -> None:
        """SIGINT 핸들러."""
        logger.info("[DiskBuffer] SIGINT received, initiating shutdown")
        _shutdown_disk_buffer()
        # 원래 핸들러 호출
        if callable(original_sigint):
            original_sigint(signum, frame)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigint_handler)


def _shutdown_disk_buffer() -> None:
    """
    DiskPersistentBuffer Graceful Shutdown.

    수행 작업:
    1. Group Commit 버퍼 플러시
    2. LMDB fsync
    3. 리소스 정리
    """
    global _disk_buffer_instance

    if _disk_buffer_instance is None:
        return

    # atexit 시점에서 logging stream이 닫힐 수 있으므로
    # 로깅 실패 시 "--- Logging error ---" 출력을 억제
    logging.raiseExceptions = False

    try:
        # 1. Group Commit 버퍼 강제 플러시
        if _disk_buffer_instance._settings.group_commit_enabled:
            _disk_buffer_instance.flush_group_commit()

        # 2. LMDB fsync (데이터 안전 보장)
        if _disk_buffer_instance._env:
            _disk_buffer_instance._env.sync()

        # 3. 버퍼 종료
        _disk_buffer_instance.close()

    except Exception:
        pass

    finally:
        _disk_buffer_instance = None
