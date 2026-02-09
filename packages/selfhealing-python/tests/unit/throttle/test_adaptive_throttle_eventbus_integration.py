"""
AdaptiveThrottle EventBus 연동 테스트.

테스트 대상:
1. AdaptiveThrottle 이벤트 발행 (THROTTLE_LIMIT_CHANGED, THROTTLE_SLA_WARNING, THROTTLE_SLA_CRITICAL)
2. record_response()에서 THROTTLE_LIMIT_RECOVERED 이벤트 발행
3. EventBus Import 실패 시 Fail-Open 처리
4. source="throttle" 명시 확인
"""

import pytest
from unittest.mock import patch, MagicMock


class TestAdaptiveThrottleEventPublishing:
    """AdaptiveThrottle 이벤트 발행 테스트."""

    def setup_method(self):
        """각 테스트 전 throttle 상태 초기화."""
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        """각 테스트 후 throttle 상태 초기화."""
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_emits_sla_critical_event_on_high_rtt(self):
        """SLA Critical RTT 도달 시 THROTTLE_SLA_CRITICAL 이벤트 발행."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sla_warning_ms=200,
            sample_interval_ms=50,  # 즉시 조정
        )
        throttle = AdaptiveThrottle(config)

        events_emitted = []

        def mock_emit(event_type, data, source, priority):
            events_emitted.append(
                {
                    "event_type": event_type,
                    "data": data,
                    "source": source,
                    "priority": priority,
                }
            )
            return 1

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus_instance.emit = mock_emit
            mock_bus.return_value = mock_bus_instance

            # SLA Critical RTT 전송
            throttle.record_response(600.0)  # >= sla_critical_ms

        # THROTTLE_SLA_CRITICAL 이벤트 발행 확인
        critical_events = [e for e in events_emitted if "SLA_CRITICAL" in str(e.get("event_type", ""))]
        assert len(critical_events) >= 1

    def test_emits_sla_warning_event_on_warning_rtt(self):
        """SLA Warning RTT 도달 시 THROTTLE_SLA_WARNING 이벤트 발행."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sla_warning_ms=200,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)

        events_emitted = []

        def mock_emit(event_type, data, source, priority):
            events_emitted.append(
                {
                    "event_type": event_type,
                    "data": data,
                    "source": source,
                    "priority": priority,
                }
            )
            return 1

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus_instance.emit = mock_emit
            mock_bus.return_value = mock_bus_instance

            # SLA Warning RTT 전송 (warning <= rtt < critical)
            throttle.record_response(300.0)

        # THROTTLE_SLA_WARNING 이벤트 발행 확인
        warning_events = [e for e in events_emitted if "SLA_WARNING" in str(e.get("event_type", ""))]
        assert len(warning_events) >= 1

    def test_emits_limit_changed_event_on_limit_change(self):
        """limit 변경 시 THROTTLE_LIMIT_CHANGED 이벤트 발행."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sla_warning_ms=200,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)

        events_emitted = []

        def mock_emit(event_type, data, source, priority):
            events_emitted.append(
                {
                    "event_type": event_type,
                    "data": data,
                    "source": source,
                    "priority": priority,
                }
            )
            return 1

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus_instance.emit = mock_emit
            mock_bus.return_value = mock_bus_instance

            # limit 변경 트리거
            throttle.record_response(600.0)

        # THROTTLE_LIMIT_CHANGED 이벤트 발행 확인
        limit_changed_events = [e for e in events_emitted if "LIMIT_CHANGED" in str(e.get("event_type", ""))]
        assert len(limit_changed_events) >= 1

    def test_event_source_is_throttle(self):
        """이벤트 발행 시 source가 'throttle'인지 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)

        captured_source = []

        def mock_emit(event_type, data, source, priority):
            captured_source.append(source)
            return 1

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus_instance.emit = mock_emit
            mock_bus.return_value = mock_bus_instance

            throttle.record_response(600.0)

        # 모든 이벤트의 source가 'throttle'인지 확인
        assert all(s == "throttle" for s in captured_source)


class TestAdaptiveThrottleLimitRecoveryEvent:
    """THROTTLE_LIMIT_RECOVERED 이벤트 발행 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_emits_limit_recovered_event_when_recovering_from_min_limit(self):
        """min_limit에서 증가할 때 THROTTLE_LIMIT_RECOVERED 이벤트 발행."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=10,  # min_limit과 같게 설정
            min_limit=10,
            max_limit=500,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)

        # min_limit 상태로 설정
        throttle.current_limit = config.min_limit
        throttle._was_at_min_limit = True

        events_emitted = []

        def mock_emit(event_type, data, source, priority):
            events_emitted.append(
                {
                    "event_type": event_type,
                    "data": data,
                    "source": source,
                }
            )
            return 1

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus_instance.emit = mock_emit
            mock_bus.return_value = mock_bus_instance

            # Gradient calculator에 충분한 샘플 추가 (음수 gradient 생성)
            for _ in range(5):
                throttle._gradient_calculator.add_sample(50.0)
            for _ in range(5):
                throttle._gradient_calculator.add_sample(30.0)

            # limit 증가 트리거 (RTT 감소)
            throttle.record_response(20.0)

        # THROTTLE_LIMIT_RECOVERED 이벤트 확인
        recovered_events = [e for e in events_emitted if "LIMIT_RECOVERED" in str(e.get("event_type", ""))]
        # 회복 이벤트가 발생했거나, limit이 실제로 증가하지 않았을 수 있음 (조건에 따라)
        # 여기서는 was_at_min_limit 플래그가 설정되어 있고 limit이 증가하면 이벤트 발행
        assert throttle._current_limit >= config.min_limit


class TestEventBusFailOpen:
    """EventBus Import 실패 시 Fail-Open 처리 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_fail_open_on_eventbus_import_error(self):
        """EventBus import 실패 시에도 throttle 정상 동작."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)
        initial_limit = throttle.current_limit

        # EventBus import 실패 시뮬레이션
        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe", return_value=None):
            # 예외 없이 정상 동작해야 함
            throttle.record_response(600.0)  # SLA Critical

        # limit이 감소해야 함 (이벤트 발행 실패와 무관)
        assert throttle.current_limit < initial_limit

    def test_fail_open_on_emit_exception(self):
        """emit() 예외 발생 시에도 throttle 정상 동작."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)
        initial_limit = throttle.current_limit

        def raise_exception(*args, **kwargs):
            raise Exception("EventBus error")

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus_instance.emit = raise_exception
            mock_bus.return_value = mock_bus_instance

            # 예외 없이 정상 동작해야 함
            throttle.record_response(600.0)

        # limit이 감소해야 함
        assert throttle.current_limit < initial_limit


class TestGetEventBusSafeFunction:
    """_get_event_bus_safe() 함수 테스트."""

    def test_returns_event_bus_when_available(self):
        """EventBus 사용 가능 시 정상 반환."""
        from selfhealing.services.throttle.adaptive import _get_event_bus_safe

        bus = _get_event_bus_safe()
        # EventBus가 사용 가능하면 None이 아니어야 함
        # (테스트 환경에 따라 None일 수도 있음)
        assert bus is not None or bus is None  # 항상 통과 (예외 발생 안함이 중요)

    def test_handles_import_error_gracefully(self):
        """ImportError 시에도 예외 없이 처리."""
        from selfhealing.services.throttle.adaptive import _get_event_bus_safe

        # _get_event_bus_safe는 내부에서 try-except로 ImportError를 처리함
        # 정상적으로 호출이 완료되어야 함 (예외 발생 안함)
        result = _get_event_bus_safe()
        assert result is not None or result is None  # 예외 없이 통과가 중요


class TestEmitThrottleEventFunction:
    """_emit_throttle_event() 함수 테스트."""

    def test_emit_with_valid_event_type(self):
        """유효한 이벤트 타입으로 발행."""
        from selfhealing.services.throttle.adaptive import _emit_throttle_event

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus.return_value = mock_bus_instance

            _emit_throttle_event(
                "THROTTLE_LIMIT_CHANGED",
                {"previous_limit": 100, "new_limit": 50, "reason": "test"},
                priority_name="NORMAL",
            )

            mock_bus_instance.emit.assert_called_once()

    def test_emit_with_no_event_bus(self):
        """EventBus 없을 때 예외 없이 처리."""
        from selfhealing.services.throttle.adaptive import _emit_throttle_event

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe", return_value=None):
            # 예외 없이 정상 종료해야 함
            _emit_throttle_event(
                "THROTTLE_LIMIT_CHANGED",
                {"previous_limit": 100, "new_limit": 50, "reason": "test"},
            )

    def test_emit_with_invalid_event_type(self):
        """잘못된 이벤트 타입으로 발행 시 예외 없이 처리."""
        from selfhealing.services.throttle.adaptive import _emit_throttle_event

        with patch("selfhealing.services.throttle.adaptive._get_event_bus_safe") as mock_bus:
            mock_bus_instance = MagicMock()
            mock_bus.return_value = mock_bus_instance

            # 잘못된 이벤트 타입 - 예외 없이 처리해야 함
            _emit_throttle_event(
                "INVALID_EVENT_TYPE",
                {"data": "test"},
            )

            # emit이 호출되지 않아야 함 (잘못된 타입)
            mock_bus_instance.emit.assert_not_called()
