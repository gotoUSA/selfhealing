"""
Capacity Reservation — 예정 이벤트 기반 사전 용량 확보.

예정 이벤트(쿠폰 오픈, 타임세일 등)를 등록하고,
이벤트 N분 전에 기존 모듈(RateController/PoolWatchdog/Bulkhead/GracefulDegradation)을
사전 조정하는 오케스트레이터.
"""

from selfhealing.services.capacity_reservation.event_calendar import (
    EventCalendar,
    EventStatus,
    ScheduledEvent,
)
from selfhealing.services.capacity_reservation.pre_warmer import (
    AdjustmentRecord,
    CoolDownResult,
    PreWarmer,
    WarmUpResult,
)
from selfhealing.services.capacity_reservation.service import (
    CapacityReservationService,
)

__all__ = [
    "AdjustmentRecord",
    "CapacityReservationService",
    "CoolDownResult",
    "EventCalendar",
    "EventStatus",
    "PreWarmer",
    "ScheduledEvent",
    "WarmUpResult",
]
