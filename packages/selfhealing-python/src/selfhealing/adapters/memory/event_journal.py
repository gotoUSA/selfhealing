"""
In-Memory Event Journal Repository.

스레드 안전 인메모리 구현. 테스트 및 단일 프로세스용.
InMemoryCircuitBreakerStateRepository (adapters/memory/circuit_breaker.py) 패턴을 따른다.
"""

from __future__ import annotations

import threading

import structlog

from selfhealing.interfaces.event_journal import (
    EventJournalRepository,
    JournalEntry,
    JournalQueryFilter,
    JournalQueryResult,
)

logger = structlog.get_logger()


class InMemoryEventJournalRepository(EventJournalRepository):
    """스레드 안전 인메모리 구현. 테스트 및 단일 프로세스용."""

    def __init__(self, max_entries: int = 10000):
        self._entries: list[JournalEntry] = []
        self._lock = threading.RLock()
        self._next_sequence = 1
        self._max_entries = max_entries

    def append(self, entry: JournalEntry) -> int:
        with self._lock:
            seq = self._next_sequence
            self._next_sequence += 1
            stored = JournalEntry(
                sequence=seq,
                event_type=entry.event_type,
                source=entry.source,
                timestamp=entry.timestamp,
                service_name=entry.service_name,
                context=entry.context,
                region=entry.region,
                tier_id=entry.tier_id,
            )
            self._entries.append(stored)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries :]
            return seq

    def query(self, filter: JournalQueryFilter) -> JournalQueryResult:
        with self._lock:
            matched = self._apply_filter(filter)
            total_count = len(matched)
            truncated = total_count > filter.limit
            entries = matched[: filter.limit]
            return JournalQueryResult(
                entries=entries,
                truncated=truncated,
                total_count=total_count,
            )

    def get_sequence_range(
        self,
        start_sequence: int,
        end_sequence: int,
    ) -> list[JournalEntry]:
        with self._lock:
            return [
                e for e in self._entries if start_sequence <= e.sequence < end_sequence
            ]

    def get_latest_sequence(self) -> int:
        with self._lock:
            if not self._entries:
                return 0
            return self._entries[-1].sequence

    def count(self, filter: JournalQueryFilter) -> int:
        with self._lock:
            return len(self._apply_filter(filter))

    def _apply_filter(self, filter: JournalQueryFilter) -> list[JournalEntry]:
        """필터 조건에 맞는 엔트리를 시퀀스 오름차순으로 반환한다."""
        results: list[JournalEntry] = []
        for entry in self._entries:
            if (
                filter.event_types is not None
                and entry.event_type not in filter.event_types
            ):
                continue
            if (
                filter.service_name is not None
                and entry.service_name != filter.service_name
            ):
                continue
            if filter.start_time is not None and entry.timestamp < filter.start_time:
                continue
            if filter.end_time is not None and entry.timestamp >= filter.end_time:
                continue
            if filter.region is not None and entry.region != filter.region:
                continue
            results.append(entry)
        return results
