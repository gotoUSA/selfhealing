"""
WAL 디스크 관리 모듈.

디스크 풀 처리, 우선순위 기반 Purge, 복구 체크 등을 담당합니다.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class WALDiskManagerMixin:
    """디스크 관리 관련 메서드."""

    def _handle_disk_full(self) -> None:
        """디스크 풀 상황 처리 (우선순위 기반 Purge 시도 후 Fail-Open 모드 전환)."""
        from selfhealing.audit.wal_pkg._models import WALState

        # 우선순위 기반 Purge 시도
        if self._config.priority_based_purge:
            if self._purge_by_priority():
                logger.info("[WAL] Priority-based purge succeeded, continuing normal operation")
                return

        # Purge 실패 또는 비활성화 시 Fail-Open 모드 전환
        self._state = WALState.DISK_FULL_FAILOPEN
        logger.critical("[WAL] DISK FULL - Switching to fail-open mode")

        # 메트릭 기록
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

    def _purge_by_priority(self) -> bool:
        """
        우선순위 기반 삭제로 디스크 공간 확보.

        Returns:
            True: 충분한 공간 확보 성공
            False: 공간 확보 실패
        """
        freed_bytes = 0
        target_free = self._config.max_file_size_bytes

        # CRITICAL 제외한 우선순위 순서로 삭제
        purge_priorities = self._config.purge_priority_order[:-1]

        for priority in purge_priorities:
            priority_pattern = f"{self._config.file_prefix}_{priority.lower()}_*.wal"
            priority_files = sorted(
                self._wal_dir.glob(priority_pattern),
                key=lambda f: f.stat().st_mtime,
            )

            for wal_file in priority_files:
                if freed_bytes >= target_free:
                    logger.info(f"[WAL] Priority purge complete, freed {freed_bytes} bytes")
                    return True

                try:
                    file_size = wal_file.stat().st_size
                    wal_file.unlink()
                    freed_bytes += file_size
                    logger.warning(
                        f"[WAL] Priority purge: deleted {wal_file.name} " f"(priority={priority}, size={file_size})"
                    )
                except Exception as e:
                    logger.error(f"[WAL] Failed to delete {wal_file}: {e}")

        # 우선순위 파일 없으면 일반 파일 중 오래된 것부터 삭제
        if freed_bytes < target_free:
            general_files = sorted(
                self._wal_dir.glob(f"{self._config.file_prefix}_*.wal"),
                key=lambda f: f.stat().st_mtime,
            )
            critical_min_bytes = self._config.critical_retention_min_mb * 1024 * 1024
            total_size = sum(f.stat().st_size for f in general_files)

            for wal_file in general_files:
                if freed_bytes >= target_free:
                    return True

                remaining_size = total_size - freed_bytes
                if remaining_size <= critical_min_bytes:
                    logger.warning(
                        f"[WAL] Priority purge stopped to protect CRITICAL logs " f"(remaining={remaining_size} bytes)"
                    )
                    break

                try:
                    file_size = wal_file.stat().st_size
                    wal_file.unlink()
                    freed_bytes += file_size
                    logger.warning(f"[WAL] General purge: deleted {wal_file.name} (size={file_size})")
                except Exception as e:
                    logger.error(f"[WAL] Failed to delete {wal_file}: {e}")

        if freed_bytes >= target_free:
            logger.info(f"[WAL] Priority purge complete, freed {freed_bytes} bytes")
            return True

        logger.critical(
            f"[WAL] Priority purge insufficient, freed only {freed_bytes} bytes "
            f"(target={target_free} bytes). CRITICAL logs at risk!"
        )
        return False

    def check_disk_recovery(self) -> bool:
        """
        디스크 여유 공간 확보 시 정상 모드 복귀.

        Returns:
            True: 정상 모드로 복귀
            False: 여전히 디스크 풀 상태
        """
        from selfhealing.audit.wal_pkg._models import WALState

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
