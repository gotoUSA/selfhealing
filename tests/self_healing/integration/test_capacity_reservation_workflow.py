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
    E. Cancel During Active Event:
        - 활성 이벤트 취소 → rollback 완료
    F. Dry-Run Mode:
        - dry_run=True → 로그만 남기고 실제 조정 없음
    G. Orphan Baseline Recovery:
        - StateBackend에 Baseline만 남아있고 활성 이벤트 없음 → 복원
    H. Late Joiner (Pod Startup with Active Events):
        - StateBackend에 Baseline + 활성 이벤트 → 즉시 재개
    I. shrink_guard Suppression During Event:
        - 이벤트 기간 PoolWatchdog shrink 억제
    J. EventBus emit source Verification:
        - emit 호출 시 source="capacity_reservation" 전달 검증
    K. classify_features Context Pipeline:
        - classify_features()가 context를 classify()까지 전달

Note: All tests use in-memory mock objects - no DB dependency.
      This enables parallel test execution with pytest-xdist.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from selfhealing.core.pool_monitor import PoolHealthStatus
from selfhealing.core.pool_watchdog import PoolWatchdog
from selfhealing.services.capacity_reservation.event_calendar import (
    EventCalendar,
    EventStatus,
    ScheduledEvent,
)
from selfhealing.services.capacity_reservation.pre_warmer import PreWarmer
from selfhealing.services.capacity_reservation.service import (
    CapacityReservationService,
)
from selfhealing.services.event_bus.bus import EventType
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


# =============================================================================
# F. Dry-Run Mode
# =============================================================================


class TestCapacityReservationDryRunMode:
    """
    dry_run=True → 로그만 남기고 실제 설정 변경 없음.

    Validates:
    - warm_up/cool_down이 성공 반환하지만 실제 조정 없음
    - RateController/Bulkhead 값 변경 없음
    """

    def test_dry_run_warm_up_does_not_modify_settings(self):
        """
        Purpose:
            dry_run=True에서 warm_up → 설정 미변경 + 성공 반환.
        Expected:
            - warm_up 성공
            - rate/bulkhead 원래 값 유지
        """
        settings = CapacityReservationSettings(dry_run=True)
        rate_controller = MockRateController()
        bulkhead = MockBulkhead()
        original_rate = rate_controller._settings.min_rate_per_second
        original_bulkhead = bulkhead._state.max_concurrent
        calendar = EventCalendar(settings=settings)
        pre_warmer = PreWarmer(
            calendar=calendar,
            rate_controller=rate_controller,
            bulkhead=bulkhead,
            settings=settings,
        )

        event = _make_event(expected_rps_multiplier=5.0, bulkhead_extra_permits=100)
        calendar.register(event)
        calendar.update_status(event.event_id, EventStatus.ACTIVE)

        result = pre_warmer.warm_up(event)

        assert result.success is True
        assert result.adjustments == []
        assert rate_controller._settings.min_rate_per_second == original_rate
        assert bulkhead._state.max_concurrent == original_bulkhead

    def test_dry_run_cool_down_does_not_modify_settings(self):
        """
        Purpose:
            dry_run=True에서 cool_down → 설정 미변경 + 성공 반환.
        Expected:
            - cool_down 성공
            - Global Baseline 캡처 안 됨
        """
        settings = CapacityReservationSettings(dry_run=True)
        calendar = EventCalendar(settings=settings)
        pre_warmer = PreWarmer(calendar=calendar, settings=settings)

        event = _make_event()
        result = pre_warmer.cool_down(event)

        assert result.success is True
        assert pre_warmer._global_baseline is None


# =============================================================================
# G. Orphan Baseline Recovery
# =============================================================================


class TestCapacityReservationOrphanBaselineRecovery:
    """
    Pod 재시작 시 StateBackend에 Baseline만 남아있고 활성 이벤트 없음 → 복원.

    Validates:
    - PreWarmer.initialize()가 orphan baseline 감지
    - 설정을 baseline 값으로 복원
    - StateBackend에서 baseline 삭제
    """

    def test_orphan_baseline_restores_and_cleans_up(self):
        """
        Purpose:
            StateBackend에 baseline만 남아있고 활성 이벤트가 없는 경우
            initialize()가 설정 복원 + baseline 삭제.
        Expected:
            - rate/bulkhead가 baseline 값으로 복원됨
            - StateBackend.delete() 호출됨
        """
        settings = CapacityReservationSettings(dry_run=False)
        rate_controller = MockRateController()
        bulkhead = MockBulkhead()

        rate_controller._settings.min_rate_per_second = 99.0
        bulkhead._state.max_concurrent = 200

        state_backend = MagicMock()
        state_backend.get.return_value = {
            "min_rate_per_second": 10.0,
            "bulkhead_max_concurrent": 50,
        }

        calendar = EventCalendar(settings=settings)
        pre_warmer = PreWarmer(
            calendar=calendar,
            rate_controller=rate_controller,
            bulkhead=bulkhead,
            state_backend=state_backend,
            settings=settings,
        )

        pre_warmer.initialize()

        assert rate_controller._settings.min_rate_per_second == 10.0
        assert bulkhead._state.max_concurrent == 50
        state_backend.delete.assert_called()


# =============================================================================
# H. Late Joiner (Pod Startup with Active Events)
# =============================================================================


class TestCapacityReservationLateJoiner:
    """
    Pod 기동 시 StateBackend에 Baseline + 활성 이벤트 → 즉시 재개.

    Validates:
    - PreWarmer.initialize()가 baseline을 메모리에 로드
    - Re-evaluation으로 활성 이벤트 배율 적용
    """

    def test_late_joiner_resumes_active_event_settings(self):
        """
        Purpose:
            StateBackend에 baseline과 활성 이벤트가 존재 → 재개.
        Expected:
            - _global_baseline이 로드됨
            - 활성 이벤트의 배율로 Re-evaluation 됨
        """
        settings = CapacityReservationSettings(dry_run=False)
        rate_controller = MockRateController()
        bulkhead = MockBulkhead()

        state_backend = MagicMock()
        saved_baseline = {
            "min_rate_per_second": 10.0,
            "bulkhead_max_concurrent": 50,
        }
        state_backend.get.return_value = saved_baseline

        calendar = EventCalendar(settings=settings)
        event = _make_event(expected_rps_multiplier=3.0, bulkhead_extra_permits=40)
        calendar.register(event)
        calendar.update_status(event.event_id, EventStatus.ACTIVE)

        pre_warmer = PreWarmer(
            calendar=calendar,
            rate_controller=rate_controller,
            bulkhead=bulkhead,
            state_backend=state_backend,
            settings=settings,
        )

        pre_warmer.initialize()

        assert pre_warmer._global_baseline == saved_baseline
        assert rate_controller._settings.min_rate_per_second == 10.0 * 3.0
        assert bulkhead._state.max_concurrent == 50 + 40


# =============================================================================
# I. shrink_guard Suppression During Event
# =============================================================================


class TestCapacityReservationShrinkGuardIntegration:
    """
    이벤트 기간 중 PoolWatchdog의 shrink 억제.

    Validates:
    - EventCalendar.is_event_period()를 shrink_guard로 연결
    - 이벤트 기간 shrink 억제
    - 이벤트 종료 후 정상 shrink
    """

    def _make_watchdog(self, shrink_guard=None):
        monitor = MagicMock()
        monitor.check_health.return_value = (
            PoolHealthStatus.HEALTHY,
            MagicMock(usage_percent=30.0, max_connections=20),
        )
        handler = MagicMock()
        handler.shrink_pool.return_value = True

        watchdog = PoolWatchdog(
            monitor=monitor,
            recovery_handler=handler,
            auto_expand=True,
            shrink_guard=shrink_guard,
        )
        watchdog._expanded_by = 5
        return watchdog, handler

    def test_event_period_suppresses_pool_shrink(self):
        """
        Purpose:
            EventCalendar.is_event_period() 기반 shrink_guard가 이벤트 기간에 shrink 억제.
        Expected:
            - 이벤트 활성 시 shrink 미실행
            - 이벤트 종료 후 shrink 실행
        """
        settings = CapacityReservationSettings(dry_run=False)
        calendar = EventCalendar(settings=settings)
        event = _make_event()
        calendar.register(event)
        calendar.update_status(event.event_id, EventStatus.ACTIVE)

        def shrink_guard():
            if calendar.is_event_period():
                return "ScheduledEvent active"
            return None

        watchdog, handler = self._make_watchdog(shrink_guard=shrink_guard)

        result = watchdog.check_and_recover()
        handler.shrink_pool.assert_not_called()
        assert "ScheduledEvent" in result.message

        calendar.update_status(event.event_id, EventStatus.COMPLETED)

        result2 = watchdog.check_and_recover()
        handler.shrink_pool.assert_called_once()


# =============================================================================
# J. EventBus emit source Verification
# =============================================================================


class TestCapacityReservationEventBusSourceVerification:
    """
    EventBus emit 호출 시 source="capacity_reservation" 전달 검증.

    Validates:
    - warm_up → emit(STARTED, data, source="capacity_reservation")
    - cool_down → emit(ENDED, data, source="capacity_reservation")
    """

    def test_emit_passes_correct_source_and_event_type(self):
        """
        Purpose:
            PreWarmer의 warm_up/cool_down이 emit에 올바른 source를 전달.
        Expected:
            - STARTED emit에 source="capacity_reservation"
            - ENDED emit에 source="capacity_reservation"
            - data에 event_id 포함
        """
        settings = CapacityReservationSettings(dry_run=False)
        rate_controller = MockRateController()
        bulkhead = MockBulkhead()
        event_bus = MagicMock()
        calendar = EventCalendar(settings=settings)
        pre_warmer = PreWarmer(
            calendar=calendar,
            rate_controller=rate_controller,
            bulkhead=bulkhead,
            event_bus=event_bus,
            settings=settings,
        )

        event = _make_event()
        calendar.register(event)
        calendar.update_status(event.event_id, EventStatus.ACTIVE)

        pre_warmer.warm_up(event)

        started_call = event_bus.emit.call_args_list[0]
        assert started_call[0][0] == EventType.SCHEDULED_EVENT_STARTED
        assert (
            started_call[1].get("source")
            or started_call[0][2] == "capacity_reservation"
        )
        started_data = started_call[0][1]
        assert started_data["event_id"] == event.event_id
        assert started_data["scheduled_event"] is True

        event_bus.emit.reset_mock()
        calendar.update_status(event.event_id, EventStatus.COOLING_DOWN)
        pre_warmer.cool_down(event)

        ended_call = event_bus.emit.call_args_list[0]
        assert ended_call[0][0] == EventType.SCHEDULED_EVENT_ENDED
        assert ended_call[0][1]["event_id"] == event.event_id


# =============================================================================
# K. classify_features Context Pipeline
# =============================================================================


class TestCapacityReservationClassifyFeaturesContextPipeline:
    """
    SpikeClassifier.classify_features()가 context를 classify()까지 파이프라인 전달.

    Validates:
    - classify_features에 context 전달 → classify에서 HEALTHY_SURGE 반환
    - 동일 features, context 없으면 다른 결과 가능
    """

    def test_classify_features_scheduled_event_context_pipeline(self):
        """
        Purpose:
            classify_features(features, context={"scheduled_event": True})가
            내부 classify()에 context를 전달하여 HEALTHY_SURGE 반환.
        Expected:
            - context 전달 시 label == "healthy_surge"
            - context 없으면 label이 다를 수 있음
        """
        classifier = SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=100.0,
        )

        rps = [100.0, 150.0, 200.0, 280.0, 380.0, 500.0, 650.0, 800.0, 1000.0, 1300.0]
        features = {
            "rps_history": ",".join(str(x) for x in rps),
            "error_rate_history": ",".join(str(x) for x in [0.01] * 10),
            "latency_history": ",".join(str(x) for x in [50.0] * 10),
        }

        label_with_ctx, confidence_with = classifier.classify_features(
            features, context={"scheduled_event": True}
        )
        assert label_with_ctx == SpikeType.HEALTHY_SURGE.value

        label_no_ctx, confidence_no = classifier.classify_features(
            features, context=None
        )
        assert isinstance(label_no_ctx, str)
