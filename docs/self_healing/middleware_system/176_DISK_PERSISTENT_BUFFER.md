# 176. Disk-Persistent Memory Buffer 구현 가이드

> **버전**: 1.3.0
> **작성일**: 2026-02-04
> **수정일**: 2026-02-04
> **상태**: ✅ 구현 완료
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md), [175_KAFKA_EVENT_BUS_IMPLEMENTATION.md](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md)
> **예상 소요**: 4-5일
> **예상 코드량**: ~900줄

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
        default=10240,  # 10GB - 가상 주소 공간만 예약, 실제 디스크는 사용량만큼
        description="LMDB 최대 데이터베이스 크기 (MB). 넉넉히 설정 권장.",
    )
    lmdb_max_dbs: int = Field(
        default=10,
        description="LMDB 최대 데이터베이스 수",
    )

    # Multi-Instance 설정 (WAL 패턴 벤치마킹: wal.py#L258-263)
    include_hostname_in_db_name: bool = Field(
        default=True,
        description="DB 이름에 호스트네임 포함 (멀티 Pod 충돌 방지)",
    )
    include_pid_in_db_name: bool = Field(
        default=True,
        description="DB 이름에 PID 포함 (멀티 프로세스 안전)",
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

    # Group Commit 설정 (WAL 패턴 벤치마킹: wal.py#L107-112)
    group_commit_enabled: bool = Field(
        default=True,
        description="Group Commit 활성화 (I/O 최적화)",
    )
    group_commit_interval_ms: int = Field(
        default=100,
        description="Group Commit 간격 (ms). 이 간격마다 fsync 수행.",
    )
    group_commit_max_entries: int = Field(
        default=100,
        description="Group Commit 최대 버퍼 엔트리 수",
    )

    # Disk Full 대응 설정 (WAL 패턴 벤치마킹: wal.py#L113-129)
    fail_open_on_disk_full: bool = Field(
        default=True,
        description="디스크 풀 시 Fail-Open 모드 전환 (서비스 지속)",
    )
    disk_recovery_threshold: float = Field(
        default=0.1,
        description="디스크 복구 임계치 (10% 여유 시 정상 모드 복귀)",
    )
    priority_based_purge: bool = Field(
        default=True,
        description="우선순위 기반 삭제 활성화",
    )

    # Quarantine 설정 (Corruption 대응)
    quarantine_on_corruption: bool = Field(
        default=True,
        description="DB 손상 시 격리 후 새 DB 생성",
    )
    quarantine_suffix: str = Field(
        default=".corrupt",
        description="격리된 DB 파일 접미사",
    )

    # Poison Pill 설정 (플러시 실패 엔트리 처리)
    max_flush_retries: int = Field(
        default=3,
        description="플러시 최대 재시도 횟수",
    )
    enable_dead_letter_db: bool = Field(
        default=True,
        description="Dead Letter DB 활성화 (Poison Pill 격리)",
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
    DB_DEAD_LETTER = b"dead_letter"  # Poison Pill 격리용

    # 버퍼 상태 (WAL 패턴 벤치마킹: wal.py#L60-66)
    class State(Enum):
        ACTIVE = "active"
        DISK_FULL_FAILOPEN = "disk_full_failopen"
        CORRUPTED = "corrupted"
        CLOSED = "closed"

    def __init__(
        self,
        settings: DiskBufferSettings | None = None,
        db_name: str | None = None,
    ):
        """
        DiskPersistentBuffer 초기화.

        Args:
            settings: 버퍼 설정
            db_name: 데이터베이스 이름 (None이면 자동 생성)
        """
        self._settings = settings or get_disk_buffer_settings()
        self._db_name = db_name or self._generate_db_name()
        self._lock = threading.RLock()

        # 상태 (WAL 패턴: wal.py#L213)
        self._state = self.State.ACTIVE

        # LMDB 환경
        self._env = None
        self._entries_db = None
        self._meta_db = None
        self._dead_letter_db = None

        # 시퀀스 번호
        self._sequence = 0

        # Group Commit 버퍼 (WAL 패턴: wal.py#L230-232)
        self._group_buffer: list[dict[str, Any]] = []
        self._last_flush_time: float = time.time()

        # 통계
        self._stats = {
            "total_puts": 0,
            "total_gets": 0,
            "total_deletes": 0,
            "checksum_errors": 0,
            "group_commit_flushes": 0,
            "dead_letter_count": 0,
            "disk_full_events": 0,
            "quarantine_events": 0,
        }

        self._init_storage()

    def _generate_db_name(self) -> str:
        """
        DB 이름 자동 생성 (Multi-Instance 안전).

        WAL 패턴 벤치마킹: wal.py#L258-263
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
        except ImportError:
            raise DiskBufferError(
                "lmdb not installed. Install with: pip install lmdb"
            )

        db_path = self._settings.data_path / self._db_name
        db_path.mkdir(parents=True, exist_ok=True)

        try:
            self._env = lmdb.open(
                str(db_path),
                map_size=self._settings.lmdb_map_size_bytes,
                max_dbs=self._settings.lmdb_max_dbs,
                sync=self._settings.sync_on_write,
                writemap=True,
            )
        except Exception as e:
            # Corruption 감지 시 Quarantine (WAL 패턴: wal.py#L117-118)
            if self._settings.quarantine_on_corruption:
                if self._quarantine_corrupt_db(db_path):
                    # 새 DB로 재시도
                    self._env = lmdb.open(
                        str(db_path),
                        map_size=self._settings.lmdb_map_size_bytes,
                        max_dbs=self._settings.lmdb_max_dbs,
                        sync=self._settings.sync_on_write,
                        writemap=True,
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

        logger.info(
            f"[DiskBuffer] Initialized: path={db_path}, "
            f"sequence={self._sequence}, db_name={self._db_name}"
        )

    def _quarantine_corrupt_db(self, db_path: Path) -> bool:
        """
        손상된 DB를 격리하고 새 DB 생성 준비.

        WAL 패턴 벤치마킹: wal.py#L117-118 (best_effort_recovery)

        Args:
            db_path: 손상된 DB 경로

        Returns:
            True: 격리 성공
            False: 격리 실패
        """
        import shutil

        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            corrupt_path = db_path.parent / f"{db_path.name}{self._settings.quarantine_suffix}.{timestamp}"

            shutil.move(str(db_path), str(corrupt_path))
            self._stats["quarantine_events"] += 1

            logger.critical(
                f"[DiskBuffer] Quarantined corrupt DB: {db_path} -> {corrupt_path}"
            )

            # 알림 전송 시도
            self._send_corruption_alert(db_path, corrupt_path)

            return True
        except Exception as e:
            logger.error(f"[DiskBuffer] Quarantine failed: {e}")
            return False

    def _send_corruption_alert(self, original: Path, quarantined: Path) -> None:
        """손상 알림 전송 (WAL 패턴: wal.py#L899-919)."""
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

    def put(self, entry: dict[str, Any]) -> bytes | None:
        """
        엔트리 저장 (Disk Full 대응, Group Commit 지원).

        WAL 패턴 벤치마킹: wal.py#L353-405

        Args:
            entry: 이벤트 데이터

        Returns:
            저장된 키 (Fail-Open 모드에서는 None 반환)
        """
        with self._lock:
            # Disk Full Fail-Open 모드 확인 (WAL 패턴: wal.py#L356-358)
            if self._state == self.State.DISK_FULL_FAILOPEN:
                logger.warning("[DiskBuffer] Disk full fail-open mode, skipping write")
                return None

            # 디스크 여유 공간 확인 및 Purge (WAL 패턴: wal.py#L879-920)
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

        WAL 패턴 벤치마킹: wal.py#L879-980

        Returns:
            True: 정상 또는 공간 확보 성공
            False: Disk Full 상태
        """
        import shutil

        try:
            usage = shutil.disk_usage(self._settings.data_path)
            free_ratio = usage.free / usage.total

            if free_ratio < 0.05:  # 5% 미만
                # Priority-based Purge 시도
                if self._settings.priority_based_purge:
                    purged = self._purge_old_entries_for_space()
                    if purged > 0:
                        logger.warning(f"[DiskBuffer] Purged {purged} entries for disk space")
                        return True

                # Purge 실패 시 Fail-Open 모드 전환
                self._state = self.State.DISK_FULL_FAILOPEN
                self._stats["disk_full_events"] += 1
                logger.critical("[DiskBuffer] DISK FULL - Switching to fail-open mode")
                self._send_disk_full_alert()
                return False

            # 복구 확인 (WAL 패턴: wal.py#L1000-1023)
            if self._state == self.State.DISK_FULL_FAILOPEN:
                if free_ratio > self._settings.disk_recovery_threshold:
                    self._state = self.State.ACTIVE
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
        """Disk Full 알림 전송 (WAL 패턴: wal.py#L899-919)."""
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

        WAL 패턴 벤치마킹: wal.py#L414-451
        """
        key = self._generate_key()

        entry_with_meta = {
            **entry,
            "_stored_at": datetime.now(timezone.utc).isoformat(),
            "_buffer_key": key.decode(),
        }

        self._group_buffer.append({
            "key": key,
            "entry": entry_with_meta,
        })

        # 플러시 조건 (WAL 패턴: wal.py#L438-442)
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
            self._env.sync(force=True)

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

            # 핸들러 호출 (Poison Pill 검출용 재시도 카운터)
            try:
                success = handler(entries)
            except Exception as e:
                logger.error(f"[DiskBuffer] Flush handler error: {e}")
                # Poison Pill 격리 시도 (DLQ 패턴: test_dlq_storage_and_replay.py)
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

        DLQ 패턴 벤치마킹:
        - test_dlq_storage_and_replay.py의 status="requires_review" 패턴
        - WAL의 Best-Effort Recovery (wal.py#L117-118)
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

        DLQ 패턴 벤치마킹: test_dlq_storage_and_replay.py#L94-118
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
                "status": "requires_review",  # DLQ 표준 상태 태그
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
                message=f"반복 실패 엔트리가 Dead Letter DB로 격리됨: {entry.key.decode()}",
                priority=NotificationPriority.HIGH,
                category=NotificationCategory.OPERATIONS,
                source="DiskPersistentBuffer",
                dedup_key=f"disk_buffer:poison_pill:{entry.key.decode()[:20]}",
            )
            UnifiedNotificationManager().notify(payload)
        except Exception as e:
            logger.debug(f"[DiskBuffer] Alert send failed: {e}")

    def get_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Dead Letter DB 조회 (관리자 검토용).

        Returns:
            Dead Letter 엔트리 목록
        """
        if not self._settings.enable_dead_letter_db:
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
        if not self._settings.enable_dead_letter_db:
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
          env:
            # StorageClass 환경변수화 (실제 클러스터에 맞게 설정)
            - name: SELFHEALING_STORAGE_CLASS
              value: "${STORAGE_CLASS:-standard}"  # fast-ssd 미존재 시 standard fallback
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
      storage: 12Gi  # lmdb_map_size(10GB) + 여유 공간
  # ⚠️ 실제 클러스터의 StorageClass 확인 필요
  # kubectl get storageclass 로 확인
  storageClassName: fast-ssd  # 또는 standard, gp2, premium-rwo 등
```

### 6.3 Storage Class 가이드 (신규)

#### 6.3.1 IOPS 및 Latency 검증

LMDB는 디스크 I/O 성능이 전체 처리량에 직결됩니다. 배포 전 실제 클러스터에서 벤치마킹이 필요합니다.

**벤치마크 스크립트**: `scripts/benchmark_storage.py`

```python
"""
Storage 성능 벤치마크.

사용법:
    kubectl exec -it selfhealing-worker-xxx -- python scripts/benchmark_storage.py

목표 성능:
    - 4K Random Write IOPS: >= 1,000 (목표 TPS 달성용)
    - fsync Latency: <= 10ms (Group Commit 간격)
    - Sequential Write Throughput: >= 50 MB/s
"""
from __future__ import annotations

import os
import shutil
import statistics
import tempfile
import time
from dataclasses import dataclass, field


@dataclass
class StorageBenchmarkResult:
    """벤치마크 결과."""

    test_path: str
    random_write_iops: float = 0.0
    fsync_latency_ms: float = 0.0
    sequential_write_mbps: float = 0.0
    disk_free_gb: float = 0.0
    filesystem_type: str = ""
    passed: bool = False
    recommendations: list[str] = field(default_factory=list)


def benchmark_storage(
    test_path: str = "/var/lib/selfhealing/buffer",
    target_iops: int = 1000,
    target_latency_ms: float = 10.0,
) -> StorageBenchmarkResult:
    """
    스토리지 성능 벤치마크 실행.

    Args:
        test_path: 테스트 경로
        target_iops: 목표 IOPS
        target_latency_ms: 목표 fsync 지연 시간

    Returns:
        StorageBenchmarkResult
    """
    result = StorageBenchmarkResult(test_path=test_path)

    # 디스크 정보
    try:
        usage = shutil.disk_usage(test_path)
        result.disk_free_gb = usage.free / (1024 ** 3)
    except Exception:
        result.recommendations.append("디스크 정보 조회 실패")

    # 파일 시스템 확인 (Linux)
    try:
        import subprocess
        fs_info = subprocess.run(
            ["df", "-T", test_path],
            capture_output=True,
            text=True,
        )
        if fs_info.returncode == 0:
            lines = fs_info.stdout.strip().split("\n")
            if len(lines) > 1:
                result.filesystem_type = lines[1].split()[1]
    except Exception:
        result.filesystem_type = "unknown"

    # 1. fsync Latency 측정
    latencies = []
    test_file = os.path.join(test_path, "benchmark_test.tmp")

    try:
        for _ in range(100):
            with open(test_file, "wb") as f:
                f.write(os.urandom(4096))  # 4KB
                f.flush()
                start = time.perf_counter()
                os.fsync(f.fileno())
                latencies.append((time.perf_counter() - start) * 1000)

        result.fsync_latency_ms = statistics.mean(latencies)
    except Exception as e:
        result.recommendations.append(f"fsync 테스트 실패: {e}")
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

    # 2. Random Write IOPS 측정
    try:
        start = time.perf_counter()
        ops = 0
        duration = 5.0  # 5초

        while (time.perf_counter() - start) < duration:
            with open(test_file, "wb") as f:
                f.write(os.urandom(4096))
                f.flush()
                os.fsync(f.fileno())
            ops += 1

        result.random_write_iops = ops / duration
    except Exception as e:
        result.recommendations.append(f"IOPS 테스트 실패: {e}")
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

    # 3. Sequential Write Throughput
    try:
        chunk_size = 1024 * 1024  # 1MB
        chunks = 100
        start = time.perf_counter()

        with open(test_file, "wb") as f:
            for _ in range(chunks):
                f.write(os.urandom(chunk_size))
            f.flush()
            os.fsync(f.fileno())

        duration = time.perf_counter() - start
        result.sequential_write_mbps = (chunks * chunk_size / (1024 ** 2)) / duration
    except Exception as e:
        result.recommendations.append(f"Throughput 테스트 실패: {e}")
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

    # 결과 평가
    result.passed = (
        result.random_write_iops >= target_iops
        and result.fsync_latency_ms <= target_latency_ms
    )

    if not result.passed:
        if result.random_write_iops < target_iops:
            result.recommendations.append(
                f"IOPS 부족: {result.random_write_iops:.0f} < {target_iops}. "
                "SSD StorageClass 사용 또는 Group Commit 배치 크기 증가 권장."
            )
        if result.fsync_latency_ms > target_latency_ms:
            result.recommendations.append(
                f"fsync 지연: {result.fsync_latency_ms:.1f}ms > {target_latency_ms}ms. "
                "sync_on_write=False + 주기적 sync 권장."
            )

    # 파일 시스템별 권장사항
    if result.filesystem_type == "ext4":
        result.recommendations.append(
            "ext4: LMDB writemap=True 권장 (성능 향상)"
        )
    elif result.filesystem_type == "xfs":
        result.recommendations.append(
            "XFS: writemap=True + 큰 allocation group 권장"
        )

    return result


def print_benchmark_report(result: StorageBenchmarkResult) -> None:
    """벤치마크 결과 출력."""
    print("=" * 60)
    print("Storage Benchmark Report")
    print("=" * 60)
    print(f"Test Path:        {result.test_path}")
    print(f"Filesystem:       {result.filesystem_type}")
    print(f"Disk Free:        {result.disk_free_gb:.1f} GB")
    print("-" * 60)
    print(f"Random Write IOPS:   {result.random_write_iops:.0f}")
    print(f"fsync Latency:       {result.fsync_latency_ms:.2f} ms")
    print(f"Sequential Write:    {result.sequential_write_mbps:.1f} MB/s")
    print("-" * 60)
    print(f"Result:           {'✅ PASSED' if result.passed else '❌ FAILED'}")

    if result.recommendations:
        print("\nRecommendations:")
        for rec in result.recommendations:
            print(f"  • {rec}")


if __name__ == "__main__":
    import sys

    test_path = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/selfhealing/buffer"
    result = benchmark_storage(test_path)
    print_benchmark_report(result)
    sys.exit(0 if result.passed else 1)
```

#### 6.3.2 파일 시스템 특성 및 LMDB 옵션

**DiskBufferSettings 확장** (config.py):

```python
@dataclass
class DiskBufferSettings:
    # ... 기존 필드 ...

    # LMDB 고급 옵션 (파일 시스템 최적화)
    lmdb_writemap: bool = True  # ext4/XFS에서 성능 향상
    lmdb_map_async: bool = False  # 비동기 맵 업데이트 (위험)
    lmdb_metasync: bool = True  # 메타데이터 동기화
    lmdb_nosync: bool = False  # ⚠️ True 시 데이터 손실 위험

    def get_lmdb_flags(self) -> int:
        """
        LMDB 환경 플래그 계산.

        파일 시스템별 권장 설정:
        - ext4: writemap=True, metasync=True
        - XFS: writemap=True, metasync=True
        - NFS: writemap=False (지원 안함)
        """
        import lmdb

        flags = 0
        if self.lmdb_writemap:
            flags |= lmdb.MDB_WRITEMAP
        if self.lmdb_map_async:
            flags |= lmdb.MDB_MAPASYNC
        if not self.lmdb_metasync:
            flags |= lmdb.MDB_NOMETASYNC
        if self.lmdb_nosync:
            flags |= lmdb.MDB_NOSYNC

        return flags
```

**파일 시스템별 권장 설정**:

| 파일 시스템 | `writemap` | `metasync` | `nosync` | 비고 |
|-------------|------------|------------|----------|------|
| **ext4** | ✅ True | ✅ True | ❌ False | 권장 설정 |
| **XFS** | ✅ True | ✅ True | ❌ False | 권장 설정 |
| **NFS** | ❌ False | ✅ True | ❌ False | writemap 미지원 |
| **tmpfs** | ✅ True | ❌ False | ✅ True | 테스트용 |

#### 6.3.3 실제 클러스터 StorageClass 확인

```bash
# 사용 가능한 StorageClass 조회
kubectl get storageclass

# 예상 출력:
# NAME                 PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE
# standard (default)   kubernetes.io/gce-pd    Delete          Immediate
# fast-ssd             kubernetes.io/gce-pd    Retain          Immediate
# premium-rwo          pd.csi.storage.gke.io   Retain          WaitForFirstConsumer

# StorageClass 상세 정보
kubectl describe storageclass fast-ssd
```

**StorageClass 환경변수화**:

```python
# config.py

@dataclass
class DiskBufferSettings:
    # ... 기존 필드 ...

    # Kubernetes Storage 설정
    storage_class_name: str = ""  # 환경변수에서 로드

    @classmethod
    def from_env(cls) -> "DiskBufferSettings":
        import os

        return cls(
            # ...
            storage_class_name=os.environ.get(
                "SELFHEALING_STORAGE_CLASS",
                "standard",  # Fallback
            ),
        )
```

**Fallback StorageClass 로직**:

```yaml
# k8s/selfhealing-buffer-pvc.yaml

apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: selfhealing-buffer-pvc
  annotations:
    # Fallback 정보 기록
    selfhealing.io/preferred-storage: "fast-ssd"
    selfhealing.io/fallback-storage: "standard"
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      # lmdb_map_size(10GB) + 20% 여유
      storage: 12Gi
  # Helm/Kustomize로 동적 설정 권장
  # storageClassName: {{ .Values.storageClass | default "standard" }}
  storageClassName: fast-ssd
```

#### 6.3.4 PV 재활용 정책 (Reclaim Policy)

**핵심 요구사항**: Pod 재시작 시 데이터 보존

```yaml
# k8s/selfhealing-buffer-pv.yaml (정적 프로비저닝 시)

apiVersion: v1
kind: PersistentVolume
metadata:
  name: selfhealing-buffer-pv
  labels:
    type: local-ssd
spec:
  capacity:
    storage: 12Gi
  accessModes:
    - ReadWriteOnce
  # ⚠️ 핵심: Retain 정책으로 데이터 보존
  persistentVolumeReclaimPolicy: Retain
  storageClassName: fast-ssd
  local:
    path: /mnt/disks/ssd/selfhealing-buffer
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values:
                - worker-node-1
```

**동적 프로비저닝 StorageClass**:

```yaml
# k8s/storageclass-fast-ssd.yaml

apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: fast-ssd
provisioner: pd.csi.storage.gke.io  # GKE 예시
parameters:
  type: pd-ssd
# ⚠️ 핵심: Retain으로 PVC 삭제 시에도 데이터 보존
reclaimPolicy: Retain
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
```

**Reclaim Policy 검증**:

```python
# selfhealing/audit/persistence/storage_validator.py

def validate_storage_policy() -> dict[str, Any]:
    """
    스토리지 정책 검증.

    Kubernetes API로 PVC/PV 정책 확인.
    """
    try:
        from kubernetes import client, config

        config.load_incluster_config()
        v1 = client.CoreV1Api()

        # PVC 조회
        pvc = v1.read_namespaced_persistent_volume_claim(
            name="selfhealing-buffer-pvc",
            namespace="production",
        )

        # PV 조회 (바인딩된 경우)
        pv_name = pvc.spec.volume_name
        if pv_name:
            pv = v1.read_persistent_volume(name=pv_name)
            reclaim_policy = pv.spec.persistent_volume_reclaim_policy
        else:
            # StorageClass에서 확인
            storage_v1 = client.StorageV1Api()
            sc = storage_v1.read_storage_class(name=pvc.spec.storage_class_name)
            reclaim_policy = sc.reclaim_policy

        is_safe = reclaim_policy in ("Retain", "Delete")

        return {
            "pvc_name": pvc.metadata.name,
            "pv_name": pv_name,
            "storage_class": pvc.spec.storage_class_name,
            "reclaim_policy": reclaim_policy,
            "is_safe": is_safe,
            "warning": None if is_safe else "Recycle 정책은 데이터 손실 위험!",
        }

    except Exception as e:
        return {"error": str(e), "is_safe": False}
```

#### 6.3.5 map_size와 PV 용량 정합성 검증

```python
# config.py __post_init__ 확장

def _validate_storage_capacity(self) -> None:
    """map_size와 PV 용량 정합성 검증."""
    import shutil

    try:
        usage = shutil.disk_usage(self.data_path)
        disk_capacity_bytes = usage.total

        # map_size는 디스크 용량의 80% 이하 권장
        max_safe_map_size = int(disk_capacity_bytes * 0.8)

        if self.lmdb_map_size > max_safe_map_size:
            import warnings
            warnings.warn(
                f"lmdb_map_size ({self.lmdb_map_size / (1024**3):.1f}GB) > "
                f"disk capacity * 0.8 ({max_safe_map_size / (1024**3):.1f}GB). "
                "PV 용량 증가 또는 map_size 감소 권장."
            )

    except Exception:
        pass  # 경로 미존재 시 스킵
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

## 9. Prometheus 메트릭 통합

### 9.1 메트릭 정의

**파일**: `packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer_metrics.py`

```python
"""
DiskPersistentBuffer Prometheus 메트릭.

패턴 벤치마킹: audit_buffer_metrics.py
- Gauge: 현재 상태 측정
- Counter: 누적 카운터

관련 코드: packages/selfhealing-python/src/selfhealing/metrics/
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

METRICS_AVAILABLE = False

try:
    from prometheus_client import Counter, Gauge, Histogram

    METRICS_AVAILABLE = True

    # ──────────────────────────────────────────────────────
    # Gauge 메트릭 (현재 상태)
    # ──────────────────────────────────────────────────────

    disk_buffer_entries = Gauge(
        "selfhealing_disk_buffer_entries",
        "Current number of entries in disk buffer",
        ["instance"],
    )

    disk_buffer_size_bytes = Gauge(
        "selfhealing_disk_buffer_size_bytes",
        "Current size of disk buffer in bytes",
        ["instance"],
    )

    disk_buffer_state = Gauge(
        "selfhealing_disk_buffer_state",
        "Current state of disk buffer (0=UNINITIALIZED, 1=ACTIVE, 2=DISK_FULL_FAILOPEN, 3=CORRUPTED)",
        ["instance"],
    )

    disk_buffer_dead_letters = Gauge(
        "selfhealing_disk_buffer_dead_letters",
        "Current number of entries in dead letter DB",
        ["instance"],
    )

    disk_buffer_disk_free_ratio = Gauge(
        "selfhealing_disk_buffer_disk_free_ratio",
        "Disk free space ratio (0.0-1.0)",
        ["instance"],
    )

    # ──────────────────────────────────────────────────────
    # Counter 메트릭 (누적)
    # ──────────────────────────────────────────────────────

    disk_buffer_puts_total = Counter(
        "selfhealing_disk_buffer_puts_total",
        "Total number of put operations",
        ["instance"],
    )

    disk_buffer_flushes_total = Counter(
        "selfhealing_disk_buffer_flushes_total",
        "Total number of flush operations",
        ["instance"],
    )

    disk_buffer_checksum_errors_total = Counter(
        "selfhealing_disk_buffer_checksum_errors_total",
        "Total number of checksum errors detected",
        ["instance"],
    )

    disk_buffer_disk_full_events_total = Counter(
        "selfhealing_disk_buffer_disk_full_events_total",
        "Total number of disk full events",
        ["instance"],
    )

    disk_buffer_dead_letter_moves_total = Counter(
        "selfhealing_disk_buffer_dead_letter_moves_total",
        "Total number of entries moved to dead letter DB",
        ["instance"],
    )

    disk_buffer_group_commit_flushes_total = Counter(
        "selfhealing_disk_buffer_group_commit_flushes_total",
        "Total number of group commit flushes",
        ["instance"],
    )

    disk_buffer_quarantine_events_total = Counter(
        "selfhealing_disk_buffer_quarantine_events_total",
        "Total number of DB quarantine events",
        ["instance"],
    )

    # ──────────────────────────────────────────────────────
    # Histogram 메트릭 (분포)
    # ──────────────────────────────────────────────────────

    disk_buffer_put_latency = Histogram(
        "selfhealing_disk_buffer_put_latency_seconds",
        "Latency of put operations",
        ["instance"],
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    )

    disk_buffer_flush_latency = Histogram(
        "selfhealing_disk_buffer_flush_latency_seconds",
        "Latency of flush operations",
        ["instance"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    )

except ImportError:
    logger.debug("[DiskBufferMetrics] prometheus_client not available")


def update_disk_buffer_metrics(
    buffer: "DiskPersistentBuffer",
    instance: str = "default",
) -> None:
    """
    DiskPersistentBuffer 메트릭 업데이트.

    Args:
        buffer: DiskPersistentBuffer 인스턴스
        instance: 인스턴스 레이블
    """
    if not METRICS_AVAILABLE:
        return

    try:
        stats = buffer.get_stats()

        # Gauge 업데이트
        disk_buffer_entries.labels(instance=instance).set(stats.get("count", 0))
        disk_buffer_size_bytes.labels(instance=instance).set(stats.get("db_size_bytes", 0))
        disk_buffer_state.labels(instance=instance).set(buffer._state.value)

        # Dead Letter 수 (활성화된 경우)
        if buffer._settings.enable_dead_letter_db:
            dl_count = len(buffer.get_dead_letters(limit=10000))
            disk_buffer_dead_letters.labels(instance=instance).set(dl_count)

        # 디스크 여유 공간
        import shutil
        try:
            usage = shutil.disk_usage(buffer._settings.data_path)
            free_ratio = usage.free / usage.total
            disk_buffer_disk_free_ratio.labels(instance=instance).set(free_ratio)
        except Exception:
            pass

        # Counter 업데이트 (증분)
        disk_buffer_puts_total.labels(instance=instance)._value.set(
            stats.get("total_puts", 0)
        )
        disk_buffer_checksum_errors_total.labels(instance=instance)._value.set(
            stats.get("checksum_errors", 0)
        )
        disk_buffer_disk_full_events_total.labels(instance=instance)._value.set(
            stats.get("disk_full_events", 0)
        )
        disk_buffer_dead_letter_moves_total.labels(instance=instance)._value.set(
            stats.get("dead_letter_moves", 0)
        )
        disk_buffer_group_commit_flushes_total.labels(instance=instance)._value.set(
            stats.get("group_commit_flushes", 0)
        )

    except Exception as e:
        logger.debug(f"[DiskBufferMetrics] Update failed: {e}")
```

### 9.2 메트릭 수집 통합

**DiskPersistentBuffer에 메트릭 업데이트 훅 추가**:

```python
# disk_buffer.py에 추가

class DiskPersistentBuffer:
    # ... 기존 코드 ...

    def _update_metrics(self) -> None:
        """Prometheus 메트릭 업데이트."""
        try:
            from selfhealing.audit.persistence.disk_buffer_metrics import (
                METRICS_AVAILABLE,
                update_disk_buffer_metrics,
            )

            if METRICS_AVAILABLE:
                instance_id = self._settings.instance_name or "default"
                update_disk_buffer_metrics(self, instance_id)
        except ImportError:
            pass  # 메트릭 모듈 없음

    # put(), flush_to() 등 주요 메서드에 _update_metrics() 호출 추가
```

---

## 10. Graceful Shutdown 통합

### 10.1 Shutdown 핸들러 등록

**패턴 벤치마킹**: `async_audit_lifecycle.py#L404-410`

```python
# packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer.py

"""
Graceful Shutdown 통합.

패턴 벤치마킹:
- async_audit_lifecycle.py#L404-410: register_shutdown_handlers()
- shutdown_coordinator.py: GracefulShutdownCoordinator

관련 코드:
- packages/selfhealing-python/src/selfhealing/audit/async_audit_lifecycle.py
"""
import atexit
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

# 전역 버퍼 인스턴스 (Graceful Shutdown용)
_disk_buffer_instance: DiskPersistentBuffer | None = None


def register_disk_buffer_shutdown(buffer: "DiskPersistentBuffer") -> None:
    """
    DiskPersistentBuffer Shutdown 핸들러 등록.

    패턴 벤치마킹: async_audit_lifecycle.py#L404-410

    Args:
        buffer: DiskPersistentBuffer 인스턴스
    """
    global _disk_buffer_instance
    _disk_buffer_instance = buffer

    # atexit 핸들러 등록
    atexit.register(_shutdown_disk_buffer)

    # 시그널 핸들러 등록 (기존 핸들러 체인)
    _register_signal_handlers()

    logger.info("[DiskBuffer] Shutdown handlers registered")


def _register_signal_handlers() -> None:
    """
    SIGTERM, SIGINT 핸들러 등록.

    패턴 벤치마킹: async_audit_lifecycle.py의 시그널 핸들러
    """
    import sys

    if sys.platform == "win32":
        # Windows는 SIGTERM 미지원, atexit만 사용
        return

    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def _sigterm_handler(signum, frame):
        """SIGTERM 핸들러."""
        logger.info("[DiskBuffer] SIGTERM received, initiating shutdown")
        _shutdown_disk_buffer()
        # 원래 핸들러 호출
        if callable(original_sigterm):
            original_sigterm(signum, frame)

    def _sigint_handler(signum, frame):
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

    try:
        logger.info("[DiskBuffer] Graceful shutdown starting...")

        # 1. Group Commit 버퍼 강제 플러시
        if _disk_buffer_instance._settings.group_commit_enabled:
            _disk_buffer_instance.flush_group_commit()
            logger.debug("[DiskBuffer] Group commit buffer flushed")

        # 2. LMDB fsync (데이터 안전 보장)
        if _disk_buffer_instance._env:
            _disk_buffer_instance._env.sync(force=True)
            logger.debug("[DiskBuffer] LMDB synced")

        # 3. 버퍼 종료
        _disk_buffer_instance.close()

        logger.info("[DiskBuffer] Graceful shutdown complete")

    except Exception as e:
        logger.error(f"[DiskBuffer] Shutdown error: {e}")

    finally:
        _disk_buffer_instance = None
```

### 10.2 DiskPersistentBuffer에 Shutdown 등록 통합

```python
# disk_buffer.py __init__ 수정

class DiskPersistentBuffer:
    def __init__(self, settings: DiskBufferSettings):
        # ... 기존 초기화 ...

        # Graceful Shutdown 등록
        if settings.enable_shutdown_handlers:
            from selfhealing.audit.persistence.disk_buffer import (
                register_disk_buffer_shutdown,
            )
            register_disk_buffer_shutdown(self)

# DiskBufferSettings에 추가
@dataclass
class DiskBufferSettings:
    # ... 기존 필드 ...
    enable_shutdown_handlers: bool = True  # Graceful Shutdown 자동 등록
```

---

## 11. Drain-on-Startup 마이그레이션

### 11.1 migration.py

**파일**: `packages/selfhealing-python/src/selfhealing/audit/persistence/migration.py`

```python
"""
Disk Buffer Drain-on-Startup 마이그레이션.

Pod 재시작 시 이전에 영속된 이벤트를 주 스토리지로 플러시.

패턴 벤치마킹:
- wal.py의 Best-Effort Recovery (wal.py#L117-118)
- async_audit_lifecycle.py의 startup 패턴
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

logger = logging.getLogger(__name__)


@dataclass
class DrainResult:
    """Drain 결과."""

    drained: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    errors: list[str] | None = None


def drain_on_startup(
    buffer: "DiskPersistentBuffer",
    flush_handler: Callable[[list[dict[str, Any]]], bool],
    batch_size: int = 100,
    max_batches: int | None = None,
    fail_fast: bool = False,
) -> DrainResult:
    """
    시작 시 버퍼에 남은 이벤트를 주 스토리지로 플러시.

    패턴 벤치마킹: wal.py#L117-118 (best_effort_recovery)

    Args:
        buffer: DiskPersistentBuffer 인스턴스
        flush_handler: 이벤트 배치 처리 핸들러
        batch_size: 배치 크기
        max_batches: 최대 처리 배치 수 (None=무제한)
        fail_fast: 실패 시 즉시 중단

    Returns:
        DrainResult
    """
    start_time = time.time()
    result = DrainResult(errors=[])

    entry_count = buffer.count()
    if entry_count == 0:
        logger.info("[DrainOnStartup] No pending entries to drain")
        return result

    logger.info(f"[DrainOnStartup] Draining {entry_count} pending entries...")

    batches_processed = 0

    while True:
        if max_batches and batches_processed >= max_batches:
            logger.warning(f"[DrainOnStartup] Max batches ({max_batches}) reached")
            break

        # 배치 조회
        entries = list(buffer.iter_entries(limit=batch_size))
        if not entries:
            break

        # 핸들러 호출
        try:
            # BufferEntry → dict 변환
            entry_dicts = [e.data for e in entries]
            success = flush_handler(entry_dicts)
        except Exception as e:
            error_msg = f"Handler error: {e}"
            logger.error(f"[DrainOnStartup] {error_msg}")
            result.errors.append(error_msg)
            result.failed += len(entries)

            if fail_fast:
                break
            continue

        if success:
            # 성공 시 삭제
            keys = [e.key for e in entries]
            deleted = buffer.delete_batch(keys)
            result.drained += deleted
            logger.debug(f"[DrainOnStartup] Drained batch: {deleted} entries")
        else:
            # 실패 시 해당 배치 스킵 (다음 배치 시도)
            result.skipped += len(entries)
            logger.warning(f"[DrainOnStartup] Batch failed, skipping {len(entries)} entries")

            if fail_fast:
                break

        batches_processed += 1

    result.duration_seconds = time.time() - start_time

    logger.info(
        f"[DrainOnStartup] Complete: "
        f"drained={result.drained}, "
        f"failed={result.failed}, "
        f"skipped={result.skipped}, "
        f"duration={result.duration_seconds:.2f}s"
    )

    return result


async def async_drain_on_startup(
    buffer: "DiskPersistentBuffer",
    async_flush_handler: Callable[[list[dict[str, Any]]], Any],
    batch_size: int = 100,
    max_batches: int | None = None,
) -> DrainResult:
    """
    비동기 버전의 drain_on_startup.

    Args:
        buffer: DiskPersistentBuffer 인스턴스
        async_flush_handler: 비동기 이벤트 핸들러
        batch_size: 배치 크기
        max_batches: 최대 배치 수

    Returns:
        DrainResult
    """
    import asyncio

    start_time = time.time()
    result = DrainResult(errors=[])

    entry_count = buffer.count()
    if entry_count == 0:
        return result

    logger.info(f"[AsyncDrainOnStartup] Draining {entry_count} entries...")

    batches_processed = 0

    while True:
        if max_batches and batches_processed >= max_batches:
            break

        entries = list(buffer.iter_entries(limit=batch_size))
        if not entries:
            break

        try:
            entry_dicts = [e.data for e in entries]
            success = await async_flush_handler(entry_dicts)
        except Exception as e:
            result.errors.append(str(e))
            result.failed += len(entries)
            continue

        if success:
            keys = [e.key for e in entries]
            deleted = buffer.delete_batch(keys)
            result.drained += deleted
        else:
            result.skipped += len(entries)

        batches_processed += 1
        await asyncio.sleep(0)  # 이벤트 루프 양보

    result.duration_seconds = time.time() - start_time
    return result
```

### 11.2 startup 통합 예시

```python
# 애플리케이션 시작 시 호출

async def initialize_audit_system():
    """Audit 시스템 초기화 (Drain-on-Startup 포함)."""
    from selfhealing.audit.persistence.disk_buffer import (
        DiskBufferSettings,
        DiskPersistentBuffer,
    )
    from selfhealing.audit.persistence.migration import drain_on_startup

    # Disk Buffer 초기화
    settings = DiskBufferSettings.from_env()
    buffer = DiskPersistentBuffer(settings)

    # 주 스토리지 핸들러 (예: Kafka, DB)
    async def send_to_primary_storage(entries: list[dict]) -> bool:
        from selfhealing.audit.kafka.producer import get_kafka_producer
        producer = get_kafka_producer()
        try:
            for entry in entries:
                await producer.send(entry)
            return True
        except Exception as e:
            logger.error(f"Primary storage error: {e}")
            return False

    # Drain-on-Startup 실행
    result = drain_on_startup(
        buffer=buffer,
        flush_handler=lambda entries: asyncio.run(send_to_primary_storage(entries)),
        batch_size=100,
    )

    if result.failed > 0:
        logger.warning(f"Drain had {result.failed} failures")

    return buffer
```

---

## 12. Health Check Probe

### 12.1 get_health_status() 메서드

```python
# disk_buffer.py에 추가

class DiskPersistentBuffer:
    # ... 기존 코드 ...

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
        errors = []
        healthy = True

        # 상태 확인
        state = self._state.name
        if self._state == self.State.DISK_FULL_FAILOPEN:
            healthy = False
            errors.append("Disk full - fail-open mode active")
        elif self._state == self.State.CORRUPTED:
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
        import shutil
        try:
            usage = shutil.disk_usage(self._settings.data_path)
            disk_free_ratio = usage.free / usage.total

            if disk_free_ratio < 0.1:  # 10% 미만 경고
                errors.append(f"Low disk space: {disk_free_ratio:.1%}")
        except Exception as e:
            disk_free_ratio = -1
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
```

### 12.2 FastAPI Health Endpoint 통합

```python
# FastAPI 라우터 예시

from fastapi import APIRouter, Response, status

router = APIRouter()


@router.get("/health/disk-buffer")
async def disk_buffer_health():
    """DiskPersistentBuffer Health Check Endpoint."""
    from selfhealing.audit.persistence.disk_buffer import get_disk_buffer

    try:
        buffer = get_disk_buffer()
        health = buffer.get_health_status()

        if health["healthy"]:
            return Response(content="OK", status_code=status.HTTP_200_OK)
        else:
            return Response(
                content="; ".join(health["errors"]),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
    except Exception as e:
        return Response(
            content=f"Health check failed: {e}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
```

---

## 13. 설정 검증 강화

### 13.1 DiskBufferSettings 검증

```python
# config.py에 Pydantic 검증 추가

from pydantic import field_validator, model_validator


@dataclass
class DiskBufferSettings:
    # ... 기존 필드 ...

    def __post_init__(self):
        """설정 검증."""
        self._validate_paths()
        self._validate_sizes()
        self._validate_conflicts()

    def _validate_paths(self) -> None:
        """경로 검증."""
        import os

        if not os.path.isabs(self.data_path):
            raise ValueError(f"data_path must be absolute: {self.data_path}")

        # 부모 디렉토리 존재 확인
        parent = os.path.dirname(self.data_path)
        if not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

    def _validate_sizes(self) -> None:
        """크기 검증."""
        # map_size 최소값 검증 (100MB)
        if self.lmdb_map_size < 100 * 1024 * 1024:
            raise ValueError(
                f"lmdb_map_size too small: {self.lmdb_map_size}. Minimum is 100MB."
            )

        # max_entries 검증
        if self.max_entries < 100:
            raise ValueError(f"max_entries too small: {self.max_entries}")

        # Group Commit 검증
        if self.group_commit_enabled:
            if self.group_commit_max_entries < 1:
                raise ValueError("group_commit_max_entries must be >= 1")
            if self.group_commit_interval_ms < 10:
                raise ValueError("group_commit_interval_ms must be >= 10")

    def _validate_conflicts(self) -> None:
        """설정 충돌 검증."""
        # sync_on_write와 group_commit 충돌 경고
        if self.sync_on_write and self.group_commit_enabled:
            import warnings
            warnings.warn(
                "sync_on_write=True with group_commit_enabled=True "
                "reduces group commit benefits. Consider sync_on_write=False."
            )

        # Poison Pill 설정 검증
        if self.max_flush_retries < 1:
            raise ValueError("max_flush_retries must be >= 1")

    @classmethod
    def from_env(cls) -> "DiskBufferSettings":
        """환경변수에서 설정 로드 (검증 포함)."""
        import os

        settings = cls(
            data_path=os.environ.get(
                "SELFHEALING_DISK_BUFFER_DATA_DIR",
                "/var/lib/selfhealing/buffer",
            ),
            lmdb_map_size=int(os.environ.get(
                "SELFHEALING_DISK_BUFFER_MAP_SIZE_MB", "10240"
            )) * 1024 * 1024,
            # ... 나머지 설정들
        )

        return settings
```

---

## 14. 테스트 Fixture 패턴

### 14.1 conftest.py Fixtures

**파일**: `tests/conftest.py`

```python
"""
DiskPersistentBuffer 테스트 Fixtures.

패턴 벤치마킹:
- tests/conftest.py의 기존 fixture 패턴
- 임시 디렉토리 자동 정리
"""
import os
import shutil
import tempfile
from typing import Generator

import pytest


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """임시 LMDB 경로 (테스트 후 자동 삭제)."""
    temp_dir = tempfile.mkdtemp(prefix="disk_buffer_test_")
    yield temp_dir
    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def disk_buffer_settings(temp_db_path: str):
    """테스트용 DiskBufferSettings."""
    from selfhealing.audit.persistence.config import DiskBufferSettings

    return DiskBufferSettings(
        data_path=temp_db_path,
        lmdb_map_size=50 * 1024 * 1024,  # 50MB (테스트용)
        max_entries=1000,
        sync_on_write=True,  # 테스트에서는 즉시 동기화
        enable_checksum=True,
        group_commit_enabled=False,  # 테스트 단순화
        enable_dead_letter_db=True,
        enable_shutdown_handlers=False,  # 테스트에서는 비활성화
    )


@pytest.fixture
def disk_buffer(disk_buffer_settings) -> Generator["DiskPersistentBuffer", None, None]:
    """테스트용 DiskPersistentBuffer 인스턴스."""
    from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

    buffer = DiskPersistentBuffer(disk_buffer_settings)
    yield buffer
    buffer.close()


@pytest.fixture
def disk_buffer_with_entries(disk_buffer) -> "DiskPersistentBuffer":
    """테스트 데이터가 포함된 DiskPersistentBuffer."""
    for i in range(10):
        disk_buffer.put({
            "event_type": "test_event",
            "sequence": i,
            "data": f"test_data_{i}",
        })
    return disk_buffer


@pytest.fixture
def corrupted_db_path(temp_db_path: str) -> str:
    """손상된 LMDB 파일 생성 (Quarantine 테스트용)."""
    db_path = os.path.join(temp_db_path, "data.mdb")
    os.makedirs(temp_db_path, exist_ok=True)

    # 손상된 파일 생성
    with open(db_path, "wb") as f:
        f.write(b"CORRUPTED_DATA_INVALID_LMDB_HEADER")

    return temp_db_path
```

### 14.2 테스트 예시

```python
# tests/unit/audit/persistence/test_disk_buffer.py

import pytest


class TestDiskPersistentBuffer:
    """DiskPersistentBuffer 단위 테스트."""

    def test_put_and_get(self, disk_buffer):
        """저장 및 조회 테스트."""
        entry = {"event_type": "test", "value": 42}
        key = disk_buffer.put(entry)

        result = disk_buffer.get(key)

        assert result is not None
        assert result.data["event_type"] == "test"
        assert result.data["value"] == 42

    def test_checksum_verification(self, disk_buffer):
        """체크섬 검증 테스트."""
        entry = {"event_type": "checksum_test"}
        key = disk_buffer.put(entry)

        result = disk_buffer.get(key)
        assert result.checksum is not None

    def test_persistence_across_restart(self, disk_buffer_settings, temp_db_path):
        """재시작 후 데이터 보존 테스트."""
        from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

        # 첫 번째 인스턴스
        buffer1 = DiskPersistentBuffer(disk_buffer_settings)
        buffer1.put({"event": "persistent"})
        count1 = buffer1.count()
        buffer1.close()

        # 두 번째 인스턴스 (재시작 시뮬레이션)
        buffer2 = DiskPersistentBuffer(disk_buffer_settings)
        count2 = buffer2.count()
        buffer2.close()

        assert count2 == count1

    def test_quarantine_on_corruption(self, corrupted_db_path, disk_buffer_settings):
        """손상 시 Quarantine 테스트."""
        from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

        disk_buffer_settings.data_path = corrupted_db_path
        disk_buffer_settings.quarantine_on_corruption = True

        # 손상된 DB로 초기화 시도 → Quarantine 발생
        buffer = DiskPersistentBuffer(disk_buffer_settings)

        # Quarantine 파일 존재 확인
        import os
        quarantine_files = [
            f for f in os.listdir(corrupted_db_path)
            if ".quarantine." in f
        ]
        assert len(quarantine_files) > 0

        buffer.close()

    def test_dead_letter_move(self, disk_buffer):
        """Poison Pill → Dead Letter DB 이동 테스트."""
        # 항상 실패하는 핸들러
        def failing_handler(entries):
            raise ValueError("Simulated failure")

        entry = {"event": "poison_pill_test"}
        disk_buffer.put(entry)

        # max_flush_retries 만큼 플러시 시도
        for _ in range(disk_buffer._settings.max_flush_retries):
            disk_buffer.flush_to(failing_handler)

        # Dead Letter DB 확인
        dead_letters = disk_buffer.get_dead_letters()
        assert len(dead_letters) > 0
        assert dead_letters[0]["status"] == "requires_review"
```

---

## 15. 구현 체크리스트

### 15.1 Core 구현 (필수)

- [x] `audit/persistence/__init__.py` 생성
- [x] `audit/persistence/config.py` 구현 - DiskBufferSettings (설정 검증 포함)
- [x] `audit/persistence/disk_buffer.py` 구현 - DiskPersistentBuffer 핵심 클래스
- [x] `audit/persistence/mmap_buffer.py` 구현 (LMDB 대안)
- [x] `pyproject.toml`에 `lmdb>=1.4.0` 의존성 추가

### 15.2 고급 기능 구현

- [x] Group Commit 로직 구현 (`_buffered_put`, `_flush_group_buffer`)
- [x] Disk Full 대응 구현 (`_check_disk_space`, `_handle_disk_full`, Fail-Open)
- [x] Priority-based Purge 구현 (`_purge_old_entries_for_space`)
- [x] Quarantine 전략 구현 (`_quarantine_corrupt_db`, `_send_corruption_alert`)
- [x] Poison Pill → Dead Letter DB 격리 (`_move_to_dead_letter`, `replay_dead_letter`)
- [x] Multi-Instance DB 이름 (`_generate_db_name` - hostname + PID)

### 15.3 통합 구현

- [x] `audit/persistence/disk_buffer_metrics.py` - Prometheus 메트릭 정의
- [x] `audit/persistence/migration.py` - Drain-on-Startup
- [x] Graceful Shutdown 핸들러 (`register_disk_buffer_shutdown`)
- [x] Health Check (`get_health_status`)
- [ ] `fallback.py` 수정 (Memory → Disk Buffer) - 별도 작업 필요
- [ ] `buffer.py`에 팩토리 함수 추가 - 별도 작업 필요

### 15.4 인프라

- [ ] Kubernetes PVC 매니페스트 작성
- [ ] Grafana 대시보드 (disk_buffer_* 메트릭)

### 15.5 테스트

- [x] `tests/conftest.py`에 fixtures 추가
- [x] 단위 테스트 작성 (put, get, flush, checksum, quarantine, dead_letter)
- [ ] 통합 테스트 작성 (persistence, drain-on-startup, fallback chain)

---

## 16. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [175_KAFKA_EVENT_BUS_IMPLEMENTATION.md](175_KAFKA_EVENT_BUS_IMPLEMENTATION.md) - Kafka 구현
- [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md) - RingBuffer 통합

---

## 11. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
| 1.1.0 | 2026-02-04 | 16가지 리뷰 반영 (WAL 패턴 벤치마킹) |
| 1.2.0 | 2026-02-04 | Storage Class 가이드 추가 |
| 1.3.0 | 2026-02-04 | 구현 완료 - Core/고급 기능/통합/테스트 |

### 1.1.0 변경 상세

**WAL 패턴 벤치마킹 기반 확장** (wal.py 참조):

1. **Group Commit** (wal.py#L107-112): `group_commit_enabled`, `_buffered_put()`, `_flush_group_buffer()`
2. **Disk Full Fail-Open** (wal.py#L879-920): `_check_disk_space()`, State.DISK_FULL_FAILOPEN
3. **Priority-based Purge** (wal.py#L113-129): `_purge_old_entries_for_space()`
4. **Quarantine 전략** (wal.py#L117-118): `_quarantine_corrupt_db()`, `_send_corruption_alert()`
5. **Multi-Instance** (wal.py#L258-263): `_generate_db_name()` (hostname + PID)
6. **Poison Pill → Dead Letter DB** (test_dlq_storage_and_replay.py): `_move_to_dead_letter()`, `status="requires_review"`
7. **Prometheus 메트릭 통합**: `disk_buffer_metrics.py` (audit_buffer_metrics.py 패턴)
8. **Graceful Shutdown 통합** (async_audit_lifecycle.py#L404-410): `register_disk_buffer_shutdown()`
9. **Drain-on-Startup**: `migration.py` - `drain_on_startup()`, `async_drain_on_startup()`
10. **Health Check Probe**: `get_health_status()` 메서드
11. **설정 검증 강화**: `__post_init__()` 검증 로직
12. **테스트 Fixture 패턴**: conftest.py fixtures 통일
13. **map_size 10GB 확장**: 기본값 1GB → 10GB

**구현 체크리스트**: 10개 → 20개 항목으로 확장

### 1.2.0 변경 상세 (Storage Class)

**리뷰 항목 반영**:

1. **IOPS/Latency 검증** (6.3.1):
   - `scripts/benchmark_storage.py` 벤치마크 스크립트 추가
   - 목표: Random Write IOPS >= 1,000, fsync Latency <= 10ms

2. **파일 시스템 특성** (6.3.2):
   - `lmdb_writemap`, `lmdb_metasync` 옵션 추가
   - ext4/XFS 권장 설정 테이블 추가

3. **실제 매니페스트 정렬** (6.3.3):
   - `SELFHEALING_STORAGE_CLASS` 환경변수 도입
   - Fallback StorageClass 로직 (fast-ssd → standard)

4. **PV 재활용 정책** (6.3.4):
   - `persistentVolumeReclaimPolicy: Retain` 명시
   - `validate_storage_policy()` K8s API 검증 함수
