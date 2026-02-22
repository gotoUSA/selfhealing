"""
WAL 파일 읽기/복구 모듈.

기존 코드에서 구조가 동일했던 _read_wal_file과 _read_wal_file_best_effort를
모드 파라미터로 통합합니다.
"""

from __future__ import annotations

import json
import structlog
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from selfhealing.audit.wal._serialization import (
    compute_checksum,
    verify_checksum,
)

logger = structlog.get_logger()


class WALReaderMixin:
    """WAL 파일 읽기/복구 관련 메서드."""

    def _read_wal_file(self, filepath: Path) -> Iterator[Any]:
        """
        WAL 파일 읽기.

        Args:
            filepath: WAL 파일 경로

        Yields:
            WALEntry 객체
        """
        yield from self._read_wal_file_impl(filepath, best_effort=False)

    def _read_wal_file_best_effort(self, filepath: Path) -> Iterator[Any]:
        """
        Best-effort 복구 모드로 WAL 파일 읽기.

        손상된 레코드를 건너뛰고 가능한 많은 엔트리를 복구합니다.
        """
        yield from self._read_wal_file_impl(filepath, best_effort=True)

    def _read_wal_file_impl(self, filepath: Path, best_effort: bool = False) -> Iterator[Any]:
        """
        WAL 파일 읽기 통합 구현.

        기존 _read_wal_file과 _read_wal_file_best_effort의
        동일 구조를 하나로 통합합니다.

        Args:
            filepath: WAL 파일 경로
            best_effort: True면 손상 레코드를 건너뛰고 계속 진행

        Yields:
            WALEntry 객체
        """
        from selfhealing.audit.wal._models import WALCorruptionError, WALEntry

        # Drift Detection 메트릭 (선택적 import)
        try:
            from selfhealing.metrics.drift_metrics import record_wal_corruption

            has_metrics = True
        except ImportError:
            has_metrics = False

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

                    # Best-effort: 비정상적인 길이 감지 (10MB 초과 = 손상)
                    if best_effort and length > 10 * 1024 * 1024:
                        if not self._handle_corrupted_record_length(f):
                            break
                        continue

                    # 체크섬 읽기
                    checksum_bytes = f.read(8)
                    if len(checksum_bytes) < 8:
                        break

                    if best_effort:
                        checksum = checksum_bytes.decode("ascii", errors="replace")
                    else:
                        checksum = checksum_bytes.decode("ascii")

                    # 데이터 읽기
                    data_bytes = f.read(length)
                    if len(data_bytes) < length:
                        break

                    # 체크섬 검증
                    if not verify_checksum(data_bytes, checksum):
                        self._corrupted_entries += 1

                        if best_effort:
                            if self._config.best_effort_recovery:
                                continue
                            break
                        else:
                            computed_cs = compute_checksum(data_bytes)
                            error = WALCorruptionError(
                                f"Checksum mismatch in {filepath}",
                                sequence=-1,
                                expected=checksum,
                                computed=computed_cs,
                            )
                            if has_metrics:
                                record_wal_corruption()
                            self._record_audit_event(
                                event_type="WAL_CORRUPTION_DETECTED",
                                details={
                                    "filepath": str(filepath),
                                    "expected_checksum": checksum,
                                    "computed_checksum": computed_cs,
                                },
                            )
                            if self._on_corruption:
                                self._on_corruption(error)
                            continue

                    # JSON 파싱
                    entry = self._parse_wal_record(data_bytes, checksum)
                    if entry is not None:
                        yield entry
                    elif best_effort and not self._config.best_effort_recovery:
                        break

        except Exception:
            pass

    def _handle_corrupted_record_length(self, f) -> bool:
        """손상된 레코드 길이 처리. 계속 진행 가능하면 True."""
        if self._config.best_effort_recovery:
            pos = self._scan_for_valid_record(f)
            return pos != -1
        return False

    def _parse_wal_record(self, data_bytes: bytes, checksum: str):
        """WAL 레코드 파싱. 실패 시 None."""
        from selfhealing.audit.wal._models import WALEntry

        try:
            entry_dict = json.loads(data_bytes.decode("utf-8"))
            return WALEntry(
                sequence=entry_dict["seq"],
                timestamp=entry_dict["ts"],
                data=entry_dict["data"],
                checksum=checksum,
            )
        except (json.JSONDecodeError, KeyError):
            self._corrupted_entries += 1
            return None

    def _scan_for_valid_record(self, f) -> int:
        """
        다음 유효한 레코드 위치까지 스캔.

        손상된 영역을 건너뛰고 다음 유효한 JSON 레코드를 찾습니다.
        """
        scan_buffer = bytearray()
        max_scan_bytes = 1024 * 1024  # 최대 1MB 스캔
        scanned = 0

        while scanned < max_scan_bytes:
            byte = f.read(1)
            if not byte:
                return -1

            scan_buffer.append(byte[0])
            scanned += 1

            if len(scan_buffer) > 20:
                try:
                    potential_checksum = bytes(scan_buffer[-8:]).decode("ascii")
                    if all(c in "0123456789abcdef" for c in potential_checksum.lower()):
                        f.seek(f.tell() - 8)
                        f.seek(f.tell() - 4)
                        return f.tell()
                except Exception:
                    pass

                if len(scan_buffer) > 1024:
                    scan_buffer = scan_buffer[-512:]

        return -1

    def recover_unprocessed(self, last_processed_seq: int = 0) -> list:
        """
        마지막 처리된 시퀀스 이후 엔트리 복구.

        Args:
            last_processed_seq: 마지막으로 처리된 시퀀스 번호

        Returns:
            미처리 WALEntry 목록
        """
        entries = []

        try:
            from selfhealing.metrics.drift_metrics import record_wal_entries_recovered

            has_metrics = True
        except ImportError:
            has_metrics = False

        with self._lock:
            wal_files = sorted(self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"))

            for wal_file in wal_files:
                for entry in self._read_wal_file(wal_file):
                    if entry.sequence > last_processed_seq:
                        entries.append(entry)
                        self._recovered_entries += 1

        sorted_entries = sorted(entries, key=lambda e: e.sequence)

        if has_metrics and sorted_entries:
            record_wal_entries_recovered(len(sorted_entries))

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
                if self._current_file and wal_file == self._current_file:
                    continue

                max_seq = 0
                for entry in self._read_wal_file(wal_file):
                    max_seq = max(max_seq, entry.sequence)

                if max_seq > 0 and max_seq <= last_processed_seq:
                    try:
                        wal_file.unlink()
                        deleted_count += 1
                    except Exception:
                        pass

        return deleted_count
