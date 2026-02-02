"""
RedisEventBus 다중 채널 지원 테스트.

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

import pytest
from unittest.mock import MagicMock, patch


class TestEventChannel:
    """EventChannel 열거형 테스트."""

    def test_event_channel_values(self):
        """모든 채널 값 확인."""
        from selfhealing.services.event_bus_redis import EventChannel

        assert EventChannel.CHAOS.value == "chaos"
        assert EventChannel.CONFIG.value == "config"
        assert EventChannel.EMERGENCY.value == "emergency"
        assert EventChannel.CIRCUIT_BREAKER.value == "circuit_breaker"
        assert EventChannel.THROTTLE.value == "throttle"
        assert EventChannel.GLOBAL.value == "global"


class TestEventTypeToChannel:
    """EventType -> Channel 매핑 테스트."""

    def test_chaos_events_mapped_to_chaos_channel(self):
        """Chaos 이벤트는 chaos 채널로 매핑."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import (
            EVENT_TYPE_TO_CHANNEL,
            EventChannel,
        )

        assert EVENT_TYPE_TO_CHANNEL[EventType.CHAOS_EXPERIMENT_STARTED] == EventChannel.CHAOS
        assert EVENT_TYPE_TO_CHANNEL[EventType.CHAOS_EXPERIMENT_STOPPED] == EventChannel.CHAOS

    def test_config_events_mapped_to_config_channel(self):
        """Config 이벤트는 config 채널로 매핑."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import (
            EVENT_TYPE_TO_CHANNEL,
            EventChannel,
        )

        assert EVENT_TYPE_TO_CHANNEL[EventType.CONFIG_UPDATED] == EventChannel.CONFIG
        assert EVENT_TYPE_TO_CHANNEL[EventType.KILL_SWITCH_ACTIVATED] == EventChannel.CONFIG

    def test_emergency_events_mapped_to_emergency_channel(self):
        """Emergency 이벤트는 emergency 채널로 매핑."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import (
            EVENT_TYPE_TO_CHANNEL,
            EventChannel,
        )

        assert EVENT_TYPE_TO_CHANNEL[EventType.EMERGENCY_LEVEL_CHANGED] == EventChannel.EMERGENCY
        assert EVENT_TYPE_TO_CHANNEL[EventType.EMERGENCY_ACTIVATED] == EventChannel.EMERGENCY

    def test_circuit_breaker_events_mapped_to_cb_channel(self):
        """CB 이벤트는 circuit_breaker 채널로 매핑."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import (
            EVENT_TYPE_TO_CHANNEL,
            EventChannel,
        )

        assert EVENT_TYPE_TO_CHANNEL[EventType.CIRCUIT_BREAKER_OPENED] == EventChannel.CIRCUIT_BREAKER
        assert EVENT_TYPE_TO_CHANNEL[EventType.CIRCUIT_BREAKER_CLOSED] == EventChannel.CIRCUIT_BREAKER

    def test_global_events_mapped_to_global_channel(self):
        """글로벌 이벤트는 global 채널로 매핑."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import (
            EVENT_TYPE_TO_CHANNEL,
            EventChannel,
        )

        assert EVENT_TYPE_TO_CHANNEL[EventType.ERROR_BUDGET_CRITICAL] == EventChannel.GLOBAL
        assert EVENT_TYPE_TO_CHANNEL[EventType.SECURITY_VIOLATION_CRITICAL] == EventChannel.GLOBAL


class TestSelfhealingEventChannels:
    """SELFHEALING_EVENT_CHANNELS 테스트."""

    def test_all_channels_defined(self):
        """모든 채널이 정의되어 있는지 확인."""
        from selfhealing.services.event_bus_redis import (
            SELFHEALING_EVENT_CHANNELS,
            EventChannel,
        )

        for channel in EventChannel:
            assert channel.value in SELFHEALING_EVENT_CHANNELS

    def test_channel_key_format(self):
        """채널 키 형식 확인."""
        from selfhealing.services.event_bus_redis import SELFHEALING_EVENT_CHANNELS

        for name, key in SELFHEALING_EVENT_CHANNELS.items():
            assert key.startswith("selfhealing:")
            assert name in key or name == "circuit_breaker" and "cb" in key


class TestRedisEventBusMultiChannel:
    """RedisEventBus 다중 채널 테스트."""

    @pytest.fixture(scope="class")
    def redis_bus(self):
        """RedisEventBus 인스턴스 (class-scoped로 재사용)."""
        from selfhealing.services.event_bus_redis import RedisEventBus

        with patch.dict("os.environ", {"REDIS_URL": ""}, clear=False):
            return RedisEventBus(redis_url=None)

    def test_init_with_default_channels(self, redis_bus):
        """기본 채널로 초기화."""
        from selfhealing.services.event_bus_redis import SELFHEALING_EVENT_CHANNELS

        assert redis_bus._channels == SELFHEALING_EVENT_CHANNELS

    def test_get_channel_for_event(self, redis_bus):
        """이벤트 타입에 맞는 채널 반환."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import SELFHEALING_EVENT_CHANNELS

        # Chaos 이벤트 → chaos 채널
        channel = redis_bus._get_channel_for_event(EventType.CHAOS_EXPERIMENT_STARTED)
        assert channel == SELFHEALING_EVENT_CHANNELS["chaos"]

        # Config 이벤트 → config 채널
        channel = redis_bus._get_channel_for_event(EventType.CONFIG_UPDATED)
        assert channel == SELFHEALING_EVENT_CHANNELS["config"]

    def test_get_channel_returns_global_for_unknown(self, redis_bus):
        """알 수 없는 이벤트 타입은 global 채널로."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.event_bus_redis import SELFHEALING_EVENT_CHANNELS

        # DLQ 이벤트는 매핑 안 됨 → global 채널
        channel = redis_bus._get_channel_for_event(EventType.DLQ_REPLAY_COMPLETED)
        assert channel == SELFHEALING_EVENT_CHANNELS["global"]


class TestRedisEventBusPublish:
    """RedisEventBus publish 테스트."""

    def test_publish_to_correct_channel(self):
        """이벤트 타입에 맞는 채널로 발행."""
        from selfhealing.services.event_bus import EventType, SelfHealingEvent
        from selfhealing.services.event_bus_redis import (
            RedisEventBus,
            SELFHEALING_EVENT_CHANNELS,
        )

        mock_redis = MagicMock()

        with patch.dict("os.environ", {"REDIS_URL": ""}, clear=False):
            bus = RedisEventBus(redis_url=None)
            bus._redis_client = mock_redis

            event = SelfHealingEvent(
                event_type=EventType.CHAOS_EXPERIMENT_STARTED,
                data={"experiment_id": "test-123"},
                source="test",
            )

            bus.publish(event)

            # chaos 채널로 발행되었는지 확인
            mock_redis.publish.assert_called_once()
            call_args = mock_redis.publish.call_args
            assert call_args[0][0] == SELFHEALING_EVENT_CHANNELS["chaos"]
