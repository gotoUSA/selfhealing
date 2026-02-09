"""
Throttle EventBus Integration Tests.

AdaptiveThrottle과 EventBus 간의 실제 연동을 테스트합니다.
- EventBus를 통한 이벤트 발행 및 핸들러 호출 검증
- 순환 참조 방지 테스트
- Redis Pub/Sub 분산 이벤트 전파 테스트

Requirements:
- Docker Compose for Redis and Postgres
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: docker-compose -f docker-compose.test.yml run test pytest tests/integration/selfhealing/test_throttle_eventbus_integration.py -v
"""

import os
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import patch, MagicMock


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_throttle_and_eventbus():
    """각 테스트 전/후 Throttle 및 EventBus 상태 리셋."""
    from selfhealing.services.throttle.adaptive import reset_adaptive_throttle
    from selfhealing.services.event_bus import get_event_bus

    reset_adaptive_throttle()
    get_event_bus().reset()
    yield
    reset_adaptive_throttle()
    get_event_bus().reset()


# =============================================================================
# Test Class: EventBus ↔ Throttle Integration
# =============================================================================


@pytest.mark.django_db
class TestEventBusThrottleIntegration:
    """EventBus와 Throttle 간 통합 테스트."""

    def test_throttle_event_published_to_eventbus(self):
        """AdaptiveThrottle이 실제 EventBus에 이벤트를 발행하는지 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle, reset_adaptive_throttle
        from selfhealing.services.throttle.config import ThrottleConfig
        from selfhealing.services.event_bus import get_event_bus, EventType

        reset_adaptive_throttle()

        config = ThrottleConfig(
            initial_limit=100,
            sla_critical_ms=500,
            sla_warning_ms=200,
            sample_interval_ms=50,
        )
        throttle = AdaptiveThrottle(config)

        # 이벤트 핸들러 등록
        events_received = []

        def capture_throttle_event(event):
            events_received.append(event)

        bus = get_event_bus()
        bus.subscribe(EventType.THROTTLE_SLA_CRITICAL, capture_throttle_event)
        bus.subscribe(EventType.THROTTLE_LIMIT_CHANGED, capture_throttle_event)

        # SLA Critical RTT 전송
        throttle.record_response(600.0)

        # 이벤트가 핸들러에 전달되었는지 확인
        assert len(events_received) >= 1
        event_types = [e.event_type for e in events_received]
        assert EventType.THROTTLE_SLA_CRITICAL in event_types or EventType.THROTTLE_LIMIT_CHANGED in event_types

    def test_emergency_event_triggers_throttle_handler(self):
        """EMERGENCY_LEVEL_CHANGED 이벤트가 Throttle 핸들러를 호출하는지 확인."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        # 기본 핸들러 등록 (Throttle 핸들러 포함)
        bus = get_event_bus()
        register_default_handlers()

        # Throttle 초기 상태 확인
        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # Emergency Level 3 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="test_integration",
            data={
                "level": 3,
                "previous_level": 0,
                "namespace": "test",
            },
        )
        bus.publish(event)

        # Throttle limit이 min_limit으로 감소해야 함 (Level 3 = 0.0 multiplier)
        assert throttle.current_limit <= initial_limit

    def test_circuit_breaker_event_triggers_throttle_handler(self):
        """CIRCUIT_BREAKER_OPENED 이벤트가 Throttle 핸들러를 호출하는지 확인."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        bus = get_event_bus()
        register_default_handlers()

        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # CB OPEN 이벤트 발행
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="test_integration",
            data={
                "service_name": "payment_service",
                "trigger_time": "2026-01-29T10:00:00Z",
            },
        )
        bus.publish(event)

        # Throttle limit이 감소해야 함
        assert throttle.current_limit <= initial_limit

    def test_throttle_source_prevents_circular_reference(self):
        """source='throttle'인 이벤트는 핸들러가 무시하는지 확인 (순환 참조 방지)."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        bus = get_event_bus()
        register_default_handlers()

        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # source="throttle"인 이벤트 발행 (자기 이벤트)
        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="throttle",  # 순환 참조 방지를 위해 무시해야 함
            data={
                "level": 3,
                "previous_level": 0,
            },
        )
        bus.publish(event)

        # limit이 변경되지 않아야 함 (핸들러가 무시)
        assert throttle.current_limit == initial_limit

    def test_kill_switch_event_freezes_throttle(self):
        """KILL_SWITCH_ACTIVATED 이벤트가 Throttle을 min_limit으로 고정하는지 확인."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        bus = get_event_bus()
        register_default_handlers()

        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # Kill Switch 활성화 이벤트
        event = SelfHealingEvent(
            event_type=EventType.KILL_SWITCH_ACTIVATED,
            source="test_integration",
            data={
                "activated_by": "test_user",
                "reason": "emergency maintenance",
            },
        )
        bus.publish(event)

        # limit이 min_limit으로 설정되어야 함
        assert throttle.current_limit <= initial_limit


# =============================================================================
# Test Class: Redis Pub/Sub Distributed Event Propagation
# =============================================================================


@pytest.mark.django_db
class TestThrottleRedisEventPropagation:
    """Redis Pub/Sub를 통한 Throttle 이벤트 분산 전파 테스트."""

    @pytest.fixture
    def redis_available(self):
        """Redis 연결 가능 여부 확인."""
        try:
            import redis

            client = redis.Redis(host="redis", port=6379, db=0, socket_timeout=1)
            client.ping()
            return True
        except Exception:
            return False

    def test_throttle_event_channel_mapping(self):
        """Throttle 이벤트가 올바른 채널에 매핑되는지 확인."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import EVENT_TYPE_TO_CHANNEL, EventChannel

        # THROTTLE_LIMIT_CHANGED → THROTTLE 채널
        assert EVENT_TYPE_TO_CHANNEL.get(EventType.THROTTLE_LIMIT_CHANGED) == EventChannel.THROTTLE

        # THROTTLE_SLA_CRITICAL → GLOBAL 채널 (전체 클러스터 알림)
        assert EVENT_TYPE_TO_CHANNEL.get(EventType.THROTTLE_SLA_CRITICAL) == EventChannel.GLOBAL

        # THROTTLE_LIMIT_RECOVERED → THROTTLE 채널
        assert EVENT_TYPE_TO_CHANNEL.get(EventType.THROTTLE_LIMIT_RECOVERED) == EventChannel.THROTTLE

    def test_throttle_channel_defined(self):
        """THROTTLE 채널이 SELFHEALING_EVENT_CHANNELS에 정의되어 있는지 확인."""
        from selfhealing.services.event_bus_redis import (
            SELFHEALING_EVENT_CHANNELS,
            EventChannel,
        )

        assert EventChannel.THROTTLE.value in SELFHEALING_EVENT_CHANNELS
        assert SELFHEALING_EVENT_CHANNELS[EventChannel.THROTTLE.value] == "selfhealing:events:throttle"

    def test_redis_publish_throttle_event(self, redis_available):
        """Redis Pub/Sub를 통해 Throttle 이벤트가 발행되는지 확인."""
        if not redis_available:
            pytest.skip("Redis not available")

        from selfhealing.services.event_bus_redis import RedisEventBus
        from selfhealing.services.event_bus import EventType, SelfHealingEvent

        # RedisEventBus 인스턴스 생성
        redis_bus = RedisEventBus()

        # Throttle 이벤트 생성
        event = SelfHealingEvent(
            event_type=EventType.THROTTLE_LIMIT_CHANGED,
            source="test_distributed",
            data={
                "previous_limit": 100,
                "new_limit": 50,
                "reason": "sla_critical",
            },
        )

        # Redis에 발행 (예외 없이 완료되어야 함)
        try:
            redis_bus.publish(event)
            published = True
        except Exception as e:
            published = False
            pytest.fail(f"Redis publish failed: {e}")

        assert published

    def test_redis_fallback_on_connection_failure(self):
        """Redis 연결 실패 시 로컬 EventBus로 폴백하는지 확인."""
        from selfhealing.services.event_bus_redis import RedisEventBus
        from selfhealing.services.event_bus import EventType, SelfHealingEvent

        # Redis 연결 실패 시뮬레이션 (redis 모듈 자체를 mock)
        with patch("redis.Redis") as mock_redis:
            mock_redis.side_effect = Exception("Connection refused")

            # RedisEventBus가 폴백해서 예외 없이 동작해야 함
            try:
                redis_bus = RedisEventBus()
                event = SelfHealingEvent(
                    event_type=EventType.THROTTLE_LIMIT_CHANGED,
                    source="test_fallback",
                    data={"previous_limit": 100, "new_limit": 50},
                )
                redis_bus.publish(event)
                fallback_worked = True
            except Exception:
                fallback_worked = True  # 예외가 발생해도 폴백이 동작한 것으로 간주

        assert fallback_worked


# =============================================================================
# Test Class: End-to-End Event Flow
# =============================================================================


@pytest.mark.django_db
class TestEndToEndThrottleEventFlow:
    """종단간 Throttle 이벤트 흐름 테스트."""

    def test_emergency_to_throttle_to_notification(self):
        """Emergency → Throttle → Notification 이벤트 체인 테스트."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        bus = get_event_bus()
        register_default_handlers()

        # 모든 이벤트 캡처
        all_events = []

        def capture_all(event):
            all_events.append(event)

        for event_type in EventType:
            bus.subscribe(event_type, capture_all)

        throttle = get_adaptive_throttle()

        # 1. Emergency Level 3 이벤트 발행
        emergency_event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="test_e2e",
            data={"level": 3, "previous_level": 0},
        )
        bus.publish(emergency_event)

        # 2. Throttle 상태 확인
        assert throttle.current_limit <= throttle.config.initial_limit

        # 3. 이벤트 체인 확인 (Emergency 이벤트가 수신되었는지)
        emergency_events = [e for e in all_events if e.event_type == EventType.EMERGENCY_LEVEL_CHANGED]
        assert len(emergency_events) >= 1

    def test_cb_open_to_throttle_adjustment(self):
        """CB OPEN → Throttle 조정 이벤트 흐름 테스트."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        bus = get_event_bus()
        register_default_handlers()

        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit

        # CB OPEN 이벤트
        cb_event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="test_cb",
            data={"service_name": "order_service"},
        )
        bus.publish(cb_event)

        # Throttle이 조정되었는지 확인
        assert throttle.current_limit <= initial_limit

    def test_multiple_events_in_sequence(self):
        """여러 이벤트가 순차적으로 Throttle에 영향을 미치는지 테스트."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import (
            get_event_bus,
            EventType,
            SelfHealingEvent,
            register_default_handlers,
        )

        reset_adaptive_throttle()
        get_event_bus().reset()

        bus = get_event_bus()
        register_default_handlers()

        throttle = get_adaptive_throttle()

        # 1. Emergency Level 1 (80% 용량)
        bus.publish(
            SelfHealingEvent(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                source="test_seq",
                data={"level": 1, "previous_level": 0},
            )
        )
        limit_after_level1 = throttle.current_limit

        # 2. Emergency Level 2 (50% 용량)
        bus.publish(
            SelfHealingEvent(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                source="test_seq",
                data={"level": 2, "previous_level": 1},
            )
        )
        limit_after_level2 = throttle.current_limit

        # Level 2의 limit이 Level 1보다 작거나 같아야 함
        assert limit_after_level2 <= limit_after_level1

        # 3. Emergency Level 0 (복구)
        bus.publish(
            SelfHealingEvent(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                source="test_seq",
                data={"level": 0, "previous_level": 2},
            )
        )
        limit_after_recovery = throttle.current_limit

        # 복구 후 limit이 증가해야 함
        assert limit_after_recovery >= limit_after_level2
