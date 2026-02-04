"""
Region Replicator 테스트.

테스트 대상:
- ReplicationEventType: 복제 이벤트 타입 Enum
- ReplicationEvent: 복제 이벤트 데이터클래스
- ReplicationFilter: 복제 필터
- RegionReplicator: 리전 복제기
"""

import time

import pytest

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.replicator import (
    ReplicationEvent,
    ReplicationEventType,
    ReplicationFilter,
    RegionReplicator,
)


class TestReplicationEventType:
    """ReplicationEventType Enum 테스트."""

    def test_values(self) -> None:
        """Enum 값 확인."""
        assert ReplicationEventType.SET.value == "set"
        assert ReplicationEventType.DELETE.value == "delete"
        assert ReplicationEventType.EXPIRE.value == "expire"
        assert ReplicationEventType.HSET.value == "hset"


class TestReplicationEvent:
    """ReplicationEvent 데이터클래스 테스트."""

    def test_create_event(self) -> None:
        """이벤트 생성."""
        event = ReplicationEvent(
            event_type=ReplicationEventType.SET,
            key="cb:payment:123",
            value=b"closed",
        )

        assert event.event_type == ReplicationEventType.SET
        assert event.key == "cb:payment:123"
        assert event.value == b"closed"

    def test_from_dict(self) -> None:
        """딕셔너리에서 이벤트 생성."""
        data = {
            "event_type": "set",
            "key": "cb:test:key",
            "value": "test_value",
            "timestamp": 1234567890.0,
            "source_region": "us-east-1",
        }

        event = ReplicationEvent.from_dict(data)

        assert event.event_type == ReplicationEventType.SET
        assert event.key == "cb:test:key"
        assert event.value == "test_value"
        assert event.source_region == "us-east-1"


class TestReplicationFilter:
    """ReplicationFilter 테스트."""

    def test_default_patterns(self) -> None:
        """기본 패턴."""
        flt = ReplicationFilter()

        # 기본 포함 패턴
        assert flt.should_replicate("cb:payment:123") is True
        assert flt.should_replicate("idempotency:order:456") is True

    def test_exclude_patterns(self) -> None:
        """제외 패턴."""
        flt = ReplicationFilter(
            exclude_patterns=["temp:.*", "local:.*"],
        )

        assert flt.should_replicate("temp:session:123") is False
        assert flt.should_replicate("local:cache:456") is False

    def test_custom_replicate_patterns(self) -> None:
        """커스텀 포함 패턴."""
        flt = ReplicationFilter(
            replicate_patterns=["custom:.*"],
        )

        assert flt.should_replicate("custom:data:123") is True


class TestRegionReplicator:
    """RegionReplicator 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 리셋."""
        reset_multiregion_settings()

    def test_init(self) -> None:
        """초기화."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        replicator = RegionReplicator(settings=settings)

        assert replicator._settings.current_region == "ap-northeast-2"

    def test_enqueue_filtered_out(self) -> None:
        """필터에 의해 제외된 키."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        replicator = RegionReplicator(settings=settings)
        # 필터 설정
        replicator._filter = ReplicationFilter(replicate_patterns=["cb:.*"])

        event = ReplicationEvent(
            event_type=ReplicationEventType.SET,
            key="other:key:123",
            value=b"value",
        )

        result = replicator.enqueue(event)

        # 필터링됨 (성공으로 처리)
        assert result is True

    def test_start_stop(self) -> None:
        """시작/중지."""
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            enabled=True,  # 활성화해야 start()가 동작
        )
        replicator = RegionReplicator(settings=settings)

        assert replicator._running is False

        replicator.start()
        assert replicator._running is True

        replicator.stop()
        assert replicator._running is False

    def test_get_stats(self) -> None:
        """통계 조회."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        replicator = RegionReplicator(settings=settings)

        stats = replicator.get_stats()

        assert "enqueued" in stats
        assert "replicated" in stats
        assert "filtered" in stats
        assert "failed" in stats
