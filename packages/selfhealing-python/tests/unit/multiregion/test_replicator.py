"""
Region Replicator 테스트.

테스트 대상:
- ReplicationEventType: 복제 이벤트 타입 Enum
- ReplicationEvent: 복제 이벤트 데이터클래스
- ReplicationFilter: 복제 필터
- RegionReplicator: 리전 복제기
"""



from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.replicator import (
    RegionReplicator,
    ReplicationEvent,
    ReplicationEventType,
    ReplicationFilter,
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

    # =========================================================================
    # refresh_targets() 테스트 (237 약점 1)
    # =========================================================================

    def test_refresh_targets_adds_new_region(self) -> None:
        """refresh_targets()는 새 리전을 _targets에 추가한다."""
        peer_json = '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "http://us:8000"}]'
        settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions=peer_json)
        replicator = RegionReplicator(settings=settings)

        # 초기 타겟 확인
        assert len(replicator._targets) == 1
        assert replicator._targets[0].endpoint.region == "us-east-1"

        # 피어 추가 (settings 변경 시뮬레이션)
        new_peer_json = (
            '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "http://us:8000"},'
            '{"region": "eu-west-1", "redis_url": "redis://eu:6379", "api_endpoint": "http://eu:8000"}]'
        )
        replicator._settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions=new_peer_json)

        result = replicator.refresh_targets()

        assert result == 2
        regions = {t.endpoint.region for t in replicator._targets}
        assert "us-east-1" in regions
        assert "eu-west-1" in regions

    def test_refresh_targets_removes_old_region(self) -> None:
        """refresh_targets()는 제거된 리전을 _targets에서 제거한다."""
        peer_json = (
            '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "http://us:8000"},'
            '{"region": "eu-west-1", "redis_url": "redis://eu:6379", "api_endpoint": "http://eu:8000"}]'
        )
        settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions=peer_json)
        replicator = RegionReplicator(settings=settings)
        assert len(replicator._targets) == 2

        # eu-west-1 제거
        new_peer_json = '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "http://us:8000"}]'
        replicator._settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions=new_peer_json)

        result = replicator.refresh_targets()

        assert result == 1
        assert replicator._targets[0].endpoint.region == "us-east-1"

    def test_refresh_targets_no_change(self) -> None:
        """피어 목록이 변경되지 않으면 _targets는 그대로 유지된다."""
        peer_json = '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "http://us:8000"}]'
        settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions=peer_json)
        replicator = RegionReplicator(settings=settings)

        result = replicator.refresh_targets()

        assert result == 1

    def test_refresh_targets_empty_peers(self) -> None:
        """피어 목록이 비어있으면 모든 타겟이 제거된다."""
        peer_json = '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "http://us:8000"}]'
        settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions=peer_json)
        replicator = RegionReplicator(settings=settings)
        assert len(replicator._targets) == 1

        replicator._settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions="[]")

        result = replicator.refresh_targets()

        assert result == 0
        assert len(replicator._targets) == 0

    def test_refresh_targets_returns_count(self) -> None:
        """refresh_targets()는 갱신 후 타겟 수를 반환한다."""
        settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions="[]")
        replicator = RegionReplicator(settings=settings)

        result = replicator.refresh_targets()

        assert result == len(replicator._targets)
