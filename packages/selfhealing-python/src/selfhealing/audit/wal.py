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
import os
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple


class WALState(Enum):
    """WAL 상태."""
    ACTIVE = "active"
    ROTATING = "rotating"
    CLOSED = "closed"
    CORRUPTED = "corrupted"


@dataclass
class WALEntry:
    """WAL 엔트리."""
    sequence: int
    timestamp: float
    data: Dict[str, Any]
    checksum: str
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "seq": self.sequence,
            "ts": self.timestamp,
            "data": self.data,
            "checksum": self.checksum,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WALEntry":
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
    
    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@dataclass
class WALStats:
    """WAL 통계."""
    state: WALState
    current_file: Optional[str]
    current_size_bytes: int
    total_entries: int
    total_files: int
    last_sequence: int
    last_write_time: Optional[float]
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
    """
    
    # 파일 포맷 상수
    MAGIC = b'AWAL'  # Audit WAL
    VERSION = 1
    HEADER_SIZE = 8  # MAGIC(4) + VERSION(2) + reserved(2)
    RECORD_HEADER_SIZE = 12  # length(4) + checksum(8)
    
    def __init__(
        self,
        config: Optional[WALConfig] = None,
        on_rotate: Optional[Callable[[str], None]] = None,
        on_corruption: Optional[Callable[[WALCorruptionError], None]] = None,
    ):
        """
        WAL 초기화.
        
        Args:
            config: WAL 설정
            on_rotate: 파일 로테이션 시 콜백
            on_corruption: 손상 발견 시 콜백
        """
        self._config = config or WALConfig()
        self._on_rotate = on_rotate
        self._on_corruption = on_corruption
        
        self._wal_dir = Path(self._config.wal_dir)
        self._current_file: Optional[Path] = None
        self._current_handle: Optional[Any] = None
        self._sequence = 0
        self._state = WALState.ACTIVE
        self._lock = threading.RLock()
        
        # 통계
        self._total_entries = 0
        self._corrupted_entries = 0
        self._recovered_entries = 0
        self._last_write_time: Optional[float] = None
        
        # Group Commit 버퍼
        self._group_buffer: List[Dict[str, Any]] = []
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
        """현재 WAL 파일명 생성."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"{self._config.file_prefix}_{timestamp}.wal"
    
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
                
                if self._current_handle:
                    self._current_handle.flush()
                    if self._config.sync_on_write:
                        os.fsync(self._current_handle.fileno())
                    self._current_handle.close()
                    self._current_handle = None
                
                self._current_file = None
                
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
    
    def write(self, data: Dict[str, Any]) -> int:
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
    
    def _direct_write(self, data: Dict[str, Any]) -> int:
        """직접 기록 (기존 로직)."""
        with self._lock:
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
            record = (
                struct.pack(">I", len(entry_bytes)) +
                checksum.encode("ascii") +
                entry_bytes
            )
            
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
            
            return current_seq
    
    def _buffered_write(self, data: Dict[str, Any]) -> int:
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
                
                record = (
                    struct.pack(">I", len(entry_bytes)) +
                    checksum.encode("ascii") +
                    entry_bytes
                )
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
                        error = WALCorruptionError(
                            f"Checksum mismatch in {filepath}",
                            sequence=-1,
                            expected=checksum,
                            computed=self._compute_checksum(data_bytes),
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
    
    def recover_unprocessed(self, last_processed_seq: int = 0) -> List[WALEntry]:
        """
        마지막 처리된 시퀀스 이후 엔트리 복구.
        
        Args:
            last_processed_seq: 마지막으로 처리된 시퀀스 번호
            
        Returns:
            미처리 WALEntry 목록
        """
        entries: List[WALEntry] = []
        
        with self._lock:
            wal_files = sorted(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"))
            
            for wal_file in wal_files:
                for entry in self._read_wal_file(wal_file):
                    if entry.sequence > last_processed_seq:
                        entries.append(entry)
                        self._recovered_entries += 1
        
        return sorted(entries, key=lambda e: e.sequence)
    
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
