"""
Throttle-CircuitBreaker 통합 테스트.

테스트 대상:
1. Phase 1: CB 이벤트 핸들러 (HALF_OPENED)
2. Phase 1.5: CB 동기 콜백 (register_state_change_callback)
3. Phase 2: ThrottleRegistry (서비스별 Throttle)
4. Phase 3: ThrottleCircuitBreakerBridge (RTT 데이터 공유)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from selfhealing.services.event_bus import (
    EventType,
    SelfHealingEvent,
    _on_circuit_breaker_half_opened_throttle,
)
from selfhealing.services.throttle.adaptive import (
    get_adaptive_throttle,
    reset_adaptive_throttle,
)
from selfhealing.services.throttle.registry import (
    ServiceThrottleConfig,
    ThrottleRegistry,
    get_throttle_registry,
    reset_throttle_registry,
)
from selfhealing.services.throttle.cb_bridge import (
    RTTSeverity,
    ThrottleCircuitBreakerBridge,
    get_throttle_cb_bridge,
    reset_throttle_cb_bridge,
)


class TestCircuitBreakerHalfOpenedThrottleHandler:
    """CB HALF_OPENED 이벤트 핸들러 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_half_opened_sets_limit_to_half_initial(self):
        """CB HALF_OPEN 시 limit = initial_limit × 0.5."""
        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_HALF_OPENED,
            data={"service_name": "payment_api", "previous_state": "open"},
            source="circuit_breaker_service",
        )

        _on_circuit_breaker_half_opened_throttle(event)

        # initial_limit × 0.5
        expected = int(throttle.config.initial_limit * 0.5)
        assert throttle.current_limit == expected

    def test_half_opened_ignores_throttle_source(self):
        """source가 throttle인 경우 무시."""
        throttle = get_adaptive_throttle()
        original_limit = throttle.current_limit

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_HALF_OPENED,
            data={"service_name": "payment_api"},
            source="throttle",  # 순환 참조 방지
        )

        _on_circuit_breaker_half_opened_throttle(event)

        assert throttle.current_limit == original_limit


class TestCircuitBreakerSyncCallback:
    """CB 동기 콜백 테스트."""

    def test_register_state_change_callback(self):
        """동기 콜백 등록 및 호출 테스트."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig

        # Mock repository
        mock_repo = MagicMock()
        mock_state = MagicMock()
        mock_state.state = "closed"
        mock_state.service_name = "test_service"
        mock_state.failure_count = 0
        mock_state.success_count = 0
        mock_state.manually_controlled = False
        mock_repo.get_or_create.return_value = mock_state

        config = CircuitBreakerConfig(enabled=True)
        service = CircuitBreakerService(config=config, repository=mock_repo)

        # 콜백 등록
        callback_called = []

        def test_callback(service_name, old_state, new_state):
            callback_called.append(
                {
                    "service_name": service_name,
                    "old_state": old_state,
                    "new_state": new_state,
                }
            )

        service.register_state_change_callback("open", test_callback)

        # 콜백 직접 호출 테스트
        service._invoke_state_change_callbacks("test_service", "closed", "open")

        assert len(callback_called) == 1
        assert callback_called[0]["service_name"] == "test_service"
        assert callback_called[0]["old_state"] == "closed"
        assert callback_called[0]["new_state"] == "open"

    def test_unregister_callback(self):
        """콜백 해제 테스트."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig

        config = CircuitBreakerConfig(enabled=True)
        service = CircuitBreakerService(config=config, repository=MagicMock())

        callback_called = []

        def test_callback(service_name, old_state, new_state):
            callback_called.append(True)

        # 등록 후 해제
        service.register_state_change_callback("open", test_callback)
        result = service.unregister_state_change_callback("open", test_callback)

        assert result is True

        # 해제 후 호출해도 실행 안됨
        service._invoke_state_change_callbacks("test_service", "closed", "open")
        assert len(callback_called) == 0


class TestThrottleRegistry:
    """ThrottleRegistry 테스트."""

    def setup_method(self):
        reset_throttle_registry()

    def teardown_method(self):
        reset_throttle_registry()

    def test_get_throttle_creates_instance(self):
        """서비스별 Throttle 인스턴스 생성."""
        registry = get_throttle_registry()

        throttle = registry.get_throttle("payment_api")

        assert throttle is not None
        assert throttle.current_limit > 0

    def test_get_throttle_returns_same_instance(self):
        """동일 서비스는 동일 인스턴스 반환."""
        registry = get_throttle_registry()

        throttle1 = registry.get_throttle("payment_api")
        throttle2 = registry.get_throttle("payment_api")

        assert throttle1 is throttle2

    def test_different_services_get_different_instances(self):
        """다른 서비스는 다른 인스턴스."""
        registry = get_throttle_registry()

        throttle1 = registry.get_throttle("payment_api")
        throttle2 = registry.get_throttle("external_api")

        assert throttle1 is not throttle2

    def test_register_service_config(self):
        """서비스별 설정 등록."""
        registry = get_throttle_registry()

        config = ServiceThrottleConfig(
            service_name="payment_api",
            initial_limit=50,
            min_limit=5,
            max_limit=200,
        )
        registry.register_service_config(config)

        throttle = registry.get_throttle("payment_api")

        assert throttle.config.initial_limit == 50
        assert throttle.config.min_limit == 5
        assert throttle.config.max_limit == 200

    def test_on_cb_state_changed_to_open(self):
        """CB OPEN 시 limit = min_limit."""
        registry = get_throttle_registry()

        config = ServiceThrottleConfig(
            service_name="payment_api",
            initial_limit=100,
            min_limit=10,
        )
        registry.register_service_config(config)

        throttle = registry.get_throttle("payment_api")
        original_limit = throttle.current_limit

        # CB OPEN
        registry.on_circuit_breaker_state_changed("payment_api", "open")

        assert throttle.current_limit == 10  # min_limit

        # 상태 확인
        state = registry.get_service_state("payment_api")
        assert state["cb_state"] == "open"
        assert state["original_limit"] == original_limit

    def test_on_cb_state_changed_to_half_open(self):
        """CB HALF_OPEN 시 limit = initial_limit × 0.5."""
        registry = get_throttle_registry()

        config = ServiceThrottleConfig(
            service_name="payment_api",
            initial_limit=100,
            cb_half_open_limit_ratio=0.5,
        )
        registry.register_service_config(config)

        registry.get_throttle("payment_api")

        # CB HALF_OPEN
        registry.on_circuit_breaker_state_changed("payment_api", "half_open")

        state = registry.get_service_state("payment_api")
        assert state["current_limit"] == 50  # 100 × 0.5
        assert state["cb_state"] == "half_open"

    def test_on_cb_state_changed_to_closed_restores_limit(self):
        """CB CLOSED 시 원래 limit 복구."""
        registry = get_throttle_registry()

        config = ServiceThrottleConfig(
            service_name="payment_api",
            initial_limit=100,
            min_limit=10,
        )
        registry.register_service_config(config)

        throttle = registry.get_throttle("payment_api")
        throttle.current_limit = 80  # 사용자 정의 limit

        # CB OPEN → CLOSED
        registry.on_circuit_breaker_state_changed("payment_api", "open")
        assert throttle.current_limit == 10

        registry.on_circuit_breaker_state_changed("payment_api", "closed")
        assert throttle.current_limit == 80  # 원래 limit 복구


class TestThrottleCircuitBreakerBridge:
    """ThrottleCircuitBreakerBridge 테스트."""

    def setup_method(self):
        reset_throttle_cb_bridge()

    def teardown_method(self):
        reset_throttle_cb_bridge()

    def test_record_rtt_normal(self):
        """정상 RTT 기록."""
        bridge = get_throttle_cb_bridge()

        severity = bridge.record_rtt("payment_api", rtt_ms=50.0)

        assert severity == RTTSeverity.NORMAL

    def test_record_rtt_warning(self):
        """Warning RTT 기록."""
        bridge = get_throttle_cb_bridge(sla_warning_ms=100, sla_critical_ms=500)

        severity = bridge.record_rtt("payment_api", rtt_ms=150.0)

        assert severity == RTTSeverity.WARNING

    def test_record_rtt_critical(self):
        """Critical RTT 기록."""
        bridge = get_throttle_cb_bridge(sla_warning_ms=100, sla_critical_ms=200)

        severity = bridge.record_rtt("payment_api", rtt_ms=300.0)

        assert severity == RTTSeverity.CRITICAL

    def test_gradient_triggers_warning(self):
        """Gradient 급증 시 Warning."""
        bridge = ThrottleCircuitBreakerBridge(
            sla_warning_ms=500,
            sla_critical_ms=1000,
            gradient_warning_threshold=0.2,
        )

        # RTT는 정상이지만 gradient가 높음
        severity = bridge.record_rtt("payment_api", rtt_ms=100.0, gradient=0.35)

        assert severity == RTTSeverity.WARNING

    def test_get_service_health(self):
        """서비스 건강 상태 조회."""
        bridge = get_throttle_cb_bridge()

        # RTT 기록
        bridge.record_rtt("payment_api", rtt_ms=150.0)

        health = bridge.get_service_health("payment_api")

        assert health.service_name == "payment_api"
        assert health.rtt.current_rtt_ms == 150.0
        assert health.rtt.sample_count == 1

    def test_should_record_as_cb_failure(self):
        """RTT 기반 CB failure 판단."""
        bridge = get_throttle_cb_bridge(sla_critical_ms=200)

        # Critical RTT
        result = bridge.should_record_as_cb_failure("payment_api", rtt_ms=300.0)
        assert result is True

        # Normal RTT
        result = bridge.should_record_as_cb_failure("payment_api", rtt_ms=100.0)
        assert result is False


class TestThrottleRegistryCBCallback:
    """ThrottleRegistry CB 콜백 통합 테스트."""

    def setup_method(self):
        reset_throttle_registry()

    def teardown_method(self):
        reset_throttle_registry()

    def test_register_cb_callbacks(self):
        """CB 콜백 등록 테스트."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig

        config = CircuitBreakerConfig(enabled=True)
        cb_service = CircuitBreakerService(config=config, repository=MagicMock())

        registry = get_throttle_registry()
        registry.register_circuit_breaker_callbacks(cb_service)

        # 콜백 등록 확인
        assert registry._cb_callbacks_registered is True
        assert len(cb_service._state_change_callbacks["open"]) > 0
        assert len(cb_service._state_change_callbacks["closed"]) > 0
        assert len(cb_service._state_change_callbacks["half_open"]) > 0

    def test_cb_callback_adjusts_throttle(self):
        """CB 콜백으로 Throttle limit 조정."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig

        config = CircuitBreakerConfig(enabled=True)
        cb_service = CircuitBreakerService(config=config, repository=MagicMock())

        registry = get_throttle_registry()

        # 서비스 설정 등록
        service_config = ServiceThrottleConfig(
            service_name="payment_api",
            initial_limit=100,
            min_limit=10,
        )
        registry.register_service_config(service_config)

        # CB 콜백 등록
        registry.register_circuit_breaker_callbacks(cb_service)

        # Throttle 생성
        throttle = registry.get_throttle("payment_api")

        # CB OPEN 콜백 직접 호출 시뮬레이션
        cb_service._invoke_state_change_callbacks("payment_api", "closed", "open")

        # limit이 min_limit으로 변경되었는지 확인
        assert throttle.current_limit == 10


class TestEventBusRegistration:
    """EventBus 핸들러 등록 테스트."""

    def test_half_opened_handler_registered(self):
        """HALF_OPENED 핸들러가 register_default_handlers에 등록됨."""
        from selfhealing.services.event_bus import (
            get_event_bus,
            register_default_handlers,
            EventType,
        )

        bus = get_event_bus()
        bus.reset()

        register_default_handlers()

        # HALF_OPENED 이벤트에 구독된 핸들러 확인
        subscriptions = bus._subscriptions.get(EventType.CIRCUIT_BREAKER_HALF_OPENED, [])

        handler_names = [s.handler_name for s in subscriptions]
        assert "_on_circuit_breaker_half_opened_throttle" in handler_names
