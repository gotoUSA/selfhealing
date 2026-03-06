"""
EventCalendar — 예정 이벤트 등록/관리/스케줄링.

인메모리 dict 기반 이벤트 캘린더.
이벤트 수가 많지 않으므로 (일 수십 건 이하) 인메모리가 기본이다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock

import structlog

logger = structlog.get_logger()


class EventStatus(str, Enum):
    """예정 이벤트 상태."""

    PENDING = "pending"
    WARMING = "warming"
    ACTIVE = "active"
    COOLING_DOWN = "cooling_down"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledEvent:
    """예정 이벤트 정의."""

    name: str
    start_time: datetime
    end_time: datetime
    expected_rps_multiplier: float = 2.0
    pool_multiplier: float = 1.5
    bulkhead_extra_permits: int = 50
    suppress_degradation: bool = True
    warmup_minutes: int = 5
    tags: list[str] = field(default_factory=list)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: EventStatus = EventStatus.PENDING

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        if self.end_time.tzinfo is None:
            self.end_time = self.end_time.replace(tzinfo=timezone.utc)

    @property
    def warmup_time(self) -> datetime:
        """워밍 시작 시각."""
        from datetime import timedelta

        return self.start_time - timedelta(minutes=self.warmup_minutes)

    def to_event_context(self) -> dict:
        """EventBus/ML context용 메타데이터."""
        return {
            "event_id": self.event_id,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "expected_rps_multiplier": self.expected_rps_multiplier,
            "tags": self.tags,
            "scheduled_event": True,
        }


class EventCalendar:
    """예정 이벤트 캘린더 — 등록/조회/스케줄링."""

    def __init__(self) -> None:
        self._events: dict[str, ScheduledEvent] = {}
        self._lock = Lock()

    def register(self, event: ScheduledEvent) -> None:
        """이벤트 등록. 시작 시간이 과거이면 ValueError."""
        now = datetime.now(timezone.utc)
        if event.start_time <= now:
            raise ValueError(
                f"이벤트 시작 시간이 과거입니다: {event.start_time.isoformat()}"
            )
        if event.end_time <= event.start_time:
            raise ValueError(
                f"종료 시간이 시작 시간보다 빠릅니다: "
                f"start={event.start_time.isoformat()}, end={event.end_time.isoformat()}"
            )

        with self._lock:
            if event.event_id in self._events:
                raise ValueError(f"이미 등록된 이벤트 ID: {event.event_id}")

            overlapping = self._find_overlapping(event)
            if overlapping:
                logger.warning(
                    "capacity_reservation.event_overlap",
                    new_event=event.event_id,
                    overlapping=[e.event_id for e in overlapping],
                )

            self._events[event.event_id] = event
            logger.info(
                "capacity_reservation.event_registered",
                event_id=event.event_id,
                name=event.name,
                start_time=event.start_time.isoformat(),
                end_time=event.end_time.isoformat(),
                warmup_minutes=event.warmup_minutes,
            )

    def cancel(self, event_id: str) -> bool:
        """이벤트 취소. 존재하지 않으면 False."""
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                return False
            event.status = EventStatus.CANCELLED
            logger.info(
                "capacity_reservation.event_cancelled",
                event_id=event_id,
                previous_status=event.status.value,
            )
            return True

    def get_upcoming(self, within_minutes: int = 60) -> list[ScheduledEvent]:
        """워밍 시작 시각이 N분 이내인 PENDING 이벤트 조회."""
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=within_minutes)
        with self._lock:
            return [
                e
                for e in self._events.values()
                if e.status == EventStatus.PENDING and e.warmup_time <= cutoff
            ]

    def get_needs_warmup(self) -> list[ScheduledEvent]:
        """워밍 시작 시각에 도달한 PENDING 이벤트 조회."""
        now = datetime.now(timezone.utc)
        with self._lock:
            return [
                e
                for e in self._events.values()
                if e.status == EventStatus.PENDING and e.warmup_time <= now
            ]

    def get_needs_cooldown(self) -> list[ScheduledEvent]:
        """종료 시각에 도달한 ACTIVE 이벤트 조회."""
        now = datetime.now(timezone.utc)
        with self._lock:
            return [
                e
                for e in self._events.values()
                if e.status == EventStatus.ACTIVE and e.end_time <= now
            ]

    def get_active(self) -> list[ScheduledEvent]:
        """현재 ACTIVE 또는 WARMING 상태 이벤트 조회."""
        with self._lock:
            return [
                e
                for e in self._events.values()
                if e.status in (EventStatus.ACTIVE, EventStatus.WARMING)
            ]

    def is_event_period(self) -> bool:
        """현재 시각이 이벤트 기간인지 여부. ML context 주입에 사용."""
        return len(self.get_active()) > 0

    def update_status(self, event_id: str, status: EventStatus) -> None:
        """이벤트 상태 업데이트."""
        with self._lock:
            event = self._events.get(event_id)
            if event is not None:
                event.status = status

    def get_event(self, event_id: str) -> ScheduledEvent | None:
        """이벤트 ID로 조회."""
        with self._lock:
            return self._events.get(event_id)

    def remove_completed(self) -> int:
        """완료/취소 이벤트 정리. 제거된 수 반환."""
        with self._lock:
            to_remove = [
                eid
                for eid, e in self._events.items()
                if e.status in (EventStatus.COMPLETED, EventStatus.CANCELLED)
            ]
            for eid in to_remove:
                del self._events[eid]
            return len(to_remove)

    def _find_overlapping(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        """시간이 겹치는 기존 이벤트 검색 (lock 내부 호출)."""
        overlapping = []
        for existing in self._events.values():
            if existing.status in (EventStatus.COMPLETED, EventStatus.CANCELLED):
                continue
            if (
                event.start_time < existing.end_time
                and event.end_time > existing.start_time
            ):
                overlapping.append(existing)
        return overlapping
