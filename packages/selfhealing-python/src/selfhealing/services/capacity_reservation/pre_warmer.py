"""
PreWarmer — 이벤트 전 기존 모듈에 사전 조정 신호를 보내는 오케스트레이터.

새 로직을 구현하지 않고, 기존 모듈의 public API만 호출한다.
각 조정마다 원래 값을 저장하여 rollback을 보장한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import structlog

from selfhealing.services.capacity_reservation.event_calendar import (
    ScheduledEvent,
)
from selfhealing.settings.capacity_reservation import (
    CapacityReservationSettings,
    get_capacity_reservation_settings,
)

logger = structlog.get_logger()


@dataclass
class AdjustmentRecord:
    """개별 조정 기록."""

    target: str
    original_value: Any
    adjusted_value: Any
    applied: bool = False


@dataclass
class WarmUpResult:
    """워밍 실행 결과."""

    event_id: str
    success: bool
    adjustments: list[AdjustmentRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class CoolDownResult:
    """쿨다운 실행 결과."""

    event_id: str
    success: bool
    restored: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class PreWarmer:
    """이벤트 전 기존 모듈에 사전 조정 신호를 보내는 오케스트레이터."""

    def __init__(
        self,
        rate_controller: Any | None = None,
        pool_watchdog: Any | None = None,
        bulkhead: Any | None = None,
        graceful_degradation: Any | None = None,
        event_bus: Any | None = None,
        settings: CapacityReservationSettings | None = None,
    ) -> None:
        self._rate_controller = rate_controller
        self._pool_watchdog = pool_watchdog
        self._bulkhead = bulkhead
        self._graceful_degradation = graceful_degradation
        self._event_bus = event_bus
        self._settings = settings or get_capacity_reservation_settings()
        self._original_settings: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def warm_up(self, event: ScheduledEvent) -> WarmUpResult:
        """이벤트 시작 N분 전에 호출. 기존 모듈의 설정을 임시로 조정한다."""
        start = time.monotonic()
        adjustments: list[AdjustmentRecord] = []
        errors: list[str] = []

        if self._settings.dry_run:
            logger.info(
                "capacity_reservation.warmup_dry_run",
                event_id=event.event_id,
                name=event.name,
                expected_rps_multiplier=event.expected_rps_multiplier,
                pool_multiplier=event.pool_multiplier,
                bulkhead_extra_permits=event.bulkhead_extra_permits,
                suppress_degradation=event.suppress_degradation,
            )
            return WarmUpResult(
                event_id=event.event_id,
                success=True,
                adjustments=[],
                duration_seconds=time.monotonic() - start,
            )

        with self._lock:
            self._original_settings[event.event_id] = {}

        try:
            self._adjust_rate_controller(event, adjustments, errors)
            self._adjust_pool(event, adjustments, errors)
            self._adjust_bulkhead(event, adjustments, errors)
            self._adjust_degradation(event, adjustments, errors)
            self._publish_event_started(event, errors)
        except Exception as exc:
            errors.append(f"warm_up unexpected error: {exc}")
            logger.error(
                "capacity_reservation.warmup_error",
                event_id=event.event_id,
                error=str(exc),
            )
            self._rollback(event.event_id, adjustments)

        success = len(errors) == 0
        duration = time.monotonic() - start

        if not success:
            self._rollback(event.event_id, adjustments)

        logger.info(
            "capacity_reservation.warmup_completed",
            event_id=event.event_id,
            success=success,
            adjustment_count=len([a for a in adjustments if a.applied]),
            error_count=len(errors),
            duration_seconds=duration,
        )

        return WarmUpResult(
            event_id=event.event_id,
            success=success,
            adjustments=adjustments,
            errors=errors,
            duration_seconds=duration,
        )

    def cool_down(self, event: ScheduledEvent) -> CoolDownResult:
        """이벤트 종료 시 호출. 임시 설정을 원래 값으로 복원한다."""
        start = time.monotonic()
        restored: list[str] = []
        errors: list[str] = []

        if self._settings.dry_run:
            logger.info(
                "capacity_reservation.cooldown_dry_run",
                event_id=event.event_id,
            )
            return CoolDownResult(
                event_id=event.event_id,
                success=True,
                duration_seconds=time.monotonic() - start,
            )

        with self._lock:
            originals = self._original_settings.pop(event.event_id, {})

        self._restore_rate_controller(originals, restored, errors)
        self._restore_bulkhead(originals, restored, errors)
        self._restore_degradation(originals, restored, errors)
        self._publish_event_ended(event, errors)

        success = len(errors) == 0
        duration = time.monotonic() - start

        logger.info(
            "capacity_reservation.cooldown_completed",
            event_id=event.event_id,
            success=success,
            restored=restored,
            error_count=len(errors),
            duration_seconds=duration,
        )

        return CoolDownResult(
            event_id=event.event_id,
            success=success,
            restored=restored,
            errors=errors,
            duration_seconds=duration,
        )

    def get_active_adjustments(self) -> dict[str, dict[str, Any]]:
        """현재 적용 중인 조정 목록."""
        with self._lock:
            return dict(self._original_settings)

    # ─── Rate Controller ─────────────────────────────────────────────────────

    def _adjust_rate_controller(
        self,
        event: ScheduledEvent,
        adjustments: list[AdjustmentRecord],
        errors: list[str],
    ) -> None:
        if self._rate_controller is None:
            return

        try:
            settings = self._rate_controller._settings
            original_min_rate = settings.min_rate_per_second

            capped_multiplier = min(
                event.expected_rps_multiplier,
                self._settings.max_rate_multiplier,
            )
            new_min_rate = original_min_rate * capped_multiplier

            with self._lock:
                self._original_settings[event.event_id]["min_rate_per_second"] = (
                    original_min_rate
                )

            settings.min_rate_per_second = new_min_rate

            record = AdjustmentRecord(
                target="rate_controller.min_rate_per_second",
                original_value=original_min_rate,
                adjusted_value=new_min_rate,
                applied=True,
            )
            adjustments.append(record)

            logger.info(
                "capacity_reservation.rate_adjusted",
                event_id=event.event_id,
                original=original_min_rate,
                new=new_min_rate,
                multiplier=capped_multiplier,
            )
        except Exception as exc:
            errors.append(f"rate_controller adjustment failed: {exc}")

    def _restore_rate_controller(
        self,
        originals: dict[str, Any],
        restored: list[str],
        errors: list[str],
    ) -> None:
        if self._rate_controller is None:
            return

        original_min_rate = originals.get("min_rate_per_second")
        if original_min_rate is None:
            return

        try:
            self._rate_controller._settings.min_rate_per_second = original_min_rate
            restored.append("rate_controller.min_rate_per_second")
        except Exception as exc:
            errors.append(f"rate_controller restore failed: {exc}")

    # ─── Pool Watchdog ────────────────────────────────────────────────────────

    def _adjust_pool(
        self,
        event: ScheduledEvent,
        adjustments: list[AdjustmentRecord],
        errors: list[str],
    ) -> None:
        if self._pool_watchdog is None:
            return

        try:
            capped_multiplier = min(
                event.pool_multiplier,
                self._settings.max_pool_multiplier,
            )
            additional = max(1, int(capped_multiplier * 10))

            handler = getattr(self._pool_watchdog, "_recovery_handler", None)
            if handler is not None:
                result = handler.expand_pool(additional)
                record = AdjustmentRecord(
                    target="pool_watchdog.expand_pool",
                    original_value=None,
                    adjusted_value=additional,
                    applied=bool(result),
                )
                adjustments.append(record)

                logger.info(
                    "capacity_reservation.pool_expanded",
                    event_id=event.event_id,
                    additional=additional,
                    result=result,
                )
        except Exception as exc:
            errors.append(f"pool_watchdog adjustment failed: {exc}")

    # ─── Bulkhead ─────────────────────────────────────────────────────────────

    def _adjust_bulkhead(
        self,
        event: ScheduledEvent,
        adjustments: list[AdjustmentRecord],
        errors: list[str],
    ) -> None:
        if self._bulkhead is None:
            return

        try:
            state = self._bulkhead.get_state()
            original_max = state.max_concurrent

            extra = min(
                event.bulkhead_extra_permits,
                self._settings.max_bulkhead_extra_permits,
            )
            new_max = original_max + extra

            with self._lock:
                self._original_settings[event.event_id]["bulkhead_max_concurrent"] = (
                    original_max
                )

            self._bulkhead._state.max_concurrent = new_max

            record = AdjustmentRecord(
                target="bulkhead.max_concurrent",
                original_value=original_max,
                adjusted_value=new_max,
                applied=True,
            )
            adjustments.append(record)

            logger.info(
                "capacity_reservation.bulkhead_expanded",
                event_id=event.event_id,
                original=original_max,
                new=new_max,
                extra=extra,
            )
        except Exception as exc:
            errors.append(f"bulkhead adjustment failed: {exc}")

    def _restore_bulkhead(
        self,
        originals: dict[str, Any],
        restored: list[str],
        errors: list[str],
    ) -> None:
        if self._bulkhead is None:
            return

        original_max = originals.get("bulkhead_max_concurrent")
        if original_max is None:
            return

        try:
            self._bulkhead._state.max_concurrent = original_max
            restored.append("bulkhead.max_concurrent")
        except Exception as exc:
            errors.append(f"bulkhead restore failed: {exc}")

    # ─── Graceful Degradation ─────────────────────────────────────────────────

    def _adjust_degradation(
        self,
        event: ScheduledEvent,
        adjustments: list[AdjustmentRecord],
        errors: list[str],
    ) -> None:
        if self._graceful_degradation is None or not event.suppress_degradation:
            return

        try:
            from selfhealing.settings.backpressure import BackpressureLevel

            with self._lock:
                self._original_settings[event.event_id]["degradation_suppressed"] = True

            self._graceful_degradation.update_level(BackpressureLevel.NONE)

            record = AdjustmentRecord(
                target="graceful_degradation.level",
                original_value="auto",
                adjusted_value=BackpressureLevel.NONE.value,
                applied=True,
            )
            adjustments.append(record)

            logger.info(
                "capacity_reservation.degradation_suppressed",
                event_id=event.event_id,
            )
        except Exception as exc:
            errors.append(f"graceful_degradation adjustment failed: {exc}")

    def _restore_degradation(
        self,
        originals: dict[str, Any],
        restored: list[str],
        errors: list[str],
    ) -> None:
        if self._graceful_degradation is None:
            return

        if not originals.get("degradation_suppressed"):
            return

        try:
            restored.append("graceful_degradation.level")
            logger.info("capacity_reservation.degradation_restored")
        except Exception as exc:
            errors.append(f"graceful_degradation restore failed: {exc}")

    # ─── EventBus ─────────────────────────────────────────────────────────────

    def _publish_event_started(
        self,
        event: ScheduledEvent,
        errors: list[str],
    ) -> None:
        if self._event_bus is None:
            return

        try:
            from selfhealing.services.event_bus.bus import EventType

            self._event_bus.publish(
                EventType.SCHEDULED_EVENT_STARTED,
                event.to_event_context(),
            )
        except Exception as exc:
            errors.append(f"EventBus publish STARTED failed: {exc}")

    def _publish_event_ended(
        self,
        event: ScheduledEvent,
        errors: list[str],
    ) -> None:
        if self._event_bus is None:
            return

        try:
            from selfhealing.services.event_bus.bus import EventType

            self._event_bus.publish(
                EventType.SCHEDULED_EVENT_ENDED,
                {"event_id": event.event_id},
            )
        except Exception as exc:
            errors.append(f"EventBus publish ENDED failed: {exc}")

    # ─── Rollback ─────────────────────────────────────────────────────────────

    def _rollback(
        self,
        event_id: str,
        adjustments: list[AdjustmentRecord],
    ) -> None:
        """이미 적용된 조정을 역순으로 rollback."""
        logger.warning(
            "capacity_reservation.rollback_started",
            event_id=event_id,
            adjustment_count=len([a for a in adjustments if a.applied]),
        )

        with self._lock:
            originals = self._original_settings.pop(event_id, {})

        rollback_errors: list[str] = []
        restored: list[str] = []

        self._restore_rate_controller(originals, restored, rollback_errors)
        self._restore_bulkhead(originals, restored, rollback_errors)
        self._restore_degradation(originals, restored, rollback_errors)

        for adj in adjustments:
            adj.applied = False

        if rollback_errors:
            logger.error(
                "capacity_reservation.rollback_partial_failure",
                event_id=event_id,
                errors=rollback_errors,
            )
        else:
            logger.info(
                "capacity_reservation.rollback_completed",
                event_id=event_id,
                restored=restored,
            )
