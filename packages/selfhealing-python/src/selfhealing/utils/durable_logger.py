# packages/selfhealing-python/src/selfhealing/utils/durable_logger.py
"""
DurableEventLogger - WAL 보장 비동기 로거.

AsyncHealingLogger의 WAL 통합 확장 버전.
모든 이벤트가 WAL에 먼저 기록된 후 큐에 추가됨.

특징:
- WAL-First: 메모리 큐 전에 WAL에 기록 (디스크 영속화)
- Fail-Open: WAL 실패해도 서비스 계속 운영
- 복구 지원: 재시작 시 WAL에서 미처리 이벤트 복구

Usage:
    from selfhealing.audit.wal import WriteAheadLog
    from selfhealing.utils.durable_logger import DurableEventLogger

    wal = WriteAheadLog()
    DurableEventLogger.configure_wal(wal)
    DurableEventLogger.configure(flush_callback=send_to_server)
    DurableEventLogger.start()

    # 모든 이벤트가 WAL에 먼저 기록됨
    DurableEventLogger.log({'type': 'retry', 'service': 'payment'})
"""

from __future__ import annotations

import queue
import threading
import time
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.utils.async_logger import (
    SEVERITY_PRIORITY_MAP,
    AsyncHealingLogger,
    EventSeverity,
    LogFlushPriority,
    PrioritizedEvent,
)

if TYPE_CHECKING:
    from selfhealing.audit.wal import WriteAheadLog

__all__ = ["DurableEventLogger"]

logger = structlog.get_logger()


class DurableEventLogger(AsyncHealingLogger):
    """
    WAL 보장 비동기 로거.

    AsyncHealingLogger를 상속하며 WAL-First 정책을 강제합니다.
    모든 이벤트가 WAL에 먼저 기록된 후 메모리 큐에 추가됩니다.

    주요 차이점:
    - WAL 기록이 기본 동작 (옵션이 아님)
    - 모든 이벤트에 WAL 시퀀스 번호 포함
    - 재시작 시 WAL에서 복구 가능
    """

    # 추가 통계
    _durable_stats = {
        "wal_writes_success": 0,
        "wal_writes_failed": 0,
        "recovered_events": 0,
    }

    @classmethod
    def configure_wal(
        cls,
        wal: WriteAheadLog,
    ) -> None:
        """
        WAL 인스턴스 설정 (필수).

        DurableEventLogger는 WAL이 필수입니다.

        Args:
            wal: WriteAheadLog 인스턴스
        """
        with cls._lock:
            cls._wal = wal
        logger.info("durable_event_logger.wal_configured_durable_mode")

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        """
        WAL-First 이벤트 로깅.

        처리 순서:
        1. WAL에 기록 (디스크 영속화) - 필수
        2. 메모리 큐에 추가 (배치 처리용)

        Args:
            event: 힐링 이벤트 딕셔너리
            severity: 이벤트 심각도
        """
        enriched_event = {
            **event,
            "severity": severity.name,
            "timestamp": time.time(),
        }

        # 1. WAL-First: 디스크에 먼저 기록 (필수)
        wal_seq = -1
        if cls._wal:
            try:
                wal_seq = cls._wal.write(enriched_event)
                enriched_event["_wal_seq"] = wal_seq
                with cls._lock:
                    cls._stats["wal_writes"] += 1
                    cls._durable_stats["wal_writes_success"] += 1
            except Exception as e:
                with cls._lock:
                    cls._durable_stats["wal_writes_failed"] += 1
                # Fail-Open: WAL 실패해도 계속 진행
                logger.warning(
                    "durable_event_logger.wal_write_failed_continuing",
                    error=e,
                )
        else:
            logger.warning("durable_event_logger.wal_configured_event_durable")

        with cls._lock:
            cls._stats["events_logged"] += 1

        # 2. Priority 결정 및 큐에 추가
        priority = SEVERITY_PRIORITY_MAP.get(severity, LogFlushPriority.INFO)
        prioritized = PrioritizedEvent(
            priority=priority,
            timestamp=time.time(),
            event=enriched_event,
        )

        if severity in cls.IMMEDIATE_SEVERITIES:
            # CRITICAL: 스레드 풀 사용
            if cls._critical_executor:
                cls._critical_executor.submit(cls._flush_immediate, [enriched_event])
            else:
                threading.Thread(target=cls._flush_immediate, args=([enriched_event],), daemon=True).start()
        else:
            # 일반: Priority Queue에 추가 (배압 적용)
            try:
                if cls._priority_queue:
                    cls._priority_queue.put_nowait(prioritized)
            except queue.Full:
                with cls._lock:
                    cls._stats["queue_overflows"] += 1
                # 큐가 가득 차도 WAL에는 이미 기록됨
                logger.warning("durable_event_logger.queue_full_event_wal")

    @classmethod
    def recover_from_wal(cls, last_processed_seq: int = 0) -> int:
        """
        WAL에서 미처리 이벤트 복구.

        프로세스 재시작 시 호출하여 WAL의 미처리 이벤트를 큐에 재추가.
        CheckpointManager와 함께 사용하여 정확한 복구 지점 결정.

        Args:
            last_processed_seq: 마지막 처리된 시퀀스 번호 (기본값: 0)

        Returns:
            복구된 이벤트 수
        """
        if not cls._wal:
            logger.warning("durable_event_logger.wal_configured_cannot_recover")
            return 0

        try:
            entries = cls._wal.recover_unprocessed(last_processed_seq)

            if not entries:
                logger.info("durable_event_logger.no_events_recover_wal")
                return 0

            recovered_count = 0
            for entry in entries:
                # WAL 엔트리를 Priority Queue에 추가
                event = entry.data
                event["_wal_seq"] = entry.sequence
                event["_recovered"] = True

                # 복구된 이벤트는 일반 우선순위로 처리
                prioritized = PrioritizedEvent(
                    priority=LogFlushPriority.INFO,
                    timestamp=entry.timestamp,
                    event=event,
                )

                if cls._priority_queue:
                    try:
                        cls._priority_queue.put_nowait(prioritized)
                        recovered_count += 1
                    except queue.Full:
                        logger.warning("durable_event_logger.queue_full_during_recovery")
                        break

            with cls._lock:
                cls._durable_stats["recovered_events"] += recovered_count

            logger.info(
                "durable_event_logger.recovered_events_wal",
                recovered_count=recovered_count,
            )
            return recovered_count

        except Exception as e:
            logger.exception(
                "durable_event_logger.wal_recovery_failed",
                error=e,
            )
            return 0

    @classmethod
    def get_durable_stats(cls) -> dict[str, int]:
        """DurableEventLogger 전용 통계 조회."""
        with cls._lock:
            base_stats = cls._stats.copy()
            base_stats.update(cls._durable_stats)
            if cls._priority_queue:
                base_stats["current_queue_size"] = cls._priority_queue.qsize()
            else:
                base_stats["current_queue_size"] = 0
            return base_stats

    @classmethod
    def reset(cls) -> None:
        """상태 초기화 (테스트용)"""
        super().reset()
        with cls._lock:
            cls._durable_stats = {
                "wal_writes_success": 0,
                "wal_writes_failed": 0,
                "recovered_events": 0,
            }
