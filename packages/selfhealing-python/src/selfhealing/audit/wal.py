"""
Write-Ahead Log (WAL) with CRC32 Checksum.

이 모듈은 selfhealing.audit.wal_pkg에서 실제 구현을 re-export합니다.
기존 import 경로를 유지하기 위한 호환성 모듈입니다.

Usage:
    from selfhealing.audit.wal import WriteAheadLog, WALEntry, WALConfig

    wal = WriteAheadLog(wal_dir="/var/log/audit/wal")
    seq = wal.write({"event": "config_change", "key": "max_retries"})
    entries = wal.recover_unprocessed(last_processed_seq=100)
    wal.cleanup_processed(last_processed_seq=500)
"""

from selfhealing.audit.wal_pkg import (  # noqa: F401
    WALConfig,
    WALCorruptionError,
    WALEntry,
    WALError,
    WALState,
    WALStats,
    WriteAheadLog,
    create_wal,
)

__all__ = [
    "WriteAheadLog",
    "WALEntry",
    "WALConfig",
    "WALStats",
    "WALError",
    "WALCorruptionError",
    "WALState",
    "create_wal",
]
