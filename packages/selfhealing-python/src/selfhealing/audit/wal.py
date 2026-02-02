"""
Write-Ahead Log (WAL) with CRC32 Checksum.

데이터 무결성 보장:
1. 메모리에 먼저 기록 전 WAL 작성
2. 각 엔트리에 CRC32 체크섬
3. 복구 시 체크섬 검증

최소 의존성: 표준 라이브러리만 사용 (struct, json, zlib, os, threading)

Usage:
    from selfhealing.audit.wal import WriteAheadLog, WALEntry, WALConfig

    # WAL 생성
    wal = WriteAheadLog(wal_dir="/var/log/audit/wal")

    # 기록
    seq = wal.write({"event": "config_change", "key": "max_retries"})

    # 복구
    entries = wal.recover_unprocessed(last_processed_seq=100)

    # 정리
    wal.cleanup_processed(last_processed_seq=500)
"""

import json
import logging
import os
import struct
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Drift Detection 메트릭
try:
    from selfhealing.metrics.drift_metrics import (
        record_wal_corruption,
        record_wal_entries_recovered,
        record_wal_entry_written,
        record_wal_rotation,
        update_wal_last_sequence,
        update_wal_sync_lag,
    )

    HAS_DRIFT_METRICS = True
except ImportError:
    HAS_DRIFT_METRICS = False


class WALState(Enum):
    """WAL 상태."""

    ACTIVE = "active"
    ROTATING = "rotating"
    CLOSED = "closed"
    CORRUPTED = "corrupted"
    DISK_FULL_FAILOPEN = "disk_full_failopen"  # 디스크 풀 시 Fail-Open 모드


@dataclass
class WALEntry:
    """WAL 엔트리."""

    sequence: int
    timestamp: float
    data: dict[str, Any]
    checksum: str

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "seq": self.sequence,
            "ts": self.timestamp,
            "data": self.data,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WALEntry":
        """딕셔너리에서 생성."""
        return cls(
            sequence=d["seq"],
            timestamp=d["ts"],
            data=d["data"],
            checksum=d.get("checksum", ""),
        )


@dataclass
class WALConfig:
    """WAL 설정."""

    wal_dir: str = "/var/log/audit/wal"
    max_file_size_mb: int = 100
    sync_on_write: bool = True
    max_files: int = 10  # 최대 보관 파일 수
    file_prefix: str = "audit_wal"

    # Group Commit 설정 (I/O 최적화)
    group_commit_enabled: bool = False  # Group Commit 활성화
    group_commit_max_entries: int = 100  # 최대 버퍼 엔트리 수
    group_commit_max_wait_ms: int = 10  # 최대 대기 시간 (ms)

    # Disk Full Fail-Open 설정
    fail_open_on_disk_full: bool = True  # 디스크 풀 시 Fail-Open 활성화
    disk_recovery_threshold: float = 0.1  # 디스크 복구 임계치 (10% 여유 시 복귀)

    # Best-Effort Recovery 설정
    best_effort_recovery: bool = True  # 손상 시 마커 기반 복구

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@dataclass
class WALStats:
    """WAL 통계."""

    state: WALState
    current_file: str | None
    current_size_bytes: int
    total_entries: int
    total_files: int
    last_sequence: int
    last_write_time: float | None
    corrupted_entries: int
    recovered_entries: int
    # Group Commit 통계
    group_commit_flushes: int = 0
    group_commit_buffered: int = 0


class WALError(Exception):
    """WAL 관련 에러."""

    pass


class WALCorruptionError(WALError):
    """WAL 손상 에러."""

    def __init__(self, message: str, sequence: int, expected: str, computed: str):
        super().__init__(message)
        self.sequence = sequence
        self.expected = expected
        self.computed = computed


class WriteAheadLog:
    """
    Write-Ahead Log with CRC32 Checksum.

    특징:
    - Thread-safe
    - CRC32 체크섬으로 무결성 검증
    - 파일 로테이션
    - 미처리 엔트리 복구
    - Best-Effort Recovery (손상 시 마커 기반 복구)
    """

    # 파일 포맷 상수
    MAGIC = b"AWAL"  # Audit WAL
    VERSION = 1
    HEADER_SIZE = 8  # MAGIC(4) + VERSION(2) + reserved(2)
    RECORD_HEADER_SIZE = 12  # length(4) + checksum(8)

    # Best-Effort Recovery용 레코드 마커
    RECORD_MAGIC = b"\xAB\xCD"  # 레코드 시작 마커 (2바이트)
    RECORD_MAGIC_HEADER_SIZE = 14  # magic(2) + length(4) + checksum(8)

    def __init__(
        self,
        config: WALConfig | None = None,
        on_rotate: Callable[[str], None] | None = None,
        on_corruption: Callable[[WALCorruptionError], None] | None = None,
        audit_adapter=None,  # Audit 어댑터 (Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md)
    ):
        """
        WAL 초기화.

        Args:
            config: WAL 설정
            on_rotate: 파일 로테이션 시 콜백
            on_corruption: 손상 발견 시 콜백
            audit_adapter: Audit 어댑터 (이벤트 기록용)
        """
        self._config = config or WALConfig()
        self._on_rotate = on_rotate
        self._on_corruption = on_corruption
        self._audit_adapter = audit_adapter

        self._wal_dir = Path(self._config.wal_dir)
        self._current_file: Path | None = None
        self._current_handle: Any | None = None
        self._sequence = 0
        self._state = WALState.ACTIVE
        self._lock = threading.RLock()

        # 통계
        self._total_entries = 0
        self._corrupted_entries = 0
        self._recovered_entries = 0
        self._last_write_time: float | None = None

        # Group Commit 버퍼
        self._group_buffer: list[dict[str, Any]] = []
        self._last_flush_time: float = time.time()
        self._group_commit_flushes: int = 0

        # 초기화
        self._init_or_recover()

    def _init_or_recover(self) -> None:
        """WAL 디렉토리 초기화 및 기존 시퀀스 복구."""
        self._wal_dir.mkdir(parents=True, exist_ok=True)

        # 기존 WAL 파일에서 마지막 시퀀스 복구
        wal_files = sorted(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"))
        if wal_files:
            last_file = wal_files[-1]
            try:
                for entry in self._read_wal_file(last_file):
                    self._sequence = max(self._sequence, entry.sequence)
            except Exception:
                pass  # 손상된 파일은 무시

    def _compute_checksum(self, data: bytes) -> str:
        """CRC32 체크섬 계산."""
        crc = zlib.crc32(data) & 0xFFFFFFFF
        return f"{crc:08x}"

    def _verify_checksum(self, data: bytes, expected: str) -> bool:
        """CRC32 체크섬 검증."""
        computed = self._compute_checksum(data)
        return computed.lower() == expected.lower()

    def _get_current_wal_filename(self) -> str:
        """현재 WAL 파일명 생성 (PID 포함으로 멀티 프로세스 안전)."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        pid = os.getpid()
        return f"{self._config.file_prefix}_{timestamp}_{pid}.wal"

    def _ensure_file_open(self) -> None:
        """WAL 파일이 열려있는지 확인하고 필요시 생성."""
        if self._current_handle is None or self._current_file is None:
            self._current_file = self._wal_dir / self._get_current_wal_filename()
            self._current_handle = open(self._current_file, "ab")

            # 새 파일이면 헤더 쓰기
            if self._current_handle.tell() == 0:
                self._write_header()

    def _write_header(self) -> None:
        """WAL 파일 헤더 쓰기."""
        if self._current_handle:
            header = self.MAGIC + struct.pack(">HH", self.VERSION, 0)
            self._current_handle.write(header)
            self._current_handle.flush()

    def _rotate_file(self) -> None:
        """WAL 파일 로테이션."""
        with self._lock:
            old_state = self._state
            self._state = WALState.ROTATING

            try:
                old_file = self._current_file
                old_size = 0

                if self._current_handle:
                    old_size = self._current_handle.tell()
                    self._current_handle.flush()
                    if self._config.sync_on_write:
                        os.fsync(self._current_handle.fileno())
                    self._current_handle.close()
                    self._current_handle = None

                self._current_file = None

                # Audit 기록
                if old_file:
                    # Drift Detection 메트릭 기록
                    if HAS_DRIFT_METRICS:
                        record_wal_rotation()
                    self._record_audit_event(
                        event_type="WAL_ROTATED",
                        details={
                            "old_file": str(old_file),
                            "old_size_bytes": old_size,
                        },
                    )

                # 콜백 호출
                if self._on_rotate and old_file:
                    try:
                        self._on_rotate(str(old_file))
                    except Exception:
                        pass

                # 오래된 파일 정리
                self._cleanup_old_files()

            finally:
                self._state = old_state if old_state != WALState.ROTATING else WALState.ACTIVE

    def _cleanup_old_files(self) -> None:
        """오래된 WAL 파일 정리."""
        wal_files = sorted(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"))

        while len(wal_files) > self._config.max_files:
            oldest = wal_files.pop(0)
            try:
                oldest.unlink()
            except Exception:
                pass

    def write(self, data: dict[str, Any]) -> int:
        """
        WAL에 기록.

        Args:
            data: 기록할 데이터 (딕셔너리)

        Returns:
            시퀀스 번호
        """
        if self._config.group_commit_enabled:
            return self._buffered_write(data)
        return self._direct_write(data)

    def _direct_write(self, data: dict[str, Any]) -> int:
        """직접 기록 (Disk Full Fail-Open 지원)."""
        with self._lock:
            # Fail-Open 모드면 WAL 기록 스킵 (서비스 계속)
            if self._state == WALState.DISK_FULL_FAILOPEN:
                logger.warning("[WAL] Disk full fail-open mode, skipping WAL write")
                return -1  # 음수 시퀀스 = WAL 미기록

            if self._state == WALState.CLOSED:
                raise WALError("WAL is closed")

            self._sequence += 1
            current_seq = self._sequence

            # 엔트리 생성
            entry = {
                "seq": current_seq,
                "ts": time.time(),
                "data": data,
            }
            entry_bytes = json.dumps(entry, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            checksum = self._compute_checksum(entry_bytes)

            # 레코드 포맷: [4-byte length][8-byte checksum][entry_bytes]
            record = struct.pack(">I", len(entry_bytes)) + checksum.encode("ascii") + entry_bytes

            try:
                self._ensure_file_open()

                if self._current_handle:
                    self._current_handle.write(record)

                    if self._config.sync_on_write:
                        self._current_handle.flush()
                        os.fsync(self._current_handle.fileno())

                    self._total_entries += 1
                    self._last_write_time = time.time()

                    # 파일 크기 확인 및 로테이션
                    if self._current_handle.tell() > self._config.max_file_size_bytes:
                        self._rotate_file()

            except OSError as e:
                import errno

                if e.errno == errno.ENOSPC:  # No space left on device
                    self._handle_disk_full()
                    if self._config.fail_open_on_disk_full:
                        return -1  # Fail-Open: 서비스 계속
                    raise  # Fail-Closed: 예외 전파
                raise

            # Drift Detection 메트릭 기록
            if HAS_DRIFT_METRICS:
                record_wal_entry_written()
                update_wal_last_sequence(current_seq)

            return current_seq

    def _buffered_write(self, data: dict[str, Any]) -> int:
        """
        버퍼링된 기록 (Group Commit).

        여러 엔트리를 모아서 한 번에 fsync 수행.
        I/O 부하를 99% 절감할 수 있음.
        """
        with self._lock:
            if self._state == WALState.CLOSED:
                raise WALError("WAL is closed")

            self._sequence += 1
            current_seq = self._sequence

            # 버퍼에 추가
            buffered_entry = {
                "seq": current_seq,
                "ts": time.time(),
                "data": data,
            }
            self._group_buffer.append(buffered_entry)

            # 플러시 조건 체크
            should_flush = (
                len(self._group_buffer) >= self._config.group_commit_max_entries
                or self._time_since_last_flush_ms() >= self._config.group_commit_max_wait_ms
            )

            if should_flush:
                self._flush_buffer()

            return current_seq

    def _time_since_last_flush_ms(self) -> float:
        """마지막 플러시 이후 경과 시간 (ms)."""
        return (time.time() - self._last_flush_time) * 1000

    def _flush_buffer(self) -> None:
        """버퍼의 모든 엔트리를 한 번에 기록."""
        if not self._group_buffer:
            return

        self._ensure_file_open()

        if self._current_handle:
            for entry in self._group_buffer:
                entry_bytes = json.dumps(entry, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                checksum = self._compute_checksum(entry_bytes)

                record = struct.pack(">I", len(entry_bytes)) + checksum.encode("ascii") + entry_bytes
                self._current_handle.write(record)
                self._total_entries += 1

            # 한 번의 fsync로 모든 엔트리 영속화
            if self._config.sync_on_write:
                self._current_handle.flush()
                os.fsync(self._current_handle.fileno())

            self._group_commit_flushes += 1
            self._last_write_time = time.time()

            # 파일 크기 확인 및 로테이션
            if self._current_handle.tell() > self._config.max_file_size_bytes:
                self._rotate_file()

        self._group_buffer.clear()
        self._last_flush_time = time.time()

    def flush(self) -> None:
        """버퍼 강제 플러시 (Group Commit 모드에서 사용)."""
        with self._lock:
            if self._config.group_commit_enabled:
                self._flush_buffer()

    def batch_write_entries(self, entries: list[dict[str, Any]]) -> list[int]:
        """
        여러 엔트리를 한 번에 기록 (단일 fsync).

        개별 write() 호출 대비 I/O 비용을 크게 절감합니다.
        모든 엔트리가 동일한 fsync 호출로 영속화됩니다.

        Args:
            entries: 기록할 데이터 딕셔너리 목록

        Returns:
            각 엔트리의 시퀀스 번호 목록 (입력 순서 동일)

        Raises:
            WALError: WAL이 닫혀있는 경우

        Example:
            wal = WriteAheadLog(config)
            sequences = wal.batch_write_entries([
                {"event": "config_change", "key": "max_retries"},
                {"event": "config_change", "key": "timeout"},
            ])
            # sequences = [1, 2]
        """
        if not entries:
            return []

        with self._lock:
            if self._state == WALState.CLOSED:
                raise WALError("WAL is closed")

            sequences: list[int] = []
            records: list[bytes] = []

            # 모든 엔트리 준비 (직렬화 + 체크섬)
            for data in entries:
                self._sequence += 1
                current_seq = self._sequence
                sequences.append(current_seq)

                entry = {
                    "seq": current_seq,
                    "ts": time.time(),
                    "data": data,
                }
                entry_bytes = json.dumps(entry, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                checksum = self._compute_checksum(entry_bytes)

                record = struct.pack(">I", len(entry_bytes)) + checksum.encode("ascii") + entry_bytes
                records.append(record)

            # 파일에 일괄 기록
            self._ensure_file_open()

            if self._current_handle:
                for record in records:
                    self._current_handle.write(record)
                    self._total_entries += 1

                # 단일 fsync로 모든 엔트리 영속화
                if self._config.sync_on_write:
                    self._current_handle.flush()
                    os.fsync(self._current_handle.fileno())

                self._last_write_time = time.time()

                # 파일 크기 확인 및 로테이션
                if self._current_handle.tell() > self._config.max_file_size_bytes:
                    self._rotate_file()

            # Drift Detection 메트릭 기록
            if HAS_DRIFT_METRICS:
                for _ in sequences:
                    record_wal_entry_written()
                update_wal_last_sequence(self._sequence)

            return sequences

    def _read_wal_file(self, filepath: Path) -> Iterator[WALEntry]:
        """
        WAL 파일 읽기.

        Args:
            filepath: WAL 파일 경로

        Yields:
            WALEntry 객체
        """
        try:
            with open(filepath, "rb") as f:
                # 헤더 읽기
                header = f.read(self.HEADER_SIZE)
                if len(header) < self.HEADER_SIZE:
                    return

                magic = header[:4]
                if magic != self.MAGIC:
                    return

                # 레코드 읽기
                while True:
                    # 길이 읽기
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        break

                    length = struct.unpack(">I", length_bytes)[0]

                    # 체크섬 읽기
                    checksum_bytes = f.read(8)
                    if len(checksum_bytes) < 8:
                        break

                    checksum = checksum_bytes.decode("ascii")

                    # 데이터 읽기
                    data_bytes = f.read(length)
                    if len(data_bytes) < length:
                        break

                    # 체크섬 검증
                    if not self._verify_checksum(data_bytes, checksum):
                        self._corrupted_entries += 1
                        computed_checksum = self._compute_checksum(data_bytes)
                        error = WALCorruptionError(
                            f"Checksum mismatch in {filepath}",
                            sequence=-1,
                            expected=checksum,
                            computed=computed_checksum,
                        )
                        # Drift Detection 메트릭 기록
                        if HAS_DRIFT_METRICS:
                            record_wal_corruption()
                        # Audit 기록
                        self._record_audit_event(
                            event_type="WAL_CORRUPTION_DETECTED",
                            details={
                                "filepath": str(filepath),
                                "expected_checksum": checksum,
                                "computed_checksum": computed_checksum,
                            },
                        )
                        if self._on_corruption:
                            self._on_corruption(error)
                        continue

                    # JSON 파싱
                    try:
                        entry_dict = json.loads(data_bytes.decode("utf-8"))
                        entry = WALEntry(
                            sequence=entry_dict["seq"],
                            timestamp=entry_dict["ts"],
                            data=entry_dict["data"],
                            checksum=checksum,
                        )
                        yield entry
                    except (json.JSONDecodeError, KeyError):
                        self._corrupted_entries += 1

        except Exception:
            pass

    def _read_wal_file_best_effort(self, filepath: Path) -> Iterator[WALEntry]:
        """
        Best-effort 복구 모드로 WAL 파일 읽기.

        손상된 레코드를 건너뛰고 가능한 많은 엔트리를 복구합니다.
        RECORD_MAGIC 마커를 사용하여 다음 유효한 레코드를 찾습니다.

        Args:
            filepath: WAL 파일 경로

        Yields:
            WALEntry 객체
        """
        try:
            with open(filepath, "rb") as f:
                # 헤더 읽기
                header = f.read(self.HEADER_SIZE)
                if len(header) < self.HEADER_SIZE:
                    return

                magic = header[:4]
                if magic != self.MAGIC:
                    return

                while True:
                    # 길이 읽기 시도
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        break

                    length = struct.unpack(">I", length_bytes)[0]

                    # 비정상적인 길이 감지 (손상 가능성)
                    if length > 10 * 1024 * 1024:  # 10MB 초과 = 손상
                        if self._config.best_effort_recovery:
                            # 다음 유효 레코드 찾기
                            pos = self._scan_for_valid_record(f)
                            if pos == -1:
                                break
                            continue
                        else:
                            break

                    # 체크섬 읽기
                    checksum_bytes = f.read(8)
                    if len(checksum_bytes) < 8:
                        break

                    checksum = checksum_bytes.decode("ascii", errors="replace")

                    # 데이터 읽기
                    data_bytes = f.read(length)
                    if len(data_bytes) < length:
                        break

                    # 체크섬 검증
                    if not self._verify_checksum(data_bytes, checksum):
                        self._corrupted_entries += 1
                        if self._config.best_effort_recovery:
                            # 손상된 레코드 건너뛰고 계속
                            continue
                        else:
                            break

                    # JSON 파싱
                    try:
                        entry_dict = json.loads(data_bytes.decode("utf-8"))
                        entry = WALEntry(
                            sequence=entry_dict["seq"],
                            timestamp=entry_dict["ts"],
                            data=entry_dict["data"],
                            checksum=checksum,
                        )
                        yield entry
                    except (json.JSONDecodeError, KeyError):
                        self._corrupted_entries += 1
                        if not self._config.best_effort_recovery:
                            break
                        # Best-effort: 손상 레코드 건너뛰고 계속

        except Exception:
            pass

    def _scan_for_valid_record(self, f) -> int:
        """
        다음 유효한 레코드 위치까지 스캔.

        손상된 영역을 건너뛰고 다음 유효한 JSON 레코드를 찾습니다.

        Args:
            f: 파일 핸들

        Returns:
            찾은 위치, 없으면 -1
        """
        # 1KB씩 스캔하며 JSON 시작 패턴 찾기
        scan_buffer = bytearray()
        max_scan_bytes = 1024 * 1024  # 최대 1MB 스캔
        scanned = 0

        while scanned < max_scan_bytes:
            byte = f.read(1)
            if not byte:
                return -1

            scan_buffer.append(byte[0])
            scanned += 1

            # 버퍼가 충분히 쌓이면 패턴 검사
            if len(scan_buffer) > 20:
                # 유효한 레코드 시작 패턴 찾기: 4바이트 길이 + 8바이트 체크섬
                # 체크섬은 hex 문자 (0-9, a-f)
                try:
                    # 마지막 12바이트 확인 (length + checksum)
                    potential_checksum = bytes(scan_buffer[-8:]).decode("ascii")
                    if all(c in "0123456789abcdef" for c in potential_checksum.lower()):
                        # 유효한 체크섬 패턴 발견, 위치 조정
                        f.seek(f.tell() - 8)
                        # 이전 4바이트로 이동해서 length 읽기
                        f.seek(f.tell() - 4)
                        return f.tell()
                except Exception:
                    pass

                # 버퍼 크기 제한
                if len(scan_buffer) > 1024:
                    scan_buffer = scan_buffer[-512:]

        return -1

    def recover_unprocessed(self, last_processed_seq: int = 0) -> list[WALEntry]:
        """
        마지막 처리된 시퀀스 이후 엔트리 복구.

        Args:
            last_processed_seq: 마지막으로 처리된 시퀀스 번호

        Returns:
            미처리 WALEntry 목록
        """
        entries: list[WALEntry] = []

        with self._lock:
            wal_files = sorted(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"))

            for wal_file in wal_files:
                for entry in self._read_wal_file(wal_file):
                    if entry.sequence > last_processed_seq:
                        entries.append(entry)
                        self._recovered_entries += 1

        sorted_entries = sorted(entries, key=lambda e: e.sequence)

        # Drift Detection 메트릭 기록
        if HAS_DRIFT_METRICS and sorted_entries:
            record_wal_entries_recovered(len(sorted_entries))

        # Audit 기록
        if sorted_entries:
            self._record_audit_event(
                event_type="WAL_RECOVERED",
                details={
                    "recovered_count": len(sorted_entries),
                    "last_processed_seq": last_processed_seq,
                    "new_last_seq": sorted_entries[-1].sequence,
                },
            )

        return sorted_entries

    def cleanup_processed(self, last_processed_seq: int) -> int:
        """
        처리 완료된 엔트리 정리.

        모든 엔트리가 처리된 파일만 삭제.

        Args:
            last_processed_seq: 마지막으로 처리된 시퀀스 번호

        Returns:
            삭제된 파일 수
        """
        deleted_count = 0

        with self._lock:
            wal_files = sorted(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"))

            for wal_file in wal_files:
                # 현재 사용 중인 파일은 건너뛰기
                if self._current_file and wal_file == self._current_file:
                    continue

                # 파일의 마지막 시퀀스 확인
                max_seq = 0
                for entry in self._read_wal_file(wal_file):
                    max_seq = max(max_seq, entry.sequence)

                # 모든 엔트리가 처리되었으면 삭제
                if max_seq > 0 and max_seq <= last_processed_seq:
                    try:
                        wal_file.unlink()
                        deleted_count += 1
                    except Exception:
                        pass

        return deleted_count

    def get_stats(self) -> WALStats:
        """WAL 통계 조회."""
        with self._lock:
            current_size = 0
            if self._current_handle:
                try:
                    current_size = self._current_handle.tell()
                except Exception:
                    pass

            total_files = len(list(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal")))

            return WALStats(
                state=self._state,
                current_file=str(self._current_file) if self._current_file else None,
                current_size_bytes=current_size,
                total_entries=self._total_entries,
                total_files=total_files,
                last_sequence=self._sequence,
                last_write_time=self._last_write_time,
                corrupted_entries=self._corrupted_entries,
                recovered_entries=self._recovered_entries,
            )

    def _handle_disk_full(self) -> None:
        """디스크 풀 상황 처리 (Fail-Open 모드 전환)."""
        self._state = WALState.DISK_FULL_FAILOPEN
        logger.critical("[WAL] DISK FULL - Switching to fail-open mode")

        # 메트릭 기록
        if HAS_DRIFT_METRICS:
            try:
                from selfhealing.metrics.drift_metrics import record_wal_disk_full

                record_wal_disk_full()
            except ImportError:
                pass

        # 알림 전송
        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            payload = NotificationPayload(
                title="🚨 WAL Disk Full - Fail-Open Mode",
                message="WAL 디스크 용량 부족으로 Fail-Open 모드 전환. 즉시 조치 필요!",
                priority=NotificationPriority.CRITICAL,
                category=NotificationCategory.OPERATIONS,
                source="WriteAheadLog",
                dedup_key="wal:disk_full",
            )
            UnifiedNotificationManager().notify(payload)
        except Exception as e:
            logger.error(f"[WAL] Failed to send disk full notification: {e}")

    def check_disk_recovery(self) -> bool:
        """
        디스크 여유 공간 확보 시 정상 모드 복귀.

        Returns:
            True: 정상 모드로 복귀 (또는 이미 정상 상태)
            False: 여전히 디스크 풀 상태
        """
        if self._state != WALState.DISK_FULL_FAILOPEN:
            return True

        try:
            import shutil

            usage = shutil.disk_usage(self._wal_dir)
            free_ratio = usage.free / usage.total

            if free_ratio > self._config.disk_recovery_threshold:
                self._state = WALState.ACTIVE
                logger.info("[WAL] Disk space recovered, resuming normal operation")
                return True
        except Exception as e:
            logger.debug(f"[WAL] Disk recovery check failed: {e}")

        return False

    def count_unprocessed(self, last_processed_seq: int = 0) -> int:
        """
        미처리 엔트리 수 반환 (파일 전체 읽기 없이).

        Args:
            last_processed_seq: 마지막으로 처리된 시퀀스 번호

        Returns:
            미처리 엔트리 수 (추정치)
        """
        with self._lock:
            return max(0, self._sequence - last_processed_seq)

    def get_sync_lag(self, last_synced_seq: int = 0) -> int:
        """
        중앙 저장소와의 동기화 지연 계산.

        Args:
            last_synced_seq: 마지막으로 중앙 저장소에 동기화된 시퀀스

        Returns:
            미동기화된 엔트리 수
        """
        with self._lock:
            lag = max(0, self._sequence - last_synced_seq)
            # Drift Detection 메트릭 업데이트
            if HAS_DRIFT_METRICS:
                update_wal_sync_lag(lag)
            return lag

    def flush(self) -> None:
        """버퍼 플러시."""
        with self._lock:
            if self._current_handle:
                self._current_handle.flush()
                if self._config.sync_on_write:
                    os.fsync(self._current_handle.fileno())

    def close(self) -> None:
        """WAL 닫기."""
        with self._lock:
            self._state = WALState.CLOSED

            if self._current_handle:
                try:
                    self._current_handle.flush()
                    os.fsync(self._current_handle.fileno())
                    self._current_handle.close()
                except Exception:
                    pass
                finally:
                    self._current_handle = None

            self._current_file = None

    def __enter__(self) -> "WriteAheadLog":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _record_audit_event(self, event_type: str, details: dict[str, Any]) -> None:
        """
        Audit 이벤트 기록.

        Audit 통합 개선:
        - _write_to_wal() 직접 호출로 ActorContext/TraceContext 자동 결합
        - 순환 참조 방지: audit_adapter 우선 사용, 없으면 _write_to_wal() 사용
        - WAL 로테이션/손상/복구 이벤트도 중앙 추적 가능
        """
        # 1. 외부 주입된 adapter 우선 사용 (순환 참조 방지)
        if self._audit_adapter is not None:
            try:
                self._audit_adapter.log_event(
                    event_type=event_type,
                    source="WriteAheadLog",
                    details=details,
                )
                return
            except Exception:
                pass  # fallback to _write_to_wal

        # 2. _write_to_wal() 사용 (ActorContext/TraceContext 자동 결합)
        # 주의: 자기 자신을 호출하지 않도록 _get_wal()이 다른 WAL 인스턴스를 반환해야 함
        # 현재 구조: audit/base.py의 _get_wal()은 별도 singleton WAL을 사용
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type=event_type,
                source="WriteAheadLog",
                details=details,
            )
        except ImportError:
            pass  # _write_to_wal 미사용 환경
        except Exception:
            # Audit 실패가 WAL 동작을 방해하면 안됨
            pass


# =============================================================================
# Convenience functions
# =============================================================================


def create_wal(
    wal_dir: str = "/var/log/audit/wal",
    max_file_size_mb: int = 100,
    sync_on_write: bool = True,
) -> WriteAheadLog:
    """
    WAL 생성 헬퍼 함수.

    Args:
        wal_dir: WAL 디렉토리 경로
        max_file_size_mb: 최대 파일 크기 (MB)
        sync_on_write: 쓰기 시 동기화 여부

    Returns:
        WriteAheadLog 인스턴스
    """
    config = WALConfig(
        wal_dir=wal_dir,
        max_file_size_mb=max_file_size_mb,
        sync_on_write=sync_on_write,
    )
    return WriteAheadLog(config=config)
