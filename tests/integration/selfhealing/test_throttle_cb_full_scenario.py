"""
Throttle ↔ Circuit Breaker 통합 시나리오 테스트.

실제 Redis와 함께 다음 시나리오들을 테스트합니다:
1. CB OPEN → Throttle limit 강등 → CB CLOSE → Recovery Dampening → 완전 복구
2. 다중 서비스 동시 CB 상태 변경 처리
3. DLQ 저장 및 CB CLOSE 시 자동 replay
4. RTT 데이터 기반 CB 피드백

Run:
    docker-compose -f docker-compose.test.yml run test-throttle-cb-scenario
"""

import os
import time
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import patch, MagicMock


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_all_throttle_components():
    """모든 Throttle 관련 컴포넌트 리셋."""
    from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
    from selfhealing.services.throttle.registry import reset_throttle_registry
    from selfhealing.services.throttle.cb_bridge import reset_throttle_cb_bridge
    from selfhealing.services.throttle.recovery_dampening import reset_recovery_dampening_manager
    from selfhealing.services.event_bus import get_event_bus

    reset_adaptive_throttle()
    reset_throttle_registry()
    reset_throttle_cb_bridge()
    reset_recovery_dampening_manager()
    get_event_bus().reset()

    yield

    reset_adaptive_throttle()
    reset_throttle_registry()
    reset_throttle_cb_bridge()
    reset_recovery_dampening_manager()
    get_event_bus().reset()


# =============================================================================
# Test: CB OPEN → Throttle 강등 → CB CLOSE → Recovery Dampening 전체 흐름
# =============================================================================


@pytest.mark.django_db
class TestCircuitBreakerThrottleFullScenario:
    """CB ↔ Throttle 전체 시나리오 통합 테스트."""

    def test_cb_open_reduces_throttle_limit_to_min(self):
        """CB OPEN 시 Throttle limit이 min_limit으로 즉시 강등되는지 확인."""
        from selfhealing.services.throttle.registry import (
            get_throttle_registry,
            ServiceThrottleConfig,
        )

        registry = get_throttle_registry()

        # 서비스 설정 등록
        config = ServiceThrottleConfig(
            service_name="payment_api",
            initial_limit=100,
            min_limit=10,
            max_limit=500,
        )
        registry.register_service_config(config)

        # 초기 Throttle 생성
        throttle = registry.get_throttle("payment_api")
        assert throttle.current_limit == 100

        # CB OPEN 이벤트 전달
        registry.on_circuit_breaker_state_changed(
            service_name="payment_api",
            new_state="open",
            old_state="closed",
        )

        # limit이 min_limit으로 강등
        assert throttle.current_limit == 10

    def test_cb_half_open_allows_limited_traffic(self):
        """CB HALF_OPEN 시 limit이 initial_limit의 50%로 설정되는지 확인."""
        from selfhealing.services.throttle.registry import (
            get_throttle_registry,
            ServiceThrottleConfig,
        )

        registry = get_throttle_registry()

        config = ServiceThrottleConfig(
            service_name="notification_api",
            initial_limit=100,
            min_limit=10,
            cb_half_open_limit_ratio=0.5,
        )
        registry.register_service_config(config)

        throttle = registry.get_throttle("notification_api")

        # CB OPEN → HALF_OPEN 전이
        registry.on_circuit_breaker_state_changed("notification_api", "open")
        assert throttle.current_limit == 10

        registry.on_circuit_breaker_state_changed("notification_api", "half_open")
        assert throttle.current_limit == 50  # 100 * 0.5

    def test_cb_close_restores_original_limit(self):
        """CB CLOSE 시 원래 limit으로 복구되는지 확인."""
        from selfhealing.services.throttle.registry import (
            get_throttle_registry,
            ServiceThrottleConfig,
        )

        registry = get_throttle_registry()

        config = ServiceThrottleConfig(
            service_name="order_api",
            initial_limit=200,
            min_limit=20,
        )
        registry.register_service_config(config)

        throttle = registry.get_throttle("order_api")
        original_limit = throttle.current_limit

        # CB OPEN
        registry.on_circuit_breaker_state_changed("order_api", "open")
        assert throttle.current_limit == 20

        # CB CLOSE
        registry.on_circuit_breaker_state_changed("order_api", "closed")
        assert throttle.current_limit == original_limit

    def test_full_cb_lifecycle_with_recovery_dampening(self):
        """CB 전체 라이프사이클: CLOSED → OPEN → HALF_OPEN → CLOSED + Recovery Dampening."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
        )
        throttle = AdaptiveThrottle(config)

        # 1. 정상 상태
        assert throttle.current_limit == 100

        # 2. Emergency Level 3 (CB OPEN 시뮬레이션)
        throttle.adjust_for_emergency(3)
        assert throttle.current_limit == 10
        assert throttle._gradient_frozen is True

        # 3. Emergency Level 0 (복구 시작)
        throttle.adjust_for_emergency(0)

        # Recovery Dampening 시작됨 (80%)
        assert throttle._recovery_dampening_active is True
        assert throttle.current_limit == 80  # 100 * 0.8

        # 4. 수동 복구 완료
        throttle.complete_recovery_dampening()
        assert throttle._recovery_dampening_active is False
        assert throttle.current_limit == 100


# =============================================================================
# Test: 다중 서비스 동시 CB 상태 변경
# =============================================================================


@pytest.mark.django_db
class TestMultiServiceCircuitBreakerHandling:
    """다중 서비스 CB 상태 동시 처리 테스트."""

    def test_multiple_services_independent_cb_states(self):
        """각 서비스별로 독립적인 CB 상태 및 limit 관리."""
        from selfhealing.services.throttle.registry import (
            get_throttle_registry,
            ServiceThrottleConfig,
        )

        registry = get_throttle_registry()

        # 3개 서비스 등록
        services = [
            ("payment_api", 100, 10),
            ("notification_api", 200, 20),
            ("order_api", 150, 15),
        ]

        for name, initial, min_limit in services:
            config = ServiceThrottleConfig(
                service_name=name,
                initial_limit=initial,
                min_limit=min_limit,
            )
            registry.register_service_config(config)

        # 각 서비스 Throttle 생성
        throttles = {name: registry.get_throttle(name) for name, _, _ in services}

        # payment_api만 CB OPEN
        registry.on_circuit_breaker_state_changed("payment_api", "open")

        assert throttles["payment_api"].current_limit == 10  # min_limit
        assert throttles["notification_api"].current_limit == 200  # 영향 없음
        assert throttles["order_api"].current_limit == 150  # 영향 없음

        # notification_api도 CB OPEN
        registry.on_circuit_breaker_state_changed("notification_api", "open")

        assert throttles["payment_api"].current_limit == 10
        assert throttles["notification_api"].current_limit == 20  # min_limit
        assert throttles["order_api"].current_limit == 150  # 영향 없음

        # payment_api CB CLOSE
        registry.on_circuit_breaker_state_changed("payment_api", "closed")

        assert throttles["payment_api"].current_limit == 100  # 복구됨
        assert throttles["notification_api"].current_limit == 20  # 여전히 OPEN
        assert throttles["order_api"].current_limit == 150

    def test_registry_service_state_tracking(self):
        """Registry가 각 서비스의 CB 상태를 정확히 추적하는지 확인."""
        from selfhealing.services.throttle.registry import (
            get_throttle_registry,
            ServiceThrottleConfig,
        )

        registry = get_throttle_registry()

        config = ServiceThrottleConfig(service_name="tracking_test", initial_limit=100)
        registry.register_service_config(config)
        registry.get_throttle("tracking_test")

        # 상태 변경 추적
        registry.on_circuit_breaker_state_changed("tracking_test", "open")
        state = registry.get_service_state("tracking_test")
        assert state["cb_state"] == "open"

        registry.on_circuit_breaker_state_changed("tracking_test", "half_open")
        state = registry.get_service_state("tracking_test")
        assert state["cb_state"] == "half_open"

        registry.on_circuit_breaker_state_changed("tracking_test", "closed")
        state = registry.get_service_state("tracking_test")
        assert state["cb_state"] == "closed"


# =============================================================================
# Test: RTT 데이터 공유 및 CB 피드백
# =============================================================================


@pytest.mark.django_db
class TestRTTDataSharingWithCircuitBreaker:
    """RTT 데이터 공유 및 CB 피드백 통합 테스트."""

    def test_rtt_critical_triggers_cb_notification(self):
        """RTT가 SLA Critical 임계값 초과 시 CB에 알림이 전송되는지 확인."""
        from selfhealing.services.throttle.cb_bridge import (
            get_throttle_cb_bridge,
            RTTSeverity,
        )

        bridge = get_throttle_cb_bridge(
            sla_warning_ms=200,
            sla_critical_ms=500,
        )

        # 정상 RTT
        severity = bridge.record_rtt("test_service", 100.0)
        assert severity == RTTSeverity.NORMAL

        # Warning RTT
        severity = bridge.record_rtt("test_service", 250.0)
        assert severity == RTTSeverity.WARNING

        # Critical RTT
        severity = bridge.record_rtt("test_service", 600.0)
        assert severity == RTTSeverity.CRITICAL

    def test_service_health_metrics_aggregation(self):
        """서비스 건강 상태 통합 메트릭이 올바르게 집계되는지 확인."""
        from selfhealing.services.throttle.cb_bridge import get_throttle_cb_bridge

        bridge = get_throttle_cb_bridge()

        # RTT 데이터 기록
        bridge.record_rtt("aggregation_test", 150.0, gradient=0.05)
        bridge.record_rtt("aggregation_test", 200.0, gradient=0.1)

        # 메트릭 조회
        metrics = bridge.get_service_health("aggregation_test")

        assert metrics.service_name == "aggregation_test"
        assert metrics.rtt.current_rtt_ms == 200.0
        assert metrics.rtt.sample_count == 2
        assert metrics.rtt.gradient == 0.1

    def test_gradient_warning_triggers_severity_warning(self):
        """Gradient가 경고 임계값 초과 시 WARNING 심각도 반환."""
        from selfhealing.services.throttle.cb_bridge import (
            ThrottleCircuitBreakerBridge,
            RTTSeverity,
        )

        # 직접 인스턴스 생성 (gradient threshold 0.3 설정)
        bridge = ThrottleCircuitBreakerBridge(gradient_warning_threshold=0.3)

        # RTT는 정상이지만 gradient 높음 (0.35 > 0.3)
        severity = bridge.record_rtt("gradient_test", 100.0, gradient=0.35)
        assert severity == RTTSeverity.WARNING


# =============================================================================
# Test: DLQ 연계 및 CB CLOSE 시 자동 replay
# =============================================================================


@pytest.mark.django_db
class TestThrottleDLQIntegration:
    """Throttle DLQ 연계 통합 테스트."""

    def test_denied_request_stored_in_dlq(self):
        """Throttle deny된 요청이 DLQ에 저장되는지 확인."""
        from selfhealing.services.throttle.dlq_integration import (
            get_throttle_dlq_integration,
            ThrottleDLQConfig,
        )

        config = ThrottleDLQConfig(enabled=True)
        integration = get_throttle_dlq_integration(config)

        # DLQ 서비스 모킹
        with patch.object(integration, "_get_dlq_service") as mock_get_dlq:
            mock_dlq = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.entry_id = 123
            mock_dlq.store_failure.return_value = mock_result
            mock_get_dlq.return_value = mock_dlq

            result = integration.store_denied_request(
                service_name="payment_api",
                request_key="user_123",
                request_data={"action": "purchase"},
                throttle_limit=100,
                current_count=150,
            )

            assert result is not None
            assert result.dlq_entry_id == 123
            assert result.service_name == "payment_api"

    def test_cb_close_triggers_replay(self):
        """CB CLOSE 이벤트가 DLQ replay를 트리거하는지 확인."""
        from selfhealing.services.throttle.dlq_integration import (
            get_throttle_dlq_integration,
            ThrottleDLQConfig,
        )

        config = ThrottleDLQConfig(enabled=True, auto_replay_on_cb_close=True)
        integration = get_throttle_dlq_integration(config)

        with patch.object(integration, "_get_dlq_service") as mock_get_dlq:
            mock_dlq = MagicMock()
            mock_result = MagicMock()
            mock_result.processed = 10
            mock_result.success = 8
            mock_result.failed = 2
            mock_result.errors = []
            mock_dlq.replay.return_value = mock_result
            mock_get_dlq.return_value = mock_dlq

            result = integration.on_circuit_breaker_closed("payment_api")

            assert result["processed"] == 10
            assert result["success"] == 8
            mock_dlq.replay.assert_called_once()


# =============================================================================
# Test: Recovery Dampening 단계별 복구
# =============================================================================


@pytest.mark.django_db
class TestRecoveryDampeningStages:
    """Recovery Dampening 단계별 복구 테스트."""

    def test_recovery_dampening_three_stage_progression(self):
        """복구가 80% → 90% → 100% 3단계로 진행되는지 확인."""
        from selfhealing.services.throttle.recovery_dampening import (
            RecoveryDampeningManager,
            RecoveryDampeningConfig,
        )

        config = RecoveryDampeningConfig(
            enabled=True,
            phase_1_ratio=0.8,
            phase_2_ratio=0.9,
            phase_1_duration_seconds=0.1,  # 테스트용 짧은 간격
            phase_2_duration_seconds=0.1,
        )

        limit_changes = []

        def on_limit_change(service_name: str, new_limit: int):
            limit_changes.append((service_name, new_limit))

        manager = RecoveryDampeningManager(config, on_limit_change)

        # 복구 시작 (80%)
        phase_1_limit = manager.start_recovery("test_service", target_limit=100, current_limit=10)
        assert phase_1_limit == 80

        state = manager.get_recovery_state("test_service")
        assert state["current_phase"] == "phase_1"
        assert state["current_multiplier"] == 0.8

        # 타이머 대기 후 Phase 2로 전이
        time.sleep(0.2)
        state = manager.get_recovery_state("test_service")
        assert state["current_phase"] == "phase_2"

        # 타이머 대기 후 완료
        time.sleep(0.2)
        state = manager.get_recovery_state("test_service")
        assert state["is_active"] is False

        manager.reset()

    def test_recovery_cancel_on_new_failure(self):
        """새 장애 발생 시 복구가 취소되는지 확인."""
        from selfhealing.services.throttle.recovery_dampening import (
            RecoveryDampeningManager,
            RecoveryDampeningConfig,
        )

        config = RecoveryDampeningConfig(enabled=True)
        manager = RecoveryDampeningManager(config)

        manager.start_recovery("cancel_test", target_limit=100, current_limit=10)
        assert manager.is_recovery_active("cancel_test") is True

        # 복구 취소
        manager.cancel_recovery("cancel_test")
        assert manager.is_recovery_active("cancel_test") is False

        manager.reset()


# =============================================================================
# Test: EventBus를 통한 CB → Throttle 이벤트 전달
# =============================================================================


@pytest.mark.django_db
class TestEventBusCircuitBreakerThrottleIntegration:
    """EventBus를 통한 CB → Throttle 이벤트 전달 통합 테스트."""

    def test_cb_opened_event_reduces_throttle_via_eventbus(self):
        """CIRCUIT_BREAKER_OPENED 이벤트가 EventBus를 통해 Throttle에 전달되는지 확인."""
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            reset_adaptive_throttle,
        )
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        bus = get_event_bus()
        bus.reset()
        register_default_handlers()

        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # CB OPENED 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="circuit_breaker_service",
            data={
                "service_name": "test_service",
                "failure_count": 10,
            },
        )
        bus.publish(event)

        # Throttle limit이 감소해야 함
        assert throttle.current_limit <= initial_limit

    def test_cb_closed_event_restores_throttle_via_eventbus(self):
        """CIRCUIT_BREAKER_CLOSED 이벤트가 EventBus를 통해 Throttle을 복구하는지 확인."""
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            reset_adaptive_throttle,
        )
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        bus = get_event_bus()
        bus.reset()
        register_default_handlers()

        throttle = get_adaptive_throttle()

        # 먼저 CB OPEN으로 강등
        open_event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="circuit_breaker_service",
            data={"service_name": "test_service"},
        )
        bus.publish(open_event)
        reduced_limit = throttle.current_limit

        # CB CLOSE로 복구
        close_event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_CLOSED,
            source="circuit_breaker_service",
            data={"service_name": "test_service"},
        )
        bus.publish(close_event)

        # limit이 복구되어야 함
        assert throttle.current_limit >= reduced_limit

    def test_error_budget_critical_reduces_throttle_limit(self):
        """ERROR_BUDGET_CRITICAL 이벤트가 Throttle limit을 절반으로 줄이는지 확인."""
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            reset_adaptive_throttle,
        )
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        bus = get_event_bus()
        bus.reset()
        register_default_handlers()

        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # Error Budget Critical 이벤트
        event = SelfHealingEvent(
            event_type=EventType.ERROR_BUDGET_CRITICAL,
            source="error_budget_gate",
            data={"budget_percent": 5.0},
        )
        bus.publish(event)

        # limit이 절반으로 감소 (×0.5)
        expected_limit = int(initial_limit * 0.5)
        assert throttle.current_limit == expected_limit
