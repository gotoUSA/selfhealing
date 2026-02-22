"""
알림 전송 최종 실패 기록기.

전송 실패 시 디스크 JSONL 파일에 기록하고, 디스크 기록 실패 시 메모리 버퍼에 저장합니다.
재시도를 위한 미처리 알림 조회 기능을 제공합니다.
"""

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

DEFAULT_FALLBACK_PATH = "/var/log/selfhealing/notification_fallback.jsonl"


class NotificationFallbackRecorder:
    """
    알림 전송 실패 기록기.

    3단계 폴백:
    1. Celery autoretry (max 3회, 30초 간격)
    2. JSONL 파일 기록 시도
    3. 파일 기록 실패 시 메모리 버퍼에 저장
    """

    def __init__(
        self,
        file_path: str = DEFAULT_FALLBACK_PATH,
        max_memory_entries: int = 1000,
    ):
        self._file_path = Path(file_path)
        self._memory_buffer: deque[dict] = deque(maxlen=max_memory_entries)
        self._lock = threading.RLock()

    def record_failed_notification(
        self,
        dedup_key: str,
        notification_type: str,
        event_data: dict[str, Any],
        error: str,
    ) -> None:
        """실패한 알림을 기록합니다."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dedup_key": dedup_key,
            "notification_type": notification_type,
            "event_data": event_data,
            "error": str(error),
        }

        with self._lock:
            if not self._write_to_file(entry):
                self._write_to_memory(entry)

    def _write_to_file(self, entry: dict) -> bool:
        """JSONL 파일에 기록."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file_path, "a") as f:
                json.dump(entry, f, ensure_ascii=False)
                f.write("\n")
            return True
        except Exception as e:
            logger.warning(
                "notification_fallback.file_write_failed",
                error=e,
            )
            return False

    def _write_to_memory(self, entry: dict) -> None:
        """메모리 버퍼에 저장 (최종 폴백)."""
        self._memory_buffer.append(entry)
        logger.debug(
            "notification_fallback.stored_memory_buffer_entries",
            count=len(self._memory_buffer),
        )

    def get_pending_notifications(self) -> list[dict]:
        """미처리 알림 목록을 반환 (재시도용)."""
        with self._lock:
            return list(self._memory_buffer)

    def clear_memory_buffer(self) -> None:
        """메모리 버퍼를 비웁니다."""
        with self._lock:
            self._memory_buffer.clear()
