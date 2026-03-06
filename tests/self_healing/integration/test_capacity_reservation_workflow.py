"""
Capacity Reservation Workflow Integration Tests.

Mock 기반 통합 테스트 — DB 불필요, pytest-xdist 병렬 실행 가능.

Test Categories:
    A. Full Lifecycle:
        - 이벤트 등록 → warm_up → 이벤트 종료 → cool_down → 설정 원복
    B. Overlapping Events (Re-evaluation):
        - 이벤트 A(2x) + B(4x) → MAX=4x → A종료 → B(4x) 유지 → B종료 → Baseline 복원
    C. Safety Valve Lifecycle:
        - 이벤트 중 CPU 초과 → CRITICAL → min_hold 경과 → 복귀
    D. ML Decision Authority Conflict Prevention:
        - 이벤트 기간 SpikeClassifier가 HEALTHY_SURGE 반환
    E. Cancel During Warming:
        - 워밍 진행 중 이벤트 취소 → rollback 완료

Note: All tests use in-memory mock objects - no DB dependency.
      This enables parallel test execution with pytest-xdist.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from selfhealing.services.capacity_reservation.event_calendar import (
    EventCalendar,
    EventStatus,
    ScheduledEvent,
)
from selfhealing.services.capacity_reservation.pre_warmer import PreWarmer
from selfhealing.services.capacity_reservation.service import (
    CapacityReservationService,
)
from selfhealing.services.predictive_forecaster.proactive_action import (
    SpikeClassifier,
    SpikeType,
)
from selfhealing.settings.capacity_reservation import CapacityReservationSettings


# ─── Mock Infrastructure ─────────────────────────────────────────────────────


@dataclass
class MockRateControllerSettings:
    min_rate_per_second: float = 10.0


@dataclass
class MockBulkheadState:
    max_concurrent: int = 50
    active_count: int = 0
    waiting_count: int = 0
    rejected_count: int = 0


class MockRateController:
    def __init__(self):
        self._settings = MockRateControllerSettings()


class MockBulkhead:
    def __init__(self):
        self._state = MockBulkheadState()

    def get_state(self):
        return self._state


class MockMetricsProvider:
    def __init__(self, cpu=0.5, error_rate=0.01):
        self._cpu = cpu
        self._error_rate = error_rate

    def get_cpu_usage(self) -> float:
        return self._cpu

    def get_error_rate(self) -> float:
        return self._error_rate


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _future(minutes: int = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def _make_event(**kwargs) -> ScheduledEvent:
    defaults = {
        "name": "test-event",
        "start_time": _future(30),
        "end_time": _future(90),
    }
    defaults.update(kwargs)
    return ScheduledEvent(**defaults)


# =============================================================================
# A. Full Lifecycle Integration
# =============================================================================


class TestCapacityReservationFullLifecycle:
    """
    이벤트 등록 → warm_up → cool_down → 설정 원복 전체 라이프사이클.

    Validates:
    - warm_up이 RateController/Bulkhead를 조정
    - cool_down이 Global Baseline으로 완전 복원
    - EventBus에 STARTED/ENDED 이벤트 발행
    """

    def setup_method(self):
        self.settings = CapacityReservationSettings(dry_run=False)
        self.rate_controller = MockRateController()
        self.bulkhead = MockBulkhead()
        self.degradation = MagicMock()
        self.event_bus = MagicMock()
        self.calendar = EventCalendar(settings=self.settings)
        self.pre_warmer = PreWarmer(
            calendar=self.calendar,
            rate_controller=self.rate_controller,
            bulkhead=self.bulkhead,
            graceful_degradation=self.degradation,
            event_bus=self.event_bus,
            settings=self.settings,
        )

    def test_full_lifecycle_warm_up_and_cool_down(self):
        """
        Purpose:
            이벤트 등록 → warm_up → cool_down → 설정 원복 전체 검증.
        Expected:
            - warm_up 후 rate/bulkhead가 조정됨
            - cool_down 후 원래 값으로 복원됨
            - EventBus에 STARTED/ENDED 발행됨
        """
        # Given
        original_rate = self.rate_controller._settings.min_rate_per_second
        original_bulkhead = self.bulkhead._state.max_concurrent
        event = _make_event(
            expected_rps_multiplier=3.0,
            bulkhead_extra_permits=40,
        )
        self.calendar.register(event)
        self.calendar.update_status(event.event_id, EventStatus.ACTIVE)

        # When — warm_up
        warm_result = self.pre_warmer.warm_up(event)

        # Then — adjustments applied
        assert warm_result.success is True
        assert self.rate_controller._settings.min_rate_per_second == original_rate * 3.0
        assert self.bulkhead._state.max_concurrent == original_bulkhead + 40
        assert self.event_bus.emit.call_count == 1

        # When — cool_down
        self.calendar.update_status(event.event_id, EventStatus.COOLING_DOWN)
        cool_result = self.pre_warmer.cool_down(event)

        # Then — baseline restored
        assert cool_result.success is True
        assert self.rate_controller._settings.min_rate_per_second == original_rate
        assert self.bulkhead._state.max_concurrent == original_bulkhead
        assert self.event_bus.emit.call_count == 2


# =============================================================================
# B. Overlapping Events Re-evaluation
# =============================================================================


class TestCapacityReservationOverlappingEvents:
    """
    이벤트 겹침 시 Re-evaluation (MAX 재계산) 검증.

    Validates:
    - A(2x) + B(4x) → MAX=4x
    - A 종료 → B(4x) 유지
    - B 종료 → Baseline 복원
    """

    def setup_method(self):
        self.settings = CapacityReservationSettings(dry_run=False)
        self.rate_controller = MockRateController()
        self.bulkhead = MockBulkhead()
        self.calendar = EventCalendar(settings=self.settings)
        self.pre_warmer = PreWarmer(
            calendar=self.calendar,
            rate_controller=self.rate_controller,
            bulkhead=self.bulkhead,
            settings=self.settings,
        )

    def test_overlapping_events_reevaluation(self):
        """
        Purpose:
            이벤트 A(2x) + B(4x) 겹침 → MAX 적용 → A종료 → B유지 → B종료 → Baseline.
        Expected:
            - 겹침 시 MAX(4x) 적용
            - A 종료 후 B의 4x 유지
            - B 종료 후 Baseline 원복
        """
        original_rate = self.rate_controller._settings.min_rate_per_second

        # Given — 두 이벤트 등록 및 활성화
        e_a = _make_event(
            event_id="event-A", expected_rps_multiplier=2.0, bulkhead_extra_permits=20
        )
        e_b = _make_event(
            event_id="event-B", expected_rps_multiplier=4.0, bulkhead_extra_permits=60
        )

        self.calendar.register(e_a)
        self.calendar.register(e_b)
        self.calendar.update_status("event-A", EventStatus.ACTIVE)
        self.calendar.update_status("event-B", EventStatus.ACTIVE)

        # When — warm_up 모두 실행
        self.pre_warmer.warm_up(e_a)
        self.pre_warmer.warm_up(e_b)

        # Then — MAX 적용: rate=4x, bulkhead=+60
        assert self.rate_controller._settings.min_rate_per_second == original_rate * 4.0

        # When — A 종료
        self.calendar.update_status("event-A", EventStatus.COOLING_DOWN)
        self.pre_warmer.cool_down(e_a)

        # Then — B의 배율(4x) 유지
        assert self.rate_controller._settings.min_rate_per_second == original_rate * 4.0

        # When — B 종료
        self.calendar.update_status("event-B", EventStatus.COOLING_DOWN)
        self.pre_warmer.cool_down(e_b)

        # Then — Baseline 복원
        assert self.rate_controller._settings.min_rate_per_second == original_rate


# =============================================================================
# C. Safety Valve Lifecycle
# =============================================================================


class TestCapacityReservationSafetyValveLifecycle:
    """
    Safety Valve 발동 → 유지 → 복귀 라이프사이클 검증.

    Validates:
    - CPU 초과 시 Safety Valve 발동
    - min_hold 내 복구 차단
    - 조건 충족 시 복귀
    """

    def setup_method(self):
        self.settings = CapacityReservationSettings(
            dry_run=False,
            safety_valve_cpu_threshold=0.95,
            safety_valve_min_hold_seconds=30,
        )
        self.metrics = MockMetricsProvider(cpu=0.5, error_rate=0.01)
        self.recovery_gate = MagicMock()
        self.recovery_gate.check_recovery_allowed.return_value = (True, "ok")
        self.calendar = EventCalendar(settings=self.settings)
        self.pre_warmer = PreWarmer(
            calendar=self.calendar,
            metrics_provider=self.metrics,
            recovery_gate=self.recovery_gate,
            settings=self.settings,
        )

    def test_safety_valve_full_lifecycle(self):
        """
        Purpose:
            정상 → CPU 초과 → Safety Valve 발동 → min_hold 경과 + 안정 → 복귀.
        Expected:
            - 발동 후 safety_valve_active == True
            - min_hold 내 복구 차단
            - 조건 충족 시 복귀
        """
        # Phase 1: 정상 상태
        assert self.pre_warmer.check_safety_valve() is False
        assert self.pre_warmer.safety_valve_active is False

        # Phase 2: CPU 초과 → 발동
        self.metrics._cpu = 0.97
        assert self.pre_warmer.check_safety_valve() is True
        self.pre_warmer.emergency_override()
        assert self.pre_warmer.safety_valve_active is True

        # Phase 3: 즉시 복구 시도 → 차단 (min_hold 미경과)
        self.metrics._cpu = 0.5
        assert self.pre_warmer.check_safety_valve_recovery() is False

        # Phase 4: min_hold 경과 + 안정 → 복귀
        self.pre_warmer._safety_valve_activated_at = time.monotonic() - 60
        assert self.pre_warmer.check_safety_valve_recovery() is True
        assert self.pre_warmer.safety_valve_active is False


# =============================================================================
# D. ML Decision Authority Conflict Prevention
# =============================================================================


class TestMLDecisionAuthorityConflictPrevention:
    """
    SpikeClassifier가 이벤트 기간 HEALTHY_SURGE를 반환하여 ML 충돌 방지.

    Validates:
    - 이벤트 기간 context.scheduled_event=True → HEALTHY_SURGE
    - 이벤트 종료 후 context 없이 정상 분류
    """

    def test_event_period_classifier_returns_healthy_surge(self):
        """
        Purpose:
            이벤트 기간 중 RPS 급증 시 SpikeClassifier가 DDoS가 아닌 HEALTHY_SURGE로 분류.
        Expected:
            - scheduled_event context 전달 시 HEALTHY_SURGE 반환
            - error_rate 급등 없음 (안정적)
        """
        classifier = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=100.0,
        )

        rps = [100.0, 150.0, 200.0, 280.0, 380.0, 500.0, 650.0, 800.0, 1000.0, 1300.0]
        error_rate = [0.01] * 10
        latency = [50.0] * 10

        # With event context → HEALTHY_SURGE
        result_during = classifier.classify(
            rps,
            error_rate,
            latency,
            context={"scheduled_event": True},
        )
        assert result_during == SpikeType.HEALTHY_SURGE

        # Without context → may be different classification
        result_after = classifier.classify(rps, error_rate, latency, context=None)
        assert isinstance(result_after, SpikeType)


# =============================================================================
# E. Cancel During Active Event
# =============================================================================


class TestCapacityReservationCancelDuringActive:
    """
    활성 이벤트 취소 시 설정 원복 검증.

    Validates:
    - 활성 이벤트 취소 시 cool_down 실행
    - 설정이 Baseline으로 복원됨
    """

    def test_cancel_active_event_restores_settings(self):
        """
        Purpose:
            활성 이벤트를 CapacityReservationService.cancel_event()로 취소 시 복원 검증.
        Expected:
            - cancel_event()가 True 반환
            - 설정이 원래 값으로 복원
        """
        CapacityReservationService.reset()
        try:
            rate_controller = MockRateController()
            bulkhead = MockBulkhead()
            original_rate = rate_controller._settings.min_rate_per_second

            svc = CapacityReservationService()
            svc.initialize(
                rate_controller=rate_controller,
                bulkhead=bulkhead,
                settings=CapacityReservationSettings(dry_run=False),
            )

            event = _make_event(expected_rps_multiplier=3.0)
            svc.register_event(event)
            svc.calendar.update_status(event.event_id, EventStatus.ACTIVE)
            svc.pre_warmer.warm_up(event)

            assert rate_controller._settings.min_rate_per_second == original_rate * 3.0

            result = svc.cancel_event(event.event_id)
            assert result is True
            assert rate_controller._settings.min_rate_per_second == original_rate
        finally:
            CapacityReservationService.reset()
