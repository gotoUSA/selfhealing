"""
CheckpointManager 단위 테스트.

테스트 항목:
1. 체크포인트 저장/로드
2. 파일 존재 여부 확인
3. 파일 삭제
4. 체크포인트 경과 시간
5. 싱글톤 패턴
6. 원자적 쓰기 (임시 파일 사용)
"""

import time

from selfhealing.audit.checkpoint_manager import (
    CheckpointData,
    CheckpointManager,
    get_checkpoint_manager,
    reset_checkpoint_manager,
)


class TestCheckpointManager:
    """CheckpointManager 단위 테스트."""

    def test_save_and_load(self, tmp_path):
        """체크포인트 저장 및 로드."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        # 저장
        manager.save(last_sequence=1234)

        # 로드
        loaded = manager.load()
        assert loaded == 1234

    def test_load_nonexistent_returns_zero(self, tmp_path):
        """존재하지 않는 파일 로드 시 0 반환."""
        checkpoint_path = tmp_path / "nonexistent.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        loaded = manager.load()
        assert loaded == 0

    def test_exists(self, tmp_path):
        """파일 존재 여부 확인."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        assert manager.exists() is False

        manager.save(last_sequence=100)

        assert manager.exists() is True

    def test_delete(self, tmp_path):
        """체크포인트 파일 삭제."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        manager.save(last_sequence=100)
        assert manager.exists() is True

        result = manager.delete()
        assert result is True
        assert manager.exists() is False

    def test_delete_nonexistent(self, tmp_path):
        """존재하지 않는 파일 삭제 시도."""
        checkpoint_path = tmp_path / "nonexistent.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        result = manager.delete()
        assert result is True  # missing_ok=True

    def test_load_full(self, tmp_path):
        """전체 체크포인트 데이터 로드."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        manager.save(last_sequence=5678)

        data = manager.load_full()
        assert data is not None
        assert data.last_sequence == 5678
        assert data.version == 1
        assert data.timestamp > 0

    def test_load_full_nonexistent(self, tmp_path):
        """존재하지 않는 파일의 전체 데이터 로드."""
        checkpoint_path = tmp_path / "nonexistent.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        data = manager.load_full()
        assert data is None

    def test_get_age_seconds(self, tmp_path):
        """체크포인트 경과 시간."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        manager.save(last_sequence=100)
        time.sleep(0.1)

        age = manager.get_age_seconds()
        assert age is not None
        assert age >= 0.1

    def test_get_age_seconds_nonexistent(self, tmp_path):
        """존재하지 않는 체크포인트 경과 시간."""
        checkpoint_path = tmp_path / "nonexistent.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        age = manager.get_age_seconds()
        assert age is None

    def test_path_property(self, tmp_path):
        """체크포인트 경로 속성."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        assert manager.path == checkpoint_path

    def test_creates_parent_directory(self, tmp_path):
        """부모 디렉토리 자동 생성."""
        checkpoint_path = tmp_path / "subdir" / "nested" / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        manager.save(last_sequence=100)

        assert checkpoint_path.exists()

    def test_multiple_saves_overwrites(self, tmp_path):
        """여러 번 저장 시 덮어쓰기."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        manager.save(last_sequence=100)
        manager.save(last_sequence=200)
        manager.save(last_sequence=300)

        loaded = manager.load()
        assert loaded == 300

    def test_atomic_write_temp_file_cleanup(self, tmp_path):
        """원자적 쓰기 후 임시 파일 정리."""
        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        manager.save(last_sequence=100)

        # 임시 파일이 남아있지 않음
        temp_file = checkpoint_path.with_suffix(".tmp")
        assert not temp_file.exists()


class TestCheckpointData:
    """CheckpointData 데이터 클래스 테스트."""

    def test_to_dict(self):
        """딕셔너리 변환."""
        data = CheckpointData(
            last_sequence=1234,
            timestamp=1234567890.0,
            version=1,
        )

        result = data.to_dict()
        assert result["last_sequence"] == 1234
        assert result["timestamp"] == 1234567890.0
        assert result["version"] == 1

    def test_from_dict(self):
        """딕셔너리에서 생성."""
        d = {
            "last_sequence": 5678,
            "timestamp": 9876543210.0,
            "version": 1,
        }

        data = CheckpointData.from_dict(d)
        assert data.last_sequence == 5678
        assert data.timestamp == 9876543210.0
        assert data.version == 1

    def test_from_dict_missing_fields(self):
        """필드 누락 시 기본값."""
        d = {}

        data = CheckpointData.from_dict(d)
        assert data.last_sequence == 0
        assert data.timestamp == 0.0
        assert data.version == 1


class TestCheckpointManagerSingleton:
    """싱글톤 패턴 테스트."""

    def test_singleton_returns_same_instance(self):
        """동일 인스턴스 반환."""
        reset_checkpoint_manager()

        manager1 = get_checkpoint_manager()
        manager2 = get_checkpoint_manager()

        assert manager1 is manager2

    def test_reset_singleton(self):
        """싱글톤 초기화."""
        reset_checkpoint_manager()

        manager1 = get_checkpoint_manager()
        reset_checkpoint_manager()
        manager2 = get_checkpoint_manager()

        # 다른 인스턴스
        assert manager1 is not manager2


class TestCheckpointManagerThreadSafety:
    """스레드 안전성 테스트."""

    def test_concurrent_saves(self, tmp_path):
        """동시 저장 테스트."""
        import threading

        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)

        results = []
        errors = []

        def save_checkpoint(seq):
            try:
                manager.save(last_sequence=seq)
                results.append(seq)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            t = threading.Thread(target=save_checkpoint, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # 에러 없음
        assert len(errors) == 0

        # 최종 값 확인 (마지막 저장 값 중 하나)
        loaded = manager.load()
        assert 0 <= loaded <= 9

    def test_concurrent_loads(self, tmp_path):
        """동시 로드 테스트."""
        import threading

        checkpoint_path = tmp_path / "checkpoint.json"
        manager = CheckpointManager(checkpoint_path=checkpoint_path)
        manager.save(last_sequence=12345)

        results = []
        errors = []

        def load_checkpoint():
            try:
                seq = manager.load()
                results.append(seq)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            t = threading.Thread(target=load_checkpoint)
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # 에러 없음
        assert len(errors) == 0

        # 모든 결과가 동일
        assert all(r == 12345 for r in results)
