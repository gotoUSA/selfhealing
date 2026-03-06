"""
CapacityReservationService — Capacity Reservation 서비스 싱글톤.

백그라운드 스케줄러가 EventCalendar을 주기적으로 확인하여
워밍/쿨다운을 자동 실행한다. Safety Valve를 매 주기 체크하여
하드 리밋 초과 시 즉시 CRITICAL 전환한다.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog

from selfhealing.services.capacity_reservation.event_calendar import (
    EventCalendar,
    EventStatus,
    ScheduledEvent,
)
from selfhealing.services.capacity_reservation.pre_warmer import (
    PreWarmer,
    SafetyValveMetricsProvider,
)
from selfhealing.settings.capacity_reservation import (
    CapacityReservationSettings,
    get_capacity_reservation_settings,
)

logger = structlog.get_logger()


class CapacityReservationService:
    """Capacity Reservation 서비스 — 싱글톤."""

    _instance: CapacityReservationService | None = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> CapacityReservationService:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def initialize(
        self,
        rate_controller: Any | None = None,
        pool_watchdog: Any | None = None,
        bulkhead: Any | None = None,
        graceful_degradation: Any | None = None,
        event_bus: Any | None = None,
        metrics_provider: SafetyValveMetricsProvider | None = None,
        recovery_gate: Any | None = None,
        state_backend: Any | None = None,
        settings: CapacityReservationSettings | None = None,
    ) -> None:
        """초기화. EventCalendar + PreWarmer 생성, StateBackend 기반 복원."""
        if self._initialized:
            return

        self._settings = settings or get_capacity_reservation_settings()
        self._calendar = EventCalendar(
            state_backend=state_backend,
            settings=self._settings,
        )
        self._pre_warmer = PreWarmer(
            calendar=self._calendar,
            rate_controller=rate_controller,
            pool_watchdog=pool_watchdog,
            bulkhead=bulkhead,
            graceful_degradation=graceful_degradation,
            event_bus=event_bus,
            metrics_provider=metrics_provider,
            recovery_gate=recovery_gate,
            state_backend=state_backend,
            settings=self._settings,
        )
        self._scheduler_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._initialized = True

        self._calendar.initialize()
        self._pre_warmer.initialize()

        logger.info(
            "capacity_reservation.service_initialized",
            enabled=self._settings.enabled,
            dry_run=self._settings.dry_run,
        )

    def register_event(self, event: ScheduledEvent) -> None:
        """이벤트 등록 + 유효성 검증."""
        self._ensure_initialized()

        active_count = len(self._calendar.get_active())
        if active_count >= self._settings.max_concurrent_events:
            raise ValueError(
                f"동시 진행 이벤트 상한 초과: "
                f"{active_count}/{self._settings.max_concurrent_events}"
            )

        self._calendar.register(event)

    def cancel_event(self, event_id: str) -> bool:
        """이벤트 취소. 워밍 진행 중이면 rollback 포함."""
        self._ensure_initialized()

        event = self._calendar.get_event(event_id)
        if event is None:
            return False

        if event.status in (EventStatus.WARMING, EventStatus.ACTIVE):
            self._pre_warmer.cool_down(event)

        return self._calendar.cancel(event_id)

    def get_status(self) -> dict:
        """현재 상태 조회."""
        self._ensure_initialized()

        return {
            "enabled": self._settings.enabled,
            "dry_run": self._settings.dry_run,
            "scheduler_running": (
                self._scheduler_thread is not None and self._scheduler_thread.is_alive()
            ),
            "active_events": [
                {
                    "event_id": e.event_id,
                    "name": e.name,
                    "status": e.status.value,
                    "start_time": e.start_time.isoformat(),
                    "end_time": e.end_time.isoformat(),
                }
                for e in self._calendar.get_active()
            ],
            "active_adjustments": self._pre_warmer.get_active_adjustments(),
            "safety_valve_active": self._pre_warmer.safety_valve_active,
        }

    @property
    def calendar(self) -> EventCalendar:
        """EventCalendar 접근."""
        self._ensure_initialized()
        return self._calendar

    @property
    def pre_warmer(self) -> PreWarmer:
        """PreWarmer 접근."""
        self._ensure_initialized()
        return self._pre_warmer

    def start(self) -> None:
        """스케줄러 시작 (백그라운드 스레드)."""
        self._ensure_initialized()

        if not self._settings.enabled:
            logger.info("capacity_reservation.service_disabled")
            return

        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            logger.warning("capacity_reservation.scheduler_already_running")
            return

        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="capacity-reservation-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

        logger.info(
            "capacity_reservation.scheduler_started",
            interval_seconds=self._settings.scheduler_interval_seconds,
        )

    def stop(self) -> None:
        """스케줄러 중지 + 진행 중 이벤트 cooldown."""
        self._ensure_initialized()

        self._stop_event.set()

        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=10)
            self._scheduler_thread = None

        for event in self._calendar.get_active():
            self._pre_warmer.cool_down(event)
            self._calendar.update_status(event.event_id, EventStatus.COMPLETED)

        logger.info("capacity_reservation.scheduler_stopped")

    def _scheduler_loop(self) -> None:
        """스케줄러 메인 루프."""
        while not self._stop_event.is_set():
            try:
                self._process_events()
                self._check_safety_valve()
            except Exception as exc:
                logger.error(
                    "capacity_reservation.scheduler_error",
                    error=str(exc),
                )

            self._stop_event.wait(self._settings.scheduler_interval_seconds)

    def _process_events(self) -> None:
        """워밍/쿨다운 대상 이벤트 처리."""
        for event in self._calendar.get_needs_warmup():
            self._calendar.update_status(event.event_id, EventStatus.WARMING)
            result = self._pre_warmer.warm_up(event)
            if result.success:
                self._calendar.update_status(event.event_id, EventStatus.ACTIVE)
            else:
                self._calendar.update_status(event.event_id, EventStatus.CANCELLED)
                logger.error(
                    "capacity_reservation.warmup_failed",
                    event_id=event.event_id,
                    errors=result.errors,
                )

        for event in self._calendar.get_needs_cooldown():
            self._calendar.update_status(event.event_id, EventStatus.COOLING_DOWN)
            self._pre_warmer.cool_down(event)
            self._calendar.update_status(event.event_id, EventStatus.COMPLETED)

        self._calendar.remove_completed()

    def _check_safety_valve(self) -> None:
        """Safety Valve 매 주기 체크."""
        if not self._calendar.is_event_period():
            return

        if self._pre_warmer.safety_valve_active:
            self._pre_warmer.check_safety_valve_recovery()
        elif self._pre_warmer.check_safety_valve():
            self._pre_warmer.emergency_override()

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "CapacityReservationService not initialized. Call initialize() first."
            )

    @classmethod
    def reset(cls) -> None:
        """싱글톤 리셋 (테스트용)."""
        with cls._singleton_lock:
            if cls._instance is not None and cls._instance._initialized:
                cls._instance._stop_event.set()
                if (
                    cls._instance._scheduler_thread is not None
                    and cls._instance._scheduler_thread.is_alive()
                ):
                    cls._instance._scheduler_thread.join(timeout=5)
            cls._instance = None
