"""
Disk-Persistent Buffer 모듈.

Pod 재시작에도 데이터가 보존되는 LMDB 기반 영속 버퍼.

주요 컴포넌트:
- DiskBufferSettings: 버퍼 설정
- DiskPersistentBuffer: LMDB 기반 영속 버퍼
- DiskBufferAdapter: InMemoryAuditBuffer 호환 어댑터
- MmapBuffer: mmap 기반 대안 버퍼 (표준 라이브러리만 사용)
- drain_on_startup: Pod 재시작 시 이벤트 복구

사용법:
    from selfhealing.audit.persistence import (
        DiskBufferSettings,
        DiskPersistentBuffer,
        get_disk_buffer,
    )

    # 버퍼 생성
    buffer = DiskPersistentBuffer()

    # 이벤트 저장
    buffer.put({"event_type": "audit", "data": "..."})

    # 조회
    for entry in buffer.iter_entries():
        print(entry.data)

    # 플러시
    buffer.flush_to(lambda entries: send_to_kafka(entries))
"""

from __future__ import annotations

from selfhealing.audit.persistence.config import (
    DiskBufferSettings,
    get_disk_buffer_settings,
    reset_disk_buffer_settings,
)
from selfhealing.audit.persistence.disk_buffer import (
    BufferEntry,
    DiskBufferAdapter,
    DiskBufferError,
    DiskPersistentBuffer,
    get_disk_buffer,
    reset_disk_buffer,
)
from selfhealing.audit.persistence.migration import (
    DrainResult,
    async_drain_on_startup,
    drain_on_startup,
)

__all__ = [
    # Config
    "DiskBufferSettings",
    "get_disk_buffer_settings",
    "reset_disk_buffer_settings",
    # Disk Buffer
    "BufferEntry",
    "DiskBufferAdapter",
    "DiskBufferError",
    "DiskPersistentBuffer",
    "get_disk_buffer",
    "reset_disk_buffer",
    # Migration
    "DrainResult",
    "async_drain_on_startup",
    "drain_on_startup",
]
