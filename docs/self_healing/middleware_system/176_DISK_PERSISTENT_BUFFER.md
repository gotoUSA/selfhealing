# 176. Disk-Persistent Memory Buffer 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md), [175_KAFKA_EVENT_BUS_IMPLEMENTATION.md](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md)
> **예상 소요**: 3-4일
> **예상 코드량**: ~500줄

---

## 0. 문서 목적

이 문서는 현재 **휘발성(volatile)** 상태인 Memory Buffer를 **Pod 재시작에도 데이터가 보존되는** Disk-Persistent Buffer로 교체하는 구현 가이드입니다.

**핵심 가치**: "Pod 재시작, 컨테이너 크래시에도 버퍼 데이터 0% 손실"

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 Memory Buffer 휘발성 문제

**파일**: `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/fallback.py`
**라인**: 284-305

```python
def _add_integrity_memory(self, entry: dict[str, Any]) -> dict[str, Any]:
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
            "volatile": True,  # ⚠️ Warning: will be lost on restart
        }
```

**문제점**: `volatile: True` - Pod 재시작 시 모든 데이터 손실

### 1.2 InMemoryAuditBuffer

**파일**: `packages/selfhealing-python/src/selfhealing/audit/resilience/buffer.py`
**라인**: 30-80

```python
class InMemoryAuditBuffer:
    """
    WAL 실패 시 메모리 폴백 버퍼.

    디스크 장애 시 중요 로그를 메모리에 임시 보관하고,
    시스템 정상화 시 파일로 플러시합니다.

    설계 원칙:
    - 최대 엔트리 수는 ResilientRecorderSettings에서 설정 (기본 10,000개)
    - FIFO: 용량 초과 시 가장 오래된 엔트리 삭제
    - 주기적 플러시 시도 (기본 30초 간격)

    Thread-safe: RLock 사용
    """

    def __init__(
        self,
        max_entries: int | None = None,
        flush_interval_seconds: float | None = None,
    ):
        self._buffer: list[dict[str, Any]] = []  # ⚠️ 순수 메모리
        self._buffer_lock = threading.RLock()
        # ...
```

**문제점**: `self._buffer: list[dict[str, Any]] = []` - 순수 Python 리스트로 메모리에만 존재

### 1.3 RingBuffer도 메모리 기반

**파일**: `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py`
**라인**: 90-100

```python
def __init__(
    self,
    capacity: int = 10000,
    strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
    # ...
):
    # ...
    self._buffer: deque = deque(maxlen=capacity)  # ⚠️ 메모리 기반
```

**문제점**: `collections.deque`는 순수 메모리 기반

---

## 2. 구현 목표

### 2.1 목표 아키텍처

```
현재:
┌────────────────────────────────────────────────┐
│              InMemoryAuditBuffer               │
│  ┌─────────────────────────────────────────┐   │
│  │     Python List (RAM Only)              │   │
│  │     ⚠️ Pod 재시작 = 전체 손실           │   │
│  └─────────────────────────────────────────┘   │
└────────────────────────────────────────────────┘

목표:
┌────────────────────────────────────────────────┐
│           DiskPersistentBuffer                 │
│  ┌─────────────────────────────────────────┐   │
│  │   Memory-Mapped File (/dev/shm)         │   │
│  │   또는 LMDB (Embedded Key-Value)        │   │
│  │   ✅ Pod 재시작에도 데이터 보존          │   │
│  └─────────────────────────────────────────┘   │
└────────────────────────────────────────────────┘
```

### 2.2 기술 선택지

| 옵션 | 장점 | 단점 | 권장 |
|------|------|------|------|
| **mmap** | 표준 라이브러리, 간단 | 구조 관리 복잡 | ⚠️ |
| **LMDB** | 초고속, ACID, Python 바인딩 | 외부 의존성 | ✅ **권장** |
| **SQLite** | 익숙함, SQL 지원 | mmap 대비 느림 | ⚠️ |
| **RocksDB** | 고성능, 대용량 | 복잡한 설정 | ❌ |

**결정**: **LMDB** (Lightning Memory-Mapped Database)
- 읽기 성능: 수백만 TPS
- 쓰기 성능: 수만 TPS
- ACID 트랜잭션 지원
- 파일 기반 (Pod 재시작에도 보존)
- `lmdb` Python 패키지로 간단히 사용 가능

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/audit/persistence/
├── __init__.py
├── config.py              # 설정
├── disk_buffer.py         # DiskPersistentBuffer (메인)
├── mmap_buffer.py         # mmap 기반 버퍼 (대안)
└── migration.py           # InMemoryBuffer → DiskBuffer 마이그레이션
```

### 3.2 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/audit/persistence/config.py
"""
Disk-Persistent Buffer 설정.

기존 코드 참조:
- settings/resilient_recorder.py: ResilientRecorderSettings 패턴
- audit/resilience/buffer.py: InMemoryAuditBuffer 설정
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class DiskBufferSettings(BaseSettings):
    """
    Disk-Persistent Buffer 설정.

    환경변수:
    - SELFHEALING_DISK_BUFFER_*
    """

    # 스토리지 설정
    storage_type: str = Field(
        default="lmdb",
        description="스토리지 유형: lmdb, mmap, sqlite",
    )

    data_dir: str = Field(
        default="/var/lib/selfhealing/buffer",
        description="데이터 디렉토리 경로",
    )

    # LMDB 설정
    lmdb_map_size_mb: int = Field(
        default=1024,
        description="LMDB 최대 데이터베이스 크기 (MB)",
    )
    lmdb_max_dbs: int = Field(
        default=10,
        description="LMDB 최대 데이터베이스 수",
    )

    # 버퍼 설정
    max_entries: int = Field(
        default=100000,
        description="최대 엔트리 수",
    )
    flush_batch_size: int = Field(
        default=1000,
        description="플러시 배치 크기",
    )

    # 정리 설정
    retention_hours: int = Field(
        default=72,
        description="데이터 보관 기간 (시간)",
    )
    cleanup_interval_seconds: float = Field(
        default=3600.0,
        description="정리 작업 주기 (초)",
    )

    # 무결성 설정
    enable_checksum: bool = Field(
        default=True,
        description="CRC32 체크섬 활성화",
    )
    sync_on_write: bool = Field(
        default=False,
        description="매 쓰기 시 fsync (성능 영향)",
    )

    class Config:
        env_prefix = "SELFHEALING_DISK_BUFFER_"
        env_file = ".env"

    @property
    def data_path(self) -> Path:
        """데이터 경로 반환."""
        return Path(self.data_dir)

    @property
    def lmdb_map_size_bytes(self) -> int:
        """LMDB 맵 크기 (바이트)."""
        return self.lmdb_map_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_disk_buffer_settings() -> DiskBufferSettings:
    """설정 싱글톤 반환."""
    return DiskBufferSettings()


def reset_disk_buffer_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_disk_buffer_settings.cache_clear()
```

### 3.3 DiskPersistentBuffer (disk_buffer.py)

```python
# packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer.py
"""
Disk-Persistent Buffer - LMDB 기반 영속 버퍼.

기존 InMemoryAuditBuffer를 대체하여 Pod 재시작에도 데이터 보존.

기존 코드 참조:
- audit/resilience/buffer.py: InMemoryAuditBuffer 인터페이스
- audit/wal.py: WAL 패턴 (CRC32 체크섬)
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selfhealing.audit.persistence.config import (
    DiskBufferSettings,
    get_disk_buffer_settings,
)

logger = logging.getLogger(__name__)


@dataclass
class BufferEntry:
    """버퍼 엔트리."""

    key: bytes
    """고유 키 (timestamp + sequence)."""

    data: dict[str, Any]
    """이벤트 데이터."""

    timestamp: float
    """저장 시각."""

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


class DiskPersistentBuffer:
    """
    LMDB 기반 Disk-Persistent Buffer.

    특징:
    - Pod 재시작에도 데이터 보존
    - ACID 트랜잭션
    - 초고속 읽기/쓰기
    - CRC32 체크섬으로 무결성 검증
    - 자동 정리 (retention 기반)

    기존 InMemoryAuditBuffer와 호환되는 인터페이스:
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

    def __init__(
        self,
        settings: DiskBufferSettings | None = None,
        db_name: str = "audit_buffer",
    ):
        """
        DiskPersistentBuffer 초기화.

        Args:
            settings: 버퍼 설정
            db_name: 데이터베이스 이름
        """
        self._settings = settings or get_disk_buffer_settings()
        self._db_name = db_name
        self._lock = threading.RLock()

        # LMDB 환경
        self._env = None
        self._entries_db = None
        self._meta_db = None

        # 시퀀스 번호
        self._sequence = 0

        # 통계
        self._stats = {
            "total_puts": 0,
            "total_gets": 0,
            "total_deletes": 0,
            "checksum_errors": 0,
        }

        self._init_storage()

    def _init_storage(self) -> None:
        """LMDB 스토리지 초기화."""
        try:
            import lmdb
        except ImportError:
            raise DiskBufferError(
                "lmdb not installed. Install with: pip install lmdb"
            )

        # 디렉토리 생성
        db_path = self._settings.data_path / self._db_name
        db_path.mkdir(parents=True, exist_ok=True)

        # LMDB 환경 열기
        self._env = lmdb.open(
            str(db_path),
            map_size=self._settings.lmdb_map_size_bytes,
            max_dbs=self._settings.lmdb_max_dbs,
            sync=self._settings.sync_on_write,
            writemap=True,  # 성능 향상
        )

        # 데이터베이스 열기
        self._entries_db = self._env.open_db(self.DB_ENTRIES, create=True)
        self._meta_db = self._env.open_db(self.DB_META, create=True)

        # 시퀀스 복구
        self._recover_sequence()

        logger.info(
            f"[DiskBuffer] Initialized: path={db_path}, "
            f"sequence={self._sequence}"
        )

    def _recover_sequence(self) -> None:
        """마지막 시퀀스 복구."""
        with self._env.begin(db=self._meta_db) as txn:
            value = txn.get(b"last_sequence")
            if value:
                self._sequence = struct.unpack(">Q", value)[0]

    def _save_sequence(self, txn) -> None:
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

    def put(self, entry: dict[str, Any]) -> bytes:
        """
        엔트리 저장.

        Args:
            entry: 이벤트 데이터

        Returns:
            저장된 키
        """
        with self._lock:
            key = self._generate_key()

            # 메타데이터 추가
            entry_with_meta = {
                **entry,
                "_stored_at": datetime.now(timezone.utc).isoformat(),
                "_buffer_key": key.decode(),
            }

            # JSON 직렬화
            data = json.dumps(entry_with_meta, default=str, ensure_ascii=False)
            data_bytes = data.encode("utf-8")

            # 체크섬 계산
            checksum = self._compute_checksum(data_bytes)

            # 값 형식: checksum(4) + data
            value = struct.pack(">I", checksum) + data_bytes

            # LMDB 트랜잭션으로 저장
            with self._env.begin(write=True, db=self._entries_db) as txn:
                txn.put(key, value)
                self._save_sequence(txn)

            self._stats["total_puts"] += 1

            logger.debug(f"[DiskBuffer] Put: key={key.decode()}")
            return key

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
                    logger.warning(
                        f"[DiskBuffer] Checksum mismatch: key={key.decode()}"
                    )
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
        with self._env.begin(db=self._entries_db) as txn:
            return txn.stat()["entries"]

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
                break

            if success:
                # 성공 시 삭제
                keys = [e.key for e in entries]
                deleted = self.delete_batch(keys)
                flushed += deleted

                logger.debug(f"[DiskBuffer] Flushed {deleted} entries")
            else:
                # 실패 시 중단
                logger.warning("[DiskBuffer] Flush handler returned False, stopping")
                break

        return flushed

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

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        with self._env.begin(db=self._entries_db) as txn:
            stat = txn.stat()

        return {
            **self._stats,
            "count": stat["entries"],
            "db_size_bytes": stat.get("psize", 0) * stat.get("leaf_pages", 0),
            "sequence": self._sequence,
        }

    def close(self) -> None:
        """버퍼 종료."""
        if self._env:
            self._env.close()
            self._env = None
            logger.info("[DiskBuffer] Closed")

    def __enter__(self) -> "DiskPersistentBuffer":
        """Context manager 진입."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager 종료."""
        self.close()


# =============================================================================
# Singleton
# =============================================================================

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


# =============================================================================
# InMemoryAuditBuffer 호환 어댑터
# =============================================================================


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

    _instance: "DiskBufferAdapter | None" = None
    _lock = threading.Lock()

    def __init__(self, disk_buffer: DiskPersistentBuffer | None = None):
        """초기화."""
        self._disk_buffer = disk_buffer or get_disk_buffer()
        self._total_buffered = 0
        self._total_dropped = 0

    @classmethod
    def get_instance(cls) -> "DiskBufferAdapter":
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
            True (항상 성공, 드롭 없음)
        """
        self._disk_buffer.put(entry)
        self._total_buffered += 1
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
```

---

## 4. mmap 기반 대안 구현 (mmap_buffer.py)

LMDB 의존성을 피하고 싶은 경우 mmap 기반 구현:

```python
# packages/selfhealing-python/src/selfhealing/audit/persistence/mmap_buffer.py
"""
mmap 기반 Disk-Persistent Buffer.

표준 라이브러리만 사용 (외부 의존성 없음).
단, LMDB 대비 기능이 제한적.
"""

from __future__ import annotations

import json
import logging
import mmap
import os
import struct
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MmapBuffer:
    """
    mmap 기반 간단한 영속 버퍼.

    구조:
    - Header (16 bytes): magic(4) + version(2) + entry_count(4) + write_pos(4) + reserved(2)
    - Entries: [length(4) + data(variable)] ...

    제한사항:
    - 고정 크기 파일
    - 삭제 없음 (덮어쓰기만)
    - 단순 순차 접근
    """

    MAGIC = b"MMBF"
    VERSION = 1
    HEADER_SIZE = 16
    DEFAULT_SIZE_MB = 100

    def __init__(
        self,
        file_path: str | Path = "/var/lib/selfhealing/mmap_buffer.dat",
        size_mb: int = DEFAULT_SIZE_MB,
    ):
        self._file_path = Path(file_path)
        self._size_bytes = size_mb * 1024 * 1024
        self._lock = threading.RLock()
        self._mmap = None
        self._file = None

        self._init_storage()

    def _init_storage(self) -> None:
        """스토리지 초기화."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        # 파일 생성 또는 열기
        if not self._file_path.exists():
            self._create_new_file()
        else:
            self._open_existing_file()

    def _create_new_file(self) -> None:
        """새 파일 생성."""
        with open(self._file_path, "wb") as f:
            # Header 쓰기
            header = struct.pack(
                ">4sHIIxx",  # magic(4) + version(2) + count(4) + pos(4) + reserved(2)
                self.MAGIC,
                self.VERSION,
                0,  # entry_count
                self.HEADER_SIZE,  # write_pos
            )
            f.write(header)
            # 나머지 공간 0으로 채우기
            f.write(b"\x00" * (self._size_bytes - self.HEADER_SIZE))

        self._open_existing_file()
        logger.info(f"[MmapBuffer] Created new file: {self._file_path}")

    def _open_existing_file(self) -> None:
        """기존 파일 열기."""
        self._file = open(self._file_path, "r+b")
        self._mmap = mmap.mmap(self._file.fileno(), 0)

        # Header 검증
        magic = self._mmap[:4]
        if magic != self.MAGIC:
            raise ValueError(f"Invalid magic: {magic}")

        logger.info(f"[MmapBuffer] Opened: {self._file_path}")

    def _read_header(self) -> tuple[int, int]:
        """헤더 읽기: (entry_count, write_pos)."""
        data = struct.unpack(">4sHIIxx", self._mmap[:self.HEADER_SIZE])
        return data[2], data[3]

    def _write_header(self, entry_count: int, write_pos: int) -> None:
        """헤더 쓰기."""
        header = struct.pack(
            ">4sHIIxx",
            self.MAGIC,
            self.VERSION,
            entry_count,
            write_pos,
        )
        self._mmap[:self.HEADER_SIZE] = header
        self._mmap.flush()

    def put(self, entry: dict[str, Any]) -> bool:
        """
        엔트리 저장.

        Args:
            entry: 이벤트 데이터

        Returns:
            저장 성공 여부
        """
        with self._lock:
            entry_count, write_pos = self._read_header()

            # JSON 직렬화
            data = json.dumps(entry, default=str, ensure_ascii=False).encode("utf-8")
            record_size = 4 + len(data)  # length(4) + data

            # 공간 확인
            if write_pos + record_size > self._size_bytes:
                logger.warning("[MmapBuffer] Buffer full, wrapping around")
                write_pos = self.HEADER_SIZE
                entry_count = 0

            # 레코드 쓰기
            self._mmap[write_pos:write_pos + 4] = struct.pack(">I", len(data))
            self._mmap[write_pos + 4:write_pos + record_size] = data

            # 헤더 업데이트
            self._write_header(entry_count + 1, write_pos + record_size)

            return True

    def iter_entries(self) -> list[dict[str, Any]]:
        """모든 엔트리 읽기."""
        entries = []
        with self._lock:
            entry_count, write_pos = self._read_header()
            pos = self.HEADER_SIZE

            while pos < write_pos:
                length = struct.unpack(">I", self._mmap[pos:pos + 4])[0]
                if length == 0:
                    break

                data = self._mmap[pos + 4:pos + 4 + length]
                try:
                    entry = json.loads(data.decode("utf-8"))
                    entries.append(entry)
                except json.JSONDecodeError:
                    pass

                pos += 4 + length

        return entries

    def clear(self) -> None:
        """버퍼 초기화."""
        with self._lock:
            self._write_header(0, self.HEADER_SIZE)

    def close(self) -> None:
        """버퍼 종료."""
        if self._mmap:
            self._mmap.close()
            self._mmap = None
        if self._file:
            self._file.close()
            self._file = None
```

---

## 5. 마이그레이션 가이드

### 5.1 기존 코드 수정

**파일**: `packages/selfhealing-python/src/selfhealing/audit/resilience/buffer.py`

```python
# 기존 InMemoryAuditBuffer를 DiskBufferAdapter로 대체하는 팩토리

def get_audit_buffer() -> InMemoryAuditBuffer | DiskBufferAdapter:
    """
    Audit Buffer 팩토리.

    환경변수로 구현 선택:
    - SELFHEALING_BUFFER_TYPE=memory (기본, 기존 동작)
    - SELFHEALING_BUFFER_TYPE=disk (신규, 영속 버퍼)
    """
    import os

    buffer_type = os.environ.get("SELFHEALING_BUFFER_TYPE", "memory")

    if buffer_type == "disk":
        from selfhealing.audit.persistence.disk_buffer import DiskBufferAdapter
        return DiskBufferAdapter.get_instance()
    else:
        return InMemoryAuditBuffer.get_instance()
```

### 5.2 Fallback Chain 수정

**파일**: `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/fallback.py`

Memory Buffer 대신 DiskPersistentBuffer 사용:

```python
# 수정 제안: _add_integrity_memory 메서드

def _add_integrity_memory(self, entry: dict[str, Any]) -> dict[str, Any]:
    """Add integrity using disk-persistent buffer (last resort)."""
    # DiskPersistentBuffer 사용
    from selfhealing.audit.persistence.disk_buffer import get_disk_buffer

    disk_buffer = get_disk_buffer()

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
            "tier": "disk_buffer",  # 변경: memory → disk_buffer
            "degraded": True,
            "degraded_reason": "all_persistent_storage_unavailable",
            "degraded_at": timestamp,
            "volatile": False,  # ✅ 변경: True → False
        }

        current_hash = self._compute_hash(entry)
        entry["integrity"]["current_hash"] = current_hash
        self._memory_previous_hash = current_hash

        # Disk Buffer에 저장
        disk_buffer.put(entry.copy())

        return entry
```

---

## 6. 설정 예시

### 6.1 환경변수

```bash
# .env 또는 시스템 환경변수

# 버퍼 유형 선택
SELFHEALING_BUFFER_TYPE=disk

# Disk Buffer 설정
SELFHEALING_DISK_BUFFER_STORAGE_TYPE=lmdb
SELFHEALING_DISK_BUFFER_DATA_DIR=/var/lib/selfhealing/buffer
SELFHEALING_DISK_BUFFER_LMDB_MAP_SIZE_MB=1024
SELFHEALING_DISK_BUFFER_MAX_ENTRIES=100000
SELFHEALING_DISK_BUFFER_RETENTION_HOURS=72
SELFHEALING_DISK_BUFFER_ENABLE_CHECKSUM=true
```

### 6.2 Kubernetes Volume Mount

```yaml
# k8s/deployment.yaml

apiVersion: apps/v1
kind: Deployment
metadata:
  name: selfhealing-worker
spec:
  template:
    spec:
      containers:
        - name: worker
          volumeMounts:
            - name: buffer-storage
              mountPath: /var/lib/selfhealing/buffer
      volumes:
        - name: buffer-storage
          persistentVolumeClaim:
            claimName: selfhealing-buffer-pvc
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: selfhealing-buffer-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
  storageClassName: fast-ssd
```

---

## 7. pyproject.toml 의존성

```toml
# packages/selfhealing-python/pyproject.toml

[project.optional-dependencies]
# LMDB 기반 영속 버퍼
disk-buffer = [
    "lmdb>=1.4.0",
]

# 전체 설치
all = [
    "selfhealing[kafka]",
    "selfhealing[disk-buffer]",
]
```

---

## 8. 테스트 계획

### 8.1 단위 테스트

```python
# tests/unit/audit/persistence/test_disk_buffer.py

class TestDiskPersistentBuffer:
    """DiskPersistentBuffer 단위 테스트."""

    def test_put_and_get(self, temp_db_path):
        """저장 및 조회 테스트."""
        pass

    def test_checksum_verification(self, temp_db_path):
        """체크섬 검증 테스트."""
        pass

    def test_persistence_across_restart(self, temp_db_path):
        """재시작 후 데이터 보존 테스트."""
        pass

    def test_cleanup_old_entries(self, temp_db_path):
        """오래된 엔트리 정리 테스트."""
        pass

    def test_flush_to_handler(self, temp_db_path):
        """핸들러로 플러시 테스트."""
        pass
```

### 8.2 통합 테스트

```python
# tests/integration/audit/test_disk_buffer_integration.py

@pytest.mark.integration
class TestDiskBufferIntegration:
    """Disk Buffer 통합 테스트."""

    def test_fallback_chain_with_disk_buffer(self):
        """Fallback Chain에서 Disk Buffer 사용 테스트."""
        pass

    def test_pod_restart_data_preservation(self):
        """Pod 재시작 시 데이터 보존 테스트."""
        pass
```

---

## 9. 구현 체크리스트

- [ ] `audit/persistence/__init__.py` 생성
- [ ] `audit/persistence/config.py` 구현
- [ ] `audit/persistence/disk_buffer.py` 구현
- [ ] `audit/persistence/mmap_buffer.py` 구현 (대안)
- [ ] `pyproject.toml`에 `lmdb` 의존성 추가
- [ ] `fallback.py` 수정 (Memory → Disk Buffer)
- [ ] `buffer.py`에 팩토리 함수 추가
- [ ] Kubernetes PVC 매니페스트 작성
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성

---

## 10. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [175_KAFKA_EVENT_BUS_IMPLEMENTATION.md](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md) - Kafka 구현
- [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md) - RingBuffer 통합

---

## 11. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
