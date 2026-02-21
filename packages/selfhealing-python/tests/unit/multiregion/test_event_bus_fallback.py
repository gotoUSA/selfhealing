"""
RedisEventBus 이벤트 전파 폴백 체인 테스트.

테스트 대상:
- _is_critical_event(): 크리티컬 이벤트 판별
- publish(): Redis → Kafka → WAL 폴백 체인
- _publish_to_kafka_fallback(): Kafka 폴백 발행
- _write_to_wal(): WAL 최종 안전망
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.event_bus.bus import (
    EventType,
    SelfHealingEvent,
)
from selfhealing.services.event_bus.redis_bus import (
    CRITICAL_EVENT_TYPES,
    RedisEventBus,
)


def _make_bus_no_redis() -> RedisEventBus:
    """Redis 연결 없는 테스트용 버스 생성."""
    with (
        patch.object(RedisEventBus, "_connect_redis", return_value=False),
        patch.object(RedisEventBus, "_get_redis_url", return_value=None),
    ):
        bus = RedisEventBus(redis_url=None)
    bus._redis_client = None
    return bus


def _make_bus_with_redis(mock_redis: MagicMock) -> RedisEventBus:
    """Redis 클라이언트가 설정된 테스트용 버스 생성."""
    with (
        patch.object(RedisEventBus, "_connect_redis", return_value=True),
        patch.object(RedisEventBus, "_get_redis_url", return_value=None),
    ):
        bus = RedisEventBus(redis_url=None)
    bus._redis_client = mock_redis
    return bus


def _make_critical_event() -> SelfHealingEvent:
    return SelfHealingEvent(
        event_type=EventType.REGION_PRIMARY_CHANGED,
        data={"key": "region_primary", "value": "us-west-2"},
        source="failover",
    )


def _make_normal_event() -> SelfHealingEvent:
    return SelfHealingEvent(
        event_type=EventType.CONFIG_UPDATED,
        data={"key": "test"},
        source="test",
    )


# =============================================================================
# CRITICAL_EVENT_TYPES 계약 검증
# =============================================================================


class TestCriticalEventTypesContract:
    """크리티컬 이벤트 타입 계약값 검증."""

    def test_contains_region_primary_changed(self) -> None:
        """REGION_PRIMARY_CHANGED가 크리티컬 이벤트에 포함된다."""
        assert EventType.REGION_PRIMARY_CHANGED in CRITICAL_EVENT_TYPES

    def test_contains_emergency_activated(self) -> None:
        """EMERGENCY_ACTIVATED가 크리티컬 이벤트에 포함된다."""
        assert EventType.EMERGENCY_ACTIVATED in CRITICAL_EVENT_TYPES

    def test_contains_emergency_deactivated(self) -> None:
        """EMERGENCY_DEACTIVATED가 크리티컬 이벤트에 포함된다."""
        assert EventType.EMERGENCY_DEACTIVATED in CRITICAL_EVENT_TYPES

    def test_contains_kill_switch_activated(self) -> None:
        """KILL_SWITCH_ACTIVATED가 크리티컬 이벤트에 포함된다."""
        assert EventType.KILL_SWITCH_ACTIVATED in CRITICAL_EVENT_TYPES

    def test_count(self) -> None:
        """크리티컬 이벤트 타입은 4개이다."""
        assert len(CRITICAL_EVENT_TYPES) == 4

    def test_is_frozenset(self) -> None:
        """CRITICAL_EVENT_TYPES은 frozenset이다."""
        assert isinstance(CRITICAL_EVENT_TYPES, frozenset)


# =============================================================================
# _is_critical_event() 동작 검증
# =============================================================================


class TestIsCriticalEventBehavior:
    """_is_critical_event() 동작 검증."""

    def test_critical_event_returns_true(self) -> None:
        """크리티컬 이벤트 타입이면 True를 반환한다."""
        bus = _make_bus_no_redis()
        for event_type in CRITICAL_EVENT_TYPES:
            event = SelfHealingEvent(
                event_type=event_type,
                data={},
                source="test",
            )
            assert bus._is_critical_event(event) is True

    def test_non_critical_event_returns_false(self) -> None:
        """비크리티컬 이벤트 타입이면 False를 반환한다."""
        bus = _make_bus_no_redis()
        event = SelfHealingEvent(
            event_type=EventType.CONFIG_UPDATED,
            data={},
            source="test",
        )
        assert bus._is_critical_event(event) is False


# =============================================================================
# publish() 폴백 체인 동작 검증
# =============================================================================


class TestPublishFallbackChainBehavior:
    """publish() Redis → Kafka → WAL 폴백 체인 동작 검증."""

    def test_redis_success_does_not_trigger_fallback(self) -> None:
        """Redis 발행 성공 시 Kafka/WAL 폴백이 호출되지 않는다."""
        mock_redis = MagicMock()
        bus = _make_bus_with_redis(mock_redis)

        with patch.object(bus, "_publish_to_kafka_fallback") as mock_kafka, patch.object(bus, "_write_to_wal") as mock_wal:
            bus.publish(_make_critical_event())

            mock_kafka.assert_not_called()
            mock_wal.assert_not_called()

    def test_redis_failure_triggers_kafka_for_critical(self) -> None:
        """Redis 실패 + 크리티컬 이벤트 → Kafka 폴백 호출."""
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        bus = _make_bus_with_redis(mock_redis)

        with patch.object(bus, "_publish_to_kafka_fallback") as mock_kafka, patch.object(bus, "_write_to_wal") as mock_wal:
            bus.publish(_make_critical_event())

            mock_kafka.assert_called_once()
            mock_wal.assert_not_called()

    def test_redis_failure_no_kafka_for_non_critical(self) -> None:
        """Redis 실패 + 비크리티컬 이벤트 → Kafka 폴백 호출되지 않음."""
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        bus = _make_bus_with_redis(mock_redis)

        with patch.object(bus, "_publish_to_kafka_fallback") as mock_kafka, patch.object(bus, "_write_to_wal") as mock_wal:
            bus.publish(_make_normal_event())

            mock_kafka.assert_not_called()
            mock_wal.assert_not_called()

    def test_redis_and_kafka_failure_triggers_wal(self) -> None:
        """Redis + Kafka 모두 실패 시 크리티컬 이벤트는 WAL에 기록된다."""
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        bus = _make_bus_with_redis(mock_redis)

        with (
            patch.object(bus, "_publish_to_kafka_fallback", side_effect=Exception("Kafka down")),
            patch.object(bus, "_write_to_wal") as mock_wal,
        ):
            bus.publish(_make_critical_event())

            mock_wal.assert_called_once()

    def test_no_redis_client_triggers_kafka_fallback(self) -> None:
        """Redis 클라이언트가 없으면 크리티컬 이벤트는 Kafka로 폴백."""
        bus = _make_bus_no_redis()

        with patch.object(bus, "_publish_to_kafka_fallback") as mock_kafka:
            bus.publish(_make_critical_event())
            mock_kafka.assert_called_once()

    def test_local_bus_always_receives_event(self) -> None:
        """로컬 핸들러에는 항상 이벤트가 전달된다."""
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        bus = _make_bus_with_redis(mock_redis)

        received = []
        bus._local_bus.subscribe(
            EventType.REGION_PRIMARY_CHANGED,
            lambda e: received.append(e),
        )

        with patch.object(bus, "_publish_to_kafka_fallback"):
            bus.publish(_make_critical_event())

        assert len(received) == 1

    def test_propagate_false_skips_all_remote(self) -> None:
        """propagate_to_redis=False이면 Redis/Kafka/WAL 모두 건너뛴다."""
        mock_redis = MagicMock()
        bus = _make_bus_with_redis(mock_redis)

        with patch.object(bus, "_publish_to_kafka_fallback") as mock_kafka, patch.object(bus, "_write_to_wal") as mock_wal:
            bus.publish(_make_critical_event(), propagate_to_redis=False)

            mock_redis.publish.assert_not_called()
            mock_kafka.assert_not_called()
            mock_wal.assert_not_called()


# =============================================================================
# _publish_to_kafka_fallback() 동작 검증
# =============================================================================


class TestPublishToKafkaFallbackBehavior:
    """_publish_to_kafka_fallback() 동작 검증."""

    @patch.dict("os.environ", {"KAFKA_BOOTSTRAP_SERVERS": ""})
    def test_raises_when_no_kafka_bootstrap(self) -> None:
        """KAFKA_BOOTSTRAP_SERVERS 미설정 시 RuntimeError를 발생시킨다."""
        bus = _make_bus_no_redis()
        event = SelfHealingEvent(
            event_type=EventType.REGION_PRIMARY_CHANGED,
            data={},
            source="test",
        )
        with pytest.raises(RuntimeError, match="KAFKA_BOOTSTRAP_SERVERS"):
            bus._publish_to_kafka_fallback(event)

    @patch.dict("os.environ", {"KAFKA_BOOTSTRAP_SERVERS": "kafka:9092"})
    def test_raises_when_kafka_package_missing(self) -> None:
        """kafka-python 미설치 시 RuntimeError를 발생시킨다."""
        bus = _make_bus_no_redis()
        event = SelfHealingEvent(
            event_type=EventType.REGION_PRIMARY_CHANGED,
            data={},
            source="test",
        )
        with patch.dict("sys.modules", {"kafka": None}):
            with pytest.raises((RuntimeError, ImportError)):
                bus._publish_to_kafka_fallback(event)


# =============================================================================
# _write_to_wal() 동작 검증
# =============================================================================


class TestWriteToWalBehavior:
    """_write_to_wal() 동작 검증."""

    @patch("selfhealing.audit.wal.WriteAheadLog")
    @patch("selfhealing.audit.wal._models.WALConfig")
    def test_writes_event_to_wal(self, mock_config_cls: MagicMock, mock_wal_cls: MagicMock) -> None:
        """크리티컬 이벤트를 WAL에 기록한다."""
        mock_wal = MagicMock()
        mock_wal_cls.return_value = mock_wal

        bus = _make_bus_no_redis()
        event = SelfHealingEvent(
            event_type=EventType.REGION_PRIMARY_CHANGED,
            data={"key": "test"},
            source="failover",
        )
        bus._write_to_wal(event)

        mock_wal.write.assert_called_once()

    def test_wal_import_failure_does_not_raise(self) -> None:
        """WAL import 실패 시 예외가 전파되지 않는다."""
        bus = _make_bus_no_redis()
        event = SelfHealingEvent(
            event_type=EventType.REGION_PRIMARY_CHANGED,
            data={},
            source="test",
        )
        with patch(
            "selfhealing.audit.wal.WriteAheadLog",
            side_effect=ImportError("no WAL module"),
        ):
            # 예외 없이 완료
            bus._write_to_wal(event)
