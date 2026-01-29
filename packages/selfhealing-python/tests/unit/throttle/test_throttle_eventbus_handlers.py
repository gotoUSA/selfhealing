"""
Throttle EventBus 구독 핸들러 테스트.

테스트 대상:
1. _on_emergency_level_changed_throttle() - Emergency Level에 따른 limit 조정
2. _on_circuit_breaker_opened_throttle() - CB OPEN 시 min_limit 강등
3. _on_circuit_breaker_closed_throttle() - CB CLOSED 시 limit 복원
4. _on_error_budget_critical_throttle() - Error Budget Critical 시 ×0.5 조정
5. _on_error_budget_recovered_throttle() - Error Budget 회복 시 limit 해제
6. _on_kill_switch_activated_throttle() - Kill Switch 시 min_limit 고정
7. 순환 참조 방지 (source="throttle" 체크)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from selfhealing.services.event_bus import (
    EventType,
    EventPriority,
    SelfHealingEvent,
    _on_emergency_level_changed_throttle,
    _on_circuit_breaker_opened_throttle,
    _on_circuit_breaker_closed_throttle,
    _on_error_budget_critical_throttle,
    _on_error_budget_recovered_throttle,
    _on_kill_switch_activated_throttle,
)


class TestEmergencyLevelChangedThrottleHandler:
    """Emergency 레벨 변경 Throttle 핸들러 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_level_0_restores_base_limit(self):
        """Emergency Level 0 시 이전 base_limit으로 복구."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100  # 원래 limit

        # 먼저 Emergency 활성화
        event1 = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 1, "previous_level": 0},
            source="emergency_manager",
        )
        _on_emergency_level_changed_throttle(event1)
        assert throttle.current_limit == 80  # 100 × 0.8

        # Emergency 해제
        event2 = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 0, "previous_level": 1},
            source="emergency_manager",
        )

        _on_emergency_level_changed_throttle(event2)

        # Recovery Dampening 첫 단계: base_limit × 0.8 = 100 × 0.8 = 80
        # (Emergency Level 1의 80%와 동일한 값이지만 Recovery Dampening 상태)
        assert throttle.current_limit == 80
        assert throttle.is_recovery_dampening_active()

    def test_level_1_reduces_limit_by_20_percent(self):
        """Emergency Level 1 시 limit × 0.8."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 1, "previous_level": 0},
            source="emergency_manager",
        )

        _on_emergency_level_changed_throttle(event)

        assert throttle.current_limit == 80

    def test_level_2_reduces_limit_by_50_percent(self):
        """Emergency Level 2 시 limit × 0.5 (문서 기준)."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 2, "previous_level": 0},
            source="emergency_manager",
        )

        _on_emergency_level_changed_throttle(event)

        assert throttle.current_limit == 50  # 100 × 0.5

    def test_level_3_sets_min_limit(self):
        """Emergency Level 3+ 시 min_limit으로 고정."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 3, "previous_level": 0},
            source="emergency_manager",
        )

        _on_emergency_level_changed_throttle(event)

        assert throttle.current_limit == throttle.config.min_limit

    def test_ignores_self_source_events(self):
        """source='throttle' 이벤트 무시 (순환 참조 방지)."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        original_limit = 100
        throttle.current_limit = original_limit

        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 3, "previous_level": 0},
            source="throttle",  # 자기 이벤트
        )

        _on_emergency_level_changed_throttle(event)

        # limit이 변경되지 않아야 함
        assert throttle.current_limit == original_limit


class TestCircuitBreakerOpenedThrottleHandler:
    """CB OPEN Throttle 핸들러 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_sets_min_limit_on_cb_open(self):
        """CB OPEN 시 min_limit으로 강등."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "test_service"},
            source="circuit_breaker_service",
        )

        _on_circuit_breaker_opened_throttle(event)

        assert throttle.current_limit == throttle.config.min_limit

    def test_ignores_self_source_events(self):
        """source='throttle' 이벤트 무시."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        original_limit = 100
        throttle.current_limit = original_limit

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "test_service"},
            source="throttle",
        )

        _on_circuit_breaker_opened_throttle(event)

        assert throttle.current_limit == original_limit


class TestCircuitBreakerClosedThrottleHandler:
    """CB CLOSED Throttle 핸들러 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_restores_initial_limit_on_cb_closed(self):
        """CB CLOSED 시 initial_limit으로 복원."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = throttle.config.min_limit

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="circuit_breaker_service",
        )

        _on_circuit_breaker_closed_throttle(event)

        assert throttle.current_limit == throttle.config.initial_limit

    def test_ignores_self_source_events(self):
        """source='throttle' 이벤트 무시."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = throttle.config.min_limit
        original_limit = throttle.current_limit

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            data={"service_name": "test_service"},
            source="throttle",
        )

        _on_circuit_breaker_closed_throttle(event)

        assert throttle.current_limit == original_limit


class TestErrorBudgetCriticalThrottleHandler:
    """Error Budget Critical Throttle 핸들러 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_halves_limit_on_error_budget_critical(self):
        """Error Budget Critical 시 limit × 0.5."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.ERROR_BUDGET_CRITICAL,
            data={"budget_percent": 15.0, "threshold": 20.0},
            source="error_budget_gate",
        )

        _on_error_budget_critical_throttle(event)

        assert throttle.current_limit == 50

    def test_ignores_self_source_events(self):
        """source='throttle' 이벤트 무시."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        original_limit = 100
        throttle.current_limit = original_limit

        event = SelfHealingEvent(
            event_type=EventType.ERROR_BUDGET_CRITICAL,
            data={"budget_percent": 15.0},
            source="throttle",
        )

        _on_error_budget_critical_throttle(event)

        assert throttle.current_limit == original_limit


class TestErrorBudgetRecoveredThrottleHandler:
    """Error Budget 회복 Throttle 핸들러 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_restores_initial_limit_on_error_budget_recovered(self):
        """Error Budget 회복 시 initial_limit으로 복원."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 30  # 낮은 limit

        event = SelfHealingEvent(
            event_type=EventType.ERROR_BUDGET_RECOVERED,
            data={"budget_percent": 50.0},
            source="error_budget_gate",
        )

        _on_error_budget_recovered_throttle(event)

        assert throttle.current_limit == throttle.config.initial_limit

    def test_ignores_self_source_events(self):
        """source='throttle' 이벤트 무시."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        original_limit = 30
        throttle.current_limit = original_limit

        event = SelfHealingEvent(
            event_type=EventType.ERROR_BUDGET_RECOVERED,
            data={"budget_percent": 50.0},
            source="throttle",
        )

        _on_error_budget_recovered_throttle(event)

        assert throttle.current_limit == original_limit


class TestKillSwitchActivatedThrottleHandler:
    """Kill Switch 활성화 Throttle 핸들러 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_sets_min_limit_on_kill_switch(self):
        """Kill Switch 활성화 시 min_limit으로 고정."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.KILL_SWITCH_ACTIVATED,
            data={"activated_by": "admin"},
            source="system_control",
        )

        _on_kill_switch_activated_throttle(event)

        assert throttle.current_limit == throttle.config.min_limit

    def test_ignores_self_source_events(self):
        """source='throttle' 이벤트 무시."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        original_limit = 100
        throttle.current_limit = original_limit

        event = SelfHealingEvent(
            event_type=EventType.KILL_SWITCH_ACTIVATED,
            data={"activated_by": "admin"},
            source="throttle",
        )

        _on_kill_switch_activated_throttle(event)

        assert throttle.current_limit == original_limit


class TestCircularReferenceProtection:
    """순환 참조 방지 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_all_handlers_ignore_throttle_source(self):
        """모든 핸들러가 source='throttle' 이벤트를 무시."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        original_limit = 100
        throttle.current_limit = original_limit

        handlers = [
            (_on_emergency_level_changed_throttle, EventType.EMERGENCY_LEVEL_CHANGED, {"level": 3, "previous_level": 0}),
            (_on_circuit_breaker_opened_throttle, EventType.CIRCUIT_BREAKER_OPENED, {"service_name": "test"}),
            (_on_circuit_breaker_closed_throttle, EventType.CIRCUIT_BREAKER_CLOSED, {"service_name": "test"}),
            (_on_error_budget_critical_throttle, EventType.ERROR_BUDGET_CRITICAL, {"budget_percent": 10.0}),
            (_on_error_budget_recovered_throttle, EventType.ERROR_BUDGET_RECOVERED, {"budget_percent": 50.0}),
            (_on_kill_switch_activated_throttle, EventType.KILL_SWITCH_ACTIVATED, {"activated_by": "test"}),
        ]

        for handler, event_type, data in handlers:
            throttle.current_limit = original_limit  # Reset before each test

            event = SelfHealingEvent(
                event_type=event_type,
                data=data,
                source="throttle",  # 자기 이벤트
            )

            handler(event)

            assert throttle.current_limit == original_limit, f"Handler {handler.__name__} should ignore throttle source"


class TestHandlerRegistration:
    """핸들러 등록 테스트."""

    def test_throttle_handlers_registered_in_default_handlers(self):
        """register_default_handlers()에 Throttle 핸들러 등록 확인."""
        from selfhealing.services.event_bus import get_event_bus, register_default_handlers

        bus = get_event_bus()
        bus.reset()

        register_default_handlers()

        # Emergency Level Changed에 throttle 핸들러 등록 확인
        emergency_subs = bus.get_subscriptions(EventType.EMERGENCY_LEVEL_CHANGED)
        throttle_handlers = [s for s in emergency_subs if "throttle" in s["handler_name"].lower()]
        assert len(throttle_handlers) >= 1

        # CB OPENED에 throttle 핸들러 등록 확인
        cb_opened_subs = bus.get_subscriptions(EventType.CIRCUIT_BREAKER_OPENED)
        throttle_handlers = [s for s in cb_opened_subs if "throttle" in s["handler_name"].lower()]
        assert len(throttle_handlers) >= 1

        # CB CLOSED에 throttle 핸들러 등록 확인
        cb_closed_subs = bus.get_subscriptions(EventType.CIRCUIT_BREAKER_CLOSED)
        throttle_handlers = [s for s in cb_closed_subs if "throttle" in s["handler_name"].lower()]
        assert len(throttle_handlers) >= 1

        # Error Budget Critical에 throttle 핸들러 등록 확인
        eb_critical_subs = bus.get_subscriptions(EventType.ERROR_BUDGET_CRITICAL)
        throttle_handlers = [s for s in eb_critical_subs if "throttle" in s["handler_name"].lower()]
        assert len(throttle_handlers) >= 1

        # Error Budget Recovered에 throttle 핸들러 등록 확인
        eb_recovered_subs = bus.get_subscriptions(EventType.ERROR_BUDGET_RECOVERED)
        throttle_handlers = [s for s in eb_recovered_subs if "throttle" in s["handler_name"].lower()]
        assert len(throttle_handlers) >= 1

        # Kill Switch에 throttle 핸들러 등록 확인
        kill_switch_subs = bus.get_subscriptions(EventType.KILL_SWITCH_ACTIVATED)
        throttle_handlers = [s for s in kill_switch_subs if "throttle" in s["handler_name"].lower()]
        assert len(throttle_handlers) >= 1
