"""통합 체크포인트 저장 전략 테스트."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestUnifiedCheckpointData:
    """UnifiedCheckpointData 테스트."""

    def test_to_dict_and_from_dict(self):
        """직렬화/역직렬화 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(
            wal_sequence=1234,
            kafka_topic="test.topic",
            kafka_partition=3,
            kafka_offset=56789,
            checksum="abc123",
        )

        dict_data = data.to_dict()
        restored = UnifiedCheckpointData.from_dict(dict_data)

        assert restored.wal_sequence == 1234
        assert restored.kafka_topic == "test.topic"
        assert restored.kafka_partition == 3
        assert restored.kafka_offset == 56789
        assert restored.checksum == "abc123"

    def test_from_legacy_checkpoint_data(self):
        """레거시 CheckpointData 변환 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        legacy = {
            "last_sequence": 999,
            "timestamp": 1700000000.0,
            "version": 1,
        }

        data = UnifiedCheckpointData.from_legacy_checkpoint_data(legacy)

        assert data.wal_sequence == 999
        assert data.kafka_topic is None  # 레거시에는 없음
        assert "2023" in data.timestamp  # ISO 8601 형식으로 변환

    def test_from_dict_with_legacy_last_sequence(self):
        """from_dict가 last_sequence도 지원하는지 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        legacy = {
            "last_sequence": 500,
            "timestamp": "2024-01-01T00:00:00Z",
            "version": 1,
        }

        data = UnifiedCheckpointData.from_dict(legacy)
        assert data.wal_sequence == 500

    def test_default_timestamp(self):
        """timestamp 기본값 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1)
        assert data.timestamp is not None
        assert "T" in data.timestamp  # ISO 8601 형식


class TestFileCheckpointStorage:
    """FileCheckpointStorage 테스트."""

    @pytest.fixture
    def storage(self, tmp_path):
        """FileCheckpointStorage 인스턴스."""
        from selfhealing.audit.checkpoint_strategy import FileCheckpointStorage

        return FileCheckpointStorage(base_path=tmp_path)

    def test_save_and_load(self, storage):
        """저장 및 로드 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        loaded = storage.load("test")
        assert loaded is not None
        assert loaded.wal_sequence == 1234

    def test_load_nonexistent(self, storage):
        """존재하지 않는 체크포인트 로드."""
        loaded = storage.load("nonexistent")
        assert loaded is None

    def test_delete(self, storage):
        """삭제 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        assert storage.exists("test")
        assert storage.delete("test")
        assert not storage.exists("test")

    def test_get_wal_sequence(self, storage):
        """WAL 시퀀스 조회 편의 메서드."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        # 없을 때
        assert storage.get_wal_sequence("test") == 0

        # 있을 때
        data = UnifiedCheckpointData(wal_sequence=5678)
        storage.save("test", data)
        assert storage.get_wal_sequence("test") == 5678

    def test_multiple_namespaces(self, storage):
        """여러 네임스페이스 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        storage.save("ns1", UnifiedCheckpointData(wal_sequence=100))
        storage.save("ns2", UnifiedCheckpointData(wal_sequence=200))

        assert storage.get_wal_sequence("ns1") == 100
        assert storage.get_wal_sequence("ns2") == 200

    def test_overwrite(self, storage):
        """덮어쓰기 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        storage.save("test", UnifiedCheckpointData(wal_sequence=1))
        storage.save("test", UnifiedCheckpointData(wal_sequence=2))

        assert storage.get_wal_sequence("test") == 2

    def test_commit_is_noop(self, storage):
        """commit()이 no-op인지 테스트."""
        # 예외 발생하지 않아야 함
        storage.commit("test")

    def test_load_legacy_format(self, storage, tmp_path):
        """레거시 형식 로드 테스트."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        # 레거시 형식으로 직접 파일 생성
        file_path = tmp_path / "checkpoint.legacy.json"
        legacy_data = {
            "last_sequence": 999,
            "timestamp": 1700000000.0,
            "version": 1,
        }
        with open(file_path, "w") as f:
            json.dump(legacy_data, f)

        loaded = storage.load("legacy")
        assert loaded is not None
        assert loaded.wal_sequence == 999


class TestRedisCheckpointStorage:
    """RedisCheckpointStorage 테스트 (Mock)."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 클라이언트."""
        return MagicMock()

    @pytest.fixture
    def storage(self, mock_redis):
        """RedisCheckpointStorage 인스턴스."""
        from selfhealing.audit.checkpoint_strategy import RedisCheckpointStorage

        return RedisCheckpointStorage(
            redis_client=mock_redis,
            use_distributed_lock=False,  # 테스트에서 락 비활성화
            enable_notification=False,
        )

    def test_save_calls_redis_set(self, storage, mock_redis):
        """save()가 Redis set을 호출하는지 확인."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert "selfhealing:checkpoint:test" in call_args[0]

    def test_save_with_ttl(self, mock_redis):
        """TTL이 설정된 경우 setex 호출."""
        from selfhealing.audit.checkpoint_strategy import (
            RedisCheckpointStorage,
            UnifiedCheckpointData,
        )

        storage = RedisCheckpointStorage(
            redis_client=mock_redis,
            ttl_seconds=3600,
            use_distributed_lock=False,
            enable_notification=False,
        )
        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        mock_redis.setex.assert_called_once()

    def test_load_returns_data(self, storage, mock_redis):
        """load()가 데이터를 올바르게 반환하는지 확인."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        mock_redis.get.return_value = json.dumps({
            "wal_sequence": 5678,
            "timestamp": "2024-01-01T00:00:00Z",
            "version": 1,
        })

        data = storage.load("test")
        assert data is not None
        assert data.wal_sequence == 5678

    def test_load_returns_none_when_missing(self, storage, mock_redis):
        """키가 없을 때 None 반환."""
        mock_redis.get.return_value = None
        assert storage.load("missing") is None

    def test_delete(self, storage, mock_redis):
        """delete() 테스트."""
        mock_redis.delete.return_value = 1
        assert storage.delete("test")
        mock_redis.delete.assert_called_once()

    def test_exists(self, storage, mock_redis):
        """exists() 테스트."""
        mock_redis.exists.return_value = 1
        assert storage.exists("test")

        mock_redis.exists.return_value = 0
        assert not storage.exists("test2")


class TestKafkaRedisCheckpointStorage:
    """KafkaRedisCheckpointStorage 테스트 (Mock)."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 클라이언트."""
        return MagicMock()

    @pytest.fixture
    def storage(self, mock_redis, tmp_path):
        """KafkaRedisCheckpointStorage 인스턴스."""
        from selfhealing.audit.checkpoint_strategy import KafkaRedisCheckpointStorage

        return KafkaRedisCheckpointStorage(
            redis_client=mock_redis,
            file_backup_path=tmp_path / "backup",
            enable_file_backup=True,
            enable_notification=False,
        )

    def test_save_to_both_redis_and_file(self, storage, mock_redis, tmp_path):
        """Redis와 File 둘 다 저장되는지 확인."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        # Redis 호출 확인
        mock_redis.set.assert_called_once()

        # File 백업 존재 확인
        backup_path = tmp_path / "backup" / "checkpoint.test.json"
        assert backup_path.exists()

    def test_fallback_to_file_when_redis_fails(self, storage, mock_redis, tmp_path):
        """Redis 실패 시 File로 폴백."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        mock_redis.set.side_effect = Exception("Redis connection failed")

        data = UnifiedCheckpointData(wal_sequence=1234)
        # 예외가 발생하지 않아야 함 (File 백업 성공)
        storage.save("test", data)

        # File 백업 존재 확인
        backup_path = tmp_path / "backup" / "checkpoint.test.json"
        assert backup_path.exists()

    def test_load_from_redis_first(self, storage, mock_redis):
        """load()가 Redis를 먼저 시도하는지 확인."""
        mock_redis.get.return_value = json.dumps({
            "wal_sequence": 5678,
            "kafka_topic": "test.topic",
            "kafka_partition": 0,
            "kafka_offset": 100,
            "timestamp": "2024-01-01T00:00:00Z",
            "version": 1,
        })

        data = storage.load("test")
        assert data is not None
        assert data.wal_sequence == 5678
        assert data.kafka_topic == "test.topic"

    def test_load_fallback_to_file(self, storage, mock_redis, tmp_path):
        """Redis 실패 시 File에서 로드."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        mock_redis.get.return_value = None

        # File에 직접 데이터 저장
        backup_path = tmp_path / "backup" / "checkpoint.test.json"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with open(backup_path, "w") as f:
            json.dump(UnifiedCheckpointData(wal_sequence=9999).to_dict(), f)

        data = storage.load("test")
        assert data is not None
        assert data.wal_sequence == 9999

    def test_save_with_kafka_offset(self, storage, mock_redis):
        """save_with_kafka_offset() 테스트."""
        storage.save_with_kafka_offset(
            namespace="test",
            wal_sequence=1234,
            kafka_topic="my.topic",
            kafka_partition=2,
            kafka_offset=5678,
            checksum="abc",
        )

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        saved_data = json.loads(call_args[0][1])
        assert saved_data["wal_sequence"] == 1234
        assert saved_data["kafka_topic"] == "my.topic"
        assert saved_data["kafka_partition"] == 2
        assert saved_data["kafka_offset"] == 5678

    def test_delete_from_both(self, storage, mock_redis, tmp_path):
        """Redis와 File 둘 다에서 삭제."""
        from selfhealing.audit.checkpoint_strategy import UnifiedCheckpointData

        # 먼저 저장
        data = UnifiedCheckpointData(wal_sequence=1234)
        storage.save("test", data)

        mock_redis.delete.return_value = 1

        # 삭제
        assert storage.delete("test")
        mock_redis.delete.assert_called_once()


class TestCompositeCheckpointStorage:
    """CompositeCheckpointStorage 테스트."""

    @pytest.fixture
    def primary(self, tmp_path):
        """Primary 저장소 (File)."""
        from selfhealing.audit.checkpoint_strategy import FileCheckpointStorage

        return FileCheckpointStorage(base_path=tmp_path / "primary")

    @pytest.fixture
    def secondary(self, tmp_path):
        """Secondary 저장소 (File)."""
        from selfhealing.audit.checkpoint_strategy import FileCheckpointStorage

        return FileCheckpointStorage(base_path=tmp_path / "secondary")

    def test_save_to_primary(self, primary, secondary):
        """정상적인 경우 Primary에 저장."""
        from selfhealing.audit.checkpoint_strategy import (
            CompositeCheckpointStorage,
            UnifiedCheckpointData,
        )

        composite = CompositeCheckpointStorage(primary=primary, secondary=secondary)

        data = UnifiedCheckpointData(wal_sequence=1234)
        composite.save("test", data)

        assert primary.exists("test")
        stats = composite.get_stats()
        assert stats["primary_writes"] == 1
        assert stats["current_tier"] == "primary"

    def test_fallback_to_secondary(self, secondary):
        """Primary 실패 시 Secondary로 폴백."""
        from selfhealing.audit.checkpoint_strategy import (
            CompositeCheckpointStorage,
            UnifiedCheckpointData,
        )

        # 실패하는 Mock Primary
        mock_primary = MagicMock()
        mock_primary.save.side_effect = Exception("Primary failed")

        composite = CompositeCheckpointStorage(
            primary=mock_primary,
            secondary=secondary,
        )

        data = UnifiedCheckpointData(wal_sequence=1234)
        composite.save("test", data)

        assert secondary.exists("test")
        stats = composite.get_stats()
        assert stats["secondary_writes"] == 1
        assert stats["fallback_events"] == 1
        assert stats["current_tier"] == "secondary"

    def test_fallback_to_memory(self):
        """모든 저장소 실패 시 Memory로 폴백."""
        from selfhealing.audit.checkpoint_strategy import (
            CompositeCheckpointStorage,
            UnifiedCheckpointData,
        )

        mock_primary = MagicMock()
        mock_primary.save.side_effect = Exception("Primary failed")

        mock_secondary = MagicMock()
        mock_secondary.save.side_effect = Exception("Secondary failed")

        composite = CompositeCheckpointStorage(
            primary=mock_primary,
            secondary=mock_secondary,
            enable_memory_fallback=True,
        )

        data = UnifiedCheckpointData(wal_sequence=1234)
        composite.save("test", data)

        stats = composite.get_stats()
        assert stats["memory_writes"] == 1
        assert stats["current_tier"] == "memory"

        # Memory에서 로드
        mock_primary.load.return_value = None
        mock_secondary.load.return_value = None
        loaded = composite.load("test")
        assert loaded is not None
        assert loaded.wal_sequence == 1234

    def test_load_tiered(self, primary, secondary):
        """Tiered Load 테스트."""
        from selfhealing.audit.checkpoint_strategy import (
            CompositeCheckpointStorage,
            UnifiedCheckpointData,
        )

        composite = CompositeCheckpointStorage(primary=primary, secondary=secondary)

        # Secondary에만 데이터 저장
        secondary.save("test", UnifiedCheckpointData(wal_sequence=9999))

        # Primary에는 없으므로 Secondary에서 로드
        data = composite.load("test")
        assert data is not None
        assert data.wal_sequence == 9999

    def test_exists_checks_all_tiers(self, primary, secondary):
        """exists()가 모든 Tier를 체크하는지 확인."""
        from selfhealing.audit.checkpoint_strategy import (
            CompositeCheckpointStorage,
            UnifiedCheckpointData,
        )

        composite = CompositeCheckpointStorage(primary=primary, secondary=secondary)

        assert not composite.exists("test")

        secondary.save("test", UnifiedCheckpointData(wal_sequence=1))
        assert composite.exists("test")


class TestGetCheckpointStrategy:
    """get_checkpoint_strategy 팩토리 테스트."""

    def test_file_strategy_default(self):
        """기본값은 FileCheckpointStorage."""
        from selfhealing.audit.checkpoint_strategy import (
            FileCheckpointStorage,
            get_checkpoint_strategy,
        )

        strategy = get_checkpoint_strategy(storage_type="file")
        assert isinstance(strategy, FileCheckpointStorage)

    def test_redis_requires_client(self):
        """Redis 전략은 클라이언트 필수."""
        from selfhealing.audit.checkpoint_strategy import get_checkpoint_strategy

        with pytest.raises(ValueError, match="redis_client is required"):
            get_checkpoint_strategy(storage_type="redis")

    def test_kafka_redis_requires_client(self):
        """Kafka+Redis 전략은 클라이언트 필수."""
        from selfhealing.audit.checkpoint_strategy import get_checkpoint_strategy

        with pytest.raises(ValueError, match="redis_client is required"):
            get_checkpoint_strategy(storage_type="kafka_redis")

    def test_unknown_storage_type(self):
        """알 수 없는 저장소 유형."""
        from selfhealing.audit.checkpoint_strategy import get_checkpoint_strategy

        with pytest.raises(ValueError, match="Unknown storage_type"):
            get_checkpoint_strategy(storage_type="unknown")

    def test_redis_strategy_with_client(self):
        """Redis 전략 생성."""
        from selfhealing.audit.checkpoint_strategy import (
            RedisCheckpointStorage,
            get_checkpoint_strategy,
        )

        mock_redis = MagicMock()
        strategy = get_checkpoint_strategy(
            storage_type="redis",
            redis_client=mock_redis,
        )
        assert isinstance(strategy, RedisCheckpointStorage)

    def test_composite_strategy(self):
        """Composite 전략 생성."""
        from selfhealing.audit.checkpoint_strategy import (
            CompositeCheckpointStorage,
            get_checkpoint_strategy,
        )

        mock_redis = MagicMock()
        strategy = get_checkpoint_strategy(
            storage_type="composite",
            redis_client=mock_redis,
            primary_type="redis",
            secondary_type="file",
        )
        assert isinstance(strategy, CompositeCheckpointStorage)


class TestCheckpointStrategyRegistry:
    """CheckpointStrategyRegistry 테스트."""

    def teardown_method(self):
        """각 테스트 후 레지스트리 초기화."""
        from selfhealing.audit.checkpoint_strategy import CheckpointStrategyRegistry

        CheckpointStrategyRegistry.clear()

    def test_register_and_get(self):
        """등록 및 조회 테스트."""
        from selfhealing.audit.checkpoint_strategy import (
            CheckpointStrategyRegistry,
            FileCheckpointStorage,
        )

        CheckpointStrategyRegistry.register("custom_file", FileCheckpointStorage)

        strategy = CheckpointStrategyRegistry.get("custom_file")
        assert isinstance(strategy, FileCheckpointStorage)

    def test_auto_register(self):
        """자동 등록 테스트."""
        from selfhealing.audit.checkpoint_strategy import CheckpointStrategyRegistry

        strategies = CheckpointStrategyRegistry.list_strategies()
        assert "file" in strategies
        assert "redis" in strategies
        assert "kafka_redis" in strategies
        assert "composite" in strategies

    def test_set_default(self):
        """기본값 설정 테스트."""
        from selfhealing.audit.checkpoint_strategy import (
            CheckpointStrategyRegistry,
            FileCheckpointStorage,
        )

        CheckpointStrategyRegistry.set_default("file")
        strategy = CheckpointStrategyRegistry.get()  # name=None
        assert isinstance(strategy, FileCheckpointStorage)


class TestCheckpointErrors:
    """에러 클래스 테스트."""

    def test_checkpoint_error(self):
        """CheckpointError 테스트."""
        from selfhealing.audit.checkpoint_strategy import CheckpointError

        error = CheckpointError("Test error")
        assert str(error) == "Test error"

    def test_checkpoint_corrupted_error(self):
        """CheckpointCorruptedError 테스트."""
        from selfhealing.audit.checkpoint_strategy import CheckpointCorruptedError

        error = CheckpointCorruptedError(
            message="Checksum mismatch",
            expected="abc123",
            computed="xyz789",
        )
        assert "Checksum mismatch" in str(error)
        assert error.expected == "abc123"
        assert error.computed == "xyz789"


class TestSingleton:
    """싱글톤 테스트."""

    def teardown_method(self):
        """각 테스트 후 싱글톤 초기화."""
        from selfhealing.audit.checkpoint_strategy import reset_default_checkpoint_strategy

        reset_default_checkpoint_strategy()

    def test_get_default_checkpoint_strategy(self, monkeypatch):
        """get_default_checkpoint_strategy 테스트."""
        from selfhealing.audit.checkpoint_strategy import (
            FileCheckpointStorage,
            get_default_checkpoint_strategy,
        )

        monkeypatch.setenv("SELFHEALING_CHECKPOINT_STORAGE", "file")

        strategy = get_default_checkpoint_strategy()
        assert isinstance(strategy, FileCheckpointStorage)

        # 같은 인스턴스 반환
        strategy2 = get_default_checkpoint_strategy()
        assert strategy is strategy2

    def test_reset_default_checkpoint_strategy(self, monkeypatch):
        """reset_default_checkpoint_strategy 테스트."""
        from selfhealing.audit.checkpoint_strategy import (
            get_default_checkpoint_strategy,
            reset_default_checkpoint_strategy,
        )

        monkeypatch.setenv("SELFHEALING_CHECKPOINT_STORAGE", "file")

        strategy1 = get_default_checkpoint_strategy()
        reset_default_checkpoint_strategy()
        strategy2 = get_default_checkpoint_strategy()

        # 리셋 후 새 인스턴스
        assert strategy1 is not strategy2
