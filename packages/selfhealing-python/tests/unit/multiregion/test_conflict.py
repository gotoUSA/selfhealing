"""
Conflict Resolver 테스트.

테스트 대상:
- ConflictKey: 충돌 해결용 복합 키
- ConflictMetrics: 충돌 통계
- LastWriteWinsResolver: LWW 충돌 해결
- CRDTGCounter: G-Counter CRDT
- CRDTLWWRegister: LWW-Register CRDT
- CRDTResolver: CRDT 기반 충돌 해결
"""

from dataclasses import dataclass
from typing import Any

import pytest

from selfhealing.multiregion.config import reset_multiregion_settings
from selfhealing.multiregion.conflict import (
    ConflictKey,
    ConflictMetrics,
    CRDTGCounter,
    CRDTLWWRegister,
    CRDTResolver,
    LastWriteWinsResolver,
    get_conflict_resolver,
    reset_conflict_resolver,
)


@dataclass
class MockEvent:
    """테스트용 모의 이벤트."""

    key: str
    value: Any
    timestamp: float
    region_priority: int = 100
    cluster_id: str = "test-cluster"


class TestConflictKey:
    """ConflictKey 클래스 테스트."""

    def test_timestamp_comparison(self) -> None:
        """타임스탬프 비교."""
        key1 = ConflictKey(timestamp=1000.0, region_priority=100, cluster_id="a")
        key2 = ConflictKey(timestamp=2000.0, region_priority=100, cluster_id="a")

        assert key2 > key1
        assert key1 < key2

    def test_priority_comparison_when_timestamp_equal(self) -> None:
        """동일 타임스탬프 시 우선순위 비교."""
        key1 = ConflictKey(timestamp=1000.0, region_priority=100, cluster_id="a")
        key2 = ConflictKey(timestamp=1000.0, region_priority=50, cluster_id="a")

        # 낮은 priority가 더 높은 우선순위
        assert key2 > key1
        assert key1 < key2

    def test_cluster_id_comparison_when_both_equal(self) -> None:
        """동일 타임스탬프, 동일 우선순위 시 cluster_id 비교."""
        key1 = ConflictKey(timestamp=1000.0, region_priority=100, cluster_id="aaa")
        key2 = ConflictKey(timestamp=1000.0, region_priority=100, cluster_id="zzz")

        assert key2 > key1
        assert key1 < key2

    def test_equality(self) -> None:
        """동등성 비교."""
        key1 = ConflictKey(timestamp=1000.0, region_priority=100, cluster_id="a")
        key2 = ConflictKey(timestamp=1000.0, region_priority=100, cluster_id="a")

        assert key1 == key2
        assert not (key1 > key2)
        assert not (key1 < key2)


class TestConflictMetrics:
    """ConflictMetrics 클래스 테스트."""

    def test_initial_state(self) -> None:
        """초기 상태."""
        metrics = ConflictMetrics()

        assert metrics._total_events == 0
        assert metrics._conflicts_detected == 0
        assert metrics.get_conflict_ratio() == 0.0

    def test_record_non_conflict_event(self) -> None:
        """비충돌 이벤트 기록."""
        metrics = ConflictMetrics()
        metrics.record_event(is_conflict=False)

        assert metrics._total_events == 1
        assert metrics._conflicts_detected == 0

    def test_record_conflict_event(self) -> None:
        """충돌 이벤트 기록."""
        metrics = ConflictMetrics()
        metrics.record_event(is_conflict=True, resolution_method="timestamp")

        assert metrics._total_events == 1
        assert metrics._conflicts_detected == 1
        assert metrics._conflicts_resolved_by_timestamp == 1

    def test_conflict_ratio(self) -> None:
        """충돌 비율 계산."""
        metrics = ConflictMetrics()
        metrics.record_event(is_conflict=False)
        metrics.record_event(is_conflict=True, resolution_method="timestamp")
        metrics.record_event(is_conflict=False)
        metrics.record_event(is_conflict=True, resolution_method="priority")

        assert metrics.get_conflict_ratio() == 0.5  # 2/4

    def test_get_stats(self) -> None:
        """통계 조회."""
        metrics = ConflictMetrics()
        metrics.record_event(is_conflict=True, resolution_method="timestamp")
        metrics.record_event(is_conflict=True, resolution_method="priority")
        metrics.record_event(is_conflict=True, resolution_method="cluster_id")
        metrics.record_event(is_conflict=True, resolution_method="dropped")

        stats = metrics.get_stats()

        assert stats["total_events"] == 4
        assert stats["conflicts_detected"] == 4
        assert stats["by_timestamp"] == 1
        assert stats["by_priority"] == 1
        assert stats["by_cluster_id"] == 1
        assert stats["dropped"] == 1

    def test_reset(self) -> None:
        """통계 리셋."""
        metrics = ConflictMetrics()
        metrics.record_event(is_conflict=True)
        metrics.reset()

        assert metrics._total_events == 0


class TestLastWriteWinsResolver:
    """LastWriteWinsResolver 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def test_first_event_accepted(self) -> None:
        """첫 이벤트는 항상 수락."""
        resolver = LastWriteWinsResolver()
        event = MockEvent(key="test:key", value="value1", timestamp=1000.0)

        result = resolver.resolve(event)

        assert result is event

    def test_newer_event_accepted(self) -> None:
        """더 최신 이벤트 수락."""
        resolver = LastWriteWinsResolver()

        event1 = MockEvent(key="test:key", value="value1", timestamp=1000.0)
        event2 = MockEvent(key="test:key", value="value2", timestamp=2000.0)

        resolver.resolve(event1)
        result = resolver.resolve(event2)

        assert result is event2

    def test_older_event_rejected(self) -> None:
        """더 오래된 이벤트 거부."""
        resolver = LastWriteWinsResolver()

        event1 = MockEvent(key="test:key", value="value1", timestamp=2000.0)
        event2 = MockEvent(key="test:key", value="value2", timestamp=1000.0)

        resolver.resolve(event1)
        result = resolver.resolve(event2)

        assert result is None

    def test_tie_breaking_by_priority(self) -> None:
        """동일 타임스탬프 시 우선순위로 해결."""
        resolver = LastWriteWinsResolver()

        event1 = MockEvent(key="test:key", value="v1", timestamp=1000.0, region_priority=100)
        event2 = MockEvent(key="test:key", value="v2", timestamp=1000.0, region_priority=50)

        resolver.resolve(event1)
        result = resolver.resolve(event2)

        # 낮은 priority가 승리
        assert result is event2

    def test_tie_breaking_by_cluster_id(self) -> None:
        """동일 타임스탬프, 동일 우선순위 시 cluster_id로 해결."""
        resolver = LastWriteWinsResolver()

        event1 = MockEvent(
            key="test:key",
            value="v1",
            timestamp=1000.0,
            region_priority=100,
            cluster_id="aaa",
        )
        event2 = MockEvent(
            key="test:key",
            value="v2",
            timestamp=1000.0,
            region_priority=100,
            cluster_id="zzz",
        )

        resolver.resolve(event1)
        result = resolver.resolve(event2)

        # 알파벳순 뒤가 승리
        assert result is event2

    def test_metrics_tracked(self) -> None:
        """메트릭 추적."""
        resolver = LastWriteWinsResolver()

        event1 = MockEvent(key="k", value="v1", timestamp=1000.0)
        event2 = MockEvent(key="k", value="v2", timestamp=2000.0)
        event3 = MockEvent(key="k", value="v3", timestamp=1500.0)  # 거부됨

        resolver.resolve(event1)
        resolver.resolve(event2)
        resolver.resolve(event3)

        metrics = resolver.get_metrics()
        stats = metrics.get_stats()

        assert stats["total_events"] == 3
        assert stats["conflicts_detected"] == 2  # event2, event3

    def test_different_keys_independent(self) -> None:
        """다른 키는 독립적."""
        resolver = LastWriteWinsResolver()

        event_a = MockEvent(key="key:a", value="a", timestamp=1000.0)
        event_b = MockEvent(key="key:b", value="b", timestamp=500.0)

        result_a = resolver.resolve(event_a)
        result_b = resolver.resolve(event_b)

        assert result_a is event_a
        assert result_b is event_b


class TestCRDTGCounter:
    """CRDTGCounter 클래스 테스트."""

    def test_initial_value(self) -> None:
        """초기값은 0."""
        counter = CRDTGCounter()
        assert counter.value() == 0

    def test_increment(self) -> None:
        """증가."""
        counter = CRDTGCounter()
        counter.increment("node-1", 5)
        counter.increment("node-1", 3)

        assert counter.value() == 8

    def test_multiple_nodes(self) -> None:
        """여러 노드."""
        counter = CRDTGCounter()
        counter.increment("node-1", 10)
        counter.increment("node-2", 20)
        counter.increment("node-3", 30)

        assert counter.value() == 60

    def test_merge(self) -> None:
        """머지."""
        counter1 = CRDTGCounter()
        counter1.increment("node-1", 10)
        counter1.increment("node-2", 5)

        counter2 = CRDTGCounter()
        counter2.increment("node-1", 8)  # 더 작음
        counter2.increment("node-2", 15)  # 더 큼
        counter2.increment("node-3", 20)  # 새 노드

        counter1.merge(counter2)

        # node-1: max(10, 8) = 10
        # node-2: max(5, 15) = 15
        # node-3: 20
        assert counter1.value() == 45

    def test_increment_negative_raises(self) -> None:
        """음수 증가는 에러."""
        counter = CRDTGCounter()

        with pytest.raises(ValueError, match="increment"):
            counter.increment("node-1", -5)

    def test_serialization(self) -> None:
        """직렬화/역직렬화."""
        counter = CRDTGCounter()
        counter.increment("a", 10)
        counter.increment("b", 20)

        data = counter.to_dict()
        restored = CRDTGCounter.from_dict(data)

        assert restored.value() == counter.value()


class TestCRDTLWWRegister:
    """CRDTLWWRegister 클래스 테스트."""

    def test_initial_value(self) -> None:
        """초기값은 None."""
        register = CRDTLWWRegister()
        assert register.value() is None

    def test_set_value(self) -> None:
        """값 설정."""
        register = CRDTLWWRegister()
        result = register.set("hello", timestamp=1000.0)

        assert result is True
        assert register.value() == "hello"

    def test_set_newer_value(self) -> None:
        """더 최신 값 설정."""
        register = CRDTLWWRegister()
        register.set("old", timestamp=1000.0)
        result = register.set("new", timestamp=2000.0)

        assert result is True
        assert register.value() == "new"

    def test_set_older_value_ignored(self) -> None:
        """더 오래된 값 무시."""
        register = CRDTLWWRegister()
        register.set("new", timestamp=2000.0)
        result = register.set("old", timestamp=1000.0)

        assert result is False
        assert register.value() == "new"

    def test_merge(self) -> None:
        """머지."""
        register1 = CRDTLWWRegister()
        register1.set("v1", timestamp=1000.0)

        register2 = CRDTLWWRegister()
        register2.set("v2", timestamp=2000.0)

        register1.merge(register2)

        assert register1.value() == "v2"

    def test_serialization(self) -> None:
        """직렬화/역직렬화."""
        register = CRDTLWWRegister()
        register.set("test", timestamp=1234.5)

        data = register.to_dict()
        restored = CRDTLWWRegister.from_dict(data)

        assert restored.value() == "test"
        assert restored.timestamp() == 1234.5


class TestCRDTResolver:
    """CRDTResolver 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def test_gcounter_key(self) -> None:
        """counter: 키는 G-Counter로 처리."""
        resolver = CRDTResolver()

        event = MockEvent(
            key="counter:page_views",
            value={"node-1": 100, "node-2": 50},
            timestamp=1000.0,
        )

        result = resolver.resolve(event)

        assert result is event
        assert result.value == 150  # 합계

    def test_lww_register_key(self) -> None:
        """register: 키는 LWW-Register로 처리."""
        resolver = CRDTResolver()

        event = MockEvent(
            key="register:config",
            value={"value": "config_data", "timestamp": 1000.0},
            timestamp=1000.0,
        )

        result = resolver.resolve(event)

        assert result is event
        assert result.value == "config_data"

    def test_fallback_to_lww(self) -> None:
        """다른 키는 LWW 폴백."""
        resolver = CRDTResolver()

        event = MockEvent(key="some:other:key", value="v1", timestamp=1000.0)
        result = resolver.resolve(event)

        assert result is event

    def test_clear_state(self) -> None:
        """상태 초기화."""
        resolver = CRDTResolver()

        event = MockEvent(key="counter:x", value={"a": 10}, timestamp=1000.0)
        resolver.resolve(event)

        resolver.clear_state()

        # 상태가 초기화되어 새 이벤트로 처리
        event2 = MockEvent(key="counter:x", value={"b": 5}, timestamp=1000.0)
        result = resolver.resolve(event2)

        assert result.value == 5  # 이전 값 없이 새로 시작


class TestFactory:
    """팩토리 함수 테스트."""

    def setup_method(self) -> None:
        """테스트 전 리셋."""
        reset_conflict_resolver()
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 리셋."""
        reset_conflict_resolver()
        reset_multiregion_settings()

    def test_get_conflict_resolver_singleton(self) -> None:
        """싱글톤 패턴."""
        resolver1 = get_conflict_resolver()
        resolver2 = get_conflict_resolver()

        assert resolver1 is resolver2

    def test_reset_conflict_resolver(self) -> None:
        """리졸버 리셋."""
        resolver1 = get_conflict_resolver()
        reset_conflict_resolver()
        resolver2 = get_conflict_resolver()

        assert resolver1 is not resolver2
